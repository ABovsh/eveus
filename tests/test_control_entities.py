"""Unit tests for Eveus switch and number control behavior."""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest
from homeassistant.core import State

from conftest import EveusTestUpdater as _Updater
from conftest import disable_state_writes as _disable_state_writes
from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.number import async_setup_entry as async_setup_number_entry
from custom_components.eveus.button import (
    EveusResetCounterAButton,
    EveusResetCounterBButton,
)
from custom_components.eveus.switch import (
    BaseSwitchEntity,
    EveusSocLimitSwitch,
    SWITCH_DESCRIPTIONS,
    async_setup_entry as async_setup_switch_entry,
)
from custom_components.eveus.time import EveusScheduleTimeEntity, TIME_DESCRIPTIONS


def _one_charge_switch(updater: _Updater) -> BaseSwitchEntity:
    return BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[1])


def _stop_charging_switch(updater: _Updater) -> BaseSwitchEntity:
    return BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0])


def _schedule_start_time(updater: _Updater) -> EveusScheduleTimeEntity:
    return EveusScheduleTimeEntity(updater, TIME_DESCRIPTIONS[0])


def test_current_number_native_value_precedence_and_restore() -> None:
    updater = _Updater({"currentSet": "16"})
    entity = EveusCurrentNumber(updater, "32A")

    assert entity.native_value == 16

    entity._pending_value = 20
    assert entity.native_value == 16
    assert entity._resolve_value() == 16

    entity._pending_value = None
    entity._optimistic_value = 24
    entity._optimistic_value_time = time.time()
    assert entity.native_value == 16
    assert entity._resolve_value() == 24

    entity._optimistic_value_time = 0
    updater.data = {}
    entity._last_device_value = 18
    entity._last_successful_read = time.time()
    assert entity.native_value == 16
    assert entity._resolve_value() == 18

    asyncio.run(entity._async_restore_state(State("number.current", "19")))
    assert entity._last_device_value == 19


def test_current_number_set_value_clamps_and_records_command() -> None:
    updater = _Updater({"currentSet": "16"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)

    asyncio.run(entity.async_set_native_value(99))

    assert updater.commands == [("currentSet", 16)]
    assert entity._optimistic_value == 16


def test_current_number_update_reconciles_optimistic_value() -> None:
    updater = _Updater({"currentSet": "12"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)
    entity._optimistic_value = 12
    entity._optimistic_value_time = time.time()

    entity._handle_coordinator_update()

    assert entity._optimistic_value is None
    assert entity._last_device_value == 12


def test_command_backed_controls_preserve_optimistic_lifecycle_parity() -> None:
    cases = [
        (
            EveusCurrentNumber(_Updater({"currentSet": "10"}), "16A"),
            "currentSet",
            "14",
            14.0,
            10.0,
            14.0,
            lambda entity: entity.native_value,
            14.0,
        ),
        (
            _one_charge_switch(_Updater({"oneCharge": "0"})),
            "oneCharge",
            "1",
            True,
            False,
            True,
            lambda entity: entity.is_on,
            True,
        ),
        (
            _schedule_start_time(_Updater({"sh1Start": "60"})),
            "sh1Start",
            "390",
            390,
            60,
            390,
            lambda entity: entity.native_value,
            dt.time(6, 30),
        ),
    ]

    for (
        entity,
        state_key,
        confirmed_payload,
        optimistic_value,
        stale_device_value,
        confirmed_device_value,
        visible_value,
        confirmed_visible,
    ) in cases:
        _disable_state_writes(entity)
        entity._set_optimistic_value(optimistic_value)

        entity._handle_coordinator_update()

        assert entity._optimistic_value == optimistic_value
        assert entity._last_device_value == stale_device_value
        assert visible_value(entity) == confirmed_visible

        entity._updater.data = {state_key: confirmed_payload}
        entity._handle_coordinator_update()

        assert entity._optimistic_value is None
        assert entity._last_device_value == confirmed_device_value
        assert visible_value(entity) == confirmed_visible

        entity._set_optimistic_value(optimistic_value)
        entity._optimistic_value_time = time.time() - 3600
        entity._updater.data = {state_key: confirmed_payload}
        entity._handle_coordinator_update()

        assert entity._optimistic_value is None
        assert visible_value(entity) == confirmed_visible

        entity._updater.data = {}
        entity._handle_coordinator_update()

        assert entity._last_device_value == confirmed_device_value
        assert visible_value(entity) == confirmed_visible


def test_current_number_update_clears_stale_mismatched_optimistic_value() -> None:
    updater = _Updater({"currentSet": "10"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)
    entity._optimistic_value = 14
    entity._optimistic_value_time = 0

    entity._handle_coordinator_update()

    assert entity._optimistic_value is None
    assert entity._last_device_value == 10


@pytest.mark.parametrize("payload", [{}, {"currentSet": "99"}, {"currentSet": "bad"}])
def test_current_number_update_ignores_missing_or_invalid_device_values(
    payload: dict[str, object],
) -> None:
    updater = _Updater(payload)
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)

    entity._handle_coordinator_update()

    assert entity._last_device_value is None
    assert entity.native_value is None


def test_current_number_ignores_stale_coordinator_update_while_command_pending() -> None:
    updater = _Updater({"currentSet": "10"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)
    entity._pending_value = 14.0
    entity._attr_native_value = 14.0

    entity._handle_coordinator_update()

    assert entity.native_value == pytest.approx(14.0)
    assert entity._last_device_value is None


def test_current_number_handles_failed_command() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"currentSet": "16"})
    updater.command_result = False
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_native_value(12))

    assert updater.commands == [("currentSet", 12)]
    assert entity._optimistic_value is None


