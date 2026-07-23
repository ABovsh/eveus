"""Unit tests for entity construction."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory
from conftest import TEST_BASE_URL, TEST_HOST, EveusTestUpdater
from custom_components.eveus import common
from custom_components.eveus import binary_sensor as binary_sensor_mod
from custom_components.eveus.common_base import (
    BaseEveusEntity,
    EveusSensorBase,
    OptimisticControlMixin,
    WriteOnChangeMixin,
)
from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.sensor_definitions import OptimizedEveusSensor, SensorSpec, SensorType
from custom_components.eveus.button import (
    EveusResetCounterAButton,
    EveusResetCounterBButton,
)
from custom_components.eveus.switch import (
    BaseSwitchEntity,
    SWITCH_DESCRIPTIONS,
)
from custom_components.eveus.const import SESSION_ACTIVE_STATES


class _Updater:
    host = TEST_HOST
    available = True
    last_update_success = True

    def __init__(self) -> None:
        self.available = True
        self.last_update_success = True
        self.data = {
            "currentSet": "16",
            "evseEnabled": "1",
            "oneCharge": "0",
            "IEM1": "5.5",
            "powerMeas": "7200",
        }

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


def _make_binary_sensor(name: str, data: dict, *, available: bool = True):
    updater = EveusTestUpdater(data, available=available)
    descriptions = getattr(binary_sensor_mod, "BINARY_SENSORS", None)
    if descriptions is not None:
        description = next(item for item in descriptions if item.name == name)
        entity = binary_sensor_mod.EveusBinarySensor(updater, description, 1)
    else:
        class_name = {
            "Car Connected": "EveusCarConnectedBinarySensor",
            "Session Active": "EveusSessionActiveBinarySensor",
            "OCPP Connected": "EveusOcppConnectedBinarySensor",
        }[name]
        entity = getattr(binary_sensor_mod, class_name)(updater, 1)
    entity._entity_available = available
    return entity


def test_sensor_uses_fresh_coordinator_data_without_ttl_cache() -> None:
    updater = _Updater()
    spec = SensorSpec(
        key="power",
        name="Power",
        value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
        sensor_type=SensorType.MEASUREMENT,
    )
    sensor = OptimizedEveusSensor(updater, spec)
    sensor.hass = object()
    sensor.async_write_ha_state = lambda: None

    updater.data["powerMeas"] = "7200"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 7200

    updater.data["powerMeas"] = "1000"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1000


@pytest.mark.parametrize(
    "name,device_class,icon,entity_category",
    [
        ("Car Connected", BinarySensorDeviceClass.PLUG, "mdi:ev-plug-type2", None),
        ("Session Active", BinarySensorDeviceClass.RUNNING, "mdi:ev-station", None),
        (
            "OCPP Connected",
            BinarySensorDeviceClass.CONNECTIVITY,
            "mdi:cloud-check",
            EntityCategory.DIAGNOSTIC,
        ),
    ],
)
def test_binary_sensor_metadata_is_backward_compatible(
    name: str,
    device_class: str,
    icon: str,
    entity_category: EntityCategory | None,
) -> None:
    entity = _make_binary_sensor(name, {})

    assert entity.ENTITY_NAME == name
    assert entity.device_class == device_class
    assert entity.icon == icon
    assert entity.entity_category == entity_category


@pytest.mark.parametrize(
    "state,expected",
    [
        (4, True),
        (2, False),
        (7, None),
        (99, None),
    ],
)
def test_car_connected_binary_sensor_truth_table(
    state: int,
    expected: bool | None,
) -> None:
    entity = _make_binary_sensor("Car Connected", {"state": state})

    assert entity.is_on is expected


@pytest.mark.parametrize(
    "state,expected",
    [
        (next(iter(SESSION_ACTIVE_STATES)), True),
        (3, False),
        (99, None),
    ],
)
def test_session_active_binary_sensor_truth_table(
    state: int,
    expected: bool | None,
) -> None:
    entity = _make_binary_sensor("Session Active", {"state": state})

    assert entity.is_on is expected


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"ocppconnected": 1}, True),
        ({"ocppconnected": 0}, False),
        ({}, None),
    ],
)
def test_ocpp_connected_binary_sensor_truth_table(
    data: dict,
    expected: bool | None,
) -> None:
    entity = _make_binary_sensor("OCPP Connected", data)

    assert entity.is_on is expected


def test_base_entity_availability_grace_and_cache_paths() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.available is True
    assert entity.get_cached_data_value("powerMeas") == "7200"
    updater.data = {}
    assert entity.get_cached_data_value("powerMeas") is None
    assert entity.device_info["name"] == "Eveus EV Charger"

    updater.available = False
    entity._unavailable_since = 0
    entity._update_availability_state()
    assert entity.available is False


def test_base_entity_availability_stays_available_during_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr("custom_components.eveus.common_base.time.monotonic", lambda: now)
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    updater.available = False
    assert entity._update_availability_state() is False
    assert entity.available is True
    assert entity._unavailable_since == 100.0

    now = 105.0
    assert entity._update_availability_state(grace_period=10) is False
    assert entity.available is True

    now = 111.0
    assert entity._update_availability_state(grace_period=10) is True
    assert entity.available is False


def test_available_property_is_pure_until_coordinator_update() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    updater.available = False

    assert entity.available is True
    assert entity._unavailable_since is None


def test_sensor_coordinator_update_writes_only_when_state_changes() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    writes = 0

    def write_state() -> None:
        nonlocal writes
        writes += 1

    entity.hass = object()
    entity.async_write_ha_state = write_state

    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    updater.data["powerMeas"] = "6000"
    entity._handle_coordinator_update()

    assert writes == 2
    assert entity.native_value == 6000


def test_sensor_coordinator_update_clears_value_after_grace_period() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    entity.async_write_ha_state = lambda: None
    entity._handle_coordinator_update()

    updater.available = False
    entity._unavailable_since = 0
    entity._handle_coordinator_update()

    assert entity.available is False
    assert entity.native_value is None


def test_sensor_value_errors_are_contained() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="bad",
            name="Bad",
            value_fn=lambda updater, hass: (_ for _ in ()).throw(ValueError("boom")),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.native_value is None


def test_entity_unavailable_transition_is_quiet_at_normal_log_levels(
    caplog,
) -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    updater.available = False
    entity._unavailable_since = 0

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.common_base"):
        entity._update_availability_state()
        assert entity.available is False

    assert caplog.records == []


def test_base_entity_availability_restores_after_grace_period() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity._unavailable_since = 0
    entity._last_known_available = False

    entity._update_availability_state()
    assert entity.available is True
    assert entity._unavailable_since is None
    assert entity._last_known_available is True


def test_base_entity_availability_restore_log_is_rate_limited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity._unavailable_since = 0
    entity._last_known_available = False

    with caplog.at_level(logging.DEBUG, logger="custom_components.eveus.common_base"):
        entity._update_availability_state(label="Sensor")
        entity._unavailable_since = 0
        entity._update_availability_state(label="Sensor")

    restore_logs = [
        record
        for record in caplog.records
        if "connection restored" in record.getMessage()
    ]
    assert len(restore_logs) == 1


def test_base_entity_cached_data_value_uses_default_for_none_payload_value() -> None:
    updater = EveusTestUpdater({"powerMeas": None})
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: updater.data["powerMeas"],
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.get_cached_data_value("powerMeas", default="fallback") == "fallback"


def test_base_entity_device_info_falls_back_when_payload_is_malformed() -> None:
    updater = _Updater()
    updater.data = "not-a-dict"
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.device_info == {
        "identifiers": {("eveus", TEST_HOST)},
        "name": "Eveus EV Charger",
        "manufacturer": "Eveus",
        "model": "Eveus EV Charger",
        "sw_version": "Unknown",
        "configuration_url": TEST_BASE_URL,
    }


def test_control_availability_mixin_clears_optimistic_number_state_after_grace() -> None:
    updater = _Updater()
    entity = EveusCurrentNumber(updater, "16A")

    updater.available = False
    entity._unavailable_since = 0
    entity._optimistic_value = 12
    entity._last_known_available = True

    entity._update_availability_state()
    assert entity.available is False
    assert entity._optimistic_value is None


def test_control_unavailable_transition_is_quiet_at_normal_log_levels(caplog) -> None:
    updater = _Updater()
    entity = EveusCurrentNumber(updater, "16A")

    updater.available = False
    entity._unavailable_since = 0
    entity._optimistic_value = 12
    entity._last_known_available = True

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.common_base"):
        entity._update_availability_state()
        assert entity.available is False

    assert caplog.records == []


def test_switch_entities_keep_backward_compatible_unique_ids() -> None:
    updater = _Updater()

    assert (
        BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0]).unique_id
        == "eveus_stop_charging"
    )
    assert (
        BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[1]).unique_id
        == "eveus_one_charge"
    )
    assert EveusResetCounterAButton(updater).unique_id == "eveus_reset_counter_a"
    assert EveusResetCounterBButton(updater).unique_id == "eveus_reset_counter_b"


def test_number_entity_keeps_backward_compatible_unique_id_and_limits() -> None:
    entity = EveusCurrentNumber(_Updater(), "16A")

    assert entity.unique_id == "eveus_charging_current"
    assert entity.native_min_value == 7
    assert entity.native_max_value == 16


def test_entities_do_not_set_name_attr_so_translation_keys_are_used() -> None:
    updater = _Updater()
    sensor = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="battery_voltage",
            name="Battery Voltage",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    number = EveusCurrentNumber(updater, "16A")

    assert not hasattr(sensor, "_attr_name")
    assert sensor.translation_key == "battery_voltage"
    assert sensor.unique_id == "eveus_battery_voltage"
    assert not hasattr(number, "_attr_name")
    assert number.translation_key == "charging_current"
    assert number.unique_id == "eveus_charging_current"


def test_control_state_properties_do_not_mutate_cached_device_state() -> None:
    updater = _Updater()
    number = EveusCurrentNumber(updater, "16A")
    switch = BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[1])

    assert number.native_value == 16
    assert number._last_device_value is None
    assert switch.is_on is None
    assert switch._last_device_state is None


def test_common_module_exports_backward_compatible_symbols() -> None:
    assert common.BaseEveusEntity is BaseEveusEntity
    assert common.CommandManager.__name__ == "CommandManager"
    assert common.EveusUpdater.__name__ == "EveusUpdater"
    assert set(common.__all__) == {
        "BaseEveusEntity",
        "ControlEntityMixin",
        "EveusSensorBase",
        "EveusDiagnosticSensor",
        "EveusUpdater",
        "CommandManager",
        "EveusError",
    }


def test_base_entity_requires_entity_name() -> None:
    class NamelessEntity(BaseEveusEntity):
        pass

    with pytest.raises(NotImplementedError):
        NamelessEntity(_Updater())


def test_base_entity_async_added_to_hass_restores_state(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    restored: list[str] = []

    async def fake_get_last_state():
        return SimpleNamespace(state="42")

    async def fake_restore_state(state):
        restored.append(state.state)

    monkeypatch.setattr(entity, "async_get_last_state", fake_get_last_state)
    monkeypatch.setattr(entity, "_async_restore_state", fake_restore_state)

    import asyncio

    asyncio.run(entity.async_added_to_hass())

    assert restored == ["42"]
    assert entity._state_restored is True


def test_base_entity_async_added_to_hass_contains_restore_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = OptimizedEveusSensor(
        _Updater(),
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    async def broken_last_state():
        raise RuntimeError("restore failed")

    monkeypatch.setattr(entity, "async_get_last_state", broken_last_state)

    import asyncio

    asyncio.run(entity.async_added_to_hass())

    assert entity._state_restored is False


def test_base_entity_finalize_device_info_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = _Updater()
    updater.data = {}
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    entity._maybe_finalize_device_info()
    assert entity._device_info_finalized is False

    updater.data = {"verFWMain": "R3.05.2", "verFWWifi": "W1.0"}
    entity._maybe_finalize_device_info()
    assert entity._device_info_finalized is True
    assert entity.device_info["sw_version"] == "W1.0 (R3.05.2)"


def test_base_entity_finalize_waits_for_real_firmware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _Updater()
    updater.data = {}
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    monkeypatch.setattr(entity, "_build_device_info", lambda: {"sw_version": "Unknown"})
    updater.data = {"verFWMain": "Unknown"}

    entity._maybe_finalize_device_info()

    assert entity._device_info_finalized is False


def test_base_entity_finalize_updates_registry_device(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = _Updater()
    updater.data = {}
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    updater.data = {
        "verFWMain": "R3.05.2",
        "verFWWifi": "W1.0",
        "serialNum": "EV-12345",
    }
    updates: list[tuple[str, dict[str, object]]] = []

    class Registry:
        def async_get_device(self, *, identifiers):
            assert identifiers == {("eveus", TEST_HOST)}
            return SimpleNamespace(id="device-id")

        def async_update_device(self, device_id, **kwargs):
            updates.append((device_id, kwargs))

    monkeypatch.setattr("custom_components.eveus.common_base.dr.async_get", lambda hass: Registry())

    entity._maybe_finalize_device_info()

    assert updates == [
        (
            "device-id",
            {
                "sw_version": "W1.0 (R3.05.2)",
                "model": "Eveus EV Charger",
                "manufacturer": "Eveus",
                "hw_version": None,
                "serial_number": "EV-12345",
            },
        )
    ]


def test_base_entity_finalize_updates_registry_with_minimal_device_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _Updater()
    updater.data = {}
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    updater.data = {"verFWMain": "R3.05.2"}
    monkeypatch.setattr(
        entity,
        "_build_device_info",
        lambda: {
            "sw_version": "R3.05.2",
            "model": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "identifiers": {("eveus", TEST_HOST)},
        },
    )
    updates: list[tuple[str, dict[str, object]]] = []

    class Registry:
        def async_get_device(self, *, identifiers):
            return SimpleNamespace(id="device-id")

        def async_update_device(self, device_id, **kwargs):
            updates.append((device_id, kwargs))

    monkeypatch.setattr("custom_components.eveus.common_base.dr.async_get", lambda hass: Registry())

    entity._maybe_finalize_device_info()

    assert updates == [
        (
            "device-id",
            {
                "sw_version": "R3.05.2",
                "model": "Eveus EV Charger",
                "manufacturer": "Eveus",
                "hw_version": None,
            },
        )
    ]


def test_base_entity_finalize_skips_missing_registry_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _Updater()
    updater.data = {}
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    updater.data = {"verFWMain": "R3.05.2"}

    class Registry:
        def async_get_device(self, *, identifiers):
            return None

        def async_update_device(self, *args, **kwargs):
            raise AssertionError("must not update missing device")

    monkeypatch.setattr("custom_components.eveus.common_base.dr.async_get", lambda hass: Registry())

    entity._maybe_finalize_device_info()
    assert entity._device_info_finalized is True


def test_base_entity_finalize_skips_registry_update_without_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusTestUpdater({})
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    updater.data = {"verFWMain": "R3.05.2"}
    monkeypatch.setattr(
        entity,
        "_build_device_info",
        lambda: {"sw_version": "R3.05.2", "model": "Eveus EV Charger"},
    )

    class Registry:
        def async_get_device(self, *args, **kwargs):
            raise AssertionError("must not query registry without identifiers")

    monkeypatch.setattr("custom_components.eveus.common_base.dr.async_get", lambda hass: Registry())

    entity._maybe_finalize_device_info()
    assert entity._device_info_finalized is True


def test_optimistic_control_mixin_reconciles_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    class Optimistic(OptimisticControlMixin[int]):
        pass

    control = Optimistic()
    control._init_optimistic_control()
    monkeypatch.setattr("custom_components.eveus.common_base.time.time", lambda: 10.0)
    control._set_optimistic_value(7)

    assert control._optimistic_value_is_valid(12.0, 5.0) is True
    control._reconcile_with_device(7, 13.0, lambda optimistic, device: optimistic == device)
    assert control._optimistic_value is None
    assert control._last_device_value == 7
    assert control._last_successful_read == 13.0

    control._set_optimistic_value(9)
    control._reconcile_with_device(
        10,
        19.0,
        lambda optimistic, device: optimistic == device,
        mismatch_ttl=10.0,
    )
    assert control._optimistic_value == 9
    control._expire_optimistic_value(25.0, 10.0)
    assert control._optimistic_value is None


def test_write_on_change_mixin_suppresses_redundant_writes() -> None:
    class Writable(WriteOnChangeMixin):
        available = True

        def __init__(self) -> None:
            self.writes = 0
            self._init_write_on_change()

        def async_write_ha_state(self) -> None:
            self.writes += 1

    entity = Writable()

    assert entity._write_if_changed("on") is True
    assert entity._write_if_changed("on") is False
    entity.available = False
    assert entity._write_if_changed("on") is True
    assert entity.writes == 2


def test_sensor_base_value_errors_are_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenSensor(EveusSensorBase):
        ENTITY_NAME = "Broken"

        def _get_sensor_value(self):
            raise ValueError("boom")

    entity = BrokenSensor(_Updater())
    times = iter([1000.0, 1001.0, 1400.0])
    monkeypatch.setattr("custom_components.eveus.common_base.time.time", lambda: next(times))

    assert entity._update_native_value() is False
    assert entity._last_error_log == 1000.0
    assert entity._update_native_value() is False
    assert entity._last_error_log == 1000.0
    assert entity._update_native_value() is False
    assert entity._last_error_log == 1400.0


def test_sensor_base_value_error_boundary_at_exact_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rate-limit gate is a strict '>' (not '>='): at exactly
    ERROR_LOG_RATE_LIMIT since the last log, the log must NOT re-fire yet."""
    from custom_components.eveus.const import ERROR_LOG_RATE_LIMIT

    class BrokenSensor(EveusSensorBase):
        ENTITY_NAME = "BrokenBoundary"

        def _get_sensor_value(self):
            raise ValueError("boom")

    entity = BrokenSensor(_Updater())
    times = iter([1000.0, 1000.0 + ERROR_LOG_RATE_LIMIT])
    monkeypatch.setattr("custom_components.eveus.common_base.time.time", lambda: next(times))

    entity._update_native_value()
    assert entity._last_error_log == 1000.0
    # Second call lands exactly on the boundary: strictly-greater must reject it.
    entity._update_native_value()
    assert entity._last_error_log == 1000.0, (
        "exact-boundary elapsed time must not re-trigger the rate-limited log"
    )


