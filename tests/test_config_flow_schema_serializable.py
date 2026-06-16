"""Config-flow step schemas must survive the full round-trip with the frontend.

Home Assistant serializes a flow's ``data_schema`` with ``voluptuous_serialize``
to render the dialog, then validates whatever the user submits back through that
same schema. Two failure modes have hit users:

* **Render direction (issue #8):** a bare validator function makes serialization
  raise, so the dialog fails to load with a 500 error.
* **Submit direction (issue #5):** the frontend returns select/number values as
  *strings* (``"1"``/``"3"``), so a schema that doesn't coerce rejects every
  choice with ``value must be one of [1, 3]``.

These tests reproduce both directions the exact way HA does.

The drivers are intentionally self-maintaining: they discover every
``async_step_*`` handler on both flow classes, drive each to the point where it
shows a form, and exercise whatever schema that form carries. A new step (or a
new schema builder reached from one) is therefore guarded automatically, with no
per-step test to remember to add.
"""
from __future__ import annotations

import asyncio
import inspect

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
import voluptuous_serialize

from custom_components.eveus import config_flow as cf
from custom_components.eveus.const import (
    CONF_BATTERY_CAPACITY,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    MODEL_16A,
    SOC_MODE_ADVANCED,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME


class _Hass:
    """Minimal hass: the schema builders only read ``states.get``."""

    states = type("S", (), {"get": staticmethod(lambda eid: None)})()


class _FakeEntry:
    """A stored entry whose data exercises the prefilled form branches.

    ``scheme=https`` drives the ``https://`` host-prefill path in
    ``build_user_data_schema``; the SOC values drive the prefilled SOC step.
    """

    entry_id = "test_entry"
    unique_id = "192.168.1.50"  # NOSONAR(python:S1313) - LAN test fixture
    data = {
        CONF_HOST: "192.168.1.50",  # NOSONAR(python:S1313) - LAN test fixture
        CONF_SCHEME: "https",
        CONF_USERNAME: "user",  # NOSONAR(python:S2068) - test fixture
        CONF_PASSWORD: "pass",  # NOSONAR(python:S2068) - test fixture
        CONF_MODEL: MODEL_16A,
        CONF_PHASES: 1,
        CONF_SOC_MODE: SOC_MODE_ADVANCED,
        CONF_BATTERY_CAPACITY: 80,
        CONF_SOC_CORRECTION: 10,
    }


def _assert_serializable(schema) -> None:
    """Serialize a schema exactly as HA's frontend layer does."""
    voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)


def _own_step_handlers(obj):
    """Yield the bound ``async_step_*`` handlers defined in our module.

    Filtering on ``__module__`` keeps the driver focused on the integration's
    own steps (auto-including any future one) while skipping generic handlers
    inherited from the Home Assistant base classes.
    """
    for name in dir(obj):
        if not name.startswith("async_step_"):
            continue
        func = getattr(type(obj), name, None)
        if getattr(func, "__module__", None) != cf.__name__:
            continue
        yield getattr(obj, name)


def _drive_to_form(handler):
    """Call a step handler so it renders its form, returning the schema or None.

    Steps display their form on the no-input path: ``user_input=None`` for the
    interactive steps, and the reauth entrypoint takes ``entry_data`` instead.
    ``async_show_form`` is stubbed so we never depend on flow-manager internals
    (flow_id/handler) or on a specific Home Assistant version.
    """
    captured: dict[str, object] = {}

    def _fake_show_form(*_args, step_id=None, data_schema=None, **_kwargs):
        captured["schema"] = data_schema
        return {"type": "form", "step_id": step_id, "data_schema": data_schema}

    handler.__self__.async_show_form = _fake_show_form

    params = list(inspect.signature(handler).parameters)
    kwargs = {params[0]: ({} if params[0] == "entry_data" else None)} if params else {}

    result = asyncio.run(handler(**kwargs))
    if "schema" in captured:
        return captured["schema"]
    # Step delegated without showing a form (e.g. it forwarded to another step
    # which captured instead); fall back to the result's own schema if any.
    return result.get("data_schema") if isinstance(result, dict) else None


def _collect_flow_schemas() -> dict[str, object]:
    """Drive every own step on both flow classes; map step name -> schema."""
    hass = _Hass()
    entry = _FakeEntry()

    config_flow = cf.ConfigFlow()
    config_flow.hass = hass
    # Override the HA base-class entry lookups so reconfigure/reauth resolve to
    # our fixture without standing up a real config-entry registry.
    config_flow._get_reconfigure_entry = lambda: entry
    config_flow._get_reauth_entry = lambda: entry

    options_flow = cf.EveusOptionsFlow(entry)
    options_flow.hass = hass

    schemas: dict[str, object] = {}
    for flow in (config_flow, options_flow):
        for handler in _own_step_handlers(flow):
            schemas[handler.__name__] = _drive_to_form(handler)
    return schemas


# --- Explicit per-builder guards (fast, name the contract directly) ----------


def test_user_step_schema_is_serializable() -> None:
    _assert_serializable(cf.build_user_data_schema({}))


def test_soc_step_schema_is_serializable() -> None:
    _assert_serializable(cf.build_soc_step_schema(_Hass(), defaults={}))


def test_reauth_step_schema_is_serializable() -> None:
    _assert_serializable(cf.build_reauth_data_schema({}))


# --- Self-maintaining whole-flow guard ---------------------------------------


def test_every_flow_step_schema_is_serializable() -> None:
    """Drive every step that shows a form; each schema must serialize.

    Covers the setup, SOC, reconfigure, reauth, and options steps — including
    the prefilled (stored-entry) variants — and auto-covers any step added
    later. This is the regression net for issue #8.
    """
    schemas = _collect_flow_schemas()

    # Floor: the known form steps must all be exercised, so a future refactor
    # that silently stops driving them can't make this test a no-op.
    expected = {
        "async_step_user",
        "async_step_soc",          # appears on both flows; last write wins, fine
        "async_step_reconfigure",
        "async_step_reauth_confirm",
        "async_step_init",
    }
    assert expected <= set(schemas), f"uncovered steps: {expected - set(schemas)}"

    for name, schema in schemas.items():
        assert schema is not None, f"{name} showed a form with no data_schema"
        _assert_serializable(schema)


def _frontend_submission(schema) -> dict:
    """Build the values the HA frontend would submit for a schema.

    The dialog renders from the serialized schema and returns the chosen values
    — crucially, select options and numbers come back as *strings*. We derive
    the submission from that same serialized contract and stringify it,
    reproducing exactly what the frontend sends.
    """
    serialized = voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)
    submission = {}
    for field in serialized:
        options = field.get("options")
        if options:
            first = options[0]
            value = first[0] if isinstance(first, (list, tuple)) else first
        elif field.get("default") not in (None, vol.UNDEFINED):
            value = field["default"]
        else:
            value = "x"  # field with no default (e.g. password)
        submission[field["name"]] = str(value)  # frontend submits scalars as strings
    return submission


def test_every_flow_step_accepts_frontend_string_input() -> None:
    """The frontend submits every field as a string; the schema must accept it.

    Mirror of the serialization guard for the opposite direction: the regression
    net for issue #5 ("value must be one of [1, 3]") and the same class of
    select/number coercion bug in any current or future step.
    """
    for name, schema in _collect_flow_schemas().items():
        submission = _frontend_submission(schema)
        try:
            schema(submission)
        except vol.Invalid as err:
            raise AssertionError(
                f"{name}: frontend submission {submission!r} rejected: {err}"
            ) from err
