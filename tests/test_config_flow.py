"""Unit tests for Eveus config-flow validation."""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from conftest import (
    TEST_BASE_URL,
    TEST_HOST,
    TEST_HOST_ALT,
    TEST_PASSWORD,
    TEST_USERNAME,
)
from custom_components.eveus import config_flow
from custom_components.eveus import CONFIG_ENTRY_VERSION
from custom_components.eveus.config_flow import (
    CannotConnect,
    InvalidAuth,
    InvalidDevice,
    InvalidInput,
    build_user_data_schema,
    build_reauth_data_schema,
    normalize_user_input,
    validate_credentials,
    validate_device_response,
    validate_input,
)
from custom_components.eveus.const import (
    CONF_BATTERY_CAPACITY,
    CONF_INITIAL_SOC,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    CONF_TARGET_SOC,
    DEFAULT_INITIAL_SOC,
    DEFAULT_PHASES,
    DEFAULT_TARGET_SOC,
    MODEL_16A,
    SOC_MODE_ADVANCED,
    SOC_MODE_BASIC,
)


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("192.168.1.50", True),
        ("eveus.local", True),
        ("charger-1.lan", True),
        ("", False),
        ("256.256.0.1", False),
        ("bad host", False),
        ("-bad.com", False),
    ],
)
def test_host_validation_unchanged(host, ok):
    assert config_flow._host_is_valid(host) is ok


def validate_host(host: str) -> str:
    """Exercise the production host parser, returning only the host part."""
    return config_flow._split_host_and_scheme(host)[0]


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

    def _body_bytes(self) -> bytes:
        if isinstance(self.payload, Exception):
            return b"<<invalid json>>"
        if isinstance(self.payload, str):
            return self.payload.encode()
        return json.dumps(self.payload).encode()

    @property
    def content_length(self) -> int:
        return len(self._body_bytes())

    @property
    def content(self) -> "_StreamReader":
        return _StreamReader(self._body_bytes())


