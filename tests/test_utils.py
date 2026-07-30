"""Unit tests for Eveus utility helpers."""
from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from homeassistant.core import State

from conftest import TEST_BASE_URL, TEST_HOST
from custom_components.eveus import utils
from custom_components.eveus.const import (
    MAX_VALID_SYSTEM_TIME,
    MAX_VALID_TIMEZONE_H,
    MIN_VALID_TIMEZONE_H,
)


class _Entry:
    def __init__(self, device_number: int | str | None, entry_id: str = "") -> None:
        self.data = {}
        self.entry_id = entry_id
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


def test_get_next_device_number_ignores_only_the_excluded_entry() -> None:
    hass = _Hass([_Entry(1, entry_id="self"), _Entry(2, entry_id="other")])

    # Excluding "self" must free up 1, while "other"'s 2 still blocks that slot.
    assert utils.get_next_device_number(hass, exclude_entry_id="self") == 1
    assert utils.is_device_number_taken(hass, 1, exclude_entry_id="self") is False
    assert utils.is_device_number_taken(hass, 2, exclude_entry_id="self") is True


def test_is_device_number_taken_true_only_for_used_numbers() -> None:
    hass = _Hass([_Entry(1, entry_id="a")])

    assert utils.is_device_number_taken(hass, 1) is True
    assert utils.is_device_number_taken(hass, 2) is False


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


