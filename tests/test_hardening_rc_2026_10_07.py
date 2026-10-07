"""Adversarial regressions for the complete post-4.26.0 change set."""
import pytest

from test_meter_publication import METERS, make_meter


@pytest.mark.parametrize("key", METERS)
@pytest.mark.parametrize("missing", [None, "invalid", "absent"])
def test_missing_meter_stays_unavailable_during_connectivity_grace(monkeypatch, key, missing):
    entity, updater, clock, field = make_meter(monkeypatch, key)
    rows = []
    entity.async_write_ha_state = lambda: rows.append((entity.native_value, entity.available))
    entity._handle_coordinator_update()
    clock[0] = 150
    updater.data[field] = 11
    entity._handle_coordinator_update()
    if missing == "absent":
        updater.data.pop(field)
    else:
        updater.data[field] = missing
    entity._handle_coordinator_update()
    assert rows == [(10, True), (11, True), (None, False)]

    updater.available = False
    updater.seconds_unavailable = 1
    entity._handle_coordinator_update()
    assert entity.available is False
    assert rows == [(10, True), (11, True), (None, False)]
    updater.seconds_unavailable = 61
    entity._handle_coordinator_update()
    assert len(rows) == 3

    updater.available = True
    updater.data[field] = 0
    entity._handle_coordinator_update()
    assert rows[-1] == (0, True)


@pytest.mark.parametrize("key", METERS)
@pytest.mark.parametrize("value", [0, 10])
def test_meter_grace_holds_valid_values_and_expires_on_time(monkeypatch, key, value):
    entity, updater, _, field = make_meter(monkeypatch, key)
    updater.data[field] = value
    rows = []
    entity.async_write_ha_state = lambda: rows.append((entity.native_value, entity.available))
    entity._handle_coordinator_update()
    updater.available = False
    updater.seconds_unavailable = 1
    entity._handle_coordinator_update()
    assert entity.available is True
    assert entity.native_value == value
    assert rows == [(value, True)]

    updater.seconds_unavailable = 61
    assert entity.available is False
    entity._handle_coordinator_update()
    assert rows == [(value, True), (None, False)]
