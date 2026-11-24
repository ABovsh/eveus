"""REVE Leakage Current: the controller's processed slow leakage word, as the charger's display shows it.

REVE's ``/main`` ``leakValue``/``leakValueH`` are a startup ADC sample and a
constant. On REVE_PLUS 1.0.5 with controller GRM070A-R3.05.7 the processed word
the classic display prints (``word / 100`` mA) is ``serviceData.adc_plug_temperature``.
It is read only for that pair, as an information reading: no Home Assistant
warning or colour is derived from it.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.eveus import reve
from custom_components.eveus.safety import POLICIES, evaluate_policy_signals
from custom_components.eveus.snapshot import EveusSnapshot
from test_reve_firmware import _Charger, _Response, _reve_main, _updater

_PAIR = {"wifiFWVersion": "REVE_PLUS_1.0.5", "mainFWVersion": "GRM070A-R3.05.7 "}


class _LeakCharger(_Charger):
    """The fake REVE charger, also answering the ``serviceData`` root."""

    def __init__(self, *, word: Any = 33, service_status: int = 200, pair: bool = True) -> None:
        super().__init__(_reve_main())
        if pair:
            self.station.update(_PAIR)
        self.service: dict[str, Any] | None = {"adc_plug_temperature": word}
        self.service_status = service_status

    def post(self, url: str, **kwargs: Any) -> _Response:
        import json

        body = json.loads(kwargs["data"]) if url.endswith("/graphql") else None
        if body is not None and "serviceData" in body["query"]:
            self.calls.append(("/graphql", body))
            if self.service_status != 200:
                return _Response(self.service_status, "Server has encountered an unexpected error")
            reply = super().post(url, **kwargs)
            self.calls.pop()  # the parent recorded the same request
            data = dict(json.loads(reply._body)["data"])
            if self.service is not None:
                data["serviceData"] = self.service
            return _Response(200, {"data": data})
        return super().post(url, **kwargs)


def _known(monkeypatch: pytest.MonkeyPatch, charger: _Charger):
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    return updater


def _graphql_queries(charger: _Charger) -> list[str]:
    return [body["query"] for path, body in charger.calls if path == "/graphql"]


# ---------------------------------------------------------------------------
# The word and the pair
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [(0, 0), (1, 0), (33, 0), (99, 0), (100, 1), (101, 1), (299, 2), (420, 4), (421, 4), (65535, 655)],
)
def test_the_word_reads_as_the_display_shows_it(word: int, expected: int) -> None:
    assert reve.leakage_ma(word) == expected


@pytest.mark.parametrize("word", [65536, -1, True, False, 33.0, "33", None, [33], {"v": 33}])
def test_anything_but_a_16_bit_whole_number_is_no_reading(word: Any) -> None:
    assert reve.leakage_ma(word) is None


@pytest.mark.parametrize(
    ("wifi", "main", "expected"),
    [
        ("REVE_PLUS_1.0.5", "GRM070A-R3.05.7", True),
        ("REVE_PLUS_1.0.5", "GRM070A-R3.05.7 ", True),
        (" REVE_PLUS_1.0.5 ", "  GRM070A-R3.05.7", True),
        ("REVE_PLUS_1.0.3", "GRM070A-R3.05.7", False),
        ("REVE_PLUS_1.0.0", "GRM070A-R3.05.7", False),
        ("REVE_PLUS_1.0.5", "GRM070A-R3.05.70", False),
        ("REVE_PLUS_1.0.5", "GRP070A-R3.05.7", False),
        ("REVE_PLUS_1.0.5", "GRM070A-R3.05.4", False),
        ("REVE_PLUS_1.0.5", "          2.4.0 ", False),
        ("REVE_PLUS_1.0.5", None, False),
        (None, "GRM070A-R3.05.7", False),
        (105, "GRM070A-R3.05.7", False),
    ],
)
def test_only_the_verified_firmware_pair_reports_leakage(wifi: Any, main: Any, expected: bool) -> None:
    assert reve.leakage_pair({"wifiFWVersion": wifi, "mainFWVersion": main}) is expected


def test_the_leakage_documents_add_only_the_service_root() -> None:
    for plain, leak in (
        (reve.MEASUREMENTS_QUERY, reve.MEASUREMENTS_LEAK_QUERY),
        (reve.MEASUREMENTS_AND_SETTINGS_QUERY, reve.MEASUREMENTS_AND_SETTINGS_LEAK_QUERY),
    ):
        assert "serviceData" not in plain
        assert leak.startswith("query StationData")
        assert leak.replace(reve._LEAKAGE_ROOT, "") == plain
        assert "adc_plug_temperature" in leak


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


def test_the_verified_pair_shows_its_leakage(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger(word=250)
    updater = _known(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    queries = _graphql_queries(charger)
    assert len(queries) == 1 and "serviceData" in queries[0]
    assert charger.calls[0][1]["operationName"] == "StationData"
    assert data["leakValue"] == 2
    assert updater.snapshot.informational == frozenset({"leakValue"})
    assert updater.snapshot.supports("leakValue")
    assert not updater.snapshot.supports("leakValueH")


def test_the_first_poll_asks_for_leakage_only_once_the_pair_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger()
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert all("serviceData" not in query for query in _graphql_queries(charger))
    assert "leakValue" not in data
    assert not updater.snapshot.supports("leakValue")


@pytest.mark.parametrize("pair", [False])
def test_other_firmware_never_asks_for_leakage(monkeypatch: pytest.MonkeyPatch, pair: bool) -> None:
    charger = _LeakCharger(pair=pair)
    updater = _known(monkeypatch, charger)
    for _ in range(3):
        data = asyncio.run(updater._async_update_data())

    assert all("serviceData" not in query for query in _graphql_queries(charger))
    assert "leakValue" not in data
    # The raw /main startup sample never reaches the entities.
    assert charger.main["leakValue"] > 1000


def test_a_failed_leakage_read_keeps_the_poll_and_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger(service_status=500)
    updater = _known(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    queries = _graphql_queries(charger)
    assert ["serviceData" in query for query in queries] == [True, False]
    assert charger.paths() == ["/graphql", "/graphql"]
    assert data["voltMeas1"] == charger.station["measurements"]["voltMeasurement1"]
    assert "leakValue" not in data
    assert not updater.snapshot.supports("leakValue")
    assert updater.last_update_success is True

    charger.calls.clear()
    charger.service_status = 200
    asyncio.run(updater._async_update_data())
    assert all("serviceData" not in query for query in _graphql_queries(charger))


def test_leakage_is_asked_again_after_the_back_off(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger(service_status=500)
    updater = _known(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.service_status = 200
    later = updater._reve._leakage_retry_at
    monkeypatch.setattr(reve.time, "monotonic", lambda: later)
    charger.calls.clear()

    data = asyncio.run(updater._async_update_data())

    assert "serviceData" in _graphql_queries(charger)[0]
    assert data["leakValue"] == 0


@pytest.mark.parametrize(
    "service", [None, {}, [33], "33", {"adc_plug_temperature": None}, {"adc_plug_temperature": "33"}]
)
def test_a_reply_without_a_usable_word_loses_only_leakage(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    charger = _LeakCharger()
    charger.service = service
    updater = _known(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert len(_graphql_queries(charger)) == 1
    assert "leakValue" not in data
    assert data["state"] == charger.station["stationState"]


def test_a_main_fallback_poll_has_no_leakage(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger(word=250)
    updater = _known(monkeypatch, charger)
    assert asyncio.run(updater._async_update_data())["leakValue"] == 2
    charger.measurements_status = 500
    charger.service_status = 500

    data = asyncio.run(updater._async_update_data())

    assert "/main" in charger.paths()
    assert "leakValue" not in data
    assert not updater.snapshot.supports("leakValue")


def test_a_reve_leakage_above_30_ma_raises_no_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger(word=5000)
    updater = _known(monkeypatch, charger)
    data = asyncio.run(updater._async_update_data())
    assert data["leakValue"] == 50
    assert evaluate_policy_signals(_LEAK_POLICY, updater.snapshot) == (False, True)


def test_a_firmware_change_stops_the_leakage_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _LeakCharger()
    updater = _known(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.station["wifiFWVersion"] = "REVE_PLUS_1.0.6"
    charger.calls.clear()

    data = asyncio.run(updater._async_update_data())  # asked once more, reply names the new version
    assert "leakValue" not in data
    charger.calls.clear()
    asyncio.run(updater._async_update_data())
    assert all("serviceData" not in query for query in _graphql_queries(charger))


# ---------------------------------------------------------------------------
# Information only
# ---------------------------------------------------------------------------


def _snapshot(leak: int, *, informational: bool) -> EveusSnapshot:
    return EveusSnapshot.parse(
        {"state": 2, "subState": 0, "leakValue": leak},
        "16A",
        informational=frozenset({"leakValue"}) if informational else frozenset(),
    )


_LEAK_POLICY = next(policy for policy in POLICIES if policy.key == "leakage_detected")


def test_reve_leakage_never_raises_a_warning() -> None:
    assert evaluate_policy_signals(_LEAK_POLICY, _snapshot(300, informational=True)) == (False, True)


def test_classic_leakage_warning_is_unchanged() -> None:
    assert evaluate_policy_signals(_LEAK_POLICY, _snapshot(300, informational=False)) == (True, False)
    assert evaluate_policy_signals(_LEAK_POLICY, _snapshot(0, informational=False)) == (False, True)


def test_a_reve_firmware_leak_fault_still_warns() -> None:
    for code in (2, 4):
        snapshot = EveusSnapshot.parse(
            {"state": 7, "subState": code, "leakValue": 0}, "16A", informational=frozenset({"leakValue"})
        )
        assert evaluate_policy_signals(_LEAK_POLICY, snapshot)[0] is True
