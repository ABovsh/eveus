"""Device triggers for the automation UI, backed by the coordinator bus events."""
from __future__ import annotations

from typing import Any, Final

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_CAR_CONNECTED,
    EVENT_CAR_DISCONNECTED,
    EVENT_CHARGING_FINISHED,
    EVENT_CHARGING_STARTED,
    EVENT_ERROR,
)

_EVENT_FOR_TYPE: Final[dict[str, str]] = {
    "charging_started": EVENT_CHARGING_STARTED,
    "charging_finished": EVENT_CHARGING_FINISHED,
    "error_occurred": EVENT_ERROR,
    "car_connected": EVENT_CAR_CONNECTED,
    "car_disconnected": EVENT_CAR_DISCONNECTED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(_EVENT_FOR_TYPE)}
)


def _device_number_for(hass: HomeAssistant, device_id: str) -> int:
    """Resolve the config entry's device number for a registry device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            runtime = getattr(entry, "runtime_data", None)
            number = getattr(runtime, "device_number", None)
            if number:
                return number
            # runtime_data is None while the entry is mid-setup-retry; the
            # stored device number is still available in entry.data, so a
            # trigger attached for charger #2 must not silently filter on
            # charger #1's events. Same coercion async_setup_entry applies:
            # int(), bool excluded, only values >= 1 are valid.
            raw = entry.data.get("device_number")
            if not isinstance(raw, bool):
                try:
                    stored = int(raw)
                except (TypeError, ValueError):
                    stored = None
                if stored is not None and stored >= 1:
                    return stored
    return 1


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List the triggers offered for an Eveus device."""
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in _EVENT_FOR_TYPE
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach the underlying event trigger scoped to this device."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: _EVENT_FOR_TYPE[config[CONF_TYPE]],
            event_trigger.CONF_EVENT_DATA: {
                "device_number": _device_number_for(hass, config[CONF_DEVICE_ID])
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
