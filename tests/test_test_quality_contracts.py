"""Meta-tests for keeping the test suite maintainable."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _test_sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in TESTS.glob("test_*.py")
        if path.name != "test_test_quality_contracts.py"
    }


def test_tests_do_not_assert_on_production_source_text() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path, text in _test_sources().items()
        if "inspect.getsource(" in text
    ]
    assert offenders == []


def test_tests_do_not_keep_rc_history_section_markers() -> None:
    marker = re.compile(r"^\s*# From test_(?:rc|hardening|numeric|privacy|setup)", re.M)
    offenders = [
        str(path.relative_to(ROOT))
        for path, text in _test_sources().items()
        if marker.search(text)
    ]
    assert offenders == []


def test_ha_smoke_tests_emit_stack_traces_on_timeout() -> None:
    workflow = (ROOT / ".github" / "workflows" / "validate.yaml").read_text(
        encoding="utf-8"
    )
    assert "PYTHONFAULTHANDLER" in workflow
    assert '"1"' in workflow
    assert "-X faulthandler" in workflow
    assert "timeout" in workflow
    assert "tests_ha" in workflow


def test_mutation_workflow_targets_hostile_firmware_layer() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "mutation-tests.yaml"
    ).read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert "mutmut" in workflow
    for module in (
        "custom_components/eveus/utils.py",
        "custom_components/eveus/_payload.py",
        "custom_components/eveus/common_network.py",
        "custom_components/eveus/soc_limit.py",
    ):
        assert module in workflow
