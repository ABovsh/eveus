"""CI workflow contracts: structural invariants only (one mutation job, its
config in one place, every module mutated or excused, one survivor ratchet).
Flags, caps and strings inside the workflow files are not pinned."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tomllib

import yaml


# --- Mutation-tests workflow contracts -------------------------------------
#
# The mutation workflow exists to prove the test suite catches real bugs. It
# is one mutmut 3 job: the tool forks per mutant and runs only the tests that
# reach the mutated function, so the twelve hand-curated killer lists and the
# per-leg baselines that kept mutmut 2 inside its time budget are gone. What
# is left to keep honest is where the selection lives (pyproject.toml, once),
# that no package module silently escapes it, and the survivor ratchet.

_MUTATION_WORKFLOW = Path(".github/workflows/mutation-tests.yaml")
_MUTATION_BASELINE = Path(".github/mutation-baseline.json")
_PACKAGE = Path("custom_components/eveus")


def _mutmut_config() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "mutmut"
    ]


def test_mutation_workflow_is_a_single_job_without_a_matrix() -> None:
    """One job, no legs: per-leg target and killer lists cannot come back."""
    doc = yaml.safe_load(_MUTATION_WORKFLOW.read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert len(jobs) == 1, "expected a single mutation job"
    job = next(iter(jobs.values()))
    assert "strategy" not in job, "mutation targets/tests belong in [tool.mutmut]"


def test_mutmut_targets_are_config_not_workflow_flags() -> None:
    """The selection lives in `[tool.mutmut]`, and every path in it exists."""
    config = _mutmut_config()
    for path in config["source_paths"] + config.get("do_not_mutate", []):
        assert Path(path).exists(), f"[tool.mutmut] names a missing path: {path}"
    for arg in config["pytest_add_cli_args"]:
        if arg.startswith("--ignore="):
            target = arg.removeprefix("--ignore=")
            assert Path(target).is_file(), f"ignored test file is gone: {target}"


def test_every_package_module_is_mutated_or_excused() -> None:
    """A new module is mutated by default; leaving one out needs a config entry.

    `source_paths` names the package directory, so a module added to it is
    picked up with no edit. The only way to escape is `do_not_mutate`, which
    is where a reason gets written down.
    """
    config = _mutmut_config()
    assert config["source_paths"] == [f"{_PACKAGE}/"], (
        "mutate the whole package directory, not a hand-kept list of files"
    )
    on_disk = {f"{_PACKAGE}/{p.name}" for p in _PACKAGE.glob("*.py")}
    excused = set(config.get("do_not_mutate", []))
    assert excused <= on_disk, f"stale do_not_mutate entries: {sorted(excused - on_disk)}"


def test_mutation_baseline_is_one_survivor_ratchet() -> None:
    """A single observed number; the per-leg keys belonged to the matrix."""
    import json

    baseline = json.loads(_MUTATION_BASELINE.read_text(encoding="utf-8"))
    keys = {k for k in baseline if not k.startswith("_")}
    assert keys == {"survivors"}, f"unexpected baseline keys: {sorted(keys)}"
    assert isinstance(baseline["survivors"], int) and baseline["survivors"] >= 0


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