def test_current_number_wraps_unexpected_command_exception() -> None:
    from homeassistant.exceptions import HomeAssistantError

    class BrokenUpdater(_Updater):
        async def send_command(self, command: str, value: object, *, retry: bool = True) -> bool:
            raise RuntimeError("network disappeared")

    entity = EveusCurrentNumber(BrokenUpdater({"currentSet": "16"}), "16A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="Failed to set charging current"):
        asyncio.run(entity.async_set_native_value(12))

    assert entity._pending_value is None
    assert entity._optimistic_value is None


def test_current_number_restore_ignores_invalid_or_out_of_range_values() -> None:
    entity = EveusCurrentNumber(_Updater({"currentSet": "16"}), "16A")

    asyncio.run(entity._async_restore_state(State("number.current", "bad")))
    assert entity._last_device_value is None

    asyncio.run(entity._async_restore_state(State("number.current", "99")))
    assert entity._last_device_value is None


def test_current_number_returns_none_for_stale_device_value() -> None:
    entity = EveusCurrentNumber(_Updater({}), "16A")
    entity._last_device_value = 12
    entity._last_successful_read = 0

    assert entity.native_value is None


def test_switch_state_precedence_restore_and_commands() -> None:
    updater = _Updater({"oneCharge": "0"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)

    # Cached is_on starts unknown (None) until a resolve/coordinator update.
    assert entity.is_on is None

    entity._pending_command = True
    assert entity.is_on is None
    assert entity._resolve_state() is False

    entity._pending_command = None
    entity._optimistic_state = True
    entity._optimistic_state_time = time.time()
    assert entity.is_on is None
    assert entity._resolve_state() is True

    entity._optimistic_state_time = 0
    updater.data = {"oneCharge": "1"}
    assert entity.is_on is None
    assert entity._resolve_state() is True

    asyncio.run(entity._async_restore_state(State("switch.one", "off")))
    assert entity._last_device_state is False

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())
    assert updater.commands[-2:] == [("oneCharge", 1), ("oneCharge", 0)]


def test_switch_update_reconciles_optimistic_state() -> None:
    updater = _Updater({"oneCharge": "1"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)
    entity._optimistic_state = True
    entity._optimistic_state_time = time.time()

    entity._handle_coordinator_update()

    assert entity._optimistic_state is None
    assert entity._last_device_state is True


def test_switch_update_clears_stale_mismatched_optimistic_state() -> None:
    updater = _Updater({"oneCharge": "0"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)
    entity._optimistic_state = True
    entity._optimistic_state_time = 0

    entity._handle_coordinator_update()

    assert entity._optimistic_state is None
    assert entity._last_device_state is False


def test_switch_ignores_stale_coordinator_update_while_command_pending() -> None:
    updater = _Updater({"oneCharge": "0"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)
    entity._pending_command = True
    entity._attr_is_on = True

    entity._handle_coordinator_update()

    assert entity.is_on is True
    assert entity._last_device_state is None


def test_switch_failed_command_does_not_set_optimistic_state() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"oneCharge": "0"})
    updater.command_result = False
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_turn_on())

    assert updater.commands == [("oneCharge", 1)]
    assert entity._optimistic_state is None


def test_stop_charging_switch_preserves_existing_semantics() -> None:
    updater = _Updater({"evseEnabled": "0"})
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())

    assert updater.commands == [("evseEnabled", 1), ("evseEnabled", 0)]


