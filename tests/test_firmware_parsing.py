"""Firmware/payload parsing & outlier rejection.

Tests for safe_value helpers, safe_str, fractional/boolean/non-finite current,
temperature/measurement/cost outliers, and device_info built from firmware fields.
"""
from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from conftest import EveusTestUpdater, spec_value_fn, TEST_HOST
from custom_components.eveus.utils import get_safe_value
from custom_components.eveus.sensor_definitions import (
    get_voltage,
    get_current,
    get_power,
    get_session_energy,
    get_leak_current,
    get_connection_quality,
    get_sensor_specifications,
)
from custom_components.eveus import ev_sensors


def test_get_safe_value_rejects_bool_for_numeric():
    assert get_safe_value({"x": True}, "x", float, default=None) is None
    assert get_safe_value({"x": False}, "x", int, default=None) is None
    # but real numbers still work
    assert get_safe_value({"x": "1.5"}, "x", float) == 1.5


def test_value_getter_rejects_bool():
    assert get_voltage(EveusTestUpdater({"voltMeas1": True}), None) is None


def test_negative_voltage_returns_none():
    assert get_voltage(EveusTestUpdater({"voltMeas1": -5}), None) is None
    assert get_voltage(EveusTestUpdater({"voltMeas1": 230}), None) == 230


def test_negative_current_returns_none():
    assert get_current(EveusTestUpdater({"curMeas1": -1.2}), None) is None


def test_negative_power_returns_none():
    assert get_power(EveusTestUpdater({"powerMeas": -10}), None) is None


def test_current_set_displays_sub_minimum_but_rejects_negative_and_over_max():
    # A sub-7 A setpoint set directly on the charger is a legitimate reported
    # value and must be displayed; only negative / above-model values are corrupt.
    assert spec_value_fn("current_set")(EveusTestUpdater({"currentSet": 5}), None) == 5
    assert spec_value_fn("current_set")(EveusTestUpdater({"currentSet": 7}), None) == 7
    assert spec_value_fn("current_set")(EveusTestUpdater({"currentSet": 16}), None) == 16
    assert spec_value_fn("current_set")(EveusTestUpdater({"currentSet": -1}), None) is None


def test_leak_current_negative_returns_none():
    assert get_leak_current(EveusTestUpdater({"leakValue": -3}), None) is None


def test_session_energy_negative_returns_none():
    assert get_session_energy(EveusTestUpdater({"sessionEnergy": -0.5}), None) is None


def test_connection_quality_nan_returns_none():
    assert get_connection_quality(
        EveusTestUpdater({}, quality={"success_rate": float("nan")}), None
    ) is None
    assert get_connection_quality(
        EveusTestUpdater({}, quality={"success_rate": float("inf")}), None
    ) is None


def test_connection_quality_bool_returns_none():
    assert get_connection_quality(
        EveusTestUpdater({}, quality={"success_rate": True}), None
    ) is None


def test_connection_quality_valid_clamped():
    assert get_connection_quality(EveusTestUpdater({}, quality={"success_rate": 150}), None) == 100
    assert get_connection_quality(EveusTestUpdater({}, quality={"success_rate": -5}), None) == 0
    assert get_connection_quality(EveusTestUpdater({}, quality={"success_rate": 87.4}), None) == 87


def test_counter_cost_sensors_use_monetary_iso_unit():
    by_key = {s.key: s for s in get_sensor_specifications(1)}
    for key in ("counter_a_cost", "counter_b_cost"):
        spec = by_key[key]
        assert spec.device_class == SensorDeviceClass.MONETARY
        assert spec.unit == "UAH"
        assert spec.state_class == SensorStateClass.TOTAL


class _SessionEnergyHolder:
    def __init__(self, value):
        self._updater = type("U", (), {"data": {"sessionEnergy": value}})()


def test_ev_energy_charged_rejects_negative():
    obj = _SessionEnergyHolder(-1.0)
    assert ev_sensors.BaseEVHelperSensor._get_energy_charged(obj) is None
    obj2 = _SessionEnergyHolder(3.5)
    assert ev_sensors.BaseEVHelperSensor._get_energy_charged(obj2) == 3.5


