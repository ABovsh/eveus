"""The one way this integration talks to an Eveus charger.

Four call sites reach the firmware: the coordinator's ``/main`` poll, the
one-shot ``/init`` firmware probe, every command's ``/pageEvent`` form, and the
config flow's setup validation. REVE firmware adds a fifth, its local
``/graphql`` endpoint, under the same transport rules. Each used to spell out the transport rules
itself — opt out of redirects, reject a 3xx rather than follow it, raise on an
HTTP error, cap the body, decode it leniently, then JSON-decode — and that
divergence is why setup once ran on a shorter timeout than steady-state
polling, the one moment a struggling charger most needs patience.

Those rules live here now, and the timeout objects with them. What a 401
*means* deliberately does not: polling hands it to Home Assistant's reauth
flow, setup turns it into a form error, and a command lets it surface as an
HTTP error for its own handler to map. So the caller passes
``on_unauthorized`` and the transport asks instead of deciding.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode

import aiohttp

from ._payload import raise_for_redirect, read_json_capped
from .const import COMMAND_TIMEOUT, UPDATE_TIMEOUT

# Built once and shared, so the poll and the setup validation cannot drift onto
# two different budgets again.
UPDATE_TIMEOUT_OBJ: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=UPDATE_TIMEOUT)
COMMAND_TIMEOUT_OBJ: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=COMMAND_TIMEOUT)

_FORM_HEADERS = {"Content-type": "application/x-www-form-urlencoded"}
_GRAPHQL_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


class GraphQLError(ValueError):
    """The charger answered ``/graphql``, but not with a usable result.

    A ``ValueError`` so the poll and command paths treat it exactly like a
    malformed ``/main`` body.
    """


@asynccontextmanager
async def charger_post(
    session: Any,
    url: str,
    *,
    auth: aiohttp.BasicAuth,
    timeout: aiohttp.ClientTimeout | None,
    data: Any = None,
    headers: dict[str, str] | None = None,
    on_unauthorized: Callable[[], None] | None = None,
):
    """POST to the charger and yield a response that already passed the checks.

    ``allow_redirects=False`` keeps credentials and command forms from reaching
    another origin; aiohttp then returns the 3xx itself, which
    ``raise_for_status`` accepts, so ``raise_for_redirect`` turns it into the
    error every caller's HTTP handling already covers.

    ``on_unauthorized`` runs on a 401 *before* the status check and is expected
    to raise the caller's own exception. Without it a 401 continues as an
    ordinary ``ClientResponseError``.

    Yields the open response, so a caller that needs the body's provenance (the
    config flow logs the media type to tell an HTML login page from malformed
    JSON) can read it here rather than re-implementing the request.
    """
    async with session.post(
        url,
        auth=auth,
        timeout=timeout,
        data=data,
        headers=headers,
        allow_redirects=False,
    ) as response:
        if response.status == 401 and on_unauthorized is not None:
            on_unauthorized()
        raise_for_redirect(response)
        response.raise_for_status()
        yield response


async def fetch_json(
    session: Any,
    url: str,
    *,
    auth: aiohttp.BasicAuth,
    timeout: aiohttp.ClientTimeout | None,
    on_unauthorized: Callable[[], None] | None = None,
) -> Any:
    """POST and return the decoded JSON body, capped and leniently decoded.

    Serves ``/main`` and ``/init`` alike: they differ only in the path, which
    the caller builds, so one function is the whole of both.
    """
    async with charger_post(
        session, url, auth=auth, timeout=timeout, on_unauthorized=on_unauthorized
    ) as response:
        return await read_json_capped(response)


async def post_page_event(
    session: Any,
    url: str,
    *,
    auth: aiohttp.BasicAuth,
    timeout: aiohttp.ClientTimeout | None,
    fields: dict[str, Any],
) -> None:
    """Write a ``/pageEvent`` form; returns on success, raises otherwise.

    The body is not read: the firmware answers a write with its web UI, and
    nothing in it is worth the bytes.
    """
    async with charger_post(
        session,
        url,
        auth=auth,
        timeout=timeout,
        data=urlencode(fields),
        headers=dict(_FORM_HEADERS),
    ):
        return


async def post_graphql(
    session: Any,
    url: str,
    *,
    auth: aiohttp.BasicAuth,
    timeout: aiohttp.ClientTimeout | None,
    operation: str,
    document: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one GraphQL operation and return its ``data`` object.

    Only fixed documents are ever sent (the caller passes a module constant and
    plain ``variables``): the REVE server stops answering every query after a
    few malformed ones until the charger is power-cycled, so nothing here may
    build query text at runtime.
    """
    body = json.dumps(
        {"operationName": operation, "variables": variables or {}, "query": document}
    )
    async with charger_post(
        session,
        url,
        auth=auth,
        timeout=timeout,
        data=body,
        headers=dict(_GRAPHQL_HEADERS),
    ) as response:
        result = await read_json_capped(response)
    if not isinstance(result, dict):
        raise GraphQLError("GraphQL reply is not an object")  # pragma: no mutate - message text; only the exception type is ever logged
    if result.get("errors"):
        raise GraphQLError("GraphQL reply reports errors")  # pragma: no mutate - message text; only the exception type is ever logged
    data = result.get("data")
    if not isinstance(data, dict):
        raise GraphQLError("GraphQL reply has no data")  # pragma: no mutate - message text; only the exception type is ever logged
    return data
