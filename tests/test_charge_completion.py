"""Why state 5 was reached, told apart from a finished charge.

The charger reports state 5 ("Charge Complete") both when the car is full and
when a schedule window closes or a limit fires mid-charge — subState carries
the difference. But subState is a LIVE signal, not the stop cause: it reads
"Schedule 1 Limit" all day outside the window, with or without a car. So a car
that finished at 03:00 sees subState flip to 5 at 07:00 while state stays 5.

The sequences below replay what the live charger recorded (2026-09-19 ..
2026-09-26) through the real coordinator.
"""
from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import EVENT_CHARGING_FINISHED
from custom_components.eveus.sensor_definitions import (
    OptimizedEveusSensor,
    create_sensor_specifications,
    get_not_charging_reason,
)
from custom_components.eveus.snapshot import EveusSnapshot

_MODERN = {"verFWMain": "GRM070A-R3.05.4 "}


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


def _updater() -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), device_number=1)
    updater._schedule_post_command_refresh = Mock()
    return updater


def _feed(updater: EveusUpdater, payload: dict, *, modern: bool = True) -> None:
    """One good poll, in the order `_async_update_data` runs it."""
    data = {**_MODERN, **payload} if modern else dict(payload)
    data = updater._normalize_legacy_device_state(data)
    updater._snapshot = EveusSnapshot.parse(data, updater._model)
    updater._record_success(0.05, data)
    updater.data = data


def _fail(updater: EveusUpdater) -> None:
    updater._record_failure(TimeoutError())


def _reason(updater: EveusUpdater) -> str | None:
    return get_not_charging_reason(updater, None)


def _finished(updater: EveusUpdater) -> list[str]:
    return [
        call.args[1]["reason"]
        for call in updater.hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_CHARGING_FINISHED
    ]


def _charging(session_time: int = 1000) -> dict:
    return {"state": 4, "subState": 0, "sessionTime": session_time, "sessionEnergy": 5.0}


# --- the reported case --------------------------------------------------------


def test_a_schedule_window_closing_mid_charge_is_not_charge_complete():
    """2026-09-26 07:01: Charging -> state 5 + Schedule 1 Limit, car not full."""
    updater = _updater()
    _feed(updater, _charging(39000))
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 39060})
    assert _reason(updater) == "Waiting for Schedule"
    assert _finished(updater) == ["schedule"]


def test_schedule_2_closing_reads_the_same():
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 5, "subState": 7, "sessionTime": 1060})
    assert _reason(updater) == "Waiting for Schedule"
    assert _finished(updater) == ["schedule"]


@pytest.mark.parametrize(
    ("substate", "reason"),
    [
        (2, "Energy Limit Reached"),
        (3, "Time Limit Reached"),
        (4, "Cost Limit Reached"),
        (6, "Schedule Energy Limit Reached"),
        (8, "Schedule Energy Limit Reached"),
    ],
)
def test_a_limit_that_stops_the_charge_is_named(substate, reason):
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 5, "subState": substate, "sessionTime": 1060})
    assert _reason(updater) == reason
    assert _finished(updater) == ["limit"]


def test_a_user_stop_into_state_5_is_stopped():
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 5, "subState": 1, "sessionTime": 1060})
    assert _reason(updater) == "Stopped by User"
    assert _finished(updater) == ["stopped"]


def test_a_schedule_stop_into_connected_is_schedule():
    """The charger can also park in Connected with the schedule substate."""
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 3, "subState": 5, "sessionTime": 1060})
    assert _finished(updater) == ["schedule"]


# --- a real completion must stay one ------------------------------------------


def test_a_real_completion_is_charge_complete():
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1060})
    assert _reason(updater) == "Charge Complete"
    assert _finished(updater) == ["complete"]


def test_a_full_car_stays_complete_when_the_window_closes_later():
    """2026-09-19 23:15 complete (No Limits) -> 2026-09-20 07:00 subState 5."""
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1060})
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 30000})
    assert _reason(updater) == "Charge Complete"
    # ...and back open at 23:00, still the same finished charge.
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 90000})
    assert _reason(updater) == "Charge Complete"
    assert _finished(updater) == ["complete"]


