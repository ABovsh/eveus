"""Hardening tests for the 4.10.1 deep-audit round 2 (rc13).

Covers defects the second parallel audit found that round 1 missed:

  * R2-1 — the SOC / ETA calculations now reject finite-but-impossible
    `powerMeas` / `sessionEnergy` outliers, matching the caps already applied to
    the Power and Session Energy display sensors, so a corrupt payload can no
    longer drive SOC %/kWh to a false full battery or collapse the ETA to "<1m".
  * R2-2 — the Time Zone select suppresses reconciliation while its own command
    is in flight (like the other controls), so a routine poll landing mid-command
    no longer reverts the displayed zone.
  * R2-3 — migration strips a legacy `/main` path even when the stored URL uses
    an uppercase scheme (`HTTP://.../main`), instead of leaving it to fail setup.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from homeassistant.const import CONF_HOST

from conftest import EveusTestUpdater, TEST_HOST, disable_state_writes
from custom_components.eveus import (
    CONFIG_ENTRY_VERSION,
    async_migrate_entry,
)
from custom_components.eveus.const import CONF_SCHEME
from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
)
from custom_components.eveus.select import EveusTimeZoneSelect


def _soc_calc() -> CachedSOCCalculator:
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 10)
    calc.set_value("target_soc", 80)
    return calc


# ---------------------------------------------------------------------------
# R2-1 — SOC/ETA calculations reject finite outliers the same as the sensors
# ---------------------------------------------------------------------------

def test_soc_percent_rejects_session_energy_outlier() -> None:
    bad = EVSocPercentSensor(EveusTestUpdater(data={"sessionEnergy": 1e100}), 1, _soc_calc())
    assert bad._get_sensor_value() is None
    good = EVSocPercentSensor(EveusTestUpdater(data={"sessionEnergy": 10}), 1, _soc_calc())
    assert good._get_sensor_value() is not None


def test_eta_rejects_power_outlier() -> None:
    bad = TimeToTargetSocSensor(
        EveusTestUpdater(data={"sessionEnergy": 10, "powerMeas": 1e100, "state": 4}), 1, _soc_calc()
    )
    assert bad._get_sensor_value() is None
    good = TimeToTargetSocSensor(
        EveusTestUpdater(data={"sessionEnergy": 10, "powerMeas": 3000, "state": 4}), 1, _soc_calc()
    )
    assert isinstance(good._get_sensor_value(), str)


# ---------------------------------------------------------------------------
# R2-2 — Time Zone select keeps optimistic state while a command is pending
# ---------------------------------------------------------------------------

def test_timezone_select_suppresses_reconcile_while_pending() -> None:
    updater = EveusTestUpdater(data={"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    disable_state_writes(select)

    # Optimistic +3, stamped longer ago than the 10s mismatch TTL.
    select._set_optimistic_value(3)
    select._optimistic_value_time = time.time() - 11

    # While the command is in flight, a poll returning the old zone must NOT
    # expire the optimistic value.
    select._command_pending = True
    select._handle_coordinator_update()
    assert select.current_option == "+3"

    # Once the command settles, the normal reconcile path applies again.
    select._command_pending = False
    select._handle_coordinator_update()
    assert select.current_option == "0"


# ---------------------------------------------------------------------------
# R2-3 — migration strips a legacy /main path for uppercase schemes too
# ---------------------------------------------------------------------------

class _ConfigEntries:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def async_entries(self, _domain=None):
        return []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _EmptyRegistry:
    def async_get(self, entity_id: str) -> object | None:
        return None


def test_migration_strips_main_path_for_uppercase_scheme(monkeypatch) -> None:
    from custom_components import eveus

    monkeypatch.setattr(eveus.er, "async_get", lambda hass: _EmptyRegistry())

    config_entries = _ConfigEntries()
    hass = SimpleNamespace(config_entries=config_entries)
    legacy = f"HTTP://{TEST_HOST}/main"
    entry = SimpleNamespace(
        data={CONF_HOST: legacy},
        unique_id=legacy,
        title=f"Eveus Charger ({legacy})",
        version=1,
    )

    assert asyncio.run(async_migrate_entry(hass, entry)) is True

    data = config_entries.calls[0]["data"]
    assert data[CONF_HOST] == TEST_HOST
    assert data[CONF_SCHEME] == "http"
    assert config_entries.calls[0]["version"] == CONFIG_ENTRY_VERSION
