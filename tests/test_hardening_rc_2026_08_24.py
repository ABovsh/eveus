"""Hardening round on rc, 2026-08-24: gaps left by the recorder-churn work.

The damping landed on the shared getter factory but was wired to only four of
the dithering readings, and the new `soc_anchor` attribute re-derives its text
on every poll — which is the very thing a deadband exists to stop.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.eveus import sensor_definitions as sd

from test_soc_autofill import _build, _poll  # noqa: F401  (fixtures come along)
from test_soc_autofill import _no_dispatcher  # noqa: F401


def _updater(data: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(data=data, available=True, host="192.168.1.50")


def _read(getter, updater, key: str, values) -> list:
    out = []
    for value in values:
        updater.data[key] = value
        out.append(getter(updater, None))
    return out


# ---------------------------------------------------------------------------
# The damping has to cover every dithering reading, not four of them
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("getter", "key", "feed", "expected"),
    [
        # Phases 2 and 3 are the same telemetry as phase 1 on a 3-phase entry,
        # so they dither the same way and take the same step.
        (sd.get_voltage_phase_2, "voltMeas2", [230, 231, 229, 232], [230, 230, 230, 232]),
        (sd.get_voltage_phase_3, "voltMeas3", [230, 231, 229, 232], [230, 230, 230, 232]),
        (sd.get_current_phase_2, "curMeas2", [16.0, 16.1, 15.9, 16.3], [16.0, 16.0, 16.0, 16.3]),
        (sd.get_current_phase_3, "curMeas3", [16.0, 16.1, 15.9, 16.3], [16.0, 16.0, 16.0, 16.3]),
        # Whole-degree enclosure temperatures alternate between two adjacent
        # readings for hours; a 1 degree band would be no band at all, because
        # the next distinct value is already 1 away.
        (sd.get_box_temperature, "temperature1", [30, 31, 30, 32], [30, 30, 30, 32]),
        (sd.get_plug_temperature, "temperature2", [30, 31, 30, 32], [30, 30, 30, 32]),
    ],
)
def test_dithering_getters_hold_until_the_deadband_is_crossed(
    getter, key, feed, expected
) -> None:
    assert _read(getter, _updater({}), key, feed) == pytest.approx(expected)


def test_wifi_rssi_holds_a_swing_the_link_quality_does_not_notice() -> None:
    """Measured on the live charger: RSSI wanders across ~7 dBm all day.

    A 3 dBm band still published one reading in seven; the link is "Excellent"
    across the whole swing, so the rows carried nothing.
    """
    assert _read(sd.get_wifi_rssi, _updater({}), "RSSI", [-66, -70, -69, -73]) == [
        -66,
        -66,
        -66,
        -73,
    ]


# ---------------------------------------------------------------------------
# A seed that keeps failing must not re-word its reason on every poll
# ---------------------------------------------------------------------------

def test_a_repeating_seed_failure_reports_one_steady_reason() -> None:
    """`soc_anchor` on SOC Percent mirrors this text.

    The rebase subtracts the energy delivered so far, so a reason quoting it
    reads differently on every poll of a running session — a recorder row per
    poll for the rest of the cycle, on the entity whose attribute it is.
    """
    entity, updater, calc = _build(car_soc=10, seed=20)

    _poll(entity, updater, 4, session_energy=20.0)
    first = dict(calc.last_seed)
    assert first["seeded"] is False

    _poll(entity, updater, 4, session_energy=21.0)

    assert calc.last_seed == first