from custom_components.eveus import utils


@pytest.mark.parametrize("bad", [True, False])
def test_get_safe_value_rejects_bool(bad: bool) -> None:
    assert utils.get_safe_value({"state": bad}, "state", int) is None


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_get_safe_value_rejects_non_finite(bad: float) -> None:
    assert utils.get_safe_value({"x": bad}, "x", int) is None
    assert utils.get_safe_value({"x": bad}, "x", float) is None


@pytest.mark.parametrize("bad", [4.9, 0.9, 1.2, -0.5])
def test_get_safe_value_int_rejects_fractional_float(bad: float) -> None:
    assert utils.get_safe_value({"x": bad}, "x", int) is None


@pytest.mark.parametrize("good,expected", [(4.0, 4), (0.0, 0), (16, 16), ("7", 7)])
def test_get_safe_value_int_accepts_integral_values(good: object, expected: int) -> None:
    assert utils.get_safe_value({"x": good}, "x", int) == expected


def test_get_safe_value_float_still_accepts_fractional() -> None:
    assert utils.get_safe_value({"x": 4.9}, "x", float) == 4.9


def test_switch_state_getter_rejects_fractional() -> None:
    # A fractional evseEnabled must not truncate to a definite on/off.
    assert utils.get_safe_value({"evseEnabled": 0.9}, "evseEnabled", int) is None


from custom_components.eveus import sensor_definitions as sd


def test_ground_status_ignores_fractional() -> None:
    updater = EveusTestUpdater({"ground": 0.9})
    assert sd.get_ground_status(updater, None) is None


def test_adaptive_status_ignores_fractional() -> None:
    updater = EveusTestUpdater({"aiStatus": 1.9})
    assert sd.get_adaptive_charging_state(updater, None) is None


def test_voltage_getter_rejects_outlier() -> None:
    assert sd.get_voltage(EveusTestUpdater({"voltMeas1": 99999}), None) is None
    assert sd.get_voltage(EveusTestUpdater({"voltMeas1": 230}), None) == 230


def test_current_getter_rejects_outlier() -> None:
    assert sd.get_current(EveusTestUpdater({"curMeas1": 999}), None) is None
    assert sd.get_current(EveusTestUpdater({"curMeas1": 16}), None) == 16


def test_power_getter_rejects_outlier() -> None:
    assert sd.get_power(EveusTestUpdater({"powerMeas": 999999}), None) is None
    assert sd.get_power(EveusTestUpdater({"powerMeas": 7200}), None) == 7200


def test_current_set_getter_rejects_above_model_max() -> None:
    assert spec_value_fn("current_set")(EveusTestUpdater({"currentSet": 999}), None) is None


def test_adaptive_telemetry_rejects_outliers() -> None:
    assert spec_value_fn("adaptive_current_limit")(EveusTestUpdater({"aiModecurrent": 999}), None) is None


@pytest.mark.parametrize(
    "getter,key,good,expected",
    [
        (sd.get_box_temperature, "temperature1", 42, 42),
        (sd.get_plug_temperature, "temperature2", 55, 55),
        (sd.get_battery_voltage, "vBat", 3.0, 3.0),
        (sd.get_leak_current, "leakValue", 5, 5),
        (sd.get_leak_current_peak, "leakValueH", 9, 9),
        (sd.get_primary_rate_cost, "tarif", 450, 4.5),
        (sd.get_rate2_cost, "tarifAValue", 600, 6.0),
        (sd.get_rate3_cost, "tarifBValue", 720, 7.2),
    ],
)
def test_measurement_getters_reject_finite_outliers(getter, key, good, expected) -> None:
    assert getter(EveusTestUpdater(data={key: 1e100}), None) is None
    assert getter(EveusTestUpdater(data={key: good}), None) == pytest.approx(expected)


def test_temperature_rejects_impossible_negative() -> None:
    # Below the -40 floor is corrupt; a plausible cold reading still passes.
    assert sd.get_box_temperature(EveusTestUpdater(data={"temperature1": -100}), None) is None
    assert sd.get_box_temperature(EveusTestUpdater(data={"temperature1": -20}), None) == -20


