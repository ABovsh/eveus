"""Unit tests for integration setup helpers."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
import custom_components.eveus as eveus
from custom_components.eveus.const import (
    CONF_PHASES,
    CONF_MODEL,
    CONF_SCHEME,
    CONF_SOC_MODE,
    DEFAULT_PHASES,
    MODEL_16A,
    SOC_MODE_ADVANCED,
    SOC_MODE_BASIC,
)
from custom_components.eveus.number import async_setup_entry as async_setup_number_entry
from custom_components.eveus.sensor import async_setup_entry as async_setup_sensor_entry
from custom_components.eveus.ev_sensors import (
    ChargingFinishTimeSensor,
    EVSocKwhSensor,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
)
from custom_components.eveus.button import (
    EveusResetCounterAButton,
    EveusResetCounterBButton,
    async_setup_entry as async_setup_button_entry,
)
from custom_components.eveus.switch import (
    async_setup_entry as async_setup_switch_entry,
)


class _ConfigEntries:
    def __init__(self) -> None:
        self.updated: list[dict[str, object]] = []
        self.forwarded: list[tuple[object, object]] = []
        self.unloaded: list[tuple[object, object]] = []
        self.reloaded: list[str] = []

    def async_entries(self, domain: str) -> list[object]:
        return []

    def async_get_entry(self, entry_id: str) -> object | None:
        return None

    def async_entries(self, _domain=None):
        return []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.updated.append(kwargs)

    async def async_forward_entry_setups(self, entry: object, platforms: object) -> None:
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
        self.unloaded.append((entry, platforms))
        return True

    async def async_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)


class _CancellingConfigEntries(_ConfigEntries):
    async def async_forward_entry_setups(self, entry: object, platforms: object) -> None:
        self.forwarded.append((entry, platforms))
        raise asyncio.CancelledError


class _Entry:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.title = "Eveus Charger"
        self.entry_id = "entry-id"
        self.runtime_data = None
        self.unloads: list[object] = []

    def async_on_unload(self, callback: object) -> None:
        self.unloads.append(callback)

    def add_update_listener(self, listener: object) -> object:
        return listener


class _Updater:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.host = kwargs["host"]
        self.available = True
        self.last_update_success = True
        self.data = {"currentSet": "16"}
        self.listeners: list[object] = []

    def async_add_listener(self, update_callback: object, *args: object, **kwargs: object):
        self.listeners.append(update_callback)
        return lambda: None

    async def async_config_entry_first_refresh(self) -> None:
        return None

    async def async_shutdown(self) -> None:
        return None


class _AuthFailingUpdater(_Updater):
    async def async_config_entry_first_refresh(self) -> None:
        raise ConfigEntryAuthFailed("bad credentials")


class _UnexpectedFailingUpdater(_Updater):
    async def async_config_entry_first_refresh(self) -> None:
        raise RuntimeError("network stack exploded")


class _SafetyManager:
    instances: list["_SafetyManager"] = []

    def __init__(self, hass: object, entry: object, updater: object) -> None:
        self.hass = hass
        self.entry = entry
        self.updater = updater
        self.process_calls = 0
        self.load_calls = 0
        self.instances.append(self)

    async def async_load(self) -> None:
        self.load_calls += 1

    def process(self) -> None:
        self.process_calls += 1


def _hass() -> SimpleNamespace:
    return SimpleNamespace(config_entries=_ConfigEntries())


def _hass_with_config_entries(config_entries: object) -> SimpleNamespace:
    return SimpleNamespace(config_entries=config_entries)


def _data(**overrides: object) -> dict[str, object]:
    data = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


def test_platforms_list_is_exact() -> None:
    assert eveus.PLATFORMS == [
        eveus.Platform.SENSOR,
        eveus.Platform.BINARY_SENSOR,
        eveus.Platform.SWITCH,
        eveus.Platform.NUMBER,
        eveus.Platform.BUTTON,
        eveus.Platform.SELECT,
        eveus.Platform.TIME,
    ]


def test_runtime_data_phases_defaults_to_default_phases() -> None:
    rd = eveus.EveusRuntimeData(
        updater=object(),
        device_number=1,
        title="t",
        soc_calculator=object(),
        soc_limit=object(),
    )
    assert rd.phases == DEFAULT_PHASES


def test_legacy_helpers_present_requires_both(monkeypatch: pytest.MonkeyPatch) -> None:
    class _OnlyInitialRegistry:
        def async_get(self, entity_id: str) -> object | None:
            return object() if entity_id == "input_number.ev_initial_soc" else None

    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _OnlyInitialRegistry())
    assert eveus._legacy_helpers_present(object()) is False


def test_update_ocpp_issue_guard_and_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)),
    )
    monkeypatch.setattr(
        eveus.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id)
    )
    entry = SimpleNamespace(entry_id="e1")

    unavailable = SimpleNamespace(available=False, last_update_success=True, data={"ocppEnabled": 1})
    eveus._update_ocpp_issue(None, entry, unavailable)
    assert not created

    failed = SimpleNamespace(available=True, last_update_success=False, data={"ocppEnabled": 1})
    eveus._update_ocpp_issue(None, entry, failed)
    assert not created

    enabled = SimpleNamespace(available=True, last_update_success=True, data={"ocppEnabled": 1})
    eveus._update_ocpp_issue(None, entry, enabled)
    assert created == [
        (
            eveus._ocpp_issue_id(entry),
            {
                "is_fixable": False,
                "is_persistent": False,
                "issue_domain": "eveus",
                "severity": eveus.ir.IssueSeverity.WARNING,
                "translation_key": "ocpp_enabled",
            },
        )
    ]

    # A missing/garbled ocppEnabled (None) must NOT clear an existing warning.
    garbled = SimpleNamespace(available=True, last_update_success=True, data={"ocppEnabled": None})
    eveus._update_ocpp_issue(None, entry, garbled)
    assert not deleted

    disabled = SimpleNamespace(available=True, last_update_success=True, data={"ocppEnabled": 0})
    eveus._update_ocpp_issue(None, entry, disabled)
    assert deleted == [eveus._ocpp_issue_id(entry)]


def test_async_setup_returns_true() -> None:
    assert asyncio.run(eveus.async_setup(object(), {})) is True


def test_config_schema_is_config_entry_only() -> None:
    result = eveus.CONFIG_SCHEMA({"eveus": {}})

    assert result == {"eveus": {}}


def test_soc_limit_switch_is_advanced_only_prunable():
    from custom_components.eveus import _ADVANCED_ONLY_ENTITIES

    assert ("switch", "limit_soc_enabled") in _ADVANCED_ONLY_ENTITIES


@pytest.fixture(autouse=True)
def _patch_issue_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eveus.ir, "async_create_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *args, **kwargs: None)
    # The safety manager (wired on first refresh) looks up existing issues via
    # the registry; the real HA implementation rejects the stub SimpleNamespace
    # hass. Report "no issue present" so setup runs against the lightweight hass.
    monkeypatch.setattr(
        eveus.ir,
        "async_get",
        lambda hass: SimpleNamespace(async_get_issue=lambda domain, issue_id: None),
    )
    # The setup path now consults the entity registry (status-sensor purge and
    # SOC-mode detection). Default to an empty registry so the stub hass used by
    # these tests does not need a real registry; individual tests override this.
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _FakeEntityRegistry())


def test_async_setup_entry_populates_runtime_data(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data is not None
    assert entry.runtime_data.updater.host == TEST_HOST
    assert entry.runtime_data.device_number == 1
    assert entry.runtime_data.soc_calculator is not None
    assert hass.config_entries.updated == [{"data": {**_data(), "device_number": 1}}]
    assert hass.config_entries.forwarded
    # async_shutdown is registered by DataUpdateCoordinator itself (via the
    # config_entry= constructor argument); we no longer hook it manually.


def test_async_setup_entry_accepts_already_normalized_device_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number=2))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.device_number == 2
    assert hass.config_entries.updated == []


def test_async_setup_entry_propagates_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _AuthFailingUpdater)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert hass.config_entries.forwarded == []


def test_async_setup_entry_wraps_unexpected_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _UnexpectedFailingUpdater)

    with pytest.raises(ConfigEntryNotReady) as exc_info:
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert str(exc_info.value) == "Unexpected error: RuntimeError"
    assert hass.config_entries.forwarded == []


def _registry_with_soc_orphan() -> "_FakeEntityRegistry":
    """Registry holding a SOC sensor row that Basic mode would prune."""
    registry = _FakeEntityRegistry()
    registry.by_unique[("sensor", "eveus", "eveus_soc_energy")] = "sensor.eveus_soc_energy"
    return registry


def test_async_setup_entry_defers_pruning_until_setup_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient first-refresh failure must not delete registry rows.

    Pruning is destructive (it drops user customizations: area, disabled
    state, custom entity_id). It must only run once the entry is committed,
    otherwise a Basic/1-phase reduction plus a flaky charger permanently
    loses entities on a setup attempt that HA will simply retry.
    """
    registry = _registry_with_soc_orphan()
    hass = _hass()
    entry = _Entry(_data(**{CONF_SOC_MODE: SOC_MODE_BASIC}))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _UnexpectedFailingUpdater)

    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert hass.config_entries.forwarded == []
    assert registry.removed == []


