"""Seeding Initial SOC from an optional external car SOC sensor."""
import asyncio
from types import SimpleNamespace

import pytest

from conftest import EveusTestUpdater, HelperHass, disable_state_writes
from custom_components.eveus import number as number_module
from custom_components.eveus.const import CONF_EXTERNAL_SOC_ENTITY
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.number import EveusInitialSocNumber

CAR_SOC = "sensor.car_battery_level"


@pytest.fixture(autouse=True)
def _no_dispatcher(monkeypatch):
    """SOC pushes go through the dispatcher, which these unit tests don't run."""
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)


def _build(
    *,
    external: str | None = CAR_SOC,
    car_soc: object = 55,
    capacity: float = 60,
    correction: float = 5,
    seed: float = 20,
):
    """Build an Initial SOC entity wired to a fake charger and car sensor."""
    updater = EveusTestUpdater({})
    updater.config_entry = SimpleNamespace(
        entry_id="entry-id",
        data={CONF_EXTERNAL_SOC_ENTITY: external} if external else {},
    )
    calc = CachedSOCCalculator()
    calc.set_value("battery_capacity", capacity)
    calc.set_value("soc_correction", correction)
    entity = EveusInitialSocNumber(updater, calc, seed=seed, device_number=1)
    entity.hass = HelperHass({CAR_SOC: car_soc} if car_soc is not None else {})
    disable_state_writes(entity)
    return entity, updater, calc


def _poll(entity, updater, state, *, session_energy=None, success=True):
    """Feed one coordinator poll with the given device state."""
    payload: dict[str, object] = {"state": state}
    if session_energy is not None:
        payload["sessionEnergy"] = session_energy
    updater.data = payload
    updater.last_update_success = success
    entity._handle_coordinator_update()


def test_session_start_seeds_initial_soc_from_car_sensor() -> None:
    """Connected -> Charging copies the car's SOC into Initial SOC."""
    entity, updater, calc = _build(car_soc=55)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 55
    assert calc.initial_soc == 55


def test_without_a_configured_sensor_initial_soc_is_untouched() -> None:
    """No external sensor configured: manual entry keeps working unchanged."""
    entity, updater, _ = _build(external=None, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 20


def test_energy_already_delivered_is_backed_out_of_the_anchor() -> None:
    """A late-observed session start must not double-count delivered energy."""
    entity, updater, _ = _build(car_soc=60, capacity=60, correction=5)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=5.0)

    # 60% minus 5 kWh grid * 0.95 efficiency on a 60 kWh pack.
    assert entity.native_value == pytest.approx(60 - 5.0 * 0.95 / 60 * 100)


def test_seeding_happens_once_per_plug_in_cycle() -> None:
    """A pause/resume inside the same session must not re-anchor."""
    entity, updater, _ = _build(car_soc=55)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 70})
    _poll(entity, updater, 6)
    _poll(entity, updater, 4)

    assert entity.native_value == 55


def test_manual_override_survives_the_rest_of_the_session() -> None:
    """A value the user sets after the seed is never overwritten mid-session."""
    entity, updater, _ = _build(car_soc=55)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    asyncio.run(entity.async_set_native_value(42))
    _poll(entity, updater, 6)
    _poll(entity, updater, 4)

    assert entity.native_value == 42


def test_next_plug_in_cycle_seeds_again() -> None:
    """Unplugging ends the session, so the next one reads the car again."""
    entity, updater, _ = _build(car_soc=55)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    _poll(entity, updater, 2)
    entity.hass = HelperHass({CAR_SOC: 70})
    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 70


def test_error_state_does_not_end_the_session() -> None:
    """Error hides the plug status, so it must not re-arm the seeding."""
    entity, updater, _ = _build(car_soc=55)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 70})
    _poll(entity, updater, 7)
    _poll(entity, updater, 4)

    assert entity.native_value == 55