def test_reset_counter_buttons_emit_reset_commands() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"IEM1": "5.5", "IEM2": "2.2"})
    button_a = EveusResetCounterAButton(updater)
    button_b = EveusResetCounterBButton(updater)
    _disable_state_writes(button_a)
    _disable_state_writes(button_b)

    asyncio.run(button_a.async_press())
    asyncio.run(button_b.async_press())
    assert updater.commands == [("rstEM1", 0), ("rstEM2", 0)]
    assert updater.last_retry is False

    updater.command_result = False
    with pytest.raises(HomeAssistantError):
        asyncio.run(button_a.async_press())
    with pytest.raises(HomeAssistantError):
        asyncio.run(button_b.async_press())


def test_stop_charging_switch_raises_on_command_failure() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"evseEnabled": "0"})
    updater.command_result = False
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_turn_on())
    assert updater.commands == [("evseEnabled", 1)]


def test_current_number_raises_on_command_failure() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"currentSet": "16"})
    updater.command_result = False
    entity = EveusCurrentNumber(updater, "32A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_set_native_value(20))
    assert updater.commands == [("currentSet", 20)]


def test_switch_optimistic_state_survives_until_device_confirms() -> None:
    """Toggle ON, ensure optimistic ON survives a coordinator read that
    still shows OFF (charger hasn't committed yet)."""
    updater = _Updater({"evseEnabled": "0"})
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    assert entity.is_on is True
    assert entity._optimistic_state is True

    # Coordinator returns stale OFF — optimistic must hold ON within TTL window.
    entity._handle_coordinator_update()
    assert entity.is_on is True

    # Device finally confirms ON — optimistic clears, state stays ON.
    updater.data = {"evseEnabled": "1"}
    entity._handle_coordinator_update()
    assert entity._optimistic_state is None
    assert entity.is_on is True


def test_switch_rapid_toggle_does_not_flicker_back() -> None:
    """ON, then OFF 2s later — a stale ON read must not flip the entity."""
    updater = _Updater({"evseEnabled": "0"})
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())
    assert entity.is_on is False
    assert entity._optimistic_state is False

    # Stale read still shows ON — optimistic OFF wins inside TTL.
    updater.data = {"evseEnabled": "1"}
    entity._handle_coordinator_update()
    assert entity.is_on is False

    # Device commits OFF — optimistic clears.
    updater.data = {"evseEnabled": "0"}
    entity._handle_coordinator_update()
    assert entity.is_on is False
    assert entity._optimistic_state is None


def test_switch_test_alias_properties_round_trip() -> None:
    entity = _one_charge_switch(_Updater({}))

    entity._optimistic_state_time = 123.0
    entity._last_device_state = True

    assert entity._optimistic_state_time == 123.0
    assert entity._last_device_state is True


def test_switch_resolves_recent_restored_state_when_payload_missing() -> None:
    entity = _one_charge_switch(_Updater({}))
    entity._last_device_state = True
    entity._last_successful_read = time.time()

    assert entity._resolve_state() is True


