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

    async def async_delete_item(self, item_id):
        self.items = [item for item in self.items if item["id"] != item_id]


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


# --- entity lookup by unique_id -------------------------------------------------

def _reg_entry(unique_id, entity_id, device_id="dev1", platform="eveus"):
    return SimpleNamespace(unique_id=unique_id, entity_id=entity_id, device_id=device_id, platform=platform)


def _ws_hass(entries):
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [SimpleNamespace(entry_id=e) for e in entries]),
    )


def _run_ws(hass, registry_entries, **msg):
    connection = MagicMock()
    by_entry = registry_entries
    with patch.object(eveus.er, "async_get", return_value=object()), patch.object(
        eveus.er, "async_entries_for_config_entry", side_effect=lambda _reg, entry_id: by_entry.get(entry_id, [])
    ):
        eveus._ws_card_entities(hass, connection, {"id": 7, "type": "eveus/card_entities", **msg})
    return connection


def test_card_entities_are_keyed_by_unique_id_not_entity_id():
    """A renamed entity_id still lands on the right tile."""
    registry = {"e1": [
        _reg_entry("eveus_state", "sensor.garage_state"),
        _reg_entry("eveus_soc_percent", "sensor.my_car_battery"),
        _reg_entry("eveus_charging_current", "number.amps"),
        _reg_entry("other_state", "sensor.not_ours", platform="other"),
    ]}
    connection = _run_ws(_ws_hass(["e1"]), registry)
    connection.send_error.assert_not_called()
    msg_id, result = connection.send_result.call_args[0]
    assert msg_id == 7
    assert result == {
        "device_id": "dev1",
        "entities": {
            "state": "sensor.garage_state",
            "soc_percent": "sensor.my_car_battery",
            "charging_current": "number.amps",
        },
    }


def test_second_charger_is_selected_by_device_id_and_its_suffix_is_stripped():
    registry = {
        "e1": [_reg_entry("eveus_state", "sensor.first_state", device_id="dev1")],
        "e2": [
            _reg_entry("eveus2_state", "sensor.second_state", device_id="dev2"),
            _reg_entry("eveus2_one_charge", "switch.second_one", device_id="dev2"),
        ],
    }
    connection = _run_ws(_ws_hass(["e1", "e2"]), registry, device_id="dev2")
    _, result = connection.send_result.call_args[0]
    assert result == {"device_id": "dev2", "entities": {"state": "sensor.second_state", "one_charge": "switch.second_one"}}


def test_without_device_id_the_first_charger_is_used():
    registry = {
        "e1": [_reg_entry("eveus_state", "sensor.first_state", device_id="dev1")],
        "e2": [_reg_entry("eveus2_state", "sensor.second_state", device_id="dev2")],
    }
    _, result = _run_ws(_ws_hass(["e1", "e2"]), registry).send_result.call_args[0]
    assert result["device_id"] == "dev1"


def test_unknown_device_is_reported_as_not_found():
    registry = {"e1": [_reg_entry("eveus_state", "sensor.first_state")]}
    connection = _run_ws(_ws_hass(["e1"]), registry, device_id="nope")
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args[0][:2] == (7, "not_found")


# --- card resource removal on last-entry teardown -------------------------


def _teardown_hass(resources, remaining_entries=()):
    return SimpleNamespace(
        data={"lovelace": SimpleNamespace(resources=resources, resource_mode="storage")},
        config_entries=SimpleNamespace(async_entries=lambda domain: list(remaining_entries)),
    )


async def test_card_resource_is_removed_when_last_entry_goes():
    resources = FakeResources([{"id": "x", "res_type": "module", "url": f"{eveus.CARD_URL}?v=abc"}])
    await eveus._async_unregister_card(_teardown_hass(resources), "e1")
    assert resources.items == []


async def test_card_resource_is_kept_while_other_entries_remain():
    resources = FakeResources([{"id": "x", "res_type": "module", "url": f"{eveus.CARD_URL}?v=abc"}])
    other_entry = SimpleNamespace(entry_id="e2")
    await eveus._async_unregister_card(
        _teardown_hass(resources, remaining_entries=[other_entry]), "e1"
    )
    assert len(resources.items) == 1


