"""Entities REVE can never fill are not created for a REVE charger.

REVE schedules carry no energy limit (``scheduleV2`` has a current limit only)
and REVE resets ``suspendLimits`` within seconds (hardware test 2026-10-18), so
Schedule 1/2 Energy limit, their switches and Limit: disable all would read
``unavailable`` forever on REVE. They are built only for Eveus firmware.

The firmware is known only once the charger answers, which can be long after
setup, so the entry remembers it: setup builds from the stored firmware when
the charger is off, a charger first identified as REVE after setup reloads the
entry once, and a charger set back to Eveus firmware gets the entities again on
the next setup that hears it. The leftover registry rows of a REVE charger are
pruned the same way as the 3-phase sensors on a 1-phase entry.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import custom_components.eveus as eveus
from custom_components.eveus.const import CONF_FIRMWARE_FAMILY, MODEL_16A, CONF_MODEL
from custom_components.eveus.number import async_setup_entry as setup_numbers
from custom_components.eveus.reve import FAMILY_CLASSIC, FAMILY_REVE
from custom_components.eveus.switch import async_setup_entry as setup_switches
from test_setup_entry import _ConfigEntries, _data, _Entry, _Updater
from test_setup_entry import _patch_issue_registry  # noqa: F401 - autouse fixture

_EVEUS_ONLY = {
    ("switch", "limit_disable_all"),
    ("switch", "schedule_1_energy_limit_enabled"),
    ("switch", "schedule_2_energy_limit_enabled"),
    ("number", "schedule_1_energy_limit"),
    ("number", "schedule_2_energy_limit"),
}


class _ReveUpdater(_Updater):
    firmware_family = FAMILY_REVE


class _ClassicUpdater(_Updater):
    firmware_family = FAMILY_CLASSIC


class _OffUpdater(_Updater):
    firmware_family = FAMILY_CLASSIC

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.last_update_success = False


class _Reloads(_ConfigEntries):
    def __init__(self) -> None:
        super().__init__()
        self.scheduled: list[str] = []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        super().async_update_entry(entry, **kwargs)
        if "data" in kwargs:
            entry.data = kwargs["data"]

    def async_schedule_reload(self, entry_id: str) -> None:
        self.scheduled.append(entry_id)


def _setup(monkeypatch: pytest.MonkeyPatch, updater_cls: type, **data: object):
    hass = SimpleNamespace(config_entries=_Reloads())
    entry = _Entry(_data(**data))
    monkeypatch.setattr(eveus, "EveusUpdater", updater_cls)
    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True
    return hass, entry


def _keys(setup, entry) -> set[str]:
    added: list[object] = []
    asyncio.run(setup(None, entry, added.extend))
    return {entity.entity_description.key for entity in added if hasattr(entity, "entity_description")}


# ---------------------------------------------------------------------------
# What the platforms build
# ---------------------------------------------------------------------------


def test_a_reve_charger_gets_none_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    _hass, entry = _setup(monkeypatch, _ReveUpdater)
    assert eveus.built_for_reve(entry)
    built = {("switch", key) for key in _keys(setup_switches, entry)}
    built |= {("number", key) for key in _keys(setup_numbers, entry)}
    assert not built & _EVEUS_ONLY
    assert ("switch", "schedule_1_current_limit_enabled") in built
    assert ("number", "schedule_1_current_limit") in built


def test_an_eveus_firmware_charger_gets_all_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    _hass, entry = _setup(monkeypatch, _ClassicUpdater)
    built = {("switch", key) for key in _keys(setup_switches, entry)}
    built |= {("number", key) for key in _keys(setup_numbers, entry)}
    assert _EVEUS_ONLY <= built


# ---------------------------------------------------------------------------
# Remembering the firmware
# ---------------------------------------------------------------------------


def test_a_charger_answering_as_reve_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    hass, entry = _setup(monkeypatch, _ReveUpdater)
    assert entry.data[CONF_FIRMWARE_FAMILY] == FAMILY_REVE
    assert hass.config_entries.scheduled == []


def test_a_switched_off_reve_charger_is_built_as_reve(monkeypatch: pytest.MonkeyPatch) -> None:
    _hass, entry = _setup(monkeypatch, _OffUpdater, **{CONF_FIRMWARE_FAMILY: FAMILY_REVE})
    assert eveus.built_for_reve(entry)


def test_a_charger_back_on_eveus_firmware_is_forgotten_as_reve(monkeypatch: pytest.MonkeyPatch) -> None:
    _hass, entry = _setup(monkeypatch, _ClassicUpdater, **{CONF_FIRMWARE_FAMILY: FAMILY_REVE})
    assert not eveus.built_for_reve(entry)
    assert CONF_FIRMWARE_FAMILY not in entry.data


def test_an_eveus_firmware_charger_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    hass, entry = _setup(monkeypatch, _ClassicUpdater)
    assert CONF_FIRMWARE_FAMILY not in entry.data
    assert hass.config_entries.updated == [{"data": {**_data(), "device_number": 1}}]


def test_reve_identified_after_setup_reloads_the_entry_once(monkeypatch: pytest.MonkeyPatch) -> None:
    hass, entry = _setup(monkeypatch, _OffUpdater)
    updater = entry.runtime_data.updater
    assert not eveus.built_for_reve(entry)

    for listener in updater.listeners:
        listener()
    assert hass.config_entries.scheduled == []

    updater.last_update_success = True
    updater.firmware_family = FAMILY_REVE
    for _ in range(3):
        for listener in updater.listeners:
            listener()

    assert entry.data[CONF_FIRMWARE_FAMILY] == FAMILY_REVE
    assert hass.config_entries.scheduled == [entry.entry_id]


# ---------------------------------------------------------------------------
# The leftover registry rows
# ---------------------------------------------------------------------------


class _Registry:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str:
        return f"{platform}.{unique_id}"

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


def _pruned(monkeypatch: pytest.MonkeyPatch, family: str) -> set[str]:
    reg = _Registry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), 1, eveus.SOC_MODE_ADVANCED, 3, family)
    return set(reg.removed)


def test_a_reve_charger_loses_their_leftover_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    removed = _pruned(monkeypatch, FAMILY_REVE)
    assert {f"{platform}.eveus_{key}" for platform, key in _EVEUS_ONLY} <= removed


def test_an_eveus_firmware_charger_keeps_them(monkeypatch: pytest.MonkeyPatch) -> None:
    removed = _pruned(monkeypatch, FAMILY_CLASSIC)
    assert not {f"{platform}.eveus_{key}" for platform, key in _EVEUS_ONLY} & removed


def test_the_model_is_needed_for_the_numbers_anyway() -> None:
    # Guard on the test data: without a model no limit numbers are built at all,
    # which would make the REVE assertion above pass vacuously.
    assert _data()[CONF_MODEL] == MODEL_16A
