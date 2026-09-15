"""The bundled dashboard card: served and registered by the integration."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import custom_components.eveus as eveus

CARD = Path(__file__).resolve().parents[1] / "custom_components/eveus/frontend/eveus-card.js"


class FakeResources:
    """Storage-mode Lovelace resources, as HACS cards are registered."""

    def __init__(self, items=()):
        self.items = [dict(item) for item in items]
        self.loaded = False

    async def async_get_info(self):
        self.loaded = True
        return {"resources": len(self.items)}

    def async_items(self):
        return list(self.items)

    async def async_create_item(self, data):
        self.items.append({"id": f"r{len(self.items)}", **data})

    async def async_update_item(self, item_id, data):
        for item in self.items:
            if item["id"] == item_id:
                item.update(data)


def _hass(resources, mode="storage", frontend=True):
    async def _executor(func, *args):
        return func(*args)

    return SimpleNamespace(
        config=SimpleNamespace(components={"frontend", "lovelace"} if frontend else set()),
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        data={"lovelace": SimpleNamespace(resources=resources, resource_mode=mode)},
        async_add_executor_job=_executor,
        is_running=True,
        bus=MagicMock(),
    )


async def test_card_is_served_and_registered_as_a_lovelace_resource():
    """Loaded after the frontend is ready, like any HACS card."""
    resources = FakeResources([{"id": "x", "res_type": "module", "url": "/hacsfiles/other.js"}])
    hass = _hass(resources)
    with patch.object(eveus, "add_extra_js_url") as add_js:
        await eveus._async_register_card(hass)
    [[paths], _] = hass.http.async_register_static_paths.call_args
    assert paths[0].url_path == eveus.CARD_URL == "/eveus/eveus-card.js"
    assert Path(paths[0].path) == CARD
    ours = [i for i in resources.items if i["url"].startswith(eveus.CARD_URL)]
    assert len(ours) == 1
    assert ours[0]["res_type"] == "module"
    assert len(ours[0]["url"].split("?v=")[1]) == 8, "cache-busting content hash"
    assert resources.loaded
    add_js.assert_not_called()


async def test_resource_url_is_updated_not_duplicated():
    resources = FakeResources([{"id": "old", "res_type": "module", "url": f"{eveus.CARD_URL}?v=deadbeef"}])
    hass = _hass(resources)
    await eveus._async_register_card(hass)
    await eveus._async_register_card(hass)
    ours = [i for i in resources.items if i["url"].startswith(eveus.CARD_URL)]
    assert len(ours) == 1
    assert ours[0]["id"] == "old"
    assert not ours[0]["url"].endswith("deadbeef")


async def test_unchanged_resource_is_not_rewritten():
    resources = FakeResources()
    hass = _hass(resources)
    await eveus._async_register_card(hass)
    resources.async_update_item = AsyncMock()
    await eveus._async_register_card(hass)
    resources.async_update_item.assert_not_called()


async def test_yaml_mode_dashboards_fall_back_to_an_extra_module():
    hass = _hass(MagicMock(spec=["async_items"]), mode="yaml")
    with patch.object(eveus, "add_extra_js_url") as add_js:
        await eveus._async_register_card(hass)
    assert add_js.call_args[0][0] is hass
    assert add_js.call_args[0][1].startswith(f"{eveus.CARD_URL}?v=")


async def test_card_registration_is_skipped_without_the_frontend():
    for hass in (_hass(FakeResources(), frontend=False), SimpleNamespace(http=None, config=SimpleNamespace(components={"frontend"}))):
        with patch.object(eveus, "add_extra_js_url") as add_js:
            await eveus._async_register_card(hass)
        add_js.assert_not_called()


async def test_setup_registers_the_card_now_or_after_start():
    running = _hass(FakeResources())
    with patch.object(eveus, "_async_register_card", AsyncMock()) as register:
        assert await eveus.async_setup(running, {}) is True
    register.assert_awaited_once_with(running)

    starting = _hass(FakeResources())
    starting.is_running = False
    with patch.object(eveus, "_async_register_card", AsyncMock()) as register:
        assert await eveus.async_setup(starting, {}) is True
        register.assert_not_called()
        event, callback = starting.bus.async_listen_once.call_args[0]
        assert event == eveus.EVENT_HOMEASSISTANT_STARTED
        await callback(None)
    register.assert_awaited_once_with(starting)


async def test_a_failed_registration_does_not_fail_setup(caplog):
    hass = _hass(FakeResources())
    hass.http.async_register_static_paths = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(eveus, "_async_register_card", wraps=eveus._async_register_card):
        assert await eveus.async_setup(hass, {}) is True


def test_card_ships_in_the_package_with_picker_entry_and_four_layouts():
    source = CARD.read_text(encoding="utf-8")
    assert 'customElements.define(CARD' in source
    assert "window.customCards" in source
    assert "getConfigForm" in source
    assert 'const CARD = "eveus-card"' in source
    assert 'LAYOUTS = ["compact", "status", "control", "full"]' in source


def test_manifest_loads_after_frontend_and_lovelace():
    import json

    manifest = json.loads((CARD.parents[1] / "manifest.json").read_text())
    assert {"frontend", "http", "lovelace"} <= set(manifest.get("after_dependencies", []))
