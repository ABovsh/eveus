"""Seeding Initial SOC from an optional external car SOC sensor."""
import asyncio
import logging
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


def _poll(entity, updater, state, *, session_energy=0.0, success=True):
    """Feed one coordinator poll with the given device state.

    ``session_energy=None`` omits the field entirely, which the charger does
    only in an incomplete reply; every normal poll carries it.
    """
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


def test_a_sleeping_car_is_chased_until_it_wakes() -> None:
    """A car asleep at the start is picked up later in the same session."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 62})
    _poll(entity, updater, 6, session_energy=2.0)

    assert entity.native_value == pytest.approx(62 - 2.0 * 0.95 / 60 * 100)


def test_a_restarted_session_seeds_after_a_failed_first_read() -> None:
    """Charge Complete back to Charging is a new attempt, rebased on the meter."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 62})
    _poll(entity, updater, 5)
    _poll(entity, updater, 4, session_energy=2.0)

    assert entity.native_value == pytest.approx(62 - 2.0 * 0.95 / 60 * 100)


def test_a_failed_poll_itself_never_seeds() -> None:
    """Stale meter data must not be rebased against a live car reading."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=8.0, success=False)

    assert entity.native_value == 20


def test_corrupt_session_energy_blocks_seeding() -> None:
    """Without a trustworthy delivered figure the anchor cannot be computed."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy="not-a-number")

    assert entity.native_value == 20


def test_a_manually_set_value_is_still_clamped_into_the_entity_range() -> None:
    """The slider itself keeps clamping; only the automatic anchor refuses."""
    entity, _, _ = _build(car_soc=None, seed=20)

    asyncio.run(entity.async_set_native_value(140))

    assert entity.native_value == 100


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


def test_the_sensor_can_be_picked_on_the_first_switch_to_advanced() -> None:
    """Basic hides the picker, so the SOC step is where the switcher picks one."""
    from custom_components.eveus.const import (
        CONF_BATTERY_CAPACITY,
        CONF_SOC_CORRECTION,
        CONF_SOC_MODE,
        SOC_MODE_ADVANCED,
        SOC_MODE_BASIC,
    )

    flow, entry = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC})

    form = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    assert form["step_id"] == "soc"
    asyncio.run(
        flow.async_step_soc(
            {
                CONF_BATTERY_CAPACITY: 60,
                CONF_SOC_CORRECTION: 5,
                CONF_EXTERNAL_SOC_ENTITY: CAR_SOC,
            }
        )
    )

    assert entry.data[CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC


def test_basic_mode_does_not_offer_the_picker() -> None:
    """There is no Initial SOC in Basic mode, so the field has nothing to fill."""
    from custom_components.eveus.const import CONF_SOC_MODE, SOC_MODE_BASIC

    flow, _entry = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC})

    form = asyncio.run(flow.async_step_init())

    assert CONF_EXTERNAL_SOC_ENTITY not in form["data_schema"].schema


