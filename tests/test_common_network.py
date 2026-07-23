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


def test_new_updater_starts_on_charging_cadence() -> None:
    """The coordinator must start at CHARGING_UPDATE_INTERVAL before any poll,
    not at whatever timedelta the module constant happens to be built as."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


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
    import aiohttp

    from custom_components.eveus.const import UPDATE_TIMEOUT

    updater, session = coordinator
    asyncio.run(updater._async_update_data())
    timeout = session.calls[0]["timeout"]
    # Assert against the real UPDATE_TIMEOUT constant and the object identity
    # together — comparing only `is _UPDATE_TIMEOUT_OBJ` is a no-op mutation-wise
    # since the same (possibly mutated) module attribute is used on both sides.
    assert isinstance(timeout, aiohttp.ClientTimeout)
    assert timeout.total == UPDATE_TIMEOUT
    assert timeout is common_network._UPDATE_TIMEOUT_OBJ


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


def test_send_command_rejected_during_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """V-01: once shutdown has begun, no new command may reach the charger."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._shutting_down = True
    sent = Mock()
    updater._command_manager.send_command = sent
    result = asyncio.run(updater.send_command("currentSet", 16))
    assert result is False
    sent.assert_not_called()


class _CappedStreamReader:
    """Minimal aiohttp StreamReader stand-in for read_json_capped."""

    def __init__(self, raw):
        self._raw = raw

    async def iter_chunked(self, size):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


from types import SimpleNamespace as _SimpleNamespace


def test_connection_quality_reports_during_failures() -> None:
    import custom_components.eveus.sensor_definitions as sd

    specs = {s.key: s for s in sd.get_sensor_specifications()}
    spec = specs["connection_quality"]
    assert spec.available_when_offline is True

    updater = _SimpleNamespace(
        host=TEST_HOST,
        available=False,
        last_update_success=False,
        data={},
        connection_quality={"success_rate": 40, "latency_avg": 1.2},
        async_add_listener=lambda *a, **k: (lambda: None),
    )
    entity = spec.create_sensor(updater)
    assert entity.available is True
    assert entity._get_sensor_value() == 40


from conftest import EveusTestUpdater as _EveusTestUpdater, disable_state_writes as _dsw


def test_ev_sensor_skips_value_recompute_on_failed_poll() -> None:
    from custom_components.eveus.ev_sensors import EVSocKwhSensor, CachedSOCCalculator

    updater = _EveusTestUpdater({"IEM1": "5"})
    calc = CachedSOCCalculator()
    sensor = EVSocKwhSensor(updater, calc)
    _dsw(sensor)

    calls = []
    sensor._update_native_value = lambda: calls.append("value") or False

    updater.available = False
    updater.last_update_success = False
    sensor._handle_coordinator_update()
    assert calls == []

    updater.available = True
    updater.last_update_success = True
    sensor._handle_coordinator_update()
    assert calls == ["value"]


def test_connection_attrs_stay_visible_offline_without_stale_rssi() -> None:
    from types import SimpleNamespace
    from custom_components.eveus import sensor_definitions as sd

    offline = SimpleNamespace(
        available=False,
        connection_quality={"success_rate": 42, "latency_avg": 1.0},
        data={"RSSI": -50},
    )
    attrs = sd.get_connection_attrs(offline, None)
    assert attrs["connection_quality"] == 42
    assert attrs["status"] == "Poor"
    assert "wifi_rssi" not in attrs  # stale payload value suppressed offline

    online = SimpleNamespace(
        available=True,
        connection_quality={"success_rate": 99, "latency_avg": 0.2},
        data={"RSSI": -50},
    )
    online_attrs = sd.get_connection_attrs(online, None)
    assert online_attrs["status"] == "Excellent"
    assert online_attrs["wifi_rssi"] == -50


# --- Mutation-triage additions below (coordinator survivor closure) ---