class _StreamReader:
    """Minimal aiohttp StreamReader stand-in for read_json_capped."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


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
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f" {TEST_HOST} ", TEST_HOST),
        ("http://charger.local", "charger.local"),
        ("http://charger.local/", "charger.local"),
        ("https://eveus.local:8443", "eveus.local:8443"),
    ],
)
def test_validate_host_accepts_ips_hostnames_and_urls(raw: str, expected: str) -> None:
    assert validate_host(raw) == expected


def test_validate_host_removes_trailing_hostname_dot() -> None:
    assert validate_host("charger.local.") == "charger.local"


@pytest.mark.parametrize("raw", ["", ".", "bad host name", "-bad.local", "bad-.local"])
def test_validate_host_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(vol.Invalid) as exc_info:
        validate_host(raw)
    assert str(exc_info.value) in {
        "Host cannot be empty",
        "Invalid IP address or hostname",
    }


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("ftp://charger.local", "Unsupported URL scheme"),
        (f"http://{TEST_HOST}/main", "URL must not include a path"),
        (f"http://{TEST_HOST}?x=1", "URL must not include a query or fragment"),
        (f"http://{TEST_HOST}#frag", "URL must not include a query or fragment"),
        ("http://charger.local:bad", "Invalid port"),
        ("http://charger.local:99999", "Invalid port"),
        ("http:///missing-host", "URL must not include a path"),
    ],
)
def test_validate_host_rejects_url_shapes(raw: str, message: str) -> None:
    with pytest.raises(vol.Invalid, match=message):
        validate_host(raw)


def test_validate_host_normalizes_case_and_ipv6_port() -> None:
    assert validate_host("HTTP://Charger.LOCAL") == "charger.local"
    assert validate_host("http://[::1]:8080") == "[::1]:8080"


def test_validate_credentials_strips_username_but_preserves_password() -> None:
    assert validate_credentials(f" {TEST_USERNAME} ", f" {TEST_PASSWORD} ") == (TEST_USERNAME, f" {TEST_PASSWORD} ")


@pytest.mark.parametrize(
    ("username", "password"),
    [("", TEST_PASSWORD), (TEST_USERNAME, ""), ("a" * 33, TEST_PASSWORD), (TEST_USERNAME, "b" * 33)],
)
def test_validate_credentials_rejects_missing_or_long_values(
    username: str, password: str
) -> None:
    with pytest.raises(vol.Invalid) as exc_info:
        validate_credentials(username, password)
    assert str(exc_info.value) in {
        "Username and password cannot be empty",
        "Username and password must be less than 32 characters",
    }


def test_normalize_user_input_returns_persistable_config_data() -> None:
    data = normalize_user_input(
        _input(
            **{
                CONF_HOST: f" {TEST_BASE_URL} ",
                CONF_USERNAME: f" {TEST_USERNAME} ",
                CONF_PASSWORD: f" {TEST_PASSWORD} ",
            }
        )
    )

    assert data == {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: f" {TEST_PASSWORD} ",
        CONF_MODEL: MODEL_16A,
        CONF_SCHEME: "http",
        CONF_PHASES: DEFAULT_PHASES,
        CONF_SOC_MODE: SOC_MODE_ADVANCED,
    }


def test_normalize_user_input_accepts_three_phase() -> None:
    data = normalize_user_input(_input(**{CONF_PHASES: 3}))
    assert data[CONF_PHASES] == 3


def test_normalize_user_input_rejects_invalid_phases() -> None:
    with pytest.raises(vol.Invalid, match="Invalid phase count"):
        normalize_user_input(_input(**{CONF_PHASES: 2}))

    with pytest.raises(vol.Invalid, match="Invalid phase count"):
        normalize_user_input(_input(**{CONF_PHASES: "bad"}))


def test_normalize_user_input_preserves_stored_https_scheme() -> None:
    data = normalize_user_input(
        _input(**{CONF_HOST: "eveus.local:8443", CONF_SCHEME: "https"})
    )

    assert data[CONF_HOST] == "eveus.local:8443"
    assert data[CONF_SCHEME] == "https"


def test_build_user_schema_prefixes_https_default_host() -> None:
    schema = build_user_data_schema({CONF_HOST: "eveus.local:8443", CONF_SCHEME: "https"})
    host_key = next(key for key in schema.schema if key.schema == CONF_HOST)

    assert host_key.default() == "https://eveus.local:8443"


def test_warn_if_plaintext_does_not_warn_for_https(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.config_flow"):
        config_flow._warn_if_plaintext("https")

    assert caplog.records == []


def test_normalize_user_input_rejects_invalid_model() -> None:
    with pytest.raises(vol.Invalid, match="Invalid charger model"):
        normalize_user_input(_input(**{CONF_MODEL: "bad"}))


def test_config_flow_version_matches_migration_target() -> None:
    assert config_flow.ConfigFlow.VERSION == CONFIG_ENTRY_VERSION


def test_validate_device_response_rejects_non_eveus_json() -> None:
    with pytest.raises(InvalidDevice, match="missing state"):
        validate_device_response({"name": "Not Eveus"}, MODEL_16A)


def test_validate_device_response_accepts_model_limit_boundary() -> None:
    assert validate_device_response({"state": 2, "currentSet": "16"}, MODEL_16A) == {
        "current_set": 16.0,
        "firmware": "Unknown",
    }


def test_validate_input_posts_to_normalized_host() -> None:
    response = _Response(payload={"state": 2, "currentSet": "12", "verFWMain": "3.0.3"})
    session = _Session(response)
    hass = _Hass(session)

    result = asyncio.run(
        validate_input(hass, _input(**{CONF_HOST: TEST_BASE_URL}))
    )

    assert result["title"] == f"Eveus Charger ({TEST_HOST})"
    assert "data" in result
    assert result["data"][CONF_HOST] == TEST_HOST
    assert result["data"][CONF_SCHEME] == "http"
    assert "device_info" in result
    assert result["device_info"]["current_set"] == 12
    assert len(session.calls) >= 1
    assert session.calls[0]["url"] == f"{TEST_BASE_URL}/main"


def test_validate_input_preserves_https_scheme_and_port() -> None:
    response = _Response(payload={"state": 2, "currentSet": "12", "verFWMain": "3.0.3"})
    session = _Session(response)
    hass = _Hass(session)

    result = asyncio.run(
        validate_input(hass, _input(**{CONF_HOST: "https://eveus.local:8443"}))
    )

    assert result["title"] == "Eveus Charger (eveus.local:8443)"
    assert "data" in result
    assert result["data"][CONF_HOST] == "eveus.local:8443"
    assert result["data"][CONF_SCHEME] == "https"
    assert len(session.calls) >= 1
    assert session.calls[0]["url"] == "https://eveus.local:8443/main"


def test_validate_input_rejects_unauthorized_response() -> None:
    hass = _Hass(_Session(_Response(status=401)))

    with pytest.raises(InvalidAuth, match="Invalid credentials"):
        asyncio.run(validate_input(hass, _input()))


def test_validate_input_maps_http_401_response_error_to_invalid_auth() -> None:
    error = aiohttp.ClientResponseError(
        request_info=type("RequestInfo", (), {"real_url": f"{TEST_BASE_URL}/main"})(),
        history=(),
        status=401,
    )
    hass = _Hass(_Session(_Response(raise_status=error)))

    with pytest.raises(InvalidAuth):
        asyncio.run(validate_input(hass, _input()))


def test_validate_input_maps_http_errors_to_cannot_connect() -> None:
    error = aiohttp.ClientResponseError(
        request_info=type("RequestInfo", (), {"real_url": f"{TEST_BASE_URL}/main"})(),
        history=(),
        status=500,
    )
    hass = _Hass(_Session(_Response(raise_status=error)))

    with pytest.raises(CannotConnect, match="Connection error: ClientResponseError"):
        asyncio.run(validate_input(hass, _input()))


def test_validate_input_maps_timeout_and_unexpected_errors() -> None:
    hass = _Hass(_Session(_Response(raise_status=asyncio.TimeoutError())))
    with pytest.raises(CannotConnect, match="Connection error: TimeoutError"):
        asyncio.run(validate_input(hass, _input()))

    class BrokenSession:
        def post(self, *args, **kwargs):
            raise RuntimeError("boom")

    with pytest.raises(CannotConnect, match="Unexpected error: RuntimeError"):
        asyncio.run(validate_input(_Hass(BrokenSession()), _input()))


@pytest.mark.parametrize(
    "payload",
    [
        ValueError("not json"),
        ["not", "a", "dict"],
    ],
)
def test_validate_input_rejects_malformed_device_response(payload: object) -> None:
    hass = _Hass(_Session(_Response(payload=payload)))

    with pytest.raises(CannotConnect) as exc_info:
        asyncio.run(validate_input(hass, _input()))
    assert str(exc_info.value) in {
        "Invalid response format",
        "Invalid response format: Invalid response format",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"state": 2, "currentSet": "-1"},
        {"state": 2, "currentSet": "not-a-number"},
        {"state": 2, "currentSet": "32"},
    ],
)
def test_validate_input_rejects_device_values_outside_model_limits(
    payload: dict[str, str]
) -> None:
    hass = _Hass(_Session(_Response(payload=payload)))

    with pytest.raises(InvalidDevice):
        asyncio.run(validate_input(hass, _input()))
    assert hass.session.calls[0]["url"] == f"{TEST_BASE_URL}/main"


def test_validate_input_wraps_local_validation_errors() -> None:
    hass = _Hass(_Session(_Response()))

    with pytest.raises(InvalidInput, match="Invalid IP address or hostname"):
        asyncio.run(validate_input(hass, _input(**{CONF_HOST: "bad host name"})))


def test_user_flow_creates_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
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

    result = asyncio.run(
        flow.async_step_user(_input(**{CONF_SOC_MODE: SOC_MODE_BASIC}))
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_HOST] == TEST_HOST


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
            "title": f"Eveus Charger ({TEST_HOST_ALT})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST}),
            "unique_id": TEST_HOST,
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
    migrated: list[tuple[str, str]] = []
    flow._migrate_device_identifiers = (
        lambda entry, old, new: migrated.append((old, new))
    )
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reconfigure(_input(**{CONF_HOST: TEST_HOST_ALT}))
    )

    assert migrated == [(TEST_HOST, TEST_HOST_ALT)]

    assert result["type"] == "abort"
    assert result["unique_id"] == TEST_HOST_ALT


def test_reconfigure_flow_skips_duplicate_check_when_unique_id_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, "device_number": 2}),
            "unique_id": TEST_HOST,
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = (
        lambda: (_ for _ in ()).throw(AssertionError("duplicate check should be skipped"))
    )
    flow.async_update_reload_and_abort = lambda entry, **kwargs: {
        "type": "abort",
        "reason": "reconfigure_successful",
        **kwargs,
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_reconfigure(_input()))

    assert result["type"] == "abort"
    assert result["data"]["device_number"] == 2


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
            "data": _input(**{CONF_HOST: TEST_HOST}),
            "unique_id": TEST_HOST,
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_reconfigure(_input()))

    assert result["errors"] == {"base": error_key}


def test_reconfigure_flow_maps_unexpected_errors_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise RuntimeError("boom")

    entry = type("Entry", (), {"data": _input(), "unique_id": TEST_HOST})()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_reconfigure(_input()))

    assert result["errors"] == {"base": "unknown"}


def test_reauth_schema_contains_only_credentials() -> None:
    schema = build_reauth_data_schema(
        {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
    )

    assert set(schema.schema) == {CONF_USERNAME, CONF_PASSWORD}


def test_reauth_flow_updates_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(
                **{
                    CONF_HOST: TEST_HOST,
                    CONF_USERNAME: "old",
                    CONF_PASSWORD: "old",
                }
            ),
            "unique_id": TEST_HOST,
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
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert result["type"] == "abort"
    assert result["data"][CONF_USERNAME] == TEST_USERNAME
    assert result["data"][CONF_PASSWORD] == TEST_PASSWORD
    assert result["data"][CONF_HOST] == TEST_HOST


def test_reauth_step_delegates_to_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = config_flow.ConfigFlow()

    async def fake_confirm(user_input=None):
        return {"type": "form", "user_input": user_input}

    monkeypatch.setattr(flow, "async_step_reauth_confirm", fake_confirm)

    assert asyncio.run(flow.async_step_reauth({CONF_HOST: TEST_HOST})) == {
        "type": "form",
        "user_input": None,
    }


def test_reauth_flow_cannot_change_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reauth rebases on live entry data: a validation snapshot reporting a
    different host can neither change the stored host nor brick the flow."""

    async def fake_validate_input(hass, data):
        normalized = normalize_user_input(data)
        normalized[CONF_HOST] = TEST_HOST_ALT
        return {
            "title": f"Eveus Charger ({TEST_HOST_ALT})",
            "data": normalized,
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST}),
            "unique_id": TEST_HOST,
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    captured = {}
    flow.async_update_reload_and_abort = lambda entry, **kw: captured.update(kw) or {
        "type": "abort",
        "reason": "reauth_successful",
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert result["reason"] == "reauth_successful"
    assert captured["data"][CONF_HOST] == TEST_HOST
    assert captured["data"][CONF_USERNAME] == TEST_USERNAME


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
            "data": _input(**{CONF_HOST: TEST_HOST}),
            "unique_id": TEST_HOST,
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert result["errors"] == {"base": error_key}


def test_reauth_flow_maps_unexpected_errors_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise RuntimeError("boom")

    entry = type("Entry", (), {"data": _input(), "unique_id": TEST_HOST})()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert result["errors"] == {"base": "unknown"}


# --- rc.2 hardening tests -----------------------------------------------------


def test_validate_host_rejects_userinfo_credentials() -> None:
    """Reject URLs that embed credentials, since aiohttp BasicAuth would not pick them up."""
    with pytest.raises(vol.Invalid, match="Credentials in URL"):
        validate_host(f"http://user:pass@{TEST_HOST}")


def test_validate_host_keeps_brackets_for_ipv6_without_port() -> None:
    """IPv6 literals normalize with brackets even when no port is given."""
    normalized, scheme = config_flow._split_host_and_scheme("[::1]")
    assert normalized == "[::1]"
    assert scheme == "http"


def test_validate_credentials_rejects_colon_in_username() -> None:
    """':' in username breaks aiohttp BasicAuth — reject early in the form."""
    with pytest.raises(vol.Invalid, match="Username cannot contain"):
        validate_credentials("user:name", TEST_PASSWORD)


def test_safe_phases_default_falls_back_on_corrupt_input() -> None:
    """A corrupt stored phases value must not crash the schema build."""
    assert config_flow._safe_phases_default("oops") == DEFAULT_PHASES
    assert config_flow._safe_phases_default(None) == DEFAULT_PHASES
    assert config_flow._safe_phases_default(2) == DEFAULT_PHASES
    assert config_flow._safe_phases_default("3") == 3


# --- SOC monitoring mode chooser ---------------------------------------------


class _State:
    def __init__(self, state: str) -> None:
        self.state = state


class _States:
    def __init__(self, states: dict[str, _State] | None = None) -> None:
        self._states = states or {}

    def get(self, entity_id: str) -> _State | None:
        return self._states.get(entity_id)


class _HassWithStates:
    def __init__(self, states: dict[str, _State] | None = None) -> None:
        self.states = _States(states)


def _async_validate_input_factory():
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    return fake_validate_input


def test_basic_mode_skips_soc_step(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = config_flow.ConfigFlow()
    flow.hass = _HassWithStates()
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }
    monkeypatch.setattr(
        config_flow, "validate_input", _async_validate_input_factory()
    )

    result = asyncio.run(
        flow.async_step_user(_input(**{CONF_SOC_MODE: SOC_MODE_BASIC}))
    )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_SOC_MODE] == SOC_MODE_BASIC
    assert CONF_BATTERY_CAPACITY not in result["data"]
    assert CONF_SOC_CORRECTION not in result["data"]