def test_active_rate_cost_rejects_finite_outlier() -> None:
    outlier = EveusTestUpdater(data={"activeTarif": 0, "tarif": 1e100})
    assert sd.get_active_rate_cost(outlier, None) is None
    good = EveusTestUpdater(data={"activeTarif": 0, "tarif": 450})
    assert sd.get_active_rate_cost(good, None) == pytest.approx(4.5)


from custom_components.eveus.utils import (
    _safe_str,
    get_device_info,
)


def test_safe_str_rejects_non_string_values() -> None:
    assert _safe_str(True) == "Unknown"
    assert _safe_str([1, 2]) == "Unknown"
    assert _safe_str({"a": 1}) == "Unknown"
    assert _safe_str(("x",)) == "Unknown"


def test_safe_str_keeps_real_strings() -> None:
    assert _safe_str("R3.05.2") == "R3.05.2"


def test_device_info_ignores_boolean_firmware() -> None:
    info = get_device_info(TEST_HOST, {"verFWMain": True}, 1)
    assert info["sw_version"] == "Unknown"


def test_payload_rejects_fractional_current_set() -> None:
    from custom_components.eveus._payload import PayloadError, validate_main_payload

    with pytest.raises(PayloadError):
        validate_main_payload({"state": 4, "currentSet": 7.5})


def test_payload_rejects_fractional_current_set_as_string() -> None:
    from custom_components.eveus._payload import PayloadError, validate_main_payload

    with pytest.raises(PayloadError):
        validate_main_payload({"state": 4, "currentSet": "7.5"})


def test_payload_accepts_integral_current_set() -> None:
    from custom_components.eveus._payload import validate_main_payload

    assert validate_main_payload({"state": 4, "currentSet": 16.0})["currentSet"] == 16.0
    assert validate_main_payload({"state": 4, "currentSet": 16})["currentSet"] == 16


def test_safe_str_rejects_non_finite_floats() -> None:
    assert _safe_str(float("nan")) == "Unknown"
    assert _safe_str(float("inf")) == "Unknown"
    assert _safe_str(3.5) == "3.5"


def test_device_info_alias_fallback_survives_corrupt_primary() -> None:
    info = get_device_info(
        TEST_HOST,
        {"verFWMain": True, "firmware": "1.2.3", "serialNum": {}, "stationId": "ST99"},
    )
    assert info["sw_version"] == "1.2.3"
    assert info["serial_number"] == "ST99"


@pytest.mark.parametrize("bad_serial", [True, {"a": 1}, ["x"], ""])
def test_malformed_serial_is_dropped(bad_serial) -> None:
    info = get_device_info(TEST_HOST, {"serialNum": bad_serial}, 1)
    assert "serial_number" not in info


def test_valid_serial_is_kept() -> None:
    info = get_device_info(TEST_HOST, {"serialNum": " SN123 "}, 1)
    assert info["serial_number"] == "SN123"


def test_device_info_omits_hw_version():
    info = utils.get_device_info(
        TEST_HOST,
        {"verFWMain": "GRM070A-R3.05.2", "verFWWifi": "1PGRW001A-R3.05.2"},
    )
    assert "hw_version" not in info
    # Both firmware strings are folded into sw_version, app board (verFWWifi)
    # leading; neither is exposed as a hardware revision.
    assert info["sw_version"] == "1PGRW001A-R3.05.2 (GRM070A-R3.05.2)"


def test_device_info_omits_hw_version_even_with_legacy_hardware_key():
    info = utils.get_device_info(TEST_HOST, {"verFWMain": "x1", "hardware": "h1"})
    assert "hw_version" not in info


def test_v22_metadata_strings_are_length_capped():
    huge = "x" * 100_000
    assert len(_safe_str(huge)) == 128


def test_v22_capped_reader_rejects_oversized_chunked_body():
    from custom_components.eveus._payload import PayloadError, read_json_capped

    class _Content:
        async def iter_chunked(self, n):
            # Stream more than the cap with no Content-Length set.
            for _ in range(5):
                yield b"x" * 300_000

    class _Resp:
        content_length = None
        content = _Content()

    with pytest.raises(PayloadError):
        import asyncio
        asyncio.run(read_json_capped(_Resp(), limit=1_000_000))