def test_base_entity_construction_defaults_are_exact() -> None:
    """Pins every literal default set in BaseEveusEntity.__init__ so a flipped
    constant (has_entity_name, should_poll, initial availability bookkeeping,
    or the device_number default) is caught even though nothing else in the
    suite happens to construct with these exact defaults."""

    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Probe Entity"

    entity = Probe(_Updater())  # device_number defaults to 1

    assert entity._attr_has_entity_name is True
    assert entity._attr_should_poll is False
    assert entity._last_known_available is True
    # device_number=1 must produce the un-suffixed unique_id.
    assert entity.unique_id == "eveus_probe_entity"


def test_base_entity_requires_entity_name_exact_message() -> None:
    """Pins the exact NotImplementedError text (not just the exception type),
    which the plain `pytest.raises(NotImplementedError)` check elsewhere in
    this file does not verify."""

    class NamelessEntity(BaseEveusEntity):
        pass

    with pytest.raises(NotImplementedError, match="^ENTITY_NAME must be defined in child class$"):
        NamelessEntity(_Updater())


def test_suggested_object_id_is_a_property_not_a_bound_method() -> None:
    """suggested_object_id must be a @property: accessed without calling it,
    it has to already be the resolved string, not a bound method object."""

    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Probe Suggested Id"

    entity = Probe(_Updater())

    assert entity.suggested_object_id == "Probe Suggested Id"
    assert isinstance(entity.suggested_object_id, str)