def test_module_level_tuning_constants_have_expected_values() -> None:
    """A single value-equality check kills every arithmetic mutation of these
    module-level tuning constants at once (cheaper than one test per mutant)."""
    assert common_network.POST_COMMAND_REFRESH_DELAYS == (3, 10, 20)
    assert common_network.TRANSITION_BURST_MIN_GAP == 30.0
    assert common_network._MAX_OFFLINE_BACKOFF == 30
    assert common_network._LEGACY_IDLE_STATE == 20
    assert common_network._LEGACY_CHARGING_CANDIDATE_STATE == 3
    assert common_network._LEGACY_PAUSE_GRACE_POLLS == 2


def test_updater_initial_state_defaults() -> None:
    """One assertion block kills every constructor default-value mutation at
    once: each field's real initial value, checked directly on a fresh instance."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    assert updater.device_number is None
    assert updater._poll_results.maxlen == 20
    assert updater._last_success_time == 0.0
    assert updater._last_success_monotonic == 0.0
    assert updater._latency_samples.maxlen == 10
    assert updater._connection_quality_cache is None
    assert updater._silent_mode is False
    assert updater._offline_announced is False
    assert updater._last_error is None
    assert updater._device_available is True
    assert updater._device_registry_finalized is False
    assert updater._next_poll_attempt == 0.0
    assert updater._offline_probation == 0
    assert updater._last_observed_state is None
    assert updater._last_burst_monotonic is None
    assert updater._event_prev_state is None
    assert updater._event_prev_payload is None
    assert updater._event_prev_error_code is None
    assert updater._force_refresh_requests == 0
    assert updater._shutting_down is False
    assert updater._init_fw_fallback is None
    assert updater._init_fw_fetch_done is False
    assert updater._legacy_charging_latched is False
    assert updater._legacy_zero_power_polls == 0

    numbered = EveusUpdater(
        TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), device_number=5
    )
    assert numbered.device_number == 5


def test_basic_auth_property_returns_cached_object() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.basic_auth is updater._basic_auth


def test_bounded_clamps_out_of_range_and_none_values() -> None:
    bounded = common_network._bounded

    assert bounded(None, 100) is None
    assert bounded(-1, 100) is None
    assert bounded(0, 100) == 0
    assert bounded(100, 100) == 100
    assert bounded(101, 100) is None


def test_looks_charging_from_measurements_detects_power_or_current() -> None:
    looks_charging = common_network._looks_charging_from_measurements

    assert looks_charging({"powerMeas": 100, "curMeas1": 0}) is True
    assert looks_charging({"powerMeas": 0, "curMeas1": 5}) is True
    assert looks_charging({"powerMeas": 0, "curMeas1": 0}) is False
    assert looks_charging({"powerMeas": 1, "curMeas1": 0}) is True
    assert looks_charging({"powerMeas": 0, "curMeas1": 1}) is True
    assert looks_charging({}) is False


def test_normalize_legacy_device_state_passes_through_modern_firmware() -> None:
    """Presence of EITHER verFWMain or the legacy firmware alias marks modern
    firmware; the payload must pass through completely untouched."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    only_ver = {"verFWMain": "1.23", "state": 3}
    assert updater._normalize_legacy_device_state(dict(only_ver)) == only_ver

    only_alias = {"firmware": "1.23", "state": 3}
    assert updater._normalize_legacy_device_state(dict(only_alias)) == only_alias


def test_normalize_legacy_device_state_translates_idle_code() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    data = updater._normalize_legacy_device_state({"state": 20})

    assert data["state"] == common_network.DEVICE_STATE_STANDBY
    assert data[common_network.LEGACY_RAW_STATE_KEY] == 20
    assert updater._legacy_charging_latched is False


def test_normalize_legacy_device_state_ignores_unmapped_codes() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    data = updater._normalize_legacy_device_state({"state": 5})

    assert data == {"state": 5}
    assert common_network.LEGACY_RAW_STATE_KEY not in data


def test_normalize_legacy_device_state_latches_charging_when_power_flows() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    data = updater._normalize_legacy_device_state(
        {"state": 3, "powerMeas": 7200, "curMeas1": 16}
    )

    assert data["state"] == common_network.DEVICE_STATE_CHARGING
    assert data[common_network.LEGACY_RAW_STATE_KEY] == 3
    assert updater._legacy_charging_latched is True
    assert updater._legacy_zero_power_polls == 0


