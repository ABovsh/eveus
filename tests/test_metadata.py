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
    manifest = json.loads(
        (ROOT / "custom_components" / "eveus" / "manifest.json").read_text()
    )
    readme = (ROOT / "README.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert manifest["version"] == "4.0.1"
    assert "version-4.0.1-blue" in readme
    assert "## 4.0.1" in changelog


def test_hacs_metadata_has_allowed_keys_only() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text())

    assert set(hacs) == {
        "name",
        "content_in_root",
        "render_readme",
        "homeassistant",
    }
    assert hacs["homeassistant"] == "2024.4.0"


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


def test_brand_images_are_complete_and_sized() -> None:
    brand_dir = ROOT / "custom_components" / "eveus" / "brand"
    expected_sizes = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "logo.png": (256, 256),
        "logo@2x.png": (512, 512),
    }

    for filename, expected_size in expected_sizes.items():
        path = brand_dir / filename
        assert path.exists(), filename
        with Image.open(path) as image:
            assert image.size == expected_size
            assert image.mode == "RGBA"


def test_macos_metadata_files_are_not_packaged() -> None:
    assert not list((ROOT / "custom_components" / "eveus").rglob(".DS_Store"))
