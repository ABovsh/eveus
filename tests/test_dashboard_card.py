"""The bundled dashboard card: served and registered by the integration."""
from __future__ import annotations

import re
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
    assert "getConfigElement" in source
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


def _editor_function(source: str, name: str) -> str:
    """Both classes have a _render and a _resolve; this one reads the editor's."""
    return _card_function(source[source.index("class EveusCardEditor"):], name)


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


def test_full_only_adds_settings_to_control():
    """Full is Control plus the settings row -- it never repeats a reading Control already shows."""
    source = CARD.read_text(encoding="utf-8")
    full = _card_function(source, "_full")
    assert "this._controls() +" in full
    assert '<div class="g4">' in full, "four steppers, one row"
    assert "socLimit" not in source, "the SOC-limit toggle was dropped from the card"


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
    form = _editor_function(source, "_render")
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
    assert compact.rstrip().endswith("${this._bar()}${this._note()}${this._alerts()}</div>`;")


def test_long_press_on_a_reading_opens_the_setting_behind_it():
    """SOC -> Initial SOC, To target -> Target SOC, Current -> Charging Current."""
    source = CARD.read_text(encoding="utf-8")
    for tile, setting in (("_tSoc", "initialSoc"), ("_tGoal", "targetSoc"), ("_tCurrent", "chargingCurrent")):
        assert f"hold: this._ids.{setting}" in _card_function(source, tile), tile
    assert 'data-hold="${hold}"' in _card_function(source, "_tile")


def test_long_press_does_not_also_fire_the_tap():
    """The click that ends a long press must not toggle or open the reading's own dialog."""
    source = CARD.read_text(encoding="utf-8")
    assert '"pointerdown"' in source and '"contextmenu"' in source
    click = _card_function(source, "_onClick")
    assert "this._held" in click.split("\n", 2)[1], "the hold check comes first"


def test_to_goal_tile_spans_two_rows_with_energy_cost_and_finish():
    """To target grows into the space the toggles freed: energy, money and finish time."""
    source = CARD.read_text(encoding="utf-8")
    goal = _card_function(source, "_tGoal")
    for part in ("energyToTarget", "costToTarget", "this._finish()"):
        assert part in goal, part
    assert 'this._tGoal("tall")' in source, "the goal tile spans both rows where there are two"
    assert ".t.tall{grid-row:span 2}" in source


def test_one_charge_and_stop_share_one_cell_and_carry_a_caption():
    source = CARD.read_text(encoding="utf-8")
    run = _card_function(source, "_btnsRun")
    assert 'class="bt"' in run
    assert "t.one," in run and "t.stop," in run
    assert "mdi:lightning-bolt-circle" in run
    assert "STOP" in source and "<polygon" in source, "a road-style STOP sign"
    btn = _card_function(source, "_btn")
    assert '<span class="l">' in btn, "a caption; title= is invisible on touch"
    assert 'aria-label="${label}"' in btn


def test_full_layout_does_not_repeat_the_to_goal_readings():
    source = CARD.read_text(encoding="utf-8")
    full = _card_function(source, "_full")
    assert "t.toTarget" not in full and "t.finish" not in full


def test_status_and_control_share_one_to_target_block():
    """Status showed target -> eta and dropped energy, cost and the finish clock."""
    source = CARD.read_text(encoding="utf-8")
    for layout in ("_status", "_controls"):
        assert "this._tGoal(" in _card_function(source, layout), layout
    assert "extended" not in source, "one block, no density switch"


def test_basic_control_puts_the_session_between_the_two_switch_pairs():
    """Basic Control mirrors Advanced: switches, session, switches -- session in the middle."""
    source = CARD.read_text(encoding="utf-8")
    controls = _card_function(source, "_controls")
    basic = controls.split(": [", 1)[1]
    assert basic.index("_tSession(") > basic.index("_btnsCfg()")
    assert basic.index("_tSession(") < basic.index("_btnsRun()")


def test_voltage_and_temperature_ride_in_the_state_line_and_never_in_a_tile():
    """Both are what a bad charge is diagnosed with, so every layout carries them at no extra height."""
    source = CARD.read_text(encoding="utf-8")
    assert source.count('num(this._s("voltage"))') == 1, "one place builds the voltage"
    assert 'this._volt()' in _card_function(source, "_strip")
    assert 'this._temps()' in _card_function(source, "_strip")
    for tile in ("_tPower", "_tCurrent", "_tSession"):
        assert "voltage" not in _card_function(source, tile), tile
        assert "Temp" not in _card_function(source, tile), tile


