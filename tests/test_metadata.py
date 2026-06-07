"""Tests for release and metadata files."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.util import slugify
from PIL import Image

from conftest import TEST_HOST
from custom_components.eveus.common_base import BaseEveusEntity
from custom_components.eveus.safety import POLICIES


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_domain_matches_integration_directory() -> None:
    manifest_path = ROOT / "custom_components" / "eveus" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest["domain"] == manifest_path.parent.name
    assert manifest["integration_type"] == "device"


def test_manifest_readme_and_changelog_versions_match() -> None:
    """Manifest, README badge and CHANGELOG must agree.

    Home Assistant's manifest validator accepts final ``X.Y.Z`` versions and
    PEP 440-style prereleases such as ``X.Y.Zb1``. The README badge mirrors the
    manifest version exactly. Prereleases document changes under the matching
    final-version CHANGELOG heading.
    """
    import re

    manifest = json.loads(
        (ROOT / "custom_components" / "eveus" / "manifest.json").read_text()
    )
    readme = (ROOT / "README.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    version = manifest["version"]
    final_match = re.fullmatch(r"(\d+\.\d+\.\d+)", version)
    prerelease_match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:a|b|rc)\d+", version)
    assert final_match or prerelease_match, version

    # README badge mirrors the manifest version verbatim (shields escapes - as --).
    badge_version = version.replace("-", "--")
    assert f"version-{badge_version}-blue" in readme

    changelog_version = (
        prerelease_match.group(1) if prerelease_match is not None else version
    )
    assert f"## {changelog_version}" in changelog


def test_hacs_metadata_has_allowed_keys_only() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text())

    assert set(hacs) == {
        "name",
        "content_in_root",
        "render_readme",
        "homeassistant",
    }
    assert hacs["homeassistant"] == "2025.1.0"


def test_translation_state_attributes_use_dictionary_shape() -> None:
    translations = json.loads(
        (ROOT / "custom_components" / "eveus" / "translations" / "en.json").read_text()
    )
    state_attributes = translations["entity"]["number"]["charging_current"][
        "state_attributes"
    ]

    assert state_attributes["min"] == {"name": "Minimum Current"}
    assert state_attributes["max"] == {"name": "Maximum Current"}


def test_repair_issue_translations_are_present() -> None:
    translations = json.loads(
        (ROOT / "custom_components" / "eveus" / "translations" / "en.json").read_text()
    )

    assert "invalid_config" in translations["issues"]
    assert "fix_flow" in translations["issues"]["invalid_config"]


def test_soc_dashboard_repair_issue_lists_exact_entity_replacements() -> None:
    translations = json.loads(
        (ROOT / "custom_components" / "eveus" / "translations" / "en.json").read_text()
    )
    strings = json.loads(
        (ROOT / "custom_components" / "eveus" / "strings.json").read_text()
    )

    description = translations["issues"]["soc_dashboard_update"]["description"]
    assert strings["issues"]["soc_dashboard_update"]["description"] == description
    for old_entity, new_entity in (
        ("input_number.ev_initial_soc", "number.eveus_ev_charger_initial_soc"),
        ("input_number.ev_target_soc", "number.eveus_ev_charger_target_soc"),
        ("input_number.ev_battery_capacity", "number.eveus_ev_charger_battery_capacity"),
        ("input_number.ev_soc_correction", "number.eveus_ev_charger_soc_correction"),
    ):
        assert f"`{old_entity}` → `{new_entity}`" in description


def test_entity_key_matches_slugify_for_all_entity_names():
    names = ['Car Connected', 'OCPP Connected', 'Session Active',
             'Connection Quality', 'Active Rate Cost']
    for name in names:
        assert name.lower().replace(' ', '_') == slugify(name)


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_")


def _expected_entity_translation_keys() -> dict[str, set[str]]:
    """Every entity the integration builds, grouped by platform → translation_key.

    Mirrors the `_attr_translation_key = ENTITY_NAME.lower().replace(" ", "_")`
    rule in common_base, so a new entity that forgets its translation block fails
    here (and in hassfest) before release.
    """
    import sys

    sys.path.insert(0, str(ROOT))
    from custom_components.eveus.sensor_definitions import create_sensor_specifications
    from custom_components.eveus.switch import SWITCH_DESCRIPTIONS
    from custom_components.eveus.time import TIME_DESCRIPTIONS

    expected: dict[str, set[str]] = {p: set() for p in (
        "sensor", "number", "switch", "time", "button", "select", "binary_sensor"
    )}
    for spec in create_sensor_specifications(phases=3):
        expected["sensor"].add(_slug(spec.name))
    for name in ("SOC Energy", "SOC Percent", "Time to Target SOC", "Charging Finish Time"):
        expected["sensor"].add(_slug(name))
    for name in ("Charging Current", "Initial SOC", "Target SOC", "Battery Capacity", "SOC Correction"):
        expected["number"].add(_slug(name))
    for desc in SWITCH_DESCRIPTIONS:
        expected["switch"].add(_slug(desc.name))
    for desc in TIME_DESCRIPTIONS:
        expected["time"].add(_slug(desc.name))
    for name in ("Force Refresh", "Reset Counter A", "Reset Counter B", "Sync Time"):
        expected["button"].add(_slug(name))
    expected["select"].add(_slug("Time Zone"))
    for name in ("Car Connected", "Session Active", "OCPP Connected"):
        expected["binary_sensor"].add(_slug(name))
    return expected


def test_every_entity_has_a_translation_name() -> None:
    """en.json must carry a name for every entity translation_key the code emits."""
    en = json.loads(
        (ROOT / "custom_components" / "eveus" / "translations" / "en.json").read_text()
    )
    entity = en["entity"]
    for platform, keys in _expected_entity_translation_keys().items():
        present = set(entity.get(platform, {}))
        missing = keys - present
        assert not missing, f"{platform}: missing translation names for {sorted(missing)}"
        extra = present - keys
        assert not extra, f"{platform}: stale translation names for {sorted(extra)}"
        for key in keys:
            assert entity[platform][key].get("name"), f"{platform}.{key} has no name"


def test_entity_translations_are_consistent_across_locales() -> None:
    """strings.json, en.json and uk.json expose the exact same entity key tree."""
    base = ROOT / "custom_components" / "eveus"
    strings = json.loads((base / "strings.json").read_text())["entity"]
    en = json.loads((base / "translations" / "en.json").read_text())["entity"]
    uk = json.loads((base / "translations" / "uk.json").read_text())["entity"]

    def name_paths(section: dict) -> set[str]:
        paths: set[str] = set()
        for platform, ents in section.items():
            for key, body in ents.items():
                paths.add(f"{platform}/{key}")
                for attr in body.get("state_attributes", {}):
                    paths.add(f"{platform}/{key}/{attr}")
        return paths

    assert name_paths(strings) == name_paths(en) == name_paths(uk)


def _flatten_translation_paths(section: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in section.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths.update(_flatten_translation_paths(value, path))
        else:
            paths.add(path)
    return paths


def test_translation_trees_are_consistent_across_locales() -> None:
    """strings.json, en.json and uk.json expose the same translation keys."""
    base = ROOT / "custom_components" / "eveus"
    strings = json.loads((base / "strings.json").read_text())
    en = json.loads((base / "translations" / "en.json").read_text())
    uk = json.loads((base / "translations" / "uk.json").read_text())

    assert _flatten_translation_paths(strings) == _flatten_translation_paths(en)
    assert _flatten_translation_paths(en) == _flatten_translation_paths(uk)


def test_ocpp_warning_is_a_repair_issue_translation() -> None:
    """The OCPP warning is created through the issue registry, not options flow."""
    base = ROOT / "custom_components" / "eveus"
    for relative in (
        "strings.json",
        "translations/en.json",
        "translations/uk.json",
    ):
        translations = json.loads((base / relative).read_text())
        assert "ocpp_enabled" in translations["issues"]
        assert "ocpp_enabled" not in translations.get("options", {})


def test_battery_low_warning_is_a_repair_issue_translation() -> None:
    """The low RTC-battery warning is a repair issue with title + description."""
    base = ROOT / "custom_components" / "eveus"
    for relative in (
        "strings.json",
        "translations/en.json",
        "translations/uk.json",
    ):
        translations = json.loads((base / relative).read_text())
        issue = translations["issues"]["battery_low"]
        assert issue["title"]
        assert issue["description"]


def test_all_safety_repair_issue_translations_are_present() -> None:
    base = ROOT / "custom_components" / "eveus"
    expected = {f"safety_{policy.key}" for policy in POLICIES}
    loaded = {}
    for relative in ("strings.json", "translations/en.json", "translations/uk.json"):
        translations = json.loads((base / relative).read_text())
        loaded[relative] = translations
        for key in expected:
            issue = translations["issues"][key]
            assert issue["title"].startswith("Eveus:")
            assert len(issue["title"]) <= 90
            assert len(issue["description"]) <= 550

    assert all(
        "What to do:\n\n-"
        in loaded["translations/en.json"]["issues"][key]["description"]
        for key in expected
    )
    assert all(
        "Що зробити:\n\n-"
        in loaded["translations/uk.json"]["issues"][key]["description"]
        for key in expected
    )
    assert all(
        loaded["strings.json"]["issues"][key]
        == loaded["translations/en.json"]["issues"][key]
        for key in expected
    )


def test_ukrainian_translation_has_no_known_untranslated_ui_phrases() -> None:
    """Guard against the known English phrases left in uk.json."""
    uk_text = (
        ROOT / "custom_components" / "eveus" / "translations" / "uk.json"
    ).read_text()

    for phrase in (
        "Connect to OCPP",
        " or ",
        "Settings → Devices & Services",
        "Charger Model",
        "Username",
        "Password",
    ):
        assert phrase not in uk_text


def test_brand_images_are_complete_and_sized() -> None:
    brand_dir = ROOT / "custom_components" / "eveus" / "brand"
    expected_sizes = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
    }

    for filename, expected_size in expected_sizes.items():
        path = brand_dir / filename
        assert path.exists(), filename
        with Image.open(path) as image:
            assert image.size == expected_size
            assert image.mode == "RGBA"


def test_macos_metadata_files_are_not_packaged() -> None:
    assert not list((ROOT / "custom_components" / "eveus").rglob(".DS_Store"))


def test_manifest_has_quality_scale() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "eveus" / "manifest.json").read_text()
    )
    assert "quality_scale" in manifest, "manifest.json must declare quality_scale"


def test_manifest_has_loggers() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "eveus" / "manifest.json").read_text()
    )
    assert "loggers" in manifest, "manifest.json must declare loggers"
    assert "custom_components.eveus" in manifest["loggers"]


def test_device_registry_finalized_once_for_shared_updater(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firmware finalization writes the shared device registry row only once."""

    class Updater:
        host = TEST_HOST
        scheme = "http"
        available = True
        last_update_success = True
        _device_registry_finalized = False

        def __init__(self) -> None:
            self.data = {}

        def async_add_listener(self, *args: object, **kwargs: object):
            return lambda: None

    class PowerEntity(BaseEveusEntity):
        ENTITY_NAME = "Power"

    class VoltageEntity(BaseEveusEntity):
        ENTITY_NAME = "Voltage"

    updater = Updater()
    entities = [PowerEntity(updater), VoltageEntity(updater)]
    for entity in entities:
        entity.hass = object()

    updates: list[tuple[str, dict[str, object]]] = []

    class Registry:
        def async_get_device(self, *, identifiers):
            assert identifiers == {("eveus", TEST_HOST)}
            return SimpleNamespace(id="device-id")

        def async_update_device(self, device_id, **kwargs):
            updates.append((device_id, kwargs))

    monkeypatch.setattr(
        "custom_components.eveus.common_base.dr.async_get",
        lambda hass: Registry(),
    )

    for entity in entities:
        entity._maybe_finalize_device_info()

    updater.data = {
        "verFWMain": "R3.05.2",
        "verFWWifi": "W1.0",
        "serialNum": "EV-12345",
    }
    for entity in entities:
        entity._maybe_finalize_device_info()

    assert len(updates) == 1