def test_switching_to_basic_keeps_a_stored_sensor() -> None:
    """A form that never showed the field must not be read as clearing it."""
    from custom_components.eveus.const import (
        CONF_SOC_MODE,
        SOC_MODE_ADVANCED,
        SOC_MODE_BASIC,
    )

    flow, entry = _options_flow(
        {
            CONF_SOC_MODE: SOC_MODE_ADVANCED,
            CONF_EXTERNAL_SOC_ENTITY: CAR_SOC,
            "battery_capacity": 60,
            "soc_correction": 5,
        }
    )
    asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC}))
    assert entry.data[CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC

    flow, entry = _options_flow(entry.data)
    asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))

    assert entry.data[CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC


def test_a_payload_without_a_device_state_is_ignored() -> None:
    """A reply missing `state` is not a session start and not a transition."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    updater.data = {}
    entity._handle_coordinator_update()

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


# ---------------------------------------------------------------------------
# Setup flow: picking the sensor during initial setup
# ---------------------------------------------------------------------------

def test_the_setup_flow_offers_the_picker_on_the_soc_step() -> None:
    """Advanced setup collects the sensor alongside capacity and correction."""
    from custom_components.eveus.config_flow import build_soc_step_schema

    schema = build_soc_step_schema(
        SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
    )

    assert CONF_EXTERNAL_SOC_ENTITY in schema.schema


def test_the_setup_flow_stores_the_sensor_picked_during_setup() -> None:
    """The pick lands in entry data, ready for the Initial SOC entity."""
    from custom_components.eveus import config_flow
    from custom_components.eveus.const import (
        CONF_BATTERY_CAPACITY,
        CONF_SOC_CORRECTION,
        CONF_SOC_MODE,
        SOC_MODE_ADVANCED,
    )

    flow = config_flow.ConfigFlow()
    flow.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
    flow._pending_entry = {"title": "Eveus", "data": {CONF_SOC_MODE: SOC_MODE_ADVANCED}}
    flow.async_create_entry = lambda *, title, data: {"data": data}

    entry = asyncio.run(
        flow.async_step_soc(
            {
                CONF_BATTERY_CAPACITY: 60,
                CONF_SOC_CORRECTION: 5,
                CONF_EXTERNAL_SOC_ENTITY: CAR_SOC,
            }
        )
    )

    assert entry["data"][CONF_EXTERNAL_SOC_ENTITY] == CAR_SOC


def test_the_setup_flow_stores_nothing_when_the_picker_is_left_empty() -> None:
    """The field is optional; skipping it must not write an empty key."""
    from custom_components.eveus import config_flow
    from custom_components.eveus.const import (
        CONF_BATTERY_CAPACITY,
        CONF_SOC_CORRECTION,
        CONF_SOC_MODE,
        SOC_MODE_ADVANCED,
    )

    flow = config_flow.ConfigFlow()
    flow.hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None))
    flow._pending_entry = {"title": "Eveus", "data": {CONF_SOC_MODE: SOC_MODE_ADVANCED}}
    flow.async_create_entry = lambda *, title, data: {"data": data}

    entry = asyncio.run(
        flow.async_step_soc({CONF_BATTERY_CAPACITY: 60, CONF_SOC_CORRECTION: 5})
    )

    assert CONF_EXTERNAL_SOC_ENTITY not in entry["data"]


# ---------------------------------------------------------------------------
# Invariant: a session is a PLUG-IN, not a charge
# ---------------------------------------------------------------------------

def test_ten_pauses_without_unplugging_stay_one_session() -> None:
    """The charger's energy counter is plug-based, so the anchor must be too.

    Pausing, stopping and completing all leave the car plugged in and leave
    ``sessionEnergy`` accumulating. Re-anchoring on any of them would subtract
    energy that is still on the meter and make every SOC figure wrong for the
    rest of the plug-in cycle.
    """
    entity, updater, calc = _build(car_soc=40, capacity=60, correction=0, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    assert entity.native_value == 40

    delivered = 0.0
    for cycle, interruption in enumerate([6, 3, 5, 6, 7, 3, 6, 5, 7, 6]):
        delivered += 1.2
        _poll(entity, updater, 4, session_energy=delivered)
        # The car reports its real, rising SOC throughout; it must be ignored.
        entity.hass = HelperHass({CAR_SOC: 40 + (cycle + 1) * 2})
        _poll(entity, updater, interruption, session_energy=delivered)

    _poll(entity, updater, 4, session_energy=delivered)

    assert entity.native_value == 40
    # 12 kWh into a 60 kWh pack at 0% loss = +20 points, accumulated across all
    # ten interruptions rather than restarting at any of them.
    assert delivered == pytest.approx(12.0)
    assert calc.get_soc_percent(delivered) == 60


def test_unplugging_is_the_only_thing_that_starts_a_new_session() -> None:
    """Standby (2) is the unplugged state, and only it re-arms the seeding."""
    entity, updater, _ = _build(car_soc=40, capacity=60, correction=0, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=6.0)
    entity.hass = HelperHass({CAR_SOC: 50})
    _poll(entity, updater, 2)
    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=0.0)

    assert entity.native_value == 50


# ---------------------------------------------------------------------------
# Retrying the seed for the rest of the plug-in cycle
# ---------------------------------------------------------------------------

def test_a_car_that_wakes_mid_session_is_picked_up() -> None:
    """The seed is retried every poll until it lands, then rebased."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20, capacity=60, correction=5)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    _poll(entity, updater, 4, session_energy=3.0)
    entity.hass = HelperHass({CAR_SOC: 45})
    _poll(entity, updater, 4, session_energy=6.0)

    assert entity.native_value == pytest.approx(45 - 6.0 * 0.95 / 60 * 100)


def test_a_pause_rescues_a_missed_seed_exactly_like_a_stop() -> None:
    """Which resume state the charger reports must not change the outcome."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20, capacity=60, correction=5)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    entity.hass = HelperHass({CAR_SOC: 62})
    _poll(entity, updater, 6, session_energy=2.0)
    _poll(entity, updater, 4, session_energy=2.0)

    assert entity.native_value == pytest.approx(62 - 2.0 * 0.95 / 60 * 100)


def test_a_transition_across_a_failed_poll_still_seeds() -> None:
    """A Wi-Fi blip at the session start must not cost the whole cycle."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, success=False)
    _poll(entity, updater, 4)

    assert entity.native_value == 55


def test_an_incomplete_reply_at_the_seed_point_is_retried() -> None:
    """A missing sessionEnergy blocks one attempt, not the whole session."""
    entity, updater, _ = _build(car_soc=44, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=None)
    _poll(entity, updater, 4)

    assert entity.native_value == 44


def test_an_anchor_below_zero_is_rejected_not_clamped() -> None:
    """A nonsense rebase must leave the user's value alone, not read 0%."""
    entity, updater, _ = _build(car_soc=1, seed=20, capacity=60, correction=0)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4, session_energy=10.0)

    assert entity.native_value == 20