def test_async_setup_entry_prunes_orphans_after_successful_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a committed setup, the reduced scope still drops its orphans."""
    registry = _registry_with_soc_orphan()
    hass = _hass()
    entry = _Entry(_data(**{CONF_SOC_MODE: SOC_MODE_BASIC}))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert hass.config_entries.forwarded
    assert "sensor.eveus_soc_energy" in registry.removed


@pytest.mark.parametrize(
    "overrides",
    [
        {CONF_HOST: ""},
        {CONF_USERNAME: ""},
        {CONF_PASSWORD: ""},
        {CONF_MODEL: "bad"},
    ],
)
def test_async_setup_entry_rejects_invalid_stored_data(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigEntryError) as exc_info:
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data(**overrides))))
    assert str(exc_info.value) in {
        "No host specified",
        "No username specified",
        "No password specified",
        "Invalid model specified",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({CONF_HOST: 123}, "Host is not a string"),
        ({CONF_HOST: "http://charger.local/main"}, "Invalid host:"),
        ({CONF_USERNAME: "bad:user"}, "Invalid credentials:"),
    ],
)
def test_async_setup_entry_rejects_hardened_stored_data_edges(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ConfigEntryError, match=message):
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data(**overrides))))


def test_async_setup_entry_rejects_invalid_scheme_returned_after_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus import config_flow

    monkeypatch.setattr(
        config_flow,
        "_split_host_and_scheme",
        lambda host, scheme="http": (host, "ftp"),
    )

    with pytest.raises(ConfigEntryError, match="Invalid scheme"):
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data())))


def test_async_setup_entry_creates_repair_for_invalid_stored_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    def create_issue(hass, domain, issue_id, **kwargs):
        created.append({"domain": domain, "issue_id": issue_id, **kwargs})

    monkeypatch.setattr(eveus.ir, "async_create_issue", create_issue)
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *args, **kwargs: None)

    with pytest.raises(ConfigEntryError):
        asyncio.run(
            eveus.async_setup_entry(
                _hass(),
                _Entry(_data(**{CONF_MODEL: "bad"})),
            )
        )

    assert created
    assert created[0]["translation_key"] == "invalid_config"
    assert created[0]["is_fixable"] is True


def test_async_setup_entry_purges_retired_status_sensor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeEntityRegistry()
    registry.by_unique[
        ("sensor", "eveus", "eveus_input_entities_status")
    ] = "sensor.eveus_input_entities_status"
    hass = _hass()
    entry = _Entry(_data(device_number=1))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert registry.removed == ["sensor.eveus_input_entities_status"]


def test_async_setup_entry_creates_soc_dashboard_issue_for_legacy_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []
    registry = _FakeEntityRegistry(
        registered={
            "input_number.ev_initial_soc",
            "input_number.ev_battery_capacity",
        }
    )
    hass = _hass()
    entry = _Entry(_data(device_number=1, **{CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append({"issue_id": issue_id, **kw}),
    )

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert created == [
        {
            "issue_id": "soc_dashboard_update_entry-id",
            "is_fixable": False,
            "is_persistent": True,
            "issue_domain": "eveus",
            "severity": eveus.ir.IssueSeverity.WARNING,
            "translation_key": "soc_dashboard_update",
        }
    ]


def test_async_setup_entry_normalizes_invalid_phase_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number=1, **{CONF_PHASES: "bad"}))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.phases == DEFAULT_PHASES
    # The invalid value must NOT be persisted: writing it back would make
    # raw_phases valid on the next reload, losing the phases_were_invalid
    # signal that protects the phase 2/3 registry rows from the destructive
    # prune (regression test for A04).
    assert hass.config_entries.updated == []


def test_async_setup_entry_normalizes_unsupported_phase_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number=1, **{CONF_PHASES: 2}))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.phases == DEFAULT_PHASES
    assert hass.config_entries.updated == []


def test_async_setup_entry_raises_ocpp_issue_on_first_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OcppUpdater(_Updater):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.data = {"ocppEnabled": 1}

    created: list[dict[str, object]] = []
    hass = _hass()
    entry = _Entry(_data(device_number=1))
    monkeypatch.setattr(eveus, "EveusUpdater", OcppUpdater)
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append({"issue_id": issue_id, **kw}),
    )

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert created == [
        {
            "issue_id": "ocpp_enabled_entry-id",
            "is_fixable": False,
            "is_persistent": False,
            "issue_domain": "eveus",
            "severity": eveus.ir.IssueSeverity.WARNING,
            "translation_key": "ocpp_enabled",
        }
    ]


def test_async_setup_entry_normalizes_stored_device_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number="2"))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data is not None
    assert entry.runtime_data.device_number == 2
    assert hass.config_entries.updated == [{"data": {**_data(), "device_number": 2}}]


def test_unload_entry() -> None:
    hass = _hass()
    entry = _Entry(_data())

    assert asyncio.run(eveus.async_unload_entry(hass, entry)) is True
    assert hass.config_entries.unloaded


def test_async_setup_entry_registers_and_runs_safety_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus import safety

    _SafetyManager.instances.clear()
    hass = _hass()
    entry = _Entry(_data(device_number=1))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)
    monkeypatch.setattr(safety, "EveusSafetyManager", _SafetyManager)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    manager = _SafetyManager.instances[0]
    assert manager.entry is entry
    assert manager.updater is entry.runtime_data.updater
    assert manager.process_calls == 1
    assert manager.process in entry.runtime_data.updater.listeners


def test_unload_does_not_delete_safety_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    hass = _hass()
    entry = _Entry(_data())

    assert asyncio.run(eveus.async_unload_entry(hass, entry)) is True
    assert all(not issue_id.startswith("safety_") for issue_id in deleted)


def test_unload_entry_propagates_platform_unload_failure() -> None:
    class _FailingConfigEntries(_ConfigEntries):
        async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
            raise RuntimeError("unload failed")

    hass = SimpleNamespace(config_entries=_FailingConfigEntries())
    entry = _Entry(_data())

    import pytest

    with pytest.raises(RuntimeError, match="unload failed"):
        asyncio.run(eveus.async_unload_entry(hass, entry))
    assert hass.config_entries.unloaded == []


def test_sensor_setup_creates_standard_and_ev_sensors() -> None:
    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=1,
        soc_calculator=object(),
        phases=1,
    )

    asyncio.run(
        async_setup_sensor_entry(
            object(),
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
    )

    assert len(added) >= 20


def _setup_sensors_for_mode(soc_mode: str | None) -> list[object]:
    """Run the sensor platform setup and return the added entities."""
    added: list[object] = []
    overrides = {} if soc_mode is None else {"soc_mode": soc_mode}
    entry = _Entry(_data(**overrides))
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=1,
        soc_calculator=object(),
        phases=1,
    )
    asyncio.run(
        async_setup_sensor_entry(
            object(),
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
    )
    return added


def test_soc_sensors_only_in_advanced() -> None:
    soc_classes = (
        EVSocKwhSensor,
        EVSocPercentSensor,
        TimeToTargetSocSensor,
        ChargingFinishTimeSensor,
    )

    advanced = _setup_sensors_for_mode(SOC_MODE_ADVANCED)
    advanced_types = {type(entity) for entity in advanced}
    for cls in soc_classes:
        assert cls in advanced_types, f"{cls.__name__} missing in Advanced mode"

    basic = _setup_sensors_for_mode(SOC_MODE_BASIC)
    basic_types = {type(entity) for entity in basic}
    for cls in soc_classes:
        assert cls not in basic_types, f"{cls.__name__} present in Basic mode"

    # The retired status sensor must never be created in either mode.
    for entities in (advanced, basic):
        assert not any(
            type(entity).__name__ == "InputEntitiesStatusSensor" for entity in entities
        )


def test_switch_setup_creates_control_entities() -> None:
    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=2,
        soc_limit=object(),
    )

    asyncio.run(
        async_setup_switch_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert [entity.name for entity in added] == [
        "Stop Charging",
        "One Charge",
        "Schedule 1 Enabled",
        "Schedule 2 Enabled",
        "Ground Protection",
        "Connect to OCPP",
        "Limit: disable all",
        "Limit: Time enabled",
        "Limit: Energy enabled",
        "Limit: Cost enabled",
        "Schedule 1 Current limit enabled",
        "Schedule 1 Energy limit enabled",
        "Schedule 2 Current limit enabled",
        "Schedule 2 Energy limit enabled",
        "Limit: SOC enabled",
    ]
    assert {entity.unique_id for entity in added} == {
        "eveus2_stop_charging",
        "eveus2_one_charge",
        "eveus2_schedule_1_enabled",
        "eveus2_schedule_2_enabled",
        "eveus2_ground_protection",
        "eveus2_connect_to_ocpp",
        "eveus2_limit_disable_all",
        "eveus2_limit_time_enabled",
        "eveus2_limit_energy_enabled",
        "eveus2_limit_cost_enabled",
        "eveus2_schedule_1_current_limit_enabled",
        "eveus2_schedule_1_energy_limit_enabled",
        "eveus2_schedule_2_current_limit_enabled",
        "eveus2_schedule_2_energy_limit_enabled",
        "eveus2_limit_soc_enabled",
    }


def test_button_setup_creates_refresh_and_reset_buttons() -> None:
    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=2,
    )

    asyncio.run(
        async_setup_button_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert [entity.name for entity in added] == [
        "Force Refresh",
        "Reset Counter A",
        "Reset Counter B",
        "Sync Time",
    ]
    assert {entity.unique_id for entity in added} == {
        "eveus2_force_refresh",
        "eveus2_reset_counter_a",
        "eveus2_reset_counter_b",
        "eveus2_sync_time",
    }


def test_select_setup_creates_control_entities() -> None:
    from custom_components.eveus.select import async_setup_entry as async_setup_select_entry

    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=2,
    )

    asyncio.run(
        async_setup_select_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert [entity.name for entity in added] == [
        "Time Zone",
        "Adaptive Mode",
        "Minimum voltage",
    ]
    assert {entity.unique_id for entity in added} == {
        "eveus2_time_zone",
        "eveus2_adaptive_mode",
        "eveus2_minimum_voltage",
    }


def test_select_setup_model_gates_minimum_voltage_only() -> None:
    from custom_components.eveus.select import async_setup_entry as async_setup_select_entry

    added: list[object] = []
    data = _data()
    data.pop(CONF_MODEL)
    entry = _Entry(data)
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=2,
    )

    asyncio.run(
        async_setup_select_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert [entity.name for entity in added] == ["Time Zone", "Adaptive Mode"]


def test_number_setup_creates_current_entity() -> None:
    added: list[object] = []
    entry = _Entry(_data(soc_mode="basic"))
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=3,
    )

    asyncio.run(
        async_setup_number_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    # Charging Current + global limits + schedule limits + undervoltage threshold.
    assert len(added) == 9
    names = [e.name for e in added]
    assert "Charging Current" in names
    assert {
        "Limit Time",
        "Limit Energy",
        "Limit Cost",
        "Schedule 1 Current limit",
        "Schedule 1 Energy limit",
        "Schedule 2 Current limit",
        "Schedule 2 Energy limit",
        "Undervoltage threshold",
    } <= set(names)
    assert "Money Limit" not in names
    current = next(e for e in added if e.name == "Charging Current")
    assert current.unique_id == "eveus3_charging_current"


def test_number_setup_always_creates_undervoltage_threshold() -> None:
    added: list[object] = []
    data = _data(soc_mode="basic")
    data.pop(CONF_MODEL)
    entry = _Entry(data)
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD),
        device_number=2,
    )

    asyncio.run(
        async_setup_number_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert [entity.name for entity in added] == ["Undervoltage threshold"]
    assert added[0].unique_id == "eveus2_undervoltage_threshold"


class _FakeEntityRegistry:
    """Minimal entity-registry stand-in exercising the SOC detector + purge."""

    def __init__(self, registered: set[str] | None = None) -> None:
        # registered: set of "domain.object_id" entity_ids present in the registry
        self.registered = set(registered or set())
        # map of (platform, domain, unique_id) -> entity_id for our entities
        self.by_unique: dict[tuple[str, str, str], str] = {}
        self.removed: list[str] = []
        self.renamed: list[tuple[str, str]] = []

    def async_get(self, entity_id: str) -> object | None:
        return object() if entity_id in self.registered else None

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str | None:
        return self.by_unique.get((platform, domain, unique_id))

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)

    def async_update_entity(self, entity_id: str, **kwargs: object) -> None:
        new_entity_id = kwargs.get("new_entity_id")
        if not isinstance(new_entity_id, str):
            return
        self.renamed.append((entity_id, new_entity_id))
        self.registered.discard(entity_id)
        self.registered.add(new_entity_id)
        for key, value in list(self.by_unique.items()):
            if value == entity_id:
                self.by_unique[key] = new_entity_id


class _MigrateEntries:
    """Config-entries stub that applies update kwargs back onto the entry."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def async_entries(self, _domain=None):
        return []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.calls.append(kwargs)
        for key, value in kwargs.items():
            setattr(entry, key, value)


