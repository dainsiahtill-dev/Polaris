"""Task normalization and management for loop-pm."""

import hashlib
import json
import os
import re
from typing import Any

from polaris.delivery.cli.pm.config import (
    ACTIVE_TASK_STATUSES,
    DEFAULT_PM_SCHEMA_REQUIRED_FIELDS,
    PROJECT_ROOT,
    TERMINAL_TASK_STATUSES,
)
from polaris.delivery.cli.pm.qa_auditor import normalize_qa_contract
from polaris.delivery.cli.pm.task_helpers import (
    compute_task_fingerprint,
    generate_task_id,
    normalize_assigned_to,
    normalize_priority,
    normalize_required_evidence,
)
from polaris.delivery.cli.pm.task_special import (
    build_defect_followup_task,
    build_interrupt_task,
    build_resume_payload_from_last_tasks,
    consume_interrupt_task,
    extract_defect_ticket,
    validate_ticket_fields as _validate_ticket_fields,
)
from polaris.delivery.cli.pm.task_splitting import (
    merge_director_tasks,
    persist_pm_payloads,
    split_director_tasks,
)
from polaris.delivery.cli.pm.utils import (
    _normalize_scope_list,
    normalize_path,
    normalize_path_list,
    normalize_str_list,
)
from polaris.kernelone.utils.time_utils import utc_now_str

_ACCEPTANCE_ANCHOR_RE = re.compile(
    r"(command|verify|assert|evidence|stdout|stderr|log|路径|命令|验证|断言|产物|证据)",
    re.IGNORECASE,
)
_ACCEPTANCE_COMMAND_LIKE_RE = re.compile(
    r"\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b",
    re.IGNORECASE,
)
_ACCEPTANCE_BACKTICK_RE = re.compile(r"`[^`]{2,}`")


def _resolve_pm_task_limit() -> int:
    """Resolve PM task normalization cap.

    Default allows richer decomposition while preventing unbounded payload size.
    Set POLARIS_PM_MAX_TASKS=0 to disable truncation.
    """
    raw = str(os.environ.get("POLARIS_PM_MAX_TASKS", "6") or "6").strip()
    try:
        value = int(raw)
    except (RuntimeError, ValueError):
        value = 6
    if value <= 0:
        return 0
    return min(max(value, 1), 20)


def normalize_engine_config(raw_config: Any) -> dict[str, Any]:
    """Normalize top-level engine execution config from PM payload."""
    if not isinstance(raw_config, dict):
        return {}

    normalized: dict[str, Any] = {}

    mode = str(raw_config.get("director_execution_mode") or "").strip().lower()
    if mode in ("single", "multi"):
        normalized["director_execution_mode"] = mode

    policy = str(raw_config.get("scheduling_policy") or "").strip().lower()
    if policy in ("fifo", "priority", "dag"):
        normalized["scheduling_policy"] = policy

    max_directors_raw = raw_config.get("max_directors")
    if max_directors_raw is not None:
        try:
            max_directors = int(max_directors_raw)
        except (RuntimeError, ValueError):
            max_directors = 0
        if max_directors > 0:
            normalized["max_directors"] = max_directors

    return normalized


def _normalize_optional_bool(value: Any) -> bool | None:
    """Normalize optional boolean value."""
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in ("true", "1", "yes", "on"):
        return True
    if token in ("false", "0", "no", "off"):
        return False
    return None


