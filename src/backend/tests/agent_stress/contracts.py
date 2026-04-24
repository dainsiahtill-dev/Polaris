"""tests.agent_stress 的正式接口合同辅助函数。

统一处理当前 Polaris 正式 HTTP 合同，避免各模块各自猜字段。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

DIRECTOR_COMPLETED_STATUSES = {"completed", "success", "done"}
DIRECTOR_FAILED_STATUSES = {"failed", "error", "cancelled", "blocked", "timeout"}
DIRECTOR_RUNNING_STATUSES = {"running", "claimed", "executing", "in_progress"}
GENERIC_FAILURE_POINTS = {"unknown", "failed", "cancelled", "completed", "pending"}

FACTORY_STAGE_ORDER = {
    "docs_generation": 0,
    "pm_planning": 1,
    "director_dispatch": 2,
    "quality_gate": 3,
}

FACTORY_PHASE_STAGE_ORDER = {
    "architect": 0,
    "planning": 1,
    "implementation": 2,
    "verification": 3,
    "qa_gate": 3,
    "handover": 3,
    "completed": 3,
    "failed": 3,
    "cancelled": 3,
}

FACTORY_ROLE_TO_STAGE = {
    "architect": "docs_generation",
    "pm": "pm_planning",
    "chief_engineer": "chief_engineer_review",
    "director": "director_dispatch",
    "qa": "quality_gate",
}

FACTORY_PHASE_TO_STAGE = {
    "architect": "docs_generation",
    "planning": "pm_planning",
    "analysis": "chief_engineer_review",
    "implementation": "director_dispatch",
    "verification": "quality_gate",
    "qa_gate": "quality_gate",
    "handover": "quality_gate",
}

FACTORY_FAILURE_HINTS = (
    ("docs generation", "docs_generation"),
    ("architect", "docs_generation"),
    ("pm planning", "pm_planning"),
    ("pm ", "pm_planning"),
    ("chief engineer", "chief_engineer_review"),
    ("tech review", "chief_engineer_review"),
    ("director dispatch", "director_dispatch"),
    ("director ", "director_dispatch"),
    ("quality gate", "quality_gate"),
    ("qa gate", "quality_gate"),
    ("qa ", "quality_gate"),
)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_status(value: Any) -> str:
    return normalize_text(value).lower()


def director_task_id(task_data: Mapping[str, Any]) -> str:
    return normalize_text(task_data.get("id"))


def director_task_claimed_by(task_data: Mapping[str, Any]) -> str:
    claimed_by = normalize_text(task_data.get("claimed_by"))
    if claimed_by:
        return claimed_by
    metadata = task_data.get("metadata")
    if isinstance(metadata, Mapping):
        return normalize_text(metadata.get("claimed_by"))
    return ""


def director_task_pm_task_id(task_data: Mapping[str, Any]) -> str:
    metadata = task_data.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return normalize_text(metadata.get("pm_task_id"))


def director_task_workflow_run_id(task_data: Mapping[str, Any]) -> str:
    top_level = normalize_text(task_data.get("workflow_run_id"))
    if top_level:
        return top_level
    metadata = task_data.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return normalize_text(metadata.get("workflow_run_id"))


def summarize_director_task_result(task_data: Mapping[str, Any]) -> str:
    result = task_data.get("result")
    if not isinstance(result, Mapping):
        return ""
    for key in ("summary", "message", "error", "output"):
        value = normalize_text(result.get(key))
        if value:
            return value
    return ""


def factory_failure_info(status_payload: Mapping[str, Any]) -> dict[str, Any]:
    failure = status_payload.get("failure")
    failure_dict = failure if isinstance(failure, Mapping) else {}
    detail = (
        normalize_text(failure_dict.get("detail"))
        or normalize_text(failure_dict.get("message"))
        or normalize_text(status_payload.get("detail"))
        or normalize_text(status_payload.get("message"))
    )
    code = normalize_text(failure_dict.get("code")) or "FACTORY_FAILED"
    raw_phase = (
        normalize_status(failure_dict.get("phase"))
        or normalize_status(status_payload.get("current_stage"))
        or normalize_status(status_payload.get("phase"))
        or "unknown"
    )
    stage = infer_factory_failure_stage(status_payload, raw_phase=raw_phase, detail=detail)
    failure_point = stage or raw_phase
    return {
        "code": code,
        "detail": detail,
        "phase": raw_phase,
        "stage": stage,
        "failure_point": failure_point,
        "recoverable": bool(failure_dict.get("recoverable")),
        "raw": dict(failure_dict),
    }


def resolve_factory_stage_index(status_payload: Mapping[str, Any]) -> int | None:
    current_stage = normalize_status(status_payload.get("current_stage"))
    if current_stage in FACTORY_STAGE_ORDER:
        return FACTORY_STAGE_ORDER[current_stage]
    phase = normalize_status(status_payload.get("phase"))
    if phase in FACTORY_PHASE_STAGE_ORDER:
        return FACTORY_PHASE_STAGE_ORDER[phase]
    return None


def factory_gate_name(gate: Mapping[str, Any]) -> str:
    return normalize_text(gate.get("gate_name")) or normalize_text(gate.get("name"))


def is_generic_failure_point(value: Any) -> bool:
    return normalize_status(value) in GENERIC_FAILURE_POINTS


def infer_factory_failure_stage(
    status_payload: Mapping[str, Any],
    *,
    raw_phase: str = "",
    detail: str = "",
) -> str:
    failure = status_payload.get("failure")
    failure_dict = failure if isinstance(failure, Mapping) else {}
    detail_text = normalize_status(detail or failure_dict.get("detail") or status_payload.get("detail"))

    direct_candidates = [
        normalize_status(failure_dict.get("stage")),
        normalize_status(status_payload.get("current_stage")),
    ]
    for candidate in direct_candidates:
        if candidate in FACTORY_STAGE_ORDER:
            return candidate

    roles = status_payload.get("roles")
    if isinstance(roles, Mapping):
        for role_name, role_status in roles.items():
            if not isinstance(role_status, Mapping):
                continue
            if normalize_status(role_status.get("status")) != "failed":
                continue
            current_task = normalize_status(role_status.get("current_task"))
            if current_task in FACTORY_STAGE_ORDER:
                return current_task
            mapped_stage = FACTORY_ROLE_TO_STAGE.get(normalize_status(role_name))
            if mapped_stage:
                return mapped_stage

    for hint, stage_name in FACTORY_FAILURE_HINTS:
        if hint in detail_text:
            return stage_name

    normalized_phase = normalize_status(raw_phase or failure_dict.get("phase") or status_payload.get("phase"))
    mapped_phase = FACTORY_PHASE_TO_STAGE.get(normalized_phase)
    if mapped_phase:
        return mapped_phase

    last_successful_stage = normalize_status(status_payload.get("last_successful_stage"))
    if last_successful_stage in FACTORY_STAGE_ORDER:
        next_stage = _next_factory_stage(last_successful_stage)
        if next_stage:
            return next_stage

    return ""


def factory_failure_evidence(status_payload: Mapping[str, Any]) -> str:
    failure_info = factory_failure_info(status_payload)
    evidence_lines: list[str] = []
    detail = normalize_text(failure_info.get("detail"))
    if detail:
        evidence_lines.append(detail)

    roles = status_payload.get("roles")
    if isinstance(roles, Mapping):
        for role_name, role_status in roles.items():
            if not isinstance(role_status, Mapping):
                continue
            if normalize_status(role_status.get("status")) != "failed":
                continue
            role_detail = normalize_text(role_status.get("detail"))
            if role_detail:
                evidence_lines.append(f"{normalize_text(role_name)}: {role_detail}")

    gates = status_payload.get("gates")
    if isinstance(gates, list):
        for gate in gates:
            if not isinstance(gate, Mapping):
                continue
            if normalize_status(gate.get("status")) != "failed":
                continue
            gate_name = factory_gate_name(gate) or "gate"
            gate_message = normalize_text(gate.get("message"))
            if gate_message:
                evidence_lines.append(f"{gate_name}: {gate_message}")

    if not evidence_lines:
        failure_point = normalize_text(failure_info.get("failure_point"))
        code = normalize_text(failure_info.get("code"))
        if failure_point or code:
            evidence_lines.append(f"{failure_point or 'unknown'} [{code or 'FACTORY_FAILED'}]")

    unique_lines: list[str] = []
    seen: set[str] = set()
    for line in evidence_lines:
        if line in seen:
            continue
        seen.add(line)
        unique_lines.append(line)
    return "\n".join(unique_lines)


def _next_factory_stage(stage_name: str) -> str:
    stage = normalize_status(stage_name)
    if stage not in FACTORY_STAGE_ORDER:
        return ""
    current_index = FACTORY_STAGE_ORDER[stage]
    for candidate, candidate_index in FACTORY_STAGE_ORDER.items():
        if candidate_index == current_index + 1:
            return candidate
    return ""


def event_kind(event: Mapping[str, Any]) -> str:
    return normalize_status(event.get("type") or event.get("event_type"))


def event_timestamp(event: Mapping[str, Any]) -> str:
    return normalize_text(event.get("timestamp") or event.get("ts"))


def event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def event_identity(event: Mapping[str, Any]) -> str:
    event_id = normalize_text(event.get("event_id"))
    if event_id:
        return event_id
    canonical = {
        "kind": event_kind(event),
        "timestamp": event_timestamp(event),
        "message": normalize_text(event.get("message")),
        "stage": normalize_text(event.get("stage")),
        "payload": event_payload(event),
    }
    digest = hashlib.sha1(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"evt:{digest}"


def llm_event_identity(event: Mapping[str, Any]) -> str:
    call_id = normalize_text(event.get("call_id"))
    kind = event_kind(event)
    timestamp = event_timestamp(event)
    task_id = normalize_text(event.get("task_id"))
    if call_id:
        return f"llm:{call_id}:{kind}:{timestamp or task_id}"
    return f"llm:{event_identity(event)}"


def llm_event_success(event: Mapping[str, Any]) -> bool:
    return event_kind(event) not in {"llm_error", "call_error"}
