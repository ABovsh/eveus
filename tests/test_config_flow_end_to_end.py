"""Config-flow behaviour observed the way Home Assistant observes it.

Every test drives a flow step (setup, reconfigure, reauth, options) with the
real ``validate_input`` talking to a fake charger session, then asserts on the
flow result, the form schema HA renders and validates, or the config entry /
device registry the flow wrote to. Nothing here patches ``validate_input`` or
calls a private helper, so a change in any of them shows up as a different
result, not as a mocked-away call.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import AbortFlow

from conftest import (
    StreamReaderStub,
    TEST_HOST,
    TEST_HOST_ALT,
    TEST_PASSWORD,
    TEST_USERNAME,
    serialize_schema,
)
from custom_components.eveus import config_flow
from custom_components.eveus.const import (
    CONF_BATTERY_CAPACITY,
    CONF_EXTERNAL_SOC_ENTITY,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    DOMAIN,
)

_CHARGER_MAIN = {"state": 2, "currentSet": "16", "verFWMain": "1.0"}


class _ChargerResponse:
    def __init__(self, status: int = 200, payload: object = None) -> None:
        self.status = status
        self._body = json.dumps(_CHARGER_MAIN if payload is None else payload).encode()
        self.headers = {"Content-Type": "application/json"}

    async def __aenter__(self) -> "_ChargerResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, (), status=self.status)

    @property
    def content_length(self) -> int:
        return len(self._body)

    @property
    def content(self) -> StreamReaderStub:
        return StreamReaderStub(self._body)


class _ChargerSession:
    """The charger at the other end of the setup request."""

    def __init__(self, status: int = 200, on_post=None) -> None:
        self.status = status
        self.on_post = on_post
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _ChargerResponse:
        self.calls.append({"url": url, **kwargs})
        if self.on_post is not None:
            self.on_post()
        return _ChargerResponse(self.status)


class _ConfigEntries:
    """In-memory ``hass.config_entries``: entries, flows in progress, updates."""

    def __init__(self, entries=(), in_progress=()) -> None:
        self._entries = list(entries)
        self._in_progress = list(in_progress)
        self.reloaded: list[str] = []
        self.flow = SimpleNamespace(
            async_progress_by_handler=self._progress,
            async_abort=lambda flow_id: None,
        )

    def _progress(self, handler, include_uninitialized=False, match_context=None):
        wanted = match_context or {}
        return [
            flow
            for flow in self._in_progress
            if flow["handler"] == handler
            and all(flow["context"].get(key) == value for key, value in wanted.items())
        ]

    def async_entry_for_domain_unique_id(self, domain, unique_id):
        return next(
            (
                entry
                for entry in self._entries
                if entry.domain == domain and entry.unique_id == unique_id
            ),
            None,
        )

    def async_update_entry(self, entry, **changes) -> bool:
        for key, value in changes.items():
            setattr(entry, key, value)
        return True

    async def async_reload(self, entry_id: str) -> bool:
        self.reloaded.append(entry_id)
        return True


class _Hass:
    def __init__(self, *, session=None, entries=(), in_progress=(), states=None) -> None:
        self.session = session or _ChargerSession()
        self.config_entries = _ConfigEntries(entries, in_progress)
        known = states or {}
        self.states = SimpleNamespace(get=known.get)


class _DeviceRegistry:
    def __init__(self, devices) -> None:
        self.devices = {device.id: device for device in devices}

    def async_update_device(self, device_id, *, new_identifiers) -> None:
        self.devices[device_id].identifiers = new_identifiers


@pytest.fixture(autouse=True)
def _charger_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_flow.aiohttp_client, "async_get_clientsession", lambda hass: hass.session
    )


def _install_device_registry(monkeypatch, hass, devices) -> _DeviceRegistry:
    """One registry per hass, filtered by config entry, as HA's is."""
    registry = _DeviceRegistry(devices)
    registries = {id(hass): registry}
    monkeypatch.setattr(config_flow.dr, "async_get", lambda h: registries[id(h)])
    monkeypatch.setattr(
        config_flow.dr,
        "async_entries_for_config_entry",
        lambda reg, entry_id: [
            device for device in reg.devices.values() if device.config_entry_id == entry_id
        ],
    )
    return registry


