"""Coordinator-backed network handling for Eveus integration."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
import logging
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .common_command import CommandManager
from .const import (
    CHARGING_STATES,
    CHARGING_UPDATE_INTERVAL,
    CONNECTED_STATES,
    DEFAULT_SCHEME,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_STANDBY,
    LEGACY_RAW_STATE_KEY,
    DEVICE_STATE_ERROR,
    ERROR_LOG_RATE_LIMIT,
    EVENT_CAR_CONNECTED,
    EVENT_CAR_DISCONNECTED,
    EVENT_CHARGING_FINISHED,
    EVENT_CHARGING_STARTED,
    EVENT_ERROR,
    FINISHED_REASONS,
    get_error_state,
    IDLE_UPDATE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL,
    PLUG_UNKNOWN_STATES,
    SESSION_ACTIVE_STATES,
    UPDATE_TIMEOUT,
)
from ._payload import PayloadError, read_json_capped, validate_main_payload
from .utils import RateLog, get_safe_value

_UPDATE_TIMEOUT_OBJ: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=UPDATE_TIMEOUT)

# Sequence of refreshes after a successful command. Covers both fast
# commits (e.g. Charging Current — applied immediately, visible at 3 s)
# and slow state transitions (Stop Charging off + One Charge on — the
# contactor typically closes 5-15 s after the command, so a single early
# poll catches the device still in Standby/Connected and the coordinator
# then reverts to the 60 s idle cadence, hiding the real transition for
# almost a minute).
POST_COMMAND_REFRESH_DELAYS: tuple[int, ...] = (3, 10, 20)

# Minimum gap between transition-triggered poll bursts. A state transition
# observed between two scheduled polls (schedule/charger-UI/OCPP-started
# session, fault, unplug) triggers the same short refresh burst as a command,
# so external changes surface quickly without raising idle traffic. The gap
# stops a flapping state from keeping the coordinator in a permanent burst.
TRANSITION_BURST_MIN_GAP: float = 30.0

_LOGGER = logging.getLogger(__name__)
_CHARGING_INTERVAL = timedelta(seconds=CHARGING_UPDATE_INTERVAL)
_IDLE_INTERVAL = timedelta(seconds=IDLE_UPDATE_INTERVAL)
_OFFLINE_INTERVAL = timedelta(seconds=OFFLINE_UPDATE_INTERVAL)
_UPDATE_INTERVALS = {
    CHARGING_UPDATE_INTERVAL: _CHARGING_INTERVAL,
    IDLE_UPDATE_INTERVAL: _IDLE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL: _OFFLINE_INTERVAL,
}

# Offline backoff is deliberately flat and short: powering the charger off
# between sessions is a normal workflow, so a returning charger must show up
# within one OFFLINE_UPDATE_INTERVAL tick (worst case 60 s). The deadline only
# dedupes extra attempts inside a cycle (e.g. burst refreshes landing during an
# outage); it never defers past the next scheduled tick. It also bounds the
# skip check, so a backward wall-clock step (which would otherwise leave the
# deadline far in the future) can't strand the charger as unavailable: a
# remaining wait beyond this means the clock moved.
_MAX_OFFLINE_BACKOFF = min(30, OFFLINE_UPDATE_INTERVAL // 2)


def _looks_charging_from_measurements(data: dict[str, Any]) -> bool:
    """Electrical-measurement signal that a session is actually delivering power.

    Used for firmware whose state codes can't answer the question themselves
    (firmware 1.x / MCU_SW_version 151, GitHub issue #11): unmapped states in
    the adaptive-interval fallback, and the legacy code 3 in
    ``normalize_legacy_device_state`` (1.x reports 3 both plugged-idle and
    actively charging). Modern known states 0-7 always decide charging
    activity from the state value itself and never reach this helper.
    """
    power = get_safe_value(data, "powerMeas", float)
    current = get_safe_value(data, "curMeas1", float)
    return (power is not None and power > 0) or (current is not None and current > 0)


# Firmware-1.x device-state codes with a different meaning than the modern
# 0-7 map (GitHub issue #11): 20 is that firmware's idle/standby code, and 3
# is reported during active charging (modern firmware: 3 = connected-idle,
# 4 = charging). Verified against live captures from MCU_SW_version 151.
_LEGACY_IDLE_STATE = 20
_LEGACY_CHARGING_CANDIDATE_STATE = 3


def normalize_legacy_device_state(data: dict[str, Any]) -> dict[str, Any]:
    """Translate firmware-1.x state codes to their modern equivalents.

    Firmware 1.x payloads are recognizable by the complete absence of the
    ``verFWMain``/``firmware`` fields (modern firmware always sends
    ``verFWMain``), so modern payloads pass through untouched — the gate, not
    the code values, is what guarantees no behavior change for current
    hardware. Translating once, right after validation, lets every consumer
    (state sensor, session-active logic, adaptive polling, transition events)
    work on the canonical 0-7 domain with no per-call-site special cases.

    Only the two observed codes are translated: 20 -> Standby, and 3 ->
    Charging when the electrical measurements confirm power is flowing
    (without power, 3 keeps its modern "Connected" reading, which matches a
    plugged-idle car on either firmware generation). The original code is
    preserved under LEGACY_RAW_STATE_KEY for the State sensor's `raw_state`
    diagnostic attribute. Codes this firmware may use that we have not seen
    yet stay untranslated and render as "Unknown".
    """
    if data.get("verFWMain") or data.get("firmware"):
        return data
    state = get_safe_value(data, "state", int)
    if state == _LEGACY_IDLE_STATE:
        data[LEGACY_RAW_STATE_KEY] = state
        data["state"] = DEVICE_STATE_STANDBY
    elif state == _LEGACY_CHARGING_CANDIDATE_STATE and _looks_charging_from_measurements(
        data
    ):
        data[LEGACY_RAW_STATE_KEY] = state
        data["state"] = DEVICE_STATE_CHARGING
    return data


class EveusUpdater(DataUpdateCoordinator[dict[str, Any]]):
    """Data coordinator for an Eveus charger."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        hass: HomeAssistant,
        scheme: str = DEFAULT_SCHEME,
        config_entry: ConfigEntry | None = None,
        device_number: int | None = None,
        model: str | None = None,
    ) -> None:
        """Initialize updater."""
        # HA logs the coordinator name at ERROR/INFO level on poll failures, so
        # it must stay host-free to honor the host-redaction-in-logs guarantee.
        # The device number keeps multi-charger logs distinguishable instead.
        coordinator_name = (
            f"Eveus EV Charger {device_number}"
            if device_number is not None
            else "Eveus EV Charger"
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=coordinator_name,
            update_interval=_CHARGING_INTERVAL,
        )
        self.host = host
        self.scheme = scheme
        self.device_number = device_number
        # Configured charger model, so runtime polls reject a currentSet above
        # THIS model's maximum (a wrong-host/corrupt payload) instead of only the
        # largest supported model's maximum.
        self._model = model
        self._basic_auth = aiohttp.BasicAuth(username, password)
        self._command_manager = CommandManager(self)

        self._poll_results: deque[bool] = deque(maxlen=20)
        self._consecutive_failures = 0
        # 0.0 until the first successful poll, so connection_quality does not
        # report "healthy" before the charger has ever answered.
        self._last_success_time = 0.0
        # Monotonic twin of _last_success_time: ages must survive wall-clock
        # corrections (NTP, VM resume), which would otherwise freeze offline
        # detection and health reporting until wall time catches up.
        self._last_success_monotonic = 0.0
        self._latency_samples: deque[float] = deque(maxlen=10)
        self._connection_quality_cache: dict[str, Any] | None = None

        self._availability_log = RateLog()
        self._silent_mode = False
        self._offline_announced = False
        self._last_error: str | None = None
        self._device_available = True
        self._device_registry_finalized = False
        self._next_poll_attempt = 0.0
        # Successful polls still owed at the offline cadence after an outage,
        # so a single recovered tick can't snap straight back to fast polling.
        self._offline_probation = 0
        self._last_observed_state: int | None = None
        self._last_burst_monotonic: float | None = None
        # Transition-event memory, separate from the burst tracker above:
        # unlike it, this resets on every failed poll so transitions that
        # happened across an offline gap stay silent.
        self._event_prev_state: int | None = None
        self._event_prev_payload: dict[str, Any] | None = None
        self._force_refresh_requests = 0
        self._pending_refresh_unsubs: list = []
        self._post_command_refresh_tasks: list = []
        # Set once async_shutdown runs (entry unload / HA stop). Blocks a command
        # that completes mid-unload from scheduling fresh refresh timers, and a
        # just-fired timer from starting a refresh on a torn-down coordinator.
        self._shutting_down = False
        # Firmware-version /init fallback (GitHub issue #11): fw-1.x omits
        # verFWMain/firmware from /main entirely. Populated at most once, on
        # the first successful poll that lacks a firmware field; None means
        # either "not needed" (modern firmware) or "fetch failed/pending".
        self._init_fw_fallback: str | None = None
        self._init_fw_fetch_done = False

    @property
    def basic_auth(self) -> aiohttp.BasicAuth:
        """Cached Basic Auth credentials for charger requests."""
        return self._basic_auth

    @property
    def available(self) -> bool:
        """Return whether the most recent poll succeeded.

        Kept in sync with `last_update_success` via `_record_success` /
        `_record_failure` so the entity layer and HA's coordinator framework
        agree on reachability. Entities layer their own grace period on top
        of this in `BaseEveusEntity`.
        """
        return self._device_available

    @property
    def connection_quality(self) -> dict[str, Any]:
        """Connection metrics exposed for diagnostics and sensors."""
        if self._connection_quality_cache is not None:
            # is_healthy is time-dependent; recompute it on every read so a
            # cached snapshot can't keep reporting "healthy" while polling is
            # stalled past the freshness threshold.
            return {
                **self._connection_quality_cache,
                "is_healthy": self._is_healthy(
                    self._connection_quality_cache["success_rate"]
                ),
            }

        if self._poll_results:
            success_rate = (sum(self._poll_results) / len(self._poll_results)) * 100
        else:
            success_rate = 100.0
        avg_latency = (
            (sum(self._latency_samples) / len(self._latency_samples))
            if self._latency_samples
            else 0.0
        )
        self._connection_quality_cache = {
            "success_rate": success_rate,
            "latency_avg": avg_latency,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_command_failures": self._command_manager.consecutive_failures,
            "is_healthy": self._is_healthy(success_rate),
            "last_success_time": self._last_success_time,
            "last_error": self._last_error,
            "sample_count": len(self._poll_results),
        }
        return self._connection_quality_cache

    def _seconds_since_success(self) -> float:
        """Age of the last successful poll, immune to wall-clock jumps."""
        if self._last_success_monotonic <= 0:
            return float("inf")
        return time.monotonic() - self._last_success_monotonic

    def _is_healthy(self, success_rate: float) -> bool:
        """Whether the connection is currently considered healthy."""
        return (
            self._last_success_time > 0
            and success_rate > 80
            and self._seconds_since_success() < 300
        )

    @property
    def is_likely_offline(self) -> bool:
        """Check if the device appears to be powered off."""
        return self._consecutive_failures > 10 and self._seconds_since_success() > 600

    def get_session(self) -> aiohttp.ClientSession:
        """Get Home Assistant shared HTTP session."""
        return async_get_clientsession(self.hass)

    def url_for(self, path: str) -> str:
        """Build a charger URL with the configured transport."""
        return f"{self.scheme}://{self.host}{path}"

    async def send_command(
        self,
        command: str,
        value: Any,
        *,
        retry: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Send command to the device and schedule a delayed refresh on success."""
        if self._shutting_down:
            # Reject new/queued commands once unload has begun so a control or
            # background task can't mutate the charger after teardown.
            return False
        try:
            success = await self._command_manager.send_command(
                command, value, retry=retry, extra=extra
            )
        finally:
            # Invalidate even on the raising path (401 -> ConfigEntryAuthFailed)
            # so the cached consecutive_command_failures can't go stale.
            self._connection_quality_cache = None
        if success and not self._shutting_down:
            self._schedule_post_command_refresh()
        return success

    async def async_force_refresh(self) -> None:
        """Force an immediate refresh, bypassing offline-backoff skips.

        The bypass is scoped to this call via a counter rather than a single
        consumable flag, so a scheduled poll that interleaves with the forced
        refresh cannot steal the bypass and leave the forced poll skipped.
        """
        self._force_refresh_requests += 1
        try:
            await self.async_refresh()
        finally:
            self._force_refresh_requests -= 1

    def _schedule_post_command_refresh(self) -> None:
        """Schedule delayed refreshes after a successful command.

        Refreshes fire at POST_COMMAND_REFRESH_DELAYS to catch both fast
        commits (e.g. setting current) and slower transitions (Stop Charging
        off + One Charge on, where the contactor may take 5-15 s to close).
        A single early poll is not enough: it sees the device still idle and
        the coordinator reverts to the 60 s idle cadence, so the real
        CHARGING state is hidden for almost a minute. Multiple polls bracket
        the transition window so the coordinator snaps to the 30 s charging
        cadence as soon as the device actually transitions.

        Rapid toggles cancel ALL pending refreshes and reschedule, so refreshes
        always fire relative to the most recent command. A timer that has not
        fired yet is cancelled via its async_call_later unsub; a refresh that
        has already fired and is still in flight is run as a tracked task so it
        too can be cancelled on reschedule or shutdown -- otherwise a slow /main
        poll could complete after a newer command and publish stale data, or
        outlive async_shutdown. Combined with the entity-level optimistic state
        TTL this prevents stale-read flicker.
        """
        self._cancel_pending_refreshes()
        for delay in POST_COMMAND_REFRESH_DELAYS:
            async def _run(_now, _delay=delay):
                if self._shutting_down or self.hass is None or self.hass.is_stopping:
                    return
                task = asyncio.ensure_future(self.async_refresh())
                self._post_command_refresh_tasks.append(task)
                try:
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Post-command refresh failed", exc_info=True)
                finally:
                    if task in self._post_command_refresh_tasks:
                        self._post_command_refresh_tasks.remove(task)

            self._pending_refresh_unsubs.append(async_call_later(self.hass, delay, _run))

    def _pop_pending_refreshes(self) -> list:
        pending, self._pending_refresh_unsubs = self._pending_refresh_unsubs, []
        return pending

    def _cancel_pending_refreshes(self) -> None:
        for unsub in self._pop_pending_refreshes():
            unsub()
        # task.cancel() schedules each task's done-callback via the event loop,
        # so _post_command_refresh_tasks is not mutated during this loop —
        # iterate it directly rather than over a throwaway snapshot.
        # Never cancel the task this call is running inside: a tracked refresh
        # that observes a state transition reschedules the burst synchronously,
        # and cancelling itself would discard the payload it just fetched.
        try:
            current = asyncio.current_task()
        except RuntimeError:  # not inside a running event loop
            current = None
        for task in self._post_command_refresh_tasks:
            if task is not current and not task.done():
                task.cancel()

    async def async_shutdown(self) -> None:
        """Cancel any pending delayed refreshes and shut down."""
        self._shutting_down = True
        self._cancel_pending_refreshes()
        pending = list(self._post_command_refresh_tasks)
        self._post_command_refresh_tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await super().async_shutdown()

    def _should_log(self) -> bool:
        """Rate-limit availability logging."""
        return self._availability_log.should_log(ERROR_LOG_RATE_LIMIT)

    def _record_success(self, response_time: float, new_data: dict[str, Any]) -> None:
        """Record a successful poll and tune the next interval."""
        self._connection_quality_cache = None
        was_likely_offline = self.is_likely_offline
        self._poll_results.append(True)
        self._consecutive_failures = 0
        self._device_available = True
        self._next_poll_attempt = 0.0
        self._last_success_time = time.time()
        self._last_success_monotonic = time.monotonic()
        self._latency_samples.append(response_time)
        self._silent_mode = False
        self._offline_announced = False
        self._last_error = None
        # Two-success recovery probation: the first successes after a long
        # outage stay on the offline cadence so one lucky tick (a router blip
        # mid-outage) doesn't snap polling back to the fast cycle.
        if was_likely_offline:
            self._offline_probation = 2
        elif self._offline_probation:
            self._offline_probation -= 1
        self._tune_update_interval(
            new_data,
            preserve_offline=was_likely_offline or self._offline_probation > 0,
        )
        self._maybe_burst_on_transition(new_data)
        self._emit_transition_events(new_data)

    def _emit_transition_events(self, new_data: dict[str, Any]) -> None:
        """Fire bus events for device-state transitions between valid polls.

        Only consecutive successful polls count: `_record_failure` clears the
        memory, so a change that happened while the charger was unreachable
        (or while HA was down) never produces an event.
        """
        try:
            state = int(new_data.get("state")) if new_data.get("state") is not None else None
        except (TypeError, ValueError):
            state = None
        if state is None or state not in CHARGING_STATES:
            return
        previous, prev_payload = self._event_prev_state, self._event_prev_payload
        self._event_prev_state, self._event_prev_payload = state, new_data
        if previous is None or previous == state or self._shutting_down:
            return
        bus = getattr(self.hass, "bus", None)
        if bus is None:
            return
        base = {"device_number": self.device_number or 1}

        if state == DEVICE_STATE_CHARGING:
            bus.async_fire(EVENT_CHARGING_STARTED, dict(base))
        elif previous == DEVICE_STATE_CHARGING and state != DEVICE_STATE_ERROR:
            # Firmware resets session counters at the START of the next session,
            # not at charge end; snapshotting the last charging poll keeps the
            # numbers stable regardless of when the next session begins.
            snapshot = prev_payload or {}
            bus.async_fire(
                EVENT_CHARGING_FINISHED,
                {
                    **base,
                    "reason": FINISHED_REASONS.get(state, "stopped"),
                    "session_energy_kwh": get_safe_value(snapshot, "sessionEnergy", float),
                    "session_cost": get_safe_value(snapshot, "sessionMoney", float),
                    "session_duration_s": get_safe_value(snapshot, "sessionTime", int),
                },
            )

        if state == DEVICE_STATE_ERROR:
            code = get_safe_value(new_data, "subState", int)
            bus.async_fire(
                EVENT_ERROR,
                {
                    **base,
                    "error_code": code,
                    "error_text": get_error_state(code) if code is not None else None,
                },
            )

        # Plugged-in status is indeterminate in the Error state; only fire when
        # both sides of the transition are definite.
        if previous not in PLUG_UNKNOWN_STATES and state not in PLUG_UNKNOWN_STATES:
            was_connected = previous in CONNECTED_STATES
            is_connected = state in CONNECTED_STATES
            if was_connected != is_connected:
                bus.async_fire(
                    EVENT_CAR_CONNECTED if is_connected else EVENT_CAR_DISCONNECTED,
                    dict(base),
                )

    def _maybe_burst_on_transition(self, data: dict[str, Any]) -> None:
        """Briefly poll fast after an observed device state transition.

        Covers transitions HA did not initiate (schedules, the charger's own
        UI, OCPP). An invalid/missing state neither bursts nor clears the
        transition memory, so a glitched payload can't fabricate a transition
        on the next valid poll.
        """
        try:
            state = int(data.get("state")) if data.get("state") is not None else None
        except (TypeError, ValueError):
            state = None
        if state is None or state not in CHARGING_STATES:
            return
        previous, self._last_observed_state = self._last_observed_state, state
        if previous is None or previous == state or self._shutting_down:
            return
        now = time.monotonic()
        if (
            self._last_burst_monotonic is not None
            and now - self._last_burst_monotonic < TRANSITION_BURST_MIN_GAP
        ):
            return
        self._last_burst_monotonic = now
        self._schedule_post_command_refresh()

    def _record_failure(self, error: Exception) -> None:
        """Record a failed poll and tune retry cadence."""
        self._connection_quality_cache = None
        # Transitions across an offline gap must stay silent (see
        # _emit_transition_events); forget the last observed state.
        self._event_prev_state = None
        self._event_prev_payload = None
        self._poll_results.append(False)
        self._consecutive_failures += 1
        self._device_available = False
        self._last_error = "ValueError" if isinstance(error, PayloadError) else type(error).__name__
        # A failure during recovery probation restarts it: the two qualifying
        # successes must be consecutive, or an unstable link could reach the
        # fast cadence on alternating good/bad polls.
        if self._offline_probation:
            self._offline_probation = 2

        if self._consecutive_failures > 20:
            self._silent_mode = True

        if self.is_likely_offline:
            self._next_poll_attempt = time.time() + _MAX_OFFLINE_BACKOFF
            self._set_update_interval(OFFLINE_UPDATE_INTERVAL)
            if not self._offline_announced:
                _LOGGER.debug("Eveus device appears offline, reducing poll frequency")
                self._offline_announced = True

        if not self._silent_mode and self._should_log():
            _LOGGER.debug("Eveus connection issue: %s", type(error).__name__)

    def _tune_update_interval(
        self, data: dict[str, Any], *, preserve_offline: bool = False
    ) -> None:
        """Pick a poll cadence based on charger activity.

        An active session (Charging, or briefly Paused mid-session) is where
        users want fast feedback, so both get CHARGING_UPDATE_INTERVAL. When the
        charger is merely idle/connected we relax to IDLE_UPDATE_INTERVAL to
        halve background HTTP load, and snap back the moment a session resumes.

        ``preserve_offline`` keeps the long offline cadence for the first
        successful poll right after a long outage, so the coordinator does
        not snap straight back to a 30s cycle on a single recovered tick.
        """
        if preserve_offline or self.is_likely_offline:
            self._set_update_interval(OFFLINE_UPDATE_INTERVAL)
            return

        try:
            state_value = int(data.get("state")) if data.get("state") is not None else None
        except (TypeError, ValueError):
            state_value = None

        if state_value in SESSION_ACTIVE_STATES:
            self._set_update_interval(CHARGING_UPDATE_INTERVAL)
        elif state_value is not None and state_value in CHARGING_STATES:
            self._set_update_interval(IDLE_UPDATE_INTERVAL)
        elif state_value is not None and _looks_charging_from_measurements(data):
            # Firmware 1.x (MCU_SW_version 151, GitHub issue #11) reports device
            # states outside CHARGING_STATES (observed: 20), which the payload
            # validator now accepts rather than rejecting the whole poll. There
            # is no state-based signal for those firmwares, so fall back to the
            # electrical measurements: nonzero power/current means a session is
            # actually running and deserves the fast cadence. This branch is
            # unreachable for any state in CHARGING_STATES (0-7).
            self._set_update_interval(CHARGING_UPDATE_INTERVAL)
        else:
            # Unknown/invalid state with no sign of an active session: hold
            # offline cadence rather than snap to idle polling.
            self._set_update_interval(OFFLINE_UPDATE_INTERVAL)

    def _set_update_interval(self, seconds: int) -> None:
        """Apply a new poll interval if it differs from the current one."""
        new_interval = _UPDATE_INTERVALS.get(seconds)
        if new_interval is None:
            new_interval = timedelta(seconds=seconds)
        if self.update_interval != new_interval:
            self.update_interval = new_interval

    async def async_maybe_fetch_init_firmware(self) -> None:
        """Resolve a firmware-version fallback from /init, at most once.

        Firmware 1.x drops verFWMain (and the legacy `firmware` alias) from
        /main entirely (GitHub issue #11), so device_info's sw_version would
        stay "Unknown" forever. /init exposes the firmware as an integer --
        ESP_SW_version, falling back to MCU_SW_version -- e.g. 151, formatted
        here as "1.51".

        Intended to be awaited once, right after the coordinator's first
        successful refresh (see ``async_setup_entry``) -- not from the
        regular poll path, so a slow or hanging /init can never delay or fail
        the poll cycle every entity depends on. Gated by
        ``_init_fw_fetch_done`` so a modern charger with a real verFWMain
        never triggers a fetch at all, and a fw-1.x charger is only ever
        asked once. Every failure mode (timeout, non-JSON, missing/bad keys,
        HTTP error) degrades to leaving ``_init_fw_fallback`` unset (still
        "Unknown" in device_info) -- never raises, never fails setup.
        """
        if self._init_fw_fetch_done:
            return
        data = self.data if isinstance(self.data, dict) else {}
        if data.get("verFWMain") or data.get("firmware"):
            self._init_fw_fetch_done = True
            return
        self._init_fw_fetch_done = True
        try:
            async with self.get_session().post(
                self.url_for("/init"),
                auth=self._basic_auth,
                timeout=_UPDATE_TIMEOUT_OBJ,
            ) as response:
                response.raise_for_status()
                init_data = await read_json_capped(response)
        except (
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
        ) as err:
            _LOGGER.debug(
                "Eveus /init firmware fallback fetch failed: %s", type(err).__name__
            )
            return

        if not isinstance(init_data, dict):
            return
        raw_version = init_data.get("ESP_SW_version", init_data.get("MCU_SW_version"))
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            return
        self._init_fw_fallback = f"{raw_version / 100:.2f}"

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current device data."""
        start_time = time.time()
        # Latency is measured on the monotonic clock so a wall-clock step during
        # the request can't record a negative or absurd sample.
        start_monotonic = time.monotonic()
        bypass_backoff = self._force_refresh_requests > 0
        backoff_remaining = self._next_poll_attempt - start_time
        if 0 < backoff_remaining <= _MAX_OFFLINE_BACKOFF and not bypass_backoff:
            raise UpdateFailed("Skipping Eveus poll during offline backoff")

        try:
            async with self.get_session().post(
                self.url_for("/main"),
                auth=self._basic_auth,
                timeout=_UPDATE_TIMEOUT_OBJ,
            ) as response:
                if response.status == 401:
                    # An auth rejection is not a connectivity failure: don't
                    # feed the offline-backoff counters or connection-quality
                    # stats, or reauth recovery gets misattributed/deferred as
                    # "device offline". Just mark unavailable and hand off to
                    # HA's reauth flow.
                    self._connection_quality_cache = None
                    self._device_available = False
                    self._last_error = "ConfigEntryAuthFailed"
                    raise ConfigEntryAuthFailed("Invalid authentication")
                response.raise_for_status()

                new_data = await read_json_capped(response)
                # Shared validator retains the historical common-network guards:
                # "Eveus 'state' field is boolean" / "Eveus 'state' field is not finite".
                # Passing the configured model bounds currentSet to this charger's
                # maximum, so a wrong-device or corrupt payload fails the poll
                # rather than being published as healthy.
                new_data = validate_main_payload(new_data, self._model)
                new_data = normalize_legacy_device_state(new_data)

                self._record_success(time.monotonic() - start_monotonic, new_data)
                return new_data

        except ConfigEntryAuthFailed:
            raise
        except ValueError as err:
            self._record_failure(err)
            raise UpdateFailed(f"Invalid Eveus response: {type(err).__name__}") from err
        except (
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            self._record_failure(err)
            raise UpdateFailed(f"Eveus connection issue: {type(err).__name__}") from err
