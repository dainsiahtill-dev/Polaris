"""Build a bounded, read-only retrieval plan for a probe target (UTF-8)."""
from __future__ import annotations

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.target import extract_terms, hint_globs, hint_paths

_RG_MAX = "40"
_SYMBOL_PREFIXES = ("def ", "class ", "func ", "function ", "interface ", "type ")


def build_read_plan(target: ScoutProbeTargetV1) -> list[tuple[str, list[str]]]:
    """Return an ordered list of (tool, args) read-tool calls."""
    plan: list[tuple[str, list[str]]] = []
    paths = hint_paths(target)
    globs = hint_globs(target)
    terms = extract_terms(target)

    # boundary mode: map the structure of hinted paths first
    if target.mode == "boundary":
        for p in paths or ["."]:
            plan.append(("repo_tree", [p, "--depth", "2"]))

    # search each term; symbol-biased pattern first, then plain text
    for term in terms:
        symbol_pattern = rf"(def|class|func|function|interface|type)\s+\w*{term}"
        plan.append(("repo_rg", _rg_args(symbol_pattern, paths, globs)))
        plan.append(("repo_rg", _rg_args(term, paths, globs)))

    return plan


def _rg_args(pattern: str, paths: list[str], globs: list[str]) -> list[str]:
    args = [pattern, *paths]
    if globs:
        args += ["--glob", globs[0]]
    args += ["--max", _RG_MAX]
    return args
