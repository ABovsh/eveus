"""Unit tests for entity construction."""
from __future__ import annotations

from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.switch import (
    EveusOneChargeSwitch,
    EveusResetCounterASwitch,
    EveusStopChargingSwitch,
)


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True
    data = {
        "currentSet": "16",
        "evseEnabled": "1",
        "oneCharge": "0",
        "IEM1": "5.5",
    }

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


def test_switch_entities_keep_backward_compatible_unique_ids() -> None:
    updater = _Updater()

    assert EveusStopChargingSwitch(updater).unique_id == "eveus_stop_charging"
    assert EveusOneChargeSwitch(updater).unique_id == "eveus_one_charge"
    assert EveusResetCounterASwitch(updater).unique_id == "eveus_reset_counter_a"


def test_number_entity_keeps_backward_compatible_unique_id_and_limits() -> None:
    entity = EveusCurrentNumber(_Updater(), "16A")

    assert entity.unique_id == "eveus_charging_current"
    assert entity.native_min_value == 7
    assert entity.native_max_value == 16
