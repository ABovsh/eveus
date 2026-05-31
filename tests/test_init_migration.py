"""Unit tests for Eveus config entry migration."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_HOST

from conftest import TEST_BASE_URL, TEST_HOST
import custom_components.eveus as eveus
from custom_components.eveus import CONFIG_ENTRY_VERSION, async_migrate_entry
from custom_components.eveus.const import (
    CONF_BATTERY_CAPACITY,
    CONF_INITIAL_SOC,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    CONF_TARGET_SOC,
    DEFAULT_PHASES,
    SOC_MODE_ADVANCED,
    SOC_MODE_BASIC,
)


@pytest.fixture(autouse=True)
def _no_legacy_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """No legacy input_number helpers registered -> migration picks Basic mode."""

    class _EmptyRegistry:
        def async_get(self, entity_id: str) -> object | None:
            return None

    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistry())


class _ConfigEntries:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


def test_migrate_entry_normalizes_host_and_bumps_version() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: f"{TEST_BASE_URL}/main"},
        unique_id=f"{TEST_BASE_URL}/main",
        title=f"Eveus Charger ({TEST_BASE_URL}/main)",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == [
        {
            "data": {
                CONF_HOST: TEST_HOST,
                CONF_SCHEME: "http",
                CONF_PHASES: DEFAULT_PHASES,
                CONF_SOC_MODE: SOC_MODE_BASIC,
            },
            "unique_id": TEST_HOST,
            "title": f"Eveus Charger ({TEST_HOST})",
            "version": CONFIG_ENTRY_VERSION,
        }
    ]


def test_migrate_entry_only_bumps_old_version_when_data_is_current() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={
            CONF_HOST: TEST_HOST,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_BASIC,
        },
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == [{"version": CONFIG_ENTRY_VERSION}]


def test_migrate_entry_leaves_current_entries_unchanged() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={
            CONF_HOST: TEST_HOST,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_BASIC,
        },
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=CONFIG_ENTRY_VERSION,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == []


def test_migrate_entry_adds_default_scheme_to_current_host_data() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: TEST_HOST},
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=CONFIG_ENTRY_VERSION,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == [
        {
            "data": {
                CONF_HOST: TEST_HOST,
                CONF_SCHEME: "http",
                CONF_PHASES: DEFAULT_PHASES,
                CONF_SOC_MODE: SOC_MODE_BASIC,
            },
            "unique_id": TEST_HOST,
            "title": f"Eveus Charger ({TEST_HOST})",
        }
    ]


def test_migrate_entry_bumps_version_even_if_old_url_is_invalid() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: "http://bad host name/main"},
        unique_id="http://bad host name/main",
        title="Eveus Charger (http://bad host name/main)",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == [
        {
            "data": {
                CONF_HOST: "http://bad host name/main",
                CONF_SCHEME: "http",
                CONF_PHASES: DEFAULT_PHASES,
                CONF_SOC_MODE: SOC_MODE_BASIC,
            },
            "unique_id": "http://bad host name/main",
            "title": "Eveus Charger (http://bad host name/main)",
            "version": CONFIG_ENTRY_VERSION,
        }
    ]


class _LegacyRegistry:
    """Registry that reports the legacy input_number SOC helpers present."""

    def async_get(self, entity_id: str) -> object | None:
        if entity_id.startswith("input_number.ev_"):
            return object()
        return None


class _States:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, entity_id: str) -> object | None:
        if entity_id in self._values:
            return SimpleNamespace(state=self._values[entity_id])
        return None


def test_migrate_entry_seeds_all_four_soc_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _LegacyRegistry())
    config_entries = _ConfigEntries()
    states = _States(
        {
            "input_number.ev_initial_soc": "65",
            "input_number.ev_target_soc": "95",
            "input_number.ev_battery_capacity": "64",
            "input_number.ev_soc_correction": "9",
        }
    )
    hass = SimpleNamespace(config_entries=config_entries, states=states)
    entry = SimpleNamespace(
        data={CONF_HOST: TEST_HOST, CONF_SCHEME: "http", CONF_PHASES: DEFAULT_PHASES},
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=3,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    data = config_entries.calls[0]["data"]
    assert config_entries.calls[0]["version"] == CONFIG_ENTRY_VERSION
    assert data[CONF_SOC_MODE] == SOC_MODE_ADVANCED
    assert data[CONF_INITIAL_SOC] == 65
    assert data[CONF_TARGET_SOC] == 95
    assert data[CONF_BATTERY_CAPACITY] == 64
    assert data[CONF_SOC_CORRECTION] == 9


def test_migrate_entry_clamps_out_of_range_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _LegacyRegistry())
    config_entries = _ConfigEntries()
    states = _States(
        {
            "input_number.ev_initial_soc": "20",
            "input_number.ev_target_soc": "80",
            "input_number.ev_battery_capacity": "0",
            "input_number.ev_soc_correction": "7.5",
        }
    )
    hass = SimpleNamespace(config_entries=config_entries, states=states)
    entry = SimpleNamespace(
        data={CONF_HOST: TEST_HOST, CONF_SCHEME: "http", CONF_PHASES: DEFAULT_PHASES},
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=3,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    data = config_entries.calls[0]["data"]
    assert data[CONF_BATTERY_CAPACITY] == 10  # clamped from 0 to min
