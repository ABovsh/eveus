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


def test_master_disable_shows_soc_off_then_resumes():
    # "Disable limits" (suspendLimits=1) suppresses the SOC limit display the
    # same way it suppresses the charger's own limits; releasing it restores the
    # user's choice (it must NOT stay off).
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 0}
    asyncio.run(sw.async_turn_on())
    assert sw.is_on is True
    sw.async_write_ha_state.reset_mock()

    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()
    assert sw.is_on is False                    # suppressed while master is on
    sw.async_write_ha_state.assert_called()

    sw._updater.data = {"suspendLimits": 0}
    sw._handle_coordinator_update()
    assert sw.is_on is True                     # returns to the user's choice


def test_enable_intent_during_suspend_applies_after_release():
    # Flipping it on while suspended records the intent but stays visibly off
    # until the master is released, then takes effect.
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()
    asyncio.run(sw.async_turn_on())
    assert sw.is_on is False
    sw._updater.data = {"suspendLimits": 0}
    sw._handle_coordinator_update()
    assert sw.is_on is True
