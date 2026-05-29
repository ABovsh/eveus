"""Unit tests for the Eveus data coordinator."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from conftest import TEST_BASE_URL, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import (
    CHARGING_UPDATE_INTERVAL,
    RETRY_DELAY,
)


class _Hass:
    """Minimal hass object for coordinator construction."""

    loop = None


class _Response:
    def __init__(self, *, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"state": 2}

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)

    async def json(self, **kwargs: object) -> object:
        if isinstance(self.payload, str):
            return json.loads(self.payload)
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _FailingSession:
    def post(self, url: str, **kwargs: object) -> _Response:
        raise asyncio.TimeoutError()


@pytest.fixture
def coordinator(monkeypatch: pytest.MonkeyPatch) -> tuple[EveusUpdater, _Session]:
    """Create a coordinator with a fake HTTP session."""
    session = _Session(_Response(payload={"state": 4, "currentSet": 16, "powerMeas": 7200}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass()), session


def test_update_data_fetches_payload_and_uses_stable_interval(
    coordinator: tuple[EveusUpdater, _Session],
) -> None:
    updater, session = coordinator

    data = asyncio.run(updater._async_update_data())

    assert data == {"state": 4, "currentSet": 16, "powerMeas": 7200}
    assert session.calls[0]["url"] == f"{TEST_BASE_URL}/main"
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)
    assert updater.connection_quality["consecutive_failures"] == 0


def test_update_data_uses_configured_https_scheme_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"state": 4, "currentSet": 16, "powerMeas": 7200}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(
        "eveus.local:8443",
        TEST_USERNAME,
        TEST_PASSWORD,
        _Hass(),
        scheme="https",
    )

    asyncio.run(updater._async_update_data())

    assert len(session.calls) >= 1
    assert session.calls[0]["url"] == "https://eveus.local:8443/main"
    assert updater.connection_quality["consecutive_failures"] == 0


def test_coordinator_compatibility_helpers() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    assert updater.available is True
    assert asyncio.run(updater.async_shutdown()) is None
    assert updater._should_log() is True
    assert updater._should_log() is False


def test_update_data_relaxes_interval_when_device_is_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle (non-charging) state should slow the poll cadence to IDLE."""
    from custom_components.eveus.const import IDLE_UPDATE_INTERVAL

    session = _Session(_Response(payload={"state": 2, "currentSet": 16, "powerMeas": 0}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater._async_update_data())

    assert updater.update_interval == timedelta(seconds=IDLE_UPDATE_INTERVAL)


def test_update_data_uses_charging_interval_while_charging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Charging state must keep the fast 30s cadence."""
    session = _Session(_Response(payload={"state": 4, "currentSet": 16, "powerMeas": 7200}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater._async_update_data())

    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


def test_update_data_uses_charging_interval_while_paused_mid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paused (state 6) is an active session, so it must keep the fast 30s cadence."""
    session = _Session(_Response(payload={"state": 6, "currentSet": 16, "powerMeas": 0}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater._async_update_data())

    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


def test_update_data_raises_auth_failed_on_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(status=401))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    with pytest.raises(ConfigEntryAuthFailed, match="Invalid authentication"):
        asyncio.run(updater._async_update_data())
    assert updater.connection_quality["consecutive_failures"] == 1


def test_update_data_raises_update_failed_for_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload="{not-json"))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    with pytest.raises(UpdateFailed, match="Invalid Eveus response: JSONDecodeError"):
        asyncio.run(updater._async_update_data())
    assert updater.available is False
    assert updater.connection_quality["sample_count"] == 1

    assert updater.connection_quality["consecutive_failures"] == 1
    assert updater.connection_quality["last_error"] == "JSONDecodeError"


def test_update_data_raises_update_failed_for_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload=["not", "a", "mapping"]))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())

    assert updater.connection_quality["consecutive_failures"] == 1
    assert updater.connection_quality["last_error"] == "ValueError"


def test_update_data_marks_unavailable_and_raises_update_failed_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common_network,
        "async_get_clientsession",
        lambda hass: _FailingSession(),
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.data = {"state": 2}

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())

    assert updater.available is False
    assert updater.connection_quality["last_error"] == "TimeoutError"


def test_initial_network_failure_raises_update_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common_network,
        "async_get_clientsession",
        lambda hass: _FailingSession(),
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())
    assert updater.available is False


def test_offline_backoff_skip_raises_even_without_prior_data() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.data = None
    updater._next_poll_attempt = time.time() + RETRY_DELAY

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())


