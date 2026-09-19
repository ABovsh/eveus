"""Contract for the single charger HTTP path (P2.3).

Polling, the /init firmware probe, commands and config-flow setup all talk to
the same firmware over the same rules. Before this module each of them spelled
those rules out itself, which is how setup once ended up with a shorter timeout
than steady-state polling.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from conftest import StreamReaderStub
from custom_components.eveus import client
from custom_components.eveus._payload import PayloadError


class _Response:
    def __init__(self, *, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body
        self.request_info = None

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status
            )

    @property
    def content_length(self) -> int:
        return len(self._body)

    @property
    def content(self) -> StreamReaderStub:
        return StreamReaderStub(self._body)


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


_AUTH = aiohttp.BasicAuth("u", "p")


# --- transport rules --------------------------------------------------------


def test_every_request_refuses_to_follow_a_redirect() -> None:
    """Credentials and command forms must never reach another origin, so the
    request opts out of redirects and the 3xx itself is raised as an error."""
    session = _Session(_Response(status=302))

    with pytest.raises(aiohttp.ClientResponseError) as err:
        asyncio.run(
            client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=None)
        )

    assert err.value.status == 302
    assert session.calls[0]["allow_redirects"] is False


def test_request_carries_the_auth_and_timeout_it_was_given() -> None:
    session = _Session(_Response(body=b'{"state": 2}'))
    timeout = aiohttp.ClientTimeout(total=7)

    asyncio.run(
        client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=timeout)
    )

    call = session.calls[0]
    assert call["auth"] is _AUTH
    assert call["timeout"] is timeout


def test_http_error_is_raised() -> None:
    session = _Session(_Response(status=500))
    with pytest.raises(aiohttp.ClientResponseError):
        asyncio.run(
            client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=None)
        )


def test_oversized_body_is_rejected_before_it_is_buffered() -> None:
    session = _Session(_Response(body=b"x" * 2_000_000))
    with pytest.raises(PayloadError) as err:
        asyncio.run(
            client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=None)
        )
    assert err.value.code == "body_too_large"


def test_body_decode_is_lenient_about_non_utf8_bytes() -> None:
    """Firmware puts raw uninitialized bytes in an unset serialNum; a strict
    decode would fail an otherwise perfectly good poll."""
    session = _Session(_Response(body=b'{"state": 2, "serialNum": "\xff\xfe"}'))

    result = asyncio.run(
        client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=None)
    )

    assert result["state"] == 2


def test_malformed_json_is_a_value_error() -> None:
    session = _Session(_Response(body=b"<html>login</html>"))
    with pytest.raises(ValueError):
        asyncio.run(
            client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=None)
        )


# --- who decides what a 401 means -------------------------------------------


def test_unauthorized_is_handed_back_to_the_caller() -> None:
    """Polling turns a 401 into HA's reauth flow and setup into a form error,
    so the transport asks rather than deciding."""
    session = _Session(_Response(status=401))

    class _Rejected(Exception):
        pass

    def _reject() -> None:
        raise _Rejected

    with pytest.raises(_Rejected):
        asyncio.run(
            client.fetch_json(
                session,
                "http://charger/main",
                auth=_AUTH,
                timeout=None,
                on_unauthorized=_reject,
            )
        )


def test_unauthorized_without_a_handler_is_an_ordinary_http_error() -> None:
    """A command has no reauth path of its own; its caller maps the 401."""
    session = _Session(_Response(status=401))

    with pytest.raises(aiohttp.ClientResponseError) as err:
        asyncio.run(
            client.fetch_json(session, "http://charger/main", auth=_AUTH, timeout=None)
        )

    assert err.value.status == 401


# --- the command form -------------------------------------------------------


def test_page_event_posts_a_urlencoded_form() -> None:
    session = _Session(_Response())

    asyncio.run(
        client.post_page_event(
            session,
            "http://charger/pageEvent",
            auth=_AUTH,
            timeout=None,
            fields={"pageevent": "currentSet", "currentSet": 10},
        )
    )

    call = session.calls[0]
    assert call["data"] == "pageevent=currentSet&currentSet=10"
    assert call["headers"] == {"Content-type": "application/x-www-form-urlencoded"}
    assert call["allow_redirects"] is False


# --- the divergence this module exists to make impossible -------------------


def test_setup_and_polling_share_one_main_timeout() -> None:
    """Setup is the one moment a struggling charger most needs patience, so it
    must never be stricter than steady-state polling. One object, one number."""
    from custom_components.eveus import common_network, config_flow
    from custom_components.eveus.const import UPDATE_TIMEOUT

    assert client.UPDATE_TIMEOUT_OBJ.total == UPDATE_TIMEOUT
    assert common_network._UPDATE_TIMEOUT_OBJ is client.UPDATE_TIMEOUT_OBJ
    assert config_flow._UPDATE_TIMEOUT_OBJ is client.UPDATE_TIMEOUT_OBJ


def test_command_timeout_is_the_command_budget() -> None:
    from custom_components.eveus import common_command
    from custom_components.eveus.const import COMMAND_TIMEOUT

    assert client.COMMAND_TIMEOUT_OBJ.total == COMMAND_TIMEOUT
    assert common_command._COMMAND_TIMEOUT_OBJ is client.COMMAND_TIMEOUT_OBJ