def test_sensor_base_own_init_device_number_default() -> None:
    """EveusSensorBase.__init__ has its own device_number=1 default distinct
    from BaseEveusEntity's."""

    class Probe(EveusSensorBase):
        ENTITY_NAME = "Probe Sensor Default"

    entity = Probe(_Updater())  # device_number defaults to 1
    assert entity.unique_id == "eveus_probe_sensor_default"
    assert entity._last_error_log == 0.0


def test_update_extra_state_attributes_base_default_is_false() -> None:
    """Base EveusSensorBase._update_extra_state_attributes must report 'no
    change' by default; a stray True would force a spurious HA write on every
    single coordinator poll for every sensor that doesn't override it."""

    class Probe(EveusSensorBase):
        ENTITY_NAME = "Probe No Attrs"

    entity = Probe(_Updater())
    assert entity._update_extra_state_attributes() is False


def test_diagnostic_sensor_base_class_defaults() -> None:
    """EveusDiagnosticSensor's own class-level defaults (kept for backward
    compatibility) are otherwise never instantiated/asserted anywhere."""
    from custom_components.eveus.common_base import EveusDiagnosticSensor

    class Probe(EveusDiagnosticSensor):
        ENTITY_NAME = "Probe Diagnostic"

    entity = Probe(_Updater())
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    assert entity.icon == "mdi:information"


