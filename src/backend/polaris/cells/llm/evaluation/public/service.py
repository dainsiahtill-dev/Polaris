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
    ToolArgumentRule as AgenticToolArgumentRule,
)
from polaris.cells.llm.evaluation.internal.context_projection_matrix import (
    run_context_projection_matrix_suite,
)
from polaris.cells.llm.evaluation.internal.index import (
    load_llm_test_index,
    load_llm_test_index_candidates,
    reconcile_llm_test_index,
    reset_llm_test_index,
    update_index_with_report,
)
from polaris.cells.llm.evaluation.internal.interview import (
    build_interview_prompt,
    evaluate_interview_answer,
    generate_interview_answer,
    generate_interview_answer_streaming,
    save_interview_report,
)
from polaris.cells.llm.evaluation.internal.judge.orchestrator import judge_agentic_case
from polaris.cells.llm.evaluation.internal.projection_adaptive_matrix import (
    run_projection_adaptive_matrix_suite,
)
from polaris.cells.llm.evaluation.internal.readiness_freshness import (
    DEFAULT_READINESS_MAX_AGE_SECONDS,
    parse_readiness_timestamp,
    readiness_freshness_issue,
    readiness_max_age_seconds,
)
from polaris.cells.llm.evaluation.internal.readiness_tests import (
    run_readiness_tests,
    run_readiness_tests_streaming,
)
from polaris.cells.llm.evaluation.internal.runner import EvaluationRunner
from polaris.cells.llm.evaluation.internal.scout_matrix import run_scout_matrix_suite
from polaris.cells.llm.evaluation.internal.speculation_matrix import (
    run_speculation_matrix_suite,
)
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
    "DEFAULT_READINESS_MAX_AGE_SECONDS",
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
    "load_llm_test_index_candidates",
    "load_tool_calling_matrix_case",
    "parse_readiness_timestamp",
    "pull_baseline_library",
    "readiness_freshness_issue",
    "readiness_max_age_seconds",
    "reconcile_llm_test_index",
    "reset_llm_test_index",
    "run_agentic_benchmark_suite",
    "run_connectivity_suite",
    "run_connectivity_suite_sync",
    "run_context_benchmark_suite",
    "run_context_projection_matrix_suite",
    "run_projection_adaptive_matrix_suite",
    "run_readiness_tests",
    "run_readiness_tests_streaming",
    "run_scout_matrix_suite",
    "run_speculation_matrix_suite",
    "run_strategy_benchmark_suite",
    "run_tool_calling_matrix_suite",
    "save_interview_report",
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

    Agentic benchmark fixtures keep their historical data shape, while
    execution now flows through the KernelOne unified runner contract.

    Args:
        case: The agentic benchmark case to convert.

    Returns:
        UnifiedBenchmarkCase ready for unified runner execution.
    """
    # Normalize persisted case payloads before handing them to KernelOne.
    agentic_judge = (
        case.judge if isinstance(case.judge, AgenticJudgeConfig) else AgenticJudgeConfig.from_dict(case.judge)
    )

    # Convert tool argument rules
    def _convert_arg_rule(rule: AgenticToolArgumentRule | dict[str, Any]) -> ToolArgumentRule:
        if isinstance(rule, AgenticToolArgumentRule):
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
        score_threshold=agentic_judge.score_threshold,
        required_tools=agentic_judge.required_tools,
        forbidden_tools=agentic_judge.forbidden_tools,
        required_tool_arguments=tuple(_convert_arg_rule(r) for r in agentic_judge.required_tool_arguments),
        forbidden_tool_arguments=tuple(_convert_arg_rule(r) for r in agentic_judge.forbidden_tool_arguments),
        min_tool_calls=agentic_judge.min_tool_calls,
        max_tool_calls=agentic_judge.max_tool_calls,
        required_output_substrings=agentic_judge.required_output_substrings,
        forbidden_output_substrings=agentic_judge.forbidden_output_substrings,
        validators=agentic_judge.validators,
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


def _convert_result_to_public_report(
    benchmark_result: BenchmarkSuiteResult,
    public_case_rows: list[dict[str, Any]],
    *,
    workspace: str,
) -> dict[str, Any]:
    """Convert a unified benchmark result into the stable public report.

    The public facade keeps the report shape consumed by CLI tools and
    historical artifacts, while the execution source of truth remains
    KernelOne's ``BenchmarkSuiteResult``.

    Args:
        benchmark_result: The unified benchmark suite result.
        public_case_rows: Pre-computed case row order for the public report.
        workspace: The workspace path (required for correct artifact_path calculation).

    Returns:
        Dict matching the stable public report contract:
        - ok: bool indicating all passed
        - details: dict with cases, artifact_path, report, etc.
    """
    artifact_path = resolve_runtime_path(
        workspace,
        f"runtime/llm_evaluations/{benchmark_result.run_id}/AGENTIC_BENCHMARK_REPORT.json",
    )

    # Build public verdict details from unified results.
    verdicts_map: dict[str, dict[str, Any]] = {}
    for r in benchmark_result.results:
        verdicts_map[r.case_id] = {
            "passed": r.passed,
            "score": r.score,
            "verdict": r.verdict.to_dict() if r.verdict else {},
            "error": r.error,
            "duration_ms": r.duration_ms,
        }

    # Keep the case order stable for existing CLI/report consumers.
    matched_case_rows: list[dict[str, Any]] = []
    for case_row in public_case_rows:
        case_id = case_row.get("id", "")
        unified_verdict = verdicts_map.get(case_id, {})
        matched_case_rows.append(
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
        "cases": matched_case_rows,
    }

    return {
        "ok": benchmark_result.passed_cases == benchmark_result.total_cases,
        "details": {
            "cases": matched_case_rows,
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
    runs them through the unified runner, and returns the stable public
    report contract used by CLI and artifact consumers.

    Args:
        provider_cfg: Provider configuration dict (currently unused,
            retained by the stable public signature).
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
            - cases: List of public case results
            - artifact_path: Path to the JSON report
            - report: Full structured report
            - total_cases, passed_cases, failed_cases, average_score
    """
    del provider_cfg, settings  # unused but kept for stable public signature

    context_payload = dict(context or {})
    options_payload = dict(options or {})
    requested_role = str(role or "all").strip().lower() or "all"
    case_ids = options_payload.get("benchmark_case_ids") or context_payload.get("benchmark_case_ids")
    if case_ids and isinstance(case_ids, str):
        case_ids = [case_ids]

    cases = load_builtin_agentic_benchmark_cases(role=requested_role, case_ids=case_ids or None)
    if not cases:
        return {
            "ok": False,
            "error": f"no benchmark cases matched role={requested_role!r}",
            "details": {"cases": []},
        }

    public_case_rows: list[dict[str, Any]] = [
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

    unified_cases = [_convert_agentic_case_to_unified(case) for case in cases]

    runner = UnifiedBenchmarkRunner(judge=UnifiedJudge())
    benchmark_result = await runner.run_suite(
        cases=unified_cases,
        workspace=workspace,
        mode="agentic",
    )

    return _convert_result_to_public_report(benchmark_result, public_case_rows, workspace=workspace)


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
        provider_cfg: Provider configuration dict (unused; retained by the public signature).
        model: Model name (unused; retained by the public signature).
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
    del provider_cfg, model, settings  # unused but retained by the public signature

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

    return _convert_suite_result_to_public_summary(result)


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
        provider_cfg: Provider configuration dict (unused; retained by the public signature).
        model: Model name (unused; retained by the public signature).
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
    del provider_cfg, model, settings  # unused but retained by the public signature

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

    return _convert_suite_result_to_public_summary(result)


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


def _convert_suite_result_to_public_summary(result: BenchmarkSuiteResult) -> dict[str, Any]:
    """Convert BenchmarkSuiteResult to the public summary dict.

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
