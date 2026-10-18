"""REVE schedules for the card: all of them, and their days of the week.

REVE keeps up to eight schedules, each with its days of the week; Home
Assistant has entities for the first two only, and none for the days. The card
asks for the schedules over a WebSocket command — nothing is published, so the
recorder writes nothing for it — and changes a schedule's days through the
same full-list ``scheduleV2`` write the time and current entities use.
"""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import custom_components.eveus as eveus
from custom_components.eveus import reve
from custom_components.eveus.const import DOMAIN
from custom_components.eveus.reve import ReveCommandUnsupported, apply_schedule_command
from test_reve_firmware import _Charger, _classic_main, _clock, _reve_main, _updater


def _items() -> list[dict[str, Any]]:
    return [
        {"name": "Night", "start": 1380, "end": 420, "weekdays": list(range(7)),
         "enabled": True, "value": 16, "currentLimitEnabled": True},
        {"name": "", "start": 540, "end": 1080, "weekdays": [1, 2, 3, 4, 5],
         "enabled": False, "value": 10, "currentLimitEnabled": False},
    ]


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


def test_a_schedules_days_change_alone() -> None:
    items = _items()
    updated = apply_schedule_command(items, "sh2Weekdays", [6, 0, 6])
    assert updated[1]["weekdays"] == [0, 6]
    assert {k: v for k, v in updated[1].items() if k != "weekdays"} == {
        k: v for k, v in items[1].items() if k != "weekdays"
    }
    assert updated[0] == items[0]
    assert items[1]["weekdays"] == [1, 2, 3, 4, 5]  # the read list is not touched


@pytest.mark.parametrize("value", [[], [7], [-1], "1,2", [True], 5])
def test_impossible_days_are_refused(value: Any) -> None:
    with pytest.raises(ValueError):
        apply_schedule_command(_items(), "sh1Weekdays", value)


def test_days_of_a_schedule_that_does_not_exist_are_refused() -> None:
    with pytest.raises(ReveCommandUnsupported):
        apply_schedule_command(_items()[:1], "sh2Weekdays", [1])


def test_the_days_go_to_the_charger_as_the_whole_schedule_list(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    assert asyncio.run(updater._command_manager.send_command("sh1Weekdays", [1, 3, 5])) is True

    mutations = [
        body["variables"]["input"] for path, body in charger.calls
        if path == "/graphql" and body["query"].lstrip().startswith("mutation")
    ]
    assert len(mutations) == 1
    written = mutations[0]["scheduleV2"]
    assert written[0]["weekdays"] == [1, 3, 5]
    assert written[1] == charger.schedules[1]
    assert updater.reve_schedules()[0]["weekdays"] == [1, 3, 5]


# ---------------------------------------------------------------------------
# What the card reads
# ---------------------------------------------------------------------------


def test_every_schedule_is_offered_to_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    third = {**copy.deepcopy(charger.schedules[0]), "name": "Weekend", "weekdays": [0, 6]}
    charger.schedules.append(third)
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    now[0] += 42

    card = updater.reve_card_schedules()

    assert card["reve"] is True
    assert card["read_age_seconds"] == 42
    assert [item["name"] for item in card["schedules"]][2] == "Weekend"
    assert card["schedules"][2] == {
        "name": "Weekend", "start": third["start"], "end": third["end"],
        "weekdays": [0, 6], "enabled": third["enabled"],
        "current_limit": third["value"] if third["currentLimitEnabled"] else None,
        "set": True,
    }


@pytest.mark.parametrize(
    ("enabled", "start", "end", "expected"),
    [(False, 0, 0, False), (True, 0, 0, True), (False, 600, 720, True)],
)
def test_a_schedule_counts_as_set_when_on_or_given_a_window(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, start: int, end: int, expected: bool,
) -> None:
    charger = _Charger(_reve_main())
    charger.schedules.append({**copy.deepcopy(charger.schedules[0]), "enabled": enabled, "start": start, "end": end})
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.reve_card_schedules()["schedules"][2]["set"] is expected


def test_every_rate_is_offered_with_whether_it_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    charger.rates = {"base": 4.32, "rates": [
        {"name": "Night", "start": 1380, "end": 420, "weekdays": [0, 1, 2, 3, 4, 5, 6], "enabled": True, "value": 2.16},
        {"name": "Rate 2", "start": 0, "end": 0, "weekdays": [0, 1, 2, 3, 4, 5, 6], "enabled": False, "value": 0},
        {"name": "Peak", "start": 480, "end": 660, "weekdays": [1, 2, 3, 4, 5], "enabled": False, "value": 6.5},
    ]}
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())

    card = updater.reve_card_schedules()

    assert card["base_rate"] == 4.32
    assert [(rate["name"], rate["set"]) for rate in card["rates"]] == [("Night", True), ("Rate 2", False), ("Peak", True)]
    assert card["rates"][0] == {
        "name": "Night", "start": 1380, "end": 420, "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "enabled": True, "price": 2.16, "set": True,
    }


