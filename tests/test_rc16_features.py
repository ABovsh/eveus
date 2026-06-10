"""Tests for the 4.13 feature round: device firmware metadata, target-SOC
energy/cost sensors, transition burst polling, progressive offline backoff,
and the charger clock-drift notice."""
from __future__ import annotations

import conftest  # noqa: F401  (installs HA stubs)

from custom_components.eveus import utils
from conftest import TEST_HOST


class TestDeviceFirmwareMetadata:
    """Wi-Fi firmware must not masquerade as the charger hardware revision."""

    def test_device_info_omits_hw_version(self):
        info = utils.get_device_info(
            TEST_HOST,
            {"verFWMain": "GRM070A-R3.05.2", "verFWWifi": "1PGRW001A-R3.05.2"},
        )
        assert "hw_version" not in info
        assert info["sw_version"] == "GRM070A-R3.05.2"

    def test_device_info_omits_hw_version_even_with_legacy_hardware_key(self):
        info = utils.get_device_info(TEST_HOST, {"verFWMain": "x1", "hardware": "h1"})
        assert "hw_version" not in info