def _entry(**data: Any) -> SimpleNamespace:
    stored = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: "16A",
        CONF_SCHEME: "http",
        CONF_PHASES: 1,
        CONF_SOC_MODE: "basic",
        **data,
    }
    return SimpleNamespace(
        domain=DOMAIN,
        entry_id=f"entry-{stored[CONF_HOST]}",
        unique_id=stored[CONF_HOST],
        title=f"Eveus Charger ({stored[CONF_HOST]})",
        source="user",
        data=stored,
    )


def _flow(hass: _Hass, source: str = "user", entry=None) -> config_flow.ConfigFlow:
    flow = config_flow.ConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "flow-under-test"
    flow.context = {"source": source}
    flow.async_create_entry = lambda *, title, data: {
        "type": "create_entry",
        "title": title,
        "data": data,
    }
    flow.async_abort = lambda *, reason: {"type": "abort", "reason": reason}
    if entry is not None:
        flow._get_reconfigure_entry = lambda: entry
        flow._get_reauth_entry = lambda: entry
    return flow


def _options_flow(hass: _Hass, entry) -> config_flow.EveusOptionsFlow:
    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = hass
    flow.async_show_form = lambda *, step_id, data_schema=None: {
        "type": "form",
        "step_id": step_id,
        "data_schema": data_schema,
    }
    return flow


def _user_input(**overrides: Any) -> dict[str, Any]:
    return {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: "16A",
        CONF_PHASES: 1,
        CONF_SOC_MODE: "basic",
        **overrides,
    }


def _field(schema: vol.Schema, name: str):
    return next(key for key in schema.schema if key.schema == name)


def _rendered_field(schema: vol.Schema, name: str) -> dict[str, Any]:
    return next(field for field in serialize_schema(schema) if field["name"] == name)


# --- setup (user step) -------------------------------------------------------


def test_setup_creates_the_entry_titled_by_host_and_authenticates_to_the_charger() -> None:
    hass = _Hass()

    result = asyncio.run(_flow(hass).async_step_user(_user_input()))

    assert result["type"] == "create_entry"
    assert result["title"] == f"Eveus Charger ({TEST_HOST})"
    assert result["data"][CONF_HOST] == TEST_HOST
    (call,) = hass.session.calls
    assert call["url"] == f"http://{TEST_HOST}/main"
    assert call["auth"] == aiohttp.BasicAuth(TEST_USERNAME, TEST_PASSWORD)


def test_setup_over_http_warns_that_credentials_travel_in_cleartext(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=config_flow.__name__)

    asyncio.run(_flow(_Hass()).async_step_user(_user_input()))

    assert any("cleartext" in record.getMessage() for record in caplog.records)


def test_setup_rejects_a_username_basic_auth_cannot_encode() -> None:
    """A non-Latin-1 username alone (ASCII password) must fail as invalid
    input before any request, not surface later as a broken poll."""
    hass = _Hass()

    result = asyncio.run(
        _flow(hass).async_step_user(_user_input(**{CONF_USERNAME: "Антон"}))
    )

    assert result["errors"] == {"base": "invalid_input"}
    assert hass.session.calls == []


def test_setup_form_redisplay_keeps_everything_but_the_password() -> None:
    hass = _Hass(session=_ChargerSession(status=401))
    submitted = _user_input(
        **{CONF_USERNAME: "garage", CONF_MODEL: "32A", CONF_SOC_MODE: "basic"}
    )

    result = asyncio.run(_flow(hass).async_step_user(submitted))

    assert result["errors"] == {"base": "invalid_auth"}
    schema = result["data_schema"]
    assert _field(schema, CONF_USERNAME).default() == "garage"
    assert _field(schema, CONF_MODEL).default() == "32A"
    assert _field(schema, CONF_SOC_MODE).default() == "basic"
    assert _rendered_field(schema, CONF_PASSWORD)["selector"]["text"]["type"] == "password"


# --- advanced SOC step -------------------------------------------------------


def _setup_soc_form() -> vol.Schema:
    flow = _flow(_Hass())
    form = asyncio.run(flow.async_step_user(_user_input(**{CONF_SOC_MODE: "advanced"})))
    assert form["step_id"] == "soc"
    return form["data_schema"]