def test_switch_added_to_hass_resolves_initial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _Updater({"oneCharge": "1"})
    entity = _one_charge_switch(updater)

    async def noop_added_to_hass(self):
        return None

    monkeypatch.setattr(
        "custom_components.eveus.common_base.BaseEveusEntity.async_added_to_hass",
        noop_added_to_hass,
    )

    asyncio.run(entity.async_added_to_hass())

    assert entity.is_on is True


def test_switch_restore_ignores_invalid_state() -> None:
    entity = _one_charge_switch(_Updater({}))

    asyncio.run(entity._async_restore_state(State("switch.one", "unknown")))

    assert entity._last_device_state is None
    assert entity.is_on is None


def test_switch_setup_entry_adds_all_switches() -> None:
    added = []
    entry = type(
        "Entry",
        (),
        {
            "data": {"soc_mode": "advanced"},
            "runtime_data": type(
                "RuntimeData",
                (),
                {"updater": _Updater({}), "device_number": 3, "soc_limit": object()},
            )()
        },
    )()

    asyncio.run(async_setup_switch_entry(None, entry, lambda entities: added.extend(entities)))

    assert [entity.entity_description.key for entity in added[:-1]] == [
        description.key for description in SWITCH_DESCRIPTIONS
    ]
    assert isinstance(added[-1], EveusSocLimitSwitch)
    assert all(entity.unique_id.startswith("eveus3_") for entity in added)


def test_number_setup_entry_keeps_model_independent_entity_when_model_is_missing() -> None:
    added = []
    entry = type(
        "Entry",
        (),
        {
            "data": {"soc_mode": "basic"},
            "runtime_data": type(
                "RuntimeData",
                (),
                {"updater": _Updater({"currentSet": "16"}), "device_number": 1},
            )(),
        },
    )()

    asyncio.run(async_setup_number_entry(None, entry, lambda entities: added.extend(entities)))

    assert [entity.name for entity in added] == ["Undervoltage threshold"]


def test_switch_missing_key_resolves_unknown() -> None:
    from custom_components.eveus import switch as switch_mod
    description = switch_mod.SWITCH_DESCRIPTIONS[1]  # One Charge / oneCharge
    sw = switch_mod.BaseSwitchEntity(_Updater({}), description, 1)
    assert sw._resolve_state() is None
    assert sw.is_on is None


def test_switch_valid_key_resolves_bool() -> None:
    from custom_components.eveus import switch as switch_mod
    description = switch_mod.SWITCH_DESCRIPTIONS[1]
    sw = switch_mod.BaseSwitchEntity(_Updater({"oneCharge": 1}), description, 1)
    assert sw._resolve_state() is True


def test_switch_restore_seeds_successful_read() -> None:
    import asyncio, time
    from homeassistant.core import State
    from custom_components.eveus import switch as switch_mod
    description = switch_mod.SWITCH_DESCRIPTIONS[1]
    sw = switch_mod.BaseSwitchEntity(_Updater({}), description, 1)
    before = time.time()
    asyncio.run(sw._async_restore_state(State("switch.x", "on")))
    assert sw._last_successful_read >= before
    assert sw._last_device_value is True


def test_number_restore_seeds_successful_read() -> None:
    import asyncio, time
    from homeassistant.core import State
    num = EveusCurrentNumber(_Updater({}), "16A", 1)
    before = time.time()
    asyncio.run(num._async_restore_state(State("number.x", "12")))
    assert num._last_successful_read >= before
    assert num._last_device_value == 12.0


def test_command_after_shutdown_skips_refresh_scheduling(monkeypatch) -> None:
    import asyncio
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    async def ok(*args, **kwargs) -> bool:
        return True

    updater._command_manager.send_command = ok
    scheduled: list[int] = []
    monkeypatch.setattr(
        updater, "_schedule_post_command_refresh", lambda: scheduled.append(1)
    )

    # Once shutdown has begun, a new/queued command is REJECTED outright (V-01)
    # so it cannot POST to the charger after teardown — and therefore schedules
    # no refresh either.
    updater._shutting_down = True
    assert asyncio.run(updater.send_command("evseEnabled", 1)) is False
    assert scheduled == []

    # Sanity: while live, a successful command still schedules refreshes.
    updater._shutting_down = False
    asyncio.run(updater.send_command("evseEnabled", 1))
    assert scheduled == [1]


