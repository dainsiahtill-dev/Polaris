"""Build a bounded, read-only retrieval plan for a probe target (UTF-8).

Each plan step is a ``(tool, args)`` pair where ``args`` is a dict keyed by the
tool's real parameter names (as declared in ``ToolSpecRegistry``), so the plan
can be handed straight to a :class:`ReadToolPort` / the canonical executor.

Host-authority planning
------------------------
``read_roots`` are the roots a HOST committed for reading (never model
supplied). When they are given, the planner emits one step per root so every
authorized root is actually searched — searching only the first root would
silently starve the others. Model hints may only NARROW the root set; a hint
never adds a root and never widens one to its parent directory.

Without ``read_roots`` (no committed scope) the planner keeps its historical
behaviour: hint paths, else the workspace root.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.scout.internal.target import extract_terms, hint_globs, hint_paths
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1

_RG_MAX = 40
_TREE_DEPTH = 2
_SYMBOLS_MAX = 200


def build_read_plan(
    target: ScoutProbeTargetV1,
    *,
    read_roots: tuple[str, ...] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return an ordered list of ``(tool, args)`` read-tool calls.

    Args are dicts keyed by each tool's real parameter names:
    - ``repo_rg``: ``pattern`` (+ optional ``path``, ``glob``) and ``max_results``
    - ``repo_tree``: ``path`` and ``depth``
    - ``repo_symbols_index``: ``paths`` (+ optional ``glob``) and ``max_results``

    Args:
        target: The probe target (its hints may narrow, never widen).
        read_roots: Host-committed readable roots, or None when no scope was
            committed (legacy undefined scope).
    """
    plan: list[tuple[str, dict[str, Any]]] = []
    paths = hint_paths(target)
    globs = hint_globs(target)
    terms = extract_terms(target)

    if read_roots is not None:
        # The host committed a scope: every root it authorized must be
        # searched. Model hints may narrow a root set, never extend it.
        roots = _narrow_to_roots(paths, read_roots)
        if not roots:
            # The hints point entirely outside the authorized roots; searching
            # the host roots is still correct and stays inside the scope.
            roots = list(read_roots)
    else:
        roots = paths or ["."]

    # Always map the structure of the search scope first. This is the most
    # reliable read tool (pure filesystem) and seeds boundary-style findings.
    for root in roots:
        plan.append(("repo_tree", {"path": root, "depth": _TREE_DEPTH}))

    if target.mode == "boundary":
        # Boundary mode is about structure; the tree above is sufficient.
        return plan

    if not roots:
        # An explicitly empty scope authorizes nothing: there is no directory
        # to index and no path to search. Falling back to "." would search the
        # whole workspace and defeat the committed scope.
        return plan

    # Index symbols across the scope so symbol-name matches surface even when a
    # text search backend is unavailable.
    plan.append(("repo_symbols_index", _symbols_args(roots, globs)))

    # Search each term in each authorized root; symbol-biased pattern first,
    # then plain text. One step per (root, term) keeps every root reachable.
    for term in terms:
        symbol_pattern = rf"(def|class|func|function|interface|type)\s+\w*{term}"
        for root in roots:
            plan.append(("repo_rg", _rg_args(symbol_pattern, root, globs)))
            plan.append(("repo_rg", _rg_args(term, root, globs)))

    return plan


def _narrow_to_roots(hint_paths: list[str], read_roots: tuple[str, ...]) -> list[str]:
    """Keep only the host roots that a model hint points into.

    A hint is a narrowing hint, not an authority: a hint naming a path
    outside every authorized root contributes nothing, and a hint never adds a
    root that the host did not commit.
    """
    if not hint_paths:
        return list(read_roots)
    narrowed = [root for root in read_roots if any(_is_within(hint, root) for hint in hint_paths)]
    return narrowed


def _is_within(hint: str, root: str) -> bool:
    """True when ``hint`` denotes ``root`` or something inside it."""
    hint_norm = str(hint or "").replace("\\", "/").strip("/")
    root_norm = str(root or "").replace("\\", "/").strip("/")
    if not hint_norm or not root_norm:
        return False
    if hint_norm == root_norm:
        return True
    return hint_norm.startswith(f"{root_norm}/")


def _rg_args(pattern: str, path: str, globs: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {"pattern": pattern, "max_results": _RG_MAX}
    if path and path != ".":
        # The repo_rg handler scopes the search to a single ``path``; ``paths``
        # is declared by the same ToolSpec and the canonical read-scope gate
        # derives its operand from BOTH fields, so both must name the
        # authorized root. The workspace root (".") keeps its historical shape:
        # it is already covered by the default operand of both fields.
        args["path"] = path
        args["paths"] = [path]
    if globs:
        args["glob"] = globs[0]
    return args


def _symbols_args(paths: list[str], globs: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {"paths": paths or ["."], "max_results": _SYMBOLS_MAX}
    if globs:
        args["glob"] = globs[0]
    return args