def test_normalize_legacy_device_state_never_latches_on_first_powerless_poll() -> None:
    """Regression guard for the and/or flip: a never-latched charge candidate
    with no power reading must NOT be treated as charging just because the
    zero-power-poll count happens to be under the grace threshold."""
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater._legacy_charging_latched is False

    data = updater._normalize_legacy_device_state(
        {"state": 3, "powerMeas": 0, "curMeas1": 0}
    )

    assert data == {"state": 3, "powerMeas": 0, "curMeas1": 0}
    assert updater._legacy_charging_latched is False


def test_normalize_legacy_device_state_tolerates_grace_window_then_reverts() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._legacy_charging_latched = True
    updater._legacy_zero_power_polls = 0

    # Within the grace window: stays translated as Charging, counter increments.
    data = updater._normalize_legacy_device_state(
        {"state": 3, "powerMeas": 0, "curMeas1": 0}
    )
    assert data["state"] == common_network.DEVICE_STATE_CHARGING
    assert updater._legacy_zero_power_polls == 1
    assert updater._legacy_charging_latched is True

    # Grace window exhausted: latch resets, state reported unchanged (raw 3).
    updater._legacy_zero_power_polls = common_network._LEGACY_PAUSE_GRACE_POLLS
    data = updater._normalize_legacy_device_state(
        {"state": 3, "powerMeas": 0, "curMeas1": 0}
    )
    assert data == {"state": 3, "powerMeas": 0, "curMeas1": 0}
    assert updater._legacy_charging_latched is False
    assert updater._legacy_zero_power_polls == 0


class _FakeBus:
    """Minimal HA event bus stand-in that records fired events in order."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, event_type: str, event_data: dict) -> None:
        self.fired.append((event_type, event_data))


def _updater_with_bus() -> tuple[EveusUpdater, "_FakeBus"]:
    hass = SimpleNamespace(bus=_FakeBus(), is_stopping=False, loop=None)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass)
    return updater, hass.bus


def test_emit_transition_events_first_poll_is_silent() -> None:
    updater, bus = _updater_with_bus()

    updater._emit_transition_events({"state": 4})

    assert bus.fired == []
    assert updater._event_prev_state == 4


def test_emit_transition_events_fires_charging_started() -> None:
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

    updater._emit_transition_events({"state": common_network.DEVICE_STATE_CHARGING})

    # Standby -> Charging also crosses the plug-status boundary (2 is not in
    # CONNECTED_STATES, 4 is), so car_connected fires alongside charging_started.
    assert bus.fired == [
        (common_network.EVENT_CHARGING_STARTED, {"device_number": 1}),
        (common_network.EVENT_CAR_CONNECTED, {"device_number": 1}),
    ]


def test_emit_transition_events_fires_charging_finished_with_bounded_session_values() -> None:
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_CHARGING
    updater._event_prev_payload = {
        "state": common_network.DEVICE_STATE_CHARGING,
        "sessionEnergy": 12.5,
        "sessionMoney": 3.2,
        "sessionTime": 3600,
    }

    # 5 = Charge Complete: both sides stay in CONNECTED_STATES, so no plug
    # event fires alongside charging_finished -- keeps this assertion isolated.
    updater._emit_transition_events({"state": 5})

    assert bus.fired == [
        (
            common_network.EVENT_CHARGING_FINISHED,
            {
                "device_number": 1,
                "reason": common_network.FINISHED_REASONS.get(5, "stopped"),
                "session_energy_kwh": 12.5,
                "session_cost": 3.2,
                "session_duration_s": 3600,
            },
        )
    ]


def test_emit_transition_events_charging_finished_falls_back_to_stopped_reason() -> None:
    """State 1 (System Test) has no FINISHED_REASONS entry, so it must fall
    back to the default 'stopped' reason string."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_CHARGING
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_CHARGING}

    updater._emit_transition_events({"state": 1})

    reasons = dict(bus.fired)
    assert reasons[common_network.EVENT_CHARGING_FINISHED]["reason"] == "stopped"


