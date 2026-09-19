"""Shared test helpers and lightweight dependency shims."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

TEST_HOST = "192.168.1.50"  # NOSONAR(python:S1313) - RFC 1918 LAN address, test fixture only
TEST_HOST_ALT = "192.168.1.55"  # NOSONAR(python:S1313) - RFC 1918 LAN address, test fixture only
TEST_BASE_URL = f"http://{TEST_HOST}"  # NOSONAR(python:S5332) - HTTP required by charger firmware; test fixture only
TEST_BASE_URL_ALT = f"http://{TEST_HOST_ALT}"  # NOSONAR(python:S5332) - HTTP required by charger firmware; test fixture only
TEST_USERNAME = "test_user"  # NOSONAR(python:S2068) - test fixture, not a real credential
TEST_PASSWORD = "test_password"  # NOSONAR(python:S2068) - test fixture, not a real credential
EV_HELPERS = {
    "input_number.ev_initial_soc": 20,
    "input_number.ev_battery_capacity": 80,
    "input_number.ev_soc_correction": 10,
    "input_number.ev_target_soc": 80,
}

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class HelperStates:
    """Small Home Assistant state registry fake for helper-entity tests."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self._values = values or {}
        self.calls: list[str] = []

    def get(self, entity_id: str) -> SimpleNamespace | None:
        self.calls.append(entity_id)
        value = self._values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=str(value))


class HelperHass:
    """Minimal hass object exposing only the states API used by helper sensors."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.states = HelperStates(values)


class StreamReaderStub:
    """Minimal aiohttp StreamReader stand-in for the capped body readers."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


def snapshot_of(updater: object) -> Any:
    """Parse a double's current payload the way the real coordinator does.

    The production coordinator parses once per successful poll and stores the
    result; a double is driven by assigning (or mutating) ``data``, so deriving
    the snapshot on each read is what keeps the two from ever disagreeing in a
    test the way they cannot disagree in production.
    """
    from custom_components.eveus.snapshot import EveusSnapshot

    data = getattr(updater, "data", None)
    return EveusSnapshot.parse(
        data if isinstance(data, dict) else {}, getattr(updater, "model", None)
    )


class SnapshotBackedMock(MagicMock):
    """MagicMock coordinator double whose ``snapshot`` follows ``data``."""

    @property
    def snapshot(self) -> Any:
        return snapshot_of(self)


class OutageClock:
    """The coordinator's outage clock, for updater doubles.

    Mirrors ``EveusUpdater``: flipping ``available`` to False anchors the outage
    on ``time.monotonic()`` (so a test that patches the clock drives the grace
    window), flipping it back clears it. A test may instead pin
    ``seconds_unavailable`` directly.
    """

    _available = True
    _first_failure: float | None = None
    _pinned_seconds_unavailable: float | None = None

    @property
    def available(self) -> bool:
        return self._available

    @available.setter
    def available(self, value: bool) -> None:
        import time

        if not value and self._first_failure is None:
            self._first_failure = time.monotonic()
        elif value:
            self._first_failure = None
            self._pinned_seconds_unavailable = None
        self._available = value

    @property
    def seconds_unavailable(self) -> float:
        import time

        if self._available:
            return 0.0
        if self._pinned_seconds_unavailable is not None:
            return self._pinned_seconds_unavailable
        return time.monotonic() - self._first_failure

    @seconds_unavailable.setter
    def seconds_unavailable(self, value: float) -> None:
        self._pinned_seconds_unavailable = value

    def visible_within(self, grace: int) -> bool:
        return self.seconds_unavailable < grace


