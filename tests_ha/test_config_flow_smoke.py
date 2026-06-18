"""Home Assistant-native smoke tests for the Eveus config flow.

The main unit suite disables the Home Assistant pytest plugin for speed and
stability. These tests run separately with the plugin enabled and exercise the
real config-entry flow manager, catching onboarding issues that direct
``ConfigFlow()`` unit tests can miss.
"""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.eveus as eveus
from custom_components.eveus import config_flow
from custom_components.eveus.const import (
    CONF_BATTERY_CAPACITY,
    CONF_INITIAL_SOC,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    CONF_TARGET_SOC,
    DEFAULT_INITIAL_SOC,
    DEFAULT_TARGET_SOC,
    DOMAIN,
    MODEL_16A,
    SOC_MODE_ADVANCED,
    SOC_MODE_BASIC,
)

TEST_HOST = "192.168.1.50"  # NOSONAR(python:S1313) - RFC 1918 test fixture
TEST_USERNAME = "test_user"  # NOSONAR(python:S2068) - test fixture
TEST_PASSWORD = "test_password"  # NOSONAR(python:S2068) - test fixture

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _skip_runtime_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep flow smoke tests focused on onboarding, not platform polling."""

    async def fake_setup_entry(_hass, _entry):
        return True

    monkeypatch.setattr(eveus, "async_setup_entry", fake_setup_entry)


def _user_input(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
        CONF_PHASES: 1,
        CONF_SOC_MODE: SOC_MODE_BASIC,
    }
    data.update(overrides)
    return data


def _validated(data: dict[str, object]) -> dict[str, object]:
    normalized = config_flow.normalize_user_input(data)
    return {
        "title": f"Eveus Charger ({normalized[CONF_HOST]})",
        "data": normalized,
        "device_info": {"current_set": 16},
    }


async def test_user_flow_basic_mode_creates_entry_via_ha_flow_manager(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal Basic setup completes through HA's real flow manager."""

    async def fake_validate_input(flow_hass, data):
        assert flow_hass is hass
        return _validated(data)

    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=_user_input(),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Eveus Charger ({TEST_HOST})"
    assert result["data"] == {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
        CONF_SCHEME: "http",
        CONF_PHASES: 1,
        CONF_SOC_MODE: SOC_MODE_BASIC,
    }


async def test_user_flow_advanced_mode_collects_soc_step_via_ha_flow_manager(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Advanced setup shows the SOC form, then creates a complete entry."""

    async def fake_validate_input(flow_hass, data):
        assert flow_hass is hass
        return _validated(data)

    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=_user_input(**{CONF_SOC_MODE: SOC_MODE_ADVANCED}),
    )

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "soc"

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_BATTERY_CAPACITY: 75,
            CONF_SOC_CORRECTION: 9.5,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOC_MODE] == SOC_MODE_ADVANCED
    assert result["data"][CONF_BATTERY_CAPACITY] == 75
    assert result["data"][CONF_SOC_CORRECTION] == 9.5
    assert result["data"][CONF_TARGET_SOC] == DEFAULT_TARGET_SOC
    assert result["data"][CONF_INITIAL_SOC] == DEFAULT_INITIAL_SOC


async def test_user_flow_duplicate_host_aborts_via_ha_flow_manager(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adding the same normalized host twice aborts as already configured."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_HOST,
        data=config_flow.normalize_user_input(_user_input()),
    )
    entry.add_to_hass(hass)

    async def fake_validate_input(flow_hass, data):
        assert flow_hass is hass
        return _validated(data)

    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=_user_input(),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_invalid_auth_returns_form_error_via_ha_flow_manager(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad credentials stay on the user form with the translated auth error."""

    async def fake_validate_input(flow_hass, data):
        assert flow_hass is hass
        raise config_flow.InvalidAuth

    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=_user_input(),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}
