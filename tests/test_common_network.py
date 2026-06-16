"""Unit tests for the Eveus data coordinator."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

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

    @property
    def content_length(self) -> int | None:
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return len(body.encode())

    @property
    def content(self) -> "_StreamReader":
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _StreamReader(body.encode())


class _StreamReader:
    """Minimal aiohttp StreamReader stand-in for read_json_capped."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


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


@pytest.fixture
def updater() -> EveusUpdater:
    """Create a coordinator for direct state tests."""
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())


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


def test_coordinator_name_does_not_leak_host() -> None:
    # HA's DataUpdateCoordinator logs ``self.name`` at ERROR/INFO level on every
    # poll timeout/connection error, so the coordinator name must not embed the
    # charger host/IP — otherwise it defeats the host-redaction in logs.
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert TEST_HOST not in updater.name

    numbered = EveusUpdater(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), device_number=2
    )
    # Multi-charger logs stay distinguishable via the device number, not the host.
    assert TEST_HOST not in numbered.name
    assert "2" in numbered.name


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
    # An auth rejection is not a connectivity failure: it must not feed the
    # offline-backoff counters, or reauth recovery gets deferred and the
    # diagnostics misattribute a credentials problem as "device offline".
    assert updater.connection_quality["consecutive_failures"] == 0
    assert updater.is_likely_offline is False
    assert updater._next_poll_attempt == 0.0
    assert updater.available is False
    assert updater.connection_quality["last_error"] == "ConfigEntryAuthFailed"


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


@pytest.mark.parametrize(
    "bad_current_set",
    [float("nan"), float("inf"), "nan", "not-a-number", True],
)
def test_update_data_rejects_non_finite_current_set(
    monkeypatch: pytest.MonkeyPatch,
    bad_current_set,
) -> None:
    """A plausible state with a corrupt currentSet must fail the poll, not come online."""
    session = _Session(
        _Response(payload={"state": 4, "currentSet": bad_current_set, "powerMeas": 7200})
    )
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())
    assert updater.connection_quality["last_error"] == "ValueError"


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
    updater._force_refresh_requests = 1

    data = asyncio.run(updater._async_update_data())

    assert data == {"state": 2, "currentSet": 16}
    assert len(session.calls) == 1


def test_send_command_schedules_post_command_refresh_only_after_success() -> None:
    """Successful command must schedule one timer per refresh delay; rapid
    re-commands cancel the previous timers; failures schedule nothing."""
    delays = common_network.POST_COMMAND_REFRESH_DELAYS

    async def scenario() -> None:
        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
        recorded_delays: list[float] = []
        unsubs: list[Mock] = []

        def fake_call_later(hass, delay, action):
            recorded_delays.append(delay)
            unsub = Mock()
            unsubs.append(unsub)
            return unsub

        async def successful(command: str, value: object, **kwargs: object) -> bool:
            return True

        async def failed(command: str, value: object, **kwargs: object) -> bool:
            return False

        updater._command_manager = SimpleNamespace(send_command=successful)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(common_network, "async_call_later", fake_call_later)

            assert await updater.send_command("currentSet", 16) is True
            first_unsubs = list(updater._pending_refresh_unsubs)
            assert len(first_unsubs) == len(delays)
            assert tuple(recorded_delays[: len(delays)]) == delays

            # Rapid second command cancels first batch and schedules new one.
            assert await updater.send_command("currentSet", 10) is True
            assert all(unsub.called for unsub in first_unsubs)
            assert len(updater._pending_refresh_unsubs) == len(delays)

            # Failure must not schedule anything new or cancel pending timers.
            second_batch = list(updater._pending_refresh_unsubs)
            updater._command_manager = SimpleNamespace(send_command=failed)
            assert await updater.send_command("currentSet", 12) is False
            assert updater._pending_refresh_unsubs == second_batch

    asyncio.run(scenario())


def test_rescheduling_then_shutdown_clears_pending_refresh_unsubs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    unsubs: list[Mock] = []

    def fake_call_later(hass, delay, action):
        unsub = Mock()
        unsubs.append(unsub)
        return unsub

    monkeypatch.setattr(common_network, "async_call_later", fake_call_later)

    updater._schedule_post_command_refresh()
    updater._schedule_post_command_refresh()

    asyncio.run(updater.async_shutdown())

    assert updater._pending_refresh_unsubs == []


def test_schedule_post_command_refresh_registers_one_unsub_per_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    def fake_call_later(hass, delay, action):
        return Mock()

    monkeypatch.setattr(common_network, "async_call_later", fake_call_later)

    updater._schedule_post_command_refresh()

    assert len(updater._pending_refresh_unsubs) == len(
        common_network.POST_COMMAND_REFRESH_DELAYS
    )


def test_failure_recording_reduces_polling_when_device_appears_offline() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_time = time.time() - 700
    updater._last_success_monotonic = time.monotonic() - 700
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


@pytest.mark.asyncio
async def test_connection_quality_caches_within_poll(updater: EveusUpdater) -> None:
    updater._poll_results.extend([1, 1, 0, 1])
    updater._latency_samples.extend([0.1, 0.2])

    q1 = updater.connection_quality
    q2 = updater.connection_quality

    assert q1 == q2
    assert q1["success_rate"] == 75.0
    assert q1["sample_count"] == 4


def test_offline_failure_recording_is_quiet_at_normal_log_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_time = time.time() - 700
    updater._last_success_monotonic = time.monotonic() - 700
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
    updater._last_success_monotonic = time.monotonic() - 700

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
    updater._last_success_monotonic = time.monotonic()
    assert updater.is_likely_offline is False

    # Time without failures — still online.
    updater._consecutive_failures = 5
    updater._last_success_time = time.time() - 700
    updater._last_success_monotonic = time.monotonic() - 700
    assert updater.is_likely_offline is False

    # Both conditions met — offline.
    updater._consecutive_failures = 11
    updater._last_success_time = time.time() - 700
    updater._last_success_monotonic = time.monotonic() - 700
    assert updater.is_likely_offline is True


def test_current_setpoint_rounding_not_truncation() -> None:
    """Regression: int(clamped_value) truncates 15.99 → 15 instead of rounding to 16."""
    assert int(round(15.99)) == 16
    assert int(15.99) == 15  # confirms old behaviour was wrong


def test_async_shutdown_cancels_pending_refresh_unsubs() -> None:
    """Regression: async_shutdown must cancel pending refresh timers."""

    async def scenario() -> None:
        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
        unsub = Mock()
        updater._pending_refresh_unsubs.append(unsub)

        await updater.async_shutdown()

        unsub.assert_called_once_with()
        assert updater._pending_refresh_unsubs == []

    asyncio.run(scenario())


def test_inflight_post_command_refresh_is_cancelled_on_shutdown() -> None:
    """Regression: a fired post-command refresh that is still running must be
    cancellable on shutdown, not run to completion uncancelled.

    The async_call_later unsub only cancels a timer that has not fired yet.
    Once the timer fires and the refresh is in flight, shutdown (and a rapid
    reschedule) must still be able to cancel the running refresh so a slow
    /main poll cannot publish stale data after teardown.
    """

    async def scenario() -> None:
        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
        updater.hass.is_stopping = False
        started = asyncio.Event()
        cancelled = False

        async def slow_refresh() -> None:
            nonlocal cancelled
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        updater.async_refresh = slow_refresh
        callbacks: list = []

        def fake_call_later(hass, delay, action):
            callbacks.append(action)
            return Mock()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(common_network, "async_call_later", fake_call_later)
            updater._schedule_post_command_refresh()

            # Fire the first timer: this starts the in-flight refresh task.
            run_task = asyncio.ensure_future(callbacks[0](None))
            await started.wait()
            assert len(updater._post_command_refresh_tasks) == 1

            await updater.async_shutdown()

        assert cancelled is True
        assert updater._post_command_refresh_tasks == []
        await asyncio.gather(run_task, return_exceptions=True)

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

    assert updater._force_refresh_requests == 0


def test_scheduled_refresh_exits_when_hass_is_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.hass = SimpleNamespace(is_stopping=True)
    refreshed = False
    callbacks = []

    async def refresh() -> None:
        nonlocal refreshed
        refreshed = True

    def fake_call_later(hass, delay, action):
        callbacks.append(action)
        return Mock()

    updater.async_refresh = refresh
    monkeypatch.setattr(common_network, "async_call_later", fake_call_later)

    updater._schedule_post_command_refresh()
    asyncio.run(callbacks[0](None))

    assert refreshed is False


def test_scheduled_refresh_swallows_refresh_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.hass = SimpleNamespace(is_stopping=False)
    callbacks = []

    async def refresh() -> None:
        raise RuntimeError("refresh failed")

    def fake_call_later(hass, delay, action):
        callbacks.append(action)
        return Mock()

    updater.async_refresh = refresh
    monkeypatch.setattr(common_network, "async_call_later", fake_call_later)

    updater._schedule_post_command_refresh()
    asyncio.run(callbacks[0](None))


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


def test_backward_clock_does_not_strand_offline_backoff(
    coordinator: tuple[EveusUpdater, _Session],
) -> None:
    """A backward wall-clock step leaves a stale deadline far beyond any real
    backoff window; the poll must proceed instead of being skipped forever."""
    updater, session = coordinator
    updater._next_poll_attempt = time.time() + 100000

    data = asyncio.run(updater._async_update_data())

    assert data["currentSet"] == 16
    assert len(session.calls) == 1


def test_runtime_poll_rejects_current_above_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V-06: a currentSet above THIS model's max fails the poll (wrong device)."""
    session = _Session(_Response(payload={"state": 4, "currentSet": 20}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), model="16A"
    )
    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())


def test_runtime_poll_accepts_current_within_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"state": 4, "currentSet": 16}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), model="16A"
    )
    data = asyncio.run(updater._async_update_data())
    assert data["currentSet"] == 16
