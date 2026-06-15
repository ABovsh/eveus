"""Support for Eveus switches with optimistic UI and safety."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EveusConfigEntry
from .common_base import (
    BaseEveusEntity,
    ControlEntityMixin,
    WriteOnChangeMixin,
)
from .control_base import CommandBackedEntity
from .const import (
    CONTROL_GRACE_PERIOD,
    OPTIMISTIC_CONTROL_TTL,
    SOC_MODE_ADVANCED,
    get_soc_mode,
)
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)


class EveusSwitchEntityDescription(SwitchEntityDescription, frozen_or_thawed=True):
    """Description for Eveus switch entities."""

    command: str
    state_key: str
    # Sibling form fields written alongside ``command`` in the same request,
    # each set to the same on/off value. Required for settings the firmware
    # only accepts as a bundled "save" form (e.g. OCPP needs ocppVendor).
    command_extra: tuple[str, ...] = ()


SWITCH_DESCRIPTIONS: tuple[EveusSwitchEntityDescription, ...] = (
    EveusSwitchEntityDescription(
        key="stop_charging",
        name="Stop Charging",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        command="evseEnabled",
        state_key="evseEnabled",
    ),
    EveusSwitchEntityDescription(
        key="one_charge",
        name="One Charge",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command="oneCharge",
        state_key="oneCharge",
    ),
    EveusSwitchEntityDescription(
        key="schedule_1_enabled",
        name="Schedule 1 Enabled",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        command="sh1Enabled",
        state_key="sh1Enabled",
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_enabled",
        name="Schedule 2 Enabled",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        command="sh2Enabled",
        state_key="sh2Enabled",
    ),
    EveusSwitchEntityDescription(
        key="ground_protection",
        name="Ground Protection",
        icon="mdi:shield-check",
        entity_category=EntityCategory.CONFIG,
        command="groundCtrl",
        state_key="groundCtrl",
    ),
    EveusSwitchEntityDescription(
        key="ocpp",
        name="Connect to OCPP",
        icon="mdi:cloud-sync",
        entity_category=EntityCategory.CONFIG,
        command="ocppEnabled",
        state_key="ocppEnabled",
        command_extra=("ocppVendor",),
    ),
    EveusSwitchEntityDescription(
        key="limit_disable_all",
        name="Limit: disable all",
        icon="mdi:cancel",
        entity_category=EntityCategory.CONFIG,
        command="suspendLimits",
        state_key="suspendLimits",
    ),
    EveusSwitchEntityDescription(
        key="limit_time_enabled",
        name="Limit: Time enabled",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.CONFIG,
        command="timeLimitS",
        state_key="timeLimitS",
    ),
    EveusSwitchEntityDescription(
        key="limit_energy_enabled",
        name="Limit: Energy enabled",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command="energyLimitS",
        state_key="energyLimitS",
    ),
    EveusSwitchEntityDescription(
        key="limit_cost_enabled",
        name="Limit: Cost enabled",
        icon="mdi:cash",
        entity_category=EntityCategory.CONFIG,
        command="moneyLimitS",
        state_key="moneyLimitS",
    ),
    EveusSwitchEntityDescription(
        key="schedule_1_current_limit_enabled",
        name="Schedule 1 Current limit enabled",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        command="sh1CurrentEnable",
        state_key="sh1CurrentEnable",
    ),
    EveusSwitchEntityDescription(
        key="schedule_1_energy_limit_enabled",
        name="Schedule 1 Energy limit enabled",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command="sh1EnergyEnable",
        state_key="sh1EnergyEnable",
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_current_limit_enabled",
        name="Schedule 2 Current limit enabled",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        command="sh2CurrentEnable",
        state_key="sh2CurrentEnable",
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_energy_limit_enabled",
        name="Schedule 2 Energy limit enabled",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command="sh2EnergyEnable",
        state_key="sh2EnergyEnable",
    ),
)


class BaseSwitchEntity(
    WriteOnChangeMixin,
    ControlEntityMixin,
    CommandBackedEntity[bool],
    SwitchEntity,
):
    """Description-driven switch entity with optimistic UI state."""

    _control_entity_label = "Switch"

    def __init__(
        self,
        updater,
        entity_description: EveusSwitchEntityDescription,
        device_number: int = 1,
    ) -> None:
        """Initialize the switch."""
        self.entity_description = entity_description
        self.ENTITY_NAME = entity_description.name
        super().__init__(updater, device_number)
        self._command = entity_description.command
        self._state_key = entity_description.state_key
        self._command_extra = entity_description.command_extra
        self._pending_command: bool | None = None
        self._init_optimistic_control()
        self._attr_is_on = None
        self._init_write_on_change()

    @property
    def _optimistic_state(self) -> bool | None:
        """Test-facing alias for the canonical _optimistic_value attribute."""
        return self._optimistic_value

    @_optimistic_state.setter
    def _optimistic_state(self, value: bool | None) -> None:
        self._optimistic_value = value

    @property
    def _optimistic_state_time(self) -> float:
        """Test-facing alias for the canonical _optimistic_value_time attribute."""
        return self._optimistic_value_time

    @_optimistic_state_time.setter
    def _optimistic_state_time(self, value: float) -> None:
        self._optimistic_value_time = value

    @property
    def _last_device_state(self) -> bool | None:
        """Test-facing alias for the canonical _last_device_value attribute."""
        return self._last_device_value

    @_last_device_state.setter
    def _last_device_state(self, value: bool | None) -> None:
        self._last_device_value = value

    @property
    def is_on(self) -> bool | None:
        """Return cached switch state without side effects (None = unknown)."""
        return self._attr_is_on

    def _read_device_value(self) -> bool | None:
        """Return the latest valid switch state from coordinator data."""
        if not (
            self._updater.available
            and self._updater.data
            and self._state_key in self._updater.data
        ):
            return None
        device_value = get_safe_value(self._updater.data, self._state_key, int)
        if device_value in (0, 1):
            return bool(device_value)
        return None

    def _values_equal(self, optimistic: bool, device: bool) -> bool:
        """Return whether a device switch state confirms the optimistic value."""
        return optimistic == device

    def _resolve_display_value(self) -> bool | None:
        """Resolve the switch display value."""
        return self._resolve_state()

    def _set_display_value(self, value: bool | None) -> None:
        """Store the switch display value."""
        self._attr_is_on = value

    def _get_pending(self) -> bool | None:
        """Return the pending switch command sentinel."""
        return self._pending_command

    async def async_added_to_hass(self) -> None:
        """Resolve the initial state after restore/coordinator data is available."""
        await super().async_added_to_hass()
        self._attr_is_on = self._resolve_state()

    def _resolve_state(self) -> bool | None:
        """Resolve switch state from optimistic, device, and restore state.

        Returns None (unknown) rather than a definite ``off`` when no trusted
        source is available, so a missing/invalid payload field is not exposed
        as a real ``off`` state that automations could act on.
        """
        current_time = time.time()

        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return bool(self._optimistic_value)

        if self._updater.available and self._updater.data and self._state_key in self._updater.data:
            device_value = get_safe_value(self._updater.data, self._state_key, int)
            if device_value in (0, 1):
                return bool(device_value)

        if self._last_device_value is not None:
            if 0 <= current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_send_command_or_raise(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_send_command_or_raise(0)

    async def _async_send_command(self, command_value: int) -> bool:
        """Send command with optimistic state."""
        async with self._command_lock:
            self._pending_command = bool(command_value)
            self._attr_is_on = self._pending_command
            self._write_if_changed(self._attr_is_on)

            try:
                extra = (
                    dict.fromkeys(self._command_extra, command_value)
                    if self._command_extra
                    else None
                )
                success = await self._updater.send_command(
                    self._command, command_value, extra=extra
                )
                if success:
                    self._set_optimistic_value(bool(command_value))
                return success
            finally:
                self._pending_command = None
                self._attr_is_on = self._resolve_state()
                self._write_if_changed(self._attr_is_on)

    async def _async_send_command_or_raise(self, command_value: int) -> None:
        """Send command and raise HomeAssistantError on failure so HA shows a toast."""
        success = await self._async_send_command(command_value)
        if not success:
            raise HomeAssistantError(
                f"Eveus charger did not accept '{self.name}' "
                f"{'on' if command_value else 'off'} command"
            )

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display state only; no commands sent on startup."""
        if state and state.state in ("on", "off"):
            self._last_device_value = state.state == "on"
            self._last_successful_read = time.time()
            self._attr_is_on = self._last_device_value