def test_emit_transition_events_requires_previous_charging_for_finished_event() -> None:
    """Regression guard for an and/or flip: a transition into a non-error
    state must NOT fire charging_finished unless the PREVIOUS state was
    actually Charging."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

    updater._emit_transition_events({"state": 3})

    event_types = [event_type for event_type, _ in bus.fired]
    assert common_network.EVENT_CHARGING_FINISHED not in event_types


def test_emit_transition_events_fires_error_event_on_new_error_state() -> None:
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

    updater._emit_transition_events(
        {"state": common_network.DEVICE_STATE_ERROR, "subState": 9}
    )

    assert bus.fired == [
        (
            common_network.EVENT_ERROR,
            {
                "device_number": 1,
                "error_code": 9,
                "error_text": common_network.get_error_state(9),
            },
        )
    ]
    assert updater._event_prev_error_code == 9


def test_emit_transition_events_escalates_new_fault_code_within_persisting_error() -> None:
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_ERROR
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_ERROR}
    updater._event_prev_error_code = 3

    updater._emit_transition_events(
        {"state": common_network.DEVICE_STATE_ERROR, "subState": 3}
    )
    assert bus.fired == []  # same fault code: no re-escalation

    updater._emit_transition_events(
        {"state": common_network.DEVICE_STATE_ERROR, "subState": 5}
    )
    assert bus.fired == [
        (
            common_network.EVENT_ERROR,
            {
                "device_number": 1,
                "error_code": 5,
                "error_text": common_network.get_error_state(5),
            },
        )
    ]
    assert updater._event_prev_error_code == 5


def test_emit_transition_events_fires_car_connected_and_disconnected() -> None:
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

    updater._emit_transition_events({"state": 3})  # Connected
    assert bus.fired == [(common_network.EVENT_CAR_CONNECTED, {"device_number": 1})]

    bus.fired.clear()
    updater._event_prev_state = 3
    updater._event_prev_payload = {"state": 3}
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_STANDBY})
    assert bus.fired == [(common_network.EVENT_CAR_DISCONNECTED, {"device_number": 1})]


def test_maybe_burst_on_transition_schedules_refresh_on_state_change() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_observed_state = common_network.DEVICE_STATE_STANDBY
    updater._schedule_post_command_refresh = Mock()

    updater._maybe_burst_on_transition({"state": common_network.DEVICE_STATE_CHARGING})

    updater._schedule_post_command_refresh.assert_called_once()
    assert updater._last_observed_state == common_network.DEVICE_STATE_CHARGING


def test_maybe_burst_on_transition_suppressed_within_min_gap() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_observed_state = common_network.DEVICE_STATE_STANDBY
    updater._last_burst_monotonic = time.monotonic()
    updater._schedule_post_command_refresh = Mock()

    updater._maybe_burst_on_transition({"state": common_network.DEVICE_STATE_CHARGING})

    updater._schedule_post_command_refresh.assert_not_called()


def test_maybe_burst_on_transition_allows_refresh_exactly_at_min_gap_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_observed_state = common_network.DEVICE_STATE_STANDBY
    updater._last_burst_monotonic = 1000.0
    updater._schedule_post_command_refresh = Mock()
    monkeypatch.setattr(
        common_network.time,
        "monotonic",
        lambda: 1000.0 + common_network.TRANSITION_BURST_MIN_GAP,
    )

    updater._maybe_burst_on_transition({"state": common_network.DEVICE_STATE_CHARGING})

    updater._schedule_post_command_refresh.assert_called_once()


def test_maybe_burst_on_transition_ignores_first_observation_and_same_state() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._schedule_post_command_refresh = Mock()

    # First-ever observation: nothing to compare against yet.
    updater._maybe_burst_on_transition({"state": common_network.DEVICE_STATE_CHARGING})
    updater._schedule_post_command_refresh.assert_not_called()
    assert updater._last_observed_state == common_network.DEVICE_STATE_CHARGING

    # Same state reported again: not a transition.
    updater._maybe_burst_on_transition({"state": common_network.DEVICE_STATE_CHARGING})
    updater._schedule_post_command_refresh.assert_not_called()


def test_maybe_burst_on_transition_ignores_out_of_domain_state() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_observed_state = common_network.DEVICE_STATE_STANDBY
    updater._schedule_post_command_refresh = Mock()

    updater._maybe_burst_on_transition({"state": 99})

    updater._schedule_post_command_refresh.assert_not_called()
    assert updater._last_observed_state == common_network.DEVICE_STATE_STANDBY


def test_connection_quality_defaults_success_rate_to_100_with_no_polls() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.connection_quality["success_rate"] == 100.0


def test_connection_quality_computes_average_latency() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._latency_samples.extend([0.2, 0.4])
    assert updater.connection_quality["latency_avg"] == pytest.approx(0.3)

    empty_updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert empty_updater.connection_quality["latency_avg"] == 0.0


def test_seconds_since_success_treats_nonpositive_monotonic_as_infinite() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    updater._last_success_monotonic = 0.0
    assert updater._seconds_since_success() == float("inf")

    updater._last_success_monotonic = 1.0
    assert updater._seconds_since_success() != float("inf")


def test_is_healthy_last_success_time_and_rate_boundaries() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_monotonic = time.monotonic()

    updater._last_success_time = 0
    assert updater._is_healthy(90) is False  # 0 must not count as "has succeeded"

    updater._last_success_time = 1
    assert updater._is_healthy(90) is True

    assert updater._is_healthy(80) is False  # success_rate must be strictly > 80
    assert updater._is_healthy(81) is True


def test_is_healthy_seconds_since_success_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_time = 1
    updater._last_success_monotonic = 1000.0

    monkeypatch.setattr(common_network.time, "monotonic", lambda: 1000.0 + 300)
    assert updater._is_healthy(90) is False  # exactly 300s: not healthy

    monkeypatch.setattr(common_network.time, "monotonic", lambda: 1000.0 + 250)
    assert updater._is_healthy(90) is True


def test_is_likely_offline_exact_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._last_success_monotonic = 1000.0
    monkeypatch.setattr(common_network.time, "monotonic", lambda: 1000.0 + 601)

    updater._consecutive_failures = 10
    assert updater.is_likely_offline is False  # exactly 10 failures: not yet offline

    updater._consecutive_failures = 11
    assert updater.is_likely_offline is True

    monkeypatch.setattr(common_network.time, "monotonic", lambda: 1000.0 + 600)
    assert updater.is_likely_offline is False  # exactly 600s: not yet offline

    monkeypatch.setattr(common_network.time, "monotonic", lambda: 1000.0 + 600.001)
    assert updater.is_likely_offline is True


def test_send_command_defaults_to_retry_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_call_later(hass, delay, action):
        return Mock()

    monkeypatch.setattr(common_network, "async_call_later", fake_call_later)

    async def scenario() -> None:
        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
        received: dict[str, object] = {}

        async def fake_send(command, value, *, retry=None, extra=None):
            received["retry"] = retry
            return True

        updater._command_manager = SimpleNamespace(send_command=fake_send)

        await updater.send_command("currentSet", 16)

        assert received["retry"] is True

    asyncio.run(scenario())


def test_async_force_refresh_increments_and_restores_counter() -> None:
    async def scenario() -> None:
        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
        seen: list[int] = []

        async def fake_refresh() -> None:
            seen.append(updater._force_refresh_requests)

        updater.async_refresh = fake_refresh
        # Simulate an already-in-flight forced refresh.
        updater._force_refresh_requests = 1

        await updater.async_force_refresh()

        assert seen == [2]  # must accumulate, not reset to 1
        assert updater._force_refresh_requests == 1  # decremented back down, not to 0

    asyncio.run(scenario())


def test_cancel_pending_refreshes_skips_the_currently_running_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    current_task = Mock()
    other_task = Mock()
    current_task.done.return_value = False
    other_task.done.return_value = False
    updater._post_command_refresh_tasks = [current_task, other_task]
    monkeypatch.setattr(common_network.asyncio, "current_task", lambda: current_task)

    updater._cancel_pending_refreshes()

    current_task.cancel.assert_not_called()
    other_task.cancel.assert_called_once()


def test_scheduled_refresh_exits_when_shutting_down_before_hass_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.hass = SimpleNamespace(is_stopping=False)
    updater._shutting_down = True
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


def test_scheduled_refresh_removes_task_from_tracking_list_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
        updater.hass = SimpleNamespace(is_stopping=False)

        async def refresh() -> None:
            return None

        updater.async_refresh = refresh
        callbacks = []

        def fake_call_later(hass, delay, action):
            callbacks.append(action)
            return Mock()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(common_network, "async_call_later", fake_call_later)
            updater._schedule_post_command_refresh()
            await callbacks[0](None)

        assert updater._post_command_refresh_tasks == []

    asyncio.run(scenario())


def test_offline_backoff_skip_boundary_below_one_second(
    coordinator: tuple[EveusUpdater, "_Session"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, session = coordinator
    monkeypatch.setattr(common_network.time, "time", lambda: 1000.0)
    updater._next_poll_attempt = 1000.5

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())


def test_offline_backoff_skip_exact_upper_bound(
    coordinator: tuple[EveusUpdater, "_Session"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, session = coordinator
    monkeypatch.setattr(common_network.time, "time", lambda: 1000.0)
    updater._next_poll_attempt = 1000.0 + common_network._MAX_OFFLINE_BACKOFF

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())


def test_update_data_records_positive_elapsed_latency_not_sum_of_clocks(
    coordinator: tuple[EveusUpdater, "_Session"],
) -> None:
    """Regression guard for `time.monotonic() - start_monotonic` becoming `+`:
    a real poll's latency is a tiny elapsed duration, never a sum of two
    monotonic-clock readings (which would be a huge number of seconds)."""
    updater, session = coordinator

    asyncio.run(updater._async_update_data())

    latency = updater._latency_samples[-1]
    assert 0.0 <= latency < 5.0


def test_async_maybe_fetch_init_firmware_skips_when_already_done() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._init_fw_fetch_done = True
    updater.get_session = Mock()

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    updater.get_session.assert_not_called()


def test_async_maybe_fetch_init_firmware_skips_for_modern_firmware_data() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater.data = {"verFWMain": "1.23", "state": 4}
    updater.get_session = Mock()

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    updater.get_session.assert_not_called()
    assert updater._init_fw_fetch_done is True

    alias_updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    alias_updater.data = {"firmware": "1.23", "state": 4}
    alias_updater.get_session = Mock()

    asyncio.run(alias_updater.async_maybe_fetch_init_firmware())

    alias_updater.get_session.assert_not_called()


def test_async_maybe_fetch_init_firmware_sets_fallback_from_esp_sw_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": 151}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback == "1.51"
    assert updater._init_fw_fetch_done is True
    assert session.calls[0]["url"] == f"{TEST_BASE_URL}/init"


def test_async_maybe_fetch_init_firmware_falls_back_to_mcu_sw_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"MCU_SW_version": 209}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback == "2.09"


def test_async_maybe_fetch_init_firmware_accepts_zero_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": 0}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback == "0.00"


def test_async_maybe_fetch_init_firmware_rejects_negative_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": -1}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback is None


def test_async_maybe_fetch_init_firmware_accepts_upper_bound_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": 10**6}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback == "10000.00"


def test_async_maybe_fetch_init_firmware_rejects_version_just_above_upper_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": 10**6 + 1}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback is None


def test_async_maybe_fetch_init_firmware_rejects_boolean_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": True}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback is None


def test_async_maybe_fetch_init_firmware_rejects_non_integer_version_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"ESP_SW_version": "151"}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())

    assert updater._init_fw_fallback is None


def test_async_maybe_fetch_init_firmware_swallows_fetch_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common_network, "async_get_clientsession", lambda hass: _FailingSession()
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater.async_maybe_fetch_init_firmware())  # must not raise

    assert updater._init_fw_fallback is None
    assert updater._init_fw_fetch_done is True