@pytest.mark.parametrize("bad", ["unknown", "unavailable", "abc", "120", "-5", ""])
def test_unusable_car_reading_leaves_the_value_alone(bad) -> None:
    """Anything that isn't a plausible percentage is ignored."""
    entity, updater, _ = _build(car_soc=bad, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 20


def test_missing_car_entity_leaves_the_value_alone() -> None:
    """A renamed or removed sensor must not blank Initial SOC."""
    entity, updater, _ = _build(car_soc=None, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 20


def test_a_sleeping_car_is_not_chased_for_the_rest_of_the_session() -> None:
    """One reading per session start; a car that wakes later is not polled for."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 62})
    _poll(entity, updater, 6)
    _poll(entity, updater, 4, session_energy=2.0)

    assert entity.native_value == 20


def test_a_restarted_session_seeds_after_a_failed_first_read() -> None:
    """Charge Complete back to Charging is a new attempt, rebased on the meter."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 62})
    _poll(entity, updater, 5)
    _poll(entity, updater, 4, session_energy=2.0)

    assert entity.native_value == pytest.approx(62 - 2.0 * 0.95 / 60 * 100)


def test_a_transition_across_a_failed_poll_never_seeds() -> None:
    """An interrupted poll leaves stale data; it must not fake a session start."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, success=False)
    _poll(entity, updater, 4)

    assert entity.native_value == 20


def test_corrupt_session_energy_blocks_seeding() -> None:
    """Without a trustworthy delivered figure the anchor cannot be computed."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy="not-a-number")

    assert entity.native_value == 20


def test_seeded_value_is_clamped_into_the_entity_range() -> None:
    """Backing out more energy than the car reports cannot go below zero."""
    entity, updater, _ = _build(car_soc=1, capacity=60, correction=0)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=10.0)

    assert entity.native_value == 0


# ---------------------------------------------------------------------------
# Options flow: picking, keeping and clearing the sensor
# ---------------------------------------------------------------------------

def _options_flow(entry_data: dict):
    """Build an options flow over a fake entry that records what it stores."""
    from custom_components.eveus import config_flow

    entry = SimpleNamespace(data=dict(entry_data), unique_id="host", entry_id="opt-entry")

    class _ConfigEntries:
        def async_update_entry(self, target, *, data):
            target.data = data

        async def async_reload(self, entry_id):
            return True

    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = SimpleNamespace(
        config_entries=_ConfigEntries(),
        states=SimpleNamespace(get=lambda entity_id: None),
    )
    flow.async_show_form = lambda *, step_id, data_schema=None, **kw: {
        "type": "form",
        "step_id": step_id,
        "data_schema": data_schema,
    }
    flow.async_create_entry = lambda *, title, data: {"type": "create_entry"}
    return flow, entry


def test_options_flow_stores_the_chosen_car_sensor() -> None:
    from custom_components.eveus.const import CONF_SOC_MODE, SOC_MODE_ADVANCED

    flow, entry = _options_flow(
        {CONF_SOC_MODE: SOC_MODE_ADVANCED, "battery_capacity": 60, "soc_correction": 5}
    )

    asyncio.run(
        flow.async_step_init(
            {CONF_SOC_MODE: SOC_MODE_ADVANCED, CONF_EXTERNAL_SOC_ENTITY: CAR_SOC}
        )
    )

    assert entry.data[CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC


def test_options_flow_clears_the_sensor_when_the_field_is_emptied() -> None:
    from custom_components.eveus.const import CONF_SOC_MODE, SOC_MODE_ADVANCED

    flow, entry = _options_flow(
        {
            CONF_SOC_MODE: SOC_MODE_ADVANCED,
            CONF_EXTERNAL_SOC_ENTITY: CAR_SOC,
            "battery_capacity": 60,
            "soc_correction": 5,
        }
    )

    asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))

    assert CONF_EXTERNAL_SOC_ENTITY not in entry.data


def test_chosen_sensor_survives_the_first_switch_to_advanced() -> None:
    """Switching to Advanced detours through the SOC step; the pick must persist."""
    from custom_components.eveus.const import (
        CONF_BATTERY_CAPACITY,
        CONF_SOC_CORRECTION,
        CONF_SOC_MODE,
        SOC_MODE_ADVANCED,
        SOC_MODE_BASIC,
    )

    flow, entry = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC})

    form = asyncio.run(
        flow.async_step_init(
            {CONF_SOC_MODE: SOC_MODE_ADVANCED, CONF_EXTERNAL_SOC_ENTITY: CAR_SOC}
        )
    )
    assert form["step_id"] == "soc"
    asyncio.run(
        flow.async_step_soc({CONF_BATTERY_CAPACITY: 60, CONF_SOC_CORRECTION: 5})
    )

    assert entry.data[CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC


def test_a_payload_without_a_device_state_is_ignored() -> None:
    """A reply missing `state` is not a session start and not a transition."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    updater.data = {}
    entity._handle_coordinator_update()
    _poll(entity, updater, 4)

    assert entity.native_value == 20


def test_missing_battery_capacity_blocks_a_rebased_seed() -> None:
    """Delivered energy can't be converted to percent without a capacity."""
    entity, updater, calc = _build(car_soc=55, seed=20)
    calc.set_value("battery_capacity", None)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=3.0)

    assert entity.native_value == 20


def test_seeding_is_skipped_before_the_entity_is_added_to_hass() -> None:
    """No hass yet means no state machine to read the car sensor from."""
    entity, updater, _ = _build(car_soc=55, seed=20)
    entity.hass = None

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 20


def test_reconfigure_keeps_the_chosen_car_sensor() -> None:
    """Changing host or credentials must not silently drop the sensor choice."""
    from custom_components.eveus.config_flow import _merge_entry_data

    existing = {
        "host": "192.168.1.50",
        "username": "eveus",
        CONF_EXTERNAL_SOC_ENTITY: CAR_SOC,
    }
    incoming = {"host": "192.168.1.77", "username": "eveus", "password": "pw"}

    merged = _merge_entry_data(existing, incoming)

    assert merged[CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC
