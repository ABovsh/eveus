"""Tests for release and metadata files."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_domain_matches_integration_directory() -> None:
    manifest_path = ROOT / "custom_components" / "eveus" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest["domain"] == manifest_path.parent.name
    assert manifest["integration_type"] == "device"


def test_manifest_readme_and_changelog_versions_match() -> None:
    """Manifest, README badge and CHANGELOG must agree on the release line.

    The release line is the X.Y.Z version published in the manifest, README,
    and CHANGELOG.
    """
    import re

    manifest = json.loads(
        (ROOT / "custom_components" / "eveus" / "manifest.json").read_text()
    )
    readme = (ROOT / "README.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    version = manifest["version"]
    base = re.match(r"^(\d+\.\d+\.\d+)", version)
    assert base is not None, version
    base_version = base.group(1)

    assert f"version-{base_version}-blue" in readme
    assert f"## {version}" in changelog


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
        ("input_number.ev_initial_soc", "number.eveus_initial_soc"),
        ("input_number.ev_target_soc", "number.eveus_target_soc"),
        ("input_number.ev_battery_capacity", "number.eveus_battery_capacity"),
        ("input_number.ev_soc_correction", "number.eveus_soc_correction"),
    ):
        assert f"`{old_entity}` → `{new_entity}`" in description


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
    for name in ("Car Connected", "Session Active"):
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


def test_unit_suite_disables_homeassistant_pytest_plugin(pytestconfig) -> None:
    """The fast unit path must not load the Home Assistant pytest plugin."""

    assert not pytestconfig.pluginmanager.hasplugin("homeassistant")