def _schema_default(schema: vol.Schema, key: str):
    marker = next(k for k in schema.schema if k.schema == key)
    return marker.default()


def test_advanced_mode_collects_and_prefills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = config_flow.ConfigFlow()
    flow.hass = _HassWithStates(
        {
            "input_number.ev_battery_capacity": _State("64"),
            "input_number.ev_soc_correction": _State("9"),
        }
    )
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_show_form = lambda *, step_id, data_schema=None, errors=None: {
        "type": "form",
        "step_id": step_id,
        "data_schema": data_schema,
    }
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }
    monkeypatch.setattr(
        config_flow, "validate_input", _async_validate_input_factory()
    )

    form = asyncio.run(
        flow.async_step_user(_input(**{CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    )

    assert form["type"] == "form"
    assert form["step_id"] == "soc"
    assert _schema_default(form["data_schema"], CONF_BATTERY_CAPACITY) == 64
    assert _schema_default(form["data_schema"], CONF_SOC_CORRECTION) == 9

    entry = asyncio.run(
        flow.async_step_soc(
            {CONF_BATTERY_CAPACITY: 70, CONF_SOC_CORRECTION: 8}
        )
    )

    assert entry["type"] == "create_entry"
    assert entry["data"][CONF_SOC_MODE] == SOC_MODE_ADVANCED
    assert entry["data"][CONF_BATTERY_CAPACITY] == 70
    assert entry["data"][CONF_SOC_CORRECTION] == 8
    assert entry["data"][CONF_INITIAL_SOC] == DEFAULT_INITIAL_SOC
    assert entry["data"][CONF_TARGET_SOC] == DEFAULT_TARGET_SOC


def test_options_flow_toggles_mode() -> None:
    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, CONF_SOC_MODE: SOC_MODE_ADVANCED}),
            "unique_id": TEST_HOST,
        },
    )()

    updated: dict[str, object] = {}

    class _ConfigEntries:
        def async_update_entry(self, entry, *, data):
            entry.data = data
            updated["data"] = data

    class _Hass:
        def __init__(self) -> None:
            self.config_entries = _ConfigEntries()

    options_flow = config_flow.EveusOptionsFlow(entry)
    options_flow.hass = _Hass()
    options_flow.async_show_form = lambda *, step_id, data_schema=None: {
        "type": "form",
        "step_id": step_id,
        "data_schema": data_schema,
    }
    options_flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }

    form = asyncio.run(options_flow.async_step_init())
    assert form["type"] == "form"
    assert form["step_id"] == "init"

    result = asyncio.run(
        options_flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC})
    )

    assert result["type"] == "create_entry"
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_BASIC


