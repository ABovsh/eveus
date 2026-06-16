"""Every config-flow step schema must be JSON-serializable for the frontend.

Home Assistant serializes a flow's ``data_schema`` with ``voluptuous_serialize``
before sending it to the UI. A bare validator function in the schema makes that
conversion raise, so the config dialog fails to load with a 500 error (issue #8).
These tests reproduce that serialization the exact way HA does.
"""
from __future__ import annotations

import homeassistant.helpers.config_validation as cv
import voluptuous_serialize

from custom_components.eveus import config_flow as cf


class _Hass:
    states = type("S", (), {"get": staticmethod(lambda eid: None)})()


def _assert_serializable(schema) -> None:
    voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)


def test_user_step_schema_is_serializable() -> None:
    _assert_serializable(cf.build_user_data_schema({}))


def test_soc_step_schema_is_serializable() -> None:
    _assert_serializable(cf.build_soc_step_schema(_Hass(), defaults={}))


def test_reauth_step_schema_is_serializable() -> None:
    _assert_serializable(cf.build_reauth_data_schema({}))
