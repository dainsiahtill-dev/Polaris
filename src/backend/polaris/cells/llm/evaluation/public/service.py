"""Public service exports for `llm.evaluation` cell."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.llm.evaluation.internal.baseline_library import (
    list_baseline_library_sources,
    pull_baseline_library,
)
from polaris.cells.llm.evaluation.internal.benchmark_loader import (
    load_agentic_benchmark_case,
    load_builtin_agentic_benchmark_cases,
)
from polaris.cells.llm.evaluation.internal.benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeConfig,
    ToolArgumentRule as LegacyToolArgumentRule,
)
from polaris.cells.llm.evaluation.internal.deterministic_judge import judge_agentic_case
from polaris.cells.llm.evaluation.internal.index import (
    load_llm_test_index,
    reconcile_llm_test_index,
    reset_llm_test_index,
    update_index_with_report,
)
from polaris.cells.llm.evaluation.internal.interview import (
    build_interview_prompt,
    evaluate_interview_answer,
    generate_interview_answer,
    generate_interview_answer_streaming,
)
from polaris.cells.llm.evaluation.internal.readiness_tests import (
    run_readiness_tests,
    run_readiness_tests_streaming,
)
from polaris.cells.llm.evaluation.internal.runner import EvaluationRunner
from polaris.cells.llm.evaluation.internal.suites import run_connectivity_suite, run_connectivity_suite_sync
from polaris.cells.llm.evaluation.internal.tool_calling_matrix import (
    load_builtin_tool_calling_matrix_cases,
    load_tool_calling_matrix_case,
    run_tool_calling_matrix_suite,
)
from polaris.domain.verification.business_validators import (
    validate_director_safe_scope,
    validate_no_hallucinated_paths,
    validate_pm_plan_json,
    validate_qa_json,
    validate_qa_passfail,
)
from polaris.kernelone.benchmark.unified_judge import UnifiedJudge
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    ToolArgumentRule,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.benchmark.unified_runner import (
    BenchmarkSuiteResult,
    UnifiedBenchmarkRunner,
)
from polaris.kernelone.storage import resolve_runtime_path

__all__ = [
    "EvaluationRunner",
    "build_interview_prompt",
    "evaluate_interview_answer",
    "generate_interview_answer",
    "generate_interview_answer_streaming",
    "judge_agentic_case",
    "list_baseline_library_sources",
    "load_agentic_benchmark_case",
    "load_builtin_agentic_benchmark_cases",
    "load_builtin_tool_calling_matrix_cases",
    "load_llm_test_index",
    "load_tool_calling_matrix_case",
    "pull_baseline_library",
    "reconcile_llm_test_index",
    "reset_llm_test_index",
    "run_agentic_benchmark_suite",
    "run_connectivity_suite",
    "run_connectivity_suite_sync",
    "run_context_benchmark_suite",
    "run_readiness_tests",
    "run_readiness_tests_streaming",
    "run_strategy_benchmark_suite",
    "run_tool_calling_matrix_suite",
    "update_index_with_report",
    "validate_director_safe_scope",
    "validate_no_hallucinated_paths",
    "validate_pm_plan_json",
    "validate_qa_json",
    "validate_qa_passfail",
]

# ------------------------------------------------------------------
# Conversion helpers for agentic -> unified benchmark format
# ------------------------------------------------------------------


def _convert_agentic_case_to_unified(case: AgenticBenchmarkCase) -> UnifiedBenchmarkCase:
    """Convert an AgenticBenchmarkCase to UnifiedBenchmarkCase.

    This enables running legacy agentic benchmark cases through the
    unified runner infrastructure.

    Args:
        case: The legacy AgenticBenchmarkCase to convert.

    Returns:
        UnifiedBenchmarkCase ready for unified runner execution.
    """
    # Convert legacy judge config
    legacy_judge = (
        case.judge if isinstance(case.judge, AgenticJudgeConfig) else AgenticJudgeConfig.from_dict(case.judge)
    )

    # Convert tool argument rules
    def _convert_arg_rule(rule: LegacyToolArgumentRule | dict[str, Any]) -> ToolArgumentRule:
        if isinstance(rule, LegacyToolArgumentRule):
            return ToolArgumentRule(
                fragment=rule.fragment,
                tools=rule.tools,
                description=rule.description,
            )
        return ToolArgumentRule(
            fragment=str(rule.get("fragment", "")),
            tools=tuple(rule.get("tools") or ()),
            description=str(rule.get("description") or ""),
        )

    judge_config = JudgeConfig(
        score_threshold=legacy_judge.score_threshold,
        required_tools=legacy_judge.required_tools,
        forbidden_tools=legacy_judge.forbidden_tools,
        required_tool_arguments=tuple(_convert_arg_rule(r) for r in legacy_judge.required_tool_arguments),
        forbidden_tool_arguments=tuple(_convert_arg_rule(r) for r in legacy_judge.forbidden_tool_arguments),
        min_tool_calls=legacy_judge.min_tool_calls,
        max_tool_calls=legacy_judge.max_tool_calls,
        required_output_substrings=legacy_judge.required_output_substrings,
        forbidden_output_substrings=legacy_judge.forbidden_output_substrings,
        validators=legacy_judge.validators,
        mode="agentic",
    )

    return UnifiedBenchmarkCase(
        case_id=case.case_id,
        role=case.role,
        title=case.title,
        prompt=case.prompt,
        description=case.description,
        workspace_fixture=case.workspace_fixture,
        history=case.history,
        context=dict(case.context) if case.context else {},
        metadata=dict(case.metadata) if case.metadata else {},
        tags=case.tags,
        judge=judge_config,
    )


def _convert_result_to_legacy(
    benchmark_result: BenchmarkSuiteResult,
    legacy_cases: list[dict[str, Any]],
    *,
    workspace: str,
) -> dict[str, Any]:
    """Convert UnifiedBenchmarkSuiteResult to legacy return format.

    Preserves backward compatibility for callers expecting the old
    run_agentic_benchmark_suite return structure.

    Args:
        benchmark_result: The unified benchmark suite result.
        legacy_cases: Pre-computed legacy case list for artifact.
        workspace: The workspace path (required for correct artifact_path calculation).

    Returns:
        Dict matching the legacy return format:
        - ok: bool indicating all passed
        - details: dict with cases, artifact_path, report, etc.
    """
    artifact_path = resolve_runtime_path(
        workspace,
        f"runtime/llm_evaluations/{benchmark_result.run_id}/AGENTIC_BENCHMARK_REPORT.json",
    )

    # Build legacy verdict details from unified results
    verdicts_map: dict[str, dict[str, Any]] = {}
    for r in benchmark_result.results:
        verdicts_map[r.case_id] = {
            "passed": r.passed,
            "score": r.score,
            "verdict": r.verdict.to_dict() if r.verdict else {},
            "error": r.error,
            "duration_ms": r.duration_ms,
        }

    # Match legacy_cases order with unified results
    matched_legacy: list[dict[str, Any]] = []
    for lc in legacy_cases:
        case_id = lc.get("id", "")
        unified_verdict = verdicts_map.get(case_id, {})
        matched_legacy.append(
            {
                "id": case_id,
                "passed": unified_verdict.get("passed", False),
                "output": "",
                "score": unified_verdict.get("score", 0.0),
                "error": unified_verdict.get("error", ""),
                "latency_ms": unified_verdict.get("duration_ms", 0),
            }
        )

    artifact = {
        "schema_version": 1,
        "suite": "agentic_benchmark",
        "test_run_id": benchmark_result.run_id,
        "timestamp": benchmark_result.timestamp,
        "summary": {
            "total_cases": benchmark_result.total_cases,
            "passed_cases": benchmark_result.passed_cases,
            "failed_cases": benchmark_result.failed_cases,
            "average_score": benchmark_result.average_score,
        },
        "final": {
            "ready": benchmark_result.passed_cases == benchmark_result.total_cases,
            "grade": "PASS" if benchmark_result.passed_cases == benchmark_result.total_cases else "FAIL",
            "next_action": "proceed"
            if benchmark_result.passed_cases == benchmark_result.total_cases
            else "fix_failures",
        },
        "cases": matched_legacy,
    }

    return {
        "ok": benchmark_result.passed_cases == benchmark_result.total_cases,
        "details": {
            "cases": matched_legacy,
            "artifact_path": artifact_path,
            "report": artifact,
            "total_cases": benchmark_result.total_cases,
            "passed_cases": benchmark_result.passed_cases,
            "failed_cases": benchmark_result.failed_cases,
            "average_score": benchmark_result.average_score,
        },
    }


# ------------------------------------------------------------------
# Unified runner entry point for agentic benchmarks
# ------------------------------------------------------------------


async def run_agentic_benchmark_suite(
    provider_cfg: dict[str, Any],
    model: str | None,
    role: str,
    *,
    workspace: str,
    settings: Any = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic role benchmark cases via unified_runner.

    This function has been migrated to use UnifiedBenchmarkRunner.
    It loads agentic benchmark cases, converts them to unified format,
    runs them through the unified runner, and returns results in the
    legacy dict format for backward compatibility.

    Args:
        provider_cfg: Provider configuration dict (currently unused,
            kept for API compatibility).
        model: Model name to use for the role sessions.
        role: Role identifier (e.g., "director", "pm", "qa") or "all"
            to run cases for all roles.
        workspace: Path to the workspace root directory.
        settings: Optional settings object (currently unused).
        context: Optional context mapping. May contain:
            - provider_id: Override provider identifier
            - benchmark_case_ids: Filter to specific case IDs
            - progress_callback: Callable for progress events
        options: Optional options mapping. May contain:
            - provider_id: Override provider identifier
            - benchmark_case_ids: Filter to specific case IDs

    Returns:
        A dict containing:
        - ok (bool): True if all cases passed, False otherwise
        - details (dict): Detailed results including:
            - cases: List of legacy case results
            - artifact_path: Path to the JSON report
            - report: Full structured report
            - total_cases, passed_cases, failed_cases, average_score
    """
    del provider_cfg, settings  # unused but kept for API compatibility

    # Load legacy agentic cases using existing loader
    context_payload = dict(context or {})
    options_payload = dict(options or {})
    requested_role = str(role or "all").strip().lower() or "all"
    case_ids = options_payload.get("benchmark_case_ids") or context_payload.get("benchmark_case_ids")
    if case_ids and isinstance(case_ids, str):
        case_ids = [case_ids]

    # Load cases via existing loader
    cases = load_builtin_agentic_benchmark_cases(role=requested_role, case_ids=case_ids or None)
    if not cases:
        return {
            "ok": False,
            "error": f"no benchmark cases matched role={requested_role!r}",
            "details": {"cases": []},
        }

    # Build legacy_cases list (pre-computed for artifact)
    legacy_cases: list[dict[str, Any]] = [
        {
            "id": c.case_id,
            "passed": False,
            "output": "",
            "score": 0.0,
            "error": "",
            "latency_ms": 0,
        }
        for c in cases
    ]

    # Convert to unified format
    unified_cases = [_convert_agentic_case_to_unified(case) for case in cases]

    # Run via unified runner
    runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
    benchmark_result = await runner.run_suite(
        cases=unified_cases,
        workspace=workspace,
        mode="agentic",
    )

    # Convert back to legacy format
    return _convert_result_to_legacy(benchmark_result, legacy_cases, workspace=workspace)