# ---------------------------------------------------------------------------
# Logging: why a seed did or did not happen
# ---------------------------------------------------------------------------

def test_a_successful_seed_is_logged_once(caplog) -> None:
    """The automatic write is visible in the log with its value."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    with caplog.at_level(logging.INFO, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        _poll(entity, updater, 4)
        _poll(entity, updater, 4, session_energy=2.0)

    seeded = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(seeded) == 1
    assert CAR_SOC in seeded[0].getMessage()
    assert "55" in seeded[0].getMessage()


@pytest.mark.parametrize(
    ("car_soc", "session_energy", "fragment"),
    [
        ("unavailable", 0.0, "unavailable"),
        (150, 0.0, "outside"),
        (55, None, "session energy"),
        (55, "not-a-number", "session energy"),
    ],
)
def test_a_failed_seed_logs_why(caplog, car_soc, session_energy, fragment) -> None:
    """Each distinct reason a seed cannot happen names itself in the log."""
    entity, updater, _ = _build(car_soc=car_soc, seed=20)

    with caplog.at_level(logging.WARNING, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        _poll(entity, updater, 4, session_energy=session_energy)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert fragment in warnings[0].getMessage().lower()


def test_a_missing_capacity_names_itself_in_the_log(caplog) -> None:
    """A rebase blocked by unusable SOC helpers is distinguishable."""
    entity, updater, calc = _build(car_soc=55, seed=20)
    calc.set_value("battery_capacity", None)

    with caplog.at_level(logging.WARNING, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        _poll(entity, updater, 4, session_energy=3.0)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "capacity" in warnings[0].getMessage().lower()


def test_the_warning_is_not_repeated_on_every_poll(caplog) -> None:
    """A retry every 30s must not fill the log with the same complaint."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    with caplog.at_level(logging.WARNING, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        for _ in range(10):
            _poll(entity, updater, 4)

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_a_new_plug_in_cycle_warns_again(caplog) -> None:
    """Unplugging re-arms the warning as well as the seed."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    with caplog.at_level(logging.WARNING, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        _poll(entity, updater, 4)
        _poll(entity, updater, 2)
        _poll(entity, updater, 3)
        _poll(entity, updater, 4)

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_a_late_success_after_warnings_is_still_logged(caplog) -> None:
    """The car waking up mid-session is recorded, not swallowed by the warning."""
    entity, updater, _ = _build(car_soc="unavailable", seed=20)

    with caplog.at_level(logging.INFO, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        _poll(entity, updater, 4)
        entity.hass = HelperHass({CAR_SOC: 45})
        _poll(entity, updater, 4, session_energy=2.0)

    assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1


# ---------------------------------------------------------------------------
# No effect, and no log noise, for anyone not using the feature
# ---------------------------------------------------------------------------

def test_no_sensor_configured_logs_nothing_at_all(caplog) -> None:
    """The overwhelming majority of users must not see a single new line."""
    entity, updater, _ = _build(external=None, seed=20)

    with caplog.at_level(logging.DEBUG, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        for energy in (0.0, 2.0, 5.0, 9.0):
            _poll(entity, updater, 4, session_energy=energy)
        _poll(entity, updater, 2)

    assert caplog.records == []


def test_a_configured_sensor_logs_once_per_cycle_not_once_per_poll(caplog) -> None:
    """A 30s poll across a long night must leave two lines, not a hundred."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    with caplog.at_level(logging.DEBUG, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        for energy in range(40):
            _poll(entity, updater, 4, session_energy=float(energy))
        _poll(entity, updater, 2)
        entity.hass = HelperHass({CAR_SOC: "unavailable"})
        _poll(entity, updater, 3)
        for energy in range(40):
            _poll(entity, updater, 4, session_energy=float(energy))

    assert len(caplog.records) == 2


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_only_advanced_mode_builds_the_initial_soc_entity(mode) -> None:
    """Basic mode has no Initial SOC entity, so nothing can seed or log there."""
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.number import async_setup_entry

    added: list = []
    updater = EveusTestUpdater({"currentSet": "10"})
    entry = SimpleNamespace(
        data={
            "soc_mode": mode,
            "model": "16A",
            CONF_EXTERNAL_SOC_ENTITY: CAR_SOC,
        },
        runtime_data=SimpleNamespace(
            updater=updater,
            device_number=1,
            soc_calculator=CachedSOCCalculator(),
        ),
    )

    asyncio.run(async_setup_entry(None, entry, added.extend))

    built = any(isinstance(e, EveusInitialSocNumber) for e in added)
    assert built is (mode == "advanced")


