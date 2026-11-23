"""Unit tests for Eveus command helpers."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from conftest import TEST_BASE_URL, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from conftest import OutageClock
from custom_components.eveus import common_command
from custom_components.eveus.common_command import CommandManager
from custom_components.eveus.const import COMMAND_TIMEOUT


class _Response:
    def __init__(
        self,
        *,
        raise_error: bool = False,
        response_status: int | None = None,
    ) -> None:
        self.raise_error = raise_error
        self.response_status = response_status
        self.status = 200

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


class _Updater(OutageClock):
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


def test_command_manager_first_command_no_spacing_sleep_small_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression for the monotonic-clock switch: right after boot monotonic() is
    # small, so a 0 sentinel made `time_since_last` < 1 and slept up to a second
    # before the very first command. The first command must fire immediately.
    monkeypatch.setattr(
        "custom_components.eveus.common_command.time.monotonic", lambda: 0.5
    )
    sleeps: list[float] = []

    async def _spy_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "custom_components.eveus.common_command.asyncio.sleep", _spy_sleep
    )

    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("currentSet", 16)) is True
    assert sleeps == []  # no rate-limit sleep on the first command


def test_command_manager_applies_rate_limit_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    failure_session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(failure_session))

    assert asyncio.run(manager.send_command("evseEnabled", 0)) is False
    assert manager._last_command_time > 0


def test_preflight_rejection_sends_nothing_and_keeps_rate_limit_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preflight veto is not a command: no POST, no failure, no pacing debt.

    Consuming the 1 s spacing here would delay the next real command (a user's
    Stop right after a withdrawn SOC-limit Stop) for no network reason.
    """
    sleeps: list[float] = []

    async def _spy_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _spy_sleep)
    monkeypatch.setattr(
        "custom_components.eveus.common_command.time.monotonic", lambda: 100.0
    )
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))
    manager._last_command_time = 50.0

    assert asyncio.run(
        manager.send_command("evseEnabled", 1, preflight=lambda: False)
    ) is False
    assert session.calls == []
    assert manager._consecutive_failures == 0
    assert manager._last_command_time == 50.0

    # First-ever command vetoed: the next real command is still unspaced.
    fresh = CommandManager(_Updater(session))
    assert asyncio.run(fresh.send_command("evseEnabled", 1, preflight=lambda: False)) is False
    assert asyncio.run(fresh.send_command("evseEnabled", 1)) is True
    assert sleeps == []
    assert len(session.calls) == 1


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
    assert sleep_calls
    assert 0 < sleep_calls[0] <= 1


