"""Contract tests: every user-facing translation key referenced in code exists.

A missing key does not fail at runtime — HA silently shows the raw key
("invalid_response") in the config dialog or Repairs panel, which only
surfaces via user reports. These tests scan the source for every key the
integration can emit and require it in strings.json, en.json, and uk.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "eveus"

_TRANSLATION_FILES = {
    "strings.json": _COMPONENT / "strings.json",
    "en.json": _COMPONENT / "translations" / "en.json",
    "uk.json": _COMPONENT / "translations" / "uk.json",
}


def _load(name: str) -> dict:
    return json.loads(_TRANSLATION_FILES[name].read_text(encoding="utf-8"))


def _config_flow_source() -> str:
    return (_COMPONENT / "config_flow.py").read_text(encoding="utf-8")


def _error_keys_in_code() -> set[str]:
    return set(re.findall(r'errors\["base"\]\s*=\s*"([a-z_]+)"', _config_flow_source()))


def _abort_reasons_in_code() -> set[str]:
    return set(re.findall(r'reason="([a-z_]+)"', _config_flow_source()))


def _issue_translation_keys_in_code() -> set[str]:
    """Every translation_key an ir.async_create_issue call can produce."""
    keys: set[str] = set()
    for module in ("__init__.py", "repairs.py"):
        keys |= set(
            re.findall(
                r'translation_key="([a-z_]+)"', (_COMPONENT / module).read_text(encoding="utf-8")
            )
        )
    # safety.py builds keys dynamically as f"safety_{policy.key}".
    from custom_components.eveus.safety import POLICIES

    keys |= {f"safety_{policy.key}" for policy in POLICIES}
    # Clock-drift issues pick one of three keys by drift kind.
    keys |= {"clock_drift", "clock_drift_timezone", "clock_drift_fractional_timezone"}
    return keys


@pytest.mark.parametrize("filename", sorted(_TRANSLATION_FILES))
def test_config_flow_error_keys_are_translated(filename: str) -> None:
    translated = set(_load(filename)["config"]["error"])
    used = _error_keys_in_code()
    assert used, "source scan found no error keys — the regex is broken"
    missing = used - translated
    assert not missing, f"{filename} lacks config error translations for: {sorted(missing)}"


# Abort reasons raised by flows whose translations live outside config.abort:
# entry_missing is a repairs-flow abort. reload_failed is emitted by BOTH the
# config flow (reconfigure/reauth) and the options flow, so it must exist in
# config.abort (checked here) and options.abort (checked below).
_NON_CONFIG_ABORTS = {"entry_missing"}


@pytest.mark.parametrize("filename", sorted(_TRANSLATION_FILES))
def test_config_flow_abort_reasons_are_translated(filename: str) -> None:
    translated = set(_load(filename)["config"]["abort"])
    used = _abort_reasons_in_code() - _NON_CONFIG_ABORTS
    assert used, "source scan found no abort reasons — the regex is broken"
    missing = used - translated
    assert not missing, f"{filename} lacks config abort translations for: {sorted(missing)}"


@pytest.mark.parametrize("filename", sorted(_TRANSLATION_FILES))
def test_options_flow_abort_reasons_are_translated(filename: str) -> None:
    translated = set(_load(filename)["options"].get("abort", {}))
    assert "reload_failed" in translated, (
        f"{filename} lacks the options abort translation for reload_failed"
    )


@pytest.mark.parametrize("filename", sorted(_TRANSLATION_FILES))
def test_soc_mode_selector_options_are_translated(filename: str) -> None:
    from custom_components.eveus.const import SOC_MODE_OPTIONS

    options = _load(filename)["selector"]["soc_mode"]["options"]
    missing = set(SOC_MODE_OPTIONS) - set(options)
    assert not missing, f"{filename} lacks soc_mode option labels for: {sorted(missing)}"


@pytest.mark.parametrize("filename", sorted(_TRANSLATION_FILES))
def test_config_flow_selector_translation_keys_exist(filename: str) -> None:
    """Selectors in the flow reference translation keys; a typo shows raw values."""
    used = set(re.findall(r'translation_key="([a-z_]+)"', _config_flow_source()))
    assert used, "source scan found no selector translation keys — the regex is broken"
    translated = set(_load(filename)["selector"])
    missing = used - translated
    assert not missing, f"{filename} lacks selector translations for: {sorted(missing)}"


@pytest.mark.parametrize("filename", sorted(_TRANSLATION_FILES))
def test_issue_translation_keys_are_translated(filename: str) -> None:
    translated = set(_load(filename)["issues"])
    used = _issue_translation_keys_in_code()
    assert used, "source scan found no issue keys — the regex is broken"
    missing = used - translated
    assert not missing, f"{filename} lacks Repairs issue translations for: {sorted(missing)}"


def test_en_json_mirrors_strings_json_exactly() -> None:
    """HA convention: en.json is a byte-for-byte semantic copy of strings.json."""
    assert _load("strings.json") == _load("en.json")


def test_uk_json_has_identical_key_structure() -> None:
    """uk.json must translate every key en.json has — no more, no less."""

    def keyset(d: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            out.add(path)
            if isinstance(v, dict):
                out |= keyset(v, path)
        return out

    en, uk = keyset(_load("en.json")), keyset(_load("uk.json"))
    assert en - uk == set(), f"missing in uk.json: {sorted(en - uk)[:20]}"
    assert uk - en == set(), f"extra in uk.json: {sorted(uk - en)[:20]}"