def test_the_bar_never_paints_locally_restored_numbers_as_live_data():
    """Initial/Target SOC survive an outage; the bar must not read as a charge."""
    source = CARD.read_text(encoding="utf-8")
    bar = _card_function(source, "_bar")
    assert "this._online" in bar, "fill and base are gated on live charger data"
    assert "_online" in _card_function(source, "_socColor").split("\n")[1], "offline colour comes first"
    assert "get _online()" in source


def test_purple_means_the_target_is_reached_not_merely_idle():
    source = CARD.read_text(encoding="utf-8")
    colour = _card_function(source, "_socColor")
    assert "C.purple" in colour and "_reached" in colour
    assert "background:${C.purple}" not in source, "the base segment is not a second purple meaning"
    assert "this._charging ? this._socColor() : C.purple" not in source


def test_control_shows_the_state_beside_the_soc_bar():
    source = CARD.read_text(encoding="utf-8")
    assert "this._strip()" in _card_function(source, "_controls")
    strip = _card_function(source, "_strip")
    assert "this._stateText()" in strip and "this._bar()" in strip
    assert "t.state" not in _card_function(source, "_full"), "Basic full adds nothing to control"


def test_the_reason_the_charger_is_not_charging_is_shown():
    """`not_charging_reason` was computed and dropped on a class that STYLE never defined."""
    source = CARD.read_text(encoding="utf-8")
    assert '"reason"' in _card_function(source, "_note")
    assert "this._note()" in _card_function(source, "_strip")
    assert 'cls: reason ? "wide" : ""' not in source


def test_state_and_progress_sit_in_one_strip_in_every_layout():
    source = CARD.read_text(encoding="utf-8")
    for layout in ("_status", "_controls"):
        assert "this._strip()" in _card_function(source, layout), layout
    compact = _card_function(source, "_compact")
    assert 'class="sst"' in compact and "this._bar()" in compact, "compact inlines the same strip"
    assert "t.state" not in _card_function(source, "_status"), "no separate State tile"


def test_each_mode_shows_the_session_once():
    """Basic printed "Session" on both the duration tile and the energy/cost tile."""
    source = CARD.read_text(encoding="utf-8")
    assert source.count("label: this._t.session,") == 2, "one per mode, both inside _tSession"
    session = _card_function(source, "_tSession")
    assert session.count("label: this._t.session,") == 2


def test_battery_icons_only_appear_where_soc_exists():
    source = CARD.read_text(encoding="utf-8")
    assert "_batteryIcon" not in _card_function(source, "_tPower")
    assert "this._advanced" in _card_function(source, "_batteryIcon")


def test_alerts_reach_the_compact_layout_too():
    source = CARD.read_text(encoding="utf-8")
    assert "this._alerts()" in _card_function(source, "_compact")
    assert "this._online" in _card_function(source, "_alerts"), "stale readings raise no alarm"


def test_an_idle_to_target_tile_shows_the_gap_instead_of_dashes():
    source = CARD.read_text(encoding="utf-8")
    assert "eta ? this._pair" in _card_function(source, "_tGoal"), "charging shows the time, idle shows now -> target"


def test_rows_with_no_value_are_dropped():
    source = CARD.read_text(encoding="utf-8")
    assert "_rows(" in _card_function(source, "_tGoal")
    assert "filter(Boolean)" in _card_function(source, "_rows")


def test_units_follow_one_spacing_rule():
    """One rule for every unit the card prints: no space between number and unit."""
    source = CARD.read_text(encoding="utf-8")
    spaced = re.findall(r"\}\s+(?:kWh|kW|V|A)\b", source)
    assert not spaced, spaced
    for helper in ("const kwh =", "const kw =", "const dur ="):
        assert helper in source, helper


def test_one_icon_per_concept():
    source = CARD.read_text(encoding="utf-8")
    for tile, icon in (("_tPower", "mdi:flash"), ("_tCurrent", "mdi:current-ac"), ("_tGoal", "mdi:flag-checkered")):
        assert icon in _card_function(source, tile), tile
    assert 'icon: "mdi:sine-wave"' not in source, "sine-wave is voltage, not power"
    assert 'icon: "mdi:timer-outline", label: this._t.eta' not in source, "the timer is duration, not the goal"


def test_the_slider_is_the_current_and_never_shares_a_layout_with_the_current_tile():
    """The slider sets the current, so it says so; the tile only exists where there is no slider."""
    source = CARD.read_text(encoding="utf-8")
    assert "this._t.current" in _card_function(source, "_slider")
    assert "setCurrent" not in source
    controls = _card_function(source, "_controls")
    assert "_tCurrent()" not in controls, "the slider already shows it"
    assert "_tCurrent()" in _card_function(source, "_status")


