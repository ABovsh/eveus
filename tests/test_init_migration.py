"""Unit tests for Eveus config entry migration."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.const import CONF_HOST

from custom_components.eveus import CONFIG_ENTRY_VERSION, async_migrate_entry


class _ConfigEntries:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


def test_migrate_entry_normalizes_host_and_bumps_version() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: "http://192.168.1.50/main"},
        unique_id="http://192.168.1.50/main",
        title="Eveus Charger (http://192.168.1.50/main)",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == [
        {
            "data": {CONF_HOST: "192.168.1.50"},
            "unique_id": "192.168.1.50",
            "title": "Eveus Charger (192.168.1.50)",
            "version": CONFIG_ENTRY_VERSION,
        }
    ]


def test_migrate_entry_only_bumps_old_version_when_data_is_current() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: "192.168.1.50"},
        unique_id="192.168.1.50",
        title="Eveus Charger (192.168.1.50)",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == [{"version": CONFIG_ENTRY_VERSION}]


def test_migrate_entry_leaves_current_entries_unchanged() -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: "192.168.1.50"},
        unique_id="192.168.1.50",
        title="Eveus Charger (192.168.1.50)",
        version=CONFIG_ENTRY_VERSION,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert config_entries.calls == []


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

    assert config_entries.calls == [{"version": CONFIG_ENTRY_VERSION}]
