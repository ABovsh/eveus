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


def test_class_attrs():
    sw, _ = _make()
    assert sw._attr_icon == "mdi:battery-charging-high"
    from homeassistant.helpers.entity import EntityCategory

    assert sw._attr_entity_category is EntityCategory.CONFIG


def test_initial_is_on_is_false_before_any_restore():
    sw, _ = _make()
    assert sw.is_on is False


def test_available_property_is_always_true():
    sw, _ = _make()
    assert sw.available is True


def test_handle_coordinator_update_treats_only_exact_1_as_suspended():
    # raw == 0 is a VALID "not suspended" reading, not a malformed value: it
    # must be processed (not treated as a malformed/unknown master state).
    sw, controller = _make()
    sw._was_suspended = True
    sw._updater.data = {"suspendLimits": 0}
    sw._handle_coordinator_update()
    assert sw._was_suspended is False


def test_async_added_to_hass_seeds_was_suspended_from_fresh_payload():
    sw, _ = _make()
    sw._was_suspended = False
    sw._updater.data = {"suspendLimits": 1}
    asyncio.run(sw.async_added_to_hass())
    assert sw._was_suspended is True

    sw2, _ = _make()
    sw2._was_suspended = True
    sw2._updater.data = {"suspendLimits": 0}
    asyncio.run(sw2.async_added_to_hass())
    assert sw2._was_suspended is False


def test_async_added_to_hass_restores_on_state_to_is_on_true():
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    sw, controller = _make()
    sw.async_get_last_state = AsyncMock(return_value=SimpleNamespace(state="on"))
    asyncio.run(sw.async_added_to_hass())
    assert sw.is_on is True
    controller.set_enabled.assert_called_with(True)


def test_async_added_to_hass_does_not_crash_with_no_previous_state():
    from unittest.mock import AsyncMock

    sw, _ = _make()
    sw.async_get_last_state = AsyncMock(return_value=None)
    asyncio.run(sw.async_added_to_hass())  # must not raise
    assert sw.is_on is False
