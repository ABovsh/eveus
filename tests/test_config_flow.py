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
    wire_flow_reload_success,
)
from custom_components.eveus import config_flow
from custom_components.eveus import CONFIG_ENTRY_VERSION
from custom_components.eveus.config_flow import (
    CannotConnect,
    InvalidAuth,
    InvalidDevice,
    InvalidInput,
    InvalidResponse,
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
    UPDATE_TIMEOUT,
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
        # Real aiohttp responses always expose headers; the diagnostic logging in
        # validate_input reads Content-Type, so the fake must provide them too.
        self.headers: dict[str, str] = {"Content-Type": "application/json"}

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
        if isinstance(self.payload, bytes):
            return self.payload
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


@pytest.mark.parametrize("raw", [f"http://char\ngerlocal", f"http://charger\x7flocal"])
def test_validate_host_rejects_control_characters(raw: str) -> None:
    with pytest.raises(vol.Invalid, match="Host contains invalid control characters"):
        validate_host(raw)


@pytest.mark.parametrize(
    ("raw", "expected_port"),
    [(f"http://{TEST_HOST}:1", ":1"), (f"http://{TEST_HOST}:65535", ":65535")],
)
def test_validate_host_accepts_boundary_ports(raw: str, expected_port: str) -> None:
    assert validate_host(raw).endswith(expected_port)


@pytest.mark.parametrize("raw", [f"http://{TEST_HOST}:0", f"http://{TEST_HOST}:65536"])
def test_validate_host_rejects_out_of_range_ports(raw: str) -> None:
    with pytest.raises(vol.Invalid, match="Invalid port"):
        validate_host(raw)


def test_validate_credentials_strips_username_but_preserves_password() -> None:
    assert validate_credentials(f" {TEST_USERNAME} ", f" {TEST_PASSWORD} ") == (TEST_USERNAME, f" {TEST_PASSWORD} ")


def test_validate_credentials_accepts_exact_32_char_boundary() -> None:
    # The limit is "more than 32", not "32 or more" -- exactly 32 must pass.
    assert validate_credentials("a" * 32, "b" * 32) == ("a" * 32, "b" * 32)


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


def test_warn_if_plaintext_warns_for_http(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HTTP setups must get the cleartext-credentials warning."""
    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.config_flow"):
        config_flow._warn_if_plaintext("http")

    assert len(caplog.records) == 1
    assert "cleartext" in caplog.records[0].getMessage()


def test_normalize_user_input_rejects_invalid_model() -> None:
    with pytest.raises(vol.Invalid, match="Invalid charger model"):
        normalize_user_input(_input(**{CONF_MODEL: "bad"}))


def test_config_flow_version_matches_migration_target() -> None:
    assert config_flow.ConfigFlow.VERSION == CONFIG_ENTRY_VERSION


def test_validate_device_response_rejects_non_eveus_json() -> None:
    # A reachable device whose JSON has no Eveus signature keys is not an Eveus
    # charger; setup reports invalid_response rather than a bare cannot_connect.
    with pytest.raises(InvalidResponse, match="not a recognizable Eveus payload"):
        validate_device_response({"name": "Not Eveus"}, MODEL_16A)


def test_validate_device_response_accepts_model_limit_boundary() -> None:
    assert validate_device_response({"state": 2, "currentSet": "16"}, MODEL_16A) == {
        "current_set": 16.0,
        "firmware": "Unknown",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("state", 2),
        ("currentSet", "16"),
        ("verFWWifi", "1.0"),
        ("curDesign", 16),
        ("evseType", 1),
    ],
)
def test_validate_device_response_accepts_each_signature_key_in_isolation(
    key: str, value: object
) -> None:
    # Any ONE signature key is sufficient (older firmware reports fewer
    # fields); a payload carrying only this key alone must still be accepted.
    result = validate_device_response({key: value}, MODEL_16A)
    assert isinstance(result, dict)


def test_validate_device_response_current_set_is_none_when_unparseable() -> None:
    result = validate_device_response({"state": 2, "currentSet": "not-a-number"}, MODEL_16A)
    assert result["current_set"] is None


def test_validate_input_accepts_invalid_utf8_serial_bytes() -> None:
    # R3.01.8 units with an unset serial return raw non-UTF-8 bytes in
    # serialNum; setup must decode leniently instead of failing the whole flow
    # with "Response is not valid JSON" (UnicodeDecodeError is a ValueError).
    body = b'{"serialNum": "' + b"\xff" * 17 + b'", "state": 2, "currentSet": 20}'
    hass = _Hass(_Session(_Response(payload=body)))

    result = asyncio.run(validate_input(hass, _input()))

    assert result["title"] == f"Eveus Charger ({TEST_HOST})"


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


def test_validate_input_uses_same_timeout_budget_as_the_runtime_poll() -> None:
    # Setup previously hardcoded a 10s budget while the coordinator's regular
    # poll uses UPDATE_TIMEOUT (20s) -- a charger slow enough to answer the
    # live poll every cycle could still never be added. Setup must give the
    # charger at least as much time as normal operation does.
    response = _Response(payload={"state": 2, "currentSet": "12", "verFWMain": "3.0.3"})
    session = _Session(response)
    hass = _Hass(session)

    asyncio.run(validate_input(hass, _input()))

    assert len(session.calls) >= 1
    timeout = session.calls[0]["timeout"]
    assert timeout.total == UPDATE_TIMEOUT


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

    with pytest.raises(CannotConnect, match="HTTP 500"):
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
    # Non-JSON body, or JSON that isn't an object: reachable but not a valid
    # Eveus response -> invalid_response (distinct from cannot_connect).
    hass = _Hass(_Session(_Response(payload=payload)))

    with pytest.raises(InvalidResponse):
        asyncio.run(validate_input(hass, _input()))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "some other device"},
    ],
)
def test_validate_input_rejects_unrecognizable_payload(
    payload: dict[str, str]
) -> None:
    # Reachable, valid JSON object, but not an Eveus charger.
    hass = _Hass(_Session(_Response(payload=payload)))

    with pytest.raises(InvalidResponse):
        asyncio.run(validate_input(hass, _input()))
    assert hass.session.calls[0]["url"] == f"{TEST_BASE_URL}/main"


@pytest.mark.parametrize(
    "payload",
    [
        {"state": 2, "currentSet": "-1"},
        {"state": 2, "currentSet": "not-a-number"},
        {"state": 2, "currentSet": "32"},
    ],
)
def test_validate_input_accepts_recognizable_payload_leniently(
    payload: dict[str, str]
) -> None:
    # Setup is intentionally lenient: a recognizable Eveus payload is accepted
    # even when a control field looks odd (older firmware reports fewer/older
    # fields). The live poll keeps the strict per-field validation.
    hass = _Hass(_Session(_Response(payload=payload)))

    result = asyncio.run(validate_input(hass, _input()))
    assert result["data"][CONF_HOST] == TEST_HOST
    assert hass.session.calls[0]["url"] == f"{TEST_BASE_URL}/main"


def test_validate_input_accepts_old_firmware_slim_payload() -> None:
    # Older firmware reports fewer fields; a response carrying any Eveus
    # signature key (here only verFWMain, no state/currentSet) still adds.
    hass = _Hass(_Session(_Response(payload={"verFWMain": "GRM070A-R3.01.8"})))

    result = asyncio.run(validate_input(hass, _input()))
    assert result["data"][CONF_HOST] == TEST_HOST
    assert result["device_info"]["firmware"] == "GRM070A-R3.01.8"


def test_validate_input_html_login_page_is_invalid_response() -> None:
    # Old firmware that answers /main with an HTML login page is reachable but
    # not a valid Eveus payload -> invalid_response, not a bare cannot_connect.
    hass = _Hass(_Session(_Response(payload="<html><body>Login</body></html>")))

    with pytest.raises(InvalidResponse):
        asyncio.run(validate_input(hass, _input()))


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


def test_user_flow_sets_unique_id_to_validated_host(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    seen_unique_ids: list[str | None] = []

    async def fake_set_unique_id(unique_id):
        seen_unique_ids.append(unique_id)

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow.async_set_unique_id = fake_set_unique_id
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    asyncio.run(flow.async_step_user(_input(**{CONF_SOC_MODE: SOC_MODE_BASIC})))

    assert seen_unique_ids == [TEST_HOST]


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (CannotConnect(), "cannot_connect"),
        (InvalidAuth(), "invalid_auth"),
        (InvalidInput("bad"), "invalid_input"),
        (InvalidDevice("bad"), "invalid_device"),
        (InvalidResponse("bad"), "invalid_response"),
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
    assert result["step_id"] == "user"


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
    captured: dict = {}
    wire_flow_reload_success(flow, entry, captured)
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
    assert result["reason"] == "reconfigure_successful"
    assert captured["unique_id"] == TEST_HOST_ALT
    flow.hass.config_entries.async_reload.assert_called_once_with(entry.entry_id)


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
    captured: dict = {}
    wire_flow_reload_success(flow, entry, captured)
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_reconfigure(_input()))

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert captured["data"]["device_number"] == 2


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (CannotConnect(), "cannot_connect"),
        (InvalidAuth(), "invalid_auth"),
        (InvalidInput("bad"), "invalid_input"),
        (InvalidDevice("bad"), "invalid_device"),
        (InvalidResponse("bad"), "invalid_response"),
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
    assert result["step_id"] == "reconfigure"
    # The reconfigure form must never re-offer the SOC-mode chooser -- that
    # is changed only through Configure (the options flow).
    assert CONF_SOC_MODE not in result["data_schema"].schema


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


def test_reauth_schema_prefills_username_from_stored_defaults() -> None:
    # A truthy `defaults` must be kept, not replaced by an empty dict -- the
    # reauth form would otherwise lose the stored username prefill.
    schema = build_reauth_data_schema({CONF_USERNAME: TEST_USERNAME})
    username_key = next(key for key in schema.schema if key.schema == CONF_USERNAME)

    assert username_key.default() == TEST_USERNAME


def test_reauth_max_revalidations_constant() -> None:
    assert config_flow._REAUTH_MAX_REVALIDATIONS == 3


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
    captured: dict = {}
    wire_flow_reload_success(flow, entry, captured)
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert captured["data"][CONF_USERNAME] == TEST_USERNAME
    assert captured["data"][CONF_PASSWORD] == TEST_PASSWORD
    assert captured["data"][CONF_HOST] == TEST_HOST
    flow.hass.config_entries.async_reload.assert_called_once_with(entry.entry_id)


def test_reauth_validates_live_http_entry_data(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    async def fake_validate_input(hass, data):
        seen.append(dict(data))
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
                    CONF_SCHEME: "http",
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
    wire_flow_reload_success(flow, entry)
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert result["reason"] == "reauth_successful"
    assert len(seen) == 1
    for key, value in entry.data.items():
        if key in (CONF_USERNAME, CONF_PASSWORD):
            continue
        assert seen[0][key] == value
    assert seen[0][CONF_USERNAME] == TEST_USERNAME
    assert seen[0][CONF_PASSWORD] == TEST_PASSWORD
    assert seen[0][CONF_SCHEME] == "http"


def test_reauth_step_delegates_to_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    flow = config_flow.ConfigFlow()

    async def fake_confirm(user_input=None):
        return {"type": "form", "user_input": user_input}

    monkeypatch.setattr(flow, "async_step_reauth_confirm", fake_confirm)

    assert asyncio.run(flow.async_step_reauth({CONF_HOST: TEST_HOST})) == {
        "type": "form",
        "user_input": None,
    }


def test_reauth_flow_aborts_wrong_device_on_stale_unique_id(
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
            "data": _input(**{CONF_HOST: TEST_HOST}),
            "unique_id": "stale-unique-id",
        },
    )()
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

    assert result["type"] == "abort"
    assert result["reason"] == "wrong_device"


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
    wire_flow_reload_success(flow, entry, captured)
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
        (InvalidResponse("bad"), "invalid_response"),
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
    assert result["step_id"] == "reauth_confirm"


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


@pytest.mark.parametrize(
    "raw",
    [
        f"http://user:pass@{TEST_HOST}",
        # username-only and password-only userinfo must be rejected too — the
        # check is an OR, and either half alone still smuggles credentials.
        f"http://user@{TEST_HOST}",
        f"http://:pass@{TEST_HOST}",
    ],
)
def test_validate_host_rejects_userinfo_credentials(raw: str) -> None:
    """Reject URLs that embed credentials, since aiohttp BasicAuth would not pick them up."""
    with pytest.raises(vol.Invalid, match="Credentials in URL"):
        validate_host(raw)


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
            "entry_id": "opt-entry",
        },
    )()

    updated: dict[str, object] = {}

    class _ConfigEntries:
        def async_update_entry(self, entry, *, data):
            entry.data = data
            updated["data"] = data

        async def async_reload(self, entry_id):
            updated["reloaded"] = entry_id

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


def test_options_flow_apply_reloads_the_entry() -> None:
    from unittest.mock import AsyncMock

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, CONF_SOC_MODE: SOC_MODE_BASIC}),
            "unique_id": TEST_HOST,
            "entry_id": "opt-entry",
        },
    )()

    class _ConfigEntries:
        def async_update_entry(self, entry, *, data):
            entry.data = data

        async_reload = AsyncMock(return_value=True)

    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = type("Hass", (), {"config_entries": _ConfigEntries()})()
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }

    result = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC}))

    assert result["type"] == "create_entry"
    flow.hass.config_entries.async_reload.assert_called_once_with("opt-entry")


def test_options_flow_apply_aborts_when_reload_fails() -> None:
    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, CONF_SOC_MODE: SOC_MODE_BASIC}),
            "unique_id": TEST_HOST,
            "entry_id": "opt-entry",
        },
    )()

    class _ConfigEntries:
        def async_update_entry(self, entry, *, data):
            entry.data = data

        async def async_reload(self, entry_id):
            return False

    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = type("Hass", (), {"config_entries": _ConfigEntries()})()
    flow.async_abort = lambda reason: {"type": "abort", "reason": reason}

    result = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC}))

    assert result["type"] == "abort"
    assert result["reason"] == "reload_failed"


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

        async def async_reload(self, entry_id):
            return None

    class _Hass:
        def __init__(self) -> None:
            self.config_entries = _ConfigEntries()
            self.states = type("S", (), {"get": staticmethod(lambda eid: None)})()

    if not hasattr(entry, "entry_id"):
        entry.entry_id = "opt-entry"
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


@pytest.mark.parametrize(
    "overrides",
    [
        {CONF_BATTERY_CAPACITY: 70},
        {CONF_SOC_CORRECTION: 5},
    ],
)
def test_options_flow_advanced_switch_requires_both_soc_values_present(
    overrides: dict,
) -> None:
    # Only one of the two set-once SOC values stored (a partial/corrupt
    # state) must still re-prompt for the SOC step -- "either missing" is
    # the correct condition, not "both missing".
    entry = type(
        "Entry",
        (),
        {
            "data": _input(
                **{
                    CONF_HOST: TEST_HOST,
                    CONF_SOC_MODE: SOC_MODE_BASIC,
                }
            ),
            "unique_id": TEST_HOST,
        },
    )()
    entry.data.pop(CONF_BATTERY_CAPACITY, None)
    entry.data.pop(CONF_SOC_CORRECTION, None)
    entry.data.update(overrides)
    flow = _options_flow_for(entry)

    form = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED}))

    assert form["type"] == "form"
    assert form["step_id"] == "soc"
    assert entry.data[CONF_SOC_MODE] == SOC_MODE_BASIC


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
    wire_flow_reload_success(flow, entry, captured)
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


def test_40a_model_setpoint_accepted_at_setup() -> None:
    """A 40A charger's reported setpoint is accepted at setup (lenient)."""
    from custom_components.eveus.const import MODEL_40A

    info = validate_device_response({"state": 2, "currentSet": "34"}, MODEL_40A)
    assert info["current_set"] == 34.0


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


import pytest
import voluptuous as vol


@pytest.mark.parametrize("raw", [
    "https://host/foo",
    "https://host/main",
    "host/anything",
])
def test_split_host_rejects_path(raw):
    from custom_components.eveus.config_flow import _split_host_and_scheme
    with pytest.raises(vol.Invalid, match="must not include a path"):
        _split_host_and_scheme(raw)


def test_split_host_accepts_root_path():
    from custom_components.eveus.config_flow import _split_host_and_scheme
    host, _ = _split_host_and_scheme("https://example.local/")
    assert host == "example.local"


def test_split_host_lowercases_dns_hostname():
    from custom_components.eveus.config_flow import _split_host_and_scheme
    host, _ = _split_host_and_scheme("CHARGER.Local")
    assert host == "charger.local"


def test_split_host_preserves_ipv4_case_irrelevant():
    from custom_components.eveus.config_flow import _split_host_and_scheme
    host, _ = _split_host_and_scheme("192.168.1.10")
    assert host == "192.168.1.10"


@pytest.mark.parametrize("raw", [
    "https://host/main?x=1",
    "host#frag",
    "host?a=b",
])
def test_split_host_rejects_query_or_fragment(raw):
    from custom_components.eveus.config_flow import _split_host_and_scheme
    with pytest.raises(vol.Invalid, match="query or fragment"):
        _split_host_and_scheme(raw)


def test_split_host_accepts_bare_and_trailing_slash():
    from custom_components.eveus.config_flow import _split_host_and_scheme
    assert _split_host_and_scheme("host")[0] == "host"
    assert _split_host_and_scheme("https://host/")[0] == "host"


def test_split_host_rejects_port_zero():
    from custom_components.eveus.config_flow import _split_host_and_scheme
    with pytest.raises(vol.Invalid, match="Invalid port"):
        _split_host_and_scheme("host:0")


def test_split_host_rejects_path_and_credentials() -> None:
    from custom_components.eveus.config_flow import _split_host_and_scheme
    with pytest.raises(vol.Invalid):
        _split_host_and_scheme("http://user:pass@1.2.3.4")
    with pytest.raises(vol.Invalid):
        _split_host_and_scheme("http://1.2.3.4/main")


@pytest.mark.parametrize(
    "username,password",
    [
        (12345, "pw"),
        ("user", 12345),
        (None, "pw"),
        (b"user", "pw"),
    ],
)
def test_validate_credentials_rejects_non_string_values(username, password) -> None:
    from custom_components.eveus.config_flow import validate_credentials
    with pytest.raises(vol.Invalid):
        validate_credentials(username, password)


def test_safe_phases_default_handles_infinite_value() -> None:
    from custom_components.eveus import config_flow
    from custom_components.eveus.const import DEFAULT_PHASES
    assert config_flow._safe_phases_default(float("inf")) == DEFAULT_PHASES
    assert config_flow._safe_phases_default(float("nan")) == DEFAULT_PHASES
    # Building the reconfigure schema with corrupt stored phases must not raise.
    config_flow.build_user_data_schema({"phases": float("inf")})


import asyncio as _asyncio
from types import SimpleNamespace
from homeassistant.data_entry_flow import AbortFlow


@pytest.mark.parametrize("raw", ["a\nb.com", "http://a\rb.com", "1.2.\t3.4", "host\x7f.local"])
def test_split_host_rejects_control_characters(raw: str) -> None:
    from custom_components.eveus import config_flow as cf
    with pytest.raises(vol.Invalid):
        cf._split_host_and_scheme(raw)


def test_user_flow_propagates_already_configured_abort(
    monkeypatch,
) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import CONF_SOC_MODE, SOC_MODE_BASIC, MODEL_16A
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": cf.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    def _abort() -> None:
        raise AbortFlow("already_configured")

    flow = cf.ConfigFlow()
    flow.hass = object()
    flow.async_set_unique_id = lambda unique_id: _asyncio.sleep(0)
    flow._abort_if_unique_id_configured = _abort
    monkeypatch.setattr(cf, "validate_input", fake_validate_input)

    _input = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        "model": MODEL_16A,
        CONF_SOC_MODE: SOC_MODE_BASIC,
    }
    with pytest.raises(AbortFlow):
        _asyncio.run(flow.async_step_user(_input))


def test_reconfigure_flow_propagates_already_configured_abort(
    monkeypatch,
) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import MODEL_16A
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": cf.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    def _abort() -> None:
        raise AbortFlow("already_configured")

    flow = cf.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: SimpleNamespace(
        unique_id="different-old-host",
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            "model": MODEL_16A,
        },
    )
    flow.async_set_unique_id = lambda unique_id: _asyncio.sleep(0)
    flow._abort_if_unique_id_configured = _abort
    monkeypatch.setattr(cf, "validate_input", fake_validate_input)

    _input = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        "model": MODEL_16A,
    }
    with pytest.raises(AbortFlow):
        _asyncio.run(flow.async_step_reconfigure(_input))


