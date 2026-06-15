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


def test_master_disable_switches_soc_off():
    # Pressing "Disable limits" (suspendLimits=1) must switch the SOC limit off
    # too, the same way the charger drops its own Time/Energy/Cost enable flags.
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 0}
    asyncio.run(sw.async_turn_on())
    controller.set_enabled.reset_mock()
    sw.async_write_ha_state.reset_mock()

    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()

    assert sw.is_on is False
    controller.set_enabled.assert_called_with(False)
    sw.async_write_ha_state.assert_called()


def test_can_re_enable_during_suspend_without_being_reforced_off():
    # While suspended you may flip it back on (the controller still stands down);
    # the switch must not be force-reset on every subsequent poll.
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()            # suspend observed; switch stays off
    asyncio.run(sw.async_turn_on())            # user re-enables during suspend
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()            # still suspended, no new transition
    assert sw.is_on is True