def _migrate_entry() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_MODEL: MODEL_16A,
            "scheme": "http",
            "phases": 1,
        },
        unique_id=TEST_HOST,
        title=f"Eveus Charger ({TEST_HOST})",
        version=3,
    )


def test_migration_strips_legacy_url_path_query_and_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeEntityRegistry(registered=set())
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )

    hass = SimpleNamespace(
        config_entries=_MigrateEntries(),
        states=SimpleNamespace(get=lambda entity_id: None),
    )
    entry = _migrate_entry()
    entry.data = {
        **entry.data,
        CONF_HOST: f"http://{TEST_HOST}/main?x=1#frag",
    }
    entry.unique_id = entry.data[CONF_HOST]
    entry.title = f"Eveus Charger ({entry.data[CONF_HOST]})"

    assert asyncio.run(eveus.async_migrate_entry(hass, entry)) is True

    assert entry.data[CONF_HOST] == TEST_HOST
    assert entry.data[CONF_SCHEME] == "http"
    assert entry.unique_id == TEST_HOST
    assert entry.title == f"Eveus Charger ({TEST_HOST})"


def test_migration_survives_malformed_legacy_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.parse

    registry = _FakeEntityRegistry(registered=set())
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        urllib.parse,
        "urlparse",
        lambda host: (_ for _ in ()).throw(ValueError("malformed")),
    )

    hass = SimpleNamespace(
        config_entries=_MigrateEntries(),
        states=SimpleNamespace(get=lambda entity_id: None),
    )
    entry = _migrate_entry()
    entry.data = {**entry.data, CONF_HOST: f"http://{TEST_HOST}"}

    assert asyncio.run(eveus.async_migrate_entry(hass, entry)) is True

    assert entry.version == 4
    assert entry.data[CONF_HOST] == TEST_HOST


