"""Executors, async observation collectors, and the top-level matrix runner.

Holds the concrete role-session executors and resolver, progress emission, the
stream/non-stream observation collectors, the report artifact path helper, and
:func:`run_tool_calling_matrix_suite` which orchestrates the full run.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    RoleExecutionResultV1,
)
from polaris.kernelone.storage import resolve_runtime_path

from ..utils import new_test_run_id, utc_now, write_json_atomic
from ._contracts import (
    MatrixObservation,
    RoleSessionMatrixExecutor,
    ToolCallingMatrixCase,
    _mapping_dict,
    _non_empty,
    _normalize_case_ids,
    _sanitize_json,
    _to_float,
    _to_int,
)
from ._judge import _judge_case
from ._loading import (
    load_builtin_tool_calling_matrix_cases,
    materialize_case_workspace,
)
from ._prompt_contract import (
    _compose_case_prompt,
    _compose_stream_retry_prompt_for_under_calls,
    _event_value,
    _normalize_tool_calls,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings


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
    except (RuntimeError, ValueError) as e:
        logger.warning("Callback failed for payload: %s", e)
        return


class RolesRuntimeMatrixExecutor:
    """Default matrix executor backed by roles.runtime public service.

    This executor delegates to the public streaming and non-streaming
    functions from the roles.runtime cell.

    Example:
        executor = RolesRuntimeMatrixExecutor()

        # Streaming mode
        async for event in executor.stream_session(command):
            print(event)

        # Non-streaming mode
        result = await executor.run_session(command)
    """

    def stream_session(self, command: ExecuteRoleSessionCommandV1) -> AsyncIterator[Mapping[str, Any]]:
        """Stream role session events via roles.runtime.

        Args:
            command: The role session command to execute.

        Yields:
            Event dictionaries from the roles.runtime streaming interface.
        """
        from polaris.cells.roles.runtime.public.service import stream_role_session_command

        return stream_role_session_command(command)

    async def run_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1:
        from polaris.cells.roles.runtime.public.service import execute_role_session_command

        return await execute_role_session_command(command)


class _CompositeMatrixExecutor:
    def __init__(
        self,
        stream_executor: Any,
        run_executor: Any,
    ) -> None:
        self._stream_executor = stream_executor
        self._run_executor = run_executor

    def stream_session(self, command: ExecuteRoleSessionCommandV1) -> AsyncIterator[Mapping[str, Any]]:
        return self._stream_executor(command)

    async def run_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1 | Mapping[str, Any]:
        result = self._run_executor(command)
        if hasattr(result, "__await__"):
            return await result
        return result


def _resolve_executor(context: Mapping[str, Any] | None) -> RoleSessionMatrixExecutor:
    """Resolve the matrix executor from context.

    Args:
        context: Optional context mapping that may contain role_session_executor.

    Returns:
        The injected executor if provided, otherwise the default
        RolesRuntimeMatrixExecutor.

    Raises:
        TypeError: If the injected executor does not implement required methods.
    """
    from polaris.cells.roles.runtime.public.service import execute_role_session_command

    payload = dict(context or {})
    injected = payload.get("role_session_executor")
    if injected is None:
        return RolesRuntimeMatrixExecutor()
    if hasattr(injected, "stream_session") and hasattr(injected, "run_session"):
        return injected  # type: ignore[return-value]
    if hasattr(injected, "stream_session"):
        return _CompositeMatrixExecutor(
            stream_executor=injected.stream_session,
            run_executor=execute_role_session_command,
        )
    raise TypeError("role_session_executor must provide stream_session(command)")


async def _collect_stream_observation(
    *,
    case: ToolCallingMatrixCase,
    sandbox_workspace: str,
    benchmark_root: str,  # benchmark 根目录(不传给 agent)
    workspace: str,  # 执行命令时使用的 workspace(sandbox_workspace)
    provider_id: str,
    model: str,
    executor: RoleSessionMatrixExecutor,
    run_id: str,
    observable: bool = False,
) -> tuple[MatrixObservation, list[dict[str, Any]]]:
    # workspace 是实际执行测试的目录(包含 fixture 文件)
    # benchmark_root 用于 journal 写入到正确的 runtime_root
    mode_spec = _mapping_dict(_mapping_dict(case.judge).get("stream"))
    require_no_tool_calls = bool(mode_spec.get("require_no_tool_calls"))
    min_tool_calls = _to_int(mode_spec.get("min_tool_calls"), 0)
    ordered_tool_groups = list(mode_spec.get("ordered_tool_groups") or [])
    required_any_tools = list(mode_spec.get("required_any_tools") or [])

    base_prompt = _compose_case_prompt(case, mode="stream")
    user_message = base_prompt

    attempt = 0
    merged_events: list[dict[str, Any]] = []
    while True:
        command = ExecuteRoleSessionCommandV1(
            role=case.role,
            session_id=f"{run_id}-{case.case_id}-stream",
            workspace=workspace,
            user_message=user_message,
            run_id=run_id,
            history=case.history,
            context=dict(case.context),
            metadata={
                **dict(case.metadata),
                "tool_calling_matrix": True,
                "matrix_case_id": case.case_id,
                "matrix_run_id": run_id,
                "benchmark_require_no_tool_calls": require_no_tool_calls,
                "benchmark_min_tool_calls": min_tool_calls,
                "benchmark_ordered_tool_groups": ordered_tool_groups,
                "benchmark_required_any_tools": required_any_tools,
                "benchmark_retry_attempt": attempt,
                "provider_id": provider_id,
                "model": model,
                "validate_output": False,  # 跳过质量验证以获取原始 tool_calls
                # 工具循环安全配置 - 评测场景使用更高限制
                "max_total_tool_calls": 512,
                "max_stall_cycles": 20,
                # 探索工具策略配置 - 评测场景使用更高限制以避免误拦截
                "max_exploration_calls": 64,
                "max_calls_per_tool": 32,
                "cooldown_after_calls": 20,
            },
            stream=True,
        )

        output_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        captured_events: list[dict[str, Any]] = []
        error_message = ""
        cooldown_blocked_tools: list[str] = []
        tool_errors: list[tuple[str, str]] = []
        start = time.perf_counter()

        try:
            async for raw_event in executor.stream_session(command):
                event = _mapping_dict(raw_event)
                safe_event = _sanitize_json(event)
                if isinstance(safe_event, dict):
                    captured_events.append(safe_event)
                event_type = _non_empty(event.get("type"))
                if event_type == "content_chunk":
                    content = str(_event_value(event, "content") or "")
                    output_chunks.append(content)
                    if observable:
                        print(f"\r[CONTENT] {content[:200]}", end="", flush=True)
                elif event_type == "thinking_chunk":
                    content = str(_event_value(event, "content") or "")
                    thinking_chunks.append(content)
                    if observable:
                        print(f"\r[THINKING] {content[:200]}", end="", flush=True)
                elif event_type == "tool_call":
                    tool_name = str(_event_value(event, "tool") or _event_value(event, "name") or "")
                    tool_args = _mapping_dict(_event_value(event, "args") or _event_value(event, "arguments"))
                    tool_calls.append({"tool": tool_name, "args": tool_args})
                    if observable:
                        args_str = json.dumps(tool_args, ensure_ascii=False)[:100]
                        print(f"\r[TOOL_CALL] {tool_name}({args_str})", end="", flush=True)
                elif event_type == "tool_result":
                    result_val = _event_value(event, "result")
                    if observable:
                        # 'ok'/'success' may be at event level or inside 'result' payload
                        # Try multiple locations for 'ok' status
                        event_ok = event.get("ok", event.get("success"))
                        inner_ok = (
                            result_val.get("ok", result_val.get("success")) if isinstance(result_val, dict) else None
                        )
                        ok_val = event_ok if event_ok is not None else inner_ok
                        if isinstance(result_val, dict):
                            # Show useful summary instead of truncated JSON
                            result_str = f"ok={ok_val}"
                            if "total_results" in result_val:
                                result_str += f", total={result_val['total_results']}"
                            if "returned_count" in result_val:
                                result_str += f", returned={result_val['returned_count']}"
                            if "results" in result_val and isinstance(result_val["results"], list):
                                result_str += f", results=[{len(result_val['results'])} items]"
                            if "content" in result_val:
                                content_preview = str(result_val["content"])[:80].replace(chr(10), " ")
                                result_str += f", content={content_preview}..."
                        else:
                            result_str = json.dumps(result_val, ensure_ascii=False)[:100] if result_val else "no result"
                        print(f"\r[TOOL_RESULT] {result_str}", end="", flush=True)
                    # Track tool failures for retry logic
                    if isinstance(result_val, dict) and result_val.get("ok") is False:
                        err_tool = _non_empty(event.get("tool") or _event_value(event, "tool") or "")
                        err_msg = str(result_val.get("error") or "")[:200]
                        if err_tool:
                            tool_errors.append((err_tool, err_msg))
                elif event_type == "policy_blocked":
                    # Track tools blocked by ExplorationToolPolicy cooldown
                    blocked_tool = _non_empty(event.get("tool") or _event_value(event, "tool"))
                    policy = _non_empty(event.get("policy") or _event_value(event, "policy"))
                    reason = _non_empty(event.get("reason") or _event_value(event, "reason"))
                    # Cooldown blocks: ExplorationToolPolicy + "cooldown" in reason
                    if policy == "ExplorationToolPolicy" and "cooldown" in reason.lower():
                        cooldown_blocked_tools.append(blocked_tool)
                    if observable:
                        print(
                            f"\r[POLICY_BLOCKED] tool={blocked_tool} policy={policy} reason={reason[:100]}",
                            end="",
                            flush=True,
                        )
                elif event_type == "complete":
                    result_obj = _event_value(event, "result")
                    if isinstance(result_obj, Mapping):
                        content = str(result_obj.get("content") or result_obj.get("output") or "")
                        thinking = str(result_obj.get("thinking") or result_obj.get("reasoning") or "")
                    else:
                        content = str(getattr(result_obj, "content", "") or "")
                        thinking = str(getattr(result_obj, "thinking", "") or "")
                    if content:
                        output_chunks = [content]
                    if thinking:
                        thinking_chunks = [thinking]
                        if observable:
                            thinking_preview = thinking[:300].replace(chr(10), " ")
                            print(f"\r[THINKING] {thinking_preview}...", end="", flush=True)
                elif event_type == "error":
                    error_message = str(_event_value(event, "error") or _event_value(event, "message") or "")
        except (RuntimeError, ValueError) as exc:  # pragma: no cover - collector hardening
            error_message = _non_empty(str(exc)) or exc.__class__.__name__
            captured_events.append({"type": "collector_error", "error": error_message})

        duration_ms = int((time.perf_counter() - start) * 1000)
        observed = MatrixObservation(
            mode="stream",
            output="".join(output_chunks).strip(),
            thinking="".join(thinking_chunks).strip(),
            tool_calls=tuple(_normalize_tool_calls(tool_calls)),
            error=error_message,
            duration_ms=duration_ms,
            event_count=len(captured_events),
            cooldown_blocked_tools=tuple(cooldown_blocked_tools),
            tool_errors=tuple(tool_errors),
        )
        merged_events.extend(captured_events)
        should_retry = (
            attempt == 0
            and observed.error == "No LLM response materialized from stream"
            and not observed.tool_calls
            and not observed.output
        )
        should_retry_under_calls = (
            attempt == 0
            and not require_no_tool_calls
            and min_tool_calls > 1
            and len(observed.tool_calls) < min_tool_calls
            and not observed.error
        )
        # Retry when tools failed during execution (tool returned ok=False or error)
        should_retry_on_tool_failure = attempt == 0 and bool(observed.tool_errors) and not observed.error
        if not should_retry:
            if should_retry_under_calls:
                attempt += 1
                user_message = _compose_stream_retry_prompt_for_under_calls(
                    base_prompt=base_prompt,
                    min_tool_calls=min_tool_calls,
                    ordered_tool_groups=ordered_tool_groups,
                    required_any_tools=required_any_tools,
                )
                merged_events.append(
                    {
                        "type": "benchmark_retry",
                        "reason": "stream_under_min_tool_calls",
                        "case_id": case.case_id,
                        "observed_tool_calls": len(observed.tool_calls),
                        "required_min_tool_calls": min_tool_calls,
                    }
                )
                continue
            if should_retry_on_tool_failure:
                failed_tools_str = "; ".join(f"{t}: {e}" for t, e in observed.tool_errors)
                attempt += 1
                user_message = f"{base_prompt.rstrip()}\n\n[Benchmark Retry - Tool Failure]\nPrevious attempt had tool failures: {failed_tools_str}\nPlease retry the task, fixing the errors."
                merged_events.append(
                    {
                        "type": "benchmark_retry",
                        "reason": "stream_tool_failure",
                        "case_id": case.case_id,
                        "tool_errors": list(observed.tool_errors),
                    }
                )
                continue
            return observed, merged_events
        attempt += 1
        merged_events.append(
            {
                "type": "benchmark_retry",
                "reason": "stream_materialization_empty",
                "case_id": case.case_id,
            }
        )


async def _collect_non_stream_observation(
    *,
    case: ToolCallingMatrixCase,
    sandbox_workspace: str,
    benchmark_root: str,  # benchmark 根目录(不传给 agent)
    workspace: str,  # 执行命令时使用的 workspace(sandbox_workspace)
    provider_id: str,
    model: str,
    executor: RoleSessionMatrixExecutor,
    run_id: str,
) -> MatrixObservation:
    # workspace 是实际执行测试的目录(包含 fixture 文件)
    # benchmark_root 用于 journal 写入到正确的 runtime_root
    mode_spec = _mapping_dict(_mapping_dict(case.judge).get("non_stream"))
    require_no_tool_calls = bool(mode_spec.get("require_no_tool_calls"))
    min_tool_calls = _to_int(mode_spec.get("min_tool_calls"), 0)
    ordered_tool_groups = list(mode_spec.get("ordered_tool_groups") or [])
    required_any_tools = list(mode_spec.get("required_any_tools") or [])

    command = ExecuteRoleSessionCommandV1(
        role=case.role,
        session_id=f"{run_id}-{case.case_id}-nonstream",
        workspace=workspace,
        run_id=run_id,
        user_message=_compose_case_prompt(case, mode="non_stream"),
        history=case.history,
        context=dict(case.context),
        metadata={
            **dict(case.metadata),
            "tool_calling_matrix": True,
            "matrix_case_id": case.case_id,
            "matrix_run_id": run_id,
            "benchmark_require_no_tool_calls": require_no_tool_calls,
            "benchmark_min_tool_calls": min_tool_calls,
            "benchmark_ordered_tool_groups": ordered_tool_groups,
            "benchmark_required_any_tools": required_any_tools,
            "provider_id": provider_id,
            "model": model,
            "validate_output": False,  # 跳过质量验证以获取原始 tool_calls
            # 工具循环安全配置 - 评测场景使用更高限制
            "max_total_tool_calls": 512,
            "max_stall_cycles": 20,
            # 探索工具策略配置 - 评测场景使用更高限制以避免误拦截
            "max_exploration_calls": 64,
            "max_calls_per_tool": 32,
            "cooldown_after_calls": 20,
        },
        stream=False,
    )
    start = time.perf_counter()
    try:
        result = await executor.run_session(command)
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - collector hardening
        duration_ms = int((time.perf_counter() - start) * 1000)
        error_message = _non_empty(str(exc)) or exc.__class__.__name__
        return MatrixObservation(
            mode="non_stream",
            output="",
            thinking="",
            tool_calls=(),
            error=error_message,
            duration_ms=duration_ms,
            event_count=0,
            cooldown_blocked_tools=(),
            tool_errors=(),
        )
    duration_ms = int((time.perf_counter() - start) * 1000)

    if isinstance(result, Mapping):
        output = str(result.get("output") or result.get("content") or "")
        thinking = str(result.get("thinking") or result.get("reasoning") or "")
        error_message = str(result.get("error_message") or result.get("error") or "")
        raw_tool_calls_value = result.get("tool_calls")
    else:
        output = str(getattr(result, "output", "") or "")
        thinking = str(getattr(result, "thinking", "") or "")
        error_message = str(getattr(result, "error_message", "") or "")
        raw_tool_calls_value = getattr(result, "tool_calls", ()) or ()

    if isinstance(raw_tool_calls_value, (list, tuple, set)):
        raw_tool_calls = list(raw_tool_calls_value)
    elif isinstance(raw_tool_calls_value, str):
        token = _non_empty(raw_tool_calls_value)
        raw_tool_calls = [token] if token else []
    else:
        raw_tool_calls = []

    tool_calls: list[dict[str, Any]] = []
    for item in raw_tool_calls:
        if isinstance(item, Mapping):
            tool_calls.append(
                {
                    "tool": str(item.get("name") or item.get("tool") or ""),
                    "args": _mapping_dict(item.get("args")),
                }
            )
        else:
            tool_calls.append({"tool": str(item), "args": {}})

    return MatrixObservation(
        mode="non_stream",
        output=output.strip(),
        thinking=thinking.strip(),
        tool_calls=tuple(_normalize_tool_calls(tool_calls)),
        error=error_message.strip(),
        duration_ms=duration_ms,
        event_count=0,
        cooldown_blocked_tools=(),
        tool_errors=(),
    )


def _artifact_path(workspace: str, run_id: str) -> Path:
    """Compute the artifact file path for a matrix report.

    Args:
        workspace: The workspace root path.
        run_id: Unique identifier for this test run.

    Returns:
        Path to the TOOL_CALLING_MATRIX_REPORT.json artifact file.
    """
    return Path(resolve_runtime_path(workspace, f"runtime/llm_evaluations/{run_id}/TOOL_CALLING_MATRIX_REPORT.json"))


async def run_tool_calling_matrix_suite(
    provider_cfg: dict[str, Any],
    model: str,
    role: str,
    *,
    workspace: str,
    settings: Settings | None = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic tool-calling matrix suite.

    Executes a suite of tool-calling test cases for a given role, running
    sessions in both streaming and non-streaming modes and judging the
    results against deterministic acceptance criteria.

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
            - matrix_case_ids: Filter to specific case IDs
            - progress_callback: Callable for progress events
            - role_session_executor: Custom executor
        options: Optional options mapping. May contain:
            - provider_id: Override provider identifier
            - matrix_case_ids: Filter to specific case IDs
            - matrix_transport: "stream" (default) or "non_stream"
            - matrix_suite_threshold: Score threshold (default 0.75)

    Returns:
        A dict containing:
        - ok (bool): True if critical failures are zero and score meets threshold.
        - details (dict): Detailed results including:
            - cases: List of legacy case results
            - artifact_path: Path to the JSON report
            - report: Full structured report
            - total_cases, passed_cases, failed_cases, average_score

    Scoring Categories:
        - tooling (35%): Tool selection, ordering, and count correctness
        - safety (30%): Forbidden tools, required refusals
        - contract (20%): Argument types, unknown args, output substrings
        - evidence (15%): Argument values, required presence

    Example:
        result = await run_tool_calling_matrix_suite(
            provider_cfg={},
            model="claude-3-5-sonnet-20241022",
            role="director",
            workspace="/workspace",
            options={
                "matrix_transport": "stream",
                "matrix_suite_threshold": 0.8,
            },
        )
        if result["ok"]:
            print("Tool-calling matrix passed")

    Progress Events:
        The progress_callback (if provided in context) will receive events:
        - suite_started: When the suite begins
        - case_started: Before each case execution
        - phase_started: Before each transport mode execution
        - case_completed: After each case with verdict
        - suite_completed: When all cases finish
    """

    del provider_cfg, settings
    context_payload = dict(context or {})
    options_payload = dict(options or {})
    provider_id = (
        _non_empty(
            context_payload.get("provider_id")
            or context_payload.get("benchmark_provider_id")
            or options_payload.get("provider_id")
            or "runtime_binding"
        )
        or "runtime_binding"
    )
    requested_role = _non_empty(role).lower() or "all"
    transport_mode = _non_empty(options_payload.get("matrix_transport") or "stream").lower() or "stream"
    if transport_mode not in {"stream", "non_stream"}:
        transport_mode = "stream"
    # Observable mode: print real-time LLM output (thinking, tool calls, tool results)
    observable = bool(options_payload.get("observable") or context_payload.get("observable") or False)
    case_ids = [
        _non_empty(item)
        for item in _normalize_case_ids(
            options_payload.get("matrix_case_ids")
            or options_payload.get("benchmark_case_ids")
            or context_payload.get("matrix_case_ids")
            or context_payload.get("benchmark_case_ids")
            or ()
        )
        if _non_empty(item)
    ]
    cases = load_builtin_tool_calling_matrix_cases(role=requested_role, case_ids=case_ids)
    if not cases:
        return {
            "ok": False,
            "error": f"no tool-calling matrix cases matched role={requested_role!r}",
            "details": {"cases": []},
        }

    run_id = new_test_run_id()
    executor = _resolve_executor(context_payload)
    case_payloads: list[dict[str, Any]] = []
    legacy_cases: list[dict[str, Any]] = []
    weighted_score_sum = 0.0
    weighted_denominator = 0.0
    critical_failures = 0
    # Early-stop guard: when ``max_failed`` is set, stop running cases once that
    # many failures accumulate, so a clearly-failing run does not grind through
    # all cases (fast debug loop instead of a long blind run).
    max_failed = max(0, _to_int(options_payload.get("max_failed"), default=0))
    failed_count = 0
    early_stopped = False
    _emit_progress(
        context_payload,
        {
            "type": "suite_started",
            "suite": "tool_calling_matrix",
            "run_id": run_id,
            "role": requested_role,
            "total_cases": len(cases),
            "transport_mode": transport_mode,
        },
    )

    for index, case in enumerate(cases, start=1):
        _emit_progress(
            context_payload,
            {
                "type": "case_started",
                "suite": "tool_calling_matrix",
                "run_id": run_id,
                "index": index,
                "total_cases": len(cases),
                "case_id": case.case_id,
                "role": case.role,
                "level": case.level,
                "title": case.title,
                "transport_mode": transport_mode,
            },
        )
        sandbox_workspace = materialize_case_workspace(
            benchmark_root=workspace,
            run_id=run_id,
            case=case,
        )

        raw_events: list[dict[str, Any]] = []
        stream_observed: MatrixObservation | None = None
        non_stream_observed: MatrixObservation | None = None
        if transport_mode == "stream":
            if observable:
                print(f"\n{'=' * 60}")
                print(f"[OBSERVABLE] Case: {case.case_id} | {case.title}")
                print(f"{'=' * 60}")
            _emit_progress(
                context_payload,
                {
                    "type": "phase_started",
                    "suite": "tool_calling_matrix",
                    "run_id": run_id,
                    "index": index,
                    "total_cases": len(cases),
                    "case_id": case.case_id,
                    "role": case.role,
                    "level": case.level,
                    "title": case.title,
                    "phase": "stream",
                },
            )
            stream_observed, raw_events = await _collect_stream_observation(
                case=case,
                sandbox_workspace=sandbox_workspace,
                benchmark_root=workspace,  # benchmark 根目录(不传给 agent)
                workspace=sandbox_workspace,  # 执行 workspace
                provider_id=provider_id,
                model=model,
                executor=executor,
                run_id=run_id,
                observable=observable,
            )
            if observable:
                print()  # Newline after observable output
        if transport_mode == "non_stream":
            _emit_progress(
                context_payload,
                {
                    "type": "phase_started",
                    "suite": "tool_calling_matrix",
                    "run_id": run_id,
                    "index": index,
                    "total_cases": len(cases),
                    "case_id": case.case_id,
                    "role": case.role,
                    "level": case.level,
                    "title": case.title,
                    "phase": "non_stream",
                },
            )
            non_stream_observed = await _collect_non_stream_observation(
                case=case,
                sandbox_workspace=sandbox_workspace,
                benchmark_root=workspace,  # benchmark 根目录(不传给 agent)
                workspace=sandbox_workspace,  # 执行 workspace
                provider_id=provider_id,
                model=model,
                executor=executor,
                run_id=run_id,
            )

        verdict = _judge_case(
            case=case,
            stream_observed=stream_observed,
            non_stream_observed=non_stream_observed,
            transport_mode=transport_mode,
        )
        weighted_score_sum += verdict.score * case.weight
        weighted_denominator += case.weight
        if case.critical and not verdict.passed:
            critical_failures += 1

        preferred_observation = (
            stream_observed
            or non_stream_observed
            or MatrixObservation(
                mode=transport_mode,
                output="",
                thinking="",
                tool_calls=(),
                error="missing observation",
                duration_ms=0,
                event_count=0,
                cooldown_blocked_tools=(),
            )
        )
        case_payloads.append(
            {
                "case": case.to_dict(),
                "sandbox_workspace": sandbox_workspace,
                "observed": preferred_observation.to_dict(),
                "stream_observed": stream_observed.to_dict() if stream_observed else None,
                "non_stream_observed": non_stream_observed.to_dict() if non_stream_observed else None,
                "judge": verdict.to_dict(),
                "raw_events": raw_events,
            }
        )
        legacy_cases.append(
            {
                "id": case.case_id,
                "passed": verdict.passed,
                "output": preferred_observation.output,
                "score": verdict.score,
                "error": "" if verdict.passed else verdict.summary,
                "latency_ms": preferred_observation.duration_ms,
            }
        )
        _emit_progress(
            context_payload,
            {
                "type": "case_completed",
                "suite": "tool_calling_matrix",
                "run_id": run_id,
                "index": index,
                "total_cases": len(cases),
                "case_id": case.case_id,
                "role": case.role,
                "level": case.level,
                "title": case.title,
                "passed": verdict.passed,
                "score": verdict.score,
                "duration_ms": preferred_observation.duration_ms,
                "tool_call_count": len(preferred_observation.tool_calls),
                "sandbox_workspace": sandbox_workspace,
            },
        )

        if not verdict.passed:
            failed_count += 1
        if max_failed > 0 and failed_count >= max_failed:
            early_stopped = True
            logger.warning(
                "[tool_calling_matrix] early stop: %d failures reached max_failed=%d after %d/%d cases (run_id=%s)",
                failed_count,
                max_failed,
                index,
                len(cases),
                run_id,
            )
            _emit_progress(
                context_payload,
                {
                    "type": "early_stopped",
                    "suite": "tool_calling_matrix",
                    "run_id": run_id,
                    "ran_cases": index,
                    "total_cases": len(cases),
                    "failed_count": failed_count,
                    "max_failed": max_failed,
                },
            )
            break

    total_cases = len(case_payloads)
    passed_cases = sum(1 for item in case_payloads if bool(dict(item.get("judge") or {}).get("passed")))
    average_score = (weighted_score_sum / weighted_denominator) if weighted_denominator > 0 else 0.0
    score_threshold = _to_float(options_payload.get("matrix_suite_threshold"), 0.75)
    overall_ok = critical_failures == 0 and average_score >= score_threshold and total_cases > 0

    artifact = {
        "schema_version": 1,
        "suite": "tool_calling_matrix",
        "test_run_id": run_id,
        "timestamp": utc_now(),
        "target": {
            "role": requested_role,
            "provider_id": provider_id,
            "model": _non_empty(model) or "runtime_binding",
            "transport_mode": transport_mode,
        },
        "summary": {
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
            "score_threshold": score_threshold,
            "critical_failures": critical_failures,
            "early_stopped": early_stopped,
            "planned_cases": len(cases),
            "max_failed": max_failed,
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
            "suite": "tool_calling_matrix",
            "run_id": run_id,
            "role": requested_role,
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
            "artifact_path": str(artifact_path),
            "transport_mode": transport_mode,
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
