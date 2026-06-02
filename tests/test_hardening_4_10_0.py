"""Hardening regression tests for the 4.10.0 release cycle.

One test (or parametrized group) per defect landed in the 4.10-rc audit round:
robust numeric coercion (OverflowError / bool), migration resilience, invalid
SOC-mode fallback, IPv6 host handling, nested diagnostics redaction, prefill
clamping, and the SOC-ETA "unavailable" message.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import voluptuous as vol

from conftest import (
    HelperHass,
    EveusTestUpdater as _Updater,
    TEST_HOST,
    TEST_USERNAME,
    TEST_PASSWORD,
)
from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import (
    CONF_SOC_MODE,
    SOC_MODE_ADVANCED,
    SOC_MODE_BASIC,
    get_soc_mode,
)
from custom_components.eveus.utils import normalize_soc_input
from custom_components.eveus import config_flow
from custom_components.eveus.config_flow import (
    _split_host_and_scheme,
    normalize_user_input,
    _prefill_from_helper,
)
from custom_components.eveus.diagnostics import _sensitive_keys


# --- F08 / F09 — normalize_soc_input rejects bool and huge ints ---------------

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


# --- F05 — invalid stored soc_mode resolves to advanced -----------------------

@pytest.mark.parametrize(
    "stored,expected",
    [
        (SOC_MODE_ADVANCED, SOC_MODE_ADVANCED),
        (SOC_MODE_BASIC, SOC_MODE_BASIC),
        ("garbage", SOC_MODE_ADVANCED),
        (None, SOC_MODE_ADVANCED),
        (1, SOC_MODE_ADVANCED),
    ],
)
def test_get_soc_mode_defaults_invalid_to_advanced(stored, expected) -> None:
    data = {} if stored is None else {CONF_SOC_MODE: stored}
    assert get_soc_mode(SimpleNamespace(data=data)) == expected


# --- F06 — boolean phase input is rejected ------------------------------------

def test_normalize_user_input_rejects_bool_phases() -> None:
    base = {
        "host": TEST_HOST,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "model": "16A",
        "phases": True,
        "soc_mode": SOC_MODE_ADVANCED,
    }
    with pytest.raises(vol.Invalid):
        normalize_user_input(base)


# --- F19 — bare IPv6 literal is accepted and bracketed ------------------------

@pytest.mark.parametrize(
    "raw,expected_host",
    [
        ("fe80::1", "[fe80::1]"),
        ("2001:db8::2", "[2001:db8::2]"),
        ("[2001:db8::2]:8443", "[2001:db8::2]:8443"),
    ],
)
def test_split_host_accepts_bare_ipv6(raw, expected_host) -> None:
    host, scheme = _split_host_and_scheme(raw)
    assert host == expected_host
    assert scheme in ("http", "https")


# --- F10 — prefill clamps / rejects bad helper states -------------------------

@pytest.mark.parametrize(
    "state,expected",
    [
        ("nan", 50.0),       # non-finite -> fallback
        ("inf", 50.0),       # non-finite -> fallback
        ("9999", 160.0),     # out of range -> clamped to max
        ("75", 75.0),        # valid -> used
    ],
)
def test_prefill_from_helper_sanitizes(state, expected) -> None:
    hass = HelperHass({"input_number.ev_battery_capacity": state})
    out = _prefill_from_helper(
        hass, "input_number.ev_battery_capacity", "battery_capacity", 50.0
    )
    assert out == expected


# --- F18 — diagnostics redaction heuristic reaches nested keys ----------------

def test_sensitive_keys_walks_nested_structures() -> None:
    data = {
        "powerMeas": 7200,
        "nested": {"deep": {"wifi_ssid": "x"}, "list": [{"device_mac": "y"}]},
    }
    keys = _sensitive_keys(data)
    assert "wifi_ssid" in keys
    assert "device_mac" in keys
    assert "powerMeas" not in keys


# --- F04 — migration does not crash on a host-less, unique_id-less entry -------

def test_migration_survives_missing_host_and_unique_id() -> None:
    updated: dict = {}

    class _Entries:
        def async_update_entry(self, entry, **kwargs):
            updated.update(kwargs)

    hass = SimpleNamespace(config_entries=_Entries())
    # soc_mode already present so the legacy-helper branch (entity registry) is
    # skipped; host and unique_id are absent — the KeyError path under test.
    entry = SimpleNamespace(
        data={CONF_SOC_MODE: SOC_MODE_ADVANCED},
        unique_id=None,
        title="Eveus",
        version=1,
    )

    from custom_components.eveus import async_migrate_entry

    # Must complete without raising KeyError, and must not set unique_id to a
    # non-existent host.
    assert asyncio.run(async_migrate_entry(hass, entry)) is True
    assert "unique_id" not in updated


# --- A01 — huge-int currentSet is rejected and recorded as a failure ----------

class _Resp:
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


class _Session:
    def __init__(self, payload):
        self._payload = payload

    def post(self, url, **kw):
        return _Resp(self._payload)


class _Hass:
    loop = None


def test_coordinator_rejects_overflowing_current_set(monkeypatch) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    payload = {"state": 2, "currentSet": 10 ** 400}
    monkeypatch.setattr(
        common_network, "async_get_clientsession", lambda hass: _Session(payload)
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())
    # Failure was recorded (custom availability flipped) rather than the
    # OverflowError escaping uncaught with stale availability.
    assert updater.available is False


# --- F11 — SOC ETA says "unavailable" (not "Helpers Required") when helpers
#           are set but charger telemetry is missing ---------------------------

def test_time_to_target_shows_unavailable_when_telemetry_missing() -> None:
    from custom_components.eveus.ev_sensors import (
        TimeToTargetSocSensor,
        CachedSOCCalculator,
    )

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 7.5)
    calc.set_value("target_soc", 80)  # helpers + target all present

    # Updater online but payload carries no power/SOC telemetry.
    updater = _Updater({"state": 4})
    sensor = TimeToTargetSocSensor(updater, 1, calc)

    assert sensor._get_sensor_value() == "unavailable"
