"""Tests for Eveus safety Repairs notices."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.helpers import issue_registry as ir


# ---------------------------------------------------------------------------
# In-memory issue-registry double.
#
# Real Home Assistant is installed in this environment, so the conftest no-HA
# stub never activates and the production code talks to the real issue
# registry. Production (``safety.ir``) and these tests both import the *same*
# ``homeassistant.helpers.issue_registry`` module object, so patching its
# functions (below, via an autouse fixture) routes both through this queryable
# in-memory double for the duration of each test. This mirrors how the existing
# battery/ocpp repair tests monkeypatch ``ir``, but adds lookup + ignored state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeIssueEntry:
    dismissed_version: str | None = None


class _FakeIssueRegistry:
    def __init__(self) -> None:
        self.issues: dict[tuple[str, str], _FakeIssueEntry] = {}

    def async_get_issue(self, domain: str, issue_id: str) -> _FakeIssueEntry | None:
        return self.issues.get((domain, issue_id))


def _fake_async_get(hass: Any) -> _FakeIssueRegistry:
    registry = getattr(hass, "issue_registry", None)
    if not isinstance(registry, _FakeIssueRegistry):
        registry = _FakeIssueRegistry()
        hass.issue_registry = registry
    return registry


def _fake_async_create_issue(
    hass: Any, domain: str, issue_id: str, **kwargs: Any
) -> None:
    # setdefault so a redundant create cannot clobber a user's ignored state.
    _fake_async_get(hass).issues.setdefault((domain, issue_id), _FakeIssueEntry())


def _fake_async_delete_issue(hass: Any, domain: str, issue_id: str) -> None:
    _fake_async_get(hass).issues.pop((domain, issue_id), None)


def _fake_async_ignore_issue(
    hass: Any, domain: str, issue_id: str, ignore: bool
) -> None:
    registry = _fake_async_get(hass)
    registry.issues[(domain, issue_id)] = _FakeIssueEntry(
        dismissed_version="test-version" if ignore else None
    )


@pytest.fixture(autouse=True)
def _install_fake_issue_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ir, "async_get", _fake_async_get, raising=False)
    monkeypatch.setattr(
        ir, "async_create_issue", _fake_async_create_issue, raising=False
    )
    monkeypatch.setattr(
        ir, "async_delete_issue", _fake_async_delete_issue, raising=False
    )
    monkeypatch.setattr(
        ir, "async_ignore_issue", _fake_async_ignore_issue, raising=False
    )


def test_fake_issue_registry_models_ignored_state() -> None:
    hass = SimpleNamespace()

    ir.async_create_issue(
        hass,
        "eveus",
        "safety_test_entry",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="safety_test",
    )

    issue = ir.async_get(hass).async_get_issue("eveus", "safety_test_entry")
    assert issue is not None
    assert issue.dismissed_version is None

    ir.async_ignore_issue(hass, "eveus", "safety_test_entry", True)
    issue = ir.async_get(hass).async_get_issue("eveus", "safety_test_entry")
    assert issue is not None
    assert issue.dismissed_version is not None
