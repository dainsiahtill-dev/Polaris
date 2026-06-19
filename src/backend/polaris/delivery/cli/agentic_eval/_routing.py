"""Suite/mode dispatch and argument-range normalization for agentic-eval.

Owns the suite-runner registry, the agentic/context/strategy/all mode
router, and the ``--levels`` range expansion used by the matrix suites.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

from polaris.cells.llm.evaluation.public.service import (
    run_agentic_benchmark_suite,
    run_context_benchmark_suite,
    run_context_projection_matrix_suite,
    run_projection_adaptive_matrix_suite,
    run_scout_matrix_suite,
    run_speculation_matrix_suite,
    run_strategy_benchmark_suite,
    run_tool_calling_matrix_suite,
)

__all__ = [
    "_TOOL_CALLING_MATRIX_LEVEL_PREFIXES",
    "_aggregate_all_mode_results",
    "_expand_level_range_to_case_ids",
    "_normalize_suite_name",
    "_parse_level_range",
    "_run_benchmark_by_mode",
    "_suite_runners",
]


def _suite_runners() -> dict[str, Any]:
    return {
        "agentic_benchmark": run_agentic_benchmark_suite,
        "tool_calling_matrix": run_tool_calling_matrix_suite,
        "speculation_matrix": run_speculation_matrix_suite,
        "context_projection_matrix": run_context_projection_matrix_suite,
        "projection_adaptive_matrix": run_projection_adaptive_matrix_suite,
        "scout_matrix": run_scout_matrix_suite,
    }


def _run_benchmark_by_mode(
    mode: str,
    provider_cfg: dict[str, Any],
    model: str | None,
    role: str,
    workspace: str,
    context: Mapping[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Route benchmark execution based on mode.

    Args:
        mode: Benchmark mode - "agentic", "strategy", "context", or "all"
        provider_cfg: Provider configuration dict
        model: Model name
        role: Role identifier
        workspace: Workspace path
        context: Context mapping
        options: Options mapping

    Returns:
        For single mode: benchmark result dict
        For "all" mode: aggregated result dict
    """
    if mode == "all":
        # Run all three suites sequentially and aggregate results
        agentic_result = asyncio.run(
            run_agentic_benchmark_suite(
                provider_cfg,
                model,
                role,
                workspace=workspace,
                context=context,
                options=options,
            )
        )

        # Context and strategy get role from options/context
        context_options = dict(options)
        context_options["role"] = role
        context_result = asyncio.run(
            run_context_benchmark_suite(
                provider_cfg,
                model,
                workspace=workspace,
                context=context,
                options=context_options,
            )
        )

        strategy_options = dict(options)
        strategy_options["role"] = role
        strategy_result = asyncio.run(
            run_strategy_benchmark_suite(
                provider_cfg,
                model,
                workspace=workspace,
                context=context,
                options=strategy_options,
            )
        )

        # Aggregate results from all three modes
        return _aggregate_all_mode_results(
            agentic=agentic_result,
            context=context_result,
            strategy=strategy_result,
        )

    # Single mode - route to appropriate runner
    runner: Any
    if mode == "agentic":
        runner = run_agentic_benchmark_suite
    elif mode == "context":
        runner = run_context_benchmark_suite
    elif mode == "strategy":
        runner = run_strategy_benchmark_suite
    else:
        return {"ok": False, "error": f"unknown mode: {mode}", "details": {}}

    # For context/strategy, role goes in options
    if mode in ("context", "strategy"):
        opts = dict(options)
        opts["role"] = role
        return asyncio.run(
            runner(
                provider_cfg,
                model,
                workspace=workspace,
                context=context,
                options=opts,
            )
        )

    # For agentic, role is a direct parameter
    return asyncio.run(
        runner(
            provider_cfg,
            model,
            role,
            workspace=workspace,
            context=context,
            options=options,
        )
    )


