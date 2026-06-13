"""Unit tests for Eveus utility helpers."""
from __future__ import annotations

import pytest
from homeassistant.core import State

from conftest import TEST_BASE_URL, TEST_HOST
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
    assert utils.get_safe_value(State("sensor.test", "7.5"), converter=float) == pytest.approx(
        7.5
    )
    assert utils.get_safe_value({"currentSet": "16"}, "currentSet", int) == 16
    assert utils.get_safe_value("unavailable", default=0) == 0
    assert utils.get_safe_value({"bad": "x"}, "bad", int, default=-1) == -1
    assert utils.get_safe_value("nan", converter=float, default=None) is None
    assert utils.get_safe_value("inf", converter=float, default=0) == 0


def test_get_device_info_is_backward_compatible_for_first_device() -> None:
    info = utils.get_device_info(
        TEST_HOST,
        {"verFWMain": "3.0.3", "verFWWifi": "1.2.0"},
        device_number=1,
    )

    assert info["identifiers"] == {("eveus", TEST_HOST)}
    assert info["name"] == "Eveus EV Charger"
    assert info["configuration_url"] == TEST_BASE_URL


def test_get_device_info_shows_both_firmwares_app_leading() -> None:
    # The charger's own update UI labels verFWWifi (e.g. 1PGRW001A-R3.05.5) as the
    # version "installed to your EVEUS Pro" — it must lead. verFWMain (GRM070A-...)
    # is the module firmware and is shown in parentheses. Trailing whitespace the
    # firmware emits on verFWMain must be trimmed.
    info = utils.get_device_info(
        TEST_HOST,
        {"verFWWifi": "1PGRW001A-R3.05.5", "verFWMain": "GRM070A-R3.05.4 "},
    )
    assert info["sw_version"] == "1PGRW001A-R3.05.5 (GRM070A-R3.05.4)"


def test_get_device_info_ignores_placeholder_firmware() -> None:
    # A literal placeholder ("Unknown"/"unavailable"/...) in one field must not
    # leak into the combined string and slip past the "== Unknown" finalize guard.
    one_bad = utils.get_device_info(
        TEST_HOST, {"verFWWifi": "Unknown", "verFWMain": "GRM070A-R3.05.4"}
    )
    assert one_bad["sw_version"] == "GRM070A-R3.05.4"
    # Both placeholders collapse to "Unknown" (which the finalize guard rejects).
    both_bad = utils.get_device_info(
        TEST_HOST, {"verFWWifi": "unavailable", "verFWMain": "n/a"}
    )
    assert both_bad["sw_version"] == "Unknown"


def test_get_device_info_firmware_falls_back_to_single_field() -> None:
    # Only the app firmware present -> shown alone (no empty parentheses).
    app_only = utils.get_device_info(TEST_HOST, {"verFWWifi": "1PGRW001A-R3.05.5"})
    assert app_only["sw_version"] == "1PGRW001A-R3.05.5"
    # Only the module firmware present -> shown alone (older firmware).
    module_only = utils.get_device_info(TEST_HOST, {"verFWMain": "GRM070A-R3.05.4"})
    assert module_only["sw_version"] == "GRM070A-R3.05.4"


def test_get_device_info_suffixes_additional_devices() -> None:
    info = utils.get_device_info("charger.local", {}, device_number=2)

    assert info["identifiers"] == {("eveus", "charger.local_2")}
    assert info["name"] == "Eveus EV Charger 2"
    assert info["sw_version"] == "Unknown"
    assert "hw_version" not in info


def test_get_device_info_handles_non_string_versions() -> None:
    info = utils.get_device_info(
        TEST_HOST,
        {"verFWMain": 303, "verFWWifi": 12},
    )

    assert info["sw_version"] == "12 (303)"
    assert "hw_version" not in info


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


def test_format_duration_handles_none_and_nan() -> None:
    # Regression: must not raise TypeError comparing None/NaN to 0
    assert utils.format_duration(None) == "0m"
    assert utils.format_duration(float("nan")) == "0m"
    assert utils.format_duration(-10) == "0m"
    assert utils.format_duration("bad") == "0m"


