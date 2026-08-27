"""Shipped automation blueprints stay wired to real integration triggers."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from custom_components.eveus.const import DOMAIN
from custom_components.eveus.device_trigger import _EVENT_FOR_TYPE

BLUEPRINT_DIR = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "eveus"


def _load(path: Path) -> tuple[dict, list[str]]:
    """Parse a blueprint, collecting the ``!input`` names it references.

    ``!input`` is a Home Assistant tag, so SafeLoader would refuse the file;
    resolving it to ``None`` keeps the surrounding structure inspectable.
    """
    used: list[str] = []

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor(
        "!input", lambda loader, node: used.append(loader.construct_scalar(node))
    )
    return yaml.load(path.read_text(encoding="utf-8"), Loader=Loader), used


def _blueprints() -> list[Path]:
    return sorted(BLUEPRINT_DIR.glob("*.yaml"))


def test_blueprints_are_shipped() -> None:
    """The README sends users to this directory by URL; it must not be empty."""
    assert _blueprints()


@pytest.mark.parametrize("path", _blueprints(), ids=lambda p: p.name)
def test_blueprint_inputs_match_their_uses(path: Path) -> None:
    """An undeclared ``!input`` makes HA reject the import; an unused one is dead UI."""
    data, used = _load(path)
    assert data["blueprint"]["domain"] == "automation"
    declared = set(data["blueprint"]["input"])
    assert set(used) == declared


@pytest.mark.parametrize("path", _blueprints(), ids=lambda p: p.name)
def test_device_triggers_name_real_event_types(path: Path) -> None:
    """A mistyped device-trigger type never fires and never errors — pin it here."""
    data, _ = _load(path)
    for trigger in data.get("triggers", data.get("trigger", [])):
        if trigger.get("trigger", trigger.get("platform")) != "device":
            continue
        assert trigger["domain"] == DOMAIN
        assert trigger["type"] in _EVENT_FOR_TYPE