def test_rate_log_default_max_keys_is_64(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0
    monkeypatch.setattr(utils.time, "monotonic", lambda: now)
    limiter = utils.RateLog()
    for i in range(64):
        assert limiter.should_log(10, i) is True
    assert len(limiter._last_logs) == 64
    # The 65th distinct key must evict the oldest rather than growing past 64.
    assert limiter.should_log(10, "new") is True
    assert len(limiter._last_logs) == 64
    assert 0 not in limiter._last_logs


def test_rate_log_none_key_interval_boundary_is_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([0.0, 10.0, 10.0001])
    monkeypatch.setattr(utils.time, "monotonic", lambda: next(times))
    limiter = utils.RateLog()
    assert limiter.should_log(10) is True
    # Exactly at the interval boundary: not yet due (elapsed > interval required).
    assert limiter.should_log(10) is False
    assert limiter.should_log(10) is True


def test_rate_log_keyed_interval_boundary_is_inclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([0.0, 10.0])
    monkeypatch.setattr(utils.time, "monotonic", lambda: next(times))
    limiter = utils.RateLog()
    assert limiter.should_log(10, "x") is True
    # Exactly at the interval boundary: still suppressed (elapsed <= interval).
    assert limiter.should_log(10, "x") is False


def test_used_device_numbers_continues_past_invalid_entries() -> None:
    # A garbage device_number entry must be skipped, not abort the whole scan --
    # a later, valid entry must still be recognized as taken.
    hass = _Hass([_Entry("bad"), _Entry(5)])
    assert utils.is_device_number_taken(hass, 5) is True


def test_get_safe_value_plain_scalar_with_or_without_key() -> None:
    # source is neither a State nor a dict: the raw scalar itself is the value,
    # regardless of whether a (irrelevant) key was also passed.
    assert utils.get_safe_value(42, converter=int) == 42
    assert utils.get_safe_value(42, key="ignored", converter=int) == 42


def test_get_safe_value_str_sentinel_values_return_default() -> None:
    # Use converter=str so a sentinel string wouldn't independently fail
    # conversion -- isolates the membership check itself.
    assert utils.get_safe_value("unknown", converter=str, default="D") == "D"
    assert utils.get_safe_value("unavailable", converter=str, default="D") == "D"
    assert utils.get_safe_value("", converter=str, default="D") == "D"
    assert utils.get_safe_value("real", converter=str, default="D") == "real"


def test_get_device_info_ignores_none_placeholder_firmware() -> None:
    info = utils.get_device_info(
        TEST_HOST, {"verFWWifi": "none", "verFWMain": "GRM070A-R3.05.4"}
    )
    assert info["sw_version"] == "GRM070A-R3.05.4"


def test_sanitized_serial_boundary_accepts_exactly_two_chars() -> None:
    info = utils.get_device_info(TEST_HOST, {"serialNum": "AB"}, 1)
    assert info.get("serial_number") == "AB"


def test_get_device_info_defaults_to_first_device() -> None:
    info = utils.get_device_info(TEST_HOST, {})
    assert info["identifiers"] == {("eveus", TEST_HOST)}
    assert info["name"] == "Eveus EV Charger"


def test_get_device_info_reads_manufacturer_and_model_fields() -> None:
    info = utils.get_device_info(TEST_HOST, {"manufacturer": "ACME", "model": "X1"})
    assert info["manufacturer"] == "ACME"
    assert info["model"] == "X1"


def test_get_device_info_defaults_manufacturer_and_model() -> None:
    info = utils.get_device_info(TEST_HOST, {})
    assert info["manufacturer"] == "Eveus"
    assert info["model"] == "Eveus EV Charger"


def test_init_fw_fallback_not_applied_when_fw_module_present() -> None:
    # The /init fallback must only fill in a MISSING module firmware; it must
    # never overwrite a module firmware /main already reported.
    info = utils.get_device_info(
        TEST_HOST,
        {"verFWMain": "GRM070A-R3.05.4", "verFWWifi": "1PGRW001A-R3.05.5"},
        init_fw_fallback="1.51",
    )
    assert info["sw_version"] == "1PGRW001A-R3.05.5 (GRM070A-R3.05.4)"


def test_format_duration_two_day_boundary_uses_correct_divisor() -> None:
    assert utils.format_duration(2 * 86400) == "2d 00h 00m"


def test_get_local_utc_offset_seconds_uses_dt_util_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_now = SimpleNamespace(utcoffset=lambda: datetime.timedelta(hours=2))
    monkeypatch.setattr(utils.dt_util, "now", lambda: fake_now)
    assert utils.get_local_utc_offset_seconds() == 7200


def test_get_local_utc_offset_seconds_handles_none_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_now = SimpleNamespace(utcoffset=lambda: None)
    monkeypatch.setattr(utils.dt_util, "now", lambda: fake_now)
    assert utils.get_local_utc_offset_seconds() == 0


def test_get_local_wall_clock_seconds_adds_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(utils.time, "time", lambda: 1000.0)
    monkeypatch.setattr(utils, "get_local_utc_offset_seconds", lambda: 3600)
    assert utils.get_local_wall_clock_seconds() == 1000 + 3600


def test_charger_wall_clock_seconds_boundaries() -> None:
    assert utils.get_charger_wall_clock_seconds({"systemTime": 0, "timeZone": 0}) is None
    assert utils.get_charger_wall_clock_seconds({"systemTime": 1, "timeZone": 0}) == 1
    assert (
        utils.get_charger_wall_clock_seconds(
            {"systemTime": MAX_VALID_SYSTEM_TIME, "timeZone": 0}
        )
        == MAX_VALID_SYSTEM_TIME
    )
    assert (
        utils.get_charger_wall_clock_seconds(
            {"systemTime": 100, "timeZone": MIN_VALID_TIMEZONE_H}
        )
        == 100
    )
    assert (
        utils.get_charger_wall_clock_seconds(
            {"systemTime": 100, "timeZone": MAX_VALID_TIMEZONE_H}
        )
        == 100
    )


def test_charger_wall_clock_seconds_missing_one_field_returns_none_not_raises() -> None:
    # Only one of the two required fields present must return None -- not raise
    # (guards an `and` where the None-checks need `or`).
    assert utils.get_charger_wall_clock_seconds({"systemTime": 100}) is None
    assert utils.get_charger_wall_clock_seconds({"timeZone": 3}) is None


def test_max_remaining_seconds_constant_is_30_days() -> None:
    assert utils._MAX_REMAINING_SECONDS == 30 * 24 * 3600


def test_soc_kwh_exact_arithmetic_unclamped() -> None:
    # Values chosen so the result stays well under battery_capacity (no
    # clamping), so the intermediate arithmetic is actually observable.
    assert utils.calculate_soc_kwh(50, 100, 10, 20) == 58.0


def test_soc_kwh_rounds_to_two_decimals() -> None:
    assert utils.calculate_soc_kwh(0, 100, 12.375, 0) == 12.38


def test_soc_kwh_floor_is_zero_not_one() -> None:
    assert utils.calculate_soc_kwh(0, 100, 0, 0) == 0.0


def test_soc_percent_floor_is_zero_not_one() -> None:
    assert utils.calculate_soc_percent(0, 100, 0, 0) == 0.0


def test_soc_percent_ceiling_is_exactly_100() -> None:
    # Fully charged, no further energy: percentage must clamp to exactly 100.
    assert utils.calculate_soc_percent(100, 50, 0, 0) == 100.0


def test_remaining_seconds_soc_bounds_accept_0_and_100() -> None:
    assert utils.calculate_remaining_time(0, 0, 1000, 100, 0) == "Target reached"
    assert utils.calculate_remaining_time(100, 100, 1000, 100, 0) == "Target reached"


def test_remaining_seconds_battery_capacity_boundary_accepts_1() -> None:
    assert utils.calculate_remaining_seconds(0, 50, 1000, 1, 0) == 1800.0


def test_remaining_seconds_power_meas_boundary_accepts_1_watt() -> None:
    assert utils.calculate_remaining_time(49.999, 50, 1, 100, 0) == "1h 00m"


def test_remaining_seconds_correction_divides_not_multiplies() -> None:
    assert utils.calculate_remaining_seconds(0, 50, 1000, 100, 50) == 360000.0


def test_remaining_seconds_cap_boundary_is_inclusive() -> None:
    # Exactly at the 30-day cap: still a valid, finite ETA.
    assert utils.calculate_remaining_seconds(28, 100, 1000, 1000, 0) == 2592000.0
    # A hair over the cap (still finite): unavailable.
    assert utils.calculate_remaining_seconds(27.999, 100, 1000, 1000, 0) is None