def test_reauth_normalizes_corrupt_stored_soc_mode(
    monkeypatch,
) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import CONF_SOC_MODE, SOC_MODE_OPTIONS, MODEL_16A
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

    captured: dict = {}

    async def fake_validate_input(hass, data):
        captured.update(data)
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": cf.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = SimpleNamespace(
        unique_id=TEST_HOST,
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            "model": MODEL_16A,
            CONF_SOC_MODE: "totally-bogus",
        },
    )
    flow = cf.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: _asyncio.sleep(0)
    wire_flow_reload_success(flow, entry)
    monkeypatch.setattr(cf, "validate_input", fake_validate_input)

    _asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "new-pass"}
        )
    )

    # The bogus stored soc_mode must have been replaced with a valid option
    assert captured[CONF_SOC_MODE] in SOC_MODE_OPTIONS


@pytest.mark.parametrize("raw", ["[::1", "http://[::1", "[fe80::", "https://[2001:db8::1"])
def test_unbalanced_ipv6_bracket_raises_invalid(raw: str) -> None:
    from custom_components.eveus import config_flow as cf
    with pytest.raises(vol.Invalid):
        cf._split_host_and_scheme(raw)


def test_balanced_ipv6_still_accepted() -> None:
    from custom_components.eveus import config_flow as cf
    host, scheme = cf._split_host_and_scheme("[::1]")
    assert host == "[::1]"
    assert scheme == "http"


