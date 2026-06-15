"""Hardening tests for the 4.10.1 deep-audit round (rc12).

Covers defects found by the parallel deep audit that prior rounds missed:

  * V1 — an unbalanced IPv6 bracket in the host (``[::1``, ``http://[::1``) is
    rejected as ``vol.Invalid`` instead of raising an uncaught ``ValueError``
    that surfaced as a generic "unknown" error / setup-retry loop.
  * V2 / V3 — temperature, battery-voltage, leakage-current and tariff-rate
    sensors reject corrupt finite outliers instead of recording them into
    long-term statistics.
  * V4 / V5 — SOC and per-phase entities are pruned from the registry when the
    config is reduced (Advanced -> Basic, 3 -> 1 phase) so they do not linger
    as orphaned "unavailable" entities.
  * V7 — a command completing after the coordinator has shut down no longer
    schedules fresh post-command refresh timers.
  * V8 — rapid repeated commands on the same control are serialized per entity,
    so an older command can no longer publish a stale value over a newer one.
"""
from __future__ import annotations

import asyncio

import pytest
import voluptuous as vol

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME, disable_state_writes
from custom_components.eveus import config_flow
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import MODEL_16A
from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.select import EveusTimeZoneSelect
from custom_components.eveus.switch import BaseSwitchEntity, SWITCH_DESCRIPTIONS
from custom_components.eveus.time import EveusScheduleTimeEntity, TIME_DESCRIPTIONS


class _Hass:
    """Minimal hass object for coordinator construction."""

    loop = None


# ---------------------------------------------------------------------------
# V1 — unbalanced IPv6 bracket is rejected as vol.Invalid, not ValueError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["[::1", "http://[::1", "[fe80::", "https://[2001:db8::1"])
def test_unbalanced_ipv6_bracket_raises_invalid(raw: str) -> None:
    with pytest.raises(vol.Invalid):
        config_flow._split_host_and_scheme(raw)


def test_balanced_ipv6_still_accepted() -> None:
    host, scheme = config_flow._split_host_and_scheme("[::1]")
    assert host == "[::1]"
    assert scheme == "http"


# ---------------------------------------------------------------------------
# V2 / V3 — measurement sensors reject corrupt finite outliers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "getter,key,good,expected",
    [
        (sd.get_box_temperature, "temperature1", 42, 42),
        (sd.get_plug_temperature, "temperature2", 55, 55),
        (sd.get_battery_voltage, "vBat", 12.5, 12.5),
        (sd.get_leak_current, "leakValue", 5, 5),
        (sd.get_leak_current_peak, "leakValueH", 9, 9),
        (sd.get_primary_rate_cost, "tarif", 450, 4.5),
        (sd.get_rate2_cost, "tarifAValue", 600, 6.0),
        (sd.get_rate3_cost, "tarifBValue", 720, 7.2),
    ],
)
def test_measurement_getters_reject_finite_outliers(getter, key, good, expected) -> None:
    assert getter(EveusTestUpdater(data={key: 1e100}), None) is None
    assert getter(EveusTestUpdater(data={key: good}), None) == pytest.approx(expected)


def test_temperature_rejects_impossible_negative() -> None:
    # Below the -40 floor is corrupt; a plausible cold reading still passes.
    assert sd.get_box_temperature(EveusTestUpdater(data={"temperature1": -100}), None) is None
    assert sd.get_box_temperature(EveusTestUpdater(data={"temperature1": -20}), None) == -20


def test_active_rate_cost_rejects_finite_outlier() -> None:
    outlier = EveusTestUpdater(data={"activeTarif": 0, "tarif": 1e100})
    assert sd.get_active_rate_cost(outlier, None) is None
    good = EveusTestUpdater(data={"activeTarif": 0, "tarif": 450})
    assert sd.get_active_rate_cost(good, None) == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# V4 / V5 — orphaned entities are pruned when the config scope shrinks
# ---------------------------------------------------------------------------

class _FakeRegistry:
    """Records which entities were removed; pretends every entity exists."""

    def __init__(self) -> None:
        self.removed: list[str] = []

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str:
        return f"{platform}.{unique_id}"

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


