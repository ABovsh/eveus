"""CI workflow contracts."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
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
        "custom_components/eveus/number.py",
        "custom_components/eveus/switch.py",
        "custom_components/eveus/common_command.py",
        "custom_components/eveus/select.py",
        "custom_components/eveus/time.py",
        "custom_components/eveus/binary_sensor.py",
        "custom_components/eveus/button.py",
        "custom_components/eveus/session_history.py",
        "custom_components/eveus/sensor_definitions.py",
        "custom_components/eveus/ev_sensors.py",
        "custom_components/eveus/const.py",
        "custom_components/eveus/diagnostics.py",
        "custom_components/eveus/device_trigger.py",
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
    # An increase is an error that fails the job, not a warning nobody reads.
    assert "::error::" in run_text
    assert "result-ids survived" in run_text


# Meta-tests: they assert things about the repo itself (metadata, platform
# naming, test quality) rather than exercising a mutable source module, so no
# mutation leg can be killed by them.
_NON_KILLER_TESTS = frozenset({
    "tests/test_metadata.py",
    "tests/test_name_platform_compat.py",
    "tests/test_test_quality_contracts.py",
    "tests/test_ci_workflow.py",
    # Its charger-facing test needs EVEUS_LIVE_HOST; its `_OPTIONAL_FIELDS`
    # self-check does run in CI but asserts on test data, not integration code.
    # Neither can kill a mutant; the file guards the fixture against drift.
    "tests/test_firmware_drift_live.py",
    # Asserts on the shipped blueprint YAML, not on integration code, so there
    # is no mutant for it to kill.
    "tests/test_blueprints.py",
})


def test_every_test_file_is_wired_into_a_mutation_leg() -> None:
    """A new test file must be assigned to a leg, or explicitly excused.

    Adding a test file without wiring it in silently weakens the weekly run:
    the target is still mutated, the tests that would kill those mutants never
    run, and the survivor ratchet fires as if untested code had shipped.
    """
    import pathlib

    wired = set()
    for leg in _mutation_matrix_legs():
        wired.update(leg["tests"].split())
    on_disk = {
        f"tests/{p.name}" for p in pathlib.Path("tests").glob("test_*.py")
    }
    unwired = on_disk - wired - _NON_KILLER_TESTS
    assert not unwired, (
        "test files wired to no mutation leg (add them to a leg's `tests:` "
        f"list, or to _NON_KILLER_TESTS with a reason): {sorted(unwired)}"
    )


def test_non_killer_exclusions_still_exist() -> None:
    """A renamed or deleted meta-test must not linger in the excuse list."""
    import pathlib

    missing = [t for t in _NON_KILLER_TESTS if not pathlib.Path(t).exists()]
    assert not missing, f"stale entries in _NON_KILLER_TESTS: {missing}"


def test_mutation_ratchet_fails_the_job_on_an_increase() -> None:
    """A survivor increase must fail the job, not just print a warning.

    This workflow is a weekly cron, not a PR gate, so failing it loudly costs
    nothing but attention — and a silent warning is exactly how a weakened
    killer-test list ships unnoticed.
    """
    from pathlib import Path

    body = Path(".github/workflows/mutation-tests.yaml").read_text(encoding="utf-8")
    ratchet = body.split("Check survivor baseline")[1]
    increase_branch = ratchet.split('elif [ "$survived" -lt "$baseline" ]')[0]
    assert "exit 1" in increase_branch, (
        "the survivors-increased branch does not fail the job"
    )


# Package modules deliberately outside the mutation matrix. Each needs a
# reason, and test_unmutated_modules_still_exist keeps the list from rotting.
_UNMUTATED_MODULES = frozenset({
    # Pure re-export shim: imports and __all__, no branch for a mutant to hide
    # in.
    "custom_components/eveus/common.py",
    # Platform setup. Its one real branch (the SOC-mode gate deciding whether
    # the six EV sensors exist) is covered by test_soc_sensors_only_in_advanced,
    # but wiring it into a leg shifts that leg's survivor count and the ratchet
    # fails the job on any increase — so it needs a mutmut run to re-baseline
    # .github/mutation-baseline.json in the same commit. Excused until then.
    "custom_components/eveus/sensor.py",
})


def test_every_package_module_is_mutated_or_excused() -> None:
    """A new module must join a mutation leg, or say why it does not.

    The sibling check for test files has existed since the workflow landed;
    the one for SOURCE files was a hand-written list, so a module added to the
    package simply never appeared in it and was mutated by nothing, silently.
    Deriving the set from the directory is what makes that impossible.
    """
    import pathlib

    mutated = set()
    for leg in _mutation_matrix_legs():
        mutated.update(leg["paths"].split(","))
    on_disk = {
        f"custom_components/eveus/{p.name}"
        for p in pathlib.Path("custom_components/eveus").glob("*.py")
    }
    unmutated = on_disk - mutated - _UNMUTATED_MODULES
    assert not unmutated, (
        "package modules mutated by no leg (add them to a leg's `paths:` and "
        "re-baseline, or to _UNMUTATED_MODULES with a reason): "
        f"{sorted(unmutated)}"
    )


def test_unmutated_modules_still_exist() -> None:
    """A renamed or deleted module must not linger in the excuse list."""
    import pathlib

    missing = [m for m in _UNMUTATED_MODULES if not pathlib.Path(m).exists()]
    assert not missing, f"stale entries in _UNMUTATED_MODULES: {missing}"


def _internal_doc_patterns_from_gitignore() -> list[str]:
    """The internal-doc block of .gitignore, read as data.

    Bounded by its own header comment and the first blank line after it, so a
    pattern added to that block is picked up here with no edit.
    """
    lines = Path(".gitignore").read_text(encoding="utf-8").splitlines()
    start = next(
        k for k, line in enumerate(lines) if line.startswith("# Internal/private docs")
    )
    patterns = []
    for line in lines[start + 1:]:
        if not line.strip():
            break
        if not line.startswith("#"):
            patterns.append(line.strip())
    return patterns


def _leak_guard_expression(name: str) -> str:
    """One of the deny expressions, read from the script that owns it.

    The list moved out of the workflow and into `.github/leak-guard.sh` on
    2026-09-06 (generated from /opt/scripts/git-hooks/leak-guard.sh). Reading it
    from wherever it lives today is the point: a test anchored to the old
    location stopped checking anything at all when the list moved.
    """
    script = Path(".github/leak-guard.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{name}='([^']+)'", script, re.M)
    assert match is not None, f"leak-guard.sh must define {name}"
    return match.group(1)


def _guard_matches(expression: str, sample: str) -> bool:
    """Ask the guard's own engine, not Python's.

    `scan()` classifies with `grep -iE`, so the match is case-INSENSITIVE ERE.
    Python's `re.search` would answer this question differently for two of the
    classes .gitignore lists — `AUDIT_FINDINGS*.md` and `*_PLAN.md` are matched
    only by the `-i`.
    """
    return subprocess.run(
        ["grep", "-iE", expression],
        input=f"{sample}\n", capture_output=True, text=True, check=False,
    ).returncode == 0


def test_leak_guard_denies_every_internal_doc_class_gitignore_lists() -> None:
    """The guard and `.gitignore` must name the same classes.

    .gitignore is the fast pre-flight; the guard is the control, because it
    "cannot be bypassed by a local --no-verify or a force-push" — its own words.
    A class the guard does not know is protected only by the bypassable half of
    the pair, which is the wrong way round.

    The samples are DERIVED from .gitignore, not hand-typed. This test exists
    because a round found `*.local.md` listed in .gitignore and missing from the
    guard; a fixed sample list pins today's state and cannot catch the next one
    — the same defect wearing a different filename. It checks BEHAVIOUR (the
    real expression, through the real matcher, with the real case rules) rather
    than the shape of the file the expression happens to live in.
    """
    workflow = Path(".github/workflows/leak-guard.yml").read_text(encoding="utf-8")
    assert ".github/leak-guard.sh" in workflow, (
        "the workflow must invoke the checker script — an expression nothing "
        "calls guards nothing"
    )
    internal_re = _leak_guard_expression("INTERNAL_RE")
    allow_re = _leak_guard_expression("ALLOW_RE")

    patterns = _internal_doc_patterns_from_gitignore()
    assert len(patterns) >= 7, (
        f"only {len(patterns)} patterns found in .gitignore's internal-doc block "
        "— its header comment or terminating blank line moved"
    )

    for pattern in patterns:
        if not (pattern.endswith("/") or pattern.endswith(".md")):
            continue  # non-document artifacts are not this guard's job
        sample = (
            f"{pattern}notes.md" if pattern.endswith("/") else pattern.replace("*", "x")
        )
        # `scan()` drops ALLOW_RE hits BEFORE classifying, so an exempted sample
        # would pass this test while leaking in production.
        assert not _guard_matches(allow_re, sample), (
            f"{sample!r} is exempted by the guard's ALLOW_RE, so the INTERNAL_RE "
            "check below would be vacuous"
        )
        assert _guard_matches(internal_re, sample), (
            f".gitignore lists {pattern!r} as an internal-doc class but the leak "
            f"guard would let {sample!r} through — add it to INTERNAL_RE in "
            "/opt/scripts/git-hooks/leak-guard.sh and re-run sync-leak-guard.sh"
        )