class PayloadUpdater(OutageClock):
    """Availability flags plus a payload, with the snapshot derived on read.

    For the repair trackers and other listeners, which need nothing from a
    coordinator but "was this poll good" and "what did the charger say".
    """

    def __init__(
        self,
        data: object = None,
        *,
        available: bool = True,
        last_update_success: bool = True,
        connection_quality: dict | None = None,
        **extra: object,
    ) -> None:
        self.data = data
        self.available = available
        self.last_update_success = last_update_success
        self.connection_quality = connection_quality or {}
        # Drop-in for the SimpleNamespace doubles these tests used to build,
        # so any other attribute a caller sets (host, _session_time_seconds,
        # model, ...) still lands on the object.
        for name, value in extra.items():
            setattr(self, name, value)

    @property
    def snapshot(self) -> Any:
        return snapshot_of(self)


class EveusTestUpdater(OutageClock):
    """Reusable coordinator/updater fake for direct entity tests."""

    host = TEST_HOST
    last_update_success = True
    scheme = "http"

    def __init__(
        self,
        data: dict[str, object] | None = None,
        *,
        host: str = TEST_HOST,
        available: bool = True,
        scheme: str = "http",
        quality: dict[str, object] | None = None,
        model: str | None = None,
    ) -> None:
        self.host = host
        self.available = available
        self.scheme = scheme
        # Which charger this is: the snapshot bounds amp setpoints by the
        # model's design current, exactly as the real coordinator does.
        self.model = model
        self.data = data or {}
        self.connection_quality = quality or {}
        self.commands: list[tuple[str, object]] = []
        self.command_extras: list[dict[str, object] | None] = []
        self.command_result = True
        self.config_entry = SimpleNamespace(entry_id="entry-id", data={})

    @property
    def snapshot(self) -> Any:
        """Derived on read, so a test that mutates ``data`` stays consistent."""
        return snapshot_of(self)

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(
        self,
        command: str,
        value: object,
        *,
        retry: bool = True,
        extra: dict[str, object] | None = None,
    ) -> bool:
        self.commands.append((command, value))
        self.command_extras.append(extra)
        self.last_retry = retry
        self.last_extra = extra
        return self.command_result


def disable_state_writes(entity: object) -> None:
    """Replace HA state writes with a no-op for direct entity unit tests."""
    entity.async_write_ha_state = lambda: None


def spec_value_fn(key: str, *, phases: int = 1):
    """Return the production value_fn registered for a sensor spec key.

    No model maximum: a setpoint's model bound travels with the poll now (the
    coordinator knows which charger it is), so it is set on the updater double,
    not when the specs are built.
    """
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    return next(
        spec.value_fn
        for spec in create_sensor_specifications(phases=phases)
        if spec.key == key
    )


# =============================================================================
# Real-payload fixtures (single source of truth for /main shape)
#
# Synthetic dicts scattered across the suite drift from real firmware. These
# fixtures derive every payload from a verbatim live capture so a firmware-shape
# change is updated in ONE place. See test_real_payload_schema.py for the drift
# guard on the base snapshot.
# =============================================================================

_REAL_MAIN_FIXTURE = Path(__file__).parent / "fixtures" / "real_main_response.json"


def _load_real_main() -> dict[str, Any]:
    return json.loads(_REAL_MAIN_FIXTURE.read_text())


# Only the fields that define a charger lifecycle state. Overlaid on the real
# 102-field capture so each variant keeps the real schema while exercising
# state-gated logic (ETA, substate mapping, residual-power guards).
#
# state domain (const.CHARGING_STATES): 0 startup, 1 system test, 2 standby,
# 3 connected, 4 charging, 5 charge complete, 6 charging (paused/limit), 7 error.
# SESSION_ACTIVE_STATES = {4, 6}.
STATE_VARIANTS: dict[str, dict[str, Any]] = {
    "idle": {"state": 2, "subState": 0, "evseEnabled": 0, "curMeas1": 0, "powerMeas": 0},
    "connected": {"state": 3, "subState": 0, "evseEnabled": 0, "curMeas1": 0, "powerMeas": 0},
    "charging": {
        "state": 4, "subState": 0, "evseEnabled": 0,
        "curMeas1": 14.0, "powerMeas": 3200.0,
    },
    "complete": {"state": 5, "subState": 0, "evseEnabled": 0, "curMeas1": 0, "powerMeas": 0},
    "error": {"state": 7, "subState": 1, "evseEnabled": 1, "curMeas1": 0, "powerMeas": 0},
}