def test_v22_capped_reader_parses_small_body():
    from custom_components.eveus._payload import read_json_capped
    import asyncio

    class _Content:
        async def iter_chunked(self, n):
            yield b'{"state": 4, "currentSet": 16}'

    class _Resp:
        content_length = 30
        content = _Content()

    data = asyncio.run(read_json_capped(_Resp()))
    assert data == {"state": 4, "currentSet": 16}


def test_capped_reader_tolerates_invalid_utf8_in_string_fields() -> None:
    """R3.01.8 units with an unset serial return raw non-UTF-8 bytes in serialNum.

    json.loads(bytes) does a strict UTF-8 decode first, so the whole poll/setup
    failed with UnicodeDecodeError even though the payload is otherwise valid.
    """
    from custom_components.eveus._payload import read_json_capped
    import asyncio

    body = b'{"serialNum": "' + b"\xff" * 17 + b'", "state": 2, "currentSet": 20}'

    class _Content:
        async def iter_chunked(self, n):
            yield body

    class _Resp:
        content_length = len(body)
        content = _Content()

    data = asyncio.run(read_json_capped(_Resp()))
    assert data["state"] == 2
    assert data["currentSet"] == 20
    assert data["serialNum"] == "�" * 17


def test_garbage_serial_of_replacement_chars_is_dropped() -> None:
    info = get_device_info(TEST_HOST, {"serialNum": "�" * 17}, 1)
    assert "serial_number" not in info


def test_garbage_serial_falls_back_to_station_id() -> None:
    info = get_device_info(
        TEST_HOST, {"serialNum": "�" * 17, "stationId": "ST42"}, 1
    )
    assert info["serial_number"] == "ST42"


def test_payload_rejects_current_above_global_ceiling_without_model() -> None:
    from custom_components.eveus import _payload
    with pytest.raises(_payload.PayloadError):
        _payload.validate_main_payload({"state": 2, "currentSet": 999})


def test_payload_accepts_max_supported_current_without_model() -> None:
    from custom_components.eveus import _payload
    from custom_components.eveus.const import MODEL_MAX_CURRENT
    top = max(MODEL_MAX_CURRENT.values())
    payload = {"state": 2, "currentSet": top}
    assert _payload.validate_main_payload(payload) is payload


def test_payload_accepts_sub_minimum_current() -> None:
    from custom_components.eveus import _payload
    for amps in (1, 3, 5, 6):
        payload = {"state": 4, "currentSet": amps}
        assert _payload.validate_main_payload(payload) is payload


def test_payload_still_rejects_negative_current() -> None:
    from custom_components.eveus import _payload
    with pytest.raises(_payload.PayloadError):
        _payload.validate_main_payload({"state": 4, "currentSet": -1})


from custom_components.eveus.utils import normalize_soc_input


@pytest.mark.parametrize(
    "value",
    [True, False, 10 ** 400, -(10 ** 400)],
)
def test_normalize_soc_input_falls_back_on_bool_and_overflow(value) -> None:
    # default is returned for bool (would otherwise float() to 0.0/1.0) and for
    # an integer too large to convert to float (OverflowError).
    assert normalize_soc_input("battery_capacity", value, 50.0) == 50.0


def test_normalize_soc_input_still_clamps_real_values() -> None:
    assert normalize_soc_input("initial_soc", 150, 20.0) == 100.0
    assert normalize_soc_input("initial_soc", "42", 20.0) == 42.0


class _RespOF:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def raise_for_status(self):
        return None

    async def json(self, **kw):
        return self._payload

    @property
    def content_length(self):
        import json as _json
        body = self._payload if isinstance(self._payload, str) else _json.dumps(self._payload)
        return len(body.encode())

    @property
    def content(self):
        import json as _json
        body = self._payload if isinstance(self._payload, str) else _json.dumps(self._payload)
        return _CappedStreamReaderOF(body.encode())


