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


def test_enabling_master_switches_soc_off():
    # Row #3: turning "Disable limits" on flips the SOC limit off too.
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


def test_can_reenable_while_suspended_and_master_off_never_changes_it():
    # Row #4: re-enable while suspended (real toggle, stays on across polls).
    # Rows #5/#6: turning the master off never changes the switch by itself.
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()              # suspended baseline
    asyncio.run(sw.async_turn_on())             # re-enable during suspend
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()             # still suspended -> not re-flipped
    assert sw.is_on is True
    sw._updater.data = {"suspendLimits": 0}
    sw._handle_coordinator_update()             # master off -> unchanged
    assert sw.is_on is True


def test_master_off_does_not_auto_enable_an_off_switch():
    # Row #6: SOC off while suspended, master off -> stays off (no auto-enable).
    sw, controller = _make()
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()
    sw._updater.data = {"suspendLimits": 0}
    sw._handle_coordinator_update()
    assert sw.is_on is False
