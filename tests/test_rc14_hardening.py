"""Hardening tests for the 4.10.2 deep-audit round (rc14).

Each test pins a defect the parallel audit found and verified against the live
code (guards traced one level up before reporting). Grouped by source file.
"""
from __future__ import annotations

import asyncio
import math
import time
from types import SimpleNamespace

from conftest import EveusTestUpdater, TEST_HOST, disable_state_writes

from homeassistant.const import CONF_HOST

from custom_components.eveus import async_migrate_entry
from custom_components.eveus.const import CONF_SCHEME
from custom_components.eveus.utils import (
    _safe_str,
    calculate_remaining_seconds,
    calculate_remaining_time,
    get_device_info,
)


# ---------------------------------------------------------------------------
# B04 — ETA must report "unavailable" (not inf / not a stale value) when a
# finite-but-tiny powerMeas makes the seconds division overflow.
# ---------------------------------------------------------------------------

def test_eta_seconds_is_none_when_division_overflows() -> None:
    # remaining = (80-20)*50/100 = 30 kWh; power_kw ~1e-313 (subnormal, >0) makes
    # 30 / power_kw overflow to +inf. The ETA must be None, never inf.
    result = calculate_remaining_seconds(20, 80, 1e-310, 50, 0)
    assert result is None


def test_eta_string_is_unavailable_when_division_overflows() -> None:
    # int(inf) would raise OverflowError inside format_duration; the string ETA
    # must degrade to "unavailable" instead of raising.
    result = calculate_remaining_time(20, 80, 1e-310, 50, 0)
    assert result == "unavailable"


def test_eta_seconds_still_finite_for_normal_power() -> None:
    result = calculate_remaining_seconds(20, 80, 3000, 50, 0)
    assert result is not None and math.isfinite(result) and result > 0


# ---------------------------------------------------------------------------
# B05 — _safe_str must reject non-string firmware fields so a bool/list/dict
# can't be rendered ("True") and permanently finalized into device_info.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# B01 — currentSet is an integer amp setpoint; a fractional value is corrupt and
# must be rejected (like a non-integer `state`), not silently rounded by the
# display getter.
# ---------------------------------------------------------------------------

def test_payload_rejects_fractional_current_set() -> None:
    import pytest

    from custom_components.eveus._payload import PayloadError, validate_main_payload

    with pytest.raises(PayloadError):
        validate_main_payload({"state": 4, "currentSet": 7.5})


def test_payload_rejects_fractional_current_set_as_string() -> None:
    import pytest

    from custom_components.eveus._payload import PayloadError, validate_main_payload

    with pytest.raises(PayloadError):
        validate_main_payload({"state": 4, "currentSet": "7.5"})


def test_payload_accepts_integral_current_set() -> None:
    from custom_components.eveus._payload import validate_main_payload

    assert validate_main_payload({"state": 4, "currentSet": 16.0})["currentSet"] == 16.0
    assert validate_main_payload({"state": 4, "currentSet": 16})["currentSet"] == 16


# ---------------------------------------------------------------------------
# B02 / B03 — adaptive-current and schedule-current readings must be bounded by
# THIS charger's model max, not the global ceiling (like Current Set already is).
# ---------------------------------------------------------------------------

def _spec_by_key(specs, key):
    for spec in specs:
        if spec.key == key:
            return spec
    raise KeyError(key)


def test_adaptive_current_limit_bounded_to_model_max() -> None:
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    spec = _spec_by_key(create_sensor_specifications(max_current=16), "adaptive_current_limit")
    # 48 A is impossible on a 16 A charger → unknown.
    assert spec.value_fn(EveusTestUpdater(data={"aiModecurrent": 48}), None) is None
    # A valid throttled value still shows.
    assert spec.value_fn(EveusTestUpdater(data={"aiModecurrent": 10}), None) == 10


def test_schedule_current_limit_bounded_to_model_max() -> None:
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    spec = _spec_by_key(create_sensor_specifications(max_current=16), "schedule_1")
    bad = spec.attributes_fn(
        EveusTestUpdater(data={"sh1CurrentEnable": 1, "sh1CurrentValue": 48}), None
    )
    assert "current_limit_a" not in bad
    ok = spec.attributes_fn(
        EveusTestUpdater(data={"sh1CurrentEnable": 1, "sh1CurrentValue": 12}), None
    )
    assert ok["current_limit_a"] == 12


# ---------------------------------------------------------------------------
# B06 — sessionTime needs an upper sanity cap (like systemTime) so an absurd
# value can't render an overlong state string.
# ---------------------------------------------------------------------------

def test_session_time_rejects_absurd_duration() -> None:
    from custom_components.eveus.sensor_definitions import get_session_time

    assert get_session_time(EveusTestUpdater(data={"sessionTime": 10**12}), None) is None
    assert get_session_time(EveusTestUpdater(data={"sessionTime": 3600}), None) == "1h 00m"


# ---------------------------------------------------------------------------
# A01 — migration of a legacy host carrying URL credentials must scrub them from
# both the stored host and the config-entry title, not leave them intact.
# ---------------------------------------------------------------------------

class _ConfigEntries:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def async_entries(self, _domain=None):
        return []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _EmptyRegistry:
    def async_get(self, entity_id: str) -> object | None:
        return None


def test_migration_scrubs_url_credentials(monkeypatch) -> None:
    from custom_components import eveus

    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistry())
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )

    config_entries = _ConfigEntries()
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