# ---------------------------------------------------------------------------
# Surviving a config-entry reload mid-session
# ---------------------------------------------------------------------------

def _reload(entity, monkeypatch, *, restored_value, seeded):
    """Bring the entity up the way HA does after a reload, with stored data."""
    monkeypatch.setattr(
        number_module.BaseEveusEntity,
        "async_added_to_hass",
        lambda self: asyncio.sleep(0),
    )

    async def fake_number_data(self):
        return SimpleNamespace(native_value=restored_value)

    async def fake_extra_data(self):
        return SimpleNamespace(as_dict=lambda: {"seeded": seeded})

    monkeypatch.setattr(
        type(entity), "async_get_last_number_data", fake_number_data, raising=False
    )
    monkeypatch.setattr(
        type(entity), "async_get_last_extra_data", fake_extra_data, raising=False
    )
    asyncio.run(entity.async_added_to_hass())


def test_a_reload_mid_session_keeps_a_hand_corrected_value(monkeypatch) -> None:
    """Saving options mid-charge must not revert a correction the user made."""
    entity, updater, _ = _build(car_soc=55, seed=42)

    _reload(entity, monkeypatch, restored_value=42, seeded=True)
    _poll(entity, updater, 4, session_energy=10.0)

    assert entity.native_value == 42