class EveusSocLimitSwitch(BaseEveusEntity, RestoreEntity, SwitchEntity):
    """Local switch: when on, stop charging at Target SOC (Advanced mode).

    Holds its own state (the charger has no SOC field) and pushes it to the
    SocLimitController, which performs the stop via the existing Stop Charging
    command. Persists across restarts.
    """

    ENTITY_NAME = "Limit: SOC enabled"
    _attr_icon = "mdi:battery-charging-high"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, updater, controller, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._controller = controller
        self._enabled_intent = False
        self._suspended = False

    @property
    def available(self) -> bool:
        """Always available — it is a local setting, not charger-backed."""
        return True

    def _read_suspended(self) -> bool:
        data = self._updater.data
        return isinstance(data, dict) and get_safe_value(data, "suspendLimits", int) == 1

    @property
    def is_on(self) -> bool:
        # Shown off while the master "Disable limits" is on — the same way that
        # switch suppresses the charger's own Time/Energy/Cost limits. The user's
        # choice is preserved and returns once the master is switched back off.
        return self._enabled_intent and not self._suspended

    @callback
    def _handle_coordinator_update(self) -> None:
        suspended = self._read_suspended()
        if suspended != self._suspended:
            self._suspended = suspended
            self.async_write_ha_state()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self._enabled_intent = True
        self._suspended = self._read_suspended()
        self._controller.set_enabled(self._enabled_intent)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._enabled_intent = True
        self._controller.set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._enabled_intent = False
        self._controller.set_enabled(False)
        self.async_write_ha_state()


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    entities = [
        BaseSwitchEntity(updater, description, device_number)
        for description in SWITCH_DESCRIPTIONS
    ]
    if get_soc_mode(entry) == SOC_MODE_ADVANCED:
        entities.append(
            EveusSocLimitSwitch(updater, runtime_data.soc_limit, device_number)
        )
    async_add_entities(entities)
