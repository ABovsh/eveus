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
    CONF_MODEL,
    CONF_SOC_MODE,
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

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.updated.append(kwargs)

    async def async_forward_entry_setups(self, entry: object, platforms: object) -> None:
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
        self.unloaded.append((entry, platforms))
        return True

    async def async_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)


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

    def async_add_listener(self, *args: object, **kwargs: object):
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


def _hass() -> SimpleNamespace:
    return SimpleNamespace(config_entries=_ConfigEntries())


def _data(**overrides: object) -> dict[str, object]:
    data = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def _patch_issue_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eveus.ir, "async_create_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *args, **kwargs: None)
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

    with pytest.raises(ConfigEntryNotReady, match="Unexpected error"):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert hass.config_entries.forwarded == []


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


def test_update_listener_and_unload_entry() -> None:
    hass = _hass()
    entry = _Entry(_data())

    asyncio.run(eveus.update_listener(hass, entry))
    assert hass.config_entries.reloaded == ["entry-id"]

    assert asyncio.run(eveus.async_unload_entry(hass, entry)) is True
    assert hass.config_entries.unloaded


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
        "Adaptive Mode",
        "Schedule 1 Enabled",
        "Schedule 2 Enabled",
    ]
    assert {entity.unique_id for entity in added} == {
        "eveus2_stop_charging",
        "eveus2_one_charge",
        "eveus2_adaptive_mode",
        "eveus2_schedule_1_enabled",
        "eveus2_schedule_2_enabled",
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


def test_select_setup_creates_time_zone_entity() -> None:
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

    assert [entity.name for entity in added] == ["Time Zone"]
    assert {entity.unique_id for entity in added} == {"eveus2_time_zone"}


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

    # 4.9.2-rc2: +3 session-limit Number entities (Energy/Time/Money Limit).
    # 4.9.2-rc5: Time Limit and Money Limit removed.
    # 4.9.2-rc6: Energy Limit removed; only Charging Current remains.
    assert len(added) == 1
    names = [e.name for e in added]
    assert "Charging Current" in names
    assert "Energy Limit" not in names
    assert "Time Limit" not in names
    assert "Money Limit" not in names
    current = next(e for e in added if e.name == "Charging Current")
    assert current.unique_id == "eveus3_charging_current"


class _FakeEntityRegistry:
    """Minimal entity-registry stand-in exercising the SOC detector + purge."""

    def __init__(self, registered: set[str] | None = None) -> None:
        # registered: set of "domain.object_id" entity_ids present in the registry
        self.registered = set(registered or set())
        # map of (platform, domain, unique_id) -> entity_id for our entities
        self.by_unique: dict[tuple[str, str, str], str] = {}
        self.removed: list[str] = []

    def async_get(self, entity_id: str) -> object | None:
        return object() if entity_id in self.registered else None

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str | None:
        return self.by_unique.get((platform, domain, unique_id))

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


class _MigrateEntries:
    """Config-entries stub that applies update kwargs back onto the entry."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
