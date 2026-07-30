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


@pytest.fixture(autouse=True)
def _stub_identifier_migration(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record (old_host, new_host) device-identifier migrations during migrate.

    `async_migrate_entry` lazily imports `migrate_device_identifiers` from
    config_flow; patch it at the source so the lazy import picks up the stub and
    the SimpleNamespace `hass` never reaches the real device registry.
    """
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda hass, entry, old_host, new_host: calls.append((old_host, new_host)),
    )
    return calls


class _ConfigEntries:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def async_entries(self, _domain=None):
        return []

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


def test_migrate_entry_migrates_device_identifiers_on_host_change(
    _stub_identifier_migration: list[tuple[str, str]],
) -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    old_host = f"{TEST_BASE_URL}/main"
    entry = SimpleNamespace(
        data={CONF_HOST: old_host},
        unique_id=old_host,
        title=f"Eveus Charger ({old_host})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    # The device's identifiers follow the canonicalized host, so the device
    # (area, custom name, dashboard refs) isn't orphaned on next load.
    assert _stub_identifier_migration == [(old_host, TEST_HOST)]


def test_migrate_entry_no_identifier_migration_when_host_unchanged(
    _stub_identifier_migration: list[tuple[str, str]],
) -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: TEST_HOST, CONF_SCHEME: "http"},
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    assert _stub_identifier_migration == []


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


def test_migrate_entry_invalid_url_warning_logs_real_entry_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: "http://bad host name/main"},
        unique_id="http://bad host name/main",
        title="Eveus Charger (http://bad host name/main)",
        version=1,
        entry_id="entry-xyz",
    )

    with caplog.at_level("WARNING"):
        assert asyncio.run(async_migrate_entry(hass, entry)) is True

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].args == ("entry-xyz",)


def test_migrate_entry_invalid_url_warning_falls_back_to_unknown_without_entry_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    # No `entry_id` attribute at all -- the getattr default must be used verbatim.
    entry = SimpleNamespace(
        data={CONF_HOST: "http://bad host name/main"},
        unique_id="http://bad host name/main",
        title="Eveus Charger (http://bad host name/main)",
        version=1,
    )

    with caplog.at_level("WARNING"):
        assert asyncio.run(async_migrate_entry(hass, entry)) is True

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert warnings[0].args == ("<unknown>",)


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


class _FakeRegistry:
    """Records which entities were removed; pretends every entity exists."""

    def __init__(self) -> None:
        self.removed: list[str] = []

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str:
        return f"{platform}.{unique_id}"

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


def _prune(monkeypatch, device_number, soc_mode, phases) -> list[str]:
    from custom_components import eveus

    reg = _FakeRegistry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), device_number, soc_mode, phases)
    return reg.removed


def test_prune_removes_soc_and_phase_orphans_when_reduced(monkeypatch) -> None:
    from custom_components import eveus

    removed = _prune(monkeypatch, 1, eveus.SOC_MODE_BASIC, 1)
    assert "number.eveus_initial_soc" in removed
    assert "sensor.eveus_soc_energy" in removed
    assert "sensor.eveus_charging_finish_time" in removed
    assert "sensor.eveus_current_phase_2" in removed
    assert "sensor.eveus_voltage_phase_3" in removed


def test_prune_keeps_everything_in_advanced_three_phase(monkeypatch) -> None:
    from custom_components import eveus

    # Only retired entities go — no mode/phase-scoped entity is pruned.
    assert _prune(monkeypatch, 1, eveus.SOC_MODE_ADVANCED, 3) == [
        "sensor.eveus_system_time",
        "switch.eveus_adaptive_mode",
        "number.eveus_minimum_voltage",
        "sensor.eveus_adaptive_voltage_threshold",
    ]


def test_prune_respects_device_suffix_and_keeps_phases_when_three(monkeypatch) -> None:
    from custom_components import eveus

    removed = _prune(monkeypatch, 2, eveus.SOC_MODE_BASIC, 3)
    assert "number.eveus2_initial_soc" in removed
    # phases == 3 keeps the per-phase sensors.
    assert all("phase" not in entity_id for entity_id in removed)


class _ConfigEntriesRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def async_entries(self, _domain=None):
        return []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _EmptyRegistryMigration:
    def async_get(self, entity_id: str) -> object | None:
        return None


def test_migration_strips_main_path_for_uppercase_scheme(monkeypatch) -> None:
    from custom_components import eveus
    from custom_components.eveus.const import CONF_SCHEME

    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistryMigration())
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )

    config_entries = _ConfigEntriesRecorder()
    hass = SimpleNamespace(config_entries=config_entries)
    legacy = f"HTTP://{TEST_HOST}/main"
    entry = SimpleNamespace(
        data={CONF_HOST: legacy},
        unique_id=legacy,
        title=f"Eveus Charger ({legacy})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    data = config_entries.calls[0]["data"]
    assert data[CONF_HOST] == TEST_HOST
    assert data[CONF_SCHEME] == "http"
    assert config_entries.calls[0]["version"] == CONFIG_ENTRY_VERSION


def test_migration_scrubs_url_credentials(monkeypatch) -> None:
    from custom_components import eveus
    from custom_components.eveus.const import CONF_SCHEME

    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistryMigration())
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )

    config_entries = _ConfigEntriesRecorder()
    hass = SimpleNamespace(config_entries=config_entries)
    legacy = f"http://user:secret@{TEST_HOST}/main"  # NOSONAR(python:S5332,python:S2068)
    entry = SimpleNamespace(
        data={CONF_HOST: legacy},
        unique_id=legacy,
        title=f"Eveus ({legacy})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    kwargs = config_entries.calls[0]
    assert kwargs["data"][CONF_HOST] == TEST_HOST
    assert kwargs["data"][CONF_SCHEME] == "http"
    assert "secret" not in kwargs["title"]
    assert "user" not in kwargs["title"]


class _MigrationEntries:
    def __init__(self, others=None) -> None:
        self.updates: list[dict] = []
        self._others = others or []

    def async_entries(self, _domain=None):
        return self._others

    def async_update_entry(self, _entry, **kwargs) -> None:
        self.updates.append(kwargs)


def _migration_hass(entries):
    return SimpleNamespace(
        config_entries=entries,
        states=SimpleNamespace(get=lambda _eid: None),
    )


def test_migration_canonicalizes_bare_uppercase_host(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )
    entries = _MigrationEntries()
    entry = SimpleNamespace(
        entry_id="e1",
        data={"host": "MYCHARGER.LOCAL.", "soc_mode": "basic"},
        unique_id="MYCHARGER.LOCAL.",
        title="Eveus Charger (MYCHARGER.LOCAL.)",
        version=1,
    )
    assert asyncio.run(async_migrate_entry(_migration_hass(entries), entry))

    (update,) = entries.updates
    assert update["data"]["host"] == "mycharger.local"
    assert update["unique_id"] == "mycharger.local"
    assert "mycharger.local" in update["title"]


def test_migration_skips_unique_id_rewrite_on_collision() -> None:
    other = SimpleNamespace(entry_id="e2", unique_id="mycharger.local")
    entries = _MigrationEntries([other])
    entry = SimpleNamespace(
        entry_id="e1",
        data={"host": "MYCHARGER.LOCAL", "soc_mode": "basic"},
        unique_id="MYCHARGER.LOCAL",
        title="Eveus Charger",
        version=1,
    )
    assert asyncio.run(async_migrate_entry(_migration_hass(entries), entry))

    (update,) = entries.updates
    assert update["data"]["host"] == "mycharger.local"
    assert "unique_id" not in update


def test_migration_survives_missing_host_and_unique_id() -> None:
    from custom_components.eveus.const import CONF_SOC_MODE, SOC_MODE_ADVANCED

    updated: dict = {}

    class _Entries:
        def async_update_entry(self, entry, **kwargs):
            updated.update(kwargs)

    hass = SimpleNamespace(config_entries=_Entries())
    entry = SimpleNamespace(
        data={CONF_SOC_MODE: SOC_MODE_ADVANCED},
        unique_id=None,
        title="Eveus",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True
    assert "unique_id" not in updated


def test_resolve_phases_flags_invalid_values() -> None:
    from custom_components.eveus import _resolve_phases

    assert _resolve_phases(3) == (3, False)
    assert _resolve_phases("3") == (3, False)
    assert _resolve_phases(1) == (1, False)
    assert _resolve_phases("garbage") == (1, True)
    assert _resolve_phases(2) == (1, True)
    assert _resolve_phases(None) == (1, True)
    # bool is an int subclass (int(True) == 1); must still be flagged invalid,
    # not silently accepted as a valid one-phase config.
    assert _resolve_phases(True) == (DEFAULT_PHASES, True)
    assert _resolve_phases(False) == (DEFAULT_PHASES, True)


def test_advanced_only_entities_exact_contents() -> None:
    from custom_components.eveus import _ADVANCED_ONLY_ENTITIES

    assert _ADVANCED_ONLY_ENTITIES == (
        ("sensor", "soc_energy"),
        ("sensor", "soc_percent"),
        ("sensor", "time_to_target_soc"),
        ("sensor", "charging_finish_time"),
        ("sensor", "energy_to_target_soc"),
        ("sensor", "cost_to_target_soc"),
        ("number", "initial_soc"),
        ("number", "target_soc"),
        ("number", "battery_capacity"),
        ("number", "soc_correction"),
        ("switch", "limit_soc_enabled"),
    )


def test_three_phase_only_entities_exact_contents() -> None:
    from custom_components.eveus import _THREE_PHASE_ONLY_ENTITIES

    assert _THREE_PHASE_ONLY_ENTITIES == (
        ("sensor", "current_phase_2"),
        ("sensor", "current_phase_3"),
        ("sensor", "voltage_phase_2"),
        ("sensor", "voltage_phase_3"),
    )


def test_migrate_entry_skips_url_normalization_for_empty_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/falsy host must skip the whole URL-normalization block, not
    just fail to change the host -- observable via zero warning calls."""
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(eveus._LOGGER, "warning", lambda *a, **k: warnings.append(a))
    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    entry = SimpleNamespace(
        data={CONF_HOST: ""},
        unique_id=None,
        title="Eveus",
        version=CONFIG_ENTRY_VERSION,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True
    assert warnings == []


def test_migration_strips_path_for_https_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistryMigration())
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )
    config_entries = _ConfigEntriesRecorder()
    hass = SimpleNamespace(config_entries=config_entries)
    legacy = f"https://{TEST_HOST}/main"
    entry = SimpleNamespace(
        data={CONF_HOST: legacy},
        unique_id=legacy,
        title=f"Eveus Charger ({legacy})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    data = config_entries.calls[0]["data"]
    assert data[CONF_HOST] == TEST_HOST
    assert data[CONF_SCHEME] == "https"


def test_migration_scrubs_username_only_credentials_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Username-only (no password) legacy credentials must still be
    stripped, and the sanitized host must be clean -- no urlunparse params
    artifact leaking into the persisted host."""
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistryMigration())
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )
    config_entries = _ConfigEntriesRecorder()
    hass = SimpleNamespace(config_entries=config_entries)
    legacy = f"http://onlyuser@{TEST_HOST}"  # NOSONAR(python:S5332,python:S2068)
    entry = SimpleNamespace(
        data={CONF_HOST: legacy},
        unique_id=legacy,
        title=f"Eveus ({legacy})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    data = config_entries.calls[0]["data"]
    assert data[CONF_HOST] == TEST_HOST


def test_migration_canonicalizes_unique_id_when_other_entries_dont_collide() -> None:
    """Another entry existing (with a genuinely different unique_id) must not
    block this entry's own unique_id canonicalization."""
    other = SimpleNamespace(entry_id="e2", unique_id="some-other-charger.local")
    entries = _MigrationEntries([other])
    entry = SimpleNamespace(
        entry_id="e1",
        data={"host": "MYCHARGER.LOCAL", "soc_mode": "basic"},
        unique_id="MYCHARGER.LOCAL",
        title="Eveus Charger",
        version=1,
    )
    assert asyncio.run(async_migrate_entry(_migration_hass(entries), entry))

    (update,) = entries.updates
    assert update["unique_id"] == "mycharger.local"


def test_async_remove_entry_deletes_all_per_entry_issues(monkeypatch) -> None:
    from custom_components import eveus

    deleted: list[str] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    entry = SimpleNamespace(entry_id="e1")
    asyncio.run(eveus.async_remove_entry(object(), entry))

    for expected in (
        "invalid_config_e1",
        "ocpp_enabled_e1",
        "battery_low_e1",
        "clock_drift_e1",
        "soc_dashboard_update_e1",
    ):
        assert expected in deleted, expected
    from custom_components.eveus.safety import POLICIES
    for policy in POLICIES:
        assert f"safety_{policy.key}_e1" in deleted, policy.key


def test_advanced_only_prune_list_covers_target_soc_forecast_sensors() -> None:
    from custom_components.eveus import _ADVANCED_ONLY_ENTITIES

    assert ("sensor", "energy_to_target_soc") in _ADVANCED_ONLY_ENTITIES
    assert ("sensor", "cost_to_target_soc") in _ADVANCED_ONLY_ENTITIES


def test_session_limit_number_removed() -> None:
    from custom_components.eveus import number as number_mod

    assert not hasattr(number_mod, "SESSION_LIMIT_DESCRIPTIONS")
    assert not hasattr(number_mod, "EveusSessionLimitNumber")


def test_limit_reached_binary_sensors_removed() -> None:
    from custom_components.eveus import binary_sensor as bs

    assert not hasattr(bs, "_LIMIT_REACHED_SPECS")
    assert not hasattr(bs, "EveusLimitReachedBinarySensor")


def test_control_pilot_removed_from_sensor_specs() -> None:
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    specs = create_sensor_specifications()
    names = {s.name for s in specs}
    assert "Control Pilot" not in names


def test_runtime_validation_rejects_nan_current() -> None:
    import pytest
    from custom_components.eveus._payload import validate_main_payload

    with pytest.raises(ValueError):
        validate_main_payload({"state": 2, "currentSet": float("nan")}, "16A")
    with pytest.raises(ValueError):
        validate_main_payload({"state": 2, "currentSet": float("inf")}, "16A")


def test_counter_cost_rejects_negative() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import (
        get_counter_a_cost,
        get_counter_b_cost,
    )

    assert get_counter_a_cost(EveusTestUpdater({"IEM1_money": -1.0}), None) is None
    assert get_counter_b_cost(EveusTestUpdater({"IEM2_money": -0.01}), None) is None
    assert get_counter_a_cost(EveusTestUpdater({"IEM1_money": 12.5}), None) == 12.5


def test_tariff_rate_rejects_negative() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import (
        get_primary_rate_cost,
        get_rate2_cost,
        get_rate3_cost,
    )

    assert get_primary_rate_cost(EveusTestUpdater({"tarif": -100}), None) is None
    assert get_rate2_cost(EveusTestUpdater({"tarifAValue": -50}), None) is None
    assert get_rate3_cost(EveusTestUpdater({"tarifBValue": -10}), None) is None
    assert get_primary_rate_cost(EveusTestUpdater({"tarif": 450}), None) == 4.5


def test_adaptive_metrics_reject_negative() -> None:
    from conftest import EveusTestUpdater, spec_value_fn

    assert spec_value_fn("adaptive_current_limit")(EveusTestUpdater({"aiModecurrent": -1}), None) is None
    assert spec_value_fn("adaptive_current_limit")(EveusTestUpdater({"aiModecurrent": 10}), None) == 10


def test_session_cost_spec_is_monetary_uah() -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from custom_components.eveus.sensor_definitions import get_sensor_specifications

    by_key = {s.key: s for s in get_sensor_specifications(1)}
    spec = by_key["session_cost"]
    assert spec.device_class == SensorDeviceClass.MONETARY
    assert spec.unit == "UAH"
    assert spec.state_class == SensorStateClass.TOTAL


def test_session_cost_rejects_negative() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import get_session_cost

    assert get_session_cost(EveusTestUpdater({"sessionMoney": -0.5}), None) is None
    assert get_session_cost(EveusTestUpdater({"sessionMoney": 7.2}), None) == 7.2