def test_async_shutdown_sets_shutting_down_flag() -> None:
    import asyncio
    from custom_components.eveus.common_network import EveusUpdater
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater._shutting_down is False
    asyncio.run(updater.async_shutdown())
    assert updater._shutting_down is True


def test_control_entities_have_command_lock() -> None:
    import asyncio
    from custom_components.eveus.switch import BaseSwitchEntity, SWITCH_DESCRIPTIONS
    from custom_components.eveus.number import EveusCurrentNumber
    from custom_components.eveus.time import EveusScheduleTimeEntity, TIME_DESCRIPTIONS
    from custom_components.eveus.select import EveusTimeZoneSelect
    from custom_components.eveus.const import MODEL_16A

    updater = _Updater(data={})
    entities = [
        BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0]),
        EveusCurrentNumber(updater, MODEL_16A),
        EveusScheduleTimeEntity(updater, TIME_DESCRIPTIONS[0]),
        EveusTimeZoneSelect(updater),
    ]
    for entity in entities:
        assert isinstance(entity._command_lock, asyncio.Lock)


def test_current_number_serializes_concurrent_commands() -> None:
    import asyncio
    from custom_components.eveus.const import MODEL_16A

    updater = _Updater(data={"currentSet": 10})
    number = EveusCurrentNumber(updater, MODEL_16A)
    _disable_state_writes(number)

    timeline: list[tuple[str, object]] = []

    async def instrumented(command, value, *, retry=True, extra=None) -> bool:
        timeline.append(("start", value))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        timeline.append(("end", value))
        return True

    updater.send_command = instrumented

    async def run() -> None:
        await asyncio.gather(
            number.async_set_native_value(8),
            number.async_set_native_value(15),
        )

    asyncio.run(run())

    # The per-entity lock fully serializes both commands: no command may start
    # before the previous one has ended (depth never exceeds 1).
    depth = 0
    for kind, _value in timeline:
        depth += 1 if kind == "start" else -1
        assert depth <= 1
    assert len(timeline) == 4


def test_control_pushes_availability_change_while_command_pending() -> None:
    import time
    from custom_components.eveus.const import CONTROL_GRACE_PERIOD, MODEL_16A

    updater = _Updater(data={"currentSet": 16})
    number = EveusCurrentNumber(updater, MODEL_16A)
    writes: list[int] = []
    number.async_write_ha_state = lambda: writes.append(1)
    number._last_written_available = True  # previously shown as available

    number._pending_value = 10.0  # a command is in flight
    updater.available = False  # charger drops offline, past the grace period
    number._unavailable_since = time.time() - (CONTROL_GRACE_PERIOD + 5)

    number._handle_coordinator_update()

    assert number.available is False
    assert writes, "availability transition must be pushed even while a command is pending"


def test_control_keeps_value_steady_while_pending_when_available() -> None:
    # While a command is pending and availability has NOT changed, the control
    # must not reconcile/flip its displayed value off the pending value.
    from custom_components.eveus.const import MODEL_16A

    updater = _Updater(data={"currentSet": 16})
    number = EveusCurrentNumber(updater, MODEL_16A)
    _disable_state_writes(number)
    number._set_optimistic_value(10.0)
    number._attr_native_value = 10.0
    number._last_written_available = True
    number._pending_value = 10.0

    number._handle_coordinator_update()

    assert number.native_value == 10.0  # not reverted to device's 16


class _PollResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def __aenter__(self) -> "_PollResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, **kwargs: object) -> dict:
        return self.payload

    @property
    def content_length(self):
        import json as _json
        body = self.payload if isinstance(self.payload, str) else _json.dumps(self.payload)
        return len(body.encode())

    @property
    def content(self):
        import json as _json
        body = self.payload if isinstance(self.payload, str) else _json.dumps(self.payload)
        return _CappedStreamReader(body.encode())


