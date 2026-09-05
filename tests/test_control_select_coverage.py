"""Targeted coverage for control-base contract + select edge paths (design Part 1d).

These exercise the error/restore/grace branches that the happy-path select tests
don't reach: command failure (False and raised), the command-pending reconcile
skip, restore-from-state, and the offline grace-window display. They are real
behavior guarantees, not coverage padding.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from conftest import TEST_HOST
from custom_components.eveus import select as select_module
from custom_components.eveus.const import CONTROL_GRACE_PERIOD
from custom_components.eveus.control_base import CommandBackedEntity


class _Updater:
    host = TEST_HOST
    available = True
    last_update_success = True

    def __init__(
        self,
        data: dict[str, object] | None = None,
        *,
        available: bool = True,
        result: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self.data = data or {}
        self.available = available
        self.commands: list[tuple[str, object]] = []
        self._result = result
        self._raises = raises

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(self, command: str, value: object, *, retry: bool = True) -> bool:
        self.commands.append((command, value))
        if self._raises is not None:
            raise self._raises
        return self._result


def _mute(entity: object) -> None:
    entity.async_write_ha_state = lambda: None


# --- control_base.CommandBackedEntity abstract contract ---

def test_command_backed_entity_abstract_methods_raise() -> None:
    inst = CommandBackedEntity.__new__(CommandBackedEntity)
    with pytest.raises(NotImplementedError):
        inst._read_device_value()
    with pytest.raises(NotImplementedError):
        inst._resolve_display_value()
    with pytest.raises(NotImplementedError):
        inst._set_display_value(1)
    with pytest.raises(NotImplementedError):
        inst._get_pending()


def test_command_backed_entity_default_values_equal() -> None:
    inst = CommandBackedEntity.__new__(CommandBackedEntity)
    assert inst._values_equal(5, 5) is True
    assert inst._values_equal(5, 6) is False


# --- integer select (minimum voltage) error/restore/grace paths ---

def test_min_voltage_command_failure_clears_optimistic_and_raises() -> None:
    updater = _Updater({"minVoltage": 200}, result=False)
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("180"))
    assert select._optimistic_value is None


def test_min_voltage_command_exception_propagates_and_clears_optimistic() -> None:
    updater = _Updater({"minVoltage": 200}, raises=RuntimeError("boom"))
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    with pytest.raises(RuntimeError):
        asyncio.run(select.async_select_option("180"))
    assert select._optimistic_value is None


def test_min_voltage_handle_update_skips_reconcile_while_pending() -> None:
    updater = _Updater({"minVoltage": 200})
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    select._command_pending = True
    select._handle_coordinator_update()  # must return early, no crash


def test_min_voltage_device_option_none_when_unavailable() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 200}, available=False))
    assert select._device_option() is None


def test_min_voltage_restore_state_seeds_last_device_value() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.x", "180")))
    assert select._last_device_value == 180


def test_min_voltage_restore_state_ignores_unknown() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.x", "unknown")))
    assert select._last_device_value is None


def test_min_voltage_grace_window_shows_restored_option_while_offline() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    select._last_device_value = 180
    select._last_successful_read = time.time()
    assert select.current_option == "180"


def test_min_voltage_grace_window_expired_returns_none() -> None:
    # `available=True` with the key absent: this isolates the window that
    # measures from the last successful read, which is the one this test is
    # about. With the coordinator OFFLINE the value is held for as long as
    # the entity stays visible instead — see test_grace_holds_last_value.py.
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=True))
    _mute(select)
    select._last_device_value = 180
    select._last_successful_read = time.time() - CONTROL_GRACE_PERIOD - 1
    assert select.current_option is None


# --- timezone select restore-state parse guard ---

def test_timezone_restore_state_ignores_non_integer_option() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    # Not in TIMEZONE_OPTIONS -> the int() branch is skipped, no crash, no seed.
    asyncio.run(select._async_restore_state(State("select.tz", "not-a-zone")))
    assert select._last_device_value is None


def test_timezone_restore_state_seeds_valid_offset() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.tz", "+2")))
    assert select._last_device_value == 2


def test_timezone_command_failure_clears_optimistic_and_raises() -> None:
    updater = _Updater({"timeZone": 2}, result=False)
    select = select_module.EveusTimeZoneSelect(updater)
    _mute(select)
    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("+3"))
    assert select._optimistic_value is None


# --- select entity metadata (ENTITY_NAME/icon/entity_category/options/label) ---


@pytest.mark.parametrize(
    "entity_factory,name,icon,category,options,label",
    [
        (
            lambda u: select_module.EveusTimeZoneSelect(u),
            "Time Zone",
            "mdi:map-clock-outline",
            EntityCategory.CONFIG,
            list(select_module.TIMEZONE_OPTIONS),
            "Select",
        ),
        (
            lambda u: select_module.EveusAdaptiveModeSelect(u),
            "Adaptive Mode",
            "mdi:auto-mode",
            EntityCategory.CONFIG,
            ["Off", "Voltage", "Auto", "Power"],
            "Select",
        ),
        (
            lambda u: select_module.EveusMinVoltageSelect(u),
            "Minimum voltage",
            "mdi:sine-wave",
            EntityCategory.CONFIG,
            select_module.MIN_VOLTAGE_OPTIONS,
            "Select",
        ),
    ],
)
def test_select_entity_metadata_is_exact(
    entity_factory, name, icon, category, options, label
) -> None:
    entity = entity_factory(_Updater({}))

    assert entity.ENTITY_NAME == name
    assert entity.icon == icon
    assert entity.entity_category == category
    assert entity.options == options
    assert entity._control_entity_label == label


def test_min_voltage_read_and_write_keys_are_exact() -> None:
    assert select_module.EveusMinVoltageSelect.READ_KEY == "minVoltage"
    assert select_module.EveusMinVoltageSelect.WRITE_KEY == "minVoltage"


# --- device_number default + initial _command_pending state ---


@pytest.mark.parametrize(
    "cls,unique_id",
    [
        (select_module.EveusTimeZoneSelect, "eveus_time_zone"),
        (select_module.EveusAdaptiveModeSelect, "eveus_adaptive_mode"),
        (select_module.EveusMinVoltageSelect, "eveus_minimum_voltage"),
    ],
)
def test_select_device_number_default_is_one(cls, unique_id: str) -> None:
    entity = cls(_Updater({}))
    assert entity.unique_id == unique_id


@pytest.mark.parametrize(
    "cls",
    [
        select_module.EveusTimeZoneSelect,
        select_module.EveusAdaptiveModeSelect,
        select_module.EveusMinVoltageSelect,
    ],
)
def test_select_command_pending_starts_false(cls) -> None:
    entity = cls(_Updater({}))
    assert entity._command_pending is False


# --- exact grace-window boundary (age == 0 and age == CONTROL_GRACE_PERIOD) ---


def test_timezone_grace_window_boundary_age_zero_is_valid(monkeypatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr("custom_components.eveus.select.time.time", lambda: now)
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    select._last_device_value = 3
    select._last_successful_read = now  # age == 0 exactly

    assert select.current_option == "+3"


def test_timezone_grace_window_boundary_age_equals_grace_period_expires(monkeypatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr("custom_components.eveus.select.time.time", lambda: now)
    # `available=True` with the key absent: this isolates the window that
    # measures from the last successful read, which is the one this test is
    # about. With the coordinator OFFLINE the value is held for as long as
    # the entity stays visible instead — see test_grace_holds_last_value.py.
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=True))
    _mute(select)
    select._last_device_value = 3
    select._last_successful_read = now - CONTROL_GRACE_PERIOD  # age == grace exactly

    assert select.current_option is None


def test_min_voltage_grace_window_boundary_age_zero_is_valid(monkeypatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr("custom_components.eveus.select.time.time", lambda: now)
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    select._last_device_value = 180
    select._last_successful_read = now  # age == 0 exactly

    assert select.current_option == "180"


def test_min_voltage_grace_window_boundary_age_equals_grace_period_expires(monkeypatch) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr("custom_components.eveus.select.time.time", lambda: now)
    # `available=True` with the key absent: this isolates the window that
    # measures from the last successful read, which is the one this test is
    # about. With the coordinator OFFLINE the value is held for as long as
    # the entity stays visible instead — see test_grace_holds_last_value.py.
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=True))
    _mute(select)
    select._last_device_value = 180
    select._last_successful_read = now - CONTROL_GRACE_PERIOD  # age == grace exactly

    assert select.current_option is None


# --- restore-state guard: None state, "unknown"/"unavailable" sentinels ---


@pytest.mark.parametrize(
    "entity_factory",
    [
        lambda u: select_module.EveusTimeZoneSelect(u),
        lambda u: select_module.EveusMinVoltageSelect(u),
    ],
)
def test_restore_state_none_object_is_a_noop(entity_factory) -> None:
    """`state is None or ...` must short-circuit: if the `or` were mutated to
    `and`, accessing `state.state` on a None object would raise instead of
    quietly returning."""
    select = entity_factory(_Updater({}, available=False))
    _mute(select)

    asyncio.run(select._async_restore_state(None))
    assert select._last_device_value is None


def test_timezone_restore_state_ignores_unknown_sentinel() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.tz", "unknown")))
    assert select._last_device_value is None


def test_timezone_restore_state_ignores_unavailable_sentinel() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.tz", "unavailable")))
    assert select._last_device_value is None


def test_min_voltage_restore_state_ignores_unavailable_sentinel() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.mv", "unavailable")))
    assert select._last_device_value is None


def test_timezone_restore_state_stamps_a_real_timestamp() -> None:
    """`_last_successful_read` must become a real (non-None) wall-clock value
    on a valid restore, not just get some truthy placeholder."""
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    before = time.time()
    asyncio.run(select._async_restore_state(State("select.tz", "+2")))
    assert select._last_successful_read is not None
    assert select._last_successful_read >= before


def test_min_voltage_restore_state_stamps_a_real_timestamp() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    before = time.time()
    asyncio.run(select._async_restore_state(State("select.mv", "180")))
    assert select._last_successful_read is not None
    assert select._last_successful_read >= before


# --- exact error messages ---


def test_timezone_select_rejects_unknown_option_exact_message() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({"timeZone": 0}))
    _mute(select)
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(select.async_select_option("+99"))
    assert str(exc_info.value) == "Unsupported time zone: +99"


def test_timezone_select_command_failure_exact_message() -> None:
    updater = _Updater({"timeZone": 0}, result=False)
    select = select_module.EveusTimeZoneSelect(updater)
    _mute(select)
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(select.async_select_option("+3"))
    assert str(exc_info.value) == "Eveus charger did not accept timeZone=+3"


def test_min_voltage_select_rejects_unknown_option_exact_message() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 200}))
    _mute(select)
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(select.async_select_option("999"))
    assert str(exc_info.value) == "Unsupported Minimum voltage: 999"


def test_min_voltage_select_command_failure_exact_message() -> None:
    updater = _Updater({"minVoltage": 200}, result=False)
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(select.async_select_option("180"))
    assert str(exc_info.value) == "Eveus charger did not accept minVoltage=180"


# --- _command_pending is True only while the command is actually in flight ---


def test_timezone_select_command_pending_true_during_send(monkeypatch) -> None:
    updater = _Updater({"timeZone": 0})
    select = select_module.EveusTimeZoneSelect(updater)
    _mute(select)
    observed: list[bool] = []

    async def spy_send_command(command, value, *, retry=True):
        observed.append(select._command_pending)
        return True

    updater.send_command = spy_send_command
    asyncio.run(select.async_select_option("+3"))

    assert observed == [True]
    assert select._command_pending is False


def test_min_voltage_select_command_pending_true_during_send(monkeypatch) -> None:
    updater = _Updater({"minVoltage": 200})
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    observed: list[bool] = []

    async def spy_send_command(command, value, *, retry=True):
        observed.append(select._command_pending)
        return True

    updater.send_command = spy_send_command
    asyncio.run(select.async_select_option("180"))

    assert observed == [True]
    assert select._command_pending is False


# --- _handle_coordinator_update device-value derivation guards ---


def test_timezone_handle_update_value_error_guard_skips_reconcile_not_empty_string(
    monkeypatch,
) -> None:
    """When `_device_option()` returns something non-numeric (defensive path),
    the except-branch must set device_value to None (skipping reconcile), not
    the empty string (which is not-None and would wrongly trigger reconcile)."""
    select = select_module.EveusTimeZoneSelect(_Updater({"timeZone": 0}))
    _mute(select)
    monkeypatch.setattr(select, "_device_option", lambda: "not-an-int")
    calls: list[object] = []
    monkeypatch.setattr(select, "_reconcile_with_device", lambda *a, **k: calls.append(a))

    select._handle_coordinator_update()

    assert calls == []


def test_min_voltage_handle_update_uses_real_device_option_not_forced_none(
    monkeypatch,
) -> None:
    """`device_option = self._device_option()` must actually call through; a
    mutant hard-coding None would make reconcile never fire even when the
    device genuinely reports a value."""
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 180}))
    _mute(select)
    calls: list[tuple] = []
    monkeypatch.setattr(select, "_reconcile_with_device", lambda *a, **k: calls.append(a))

    select._handle_coordinator_update()

    assert len(calls) == 1
    assert calls[0][0] == 180  # device_value derived from OPTION_TO_DEVICE["180"]


def test_min_voltage_handle_update_skips_reconcile_when_device_option_is_none(
    monkeypatch,
) -> None:
    """The `if device_option is not None` guard must gate reconcile - flipping
    it to `is None` would call reconcile with an unmapped/garbage option."""
    select = select_module.EveusMinVoltageSelect(_Updater({}))  # no minVoltage -> None
    _mute(select)
    calls: list[tuple] = []
    monkeypatch.setattr(select, "_reconcile_with_device", lambda *a, **k: calls.append(a))

    select._handle_coordinator_update()

    assert calls == []


def test_min_voltage_handle_update_confirm_fn_matches_equal_values(monkeypatch) -> None:
    """The confirm_fn passed to _reconcile_with_device must be a real equality
    check: it needs to return True when optimistic == device so a confirmed
    match clears the optimistic value immediately (not just after TTL)."""
    updater = _Updater({"minVoltage": 180})
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    select._set_optimistic_value(180)

    select._handle_coordinator_update()

    # Device already reports 180 == our optimistic value -> confirmed match,
    # optimistic bookkeeping clears immediately regardless of TTL.
    assert select._optimistic_value is None
    assert select._last_device_value == 180


# --- async_setup_entry ---


def test_select_setup_entry_wires_runtime_data_without_model(monkeypatch) -> None:
    from types import SimpleNamespace

    updater = _Updater({"timeZone": 0, "aiStatus": 0})
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(updater=updater, device_number=2),
        data={},
    )
    added: list[object] = []

    asyncio.run(
        select_module.async_setup_entry(
            object(), entry, lambda entities: added.extend(entities)
        )
    )

    assert {entity.ENTITY_NAME for entity in added} == {"Time Zone", "Adaptive Mode"}
    for entity in added:
        assert entity._updater is updater
        assert entity.unique_id.startswith("eveus2_")


def test_select_setup_entry_adds_min_voltage_when_model_configured() -> None:
    from types import SimpleNamespace

    updater = _Updater({"timeZone": 0, "aiStatus": 0, "minVoltage": 200})
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(updater=updater, device_number=1),
        data={"model": "32A"},
    )
    added: list[object] = []

    asyncio.run(
        select_module.async_setup_entry(
            object(), entry, lambda entities: added.extend(entities)
        )
    )

    assert {entity.ENTITY_NAME for entity in added} == {
        "Time Zone",
        "Adaptive Mode",
        "Minimum voltage",
    }
