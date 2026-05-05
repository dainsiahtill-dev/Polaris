"""Deterministic role benchmark suite powered by roles.runtime streaming traces.

.. deprecated::
    This module is deprecated. Use ``polaris.kernelone.benchmark.unified_runner``
    and ``polaris.kernelone.benchmark.unified_judge`` for new development.
    The canonical benchmark framework is now in
    ``polaris/kernelone/benchmark/unified_runner.py`` (UnifiedBenchmarkRunner)
    and ``polaris/kernelone/benchmark/unified_judge.py`` (UnifiedJudge).

    This module is retained for backward compatibility with existing
    evaluation cell internals and will be removed after 2026-06-30.

This module provides the agentic benchmark evaluation system that runs deterministic
test cases against the roles.runtime streaming interface. Each benchmark case defines
expected tool usage patterns, output requirements, and validation rules that are
checked against observed agent behavior.

Architecture
------------
The benchmark system operates in three phases:

1. **Case Materialization**: Creates isolated workspace sandboxes from case fixtures
2. **Observation Collection**: Streams role sessions and captures tool calls, output, thinking
3. **Deterministic Judgment**: Compares observations against case-defined acceptance criteria

Example
-------
```python
from polaris.cells.llm.evaluation import run_agentic_benchmark_suite

result = await run_agentic_benchmark_suite(
    provider_cfg={},
    model="claude-3-5-sonnet",
    role="director",
    workspace="/path/to/workspace",
    options={"benchmark_case_ids": ["case_001"]},
)
print(result["ok"])  # True if all cases passed
```
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.kernelone.storage import resolve_runtime_path

from .benchmark_loader import (
    list_workspace_files,
    load_builtin_agentic_benchmark_cases,
    materialize_case_workspace,
)
from .benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeVerdict,
    JudgeCheck,
    ObservedBenchmarkRun,
    ToolCallObservation,
)
from .deterministic_judge import judge_agentic_case
from .path_validators import PathTraversalError, validate_base_workspace, validate_case_id, validate_run_id
from .utils import new_test_run_id, utc_now, write_json_atomic

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings


class RoleSessionStreamExecutor(Protocol):
    """Protocol defining the interface for benchmark executors.

    This protocol allows custom executor implementations to be injected via
    the context parameter of `run_agentic_benchmark_suite`.

    Example:
        class CustomExecutor:
            def stream_session(
                self,
                command: ExecuteRoleSessionCommandV1,
            ) -> AsyncIterator[Mapping[str, Any]]:
                # Custom streaming implementation
                ...
    """

    def stream_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncIterator[Mapping[str, Any]]:
        """Stream one role session."""


class RolesRuntimeStreamExecutor:
    """Default benchmark executor backed by roles.runtime public service.

    This executor delegates to the public `stream_role_session_command` function
    from the roles.runtime cell. Use this when no custom executor is needed.

    Example:
        executor = RolesRuntimeStreamExecutor()
        async for event in executor.stream_session(command):
            print(event)
    """

    def stream_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncIterator[Mapping[str, Any]]:
        from polaris.cells.roles.runtime.public.service import stream_role_session_command

        return stream_role_session_command(command)


def _emit_progress(
    context: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
) -> None:
    """Emit progress events to an optional callback.

    Args:
        context: Optional context mapping that may contain a progress_callback.
        payload: Event payload to send to the callback.
    """
    callback = dict(context or {}).get("progress_callback")
    if not callable(callback):
        return
    try:
        callback(dict(payload))
    except (RuntimeError, ValueError):
        import logging

        logger = logging.getLogger(__name__)
        logger.warning("Progress callback failed")
        return


def _sanitize_json(value: Any) -> Any:
    """Convert a value to a JSON-serializable form.

    Args:
        value: Any value to sanitize for JSON serialization.

    Returns:
        A JSON-serializable version of the input value.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json(item) for item in value]
    return str(value)


