"""Shipped automation blueprints stay wired to real integration triggers."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from homeassistant.helpers import config_validation as cv
from homeassistant.util import yaml as yaml_util

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


@pytest.mark.parametrize(
    ("actions", "expected_count"),
    [
        ([{"action": "persistent_notification.create", "data": {"message": "hello"}}], 1),
        ([{"delay": "00:00:01"}, {"action": "persistent_notification.create", "data": {"message": "hello"}}], 2),
    ],
)
def test_session_notification_action_input_is_a_valid_sequence(
    actions: list[dict], expected_count: int
) -> None:
    """An action selector returns a list that must remain flat after substitution."""
    blueprint = yaml_util.load_yaml(BLUEPRINT_DIR / "notify_session.yaml")
    expanded = yaml_util.substitute(
        blueprint,
        {"charger": "charger-device", "notify_action": actions, "currency": "UAH"},
    )

    assert len(cv.SCRIPT_SCHEMA(expanded["actions"])) == expected_count


def test_low_house_battery_blueprint_actions_validate_after_substitution() -> None:
    """The other shipped blueprint also yields a valid action sequence."""
    blueprint = yaml_util.load_yaml(BLUEPRINT_DIR / "stop_on_low_house_battery.yaml")
    expanded = yaml_util.substitute(
        blueprint,
        {
            "battery_soc": "sensor.house_battery",
            "threshold": 40,
            "stop_switch": "switch.charger_stop_charging",
        },
    )

    assert len(cv.SCRIPT_SCHEMA(expanded["actions"])) == 1
