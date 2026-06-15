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
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
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
    WriteOnChangeMixin,
)
from .control_base import CommandBackedEntity
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


class EveusSetpointNumberDescription(NumberEntityDescription, frozen_or_thawed=True):
    """Declarative description for a charger-backed setpoint number."""

    command: str
    state_key: str
    # Independent scales: a device read is multiplied by ``device_to_ha`` to get
    # HA units; an HA value is multiplied by ``ha_to_device`` for the write. They
    # differ for the global energy limit (reads kWh 1:1, writes Wh-thousandths).
    device_to_ha: float = 1.0
    ha_to_device: float = 1.0


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

GLOBAL_LIMIT_NUMBERS: tuple[EveusSetpointNumberDescription, ...] = (
    EveusSetpointNumberDescription(
        key="limit_time",
        name="Limit Time",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.CONFIG,
        command="timeLimit",
        state_key="timeLimit",
        device_to_ha=1 / 60,
        ha_to_device=60.0,
        native_min_value=0,
        native_max_value=1440,
        native_step=5,
        native_unit_of_measurement="min",
        mode=NumberMode.BOX,
    ),
    EveusSetpointNumberDescription(
        key="limit_energy",
        name="Limit Energy",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command="energyLimit",
        state_key="energyLimit",
        device_to_ha=1.0,
        ha_to_device=1000.0,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="kWh",
        mode=NumberMode.BOX,
    ),
    EveusSetpointNumberDescription(
        key="limit_cost",
        name="Limit Cost",
        icon="mdi:cash",
        entity_category=EntityCategory.CONFIG,
        command="moneyLimit",
        state_key="moneyLimit",
        native_min_value=0,
        native_max_value=10000,
        native_step=1,
        native_unit_of_measurement="UAH",
        mode=NumberMode.BOX,
    ),
)

UNDERVOLTAGE_THRESHOLD_NUMBER = EveusSetpointNumberDescription(
    key="undervoltage_threshold",
    name="Undervoltage threshold",
    icon="mdi:flash-alert",
    entity_category=EntityCategory.CONFIG,
    command="aiVoltage",
    state_key="aiVoltage",
    native_min_value=210,
    native_max_value=220,
    native_step=1,
    native_unit_of_measurement="V",
    mode=NumberMode.SLIDER,
)


def _schedule_current(n: int) -> EveusSetpointNumberDescription:
    return EveusSetpointNumberDescription(
        key=f"schedule_{n}_current_limit",
        name=f"Schedule {n} Current limit",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        command=f"sh{n}CurrentValue",
        state_key=f"sh{n}CurrentValue",
        native_min_value=MIN_CURRENT,
        native_max_value=32,  # overridden per-model at setup
        native_step=1,
        native_unit_of_measurement="A",
        mode=NumberMode.BOX,
    )


def _schedule_energy(n: int) -> EveusSetpointNumberDescription:
    return EveusSetpointNumberDescription(
        key=f"schedule_{n}_energy_limit",
        name=f"Schedule {n} Energy limit",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command=f"sh{n}EnergyValue",
        state_key=f"sh{n}EnergyValue",
        device_to_ha=1.0,
        ha_to_device=1.0,  # schedule energy is 1:1 — NOT ×1000
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="kWh",
        mode=NumberMode.BOX,
    )


SCHEDULE_LIMIT_NUMBERS: tuple[EveusSetpointNumberDescription, ...] = (
    _schedule_current(1), _schedule_energy(1),
    _schedule_current(2), _schedule_energy(2),
)


