"""When the charger entered its current state: the State sensor's `since`.

The card says "Charging since 19:05" and "Charge Complete at 03:15" from it.
Home Assistant's own `last_changed` cannot carry that: it restarts with every
HA restart. The updater stamps the moment it SEES a transition between two
consecutive good polls, keeps the stamp across a restart through the entity's
restore data, and says nothing when the moment is not known — a missing time
is honest, a restart time dressed up as a start time is not.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.sensor_definitions import (
    OptimizedEveusSensor,
    create_sensor_specifications,
    get_charger_state_attributes,
)
from custom_components.eveus.snapshot import EveusSnapshot

T0 = datetime(2026, 10, 21, 19, 5, 30, 400000, tzinfo=timezone.utc)

CONNECTED, CHARGING, COMPLETE, PAUSED = 3, 4, 5, 6


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


@pytest.fixture
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(common_network.dt_util, "utcnow", lambda: now["t"])
    return now


def _updater() -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), device_number=1)
    updater._schedule_post_command_refresh = Mock()
    return updater


def _feed(updater: EveusUpdater, state: int, session_time: int = 600) -> None:
    data = {"verFWMain": "GRM070A-R3.05.4 ", "state": state, "subState": 0,
            "sessionTime": session_time, "sessionEnergy": 1.0}
    data = updater._normalize_legacy_device_state(data)
    updater._snapshot = EveusSnapshot.parse(data, updater._model)
    updater._record_success(0.05, data)
    updater.data = data


def _since(updater: EveusUpdater):
    return updater.state_since


def test_a_transition_between_two_good_polls_is_stamped_to_the_second(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    assert _since(updater) == (T0 + timedelta(minutes=5)).replace(microsecond=0)


def test_polls_inside_the_same_state_keep_the_stamp(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    stamp = _since(updater)
    clock["t"] = T0 + timedelta(hours=3)
    _feed(updater, CHARGING, 11000)
    assert _since(updater) == stamp


def test_stopping_and_starting_again_shows_the_new_start(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=1)
    _feed(updater, CHARGING)
    clock["t"] = T0 + timedelta(minutes=30)
    _feed(updater, CONNECTED, 1800)
    assert _since(updater) == (T0 + timedelta(minutes=30)).replace(microsecond=0)
    clock["t"] = T0 + timedelta(minutes=45)
    _feed(updater, CHARGING, 2700)
    assert _since(updater) == (T0 + timedelta(minutes=45)).replace(microsecond=0)


def test_the_first_reading_of_a_run_has_no_time(clock):
    """HA just started: when the charger entered this state is not known."""
    updater = _updater()
    _feed(updater, CHARGING)
    assert _since(updater) is None


def test_a_state_the_map_cannot_name_has_no_time(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=1)
    _feed(updater, 20)
    assert _since(updater) is None


def test_a_gap_that_ends_in_the_same_state_keeps_the_stamp(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=1)
    _feed(updater, CHARGING)
    stamp = _since(updater)
    updater._record_failure(TimeoutError())
    clock["t"] = T0 + timedelta(minutes=9)
    _feed(updater, CHARGING, 1200)
    assert _since(updater) == stamp


def test_a_gap_that_ends_in_another_state_has_no_time(clock):
    """The change happened while the charger could not be asked: say nothing."""
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=1)
    _feed(updater, CHARGING)
    updater._record_failure(TimeoutError())
    clock["t"] = T0 + timedelta(minutes=9)
    _feed(updater, COMPLETE, 1200)
    assert _since(updater) is None
    clock["t"] = T0 + timedelta(minutes=10)
    _feed(updater, CONNECTED, 1260)
    assert _since(updater) == (T0 + timedelta(minutes=10)).replace(microsecond=0)


# --- across a restart ---------------------------------------------------------


def _iso(minutes: int = 0) -> str:
    return (T0 + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def test_a_saved_stamp_applies_when_the_same_state_is_found_again(clock):
    updater = _updater()
    clock["t"] = T0 + timedelta(hours=2)
    updater.seed_state_since(CHARGING, _iso())
    _feed(updater, CHARGING, 2 * 3600 + 300)
    assert _since(updater) == T0.replace(microsecond=0)


def test_a_saved_stamp_waits_for_the_first_poll(clock):
    updater = _updater()
    updater.seed_state_since(CHARGING, _iso())
    assert _since(updater) is None
    clock["t"] = T0 + timedelta(minutes=20)
    _feed(updater, CHARGING, 20 * 60 + 100)
    assert _since(updater) == T0.replace(microsecond=0)


def test_a_saved_stamp_is_dropped_when_the_state_differs(clock):
    updater = _updater()
    updater.seed_state_since(CHARGING, _iso())
    clock["t"] = T0 + timedelta(minutes=20)
    _feed(updater, COMPLETE, 20 * 60)
    assert _since(updater) is None


def test_a_saved_stamp_from_an_earlier_plug_in_is_dropped(clock):
    """HA was down while the car left and another one (or the same) came back."""
    updater = _updater()
    updater.seed_state_since(CHARGING, _iso())
    clock["t"] = T0 + timedelta(days=1)
    _feed(updater, CHARGING, 3600)  # plugged in an hour ago, stamp is a day old
    assert _since(updater) is None


def test_a_saved_stamp_is_kept_at_the_idle_step_of_the_counter(clock):
    """sessionTime is floored to 5 min when idle: plug-in may read that much late."""
    updater = _updater()
    updater.seed_state_since(CONNECTED, _iso())
    clock["t"] = T0 + timedelta(minutes=10)
    _feed(updater, CONNECTED, 10 * 60 - 300)  # counter says plugged in 5 min after the stamp
    assert _since(updater) == T0.replace(microsecond=0)


def test_a_saved_stamp_needs_a_session_time_to_be_checked_against(clock):
    updater = _updater()
    updater.seed_state_since(CHARGING, _iso())
    data = {"verFWMain": "GRM070A-R3.05.4 ", "state": CHARGING, "subState": 0}
    updater._snapshot = EveusSnapshot.parse(data, updater._model)
    updater._record_success(0.05, data)
    assert _since(updater) is None


@pytest.mark.parametrize(
    "bad",
    [None, "", "yesterday", 12, True, "2026-10-21T19:05:30",  # no zone
     "2999-01-01T00:00:00+00:00"],  # in the future
)
def test_a_corrupt_saved_stamp_is_ignored(clock, bad):
    updater = _updater()
    updater.seed_state_since(CHARGING, bad)
    clock["t"] = T0 + timedelta(minutes=20)
    _feed(updater, CHARGING, 20 * 60)
    assert _since(updater) is None


@pytest.mark.parametrize("bad", [None, "4", True, 99, -1])
def test_a_corrupt_saved_state_code_is_ignored(clock, bad):
    updater = _updater()
    updater.seed_state_since(bad, _iso())
    clock["t"] = T0 + timedelta(minutes=20)
    _feed(updater, CHARGING, 20 * 60)
    assert _since(updater) is None


# --- the sensor ---------------------------------------------------------------


def _state_sensor(updater) -> OptimizedEveusSensor:
    spec = next(s for s in create_sensor_specifications() if s.key == "state")
    return OptimizedEveusSensor(updater, spec)


def test_the_attribute_is_the_stamp_in_iso(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    assert get_charger_state_attributes(updater, None) == {"since": _iso(5)}


def test_no_attribute_when_the_time_is_not_known(clock):
    updater = _updater()
    _feed(updater, CHARGING)
    assert get_charger_state_attributes(updater, None) == {}


def test_the_attribute_sits_beside_the_raw_state_of_a_legacy_firmware(clock):
    from custom_components.eveus.const import LEGACY_RAW_STATE_KEY

    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    updater.snapshot.raw[LEGACY_RAW_STATE_KEY] = 20
    assert get_charger_state_attributes(updater, None) == {"raw_state": 20, "since": _iso(5)}


def test_polling_inside_one_state_changes_no_attribute(clock):
    """A recorder row is written per attribute change: a steady state writes none."""
    updater = _updater()
    sensor = _state_sensor(updater)
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    assert sensor._update_extra_state_attributes() is True
    for minute in (6, 7, 8):
        clock["t"] = T0 + timedelta(minutes=minute)
        _feed(updater, CHARGING, 600 + minute * 60)
        assert sensor._update_extra_state_attributes() is False


def test_the_state_sensor_saves_the_stamp_whatever_its_state(clock):
    updater = _updater()
    sensor = _state_sensor(updater)
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    assert sensor.extra_restore_state_data.as_dict() == {"state": CHARGING, "since": _iso(5)}


def test_the_state_sensor_saves_nothing_when_it_has_nothing(clock):
    updater = _updater()
    sensor = _state_sensor(updater)
    assert sensor.extra_restore_state_data.as_dict() == {"state": None, "since": None}


def test_a_restart_during_an_outage_still_restores_the_stamp(clock, monkeypatch):
    """The saved state is `unavailable` with no attributes: only the extra data has it."""
    from custom_components.eveus import common_base
    from homeassistant.helpers.restore_state import ExtraStoredData

    async def _base_setup(self) -> None:
        return None

    monkeypatch.setattr(common_base.EveusSensorBase, "async_added_to_hass", _base_setup)

    class _Stored(ExtraStoredData):
        def as_dict(self):
            return {"state": CHARGING, "since": _iso()}

    updater = _updater()
    sensor = _state_sensor(updater)

    async def stored():
        return _Stored()

    monkeypatch.setattr(sensor, "async_get_last_extra_data", stored)
    asyncio.run(sensor.async_added_to_hass())
    clock["t"] = T0 + timedelta(minutes=20)
    _feed(updater, CHARGING, 20 * 60)
    assert _since(updater) == T0.replace(microsecond=0)


def test_a_first_install_with_nothing_stored_has_no_time(clock, monkeypatch):
    from custom_components.eveus import common_base

    async def _base_setup(self) -> None:
        return None

    monkeypatch.setattr(common_base.EveusSensorBase, "async_added_to_hass", _base_setup)
    updater = _updater()
    sensor = _state_sensor(updater)

    async def nothing():
        return None

    monkeypatch.setattr(sensor, "async_get_last_extra_data", nothing)
    asyncio.run(sensor.async_added_to_hass())
    _feed(updater, CHARGING)
    assert _since(updater) is None


def test_only_the_state_sensor_keeps_a_stamp():
    others = [s for s in create_sensor_specifications() if s.key != "state"]
    assert not any(getattr(s, "restores_state_since", False) for s in others)
    state = next(s for s in create_sensor_specifications() if s.key == "state")
    assert state.restores_state_since is True


# --- the sensor is added after the first poll --------------------------------
# Platforms load once setup's first refresh is done, so a charger that answers
# is polled before the State sensor restores its stamp.


def test_a_saved_stamp_applies_when_the_charger_was_already_polled(clock):
    updater = _updater()
    clock["t"] = T0 + timedelta(hours=2)
    _feed(updater, CHARGING, 2 * 3600 + 300)
    assert _since(updater) is None
    updater.seed_state_since(CHARGING, _iso())
    assert _since(updater) == T0.replace(microsecond=0)
    assert updater.state_since_anchor == (CHARGING, T0.replace(microsecond=0))


def test_a_saved_stamp_is_read_in_utc_whatever_zone_it_was_saved_in(clock, kyiv_process_zone):
    updater = _updater()
    clock["t"] = T0 + timedelta(hours=2)
    _feed(updater, CHARGING, 2 * 3600 + 300)
    kyiv = T0.astimezone(timezone(timedelta(hours=3))).replace(microsecond=0)
    updater.seed_state_since(CHARGING, kyiv.isoformat())
    assert _since(updater) == T0.replace(microsecond=0)
    assert _since(updater).utcoffset() == timedelta(0)


def test_a_saved_stamp_for_another_state_is_not_applied_to_a_polled_charger(clock):
    updater = _updater()
    clock["t"] = T0 + timedelta(hours=2)
    _feed(updater, COMPLETE, 2 * 3600 + 300)
    updater.seed_state_since(CHARGING, _iso())
    assert _since(updater) is None


def test_a_saved_stamp_does_not_replace_a_transition_already_seen(clock):
    updater = _updater()
    _feed(updater, CONNECTED)
    clock["t"] = T0 + timedelta(minutes=5)
    _feed(updater, CHARGING)
    seen = _since(updater)
    updater.seed_state_since(CHARGING, _iso())
    assert _since(updater) == seen
    # ... and a state that differs from the polled one changes nothing either.
    updater.seed_state_since(COMPLETE, _iso())
    assert _since(updater) == seen


def test_a_saved_stamp_from_the_current_second_is_still_this_plug_in(clock):
    updater = _updater()
    clock["t"] = T0
    _feed(updater, CHARGING, 0)
    updater.seed_state_since(CHARGING, _iso())
    assert _since(updater) == T0.replace(microsecond=0)


@pytest.mark.parametrize("bad", [True, "4", 99, -1, 4.0, None])
def test_a_corrupt_saved_state_is_not_kept_to_be_saved_again(clock, bad):
    updater = _updater()
    updater.seed_state_since(bad, _iso())
    assert updater.state_since_anchor is None


def test_a_saved_boolean_is_not_the_state_code_it_equals(clock):
    """True == 1 == Standby, which is a named state."""
    updater = _updater()
    updater.seed_state_since(True, _iso())
    clock["t"] = T0 + timedelta(minutes=20)
    _feed(updater, 1, 20 * 60)
    assert _since(updater) is None