def _normalize_case_ids(value: Any) -> list[str]:
    """Normalize case ID input to a deduplicated list of strings.

    Args:
        value: Case IDs as a string, list, tuple, set, or other value.

    Returns:
        Deduplicated list of non-empty case ID strings.

    Examples:
        >>> _normalize_case_ids("case1")
        ["case1"]
        >>> _normalize_case_ids(["case1", "case2"])
        ["case1", "case2"]
        >>> _normalize_case_ids("case1,case2,case1")
        ["case1", "case2"]
    """
    if value is None:
        return []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _resolve_executor(context: Mapping[str, Any] | None) -> RoleSessionStreamExecutor:
    """Resolve the role session executor from context.

    Args:
        context: Optional context mapping that may contain role_session_executor.

    Returns:
        The injected executor if provided, otherwise the default
        RolesRuntimeStreamExecutor.

    Raises:
        TypeError: If the injected executor does not implement stream_session.
    """
    payload = dict(context or {})
    executor = payload.get("role_session_executor")
    if executor is None:
        return RolesRuntimeStreamExecutor()
    if hasattr(executor, "stream_session"):
        return executor  # type: ignore[return-value]
    raise TypeError("role_session_executor must provide a stream_session(command) method")


def _merge_prompt_appendices(*parts: str) -> str:
    """Merge prompt appendices while preserving order and removing duplicates."""
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = str(part or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return "\n\n".join(normalized)


def _build_benchmark_prompt_appendix(
    *,
    case: AgenticBenchmarkCase,
    sandbox_workspace: str,
) -> str:
    """Build an execution-time appendix from the deterministic judge contract."""
    judge = case.judge
    validators = {str(item or "").strip().lower() for item in list(judge.validators or []) if str(item or "").strip()}
    lines = [
        "Benchmark acceptance contract:",
        f"- Case: {case.case_id} ({case.title})",
        f"- Workspace: {sandbox_workspace}",
        "- Use real runtime tool calls for local inspection. Do not simulate tool tags or textual wrappers.",
        "- Base every claim on files you actually searched or read in this workspace.",
        "- When a tool is required, actually call it through the runtime. Never print placeholder XML like `<parameter ...>`.",
        "- Do not repeat the final answer. Stop immediately after the numbered list or after the closing `}` of the single JSON object.",
    ]
    if "structured_steps" in validators:
        lines.append(
            "- Final answer must start directly with `1.` and remain a short numbered list. Do not add a heading or prose before step 1."
        )
    if "pm_plan_json" in validators:
        lines.append(
            "- Final answer must be exactly one JSON object with keys `goal`, `backlog`, and `timeline`. Do not use markdown fences."
        )
    if "qa_passfail_json" in validators:
        lines.append(
            "- Final answer must be exactly one JSON object with keys `passed` and `findings`. Do not use markdown fences."
        )
    if judge.required_tools:
        lines.append("- Required tools: " + ", ".join(str(item) for item in judge.required_tools) + ".")
    for rule in judge.required_tool_arguments:
        tool_scope = ", ".join(str(item) for item in rule.tools) or "relevant tools"
        line = f"- Tool evidence must include `{rule.fragment}` via {tool_scope}."
        if rule.description:
            line += f" {rule.description}."
        lines.append(line)
        for tool_name in list(rule.tools or []):
            argument_key = _preferred_argument_key_for_tool(tool_name)
            if argument_key:
                lines.append(f"- If you call `{tool_name}`, put `{rule.fragment}` literally inside `{argument_key}`.")
    if judge.required_output_substrings:
        lines.append(
            "- Final answer must include: " + ", ".join(f"`{item}`" for item in judge.required_output_substrings) + "."
        )
        lines.append("- Keep the required output literals unchanged; do not paraphrase or rename them.")
    if judge.forbidden_output_substrings:
        lines.append(
            "- Final answer must not include: "
            + ", ".join(f"`{item}`" for item in judge.forbidden_output_substrings)
            + "."
        )
    if judge.validators:
        lines.append("- Additional validators: " + ", ".join(str(item) for item in judge.validators) + ".")
    lines.append("- Collect the required evidence first, then answer the user directly.")
    return "\n".join(lines)


def _preferred_argument_key_for_tool(tool_name: str) -> str:
    token = str(tool_name or "").strip().lower()
    mapping = {
        "read_file": "file",
        "search_code": "query",
        "grep": "query",
        "ripgrep": "query",
        "glob": "pattern",
        "list_directory": "path",
        "file_exists": "path",
    }
    return mapping.get(token, "")


def _build_benchmark_repair_appendix(
    *,
    case: AgenticBenchmarkCase,
    verdict: AgenticJudgeVerdict,
) -> str:
    lines = [
        "Benchmark repair contract:",
        f"- Previous attempt for `{case.case_id}` failed. Fix every failed check below in this retry.",
        "- Use real runtime tool calls only; do not print tool names, XML parameters, or textual wrappers in the answer.",
        "- Do not repeat the final answer. Stop immediately after the final numbered line or the closing `}`.",
    ]
    seen_lines: set[str] = set()
    for check in list(verdict.checks or []):
        if bool(check.passed):
            continue
        for line in _repair_lines_for_check(check=check, case=case):
            token = str(line or "").strip()
            if not token or token in seen_lines:
                continue
            seen_lines.add(token)
            lines.append(token)
    return "\n".join(lines)


def _repair_lines_for_check(
    *,
    check: JudgeCheck,
    case: AgenticBenchmarkCase,
) -> list[str]:
    code = str(check.code or "").strip()
    if code.startswith("required_tool:"):
        tool_name = code.split("required_tool:", 1)[1].strip()
        return [f"- You must actually call `{tool_name}` before answering."]
    if code == "min_tool_calls":
        return [f"- Use at least {int(case.judge.min_tool_calls or 0)} real tool calls before the final answer."]
    if code.startswith("required_tool_argument:"):
        evidence = dict(check.evidence or {})
        fragment = str(evidence.get("fragment") or "").strip()
        tools = [str(item or "").strip() for item in list(evidence.get("tools") or []) if str(item or "").strip()]
        lines: list[str] = []
        if fragment and tools:
            lines.append(
                "- Call "
                + ", ".join(f"`{tool}`" for tool in tools)
                + f" with structured arguments that literally contain `{fragment}`."
            )
            for tool_name in tools:
                argument_key = _preferred_argument_key_for_tool(tool_name)
                if argument_key and fragment:
                    lines.append(f"- For `{tool_name}`, put `{fragment}` inside `{argument_key}`.")
        description = str(evidence.get("description") or "").strip()
        if description:
            lines.append(f"- Evidence requirement: {description}.")
        return lines
    if code.startswith("required_output:"):
        token = code.split("required_output:", 1)[1].strip()
        if token:
            return [f"- Final answer must literally include `{token}`."]
    if code == "validator:structured_steps":
        return [
            "- Final answer must start with `1.` and stay as a short numbered list only.",
        ]
    if code == "validator:pm_plan_json":
        return [
            "- Return exactly one JSON object with keys `goal`, `backlog`, and `timeline`.",
            "- Stop immediately after the closing `}` of that JSON object.",
        ]
    if code == "validator:qa_passfail_json":
        return [
            "- Return exactly one JSON object with keys `passed` and `findings`.",
            "- Stop immediately after the closing `}` of that JSON object.",
        ]
    if code == "textual_tool_protocol_without_trace":
        return [
            "- Do not print tool syntax or wrapper text. Use the runtime tool interface instead.",
        ]
    return [f"- Fix failed check: {check.message}"]


async def _collect_case_observation(
    *,
    case: AgenticBenchmarkCase,
    sandbox_workspace: str,
    provider_id: str,
    model: str,
    executor: RoleSessionStreamExecutor,
    run_id: str,
    prompt_appendix: str,
    attempt_index: int = 0,
) -> tuple[ObservedBenchmarkRun, list[dict[str, Any]]]:
    metadata = dict(case.metadata)
    metadata["prompt_appendix"] = prompt_appendix
    command = ExecuteRoleSessionCommandV1(
        role=case.role,
        session_id=f"{run_id}-{case.case_id}-attempt-{attempt_index}",
        workspace=sandbox_workspace,
        user_message=case.prompt,
        history=case.history,
        # Keep runtime context minimal to avoid deprecated context_override-only path.
        context=dict(case.context),
        metadata={
            **metadata,
            "agentic_benchmark": True,
            "benchmark_case_id": case.case_id,
            "benchmark_run_id": run_id,
            "benchmark_provider_id": provider_id,
            "benchmark_model": model,
            "benchmark_attempt_index": attempt_index,
            "provider_id": provider_id,
            "model": model,
            "validate_output": True,
            "max_retries": 1,
        },
        stream=True,
    )

    output_chunks: list[str] = []
    thinking_chunks: list[str] = []
    tool_calls: list[ToolCallObservation] = []
    fingerprint: dict[str, Any] = {}
    captured_events: list[dict[str, Any]] = []
    error_message = ""
    start = time.perf_counter()

    async for event in executor.stream_session(command):
        safe_event = _sanitize_json(dict(event))
        if isinstance(safe_event, dict):
            captured_events.append(safe_event)
        event_type = str(event.get("type") or "")
        if event_type == "fingerprint":
            fingerprint = {key: _sanitize_json(value) for key, value in dict(event).items() if key != "type"}
        elif event_type == "content_chunk":
            output_chunks.append(str(event.get("content") or ""))
        elif event_type == "thinking_chunk":
            thinking_chunks.append(str(event.get("content") or ""))
        elif event_type == "tool_call":
            tool_calls.append(
                ToolCallObservation(
                    tool=str(event.get("tool") or ""),
                    args=dict(event.get("args") or {}),
                    event_index=len(captured_events) - 1,
                )
            )
        elif event_type == "complete":
            maybe_result = event.get("result")
            if maybe_result is not None:
                output_chunks = [str(getattr(maybe_result, "content", "") or "".join(output_chunks))]
                thinking_value = str(getattr(maybe_result, "thinking", "") or "")
                if thinking_value:
                    thinking_chunks = [thinking_value]
        elif event_type == "error":
            error_message = str(event.get("error") or "")

    duration_ms = int((time.perf_counter() - start) * 1000)
    observed = ObservedBenchmarkRun(
        case_id=case.case_id,
        role=case.role,
        workspace=sandbox_workspace,
        output="".join(output_chunks).strip(),
        thinking="".join(thinking_chunks).strip(),
        tool_calls=tuple(tool_calls),
        error=error_message,
        duration_ms=duration_ms,
        event_count=len(captured_events),
        fingerprint=fingerprint,
    )
    return observed, captured_events


def _artifact_path(workspace: str, run_id: str) -> Path:
    """Compute the artifact file path for a benchmark report.

    Args:
        workspace: The workspace root path.
        run_id: Unique identifier for this test run.

    Returns:
        Path to the AGENTIC_BENCHMARK_REPORT.json artifact file.
    """
    return Path(resolve_runtime_path(workspace, f"runtime/llm_evaluations/{run_id}/AGENTIC_BENCHMARK_REPORT.json"))


async def run_agentic_benchmark_suite(
    provider_cfg: dict[str, Any],
    model: str | None,
    role: str,
    *,
    workspace: str,
    settings: Settings | None = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic role benchmark cases against roles.runtime.

    Executes a suite of benchmark cases for a given role, collecting
    observations from streaming role sessions and judging them against
    deterministic acceptance criteria.

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
            - role_session_executor: Custom executor implementing stream_session
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

    Example:
        result = await run_agentic_benchmark_suite(
            provider_cfg={},
            model="claude-3-5-sonnet-20241022",
            role="director",
            workspace="/workspace",
        )
        if result["ok"]:
            print("All benchmark cases passed")
        else:
            print(f"Failed: {result["details"]["failed_cases"]}")

    Progress Events:
        The progress_callback (if provided in context) will receive events:
        - suite_started: When the suite begins
        - case_started: Before each case execution
        - case_completed: After each case with verdict
        - suite_completed: When all cases finish
    """

    del provider_cfg, settings

    # Security: Validate workspace path to prevent path traversal
    try:
        workspace_path = validate_base_workspace(workspace, must_exist=False, must_be_dir=False)
    except PathTraversalError as exc:
        return {
            "ok": False,
            "error": f"Invalid workspace path: {exc}",
            "details": {"cases": []},
        }

    context_payload = dict(context or {})
    options_payload = dict(options or {})
    provider_id = (
        str(
            context_payload.get("provider_id")
            or context_payload.get("benchmark_provider_id")
            or options_payload.get("provider_id")
            or "runtime_binding"
        ).strip()
        or "runtime_binding"
    )
    resolved_model = str(model or "").strip() or "runtime_binding"
    requested_role = str(role or "all").strip().lower() or "all"
    case_ids = _normalize_case_ids(
        options_payload.get("benchmark_case_ids") or context_payload.get("benchmark_case_ids")
    )
    repair_attempts = max(
        0,
        int(options_payload.get("benchmark_repair_attempts") or context_payload.get("benchmark_repair_attempts") or 1),
    )
    max_failed = max(
        0,
        int(options_payload.get("max_failed") or context_payload.get("max_failed") or 0),
    )
    cases = load_builtin_agentic_benchmark_cases(role=requested_role, case_ids=case_ids)
    if not cases:
        return {
            "ok": False,
            "error": f"no benchmark cases matched role={requested_role!r}",
            "details": {"cases": []},
        }

    run_id = new_test_run_id()
    executor = _resolve_executor(context_payload)
    case_payloads: list[dict[str, Any]] = []
    legacy_cases: list[dict[str, Any]] = []
    total_failed = 0
    _emit_progress(
        context_payload,
        {
            "type": "suite_started",
            "suite": "agentic_benchmark",
            "run_id": run_id,
            "role": requested_role,
            "total_cases": len(cases),
        },
    )

    for index, case in enumerate(cases, start=1):
        _emit_progress(
            context_payload,
            {
                "type": "case_started",
                "suite": "agentic_benchmark",
                "run_id": run_id,
                "index": index,
                "total_cases": len(cases),
                "case_id": case.case_id,
                "role": case.role,
                "title": case.title,
            },
        )

        # Security: Validate run_id and case_id to prevent path traversal
        try:
            safe_run_id = validate_run_id(run_id)
            validate_case_id(case.case_id)  # Validate but use case.case_id (already immutable)
        except PathTraversalError as exc:
            _emit_progress(
                context_payload,
                {
                    "type": "case_completed",
                    "suite": "agentic_benchmark",
                    "run_id": run_id,
                    "index": index,
                    "total_cases": len(cases),
                    "case_id": case.case_id,
                    "error": f"Path traversal detected: {exc}",
                },
            )
            legacy_cases.append(
                {
                    "id": case.case_id,
                    "passed": False,
                    "output": "",
                    "score": 0.0,
                    "error": f"Path traversal detected: {exc}",
                    "latency_ms": 0,
                }
            )
            continue

        sandbox_workspace = materialize_case_workspace(
            base_workspace=str(workspace_path),
            run_id=safe_run_id,
            case=case,
        )
        base_appendix = _merge_prompt_appendices(
            str(case.metadata.get("prompt_appendix") or ""),
            _build_benchmark_prompt_appendix(
                case=case,
                sandbox_workspace=sandbox_workspace,
            ),
        )
        prompt_appendix = base_appendix
        observed: ObservedBenchmarkRun | None = None
        raw_events: list[dict[str, Any]] = []
        workspace_files: list[str] = []
        verdict: AgenticJudgeVerdict | None = None
        for attempt_index in range(repair_attempts + 1):
            observed, raw_events = await _collect_case_observation(
                case=case,
                sandbox_workspace=sandbox_workspace,
                provider_id=provider_id,
                model=resolved_model,
                executor=executor,
                run_id=run_id,
                prompt_appendix=prompt_appendix,
                attempt_index=attempt_index,
            )
            workspace_files = list_workspace_files(sandbox_workspace)
            verdict = judge_agentic_case(case, observed, workspace_files=workspace_files)
            if verdict.passed or attempt_index >= repair_attempts:
                break
            prompt_appendix = _merge_prompt_appendices(
                base_appendix,
                _build_benchmark_repair_appendix(case=case, verdict=verdict),
            )
        if observed is None or verdict is None:
            raise RuntimeError(f"benchmark case `{case.case_id}` did not produce an observation")
        case_payloads.append(
            {
                "case": case.to_dict(),
                "sandbox_workspace": sandbox_workspace,
                "workspace_files": workspace_files,
                "observed": observed.to_dict(),
                "judge": verdict.to_dict(),
                "raw_events": raw_events,
            }
        )
        legacy_cases.append(
            {
                "id": case.case_id,
                "passed": verdict.passed,
                "output": observed.output,
                "score": verdict.score,
                "error": "" if verdict.passed else verdict.summary,
                "latency_ms": observed.duration_ms,
            }
        )
        _emit_progress(
            context_payload,
            {
                "type": "case_completed",
                "suite": "agentic_benchmark",
                "run_id": run_id,
                "index": index,
                "total_cases": len(cases),
                "case_id": case.case_id,
                "role": case.role,
                "title": case.title,
                "passed": verdict.passed,
                "score": verdict.score,
                "duration_ms": observed.duration_ms,
                "tool_call_count": len(observed.tool_calls),
                "sandbox_workspace": sandbox_workspace,
            },
        )

        # Early termination: stop if max_failed threshold is reached
        if max_failed > 0 and not verdict.passed:
            total_failed += 1
            if total_failed >= max_failed:
                # Emit early termination event
                _emit_progress(
                    context_payload,
                    {
                        "type": "suite_completed",
                        "suite": "agentic_benchmark",
                        "run_id": run_id,
                        "role": requested_role,
                        "total_cases": len(case_payloads),
                        "passed_cases": sum(1 for p in case_payloads if bool(p["judge"]["passed"])),
                        "failed_cases": total_failed,
                        "average_score": 0.0,
                        "stopped_early": True,
                        "reason": f"max_failed={max_failed} threshold reached",
                        "artifact_path": "",
                    },
                )
                break

    total_cases = len(case_payloads)
    passed_cases = sum(1 for item in case_payloads if bool(item["judge"]["passed"]))
    average_score = sum(float(item["judge"]["score"]) for item in case_payloads) / total_cases if total_cases else 0.0
    overall_ok = passed_cases == total_cases and total_cases > 0

    artifact = {
        "schema_version": 1,
        "suite": "agentic_benchmark",
        "test_run_id": run_id,
        "timestamp": utc_now(),
        "target": {
            "role": requested_role,
            "provider_id": provider_id,
            "model": resolved_model,
        },
        "summary": {
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
        },
        "final": {
            "ready": overall_ok,
            "grade": "PASS" if overall_ok else "FAIL",
            "next_action": "proceed" if overall_ok else "fix_failures",
        },
        "cases": case_payloads,
    }

    artifact_path = _artifact_path(workspace, run_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(str(artifact_path), artifact)
    _emit_progress(
        context_payload,
        {
            "type": "suite_completed",
            "suite": "agentic_benchmark",
            "run_id": run_id,
            "role": requested_role,
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
            "artifact_path": str(artifact_path),
        },
    )

    return {
        "ok": overall_ok,
        "details": {
            "cases": legacy_cases,
            "artifact_path": str(artifact_path),
            "report": artifact,
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
        },
    }
