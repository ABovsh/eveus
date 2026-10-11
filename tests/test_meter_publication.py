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
