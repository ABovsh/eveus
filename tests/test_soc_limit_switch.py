from unittest.mock import MagicMock
import asyncio

from custom_components.eveus.switch import EveusSocLimitSwitch


def _make():
    controller = MagicMock()
    updater = MagicMock()
    updater.config_entry = MagicMock()
    sw = EveusSocLimitSwitch(updater, controller, device_number=1)
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()
    return sw, controller


def test_turn_on_enables_controller_and_persists():
    sw, controller = _make()
    asyncio.run(sw.async_turn_on())
    assert sw.is_on is True
    controller.set_enabled.assert_called_with(True)


def test_turn_off_disables_controller():
    sw, controller = _make()
    asyncio.run(sw.async_turn_on())
    asyncio.run(sw.async_turn_off())
    assert sw.is_on is False
    controller.set_enabled.assert_called_with(False)


def test_unique_id_slug():
    sw, _ = _make()
    assert sw.unique_id == "eveus_limit_soc_enabled"
