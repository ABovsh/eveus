"""Developer probe in a real Home Assistant (branch `reve` only, never released).

Removed together with ``custom_components/eveus/dev_probe.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eveus.const import DOMAIN
from custom_components.eveus.dev_probe import PROBE_DIR

from .test_reve_smoke import _PREFIX, _setup_reve

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_without_the_folder_there_are_no_probe_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup_reve(hass, aioclient_mock)
    assert hass.states.get(f"sensor.{_PREFIX}_dev_main_outages_today") is None
    assert not [
        entry for entry in er.async_entries_for_config_entry(
            er.async_get(hass), hass.config_entries.async_entries(DOMAIN)[0].entry_id
        )
        if "_dev_" in entry.unique_id
    ]


async def test_the_probe_logs_and_adds_no_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    folder = Path(hass.config.path(PROBE_DIR))
    await hass.async_add_executor_job(lambda: folder.mkdir(exist_ok=True))
    try:
        await _setup_reve(hass, aioclient_mock)
        assert hass.states.get(f"sensor.{_PREFIX}_dev_main_outages_today") is None
        assert hass.states.get(f"sensor.{_PREFIX}_dev_graphql_outages_today") is None

        entry = hass.config_entries.async_entries(DOMAIN)[0]
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        lines = await hass.async_add_executor_job(
            lambda: [
                json.loads(line)
                for path in folder.glob("*.jsonl")
                for line in path.read_text().splitlines()
            ]
        )
        assert [line.get("event") or line["api"] for line in lines] == [
            "start", "graphql", "graphql", "graphql", "graphql", "firmware", "main",
        ]
    finally:
        await hass.async_add_executor_job(
            lambda: [*map(Path.unlink, folder.glob("*")), folder.rmdir()]
        )
