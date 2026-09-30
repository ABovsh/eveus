"""Completion memory survives polls that cannot verify the restored session."""
import pytest

from test_charge_completion import _feed, _reason, _reason_sensor, _updater


@pytest.mark.parametrize("counter", [None, True, -1, "bad", 10**12])
def test_restored_completion_waits_for_a_usable_session_counter(counter):
    updater = _updater()
    updater.seed_charge_completion(4000)
    payload = {"state": 5, "subState": 5}
    if counter is not None:
        payload["sessionTime"] = counter
    _feed(updater, payload)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000})
    assert _reason(updater) == "Charge Complete"


def test_unverified_completion_is_saved_during_an_offline_restart():
    updater = _updater()
    updater.seed_charge_completion(4000)
    stored = _reason_sensor(updater).extra_restore_state_data.as_dict()
    assert stored["completed_session_time"] == 4000
    restarted = _updater()
    restarted.seed_charge_completion(stored["completed_session_time"])
    _feed(restarted, {"state": 5, "subState": 5, "sessionTime": 5000})
    assert _reason(restarted) == "Charge Complete"


def test_restored_completion_waits_for_a_firmware_marker():
    updater = _updater()
    updater.seed_charge_completion(4000)
    # verFWMain is optional: until it appears, this reply cannot establish
    # which subState map applies to the saved modern completion.
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000}, modern=False)
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5100})
    assert _reason(updater) == "Charge Complete"


def test_completion_anchor_advances_without_a_repeated_firmware_marker():
    updater = _updater()
    _feed(updater, {"state": 5, "subState": 0, "sessionTime": 4000})
    _feed(updater, {"state": 5, "subState": 5, "sessionTime": 5000}, modern=False)
    assert updater.charge_completion_anchor == 5000
