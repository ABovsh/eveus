"""Unit tests for entity construction."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from conftest import TEST_BASE_URL, TEST_HOST, EveusTestUpdater
from custom_components.eveus import common
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
        "hw_version": "Unknown",
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
    assert issubclass(common.EveusConnectionError, common.EveusError)
    assert set(common.__all__) == {
        "BaseEveusEntity",
        "ControlEntityMixin",
        "EveusSensorBase",
        "EveusDiagnosticSensor",
        "EveusUpdater",
        "CommandManager",
        "EveusError",
        "EveusConnectionError",
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
    assert entity.device_info["sw_version"] == "R3.05.2"


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
                "sw_version": "R3.05.2",
                "model": "Eveus EV Charger",
                "manufacturer": "Eveus",
                "hw_version": "W1.0",
                "serial_number": "EV-12345",
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
