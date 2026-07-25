"""SOC-mode constants and helper."""
from types import SimpleNamespace

from custom_components.eveus import const


def test_mode_constants_exist():
    assert const.SOC_MODE_BASIC == "basic"
    assert const.SOC_MODE_ADVANCED == "advanced"
    assert const.SOC_MODE_OPTIONS == [const.SOC_MODE_BASIC, const.SOC_MODE_ADVANCED]


def test_seed_conf_keys_and_defaults():
    assert const.CONF_SOC_MODE == "soc_mode"
    assert const.CONF_INITIAL_SOC == "initial_soc"
    assert const.CONF_TARGET_SOC == "target_soc"
    assert const.CONF_BATTERY_CAPACITY == "battery_capacity"
    assert const.CONF_SOC_CORRECTION == "soc_correction"
    assert const.DEFAULT_INITIAL_SOC == 20
    assert const.DEFAULT_TARGET_SOC == 80
    assert const.DEFAULT_BATTERY_CAPACITY == 50
    assert const.SOC_CORRECTION_MAX == 20
    assert const.SOC_INPUT_LIMITS["battery_capacity"] == (10, 160)
    assert const.SOC_INPUT_LIMITS["soc_correction"] == (0, 20)
    assert const.SOC_INPUT_LIMITS["initial_soc"] == (0, 100)
    assert const.SOC_INPUT_LIMITS["target_soc"] == (0, 100)


def test_dispatcher_signal_is_entry_scoped():
    assert const.soc_update_signal("abc") == "eveus_soc_update_abc"
    assert const.soc_update_signal("abc") != const.soc_update_signal("xyz")


def test_get_soc_mode_reads_entry_data():
    entry = SimpleNamespace(data={const.CONF_SOC_MODE: "basic"})
    assert const.get_soc_mode(entry) == const.SOC_MODE_BASIC
    assert const.get_soc_mode(SimpleNamespace(data={})) == const.SOC_MODE_ADVANCED


def test_domain():
    assert const.DOMAIN == "eveus"


def test_update_interval_constants():
    assert const.CHARGING_UPDATE_INTERVAL == 30
    assert const.IDLE_UPDATE_INTERVAL == 60
    assert const.OFFLINE_UPDATE_INTERVAL == 60
    assert const.RETRY_DELAY == 15
    assert const.UPDATE_TIMEOUT == 20
    assert const.COMMAND_TIMEOUT == 25


def test_device_state_value_constants():
    assert const.DEVICE_STATE_STANDBY == 2
    assert const.DEVICE_STATE_CHARGING == 4
    assert const.DEVICE_STATE_ERROR == 7


def test_legacy_raw_state_key():
    assert const.LEGACY_RAW_STATE_KEY == "_legacy_raw_state"


def test_session_active_and_connected_state_sets():
    assert const.SESSION_ACTIVE_STATES == frozenset({4, 6})
    assert const.CONNECTED_STATES == frozenset({3, 4, 5, 6})
    assert const.PLUG_UNKNOWN_STATES == frozenset({const.DEVICE_STATE_ERROR})
    assert const.PLUG_UNKNOWN_STATES == frozenset({7})


def test_event_name_constants():
    assert const.EVENT_CHARGING_STARTED == "eveus_charging_started"
    assert const.EVENT_CHARGING_FINISHED == "eveus_charging_finished"
    assert const.EVENT_ERROR == "eveus_error"
    assert const.EVENT_CAR_CONNECTED == "eveus_car_connected"
    assert const.EVENT_CAR_DISCONNECTED == "eveus_car_disconnected"


def test_finished_reasons_mapping():
    assert const.FINISHED_REASONS == {
        2: "unplugged",
        3: "stopped",
        5: "complete",
        6: "paused",
    }


def test_availability_and_resilience_constants():
    assert const.AVAILABILITY_GRACE_PERIOD == 60
    assert const.CONTROL_GRACE_PERIOD == 30
    assert const.ERROR_LOG_RATE_LIMIT == 300
    assert const.STATE_CACHE_TTL == 60
    assert const.OPTIMISTIC_CONTROL_TTL == 120


def test_battery_voltage_thresholds():
    assert const.BATTERY_LOW_THRESHOLD_VOLTS == 2.0
    assert const.BATTERY_OK_THRESHOLD_VOLTS == 2.3
    assert const.BATTERY_LOW_DEBOUNCE_POLLS == 3
    assert const.BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS == 5.0


