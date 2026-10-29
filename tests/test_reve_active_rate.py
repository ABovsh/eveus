"""REVE_PLUS 1.0.3 reports the price in effect (``ratesV2.activeRate``).

The station's word wins: Active Rate Cost and its rate name follow the rate
priced as the station says. The integration's own reckoning from the rate
windows and the charger clock stands in where the station does not say
(REVE_PLUS 1.0.0, a poll served by ``/main``).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.eveus import reve
from custom_components.eveus.reve import normalize_reve_payload, parse_extras
from test_reve_firmware import _NIGHT, _extras_with, _rate, _reve_main, _wall
from test_reve_graphql_writes import _known

_DAY = {**_rate(7 * 60, 23 * 60, 4.32), "name": "Rate 2 - Day"}
_NIGHT_NAMED = {**_NIGHT, "name": "Rate 1 - Night"}


def _payload(active: Any, clock: int | None, rates: list[dict[str, Any]] | None = None,
             base: float = 4.32) -> tuple[dict[str, Any], frozenset[str]]:
    rates_v2: dict[str, Any] = {"base": base, "rates": rates or [_NIGHT_NAMED, _DAY]}
    if active is not ...:
        rates_v2["activeRate"] = active
    extras = parse_extras(_extras_with(ratesV2=rates_v2))
    main = _reve_main()
    if clock is None:
        del main["systemTime"]
    else:
        main["systemTime"] = clock
    return normalize_reve_payload(main, extras)


def test_the_station_rate_is_read_with_the_tariffs() -> None:
    assert parse_extras(_extras_with(ratesV2={"base": 4.32, "rates": [], "activeRate": 2.16})).active_rate == 2.16
    assert parse_extras(_extras_with(ratesV2={"base": 4.32, "rates": []})).active_rate is None


@pytest.mark.parametrize("value", [None, True, "2.16", -1, float("nan")])
def test_anything_but_a_price_is_no_station_rate(value: Any) -> None:
    extras = parse_extras(_extras_with(ratesV2={"base": 4.32, "rates": [], "activeRate": value}))
    assert extras.active_rate is None


def test_where_both_agree_nothing_changes() -> None:
    payload, _ = _payload(2.16, _wall(16, 2, 0))
    assert payload["activeTarif"] == 1
    assert payload["activeTarifName"] == "Rate 1 - Night"


def test_the_station_wins_over_the_clock() -> None:
    # 23:00:30 by a charger clock a minute slow is still day by the windows.
    payload, _ = _payload(2.16, _wall(16, 22, 59))
    assert payload["activeTarif"] == 1
    assert payload["activeTarifName"] == "Rate 1 - Night"
    payload, _ = _payload(4.32, _wall(16, 2, 0))
    assert payload["activeTarif"] == 2
    assert payload["activeTarifName"] == "Rate 2 - Day"


def test_the_base_price_is_the_base_rate() -> None:
    payload, _ = _payload(5.0, _wall(16, 12, 0), rates=[_NIGHT_NAMED], base=5.0)
    assert payload["activeTarif"] == 0
    assert "activeTarifName" not in payload


def test_the_station_rate_needs_no_charger_clock() -> None:
    payload, unsupported = _payload(2.16, None)
    assert payload["activeTarif"] == 1
    assert "activeTarif" not in unsupported


def test_a_price_no_enabled_rate_has_leaves_the_active_rate_unknown() -> None:
    disabled = {**_rate(0, 0, 3.0, enabled=False), "name": "Rate 3"}
    payload, unsupported = _payload(3.0, _wall(16, 12, 0), rates=[_NIGHT_NAMED, _DAY, disabled])
    assert "activeTarif" in unsupported


def test_two_rates_at_one_price_keep_the_one_the_windows_name() -> None:
    evening = {**_rate(18 * 60, 23 * 60, 4.32), "name": "Evening"}
    payload, _ = _payload(4.32, _wall(16, 19, 0), rates=[_NIGHT_NAMED, _DAY, evening])
    assert payload["activeTarif"] == 2
    payload, _ = _payload(4.32, _wall(16, 2, 0), rates=[_NIGHT_NAMED, evening, _DAY])
    assert payload["activeTarif"] == 2, "the first rate at that price, when the windows disagree"


def test_without_the_station_rate_the_windows_decide() -> None:
    payload, _ = _payload(..., _wall(16, 22, 59))
    assert payload["activeTarif"] == 2


def test_every_poll_reads_the_station_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    charger.rates = {"base": 4.32, "rates": [_NIGHT_NAMED, _DAY], "activeRate": 2.16}
    updater._reve._fetched_at = None  # the new tariffs come with this poll
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.get("activeTarif") == 1
    charger.rates["activeRate"] = 4.32
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.get("activeTarif") == 2
    assert "activeRate" in reve.MEASUREMENTS_QUERY
    assert "activeRate" in reve.MEASUREMENTS_AND_SETTINGS_QUERY
    assert ("ratesV2", "activeRate") not in reve._EXPECTED_PATHS


def test_a_main_poll_drops_the_station_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    charger.rates["activeRate"] = charger.rates["base"]
    asyncio.run(updater._async_update_data())
    assert updater._reve.extras.active_rate == charger.rates["base"]
    updater._reve.from_main(_reve_main(), fallback=True)
    assert updater._reve.extras.active_rate is None


def test_an_impossible_station_price_is_no_price_instead_of_failing_the_poll() -> None:
    """A finite but absurd activeRate overflowed the price comparison and
    failed the whole poll; it now counts as no reported price."""
    from custom_components.eveus.reve import normalize_reve_payload, parse_extras

    extras = parse_extras({"stationData": {
        "scheduleV2": [],
        "ratesV2": {"base": 4.32, "rates": [], "activeRate": 1e308},
    }})

    payload, _ = normalize_reve_payload({"state": 2}, extras)

    assert extras.active_rate is None
    assert payload["tarif"] == 432