def test_merge_entry_data_preserves_soc_values() -> None:
    """Reconfigure/reauth (incoming lacks SOC keys) must not drop SOC values."""
    existing = {
        "device_number": 2,
        CONF_INITIAL_SOC: 65,
        CONF_TARGET_SOC: 95,
        CONF_BATTERY_CAPACITY: 64,
        CONF_SOC_CORRECTION: 9,
    }
    incoming = {CONF_SCHEME: "http", CONF_SOC_MODE: SOC_MODE_BASIC}

    merged = config_flow._merge_entry_data(existing, incoming)

    assert merged["device_number"] == 2
    assert merged[CONF_INITIAL_SOC] == 65
    assert merged[CONF_TARGET_SOC] == 95
    assert merged[CONF_BATTERY_CAPACITY] == 64
    assert merged[CONF_SOC_CORRECTION] == 9


def _options_flow_for(entry):
    class _ConfigEntries:
        def async_update_entry(self, entry, *, data):
            entry.data = data

    class _Hass:
        def __init__(self) -> None:
            self.config_entries = _ConfigEntries()
            self.states = type("S", (), {"get": staticmethod(lambda eid: None)})()

    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = _Hass()
    flow.async_show_form = lambda *, step_id, data_schema=None: {
        "type": "form",
        "step_id": step_id,
        "data_schema": data_schema,
    }
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }
    return flow