async def test_card_resource_is_removed_even_when_the_removed_entry_still_lists_itself():
    """HA 2025.1's ConfigEntries._async_remove calls entry.async_remove()
    (which reaches this function) before deleting the entry from
    self._entries, so async_entries(DOMAIN) still contains the entry being
    removed. Only *other* entries should block removal."""
    resources = FakeResources([{"id": "x", "res_type": "module", "url": f"{eveus.CARD_URL}?v=abc"}])
    self_entry = SimpleNamespace(entry_id="e1")
    await eveus._async_unregister_card(
        _teardown_hass(resources, remaining_entries=[self_entry]), "e1"
    )
    assert resources.items == []


async def test_card_resource_removal_never_raises_without_a_lovelace_resource_collection():
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(async_entries=lambda domain: []),
    )
    await eveus._async_unregister_card(hass, "e1")  # must not raise


async def test_card_resource_removal_swallows_errors():
    hass = object()  # missing every attribute the function touches
    await eveus._async_unregister_card(hass, "e1")  # must not raise


async def test_setup_registers_the_card_websocket_command_once():
    hass = _hass(FakeResources())
    with patch.object(eveus, "_async_register_card", AsyncMock()), patch.object(
        eveus.websocket_api, "async_register_command"
    ) as register:
        assert await eveus.async_setup(hass, {}) is True
    register.assert_called_once_with(hass, eveus._ws_card_entities)


def test_card_looks_entities_up_through_the_websocket_command():
    source = CARD.read_text(encoding="utf-8")
    assert 'type: "eveus/card_entities"' in source
    assert "_substate$" not in source, "no entity_id pattern matching"


def _card_function(source: str, name: str) -> str:
    start = source.index(f"  {name}(")
    return source[start:source.index("\n  }\n", start)]


def test_soc_steppers_stay_four_across_on_phone_widths():
    """One row of Initial / Target / Capacity / Loss; the stepper shrinks instead of wrapping."""
    source = CARD.read_text(encoding="utf-8")
    assert ".g4{grid-template-columns:repeat(4" in source
    assert ".g4{grid-template-columns:repeat(2" not in source


def test_tile_values_have_the_full_width_below_the_icon_and_label():
    """The icon no longer takes space away from a session's energy and cost."""
    source = CARD.read_text(encoding="utf-8")
    tile = _card_function(source, "_tile")
    assert '<div class="th"><ha-icon' in tile
    assert '${label}</span></div><span class="v">${value}' in tile


def test_soc_bar_marks_where_the_session_started():
    """The fill runs from Initial SOC, so the bar carries a tick there as well as at Target."""
    source = CARD.read_text(encoding="utf-8")
    bar = _card_function(source, "_bar")
    assert 'class="tg" style="left:${tgt}%"' in bar
    assert 'class="tg" style="left:${ini}%"' in bar


def test_soc_limit_toggle_is_not_under_the_initial_soc_stepper():
    """Initial SOC is the stepper touched most; a toggle directly below it gets hit by mistake."""
    source = CARD.read_text(encoding="utf-8")
    full = _card_function(source, "_full")
    row = full.split('<div class="g3">', 1)[1].split("</div>`", 1)[0]
    assert row.count("this._tile(") == 3
    assert row.index("t.socLimit") > row.rindex("this._tile("), "the toggle is the last tile of the row"


def test_values_wrap_instead_of_being_hidden_on_phone_widths():
    """Money and units must remain visible even when they need a second line."""
    source = CARD.read_text(encoding="utf-8")
    assert "text-overflow:ellipsis" not in source
    for selector in (".v", ".ch"):
        rule = source.split(f"\n{selector}{{", 1)[1].split("}", 1)[0]
        assert "flex-wrap:wrap" in rule
    assert ".ce{display:none}" not in source
    assert ".ce,.ce+.ar{display:none}" not in source


def test_editor_mode_options_match_the_integration_mode_names():
    """The integration calls its modes Advanced / Basic (Розширений / Базовий) under
    "Integration mode"; the card editor must use the same words, and no "auto"."""
    import json

    source = CARD.read_text(encoding="utf-8")
    form = _card_function(source, "static getConfigForm")
    assert '"auto", "basic"' not in form
    assert 'value: "advanced"' in form and 'value: "basic"' in form
    assert "computeLabel" in form
    translations = CARD.parents[1] / "translations"
    for lang in ("en", "uk"):
        t = json.loads((translations / f"{lang}.json").read_text(encoding="utf-8"))
        field = t["config"]["step"]["user"]["data"]["soc_mode"]
        advanced, basic = (t["selector"]["soc_mode"]["options"][m].split(" (")[0] for m in ("advanced", "basic"))
        for word in (field, advanced, basic):
            assert f'"{word}"' in source, f"{lang}: {word!r} missing from the card editor"