def test_update_native_value_when_unavailable_resets_to_none() -> None:
    """When the entity goes unavailable, the cached native value must reset to
    None exactly (not an empty string or other falsy stand-in), and the
    changed/unchanged return value must reflect a real transition."""

    class Probe(EveusSensorBase):
        ENTITY_NAME = "Probe Unavailable Value"

        def _get_sensor_value(self):
            return 42

    entity = Probe(_Updater())
    entity._entity_available = True
    assert entity._update_native_value() is True
    assert entity._attr_native_value == 42

    entity._entity_available = False
    # Transition available -> unavailable: value actually changes (42 -> None).
    assert entity._update_native_value() is True
    assert entity._attr_native_value is None

    # Still unavailable on the next poll: value stays None, so nothing changed.
    assert entity._update_native_value() is False
    assert entity._attr_native_value is None


def test_write_availability_only_truth_table() -> None:
    """Covers WriteOnChangeMixin._write_availability_only end to end: it must
    read the real `available` property, skip the write when unchanged, record
    the new value, and report True only on an actual transition."""

    class Probe(WriteOnChangeMixin):
        def __init__(self) -> None:
            self._available = True
            self.writes = 0
            self._init_write_on_change()

        @property
        def available(self):
            return self._available

        def async_write_ha_state(self) -> None:
            self.writes += 1

    entity = Probe()

    # First call: _last_written_available starts None, True != None -> writes.
    assert entity._write_availability_only() is True
    assert entity._last_written_available is True
    assert entity.writes == 1

    # Unchanged availability: must not write again.
    assert entity._write_availability_only() is False
    assert entity.writes == 1

    # Real transition: must write again and update bookkeeping.
    entity._available = False
    assert entity._write_availability_only() is True
    assert entity._last_written_available is False
    assert entity.writes == 2


