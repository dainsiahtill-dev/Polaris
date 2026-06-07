"""Build a bounded, read-only retrieval plan for a probe target (UTF-8).

Each plan step is a ``(tool, args)`` pair where ``args`` is a dict keyed by the
tool's real parameter names (as declared in ``ToolSpecRegistry``), so the plan
can be handed straight to a :class:`ReadToolPort` / the canonical executor.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.scout.internal.target import extract_terms, hint_globs, hint_paths
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1

_RG_MAX = 40
_TREE_DEPTH = 2
_SYMBOLS_MAX = 200


def build_read_plan(target: ScoutProbeTargetV1) -> list[tuple[str, dict[str, Any]]]:
    """Return an ordered list of ``(tool, args)`` read-tool calls.

    Args are dicts keyed by each tool's real parameter names:
    - ``repo_rg``: ``pattern`` (+ optional ``path``, ``glob``) and ``max_results``
    - ``repo_tree``: ``path`` and ``depth``
    - ``repo_symbols_index``: ``paths`` (+ optional ``glob``) and ``max_results``
    """
    plan: list[tuple[str, dict[str, Any]]] = []
    paths = hint_paths(target)
    globs = hint_globs(target)
    terms = extract_terms(target)

    # Always map the structure of the search scope first. This is the most
    # reliable read tool (pure filesystem) and seeds boundary-style findings.
    tree_roots = paths or ["."]
    for root in tree_roots:
        plan.append(("repo_tree", {"path": root, "depth": _TREE_DEPTH}))

    if target.mode == "boundary":
        # Boundary mode is about structure; the tree above is sufficient.
        return plan

    # Index symbols across the scope so symbol-name matches surface even when a
    # text search backend is unavailable.
    plan.append(("repo_symbols_index", _symbols_args(paths, globs)))

    # Search each term; symbol-biased pattern first, then plain text.
    for term in terms:
        symbol_pattern = rf"(def|class|func|function|interface|type)\s+\w*{term}"
        plan.append(("repo_rg", _rg_args(symbol_pattern, paths, globs)))
        plan.append(("repo_rg", _rg_args(term, paths, globs)))

    return plan


def _rg_args(pattern: str, paths: list[str], globs: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {"pattern": pattern, "max_results": _RG_MAX}
    if paths:
        # The repo_rg handler scopes the search to a single ``path``.
        args["path"] = paths[0]
    if globs:
        args["glob"] = globs[0]
    return args


def _symbols_args(paths: list[str], globs: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {"paths": paths or ["."], "max_results": _SYMBOLS_MAX}
    if globs:
        args["glob"] = globs[0]
    return args
