"""Scout (探子) capability matrix suite for agentic-eval.

Runs the Scout L1-L6 reconnaissance cases (code_search / doc_exploration /
detective) plus the PM / Chief Engineer / Director *caller* cases (which delegate
reconnaissance to the ``scout_probe`` sub-agent) THROUGH the agentic-eval engine —
real ``roles.runtime`` LLM sessions graded by ``UnifiedJudge`` with the scout
validators — and aggregates a multi-dimensional scorecard (level x dimension x
score / pass-rate / per-category).

This is a thin MATRIX WRAPPER over ``run_agentic_benchmark_suite``: it discovers
and filters the ``scout_*`` cases, delegates execution + multi-dimensional scoring
to the existing engine, then rolls the per-case results up into a matrix. It does
NOT re-implement loading, execution, or scoring.

Run via the agentic-eval CLI ONLY (never pytest):
    python -m polaris.delivery.cli.agentic_eval --suite scout_matrix [--level l1-l6]
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Scout cases live in the shared agentic_benchmark cases dir; this file is at
# cells/llm/evaluation/internal/ -> parents[1] == cells/llm/evaluation.
_CASES_DIR: Path = Path(__file__).resolve().parents[1] / "fixtures" / "agentic_benchmark" / "cases"

_SCOUT_DIMENSIONS: tuple[str, ...] = ("code_search", "doc_exploration", "detective")
_CALLER_ROLES: tuple[str, ...] = ("pm", "chief_engineer", "director")

_LEVEL_RE = re.compile(r"^scout_l(?P<level>\d+)_(?P<rest>.+)$")
_CALLER_RE = re.compile(r"^scout_caller_(?P<role>pm|chief_engineer|director)_")


def _classify_case_id(case_id: str) -> tuple[int, str] | None:
    """Map a scout case_id to ``(level, dimension)``.

    Scout level cases:  ``scout_l{N}_{dimension}_{slug}`` -> (N, dimension)
    Caller cases:        ``scout_caller_{role}_{slug}``    -> (0, "caller_{role}")
    Returns None for non-scout case ids.
    """
    caller = _CALLER_RE.match(case_id)
    if caller:
        return 0, f"caller_{caller.group('role')}"
    level_match = _LEVEL_RE.match(case_id)
    if level_match:
        level = int(level_match.group("level"))
        rest = level_match.group("rest")
        for dimension in _SCOUT_DIMENSIONS:
            if rest.startswith(dimension):
                return level, dimension
        return level, "other"
    return None


def _discover_scout_cases() -> dict[str, tuple[int, str]]:
    """Return ``{case_id: (level, dimension)}`` for every scout_* case on disk."""
    discovered: dict[str, tuple[int, str]] = {}
    if not _CASES_DIR.is_dir():
        return discovered
    for path in sorted(_CASES_DIR.glob("scout_*.json")):
        classified = _classify_case_id(path.stem)
        if classified is not None:
            discovered[path.stem] = classified
    return discovered


def _parse_level_filter(value: Any) -> set[int]:
    """Parse a level filter (``"l1-l6"``, ``"1,3"``, ``[1,2]``, ``"l4"``) -> set of ints."""
    levels: set[int] = set()
    items: list[Any]
    if value is None:
        return levels
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    for item in items:
        token = str(item or "").strip().lower().lstrip("l")
        if not token:
            continue
        if "-" in token:
            lo, _, hi = token.partition("-")
            try:
                for level in range(int(lo.lstrip("l")), int(hi.lstrip("l")) + 1):
                    levels.add(level)
            except ValueError:
                continue
        else:
            for part in token.split(","):
                part = part.strip().lstrip("l")
                if part.isdigit():
                    levels.add(int(part))
    return levels


def _select_scout_case_ids(options: Mapping[str, Any], context: Mapping[str, Any]) -> list[str]:
    """Resolve which scout cases to run from explicit ids or level/dimension filters."""
    explicit = options.get("benchmark_case_ids") or context.get("benchmark_case_ids")
    if explicit:
        ids = [explicit] if isinstance(explicit, str) else list(explicit)
        return [str(i).strip() for i in ids if str(i).strip()]

    discovered = _discover_scout_cases()
    level_filter = _parse_level_filter(
        options.get("scout_levels") or options.get("levels") or context.get("scout_levels")
    )
    # The agentic-eval CLI `--level l1-l6` arrives as matrix_case_ids prefixes
    # (e.g. "l3_"); fold those into the scout level filter.
    if not level_filter:
        for prefix in options.get("matrix_case_ids") or context.get("matrix_case_ids") or ():
            prefix_match = re.match(r"l(\d+)", str(prefix).strip().lower())
            if prefix_match:
                level_filter.add(int(prefix_match.group(1)))
    dimension_filter = str(options.get("scout_dimension") or context.get("scout_dimension") or "").strip().lower()
    include_callers = bool(options.get("include_callers", context.get("include_callers", True)))

    selected: list[str] = []
    for case_id, (level, dimension) in discovered.items():
        is_caller = dimension.startswith("caller_")
        if is_caller:
            if not include_callers:
                continue
            if dimension_filter and dimension_filter not in ("caller", dimension):
                continue
        else:
            if level_filter and level not in level_filter:
                continue
            if dimension_filter and dimension != dimension_filter:
                continue
        selected.append(case_id)
    return sorted(selected)


def _rollup(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Average score + pass-rate over a list of per-case result entries."""
    n = len(entries)
    if n == 0:
        return {"n": 0, "avg_score": 0.0, "pass_rate": 0.0}
    avg = sum(float(e.get("score") or 0.0) for e in entries) / n
    passed = sum(1 for e in entries if bool(e.get("passed")))
    return {"n": n, "avg_score": round(avg, 4), "pass_rate": round(passed / n, 4)}