def test_write_on_change_mixin_unset_sentinel_is_not_none() -> None:
    """The change-detection sentinel must be a dedicated object(), not None:
    a legitimately-None first value has to still be treated as 'changed' on
    the very first write."""

    class Writable(WriteOnChangeMixin):
        available = True

        def __init__(self) -> None:
            self.writes = 0
            self._init_write_on_change()

        def async_write_ha_state(self) -> None:
            self.writes += 1

    entity = Writable()
    # Pre-align availability bookkeeping so only the value sentinel is under
    # test: otherwise `available_now == self._last_written_available`
    # (True == None -> False) would force a "changed" result on the first
    # call regardless of the sentinel, masking the mutation.
    entity._last_written_available = True

    # A real value of None must still count as a change on the first call.
    assert entity._write_if_changed(None) is True
    assert entity.writes == 1
    # Same (None) value again: no change.
    assert entity._write_if_changed(None) is False
    assert entity.writes == 1


def test_preserve_finalized_metadata_requires_both_conditions() -> None:
    """`merged.get(key) == fallback AND old.get(key) not in (None, fallback)`
    must be a real AND: a custom (non-fallback) value from the new payload
    must never be overwritten by old data just because old had something."""
    from custom_components.eveus.common_base import _preserve_finalized_metadata

    old = {"model": "RealOldModel", "manufacturer": "RealOldMaker"}
    new = {"model": "CustomModel", "manufacturer": "Eveus"}
    # model: new value is NOT the fallback -> must survive untouched.
    # manufacturer: new value IS the fallback -> must be replaced by old's.
    merged = _preserve_finalized_metadata(old, new)

    assert merged["model"] == "CustomModel"
    assert merged["manufacturer"] == "RealOldMaker"