def test_rate_limit_sleep_formula_and_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact `max(0.0, min(1.0, 1 - time_since_last))` formula, both boundaries."""
    sleep_calls: list[float] = []

    async def _spy_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _spy_sleep)
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    def _run(mono_now: float, last_command_time: float) -> None:
        manager._last_command_time = last_command_time
        monkeypatch.setattr(
            "custom_components.eveus.common_command.time.monotonic", lambda: mono_now
        )
        asyncio.run(manager.send_command("currentSet", 16))

    # Exactly at the 1-second boundary: must NOT sleep (`< 1`, not `<= 1`).
    sleep_calls.clear()
    _run(mono_now=10.0, last_command_time=9.0)
    assert sleep_calls == []

    # 1.9s elapsed (not < 1 but would be < 2): must NOT sleep either.
    sleep_calls.clear()
    _run(mono_now=10.9, last_command_time=9.0)
    assert sleep_calls == []

    # 0.9s elapsed: exact sleep must be 0.1s, not clamped to 1.0.
    sleep_calls.clear()
    _run(mono_now=9.9, last_command_time=9.0)
    assert sleep_calls == [pytest.approx(0.1)]

    # Backward clock step (negative elapsed): must clamp to exactly 1.0.
    sleep_calls.clear()
    _run(mono_now=8.5, last_command_time=9.0)
    assert sleep_calls == [1.0]


def test_command_manager_urlencodes_command_payload() -> None:
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("profile name", "eco mode")) is True

    assert len(session.calls) == 1
    assert session.calls[0]["data"] == "pageevent=profile+name&profile+name=eco+mode"


@pytest.mark.parametrize(("command", "value"), [("currentSet", 16), ("evseEnabled", 1)])
def test_firmware_1x_gets_the_form_its_own_web_page_sends(command, value) -> None:
    """Firmware 1.x: selector in the pageEvent header, body only <cmd>=<value>.

    That is byte for byte what the 1.x web page posts (js/scriptUA.js
    postPageEvent), so it is the one form 1.x is known to accept.
    """
    session = _Session(_Response())
    updater = _Updater(session)
    updater.is_legacy_firmware = True
    manager = CommandManager(updater)

    assert asyncio.run(manager.send_command(command, value)) is True

    (call,) = session.calls
    assert call["url"] == f"{TEST_BASE_URL}/pageEvent"
    assert call["data"] == f"{command}={value}"
    assert call["headers"] == {
        "Content-type": "application/x-www-form-urlencoded",
        "pageEvent": command,
    }


def test_firmware_1x_one_charge_uses_the_page_setting_name() -> None:
    """The 1.x page writes One Charge as one_charge (js/scriptUA.js)."""
    session = _Session(_Response())
    updater = _Updater(session)
    updater.is_legacy_firmware = True
    manager = CommandManager(updater)

    assert asyncio.run(manager.send_command("oneCharge", 1)) is True

    (call,) = session.calls
    assert call["data"] == "one_charge=1"
    assert call["headers"]["pageEvent"] == "one_charge"


def test_modern_firmware_keeps_the_body_selector_form() -> None:
    """Newer firmware is untouched: the live-verified body form, no header."""
    session = _Session(_Response())
    updater = _Updater(session)
    updater.is_legacy_firmware = False
    manager = CommandManager(updater)

    assert asyncio.run(manager.send_command("evseEnabled", 1)) is True

    (call,) = session.calls
    assert call["data"] == "pageevent=evseEnabled&evseEnabled=1"
    assert call["headers"] == {"Content-type": "application/x-www-form-urlencoded"}


def test_classic_ocpp_sends_header_form_before_page_event() -> None:
    session = _Session(_Response())
    updater = _Updater(session)
    manager = CommandManager(updater)

    assert asyncio.run(
        manager.send_command("ocppEnabled", 0, extra={"ocppVendor": 0})
    ) is True

    ocpp, page = session.calls
    assert ocpp == {
        "url": f"{TEST_BASE_URL}/ocppEvent",
        "headers": {
            "Content-type": "application/x-www-form-urlencoded",
            "pageEvent": "ocppEnabled",
        },
        "data": "ocppEnabled=0",
        "auth": updater.basic_auth,
        "timeout": common_command._COMMAND_TIMEOUT_OBJ,
        "allow_redirects": False,
    }
    assert page["url"] == f"{TEST_BASE_URL}/pageEvent"
    assert page["headers"] == {"Content-type": "application/x-www-form-urlencoded"}
    assert page["data"] == "pageevent=ocppEnabled&ocppEnabled=0&ocppVendor=0"


@pytest.mark.parametrize(("ocpp_status", "page_status"), [(404, 200), (200, 503), (200, 401)])
def test_classic_ocpp_succeeds_if_either_write_succeeds(
    ocpp_status: int, page_status: int, caplog: pytest.LogCaptureFixture
) -> None:
    session = _SequencedSession([
        _Response(response_status=ocpp_status if ocpp_status != 200 else None),
        _Response(response_status=page_status if page_status != 200 else None),
    ])
    manager = CommandManager(_Updater(session))
    caplog.set_level(logging.DEBUG, logger=common_command.__name__)

    assert asyncio.run(manager.send_command("ocppEnabled", 0)) is True
    assert [call["url"] for call in session.calls] == [
        f"{TEST_BASE_URL}/ocppEvent", f"{TEST_BASE_URL}/pageEvent",
    ]
    assert manager.consecutive_failures == 0
    assert all(record.levelno <= logging.DEBUG for record in caplog.records)


def test_classic_ocpp_timeout_still_sends_page_event() -> None:
    class _TimeoutResponse(_Response):
        async def __aenter__(self) -> _Response:
            raise asyncio.TimeoutError

    session = _SequencedSession([_TimeoutResponse(), _Response()])
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("ocppEnabled", 0, retry=False)) is True
    assert [call["url"] for call in session.calls] == [
        f"{TEST_BASE_URL}/ocppEvent", f"{TEST_BASE_URL}/pageEvent",
    ]


def test_classic_ocpp_retries_only_as_part_of_a_page_event_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(common_command.asyncio, "sleep", _no_sleep)
    session = _SequencedSession([
        _Response(response_status=404), _Response(response_status=503),
    ] * 3)
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("ocppEnabled", 0)) is False
    assert [call["url"] for call in session.calls] == [
        f"{TEST_BASE_URL}/ocppEvent", f"{TEST_BASE_URL}/pageEvent",
    ] * 3
    assert manager.consecutive_failures == 1


def test_classic_ocpp_preserves_page_event_auth_failure_when_both_fail() -> None:
    session = _SequencedSession([
        _Response(response_status=404), _Response(response_status=401),
    ])
    manager = CommandManager(_Updater(session))

    with pytest.raises(ConfigEntryAuthFailed) as exc:
        asyncio.run(manager.send_command("ocppEnabled", 0))

    assert isinstance(exc.value.__cause__, aiohttp.ClientResponseError)
    assert exc.value.__cause__.status == 401
    assert len(session.calls) == 2
    assert manager.consecutive_failures == 1


@pytest.mark.parametrize("ocpp_status", [None, 404])
def test_classic_ocpp_skips_page_event_if_shutdown_begins(
    ocpp_status: int | None,
) -> None:
    class _ShutdownResponse(_Response):
        async def __aexit__(self, *exc_info: object) -> None:
            updater._shutting_down = True

    session = _Session(_ShutdownResponse(response_status=ocpp_status))
    updater = _Updater(session)
    manager = CommandManager(updater)

    assert asyncio.run(manager.send_command("ocppEnabled", 0)) is (ocpp_status is None)
    assert [call["url"] for call in session.calls] == [f"{TEST_BASE_URL}/ocppEvent"]
    assert manager.consecutive_failures == 0


def test_classic_one_charge_only_sends_page_event() -> None:
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("oneCharge", 1)) is True

    call, = session.calls
    assert call["url"] == f"{TEST_BASE_URL}/pageEvent"
    assert call["data"] == "pageevent=oneCharge&oneCharge=1"


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


# ─── module-level constants ──────────────────────────────────────────────────

def test_command_timeout_obj_wraps_the_documented_timeout() -> None:
    assert common_command._COMMAND_TIMEOUT_OBJ.total == COMMAND_TIMEOUT


def test_retry_constants_have_documented_values() -> None:
    assert common_command._COMMAND_RETRY_ATTEMPTS == 2
    assert common_command._COMMAND_RETRY_BACKOFF == (0.5, 1.5)
    assert common_command._COMMAND_RETRY_JITTER == 0.25


def test_sleep_backoff_adds_jitter_on_top_of_the_base_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_bounds: list[tuple[float, float]] = []

    def _fake_uniform(lo: float, hi: float) -> float:
        seen_bounds.append((lo, hi))
        return 0.1

    monkeypatch.setattr(
        "custom_components.eveus.common_command.random.uniform", _fake_uniform
    )
    delays: list[float] = []

    async def _spy_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _spy_sleep)
    manager = CommandManager(_Updater(_Session(_Response())))

    asyncio.run(manager._sleep_backoff(0))
    # delay = backoff[0] + uniform(0, jitter); a "-" or shifted-bounds mutation
    # would change either the recorded bounds or the resulting delay.
    assert seen_bounds == [(0, 0.25)]
    assert delays == [pytest.approx(0.6)]


def test_command_manager_stops_retrying_once_attempts_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final attempt must break immediately, not call _sleep_backoff again.

    Covers both exception branches (ClientResponseError and the generic
    connector/timeout tuple) that gate retries on `attempt >= retry_attempts`.
    """
    monkeypatch.setattr("custom_components.eveus.common_command.random.uniform", lambda a, b: 0)
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)

    for response in (_Response(response_status=500), _Response(raise_error=True)):
        session = _Session(response)
        manager = CommandManager(_Updater(session))
        seen_attempts: list[int] = []
        original = manager._sleep_backoff

        async def spy(attempt: int, _orig=original) -> None:
            seen_attempts.append(attempt)
            await _orig(attempt)

        manager._sleep_backoff = spy

        assert asyncio.run(manager.send_command("currentSet", 16)) is False
        assert seen_attempts == [0, 1]  # never invoked for the exhausted final attempt
        assert len(session.calls) == 3