def test_a_reload_before_any_seed_still_retries(monkeypatch) -> None:
    """An unseeded cycle must keep trying across a reload, rebased as usual."""
    entity, updater, _ = _build(car_soc=55, seed=20, capacity=60, correction=5)

    _reload(entity, monkeypatch, restored_value=20, seeded=False)
    _poll(entity, updater, 4, session_energy=10.0)

    assert entity.native_value == pytest.approx(55 - 10.0 * 0.95 / 60 * 100)


def test_unplugging_after_a_reload_re_arms_seeding(monkeypatch) -> None:
    """A restored seeded flag must not block the next plug-in cycle."""
    entity, updater, _ = _build(car_soc=55, seed=42)

    _reload(entity, monkeypatch, restored_value=42, seeded=True)
    _poll(entity, updater, 4, session_energy=10.0)
    _poll(entity, updater, 2)
    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 55


def test_the_seeded_flag_is_written_into_the_stored_state() -> None:
    """The flag only survives a reload if it is actually persisted."""
    entity, updater, _ = _build(car_soc=55, seed=20)

    assert entity.extra_restore_state_data.as_dict()["seeded"] is False
    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    stored = entity.extra_restore_state_data.as_dict()
    assert stored["seeded"] is True
    assert stored["native_value"] == 55


# ---------------------------------------------------------------------------
# The seed outcome reaches the calculator that diagnostics reads
# ---------------------------------------------------------------------------

def test_a_real_seed_records_its_outcome_for_diagnostics() -> None:
    """diagnostics reads last_seed; the seed path must actually populate it."""
    entity, updater, calc = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert calc.last_seed["seeded"] is True
    assert "55.0%" in calc.last_seed["detail"]
    assert CAR_SOC in calc.last_seed["detail"]


def test_a_real_failure_records_its_reason_for_diagnostics() -> None:
    """The reason in the log must be the reason diagnostics shows."""
    entity, updater, calc = _build(car_soc="unavailable", seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert calc.last_seed["seeded"] is False
    assert "unavailable" in calc.last_seed["detail"]


def test_unplugging_clears_the_recorded_outcome() -> None:
    """A finished cycle's result must not be reported as the current one."""
    entity, updater, calc = _build(car_soc=55, seed=20)

    _poll(entity, updater, 3)
    _poll(entity, updater, 4)
    _poll(entity, updater, 2)

    assert calc.last_seed == {}


def test_a_fresh_install_has_no_stored_seed_flag(monkeypatch) -> None:
    """First ever start: nothing stored, so the cycle is free to seed."""
    entity, updater, _ = _build(car_soc=55, seed=20)
    monkeypatch.setattr(
        number_module.BaseEveusEntity,
        "async_added_to_hass",
        lambda self: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        type(entity), "async_get_last_number_data",
        lambda self: asyncio.sleep(0), raising=False,
    )

    async def no_extra_data(self):
        return None

    monkeypatch.setattr(
        type(entity), "async_get_last_extra_data", no_extra_data, raising=False
    )
    asyncio.run(entity.async_added_to_hass())
    _poll(entity, updater, 3)
    _poll(entity, updater, 4)

    assert entity.native_value == 55


def test_an_out_of_range_correction_names_itself(caplog) -> None:
    """A corrupt SOC-correction helper blocks the rebase with its own reason."""
    entity, updater, calc = _build(car_soc=55, seed=20)
    calc.set_value("soc_correction", 150)

    with caplog.at_level(logging.WARNING, logger=number_module._LOGGER.name):
        _poll(entity, updater, 3)
        _poll(entity, updater, 4, session_energy=3.0)

    assert entity.native_value == 20
    assert calc.last_seed["seeded"] is False
    assert "correction" in calc.last_seed["detail"].lower()
    assert "150" in calc.last_seed["detail"]
