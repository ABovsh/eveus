#!/usr/bin/env python3
"""Daily figures and outage windows from the developer probe's log.

DEVELOPER-ONLY (branch `reve`), removed with ``custom_components/eveus/dev_probe.py``.
Reads ``eveus_probe/<YYYY-MM>.jsonl`` from the Home Assistant config folder.

    python3 tools/reve_probe_report.py                      # last 7 days
    python3 tools/reve_probe_report.py --days 30            # trend
    python3 tools/reve_probe_report.py --since 2026-10-15 --windows
    python3 tools/reve_probe_report.py --windows --min-seconds 60
    python3 tools/reve_probe_report.py --sessions           # GraphQL vs /main while charging

An outage runs from an API's first failed request to its next success. When
the other API was down for most of the same time, the window is marked
"обидва": the charger or its Wi-Fi was gone, not one interface.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DIR = Path("/opt/ha/config/eveus_probe")
APIS = ("main", "graphql")
LABELS = {"main": "/main", "graphql": "GraphQL"}
_BOTH_SHARE = 0.5


@dataclass
class Window:
    api: str
    start: datetime
    end: datetime | None
    errors: Counter[str] = field(default_factory=Counter)
    both: bool = False

    @property
    def seconds(self) -> float:
        return 0.0 if self.end is None else (self.end - self.start).total_seconds()


@dataclass
class Day:
    requests: int = 0
    failures: int = 0
    latencies: list[int] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)


def read_records(folder: Path, since: date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.jsonl")):
        if path.stem < f"{since:%Y-%m}":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                record["_ts"] = datetime.fromisoformat(record["ts"])
            except (ValueError, KeyError, TypeError):
                continue
            if record["_ts"].date() >= since:
                records.append(record)
    records.sort(key=lambda record: record["_ts"])
    return records


def outage_windows(records: Iterable[dict[str, Any]]) -> list[Window]:
    """Runs of failures per API; a probe restart ends an open run at its last failure."""
    open_runs: dict[str, Window] = {}
    last_failure: dict[str, datetime] = {}
    windows: list[Window] = []
    for record in records:
        if record.get("event") == "start":
            for api, window in open_runs.items():
                window.end = last_failure[api]
                windows.append(window)
            open_runs.clear()
            continue
        api = record.get("api")
        if api not in APIS:
            continue
        ts = record["_ts"]
        if record.get("ok"):
            window = open_runs.pop(api, None)
            if window is not None:
                window.end = ts
                windows.append(window)
            continue
        last_failure[api] = ts
        window = open_runs.setdefault(api, Window(api, ts, None))
        window.errors[record.get("err") or "?"] += 1
    windows.extend(open_runs.values())  # still down at the end of the log
    _mark_both(windows)
    windows.sort(key=lambda window: window.start)
    return windows


def _mark_both(windows: list[Window]) -> None:
    by_api = defaultdict(list)
    for window in windows:
        by_api[window.api].append(window)
    for window in windows:
        end = window.end or window.start
        span = max((end - window.start).total_seconds(), 1.0)
        other = "graphql" if window.api == "main" else "main"
        shared = 0.0
        for peer in by_api[other]:
            peer_end = peer.end or peer.start
            overlap = (min(end, peer_end) - max(window.start, peer.start)).total_seconds()
            shared += max(overlap, 0.0)
        window.both = shared / span >= _BOTH_SHARE


def daily(records: Iterable[dict[str, Any]]) -> dict[date, dict[str, Day]]:
    days: dict[date, dict[str, Day]] = defaultdict(lambda: {api: Day() for api in APIS})
    for record in records:
        api = record.get("api")
        if api not in APIS:
            continue
        day = days[record["_ts"].date()][api]
        day.requests += 1
        if record.get("ok"):
            if isinstance(record.get("ms"), int):
                day.latencies.append(record["ms"])
        else:
            day.failures += 1
            day.errors[record.get("err") or "?"] += 1
    return days


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} с"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} хв {seconds:02d} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} год {minutes:02d} хв"


def _percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def render(records: list[dict[str, Any]], *, windows: bool, min_seconds: float) -> str:
    out: list[str] = []
    firmware = [record for record in records if record.get("event") == "firmware"]
    for record in firmware:
        out.append(f"Прошивка з {record['_ts']:%Y-%m-%d %H:%M}: {record.get('main')} / {record.get('wifi')}")
    if firmware:
        out.append("")

    found = outage_windows(records)
    per_day: dict[tuple[date, str], list[Window]] = defaultdict(list)
    for window in found:
        per_day[(window.start.date(), window.api)].append(window)

    out.append("День        API      запитів  збоїв  успіх   відмов  простій      найдовша     мс p50/p95/max   помилки")
    for day, apis in sorted(daily(records).items()):
        for api in APIS:
            figures = apis[api]
            if not figures.requests:
                continue
            day_windows = per_day[(day, api)]
            only = [window for window in day_windows if not window.both]
            down = sum(window.seconds for window in only)
            longest = max((window.seconds for window in only), default=0.0)
            success = 100 * (figures.requests - figures.failures) / figures.requests
            p50 = _percentile(figures.latencies, 0.5)
            p95 = _percentile(figures.latencies, 0.95)
            peak = max(figures.latencies, default=None)
            errors = ", ".join(f"{name}×{count}" for name, count in figures.errors.most_common(3))
            both = sum(1 for window in day_windows if window.both)
            latency = f"{p50}/{p95}/{peak}"
            together = f"  (+{both} разом з іншим API)" if both else ""
            out.append(
                f"{day}  {LABELS[api]:<8} {figures.requests:>7}  {figures.failures:>5}  {success:5.1f}%  "
                f"{len(only):>6}  {_duration(down):<12} {_duration(longest):<12} "
                f"{latency:<16} {errors}{together}"
            )
    out.append("")
    out.append("«відмов», «простій» і «найдовша» — без вікон, коли обидва API мовчали разом (зарядка або Wi-Fi недоступні).")

    if windows:
        out.append("")
        out.append("Вікна збоїв:")
        for window in found:
            if window.seconds < min_seconds and window.end is not None:
                continue
            end = "триває" if window.end is None else f"{window.end:%H:%M:%S}"
            errors = ", ".join(f"{name}×{count}" for name, count in window.errors.most_common())
            mark = "  обидва" if window.both else ""
            out.append(
                f"{window.start:%Y-%m-%d %H:%M:%S}–{end}  {LABELS[window.api]:<8} "
                f"{_duration(window.seconds):<12} {errors}{mark}"
            )
    return "\n".join(out)


def _fmt(value: Any, digits: int | None = None) -> str:
    if value is None:
        return "—"
    if digits is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.{digits}f}"
    return str(value)


def render_sessions(records: list[dict[str, Any]]) -> str:
    """The session as GraphQL and /main reported it, minute by minute."""
    out = [
        "Час               стан g/m  час g    час m    кВт·год g  кВт·год m  грн g   грн m   Вт g   Вт m"
    ]
    for record in records:
        if record.get("event") != "session":
            continue
        gql, main = record.get("gql") or {}, record.get("main") or {}
        out.append(
            f"{record['_ts']:%Y-%m-%d %H:%M}  "
            f"{_fmt(gql.get('state'))}/{_fmt(gql.get('sub'))} {_fmt(main.get('state'))}/{_fmt(main.get('sub'))}  "
            f"{_fmt(gql.get('time')):>7}  {_fmt(main.get('time')):>7}  "
            f"{_fmt(gql.get('energy'), 3):>9}  {_fmt(main.get('energy'), 3):>9}  "
            f"{_fmt(gql.get('cost'), 2):>6}  {_fmt(main.get('cost'), 2):>6}  "
            f"{_fmt(gql.get('power')):>5}  {_fmt(main.get('power')):>5}"
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--windows", action="store_true", help="list every outage window")
    parser.add_argument("--min-seconds", type=float, default=0, help="hide shorter windows")
    parser.add_argument("--sessions", action="store_true", help="GraphQL vs /main session, per minute")
    args = parser.parse_args()
    since = args.since or (date.today() - timedelta(days=args.days - 1))
    records = read_records(args.dir, since)
    if args.sessions:
        print(render_sessions(records))
        return
    print(render(records, windows=args.windows, min_seconds=args.min_seconds))


if __name__ == "__main__":
    main()