class _CappedStreamReaderOF:
    def __init__(self, raw):
        self._raw = raw

    async def iter_chunked(self, size):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


class _SessionOF:
    def __init__(self, payload):
        self._payload = payload

    def post(self, url, **kw):
        return _RespOF(self._payload)


class _HassOF:
    loop = None


def test_coordinator_rejects_overflowing_current_set(monkeypatch) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater
    from conftest import TEST_HOST, TEST_USERNAME, TEST_PASSWORD

    payload = {"state": 2, "currentSet": 10 ** 400}
    monkeypatch.setattr(
        common_network, "async_get_clientsession", lambda hass: _SessionOF(payload)
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _HassOF())

    with pytest.raises(UpdateFailed):
        import asyncio
        asyncio.run(updater._async_update_data())
    assert updater.available is False


class _RespRC7:
    def __init__(self, *, status: int = 200, payload: object = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"state": 2, "currentSet": 16}

    async def __aenter__(self) -> "_RespRC7":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, **kwargs: object) -> object:
        import json
        if isinstance(self.payload, str):
            return json.loads(self.payload)
        return self.payload

    @property
    def content_length(self):
        import json as _json
        body = self.payload if isinstance(self.payload, str) else _json.dumps(self.payload)
        return len(body.encode())

    @property
    def content(self):
        import json as _json
        body = self.payload if isinstance(self.payload, str) else _json.dumps(self.payload)
        return _CappedStreamReaderRC7(body.encode())


class _CappedStreamReaderRC7:
    def __init__(self, raw):
        self._raw = raw

    async def iter_chunked(self, size):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


class _SessionRC7:
    def __init__(self, response) -> None:
        self.response = response

    def post(self, url: str, **kwargs: object):
        return self.response


class _HassRC7:
    loop = None


def _run_update_rc7(payload, monkeypatch):
    import asyncio
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater
    from conftest import TEST_HOST, TEST_USERNAME, TEST_PASSWORD

    session = _SessionRC7(_RespRC7(payload=payload))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _HassRC7())
    return asyncio.run(updater._async_update_data())


def test_coordinator_rejects_payload_without_current_set(monkeypatch) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    with pytest.raises(UpdateFailed):
        _run_update_rc7({"state": 2}, monkeypatch)


def test_coordinator_accepts_payload_with_current_set(monkeypatch) -> None:
    data = _run_update_rc7({"state": 2, "currentSet": 16}, monkeypatch)
    assert data == {"state": 2, "currentSet": 16}


def test_coordinator_rejects_fractional_state(monkeypatch) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    with pytest.raises(UpdateFailed):
        _run_update_rc7({"state": 4.9, "currentSet": 16}, monkeypatch)


def test_runtime_validation_rejects_fractional_state() -> None:
    from custom_components.eveus._payload import validate_main_payload

    with pytest.raises(ValueError):
        validate_main_payload({"state": 2.9, "currentSet": 16}, "16A")


@pytest.mark.parametrize("bad", [True, False])
def test_runtime_validation_rejects_boolean_state(bad: bool) -> None:
    from custom_components.eveus._payload import validate_main_payload

    with pytest.raises(ValueError):
        validate_main_payload({"state": bad, "currentSet": 16}, "16A")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, False])
def test_validate_finite_number_rejects_bad_input(bad) -> None:
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.eveus import number as number_mod

    with pytest.raises(HomeAssistantError):
        number_mod._validate_finite_number(bad, "Charging Current")


@pytest.mark.parametrize("good", [7, 16.0, 100])
def test_validate_finite_number_accepts_normal_input(good) -> None:
    from custom_components.eveus import number as number_mod

    assert number_mod._validate_finite_number(good, "Limit") == float(good)


def _fw_diag_sensor(updater):
    from custom_components.eveus.sensor_definitions import (
        OptimizedEveusSensor,
        SensorSpec,
        SensorType,
    )

    spec = SensorSpec(
        key="test_fw_diag",
        name="Test FW Diag",
        value_fn=lambda _updater, _hass: 1,
        sensor_type=SensorType.DIAGNOSTIC,
    )
    from conftest import disable_state_writes
    sensor = OptimizedEveusSensor(updater, spec)
    disable_state_writes(sensor)
    return sensor


