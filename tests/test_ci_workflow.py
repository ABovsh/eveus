"""CI workflow contracts."""
from __future__ import annotations

from pathlib import Path
import re


def _latest_ha_python_version() -> tuple[int, ...]:
    workflow = Path(".github/workflows/validate.yaml").read_text(encoding="utf-8")
    latest_leg = re.search(
        r'- python-version: "([^"]+)"\n\s+ha-pin: ""\n\s+ha-label: "latest"',
        workflow,
    )
    assert latest_leg is not None, "Validate workflow must keep a latest-HA matrix leg"
    return tuple(int(part) for part in latest_leg.group(1).split("."))


def test_latest_ha_ci_leg_uses_python_that_can_install_latest_homeassistant() -> None:
    """The drift canary must be able to install current unpinned Home Assistant."""
    assert _latest_ha_python_version() >= (3, 14, 2)
