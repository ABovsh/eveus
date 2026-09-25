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


def _cost_sensor(value):
    from conftest import EveusTestUpdater
    from test_sensor_definition_entities import _make_cost_sensor

    return _make_cost_sensor(EveusTestUpdater(data={"sessionMoney": value}))


def test_a_restart_during_an_outage_keeps_the_cost_window(monkeypatch) -> None:
    """A restart while the charger is offline saves an `unavailable` state with no last_reset.

    The window must come back from the entity's own stored data anyway: otherwise the
    first reading after the outage opens a new window and the statistics add the whole
    counter to the sum again (nine times in 30 days on the live charger).
    """
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from homeassistant.helpers.restore_state import RestoredExtraData

    opened = datetime(2026, 9, 21, 14, 24, tzinfo=timezone.utc)
    sensor = _cost_sensor(12.5)

    async def stored():
        return RestoredExtraData({"last_reset": opened.isoformat(), "value": 12.5})

    monkeypatch.setattr(sensor, "async_get_last_extra_data", stored)
    asyncio.run(sensor._async_restore_state(SimpleNamespace(state="unavailable", attributes={})))
    sensor._update_native_value()

    assert sensor.last_reset == opened


def test_the_cost_window_is_stored_with_the_entity() -> None:
    """What the restore above reads is what the entity saves, whatever its state."""
    from datetime import datetime, timezone

    sensor = _cost_sensor(12.5)
    sensor._update_native_value()
    opened = datetime(2026, 9, 21, 14, 24, tzinfo=timezone.utc)
    sensor._attr_last_reset = opened

    stored = sensor.extra_restore_state_data
    assert stored is not None
    assert stored.as_dict() == {"last_reset": opened.isoformat(), "value": 12.5}


@pytest.mark.parametrize("bad", [10**400, float("inf"), float("nan"), True, "12.5"])
def test_a_corrupt_stored_cost_value_is_turned_away(monkeypatch, bad) -> None:
    """Stored data is read back from disk: anything but a sane number is ignored, never raised on."""
    import asyncio
    from types import SimpleNamespace

    from homeassistant.helpers.restore_state import RestoredExtraData

    sensor = _cost_sensor(12.5)

    async def stored():
        return RestoredExtraData({"last_reset": None, "value": bad})

    monkeypatch.setattr(sensor, "async_get_last_extra_data", stored)
    asyncio.run(sensor._async_restore_state(SimpleNamespace(state="unavailable", attributes={})))

    assert sensor._prev_cost_value is None