# ------------------------------------------------------------------
# Context and Strategy benchmark entry points
# ------------------------------------------------------------------


async def run_context_benchmark_suite(
    provider_cfg: dict[str, Any],
    model: str | None,
    *,
    workspace: str,
    settings: Any = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Context Benchmark suite.

    Uses unified_runner in context mode to evaluate context selection能力.

    Args:
        provider_cfg: Provider configuration dict (unused, kept for API compat).
        model: Model name (unused, kept for API compat).
        workspace: Path to the workspace root directory.
        settings: Optional settings object.
        context: Optional context mapping.
        options: Optional options mapping. May contain:
            - case_ids: Filter to specific case IDs
            - role: Filter by role
            - run_id: Custom run ID

    Returns:
        A dict with results, summary, run_id, timestamp, and mode.
    """
    del provider_cfg, model, settings  # unused but kept for API compatibility

    context_payload = dict(context or {})
    options_payload = dict(options or {})

    case_ids = options_payload.get("case_ids") or context_payload.get("case_ids")
    if case_ids and isinstance(case_ids, str):
        case_ids = [case_ids]

    role = options_payload.get("role") or context_payload.get("role")

    cases = _load_context_benchmark_cases(case_ids=case_ids, role=role)
    if not cases:
        return {
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "average_score": 0.0, "pass_rate": 0.0},
            "run_id": options_payload.get("run_id") or "",
            "timestamp": "",
            "mode": "context",
        }

    unified_cases = [_convert_agentic_case_to_unified(case) for case in cases]

    runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
    result = await runner.run_suite(
        cases=unified_cases,
        workspace=workspace,
        run_id=options_payload.get("run_id"),
        mode="context",
    )

    return _convert_suite_result_to_legacy_format(result)


async def run_strategy_benchmark_suite(
    provider_cfg: dict[str, Any],
    model: str | None,
    *,
    workspace: str,
    settings: Any = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run Strategy Benchmark suite.

    Uses unified_runner in strategy mode to evaluate strategy planning能力.

    Args:
        provider_cfg: Provider configuration dict (unused, kept for API compat).
        model: Model name (unused, kept for API compat).
        workspace: Path to the workspace root directory.
        settings: Optional settings object.
        context: Optional context mapping.
        options: Optional options mapping. May contain:
            - case_ids: Filter to specific case IDs
            - role: Filter by role
            - run_id: Custom run ID

    Returns:
        A dict with results, summary, run_id, timestamp, and mode.
    """
    del provider_cfg, model, settings  # unused but kept for API compatibility

    context_payload = dict(context or {})
    options_payload = dict(options or {})

    case_ids = options_payload.get("case_ids") or context_payload.get("case_ids")
    if case_ids and isinstance(case_ids, str):
        case_ids = [case_ids]

    role = options_payload.get("role") or context_payload.get("role")

    cases = _load_strategy_benchmark_cases(case_ids=case_ids, role=role)
    if not cases:
        return {
            "results": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "average_score": 0.0, "pass_rate": 0.0},
            "run_id": options_payload.get("run_id") or "",
            "timestamp": "",
            "mode": "strategy",
        }

    unified_cases = [_convert_agentic_case_to_unified(case) for case in cases]

    runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
    result = await runner.run_suite(
        cases=unified_cases,
        workspace=workspace,
        run_id=options_payload.get("run_id"),
        mode="strategy",
    )

    return _convert_suite_result_to_legacy_format(result)