def test_normalize_user_input_rejects_bool_phases() -> None:
    from custom_components.eveus.config_flow import normalize_user_input
    from custom_components.eveus.const import SOC_MODE_ADVANCED
    base = {
        "host": TEST_HOST,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "model": "16A",
        "phases": True,
        "soc_mode": SOC_MODE_ADVANCED,
    }
    with pytest.raises(vol.Invalid):
        normalize_user_input(base)


@pytest.mark.parametrize(
    "raw,expected_host",
    [
        ("fe80::1", "[fe80::1]"),
        ("2001:db8::2", "[2001:db8::2]"),
        ("[2001:db8::2]:8443", "[2001:db8::2]:8443"),
    ],
)
def test_split_host_accepts_bare_ipv6(raw, expected_host) -> None:
    from custom_components.eveus.config_flow import _split_host_and_scheme
    host, scheme = _split_host_and_scheme(raw)
    assert host == expected_host
    assert scheme in ("http", "https")


def test_soc_schema_is_serializable_and_validates_range() -> None:
    import homeassistant.helpers.config_validation as cv
    import voluptuous_serialize
    from custom_components.eveus import config_flow as cf

    class _Hass:
        states = type("S", (), {"get": staticmethod(lambda eid: None)})()

    schema = cf.build_soc_step_schema(_Hass(), defaults={})
    voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)

    cap_lo, cap_hi = cf.SOC_INPUT_LIMITS["battery_capacity"]
    schema({"battery_capacity": cap_lo, "soc_correction": 0})  # valid submission
    with pytest.raises(vol.Invalid):
        schema({"battery_capacity": cap_hi + 1000, "soc_correction": 0})


