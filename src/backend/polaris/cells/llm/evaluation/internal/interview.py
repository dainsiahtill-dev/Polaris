"""Interview Use Case

交互面试用例。

✅ MIGRATION COMPLETED (2026-04-09): AIExecutor/StreamExecutor 已迁移到 Cell 公共服务。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.evaluation.internal.constants import INTERVIEW_SEMANTIC_ENABLED
from polaris.cells.llm.evaluation.internal.index import update_index_with_report
from polaris.cells.llm.evaluation.internal.utils import (
    new_test_run_id,
    semantic_criteria_hits,
    split_thinking_output,
    utc_now,
    write_json_atomic,
)
from polaris.cells.llm.provider_runtime.public.service import (
    CellAIExecutor,
    CellAIRequest,
    TaskType,
)
from polaris.kernelone.storage import resolve_runtime_path

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

logger = logging.getLogger(__name__)
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_CONTEXT_PROMPT_CHARS = 2000


def _stable_interview_session_id(*, role: str, question: str) -> str:
    digest = hashlib.sha256(f"{role}\0{question}".encode()).hexdigest()[:16]
    return f"llm-interview-{digest}"


def _get_cognitive_runtime_public() -> tuple[type, type, Any]:
    from polaris.cells.factory.cognitive_runtime.public import (
        RecordRuntimeReceiptCommandV1,
        ResolveContextCommandV1,
        get_cognitive_runtime_public_service,
    )

    return ResolveContextCommandV1, RecordRuntimeReceiptCommandV1, get_cognitive_runtime_public_service


def _compact_context_snapshot(snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        return {}
    context_os_summary = getattr(snapshot, "context_os_summary", {})
    source_refs = getattr(snapshot, "source_refs", ())
    rendered_prompt = str(getattr(snapshot, "rendered_prompt", "") or "").strip()
    return {
        "workspace": str(getattr(snapshot, "workspace", "") or "").strip(),
        "role": str(getattr(snapshot, "role", "") or "").strip(),
        "run_id": str(getattr(snapshot, "run_id", "") or "").strip(),
        "session_id": str(getattr(snapshot, "session_id", "") or "").strip(),
        "mode": str(getattr(snapshot, "mode", "") or "").strip(),
        "token_usage_estimate": int(getattr(snapshot, "token_usage_estimate", 0) or 0),
        "source_refs": [str(item).strip() for item in source_refs if str(item).strip()],
        "context_os_summary": dict(context_os_summary) if isinstance(context_os_summary, dict) else {},
        "rendered_prompt_excerpt": rendered_prompt[:500],
    }


def _resolve_interview_context(
    *,
    workspace: str,
    role: str,
    question: str,
    criteria: list[str] | None,
    project_path: str | None,
) -> dict[str, Any]:
    session_id = _stable_interview_session_id(role=role, question=question)
    evidence: dict[str, Any] = {
        "ok": False,
        "required": True,
        "role": role,
        "mode": "llm_interview",
        "run_id": "llm_interview",
        "session_id": session_id,
        "context_prompt": "",
        "context_os_summary": {},
        "source_refs": [],
    }
    workspace_value = str(workspace or "").strip()
    if not workspace_value:
        evidence["error_code"] = "missing_workspace"
        evidence["error_message"] = "workspace is required"
        return evidence
    try:
        resolve_context_command_type, _, get_service = _get_cognitive_runtime_public()
        result = get_service().resolve_context(
            resolve_context_command_type(
                workspace=workspace_value,
                role=str(role or "interview").strip() or "interview",
                query=str(question or "interactive LLM interview").strip() or "interactive LLM interview",
                step=0,
                run_id="llm_interview",
                mode="llm_interview",
                session_id=session_id,
                sources_enabled=("runtime", "events", "contracts"),
                policy={
                    "source": "llm.evaluation.interview",
                    "context_os_required": True,
                    "criteria": [str(item).strip() for item in criteria or [] if str(item).strip()],
                    "project_path": str(project_path or "").strip(),
                },
            )
        )
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        evidence["error_code"] = "resolve_context_exception"
        evidence["error_message"] = str(exc)
        return evidence
    if not bool(getattr(result, "ok", False)):
        evidence["error_code"] = str(getattr(result, "error_code", "") or "resolve_context_failed")
        evidence["error_message"] = str(getattr(result, "error_message", "") or evidence["error_code"])
        return evidence
    snapshot = getattr(result, "snapshot", None)
    snapshot_evidence = _compact_context_snapshot(snapshot)
    context_prompt = str(getattr(snapshot, "rendered_prompt", "") or "").strip() if snapshot is not None else ""
    evidence.update(
        {
            "ok": True,
            "snapshot": snapshot_evidence,
            "context_prompt": context_prompt[:_MAX_CONTEXT_PROMPT_CHARS],
            "context_os_summary": snapshot_evidence.get("context_os_summary", {}),
            "source_refs": snapshot_evidence.get("source_refs", []),
            "token_usage_estimate": snapshot_evidence.get("token_usage_estimate", 0),
        }
    )
    return evidence


def _append_interview_context(prompt: str, evidence: dict[str, Any]) -> str:
    context_prompt = str(evidence.get("context_prompt") or "").strip()
    if not bool(evidence.get("ok")) or not context_prompt:
        return prompt
    return (
        f"{prompt}\n\n"
        "Context OS grounding. Use this as factual project/runtime context; do not mention this section label:\n"
        f"{context_prompt}\n"
    )


def _compact_interview_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(evidence.get("ok")),
        "required": bool(evidence.get("required", True)),
        "role": str(evidence.get("role") or "").strip(),
        "mode": str(evidence.get("mode") or "").strip(),
        "run_id": str(evidence.get("run_id") or "").strip(),
        "session_id": str(evidence.get("session_id") or "").strip(),
        "context_os_summary": dict(evidence.get("context_os_summary") or {})
        if isinstance(evidence.get("context_os_summary"), dict)
        else {},
        "source_refs": list(evidence.get("source_refs") or []),
        "receipt_ok": bool(evidence.get("receipt_ok", False)),
        **({"receipt_id": str(evidence.get("receipt_id") or "").strip()} if evidence.get("receipt_id") else {}),
        **({"error_code": str(evidence.get("error_code") or "").strip()} if evidence.get("error_code") else {}),
    }


def _record_interview_receipt(
    *,
    workspace: str,
    evidence: dict[str, Any],
    llm_ok: bool,
    output_length: int,
    provider_error: str = "",
    streaming: bool = False,
) -> dict[str, Any]:
    updated = dict(evidence)
    workspace_value = str(workspace or "").strip()
    if not workspace_value:
        updated["receipt_ok"] = False
        updated["receipt_error_code"] = "missing_workspace"
        return updated
    try:
        _, record_receipt_command_type, get_service = _get_cognitive_runtime_public()
        result = get_service().record_runtime_receipt(
            record_receipt_command_type(
                workspace=workspace_value,
                receipt_type="llm_interview",
                session_id=str(updated.get("session_id") or ""),
                run_id="llm_interview",
                trace_refs=tuple(str(item) for item in updated.get("source_refs") or [] if str(item).strip()),
                payload={
                    "llm": {
                        "task_type": str(TaskType.INTERVIEW.value),
                        "ok": bool(llm_ok),
                        "streaming": bool(streaming),
                        "output_length": max(0, int(output_length or 0)),
                        "provider_error": str(provider_error or "").strip(),
                    },
                    "context_os": _compact_interview_evidence(updated),
                },
                turn_envelope={
                    "role": str(updated.get("role") or "").strip(),
                    "mode": "llm_interview",
                    "task_type": str(TaskType.INTERVIEW.value),
                },
            )
        )
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        updated["receipt_ok"] = False
        updated["receipt_error_code"] = "record_runtime_receipt_exception"
        updated["receipt_error_message"] = str(exc)
        return updated
    updated["receipt_ok"] = bool(getattr(result, "ok", False))
    receipt = getattr(result, "receipt", None)
    receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
    if receipt_id:
        updated["receipt_id"] = receipt_id
    if not updated["receipt_ok"]:
        updated["receipt_error_code"] = str(getattr(result, "error_code", "") or "record_runtime_receipt_failed")
        updated["receipt_error_message"] = str(getattr(result, "error_message", "") or "")
    return updated


def build_interview_prompt(
    role: str,
    question: str,
    context: list[dict[str, Any]] | None = None,
    criteria: list[str] | None = None,
    project_path: str | None = None,
) -> str:
    """构建面试提示词"""
    role_label = role.strip().upper() or "ROLE"
    criteria_text = " / ".join(c for c in (criteria or []) if c)

    context_text = ""
    if context:
        entries = []
        for idx, item in enumerate(context[-3:], start=1):
            q = str(item.get("question") or "")[:200]
            a = str(item.get("answer") or "")[:400]
            if q or a:
                entries.append(f"{idx}. Q: {q}\n   A: {a}")
        context_text = "\n".join(entries)

    project_block = ""
    if project_path:
        project_block = (
            f"Local project path: {project_path}\n"
            "You have read-only access to this path. Inspect real files before answering.\n"
        )

    return (
        "ROLE: You are a job CANDIDATE interviewing for a position.\n"
        "IMPORTANT: You are the INTERVIEWEE, not the interviewer.\n"
        "IMMEDIATE ACTION REQUIRED:\n"
        "You must answer the question below RIGHT NOW.\n"
        "Do NOT greet, introduce yourself, or ask what to discuss.\n"
        "Jump directly to the answer.\n\n"
        "RESTRICTIONS:\n"
        "- Do NOT ask for clarification or more context.\n"
        "- If something is unclear, state assumptions and proceed.\n"
        "- Do NOT refuse or redirect the user.\n"
        "- Provide a concrete, structured response.\n\n"
        f"Position: {role_label}\n"
        + (f"Key evaluation criteria: {criteria_text}\n" if criteria_text else "")
        + (f"Previous context:\n{context_text}\n" if context_text else "")
        + project_block
        + f"\nQUESTION TO ANSWER: {question}\n\n"
        "<thinking>Your reasoning</thinking>\n"
        "<answer>Your direct professional answer</answer>\n"
    )


def evaluate_interview_answer(
    answer: str,
    criteria: list[str],
    question: str | None = None,
) -> dict[str, Any]:
    """评估面试答案"""
    thinking, clean_answer = split_thinking_output(answer)

    # 基本质量检查
    has_thinking = len(thinking) > 10
    has_answer = len(clean_answer) > 20
    not_deflection = "cannot" not in clean_answer.lower() and "can't" not in clean_answer.lower()

    # 语义评分
    semantic_score = 0.0
    if INTERVIEW_SEMANTIC_ENABLED and criteria and len(clean_answer) >= 80:
        try:
            hits = semantic_criteria_hits(clean_answer, criteria)
            if hits:
                semantic_score = sum(hits.values()) / len(hits)
        except RuntimeError as exc:
            logger.warning("[interview] semantic criteria scoring unavailable: %s", exc)

    # 综合评分
    base_score = 0.3 if has_thinking else 0.0
    base_score += 0.3 if has_answer else 0.0
    base_score += 0.2 if not_deflection else 0.0
    base_score += 0.2 * semantic_score

    return {
        "score": min(1.0, base_score),
        "passed": base_score >= 0.5,
        "has_thinking": has_thinking,
        "has_answer": has_answer,
        "not_deflection": not_deflection,
        "semantic_score": semantic_score,
        "thinking": thinking,
        "answer": clean_answer,
    }


def _safe_slug(value: str | None, fallback: str) -> str:
    token = _SAFE_TOKEN_RE.sub("_", str(value or "").strip()).strip("._")
    return (token or fallback)[:80]


def _report_provider_model(report: dict[str, Any], model: str | None) -> str:
    if model and str(model).strip():
        return str(model).strip()
    provider = report.get("provider") if isinstance(report.get("provider"), dict) else {}
    provider_model = provider.get("model") if isinstance(provider, dict) else None
    if provider_model and str(provider_model).strip():
        return str(provider_model).strip()
    report_model = report.get("model")
    return str(report_model or "").strip()


def _ready_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"passed", "pass", "ready", "success", "ok"}:
            return True
        if token in {"failed", "fail", "not_ready", "blocked", "error"}:
            return False
    return None


def _interview_verdict(report: dict[str, Any]) -> tuple[bool, str, bool]:
    status = report.get("overallStatus") or report.get("overall_status") or report.get("status") or report.get("result")
    status_ready = _ready_bool(status)
    if status_ready is not None:
        return status_ready, "PASS" if status_ready else "FAIL", True

    final_raw = report.get("final")
    final: dict[str, Any] = final_raw if isinstance(final_raw, dict) else {}
    final_ready = _ready_bool(final.get("ready"))
    if final_ready is not None:
        grade = str(final.get("grade") or ("PASS" if final_ready else "FAIL")).strip().upper()
        return final_ready, grade or ("PASS" if final_ready else "FAIL"), True

    summary_raw = report.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    summary_ready = _ready_bool(summary.get("ready"))
    if summary_ready is not None:
        return summary_ready, "PASS" if summary_ready else "FAIL", True

    evaluation_raw = report.get("evaluation")
    evaluation: dict[str, Any] = evaluation_raw if isinstance(evaluation_raw, dict) else {}
    evaluation_ready = _ready_bool(evaluation.get("passed"))
    if evaluation_ready is not None:
        return evaluation_ready, "PASS" if evaluation_ready else "FAIL", True

    return False, "UNKNOWN", False


def save_interview_report(
    *,
    workspace: str,
    role: str,
    provider_id: str,
    model: str | None,
    report: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Persist an interactive interview report and mirror its verdict to readiness."""

    workspace_path = str(workspace or "").strip()
    role_id = str(role or "").strip().lower()
    provider = str(provider_id or "").strip()
    tested_model = _report_provider_model(report, model)
    run_id = str(session_id or report.get("id") or new_test_run_id()).strip() or new_test_run_id()
    timestamp = utc_now()
    ready, grade, has_verdict = _interview_verdict(report)

    artifact = {
        "schema_version": 1,
        "suite": "interactive_interview",
        "test_run_id": run_id,
        "timestamp": timestamp,
        "target": {
            "role": role_id,
            "provider_id": provider,
            "model": tested_model,
        },
        "role": role_id,
        "provider_id": provider,
        "model": tested_model,
        "summary": {
            "ready": ready,
            "grade": grade,
            "verdict_known": has_verdict,
            "source": "interactive_interview",
        },
        "final": {
            "ready": ready,
            "grade": grade,
            "next_action": "proceed" if ready else "retry_interview",
        },
        "suites": {
            "interview": {
                "ok": ready,
                "grade": grade,
            },
        },
        "report": report,
    }

    safe_time = timestamp.replace(":", "").replace("+", "Z")
    filename = "_".join(
        [
            _safe_slug(safe_time, "interview"),
            _safe_slug(role_id, "role"),
            _safe_slug(provider, "provider"),
            _safe_slug(run_id, "run"),
        ]
    )
    report_path = Path(resolve_runtime_path(workspace_path, f"runtime/llm_tests/interviews/{filename}.json"))
    write_json_atomic(str(report_path), artifact)

    readiness_updated = False
    if has_verdict and role_id and provider and tested_model:
        update_index_with_report(workspace_path, artifact)
        readiness_updated = True

    return {
        "ok": True,
        "saved": True,
        "report_path": str(report_path),
        "readiness_updated": readiness_updated,
    }