def test_preserve_finalized_metadata_serial_number_key_is_exact() -> None:
    """When `new` already carries its own serial_number, it must never be
    clobbered by `old`'s serial - the guard checks the real 'serial_number'
    key, not a look-alike."""
    from custom_components.eveus.common_base import _preserve_finalized_metadata

    old = {"serial_number": "OLD456"}
    new = {"serial_number": "NEW123"}

    merged = _preserve_finalized_metadata(old, new)

    assert merged["serial_number"] == "NEW123"


def test_preserve_finalized_metadata_fills_missing_serial_from_old() -> None:
    """The complementary path: when `new` has no serial at all, `old`'s must
    still be carried forward."""
    from custom_components.eveus.common_base import _preserve_finalized_metadata

    old = {"serial_number": "OLD456"}
    new = {}

    merged = _preserve_finalized_metadata(old, new)

    assert merged["serial_number"] == "OLD456"


def test_build_device_info_uses_updater_scheme_and_fw_fallback() -> None:
    """_build_device_info must read the real `scheme` and `_init_fw_fallback`
    attributes off the updater (a mistyped getattr key would silently fall
    back to hardcoded defaults instead)."""

    class _SchemeUpdater:
        host = TEST_HOST
        available = True
        last_update_success = True
        scheme = "https"
        _init_fw_fallback = "GRM-FALLBACK-9.9"

        def __init__(self) -> None:
            # No verFWMain/firmware in the payload: init_fw_fallback must be
            # the only source for the module firmware string.
            self.data = {"verFWWifi": "1PGRW-APP-1.0"}

        def async_add_listener(self, *args: object, **kwargs: object):
            return lambda: None

    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Probe Device Info"

    entity = Probe(_SchemeUpdater())
    info = entity._build_device_info()

    assert info["configuration_url"] == f"https://{TEST_HOST}"
    assert "GRM-FALLBACK-9.9" in info["sw_version"]