def test_device_info_refreshes_on_firmware_drift() -> None:
    updater = EveusTestUpdater({"verFWMain": "1.0"})
    sensor = _fw_diag_sensor(updater)
    sensor._maybe_finalize_device_info()
    assert sensor._attr_device_info["sw_version"] == "1.0"
    assert sensor._device_info_finalized is True

    updater.data = {"verFWMain": "2.0"}
    sensor._maybe_finalize_device_info()
    assert sensor._attr_device_info["sw_version"] == "2.0"


def test_registry_hw_version_cleared_when_info_already_final(monkeypatch) -> None:
    from types import SimpleNamespace
    from custom_components.eveus import common_base
    from custom_components.eveus.common_base import BaseEveusEntity

    class _Sensor(BaseEveusEntity):
        ENTITY_NAME = "Probe Sensor"

    updater = EveusTestUpdater({"verFWMain": "GRM070A-R3.05.2"})
    entity = _Sensor(updater, 1)
    entity.hass = SimpleNamespace()
    assert entity._device_info_finalized is True  # firmware known at construction

    updated: dict = {}

    class _Registry:
        def async_get_device(self, identifiers):
            return SimpleNamespace(id="dev1")

        def async_update_device(self, device_id, **kwargs):
            updated.update(kwargs)

    monkeypatch.setattr(
        common_base.dr, "async_get", lambda hass: _Registry(), raising=False
    )

    entity._maybe_finalize_device_info()
    assert updated.get("hw_version", "missing") is None
    assert updater._device_registry_finalized is True

    # And the write happens only once per runtime.
    updated.clear()
    entity._maybe_finalize_device_info()
    assert updated == {}


def test_finalized_metadata_survives_fallback_fields() -> None:
    from custom_components.eveus.common_base import _preserve_finalized_metadata

    old = {
        "model": "Eveus Pro 32A",
        "manufacturer": "Eveus Ltd",
        "sw_version": "R3.05.2",
        "serial_number": "SN123",
    }
    new = {
        "model": "Eveus EV Charger",  # fallback
        "manufacturer": "Eveus",  # fallback
        "sw_version": "R3.05.3",
    }
    merged = _preserve_finalized_metadata(old, new)
    assert merged["model"] == "Eveus Pro 32A"
    assert merged["manufacturer"] == "Eveus Ltd"
    assert merged["sw_version"] == "R3.05.3"
    assert merged["serial_number"] == "SN123"


def test_command_failure_log_uses_error_type_not_repr() -> None:
    import asyncio
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    import aiohttp
    from custom_components.eveus import common_command

    manager = common_command.CommandManager(SimpleNamespace())
    manager._post_command = AsyncMock(
        side_effect=aiohttp.ClientResponseError(
            request_info=MagicMock(real_url="http://192.168.1.50/pageEvent"),
            history=(),
            status=400,
            message="Bad Request",
        )
    )

    logger = logging.getLogger(common_command.__name__)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        asyncio.run(manager.send_command("currentSet", 7))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    messages = [record.getMessage() for record in records]
    assert any("ClientResponseError" in message for message in messages)
    assert all("192.168.1.50" not in message for message in messages)


def test_command_retry_skips_permanent_4xx_source_check() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    import aiohttp
    from custom_components.eveus import common_command

    manager = common_command.CommandManager(SimpleNamespace())
    response_error = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=400,
        message="Bad Request",
    )
    manager._post_command = AsyncMock(side_effect=response_error)

    assert asyncio.run(manager.send_command("currentSet", 7)) is False
    manager._post_command.assert_awaited_once()


def test_force_refresh_bypass_counter_untouched():
    import time as _t
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    def _make_offline(updater):
        updater._consecutive_failures = 11
        updater._last_success_monotonic = _t.monotonic() - 700
        updater._last_success_time = _t.time() - 700

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    _make_offline(updater)
    updater._record_failure(TimeoutError())
    assert updater._force_refresh_requests == 0