@pytest.mark.parametrize("status", [408, 425, 429, 502, 503, 504])
def test_command_manager_retries_every_documented_transient_status(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("custom_components.eveus.common_command.random.uniform", lambda a, b: 0)
    session = _Session(_Response(response_status=status))
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("currentSet", 16)) is False
    assert len(session.calls) == 3  # retried through all attempts, not treated as permanent


def test_command_manager_accumulates_consecutive_failures_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each failure path increments the counter; it must not reset to 1 every time."""
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)

    # 401 / ConfigEntryAuthFailed path.
    session = _Session(_Response(response_status=401))
    manager = CommandManager(_Updater(session))
    for _ in range(2):
        with pytest.raises(ConfigEntryAuthFailed):
            asyncio.run(manager.send_command("currentSet", 16))
    assert manager.consecutive_failures == 2

    # Retries-exhausted (permanent, non-retryable status) path.
    session = _Session(_Response(response_status=400))
    manager = CommandManager(_Updater(session))
    for _ in range(2):
        assert asyncio.run(manager.send_command("currentSet", 16)) is False
    assert manager.consecutive_failures == 2

    # Unexpected/non-aiohttp exception path.
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))

    async def broken_post(command: str, value: object) -> bool:
        raise RuntimeError("unexpected")

    manager._post_command = broken_post
    for _ in range(2):
        assert asyncio.run(manager.send_command("currentSet", 16)) is False
    assert manager.consecutive_failures == 2


def test_command_manager_logs_the_real_error_type_not_a_stale_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`last_error` must be the actual exception, in the permanent-status
    break, the retries-exhausted-status break, AND the connector/timeout
    except branch — a None default silently swallowed there would mislabel
    every failure as "NoneType" in the log."""
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("custom_components.eveus.common_command.random.uniform", lambda a, b: 0)

    with caplog.at_level(logging.DEBUG, logger="custom_components.eveus.common_command"):
        # Permanent (non-retryable) status -> break with last_error = err directly.
        manager = CommandManager(_Updater(_Session(_Response(response_status=400))))
        asyncio.run(manager.send_command("currentSet", 16))
        # Retryable status, exhausted -> break after reassigning last_error = err.
        manager2 = CommandManager(_Updater(_Session(_Response(response_status=500))))
        asyncio.run(manager2.send_command("currentSet", 16))
        # ClientConnectorError/ClientError/TimeoutError except branch.
        manager3 = CommandManager(_Updater(_Session(_Response(raise_error=True))))
        asyncio.run(manager3.send_command("currentSet", 16))

    failed_records = [r for r in caplog.records if "failed" in r.message]
    assert len(failed_records) == 3
    assert "ClientResponseError" in failed_records[0].message
    assert "ClientResponseError" in failed_records[1].message
    assert "ClientError" in failed_records[2].message
    for record in failed_records:
        assert "NoneType" not in record.message


