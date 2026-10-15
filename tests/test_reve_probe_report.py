"""tools/reve_probe_report.py (branch `reve` only, never released).

Removed together with ``custom_components/eveus/dev_probe.py``. Reads a file
outside the package, so the mutation run ignores it (pyproject.toml).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

API_MAIN = "main"
API_GRAPHQL = "graphql"
_DAY = datetime(2026, 10, 15, 9, 0, 0)


def _at(seconds: float) -> datetime:
    return _DAY + timedelta(seconds=seconds)



# ---------------------------------------------------------------------------
# tools/reve_probe_report.py
# ---------------------------------------------------------------------------


def _report_module() -> Any:
    path = Path(__file__).parent.parent / "tools" / "reve_probe_report.py"
    spec = importlib.util.spec_from_file_location("reve_probe_report", path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rec(seconds: float, api: str | None = None, ok: bool = True, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"_ts": _at(seconds), **extra}
    if api is not None:
        record.update(api=api, ok=ok)
        if not ok:
            record.setdefault("err", "TimeoutError")
    return record


def test_report_windows_tell_one_api_from_both() -> None:
    report = _report_module()
    records = [
        _rec(0, API_MAIN), _rec(1, API_GRAPHQL),
        # GraphQL alone: 09:01-09:04.
        _rec(60, API_GRAPHQL, False, err="ClientResponseError 500"),
        _rec(120, API_GRAPHQL, False, err="ClientResponseError 500"),
        _rec(240, API_GRAPHQL),
        # Both together: the charger was gone.
        _rec(600, API_MAIN, False), _rec(601, API_GRAPHQL, False),
        _rec(900, API_MAIN), _rec(901, API_GRAPHQL),
    ]
    windows = report.outage_windows(records)

    assert [(w.api, w.seconds, w.both) for w in windows] == [
        (API_GRAPHQL, 180, False),
        (API_MAIN, 300, True),
        (API_GRAPHQL, 300, True),
    ]
    assert windows[0].errors == {"ClientResponseError 500": 2}


def test_report_a_restart_ends_an_open_window_at_its_last_failure() -> None:
    report = _report_module()
    records = [
        _rec(0, API_GRAPHQL, False), _rec(60, API_GRAPHQL, False),
        _rec(3600, event="start"),
        _rec(3660, API_GRAPHQL, False),  # still down after the restart: a new window
    ]
    windows = report.outage_windows(records)
    assert [(w.seconds, w.end is None) for w in windows] == [(60, False), (0, True)]


def test_report_renders_daily_figures(tmp_path: Path) -> None:
    report = _report_module()
    folder = tmp_path / "eveus_probe"
    folder.mkdir()
    lines = [
        {"ts": "2026-10-15T09:00:00+03:00", "event": "firmware", "main": "2.4.0", "wifi": "REVE_PLUS_1.0.0"},
        {"ts": "2026-10-15T09:00:00+03:00", "api": "main", "op": "poll", "ok": True, "ms": 300},
        {"ts": "2026-10-15T09:01:00+03:00", "api": "main", "op": "poll", "ok": False, "err": "TimeoutError"},
        {"ts": "2026-10-15T09:01:15+03:00", "api": "main", "op": "poll", "ok": True, "ms": 500},
    ]
    (folder / "2026-10.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    text = report.render(
        report.read_records(folder, datetime(2026, 10, 1).date()), windows=True, min_seconds=0
    )
    assert "Прошивка з 2026-10-15 09:00: 2.4.0 / REVE_PLUS_1.0.0" in text
    row = next(line for line in text.splitlines() if line.startswith("2026-10-15  /main"))
    assert row.split()[2:6] == ["3", "1", "66.7%", "1"]
    assert "2026-10-15 09:01:00–09:01:15  /main    15 с" in text
