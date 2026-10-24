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

from collections.abc import Mapping
import math
from typing import Any, Final

METER_DROP_CONFIRM_POLLS: Final = 3
METER_STORE_VERSION: Final = 1
# The readings are saved when they changed and this long has passed since the
# last save: a baseline a little behind is as good against a dip to zero.
METER_SAVE_INTERVAL_SECONDS: Final = 900


def meter_store_key(entry_id: str) -> str:
    """The entry-scoped storage key for the last accepted meter readings."""
    return f"eveus_meters_{entry_id}"


_METER_KEYS: Final = ("totalEnergy", "IEM1", "IEM2", "IEM1_money", "IEM2_money")
# The counter reset commands and the meters each one zeroes.
_RESETS: Final[dict[str, tuple[str, ...]]] = {
    "rstEM1": ("IEM1", "IEM1_money"),
    "rstEM2": ("IEM2", "IEM2_money"),
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


class MeterGuard:
    """Per charger: the last accepted value of each lifetime meter."""

    def __init__(self) -> None:
        self._accepted: dict[str, float] = {}
        self._drops: dict[str, int] = {}
        self._resets: set[str] = set()

    def baseline(self) -> dict[str, float]:
        """The last accepted reading of each meter seen so far."""
        return dict(self._accepted)

    def seed(self, saved: Any) -> None:
        """Take the readings a previous run saved, for the meters not yet seen.

        They come back from disk: anything but a finite non-negative number
        under a known meter key is ignored.
        """
        if not isinstance(saved, Mapping):
            return
        for key in _METER_KEYS:
            value = _number(saved.get(key))
            if value is not None and value >= 0:
                self._accepted.setdefault(key, value)

    def expect_reset(self, command: str) -> None:
        """Home Assistant just reset a counter: take its next drop at once."""
        self._resets.update(_RESETS.get(command, ()))

    def apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The payload with every unconfirmed meter drop held back."""
        out = dict(payload)
        for key in _METER_KEYS:
            value = _number(out.get(key))
            if value is None:
                continue
            last = self._accepted.get(key)
            if last is not None and value < last and key not in self._resets:
                drops = self._drops.get(key, 0) + 1
                if drops < METER_DROP_CONFIRM_POLLS:
                    self._drops[key] = drops
                    out[key] = last
                    continue
            self._accepted[key] = value
            self._drops.pop(key, None)
            self._resets.discard(key)
        return out
