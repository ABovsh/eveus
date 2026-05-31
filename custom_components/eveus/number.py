"""Support for Eveus number entities with optimistic UI and safety."""
from __future__ import annotations

import logging
import math
import time

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberDeviceClass,
    NumberEntityDescription,
    RestoreNumber,
)
from homeassistant.core import HomeAssistant, callback, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    UnitOfElectricCurrent,
)

from . import EveusConfigEntry
from .const import (
    MODEL_MAX_CURRENT,
    MIN_CURRENT,
    CONF_MODEL,
    CONTROL_GRACE_PERIOD,
    OPTIMISTIC_CONTROL_TTL,
    SOC_INPUT_LIMITS,
    DEFAULT_INITIAL_SOC,
    DEFAULT_TARGET_SOC,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_SOC_CORRECTION,
    soc_update_signal,
    get_soc_mode,
    SOC_MODE_ADVANCED,
    CONF_INITIAL_SOC,
    CONF_TARGET_SOC,
    CONF_BATTERY_CAPACITY,
    CONF_SOC_CORRECTION,
)
from .common_base import (
    BaseEveusEntity,
    ControlEntityMixin,
    OptimisticControlMixin,
    WriteOnChangeMixin,
)
from .utils import get_safe_value, normalize_soc_input

_LOGGER = logging.getLogger(__name__)


def _validate_finite_number(value, label: str) -> float:
    """Reject NaN/inf/bool from service-call input before clamping."""
    if isinstance(value, bool):
        raise HomeAssistantError(f"{label}: boolean value not accepted")
    try:
        raw = float(value)
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(f"{label}: not a number") from err
    if not math.isfinite(raw):
        raise HomeAssistantError(f"{label}: NaN or infinity not accepted")
    return raw

_CHARGING_CURRENT_NAME = "Charging Current"

CHARGING_CURRENT_DESCRIPTION = NumberEntityDescription(
    key="charging_current",
    name=_CHARGING_CURRENT_NAME,
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

    ENTITY_NAME = _CHARGING_CURRENT_NAME
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
            if device_value is not None and (
                self._attr_native_min_value <= device_value <= self._attr_native_max_value
            ):
                return float(device_value)

        if self._last_device_value is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with optimistic UI."""
        try:
            raw = _validate_finite_number(value, _CHARGING_CURRENT_NAME)
            clamped_value = max(
                self._attr_native_min_value,
                min(self._attr_native_max_value, raw),
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
                    self._last_successful_read = time.time()
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
                if device_value is not None and (
                    self._attr_native_min_value <= device_value <= self._attr_native_max_value
                ):
                    self._reconcile_with_device(
                        float(device_value),
                        current_time,
                        lambda optimistic, device: abs(optimistic - device) < 0.5,
                    )

        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)

        current_value = self._resolve_value()
        self._attr_native_value = current_value
        self._write_if_changed(current_value)


class EveusSocConfigNumber(
    WriteOnChangeMixin,
    BaseEveusEntity,
    RestoreNumber,
    NumberEntity,
):
    """Local SOC-input number: holds a value, pushes it to the SOC calculator.

    Sends nothing to the charger. Persists across restarts via RestoreNumber.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _soc_key: str = ""

    def __init__(self, updater, soc_calculator, seed, device_number: int = 1) -> None:
        """Initialize the SOC-input number entity."""
        self.ENTITY_NAME = self._attr_name
        super().__init__(updater, device_number)
        self._soc_calculator = soc_calculator
        default = _SOC_DEFAULTS[self._soc_key]
        self._attr_native_value = normalize_soc_input(self._soc_key, seed, default)
        self._init_write_on_change()

    @property
    def native_value(self) -> float | None:
        """Return cached value without side effects."""
        return self._attr_native_value

    @property
    def available(self) -> bool:
        """Config inputs are always available regardless of charger state."""
        return True

    async def async_added_to_hass(self) -> None:
        """Restore last value (falling back to the seed), then push it."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            lo, hi = SOC_INPUT_LIMITS[self._soc_key]
            if lo <= last.native_value <= hi:
                self._attr_native_value = float(last.native_value)
        self._push()

    def _push(self) -> None:
        """Push the current value into the calculator and notify SOC sensors."""
        self._soc_calculator.set_value(self._soc_key, self._attr_native_value)
        if self.hass is not None:
            async_dispatcher_send(
                self.hass, soc_update_signal(self._updater.config_entry.entry_id)
            )

    async def async_set_native_value(self, value: float) -> None:
        """Clamp, store, persist, and push a new SOC-input value."""
        lo, hi = self.native_min_value, self.native_max_value
        clamped = max(lo, min(hi, float(value)))
        self._attr_native_value = clamped
        self._write_if_changed(clamped)
        self._push()


class EveusInitialSocNumber(EveusSocConfigNumber):
    """Initial state of charge (%)."""

    _soc_key = "initial_soc"
    _attr_name = "Initial SOC"
    _attr_icon = "mdi:battery-charging-40"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["initial_soc"]
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER


class EveusTargetSocNumber(EveusSocConfigNumber):
    """Target state of charge (%)."""

    _soc_key = "target_soc"
    _attr_name = "Target SOC"
    _attr_icon = "mdi:battery-charging-high"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["target_soc"]
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER


class EveusBatteryCapacityNumber(EveusSocConfigNumber):
    """Battery capacity (kWh)."""

    _soc_key = "battery_capacity"
    _attr_name = "Battery Capacity"
    _attr_icon = "mdi:car-battery"
    _attr_native_unit_of_measurement = "kWh"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["battery_capacity"]
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX


class EveusSocCorrectionNumber(EveusSocConfigNumber):
    """SOC charging-loss correction (%)."""

    _soc_key = "soc_correction"
    _attr_name = "SOC Correction"
    _attr_icon = "mdi:chart-bell-curve"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["soc_correction"]
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX


_SOC_NUMBER_CLASSES = (
    EveusInitialSocNumber,
    EveusTargetSocNumber,
    EveusBatteryCapacityNumber,
    EveusSocCorrectionNumber,
)

_SOC_DEFAULTS = {
    "initial_soc": DEFAULT_INITIAL_SOC,
    "target_soc": DEFAULT_TARGET_SOC,
    "battery_capacity": DEFAULT_BATTERY_CAPACITY,
    "soc_correction": DEFAULT_SOC_CORRECTION,
}


def build_soc_numbers(
    updater, soc_calculator, seeds: dict, device_number: int = 1
) -> list:
    """Build the four SOC config-number entities seeded from `seeds`/defaults."""
    out = []
    for cls in _SOC_NUMBER_CLASSES:
        key = cls._soc_key
        seed = seeds.get(key, _SOC_DEFAULTS[key])
        out.append(cls(updater, soc_calculator, seed, device_number))
    return out


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
    entities = []
    if model:
        entities.append(EveusCurrentNumber(updater, model, device_number))
    else:
        _LOGGER.debug("No model specified in config")

    if get_soc_mode(entry) == SOC_MODE_ADVANCED:
        seeds = {
            "initial_soc": entry.data.get(CONF_INITIAL_SOC),
            "target_soc": entry.data.get(CONF_TARGET_SOC),
            "battery_capacity": entry.data.get(CONF_BATTERY_CAPACITY),
            "soc_correction": entry.data.get(CONF_SOC_CORRECTION),
        }
        seeds = {k: v for k, v in seeds.items() if v is not None}
        entities += build_soc_numbers(
            updater, runtime_data.soc_calculator, seeds, device_number
        )

    if entities:
        async_add_entities(entities)
