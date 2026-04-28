"""Unit tests for Eveus config-flow validation."""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.eveus import config_flow
from custom_components.eveus.config_flow import (
    CannotConnect,
    InvalidAuth,
    InvalidDevice,
    InvalidInput,
    normalize_user_input,
    validate_credentials,
    validate_device_response,
    validate_host,
    validate_input,
)
from custom_components.eveus.const import CONF_MODEL, MODEL_16A


class _Response:
    def __init__(self, *, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"currentSet": "16"}

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _Hass:
    def __init__(self, session: _Session) -> None:
        self.session = session


@pytest.fixture(autouse=True)
def _patch_clientsession(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the local fake session even when Home Assistant is installed."""
    monkeypatch.setattr(
        config_flow.aiohttp_client,
        "async_get_clientsession",
        lambda hass: hass.session,
    )


def _input(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        CONF_HOST: "192.168.1.50",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 192.168.1.50 ", "192.168.1.50"),
        ("http://charger.local/main", "charger.local"),
        ("https://eveus.local", "eveus.local"),
    ],
)
def test_validate_host_accepts_ips_hostnames_and_urls(raw: str, expected: str) -> None:
    assert validate_host(raw) == expected


@pytest.mark.parametrize("raw", ["", "bad host name", "-bad.local", "bad-.local"])
def test_validate_host_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(Exception):
        validate_host(raw)


def test_validate_credentials_strips_values() -> None:
    assert validate_credentials(" admin ", " secret ") == ("admin", "secret")


@pytest.mark.parametrize(
    ("username", "password"),
    [("", "secret"), ("admin", ""), ("a" * 33, "secret"), ("admin", "b" * 33)],
)
def test_validate_credentials_rejects_missing_or_long_values(
    username: str, password: str
) -> None:
    with pytest.raises(Exception):
        validate_credentials(username, password)


def test_normalize_user_input_returns_persistable_config_data() -> None:
    data = normalize_user_input(
        _input(
            **{
                CONF_HOST: " http://192.168.1.50/main ",
                CONF_USERNAME: " admin ",
                CONF_PASSWORD: " secret ",
            }
        )
    )

    assert data == {
        CONF_HOST: "192.168.1.50",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
        CONF_MODEL: MODEL_16A,
    }


def test_validate_device_response_rejects_non_eveus_json() -> None:
    with pytest.raises(InvalidDevice):
        validate_device_response({"name": "Not Eveus"}, MODEL_16A)


def test_validate_input_posts_to_normalized_host() -> None:
    session = _Session(_Response(payload={"currentSet": "12", "verFWMain": "3.0.3"}))
    hass = _Hass(session)

    result = asyncio.run(
        validate_input(hass, _input(**{CONF_HOST: "http://192.168.1.50/main"}))
    )

    assert result["title"] == "Eveus Charger (192.168.1.50)"
    assert result["data"][CONF_HOST] == "192.168.1.50"
    assert result["device_info"]["current_set"] == 12
    assert session.calls[0]["url"] == "http://192.168.1.50/main"


def test_validate_input_rejects_unauthorized_response() -> None:
    hass = _Hass(_Session(_Response(status=401)))

    with pytest.raises(InvalidAuth):
        asyncio.run(validate_input(hass, _input()))


@pytest.mark.parametrize(
    "payload",
    [
        ValueError("not json"),
        ["not", "a", "dict"],
    ],
)
def test_validate_input_rejects_malformed_device_response(payload: object) -> None:
    hass = _Hass(_Session(_Response(payload=payload)))

    with pytest.raises(CannotConnect):
        asyncio.run(validate_input(hass, _input()))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"currentSet": "6"},
        {"currentSet": "not-a-number"},
        {"currentSet": "32"},
    ],
)
def test_validate_input_rejects_device_values_outside_model_limits(
    payload: dict[str, str]
) -> None:
    hass = _Hass(_Session(_Response(payload=payload)))

    with pytest.raises(InvalidDevice):
        asyncio.run(validate_input(hass, _input()))


def test_validate_input_wraps_local_validation_errors() -> None:
    hass = _Hass(_Session(_Response()))

    with pytest.raises(InvalidInput):
        asyncio.run(validate_input(hass, _input(**{CONF_HOST: "bad host name"})))