def _load_context_benchmark_cases(
    case_ids: list[str] | None = None,
    role: str | None = None,
) -> list:
    """Load context benchmark cases.

    Currently uses agentic cases as placeholder; Package 8 will create
    proper context benchmark cases.
    """
    # TODO(包8): Use proper context benchmark cases
    return load_builtin_agentic_benchmark_cases(role=role, case_ids=case_ids)


def _load_strategy_benchmark_cases(
    case_ids: list[str] | None = None,
    role: str | None = None,
) -> list:
    """Load strategy benchmark cases.

    Currently uses agentic cases as placeholder; Package 8 will create
    proper strategy benchmark cases.
    """
    # TODO(包8): Use proper strategy benchmark cases
    return load_builtin_agentic_benchmark_cases(role=role, case_ids=case_ids)


def _convert_suite_result_to_legacy_format(result: BenchmarkSuiteResult) -> dict[str, Any]:
    """Convert BenchmarkSuiteResult to legacy format dict.

    Args:
        result: The unified benchmark suite result.

    Returns:
        Dict with results list, summary, run_id, timestamp, and mode.
    """
    return {
        "results": [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "score": r.score,
                "duration_ms": r.duration_ms,
                "error": r.error,
            }
            for r in result.results
        ],
        "summary": {
            "total": result.total_cases,
            "passed": result.passed_cases,
            "failed": result.failed_cases,
            "average_score": result.average_score,
            "pass_rate": result.pass_rate,
        },
        "run_id": result.run_id,
        "timestamp": result.timestamp,
        "mode": result.mode,
    }