def test_ground_and_unknown_error_debounce_polls():
    assert const.GROUND_TRIGGER_POLLS == 3
    assert const.GROUND_CLEAR_POLLS == 2
    assert const.GROUND_CONTROL_TRIGGER_POLLS == 3
    assert const.GROUND_CONTROL_CLEAR_POLLS == 2
    assert const.UNKNOWN_ERROR_TRIGGER_POLLS == 3


def test_temperature_thresholds():
    assert const.TEMPERATURE_HIGH_C == 80.0
    assert const.TEMPERATURE_RECOVERED_C == 75.0
    assert const.TEMPERATURE_TRIGGER_POLLS == 2
    assert const.TEMPERATURE_RECOVERY_POLLS == 3
    assert const.MIN_VALID_TEMPERATURE_C == -40.0
    assert const.MAX_VALID_TEMPERATURE_C == 150.0


def test_leakage_thresholds():
    assert const.LEAKAGE_HIGH_MA == 30.0
    assert const.LEAKAGE_RECOVERED_MA == 15.0
    assert const.LEAKAGE_TRIGGER_POLLS == 2
    assert const.LEAKAGE_RECOVERY_POLLS == 3
    assert const.MAX_VALID_LEAKAGE_CURRENT_MA == 100_000.0


def test_fault_recovery_polls():
    assert const.FAULT_RECOVERY_POLLS == 2


def test_clock_drift_constants():
    assert const.CLOCK_DRIFT_THRESHOLD_SECONDS == 600
    assert const.CLOCK_DRIFT_TRIGGER_POLLS == 3
    assert const.CLOCK_DRIFT_CLEAR_POLLS == 2
    assert const.CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS == 120
    assert const.CLOCK_DRIFT_TZ_MATCH_TOLERANCE_SECONDS == 300
    assert const.MAX_VALID_SYSTEM_TIME == 4102444800
    assert const.TIME_DRIFT_TOLERANCE_SECONDS == 5


def test_model_and_current_constants():
    assert const.MIN_CURRENT == 7
    assert const.MODEL_16A == "16A"
    assert const.MODEL_32A == "32A"
    assert const.MODEL_40A == "40A"
    assert const.MODEL_48A == "48A"
    assert const.MODELS == ["16A", "32A", "40A", "48A"]
    assert const.MODEL_MAX_CURRENT == {
        "16A": 16,
        "32A": 32,
        "40A": 40,
        "48A": 48,
    }


def test_telemetry_sanity_ceilings():
    assert const.MAX_POWER_W == 100_000
    assert const.MAX_ENERGY_KWH == 1_000_000
    assert const.MAX_SESSION_TIME_SECONDS == 366 * 24 * 3600
    assert const.MAX_COST_VALUE == 100_000_000


def test_config_key_constants():
    assert const.CONF_MODEL == "model"
    assert const.CONF_SCHEME == "scheme"
    assert const.DEFAULT_SCHEME == "http"
    assert const.CONF_PHASES == "phases"


def test_rate_states_mapping():
    assert const.RATE_STATES == {
        0: "Primary Rate",
        1: "Rate 2",
        2: "Rate 3",
    }


def test_charging_states_mapping():
    assert const.CHARGING_STATES == {
        0: "Startup",
        1: "System Test",
        2: "Standby",
        3: "Connected",
        4: "Charging",
        5: "Charge Complete",
        6: "Paused",
        7: "Error",
    }


def test_error_states_mapping():
    assert const.ERROR_STATES == {
        0: "No Error",
        1: "Grounding Error",
        2: "Current Leak High",
        3: "Relay Error",
        4: "Current Leak Low",
        5: "Box Overheat",
        6: "Plug Overheat",
        7: "Pilot Error",
        8: "Low Voltage",
        9: "Diode Error",
        10: "Overcurrent",
        11: "Interface Timeout",
        12: "Software Failure",
        13: "GFCI Test Failure",
        14: "High Voltage",
    }


def test_normal_substates_mapping():
    assert const.NORMAL_SUBSTATES == {
        0: "No Limits",
        1: "Limited by User",
        2: "Energy Limit",
        3: "Time Limit",
        4: "Cost Limit",
        5: "Schedule 1 Limit",
        6: "Schedule 1 Energy Limit",
        7: "Schedule 2 Limit",
        8: "Schedule 2 Energy Limit",
        9: "Waiting for Activation",
        10: "Paused by Adaptive Mode",
    }


def test_state_lookup_helpers_return_mapped_and_default_values():
    assert const.get_charging_state(4) == "Charging"
    assert const.get_charging_state(999) == "Unknown"
    assert const.get_error_state(1) == "Grounding Error"
    assert const.get_error_state(999) == "Unknown Error"
    assert const.get_normal_substate(0) == "No Limits"
    assert const.get_normal_substate(999) == "Unknown State"
