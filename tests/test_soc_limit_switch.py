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


def test_switch_state_is_independent_of_master_disable():
    # The SOC switch is an honest, independent toggle (like the global limit
    # switches): "Disable limits" must NOT change its displayed state. You can
    # turn it on while suspended (the limit is ignored by the controller), and
    # releasing "Disable limits" leaves it exactly where you left it — no
    # auto-enable.
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 1}     # master "Disable limits" on
    asyncio.run(sw.async_turn_on())
    assert sw.is_on is True                      # still freely enabled while suspended

    sw._updater.data = {"suspendLimits": 0}      # master released
    sw._handle_coordinator_update()
    assert sw.is_on is True                      # unchanged — not auto-toggled

    asyncio.run(sw.async_turn_off())
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()
    assert sw.is_on is False                      # off stays off under the master too