def test_compact_shows_session_cost_and_energy():
    source = CARD.read_text(encoding="utf-8")
    compact = _card_function(source, "_compact")
    assert '"sessionCost"' in compact
    assert '"sessionEnergy"' in compact


def test_compact_soc_bar_spans_the_card_below_the_row():
    """The bar is the last child of the compact card, not squeezed beside the state text."""
    source = CARD.read_text(encoding="utf-8")
    compact = _card_function(source, "_compact")
    assert 'class="cs"' not in compact
    assert compact.rstrip().endswith("${this._bar()}</div>`;")


def test_long_press_on_a_reading_opens_the_setting_behind_it():
    """SOC → Initial SOC, Time to SOC → Target SOC, Current → Charging Current."""
    source = CARD.read_text(encoding="utf-8")
    metrics = _card_function(source, "_metrics")
    for label, setting in (("t.soc", "initialSoc"), ("t.eta", "targetSoc"), ("t.current", "chargingCurrent")):
        line = next(ln for ln in metrics.splitlines() if f"label: {label}," in ln)
        assert f"hold: this._ids.{setting}" in line, label
    assert 'data-hold="${hold}"' in _card_function(source, "_tile")


def test_long_press_does_not_also_fire_the_tap():
    """The click that ends a long press must not toggle or open the reading's own dialog."""
    source = CARD.read_text(encoding="utf-8")
    assert '"pointerdown"' in source and '"contextmenu"' in source
    click = _card_function(source, "_onClick")
    assert "this._held" in click.split("\n", 2)[1], "the hold check comes first"


def test_to_goal_tile_spans_two_rows_with_energy_cost_and_finish():
    """Time to SOC grows into the space the toggles freed: energy, money and finish time."""
    source = CARD.read_text(encoding="utf-8")
    metrics = _card_function(source, "_metrics")
    line = next(ln for ln in metrics.splitlines() if "label: t.eta," in ln)
    assert 'cls: tall' in line
    for part in ("energyToTarget", "costToTarget", "this._finish()"):
        assert part in metrics, part
    assert ".t.tall{grid-row:span 2}" in source


def test_one_charge_and_stop_are_icon_only_buttons_sharing_one_cell():
    source = CARD.read_text(encoding="utf-8")
    controls = _card_function(source, "_controls")
    assert "label: t.one" not in controls and "label: t.stop" not in controls
    assert 'class="bt"' in controls
    assert "mdi:lightning-bolt-circle" in controls
    assert "STOP" in source and "<polygon" in source, "a road-style STOP sign"
    assert 'aria-label="${label}"' in controls and "t.one," in controls and "t.stop," in controls


def test_full_layout_does_not_repeat_the_to_goal_readings():
    source = CARD.read_text(encoding="utf-8")
    full = _card_function(source, "_full")
    assert "t.toTarget" not in full and "t.finish" not in full


def test_status_layout_keeps_one_row_tiles():
    source = CARD.read_text(encoding="utf-8")
    assert "this._metrics()" in _card_function(source, "_status")
    assert "this._metrics(true)" in _card_function(source, "_controls")
    assert 'const tall = extended ? "tall" : ""' in _card_function(source, "_metrics")


def test_basic_control_groups_session_and_shows_temperature():
    """Basic: Power over Temp on the left, one two-row Session tile (time, energy, money) in the centre."""
    source = CARD.read_text(encoding="utf-8")
    metrics = _card_function(source, "_metrics")
    line = next(ln for ln in metrics.splitlines() if "label: t.session, value: extended" in ln)
    assert "cls: tall" in line and "sessionEnergy" in metrics
    assert "label: t.temp" in metrics


def test_basic_session_tile_shows_voltage_on_its_third_line():
    source = CARD.read_text(encoding="utf-8")
    metrics = _card_function(source, "_metrics")
    assert 'num(this._s("voltage"))' in metrics
    assert "t.voltage" not in _card_function(source, "_full"), "no second Voltage tile in Full"