def _prune(monkeypatch, device_number, soc_mode, phases) -> list[str]:
    from custom_components import eveus

    reg = _FakeRegistry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), device_number, soc_mode, phases)
    return reg.removed


def test_prune_removes_soc_and_phase_orphans_when_reduced(monkeypatch) -> None:
    from custom_components import eveus

    removed = _prune(monkeypatch, 1, eveus.SOC_MODE_BASIC, 1)
    assert "number.eveus_initial_soc" in removed
    assert "sensor.eveus_soc_energy" in removed
    assert "sensor.eveus_charging_finish_time" in removed
    assert "sensor.eveus_current_phase_2" in removed
    assert "sensor.eveus_voltage_phase_3" in removed


def test_prune_keeps_everything_in_advanced_three_phase(monkeypatch) -> None:
    from custom_components import eveus

    # Only retired entities go — no mode/phase-scoped entity is pruned.
    assert _prune(monkeypatch, 1, eveus.SOC_MODE_ADVANCED, 3) == [
        "sensor.eveus_system_time",
        "switch.eveus_adaptive_mode",
        "number.eveus_minimum_voltage",
        "sensor.eveus_adaptive_voltage_threshold",
    ]


def test_prune_respects_device_suffix_and_keeps_phases_when_three(monkeypatch) -> None:
    from custom_components import eveus

    removed = _prune(monkeypatch, 2, eveus.SOC_MODE_BASIC, 3)
    assert "number.eveus2_initial_soc" in removed
    # phases == 3 keeps the per-phase sensors.
    assert all("phase" not in entity_id for entity_id in removed)


# ---------------------------------------------------------------------------
# V7 — a command finishing after shutdown does not schedule fresh refreshes
# ---------------------------------------------------------------------------

def test_command_after_shutdown_skips_refresh_scheduling(monkeypatch) -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    async def ok(*args, **kwargs) -> bool:
        return True

    updater._command_manager.send_command = ok
    scheduled: list[int] = []
    monkeypatch.setattr(
        updater, "_schedule_post_command_refresh", lambda: scheduled.append(1)
    )

    updater._shutting_down = True
    assert asyncio.run(updater.send_command("evseEnabled", 1)) is True
    assert scheduled == []

    # Sanity: while live, a successful command still schedules refreshes.
    updater._shutting_down = False
    asyncio.run(updater.send_command("evseEnabled", 1))
    assert scheduled == [1]


def test_async_shutdown_sets_shutting_down_flag() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater._shutting_down is False
    asyncio.run(updater.async_shutdown())
    assert updater._shutting_down is True


# ---------------------------------------------------------------------------
# V8 — per-entity command lock serializes rapid repeated commands
# ---------------------------------------------------------------------------

def test_control_entities_have_command_lock() -> None:
    updater = EveusTestUpdater(data={})
    entities = [
        BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0]),
        EveusCurrentNumber(updater, MODEL_16A),
        EveusScheduleTimeEntity(updater, TIME_DESCRIPTIONS[0]),
        EveusTimeZoneSelect(updater),
    ]
    for entity in entities:
        assert isinstance(entity._command_lock, asyncio.Lock)


def test_current_number_serializes_concurrent_commands() -> None:
    updater = EveusTestUpdater(data={"currentSet": 10})
    number = EveusCurrentNumber(updater, MODEL_16A)
    disable_state_writes(number)

    timeline: list[tuple[str, object]] = []

    async def instrumented(command, value, *, retry=True, extra=None) -> bool:
        timeline.append(("start", value))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        timeline.append(("end", value))
        return True

    updater.send_command = instrumented

    async def run() -> None:
        await asyncio.gather(
            number.async_set_native_value(8),
            number.async_set_native_value(15),
        )

    asyncio.run(run())

    # The per-entity lock fully serializes both commands: no command may start
    # before the previous one has ended (depth never exceeds 1).
    depth = 0
    for kind, _value in timeline:
        depth += 1 if kind == "start" else -1
        assert depth <= 1
    assert len(timeline) == 4