def test_unit_suite_disables_homeassistant_pytest_plugin(pytestconfig) -> None:
    """The fast unit path must not load the Home Assistant pytest plugin."""

    assert not pytestconfig.pluginmanager.hasplugin("homeassistant")


def test_ground_repair_copy_explains_independent_conditions_and_switch() -> None:
    base = ROOT / "custom_components" / "eveus"
    en = json.loads((base / "translations" / "en.json").read_text())
    uk = json.loads((base / "translations" / "uk.json").read_text())

    en_missing = en["issues"]["safety_ground_missing"]["description"]
    en_disabled = en["issues"]["safety_ground_control_disabled"]["description"]
    assert "independently" in en_missing
    assert "**Ground Protection** switch" in en_disabled
    assert "separate" in en_disabled

    uk_missing = uk["issues"]["safety_ground_missing"]["description"]
    uk_disabled = uk["issues"]["safety_ground_control_disabled"]["description"]
    assert "незалежно" in uk_missing
    assert "перемикач **Захист заземлення**" in uk_disabled
    assert "окреме" in uk_disabled


def test_thermal_repair_copy_explains_early_warning_before_stop() -> None:
    base = ROOT / "custom_components" / "eveus"
    documents = (
        json.loads((base / "strings.json").read_text()),
        json.loads((base / "translations" / "en.json").read_text()),
        json.loads((base / "translations" / "uk.json").read_text()),
    )

    for document in documents:
        for key in ("safety_box_overheat", "safety_plug_overheat"):
            description = document["issues"][key]["description"]
            assert "80 °C" in description
            assert "85 °C" in description
