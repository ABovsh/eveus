"""Unit tests for Eveus utility helpers."""
from __future__ import annotations

from homeassistant.core import State

from custom_components.eveus import utils


class _Entry:
    def __init__(self, device_number: int | str | None) -> None:
        self.data = {}
        if device_number is not None:
            self.data["device_number"] = device_number


class _ConfigEntries:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries

    def async_entries(self, domain: str) -> list[_Entry]:
        assert domain == "eveus"
        return self._entries


class _Hass:
    def __init__(self, entries: list[_Entry]) -> None:
        self.config_entries = _ConfigEntries(entries)


def test_get_next_device_number_fills_first_available_gap() -> None:
    hass = _Hass([_Entry(1), _Entry("3"), _Entry("bad"), _Entry(None)])

    assert utils.get_next_device_number(hass) == 2


def test_get_safe_value_reads_state_dict_and_raw_values() -> None:
    assert utils.get_safe_value(State("sensor.test", "7.5"), converter=float) == 7.5
    assert utils.get_safe_value({"currentSet": "16"}, "currentSet", int) == 16
    assert utils.get_safe_value("unavailable", default=0) == 0
    assert utils.get_safe_value({"bad": "x"}, "bad", int, default=-1) == -1
    assert utils.get_safe_value("nan", converter=float, default=None) is None
    assert utils.get_safe_value("inf", converter=float, default=0) == 0


def test_get_device_info_is_backward_compatible_for_first_device() -> None:
    info = utils.get_device_info(
        "192.168.1.50",
        {"verFWMain": "3.0.3", "verFWWifi": "1.2.0"},
        device_number=1,
    )

    assert info["identifiers"] == {("eveus", "192.168.1.50")}
    assert info["name"] == "Eveus EV Charger"
    assert info["configuration_url"] == "http://192.168.1.50"


def test_get_device_info_suffixes_additional_devices() -> None:
    info = utils.get_device_info("charger.local", {}, device_number=2)

    assert info["identifiers"] == {("eveus", "charger.local_2")}
    assert info["name"] == "Eveus EV Charger 2"
    assert info["sw_version"] == "Unknown"
    assert info["hw_version"] == "Unknown"


def test_get_device_info_handles_non_string_versions() -> None:
    info = utils.get_device_info(
        "192.168.1.50",
        {"verFWMain": 303, "verFWWifi": 12},
    )

    assert info["sw_version"] == "303"
    assert info["hw_version"] == "12"


def test_format_duration_handles_minutes_hours_and_days() -> None:
    assert utils.format_duration(0) == "0m"
    assert utils.format_duration(59) == "0m"
    assert utils.format_duration(60) == "1m"
    assert utils.format_duration(3660) == "1h 01m"
    assert utils.format_duration(90000) == "1d 01h 00m"


def test_soc_calculations_clamp_to_battery_capacity() -> None:
    assert utils.calculate_soc_kwh(50, 80, 50, 10) == 80
    assert utils.calculate_soc_percent(50, 80, 10, 0) == 62


def test_calculate_remaining_time_states() -> None:
    assert utils.calculate_remaining_time(80, 80, 7000, 80, 7.5) == "Target reached"
    assert utils.calculate_remaining_time(20, 80, 0, 80, 7.5) == "Not charging"
    assert utils.calculate_remaining_time(20, 80, 7000, 80, 0) == "6h 51m"
    assert utils.calculate_remaining_time(120, 80, 7000, 80, 0) == "unavailable"