def test_async_setup_entry_keeps_device_prefixed_soc_number_entity_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeEntityRegistry(
        registered={
            "number.eveus_ev_charger_initial_soc",
            "number.eveus_ev_charger_target_soc",
            "number.eveus_ev_charger_battery_capacity",
            "number.eveus_ev_charger_soc_correction",
        }
    )
    registry.by_unique.update(
        {
            ("number", "eveus", "eveus_initial_soc"): "number.eveus_ev_charger_initial_soc",
            ("number", "eveus", "eveus_target_soc"): "number.eveus_ev_charger_target_soc",
            ("number", "eveus", "eveus_battery_capacity"): "number.eveus_ev_charger_battery_capacity",
            ("number", "eveus", "eveus_soc_correction"): "number.eveus_ev_charger_soc_correction",
        }
    )
    hass = _hass()
    entry = _Entry(_data(**{CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert registry.renamed == []


def test_async_setup_entry_keeps_device_prefixed_soc_ids_when_unique_lookup_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeEntityRegistry(
        registered={
            "number.eveus_ev_charger_initial_soc",
            "number.eveus_ev_charger_target_soc",
            "number.eveus_ev_charger_battery_capacity",
            "number.eveus_ev_charger_soc_correction",
        }
    )
    hass = _hass()
    entry = _Entry(_data(**{CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert registry.renamed == []


def test_migration_advanced_when_helpers_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeEntityRegistry(
        registered={
            "input_number.ev_initial_soc",
            "input_number.ev_battery_capacity",
        }
    )
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)

    hass = SimpleNamespace(
        config_entries=_MigrateEntries(),
        states=SimpleNamespace(get=lambda entity_id: None),
    )
    entry = _migrate_entry()

    assert asyncio.run(eveus.async_migrate_entry(hass, entry)) is True

    assert entry.version == 4
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_ADVANCED


def test_migration_basic_when_no_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeEntityRegistry(registered=set())
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)

    hass = SimpleNamespace(
        config_entries=_MigrateEntries(),
        states=SimpleNamespace(get=lambda entity_id: None),
    )
    entry = _migrate_entry()

    assert asyncio.run(eveus.async_migrate_entry(hass, entry)) is True

    assert entry.version == 4
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_BASIC


def test_reset_counter_buttons_send_reset_commands() -> None:
    updater = _Updater(host=TEST_HOST, username=TEST_USERNAME, password=TEST_PASSWORD)
    updater.send_command_calls = []

    async def send_command(command: str, value: object, *, retry: bool = True) -> bool:
        updater.send_command_calls.append((command, value, retry))
        return True

    updater.send_command = send_command  # type: ignore[assignment]

    button_a = EveusResetCounterAButton(updater, 1)
    button_b = EveusResetCounterBButton(updater, 1)

    asyncio.run(button_a.async_press())
    asyncio.run(button_b.async_press())

    assert updater.send_command_calls == [
        ("rstEM1", 0, False),
        ("rstEM2", 0, False),
    ]


def test_async_setup_entry_clears_stale_soc_dashboard_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-F03: the persistent SOC-dashboard notice is cleared when it no longer
    applies (Basic mode / no legacy helpers), instead of lingering forever."""
    deleted: list[tuple[object, str]] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append((domain, issue_id)),
    )
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _FakeEntityRegistry())
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    hass = _hass()
    entry = _Entry(_data(**{CONF_SOC_MODE: SOC_MODE_BASIC}))

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True
    assert ("eveus", "soc_dashboard_update_entry-id") in deleted


def test_v04_runtime_data_unset_after_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-refresh auth failure must leave no runtime objects on the entry."""
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _AuthFailingUpdater)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert getattr(entry, "runtime_data", None) is None


def test_v04_runtime_data_unset_after_unexpected_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _UnexpectedFailingUpdater)

    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert getattr(entry, "runtime_data", None) is None


def test_runtime_data_unset_after_setup_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus import safety

    _SafetyManager.instances.clear()
    hass = _hass_with_config_entries(_CancellingConfigEntries())
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)
    monkeypatch.setattr(safety, "EveusSafetyManager", _SafetyManager)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert getattr(entry, "runtime_data", None) is None


class _RaisingConfigEntries(_ConfigEntries):
    """Config-entries stub whose platform-forward step raises unexpectedly."""

    async def async_forward_entry_setups(self, entry: object, platforms: object) -> None:
        self.forwarded.append((entry, platforms))
        raise RuntimeError("platform forward exploded")


def test_runtime_data_is_exactly_none_after_unexpected_finish_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic-Exception cleanup branch must clear runtime_data to None,
    not merely to something falsy."""
    from custom_components.eveus import safety

    _SafetyManager.instances.clear()
    hass = _hass_with_config_entries(_RaisingConfigEntries())
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)
    monkeypatch.setattr(safety, "EveusSafetyManager", _SafetyManager)

    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert entry.runtime_data is None


# --- reason/exact-message coverage for the invalid-stored-data repair path ---


@pytest.mark.parametrize(
    ("overrides", "reason", "message"),
    [
        ({CONF_HOST: ""}, "missing_host", "No host specified"),
        ({CONF_USERNAME: ""}, "missing_username", "No username specified"),
        ({CONF_PASSWORD: ""}, "missing_password", "No password specified"),
        ({CONF_MODEL: "bad"}, "invalid_model", "Invalid model specified"),
    ],
)
def test_async_setup_entry_invalid_stored_data_reason_and_message_exact(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    reason: str,
    message: str,
) -> None:
    created: list[dict[str, object]] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    with pytest.raises(ConfigEntryError) as exc_info:
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data(**overrides))))

    assert str(exc_info.value) == message
    assert created[-1]["data"]["reason"] == reason
    assert created[-1]["data"]["entry_id"] == "entry-id"
    assert created[-1]["is_persistent"] is True


@pytest.mark.parametrize(
    ("overrides", "reason", "message_prefix"),
    [
        ({CONF_HOST: 123}, "invalid_host", "Host is not a string"),
        ({CONF_HOST: "http://charger.local/main"}, "invalid_host", "Invalid host: "),
        ({CONF_USERNAME: "bad:user"}, "invalid_credentials", "Invalid credentials: "),
    ],
)
def test_async_setup_entry_hardened_data_reason_and_exact_message(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    reason: str,
    message_prefix: str,
) -> None:
    created: list[dict[str, object]] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    with pytest.raises(ConfigEntryError) as exc_info:
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data(**overrides))))

    assert str(exc_info.value).startswith(message_prefix)
    assert created[-1]["data"]["reason"] == reason


def test_async_setup_entry_invalid_scheme_reason_and_exact_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus import config_flow

    created: list[dict[str, object]] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    monkeypatch.setattr(
        config_flow, "_split_host_and_scheme", lambda host, scheme="http": (host, "ftp")
    )

    with pytest.raises(ConfigEntryError) as exc_info:
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data())))

    assert str(exc_info.value) == "Invalid scheme: 'ftp'"
    assert created[-1]["data"]["reason"] == "invalid_scheme"


def test_async_setup_entry_accepts_https_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored https:// scheme is legitimate and must not be rejected."""
    hass = _hass()
    entry = _Entry(_data(**{CONF_SCHEME: "https"}))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True


# --- stored phase count: honored when valid, protected when invalid ---


def test_async_setup_entry_uses_stored_valid_phase_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number=1, **{CONF_PHASES: 3}))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.phases == 3


