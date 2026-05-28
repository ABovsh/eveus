"""Support for Eveus number entities with optimistic UI and safety."""
from __future__ import annotations

import logging
import time

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberDeviceClass,
    NumberEntityDescription,
)
from homeassistant.core import HomeAssistant, callback, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfTime,
)

from . import EveusConfigEntry
from .const import (
    MODEL_MAX_CURRENT,
    MIN_CURRENT,
    CONF_MODEL,
    CONTROL_GRACE_PERIOD,
    OPTIMISTIC_CONTROL_TTL,
)
from .common_base import (
    BaseEveusEntity,
    ControlEntityMixin,
    OptimisticControlMixin,
    WriteOnChangeMixin,
)
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

CHARGING_CURRENT_DESCRIPTION = NumberEntityDescription(
    key="charging_current",
    name="Charging Current",
    icon="mdi:current-ac",
    entity_category=EntityCategory.CONFIG,
    native_step=1.0,
    mode=NumberMode.SLIDER,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    device_class=NumberDeviceClass.CURRENT,
)


class EveusNumberEntity(
    WriteOnChangeMixin,
    OptimisticControlMixin[float],
    ControlEntityMixin,
    BaseEveusEntity,
    NumberEntity,
):
    """Base number entity with responsive UI and safety."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _control_entity_label = "Number"

    def __init__(
        self,
        updater,
        entity_description: NumberEntityDescription,
        device_number: int = 1,
    ) -> None:
        """Initialize the entity."""
        self.entity_description = entity_description
        self.ENTITY_NAME = entity_description.name
        super().__init__(updater, device_number)

        self._pending_value: float | None = None
        self._init_optimistic_control()
        self._init_write_on_change()


class EveusCurrentNumber(EveusNumberEntity):
    """Representation of Eveus current control with responsive UI."""

    ENTITY_NAME = "Charging Current"
    _command = "currentSet"

    def __init__(self, updater, model: str, device_number: int = 1) -> None:
        """Initialize the current control."""
        super().__init__(updater, CHARGING_CURRENT_DESCRIPTION, device_number)
        self._model = model

        self._attr_native_min_value = float(MIN_CURRENT)
        self._attr_native_max_value = float(MODEL_MAX_CURRENT[model])
        self._attr_native_value = self._resolve_value()

    @property
    def native_value(self) -> float | None:
        """Return cached current value without side effects."""
        return self._attr_native_value

    def _resolve_value(self) -> float | None:
        """Resolve current value from command, optimistic, device, and restore state."""
        current_time = time.time()

        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return self._optimistic_value

        if self._updater.available and self._updater.data and self._command in self._updater.data:
            device_value = get_safe_value(self._updater.data, self._command, float)
            if device_value is not None:
                return float(device_value)

        if self._last_device_value is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with optimistic UI."""
        try:
            clamped_value = max(
                self._attr_native_min_value,
                min(self._attr_native_max_value, value),
            )
            int_value = int(round(clamped_value))

            self._pending_value = float(int_value)
            self._attr_native_value = self._pending_value
            self._write_if_changed(self._attr_native_value)

            success = await self._updater.send_command(self._command, int_value)

            if success:
                self._set_optimistic_value(float(int_value))
            else:
                raise HomeAssistantError(
                    f"Eveus charger did not accept charging current = {int_value}A"
                )

        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to set current value: %s", err, exc_info=True)
            raise HomeAssistantError(f"Failed to set charging current: {err}") from err
        finally:
            self._pending_value = None
            self._last_command_time = time.time()
            self._attr_native_value = self._resolve_value()
            self._write_if_changed(self._attr_native_value)

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display value only — no commands sent on startup."""
        try:
            if state and state.state not in (None, "unknown", "unavailable"):
                restored_value = float(state.state)
                if self._attr_native_min_value <= restored_value <= self._attr_native_max_value:
                    self._last_device_value = restored_value
                    self._attr_native_value = restored_value
        except (TypeError, ValueError) as err:
            _LOGGER.debug("Could not restore number state for %s: %s", self.name, err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data — reconcile with device value."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._pending_value is not None:
            return

        current_time = time.time()

        if self._updater.available and self._updater.data:
            if self._command in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._command, float)
                if device_value is not None:
                    self._reconcile_with_device(
                        float(device_value),
                        current_time,
                        lambda optimistic, device: abs(optimistic - device) < 0.5,
                    )

        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)

        current_value = self._resolve_value()
        self._attr_native_value = current_value
        self._write_if_changed(current_value)


class EveusSessionLimitNumber(EveusNumberEntity):
    """Generic writable session-limit (energy / time / money) backed by /main."""

    def __init__(
        self,
        updater,
        description: NumberEntityDescription,
        command: str,
        max_value: float,
        device_number: int = 1,
    ) -> None:
        super().__init__(updater, description, device_number)
        self._command = command
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = float(max_value)
        self._attr_native_value = self._resolve_value()

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    def _resolve_value(self) -> float | None:
        current_time = time.time()
        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return self._optimistic_value
        if self._updater.available and self._updater.data and self._command in self._updater.data:
            value = get_safe_value(self._updater.data, self._command, float)
            if value is not None:
                return float(value)
        if self._last_device_value is not None and current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
            return self._last_device_value
        return None

    async def async_set_native_value(self, value: float) -> None:
        try:
            clamped = max(self._attr_native_min_value, min(self._attr_native_max_value, value))
            int_value = int(round(clamped))
            self._pending_value = float(int_value)
            self._attr_native_value = self._pending_value
            self._write_if_changed(self._attr_native_value)
            try:
                success = await self._updater.send_command(self._command, int_value)
            except Exception:
                self._optimistic_value = None
                raise
            if success:
                self._set_optimistic_value(float(int_value))
            else:
                raise HomeAssistantError(
                    f"Eveus charger did not accept {self.name} = {int_value}"
                )
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to set %s: %s", self.name, err, exc_info=True)
            raise HomeAssistantError(f"Failed to set {self.name}: {err}") from err
        finally:
            self._pending_value = None
            self._last_command_time = time.time()
            self._attr_native_value = self._resolve_value()
            self._write_if_changed(self._attr_native_value)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._pending_value is not None:
            return
        current_time = time.time()
        if self._updater.available and self._updater.data and self._command in self._updater.data:
            device_value = get_safe_value(self._updater.data, self._command, float)
            if device_value is not None:
                self._reconcile_with_device(
                    float(device_value),
                    current_time,
                    lambda optimistic, device: abs(optimistic - device) < 0.5,
                )
        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)
        self._attr_native_value = self._resolve_value()
        self._write_if_changed(self._attr_native_value)


SESSION_LIMIT_DESCRIPTIONS: tuple[tuple[NumberEntityDescription, str, float], ...] = (
    (
        NumberEntityDescription(
            key="energy_limit",
            name="Energy Limit",
            icon="mdi:flash-outline",
            entity_category=EntityCategory.CONFIG,
            native_step=1.0,
            mode=NumberMode.BOX,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=NumberDeviceClass.ENERGY,
        ),
        "energyLimit",
        200.0,
    ),
    (
        NumberEntityDescription(
            key="time_limit",
            name="Time Limit",
            icon="mdi:timer-sand",
            entity_category=EntityCategory.CONFIG,
            native_step=1.0,
            mode=NumberMode.BOX,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            device_class=NumberDeviceClass.DURATION,
        ),
        "timeLimit",
        1440.0,
    ),
    (
        NumberEntityDescription(
            key="money_limit",
            name="Money Limit",
            icon="mdi:cash",
            entity_category=EntityCategory.CONFIG,
            native_step=1.0,
            mode=NumberMode.BOX,
            # NumberDeviceClass.MONETARY requires an ISO 4217 code, not a symbol.
            native_unit_of_measurement="UAH",
            device_class=NumberDeviceClass.MONETARY,
        ),
        "moneyLimit",
        10000.0,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus number entities."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    model = entry.data.get(CONF_MODEL)
    if not model:
        _LOGGER.debug("No model specified in config")
        return

    entities: list[NumberEntity] = [EveusCurrentNumber(updater, model, device_number)]
    entities.extend(
        EveusSessionLimitNumber(updater, desc, command, max_v, device_number)
        for desc, command, max_v in SESSION_LIMIT_DESCRIPTIONS
    )
    async_add_entities(entities)
