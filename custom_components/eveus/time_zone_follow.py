"""Keep the charger's Time Zone on Home Assistant's UTC offset (opt-in).

The charger holds one whole-hour offset and never follows daylight saving, so
twice a year it runs an hour off until the Time Zone select (or, on REVE, its
app) moves it; older Eveus firmware has no app at all. With the Follow HA Time
Zone switch on, the integration writes Home Assistant's current offset through
the same command the select sends. It only ever sets the offset: the charger's
UTC time is left alone (Sync Time stays a user action).
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_FOLLOW_HA_TIME_ZONE,
    TIME_ZONE_FOLLOW_POLLS,
    TIME_ZONE_FOLLOW_RETRY_POLLS,
)
from .utils import RateLog, get_local_utc_offset_seconds

_LOGGER = logging.getLogger(__name__)
_LOG = RateLog()
_LOG_INTERVAL = 3600.0


class TimeZoneFollower:
    """Decide when the charger's offset has to be rewritten.

    A write needs the same wrong offset on several consecutive polls, so a
    single odd reading never moves the charger. After a write the next one
    waits, so a charger that keeps refusing is not hit on every poll; the wait
    ends as soon as the charger agrees.
    """

    def __init__(self) -> None:
        self._differing_polls = 0
        self._polls_to_wait = 0
        # True while a write is in flight; polls arriving meanwhile do nothing.
        self.sending = False

    def reset(self) -> None:
        """Forget the count and the pause, e.g. while the switch is off."""
        self._differing_polls = 0
        self._polls_to_wait = 0

    def evaluate(self, snapshot, local_offset_seconds: int) -> int | None:
        """Return the offset in hours to write, or None to leave it alone."""
        charger_hours = snapshot.get_int("timeZone")
        if charger_hours is None:
            # A poll without the field says nothing about the offset.
            return None
        whole_hours, remainder = divmod(local_offset_seconds, 3600)
        if remainder or charger_hours == whole_hours:
            # A fractional local offset (India +5:30) is one the whole-hour
            # select cannot hold: nothing written would be right.
            self._differing_polls = 0
            self._polls_to_wait = 0
            return None
        self._differing_polls += 1
        if self._differing_polls < TIME_ZONE_FOLLOW_POLLS:
            return None
        if self._polls_to_wait:
            self._polls_to_wait -= 1
            return None
        self._polls_to_wait = TIME_ZONE_FOLLOW_RETRY_POLLS
        return whole_hours


def update_time_zone_follow(
    hass: HomeAssistant,
    entry: ConfigEntry,
    updater,
    follower: TimeZoneFollower,
) -> None:
    """After a poll, write Home Assistant's offset when the charger's differs."""
    if not entry.data.get(CONF_FOLLOW_HA_TIME_ZONE):
        # Read on every poll: the switch changes the entry without a reload.
        follower.reset()
        return
    if follower.sending or not updater.available or not updater.last_update_success:
        return
    hours = follower.evaluate(updater.snapshot, get_local_utc_offset_seconds())
    if hours is None:
        return
    follower.sending = True
    entry.async_create_background_task(
        hass, _write(updater, follower, hours), "eveus_follow_time_zone"
    )


async def _write(updater, follower: TimeZoneFollower, hours: int) -> None:
    try:
        if await updater.send_command("timeZone", hours):
            _LOGGER.debug("Charger Time Zone set to %+d to follow Home Assistant", hours)
        elif _LOG.should_log(_LOG_INTERVAL, "refused"):
            _LOGGER.debug("Charger did not accept Time Zone %+d; will retry", hours)
    except Exception as err:  # noqa: BLE001 - a failed write must not kill the listener
        if _LOG.should_log(_LOG_INTERVAL, "failed"):
            _LOGGER.debug("Time Zone follow failed: %s", type(err).__name__)
    finally:
        follower.sending = False