# ---------------------------------------------------------------------------
# A02 — a command-backed control must still push an availability transition
# (charger went offline mid-command) instead of swallowing the write while a
# command is pending, leaving a stale "available" control on the dashboard.
# ---------------------------------------------------------------------------

def test_control_pushes_availability_change_while_command_pending() -> None:
    from custom_components.eveus.const import CONTROL_GRACE_PERIOD, MODEL_16A
    from custom_components.eveus.number import EveusCurrentNumber

    updater = EveusTestUpdater(data={"currentSet": 16})
    number = EveusCurrentNumber(updater, MODEL_16A)
    writes: list[int] = []
    number.async_write_ha_state = lambda: writes.append(1)
    number._last_written_available = True  # previously shown as available

    number._pending_value = 10.0  # a command is in flight
    updater.available = False  # charger drops offline, past the grace period
    number._unavailable_since = time.time() - (CONTROL_GRACE_PERIOD + 5)

    number._handle_coordinator_update()

    assert number.available is False
    assert writes, "availability transition must be pushed even while a command is pending"


def test_control_keeps_value_steady_while_pending_when_available() -> None:
    # While a command is pending and availability has NOT changed, the control
    # must not reconcile/flip its displayed value off the pending value.
    from custom_components.eveus.const import MODEL_16A
    from custom_components.eveus.number import EveusCurrentNumber

    updater = EveusTestUpdater(data={"currentSet": 16})
    number = EveusCurrentNumber(updater, MODEL_16A)
    disable_state_writes(number)
    number._set_optimistic_value(10.0)
    number._attr_native_value = 10.0
    number._last_written_available = True
    number._pending_value = 10.0

    number._handle_coordinator_update()

    assert number.native_value == 10.0  # not reverted to device's 16


# ---------------------------------------------------------------------------
# A04 — a force-refresh's offline-backoff bypass must not be stolen by an
# interleaved scheduled poll; it is owned by the async_force_refresh window
# (a counter), so every poll during that window bypasses.
# ---------------------------------------------------------------------------

class _PollResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def __aenter__(self) -> "_PollResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, **kwargs: object) -> dict:
        return self.payload


class _PollSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def post(self, url: str, **kwargs: object) -> "_PollResponse":
        self.calls.append(url)
        return _PollResponse(self.payload)


class _PollHass:
    loop = None


def test_force_refresh_bypass_survives_interleaved_poll(monkeypatch) -> None:
    from conftest import TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater

    session = _PollSession({"state": 2, "currentSet": 16})
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _PollHass())
    updater._next_poll_attempt = time.time() + 9999  # deep in offline backoff

    updater._force_refresh_requests = 1  # a force refresh window is open
    asyncio.run(updater._async_update_data())  # an interleaved scheduled poll
    asyncio.run(updater._async_update_data())  # the force refresh's own poll
    updater._force_refresh_requests = 0

    # Both polls bypassed backoff — the interleaved poll did not consume it.
    assert len(session.calls) == 2


# ---------------------------------------------------------------------------
# A05 — optimistic TTL is a wall-clock delta; a backward system-clock step makes
# the age negative, which must read as expired (not "valid forever").
# ---------------------------------------------------------------------------

def test_optimistic_value_expires_when_clock_steps_backward() -> None:
    from custom_components.eveus.common_base import OptimisticControlMixin

    ctrl = OptimisticControlMixin()
    ctrl._init_optimistic_control()
    ctrl._set_optimistic_value(7)
    stamp = ctrl._optimistic_value_time

    assert ctrl._optimistic_value_is_valid(stamp - 50, 120) is False
    ctrl._expire_optimistic_value(stamp - 50, 120)
    assert ctrl._optimistic_value is None


def test_optimistic_value_valid_within_ttl() -> None:
    from custom_components.eveus.common_base import OptimisticControlMixin

    ctrl = OptimisticControlMixin()
    ctrl._init_optimistic_control()
    ctrl._set_optimistic_value(7)
    stamp = ctrl._optimistic_value_time
    assert ctrl._optimistic_value_is_valid(stamp + 5, 120) is True


# ---------------------------------------------------------------------------
# C01 — Time Zone select must gate device reads on availability (like the other
# controls); otherwise it reconciles against retained data while offline.
# ---------------------------------------------------------------------------

def test_timezone_select_ignores_device_value_when_offline() -> None:
    from custom_components.eveus.select import EveusTimeZoneSelect

    select = EveusTimeZoneSelect(EveusTestUpdater(data={"timeZone": 3}, available=False))
    assert select._device_option() is None


def test_timezone_select_uses_device_value_when_online() -> None:
    from custom_components.eveus.select import EveusTimeZoneSelect

    select = EveusTimeZoneSelect(EveusTestUpdater(data={"timeZone": 3}, available=True))
    assert select._device_option() == "+3"


# ---------------------------------------------------------------------------
# C02 — Time Zone select inherits RestoreEntity but never restored its last
# option, so a restart while the charger is offline dropped it to unknown.
# ---------------------------------------------------------------------------

def test_timezone_select_restores_last_option_within_grace() -> None:
    from custom_components.eveus.select import EveusTimeZoneSelect

    select = EveusTimeZoneSelect(EveusTestUpdater(data={}, available=False))
    disable_state_writes(select)
    asyncio.run(select._async_restore_state(SimpleNamespace(state="+3")))
    assert select.current_option == "+3"
