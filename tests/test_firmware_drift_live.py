"""Opt-in firmware-drift check against the real charger.

`test_real_payload_schema.py` pins every getter against a STATIC capture. That
capture ages: it was taken on GRM070A-R3.05.2 while the charger in service had
already moved to R3.05.4, and nothing in the repo could tell.

This asks the live charger directly and compares its field set to the fixture.
It is skipped unless EVEUS_LIVE_HOST is set, so CI and every offline run are
unaffected. EVEUS_PASS is optional: the firmware serves /main on the LAN without
authentication, and credentials are only sent when one is supplied.

Run it with:
    EVEUS_LIVE_HOST=192.168.3.39 .venv/bin/pytest tests/test_firmware_drift_live.py -v -m live
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "real_main_response.json"

HOST = os.environ.get("EVEUS_LIVE_HOST")
PASSWORD = os.environ.get("EVEUS_PASS")
USERNAME = os.environ.get("EVEUS_LIVE_USER", "eveus")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not HOST,
        reason="set EVEUS_LIVE_HOST to run the live firmware-drift check",
    ),
]


def _fetch_live() -> dict:
    """Fetch /main from the real charger using the standard library only.

    urllib, not aiohttp: conftest stubs aiohttp out for the whole unit suite,
    so a genuine network call has to bypass it.
    """
    import base64
    import urllib.request

    headers: dict[str, str] = {}
    if PASSWORD:
        credentials = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"
    request = urllib.request.Request(
        f"http://{HOST}/main", data=b"", headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8", "replace"))


def test_live_charger_field_set_matches_the_fixture() -> None:
    """The fixture must describe the firmware actually in service.

    A dropped field breaks a getter silently — it returns None and the sensor
    goes unknown. A new field is a capability nobody has looked at yet.
    """
    live = _fetch_live()
    fixture = json.loads(FIXTURE.read_text())
    version = str(live.get("verFWMain", "?")).strip()

    dropped = sorted(set(fixture) - set(live))
    added = sorted(set(live) - set(fixture))

    assert not dropped, (
        f"firmware {version} no longer reports {dropped} — every getter reading "
        f"those fields now returns None. Refresh "
        f"tests/fixtures/real_main_response.json and fix the getters."
    )
    assert not added, (
        f"firmware {version} reports new fields {added}. Refresh "
        f"tests/fixtures/real_main_response.json; decide separately whether any "
        f"of them is worth exposing."
    )
