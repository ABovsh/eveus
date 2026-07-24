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
        "custom_components/eveus/safety.py",
        "custom_components/eveus/common_base.py",
        "custom_components/eveus/control_base.py",
        "custom_components/eveus/__init__.py",
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
    # The summary pane is not retrievable through the REST API; the same
    # results must also go to stdout so the job log carries them.
    assert "tee" in report_steps[0]["run"]


def test_mutation_gate_is_report_only_not_survivor_count() -> None:
    """All 12 runs before 2026-07-23 failed: every target file uses
    `from __future__ import annotations`, so some mutations (type-annotation
    flips) are permanently inert and zero survivors is unreachable. The run
    step must not let mutmut's survivor/timeout exit code fail the job.
    """
    run_steps = [
        step
        for step in _mutation_job()["steps"]
        if "mutmut run" in str(step.get("run", ""))
    ]
    assert len(run_steps) == 1
    assert run_steps[0].get("continue-on-error") is True


def test_mutation_crash_check_does_not_regress_to_legend_grep() -> None:
    """The 2026-07-23 gate fix's first attempt grepped mutmut-results.txt for
    the all-caps KILLED/TIMEOUT/SUSPICIOUS/SURVIVED legend, which is only ever
    printed by `mutmut run`'s startup banner (a different step) -- it never
    appears in `mutmut results`' own output, so that check failed every run
    regardless of outcome. The real check must key off crash signatures
    (empty file / traceback / usage error), not specific success text.
    """
    report_steps = [
        step
        for step in _mutation_job()["steps"]
        if "mutmut results" in str(step.get("run", ""))
    ]
    run_text = report_steps[0]["run"]
    assert "Traceback" in run_text
    assert not re.search(r"grep -qE '\^\(KILLED", run_text)


def test_mutation_survivor_diff_cap_covers_the_largest_leg() -> None:
    """coordinator alone has had 240 survivors; `head -20` hid over 90% of
    the report. The cap must stay well above any leg's realistic count.
    """
    report_steps = [
        step
        for step in _mutation_job()["steps"]
        if "mutmut results" in str(step.get("run", ""))
    ]
    cap_match = re.search(r"head -(\d+)\)", report_steps[0]["run"])
    assert cap_match is not None
    assert int(cap_match.group(1)) >= 300


# --- Survivor-count baseline ratchet ---------------------------------------
#
# The report-only gate (above) can never fail on survivor count by design, so
# nothing previously distinguished "expected noise" from "a real regression
# just landed." A committed baseline + comparison step closes that: an
# increase is flagged loudly, a decrease is a prompt to tighten the ratchet.

_MUTATION_BASELINE = Path(".github/mutation-baseline.json")


def test_mutation_baseline_file_covers_every_leg() -> None:
    """A leg missing from the baseline can silently regress with no signal."""
    import json

    baseline = json.loads(_MUTATION_BASELINE.read_text(encoding="utf-8"))
    leg_names = {leg["name"] for leg in _mutation_matrix_legs()}
    baseline_keys = {k for k in baseline if not k.startswith("_")}
    assert leg_names == baseline_keys, (
        f"baseline/matrix leg mismatch: matrix={leg_names} baseline={baseline_keys}"
    )


def test_mutation_workflow_checks_survivor_baseline() -> None:
    """Increases must be flagged; the step must read the committed baseline
    file and compare it against the current run's survivor count."""
    baseline_steps = [
        step
        for step in _mutation_job()["steps"]
        if "mutation-baseline.json" in str(step.get("run", ""))
    ]
    assert len(baseline_steps) == 1, "expected exactly one baseline-check step"
    run_text = baseline_steps[0]["run"]
    assert baseline_steps[0].get("if") == "always()"
    assert "::warning::" in run_text
    assert "result-ids survived" in run_text
