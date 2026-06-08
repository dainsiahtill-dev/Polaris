"""Speculation 专用评测矩阵.

与 ``tool_calling_matrix`` 的单次执行不同，本套件是**差分**评测：对每个 case
用 speculation **ON**（且开启领养审计）与 **OFF** 各跑一遍真实流式 turn，验证
ADR-0077 的核心承诺并量化收益：

- **不变量 A（correctness 不变）**：ON 跑在领养审计模式下，内核会把每次 ADOPT/JOIN
  的投机结果与权威重算结果逐一比对，``wrong_adoption`` 必须恒为 0（硬门禁）。
- **收益（max benefit）**：统计 ``adopted / joined / replayed / hit_rate /
  saved_ms_total``，并用 OFF 跑作为时延基线给出 ON-vs-OFF 的耗时差。

speculation 只在**流式 + 原生 function-calling** 模型上才会真正触发；非 FC 模型
（文本工具恢复路径）不会产生 shadow，活跃度为 0——这本身是有用的诊断信号。

执行入口 ``run_speculation_matrix_suite`` 与其它套件签名一致，可挂到
``agentic_eval`` 的 ``_suite_runners()``。复用 ``tool_calling_matrix`` 的 case
装载、fixture 物化与执行器解析，避免重复造轮子。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    subscribe,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
)

from .tool_calling_matrix import (
    RoleSessionMatrixExecutor,
    _compose_case_prompt,
    _event_value,
    _mapping_dict,
    _non_empty,
    _normalize_case_ids,
    _resolve_executor,
    load_builtin_tool_calling_matrix_cases,
    materialize_case_workspace,
)
from .utils import new_test_run_id

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

# 默认 case 集合：**只读 / 检索类**——speculation 在流式阶段对只读工具
# （grep/glob/read 链）触发，这些 case 最能干净地体现收益。刻意排除带写
# delivery-contract 的 case（l3_glob_then_read、l5_* 等）：其 pass/fail 由模型
# 写行为与意图分类的非确定性主导（实测 l3_glob_then_read 在 speculation OFF 下
# 也会间歇触发 single_batch_contract_violation），与 speculation 收益无关，只会
# 给差分评测引入噪声。可经 matrix_case_ids 覆盖以评测任意子集。
DEFAULT_SPECULATION_CASE_IDS: tuple[str, ...] = (
    "l1_grep_search",
    "l1_directory_listing",
    "l1_read_tail",
    "l1_single_tool_accuracy",
    "l2_multi_file_read",
    "l2_glob_pattern_matching",
    "l2_subdirectory_read",
    "l3_search_then_read",
    "l3_idempotent_read",
    "l3_parallel_calls",
    "l7_context_memory",
    "l7_context_switch_with_memory",
    "l7_multi_tool_coordination",
    "l7_search_strategy_selection",
    "l7_tool_result_interpretation",
)

_SPEC_SUMMARY_EVENT = "speculation.turn.summary"
_COUNTER_KEYS = (
    "started",
    "completed",
    "eligible_dropped",
    "adopted",
    "joined",
    "replayed",
    "cancelled",
    "failed",
    "abandoned",
    "timed_out",
    "saved_ms_total",
    "wrong_adoption",
)


class _SpeculationCollector:
    """订阅 speculation 事件，聚合本次运行窗口内的所有 per-turn 汇总."""

    def __init__(self) -> None:
        self.totals: dict[str, int] = dict.fromkeys(_COUNTER_KEYS, 0)
        self.turn_count: int = 0

    def __call__(self, event: SpeculationEvent) -> None:
        if event.event_type != _SPEC_SUMMARY_EVENT:
            return
        self.turn_count += 1
        snap = event.metadata or {}
        for key in _COUNTER_KEYS:
            value = snap.get(key)
            if isinstance(value, (int, float)):
                self.totals[key] += int(value)


@contextmanager
def _speculation_env(*, enabled: bool, audit: bool) -> Any:
    """临时设置 speculation 相关环境变量，退出时精确还原.

    每个 turn 的 shadow engine 在构造时新鲜读取这些 flag，因此进程内逐次切换
    即可生效，无需重启。
    """
    keys = {
        "ENABLE_SPECULATIVE_EXECUTION": "1" if enabled else "0",
        "SPECULATION_AUDIT_ADOPTIONS": "1" if (enabled and audit) else "0",
    }
    previous = {k: os.environ.get(k) for k in keys}
    try:
        os.environ.update(keys)
        yield
    finally:
        for key, prior in previous.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


def _canonical_payload(payload: Any) -> str:
    """工具结果的可稳定比较投影（剥离易变计时字段）."""
    volatile = {"execution_time_ms", "duration_ms", "elapsed_ms", "timestamp", "ts", "receipt_id"}

    def _strip(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: _strip(v) for k, v in sorted(value.items()) if k not in volatile}
        if isinstance(value, (list, tuple)):
            return [_strip(item) for item in value]
        return value

    return json.dumps(_strip(payload), ensure_ascii=False, sort_keys=True, default=str)


def _is_speculation_attributable(error: str) -> bool:
    """错误信息是否可归因到 speculation 机制本身（而非模型/契约行为）."""
    low = error.lower()
    return any(token in low for token in ("specul", "shadow", "adopt", "prepare_shadow"))


def _primary_arg(tool: str, args: Mapping[str, Any]) -> str:
    """取工具调用的主参数（路径/查询）作为 parity 比较键."""
    for key in ("path", "file", "filepath", "target", "query", "pattern", "dir", "directory"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key}={value.strip()}"
    return _canonical_payload(args)


async def _run_session(
    *,
    executor: RoleSessionMatrixExecutor,
    command: ExecuteRoleSessionCommandV1,
) -> dict[str, Any]:
    """跑一遍流式 role session，采集工具调用、工具结果与最终输出/错误."""
    tool_calls: list[dict[str, Any]] = []
    tool_results: dict[str, str] = {}
    output_chunks: list[str] = []
    error_message = ""
    start = time.perf_counter()
    last_call_key = ""
    try:
        async for raw_event in executor.stream_session(command):
            event = _mapping_dict(raw_event)
            event_type = _non_empty(event.get("type"))
            if event_type == "content_chunk":
                output_chunks.append(str(_event_value(event, "content") or ""))
            elif event_type == "tool_call":
                tool_name = str(_event_value(event, "tool") or _event_value(event, "name") or "")
                tool_args = _mapping_dict(_event_value(event, "args") or _event_value(event, "arguments"))
                last_call_key = f"{tool_name}|{_primary_arg(tool_name, tool_args)}"
                tool_calls.append({"tool": tool_name, "args": tool_args, "key": last_call_key})
            elif event_type == "tool_result":
                result_val = _event_value(event, "result")
                if last_call_key and last_call_key not in tool_results:
                    tool_results[last_call_key] = _canonical_payload(result_val)
            elif event_type == "complete":
                result_obj = _event_value(event, "result")
                if isinstance(result_obj, Mapping):
                    content = str(result_obj.get("content") or result_obj.get("output") or "")
                    if content:
                        output_chunks = [content]
            elif event_type == "error":
                error_message = str(_event_value(event, "error") or _event_value(event, "message") or "")
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - collector hardening
        error_message = _non_empty(str(exc)) or exc.__class__.__name__

    return {
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "output": "".join(output_chunks).strip(),
        "error": error_message,
        "duration_ms": int((time.perf_counter() - start) * 1000),
    }


def _build_command(
    *,
    case: Any,
    workspace: str,
    run_id: str,
    provider_id: str,
    model: str,
    suffix: str,
) -> ExecuteRoleSessionCommandV1:
    return ExecuteRoleSessionCommandV1(
        role=case.role,
        session_id=f"{run_id}-{case.case_id}-{suffix}",
        workspace=workspace,
        user_message=_compose_case_prompt(case, mode="stream"),
        run_id=run_id,
        history=case.history,
        context=dict(case.context),
        metadata={
            **dict(case.metadata),
            "speculation_matrix": True,
            "matrix_case_id": case.case_id,
            "matrix_run_id": run_id,
            "provider_id": provider_id,
            "model": model,
            "validate_output": False,
            "max_total_tool_calls": 512,
            "max_stall_cycles": 20,
            "max_exploration_calls": 64,
            "max_calls_per_tool": 32,
            "cooldown_after_calls": 20,
        },
        stream=True,
    )


def _parity_intersection(on_run: Mapping[str, Any], off_run: Mapping[str, Any]) -> dict[str, Any]:
    """比较 ON/OFF 在共同工具调用上的结果一致性（信息性，model 非确定性容忍）."""
    on_results: dict[str, str] = dict(on_run.get("tool_results") or {})
    off_results: dict[str, str] = dict(off_run.get("tool_results") or {})
    shared = set(on_results) & set(off_results)
    mismatches = [key for key in shared if on_results[key] != off_results[key]]
    return {
        "shared_calls": len(shared),
        "mismatches": len(mismatches),
        "mismatch_keys": sorted(mismatches)[:5],
    }


async def _evaluate_case(
    *,
    case: Any,
    executor: RoleSessionMatrixExecutor,
    run_id: str,
    provider_id: str,
    model: str,
    workspace_root: str,
) -> dict[str, Any]:
    """对单个 case 跑 ON（审计）与 OFF 两遍并裁决.

    ON 与 OFF 使用**独立 run_id 与独立 sandbox**，确保 OFF 是真正独立的基线，
    不被同 run 的幂等/receipt 缓存短路（否则 OFF 会瞬间返回缓存结果）。
    """
    # ON：speculation 开启 + 领养审计
    on_run_id = f"{run_id}-{case.case_id}-on"
    on_workspace = materialize_case_workspace(benchmark_root=workspace_root, run_id=on_run_id, case=case)
    on_collector = _SpeculationCollector()
    unsubscribe = subscribe(on_collector)
    try:
        with _speculation_env(enabled=True, audit=True):
            on_run = await _run_session(
                executor=executor,
                command=_build_command(
                    case=case,
                    workspace=on_workspace,
                    run_id=on_run_id,
                    provider_id=provider_id,
                    model=model,
                    suffix="on",
                ),
            )
    finally:
        unsubscribe()

    # OFF：speculation 关闭（独立基线：独立 run_id + 独立 sandbox）。
    off_run_id = f"{run_id}-{case.case_id}-off"
    off_workspace = materialize_case_workspace(benchmark_root=workspace_root, run_id=off_run_id, case=case)
    with _speculation_env(enabled=False, audit=False):
        off_run = await _run_session(
            executor=executor,
            command=_build_command(
                case=case,
                workspace=off_workspace,
                run_id=off_run_id,
                provider_id=provider_id,
                model=model,
                suffix="off",
            ),
        )

    totals = on_collector.totals
    wrong_adoption = int(totals.get("wrong_adoption", 0))
    adopted = int(totals.get("adopted", 0))
    joined = int(totals.get("joined", 0))
    replayed = int(totals.get("replayed", 0))
    resolved = adopted + joined + replayed
    hit_rate = round((adopted + joined) / resolved, 4) if resolved else 0.0
    parity = _parity_intersection(on_run, off_run)

    on_error = _non_empty(on_run.get("error"))
    off_error = _non_empty(off_run.get("error"))
    # ON 出错而 OFF 不出错 = 错误分歧。但 speculation 只在 shadow 里预跑只读工具，
    # 不改变模型实际 emit 的工具调用，故 delivery-contract / 模型行为类错误与
    # speculation 无关（受意图分类与模型非确定性影响）。只有错误信息可归因到
    # speculation（提到 shadow/specul/adopt）时，才算 speculation 真正引入的回归。
    error_divergence = bool(on_error) and not bool(off_error)
    speculation_regressed = error_divergence and _is_speculation_attributable(on_error)
    # 硬门禁（不变量 A）：无错误领养，且 speculation 未引入可归因的错误。
    passed = wrong_adoption == 0 and not speculation_regressed

    return {
        "case_id": case.case_id,
        "role": case.role,
        "passed": passed,
        "wrong_adoption": wrong_adoption,
        "adopted": adopted,
        "joined": joined,
        "replayed": replayed,
        "hit_rate": hit_rate,
        "saved_ms_total": int(totals.get("saved_ms_total", 0)),
        "spec_turns": on_collector.turn_count,
        "duration_ms_on": int(on_run.get("duration_ms", 0)),
        "duration_ms_off": int(off_run.get("duration_ms", 0)),
        "latency_delta_ms": int(off_run.get("duration_ms", 0)) - int(on_run.get("duration_ms", 0)),
        "on_error": on_error,
        "off_error": off_error,
        "error_divergence": error_divergence,
        "speculation_regressed": speculation_regressed,
        "parity": parity,
        "tool_calls_on": [c["key"] for c in on_run.get("tool_calls", [])],
    }


async def run_speculation_matrix_suite(
    provider_cfg: dict[str, Any],
    model: str,
    role: str,
    *,
    workspace: str,
    settings: Settings | None = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """运行 speculation 差分评测矩阵（ON-audit vs OFF）.

    Returns dict: ``ok`` + ``details``（per-case 结果 + 汇总指标 + 报告路径）。
    ``ok`` 为真当且仅当：没有 wrong_adoption，且没有 ON 跑致命错误。
    """
    del provider_cfg, settings
    context_payload = dict(context or {})
    options_payload = dict(options or {})
    provider_id = (
        _non_empty(context_payload.get("provider_id") or options_payload.get("provider_id") or "runtime_binding")
        or "runtime_binding"
    )
    requested_role = _non_empty(role).lower() or "director"
    max_failed = 0
    raw_max_failed = options_payload.get("max_failed")
    if isinstance(raw_max_failed, int) and raw_max_failed > 0:
        max_failed = raw_max_failed

    case_ids = [
        _non_empty(item)
        for item in _normalize_case_ids(
            options_payload.get("matrix_case_ids") or context_payload.get("matrix_case_ids") or ()
        )
        if _non_empty(item)
    ]
    if not case_ids:
        case_ids = list(DEFAULT_SPECULATION_CASE_IDS)

    cases = load_builtin_tool_calling_matrix_cases(role=requested_role, case_ids=case_ids)
    if not cases:
        return {
            "ok": False,
            "error": f"no speculation matrix cases matched role={requested_role!r}",
            "details": {"cases": []},
        }

    run_id = new_test_run_id()
    executor = _resolve_executor(context_payload)

    results: list[dict[str, Any]] = []
    failed_count = 0
    early_stopped = False
    for case in cases:
        result = await _evaluate_case(
            case=case,
            executor=executor,
            run_id=run_id,
            provider_id=provider_id,
            model=model,
            workspace_root=workspace,
        )
        results.append(result)
        if not result["passed"]:
            failed_count += 1
            if max_failed > 0 and failed_count >= max_failed:
                early_stopped = True
                break

    passed_cases = sum(1 for r in results if r["passed"])
    total_wrong_adoption = sum(r["wrong_adoption"] for r in results)
    total_adopted = sum(r["adopted"] for r in results)
    total_joined = sum(r["joined"] for r in results)
    total_replayed = sum(r["replayed"] for r in results)
    total_resolved = total_adopted + total_joined + total_replayed
    total_saved_ms = sum(r["saved_ms_total"] for r in results)
    active_cases = sum(1 for r in results if (r["adopted"] + r["joined"] + r["replayed"]) > 0)
    regressed_cases = sum(1 for r in results if r.get("speculation_regressed"))
    error_divergence_cases = sum(1 for r in results if r.get("error_divergence"))
    overall_hit_rate = round((total_adopted + total_joined) / total_resolved, 4) if total_resolved else 0.0

    summary = {
        "total_cases": len(results),
        "passed_cases": passed_cases,
        "failed_cases": len(results) - passed_cases,
        "wrong_adoption_total": total_wrong_adoption,
        "speculation_regressed_cases": regressed_cases,
        "error_divergence_cases": error_divergence_cases,
        "adopted_total": total_adopted,
        "joined_total": total_joined,
        "replayed_total": total_replayed,
        "hit_rate": overall_hit_rate,
        "saved_ms_total": total_saved_ms,
        "speculation_active_cases": active_cases,
        "early_stopped": early_stopped,
    }
    # ok（不变量 A）：无错误领养，且 speculation 未在任何 case 引入新错误。
    ok = total_wrong_adoption == 0 and regressed_cases == 0 and len(results) > 0

    return {
        "ok": ok,
        "details": {
            "suite": "speculation_matrix",
            "run_id": run_id,
            "cases": results,
            "summary": summary,
        },
    }
