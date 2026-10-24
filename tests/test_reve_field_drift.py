"""REVE: a firmware that stops reporting a field the integration reads.

REVE's GraphQL server answers a field it does not know with nothing at all
(200, the key simply absent — checked 2026-10-16), so a field renamed by a
firmware update would leave its entities quietly without a value. Every
measurements read therefore checks that each field it asked for came back;
a field missing from three reads in a row raises a repair notice naming it,
and the notice clears on the first read that has every field again. A field
that comes back as ``null`` (a session limit that is off) is reported, not
missing.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import custom_components.eveus as eveus_init
from custom_components.eveus import reve
from custom_components.eveus.issues import reve_fields_issue_id, update_reve_fields_issue
from test_reve_firmware import _Charger, _clock, _reve_main, _updater


def _known_reve(monkeypatch: pytest.MonkeyPatch, charger: _Charger):
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    return updater


def _poll(updater, now: list[float], times: int = 1) -> None:
    for _ in range(times):
        now[0] += reve.MEASUREMENTS_RETRY_SECONDS
        asyncio.run(updater._async_update_data())


def test_a_field_missing_from_three_reads_in_a_row_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    assert updater.reve_missing_fields == ()

    del charger.station["measurements"]["powerMeasurement"]
    del charger.station["RSSI"]
    _poll(updater, now, reve.FIELDS_MISSING_READS - 1)
    assert updater.reve_missing_fields == ()

    _poll(updater, now)
    assert updater.reve_missing_fields == ("RSSI", "measurements.powerMeasurement")
    assert updater.reve_diagnostics()["missing_fields"] == ["RSSI", "measurements.powerMeasurement"]


def test_a_returning_field_clears_the_report_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    power = charger.station["measurements"].pop("powerMeasurement")
    _poll(updater, now, reve.FIELDS_MISSING_READS)
    assert updater.reve_missing_fields

    charger.station["measurements"]["powerMeasurement"] = power
    _poll(updater, now)
    assert updater.reve_missing_fields == ()


def test_fields_missing_from_different_reads_are_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three reads that each lack a field, but not the same one, are no drift:
    the notice names only a field every one of the last reads lacked."""
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    rssi = charger.station.pop("RSSI")
    _poll(updater, now)
    charger.station["RSSI"] = rssi
    power = charger.station["measurements"].pop("powerMeasurement")
    _poll(updater, now)
    charger.station["measurements"]["powerMeasurement"] = power
    del charger.station["RSSI"]
    _poll(updater, now)
    assert updater.reve_missing_fields == ()


def test_only_the_field_missing_throughout_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    del charger.station["RSSI"]
    _poll(updater, now, reve.FIELDS_MISSING_READS - 1)
    del charger.station["measurements"]["powerMeasurement"]
    _poll(updater, now)
    assert updater.reve_missing_fields == ("RSSI",)
    _poll(updater, now, reve.FIELDS_MISSING_READS - 1)
    assert updater.reve_missing_fields == ("RSSI", "measurements.powerMeasurement")


def test_a_null_field_is_not_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    charger.station["session"]["energyLimit"] = None
    charger.station["ocpp"] = None  # a whole object reported as null
    updater = _known_reve(monkeypatch, charger)
    _poll(updater, now, reve.FIELDS_MISSING_READS)
    assert updater.reve_missing_fields == ()


def test_a_read_served_by_main_neither_counts_nor_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    del charger.station["RSSI"]
    _poll(updater, now, reve.FIELDS_MISSING_READS - 1)
    charger.measurements_status = 500
    _poll(updater, now)
    charger.measurements_status = 200
    _poll(updater, now)
    assert updater.reve_missing_fields == ("RSSI",)


def test_classic_firmware_reports_nothing_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_reve_firmware import _classic_main

    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.reve_missing_fields == ()


# ---------------------------------------------------------------------------
# The repair notice
# ---------------------------------------------------------------------------