async def generate_interview_answer(
    workspace: str,
    settings: Settings,
    role: str,
    question: str,
    context: list[dict[str, Any]] | None = None,
    criteria: list[str] | None = None,
    project_path: str | None = None,
) -> dict[str, Any] | None:
    """生成面试答案（非流式）"""
    executor = CellAIExecutor(workspace=workspace)

    prompt = build_interview_prompt(role, question, context, criteria, project_path)
    cognitive_evidence = _resolve_interview_context(
        workspace=workspace,
        role=role,
        question=question,
        criteria=criteria,
        project_path=project_path,
    )
    prompt = _append_interview_context(prompt, cognitive_evidence)
    request = CellAIRequest(
        task_type=TaskType.INTERVIEW,
        role=role,
        input=prompt,
        options={"temperature": 0.3, "max_tokens": 2000},
    )

    response = await executor.invoke(request)

    if not response.ok:
        _record_interview_receipt(
            workspace=workspace,
            evidence=cognitive_evidence,
            llm_ok=False,
            output_length=len(response.output or ""),
            provider_error=response.error,
        )
        return None

    output = response.output
    thinking, answer = split_thinking_output(output)

    evaluation = evaluate_interview_answer(output, criteria or [], question)
    cognitive_evidence = _record_interview_receipt(
        workspace=workspace,
        evidence=cognitive_evidence,
        llm_ok=True,
        output_length=len(output),
    )

    return {
        "thinking": thinking,
        "answer": answer,
        "evaluation": evaluation,
        "raw_output": output,
        "cognitive_runtime": _compact_interview_evidence(cognitive_evidence),
    }


