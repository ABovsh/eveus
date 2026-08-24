"""Real-Home-Assistant smoke tests for entry setup, reload and the options flow.

`tests/` runs against a hand-rolled Home Assistant stub for speed, which cannot
catch a Home Assistant release changing the setup, reload or options-flow
contract — something that has already happened once for the config flow. The
existing smoke file covers onboarding only; these cover the rest of the entry
lifecycle.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_MODE,
    DOMAIN,
    MODEL_16A,
    SOC_MODE_BASIC,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# The verbatim live capture the unit suite already pins every getter against.
REAL_MAIN = json.loads(
    (Path(__file__).parent.parent / "tests" / "fixtures" / "real_main_response.json")
    .read_text(encoding="utf-8")
)

_PATCH_TARGET = "custom_components.eveus.common_network.EveusUpdater._async_update_data"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured Basic-mode entry, matching what the user flow produces."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",  # NOSONAR(python:S1313) - RFC 1918 test fixture
            CONF_USERNAME: "test_user",  # NOSONAR(python:S2068) - test fixture
            CONF_PASSWORD: "test_password",  # NOSONAR(python:S2068) - test fixture
            CONF_MODEL: MODEL_16A,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_BASIC,
        },
        options={},
    )
    config_entry.add_to_hass(hass)
    return config_entry


async def test_entry_sets_up_and_creates_entities(hass, entry) -> None:
    """The real HA setup path must load every platform and register entities."""
    with patch(_PATCH_TARGET, return_value=REAL_MAIN):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    created = [
        state.entity_id
        for state in hass.states.async_all()
        if state.entity_id.startswith(("sensor.", "switch.", "number.", "binary_sensor."))
    ]
    # A real Basic-mode entry brings up dozens of entities; a handful would
    # mean a platform silently failed to forward.
    assert len(created) > 20, f"only {len(created)} entities registered: {created}"


async def test_entry_reloads_cleanly(hass, entry) -> None:
    """Reload is what every options/reauth/repair path calls; it must not leak."""
    with patch(_PATCH_TARGET, return_value=REAL_MAIN):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_options_flow_shows_its_form(hass, entry) -> None:
    """The options flow runs through HA's real flow manager, not a bare class."""
    with patch(_PATCH_TARGET, return_value=REAL_MAIN):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"
