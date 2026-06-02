"""Unit tests for Eveus command helpers."""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from conftest import TEST_BASE_URL, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus.common_command import CommandManager


class _Response:
    def __init__(
        self,
        *,
        raise_error: bool = False,
        response_status: int | None = None,
    ) -> None:
        self.raise_error = raise_error
        self.response_status = response_status

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.response_status is not None:
            raise aiohttp.ClientResponseError(
                request_info=None,
                history=(),
                status=self.response_status,
            )
        if self.raise_error:
            raise aiohttp.ClientError("boom")


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _SequencedSession:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


class _Updater:
    host = TEST_HOST
    username = TEST_USERNAME
    password = TEST_PASSWORD

    def __init__(self, session: _Session) -> None:
        self._session = session
        import aiohttp
        self._basic_auth = aiohttp.BasicAuth(self.username, self.password)

    @property
    def basic_auth(self):
        return self._basic_auth

    def get_session(self) -> _Session:
        return self._session

    def url_for(self, path: str) -> str:
        return f"http://{self.host}{path}"


def test_command_manager_posts_expected_form_payload() -> None:
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    ok = asyncio.run(manager.send_command("currentSet", 16))

    assert ok is True
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == f"{TEST_BASE_URL}/pageEvent"
    assert session.calls[0]["data"] == "pageevent=currentSet&currentSet=16"
    assert session.calls[0]["headers"] == {
        "Content-type": "application/x-www-form-urlencoded"
    }


def test_command_manager_records_success_and_failure_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success_session = _Session(_Response())
    manager = CommandManager(_Updater(success_session))

    assert asyncio.run(manager.send_command("evseEnabled", 1)) is True
    assert manager._consecutive_failures == 0
    assert len(success_session.calls) == 1
    assert success_session.calls[0]["data"] == "pageevent=evseEnabled&evseEnabled=1"

    # Skip retry sleeps in failure path
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)

    failure_session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(failure_session))

    assert asyncio.run(manager.send_command("evseEnabled", 0)) is False
    assert manager._consecutive_failures == 1
    # Retries: initial attempt + _COMMAND_RETRY_ATTEMPTS retries
    assert len(failure_session.calls) == 3


def test_command_manager_applies_rate_limit_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    failure_session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(failure_session))

    assert asyncio.run(manager.send_command("evseEnabled", 0)) is False
    assert manager._last_command_time > 0


def test_command_manager_recovers_after_transient_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    session = _SequencedSession([_Response(raise_error=True), _Response()])
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("currentSet", 12)) is True
    assert len(session.calls) == 2
    assert manager.consecutive_failures == 0


def test_command_manager_can_disable_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    failure_session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(failure_session))

    assert asyncio.run(manager.send_command("rstEM1", 0, retry=False)) is False

    assert len(failure_session.calls) == 1


def test_command_manager_serializes_concurrent_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []

    async def tracked_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", tracked_sleep)
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    async def scenario() -> None:
        await asyncio.gather(
            manager.send_command("currentSet", 16),
            manager.send_command("evseEnabled", 1),
        )

    asyncio.run(scenario())

    assert [call["data"] for call in session.calls] == [
        "pageevent=currentSet&currentSet=16",
        "pageevent=evseEnabled&evseEnabled=1",
    ]
    assert sleep_calls and 0 < sleep_calls[0] <= 1


def test_command_manager_urlencodes_command_payload() -> None:
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("profile name", "eco mode")) is True

    assert len(session.calls) == 1
    assert session.calls[0]["data"] == "pageevent=profile+name&profile+name=eco+mode"


def test_command_manager_includes_extra_form_fields() -> None:
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    assert asyncio.run(
        manager.send_command("ocppEnabled", 1, extra={"ocppVendor": 1})
    ) is True

    assert session.calls[0]["data"] == (
        "pageevent=ocppEnabled&ocppEnabled=1&ocppVendor=1"
    )


async def _no_sleep(_seconds: float) -> None:
    return None


def test_command_manager_uses_module_level_timeout() -> None:
    """Timeout object must come from the module-level constant, not be built per call."""
    from custom_components.eveus import common_command

    session = _Session(_Response())
    asyncio.run(CommandManager(_Updater(session)).send_command("currentSet", 16))

    assert session.calls[0]["timeout"] is common_command._COMMAND_TIMEOUT_OBJ


def test_command_manager_non_auth_response_error_retries_and_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("custom_components.eveus.common_command.random.uniform", lambda a, b: 0)
    session = _Session(_Response(response_status=500))
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("currentSet", 16)) is False

    assert len(session.calls) == 3
    assert manager.consecutive_failures == 1


def test_command_manager_does_not_retry_permanent_response_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    session = _Session(_Response(response_status=400))
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("currentSet", 16)) is False

    assert len(session.calls) == 1
    assert manager.consecutive_failures == 1


def test_command_manager_raises_auth_failed_without_retry() -> None:
    session = _Session(_Response(response_status=401))
    manager = CommandManager(_Updater(session))

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(manager.send_command("currentSet", 16))

    assert len(session.calls) == 1
    assert manager.consecutive_failures == 1


def test_command_manager_handles_unexpected_post_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    async def broken_post(command: str, value: object) -> bool:
        raise RuntimeError("unexpected")

    manager._post_command = broken_post

    assert asyncio.run(manager.send_command("currentSet", 16)) is False
    assert manager.consecutive_failures == 1
