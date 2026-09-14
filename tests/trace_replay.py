"""Replay a recorded charger timeline against the real coordinator and entities.

A trace is a JSON file under ``fixtures/traces``: ordered steps at elapsed
offsets, each an explicit poll result (``ok`` with telemetry overriding the
public base payload, or ``fail``) or a command issued through an entity. Time
is driven by the trace, never by sleeping, and no network is touched.
"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs

from custom_components.eveus import common_base, common_network, control_base, number
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.number import EveusCurrentNumber
from homeassistant.helpers.update_coordinator import UpdateFailed

FIXTURES = Path(__file__).parent / "fixtures"
_ALLOWED_KEYS = {
    "state", "subState", "evseEnabled", "powerMeas", "curMeas1", "currentSet",
    "logReady", "sessionEnergy", "sessionTime", "suspendLimits",
}


def load_trace(name: str) -> dict[str, Any]:
    trace = json.loads((FIXTURES / "traces" / name).read_text(encoding="utf-8"))
    if trace["source"] not in {"measured", "synthetic"}:
        raise ValueError("a trace must say whether it was measured or synthesized")
    for step in trace["steps"]:
        unknown = (set(step.get("main", {})) | set(step.get("drop", []))) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(f"trace replays keys outside the sanitized allowlist: {sorted(unknown)}")
    return trace


class _Response:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Type": "application/json"}
        self.content_length = len(body)

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    @property
    def content(self):
        from conftest import StreamReaderStub

        return StreamReaderStub(self._body)


class _TraceSession:
    def __init__(self) -> None:
        self.main: dict[str, Any] | None = None
        self.fail = False
        self.commands: list[dict[str, list[str]]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        if url.endswith("/pageEvent"):
            self.commands.append(parse_qs(kwargs["data"]))
            return _Response(200)
        if self.fail:
            raise asyncio.TimeoutError()
        return _Response(200, json.dumps(self.main).encode())


@dataclass
class Replay:
    updater: EveusUpdater
    entities: dict[str, Any]
    session: _TraceSession
    clock: dict[str, float]
    observed: list[dict[str, Any]] = field(default_factory=list)
    refresh_requests: int = 0


def replay(trace: dict[str, Any], monkeypatch) -> Replay:
    """Run every step and record entity values after each one."""
    clock = {"t": 10_000.0}
    fake_time = SimpleNamespace(monotonic=lambda: clock["t"], time=lambda: 1.7e9 + clock["t"])
    for module in (common_base, common_network, control_base, number):
        monkeypatch.setattr(module, "time", fake_time)
    session = _TraceSession()
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)

    updater = EveusUpdater("192.168.1.50", "u", "p", MagicMock(loop=None))  # NOSONAR(python:S1313) - RFC 1918 test fixture
    result = Replay(updater, {}, session, clock)

    def count_refresh() -> None:
        result.refresh_requests += 1

    updater._schedule_post_command_refresh = count_refresh
    current = EveusCurrentNumber(updater, "16A")
    current.async_write_ha_state = lambda: None
    result.entities["charging_current"] = current

    base = json.loads((FIXTURES / trace["base_payload"]).read_text(encoding="utf-8"))
    payload = copy.deepcopy(base)
    start = clock["t"]

    async def run() -> None:
        nonlocal payload
        for step in trace["steps"]:
            clock["t"] = start + step["at"]
            if "command" in step:
                entity = result.entities[step["command"]["entity"]]
                await entity.async_set_native_value(step["command"]["value"])
            elif step["poll"] == "ok":
                payload.update(step.get("main", {}))
                for key in step.get("drop", []):
                    payload.pop(key, None)
                session.main, session.fail = payload, False
                updater.data = await updater._async_update_data()
                updater.last_update_success = True
                _notify(result)
            else:
                session.fail = True
                updater._next_poll_attempt = 0
                try:
                    await updater._async_update_data()
                except UpdateFailed:
                    updater.last_update_success = False
                _notify(result)
            result.observed.append(
                {"at": step["at"], **{name: e.native_value for name, e in result.entities.items()}}
            )

    asyncio.run(run())
    return result


def _notify(result: Replay) -> None:
    for entity in result.entities.values():
        entity._handle_coordinator_update()