def test_credentials_must_be_latin1_encodable() -> None:
    from custom_components.eveus.config_flow import validate_credentials
    with pytest.raises(vol.Invalid):
        validate_credentials(TEST_USERNAME, "пароль")
    assert validate_credentials(TEST_USERNAME, TEST_PASSWORD) == (
        TEST_USERNAME,
        TEST_PASSWORD,
    )


def test_normalize_user_input_rejects_fractional_phases() -> None:
    from custom_components.eveus.config_flow import normalize_user_input
    base = {
        "host": TEST_HOST,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "model": "16A",
    }
    with pytest.raises(vol.Invalid):
        normalize_user_input({**base, "phases": 2.9})
    # Integral floats (JSON numbers) keep working.
    assert normalize_user_input({**base, "phases": 3.0})["phases"] == 3


def test_phases_whole_number_and_bool_rejected_by_normalize() -> None:
    from custom_components.eveus.config_flow import normalize_user_input
    base = {
        "host": "1.2.3.4",
        "username": "eveus",
        "password": "secret",
        "model": "16A",
    }
    assert normalize_user_input({**base, "phases": 3})["phases"] == 3
    for bad in (3.9, True):
        with pytest.raises(vol.Invalid):
            normalize_user_input({**base, "phases": bad})


def test_user_schema_accepts_phase_count_submitted_as_string():
    # The mobile-app frontend submits select values as strings; the schema
    # must coerce "1"/"3" instead of failing "value must be one of [1, 3]".
    from custom_components.eveus.config_flow import build_user_data_schema
    from custom_components.eveus.const import CONF_PHASES

    schema = build_user_data_schema()
    result = schema(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "model": "16A",
            CONF_PHASES: "3",
            "soc_mode": "basic",
        }
    )
    assert result[CONF_PHASES] == 3