@pytest.fixture(scope="session")
def real_main() -> dict[str, Any]:
    """Verbatim live /main capture. Treat as read-only (copy before mutating)."""
    return _load_real_main()


@pytest.fixture
def main_payload() -> Callable[..., dict[str, Any]]:
    """Factory: real-shaped /main dict with field overrides.

    Usage: ``main_payload(state=4, currentSet=10)`` returns a deep copy of the
    real capture with those keys replaced. Keeps the full real field inventory so
    tests exercise production getters against an authentic schema.
    """
    base = _load_real_main()

    def _make(**overrides: Any) -> dict[str, Any]:
        payload = copy.deepcopy(base)
        payload.update(overrides)
        return payload

    return _make


@pytest.fixture(params=list(STATE_VARIANTS), ids=list(STATE_VARIANTS))
def main_state_variant(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Parametrized real-shaped payload for each charger lifecycle state."""
    payload = copy.deepcopy(_load_real_main())
    payload.update(STATE_VARIANTS[request.param])
    return payload


def wire_flow_reload_success(flow: Any, entry: Any, captured: dict | None = None) -> None:
    """Wire a bare ConfigFlow with a hass whose entry update is applied and
    whose awaited ``async_reload`` returns True.

    Matches the reconfigure/reauth contract: the flow updates the entry via
    ``async_update_entry`` and then awaits ``async_reload``, checking its
    result, instead of the fire-and-forget update-reload-and-abort helper.
    Update kwargs are recorded into ``captured`` and applied to the entry.
    """
    from unittest.mock import AsyncMock, Mock

    if not hasattr(entry, "entry_id"):
        entry.entry_id = "test-entry-id"

    def _apply_update(target: Any, **kwargs: Any) -> bool:
        if captured is not None:
            captured.update(kwargs)
        if "data" in kwargs:
            target.data = dict(kwargs["data"])
        if "unique_id" in kwargs:
            target.unique_id = kwargs["unique_id"]
        if "title" in kwargs:
            target.title = kwargs["title"]
        return True

    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=_apply_update),
            async_reload=AsyncMock(return_value=True),
        )
    )
    flow.async_abort = lambda reason: {"type": "abort", "reason": reason}


def serialize_schema(schema):
    """Serialize a flow schema the way the running Home Assistant does.

    HA 2026.9 replaced ``voluptuous_serialize.convert`` with
    ``probatio.to_field_list`` and dropped the old package from its
    dependencies; on that release the old converter returns HA's UNSUPPORTED
    sentinel for a selector instead of a field list. Prefer whichever
    serializer the installed HA actually uses, so the guard keeps testing the
    real frontend contract on both the supported floor and the ceiling.
    """
    import homeassistant.helpers.config_validation as cv

    try:
        from probatio import to_field_list
    except ImportError:
        import voluptuous_serialize

        return voluptuous_serialize.convert(
            schema, custom_serializer=cv.custom_serializer
        )
    return to_field_list(schema, custom_serializer=cv.custom_serializer)


@pytest.fixture(autouse=True)
def _loopless_hass_grace_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let loop-less hass stubs drive a failed poll.

    The coordinator schedules its outage wake-ups with ``async_call_later`` on
    the first failed poll. Unit tests build ``EveusUpdater`` on ``_Hass`` stubs
    whose ``loop`` is None; for those alone the timer becomes a no-op. A real
    hass, or a test that installs its own fake, is unaffected.
    """
    from custom_components.eveus import common_network

    real_call_later = common_network.async_call_later

    def _call_later(hass, delay, action):
        if getattr(hass, "loop", None) is None:
            return lambda: None
        return real_call_later(hass, delay, action)

    monkeypatch.setattr(common_network, "async_call_later", _call_later)