@pytest.mark.parametrize(
    "submitted",
    [
        {CONF_BATTERY_CAPACITY: 9.9, CONF_SOC_CORRECTION: 5},
        {CONF_BATTERY_CAPACITY: 60, CONF_SOC_CORRECTION: -0.1},
        {CONF_BATTERY_CAPACITY: 60, CONF_SOC_CORRECTION: 20.1},
    ],
    ids=["capacity-below-10", "correction-below-0", "correction-above-20"],
)
def test_soc_form_rejects_values_outside_the_documented_range(submitted) -> None:
    schema = _setup_soc_form()

    with pytest.raises(vol.Invalid):
        schema(submitted)


def test_soc_form_accepts_the_range_edges() -> None:
    schema = _setup_soc_form()

    assert schema({CONF_BATTERY_CAPACITY: 10, CONF_SOC_CORRECTION: 0})
    assert schema({CONF_BATTERY_CAPACITY: 160, CONF_SOC_CORRECTION: 20})


# --- reconfigure --------------------------------------------------------------


def test_reconfigure_form_prefills_the_stored_connection_details() -> None:
    entry = _entry(**{CONF_USERNAME: "garage", CONF_MODEL: "32A", CONF_SCHEME: "https"})

    result = asyncio.run(_flow(_Hass(), "reconfigure", entry).async_step_reconfigure())

    schema = result["data_schema"]
    assert _field(schema, CONF_HOST).default() == f"https://{TEST_HOST}"
    assert _field(schema, CONF_USERNAME).default() == "garage"
    assert _field(schema, CONF_MODEL).default() == "32A"
    assert _rendered_field(schema, CONF_PASSWORD)["selector"]["text"]["type"] == "password"


def test_reconfigure_to_a_new_address_moves_the_entry_and_its_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry()
    hass = _Hass(entries=[entry])
    registry = _install_device_registry(
        monkeypatch,
        hass,
        [
            SimpleNamespace(
                id="charger",
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, TEST_HOST), (DOMAIN, f"{TEST_HOST}_2")},
            ),
            SimpleNamespace(
                id="someone-else",
                config_entry_id="another-entry",
                identifiers={(DOMAIN, TEST_HOST)},
            ),
        ],
    )

    result = asyncio.run(
        _flow(hass, "reconfigure", entry).async_step_reconfigure(
            {
                CONF_HOST: TEST_HOST_ALT,
                CONF_USERNAME: TEST_USERNAME,
                CONF_PASSWORD: TEST_PASSWORD,
                CONF_MODEL: "16A",
                CONF_PHASES: 1,
            }
        )
    )

    assert result == {"type": "abort", "reason": "reconfigure_successful"}
    assert hass.session.calls[0]["url"] == f"http://{TEST_HOST_ALT}/main"
    assert entry.unique_id == TEST_HOST_ALT
    assert entry.title == f"Eveus Charger ({TEST_HOST_ALT})"
    assert entry.data[CONF_HOST] == TEST_HOST_ALT
    assert registry.devices["charger"].identifiers == {
        (DOMAIN, TEST_HOST_ALT),
        (DOMAIN, f"{TEST_HOST_ALT}_2"),
    }
    assert registry.devices["someone-else"].identifiers == {(DOMAIN, TEST_HOST)}
    assert hass.config_entries.reloaded == [entry.entry_id]


def test_reconfigure_onto_another_chargers_address_aborts_as_already_configured() -> None:
    entry = _entry()
    other = _entry(**{CONF_HOST: TEST_HOST_ALT})
    hass = _Hass(entries=[entry, other])

    with pytest.raises(AbortFlow) as exc_info:
        asyncio.run(
            _flow(hass, "reconfigure", entry).async_step_reconfigure(
                {
                    CONF_HOST: TEST_HOST_ALT,
                    CONF_USERNAME: TEST_USERNAME,
                    CONF_PASSWORD: TEST_PASSWORD,
                    CONF_MODEL: "16A",
                    CONF_PHASES: 1,
                }
            )
        )

    assert exc_info.value.reason == "already_configured"
    assert entry.data[CONF_HOST] == TEST_HOST