def test_async_setup_entry_invalid_phase_count_protects_three_phase_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid stored phase count falls back to 1 phase for this session
    only — it must not prune the phase 2/3 registry rows (regression guard
    for the `3 if phases_were_invalid else phases` fallback)."""
    registry = _FakeEntityRegistry()
    registry.by_unique[("sensor", "eveus", "eveus_current_phase_2")] = (
        "sensor.eveus_current_phase_2"
    )
    registry.by_unique[("sensor", "eveus", "eveus_voltage_phase_3")] = (
        "sensor.eveus_voltage_phase_3"
    )
    hass = _hass()
    entry = _Entry(_data(device_number=1, **{CONF_PHASES: "garbage"}))
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.phases == DEFAULT_PHASES
    assert "sensor.eveus_current_phase_2" not in registry.removed
    assert "sensor.eveus_voltage_phase_3" not in registry.removed


class _FirmwareFallbackUpdater(_Updater):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fetch_calls = 0

    async def async_maybe_fetch_init_firmware(self) -> None:
        self.fetch_calls += 1


def test_async_setup_entry_calls_init_firmware_fallback_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number=1))
    monkeypatch.setattr(eveus, "EveusUpdater", _FirmwareFallbackUpdater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.updater.fetch_calls == 1


# --- _BatteryLowTracker: default state, guard boundaries, streak/hysteresis ---


def test_battery_low_tracker_initial_state() -> None:
    t = eveus._BatteryLowTracker()
    assert t._low_streak == 0
    assert t._active is False


def test_battery_low_tracker_guard_boundaries() -> None:
    from custom_components.eveus.const import (
        BATTERY_LOW_DEBOUNCE_POLLS,
        BATTERY_LOW_THRESHOLD_VOLTS,
        BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS,
    )

    # A reading of exactly 0 V must be treated as invalid/offline on every
    # poll (never advances the streak), not merely on the first one.
    t0 = eveus._BatteryLowTracker()
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS + 1):
        assert t0.evaluate(0.0) is None

    # A small positive reading below the low threshold IS a genuine low
    # reading and must fire after exactly the debounce count.
    t1 = eveus._BatteryLowTracker()
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        assert t1.evaluate(0.5) is None
    assert t1.evaluate(0.5) is True

    # Exactly at the max-plausible ceiling is still a plausible (healthy)
    # reading and must clear an active warning.
    t2 = eveus._BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        t2.evaluate(low)
    assert t2._active is True
    assert t2.evaluate(BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS) is False

    # A reading exactly at the low threshold is NOT "low" (strict <).
    t3 = eveus._BatteryLowTracker()
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        assert t3.evaluate(BATTERY_LOW_THRESHOLD_VOLTS) is None
    assert t3._low_streak == 0


def test_battery_low_tracker_streak_increments_exactly_and_fires_once() -> None:
    from custom_components.eveus.const import (
        BATTERY_LOW_DEBOUNCE_POLLS,
        BATTERY_LOW_THRESHOLD_VOLTS,
    )

    t = eveus._BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    assert t.evaluate(low) is None
    assert t._low_streak == 1
    assert t.evaluate(low) is None
    assert t._low_streak == 2
    assert t.evaluate(low) is True
    assert t._low_streak == BATTERY_LOW_DEBOUNCE_POLLS
    assert t._active is True
    # Staying low must not re-fire.
    assert t.evaluate(low) is None


def test_battery_low_tracker_reset_and_clear_exact_boundaries() -> None:
    from custom_components.eveus.const import (
        BATTERY_LOW_DEBOUNCE_POLLS,
        BATTERY_LOW_THRESHOLD_VOLTS,
        BATTERY_OK_THRESHOLD_VOLTS,
    )

    t = eveus._BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        t.evaluate(low)
    assert t._low_streak == BATTERY_LOW_DEBOUNCE_POLLS - 1

    # A healthy reading before the streak completes must reset it to exactly 0.
    assert t.evaluate(BATTERY_OK_THRESHOLD_VOLTS) is None
    assert t._low_streak == 0

    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        t.evaluate(low)
    assert t._active is True

    # Dead-band reading (>= low threshold but < OK threshold) must not clear.
    dead_band = (BATTERY_LOW_THRESHOLD_VOLTS + BATTERY_OK_THRESHOLD_VOLTS) / 2
    assert t.evaluate(dead_band) is None
    assert t._active is True

    # Exactly at the OK threshold, while active, must clear -- returning
    # False specifically.
    assert t.evaluate(BATTERY_OK_THRESHOLD_VOLTS) is False
    assert t._active is False


def test_update_battery_low_issue_guard_field_and_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import (
        BATTERY_LOW_DEBOUNCE_POLLS,
        BATTERY_LOW_THRESHOLD_VOLTS,
        BATTERY_OK_THRESHOLD_VOLTS,
    )

    created: list[tuple[str, dict[str, object]]] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)),
    )
    monkeypatch.setattr(
        eveus.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id)
    )
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1

    unavailable = SimpleNamespace(available=False, last_update_success=True, data={"vBat": low})
    eveus._update_battery_low_issue(None, entry, unavailable, tracker)
    assert tracker._low_streak == 0  # guard must skip entirely

    failed = SimpleNamespace(available=True, last_update_success=False, data={"vBat": low})
    eveus._update_battery_low_issue(None, entry, failed, tracker)
    assert tracker._low_streak == 0

    normal = SimpleNamespace(available=True, last_update_success=True, data={"vBat": low})
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        eveus._update_battery_low_issue(None, entry, normal, tracker)

    assert created
    issue_id, kw = created[-1]
    assert issue_id == eveus._battery_low_issue_id(entry)
    assert kw["is_fixable"] is False
    assert kw["is_persistent"] is False
    assert kw["translation_key"] == "battery_low"

    clear = SimpleNamespace(
        available=True, last_update_success=True, data={"vBat": BATTERY_OK_THRESHOLD_VOLTS}
    )
    eveus._update_battery_low_issue(None, entry, clear, tracker)
    assert deleted == [eveus._battery_low_issue_id(entry)]


# --- _ClockDriftTracker: default state, arithmetic, classification, hysteresis ---


def _clock_env(monkeypatch: pytest.MonkeyPatch, *, local_wall: int, utc_offset: int = 0) -> None:
    """Pin HA's local wall clock/offset and let the charger wall clock come
    straight from data["_charger_wall"] (or None), for deterministic drift math
    without needing real systemTime/timeZone payload encoding."""
    monkeypatch.setattr(eveus, "get_local_wall_clock_seconds", lambda: local_wall)
    monkeypatch.setattr(eveus, "get_local_utc_offset_seconds", lambda: utc_offset)
    monkeypatch.setattr(
        eveus,
        "get_charger_wall_clock_seconds",
        lambda data: data.get("_charger_wall") if data else None,
    )


def test_clock_drift_tracker_initial_state() -> None:
    t = eveus._ClockDriftTracker()
    assert t._drift_streak == 0
    assert t._ok_streak == 0
    assert t._active is False
    assert t.kind == "sync"
    assert t.hours == 0
    assert t.published is None
    assert t.still_drifted is False
    assert t.rekey_streak == 0


def test_clock_drift_missing_time_fields_resets_transient_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock_env(monkeypatch, local_wall=0)
    t = eveus._ClockDriftTracker()
    t.still_drifted = True
    t.rekey_streak = 5
    assert t.evaluate({}) is None
    assert t.still_drifted is False
    assert t.rekey_streak == 0


def test_clock_drift_uses_real_charger_wall_clock_not_forced_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock_env(monkeypatch, local_wall=1000, utc_offset=0)
    t = eveus._ClockDriftTracker()
    # Charger 5000 s ahead: a real drift, not the "missing time fields" path.
    result = t.evaluate({"_charger_wall": 1000 + 5000})
    assert result is None  # only the first of TRIGGER_POLLS
    assert t.still_drifted is True
    assert t._drift_streak == 1


def test_clock_drift_signed_drift_is_charger_minus_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock_env(monkeypatch, local_wall=100000, utc_offset=0)
    t = eveus._ClockDriftTracker()
    t.evaluate({"_charger_wall": 100000 + 7200})  # charger 2h ahead
    assert t.kind == "timezone"
    assert t.hours == 2


def test_clock_drift_whole_hours_uses_3600_second_divisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    t = eveus._ClockDriftTracker()
    # Chosen so that round(x/3600) and round(x/3601) diverge cleanly.
    signed_drift = 3600 * 3601
    t.evaluate({"_charger_wall": signed_drift})
    assert t.kind == "timezone"
    assert t.hours == 3601


def test_clock_drift_timezone_match_boundary_is_inclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    t = eveus._ClockDriftTracker()
    # whole_hours=1 (3600s); drift sits exactly CLOCK_DRIFT_TZ_MATCH_TOLERANCE
    # (300s) away from the whole-hour multiple -- must still classify as
    # "timezone" (<=), not fall through to "sync".
    t.evaluate({"_charger_wall": 3600 + 300})
    assert t.kind == "timezone"
    assert t.hours == 1


def test_clock_drift_residue_and_fractional_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # +5:45 (Nepal-style) fractional UTC offset: residue = 2700.
    _clock_env(monkeypatch, local_wall=0, utc_offset=20700)

    # Matches only via the (3600 - residue) candidate, at the tolerance edge.
    t_a = eveus._ClockDriftTracker()
    t_a.evaluate({"_charger_wall": 600})
    assert t_a.kind == "fractional"
    assert t_a.hours == 0

    # Matches only via the (-residue) candidate.
    t_b = eveus._ClockDriftTracker()
    t_b.evaluate({"_charger_wall": -2700})
    assert t_b.kind == "fractional"

    # In sync (drift 0): residue is truthy but no candidate matches --
    # must classify as "sync", proving the guard is `and`, not `or`, and
    # that whole_hours == 0 does NOT count as a timezone match.
    t_c = eveus._ClockDriftTracker()
    t_c.evaluate({"_charger_wall": 0})
    assert t_c.kind == "sync"
    assert t_c.hours == 0


def test_clock_drift_threshold_boundary_exact_is_not_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import CLOCK_DRIFT_THRESHOLD_SECONDS

    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    t = eveus._ClockDriftTracker()
    assert t.evaluate({"_charger_wall": CLOCK_DRIFT_THRESHOLD_SECONDS}) is None
    assert t.still_drifted is False
    assert t._drift_streak == 0
    assert t._ok_streak == 1
    assert t.evaluate({"_charger_wall": 550}) is None
    assert t._ok_streak == 2


def test_clock_drift_streak_increment_and_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import CLOCK_DRIFT_TRIGGER_POLLS

    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    t = eveus._ClockDriftTracker()
    drifted = {"_charger_wall": 1000}

    assert t.evaluate(drifted) is None
    assert t._ok_streak == 0
    assert t._drift_streak == 1
    assert t.evaluate(drifted) is None
    assert t._drift_streak == 2
    assert t.evaluate(drifted) is True
    assert t._drift_streak == CLOCK_DRIFT_TRIGGER_POLLS
    assert t._active is True
    # Further drifted polls must not re-fire.
    assert t.evaluate(drifted) is None


def test_clock_drift_hysteresis_band_requires_active_and_strict_clear_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS

    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    t = eveus._ClockDriftTracker()
    t._active = True
    assert t.evaluate({"_charger_wall": CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS}) is None
    assert t._ok_streak == 1


def test_clock_drift_band_reset_and_ok_streak_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import CLOCK_DRIFT_CLEAR_POLLS

    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    t = eveus._ClockDriftTracker()
    t._active = True
    t._ok_streak = 5
    # In the hysteresis band (>CLEAR_THRESHOLD, <=THRESHOLD): must reset.
    assert t.evaluate({"_charger_wall": 200}) is None
    assert t._ok_streak == 0

    assert t.evaluate({"_charger_wall": 0}) is None
    assert t._ok_streak == 1
    assert t._active is True
    assert t.evaluate({"_charger_wall": 0}) is False
    assert t._ok_streak == CLOCK_DRIFT_CLEAR_POLLS
    assert t._active is False


def test_update_clock_drift_issue_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()

    unavailable = SimpleNamespace(available=False, last_update_success=True, data={"_charger_wall": 5000})
    eveus._update_clock_drift_issue(None, entry, unavailable, tracker)
    assert tracker._drift_streak == 0

    failed = SimpleNamespace(available=True, last_update_success=False, data={"_charger_wall": 5000})
    eveus._update_clock_drift_issue(None, entry, failed, tracker)
    assert tracker._drift_streak == 0


def test_update_clock_drift_issue_creates_timezone_issue_and_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import CLOCK_DRIFT_TRIGGER_POLLS, CLOCK_DRIFT_CLEAR_POLLS

    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    created: list[tuple[str, dict[str, object]]] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)),
    )
    monkeypatch.setattr(
        eveus.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id)
    )
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()
    drifted = SimpleNamespace(available=True, last_update_success=True, data={"_charger_wall": 7200})

    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS - 1):
        eveus._update_clock_drift_issue(None, entry, drifted, tracker)
    assert not created
    assert tracker.rekey_streak == 0
    eveus._update_clock_drift_issue(None, entry, drifted, tracker)

    assert len(created) == 1
    issue_id, kw = created[0]
    assert issue_id == eveus._clock_drift_issue_id(entry)
    assert kw["is_fixable"] is False
    assert kw["is_persistent"] is False
    assert kw["translation_key"] == "clock_drift_timezone"
    assert kw["translation_placeholders"] == {"hours": "2"}
    assert tracker.published == ("timezone", 2)
    assert tracker.rekey_streak == 0

    synced = SimpleNamespace(available=True, last_update_success=True, data={"_charger_wall": 0})
    for _ in range(CLOCK_DRIFT_CLEAR_POLLS - 1):
        eveus._update_clock_drift_issue(None, entry, synced, tracker)
    assert not deleted
    eveus._update_clock_drift_issue(None, entry, synced, tracker)
    assert deleted == [eveus._clock_drift_issue_id(entry)]
    assert tracker.published is None
    assert tracker.rekey_streak == 0


def test_update_clock_drift_issue_rekeys_on_reclassification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.eveus.const import CLOCK_DRIFT_TRIGGER_POLLS

    _clock_env(monkeypatch, local_wall=0, utc_offset=0)
    created: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)),
    )
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *a, **k: None)
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()
    tz_drift = SimpleNamespace(available=True, last_update_success=True, data={"_charger_wall": 7200})
    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS):
        eveus._update_clock_drift_issue(None, entry, tz_drift, tracker)
    assert tracker.published == ("timezone", 2)
    created.clear()

    reclassified = SimpleNamespace(
        available=True, last_update_success=True, data={"_charger_wall": 1000}
    )
    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS - 1):
        eveus._update_clock_drift_issue(None, entry, reclassified, tracker)
        assert not created
    eveus._update_clock_drift_issue(None, entry, reclassified, tracker)

    assert created
    assert created[-1][1]["translation_key"] == "clock_drift"
    assert tracker.published == ("sync", 0)


def test_update_clock_drift_issue_fractional_classification_translation_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock_env(monkeypatch, local_wall=0, utc_offset=20700)  # +5:45 fractional offset
    created: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)),
    )
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *a, **k: None)
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()
    drifted = SimpleNamespace(available=True, last_update_success=True, data={"_charger_wall": -2700})

    for _ in range(3):
        eveus._update_clock_drift_issue(None, entry, drifted, tracker)

    assert created
    assert created[-1][1]["translation_key"] == "clock_drift_fractional_timezone"
    assert created[-1][1]["translation_placeholders"] is None
    assert tracker.published == ("fractional", 0)