class _PollSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def post(self, url: str, **kwargs: object) -> "_PollResponse":
        self.calls.append(url)
        return _PollResponse(self.payload)


class _PollHass:
    loop = None


class _CappedStreamReader:
    """Minimal aiohttp StreamReader stand-in for read_json_capped."""

    def __init__(self, raw):
        self._raw = raw

    async def iter_chunked(self, size):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


def test_force_refresh_bypass_survives_interleaved_poll(monkeypatch) -> None:
    import asyncio, time
    from conftest import TEST_PASSWORD, TEST_USERNAME, TEST_HOST
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater

    session = _PollSession({"state": 2, "currentSet": 16})
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _PollHass())
    updater._next_poll_attempt = time.time() + 9999  # deep in offline backoff

    updater._force_refresh_requests = 1  # a force refresh window is open
    asyncio.run(updater._async_update_data())  # an interleaved scheduled poll
    asyncio.run(updater._async_update_data())  # the force refresh's own poll
    updater._force_refresh_requests = 0

    # Both polls bypassed backoff — the interleaved poll did not consume it.
    assert len(session.calls) == 2


def test_set_current_propagates_auth_failure() -> None:
    import asyncio
    from homeassistant.exceptions import ConfigEntryAuthFailed

    class _AuthFailUpdater(_Updater):
        async def send_command(self, command, value, *, retry=True, extra=None):
            raise ConfigEntryAuthFailed("Eveus charger rejected credentials")

    entity = EveusCurrentNumber(_AuthFailUpdater({"currentSet": 10}), "16A")
    _disable_state_writes(entity)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(entity.async_set_native_value(12))


def test_control_mixin_availability_accepts_recheck_kwargs() -> None:
    from custom_components.eveus.common_base import BaseEveusEntity, ControlEntityMixin

    class _Control(ControlEntityMixin, BaseEveusEntity):
        ENTITY_NAME = "Probe Control"

    entity = _Control(_Updater({}), 1)
    entity._updater._available = False
    # The scheduled grace re-check calls polymorphically with the base kwargs;
    # the mixin override must accept them instead of raising TypeError.
    result = entity._update_availability_state(
        grace_period=30, label="Entity", clear_optimistic_state=True
    )
    assert isinstance(result, bool)


def test_cancel_pending_refreshes_skips_current_task() -> None:
    import asyncio
    from custom_components.eveus.common_network import EveusUpdater

    updater = EveusUpdater.__new__(EveusUpdater)
    updater._pending_refresh_unsubs = []

    async def _scenario() -> bool:
        cancelled_self = False

        async def _tracked() -> None:
            nonlocal cancelled_self
            try:
                # Simulate the refresh observing a transition and rescheduling
                # the burst from inside its own tracked task.
                updater._cancel_pending_refreshes()
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                cancelled_self = True
                raise

        task = asyncio.ensure_future(_tracked())
        updater._post_command_refresh_tasks = [task]
        try:
            await task
        except asyncio.CancelledError:
            pass
        return cancelled_self

    assert asyncio.run(_scenario()) is False


def test_v18_command_manager_resolves_callable_value_at_post_time():
    import asyncio, aiohttp
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_command import CommandManager

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def raise_for_status(self):
            return None

    class _Sess:
        def __init__(self):
            self.calls = []

        def post(self, url, **kw):
            self.calls.append(kw)
            return _Resp()

    class _Upd:
        host = TEST_HOST

        def __init__(self, sess):
            self._sess = sess
            self._basic_auth = aiohttp.BasicAuth(TEST_USERNAME, TEST_PASSWORD)

        @property
        def basic_auth(self):
            return self._basic_auth

        def get_session(self):
            return self._sess

        def url_for(self, path):
            return f"http://{self.host}{path}"

    sess = _Sess()
    mgr = CommandManager(_Upd(sess))
    calls_seen = []

    def _value():
        calls_seen.append(1)
        return 99999

    ok = asyncio.run(mgr.send_command("systemTime", _value))
    assert ok is True
    assert calls_seen == [1]  # evaluated once, inside the command path
    assert sess.calls[0]["data"] == "pageevent=systemTime&systemTime=99999"


