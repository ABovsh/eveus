"""A charger without a password: the username and password may stay empty.

REVE has no web-interface password (its ``/init`` reports the user ``eveus``
and empty password fields), and ``/main`` answers without credentials on the
classic firmware too (verified 2026-09-03), yet setup demanded both fields, so
owners typed in something made up. Empty fields now mean "send no
credentials"; a charger that does want them answers 401, and the flow says the
credentials are wrong, as before.

Eveus serves plain HTTP only, so the cleartext warning no longer recommends
HTTPS, and with no password there is nothing to warn about.
"""
from __future__ import annotations

import logging

import aiohttp
import pytest
import voluptuous as vol

from conftest import TEST_HOST
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.eveus import config_flow
from custom_components.eveus.client import basic_auth
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.config_flow import (
    build_user_data_schema,
    normalize_user_input,
    validate_credentials,
)
from custom_components.eveus.const import CONF_MODEL, CONF_PHASES, CONF_SOC_MODE


def test_empty_credentials_are_valid() -> None:
    assert validate_credentials("", "") == ("", "")
    assert validate_credentials("eveus", "") == ("eveus", "")


def test_omitted_credentials_are_stored_empty() -> None:
    data = normalize_user_input(
        {CONF_HOST: TEST_HOST, CONF_MODEL: "16A", CONF_PHASES: 1, CONF_SOC_MODE: "basic"}
    )
    assert (data[CONF_USERNAME], data[CONF_PASSWORD]) == ("", "")


def test_the_form_marks_both_fields_optional() -> None:
    schema = build_user_data_schema()
    keys = {key.schema: key for key in schema.schema}
    assert isinstance(keys[CONF_USERNAME], vol.Optional)
    assert isinstance(keys[CONF_PASSWORD], vol.Optional)
    validated = schema({CONF_HOST: TEST_HOST, CONF_MODEL: "16A", CONF_PHASES: 1})
    assert CONF_PASSWORD not in validated or validated[CONF_PASSWORD] == ""


def test_no_credentials_send_no_authorization() -> None:
    assert basic_auth("", "") is None
    assert basic_auth("eveus", "") == aiohttp.BasicAuth("eveus", "")
    assert basic_auth("user", "pass") == aiohttp.BasicAuth("user", "pass")


def test_the_updater_sends_no_authorization_without_credentials() -> None:
    from test_reve_firmware import _Hass

    updater = EveusUpdater(TEST_HOST, "", "", _Hass())
    assert updater.basic_auth is None


def test_no_password_means_no_cleartext_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.config_flow"):
        config_flow._warn_if_plaintext("http", "")
    assert caplog.records == []


def test_the_cleartext_warning_recommends_nothing_eveus_cannot_do(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.config_flow"):
        config_flow._warn_if_plaintext("http", "secret")
    (record,) = caplog.records
    assert "cleartext" in record.getMessage()
    assert "HTTPS" not in record.getMessage()
