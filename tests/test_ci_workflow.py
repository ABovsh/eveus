"""CI workflow contracts: structural invariants only (targets and killer tests
exist, legs are disjoint, every module is mutated or excused, every test file
is wired). Flags, caps and strings inside the workflow files are not pinned."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import shlex

import yaml


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
    # Pins the unique_id inventory across all seven platform modules at once
    # (P4.0). A single-leg wiring would only reflect one module's mutants and
    # sensor.py is already excused from the matrix, so it cannot map cleanly
    # onto one leg's survivor count.
    "tests/test_entity_inventory.py",
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
    # The typed /main parse. Its natural home is the payload-utils leg (it sits
    # next to _payload.py and is covered by tests/test_snapshot.py, which IS
    # wired there), but adding a module to a leg grows that leg's mutant count
    # and the survivor ratchet fails the job on any increase — which needs an
    # observed mutmut run to re-baseline, never a predicted number. Same
    # position as sensor.py above: excused until that run happens.
    "custom_components/eveus/snapshot.py",
    # The single charger HTTP path. Same position as snapshot.py: its natural
    # leg is `coordinator` (tests/test_client.py is wired there), but adding
    # the module grows that leg's mutant count and the ratchet fails on any
    # increase without an observed re-baseline.
    "custom_components/eveus/client.py",
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


def test_mutmut_config_skips_log_and_annotation_lines() -> None:
    """mutmut_config.pre_mutation must skip log lines, bare strings/comments,
    lines already marked `pragma: no mutate`, and annotation-only lines (dead
    under PEP 563) -- but must NOT skip a real comparison/call line."""
    import types

    import mutmut_config

    def _skip(line: str) -> bool:
        context = types.SimpleNamespace(current_source_line=line, skip=False)
        mutmut_config.pre_mutation(context)
        return context.skip

    assert _skip('    _LOGGER.debug("Eveus poll failed: %s", err)')
    assert _skip('    """Docstring line."""')
    assert _skip("    # a comment")
    assert _skip("    x = 1  # pragma: no mutate - reason")
    assert _skip("    raw_version: int | None")
    assert _skip("    phases: int = DEFAULT_PHASES")

    # A real comparison/call line must still be mutated.
    assert not _skip("    if value > threshold:")
    assert not _skip("    return get_safe_value(data, 'state', int)")
