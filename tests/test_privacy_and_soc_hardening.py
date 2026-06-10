"""Privacy and SOC input hardening tests."""
from __future__ import annotations


import pytest
import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.eveus.config_flow import _split_host_and_scheme
from custom_components.eveus.sensor_definitions import get_sensor_specifications
from custom_components.eveus.utils import _validate_soc_inputs


# F01 — URL path rejected
@pytest.mark.parametrize("raw", [
    "https://host/foo",
    "https://host/main",
    "host/anything",
])
def test_split_host_rejects_path(raw):
    with pytest.raises(vol.Invalid, match="must not include a path"):
        _split_host_and_scheme(raw)


# F01 — root path "/" still accepted (treated as no path)
def test_split_host_accepts_root_path():
    host, _ = _split_host_and_scheme("https://example.local/")
    assert host == "example.local"


# F02 — hostname lowercased
def test_split_host_lowercases_dns_hostname():
    host, _ = _split_host_and_scheme("CHARGER.Local")
    assert host == "charger.local"


def test_split_host_preserves_ipv4_case_irrelevant():
    host, _ = _split_host_and_scheme("192.168.1.10")
    assert host == "192.168.1.10"


# F06/F07/F08/F09 — host scrubbed from exception messages and logs
def test_update_failed_messages_do_not_contain_host():
    from custom_components.eveus import common_network

    src = common_network.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    # The UpdateFailed strings must not interpolate self.host.
    assert 'UpdateFailed(f"Skipping poll for {self.host}' not in text
    assert 'UpdateFailed(f"Connection issue with {self.host}' not in text
    assert 'UpdateFailed(f"Invalid response from {self.host}' not in text


def test_offline_log_messages_do_not_contain_host():
    from custom_components.eveus import common_network

    src = common_network.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "Device %s appears offline" not in text
    assert "Connection issue with %s" not in text


def test_init_device_number_log_does_not_include_host():
    import custom_components.eveus as ev_pkg

    src = ev_pkg.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "Assigned device number %d to %s" not in text
    assert "Normalized device number %d for %s" not in text


def test_config_flow_exception_does_not_stringify_aiohttp_error():
    from custom_components.eveus import config_flow

    src = config_flow.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert 'CannotConnect(f"Connection error: {err}")' not in text
    assert 'CannotConnect(f"Unexpected error: {err}")' not in text


# F11 — SOC range validation
def test_soc_inputs_reject_out_of_range_soc():
    assert _validate_soc_inputs(-1, 60, 5, 8) is None
    assert _validate_soc_inputs(101, 60, 5, 8) is None


def test_soc_inputs_reject_nonpositive_capacity():
    assert _validate_soc_inputs(50, 0, 5, 8) is None
    assert _validate_soc_inputs(50, -10, 5, 8) is None


def test_soc_inputs_reject_negative_energy():
    assert _validate_soc_inputs(50, 60, -0.1, 8) is None


def test_soc_inputs_reject_out_of_range_efficiency():
    assert _validate_soc_inputs(50, 60, 5, -1) is None
    assert _validate_soc_inputs(50, 60, 5, 100) is None


def test_soc_inputs_accept_valid():
    assert _validate_soc_inputs(50, 60, 5, 8) == (50.0, 60.0, 5.0, 8.0)


# session_cost is a resettable monetary total (TOTAL handles per-session reset
# gracefully, and MONETARY device class requires it).
def test_session_cost_is_monetary_total():
    by_key = {s.key: s for s in get_sensor_specifications(1)}
    assert by_key["session_cost"].state_class == SensorStateClass.TOTAL
    assert by_key["session_cost"].device_class == SensorDeviceClass.MONETARY


# F22 — Car Connected returns None for error state (7)
def test_car_connected_error_state_is_unknown():
    from custom_components.eveus.binary_sensor import (
        _CONNECTED_STATES,
        _PLUG_UNKNOWN_STATES,
    )
    assert 7 in _PLUG_UNKNOWN_STATES
    assert 7 not in _CONNECTED_STATES


# Absolute cost sensors are MONETARY with the ISO currency unit.
def test_cost_sensors_use_monetary_iso_unit():
    by_key = {s.key: s for s in get_sensor_specifications(1)}
    for key in ("counter_a_cost", "counter_b_cost", "session_cost"):
        assert by_key[key].unit == "UAH"
        assert by_key[key].device_class == SensorDeviceClass.MONETARY