def test_options_flow_first_switch_to_advanced_collects_soc_values() -> None:
    """Basic→Advanced via Configure must run the SOC step, like setup does."""
    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, CONF_SOC_MODE: SOC_MODE_BASIC}),
            "unique_id": TEST_HOST,
        },
    )()
    entry.data.pop(CONF_BATTERY_CAPACITY, None)
    entry.data.pop(CONF_SOC_CORRECTION, None)
    flow = _options_flow_for(entry)

    form = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    assert form["type"] == "form"
    assert form["step_id"] == "soc"
    # Nothing persisted yet — the mode change waits for the SOC values.
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_BASIC

    result = asyncio.run(
        flow.async_step_soc({CONF_BATTERY_CAPACITY: 64, CONF_SOC_CORRECTION: 9})
    )
    assert result["type"] == "create_entry"
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_ADVANCED
    assert entry.data[CONF_BATTERY_CAPACITY] == 64
    assert entry.data[CONF_SOC_CORRECTION] == 9
    assert entry.data[CONF_INITIAL_SOC] == DEFAULT_INITIAL_SOC
    assert entry.data[CONF_TARGET_SOC] == DEFAULT_TARGET_SOC


def test_options_flow_switch_to_advanced_keeps_existing_soc_values() -> None:
    """Re-enabling Advanced with stored SOC values must not re-prompt."""
    entry = type(
        "Entry",
        (),
        {
            "data": _input(
                **{
                    CONF_HOST: TEST_HOST,
                    CONF_SOC_MODE: SOC_MODE_BASIC,
                    CONF_BATTERY_CAPACITY: 70,
                    CONF_SOC_CORRECTION: 5,
                }
            ),
            "unique_id": TEST_HOST,
        },
    )()
    flow = _options_flow_for(entry)

    result = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    assert result["type"] == "create_entry"
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_ADVANCED
    assert entry.data[CONF_BATTERY_CAPACITY] == 70
    assert entry.data[CONF_SOC_CORRECTION] == 5