def test_v19_default_http_port_is_dropped():
    from custom_components.eveus import config_flow as cf
    h1, _ = cf._split_host_and_scheme("host")
    h2, _ = cf._split_host_and_scheme("host:80")
    h3, _ = cf._split_host_and_scheme("http://host")
    assert h1 == h2 == h3 == "host"


def test_v19_default_https_port_is_dropped():
    from custom_components.eveus import config_flow as cf
    h, scheme = cf._split_host_and_scheme("https://host:443")
    assert h == "host" and scheme == "https"


def test_v19_nondefault_port_is_kept():
    from custom_components.eveus import config_flow as cf
    h, _ = cf._split_host_and_scheme("host:8080")
    assert h == "host:8080"


def test_v19_ipv6_literals_canonicalize_to_same_host():
    from custom_components.eveus import config_flow as cf
    h1, _ = cf._split_host_and_scheme("[::1]")
    h2, _ = cf._split_host_and_scheme("[0:0:0:0:0:0:0:1]")
    assert h1 == h2 == "[::1]"


def test_reauth_rebases_on_live_entry_data(monkeypatch) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import MODEL_16A, MODEL_32A

    entry = SimpleNamespace(
        data={
            "host": TEST_HOST,
            "username": "old",
            "password": "old",
            "model": MODEL_16A,
        },
        unique_id=TEST_HOST,
        title="Eveus",
    )

    async def fake_validate_input(hass, data):
        # a concurrent options flow commits a model change mid-validation
        entry.data = {**entry.data, "model": MODEL_32A}
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": cf.normalize_user_input(
                {**data, "model": data.get("model", MODEL_16A)}
            ),
            "device_info": {"current_set": 16},
        }

    flow = cf.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: _asyncio.sleep(0)
    captured = {}
    wire_flow_reload_success(flow, entry, captured)
    monkeypatch.setattr(cf, "validate_input", fake_validate_input)

    _asyncio.run(
        flow.async_step_reauth_confirm(
            {"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
    )
    assert captured["data"]["model"] == MODEL_32A  # concurrent change preserved
    assert captured["data"]["username"] == TEST_USERNAME
    assert captured["data"]["password"] == TEST_PASSWORD


def test_resolve_phases_rejects_boolean() -> None:
    from custom_components.eveus import _resolve_phases
    # bool is an int subclass: int(True)=1 would otherwise pass as valid and
    # drive the destructive phase prune.
    assert _resolve_phases(True) == (1, True)
    assert _resolve_phases(False) == (1, True)


def test_reauth_revalidates_when_host_changes_mid_flight(monkeypatch) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import MODEL_16A

    calls: list[str] = []

    entry = SimpleNamespace(
        data={
            "host": TEST_HOST,
            "username": "old",
            "password": "old",
            "model": MODEL_16A,
        },
        unique_id=TEST_HOST,
        title="Eveus",
    )

    async def fake_validate_input(hass, data):
        calls.append(data["host"])
        if len(calls) == 1:
            # a concurrent reconfigure commits a host change mid-validation
            entry.data = {**entry.data, "host": "newhost.local"}
            entry.unique_id = "newhost.local"
        return {
            "title": f"Eveus Charger ({data['host']})",
            "data": cf.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    flow = cf.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: _asyncio.sleep(0)
    captured = {}
    wire_flow_reload_success(flow, entry, captured)
    monkeypatch.setattr(cf, "validate_input", fake_validate_input)

    _asyncio.run(
        flow.async_step_reauth_confirm(
            {"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
    )
    # credentials were re-validated against the live (new) host before commit
    assert calls == [TEST_HOST, "newhost.local"]
    assert captured["data"]["host"] == "newhost.local"
    assert captured["data"]["username"] == TEST_USERNAME


def test_reconfigure_migrates_device_identifiers(monkeypatch) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import DOMAIN
    from types import SimpleNamespace

    device = SimpleNamespace(
        id="dev1",
        identifiers={(DOMAIN, TEST_HOST), (DOMAIN, f"{TEST_HOST}_2"), ("other", "x")},
    )
    updated: list[tuple[str, set]] = []
    registry = SimpleNamespace(
        async_update_device=lambda dev_id, new_identifiers: updated.append(
            (dev_id, new_identifiers)
        )
    )
    monkeypatch.setattr(cf.dr, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        cf.dr,
        "async_entries_for_config_entry",
        lambda _reg, _eid: [device],
    )

    flow = cf.ConfigFlow()
    flow.hass = object()
    entry = SimpleNamespace(entry_id="e1")
    flow._migrate_device_identifiers(entry, TEST_HOST, "10.0.0.9")

    (dev_id, identifiers), = updated
    assert dev_id == "dev1"
    assert identifiers == {(DOMAIN, "10.0.0.9"), (DOMAIN, "10.0.0.9_2"), ("other", "x")}


def test_migrate_device_identifiers_skips_device_without_matching_identifiers(
    monkeypatch,
) -> None:
    from custom_components.eveus import config_flow as cf
    from types import SimpleNamespace

    device = SimpleNamespace(id="dev1", identifiers={("other", "x")})
    updated: list[tuple[str, set]] = []
    registry = SimpleNamespace(
        async_update_device=lambda dev_id, new_identifiers: updated.append(
            (dev_id, new_identifiers)
        )
    )
    monkeypatch.setattr(cf.dr, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        cf.dr, "async_entries_for_config_entry", lambda _reg, _eid: [device]
    )

    flow = cf.ConfigFlow()
    flow.hass = object()
    entry = SimpleNamespace(entry_id="e1")
    flow._migrate_device_identifiers(entry, TEST_HOST, "10.0.0.9")

    assert updated == []


def test_migrate_device_identifiers_exact_match_alone(monkeypatch) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import DOMAIN
    from types import SimpleNamespace

    device = SimpleNamespace(id="dev1", identifiers={(DOMAIN, TEST_HOST)})
    updated: list[tuple[str, set]] = []
    registry = SimpleNamespace(
        async_update_device=lambda dev_id, new_identifiers: updated.append(
            (dev_id, new_identifiers)
        )
    )
    monkeypatch.setattr(cf.dr, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        cf.dr, "async_entries_for_config_entry", lambda _reg, _eid: [device]
    )

    flow = cf.ConfigFlow()
    flow.hass = object()
    entry = SimpleNamespace(entry_id="e1")
    flow._migrate_device_identifiers(entry, TEST_HOST, "10.0.0.9")

    (dev_id, identifiers), = updated
    assert dev_id == "dev1"
    assert identifiers == {(DOMAIN, "10.0.0.9")}


def test_migrate_device_identifiers_suffix_match_alone(monkeypatch) -> None:
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import DOMAIN
    from types import SimpleNamespace

    device = SimpleNamespace(id="dev1", identifiers={(DOMAIN, f"{TEST_HOST}_2")})
    updated: list[tuple[str, set]] = []
    registry = SimpleNamespace(
        async_update_device=lambda dev_id, new_identifiers: updated.append(
            (dev_id, new_identifiers)
        )
    )
    monkeypatch.setattr(cf.dr, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        cf.dr, "async_entries_for_config_entry", lambda _reg, _eid: [device]
    )

    flow = cf.ConfigFlow()
    flow.hass = object()
    entry = SimpleNamespace(entry_id="e1")
    flow._migrate_device_identifiers(entry, TEST_HOST, "10.0.0.9")

    (dev_id, identifiers), = updated
    assert dev_id == "dev1"
    assert identifiers == {(DOMAIN, "10.0.0.9_2")}


def test_migrate_device_identifiers_ignores_wrong_domain_with_matching_suffix(
    monkeypatch,
) -> None:
    # A "domain == DOMAIN or ident.startswith(...)" mutant would incorrectly
    # rename an identifier that merely happens to start with the old host,
    # even though it belongs to an unrelated domain/integration.
    from custom_components.eveus import config_flow as cf
    from types import SimpleNamespace

    device = SimpleNamespace(
        id="dev1", identifiers={("other_domain", f"{TEST_HOST}_2")}
    )
    updated: list[tuple[str, set]] = []
    registry = SimpleNamespace(
        async_update_device=lambda dev_id, new_identifiers: updated.append(
            (dev_id, new_identifiers)
        )
    )
    monkeypatch.setattr(cf.dr, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        cf.dr, "async_entries_for_config_entry", lambda _reg, _eid: [device]
    )

    flow = cf.ConfigFlow()
    flow.hass = object()
    entry = SimpleNamespace(entry_id="e1")
    flow._migrate_device_identifiers(entry, TEST_HOST, "10.0.0.9")

    assert updated == []


def test_coordinator_does_not_store_plaintext_credentials():
    import aiohttp
    from unittest.mock import MagicMock, patch
    from custom_components.eveus.common_network import EveusUpdater

    hass = MagicMock()
    hass.config = MagicMock()
    with patch("custom_components.eveus.common_network.DataUpdateCoordinator.__init__", return_value=None):
        u = EveusUpdater(host="h", username="user", password="pw", hass=hass)
    assert not hasattr(u, "username")
    assert not hasattr(u, "password")
    assert u._basic_auth == aiohttp.BasicAuth("user", "pw")


@pytest.mark.parametrize(
    "state,expected",
    [
        ("nan", 50.0),
        ("inf", 50.0),
        ("9999", 160.0),
        ("75", 75.0),
    ],
)
def test_prefill_from_helper_sanitizes(state, expected) -> None:
    from conftest import HelperHass
    from custom_components.eveus.config_flow import _prefill_from_helper

    hass = HelperHass({"input_number.ev_battery_capacity": state})
    out = _prefill_from_helper(
        hass, "input_number.ev_battery_capacity", "battery_capacity", 50.0
    )
    assert out == expected


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("advanced", "advanced"),
        ("basic", "basic"),
        ("garbage", "advanced"),
        (None, "advanced"),
        (1, "advanced"),
    ],
)
def test_get_soc_mode_defaults_invalid_to_advanced(stored, expected) -> None:
    from types import SimpleNamespace
    from custom_components.eveus.const import (
        CONF_SOC_MODE,
        get_soc_mode,
    )

    data = {} if stored is None else {CONF_SOC_MODE: stored}
    assert get_soc_mode(SimpleNamespace(data=data)) == expected


def test_reauth_rebases_on_live_entry_data(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from custom_components.eveus import config_flow as cf
    from custom_components.eveus.const import MODEL_16A, MODEL_32A

    entry = SimpleNamespace(
        data={
            "host": TEST_HOST,
            "username": "old",
            "password": "old",
            "model": MODEL_16A,
        },
        unique_id=TEST_HOST,
        title="Eveus",
    )

    async def fake_validate_input(hass, data):
        # a concurrent options flow commits a model change mid-validation
        entry.data = {**entry.data, "model": MODEL_32A}
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": cf.normalize_user_input(
                {**data, "model": data.get("model", MODEL_16A)}
            ),
            "device_info": {"current_set": 16},
        }

    flow = cf.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    captured = {}
    wire_flow_reload_success(flow, entry, captured)
    monkeypatch.setattr(cf, "validate_input", fake_validate_input)

    asyncio.run(
        flow.async_step_reauth_confirm(
            {"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
    )
    assert captured["data"]["model"] == MODEL_32A  # concurrent change preserved
    assert captured["data"]["username"] == TEST_USERNAME
    assert captured["data"]["password"] == TEST_PASSWORD


def test_infinite_device_number_on_other_entry_does_not_crash() -> None:
    from custom_components.eveus import utils

    class _Entry:
        def __init__(self, device_number) -> None:
            self.data = {}
            if device_number is not None:
                self.data["device_number"] = device_number

    class _ConfigEntries:
        def __init__(self, entries) -> None:
            self._entries = entries

        def async_entries(self, domain: str):
            assert domain == "eveus"
            return self._entries

    class _Hass:
        def __init__(self, entries) -> None:
            self.config_entries = _ConfigEntries(entries)

    hass = _Hass([_Entry(1), _Entry(float("inf")), _Entry("bad")])

    assert utils.get_next_device_number(hass) == 2
    assert utils.is_device_number_taken(hass, 1) is True
    assert utils.is_device_number_taken(hass, 5) is False


def test_user_flow_cannot_connect_shows_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The dialog must surface WHY the connection failed (HTTP status / error
    # type), not just the generic message -- old-firmware reports hinge on it.
    async def fake_validate_input(hass, data):
        raise CannotConnect("HTTP 404")

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_user(_input()))

    assert result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"] == {"error_detail": "HTTP 404"}


def test_user_flow_cannot_connect_detail_never_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bare CannotConnect (no message) must not render an empty placeholder.
    async def fake_validate_input(hass, data):
        raise CannotConnect

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_user(_input()))

    assert result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"]["error_detail"]


def test_reconfigure_flow_cannot_connect_shows_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise CannotConnect("Connection error: TimeoutError")

    entry = type(
        "Entry",
        (),
        {"data": _input(**{CONF_HOST: TEST_HOST}), "unique_id": TEST_HOST},
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(flow.async_step_reconfigure(_input()))

    assert result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"] == {
        "error_detail": "Connection error: TimeoutError"
    }


def test_reauth_flow_cannot_connect_shows_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise CannotConnect("HTTP 500")

    entry = type(
        "Entry",
        (),
        {"data": _input(**{CONF_HOST: TEST_HOST}), "unique_id": TEST_HOST},
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

    assert result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"] == {"error_detail": "HTTP 500"}


def test_validate_input_logs_non_json_body_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Setup is user-initiated: the diagnostic body log must be visible without
    # enabling debug logging, or every old-firmware report needs a log-config
    # round-trip before diagnosis can start.
    hass = _Hass(_Session(_Response(payload="<html><body>Login</body></html>")))

    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.config_flow"):
        with pytest.raises(InvalidResponse):
            asyncio.run(validate_input(hass, _input()))

    assert any(
        record.levelno == logging.WARNING and "did not return JSON" in record.message
        for record in caplog.records
    )


def test_validate_input_logs_unrecognizable_payload_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass(_Session(_Response(payload={"name": "not eveus"})))

    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.config_flow"):
        with pytest.raises(InvalidResponse):
            asyncio.run(validate_input(hass, _input()))

    assert any(
        record.levelno == logging.WARNING
        and "not an Eveus /main payload" in record.message
        for record in caplog.records
    )
