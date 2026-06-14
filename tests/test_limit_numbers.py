from custom_components.eveus.number import GLOBAL_LIMIT_NUMBERS, UNDERVOLTAGE_NUMBER


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


def test_undervoltage_targets_minvoltage():
    assert UNDERVOLTAGE_NUMBER.command == "minVoltage"
    assert UNDERVOLTAGE_NUMBER.native_min_value == 180
    assert UNDERVOLTAGE_NUMBER.native_max_value == 245
