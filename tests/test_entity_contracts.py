"""Entity-level contracts: device classes, metadata resolution, binary sensor
grace holds, and the optimistic value outranking a stale device reading."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from homeassistant.components.sensor import SensorDeviceClass
from custom_components.eveus.session_history import LastSessionCostSensor
import asyncio
import pytest
from conftest import EveusTestUpdater
from custom_components.eveus import binary_sensor
from custom_components.eveus.const import (
    DEVICE_STATE_CHARGING,
)
from homeassistant.components.number import NumberDeviceClass
from custom_components.eveus.number import (
    CHARGING_CURRENT_DESCRIPTION,
    GLOBAL_LIMIT_NUMBERS,
    SCHEDULE_LIMIT_NUMBERS,
    UNDERVOLTAGE_THRESHOLD_NUMBER,
)
from conftest import SnapshotBackedMock
from custom_components.eveus.common_base import BaseEveusEntity


def test_last_session_cost_sensor_has_monetary_device_class() -> None:
    """Every cost sensor in the integration declares MONETARY except this one.

    Without it the frontend skips currency formatting/semantics for a sensor
    that already carries a currency unit (UAH) and icon.
    """
    instance = object.__new__(LastSessionCostSensor)
    assert instance.device_class == SensorDeviceClass.MONETARY

def _car_connected_sensor(updater):
    description = next(
        item for item in binary_sensor.BINARY_SENSORS if item.name == "Car Connected"
    )
    sensor = binary_sensor.EveusBinarySensor(updater, description, 1)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None
    return sensor


def test_binary_sensor_holds_value_during_grace_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reverses this round's own E-F01, on evidence it did not have.

    E-F01 made binary sensors blank during the grace window "as for every
    other sensor" — symmetry with the spec sensors. The spec sensors were the
    wrong sibling to copy: staying available with no value is published as
    `unknown`, and on 2026-09-05 a charger timeout blanked 36 entities for
    60 s, which two `utility_meter` helpers ingested as an invalid state
    ("received an invalid new state ... : unknown") before the entities
    honestly turned `unavailable`. The SOC sensors had always held instead,
    and every control entity holds through its own 30 s window, so blanking
    was the odd one out in three places at once.

    Both families now hold, and the entity goes `unavailable` — never
    `unknown` — when the window closes. Full reasoning in
    `BaseEveusEntity._in_availability_grace`.
    """
    updater = EveusTestUpdater({"state": DEVICE_STATE_CHARGING}, available=True)
    sensor = _car_connected_sensor(updater)

    sensor._handle_coordinator_update()
    assert sensor.is_on is True

    # One failed poll: the entity stays available for the grace window, but the
    # payload behind it is now stale.
    updater.available = False
    sensor._handle_coordinator_update()

    assert sensor.available is True
    assert sensor.is_on is True

_EXPECTED_DEVICE_CLASS = {
    "A": NumberDeviceClass.CURRENT,
    "V": NumberDeviceClass.VOLTAGE,
    "kWh": NumberDeviceClass.ENERGY,
    "min": NumberDeviceClass.DURATION,
}


def test_every_physically_typed_setpoint_declares_its_device_class() -> None:
    """A unit without its device class is a control HA cannot label or group.

    Pinned as an invariant over all setpoints rather than per entity: the
    schedule current limits were the one A-valued control left undeclared
    while Charging Current, in the same unit, carried the class.
    """
    descriptions = (
        CHARGING_CURRENT_DESCRIPTION,
        UNDERVOLTAGE_THRESHOLD_NUMBER,
        *GLOBAL_LIMIT_NUMBERS,
        *SCHEDULE_LIMIT_NUMBERS,
    )

    mismatched = {
        description.key: (
            description.native_unit_of_measurement,
            description.device_class,
        )
        for description in descriptions
        if description.native_unit_of_measurement in _EXPECTED_DEVICE_CLASS
        and description.device_class
        != _EXPECTED_DEVICE_CLASS[description.native_unit_of_measurement]
    }

    assert mismatched == {}

def test_setpoint_number_optimistic_value_outranks_a_stale_device_reading() -> None:
    """The largest control family had no guard on the rule it depends on.

    A setpoint written by the user is shown immediately and held for the
    optimistic TTL, because the charger keeps reporting the OLD number until it
    has applied the new one. `EveusCurrentNumber` has had this precedence
    pinned since the optimistic layer landed; the setpoint family — Energy and
    Cost Limit, every schedule limit, the Undervoltage threshold — never got
    the equivalent, so reversing the two reads left the whole suite green while
    every one of those sliders snapped back to the stale value after a write.
    """
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.eveus.number import (
        EveusSetpointNumber,
        EveusSetpointNumberDescription,
    )

    description = EveusSetpointNumberDescription(
        key="limit_energy",
        name="Limit Energy",
        command="energyLimit",
        state_key="energyLimit",
        device_to_ha=1.0,
        ha_to_device=1000.0,
        native_min_value=0.0,
        native_max_value=100.0,
        native_step=1.0,
        native_unit_of_measurement="kWh",
    )
    updater = SnapshotBackedMock()
    updater.available = True
    updater.data = {"energyLimit": 10}
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    entity = EveusSetpointNumber(updater, description, device_number=1)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()

    # The charger still reports the old figure, as it does until it applies the
    # write — so the two sources disagree, which is the only state in which the
    # precedence is observable at all.

    asyncio.run(entity.async_set_native_value(40))
    assert updater.data["energyLimit"] == 10

    assert entity._resolve_value() == 40.0

@pytest.mark.parametrize("attached", [True, False])
def test_metadata_uses_attached_device_without_legacy_lookup(monkeypatch, attached):
    """Metadata belongs to the entity's device, never a global identifier match."""
    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Registry Probe"

    updater = EveusTestUpdater({"verFWMain": "R3.05.2"})
    entity = Probe(updater)
    entity.hass = SimpleNamespace()
    entity.device_entry = SimpleNamespace(id="owned-device") if attached else None
    registry = Mock()
    registry.async_get_device.side_effect = AssertionError("identifier lookup must not pick the device")
    monkeypatch.setattr("custom_components.eveus.common_base.dr.async_get", lambda hass: registry)

    entity._maybe_finalize_device_info()

    registry.async_get_device.assert_not_called()
    if attached:
        assert registry.async_update_device.call_args.args == ("owned-device",)
        assert registry.async_update_device.call_args.kwargs["sw_version"] == "R3.05.2"
        assert updater._device_registry_finalized is True
    else:
        registry.async_update_device.assert_not_called()
        assert not getattr(updater, "_device_registry_finalized", False)
