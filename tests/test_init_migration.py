"""Unit tests for Eveus config entry migration."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.const import CONF_HOST

from conftest import TEST_BASE_URL, TEST_HOST
from custom_components.eveus import CONFIG_ENTRY_VERSION, async_migrate_entry
from custom_components.eveus.const import CONF_PHASES, CONF_SCHEME, DEFAULT_PHASES


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
        data={CONF_HOST: TEST_HOST, CONF_SCHEME: "http", CONF_PHASES: 1},
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
        data={CONF_HOST: TEST_HOST, CONF_SCHEME: "http", CONF_PHASES: 1},
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
            },
            "unique_id": "http://bad host name/main",
            "title": "Eveus Charger (http://bad host name/main)",
            "version": CONFIG_ENTRY_VERSION,
        }
    ]
