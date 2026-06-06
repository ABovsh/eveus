"""Safety Repairs notices for Eveus chargers.

Combines authoritative firmware fault codes (charger ``state=7`` plus its
``subState`` code) with conservative, debounced raw telemetry so Home Assistant
can warn about dangerous grounding, leakage, overheat, and other charger faults
without false alarms. This module performs no I/O and sends no charger commands;
it only reports conditions through the issue registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from .const import (
    FAULT_RECOVERY_POLLS,
    GROUND_CLEAR_POLLS,
    GROUND_CONTROL_CLEAR_POLLS,
    GROUND_CONTROL_TRIGGER_POLLS,
    GROUND_TRIGGER_POLLS,
    LEAKAGE_RECOVERY_POLLS,
    LEAKAGE_TRIGGER_POLLS,
    TEMPERATURE_RECOVERY_POLLS,
    TEMPERATURE_TRIGGER_POLLS,
)

# A raw-telemetry signal returns True (condition present), False (absent), or
# None (unknown — missing/corrupt/out-of-domain data that must not move any
# trigger or recovery counter).
Signal = Callable[[Mapping[str, Any]], "bool | None"]


class SafetyLifecycle(Enum):
    """How a recovered safety issue is removed."""

    AUTO_CLEAR = "auto_clear"
    LATCHED = "latched"


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """One user-facing safety issue policy."""

    key: str
    fault_codes: frozenset[int]
    lifecycle: SafetyLifecycle
    raw_trigger: Signal | None = None
    raw_recovered: Signal | None = None
    trigger_polls: int = 1
    recovery_polls: int = FAULT_RECOVERY_POLLS


def safety_issue_id(entry, key: str) -> str:
    """Return an entry-scoped safety issue ID."""
    return f"safety_{key}_{entry.entry_id}"


def _policy(
    key: str,
    *fault_codes: int,
    lifecycle: SafetyLifecycle = SafetyLifecycle.LATCHED,
    raw_trigger: Signal | None = None,
    raw_recovered: Signal | None = None,
    trigger_polls: int = 1,
    recovery_polls: int = FAULT_RECOVERY_POLLS,
) -> SafetyPolicy:
    return SafetyPolicy(
        key=key,
        fault_codes=frozenset(fault_codes),
        lifecycle=lifecycle,
        raw_trigger=raw_trigger,
        raw_recovered=raw_recovered,
        trigger_polls=trigger_polls,
        recovery_polls=recovery_polls,
    )


POLICIES: tuple[SafetyPolicy, ...] = (
    _policy(
        "ground_missing",
        1,
        lifecycle=SafetyLifecycle.AUTO_CLEAR,
        trigger_polls=GROUND_TRIGGER_POLLS,
        recovery_polls=GROUND_CLEAR_POLLS,
    ),
    _policy(
        "ground_control_disabled",
        lifecycle=SafetyLifecycle.AUTO_CLEAR,
        trigger_polls=GROUND_CONTROL_TRIGGER_POLLS,
        recovery_polls=GROUND_CONTROL_CLEAR_POLLS,
    ),
    _policy(
        "leakage_detected",
        2,
        4,
        trigger_polls=LEAKAGE_TRIGGER_POLLS,
        recovery_polls=LEAKAGE_RECOVERY_POLLS,
    ),
    _policy("relay_fault", 3),
    _policy(
        "box_overheat",
        5,
        trigger_polls=TEMPERATURE_TRIGGER_POLLS,
        recovery_polls=TEMPERATURE_RECOVERY_POLLS,
    ),
    _policy(
        "plug_overheat",
        6,
        trigger_polls=TEMPERATURE_TRIGGER_POLLS,
        recovery_polls=TEMPERATURE_RECOVERY_POLLS,
    ),
    _policy("pilot_fault", 7),
    _policy("low_voltage", 8),
    _policy("diode_fault", 9),
    _policy("overcurrent", 10),
    _policy("interface_timeout", 11),
    _policy("software_failure", 12),
    _policy("gfci_test_failure", 13),
    _policy("high_voltage", 14),
)
