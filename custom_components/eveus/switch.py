"""Support for Eveus switches with optimistic UI and safety."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity, RestoredExtraData

from . import EveusConfigEntry
from .common_base import (
    BaseEveusEntity,
    ControlEntityMixin,
    WriteOnChangeMixin,
)
from .control_base import CommandBackedEntity
from .const import (
    CONF_FIRMWARE_FAMILY,
    CONF_FOLLOW_HA_TIME_ZONE,
    EVEUS_FIRMWARE_ONLY_ENTITIES,
    SOC_MODE_ADVANCED,
    get_soc_mode,
)
from .reve import FAMILY_REVE

_LOGGER = logging.getLogger(__name__)

_ICON_ENERGY = "mdi:lightning-bolt"


class EveusSwitchEntityDescription(SwitchEntityDescription, frozen_or_thawed=True):
    """Description for Eveus switch entities."""

    command: str
    state_key: str
    # Sibling form fields written alongside ``command`` in the same request,
    # each set to the same on/off value. Required for settings the firmware
    # only accepts as a bundled "save" form (e.g. OCPP needs ocppVendor).
    command_extra: tuple[str, ...] = ()
    legacy_write_only: bool = False


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
        icon=_ICON_ENERGY,
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
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_enabled",
        name="Schedule 2 Enabled",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        command="sh2Enabled",
        state_key="sh2Enabled",
        legacy_write_only=True,
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
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="limit_time_enabled",
        name="Limit: Time enabled",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.CONFIG,
        command="timeLimitS",
        state_key="timeLimitS",
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="limit_energy_enabled",
        name="Limit: Energy enabled",
        icon=_ICON_ENERGY,
        entity_category=EntityCategory.CONFIG,
        command="energyLimitS",
        state_key="energyLimitS",
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="limit_cost_enabled",
        name="Limit: Cost enabled",
        icon="mdi:cash",
        entity_category=EntityCategory.CONFIG,
        command="moneyLimitS",
        state_key="moneyLimitS",
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="schedule_1_current_limit_enabled",
        name="Schedule 1 Current limit enabled",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        command="sh1CurrentEnable",
        state_key="sh1CurrentEnable",
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="schedule_1_energy_limit_enabled",
        name="Schedule 1 Energy limit enabled",
        icon=_ICON_ENERGY,
        entity_category=EntityCategory.CONFIG,
        command="sh1EnergyEnable",
        state_key="sh1EnergyEnable",
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_current_limit_enabled",
        name="Schedule 2 Current limit enabled",
        icon="mdi:current-ac",
        entity_category=EntityCategory.CONFIG,
        command="sh2CurrentEnable",
        state_key="sh2CurrentEnable",
        legacy_write_only=True,
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_energy_limit_enabled",
        name="Schedule 2 Energy limit enabled",
        icon=_ICON_ENERGY,
        entity_category=EntityCategory.CONFIG,
        command="sh2EnergyEnable",
        state_key="sh2EnergyEnable",
        legacy_write_only=True,
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
        self._last_legacy_command: bool | None = None
        self._init_optimistic_control()
        self._attr_is_on = None
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        """Return cached switch state without side effects (None = unknown)."""
        return self._attr_is_on

    @property
    def _can_assume_legacy_state(self) -> bool:
        return (
            self.entity_description.legacy_write_only
            and getattr(self._updater, "is_legacy_firmware", False) is True
        )

    @property
    def assumed_state(self) -> bool:
        """The 1.x page can write some flags that /main does not report."""
        return self._can_assume_legacy_state and not self._updater.snapshot.has(self._state_key)

    def _write_if_changed(self, value: bool | None) -> bool:
        return super()._write_if_changed((value, self.assumed_state))

    def _set_optimistic_value(self, value: bool) -> None:
        """Remember accepted 1.x commands independently of device readings."""
        if self._can_assume_legacy_state:
            self._last_legacy_command = value
        super()._set_optimistic_value(value)

    def _read_device_value(self) -> bool | None:
        """Return the latest valid switch state from coordinator data.

        "The charger stopped sending this flag" and "it sent something
        unusable" are different answers to the optimistic-write lifecycle, so
        presence is asked of the raw payload and the value of the parse.
        """
        snapshot = self._updater.snapshot
        if not (self._updater.available and snapshot.has(self._state_key)):
            return None
        device_value = snapshot.get_int(self._state_key)
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

    def _set_pending(self, value: bool | None) -> None:
        """Store the pending switch command sentinel."""
        self._pending_command = value

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
        if self.assumed_state:
            return self._last_legacy_command
        value = self._resolve_held_value(self._read_device_value())
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_send_switch_command(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_send_switch_command(0)

    async def _async_send_switch_command(self, command_value: int) -> None:
        """Send the on/off command through the shared pinned-command lifecycle."""
        async with self._command_lock:
            extra = (
                dict.fromkeys(self._command_extra, command_value)
                if self._command_extra
                else None
            )
            await self._send_pinned_command(
                device_value=command_value,
                pending=bool(command_value),
                shown=self.is_on if self.assumed_state else bool(command_value),
                accepted=bool(command_value),
                rejected_message=(
                    f"Eveus charger did not accept '{self.name}' "
                    f"{'on' if command_value else 'off'} command"  # pragma: no mutate - pure exception-message text, command_value VALUE unchanged
                ),
                failure_prefix=f"Failed to set '{self.name}'",  # pragma: no mutate - pure exception-message text, self.name VALUE unchanged
                extra=extra,
            )

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display state only; no commands sent on startup."""
        if self.entity_description.legacy_write_only:
            extra = await self.async_get_last_extra_data()
            last_command = extra.as_dict().get("last_legacy_command") if extra else None
            if isinstance(last_command, bool):
                self._last_legacy_command = last_command
        if state and state.state in ("on", "off"):
            self._last_device_value = state.state == "on"
            self._last_successful_read = time.monotonic()
            self._attr_is_on = self._last_device_value

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Persist a command even if the switch is unavailable at shutdown."""
        if self._last_legacy_command is None:
            return None
        return RestoredExtraData({"last_legacy_command": self._last_legacy_command})


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
        self._attr_is_on = False
        self._was_suspended = False  # pragma: no mutate - equivalent: only ever read via `not self._was_suspended`, where None and False negate identically; always reassigned to a real bool before any other use

    @property
    def available(self) -> bool:
        """Always available — it is a local setting, not charger-backed."""
        return True

    @property
    def is_on(self) -> bool:
        return self._attr_is_on

    @callback
    def _handle_coordinator_update(self) -> None:
        # Turning the master "Disable limits" on switches the SOC limit off too,
        # alongside the charger's own limits. Only on the off->on transition: you
        # can still flip it back on while suspended (the controller ignores the
        # limit meanwhile), and turning the master back off never changes this
        # switch by itself — it stays where you left it (no auto-enable).
        raw = self._updater.snapshot.get_int("suspendLimits")
        if raw not in (0, 1):
            # Unknown/malformed master state — leave the transition memory
            # untouched (tri-state). Collapsing it to False here would make the
            # next valid 1 look like a fresh off->on edge and flip the switch off
            # a second time on an unchanged master.
            super()._handle_coordinator_update()
            return
        suspended = raw == 1
        if suspended and not self._was_suspended and self._attr_is_on:
            self._attr_is_on = False
            self._controller.set_enabled(False)
            self.async_write_ha_state()
        self._was_suspended = suspended
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Seed the suspend-edge memory from the fresh payload that setup polled
        # before adding platforms. Without this it defaults to False, so the
        # first post-reload poll that still reports suspendLimits==1 looks like a
        # fresh off->on edge and silently flips a restored "on" back off — losing
        # a SOC limit the user deliberately re-enabled while limits were disabled.
        raw = self._updater.snapshot.get_int("suspendLimits")
        if raw in (0, 1):
            self._was_suspended = raw == 1
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self._attr_is_on = True
        self._controller.set_enabled(self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self._controller.set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self._controller.set_enabled(False)
        self.async_write_ha_state()


class EveusFollowTimeZoneSwitch(BaseEveusEntity, SwitchEntity):
    """Local switch: when on, keep the charger's Time Zone on Home Assistant's.

    The charger holds one whole-hour offset and never follows daylight saving.
    The choice lives in the config entry (so it survives restarts and restores
    and is the one value the follower reads); the entity only shows and flips
    it. Turning it on makes the Time Zone select refuse manual changes.
    """

    ENTITY_NAME = "Follow HA Time Zone"
    _attr_icon = "mdi:map-clock-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, updater, entry, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._entry = entry

    @property
    def available(self) -> bool:
        """Always available: a setting of the integration, not of the charger."""
        return True

    @property
    def is_on(self) -> bool:
        return bool(self._entry.data.get(CONF_FOLLOW_HA_TIME_ZONE))

    def _store(self, follow: bool) -> None:
        # Rebase on the LIVE entry data: a reconfigure finishing meanwhile must
        # not be rolled back. No reload: the integration registers no update
        # listener, and the follower reads this on every poll.
        data = dict(self._entry.data)
        if follow:
            data[CONF_FOLLOW_HA_TIME_ZONE] = True
        else:
            data.pop(CONF_FOLLOW_HA_TIME_ZONE, None)
        self.hass.config_entries.async_update_entry(self._entry, data=data)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._store(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._store(False)  # pragma: no mutate - _store only tests truthiness; None is falsy too


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    skipped = (
        {key for platform, key in EVEUS_FIRMWARE_ONLY_ENTITIES if platform == "switch"}
        if entry.data.get(CONF_FIRMWARE_FAMILY) == FAMILY_REVE
        else set()
    )
    entities = [
        BaseSwitchEntity(updater, description, device_number)
        for description in SWITCH_DESCRIPTIONS
        if description.key not in skipped
    ]
    if get_soc_mode(entry) == SOC_MODE_ADVANCED:
        entities.append(
            EveusSocLimitSwitch(updater, runtime_data.soc_limit, device_number)
        )
    entities.append(EveusFollowTimeZoneSwitch(updater, entry, device_number))
    async_add_entities(entities)
