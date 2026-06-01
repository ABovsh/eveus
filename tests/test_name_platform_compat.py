"""Entity name must work on the minimum supported Home Assistant.

The declared minimum is HA 2025.1, which does not define ``Entity.platform_data``
(that class attribute was added in a later release; HA Core notes it can be
removed again in 2026.8). The localized-name override must therefore detect
platform binding via the long-stable ``Entity.platform`` attribute instead, so
that reading an entity's ``name`` before it is added to a platform returns the
English fallback rather than raising ``AttributeError``.
"""
from __future__ import annotations

from homeassistant.helpers.entity import Entity

from conftest import EveusTestUpdater as _Updater
from custom_components.eveus.const import MODEL_16A
from custom_components.eveus.number import EveusCurrentNumber


def test_entity_name_does_not_require_platform_data(monkeypatch) -> None:
    """name must fall back to ENTITY_NAME without touching Entity.platform_data."""
    # Simulate an HA build that predates Entity.platform_data (e.g. the 2025.1
    # minimum). On such builds, referencing self.platform_data raises.
    monkeypatch.delattr(Entity, "platform_data", raising=False)

    updater = _Updater({"currentSet": "16"})
    entity = EveusCurrentNumber(updater, MODEL_16A, 1)

    # Entity is not bound to a platform yet, so the English name is expected.
    assert entity.name == "Charging Current"
