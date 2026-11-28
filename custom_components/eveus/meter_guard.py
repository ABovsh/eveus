"""Hold a lifetime meter that drops for a single poll.

A charger that is still starting reports its lifetime meters as 0: REVE_PLUS
1.0.3 did on 2026-10-23, one poll with Total Energy, Counter A/B energy and
Counter B cost all at 0, then the real values again. A ``total_increasing``
sensor reads such a dip as a meter reset, so Home Assistant's statistics and
every utility meter built on it count the whole lifetime once more.

So a meter lower than its last accepted reading keeps that reading until the
drop repeats in ``METER_DROP_CONFIRM_POLLS`` polls in a row (a reset in the
charger's app, a replaced board), or until Home Assistant itself reset that
counter. Growth always passes through.

The last accepted readings are kept in storage (``METER_STORE_VERSION``): a
restart or reload of the entry begins without the guard's memory, usually with
the charger switched off, and the first poll after it comes up can be the zero
poll itself.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import time
from typing import Any, Final

from .const import MAX_COST_VALUE, MAX_ENERGY_KWH, OFFLINE_UPDATE_INTERVAL

METER_DROP_CONFIRM_POLLS: Final = 3
# Allow a missed offline refresh and its next retry, with a hard expiry.
_RESET_CONFIRM_SECONDS: Final = 2 * OFFLINE_UPDATE_INTERVAL
METER_STORE_VERSION: Final = 1
# The readings are saved when they changed and this long has passed since the
# last save: a baseline a little behind is as good against a dip to zero.
METER_SAVE_INTERVAL_SECONDS: Final = 900


def meter_store_key(entry_id: str) -> str:
    """The entry-scoped storage key for the last accepted meter readings."""
    return f"eveus_meters_{entry_id}"


_METER_MAXIMUMS: Final = {
    "totalEnergy": MAX_ENERGY_KWH,
    "IEM1": MAX_ENERGY_KWH,
    "IEM2": MAX_ENERGY_KWH,
    "IEM1_money": MAX_COST_VALUE,
    "IEM2_money": MAX_COST_VALUE,
}
# The counter reset commands and the meters each one zeroes.
_RESETS: Final[dict[str, tuple[str, ...]]] = {
    "rstEM1": ("IEM1", "IEM1_money"),
    "rstEM2": ("IEM2", "IEM2_money"),
}


def _number(value: Any, maximum: int) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum:
        return None
    return value


class MeterGuard:
    """Per charger: the last accepted value of each lifetime meter."""

    def __init__(self) -> None:
        self._accepted: dict[str, float] = {}
        self._drops: dict[str, int] = {}
        self._resets: dict[str, float] = {}

    def baseline(self) -> dict[str, float]:
        """The last accepted reading of each meter seen so far."""
        return dict(self._accepted)

    @contextmanager
    def stage(self) -> Iterator[MeterGuard]:
        """Keep the published readings until the poll can commit."""
        staged = MeterGuard()
        staged._accepted = self._accepted.copy()
        staged._drops = self._drops.copy()
        previous_resets = self._resets.copy()
        staged._resets = previous_resets.copy()
        yield staged

        self._accepted = staged._accepted
        self._drops = staged._drops
        # Keep commands acknowledged while the poll waited for storage.
        self._resets = staged._resets | {
            key: deadline for key, deadline in self._resets.items()
            if previous_resets.get(key) is not deadline
        }

    def seed(self, saved: Any) -> None:
        """Take the readings a previous run saved, for the meters not yet seen.

        They come back from disk: anything but a finite non-negative number
        under a known meter key is ignored.
        """
        if not isinstance(saved, Mapping):
            return
        for key, maximum in _METER_MAXIMUMS.items():
            value = _number(saved.get(key), maximum)
            if value is not None:
                self._accepted.setdefault(key, value)

    def forget_pending_drops(self) -> None:
        """A missed poll breaks the streak but keeps accepted readings and resets."""
        self._drops.clear()

    def expect_reset(self, command: str) -> None:
        """Home Assistant just reset a counter: take its next drop at once."""
        if meters := _RESETS.get(command):
            deadline = time.monotonic() + _RESET_CONFIRM_SECONDS
            self._resets.update(dict.fromkeys(meters, deadline))

    def apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The payload with every unconfirmed meter drop held back."""
        out = dict(payload)
        now = time.monotonic() if self._resets else 0.0
        for key, maximum in _METER_MAXIMUMS.items():
            deadline = self._resets.get(key)
            if deadline is not None and now >= deadline:
                self._resets.pop(key)
                deadline = None
            value = _number(out.get(key), maximum)
            if value is None:
                self._drops.pop(key, None)
                continue
            last = self._accepted.get(key)
            dropped = last is not None and value < last
            if dropped and deadline is None:
                drops = self._drops.get(key, 0) + 1
                if drops < METER_DROP_CONFIRM_POLLS:
                    self._drops[key] = drops
                    out[key] = last
                    continue
            self._accepted[key] = value
            self._drops.pop(key, None)
            if dropped or value == 0:
                self._resets.pop(key, None)
        return out
