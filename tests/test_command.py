"""Unit tests for Eveus command helpers."""
from __future__ import annotations

import asyncio

from custom_components.eveus.common_command import CommandManager, send_eveus_command


class _Response:
    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.raise_error:
            raise RuntimeError("boom")


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _Updater:
    host = "192.168.1.50"
    username = "admin"
    password = "secret"

    def __init__(self, session: _Session) -> None:
        self._session = session

    def get_session(self) -> _Session:
        return self._session


def test_send_eveus_command_posts_expected_form_payload() -> None:
    session = _Session(_Response())

    ok = asyncio.run(
        send_eveus_command(
            session,
            "192.168.1.50",
            "admin",
            "secret",
            "currentSet",
            16,
        )
    )

    assert ok is True
    assert session.calls[0]["url"] == "http://192.168.1.50/pageEvent"
    assert session.calls[0]["data"] == "pageevent=currentSet&currentSet=16"
    assert session.calls[0]["headers"] == {
        "Content-type": "application/x-www-form-urlencoded"
    }


def test_send_eveus_command_returns_false_on_failure() -> None:
    session = _Session(_Response(raise_error=True))

    ok = asyncio.run(
        send_eveus_command(session, "host", "user", "pass", "oneCharge", 1)
    )

    assert ok is False


def test_command_manager_records_success_and_failure_counts() -> None:
    success_session = _Session(_Response())
    manager = CommandManager(_Updater(success_session))

    assert asyncio.run(manager.send_command("evseEnabled", 1)) is True
    assert manager._consecutive_failures == 0
    assert success_session.calls[0]["data"] == "pageevent=evseEnabled&evseEnabled=1"

    failure_session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(failure_session))

    assert asyncio.run(manager.send_command("evseEnabled", 0)) is False
    assert manager._consecutive_failures == 1


def test_command_manager_applies_rate_limit_after_failure() -> None:
    failure_session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(failure_session))

    assert asyncio.run(manager.send_command("evseEnabled", 0)) is False

    assert manager._last_command_time > 0