class _Updater:
    def __init__(self, missing: tuple[str, ...], *, ok: bool = True, version: str = "REVE_PLUS_1.0.0") -> None:
        self.available = ok
        self.last_update_success = ok
        self.reve_missing_fields = missing
        self.data = {"verFWWifi": version}


def _registry(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, Any]], list[str]]:
    created: list[dict[str, Any]] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir, "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append({"issue_id": issue_id, **kw}),
    )
    monkeypatch.setattr(
        eveus_init.ir, "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    return created, deleted


def test_missing_fields_raise_a_notice_that_names_them(monkeypatch: pytest.MonkeyPatch) -> None:
    created, deleted = _registry(monkeypatch)
    entry = type("E", (), {"entry_id": "abc"})()

    update_reve_fields_issue(object(), entry, _Updater(("RSSI", "measurements.powerMeasurement")))

    assert [item["issue_id"] for item in created] == [reve_fields_issue_id(entry)]
    issue = created[0]
    assert issue["translation_key"] == "reve_fields_missing"
    assert issue["is_fixable"] is False
    assert issue["translation_placeholders"] == {
        "fields": "RSSI, measurements.powerMeasurement",
        "firmware": "REVE_PLUS_1.0.0",
    }
    assert not deleted


def test_no_missing_field_clears_the_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    created, deleted = _registry(monkeypatch)
    entry = type("E", (), {"entry_id": "abc"})()

    update_reve_fields_issue(object(), entry, _Updater(()))

    assert not created
    assert deleted == [reve_fields_issue_id(entry)]


def test_a_failed_poll_leaves_the_notice_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    created, deleted = _registry(monkeypatch)
    entry = type("E", (), {"entry_id": "abc"})()

    update_reve_fields_issue(object(), entry, _Updater(("RSSI",), ok=False))
    update_reve_fields_issue(object(), entry, _Updater((), ok=False))

    assert not created and not deleted


def test_the_notice_has_its_text_in_every_language() -> None:
    import json
    from pathlib import Path

    root = Path(eveus_init.__file__).parent
    for name in ("strings.json", "translations/en.json", "translations/uk.json"):
        issue = json.loads((root / name).read_text())["issues"]["reve_fields_missing"]
        assert "{fields}" in issue["description"], name
        assert "{firmware}" in issue["description"], name


def test_the_notice_is_filed_with_the_exact_registry_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.eveus.const import DOMAIN

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(eveus_init.ir, "async_create_issue", lambda *a, **kw: calls.append(("create", a, kw)))
    monkeypatch.setattr(eveus_init.ir, "async_delete_issue", lambda *a, **kw: calls.append(("delete", a, kw)))
    entry = type("E", (), {"entry_id": "abc"})()
    hass = object()

    update_reve_fields_issue(hass, entry, _Updater(("RSSI",)))
    update_reve_fields_issue(hass, entry, _Updater(()))

    (_, create_args, create_kw), (_, delete_args, _) = calls
    assert create_args == (hass, DOMAIN, reve_fields_issue_id(entry))
    assert create_kw["is_persistent"] is False
    assert create_kw["issue_domain"] == DOMAIN
    assert create_kw["severity"] is eveus_init.ir.IssueSeverity.WARNING
    assert delete_args == (hass, DOMAIN, reve_fields_issue_id(entry))


def test_either_half_of_a_failed_poll_leaves_the_notice_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    created, deleted = _registry(monkeypatch)
    entry = type("E", (), {"entry_id": "abc"})()
    for available, success in ((False, True), (True, False)):
        updater = _Updater(("RSSI",))
        updater.available, updater.last_update_success = available, success
        update_reve_fields_issue(object(), entry, updater)
    assert not created and not deleted


def test_an_unreported_firmware_reads_as_a_question_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    created, _ = _registry(monkeypatch)
    entry = type("E", (), {"entry_id": "abc"})()
    updater = _Updater(("RSSI",))
    updater.data = None
    update_reve_fields_issue(object(), entry, updater)
    assert created[0]["translation_placeholders"]["firmware"] == "?"