def _build_matrix(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-case results into a level x dimension scorecard."""
    cells: list[dict[str, Any]] = []
    by_level: dict[int, list[dict[str, Any]]] = {}
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    grid: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for case in cases:
        case_id = str(case.get("id") or "")
        classified = _classify_case_id(case_id)
        if classified is None:
            continue
        level, dimension = classified
        entry = {
            "case_id": case_id,
            "level": level,
            "dimension": dimension,
            "score": float(case.get("score") or 0.0),
            "passed": bool(case.get("passed")),
        }
        cells.append(entry)
        by_level.setdefault(level, []).append(entry)
        by_dimension.setdefault(dimension, []).append(entry)
        grid.setdefault(f"l{level}", {}).setdefault(dimension, []).append(entry)

    return {
        "levels": sorted(by_level),
        "dimensions": sorted(by_dimension),
        "cells": cells,
        "by_level": {f"l{level}": _rollup(entries) for level, entries in sorted(by_level.items())},
        "by_dimension": {dim: _rollup(entries) for dim, entries in sorted(by_dimension.items())},
        "grid": {
            level_key: {dim: _rollup(entries) for dim, entries in dim_map.items()}
            for level_key, dim_map in sorted(grid.items())
        },
    }


async def run_scout_matrix_suite(
    provider_cfg: dict[str, Any],
    model: str | None,
    role: str = "scout",
    *,
    workspace: str,
    settings: Any = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the Scout capability matrix through the agentic-eval engine.

    Delegates execution + multi-dimensional scoring to ``run_agentic_benchmark_suite``
    (which uses ``UnifiedBenchmarkRunner`` + ``UnifiedJudge`` + the scout validators)
    and augments the result with a level x dimension scorecard.
    """
    del role  # scout matrix selects its own cases (scout + caller roles)
    ctx = dict(context or {})
    opts = dict(options or {})

    target_ids = _select_scout_case_ids(opts, ctx)
    if not target_ids:
        return {
            "ok": False,
            "error": "no scout matrix cases matched the requested level/dimension filter",
            "details": {"cases": [], "scout_matrix": _build_matrix([])},
        }

    # Lazy import avoids a load-time cycle (public service imports internal modules).
    from polaris.cells.llm.evaluation.public.service import run_agentic_benchmark_suite

    run_options = dict(opts)
    run_options["benchmark_case_ids"] = target_ids
    result = await run_agentic_benchmark_suite(
        provider_cfg,
        model,
        "all",  # cases span scout + pm/chief_engineer/director; select by id, not role
        workspace=workspace,
        settings=settings,
        context=ctx,
        options=run_options,
    )

    details = result.get("details") if isinstance(result, dict) else None
    if isinstance(details, dict):
        details["scout_matrix"] = _build_matrix(list(details.get("cases") or []))
    return result