def test_classic_firmware_offers_no_schedules(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.reve_card_schedules() == {"reve": False}
    assert updater.reve_schedules() is None


def test_schedules_no_longer_read_are_not_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.graphql_status = 500
    now[0] += reve.EXTRAS_MAX_AGE_SECONDS + 1
    asyncio.run(updater._async_update_data())
    assert updater.reve_card_schedules()["schedules"] == []
    assert updater.reve_card_schedules()["rates"] == []


# ---------------------------------------------------------------------------
# The WebSocket commands
# ---------------------------------------------------------------------------


def _ws_hass(updaters: dict[str, Any]) -> Any:
    entries = [
        SimpleNamespace(entry_id=entry_id, runtime_data=SimpleNamespace(updater=updater))
        for entry_id, updater in updaters.items()
    ]
    return SimpleNamespace(config_entries=SimpleNamespace(
        async_entries=lambda domain: entries if domain == DOMAIN else []
    ))


def _devices(mapping: dict[str, str]):
    registry = SimpleNamespace(async_get=lambda device_id: (
        SimpleNamespace(config_entries={mapping[device_id]}) if device_id in mapping else None
    ))
    return patch.object(eveus.dr, "async_get", return_value=registry)


def test_the_card_reads_the_schedules_of_its_charger(monkeypatch: pytest.MonkeyPatch) -> None:
    first = SimpleNamespace(reve_card_schedules=lambda: {"reve": False})
    second = SimpleNamespace(reve_card_schedules=lambda: {"reve": True, "schedules": [], "read_age_seconds": 3})
    connection = MagicMock()
    with _devices({"dev2": "e2"}):
        eveus._ws_reve_schedules(
            _ws_hass({"e1": first, "e2": second}), connection,
            {"id": 5, "type": "eveus/reve_schedules", "device_id": "dev2"},
        )
    connection.send_result.assert_called_once_with(5, {"reve": True, "schedules": [], "read_age_seconds": 3})


def test_an_unknown_charger_is_an_error() -> None:
    connection = MagicMock()
    with _devices({}):
        eveus._ws_reve_schedules(_ws_hass({}), connection, {"id": 5, "type": "eveus/reve_schedules"})
    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "not_found"


def _weekdays(updater: Any, **msg: Any) -> MagicMock:
    connection = MagicMock()
    with _devices({}):
        asyncio.run(eveus._async_reve_schedule_weekdays(
            _ws_hass({"e1": updater}), connection,
            {"id": 9, "type": "eveus/reve_schedule_weekdays", "schedule": 2, "weekdays": [5, 1], **msg},
        ))
    return connection


def _fake_updater(*, family: str = "reve", ok: bool = True, failure: str | None = None) -> Any:
    sent: list[tuple[str, Any]] = []

    async def send_command(command: str, value: Any) -> bool:
        sent.append((command, value))
        return ok

    return SimpleNamespace(firmware_family=family, send_command=send_command,
                           last_command_failure=failure, sent=sent)


def test_the_card_sets_a_schedules_days() -> None:
    updater = _fake_updater()
    connection = _weekdays(updater)
    assert updater.sent == [("sh2Weekdays", [1, 5])]
    connection.send_result.assert_called_once_with(9, {"weekdays": [1, 5]})


def test_days_are_refused_on_classic_firmware_without_a_write() -> None:
    updater = _fake_updater(family="classic")
    connection = _weekdays(updater)
    assert updater.sent == []
    assert connection.send_error.call_args[0][1] == "not_supported"


def test_no_day_at_all_is_refused_without_a_write() -> None:
    updater = _fake_updater()
    connection = _weekdays(updater, weekdays=[])
    assert updater.sent == []
    assert connection.send_error.call_args[0][1] == "invalid_format"


def test_a_failed_write_says_why() -> None:
    connection = _weekdays(_fake_updater(ok=False, failure="reve_not_answering"))
    code, message = connection.send_error.call_args[0][1:3]
    assert code == "failed"
    assert "REVE interface is not answering" in message

    connection = _weekdays(_fake_updater(ok=False))
    assert "did not accept" in connection.send_error.call_args[0][2]


def test_the_commands_are_registered_at_setup() -> None:
    hass = MagicMock()
    hass.is_running = False
    with patch.object(eveus.websocket_api, "async_register_command") as register:
        asyncio.run(eveus.async_setup(hass, {}))
    registered = [call.args[1] for call in register.call_args_list]
    assert eveus._ws_reve_schedules in registered
    assert eveus._ws_reve_schedule_weekdays in registered
