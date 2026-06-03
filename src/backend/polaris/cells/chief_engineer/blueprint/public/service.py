"""Stable public service exports for `chief_engineer.blueprint`."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..internal.blueprint_persistence import BlueprintPersistence
from ..internal.ce_consumer import CEConsumer
from ..internal.chief_engineer_agent import ChiefEngineerAgent
from ..internal.chief_engineer_preflight import run_pre_dispatch_chief_engineer
from ..internal.rollback_guard import create_rollback_guard
from .contracts import (
    ChiefEngineerBlueprintError,
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintResultV1,
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "task"


def _blueprint_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        token = ""
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(
                item.get("path")
                or item.get("file")
                or item.get("description")
                or item.get("text")
                or item.get("title")
                or item.get("name")
                or item.get("id")
                or item.get("value")
                or ""
            ).strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        rows = _string_list(value)
        if rows:
            return rows
    return []


def _task_payload_from_context(context: dict[str, Any]) -> dict[str, Any]:
    for key in ("task", "pm_task", "source_task", "contract_task"):
        nested = _mapping(context.get(key))
        if nested:
            return nested
    return {}


def _target_files_from_context(context: dict[str, Any]) -> list[str]:
    task_payload = _task_payload_from_context(context)
    for key in ("target_files", "scope_paths", "files", "affected_files"):
        rows = _first_string_list(context.get(key), task_payload.get(key))
        if rows:
            return rows
    return []


def _qa_acceptance_from_task(task_payload: dict[str, Any]) -> list[str]:
    qa_contract = _mapping(task_payload.get("qa_contract"))
    return _first_string_list(qa_contract.get("acceptance_criteria"), qa_contract.get("acceptance"))


def _blueprint_contract_fields(context: dict[str, Any]) -> dict[str, Any]:
    task_payload = _task_payload_from_context(context)
    acceptance_criteria = _first_string_list(
        context.get("acceptance_criteria"),
        context.get("acceptance"),
        task_payload.get("acceptance_criteria"),
        task_payload.get("acceptance"),
        _qa_acceptance_from_task(task_payload),
    )
    execution_checklist = _first_string_list(
        context.get("execution_checklist"),
        context.get("steps"),
        task_payload.get("execution_checklist"),
        task_payload.get("steps"),
    )
    scope_paths = _first_string_list(
        context.get("scope_paths"),
        context.get("scope"),
        task_payload.get("scope_paths"),
        task_payload.get("scope"),
    )
    dependencies = _first_string_list(
        context.get("dependencies"),
        context.get("depends_on"),
        context.get("blocked_by"),
        task_payload.get("dependencies"),
        task_payload.get("depends_on"),
        task_payload.get("blocked_by"),
    )
    risks = _first_string_list(context.get("risks"), task_payload.get("risks"))
    return {
        "task": task_payload,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "scope_paths": scope_paths,
        "dependencies": dependencies,
        "risks": risks,
    }


def _contract_completeness(
    *,
    target_files: list[str],
    acceptance_criteria: list[str],
    execution_checklist: list[str],
) -> dict[str, Any]:
    missing_fields: list[str] = []
    if not target_files:
        missing_fields.append("target_files")
    if not acceptance_criteria:
        missing_fields.append("acceptance_criteria")
    if not execution_checklist:
        missing_fields.append("execution_checklist")
    return {
        "handoff_ready": not missing_fields,
        "missing_fields": missing_fields,
        "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
    }


def _tuple_from_payload(value: Any) -> tuple[str, ...]:
    return tuple(_string_list(value))


def _latest_blueprint_for_task(
    persistence: BlueprintPersistence,
    *,
    task_id: str,
    run_id: str | None,
) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[str, str, dict[str, Any]]] = []
    for blueprint_id in persistence.list_all():
        payload = persistence.load(blueprint_id)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id") or "").strip() != task_id:
            continue
        payload_run_id = str(payload.get("run_id") or "").strip()
        if run_id and payload_run_id != run_id:
            continue
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
        matches.append((updated_at, blueprint_id, payload))
    if not matches:
        return None
    _updated_at, blueprint_id, payload = max(matches, key=lambda item: (item[0], item[1]))
    return blueprint_id, payload


def generate_task_blueprint(command: GenerateTaskBlueprintCommandV1) -> TaskBlueprintResultV1:
    """Generate and persist a task-level Chief Engineer blueprint."""

    now = _utc_now()
    blueprint_id = f"ce_{_safe_token(command.task_id)}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    context = dict(command.context)
    constraints = dict(command.constraints)
    contract_fields = _blueprint_contract_fields(context)
    target_files = _target_files_from_context(context)
    title = str(context.get("task_title") or context.get("title") or command.objective).strip()
    summary = f"Chief Engineer blueprint for {command.task_id}: {command.objective}"
    acceptance_criteria = list(contract_fields["acceptance_criteria"])
    execution_checklist = list(contract_fields["execution_checklist"])
    scope_paths = list(contract_fields["scope_paths"])
    dependencies = list(contract_fields["dependencies"])
    contract_completeness = _contract_completeness(
        target_files=target_files,
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
    )
    context.setdefault("acceptance_criteria", acceptance_criteria)
    context.setdefault("execution_checklist", execution_checklist)
    context.setdefault("target_files", target_files)
    context.setdefault("scope_paths", scope_paths)
    context.setdefault("dependencies", dependencies)
    recommendations = (
        "Validate PM acceptance criteria before Director execution.",
        "Keep implementation scope within the recorded target files.",
    )
    risks = tuple(contract_fields["risks"])
    payload: dict[str, Any] = {
        "schema_version": "chief_engineer.blueprint.v1",
        "role": "ChiefEngineer",
        "blueprint_id": blueprint_id,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "title": title,
        "objective": command.objective,
        "summary": summary,
        "status": "generated",
        "source": "chief_engineer.generate_task_blueprint",
        "target_files": target_files,
        "scope_paths": scope_paths,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "dependencies": dependencies,
        "constraints": constraints,
        "context": context,
        "pm_task": contract_fields["task"],
        "contract_completeness": contract_completeness,
        "handoff_ready": bool(contract_completeness["handoff_ready"]),
        "recommendations": list(recommendations),
        "risks": list(risks),
        "created_at": now,
        "updated_at": now,
    }

    BlueprintPersistence(command.workspace).save(blueprint_id, payload)
    return TaskBlueprintResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        status="generated",
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        summary=summary,
        recommendations=recommendations,
        risks=risks,
    )


def get_blueprint_status(query: GetBlueprintStatusQueryV1) -> TaskBlueprintResultV1:
    """Return the latest persisted Chief Engineer blueprint status for a task."""

    persistence = BlueprintPersistence(query.workspace, ensure_directory=False)
    match = _latest_blueprint_for_task(
        persistence,
        task_id=query.task_id,
        run_id=query.run_id,
    )
    if match is None:
        return TaskBlueprintResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            status="missing",
            summary="No Chief Engineer blueprint has been generated for this task.",
        )

    blueprint_id, payload = match
    status = str(payload.get("status") or "generated").strip() or "generated"
    return TaskBlueprintResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        status=status,
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        summary=str(payload.get("summary") or "").strip(),
        recommendations=_tuple_from_payload(payload.get("recommendations")),
        risks=_tuple_from_payload(payload.get("risks")),
    )


__all__ = [
    "CEConsumer",
    "ChiefEngineerAgent",
    "ChiefEngineerBlueprintError",
    "ChiefEngineerBlueprintErrorV1",
    "GenerateTaskBlueprintCommandV1",
    "GetBlueprintStatusQueryV1",
    "TaskBlueprintGeneratedEventV1",
    "TaskBlueprintResultV1",
    "create_rollback_guard",
    "generate_task_blueprint",
    "get_blueprint_status",
    "run_pre_dispatch_chief_engineer",
]
