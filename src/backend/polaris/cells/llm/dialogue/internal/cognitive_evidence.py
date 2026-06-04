from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

_MAX_CONTEXT_PROMPT_CHARS = 2400
_MAX_CONTEXT_META_CHARS = 500


def _get_cognitive_runtime_public_service() -> Any:
    from polaris.cells.factory.cognitive_runtime.public import get_cognitive_runtime_public_service

    return get_cognitive_runtime_public_service()


def _get_cognitive_runtime_commands() -> tuple[type, type]:
    from polaris.cells.factory.cognitive_runtime.public import (
        RecordRuntimeReceiptCommandV1,
        ResolveContextCommandV1,
    )

    return ResolveContextCommandV1, RecordRuntimeReceiptCommandV1


def _stable_session_id(*, mode: str, role: str, query: str) -> str:
    digest = hashlib.sha256(f"{mode}\0{role}\0{query}".encode()).hexdigest()[:16]
    return f"{mode}-{role}-{digest}"


def _compact_snapshot(snapshot: Any) -> dict[str, Any]:
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
        "rendered_prompt_excerpt": rendered_prompt[:_MAX_CONTEXT_META_CHARS],
    }


def resolve_llm_context(
    *,
    workspace: str,
    role: str,
    mode: str,
    query: str,
    step: int = 0,
    sources_enabled: tuple[str, ...] = ("runtime", "events", "contracts"),
    policy: dict[str, Any] | None = None,
    service_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Resolve Context OS evidence for an LLM dialogue turn."""
    evidence: dict[str, Any] = {
        "ok": False,
        "required": True,
        "role": role,
        "mode": mode,
        "run_id": mode,
        "session_id": _stable_session_id(mode=mode, role=role, query=query),
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
        resolve_context_command_type, _ = _get_cognitive_runtime_commands()
        service = service_factory() if service_factory is not None else _get_cognitive_runtime_public_service()
        result = service.resolve_context(
            resolve_context_command_type(
                workspace=workspace_value,
                role=role,
                query=str(query or "").strip() or mode,
                step=int(step or 0),
                run_id=mode,
                mode=mode,
                session_id=str(evidence["session_id"]),
                sources_enabled=sources_enabled,
                policy={
                    "source": f"llm.dialogue.{mode}",
                    "context_os_required": True,
                    **(policy or {}),
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
    snapshot_evidence = _compact_snapshot(snapshot)
    rendered_prompt = str(getattr(snapshot, "rendered_prompt", "") or "").strip() if snapshot is not None else ""
    if not rendered_prompt and snapshot_evidence["context_os_summary"]:
        rendered_prompt = json.dumps(snapshot_evidence["context_os_summary"], ensure_ascii=False, sort_keys=True)

    evidence.update(
        {
            "ok": True,
            "context_prompt": rendered_prompt[:_MAX_CONTEXT_PROMPT_CHARS],
            "snapshot": snapshot_evidence,
            "context_os_summary": snapshot_evidence["context_os_summary"],
            "source_refs": snapshot_evidence["source_refs"],
            "token_usage_estimate": snapshot_evidence["token_usage_estimate"],
        }
    )
    return evidence


def append_context_to_prompt(prompt: str, evidence: dict[str, Any]) -> str:
    context_prompt = str(evidence.get("context_prompt") or "").strip()
    if not bool(evidence.get("ok")) or not context_prompt:
        return prompt
    return (
        f"{prompt}\n\n"
        "Context OS grounding. Use this as factual project/runtime context; do not mention this section label:\n"
        f"{context_prompt}\n"
    )


def compact_evidence_for_meta(evidence: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {
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
    }
    if "receipt_id" in evidence:
        meta["receipt_id"] = str(evidence.get("receipt_id") or "").strip()
    if evidence.get("error_code"):
        meta["error_code"] = str(evidence.get("error_code") or "").strip()
        meta["error_message"] = str(evidence.get("error_message") or "").strip()
    return meta


def attach_cognitive_meta(result: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    raw_meta = result.get("meta")
    meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta["cognitive_runtime"] = compact_evidence_for_meta(evidence)
    result["meta"] = meta
    return result


def record_llm_cognitive_receipt(
    *,
    workspace: str,
    evidence: dict[str, Any],
    receipt_type: str,
    task_type: str,
    llm_ok: bool,
    output_length: int,
    provider_error: str = "",
    metadata: dict[str, Any] | None = None,
    service_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Record an LLM turn receipt and return updated cognitive evidence."""
    updated = dict(evidence)
    workspace_value = str(workspace or "").strip()
    if not workspace_value:
        updated["receipt_ok"] = False
        updated["receipt_error_code"] = "missing_workspace"
        return updated

    try:
        _, record_receipt_command_type = _get_cognitive_runtime_commands()
        service = service_factory() if service_factory is not None else _get_cognitive_runtime_public_service()
        result = service.record_runtime_receipt(
            record_receipt_command_type(
                workspace=workspace_value,
                receipt_type=receipt_type,
                session_id=str(updated.get("session_id") or ""),
                run_id=str(updated.get("run_id") or receipt_type),
                trace_refs=tuple(str(item) for item in updated.get("source_refs") or [] if str(item).strip()),
                payload={
                    "llm": {
                        "task_type": task_type,
                        "ok": bool(llm_ok),
                        "output_length": max(0, int(output_length or 0)),
                        "provider_error": str(provider_error or "").strip(),
                    },
                    "context_os": compact_evidence_for_meta(updated),
                    "metadata": dict(metadata or {}),
                },
                turn_envelope={
                    "role": str(updated.get("role") or "").strip(),
                    "mode": str(updated.get("mode") or "").strip(),
                    "task_type": task_type,
                },
            )
        )
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        updated["receipt_ok"] = False
        updated["receipt_error_code"] = "record_runtime_receipt_exception"
        updated["receipt_error_message"] = str(exc)
        return updated

    updated["receipt_ok"] = bool(getattr(result, "ok", False))
    if not updated["receipt_ok"]:
        updated["receipt_error_code"] = str(getattr(result, "error_code", "") or "record_runtime_receipt_failed")
        updated["receipt_error_message"] = str(getattr(result, "error_message", "") or "")
        return updated

    receipt = getattr(result, "receipt", None)
    receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
    if receipt_id:
        updated["receipt_id"] = receipt_id
    return updated
