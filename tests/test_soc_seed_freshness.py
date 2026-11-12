"""Audit 2026-11-12 #01: Initial SOC is seeded only from session energy this
poll actually measured.

REVE's /main fallback lays the last GraphQL session (held up to five
minutes) under a poll that otherwise succeeds. Rebasing a car reading against
that older energy leaves the anchor off for the rest of the session.
"""
import asyncio
from types import SimpleNamespace

from conftest import HelperHass, disable_state_writes
from test_reve_graphql_writes import _known_charging
from test_soc_limit import _calc
from custom_components.eveus.number import EveusInitialSocNumber


def test_cached_old_session_cannot_seed_new_session_soc(monkeypatch):
    monkeypatch.setattr('custom_components.eveus.number.async_dispatcher_send', lambda *_: None)
    charger, updater = _known_charging(monkeypatch)
    charger.station['session'].update(sessionEnergy=10, sessionTime=7200)
    asyncio.run(updater._async_update_data())
    calc = _calc(initial=20, cap=50, target=80, corr=0)
    updater.config_entry = SimpleNamespace(entry_id='e1', data={'external_soc_entity':'sensor.car_soc'})
    entity = EveusInitialSocNumber(updater, calc, seed=20)
    entity.hass = HelperHass({'sensor.car_soc':60})
    disable_state_writes(entity)
    # Car asleep previously: no seed. An unplug and new charge occur between
    # polls, while GraphQL fails but /main continues to report Charging.
    charger.graphql_status = 500
    charger.main.update(state=4, evseEnabled=0)
    charger.station['session'].update(sessionEnergy=0, sessionTime=60)
    asyncio.run(updater._async_update_data())
    assert updater.last_update_success and updater.available
    assert updater.snapshot.session_energy_kwh == 10  # retained, not current
    entity._maybe_seed_from_external_soc()
    assert not entity._session_seeded, 'car 60% rebased using an old session\'s 10 kWh'


def test_late_soc_read_during_fallback_permanently_biases_same_session(monkeypatch):
    monkeypatch.setattr('custom_components.eveus.number.async_dispatcher_send', lambda *_: None)
    charger, updater = _known_charging(monkeypatch)
    charger.station['session'].update(sessionEnergy=10, sessionTime=7200)
    asyncio.run(updater._async_update_data())
    calc = _calc(initial=20, cap=50, target=80, corr=0)
    updater.config_entry = SimpleNamespace(entry_id='e1', data={'external_soc_entity':'sensor.car_soc'})
    entity = EveusInitialSocNumber(updater, calc, seed=20)
    entity.hass = HelperHass({'sensor.car_soc':60})
    disable_state_writes(entity)
    charger.graphql_status = 500
    charger.main.update(state=4, evseEnabled=0)
    charger.station['session'].update(sessionEnergy=12, sessionTime=7440)
    asyncio.run(updater._async_update_data())
    entity._maybe_seed_from_external_soc()
    charger.graphql_status = 200
    updater._reve._measurements_retry_at = 0
    asyncio.run(updater._async_update_data())
    entity._maybe_seed_from_external_soc()
    assert calc.get_soc_percent_exact(12) == 60, 'fresh data cannot correct stale seed: estimates 64%'


def test_a_measured_session_seeds_at_once(monkeypatch):
    monkeypatch.setattr('custom_components.eveus.number.async_dispatcher_send', lambda *_: None)
    charger, updater = _known_charging(monkeypatch)
    charger.station['session'].update(sessionEnergy=10, sessionTime=7200)
    asyncio.run(updater._async_update_data())
    calc = _calc(initial=20, cap=50, target=80, corr=0)
    updater.config_entry = SimpleNamespace(entry_id='e1', data={'external_soc_entity': 'sensor.car_soc'})
    entity = EveusInitialSocNumber(updater, calc, seed=20)
    entity.hass = HelperHass({'sensor.car_soc': 60})
    disable_state_writes(entity)
    entity._maybe_seed_from_external_soc()
    assert entity._session_seeded
    assert calc.get_soc_percent_exact(10) == 60


def test_a_fresh_zero_seeds_the_car_reading_unchanged(monkeypatch):
    monkeypatch.setattr('custom_components.eveus.number.async_dispatcher_send', lambda *_: None)
    charger, updater = _known_charging(monkeypatch)
    charger.station['session'].update(sessionEnergy=0, sessionTime=10)
    asyncio.run(updater._async_update_data())
    calc = _calc(initial=20, cap=50, target=80, corr=0)
    updater.config_entry = SimpleNamespace(entry_id='e1', data={'external_soc_entity': 'sensor.car_soc'})
    entity = EveusInitialSocNumber(updater, calc, seed=20)
    entity.hass = HelperHass({'sensor.car_soc': 60})
    disable_state_writes(entity)
    entity._maybe_seed_from_external_soc()
    assert calc.get_soc_percent_exact(0) == 60


def test_classic_firmware_session_energy_is_always_measured(monkeypatch):
    from test_reve_firmware import _Charger, _classic_main, _updater

    updater = _updater(monkeypatch, _Charger(_classic_main()))
    asyncio.run(updater._async_update_data())
    assert updater.session_energy_measured