def test_button_presses_in_state_5_do_not_relabel_a_finished_charge():
    """2026-09-19 23:36: subState Limited by User for two seconds, then back."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _feed(updater, {"state": 5, "subState": 1, "sessionTime": 1002})
    assert _reason(updater) == "Charge Complete"


def test_a_failed_poll_does_not_forget_the_completion():
    """Wi-Fi drops are routine; a timeout after 07:00 must not relabel a full car."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _fail(updater)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    assert _reason(updater) == "Charge Complete"


def test_a_replug_hidden_by_an_outage_forgets_the_completion():
    """sessionTime counts from plug-in; going backwards means a new plug-in."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 5000})
    _fail(updater)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 100})
    assert _reason(updater) == "Waiting for Schedule"


def test_leaving_state_5_forgets_the_completion():
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _feed(updater, {"state": 3, "subState": 5, "sessionTime": 1100})
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 1200})
    assert _reason(updater) == "Waiting for Schedule"


def test_an_unmapped_state_does_not_forget_the_completion():
    """A code the map cannot name says nothing about the session."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _feed(updater, {"state": 20, "subState": 0, "sessionTime": 1100})
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 1200})
    assert _reason(updater) == "Charge Complete"


# --- behaviour that must not move ---------------------------------------------


def test_activation_still_wins_in_state_5():
    """subState 9 is the charger holding for a start command — a live setting."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _feed(updater, {"state": 5, "subState": 9, "sessionTime": 1010})
    assert _reason(updater) == "Waiting for Activation"


def test_ocpp_still_wins_in_state_5():
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 5, "ocppEnabled": 1, "sessionTime": 1000})
    assert _reason(updater) == "Controlled by OCPP"


def test_an_unmapped_substate_in_state_5_is_unknown_and_finishes_as_complete():
    updater = _updater()
    _feed(updater, _charging())
    _feed(updater, {"state": 5, "subState": 99, "sessionTime": 1060})
    assert _reason(updater) == "Unknown"
    assert _finished(updater) == ["complete"]


def test_legacy_firmware_state_5_is_still_charge_complete():
    """Firmware 1.x substates are its own codes; none of this reads them."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 1000}, modern=False)
    assert _reason(updater) == "Charge Complete"


def test_legacy_firmware_finish_reason_is_state_derived():
    updater = _updater()
    _feed(updater, {"state": 3, "subState": 0, "powerMeas": 1500, "curMeas1": 7}, modern=False)
    _feed(updater, {"state": 5, "subState": 5}, modern=False)
    assert _finished(updater) == ["complete"]


# --- across a restart ---------------------------------------------------------


def test_the_completion_anchor_is_the_last_session_time_seen():
    updater = _updater()
    assert updater.charge_completion_anchor is None
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 4000})
    assert updater.charge_completion_anchor == 4000
    _feed(updater, {"state": 3, "subState": 5, "sessionTime": 4100})
    assert updater.charge_completion_anchor is None


