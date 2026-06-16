"""Regression tests for the 2026-06-16 deep-assessment findings (V-01..V-22)."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

import custom_components.eveus.number as number_mod
import custom_components.eveus.sensor_definitions as sd
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.soc_limit import SocLimitController
from custom_components.eveus.utils import calculate_remaining_seconds


# =============================================================================
# V-02 — malformed/off-list minVoltage must not widen the undervoltage floor
# =============================================================================

def _threshold(data):
    updater = MagicMock()
    updater.available = True
    updater.data = data
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    ent = number_mod.EveusUndervoltageThresholdNumber(
        updater, number_mod.UNDERVOLTAGE_THRESHOLD_NUMBER, device_number=1
    )
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent


def test_v02_negative_minvoltage_keeps_static_floor():
    ent = _threshold({"aiVoltage": 215, "minVoltage": -1000})
    assert ent.native_min_value == 210


def test_v02_offlist_minvoltage_keeps_static_floor():
    ent = _threshold({"aiVoltage": 215, "minVoltage": 190})
    assert ent.native_min_value == 210


def test_v02_supported_minvoltage_still_tracks():
    ent = _threshold({"aiVoltage": 215, "minVoltage": 180})
    assert ent.native_min_value == 190


# =============================================================================
# V-03 — SOC controller must stand down when suspendLimits is not a clean 0
# =============================================================================

def _calc(target=80, initial=20, cap=50, corr=0):
    c = CachedSOCCalculator()
    c.set_value("initial_soc", initial)
    c.set_value("battery_capacity", cap)
    c.set_value("soc_correction", corr)
    c.set_value("target_soc", target)
    return c


def _soc_updater(**data):
    u = MagicMock()
    u.available = True
    u.last_update_success = True
    u.device_number = 1
    base = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
    base.update(data)
    u.data = base
    u.send_command = AsyncMock(return_value=True)
    return u


def _soc_ctrl(calc, updater):
    hass = MagicMock()
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = MagicMock()
    return SocLimitController(hass, updater, calc)


@pytest.mark.parametrize("suspend", [None, "bad", 2, -1])
def test_v03_does_not_enforce_when_suspendlimits_unknown(suspend):
    updater = _soc_updater()
    if suspend is None:
        del updater.data["suspendLimits"]   # field entirely absent
    else:
        updater.data["suspendLimits"] = suspend
    ctrl = _soc_ctrl(_calc(), updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_not_called()


def test_v03_enforces_only_when_suspendlimits_zero():
    updater = _soc_updater(suspendLimits=0)
    ctrl = _soc_ctrl(_calc(), updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_awaited_once_with("evseEnabled", 1)


# =============================================================================
# V-08 — tiny finite power must not yield an absurd ETA
# =============================================================================

def test_v08_tiny_power_returns_no_eta():
    assert (
        calculate_remaining_seconds(
            current_soc=50, target_soc=80, power_meas=1e-250,
            battery_capacity=50, correction=0,
        )
        is None
    )


def test_v08_normal_power_still_returns_eta():
    secs = calculate_remaining_seconds(
        current_soc=50, target_soc=80, power_meas=7000,
        battery_capacity=50, correction=0,
    )
    assert secs is not None and secs > 0


# =============================================================================
# V-09 — battery voltage sensor must reject implausible CR2032 readings
# =============================================================================

@pytest.mark.parametrize("bad", [0, 5.01, 12.5, 100, 500])
def test_v09_battery_voltage_rejects_implausible(bad):
    upd = SimpleNamespace(available=True, data={"vBat": bad})
    assert sd.get_battery_voltage(upd, None) is None


def test_v09_battery_voltage_accepts_plausible():
    upd = SimpleNamespace(available=True, data={"vBat": 3.0})
    assert sd.get_battery_voltage(upd, None) == 3.0


# =============================================================================
# V-10 — Time Drift must clear stale drift once the clock is back in sync
# =============================================================================

@pytest.fixture
def _tz_kyiv():
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(timezone(timedelta(hours=3)))
    yield
    dt_util.set_default_time_zone(original)


def test_v10_drift_clears_after_sync(_tz_kyiv):
    shift = 3 * 3600
    updater = SimpleNamespace(
        available=True,
        data={"timeZone": "3", "systemTime": str(int(time.time()) + shift + 30)},
    )
    assert sd.get_time_drift(updater, None) == 30
    updater.data["systemTime"] = str(int(time.time()) + shift)
    assert sd.get_time_drift(updater, None) == 0


# =============================================================================
# V-11 — target-SOC logic must use exact SOC, not the rounded display percent
# =============================================================================

def test_v11_soc_limit_does_not_stop_before_exact_target():
    # 20% initial on 50 kWh = 10 kWh; +29.8 kWh = 39.8 kWh = 79.6% which ROUNDS
    # UP to 80 (== target) but is exactly below it -> must NOT stop early.
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = _soc_updater(suspendLimits=0, sessionEnergy=29.8)
    ctrl = _soc_ctrl(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_not_called()


def test_v11_calculator_exposes_exact_percent():
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    # 10 kWh initial + 29.8 kWh = 39.8 kWh on 50 kWh = 79.6% exact
    exact = calc.get_soc_percent_exact(29.8)
    assert 79.5 < exact < 79.7
    # the displayed percent still rounds
    assert calc.get_soc_percent(29.8) == 80


# =============================================================================
# V-12 — Error state with subState 0 must not render "No Error"
# =============================================================================

def test_v12_error_state_zero_substate_is_unknown():
    upd = SimpleNamespace(available=True, data={"state": 7, "subState": 0})
    assert sd.get_charger_substate(upd, None) is None


def test_v12_normal_state_zero_substate_still_maps():
    upd = SimpleNamespace(available=True, data={"state": 2, "subState": 0})
    assert sd.get_charger_substate(upd, None) == "No Limits"


def test_v12_error_state_real_fault_still_maps():
    upd = SimpleNamespace(available=True, data={"state": 7, "subState": 10})
    assert sd.get_charger_substate(upd, None) == "Overcurrent"


# =============================================================================
# V-21 — schedule energy limit must round firmware float noise for display
# =============================================================================

def test_v21_schedule_energy_has_display_precision():
    for desc in number_mod.SCHEDULE_LIMIT_NUMBERS:
        if desc.key.endswith("energy_limit"):
            assert desc.display_precision == 3


# =============================================================================
# V-15 — a hidden small session reset must not let an old pending token confirm
# =============================================================================

def test_v15_pending_token_does_not_cross_hidden_session_reset():
    # Stop issued early (sessionEnergy 0.4, sessionTime 500). A new session whose
    # energy dropped by less than the old 0.5 kWh epsilon (0.4 -> 0.1) but whose
    # sessionTime reset (500 -> 10) must discard the stale token, not confirm it.
    calc = _calc(target=80, initial=80, cap=50, corr=0)  # already at target
    updater = _soc_updater(sessionEnergy=0.4, sessionTime=500, evseEnabled=0)
    ctrl = _soc_ctrl(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()  # issues Stop; charger stays evseEnabled=0 -> pending held
    assert ctrl._pending is not None
    # New session: tiny energy, RESET sessionTime, charger reports stopped.
    updater.data = {
        "state": 4, "sessionEnergy": 0.1, "sessionTime": 10,
        "evseEnabled": 1, "suspendLimits": 0,
    }
    ctrl.process()
    updater.send_command.reset_mock()
    assert ctrl._hass.bus.async_fire.call_count == 0


# =============================================================================
# V-16 — automatic SOC stop must surface auth failure and start reauth
# =============================================================================

def test_v16_soc_stop_auth_failure_starts_reauth_and_withdraws_token():
    from homeassistant.exceptions import ConfigEntryAuthFailed

    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = _soc_updater(sessionEnergy=30.0, evseEnabled=0)
    updater.send_command = AsyncMock(side_effect=ConfigEntryAuthFailed("401"))
    ctrl = _soc_ctrl(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.config_entry.async_start_reauth.assert_called_once()
    assert ctrl._pending is None


# =============================================================================
# V-17 — a malformed suspendLimits sample must not corrupt the switch's memory
# =============================================================================

def test_v17_malformed_suspendlimits_does_not_retrigger_switch_off():
    from custom_components.eveus.switch import EveusSocLimitSwitch

    controller = MagicMock()
    updater = MagicMock()
    updater.config_entry = MagicMock()
    sw = EveusSocLimitSwitch(updater, controller, device_number=1)
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()

    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()              # baseline: master suspended
    asyncio.run(sw.async_turn_on())             # re-enable while suspended
    assert sw.is_on is True

    sw._updater.data = {}                         # malformed poll: no suspendLimits
    sw._handle_coordinator_update()
    sw._updater.data = {"suspendLimits": 1}       # unchanged master, valid again
    sw._handle_coordinator_update()
    assert sw.is_on is True                       # NOT flipped off a second time


# =============================================================================
# V-18 — a deferred command value is evaluated at POST time, not capture time
# =============================================================================

def test_v18_command_manager_resolves_callable_value_at_post_time():
    import aiohttp
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_command import CommandManager

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def raise_for_status(self):
            return None

    class _Sess:
        def __init__(self):
            self.calls = []

        def post(self, url, **kw):
            self.calls.append(kw)
            return _Resp()

    class _Upd:
        host = TEST_HOST

        def __init__(self, sess):
            self._sess = sess
            self._basic_auth = aiohttp.BasicAuth(TEST_USERNAME, TEST_PASSWORD)

        @property
        def basic_auth(self):
            return self._basic_auth

        def get_session(self):
            return self._sess

        def url_for(self, path):
            return f"http://{self.host}{path}"

    sess = _Sess()
    mgr = CommandManager(_Upd(sess))
    calls_seen = []

    def _value():
        calls_seen.append(1)
        return 99999

    ok = asyncio.run(mgr.send_command("systemTime", _value))
    assert ok is True
    assert calls_seen == [1]  # evaluated once, inside the command path
    assert sess.calls[0]["data"] == "pageevent=systemTime&systemTime=99999"


# =============================================================================
# V-19 — equivalent addresses must canonicalize to one identity
# =============================================================================

import custom_components.eveus.config_flow as config_flow  # noqa: E402


def test_v19_default_http_port_is_dropped():
    h1, _ = config_flow._split_host_and_scheme("host")
    h2, _ = config_flow._split_host_and_scheme("host:80")
    h3, _ = config_flow._split_host_and_scheme("http://host")
    assert h1 == h2 == h3 == "host"


def test_v19_default_https_port_is_dropped():
    h, scheme = config_flow._split_host_and_scheme("https://host:443")
    assert h == "host" and scheme == "https"


def test_v19_nondefault_port_is_kept():
    h, _ = config_flow._split_host_and_scheme("host:8080")
    assert h == "host:8080"


def test_v19_ipv6_literals_canonicalize_to_same_host():
    h1, _ = config_flow._split_host_and_scheme("[::1]")
    h2, _ = config_flow._split_host_and_scheme("[0:0:0:0:0:0:0:1]")
    assert h1 == h2 == "[::1]"


# =============================================================================
# V-07 — a legitimate sub-7 A currentSet must still be displayed (read != write)
# =============================================================================

def test_v07_current_set_sensor_displays_sub_7():
    specs = {s.name: s for s in sd.create_sensor_specifications(phases=1, max_current=16)}
    getter = specs["Current Set"].value_fn
    upd = SimpleNamespace(available=True, data={"currentSet": 6})
    assert getter(upd, None) == 6


def test_v07_current_number_displays_sub7_but_writes_floor():
    upd = MagicMock()
    upd.available = True
    upd.data = {"currentSet": 6, "state": 4}
    upd.config_entry = MagicMock()
    upd.send_command = AsyncMock(return_value=True)
    num = number_mod.EveusCurrentNumber(upd, "16A")
    num.hass = MagicMock()
    num.async_write_ha_state = MagicMock()
    assert num.native_value == 6
    asyncio.run(num.async_set_native_value(3))
    upd.send_command.assert_awaited_with("currentSet", 7)


# =============================================================================
# V-14 — a charger-backed number must prefer fresh device data over restore
# =============================================================================

def test_v14_charger_number_prefers_fresh_device_over_restored(monkeypatch):
    upd = MagicMock()
    upd.available = True
    upd.data = {"currentSet": 14, "state": 4}
    upd.config_entry = MagicMock()
    num = number_mod.EveusCurrentNumber(upd, "16A")
    num.hass = MagicMock()
    num.async_write_ha_state = MagicMock()
    # Simulate a prior HA-state restore having seeded the stale value.
    num._last_device_value = 10.0
    num._last_successful_read = time.time()
    num._attr_native_value = 10.0
    monkeypatch.setattr(
        "custom_components.eveus.common_base.BaseEveusEntity.async_added_to_hass",
        AsyncMock(),
    )
    asyncio.run(num.async_added_to_hass())
    assert num.native_value == 14


# =============================================================================
# V-22 — oversized response bodies and metadata strings must be rejected/capped
# =============================================================================

def test_v22_metadata_strings_are_length_capped():
    from custom_components.eveus.utils import _safe_str

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
        asyncio.run(read_json_capped(_Resp(), limit=1_000_000))


def test_v22_capped_reader_parses_small_body():
    from custom_components.eveus._payload import read_json_capped

    class _Content:
        async def iter_chunked(self, n):
            yield b'{"state": 4, "currentSet": 16}'

    class _Resp:
        content_length = 30
        content = _Content()

    data = asyncio.run(read_json_capped(_Resp()))
    assert data == {"state": 4, "currentSet": 16}