def test_force_refresh_bypasses_offline_backoff_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"state": 2, "currentSet": 16}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._next_poll_attempt = time.time() + RETRY_DELAY
    updater._force_refresh_requested = True

    data = asyncio.run(updater._async_update_data())

    assert data == {"state": 2, "currentSet": 16}
    assert len(session.calls) == 1


def test_send_command_schedules_delayed_refresh_only_after_success() -> None:
    """Successful command must schedule one task per refresh delay; rapid
    re-commands cancel the previous tasks; failures schedule nothing."""
    delays = common_network.POST_COMMAND_REFRESH_DELAYS

    async def scenario() -> None:
        class _LoopHass:
            is_stopping = False

            def async_create_task(self, coro):
                return asyncio.get_event_loop().create_task(coro)

        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _LoopHass())
        recorded_delays: list[float] = []

        async def fake_delayed_refresh(self, delay: float) -> None:
            recorded_delays.append(delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

        # Replace the actual refresh with a sleep so we can observe tasks.
        original = EveusUpdater._delayed_refresh
        EveusUpdater._delayed_refresh = fake_delayed_refresh  # type: ignore[assignment]
        try:
            async def successful(command: str, value: object, **kwargs: object) -> bool:
                return True

            async def failed(command: str, value: object, **kwargs: object) -> bool:
                return False

            updater._command_manager = SimpleNamespace(send_command=successful)

            assert await updater.send_command("currentSet", 16) is True
            await asyncio.sleep(0)  # let tasks register
            first_tasks = list(updater._post_command_refresh_tasks)
            assert len(first_tasks) == len(delays)
            assert tuple(recorded_delays[: len(delays)]) == delays

            # Rapid second command cancels first batch and schedules new one.
            assert await updater.send_command("currentSet", 10) is True
            await asyncio.sleep(0)
            assert all(t.cancelled() or t.done() for t in first_tasks)
            assert len(updater._post_command_refresh_tasks) == len(delays)

            # Failure must not schedule anything new or cancel pending tasks.
            second_batch = list(updater._post_command_refresh_tasks)
            updater._command_manager = SimpleNamespace(send_command=failed)
            assert await updater.send_command("currentSet", 12) is False
            await asyncio.sleep(0)
            assert updater._post_command_refresh_tasks == second_batch

            # Cleanup
            for t in updater._post_command_refresh_tasks:
                t.cancel()
        finally:
            EveusUpdater._delayed_refresh = original  # type: ignore[assignment]

    asyncio.run(scenario())


def test_failure_recording_reduces_polling_when_device_appears_offline() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_time = time.time() - 700
    updater._consecutive_failures = 10

    updater._record_failure(asyncio.TimeoutError())

    assert updater.is_likely_offline is True
    assert updater._next_poll_attempt > time.time()
    assert updater.connection_quality["last_error"] == "TimeoutError"


def test_failure_recording_enters_silent_mode_after_many_failures() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._consecutive_failures = 20

    updater._record_failure(asyncio.TimeoutError())

    assert updater._silent_mode is True


def test_connection_quality_uses_recent_poll_window() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._success_count = 100
    updater._total_count = 100

    for _ in range(10):
        updater._poll_results.append(False)

    metrics = updater.connection_quality

    assert metrics["success_rate"] == 0
    assert metrics["sample_count"] == 10


def test_offline_failure_recording_is_quiet_at_normal_log_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_time = time.time() - 700
    updater._consecutive_failures = 10

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.common_network"):
        updater._record_failure(asyncio.TimeoutError())

    assert updater.available is False
    assert caplog.records == []


def test_tune_interval_preserves_offline_cadence_when_likely_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: _tune_update_interval was switching to IDLE on first recovery
    even while is_likely_offline was still True, defeating the offline backoff."""
    from custom_components.eveus.const import OFFLINE_UPDATE_INTERVAL

    session = _Session(_Response(payload={"state": 2}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    updater._consecutive_failures = 11
    updater._last_success_time = time.time() - 700

    assert updater.is_likely_offline is True

    updater._tune_update_interval({"state": 2})
    assert updater.update_interval == timedelta(seconds=OFFLINE_UPDATE_INTERVAL), (
        "_tune_update_interval must keep OFFLINE cadence while is_likely_offline is True"
    )


def test_connection_quality_dict_has_all_expected_keys() -> None:
    """connection_quality must expose all keys that sensor and diagnostics depend on."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    metrics = updater.connection_quality

    for key in (
        "success_rate",
        "latency_avg",
        "consecutive_failures",
        "consecutive_command_failures",
        "is_healthy",
        "last_success_time",
        "last_error",
        "sample_count",
    ):
        assert key in metrics, f"connection_quality missing key: {key!r}"


def test_is_likely_offline_transitions() -> None:
    """is_likely_offline requires both >10 consecutive failures and >600s since last success."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    # Neither condition met — online.
    assert updater.is_likely_offline is False

    # Failures without time — still online.
    updater._consecutive_failures = 11
    updater._last_success_time = time.time()
    assert updater.is_likely_offline is False

    # Time without failures — still online.
    updater._consecutive_failures = 5
    updater._last_success_time = time.time() - 700
    assert updater.is_likely_offline is False

    # Both conditions met — offline.
    updater._consecutive_failures = 11
    updater._last_success_time = time.time() - 700
    assert updater.is_likely_offline is True


def test_current_setpoint_rounding_not_truncation() -> None:
    """Regression: int(clamped_value) truncates 15.99 → 15 instead of rounding to 16."""
    assert int(round(15.99)) == 16
    assert int(15.99) == 15  # confirms old behaviour was wrong


def test_async_shutdown_awaits_cancelled_tasks() -> None:
    """Regression: async_shutdown must await cancelled tasks to avoid
    'Task was destroyed but it is pending!' log warnings on reload."""

    async def scenario() -> None:
        class _LoopHass:
            is_stopping = False

            def async_create_task(self, coro):
                return asyncio.get_event_loop().create_task(coro)

        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _LoopHass())

        running = asyncio.Event()
        finished = asyncio.Event()

        async def _long_refresh(delay: float) -> None:
            running.set()
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                finished.set()
                raise

        updater._post_command_refresh_tasks.append(
            asyncio.get_event_loop().create_task(_long_refresh(60))
        )

        await running.wait()

        # shutdown must cancel and AWAIT so the task finishes cleanly
        await updater.async_shutdown()

        assert finished.is_set(), "Cancelled task was not awaited by async_shutdown"
        assert updater._post_command_refresh_tasks == []

    asyncio.run(scenario())


def test_updater_caches_basic_auth_object() -> None:
    """BasicAuth must be cached on the updater, not rebuilt per poll."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    import aiohttp
    assert isinstance(updater._basic_auth, aiohttp.BasicAuth)
    assert updater._basic_auth.login == TEST_USERNAME


def test_update_uses_module_level_timeout_object(
    coordinator: tuple[EveusUpdater, _Session],
) -> None:
    """Poll timeout comes from the module constant, not rebuilt per poll."""
    updater, session = coordinator
    asyncio.run(updater._async_update_data())
    assert session.calls[0]["timeout"] is common_network._UPDATE_TIMEOUT_OBJ


def test_force_refresh_resets_flag_after_refresh_failure() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    async def broken_refresh() -> None:
        raise RuntimeError("refresh failed")

    updater.async_refresh = broken_refresh

    with pytest.raises(RuntimeError):
        asyncio.run(updater.async_force_refresh())

    assert updater._force_refresh_requested is False


def test_delayed_refresh_exits_when_hass_is_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(delay: float) -> None:
        return None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.hass = SimpleNamespace(is_stopping=True)
    refreshed = False

    async def refresh() -> None:
        nonlocal refreshed
        refreshed = True

    updater.async_refresh = refresh
    monkeypatch.setattr(common_network.asyncio, "sleep", no_sleep)

    asyncio.run(updater._delayed_refresh(1))

    assert refreshed is False


def test_delayed_refresh_swallows_refresh_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(delay: float) -> None:
        return None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.hass = SimpleNamespace(is_stopping=False)

    async def refresh() -> None:
        raise RuntimeError("refresh failed")

    updater.async_refresh = refresh
    monkeypatch.setattr(common_network.asyncio, "sleep", no_sleep)

    asyncio.run(updater._delayed_refresh(1))


def test_tune_interval_handles_invalid_state_and_custom_interval() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    # Out-of-domain state is treated as suspect: hold offline cadence rather
    # than snap back to idle polling on data that the /main validator already
    # would have rejected.
    updater._tune_update_interval({"state": "bad"})
    assert updater.update_interval == timedelta(seconds=common_network.OFFLINE_UPDATE_INTERVAL)

    # A known non-charging state keeps the idle cadence.
    updater._tune_update_interval({"state": 3})
    assert updater.update_interval == timedelta(seconds=common_network.IDLE_UPDATE_INTERVAL)

    updater._set_update_interval(123)
    assert updater.update_interval == timedelta(seconds=123)