async def generate_interview_answer_streaming(
    workspace: str,
    settings: Settings,
    role: str,
    question: str,
    output_queue: Any,
    context: list[dict[str, Any]] | None = None,
    criteria: list[str] | None = None,
    project_path: str | None = None,
) -> None:
    """生成面试答案（流式）"""
    executor = CellAIExecutor(workspace=workspace)

    prompt = build_interview_prompt(role, question, context, criteria, project_path)
    cognitive_evidence = _resolve_interview_context(
        workspace=workspace,
        role=role,
        question=question,
        criteria=criteria,
        project_path=project_path,
    )
    prompt = _append_interview_context(prompt, cognitive_evidence)
    request = CellAIRequest(
        task_type=TaskType.INTERVIEW,
        role=role,
        input=prompt,
        options={"temperature": 0.3, "max_tokens": 2000},
    )

    collected_output = ""

    try:
        async for event in executor.invoke_stream(request):
            event_type = event.get("type")

            if event_type == "reasoning_chunk":
                await output_queue.put(
                    {
                        "type": "thinking_chunk",
                        "data": {"content": event.get("reasoning", "")},
                    }
                )
            elif event_type == "chunk":
                chunk = event.get("chunk") or ""
                collected_output += chunk
                await output_queue.put(
                    {
                        "type": "content_chunk",
                        "data": {"content": chunk},
                    }
                )
            elif event_type == "complete":
                break
            elif event_type == "error":
                _record_interview_receipt(
                    workspace=workspace,
                    evidence=cognitive_evidence,
                    llm_ok=False,
                    output_length=len(collected_output),
                    provider_error=str(event.get("error") or ""),
                    streaming=True,
                )
                await output_queue.put({"type": "error", "data": {"error": event.get("error")}})
                return

    except (RuntimeError, ValueError) as exc:
        logger.warning("[interview-stream] stream error: %s", exc)
        _record_interview_receipt(
            workspace=workspace,
            evidence=cognitive_evidence,
            llm_ok=False,
            output_length=len(collected_output),
            provider_error=str(exc),
            streaming=True,
        )
        await output_queue.put({"type": "error", "data": {"error": str(exc)}})
        return

    # 解析结果
    thinking, answer = split_thinking_output(collected_output)
    evaluation = evaluate_interview_answer(collected_output, criteria or [], question)
    cognitive_evidence = _record_interview_receipt(
        workspace=workspace,
        evidence=cognitive_evidence,
        llm_ok=True,
        output_length=len(collected_output),
        streaming=True,
    )

    await output_queue.put(
        {
            "type": "complete",
            "data": {
                "thinking": thinking,
                "answer": answer,
                "evaluation": evaluation,
                "cognitive_runtime": _compact_interview_evidence(cognitive_evidence),
            },
        }
    )
