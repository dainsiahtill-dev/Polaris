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
import statistics
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
    if value is None:
        return levels
    items: list[Any] = list(value) if isinstance(value, (list, tuple, set)) else [value]
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


def _aggregate_samples(case_ids: list[str], runs: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Aggregate per-case results across ``runs`` independent samples.

    Produces pass-rate / median / mean / spread + a stability class
    (stable_pass | flaky | stable_fail) and keeps up to two failing-run digests
    so each failure is ROOT-CAUSABLE from data (systematic vs nondeterministic).
    """
    agg: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        observed = [run[case_id] for run in runs if case_id in run]
        if not observed:
            continue
        scores = [float(o.get("score") or 0.0) for o in observed]
        passes = [bool(o.get("passed")) for o in observed]
        n = len(scores)
        pass_count = sum(1 for p in passes if p)
        pass_rate = pass_count / n
        stability = "stable_pass" if pass_count == n else ("stable_fail" if pass_count == 0 else "flaky")
        fail_digests = [
            {
                "score": round(float(o.get("score") or 0.0), 3),
                "error": str(o.get("error") or ""),
                "output": str(o.get("output") or "")[:240],
            }
            for o in observed
            if not bool(o.get("passed"))
        ][:2]
        entry: dict[str, Any] = {
            "case_id": case_id,
            "n_runs": n,
            "pass_count": pass_count,
            "pass_rate": round(pass_rate, 4),
            "median_score": round(statistics.median(scores), 4),
            "mean_score": round(sum(scores) / n, 4),
            "min_score": round(min(scores), 4),
            "max_score": round(max(scores), 4),
            "scores": [round(s, 4) for s in scores],
            "stability": stability,
            "fail_digests": fail_digests,
        }
        classified = _classify_case_id(case_id)
        if classified is not None:
            entry["level"], entry["dimension"] = classified
        agg[case_id] = entry
    return agg


def _roll_sampled(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Average pass-rate + median-of-medians over a group of aggregated cases."""
    n = len(items)
    if n == 0:
        return {"n": 0, "avg_pass_rate": 0.0, "median_score": 0.0}
    return {
        "n": n,
        "avg_pass_rate": round(sum(i["pass_rate"] for i in items) / n, 4),
        "median_score": round(statistics.median([i["median_score"] for i in items]), 4),
    }


def _build_sampled_matrix(agg: dict[str, dict[str, Any]], samples: int) -> dict[str, Any]:
    """Anti-jitter level x dimension scorecard from sample-aggregated cases."""
    cells = list(agg.values())
    by_level: dict[int, list[dict[str, Any]]] = {}
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    grid: dict[str, dict[str, list[dict[str, Any]]]] = {}
    stability_counts = {"stable_pass": 0, "flaky": 0, "stable_fail": 0}
    for entry in cells:
        stability_counts[str(entry.get("stability") or "flaky")] += 1
        level = entry.get("level")
        dimension = entry.get("dimension")
        if level is None or dimension is None:
            continue
        by_level.setdefault(int(level), []).append(entry)
        by_dimension.setdefault(str(dimension), []).append(entry)
        grid.setdefault(f"l{level}", {}).setdefault(str(dimension), []).append(entry)
    return {
        "samples": samples,
        "stability_counts": stability_counts,
        "levels": sorted(by_level),
        "dimensions": sorted(by_dimension),
        "cells": cells,
        "by_level": {f"l{lvl}": _roll_sampled(items) for lvl, items in sorted(by_level.items())},
        "by_dimension": {dim: _roll_sampled(items) for dim, items in sorted(by_dimension.items())},
        "grid": {
            level_key: {dim: _roll_sampled(items) for dim, items in dim_map.items()}
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
    """Run the Scout capability matrix through the agentic-eval engine — ONE sample.

    Delegates execution + multi-dimensional scoring to ``run_agentic_benchmark_suite``
    (``UnifiedBenchmarkRunner`` + ``UnifiedJudge`` + the scout validators) and attaches
    a level x dimension scorecard.

    ANTI-JITTER multi-sampling MUST use PROCESS ISOLATION (a fresh subprocess per
    sample) — repeating ``run_agentic_benchmark_suite`` inside a single process tears
    down the LLM provider after the first run, so only the first sample gets real LLM
    responses and later samples decay to empty-output scores. Drive k samples as k
    fresh subprocesses and aggregate the per-case results with ``_aggregate_samples`` /
    ``_build_sampled_matrix`` (both reusable, deterministic, LLM-free).
    """
    del role  # scout matrix selects its own cases (scout + caller roles)
    ctx = dict(context or {})
    opts = dict(options or {})

    target_ids = _select_scout_case_ids(opts, ctx)
    if not target_ids:
        return {
            "ok": False,
            "error": "no scout matrix cases matched the requested level/dimension filter",
            "details": {"cases": [], "scout_matrix": _build_sampled_matrix({}, 0)},
        }

    # Lazy import avoids a load-time cycle (public service imports internal modules).
    from polaris.cells.llm.evaluation.public.service import run_agentic_benchmark_suite

    run_options = dict(opts)
    run_options["benchmark_case_ids"] = target_ids
    run_options.pop("samples", None)
    run_options.pop("repeats", None)
    result = await run_agentic_benchmark_suite(
        provider_cfg,
        model,
        "all",  # cases span scout + pm/chief_engineer/director; select by id, not role
        workspace=workspace,
        settings=settings,
        context=ctx,
        options=run_options,
    )
    details = (result.get("details") if isinstance(result, dict) else None) or {}
    case_map = {
        str(c.get("id")): {
            "score": c.get("score"),
            "passed": c.get("passed"),
            "output": c.get("output"),
            "error": c.get("error"),
        }
        for c in (details.get("cases") or [])
        if c.get("id")
    }
    agg = _aggregate_samples(target_ids, [case_map])
    out_details = dict(details)
    out_details["scout_matrix"] = _build_sampled_matrix(agg, 1)
    out_details["samples"] = 1
    out_details["total_cases"] = len(agg)
    return {"ok": bool(result.get("ok")) if isinstance(result, dict) else False, "details": out_details}