def test_options_flow_soc_submit_rebases_on_live_entry_data() -> None:
    """A reauth/reconfigure finishing while the SOC form is open must survive."""
    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, CONF_SOC_MODE: SOC_MODE_BASIC}),
            "unique_id": TEST_HOST,
        },
    )()
    entry.data.pop(CONF_BATTERY_CAPACITY, None)
    entry.data.pop(CONF_SOC_CORRECTION, None)
    flow = _options_flow_for(entry)

    form = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))
    assert form["step_id"] == "soc"

    # Reauth completes while the form is open: password and host change.
    entry.data = {**entry.data, CONF_PASSWORD: "new-secret", CONF_HOST: TEST_HOST_ALT}

    asyncio.run(flow.async_step_soc({CONF_BATTERY_CAPACITY: 64, CONF_SOC_CORRECTION: 9}))

    assert entry.data[CONF_PASSWORD] == "new-secret"
    assert entry.data[CONF_HOST] == TEST_HOST_ALT
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_ADVANCED
    assert entry.data[CONF_BATTERY_CAPACITY] == 64


def test_soc_step_schema_prefills_from_stored_entry_values() -> None:
    """Stored SOC values must prefill the form, not generic defaults."""
    class _Hass:
        states = type("S", (), {"get": staticmethod(lambda eid: None)})()

    schema = config_flow.build_soc_step_schema(
        _Hass(), defaults={CONF_SOC_CORRECTION: 4.5}
    )
    defaults = {
        str(key): key.default() for key in schema.schema if hasattr(key, "default")
    }
    assert defaults[CONF_SOC_CORRECTION] == 4.5
    assert defaults[CONF_BATTERY_CAPACITY] == config_flow.DEFAULT_BATTERY_CAPACITY