def test_v15_pending_token_does_not_cross_hidden_session_reset():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.soc_limit import SocLimitController

    def _calc(target=80, initial=80, cap=50, corr=0):
        c = CachedSOCCalculator()
        c.set_value("initial_soc", initial)
        c.set_value("battery_capacity", cap)
        c.set_value("soc_correction", corr)
        c.set_value("target_soc", target)
        return c

    def _soc_updater(**data):
        u = MagicMock()
        u.available = True
        u.last_update_success = True
        u.device_number = 1
        base = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
        base.update(data)
        u.data = base
        u.send_command = AsyncMock(return_value=True)
        return u

    def _soc_ctrl(calc, updater):
        hass = MagicMock()
        hass.async_create_task = lambda coro: asyncio.run(coro)
        hass.bus.async_fire = MagicMock()
        return SocLimitController(hass, updater, calc)

    # Stop issued early (sessionEnergy 0.4, sessionTime 500). A new session whose
    # energy dropped by less than the old 0.5 kWh epsilon (0.4 -> 0.1) but whose
    # sessionTime reset (500 -> 10) must discard the stale token, not confirm it.
    calc = _calc(target=80, initial=80, cap=50, corr=0)
    updater = _soc_updater(sessionEnergy=0.4, sessionTime=500, evseEnabled=0)
    ctrl = _soc_ctrl(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()  # issues Stop; charger stays evseEnabled=0 -> pending held
    assert ctrl._pending is not None
    # New session: tiny energy, RESET sessionTime, charger reports stopped.
    updater.data = {
        "state": 4, "sessionEnergy": 0.1, "sessionTime": 10,
        "evseEnabled": 1, "suspendLimits": 0,
    }
    ctrl.process()
    updater.send_command.reset_mock()
    assert ctrl._hass.bus.async_fire.call_count == 0


def test_car_connected_unknown_state_returns_none() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"state": 99})
    sensor = bs.EveusCarConnectedBinarySensor(updater, 1)
    assert sensor.is_on is None


def test_car_connected_charging_state_returns_true() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"state": 4})
    sensor = bs.EveusCarConnectedBinarySensor(updater, 1)
    assert sensor.is_on is True


@pytest.mark.parametrize(
    "state,expected",
    [(0, False), (2, False), (3, False), (4, True), (5, False), (6, True), (99, None)],
)
def test_session_active_mapping(state: int, expected) -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"state": state})
    sensor = bs.EveusSessionActiveBinarySensor(updater, 1)
    assert sensor.is_on is expected


def test_session_active_coordinator_update_writes_only_on_change() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"state": 4})
    sensor = bs.EveusSessionActiveBinarySensor(updater, 1)
    sensor._entity_available = True
    sensor.hass = object()
    writes: list = []
    sensor.async_write_ha_state = lambda: writes.append(sensor.is_on)
    sensor._maybe_finalize_device_info = lambda: None
    sensor._update_availability_state = lambda: False

    sensor._handle_coordinator_update()
    sensor._handle_coordinator_update()
    updater.data = {"state": 2}
    sensor._handle_coordinator_update()

    assert writes == [True, False]


def test_session_active_returns_unknown_when_unavailable() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"state": 4}, available=False)
    sensor = bs.EveusSessionActiveBinarySensor(updater, 1)
    sensor._entity_available = False

    assert sensor.is_on is None


def test_ocpp_connected_coordinator_update_writes_only_on_change() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"ocppconnected": 1})
    sensor = bs.EveusOcppConnectedBinarySensor(updater, 1)
    sensor._entity_available = True
    sensor.hass = object()
    writes: list = []
    sensor.async_write_ha_state = lambda: writes.append(sensor.is_on)
    sensor._maybe_finalize_device_info = lambda: None
    sensor._update_availability_state = lambda: False

    sensor._handle_coordinator_update()
    updater.data = {"ocppconnected": 0}
    sensor._handle_coordinator_update()
    sensor._handle_coordinator_update()

    assert writes == [True, False]


