"""Task normalization helpers for loop-pm."""

import hashlib
from typing import Any

from polaris.delivery.cli.pm.config import SUPPORTED_ASSIGNEES
from polaris.delivery.cli.pm.utils import _normalize_scope_list, normalize_path_list, normalize_str_list
from polaris.kernelone.runtime.shared_types import normalize_path


def normalize_priority(value: Any, fallback: int) -> int:
    """Normalize priority value."""
    from polaris.delivery.cli.pm.config import PRIORITY_ALIASES

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    token = str(value or "").strip().lower()
    if not token:
        return fallback
    if token.startswith("p") and token[1:].isdigit():
        return int(token[1:])
    if token in PRIORITY_ALIASES:
        return PRIORITY_ALIASES[token]
    try:
        return int(token)
    except (RuntimeError, ValueError):
        return fallback


def normalize_assigned_to(value: Any) -> str:
    """Normalize assigned_to value to supported assignee."""
    candidate = str(value or "").strip()
    if candidate in SUPPORTED_ASSIGNEES:
        return candidate
    lowered = candidate.lower()
    lowered_compact = lowered.replace("-", "").replace("_", "").replace(" ", "")
    alias_map = {
        "architect": "Architect",
        "chiefengineer": "ChiefEngineer",
        "chief_engineer": "ChiefEngineer",
        "chief-engineer": "ChiefEngineer",
        "工部尚书": "ChiefEngineer",
        "pm": "PM",
        "director": "Director",
        "auditor": "Auditor",
        "policygate": "PolicyGate",
        "policy_gate": "PolicyGate",
        "finops": "FinOps",
    }
    mapped = alias_map.get(lowered) or alias_map.get(lowered_compact)
    if mapped:
        return mapped
    return "Director"


def normalize_required_evidence(value: Any, *, assigned_to: str) -> Any:
    """Normalize required_evidence field."""
    if value is None:
        return None
    if isinstance(value, list):
        validation_paths = normalize_str_list(value)
        return {"validation_paths": validation_paths} if validation_paths else None
    if not isinstance(value, dict):
        return None

    normalized: dict[str, Any] = dict(value)
    validation_paths = normalize_path_list(
        normalized.get("validation_paths") or normalized.get("artifacts") or normalized.get("evidence_paths")
    )

    # Backward compatibility: fold legacy must_read entries into validation paths.
    legacy_must_read = normalized.get("must_read")
    if isinstance(legacy_must_read, list):
        for entry in legacy_must_read:
            rel = ""
            if isinstance(entry, str):
                rel = normalize_path(entry)
            elif isinstance(entry, dict):
                rel = normalize_path(str(entry.get("file") or entry.get("path") or ""))
            if rel and rel not in validation_paths:
                validation_paths.append(rel)

    # New contract: PM must not instruct Director read/search via required_evidence.
    normalized.pop("must_read", None)
    normalized.pop("must_find_calls", None)

    if validation_paths:
        normalized["validation_paths"] = validation_paths
    else:
        normalized.pop("validation_paths", None)

    # For Director tasks, required_evidence is validation metadata only.
    if assigned_to == "Director" and not normalized.get("validation_paths"):
        keep_keys = {"source", "source_task_id", "defect_ticket"}
        if not any(key in normalized for key in keep_keys):
            return None

    # Remove keys with empty/null values to keep payload compact.
    compact: dict[str, Any] = {}
    for key, item in normalized.items():
        if item is None:
            continue
        if isinstance(item, str) and not item.strip():
            continue
        if isinstance(item, list) and not item:
            continue
        compact[key] = item
    return compact or None


def compute_task_fingerprint(task: dict[str, Any]) -> str:
    """Compute fingerprint for a task."""
    title = str(task.get("title") or "").strip()
    goal = str(task.get("goal") or "").strip()
    target_files = normalize_path_list(task.get("target_files") or task.get("files"))
    scope_paths = _normalize_scope_list(task.get("scope_paths") or task.get("scope") or task.get("module_scope"))
    scope_mode = str(task.get("scope_mode") or "").strip().lower()
    acceptance = normalize_str_list(task.get("acceptance_criteria") or task.get("acceptance"))

    fingerprint_source = "|".join(
        [
            title.lower(),
            goal.lower(),
            ",".join(sorted(target_files)).lower(),
            ",".join(sorted(scope_paths)).lower(),
            scope_mode,
            ",".join(sorted(acceptance)).lower(),
        ]
    ).strip()
    return hashlib.sha1(fingerprint_source.encode("utf-8")).hexdigest() if fingerprint_source else ""


def generate_task_id(task: dict[str, Any], iteration: int, index: int) -> str:
    """Generate task ID."""
    fingerprint = compute_task_fingerprint(task)
    task_id = str(task.get("id") or "")
    if not task_id:
        task_id = f"PM-{fingerprint[:8] or f'{iteration:04d}-{index}'}"
    return task_id


def _auto_assign_role(task: dict[str, Any]) -> str:
    """Auto-assign a role based on task content."""
    from polaris.delivery.cli.pm.config import (
        ARCHITECT_KEYWORDS,
        AUDIT_KEYWORDS,
        CHIEF_ENGINEER_KEYWORDS,
        FINOPS_KEYWORDS,
        POLICY_KEYWORDS,
    )

    title = str(task.get("title") or "").lower()
    goal = str(task.get("goal") or "").lower()
    spec = str(task.get("spec") or "").lower()
    combined = f"{title} {goal} {spec}"

    # Project structure/blueprint maintenance tasks -> ChiefEngineer
    if any(kw in combined for kw in CHIEF_ENGINEER_KEYWORDS):
        return "ChiefEngineer"

    # Architecture/design tasks -> Architect
    if any(kw in combined for kw in ARCHITECT_KEYWORDS):
        return "Architect"

    # Policy/compliance tasks -> PolicyGate
    if any(kw in combined for kw in POLICY_KEYWORDS):
        return "PolicyGate"

    # Budget/cost tasks -> FinOps
    if any(kw in combined for kw in FINOPS_KEYWORDS):
        return "FinOps"

    # Review/audit tasks -> Auditor
    if any(kw in combined for kw in AUDIT_KEYWORDS):
        return "Auditor"

    # Default: Director handles implementation
    return "Director"


def extract_defect_ticket(task: dict[str, Any]) -> dict[str, Any]:
    """Extract defect ticket from task."""
    if not isinstance(task, dict):
        return {}
    required = task.get("required_evidence")
    if isinstance(required, dict):
        ticket = required.get("defect_ticket")
        if isinstance(ticket, dict):
            return dict(ticket)
    ticket = task.get("defect_ticket")
    if isinstance(ticket, dict):
        return dict(ticket)
    return {}


def validate_ticket_fields(ticket: dict[str, Any], required_fields: list[str]) -> list[str]:
    """Validate defect ticket has required fields."""
    missing: list[str] = []
    for field in required_fields:
        key = str(field or "").strip()
        if not key:
            continue
        value = ticket.get(key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(key)
            continue
        if isinstance(value, list) and not value:
            missing.append(key)
            continue
    return missing


__all__ = [
    "_auto_assign_role",
    "compute_task_fingerprint",
    "extract_defect_ticket",
    "generate_task_id",
    "normalize_assigned_to",
    "normalize_priority",
    "normalize_required_evidence",
    "validate_ticket_fields",
]