def _normalize_optional_positive_int(value: Any) -> int | None:
    """Normalize optional positive integer value."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except (RuntimeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _normalize_scope_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in ("exact", "exact_file", "exact_files", "file", "files"):
        return "exact_files"
    if token in ("module", "directory", "dir", "scope", "scoped", "auto"):
        return "module"
    return "module"


def _normalize_phase_hint(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in (
        "bootstrap",
        "scaffold",
        "core",
        "implementation",
        "integration",
        "verification",
        "qa",
        "polish",
    ):
        return token
    return ""


def _normalize_task_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        serialized = json.dumps(value, ensure_ascii=False)
        restored = json.loads(serialized)
        return restored if isinstance(restored, dict) else {}
    except (RuntimeError, ValueError):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
                sanitized[key] = raw_value
            elif isinstance(raw_value, (list, dict)):
                sanitized[key] = str(raw_value)
            else:
                sanitized[key] = str(raw_value)
        return sanitized


def _derive_scope_paths_from_target_files(target_files: list[str]) -> list[str]:
    scope_paths: list[str] = []
    for item in target_files:
        normalized = normalize_path(str(item or "").strip())
        if not normalized:
            continue
        directory = normalize_path(os.path.dirname(normalized).strip())
        if directory and directory not in scope_paths:
            scope_paths.append(directory)
            continue
        if normalized not in scope_paths:
            scope_paths.append(normalized)
    return scope_paths


def _normalize_acceptance_items(items: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in items:
        text = str(raw or "").strip()
        if not text:
            continue
        if _ACCEPTANCE_ANCHOR_RE.search(text) or _ACCEPTANCE_BACKTICK_RE.search(text):
            normalized.append(text)
            continue
        if _ACCEPTANCE_COMMAND_LIKE_RE.search(text):
            normalized.append(f"command: `{text}`")
            continue
        normalized.append(f"verify: {text}")
    return normalized


def normalize_task_status(value: Any) -> str:
    """Normalize task status value."""
    token = str(value or "").strip().lower()
    if token in ("todo", "to_do", "pending"):
        return "todo"
    if token in ("in_progress", "in-progress", "doing", "active"):
        return "in_progress"
    if token in ("review", "in_review"):
        return "review"
    if token in ("needs_continue", "need_continue", "continue", "retry_same_task"):
        return "needs_continue"
    if token in ("done", "success", "completed"):
        return "done"
    if token in ("failed", "fail", "error"):
        return "failed"
    if token in ("blocked", "block"):
        return "blocked"
    return "todo"


def normalize_director_result_status(value: Any) -> str:
    """Normalize director result status."""
    token = str(value or "").strip().lower()
    if token in ("success", "pass", "passed", "done"):
        return "done"
    if token in ("fail", "failed", "error"):
        return "failed"
    if token in ("blocked", "block"):
        return "blocked"
    if token in ("needs_continue", "need_continue", "continue", "deferred"):
        return "needs_continue"
    if token in ACTIVE_TASK_STATUSES:
        return token
    return "review"


def normalize_tasks(raw_tasks: Any, iteration: int) -> list[dict[str, Any]]:
    """Normalize raw tasks list."""
    tasks: list[dict[str, Any]] = []
    if not isinstance(raw_tasks, list):
        return tasks
    index = 1
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        goal = str(item.get("goal") or "").strip()
        description_text = str(item.get("description") or "").strip()
        if not title and not goal:
            continue

        task_id = generate_task_id(item, iteration, index)
        priority = normalize_priority(item.get("priority"), fallback=index)
        context_files = normalize_path_list(item.get("context_files") or item.get("files"))
        target_files = normalize_path_list(item.get("target_files") or item.get("files"))
        scope_paths = _normalize_scope_list(
            item.get("scope_paths") or item.get("scope") or item.get("module_scope") or item.get("write_scope")
        )
        scope_paths = list(dict.fromkeys(scope_paths))
        scope_mode = _normalize_scope_mode(item.get("scope_mode"))
        if not scope_paths and target_files:
            scope_paths = _derive_scope_paths_from_target_files(target_files)
        if scope_mode == "exact_files" and not target_files:
            scope_mode = "module"
        constraints = normalize_str_list(item.get("constraints"))
        acceptance = normalize_str_list(item.get("acceptance_criteria") or item.get("acceptance"))
        acceptance = _normalize_acceptance_items(acceptance)
        dependencies = normalize_str_list(item.get("dependencies") or item.get("deps") or item.get("depends_on"))
        phase_hint = _normalize_phase_hint(item.get("phase"))
        status = normalize_task_status(item.get("status"))

        if status not in ACTIVE_TASK_STATUSES and status not in TERMINAL_TASK_STATUSES:
            status = "todo"
        if status in TERMINAL_TASK_STATUSES:
            index += 1
            continue

        spec_value = item.get("spec")
        if not isinstance(spec_value, (str, dict, list)):
            spec_value = str(spec_value or "")
        if (isinstance(spec_value, str) and not spec_value.strip()) or (
            isinstance(spec_value, list) and not spec_value
        ):
            spec_value = f"Task spec: {goal or title}"
        elif isinstance(spec_value, dict) and not spec_value:
            spec_value = {"summary": goal or title}

        assigned_to = normalize_assigned_to(item.get("assigned_to"))
        required_evidence = normalize_required_evidence(item.get("required_evidence"), assigned_to=assigned_to)
        qa_contract = normalize_qa_contract(item.get("qa_contract"), task=item)
        stop_conditions = normalize_str_list(item.get("stop_conditions"))
        parallel_group = str(item.get("parallel_group") or "").strip()
        capability = str(item.get("capability") or "").strip()
        shardable = _normalize_optional_bool(item.get("shardable"))
        max_parallel_hint = _normalize_optional_positive_int(item.get("max_parallel_hint"))

        backlog_ref = str(item.get("backlog_ref") or "").strip()
        if len(backlog_ref) > 400:
            backlog_ref = backlog_ref[:400].rstrip()

        fingerprint = compute_task_fingerprint(item)

        task_payload: dict[str, Any] = {
            "id": task_id,
            "fingerprint": fingerprint,
            "priority": priority,
            "title": title,
            "goal": goal,
            "context_files": context_files,
            "target_files": target_files,
            "scope_paths": scope_paths,
            "scope_mode": scope_mode,
            "constraints": constraints,
            "acceptance": acceptance,
            "acceptance_criteria": acceptance,
            "assigned_to": assigned_to,
            "dependencies": dependencies,
            "depends_on": dependencies,
            "status": status,
            "spec": spec_value,
            "required_evidence": required_evidence if isinstance(required_evidence, dict) else None,
            "qa_contract": qa_contract if isinstance(qa_contract, dict) else None,
            "stop_conditions": stop_conditions,
            "backlog_ref": backlog_ref,
            "_order": index,
        }
        if parallel_group:
            task_payload["parallel_group"] = parallel_group
        if capability:
            task_payload["capability"] = capability
        if shardable is not None:
            task_payload["shardable"] = shardable
        if max_parallel_hint is not None:
            task_payload["max_parallel_hint"] = max_parallel_hint
        if phase_hint:
            task_payload["phase"] = phase_hint
        if description_text:
            task_payload["description"] = description_text

        metadata_payload = _normalize_task_metadata(item.get("metadata"))
        if metadata_payload:
            task_payload["metadata"] = metadata_payload

        execution_checklist = normalize_str_list(item.get("execution_checklist"))
        if execution_checklist:
            task_payload["execution_checklist"] = execution_checklist

        tasks.append(task_payload)
        index += 1

    tasks.sort(key=lambda task: (int(task.get("priority") or 0), int(task.get("_order") or 0)))
    for task in tasks:
        task.pop("_order", None)
    task_limit = _resolve_pm_task_limit()
    if task_limit <= 0:
        return tasks
    return tasks[:task_limit]


def normalize_pm_payload(raw_payload: dict[str, Any], iteration: int, timestamp: str) -> dict[str, Any]:
    """Normalize PM payload."""
    overall_goal = str(
        raw_payload.get("overall_goal") or raw_payload.get("focus") or "Advance global requirements"
    ).strip()
    focus = str(raw_payload.get("focus") or "").strip()
    notes = str(raw_payload.get("notes") or "").strip()
    tasks = normalize_tasks(raw_payload.get("tasks"), iteration)
    engine_config = normalize_engine_config(raw_payload.get("engine"))
    if tasks and "scheduling_policy" not in engine_config:
        has_dag_hints = any(
            bool(normalize_str_list(task.get("depends_on") or task.get("dependencies")))
            or bool(str(task.get("phase") or "").strip())
            for task in tasks
            if isinstance(task, dict)
        )
        if has_dag_hints:
            engine_config["scheduling_policy"] = "dag"
    run_id = f"pm-{iteration:05d}"
    normalized_payload: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "pm_iteration": iteration,
        "timestamp": timestamp,
        "overall_goal": overall_goal,
        "focus": focus,
        "tasks": tasks,
        "notes": notes,
    }
    if engine_config:
        normalized_payload["engine"] = engine_config
    return normalized_payload


def _migrate_tasks_in_place(payload: dict[str, Any]) -> None:
    """Migrate tasks in place for backward compatibility."""
    if not isinstance(payload, dict):
        return
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return
    for task in tasks:
        if not isinstance(task, dict):
            continue
        backlog_ref = str(task.get("backlog_ref") or "").strip()
        task["backlog_ref"] = backlog_ref if backlog_ref else ""

        status = normalize_task_status(task.get("status"))
        if status in ("failed", "blocked"):
            task.setdefault("error_code", "")
            task.setdefault("failure_detail", "")
            task.setdefault("failed_at", "")
        else:
            task.pop("error_code", None)
            task.pop("failure_detail", None)
            task.pop("failed_at", None)


def apply_task_status_updates(
    tasks: list[dict[str, Any]],
    status_updates: dict[str, str],
    failure_info: dict[str, dict[str, str]] | None = None,
) -> None:
    """Update task statuses in-place."""
    if not isinstance(tasks, list) or not isinstance(status_updates, dict):
        return
    updates = {str(k).strip(): normalize_task_status(v) for k, v in status_updates.items() if str(k).strip()}
    if not updates:
        return
    failure_info = failure_info or {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id or task_id not in updates:
            continue
        new_status = updates[task_id]
        task["status"] = new_status
        if new_status in TERMINAL_TASK_STATUSES and new_status != "done":
            info = failure_info.get(task_id) or {}
            if info.get("error_code"):
                task["error_code"] = info["error_code"]
            if info.get("failure_detail"):
                task["failure_detail"] = info["failure_detail"]
            if "failed_at" not in task or info:
                task["failed_at"] = utc_now_str()
        elif new_status in {"done", "needs_continue"}:
            task.pop("error_code", None)
            task.pop("failure_detail", None)
            task.pop("failed_at", None)


def collect_schema_warnings(normalized_payload: dict[str, Any], workspace_full: str) -> list[str]:
    """Collect schema warnings for tasks."""
    warnings: list[str] = []
    required_fields = _load_pm_schema_required_fields(workspace_full)
    tasks = normalized_payload.get("tasks") if isinstance(normalized_payload, dict) else []
    if not isinstance(tasks, list):
        return ["pm_tasks payload invalid: tasks is not a list"]
    for idx, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            warnings.append(f"task[{idx}] invalid: not an object")
            continue
        task_id = str(task.get("id") or f"TASK-{idx}")
        allow_empty_fields = {"dependencies", "depends_on", "spec"}
        for field in required_fields:
            value = task.get(field)
            missing = value is None
            if isinstance(value, str):
                if field not in allow_empty_fields:
                    missing = missing or (not value.strip())
            elif (isinstance(value, (list, dict))) and field not in allow_empty_fields:
                missing = missing or (len(value) == 0)
            if missing:
                warnings.append(f"{task_id}: missing required field '{field}'")
    return warnings


def _load_pm_schema_required_fields(workspace_full: str) -> list[str]:
    """Load required fields from schema file."""
    candidates = [
        os.path.join(workspace_full, "schema", "pm_tasks.schema.json"),
        os.path.join(
            os.path.dirname(os.path.dirname(PROJECT_ROOT)),
            "schema",
            "pm_tasks.schema.json",
        ),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                schema = json.load(handle)
            tasks_schema = schema.get("properties", {}).get("tasks", {}) if isinstance(schema, dict) else {}
            items_schema = tasks_schema.get("items", {}) if isinstance(tasks_schema, dict) else {}
            required = items_schema.get("required", []) if isinstance(items_schema, dict) else []
            parsed = [str(item).strip() for item in required if str(item).strip()]
            if parsed:
                return parsed
        except (RuntimeError, ValueError):
            continue
    return list(DEFAULT_PM_SCHEMA_REQUIRED_FIELDS)


def build_pm_spin_fingerprint(
    tasks: Any,
    director_status: str,
    completed_task_count: int,
) -> str:
    """Build a fingerprint for spin detection."""
    task_markers: list[str] = []
    if isinstance(tasks, list):
        for task in tasks[:5]:
            if not isinstance(task, dict):
                continue
            marker = str(task.get("fingerprint") or task.get("id") or task.get("title") or "").strip()
            if marker:
                task_markers.append(marker)
    payload = {
        "director_status": normalize_director_result_status(director_status),
        "completed_task_count": max(int(completed_task_count), 0),
        "task_markers": task_markers,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(serialized.encode("utf-8", errors="ignore")).hexdigest()


def list_task_dependencies(task: Any) -> list[str]:
    """Return normalized dependency IDs for one task payload."""
    if not isinstance(task, dict):
        return []
    return normalize_str_list(task.get("dependencies") or task.get("depends_on") or task.get("deps"))


def build_taskboard_sync_payload(tasks: Any) -> list[dict[str, Any]]:
    """
    Build a deterministic taskboard sync payload from PM tasks.

    Each item contains:
    - task_id: PM task id
    - title: task title/goal
    - dependencies: PM dependency ids
    - priority: normalized PM priority
    - metadata: compact execution metadata for board persistence
    """
    if not isinstance(tasks, list):
        return []
    payload: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        payload.append(
            {
                "task_id": task_id,
                "title": str(task.get("title") or task.get("goal") or "").strip(),
                "goal": str(task.get("goal") or "").strip(),
                "priority": int(normalize_priority(task.get("priority"), fallback=index)),
                "dependencies": list_task_dependencies(task),
                "metadata": {
                    "fingerprint": str(task.get("fingerprint") or "").strip(),
                    "scope_paths": normalize_path_list(task.get("scope_paths") or []),
                    "target_files": normalize_path_list(task.get("target_files") or []),
                    "assigned_to": normalize_assigned_to(task.get("assigned_to")),
                },
            }
        )
    return payload


__all__ = [
    "_migrate_tasks_in_place",
    "_validate_ticket_fields",
    "apply_task_status_updates",
    "build_defect_followup_task",
    "build_interrupt_task",
    "build_pm_spin_fingerprint",
    "build_resume_payload_from_last_tasks",
    "build_taskboard_sync_payload",
    "collect_schema_warnings",
    "consume_interrupt_task",
    "extract_defect_ticket",
    "list_task_dependencies",
    "merge_director_tasks",
    "normalize_director_result_status",
    "normalize_engine_config",
    "normalize_pm_payload",
    "normalize_task_status",
    "normalize_tasks",
    "persist_pm_payloads",
    "split_director_tasks",
]