# --- reauth -------------------------------------------------------------------


def test_reauth_form_prefills_the_stored_username_and_masks_the_password() -> None:
    entry = _entry(**{CONF_USERNAME: "garage"})

    result = asyncio.run(_flow(_Hass(), "reauth", entry).async_step_reauth_confirm())

    schema = result["data_schema"]
    assert _field(schema, CONF_USERNAME).default() == "garage"
    assert _rendered_field(schema, CONF_PASSWORD)["selector"]["text"]["type"] == "password"


def test_reauth_stores_new_credentials_and_keeps_the_entry_identity() -> None:
    entry = _entry(**{CONF_PASSWORD: "old-secret"})
    hass = _Hass(entries=[entry])

    result = asyncio.run(
        _flow(hass, "reauth", entry).async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "new-secret"}
        )
    )

    assert result == {"type": "abort", "reason": "reauth_successful"}
    assert hass.session.calls[0]["auth"] == aiohttp.BasicAuth(TEST_USERNAME, "new-secret")
    assert entry.data[CONF_PASSWORD] == "new-secret"
    assert entry.unique_id == TEST_HOST
    assert entry.title == f"Eveus Charger ({TEST_HOST})"
    assert hass.config_entries.reloaded == [entry.entry_id]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "REAL BUG candidate (mutant config_flow.xǁConfigFlowǁasync_step_reauth_confirm"
        "__mutmut_38): reauth cannot change the host, yet it rewrites the entry "
        "title to 'Eveus Charger (<host>)', discarding a title the user set. "
        "Left to the owner to decide."
    ),
)
def test_reauth_keeps_a_title_the_user_renamed() -> None:
    entry = _entry()
    entry.title = "Garage charger"
    hass = _Hass(entries=[entry])

    asyncio.run(
        _flow(hass, "reauth", entry).async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
        )
    )

    assert entry.title == "Garage charger"


def test_reauth_against_an_address_that_moved_mid_check_reads_connection_failed() -> None:
    entry = _entry()

    def _reconfigured_elsewhere() -> None:
        entry.data = {**entry.data, CONF_HOST: TEST_HOST_ALT}

    hass = _Hass(entries=[entry], session=_ChargerSession(on_post=_reconfigured_elsewhere))

    result = asyncio.run(
        _flow(hass, "reauth", entry).async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "new-secret"}
        )
    )

    assert result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"] == {"error_detail": "connection failed"}


# --- options flow -------------------------------------------------------------


def test_options_form_offers_the_mode_and_the_current_car_soc_sensor() -> None:
    entry = _entry(
        **{CONF_SOC_MODE: "advanced", CONF_EXTERNAL_SOC_ENTITY: "sensor.car_battery"}
    )

    form = asyncio.run(_options_flow(_Hass(), entry).async_step_init())

    schema = form["data_schema"]
    assert _field(schema, CONF_SOC_MODE).default() == "advanced"
    assert _field(schema, CONF_EXTERNAL_SOC_ENTITY).description == {
        "suggested_value": "sensor.car_battery"
    }
    assert schema(
        {CONF_SOC_MODE: "basic", CONF_EXTERNAL_SOC_ENTITY: "sensor.other_car"}
    ) == {CONF_SOC_MODE: "basic", CONF_EXTERNAL_SOC_ENTITY: "sensor.other_car"}


def test_options_soc_step_prefills_what_the_entry_already_stores() -> None:
    """Switching to Advanced with only one set-once value stored must show
    that value, not a generic default the user would unknowingly confirm."""
    entry = _entry(
        **{CONF_BATTERY_CAPACITY: 77, CONF_EXTERNAL_SOC_ENTITY: "sensor.car_battery"}
    )

    form = asyncio.run(
        _options_flow(_Hass(), entry).async_step_init({CONF_SOC_MODE: "advanced"})
    )

    assert form["step_id"] == "soc"
    schema = form["data_schema"]
    assert _field(schema, CONF_BATTERY_CAPACITY).default() == 77
    assert _field(schema, CONF_EXTERNAL_SOC_ENTITY).description == {
        "suggested_value": "sensor.car_battery"
    }
