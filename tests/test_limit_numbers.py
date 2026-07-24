from homeassistant.components.number import NumberMode

from custom_components.eveus.number import (
    CHARGING_CURRENT_DESCRIPTION,
    GLOBAL_LIMIT_NUMBERS,
    UNDERVOLTAGE_THRESHOLD_NUMBER,
    _VALID_MIN_VOLTAGES,
)


def _by_key(descs):
    return {d.key: d for d in descs}


def test_energy_limit_write_scale_is_1000():
    d = _by_key(GLOBAL_LIMIT_NUMBERS)["limit_energy"]
    assert d.command == "energyLimit"
    assert d.device_to_ha == 1.0 and d.ha_to_device == 1000.0


def test_time_limit_scales_minutes_to_seconds():
    d = _by_key(GLOBAL_LIMIT_NUMBERS)["limit_time"]
    assert d.command == "timeLimit"
    assert d.ha_to_device == 60.0 and abs(d.device_to_ha - 1 / 60) < 1e-9


def test_cost_limit_is_one_to_one():
    d = _by_key(GLOBAL_LIMIT_NUMBERS)["limit_cost"]
    assert d.command == "moneyLimit"
    assert d.device_to_ha == 1.0 and d.ha_to_device == 1.0


# ─── full description-field contract (icon/state_key/min/max/step/unit) ─────

def test_limit_time_description_full_contract():
    d = _by_key(GLOBAL_LIMIT_NUMBERS)["limit_time"]
    assert d.icon == "mdi:timer-sand"
    assert d.state_key == "timeLimit"
    assert d.native_min_value == 0
    assert d.native_max_value == 1440
    assert d.native_step == 5
    assert d.native_unit_of_measurement == "min"
    assert d.display_precision == 0
    assert d.mode == NumberMode.BOX


def test_limit_energy_description_full_contract():
    d = _by_key(GLOBAL_LIMIT_NUMBERS)["limit_energy"]
    assert d.icon == "mdi:lightning-bolt"
    assert d.state_key == "energyLimit"
    assert d.native_min_value == 0
    assert d.native_max_value == 100
    assert d.native_step == 1
    assert d.native_unit_of_measurement == "kWh"
    assert d.display_precision == 3
    assert d.mode == NumberMode.BOX


def test_limit_cost_description_full_contract():
    d = _by_key(GLOBAL_LIMIT_NUMBERS)["limit_cost"]
    assert d.icon == "mdi:cash"
    assert d.state_key == "moneyLimit"
    assert d.native_min_value == 0
    assert d.native_max_value == 10000
    assert d.native_step == 1
    assert d.native_unit_of_measurement == "UAH"
    assert d.mode == NumberMode.BOX


def test_charging_current_description_full_contract():
    d = CHARGING_CURRENT_DESCRIPTION
    assert d.key == "charging_current"
    assert d.icon == "mdi:current-ac"
    assert d.native_step == 1.0
    assert d.mode == NumberMode.SLIDER


def test_undervoltage_threshold_description_full_contract():
    d = UNDERVOLTAGE_THRESHOLD_NUMBER
    assert d.key == "undervoltage_threshold"
    assert d.icon == "mdi:flash-alert"
    assert d.native_min_value == 210
    assert d.native_max_value == 220
    assert d.native_step == 1
    assert d.native_unit_of_measurement == "V"
    assert d.mode == NumberMode.SLIDER


def test_valid_min_voltages_is_the_exact_curated_set():
    # Every element matters: only these firmware-supported values may widen the
    # undervoltage-threshold write floor below the safe static minimum.
    assert _VALID_MIN_VOLTAGES == {
        150.0, 155.0, 160.0, 165.0, 170.0, 175.0, 180.0, 200.0
    }