def test_command_manager_caps_failure_logging_at_five_consecutive(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    session = _Session(_Response(raise_error=True))
    manager = CommandManager(_Updater(session))
    # Bypass the real time-based rate limiter so every failure is a logging
    # candidate; only the `consecutive_failures <= 5` gate should matter here.
    monkeypatch.setattr(manager, "_should_log_error", lambda: True)

    with caplog.at_level(logging.DEBUG, logger="custom_components.eveus.common_command"):
        for _ in range(7):
            asyncio.run(manager.send_command("currentSet", 16))

    failed_records = [r for r in caplog.records if "failed" in r.message]
    assert len(failed_records) == 5  # exactly failures #1-#5, none after


def test_post_command_short_circuits_while_shutting_down() -> None:
    session = _Session(_Response())
    updater = _Updater(session)
    updater._shutting_down = True
    manager = CommandManager(updater)

    assert asyncio.run(manager._post_command("currentSet", 16)) is False
    assert len(session.calls) == 0


def test_command_manager_unexpected_error_logs_only_its_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "TOKEN-SENTINEL"

    class _SecretError(RuntimeError):
        pass

    class _ExplodingSession:
        def post(self, url: str, **kwargs: object):
            try:
                raise ValueError(f"cause {sentinel}")
            except ValueError as cause:
                raise _SecretError(f"{url} {sentinel}") from cause

    manager = CommandManager(_Updater(_ExplodingSession()))
    with caplog.at_level(logging.DEBUG, logger="custom_components.eveus.common_command"):
        assert asyncio.run(manager.send_command("currentSet", 16)) is False

    assert sentinel not in caplog.text
    assert TEST_HOST not in caplog.text
    assert "_SecretError" in caplog.text


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_command_rejects_a_redirect_without_following_or_retrying(status: int) -> None:
    """Following a redirect would re-send the command form to another origin."""
    response = _Response()
    response.status = status
    session = _Session(response)
    manager = CommandManager(_Updater(session))

    assert asyncio.run(manager.send_command("currentSet", 16)) is False
    assert [call["allow_redirects"] for call in session.calls] == [False]


def test_stop_overtakes_waiting_settings_without_interrupting_inflight(monkeypatch):
    session = _Session(_Response())
    manager = CommandManager(_Updater(session))
    original_sleep = asyncio.sleep
    monkeypatch.setattr(common_command.asyncio, 'sleep', _no_sleep)

    async def scenario():
        await manager._lock.acquire()
        settings = [asyncio.create_task(manager.send_command('currentSet', v)) for v in (8, 10)]
        stop = asyncio.create_task(manager.send_command('evseEnabled', 1))
        for _ in range(5):
            await original_sleep(0)
        manager._lock.release()
        await asyncio.gather(*settings, stop)

    asyncio.run(scenario())
    assert session.calls[0]['data'] == 'pageevent=evseEnabled&evseEnabled=1'
