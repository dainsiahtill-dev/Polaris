"""PM Planning Pipeline - Quality retry loop for PM task generation.

This module provides the planning iteration pipeline with quality gate retry logic.
Part of the orchestration.pm_planning cell.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import (
    _looks_like_tool_call_output,
    _migrate_tasks_in_place,
    collect_schema_warnings,
    get_pm_invoke_port,
    normalize_engine_config,
    normalize_path_list,
    normalize_pm_payload,
    normalize_priority,
)
from polaris.cells.orchestration.pm_planning.internal.shared_quality import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality as evaluate_shared_pm_task_quality,
)

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

# Constants for PM task quality evaluation
_PM_TASK_QUALITY_MODE_ENV = "POLARIS_PM_TASK_QUALITY_MODE"
_PM_TASK_QUALITY_RETRIES_ENV = "POLARIS_PM_TASK_QUALITY_RETRIES"
_PM_TASK_QUALITY_MODES = {"off", "warn", "strict"}
_PM_TASK_QUALITY_DEFAULT_MODE = "strict"

_PM_PROMPT_LEAK_TOKENS = (
    "you are ",
    "你是",
    "角色设定",
    "行为规范",
    "coding agent guidance",
    "agents.md instructions",
    "system prompt",
    "提示词",
    "no yapping",
    "think before you code",
    "<instructions>",
    "</instructions>",
)

_PM_PLACEHOLDER_TOKENS = (
    "placeholder",
    "待补充",
    "待完善",
    "lorem ipsum",
)

_PM_ACTION_TOKENS = (
    "build",
    "implement",
    "define",
    "design",
    "refine",
    "expand",
    "write",
    "create",
    "refactor",
    "verify",
    "validate",
    "构建",
    "实现",
    "定义",
    "设计",
    "编写",
    "创建",
    "重构",
    "验证",
)

_PM_MEASURABLE_ACCEPTANCE_TOKENS = (
    "command",
    "commands",
    "cmd",
    "shell",
    "stdout",
    "stderr",
    "exit code",
    "status code",
    "returns",
    "return",
    "response",
    "assert",
    "verify",
    "evidence",
    "artifact",
    "path",
    "logs",
    "threshold",
    "ms",
    "秒",
    "分钟",
    "%",
    "命令",
    "验证",
    "断言",
    "证据",
    "产物",
    "路径",
    "日志",
)

_PM_MEASURABLE_COMMAND_RE = re.compile(
    r"\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_ASSERT_RE = re.compile(
    r"\b(verify|assert|expect|should|must|returns?|response|status|校验|验证|断言|应当|必须)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_RESULT_RE = re.compile(
    r"\b(200|201|202|204|400|401|403|404|409|422|500|pass|fail|true|false|ok|error)\b|[<>]=?\s*\d+|\b\d+\s*(ms|s|sec|seconds?|分钟|小时|days?)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\w.\-]+[\\/][\w.\-/\\]+)",
)
_PM_MEASURABLE_BACKTICK_RE = re.compile(r"`[^`]{2,}`")


def _resolve_pm_task_quality_mode() -> str:
    raw = (
        str(os.environ.get(_PM_TASK_QUALITY_MODE_ENV, _PM_TASK_QUALITY_DEFAULT_MODE) or _PM_TASK_QUALITY_DEFAULT_MODE)
        .strip()
        .lower()
    )
    if raw not in _PM_TASK_QUALITY_MODES:
        return _PM_TASK_QUALITY_DEFAULT_MODE
    return raw


def _resolve_pm_task_quality_retries() -> int:
    raw = str(os.environ.get(_PM_TASK_QUALITY_RETRIES_ENV, "2") or "2").strip()
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = 2
    return max(0, min(4, value))


def _normalize_quality_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_quality_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    token = re.sub(r"^[A-Za-z]:/", "", token)
    token = token.lstrip("./").strip("/")
    token = re.sub(r"/+", "/", token)
    return token.lower()


def _contains_pm_prompt_leakage(text: str) -> bool:
    token = _normalize_quality_text(text).lower()
    if not token:
        return False
    return any(marker in token for marker in _PM_PROMPT_LEAK_TOKENS)


def _count_normalized_pm_tasks(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    tasks_raw = payload.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    return sum(1 for task in tasks if isinstance(task, dict))


def _is_parse_failed_pm_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return True
    focus = _normalize_quality_text(payload.get("focus")).lower()
    notes = _normalize_quality_text(payload.get("notes")).lower()
    if focus == "parse_failed":
        return True
    return "json parse failed" in notes


def _should_promote_pm_quality_candidate(
    candidate: dict[str, Any] | None,
    candidate_quality: dict[str, Any] | None,
    current_best: dict[str, Any] | None,
    current_best_quality: dict[str, Any] | None,
) -> bool:
    if not isinstance(candidate, dict):
        return False
    if not isinstance(current_best, dict) or not current_best:
        return True

    candidate_task_count = _count_normalized_pm_tasks(candidate)
    current_task_count = _count_normalized_pm_tasks(current_best)
    if candidate_task_count != current_task_count:
        return candidate_task_count > current_task_count

    candidate_ok = bool((candidate_quality or {}).get("ok"))
    current_ok = bool((current_best_quality or {}).get("ok"))
    if candidate_ok != current_ok:
        return candidate_ok

    candidate_parse_failed = _is_parse_failed_pm_payload(candidate)
    current_parse_failed = _is_parse_failed_pm_payload(current_best)
    if candidate_parse_failed != current_parse_failed:
        return not candidate_parse_failed

    candidate_score = int((candidate_quality or {}).get("score") or 0)
    current_score = int((current_best_quality or {}).get("score") or 0)
    if candidate_score != current_score:
        return candidate_score > current_score

    return False


def _looks_like_placeholder_task(*parts: str) -> bool:
    compact = _normalize_quality_text(" ".join(parts)).lower()
    if not compact:
        return True
    if any(marker in compact for marker in _PM_PLACEHOLDER_TOKENS):
        return True
    if re.match(r"^\s*(?:todo|tbd)\s*(?:[:：\-].*)?$", compact):
        return True
    if re.search(r"\btodo\s*(?:->|→)\b", compact):
        return False
    if re.search(r"\b(?:todo|tbd)\b", compact):
        if re.search(r"\b(?:todo|tbd)\b\s*(?:item|task|later|pending)?\s*$", compact):
            return True
        if re.search(r"^\s*(?:todo|tbd)\b", compact):
            return True
    return False


def _collect_task_paths(task: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("target_files", "scope_paths", "context_files"):
        raw = task.get(key)
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip()
                if token:
                    candidates.append(token)
    deduped: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        norm = _normalize_quality_path(path)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(path)
    return deduped


def _is_docs_scope_allowed(path: str, active_doc: str, active_dir: str) -> bool:
    token = _normalize_quality_path(path)
    if not token:
        return True
    if token == active_doc:
        return True
    return token == active_dir


def _evaluate_pm_task_quality(
    normalized: dict[str, Any],
    docs_stage: dict[str, Any] | None,
) -> dict[str, Any]:
    return evaluate_shared_pm_task_quality(normalized, docs_stage=docs_stage)


def _build_pm_json_retry_prompt(invalid_output: str) -> str:
    preview = str(invalid_output or "").strip()
    if len(preview) > 2000:
        preview = preview[:2000]
    return (
        "Your previous response was invalid for PM contract parsing.\n"
        "Return exactly one JSON object that matches the PM schema.\n"
        "Do not emit TOOL_CALL/function calls/markdown/explanations.\n"
        "Required top-level keys: overall_goal, focus, tasks, notes.\n"
        "tasks must be an array (can be empty).\n\n"
        "Previous invalid output (for correction only):\n"
        f"{preview}"
    )


def _build_pm_quality_retry_prompt(
    *,
    base_prompt: str,
    previous_payload: dict[str, Any],
    quality_report: dict[str, Any],
) -> str:
    report_summary = str(quality_report.get("summary") or "").strip()
    critical = [str(item).strip() for item in (quality_report.get("critical_issues") or []) if str(item).strip()][:8]
    warnings = [str(item).strip() for item in (quality_report.get("warnings") or []) if str(item).strip()][:8]
    preview_payload = {
        "overall_goal": str(previous_payload.get("overall_goal") or "").strip(),
        "focus": str(previous_payload.get("focus") or "").strip(),
        "tasks": previous_payload.get("tasks") if isinstance(previous_payload.get("tasks"), list) else [],
    }
    preview = json.dumps(preview_payload, ensure_ascii=False)
    if len(preview) > 5000:
        preview = preview[:5000]

    violation_lines = "\n".join(f"- {item}" for item in critical) or "- quality gate reported critical issues."
    warning_lines = "\n".join(f"- {item}" for item in warnings) or "- none"
    return (
        f"{base_prompt}\n\n"
        "QUALITY GATE RETRY (mandatory):\n"
        "Your previous PM task list failed strict quality validation.\n"
        "Regenerate the FULL JSON object and fix all issues below.\n"
        "Do not explain. Output JSON only.\n"
        "Each task must include: phase, execution_checklist (>=3 concrete steps), "
        "acceptance_criteria with measurable command/evidence anchors.\n"
        "For decomposed task lists, include dependency chain via depends_on/dependencies.\n"
        "For docs-stage tasks, include metadata.doc_sections and metadata.change_intent, "
        "and set backlog_ref to the source backlog sentence.\n"
        f"Quality summary: {report_summary}\n"
        "Critical issues to fix:\n"
        f"{violation_lines}\n"
        "Warnings to improve:\n"
        f"{warning_lines}\n"
        "Previous payload preview (for correction only):\n"
        f"{preview}\n"
    )


def _merge_engine_config(payload_engine: Any, args: argparse.Namespace) -> dict[str, Any]:
    normalized_payload = normalize_engine_config(payload_engine)

    raw_mode = normalized_payload.get(
        "director_execution_mode",
        getattr(args, "director_execution_mode", "single"),
    )
    mode = str(raw_mode or "single").strip().lower()
    if mode not in {"single", "multi"}:
        mode = "single"

    raw_workers = normalized_payload.get(
        "max_directors",
        getattr(args, "max_directors", 1),
    )
    try:
        max_directors = int(raw_workers)
    except (TypeError, ValueError):
        max_directors = 1
    if max_directors <= 0 or mode == "single":
        max_directors = 1

    raw_policy = normalized_payload.get(
        "scheduling_policy",
        getattr(args, "director_scheduling_policy", "priority"),
    )
    policy = str(raw_policy or "priority").strip().lower()
    if policy not in {"fifo", "priority", "dag"}:
        policy = "priority"

    return {
        "director_execution_mode": mode,
        "max_directors": max_directors,
        "scheduling_policy": policy,
    }


def _has_measurable_acceptance_anchor(acceptance_items: list[str]) -> bool:
    for item in acceptance_items:
        text = _normalize_quality_text(item)
        if not text:
            continue
        lowered = text.lower()
        if any(token in lowered for token in _PM_MEASURABLE_ACCEPTANCE_TOKENS):
            return True
        if _PM_MEASURABLE_BACKTICK_RE.search(text):
            return True
        if _PM_MEASURABLE_COMMAND_RE.search(text):
            return True
        has_assert = bool(_PM_MEASURABLE_ASSERT_RE.search(text))
        has_observable = bool(_PM_MEASURABLE_RESULT_RE.search(text) or _PM_MEASURABLE_PATH_RE.search(text))
        if has_assert and has_observable:
            return True
    return False


def _normalize_task_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [_normalize_quality_text(item) for item in value]
    elif isinstance(value, str):
        token = _normalize_quality_text(value)
        items = [token] if token else []
    else:
        items = []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        normalized = item.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def _pick_task_scope_hint(task: dict[str, Any]) -> str:
    for key in ("target_files", "scope_paths", "context_files"):
        for item in normalize_path_list(task.get(key)):
            token = _normalize_quality_text(item)
            if token:
                return token
    return "workspace scoped files"


def _infer_pm_task_phase(task: dict[str, Any], *, index: int) -> str:
    text = _normalize_quality_text(
        " ".join(
            [
                str(task.get("title") or ""),
                str(task.get("goal") or ""),
                str(task.get("description") or ""),
            ]
        )
    ).lower()
    if any(token in text for token in ("verify", "test", "qa", "验证", "测试", "断言")):
        return "verify"
    if any(token in text for token in ("deploy", "release", "publish", "发布", "部署")):
        return "deploy"
    if any(token in text for token in ("build", "compile", "bundle", "构建", "编译", "打包")):
        return "build"
    if index == 0:
        return "design"
    return "implement"


def run_pm_planning_iteration(
    args: argparse.Namespace,
    workspace_full: str,
    iteration: int,
    state: Any,
    context: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    from polaris.kernelone.events import emit_event, emit_llm_event
    from polaris.kernelone.runtime.shared_types import strip_ansi
    from polaris.kernelone.runtime.usage_metrics import UsageContext
    from polaris.kernelone.traceability.internal.safety import (
        safe_link,
        safe_register_node,
    )

    # Extract context
    requirements = context.get("requirements", "")
    plan_text = context.get("plan_text", "")
    gap_report = context.get("gap_report", "")
    last_qa = context.get("last_qa", "")
    last_tasks = context.get("last_tasks")
    director_result = context.get("director_result")
    pm_state = context.get("pm_state", {})
    docs_stage = context.get("docs_stage")
    run_id = context.get("run_id", f"pm-{iteration:05d}")
    start_timestamp = context.get("start_timestamp", "")
    run_events = context.get("run_events", "")
    dialogue_full = context.get("dialogue_full", "")
    pm_last_full = context.get("pm_last_full", "")
    pm_llm_events_full = context.get("pm_llm_events_full", "")
    pm_state_full = context.get("pm_state_full", "")
    resumed_from_manual = context.get("resumed_from_manual", False)
    resumed_payload = context.get("resumed_payload")
    pm_invoke_port = get_pm_invoke_port()

    # Determine backend
    backend = getattr(args, "pm_backend", "ollama")
    backend_llm_cfg = getattr(args, "_backend_llm_cfg", None)

    backend_label = backend
    if backend_llm_cfg is not None:
        backend_label = f"{backend}:{backend_llm_cfg.provider_id}"

    emit_llm_event(
        pm_llm_events_full,
        event="iteration",
        role="pm",
        run_id=run_id,
        iteration=iteration,
        source="system",
        data={
            "iteration": iteration,
            "timestamp": start_timestamp,
            "backend": backend_label,
            "stage": "started",
        },
    )

    quality_mode = _resolve_pm_task_quality_mode()
    strict_quality = quality_mode == "strict"
    max_quality_attempts = 1 if resumed_from_manual else 1 + _resolve_pm_task_quality_retries()

    os.environ["POLARIS_CONTEXT_ROOT"] = workspace_full

    prompt = ""
    retry_feedback: dict[str, Any] | None = None
    last_normalized: dict[str, Any] = {}
    last_quality: dict[str, Any] = {}

    if resumed_from_manual and isinstance(resumed_payload, dict):
        output = json.dumps(resumed_payload, ensure_ascii=False)
    else:
        prompt = pm_invoke_port.build_prompt(
            requirements,
            plan_text,
            gap_report,
            last_qa,
            last_tasks,
            director_result,
            pm_state,
            iteration=iteration,
            run_id=run_id,
            events_path=run_events,
            workspace_root=workspace_full,
        )

        usage_ctx = UsageContext(
            run_id=run_id,
            task_id="",
            phase="planning",
            mode="pm",
            actor="PM",
        )

        if hasattr(state, "events_full"):
            state.events_full = run_events
        if hasattr(state, "ollama_full"):
            state.ollama_full = pm_last_full
        if hasattr(state, "timeout"):
            from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds

            state.timeout = normalize_timeout_seconds(
                getattr(args, "timeout", None),
                default=0,
            )

    for quality_attempt in range(1, max_quality_attempts + 1):
        if resumed_from_manual and isinstance(resumed_payload, dict):
            output = json.dumps(resumed_payload, ensure_ascii=False)
        else:
            invoke_prompt = prompt
            if retry_feedback is not None:
                invoke_prompt = _build_pm_quality_retry_prompt(
                    base_prompt=prompt,
                    previous_payload=last_normalized,
                    quality_report=retry_feedback,
                )
            try:
                output = pm_invoke_port.invoke(
                    state,
                    invoke_prompt,
                    backend,
                    args,
                    usage_ctx=usage_ctx,
                )
            except (RuntimeError, ValueError) as exc:
                error = str(exc or "").strip() or "PM backend invoke failed"
                _handle_invoke_error(
                    error=error,
                    run_events=run_events,
                    dialogue_full=dialogue_full,
                    run_id=run_id,
                    iteration=iteration,
                    workspace_full=workspace_full,
                    pm_state=pm_state,
                    pm_state_full=pm_state_full,
                    backend_label=backend_label,
                    start_timestamp=start_timestamp,
                    pm_llm_events_full=pm_llm_events_full,
                )
                fallback = {
                    "schema_version": 2,
                    "run_id": run_id,
                    "pm_iteration": iteration,
                    "timestamp": start_timestamp,
                    "overall_goal": "planning_failed",
                    "focus": "planning_failed",
                    "tasks": [],
                    "notes": error,
                    "schema_warnings": ["PM invoke failed"],
                    "schema_warning_count": 1,
                }
                return 1, fallback

        if resumed_from_manual and isinstance(resumed_payload, dict):
            normalized = dict(resumed_payload)
        else:
            try:
                payload = json.loads(strip_ansi(output))
            except (RuntimeError, ValueError):
                payload = pm_invoke_port.extract_json(output)
                if payload is None and _looks_like_tool_call_output(output) and usage_ctx is not None:
                    retry_prompt = _build_pm_json_retry_prompt(output)
                    try:
                        retry_output = pm_invoke_port.invoke(
                            state,
                            retry_prompt,
                            backend,
                            args,
                            usage_ctx=usage_ctx,
                        )
                        retry_payload = pm_invoke_port.extract_json(retry_output)
                        if isinstance(retry_payload, dict):
                            payload = retry_payload
                    except (RuntimeError, ValueError):
                        payload = None
                if payload is None:
                    payload = {
                        "focus": "parse_failed",
                        "tasks": [],
                        "notes": "PM JSON parse failed.",
                    }
            normalized = normalize_pm_payload(payload, iteration, start_timestamp)

        _migrate_tasks_in_place(normalized if isinstance(normalized, dict) else {})

        raw_normalized_tasks = normalized.get("tasks")
        normalized_tasks: list[Any] = raw_normalized_tasks if isinstance(raw_normalized_tasks, list) else []
        normalized_tasks = sorted(
            [task for task in normalized_tasks if isinstance(task, dict)],
            key=lambda task: normalize_priority(task.get("priority"), fallback=9999),
        )
        for task in normalized_tasks:
            task.setdefault("doc_id", run_id)
            task.setdefault("blueprint_id", None)
        normalized["tasks"] = normalized_tasks

        autofix_stats = autofix_pm_contract_for_quality(
            normalized,
            workspace_full=workspace_full,
        )
        if (
            sum(
                int(autofix_stats.get(key) or 0)
                for key in ("phases_added", "checklists_added", "deps_added", "acceptance_added", "descriptions_added")
            )
            > 0
        ):
            emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_contract_autofix_applied",
                refs={"run_id": run_id, "phase": "planning"},
                summary="PM contract auto-fixed missing execution fields before quality gate",
                ok=True,
                output=autofix_stats,
            )

        schema_warnings = collect_schema_warnings(normalized, workspace_full)
        quality_report = _evaluate_pm_task_quality(
            normalized,
            docs_stage=docs_stage if isinstance(docs_stage, dict) else {},
        )
        merged_warnings = []
        seen_warnings = set()
        for item in (
            list(schema_warnings)
            + [f"PM quality warning: {i}" for i in quality_report.get("warnings", [])]
            + [f"PM quality issue: {i}" for i in quality_report.get("critical_issues", [])]
        ):
            token = str(item or "").strip()
            if token and token not in seen_warnings:
                seen_warnings.add(token)
                merged_warnings.append(token)

        normalized["schema_warnings"] = merged_warnings
        normalized["schema_warning_count"] = len(merged_warnings)
        normalized["quality_gate"] = {
            "mode": quality_mode,
            "attempt": quality_attempt,
            "max_attempts": max_quality_attempts,
            "passed": bool(quality_report.get("ok")),
            "score": int(quality_report.get("score") or 0),
            "summary": str(quality_report.get("summary") or "").strip(),
            "critical_issue_count": len(quality_report.get("critical_issues") or []),
            "warning_count": len(quality_report.get("warnings") or []),
        }

        def _register_pm_traceability(payload: dict[str, Any]) -> None:
            trace_service = context.get("trace_service")
            if trace_service is None:
                return
            doc_node = safe_register_node(
                trace_service,
                node_kind="doc",
                role="pm",
                external_id=run_id,
                content=json.dumps(payload, ensure_ascii=False)[:2048],
            )
            tasks = payload.get("tasks") if isinstance(payload, dict) else []
            for task in tasks if isinstance(tasks, list) else []:
                task_id = str(task.get("id") or "").strip()
                if not task_id:
                    continue
                task_node = safe_register_node(
                    trace_service,
                    node_kind="task",
                    role="pm",
                    external_id=task_id,
                    content=json.dumps(task, ensure_ascii=False)[:1024],
                )
                if doc_node is not None and task_node is not None:
                    safe_link(trace_service, doc_node, task_node, "derives_from")

        if bool(quality_report.get("ok")) or quality_mode in {"off", "warn"}:
            _register_pm_traceability(normalized)
            return 0, normalized

        if _should_promote_pm_quality_candidate(normalized, quality_report, last_normalized, last_quality):
            last_normalized = dict(normalized)
            last_quality = dict(quality_report)

        retry_feedback = {
            "summary": str(quality_report.get("summary") or "").strip(),
            "critical_issues": list(quality_report.get("critical_issues") or [])[:8],
            "warnings": list(quality_report.get("warnings") or [])[:8],
        }

        emit_event(
            run_events,
            kind="status",
            actor="PM",
            name="pm_quality_gate_retry",
            refs={"run_id": run_id, "phase": "planning"},
            summary="PM quality gate failed; requesting regenerated task payload",
            ok=False,
            output={
                "attempt": quality_attempt,
                "max_attempts": max_quality_attempts,
                "quality_summary": str(quality_report.get("summary") or "").strip(),
                "critical_issues": list(quality_report.get("critical_issues") or [])[:5],
            },
            error="PM_TASK_QUALITY_FAILED",
        )

        if quality_attempt >= max_quality_attempts or resumed_from_manual:
            retained_normalized = dict(last_normalized) if last_normalized else dict(normalized)
            retained_normalized["quality_gate"]["passed"] = False
            _register_pm_traceability(retained_normalized)
            return 1 if strict_quality else 0, retained_normalized

    result_payload = last_normalized or fallback
    _register_pm_traceability(result_payload)
    return 1 if strict_quality else 0, result_payload


def _handle_invoke_error(
    *,
    error: str,
    run_events: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
    workspace_full: str,
    pm_state: dict[str, Any],
    pm_state_full: str,
    backend_label: str,
    start_timestamp: str,
    pm_llm_events_full: str,
) -> None:
    from polaris.kernelone.events import emit_dialogue, emit_event, emit_llm_event
    from polaris.kernelone.fs.text_ops import write_json_atomic

    emit_event(
        run_events,
        kind="status",
        actor="PM",
        name="planning_invoke_failed",
        refs={"run_id": run_id, "phase": "planning"},
        summary="PM planning invoke failed",
        ok=False,
        output={"backend": backend_label, "stage": "invoke"},
        error=error,
    )
    emit_dialogue(
        dialogue_full,
        speaker="PM",
        type="warning",
        text=f"PM planning invoke failed: {error}",
        summary="PM invoke failed",
        run_id=run_id,
        pm_iteration=iteration,
        refs={"phase": "planning"},
        meta={"error_code": "PM_LLM_INVOKE_FAILED"},
    )

    pm_state["last_pm_error_code"] = "PM_LLM_INVOKE_FAILED"
    pm_state["last_pm_error_detail"] = error
    pm_state["last_updated_ts"] = start_timestamp
    write_json_atomic(pm_state_full, pm_state)

    emit_llm_event(
        pm_llm_events_full,
        event="invoke_error",
        role="pm",
        run_id=run_id,
        iteration=iteration,
        source="system",
        data={
            "iteration": iteration,
            "timestamp": start_timestamp,
            "backend": backend_label,
            "error": error,
        },
    )