def test_calculate_soc_percent_sanitizes_invalid_input() -> None:
    # Regression: must never return the raw unvalidated argument
    result = utils.calculate_soc_percent("bad", 80, 10, 0)
    assert isinstance(result, (int, float)), f"got {result!r}"
    assert result == pytest.approx(0.0)

    # battery_capacity <= 0 must return 0.0, not the raw initial_soc
    assert utils.calculate_soc_percent(50, 0, 10, 0) == pytest.approx(0.0)
    assert utils.calculate_soc_percent(50, -5, 10, 0) == pytest.approx(0.0)

    # NaN inputs must return 0.0
    result_nan = utils.calculate_soc_percent(50, float("nan"), 10, 0)
    assert isinstance(result_nan, (int, float))


def test_get_device_info_uses_scheme_in_configuration_url() -> None:
    # Regression: https-configured charger was linked with http:// in HA UI
    info_https = utils.get_device_info(TEST_HOST, {}, scheme="https")
    assert info_https["configuration_url"] == f"https://{TEST_HOST}"

    info_http = utils.get_device_info(TEST_HOST, {}, scheme="http")
    assert info_http["configuration_url"] == TEST_BASE_URL

    # Default must stay http for backward compat
    info_default = utils.get_device_info(TEST_HOST, {})
    assert info_default["configuration_url"] == TEST_BASE_URL


def test_rate_log_supports_global_keys_and_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1000.0
    monkeypatch.setattr(utils.time, "time", lambda: now)
    limiter = utils.RateLog(max_keys=2)

    assert limiter.should_log(10) is True
    assert limiter.should_log(10) is False

    assert limiter.should_log(10, "a") is True
    assert limiter.should_log(10, "a") is False
    now = 1011.0
    assert limiter.should_log(10, "b") is True
    assert limiter.should_log(10, "c") is True
    assert "a" not in limiter._last_logs
    assert set(limiter._last_logs) == {"b", "c"}


def test_get_safe_value_rejects_bool_and_attribute_errors() -> None:
    class BrokenState:
        @property
        def state(self):
            raise AttributeError("missing")

    assert utils.get_safe_value(True, converter=int, default=5) == 5
    assert utils.get_safe_value({"value": False}, "value", float, default=7) == 7
    assert utils.get_safe_value(BrokenState(), converter=float, default=9) == 9


def test_device_suffix_and_identifier_helpers() -> None:
    assert utils.get_device_suffix(1) == ""
    assert utils.get_device_suffix(3) == "3"
    assert utils.get_device_display_suffix(1) == ""
    assert utils.get_device_display_suffix(3) == " 3"
    assert utils.get_device_identifier("host", 1) == ("eveus", "host")
    assert utils.get_device_identifier("host", 3) == ("eveus", "host_3")


def test_get_device_info_normalizes_too_short_versions() -> None:
    info = utils.get_device_info(TEST_HOST, {"verFWMain": "x", "verFWWifi": ""})

    assert info["sw_version"] == "Unknown"
    assert "hw_version" not in info


@pytest.mark.parametrize(
    ("args", "expected_kwh", "expected_percent"),
    [
        ((-1, 80, 1, 0), 0.0, 0.0),
        ((50, 0, 1, 0), 0.0, 0.0),
        ((50, 80, -1, 0), 0.0, 0.0),
        ((50, 80, 1, 100), 0.0, 0.0),
        ((50, 80, 1, float("nan")), 0.0, 0.0),
    ],
)
def test_soc_calculations_reject_invalid_input_matrix(
    args: tuple[float, float, float, float],
    expected_kwh: float,
    expected_percent: float,
) -> None:
    assert utils.calculate_soc_kwh(*args) == pytest.approx(expected_kwh)
    assert utils.calculate_soc_percent(*args) == pytest.approx(expected_percent)


@pytest.mark.parametrize(
    ("args", "remaining", "text"),
    [
        ((80, 90, None, 80, 0), None, "unavailable"),
        ((80, 90, 7000, -1, 0), None, "unavailable"),
        ((80, 90, 7000, 80, 100), None, "Not charging"),
        ((90, 80, 7000, 80, 0), 0.0, "Target reached"),
        ((80, 81, 100000, 80, 0), pytest.approx(28.8), "< 1m"),
    ],
)
def test_remaining_time_contract_states(
    args: tuple[object, object, object, object, object],
    remaining: object,
    text: str,
) -> None:
    assert utils.calculate_remaining_seconds(*args) == remaining
    assert utils.calculate_remaining_time(*args) == text


def test_remaining_time_invalid_float_conversion_is_unavailable() -> None:
    assert utils.calculate_remaining_seconds("bad", 80, 7000, 80, 0) is None
    assert utils.calculate_remaining_time(20, 80, 7000, 80, "bad") == "unavailable"