def _aggregate_all_mode_results(
    agentic: dict[str, Any],
    context: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate results from agentic, context, and strategy benchmarks.

    Returns a combined result dict with aggregated scores and status.
    """
    # Extract ok status and scores from each mode
    agentic_ok = bool(agentic.get("ok", False))
    context_ok = bool(context.get("ok", False))
    strategy_ok = bool(strategy.get("ok", False))

    # Get scores from each mode
    def _get_score(result: dict[str, Any]) -> float:
        if "details" in result:
            # Legacy agentic format
            details = result.get("details", {})
            return float(details.get("average_score", 0.0))
        # New format from context/strategy
        summary = result.get("summary", {})
        return float(summary.get("average_score", 0.0))

    def _get_total(result: dict[str, Any]) -> int:
        if "details" in result:
            return int(result.get("details", {}).get("total_cases", 0))
        return int(result.get("summary", {}).get("total", 0))

    def _get_passed(result: dict[str, Any]) -> int:
        if "details" in result:
            return int(result.get("details", {}).get("passed_cases", 0))
        return int(result.get("summary", {}).get("passed", 0))

    total_cases = sum(_get_total(r) for r in (agentic, context, strategy))
    total_passed = sum(_get_passed(r) for r in (agentic, context, strategy))
    total_failed = total_cases - total_passed
    avg_score = sum(_get_score(r) for r in (agentic, context, strategy)) / 3.0 if total_cases > 0 else 0.0

    # Use agentic's artifact_path and run_id as primary
    agentic_details = agentic.get("details", {})
    agentic_report = agentic_details.get("report", {})
    run_id = agentic_report.get("test_run_id", "all-mode")

    return {
        "ok": agentic_ok and context_ok and strategy_ok,
        "error": "",
        "details": {
            "cases": agentic_details.get("cases", []),
            "artifact_path": agentic_details.get("artifact_path", ""),
            "report": {
                "suite": "all_benchmark",
                "test_run_id": run_id,
                "summary": {
                    "total_cases": total_cases,
                    "passed_cases": total_passed,
                    "failed_cases": total_failed,
                    "average_score": avg_score,
                },
            },
            "total_cases": total_cases,
            "passed_cases": total_passed,
            "failed_cases": total_failed,
            "average_score": avg_score,
            "mode_results": {
                "agentic": agentic,
                "context": context,
                "strategy": strategy,
            },
        },
    }


def _normalize_suite_name(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in _suite_runners():
        return token
    return "agentic_benchmark"


# Level to case prefix mapping for tool_calling_matrix suite
_TOOL_CALLING_MATRIX_LEVEL_PREFIXES: dict[int, str] = {
    1: "l1_",
    2: "l2_",
    3: "l3_",
    4: "l4_",
    5: "l5_",
    6: "l6_",
    7: "l7_",
    8: "l8_",
    9: "l9_",
}


def _parse_level_range(range_str: str) -> set[int]:
    """Parse a level range string like 'l1-l3' into a set of level numbers.

    Supports formats:
    - 'l1' or '1' -> single level {1}
    - 'l1-l3' or '1-3' -> levels {1, 2, 3}
    - 'l1,l3' or '1,3' -> levels {1, 3}

    Returns:
        Set of level numbers (1-9).
    """
    result: set[int] = set()
    token = str(range_str or "").strip().lower()
    if not token:
        return result

    # Remove single 'l' prefix if present (but not all 'l' chars)
    if token.startswith("l") and len(token) > 1 and token[1].isdigit():
        token = token[1:]

    # Handle comma-separated values
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        # Handle range like '1-3' or 'l1-l3'
        if "-" in part:
            range_parts = part.split("-")
            if len(range_parts) == 2:
                # Strip 'l' prefix from each part if present
                start_str = range_parts[0].strip()
                end_str = range_parts[1].strip()
                if start_str.startswith("l") and len(start_str) > 1 and start_str[1].isdigit():
                    start_str = start_str[1:]
                if end_str.startswith("l") and len(end_str) > 1 and end_str[1].isdigit():
                    end_str = end_str[1:]
                try:
                    start = int(start_str)
                    end = int(end_str)
                    for level in range(start, end + 1):
                        if 1 <= level <= 9:
                            result.add(level)
                except ValueError:
                    pass
        else:
            # Single number - strip 'l' prefix if present
            if part.startswith("l") and len(part) > 1 and part[1].isdigit():
                part = part[1:]
            try:
                level = int(part)
                if 1 <= level <= 9:
                    result.add(level)
            except ValueError:
                pass
    return result


def _expand_level_range_to_case_ids(level_ranges: Iterable[Any] | None) -> list[str]:
    """Expand level range strings like 'l1-l3' into case ID prefixes for filtering.

    Returns list of case ID prefixes like ['l1_', 'l2_', 'l3_'].
    """
    if level_ranges is None:
        return []
    levels: set[int] = set()
    for item in level_ranges:
        token = str(item or "").strip()
        if not token:
            continue
        levels.update(_parse_level_range(token))

    prefixes: list[str] = []
    for level in sorted(levels):
        prefix = _TOOL_CALLING_MATRIX_LEVEL_PREFIXES.get(level)
        if prefix:
            prefixes.append(prefix)
    return prefixes
