"""Regressions from the 2026-09-24 rc audit round."""
import pytest

from test_soc_autofill import CAR_SOC, _build, _no_dispatcher, _poll  # noqa: F401 - autouse fixture

from conftest import HelperHass
from custom_components.eveus.session_history import LastSessionEnergySensor
from test_last_session import _event as _last_session_event
from test_last_session import _updater as _last_session_updater


@pytest.mark.parametrize("unmapped", [8, 21, 255])
def test_an_unmapped_state_code_does_not_end_the_seeded_session(unmapped) -> None:
    """A state code the integration cannot name says nothing about the plug.

    Every other plug consumer stands down on it through ``known_state``; the
    seeding must too, or one unrecognised reply re-arms it mid-session and the
    car's later reading replaces the anchor the session started from.
    """
    entity, updater, _ = _build(car_soc=55)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 70})
    _poll(entity, updater, unmapped)
    _poll(entity, updater, 4)

    assert entity.native_value == 55


def test_a_session_with_an_unusable_reading_does_not_keep_the_previous_attributes() -> None:
    """State and attributes describe the same session.

    When the finished event carries no usable value for this sensor, the
    state goes unknown; the attributes must not keep reporting the previous
    session's reason and finish time next to it.
    """
    sensor = LastSessionEnergySensor(_last_session_updater(), 1)
    sensor._handle_finished_event(_last_session_event(reason="complete"))
    first_finish = sensor.extra_state_attributes["finished_at"]

    sensor._handle_finished_event(
        _last_session_event(reason="stopped", session_energy_kwh=None)
    )

    assert sensor.native_value is None
    assert sensor.extra_state_attributes.get("reason") != "complete"
    assert sensor.extra_state_attributes.get("finished_at") != first_finish