def test_a_restored_completion_applies_to_the_same_plug_in():
    """HA restarts after 07:00 with a full car: first poll reads subState 5."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    assert _reason(updater) == "Waiting for Schedule"
    updater.seed_charge_completion(4000)
    assert _reason(updater) == "Charge Complete"


def test_a_restored_completion_waits_for_the_first_poll():
    """The charger may not answer during setup; the seed waits for it."""
    updater = _updater()
    updater.seed_charge_completion(4000)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    assert _reason(updater) == "Charge Complete"


@pytest.mark.parametrize(
    "first_poll",
    [
        # Unplugged and replugged while HA was down: counter went backwards.
        {"state": 5, "subState": 5, "sessionTime": 100},
        # Not in state 5 any more: that charge is over.
        {"state": 3, "subState": 5, "sessionTime": 5000},
    ],
)
def test_a_restored_completion_is_dropped_when_it_no_longer_applies(first_poll):
    updater = _updater()
    updater.seed_charge_completion(4000)
    _feed(updater, first_poll)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 6000})
    assert _reason(updater) == "Waiting for Schedule"


@pytest.mark.parametrize("bad", [None, -1, "4000", True, 10**12, 4000.5])
def test_a_corrupt_restored_anchor_is_ignored(bad):
    updater = _updater()
    updater.seed_charge_completion(bad)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    assert _reason(updater) == "Waiting for Schedule"


def _reason_sensor(updater) -> OptimizedEveusSensor:
    spec = next(s for s in create_sensor_specifications() if s.key == "not_charging_reason")
    return OptimizedEveusSensor(updater, spec)


def test_the_reason_sensor_stores_the_completion_anchor():
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 4000})
    stored = _reason_sensor(updater).extra_restore_state_data
    assert stored is not None
    assert stored.as_dict() == {"completed_session_time": 4000}


def test_only_the_reason_sensor_stores_the_anchor():
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 4000})
    spec = next(s for s in create_sensor_specifications() if s.key == "state")
    assert OptimizedEveusSensor(updater, spec).extra_restore_state_data is None


def test_the_reason_sensor_seeds_the_updater_before_its_first_read(monkeypatch):
    from homeassistant.helpers.restore_state import RestoredExtraData

    from custom_components.eveus import common_base

    seen: list[str | None] = []

    async def _base_setup(self) -> None:
        # The base computes the first published value here.
        seen.append(get_not_charging_reason(self._updater, None))

    monkeypatch.setattr(common_base.EveusSensorBase, "async_added_to_hass", _base_setup)
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    sensor = _reason_sensor(updater)

    async def stored():
        return RestoredExtraData({"completed_session_time": 4000})

    monkeypatch.setattr(sensor, "async_get_last_extra_data", stored)
    asyncio.run(sensor.async_added_to_hass())
    assert seen == ["Charge Complete"]


def test_a_sensor_without_the_flag_does_not_read_stored_data(monkeypatch):
    from custom_components.eveus import common_base

    async def _base_setup(self) -> None:
        return None

    monkeypatch.setattr(common_base.EveusSensorBase, "async_added_to_hass", _base_setup)
    updater = _updater()
    spec = next(s for s in create_sensor_specifications() if s.key == "state")
    sensor = OptimizedEveusSensor(updater, spec)
    read = Mock(side_effect=AssertionError("must not read stored data"))
    monkeypatch.setattr(sensor, "async_get_last_extra_data", read)
    asyncio.run(sensor.async_added_to_hass())
    read.assert_not_called()


def test_the_new_finish_reasons_reach_the_last_session_sensors():
    from custom_components.eveus.session_history import _KNOWN_FINISH_REASONS

    assert {"schedule", "limit"} <= _KNOWN_FINISH_REASONS


def test_one_reply_without_the_firmware_marker_keeps_the_completion():
    """verFWMain is optional; a modern charger stays modern for the entry."""
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 1000})
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 1100}, modern=False)
    assert _reason(updater) == "Charge Complete"


@pytest.mark.parametrize(
    ("seed", "session_time"),
    [
        (4000, 4000),  # restart within the same second of counter: same plug-in
        (0, 10),  # the smallest saved value is a valid one
        (366 * 24 * 3600, 366 * 24 * 3600),  # so is the largest
    ],
)
def test_a_restored_completion_applies_at_the_edges(seed, session_time):
    updater = _updater()
    updater.seed_charge_completion(seed)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": session_time})
    assert _reason(updater) == "Charge Complete"


def test_a_stop_while_ocpp_controls_the_charger_keeps_its_state_reason():
    """With OCPP on, no schedule or limit here is what ended it."""
    updater = _updater()
    _feed(updater, {**_charging(), "ocppEnabled": 1})
    _feed(updater, {"state": 5, "subState": 5, "ocppEnabled": 1, "sessionTime": 1060})
    assert _finished(updater) == ["complete"]


def test_a_first_install_with_nothing_stored_reads_the_charger(monkeypatch):
    """No saved data (new install, or the sensor was never saved): no seed."""
    from custom_components.eveus import common_base

    seen: list[str | None] = []

    async def _base_setup(self) -> None:
        seen.append(get_not_charging_reason(self._updater, None))

    monkeypatch.setattr(common_base.EveusSensorBase, "async_added_to_hass", _base_setup)
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    sensor = _reason_sensor(updater)

    async def nothing():
        return None

    monkeypatch.setattr(sensor, "async_get_last_extra_data", nothing)
    asyncio.run(sensor.async_added_to_hass())
    assert seen == ["Waiting for Schedule"]
