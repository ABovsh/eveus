"""Shared validation for Eveus /main payloads."""
from __future__ import annotations

import json
import math
from typing import Any, Literal

from .const import CHARGING_STATES, MODEL_MAX_CURRENT

MessageStyle = Literal["network", "config_flow"]

# A real /main response is a few KB. Cap the body well above that so a charger,
# proxy, captive portal, or wrong endpoint returning a huge (or unbounded
# chunked) body cannot exhaust memory or stall the event loop.
MAX_RESPONSE_BODY_BYTES = 1_000_000


async def read_json_capped(response: Any, *, limit: int = MAX_RESPONSE_BODY_BYTES) -> Any:
    """Read a response body up to ``limit`` bytes, then JSON-decode it.

    Rejects an over-limit body — including a chunked response with no
    ``Content-Length`` — by streaming and aborting once the cap is passed, before
    the whole body is buffered. Raises ``ValueError`` (``PayloadError``) on an
    oversized or malformed body, matching the existing JSON-decode failure path.
    """
    content_length = getattr(response, "content_length", None)
    if content_length is not None and content_length > limit:
        raise PayloadError("body_too_large", "Eveus response body too large")
    raw = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        raw += chunk
        if len(raw) > limit:
            raise PayloadError("body_too_large", "Eveus response body too large")
    return json.loads(raw)


class PayloadError(ValueError):
    """Raised when a /main payload fails Eveus schema validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_NETWORK_MESSAGES = {
    "not_dict": "Expected dict, got {type_name}",
    "missing_state": "Response missing required Eveus 'state' field",
    "state_bool": "Eveus 'state' field is boolean",
    "state_not_finite": "Eveus 'state' field is not finite",
    "state_not_integer": "Eveus 'state' field is not an integer",
    "state_not_numeric": "Eveus 'state' field is not numeric",
    "state_unknown": "Eveus 'state' value {state_value} outside known domain",
    "missing_current": "Response missing required Eveus 'currentSet' field",
    "current_bool": "Eveus 'currentSet' field is boolean",
    "current_not_numeric": "Eveus 'currentSet' field is not numeric",
    "current_not_finite": "Eveus 'currentSet' field is not finite",
    "current_not_integer": "Eveus 'currentSet' field is not an integer",
    "current_below_min": "Eveus 'currentSet' field below minimum",
    "current_above_model": (
        "Eveus 'currentSet' value {current_set} exceeds model maximum {max_current}"
    ),
}

_CONFIG_FLOW_MESSAGES = {
    "not_dict": "Invalid response format",
    "missing_state": "Device response is missing state",
    "state_bool": "Device 'state' field is boolean",
    "state_not_finite": "Device 'state' field is not an integer",
    "state_not_integer": "Device 'state' field is not an integer",
    "state_not_numeric": "Device 'state' field is not numeric",
    "state_unknown": "Device reports unknown state {state_value}",
    "missing_current": "Device response is missing currentSet",
    "current_bool": "Device reports invalid current setting",
    "current_not_numeric": "Device reports invalid current format",
    "current_not_finite": "Device reports invalid current value",
    "current_not_integer": "Device 'currentSet' field is not an integer",
    "current_below_min": "Device reports invalid current setting",
    "current_above_model": (
        "Device current ({current_set}A) exceeds model maximum ({max_current}A)"
    ),
}


def _message(style: MessageStyle, code: str, **values: Any) -> str:
    messages = _CONFIG_FLOW_MESSAGES if style == "config_flow" else _NETWORK_MESSAGES
    return messages[code].format(**values)


def _raise(style: MessageStyle, code: str, **values: Any) -> None:
    raise PayloadError(code, _message(style, code, **values))


def validate_main_payload(
    payload: Any,
    model: str | None = None,
    *,
    message_style: MessageStyle = "network",
) -> dict[str, Any]:
    """Validate and return a raw Eveus /main payload."""
    if not isinstance(payload, dict):
        _raise(message_style, "not_dict", type_name=type(payload).__name__)

    if "state" not in payload:
        _raise(message_style, "missing_state")

    raw_state = payload["state"]
    if isinstance(raw_state, bool):
        _raise(message_style, "state_bool")
    if isinstance(raw_state, float) and not math.isfinite(raw_state):
        _raise(message_style, "state_not_finite")
    if isinstance(raw_state, float) and not raw_state.is_integer():
        _raise(message_style, "state_not_integer")
    try:
        state_value = int(raw_state)
    except (TypeError, ValueError, OverflowError) as err:
        raise PayloadError(
            "state_not_numeric",
            _message(message_style, "state_not_numeric"),
        ) from err
    if state_value not in CHARGING_STATES:
        _raise(message_style, "state_unknown", state_value=state_value)

    if "currentSet" not in payload:
        _raise(message_style, "missing_current")

    raw_current_set = payload["currentSet"]
    if isinstance(raw_current_set, bool):
        _raise(message_style, "current_bool")
    try:
        current_set = float(raw_current_set)
    except (TypeError, ValueError, OverflowError) as err:
        raise PayloadError(
            "current_not_numeric",
            _message(message_style, "current_not_numeric"),
        ) from err
    if not math.isfinite(current_set):
        _raise(message_style, "current_not_finite")
    # The amp setpoint is always a whole number; a fractional value (e.g. 7.5,
    # or "7.5") is corrupt. Reject it instead of letting the display getter round
    # it to a plausible whole-amp value. Mirrors the integer `state` guard above.
    if not current_set.is_integer():
        _raise(message_style, "current_not_integer")
    # Verified on live hardware (R3.05.2, mid-session): the firmware accepts
    # setpoints BELOW its advertised minCurrent verbatim (delivery floors at
    # ~6 A, the IEC 61851 minimum) while clamping over-max values to the
    # design current. currentSet 1..6 is therefore a legitimate reportable
    # state — only a negative value is corrupt. Failing the poll here made the
    # whole device unavailable whenever an external setpoint went below 7 A.
    if current_set < 0:
        _raise(message_style, "current_below_min")

    # Without a configured model, bound by the largest supported charger so a
    # corrupt currentSet (e.g. 999) cannot pass as a healthy poll.
    max_current = (
        MODEL_MAX_CURRENT.get(model)
        if model is not None
        else max(MODEL_MAX_CURRENT.values())
    )
    if max_current and current_set > max_current:
        _raise(
            message_style,
            "current_above_model",
            current_set=current_set,
            max_current=max_current,
        )

    return payload
