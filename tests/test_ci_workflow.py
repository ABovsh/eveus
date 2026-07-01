"""CI workflow contracts."""
from __future__ import annotations

from pathlib import Path
import re
import shlex

import yaml


def _latest_ha_python_version() -> tuple[int, ...]:
    workflow = Path(".github/workflows/validate.yaml").read_text(encoding="utf-8")
    latest_leg = re.search(
        r'- python-version: "([^"]+)"\n\s+ha-pin: ""\n\s+ha-label: "latest"',
        workflow,
    )
    assert latest_leg is not None, "Validate workflow must keep a latest-HA matrix leg"
    return tuple(int(part) for part in latest_leg.group(1).split("."))


def test_latest_ha_ci_leg_uses_python_that_can_install_latest_homeassistant() -> None:
    """The drift canary must be able to install current unpinned Home Assistant."""
    assert _latest_ha_python_version() >= (3, 14, 2)


# --- Mutation-tests workflow contracts -------------------------------------
#
# The mutation workflow exists to prove the test suite catches real bugs in the
# layers users have actually reported issues against (config flow above all:
# issues #4, #5, #8 and the old-firmware setup reports). These contracts keep
# it from silently regressing into a job that times out, mutates the wrong
# files, or runs killer tests that no longer exist.

_MUTATION_WORKFLOW = Path(".github/workflows/mutation-tests.yaml")


def _mutation_matrix_legs() -> list[dict[str, str]]:
    doc = yaml.safe_load(_MUTATION_WORKFLOW.read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert len(jobs) == 1, "expected a single matrix job"
    job = next(iter(jobs.values()))
    legs = job["strategy"]["matrix"]["include"]
    assert legs, "mutation matrix must have at least one leg"
    return legs


def _mutation_job() -> dict:
    doc = yaml.safe_load(_MUTATION_WORKFLOW.read_text(encoding="utf-8"))
    return next(iter(doc["jobs"].values()))


def test_mutation_workflow_covers_config_flow() -> None:
    """config_flow.py is the layer with the most user-reported breakage."""
    mutated = {
        path
        for leg in _mutation_matrix_legs()
        for path in leg["paths"].split(",")
    }
    assert "custom_components/eveus/config_flow.py" in mutated
    assert "custom_components/eveus/repairs.py" in mutated


def test_mutation_targets_and_killer_tests_exist() -> None:
    """A renamed target or test file must fail CI contracts, not the cron job."""
    for leg in _mutation_matrix_legs():
        for path in leg["paths"].split(","):
            assert Path(path).is_file(), f"{leg['name']}: missing target {path}"
        tests = shlex.split(leg["tests"])
        assert tests, f"{leg['name']}: empty killer test list"
        for test_file in tests:
            assert Path(test_file).is_file(), (
                f"{leg['name']}: missing killer test {test_file}"
            )


def test_mutation_matrix_legs_are_disjoint_and_complete() -> None:
    """Each target file is mutated by exactly one leg (no double work, no gaps)."""
    seen: list[str] = []
    for leg in _mutation_matrix_legs():
        seen.extend(leg["paths"].split(","))
    assert len(seen) == len(set(seen)), f"duplicated mutation targets: {seen}"
    # The pure-logic layer that predates this workflow must stay covered.
    for required in (
        "custom_components/eveus/utils.py",
        "custom_components/eveus/_payload.py",
        "custom_components/eveus/common_network.py",
        "custom_components/eveus/soc_limit.py",
    ):
        assert required in seen, f"mutation coverage lost for {required}"


def test_mutation_job_has_enough_time_and_no_fail_fast() -> None:
    """6/30's scheduled run died at the old 45-minute cap before reporting."""
    job = _mutation_job()
    assert job["timeout-minutes"] >= 90
    assert job["strategy"]["fail-fast"] is False


def test_mutation_runner_fails_fast_and_pins_mutmut2() -> None:
    """-x kills mutants on the first failing test; mutmut 3.x dropped the CLI."""
    text = _MUTATION_WORKFLOW.read_text(encoding="utf-8")
    assert "mutmut<3.0" in text, "6/23's run crashed on an unpinned mutmut 3.x"
    for leg in _mutation_matrix_legs():
        del leg  # every leg shares the single run step below
    run_steps = [
        step
        for step in _mutation_job()["steps"]
        if "mutmut run" in str(step.get("run", ""))
    ]
    assert len(run_steps) == 1
    assert "pytest -x -q" in run_steps[0]["run"]


def test_mutation_survivors_are_always_reported() -> None:
    """Survivor diffs are the workflow's entire product; never skip the report."""
    report_steps = [
        step
        for step in _mutation_job()["steps"]
        if "mutmut results" in str(step.get("run", ""))
    ]
    assert len(report_steps) == 1
    assert report_steps[0].get("if") == "always()"
    assert "GITHUB_STEP_SUMMARY" in report_steps[0]["run"]