def test_ocpp_connected_rejects_out_of_domain_value() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    updater = EveusTestUpdater({"ocppconnected": 2})
    sensor = bs.EveusOcppConnectedBinarySensor(updater, 1)

    assert sensor.is_on is None


def test_binary_sensor_setup_entry_adds_all_status_entities() -> None:
    import asyncio
    from types import SimpleNamespace
    from conftest import EveusTestUpdater
    from custom_components.eveus import binary_sensor as bs

    added: list = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            updater=EveusTestUpdater({}),
            device_number=2,
        )
    )

    asyncio.run(bs.async_setup_entry(None, entry, lambda entities: added.extend(entities)))

    assert [type(entity) for entity in added] == [
        bs.EveusCarConnectedBinarySensor,
        bs.EveusSessionActiveBinarySensor,
        bs.EveusOcppConnectedBinarySensor,
    ]
    assert {entity.unique_id for entity in added} == {
        "eveus2_car_connected",
        "eveus2_session_active",
        "eveus2_ocpp_connected",
    }


def test_car_connected_error_state_is_unknown() -> None:
    from custom_components.eveus.binary_sensor import (
        _CONNECTED_STATES,
        _PLUG_UNKNOWN_STATES,
    )
    assert 7 in _PLUG_UNKNOWN_STATES
    assert 7 not in _CONNECTED_STATES


def test_timezone_select_suppresses_reconcile_while_pending() -> None:
    import time as _t
    from conftest import EveusTestUpdater, disable_state_writes
    from custom_components.eveus.select import EveusTimeZoneSelect

    updater = EveusTestUpdater(data={"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    disable_state_writes(select)

    # Optimistic +3, stamped longer ago than the 10s mismatch TTL.
    select._set_optimistic_value(3)
    select._optimistic_value_time = _t.time() - 11

    # While the command is in flight, a poll returning the old zone must NOT
    # expire the optimistic value.
    select._command_pending = True
    select._handle_coordinator_update()
    assert select.current_option == "+3"

    # Once the command settles, the normal reconcile path applies again.
    select._command_pending = False
    select._handle_coordinator_update()
    assert select.current_option == "0"


@pytest.mark.asyncio
async def test_command_401_raises_auth_failed_no_retry():
    import aiohttp
    from unittest.mock import AsyncMock, MagicMock, patch
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from custom_components.eveus.common_command import CommandManager

    updater = MagicMock()
    mgr = CommandManager(updater)
    response_err = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=401,
        message="Unauthorized",
    )

    with patch.object(
        mgr,
        "_post_command",
        AsyncMock(side_effect=response_err),
    ) as post:
        with pytest.raises(ConfigEntryAuthFailed):
            await mgr.send_command("evseEnabled", 1)

    assert post.call_count == 1  # no retry on 401


def test_timezone_select_ignores_device_value_when_offline() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.select import EveusTimeZoneSelect

    select = EveusTimeZoneSelect(EveusTestUpdater(data={"timeZone": 3}, available=False))
    assert select._device_option() is None


def test_timezone_select_uses_device_value_when_online() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.select import EveusTimeZoneSelect

    select = EveusTimeZoneSelect(EveusTestUpdater(data={"timeZone": 3}, available=True))
    assert select._device_option() == "+3"


def test_timezone_select_restores_last_option_within_grace() -> None:
    import asyncio
    from types import SimpleNamespace
    from conftest import EveusTestUpdater, disable_state_writes
    from custom_components.eveus.select import EveusTimeZoneSelect

    select = EveusTimeZoneSelect(EveusTestUpdater(data={}, available=False))
    disable_state_writes(select)
    asyncio.run(select._async_restore_state(SimpleNamespace(state="+3")))
    assert select.current_option == "+3"