def test_maybe_finalize_device_info_preserves_real_metadata_over_fallback() -> None:
    """Integration path for `self._attr_device_info or {}` at the finalize
    call site: once finalized with real vendor metadata, a later poll that
    degrades to generic fallback strings must not overwrite the real values
    (a flipped `or` -> `and` there would pass an empty dict as `old`, losing
    them)."""

    class _MetaUpdater:
        host = TEST_HOST
        available = True
        last_update_success = True
        scheme = "http"

        def __init__(self, data: dict) -> None:
            self.data = data

        def async_add_listener(self, *args: object, **kwargs: object):
            return lambda: None

    updater = _MetaUpdater(
        {
            "verFWMain": "R3.05.2",
            "verFWWifi": "W1.0",
            "model": "Eveus 32A Real Model",
            "manufacturer": "Eveus Real Vendor",
        }
    )

    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Probe Metadata Preserve"

    entity = Probe(updater)
    entity.hass = None  # short-circuits the registry-write half of the method
    assert entity._device_info_finalized is True
    assert entity._attr_device_info["model"] == "Eveus 32A Real Model"

    # Next poll degrades to fallback strings (device omitted the fields).
    updater.data = {"verFWMain": "R3.05.3", "verFWWifi": "W1.0"}
    entity._maybe_finalize_device_info()

    assert entity._attr_device_info["model"] == "Eveus 32A Real Model"
    assert entity._attr_device_info["manufacturer"] == "Eveus Real Vendor"


def test_device_registry_write_guards_use_exact_finalized_key() -> None:
    """Both `_device_registry_finalized` getattr guards must key off the real
    attribute name: the early 'already finalized, unchanged' skip, and the
    'skip the second entity's write for the same shared updater' skip."""
    from unittest.mock import MagicMock

    class _RegistryUpdater:
        host = TEST_HOST
        available = True
        last_update_success = True
        scheme = "http"
        _device_registry_finalized = False

        def __init__(self, data: dict) -> None:
            self.data = data

        def async_add_listener(self, *args: object, **kwargs: object):
            return lambda: None

    updater = _RegistryUpdater({})

    class ProbeA(BaseEveusEntity):
        ENTITY_NAME = "Probe Registry A"

    class ProbeB(BaseEveusEntity):
        ENTITY_NAME = "Probe Registry B"

    entities = [ProbeA(updater), ProbeB(updater)]
    for entity in entities:
        entity.hass = object()

    registry = MagicMock()
    registry.async_get_device.return_value = SimpleNamespace(id="device-id")
    import custom_components.eveus.common_base as common_base_mod

    orig_async_get = common_base_mod.dr.async_get
    common_base_mod.dr.async_get = lambda hass: registry
    try:
        updater.data = {
            "verFWMain": "R3.05.2",
            "verFWWifi": "W1.0",
        }
        for entity in entities:
            entity._maybe_finalize_device_info()
    finally:
        common_base_mod.dr.async_get = orig_async_get

    # Shared updater: only the FIRST entity's finalize should have written the
    # registry once the guard flips _device_registry_finalized to True.
    assert registry.async_update_device.call_count == 1
    assert updater._device_registry_finalized is True