def test_v20_reauth_aborts_when_host_keeps_changing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SECOND mid-flight host change must not let credentials be committed
    against an address that was never validated; the flow refuses instead."""
    entry = type(
        "Entry", (), {"data": _input(**{CONF_HOST: TEST_HOST}), "unique_id": TEST_HOST}
    )()
    moving = iter(["10.0.0.91", "10.0.0.92", "10.0.0.93", "10.0.0.94", "10.0.0.95"])

    async def fake_validate_input(hass, data):
        # A concurrent reconfigure changes the live host during EVERY validation.
        new_host = next(moving)
        entry.data = {**entry.data, CONF_HOST: new_host}
        entry.unique_id = new_host
        return {
            "title": f"Eveus Charger ({data[CONF_HOST]})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )
    assert result["errors"] == {"base": "cannot_connect"}


def test_v20_reauth_commits_after_host_stabilizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """One mid-flight host change then stability: credentials commit against the
    final, validated host."""
    entry = type(
        "Entry", (), {"data": _input(**{CONF_HOST: TEST_HOST}), "unique_id": TEST_HOST}
    )()
    calls = {"n": 0}

    async def fake_validate_input(hass, data):
        calls["n"] += 1
        if calls["n"] == 1:
            entry.data = {**entry.data, CONF_HOST: TEST_HOST_ALT}
            entry.unique_id = TEST_HOST_ALT
        return {
            "title": f"Eveus Charger ({data[CONF_HOST]})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    captured = {}
    flow.async_update_reload_and_abort = lambda entry, **kw: captured.update(kw) or {
        "type": "abort",
        "reason": "reauth_successful",
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )
    assert result["reason"] == "reauth_successful"
    assert captured["data"][CONF_HOST] == TEST_HOST_ALT


def test_40a_model_is_supported() -> None:
    """EVEUS Pro units report curDesign=40; a 40A model must be selectable."""
    from custom_components.eveus.const import MODEL_40A, MODELS, MODEL_MAX_CURRENT

    assert MODEL_40A == "40A"
    assert MODEL_40A in MODELS
    assert MODEL_MAX_CURRENT[MODEL_40A] == 40


def test_40a_model_validates_setpoint_within_40() -> None:
    """A 40A charger with a 34A setpoint validates as 40A; above 40A is rejected."""
    from custom_components.eveus.const import MODEL_40A

    info = validate_device_response({"state": 2, "currentSet": "34"}, MODEL_40A)
    assert info["current_set"] == 34.0
    with pytest.raises(InvalidDevice):
        validate_device_response({"state": 2, "currentSet": "41"}, MODEL_40A)


def test_40a_model_selectable_in_user_schema() -> None:
    from custom_components.eveus.const import MODEL_40A

    schema = build_user_data_schema({})
    # vol.In(MODELS) accepts 40A for the model field.
    validated = schema(
        {
            CONF_HOST: "1.2.3.4",
            CONF_USERNAME: "eveus",
            CONF_PASSWORD: "secret",
            "model": MODEL_40A,
            "phases": 1,
            "soc_mode": "advanced",
        }
    )
    assert validated["model"] == MODEL_40A
