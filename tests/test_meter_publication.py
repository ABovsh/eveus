"""Meter publication preserves endpoints while limiting ordinary state changes."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from conftest import PayloadUpdater
from custom_components.eveus import sensor_definitions as sd

METERS = {'total_energy':'totalEnergy', 'session_energy':'sessionEnergy',
          'counter_a_energy':'IEM1', 'counter_b_energy':'IEM2',
          'session_cost':'sessionMoney', 'counter_a_cost':'IEM1_money', 'counter_b_cost':'IEM2_money'}


def make_meter(monkeypatch, key='total_energy'):
    clock = [120.0]
    monkeypatch.setattr(sd.dt_util, 'utcnow', lambda: datetime.fromtimestamp(clock[0], timezone.utc))
    field = METERS[key]
    updater = PayloadUpdater({field:10, 'state':4, 'evseEnabled':0, 'sessionTime':100}, available=True, host='192.168.1.50')
    spec = next(s for s in sd.create_sensor_specifications() if s.key == key)
    entity = sd.create_sensor(spec, updater, 1)
    entity.async_write_ha_state = Mock()
    return entity, updater, clock, field


@pytest.mark.parametrize('key', METERS)
def test_meter_publishes_at_minute_boundary_and_keeps_raw_snapshot(monkeypatch, key):
    entity, updater, clock, field = make_meter(monkeypatch, key)
    entity._handle_coordinator_update()
    first = entity.native_value
    clock[0] = 150
    updater.data[field] = 11
    entity._handle_coordinator_update()
    assert entity.native_value == first
    assert updater.snapshot.get(field) == 11
    assert entity.async_write_ha_state.call_count == 1
    clock[0] = 180
    updater.data[field] = 12
    entity._handle_coordinator_update()
    assert entity.native_value > first
    assert entity.async_write_ha_state.call_count == 2


def test_reset_flushes_withheld_tail_before_zero(monkeypatch):
    entity, updater, clock, field = make_meter(monkeypatch)
    values = []
    entity.async_write_ha_state = lambda: values.append(entity.native_value)
    entity._handle_coordinator_update()
    clock[0] = 150
    updater.data[field] = 11
    entity._handle_coordinator_update()
    clock[0] = 155
    updater.data[field] = 0
    entity._handle_coordinator_update()
    assert values == [10, 11, 0]


@pytest.mark.parametrize('change', ['stop', 'outage', 'missing', 'session'])
def test_terminal_transition_flushes_latest_value(monkeypatch, change):
    entity, updater, clock, field = make_meter(monkeypatch)
    entity._handle_coordinator_update()
    clock[0] = 150
    updater.data[field] = 11
    entity._handle_coordinator_update()
    if change == 'stop':
        updater.data['evseEnabled'] = 1
    elif change == 'outage':
        updater.available = False
    elif change == 'missing':
        updater.data.pop(field)
    else:
        updater.data['sessionTime'] = 0
    clock[0] = 155
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count >= 2
    if change in ('stop', 'session', 'outage'):
        assert entity._attr_native_value == 11


def test_diagnostic_links_no_longer_create_statistics():
    specs = {s.key:s for s in sd.create_sensor_specifications()}
    for key in ('wifi_signal', 'connection_quality'):
        assert specs[key].state_class is None


@pytest.mark.parametrize('key', ['session_cost', 'counter_a_cost', 'counter_b_cost'])
def test_cost_reset_flushes_tail_in_old_window_then_opens_new_window(monkeypatch, key):
    entity, updater, clock, field = make_meter(monkeypatch, key)
    rows = []
    entity.async_write_ha_state = lambda: rows.append((entity._attr_native_value, entity._attr_last_reset))
    entity._handle_coordinator_update()
    old_window = entity._attr_last_reset
    clock[0] = 150
    updater.data[field] = 11
    entity._handle_coordinator_update()
    clock[0] = 155
    updater.data[field] = 0
    entity._handle_coordinator_update()
    assert [(value, window) for value, window in rows[:2]] == [(10, old_window), (11, old_window)]
    assert rows[2][0] == 0
    assert rows[2][1] > old_window


def test_unchanged_meter_and_new_device_do_not_add_rows(monkeypatch):
    first, updater, clock, field = make_meter(monkeypatch)
    first._handle_coordinator_update()
    for stamp in (150, 180, 210, 240):
        clock[0] = stamp
        first._handle_coordinator_update()
    assert first.async_write_ha_state.call_count == 1
    second = sd.create_sensor(first._spec, updater, 2)
    second.async_write_ha_state = Mock()
    updater.data[field] = 20
    second._handle_coordinator_update()
    assert second.native_value == 20
    assert first.native_value == 10


# ---------------------------------------------------------------------------
# The 0.1 kWh band: a minute gone is necessary, not enough
# ---------------------------------------------------------------------------

ENERGY_OF = {'session_cost': 'sessionEnergy', 'counter_a_cost': 'IEM1', 'counter_b_cost': 'IEM2'}


def make_banded(monkeypatch, key='total_energy'):
    """A meter whose updater reports its energy as well, as a charger does."""
    clock = [120.0]
    monkeypatch.setattr(sd.dt_util, 'utcnow', lambda: datetime.fromtimestamp(clock[0], timezone.utc))
    field = METERS[key]
    energy = ENERGY_OF.get(key, field)
    updater = PayloadUpdater(
        {energy: 10.0, field: 10.0, 'state': 4, 'evseEnabled': 0, 'sessionTime': 100},
        available=True, host='192.168.1.50',
    )
    spec = next(s for s in sd.create_sensor_specifications() if s.key == key)
    entity = sd.create_sensor(spec, updater, 1)
    entity.async_write_ha_state = Mock()
    return entity, updater, clock, energy, field


def poll(entity, updater, clock, at, **data):
    clock[0] = at
    updater.data.update(data)
    entity._handle_coordinator_update()


@pytest.mark.parametrize('key', ['total_energy', 'session_energy', 'counter_a_energy', 'counter_b_energy'])
def test_less_than_a_band_of_growth_is_not_written_at_the_next_minute(monkeypatch, key):
    entity, updater, clock, energy, _field = make_banded(monkeypatch, key)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 180, **{energy: 10.05})
    assert entity.native_value == 10.0
    assert entity.async_write_ha_state.call_count == 1
    assert updater.snapshot.get(energy) == 10.05


def test_growth_of_exactly_a_band_is_written_at_the_next_minute(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 180, **{energy: 10.1})
    assert entity.native_value == 10.1
    assert entity.async_write_ha_state.call_count == 2


def test_a_band_reached_within_the_minute_waits_for_the_minute(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 150, **{energy: 10.5})
    assert entity.async_write_ha_state.call_count == 1
    poll(entity, updater, clock, 180, **{energy: 10.5})
    assert entity.native_value == 10.5
    assert entity.async_write_ha_state.call_count == 2


def test_the_band_is_counted_from_the_last_written_value(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 180, **{energy: 10.06})
    assert entity.async_write_ha_state.call_count == 1
    poll(entity, updater, clock, 240, **{energy: 10.12})
    assert entity.native_value == 10.12
    assert entity.async_write_ha_state.call_count == 2


def test_at_36_kw_a_row_is_written_about_every_other_minute(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    entity._handle_coordinator_update()
    for minute in range(1, 11):
        poll(entity, updater, clock, 120 + 60 * minute, **{energy: round(10.0 + 0.06 * minute, 2)})
    # 0.06 kWh a minute: written when 0.12, 0.12, ... have gathered.
    assert entity.async_write_ha_state.call_count == 1 + 5


def test_at_more_than_a_band_a_minute_it_is_still_one_row_a_minute(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    entity._handle_coordinator_update()
    for minute in range(1, 11):
        poll(entity, updater, clock, 120 + 60 * minute, **{energy: round(10.0 + 0.15 * minute, 2)})
    assert entity.async_write_ha_state.call_count == 1 + 10


def test_the_tail_under_a_band_is_written_when_the_charge_stops(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 180, **{energy: 10.04})
    assert entity.native_value == 10.0
    poll(entity, updater, clock, 185, evseEnabled=1)
    assert entity.native_value == 10.04


def test_a_reset_is_written_at_once_whatever_the_band(monkeypatch):
    entity, updater, clock, energy, _field = make_banded(monkeypatch)
    values = []
    entity.async_write_ha_state = lambda: values.append(entity.native_value)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 180, **{energy: 10.04})
    poll(entity, updater, clock, 185, **{energy: 0})
    assert values == [10.0, 10.04, 0]


@pytest.mark.parametrize('key', ['session_cost', 'counter_a_cost', 'counter_b_cost'])
def test_a_cost_is_written_on_the_poll_its_energy_is(monkeypatch, key):
    cost, updater, clock, energy, field = make_banded(monkeypatch, key)
    meter = next(k for k, v in METERS.items() if v == energy)
    spec = next(s for s in sd.create_sensor_specifications() if s.key == meter)
    kwh = sd.create_sensor(spec, updater, 1)
    kwh.async_write_ha_state = Mock()
    cost._handle_coordinator_update()
    kwh._handle_coordinator_update()
    for minute, (grown, price) in enumerate([(10.04, 10.1), (10.13, 10.4), (10.17, 10.5), (10.26, 10.8)], 1):
        poll(cost, updater, clock, 120 + 60 * minute, **{energy: grown, field: price})
        kwh._handle_coordinator_update()
        assert cost.async_write_ha_state.call_count == kwh.async_write_ha_state.call_count
    assert kwh.async_write_ha_state.call_count == 3


@pytest.mark.parametrize('key', ['session_cost', 'counter_a_cost', 'counter_b_cost'])
def test_a_cost_without_its_energy_is_written_every_minute_as_before(monkeypatch, key):
    entity, updater, clock, field = make_meter(monkeypatch, key)
    entity._handle_coordinator_update()
    poll(entity, updater, clock, 180, **{field: 10.01})
    assert entity.native_value == 10.01
    assert entity.async_write_ha_state.call_count == 2


def test_the_band_is_a_tenth_of_a_kilowatt_hour_on_every_meter():
    specs = {s.key: s for s in sd.create_sensor_specifications()}
    assert {key: (specs[key].publish_band_key, specs[key].publish_band) for key in METERS} == {
        'total_energy': ('totalEnergy', 0.1),
        'session_energy': ('sessionEnergy', 0.1),
        'counter_a_energy': ('IEM1', 0.1),
        'counter_b_energy': ('IEM2', 0.1),
        'session_cost': ('sessionEnergy', 0.1),
        'counter_a_cost': ('IEM1', 0.1),
        'counter_b_cost': ('IEM2', 0.1),
    }


@pytest.mark.parametrize('key', ['session_cost', 'counter_a_cost', 'counter_b_cost'])
def test_a_cost_whose_energy_stops_being_reported_is_written_every_minute(monkeypatch, key):
    # The band needs the energy to measure against; without it the minute alone
    # decides, as before the band existed.
    entity, updater, clock, energy, field = make_banded(monkeypatch, key)
    entity._handle_coordinator_update()
    del updater.data[energy]
    poll(entity, updater, clock, 180, **{field: 10.01})
    assert entity.native_value == 10.01
    assert entity.async_write_ha_state.call_count == 2