class EveusNumberEntity(
    WriteOnChangeMixin,
    ControlEntityMixin,
    CommandBackedEntity[float],
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

    @property
    def _state_key(self) -> str:
        """Return the coordinator payload key backing this control."""
        return self._command

    def _read_device_value(self) -> float | None:
        """Return the latest valid current value from coordinator data."""
        if not (self._updater.available and self._updater.data):
            return None
        if self._command not in self._updater.data:
            return None
        device_value = get_safe_value(self._updater.data, self._command, float)
        if device_value is not None and (
            self._attr_native_min_value <= device_value <= self._attr_native_max_value
        ):
            return float(device_value)
        return None

    def _values_equal(self, optimistic: float, device: float) -> bool:
        """Return whether a device current confirms the optimistic value."""
        return abs(optimistic - device) < 0.5

    def _resolve_display_value(self) -> float | None:
        """Resolve the current display value."""
        return self._resolve_value()

    def _set_display_value(self, value: float | None) -> None:
        """Store the current display value."""
        self._attr_native_value = value

    def _get_pending(self) -> float | None:
        """Return the pending current command sentinel."""
        return self._pending_value

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
            # 0 <= age: a backward wall-clock jump must not extend the grace
            # window indefinitely (mirrors the optimistic-state TTL guard).
            if 0 <= current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with optimistic UI."""
        # Validate before taking the lock so a bad value fails fast without
        # blocking on an in-flight command.
        raw = _validate_finite_number(value, _CHARGING_CURRENT_NAME)
        clamped_value = max(
            self._attr_native_min_value,
            min(self._attr_native_max_value, raw),
        )
        int_value = int(round(clamped_value))

        async with self._command_lock:
            try:
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

            except (HomeAssistantError, ConfigEntryAuthFailed):
                # ConfigEntryAuthFailed must propagate untouched so Home
                # Assistant starts the reauthentication flow on a 401.
                raise
            except Exception as err:
                _LOGGER.debug("Failed to set current value: %s", err, exc_info=True)
                raise HomeAssistantError(
                    f"Failed to set charging current: {err}"
                ) from err
            finally:
                self._pending_value = None
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


class EveusSetpointNumber(EveusNumberEntity):
    """Charger-backed optimistic setpoint number, scaled per description."""

    def __init__(
        self,
        updater,
        description: EveusSetpointNumberDescription,
        device_number: int = 1,
        *,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> None:
        super().__init__(updater, description, device_number)
        self._command = description.command
        self._state_key_value = description.state_key
        self._device_to_ha = description.device_to_ha
        self._ha_to_device = description.ha_to_device
        self._attr_native_min_value = (
            description.native_min_value if min_value is None else min_value
        )
        self._attr_native_max_value = (
            description.native_max_value if max_value is None else max_value
        )
        self._attr_native_step = description.native_step
        self._attr_native_value = self._resolve_value()

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    @property
    def _state_key(self) -> str:
        return self._state_key_value

    def _read_device_value(self) -> float | None:
        if not (self._updater.available and self._updater.data):
            return None
        if self._state_key_value not in self._updater.data:
            return None
        raw = get_safe_value(self._updater.data, self._state_key_value, float)
        if raw is None:
            return None
        value = raw * self._device_to_ha
        if self._attr_native_min_value <= value <= self._attr_native_max_value:
            return float(value)
        return None

    def _values_equal(self, optimistic: float, device: float) -> bool:
        return abs(optimistic - device) < (self._attr_native_step / 2 or 0.5)

    def _resolve_display_value(self) -> float | None:
        return self._resolve_value()

    def _set_display_value(self, value: float | None) -> None:
        self._attr_native_value = value

    def _get_pending(self) -> float | None:
        return self._pending_value

    def _resolve_value(self) -> float | None:
        current_time = time.time()
        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return self._optimistic_value
        device_value = self._read_device_value()
        if device_value is not None:
            return device_value
        if self._last_device_value is not None and (
            0 <= current_time - self._last_successful_read < CONTROL_GRACE_PERIOD
        ):
            return self._last_device_value
        return None

    def _pre_send_refresh(self) -> None:
        """Hook: refresh dynamic bounds just before clamping a queued write.

        No-op for static-bound numbers; overridden where the min/max can shift
        while a command waits for ``_command_lock``.
        """

    async def async_set_native_value(self, value: float) -> None:
        raw = _validate_finite_number(value, self.ENTITY_NAME)
        async with self._command_lock:
            # Clamp INSIDE the lock against a freshly refreshed bound: a write
            # queued behind another command must honour a dynamic min/max that
            # shifted while it waited, not the bound captured at enqueue time.
            self._pre_send_refresh()
            clamped = max(
                self._attr_native_min_value, min(self._attr_native_max_value, raw)
            )
            device_value = int(round(clamped * self._ha_to_device))
            try:
                self._pending_value = clamped
                self._attr_native_value = clamped
                self._write_if_changed(self._attr_native_value)
                success = await self._updater.send_command(self._command, device_value)
                if success:
                    self._set_optimistic_value(clamped)
                else:
                    raise HomeAssistantError(
                        f"Eveus charger did not accept {self.ENTITY_NAME} = {clamped}"
                    )
            except (HomeAssistantError, ConfigEntryAuthFailed):
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Failed to set %s: %s", self.ENTITY_NAME, err, exc_info=True)
                raise HomeAssistantError(f"Failed to set {self.ENTITY_NAME}: {err}") from err
            finally:
                self._pending_value = None
                self._attr_native_value = self._resolve_value()
                self._write_if_changed(self._attr_native_value)

    async def _async_restore_state(self, state: State) -> None:
        try:
            if state and state.state not in (None, "unknown", "unavailable"):
                restored = float(state.state)
                if self._attr_native_min_value <= restored <= self._attr_native_max_value:
                    self._last_device_value = restored
                    self._last_successful_read = time.time()
                    self._attr_native_value = restored
        except (TypeError, ValueError) as err:
            _LOGGER.debug("Could not restore %s: %s", self.ENTITY_NAME, err)


class EveusUndervoltageThresholdNumber(EveusSetpointNumber):
    """Voltage-mode threshold whose lower bound tracks Minimum voltage.

    The charger constrains this setpoint to ``minVoltage + 10`` .. 220 (its web UI
    derives the slider's lower bound the same way). Recompute the lower bound from
    the live ``minVoltage`` on every poll so the picker mirrors the charger; fall
    back to the static description minimum when ``minVoltage`` is unavailable.
    """

    _MIN_VOLTAGE_KEY = "minVoltage"
    _MIN_OFFSET = 10.0
    # Read-acceptance floor: the charger legitimately reports an ``aiVoltage`` BELOW
    # the current write floor (e.g. minVoltage=200 with a stored aiVoltage=190 from
    # an earlier config), so the displayed value must NOT be gated on the dynamic
    # write minimum — only the slider/write range tracks minVoltage+10. Accept any
    # non-negative voltage up to the max instead.
    _READ_MIN = 0.0

    def __init__(self, updater, description, device_number: int = 1) -> None:
        super().__init__(updater, description, device_number)
        # Static floor used whenever the charger hasn't reported minVoltage yet.
        self._floor_min_value = self._attr_native_min_value
        self._refresh_min_bound()
        # Re-resolve now that read-acceptance is decoupled from the write floor.
        self._attr_native_value = self._resolve_value()

    def _read_device_value(self) -> float | None:
        """Accept the charger-reported value across the full read range.

        Deliberately ignores ``native_min_value`` (the dynamic write floor): the
        firmware can report an ``aiVoltage`` below ``minVoltage + 10``, and
        rejecting it would blank the entity for a perfectly valid device value.
        """
        if not (self._updater.available and self._updater.data):
            return None
        if self._state_key_value not in self._updater.data:
            return None
        raw = get_safe_value(self._updater.data, self._state_key_value, float)
        if raw is None:
            return None
        value = raw * self._device_to_ha
        if self._READ_MIN <= value <= self._attr_native_max_value:
            return float(value)
        return None

    async def _async_restore_state(self, state: State) -> None:
        """Restore across the read range, not the (narrower) write floor."""
        try:
            if state and state.state not in (None, "unknown", "unavailable"):
                restored = float(state.state)
                if self._READ_MIN <= restored <= self._attr_native_max_value:
                    self._last_device_value = restored
                    self._last_successful_read = time.time()
                    self._attr_native_value = restored
        except (TypeError, ValueError) as err:
            _LOGGER.debug("Could not restore %s: %s", self.ENTITY_NAME, err)

    def _refresh_min_bound(self) -> None:
        """Set the lower bound to ``minVoltage + 10`` when the charger reports it."""
        dynamic: float | None = None
        if self._updater.available and self._updater.data:
            raw = get_safe_value(self._updater.data, self._MIN_VOLTAGE_KEY, float)
            if raw is not None:
                dynamic = raw + self._MIN_OFFSET
        new_min = dynamic if dynamic is not None else self._floor_min_value
        # Never cross the upper bound — a nonsense minVoltage must not invert the
        # slider range.
        self._attr_native_min_value = min(new_min, self._attr_native_max_value)

    def _pre_send_refresh(self) -> None:
        """Re-derive the dynamic floor before a queued write is clamped/sent."""
        self._refresh_min_bound()

    def _handle_coordinator_update(self) -> None:
        previous_min = self._attr_native_min_value
        self._refresh_min_bound()
        super()._handle_coordinator_update()
        # The base handler only writes HA state when value/availability/attributes
        # change; a shift in the lower bound alone (value unchanged) would leave the
        # frontend showing a stale slider minimum, so push it explicitly.
        if self._attr_native_min_value != previous_min:
            self.async_write_ha_state()


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
            # HA restores native_value without type coercion, so a corrupt
            # stored state must not raise during entity setup.
            try:
                restored = float(last.native_value)
            except (TypeError, ValueError, OverflowError):
                restored = None
            lo, hi = SOC_INPUT_LIMITS[self._soc_key]
            if restored is not None and lo <= restored <= hi:
                self._attr_native_value = restored
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
        raw = _validate_finite_number(value, self.ENTITY_NAME)
        lo, hi = self.native_min_value, self.native_max_value
        clamped = max(lo, min(hi, raw))
        self._attr_native_value = clamped
        self._write_if_changed(clamped)
        self._push()


class EveusInitialSocNumber(EveusSocConfigNumber):
    """Initial state of charge (%)."""

    _soc_key = "initial_soc"
    ENTITY_NAME = "Initial SOC"
    _attr_icon = "mdi:battery-charging-40"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["initial_soc"]
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER


class EveusTargetSocNumber(EveusSocConfigNumber):
    """Target state of charge (%)."""

    _soc_key = "target_soc"
    ENTITY_NAME = "Target SOC"
    _attr_icon = "mdi:battery-charging-high"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["target_soc"]
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER


class EveusBatteryCapacityNumber(EveusSocConfigNumber):
    """Battery capacity (kWh)."""

    _soc_key = "battery_capacity"
    ENTITY_NAME = "Battery Capacity"
    _attr_icon = "mdi:car-battery"
    _attr_native_unit_of_measurement = "kWh"
    _attr_native_min_value, _attr_native_max_value = SOC_INPUT_LIMITS["battery_capacity"]
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX


class EveusSocCorrectionNumber(EveusSocConfigNumber):
    """SOC charging-loss correction (%)."""

    _soc_key = "soc_correction"
    ENTITY_NAME = "SOC Correction"
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
    entities = [
        EveusUndervoltageThresholdNumber(
            updater, UNDERVOLTAGE_THRESHOLD_NUMBER, device_number
        )
    ]
    if model:
        entities.append(EveusCurrentNumber(updater, model, device_number))
        entities += [
            EveusSetpointNumber(updater, desc, device_number)
            for desc in GLOBAL_LIMIT_NUMBERS
        ]
        model_max = float(MODEL_MAX_CURRENT[model])
        for desc in SCHEDULE_LIMIT_NUMBERS:
            max_override = model_max if desc.native_unit_of_measurement == "A" else None
            entities.append(
                EveusSetpointNumber(
                    updater, desc, device_number, max_value=max_override
                )
            )
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