def test_device_registry_finalized_reset_on_metadata_drift() -> None:
    """After finalization, metadata drift (a real change, e.g. firmware OTA)
    must reset `_device_registry_finalized` to False so the registry gets one
    more refresh - and it has to be set to exactly False, not None/True."""
    from unittest.mock import MagicMock

    class _DriftUpdater:
        host = TEST_HOST
        available = True
        last_update_success = True
        scheme = "http"
        _device_registry_finalized = True

        def __init__(self, data: dict) -> None:
            self.data = data

        def async_add_listener(self, *args: object, **kwargs: object):
            return lambda: None

    updater = _DriftUpdater(
        {"verFWMain": "R3.05.2", "verFWWifi": "W1.0"}
    )

    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Probe Drift"

    entity = Probe(updater)
    entity.hass = object()
    assert entity._device_info_finalized is True

    registry = MagicMock()
    registry.async_get_device.return_value = SimpleNamespace(id="device-id")
    import custom_components.eveus.common_base as common_base_mod

    orig_async_get = common_base_mod.dr.async_get
    common_base_mod.dr.async_get = lambda hass: registry
    try:
        # Firmware OTA: metadata genuinely drifts after finalization.
        updater.data = {"verFWMain": "R3.05.9", "verFWWifi": "W1.0"}
        entity._maybe_finalize_device_info()
    finally:
        common_base_mod.dr.async_get = orig_async_get

    # If the reset-to-False assignment were skipped or mutated (True/None),
    # the write guard right after (`not getattr(..., False)`) would see a
    # truthy value and skip the registry write entirely.
    assert registry.async_update_device.call_count == 1


def test_optimistic_control_init_exact_initial_values() -> None:
    """Pins every numeric/None default `_init_optimistic_control` sets, since
    nothing else in the suite asserts these immediately post-init."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()

    assert control._optimistic_value_time == 0.0
    assert control._last_device_value is None
    assert control._last_successful_read == 0.0


def test_reconcile_with_device_default_mismatch_ttl_is_exactly_ten() -> None:
    """`_reconcile_with_device`'s mismatch_ttl default (10.0) is only ever
    exercised via the default in production call sites; pin the exact
    boundary here since nothing overrides it in existing tests."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    # age = 10.5s > the true 10.0 default -> must clear even though confirm_fn
    # says "not confirmed" and the clock never went backward.
    control._reconcile_with_device(
        99,
        stamp + 10.5,
        lambda optimistic, device: False,
    )
    assert control._optimistic_value is None


def test_reconcile_with_device_mismatch_ttl_boundary_is_strict_greater() -> None:
    """age > mismatch_ttl must be a strict '>': exactly at the ttl, an
    unconfirmed-but-still-within-window optimistic value must survive."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    control._reconcile_with_device(
        99,
        stamp + 10.0,  # age == mismatch_ttl exactly
        lambda optimistic, device: False,
        mismatch_ttl=10.0,
    )
    assert control._optimistic_value == 5


def test_reconcile_with_device_backward_clock_boundary_is_strict_less_than() -> None:
    """age < 0 must be a strict '<': age == 0 exactly (no clock movement) must
    NOT be treated as a backward-clock invalidation."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    control._reconcile_with_device(
        99,
        stamp,  # age == 0 exactly
        lambda optimistic, device: False,
        mismatch_ttl=1000.0,
    )
    assert control._optimistic_value == 5


def test_optimistic_value_valid_boundary_age_zero_is_valid() -> None:
    """`0 <= age < ttl`: age == 0 exactly must be valid (the left bound is
    inclusive)."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    assert control._optimistic_value_is_valid(stamp, 10.0) is True


def test_optimistic_value_valid_boundary_age_equals_ttl_is_invalid() -> None:
    """`0 <= age < ttl`: age == ttl exactly must already be expired (the right
    bound is exclusive)."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    assert control._optimistic_value_is_valid(stamp + 10.0, 10.0) is False


def test_expire_optimistic_value_boundary_age_zero_survives() -> None:
    """The expiry guard mirrors the validity check: age == 0 exactly must NOT
    be expired."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    control._expire_optimistic_value(stamp, 10.0)
    assert control._optimistic_value == 5


def test_expire_optimistic_value_boundary_age_equals_ttl_expires() -> None:
    """age == ttl exactly must expire (mirrors the exclusive right bound in
    the validity check)."""

    control = OptimisticControlMixin()
    control._init_optimistic_control()
    control._set_optimistic_value(5)
    stamp = control._optimistic_value_time

    control._expire_optimistic_value(stamp + 10.0, 10.0)
    assert control._optimistic_value is None
