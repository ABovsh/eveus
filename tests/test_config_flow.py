"""Unit tests for Eveus config-flow validation."""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.eveus import config_flow
from custom_components.eveus import CONFIG_ENTRY_VERSION
from custom_components.eveus.config_flow import (
    CannotConnect,
    InvalidAuth,
    InvalidDevice,
    InvalidInput,
    build_reauth_data_schema,
    normalize_user_input,
    validate_credentials,
    validate_device_response,
    validate_host,
    validate_input,
)
from custom_components.eveus.const import CONF_MODEL, MODEL_16A


class _Response:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: object | None = None,
        raise_status: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"currentSet": "16"}
        self.raise_status = raise_status
        self.json_kwargs: dict[str, object] = {}

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.raise_status:
            raise self.raise_status
        return None

    async def json(self, **kwargs: object) -> object:
        self.json_kwargs = kwargs
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


def test_validate_host_removes_trailing_hostname_dot() -> None:
    assert validate_host("charger.local.") == "charger.local"


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


def test_normalize_user_input_rejects_invalid_model() -> None:
    with pytest.raises(vol.Invalid):
        normalize_user_input(_input(**{CONF_MODEL: "bad"}))


def test_config_flow_version_matches_migration_target() -> None:
    assert config_flow.ConfigFlow.VERSION == CONFIG_ENTRY_VERSION


def test_validate_device_response_rejects_non_eveus_json() -> None:
    with pytest.raises(InvalidDevice):
        validate_device_response({"name": "Not Eveus"}, MODEL_16A)


def test_validate_device_response_accepts_model_limit_boundary() -> None:
    assert validate_device_response({"currentSet": "16"}, MODEL_16A) == {
        "current_set": 16.0,
        "firmware": "Unknown",
    }


def test_validate_input_posts_to_normalized_host() -> None:
    response = _Response(payload={"currentSet": "12", "verFWMain": "3.0.3"})
    session = _Session(response)
    hass = _Hass(session)

    result = asyncio.run(
        validate_input(hass, _input(**{CONF_HOST: "http://192.168.1.50/main"}))
    )

    assert result["title"] == "Eveus Charger (192.168.1.50)"
    assert result["data"][CONF_HOST] == "192.168.1.50"
    assert result["device_info"]["current_set"] == 12
    assert session.calls[0]["url"] == "http://192.168.1.50/main"
    assert response.json_kwargs == {"content_type": None}


def test_validate_input_rejects_unauthorized_response() -> None:
    hass = _Hass(_Session(_Response(status=401)))

    with pytest.raises(InvalidAuth):
        asyncio.run(validate_input(hass, _input()))


def test_validate_input_maps_http_401_response_error_to_invalid_auth() -> None:
    error = aiohttp.ClientResponseError(
        request_info=type("RequestInfo", (), {"real_url": "http://192.168.1.50/main"})(),
        history=(),
        status=401,
    )
    hass = _Hass(_Session(_Response(raise_status=error)))

    with pytest.raises(InvalidAuth):
        asyncio.run(validate_input(hass, _input()))


def test_validate_input_maps_http_errors_to_cannot_connect() -> None:
    error = aiohttp.ClientResponseError(
        request_info=type("RequestInfo", (), {"real_url": "http://192.168.1.50/main"})(),
        history=(),
        status=500,
    )
    hass = _Hass(_Session(_Response(raise_status=error)))

    with pytest.raises(CannotConnect):
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


def test_user_flow_creates_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (192.168.1.50)",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_user(_input()))

    assert result["type"] == "create_entry"
    assert result["data"][CONF_HOST] == "192.168.1.50"


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (CannotConnect(), "cannot_connect"),
        (InvalidAuth(), "invalid_auth"),
        (InvalidInput("bad"), "invalid_input"),
        (InvalidDevice("bad"), "invalid_device"),
    ],
)
def test_user_flow_maps_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    error_key: str,
) -> None:
    async def fake_validate_input(hass, data):
        raise error

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_user(_input()))

    assert result["errors"] == {"base": error_key}


def test_user_flow_maps_unexpected_errors_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise RuntimeError("boom")

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_user(_input()))

    assert result["errors"] == {"base": "unknown"}


def test_reconfigure_flow_updates_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (192.168.1.55)",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: "192.168.1.50"}),
            "unique_id": "192.168.1.50",
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_update_reload_and_abort = lambda entry, **kwargs: {
        "type": "abort",
        "reason": "reconfigure_successful",
        **kwargs,
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reconfigure(_input(**{CONF_HOST: "192.168.1.55"}))
    )

    assert result["type"] == "abort"
    assert result["unique_id"] == "192.168.1.55"


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (CannotConnect(), "cannot_connect"),
        (InvalidAuth(), "invalid_auth"),
        (InvalidInput("bad"), "invalid_input"),
        (InvalidDevice("bad"), "invalid_device"),
    ],
)
def test_reconfigure_flow_maps_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    error_key: str,
) -> None:
    async def fake_validate_input(hass, data):
        raise error

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: "192.168.1.50"}),
            "unique_id": "192.168.1.50",
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_reconfigure(_input()))

    assert result["errors"] == {"base": error_key}


def test_reauth_schema_contains_only_credentials() -> None:
    schema = build_reauth_data_schema(
        {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
    )

    assert set(schema.schema) == {CONF_USERNAME, CONF_PASSWORD}


def test_reauth_flow_updates_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (192.168.1.50)",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(
                **{
                    CONF_HOST: "192.168.1.50",
                    CONF_USERNAME: "old",
                    CONF_PASSWORD: "old",
                }
            ),
            "unique_id": "192.168.1.50",
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_mismatch = lambda **kwargs: None
    flow.async_update_reload_and_abort = lambda entry, **kwargs: {
        "type": "abort",
        "reason": "reauth_successful",
        **kwargs,
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
        )
    )

    assert result["type"] == "abort"
    assert result["data"][CONF_USERNAME] == "admin"
    assert result["data"][CONF_PASSWORD] == "secret"
    assert result["data"][CONF_HOST] == "192.168.1.50"


def test_reauth_flow_aborts_when_host_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        normalized = normalize_user_input(data)
        normalized[CONF_HOST] = "192.168.1.55"
        return {
            "title": "Eveus Charger (192.168.1.55)",
            "data": normalized,
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: "192.168.1.50"}),
            "unique_id": "192.168.1.50",
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow.async_abort = lambda *, reason: {"type": "abort", "reason": reason}
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
        )
    )

    assert result == {"type": "abort", "reason": "wrong_device"}


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (CannotConnect(), "cannot_connect"),
        (InvalidAuth(), "invalid_auth"),
        (InvalidInput("bad"), "invalid_input"),
        (InvalidDevice("bad"), "invalid_device"),
    ],
)
def test_reauth_flow_maps_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    error_key: str,
) -> None:
    async def fake_validate_input(hass, data):
        raise error

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: "192.168.1.50"}),
            "unique_id": "192.168.1.50",
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
        )
    )

    assert result["errors"] == {"base": error_key}
