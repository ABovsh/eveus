"""Repair flows for Eveus."""
from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .config_flow import (
    CannotConnect,
    InvalidAuth,
    InvalidDevice,
    InvalidInput,
    build_user_data_schema,
    validate_input,
)
from .const import DOMAIN


class InvalidConfigRepairFlow(RepairsFlow):
    """Repair invalid stored Eveus setup data."""

    def __init__(
        self,
        hass: HomeAssistant,
        issue_id: str,
        entry_id: str | None,
    ) -> None:
        """Initialize the repair flow."""
        self.hass = hass
        self._issue_id = issue_id
        self._entry_id = entry_id

    def _get_entry(self):
        """Return the config entry being repaired, if it still exists."""
        if not self._entry_id:
            return None
        return self.hass.config_entries.async_get_entry(self._entry_id)

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> data_entry_flow.FlowResult:
        """Start the repair flow."""
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> data_entry_flow.FlowResult:
        """Collect valid charger setup details and reload the entry."""
        entry = self._get_entry()
        if entry is None:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            return self.async_abort(reason="entry_missing")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                entry_data = info["data"]

                self.hass.config_entries.async_update_entry(
                    entry,
                    data=entry_data,
                    title=info["title"],
                    unique_id=entry_data[CONF_HOST],
                )
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput:
                errors["base"] = "invalid_input"
            except InvalidDevice:
                errors["base"] = "invalid_device"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="confirm",
            data_schema=build_user_data_schema(entry.data),
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a repair flow for a fixable Eveus issue."""
    entry_id = str(data["entry_id"]) if data and data.get("entry_id") else None
    return InvalidConfigRepairFlow(hass, issue_id, entry_id)