def test_stopping_confirms_inside_the_card():
    source = CARD.read_text(encoding="utf-8")
    assert "confirm(" not in source.replace("_confirm", "").replace("stopConfirm", "")
    assert "this._confirm" in _card_function(source, "_onClick")


def test_a_hidden_long_press_setting_is_marked_on_the_tile():
    source = CARD.read_text(encoding="utf-8")
    assert ".t[data-hold]::before" in source


def test_interactive_tiles_are_keyboard_reachable():
    source = CARD.read_text(encoding="utf-8")
    assert 'tabindex="0"' in _card_function(source, "_tile")
    assert 'role="button"' in _card_function(source, "_tile")
    assert '"keydown"' in source


def test_controls_are_inert_while_the_charger_is_unreachable():
    source = CARD.read_text(encoding="utf-8")
    click = _card_function(source, "_onClick")
    assert "this._online" in click
    assert "disabled" in _card_function(source, "_slider")


def test_basic_has_no_full_layout():
    """Basic has no SOC settings, so Full would be Control with a heading; it renders Control."""
    source = CARD.read_text(encoding="utf-8")
    full = _card_function(source, "_full")
    assert "if (!this._advanced) return this._controls();" in full
    size = _card_function(source, "getCardSize")
    assert '"control" : this._config.layout' in size, "and it reports Control's height"


def test_card_size_matches_what_is_rendered():
    source = CARD.read_text(encoding="utf-8")
    size = _card_function(source, "getCardSize")
    assert "_advanced" in size, "Basic full renders control; it is not five rows"


def test_money_uses_the_currency_the_entity_reports():
    """HA reports the code (UAH), not the sign, so the card maps the ones it knows."""
    source = CARD.read_text(encoding="utf-8")
    money = _card_function(source, "_money")
    assert "unit_of_measurement" in money
    assert 'UAH: "\u20b4"' in money and 'EUR: "\u20ac"' in money
    assert "|| raw" in money, "an unknown code is printed as it comes"


def test_offline_says_so_and_for_how_long():
    source = CARD.read_text(encoding="utf-8")
    assert "t.offline" in _card_function(source, "_stateText")
    assert "last_changed" in _card_function(source, "_since")
    for lang in ("en", "uk"):
        assert re.search(r'offline: "[^"]+"', source.split(f"  {lang}: {{", 1)[1]), lang


def test_both_modes_carry_the_same_two_config_switches():
    """OCPP and "no limit" are switches, so they light up like One charge does."""
    source = CARD.read_text(encoding="utf-8")
    cfg = _card_function(source, "_btnsCfg")
    assert "t.ocpp" in cfg and "t.noLimits" in cfg
    assert '"connect_to_ocpp"' in source and '"limit_disable_all"' in source
    controls = _card_function(source, "_controls")
    advanced = controls.split("? [", 1)[1].split("\n", 1)[0]
    basic = controls.split("      : [", 1)[1].split("\n", 1)[0]
    assert "_btnsCfg()" in advanced and "_btnsCfg()" in basic, "same pair in both modes"


def test_one_separator_between_every_reading():
    """The dot is a constant, so the compact row and the state line cannot drift apart."""
    source = CARD.read_text(encoding="utf-8")
    assert "const SEP = " in source
    assert "chips.join(SEP)" in _card_function(source, "_compact")
    assert "join(SEP)" in _card_function(source, "_strip")
    assert "join(SEP)" in _card_function(source, "_temps")


def test_the_compact_row_shrinks_before_it_wraps():
    """A narrow card drops a point of type rather than pushing the readings onto a second line."""
    source = CARD.read_text(encoding="utf-8")
    assert "@container (max-width: 356px){.ch,.cp .sst{font-size:12px}" in source


def test_the_editor_offers_full_only_where_it_adds_something():
    """Basic has no SOC settings, so `full` must not be offered as a layout there."""
    source = CARD.read_text(encoding="utf-8")
    render = _editor_function(source, "_render")
    assert 'if (!this._advanced && this._config.layout !== "full") delete layouts.full;' in render
    assert "static getConfigElement()" in source
    assert "static getConfigForm" not in source, "a static form cannot see the mode"
    editor = _editor_function(source, "_resolve")
    assert "eveus/card_entities" in editor and "soc_percent" in editor, "an unset mode follows the integration"
