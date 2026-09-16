"""mutmut 2.x config: skip mutants that can never be killed.

mutmut imports this module automatically (mutmut 2.5.1) and honours
`context.skip`. Every target file uses `from __future__ import annotations`
(PEP 563), so a mutation confined to a type annotation is inert at runtime --
no test can ever observe it, and the survivor is pure noise in the ratchet.
Log calls, bare strings (docstrings) and comments are likewise never
exercised by test assertions, and an explicit `pragma: no mutate` line has
already been reviewed and excused by hand.
"""
from __future__ import annotations

import re

_ANNOTATION_ONLY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.\[\]]*\s*:\s*[^=]+(?:=\s*[^=(),<>!]+)?$"
)


def _is_annotation_only(line: str) -> bool:
    """True for a bare `name: Type` / `name: Type = default` line.

    Conservative: a line with a call (contains "(") or a comparison operator
    is never treated as annotation-only, even if it happens to match the
    leading `name: Type` shape (e.g. a dict literal or a slice).
    """
    if "(" in line or any(op in line for op in ("==", "!=", "<=", ">=", "<", ">")):
        return False
    return bool(_ANNOTATION_ONLY_RE.match(line))


def pre_mutation(context) -> None:
    """Skip mutating a line that can never affect a test outcome."""
    line = context.current_source_line.strip()
    if (
        line.startswith(("_LOGGER.", '"', "'", "#"))
        or "pragma: no mutate" in line
        or _is_annotation_only(line)
    ):
        context.skip = True
