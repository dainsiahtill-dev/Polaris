"""Versioned PM product intent; CE owns topology and omitted verifier recipes.

A validated projection is a planning contract, never a write or execution grant.
Explicit typed technical constraints remain exact; prose never creates argv.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .artifact_contracts import validate_generated_outputs, validate_pm_verification_commands

PM_TASK_INTENT_SCHEMA_VERSION = "pm.task_intent.v1"
_GLOBAL_LISTS = ("required_requirements", "optional_requirements", "acceptance", "explicit_constraints")
_PATH_FIELDS = ("scope_paths", "target_files", "context_files")
_TECHNICAL_FIELDS = (*_PATH_FIELDS, "verification_commands", "generated_outputs")
_TASK_FIELDS = {
    "id",
    "title",
    "goal",
    "acceptance",
    "depends_on",
    "description",
    "scope",
    "verification_recipe_hint",
    "task_metadata",
    "planning_advisory",
    *_TECHNICAL_FIELDS,
}


class PmTaskIntentError(ValueError):
    """Structured feedback about an unqualified PM draft."""

    def __init__(self, path: str, message: str, *, hard_boundary: bool = False, **details: Any) -> None:
        super().__init__(message)
        self.diagnostic = {
            "code": "pm_task_intent_invalid",
            "path": path,
            "message": message,
            "hard_boundary": hard_boundary,
            **details,
        }


def _text(value: Any, path: str, *, draft: bool = False) -> str:
    if not isinstance(value, str) or (not draft and not value.strip()):
        raise PmTaskIntentError(path, "Expected nonempty text")
    return value


def _texts(value: Any, path: str, *, required: bool = False, draft: bool = False) -> list[str]:
    if not isinstance(value, list) or (required and not draft and not value):
        raise PmTaskIntentError(path, "Expected a nonempty text array" if required else "Expected a text array")
    return [_text(item, f"{path}[{i}]", draft=draft) for i, item in enumerate(value)]


def _path(value: Any, path: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or PureWindowsPath(value).drive
        or PurePosixPath(value).is_absolute()
        or str(PurePosixPath(value)) != value
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise PmTaskIntentError(path, "Explicit path must be canonical and workspace-relative", hard_boundary=True)


# One bounded JSON namespace absorbs product facts, never control-plane authority.
_ADVISORY_MAX_BYTES = 65536
_ADVISORY_MAX_NODES = 4096
_ADVISORY_MAX_DEPTH = 8
_RESERVED_FIELDS = frozenset({
    "id", "role", "role_id", "assigned_to", "owner", "assignee", "routing", "route",
    "execution_backend", "backend", "projection", "metadata", "job_token", "run_id",
    "trace_id", "call_id", "request_freeze_id", "request_hash", "prompt_hash", "scope",
    "command", "argv", "cwd", "tools", "tool_choice", "response_format", "stage", "phase",
    "status", "depth_policy", "level_contract", "context_snapshot_ref", "capabilities",
    "product_goal", "product_acceptance", "title", "goal", "depends_on", "schema_version",
    *_GLOBAL_LISTS, *_TECHNICAL_FIELDS,
})


def _advisory(value: Any, path: str) -> None:
    """Validate bounded JSON advice; nested suggestions never become capabilities."""
    if not isinstance(value, dict):
        raise PmTaskIntentError(path, "Planning advisory must be a JSON object")
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _ADVISORY_MAX_NODES or depth > _ADVISORY_MAX_DEPTH:
            raise PmTaskIntentError(path, "Planning advisory exceeds bounded JSON depth or node count")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise PmTaskIntentError(path, "Planning advisory JSON keys must be strings")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise PmTaskIntentError(path, "Planning advisory must contain finite JSON values")

    visit(value, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PmTaskIntentError(path, "Planning advisory must contain finite JSON values") from exc
    if len(encoded) > _ADVISORY_MAX_BYTES:
        raise PmTaskIntentError(path, "Planning advisory exceeds bounded JSON byte size")


def _reserved_authority_field(key: str) -> bool:
    token = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower().replace("-", "_")
    return (
        token in _RESERVED_FIELDS
        or token.split("_", 1)[0] in {"execution", "runtime", "provider", "trusted", "job", "jobs", "role", "roles", "projection", "depth", "identity", "protocol", "schema"}
        or token.split("_")[-1] in {"hash", "hashes", "token", "tokens", "authority", "scope", "scopes", "path", "paths", "file", "files", "command", "commands", "id", "ids", "ref", "refs", "reference", "references"}
    )


def _collect_advisory(payload: dict[str, Any], allowed: set[str], path: str) -> None:
    """Classify undeclared JSON facts once; reject reserved direct authority."""
    if any(not isinstance(key, str) for key in payload):
        raise PmTaskIntentError(path, "Semantic planning field names must be strings")
    unknown = sorted(set(payload) - allowed)
    denied = [key for key in unknown if _reserved_authority_field(key)]
    if denied:
        raise PmTaskIntentError(
            path,
            "Reserved semantic authority fields: " + ", ".join(denied),
            hard_boundary=True,
            unknown_fields=denied,
            allowed_fields=sorted(allowed - set(_TECHNICAL_FIELDS)),
        )
    advice = payload.get("planning_advisory", {})
    _advisory(advice, path + ".planning_advisory")
    for key in unknown:
        if key in advice and advice[key] != payload[key]:
            raise PmTaskIntentError(path + "." + key, "Conflicting product advisory values")
        advice[key] = payload.pop(key)
    if advice or "planning_advisory" in payload:
        _advisory(advice, path + ".planning_advisory")
        payload["planning_advisory"] = advice


def _validate(payload: Any, *, draft: bool = False) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != PM_TASK_INTENT_SCHEMA_VERSION:
        raise PmTaskIntentError("$.schema_version", "Expected pm.task_intent.v1")
    allowed = {"schema_version", "product_goal", *_GLOBAL_LISTS, "tasks", "project_metadata", "verification_recipe_hint", "planning_advisory"}
    result = deepcopy(dict(payload))
    _collect_advisory(result, allowed, "$")
    for key in ("project_metadata", "verification_recipe_hint"):
        if key in result:
            _advisory(result[key], "$." + key)
    _text(result.get("product_goal", ""), "$.product_goal", draft=draft)
    for key in _GLOBAL_LISTS:
        result[key] = _texts(
            result.get(key, []), f"$.{key}", required=key in {"required_requirements", "acceptance"}, draft=draft
        )
    rows = result.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise PmTaskIntentError("$.tasks", "At least one implementation task is required")
    identities: set[str] = set()
    for index, row in enumerate(rows):
        path = f"$.tasks[{index}]"
        if not isinstance(row, dict):
            raise PmTaskIntentError(path, "Task must use declared semantic intent fields")
        _collect_advisory(row, _TASK_FIELDS, path)
        identity = row.get("id")
        if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", identity) is None:
            raise PmTaskIntentError(path + ".id", "Task ID must be a stable canonical token")
        if identity in identities:
            raise PmTaskIntentError(path + ".id", "Task IDs must be unique")
        identities.add(identity)
        for key in ("title", "goal"):
            _text(row.get(key, ""), path + "." + key, draft=draft)
        row["acceptance"] = _texts(row.get("acceptance", []), path + ".acceptance", required=True, draft=draft)
        row["depends_on"] = _texts(row.get("depends_on", []), path + ".depends_on")
        if "description" in row:
            _text(row["description"], path + ".description", draft=draft)
        if "scope" in row:
            scope = _text(row["scope"], path + ".scope", draft=draft)
            row.setdefault("description", scope)
        for key in ("verification_recipe_hint", "task_metadata"):
            if key in row:
                _advisory(row[key], path + "." + key)
        for key in _PATH_FIELDS:
            if key in row:
                _texts(row[key], path + "." + key)
                for i, value in enumerate(row[key]):
                    _path(value, f"{path}.{key}[{i}]")
        if "verification_commands" in row:
            _, errors = validate_pm_verification_commands(
                row["verification_commands"], path=path + ".verification_commands"
            )
            if errors:
                raise PmTaskIntentError(
                    errors[0]["path"],
                    errors[0]["message"],
                    hard_boundary=errors[0]["code"] in {"shell_wrapper_forbidden", "invalid_cwd"},
                )
        if "generated_outputs" in row:
            _, errors = validate_generated_outputs(row["generated_outputs"], path=path + ".generated_outputs")
            if errors:
                raise PmTaskIntentError(errors[0]["path"], errors[0]["message"], hard_boundary=True)
    _advisory({"project": {key: result[key] for key in ("project_metadata", "verification_recipe_hint", "planning_advisory") if key in result},
               "tasks": [{key: row[key] for key in ("task_metadata", "verification_recipe_hint", "planning_advisory", "scope") if key in row} for row in rows]}, "$.planning_advisory")
    if draft:
        return result
    graph = {row["id"]: row["depends_on"] for row in rows}
    dependency_paths = {row["id"]: f"$.tasks[{i}].depends_on" for i, row in enumerate(rows)}
    for identity, dependencies in graph.items():
        if any(dependency not in graph for dependency in dependencies):
            raise PmTaskIntentError(dependency_paths[identity], "Unknown task dependency")
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(identity: str) -> None:
        if identity not in graph:
            raise PmTaskIntentError("$.tasks", "Unknown task dependency: " + identity)
        if identity in visiting:
            raise PmTaskIntentError(dependency_paths[identity], "Cyclic task dependency: " + identity)
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in graph[identity]:
            visit(dependency)
        visiting.remove(identity)
        visited.add(identity)

    for identity in graph:
        visit(identity)
    return result


@dataclass(frozen=True)
class PmTaskIntentV1:
    """Immutable text-backed product contract, with independent projections."""

    contract_json: str

    @classmethod
    def from_payload(cls, payload: Any) -> PmTaskIntentV1:
        import json

        validated = _validate(payload)
        return cls(json.dumps(validated, ensure_ascii=False, sort_keys=True, allow_nan=False))

    def to_dict(self) -> dict[str, Any]:
        import json

        payload: dict[str, Any] = json.loads(self.contract_json)
        return _validate(payload)

    def to_task_contracts(self) -> list[dict[str, Any]]:
        return _project(self.to_dict())


def _project(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = []
    for raw in payload["tasks"]:
        task = deepcopy(raw)
        task["schema_version"] = PM_TASK_INTENT_SCHEMA_VERSION
        task["product_goal"] = payload["product_goal"]
        for key in ("required_requirements", "optional_requirements", "explicit_constraints"):
            task[key] = deepcopy(payload[key])
        task["product_acceptance"] = deepcopy(payload["acceptance"])
        task["observable_acceptance"] = deepcopy(raw["acceptance"])
        task["acceptance_criteria"] = deepcopy(task["observable_acceptance"])
        task["metadata"] = {
            "planning_contract_version": PM_TASK_INTENT_SCHEMA_VERSION,
            "topology_authority": "chief_engineer",
            "verification_authority": "chief_engineer",
            "explicit_technical_fields": [key for key in _TECHNICAL_FIELDS if key in raw],
        }
        advisory = {}
        if "planning_advisory" in payload:
            advisory["project_advisory"] = deepcopy(payload["planning_advisory"])
        for key in ("task_metadata", "scope", "planning_advisory"):
            if key in task:
                advisory["task_advisory" if key == "planning_advisory" else key] = task.pop(key)
        if "project_metadata" in payload:
            advisory["project_metadata"] = deepcopy(payload["project_metadata"])
        if "verification_recipe_hint" in payload:
            advisory["project_verification_recipe_hint"] = deepcopy(payload["verification_recipe_hint"])
        if "verification_recipe_hint" in task:
            advisory["verification_recipe_hint"] = task.pop("verification_recipe_hint")
        if advisory:
            task["metadata"]["planning_advisory"] = advisory
            task["metadata"]["planning_advisory_execution_authority"] = False
        for key in (*_PATH_FIELDS, "verification_commands"):
            task.setdefault(key, [])
        task["assigned_to"] = "Director"
        tasks.append(task)
    return tasks


def normalize_pm_task_intent(payload: Any) -> list[dict[str, Any]]:
    """Validate product acceptance, dependencies and explicit boundaries, then project."""
    return PmTaskIntentV1.from_payload(payload).to_task_contracts()


def project_pm_task_intent_draft(payload: Any) -> list[dict[str, Any]]:
    """Retain correctable semantic omissions; admission still requires strict validation.

    Unsafe technical declarations are never retained for ordinary draft repair.
    """
    return _project(_validate(payload, draft=True))


def is_pm_task_intent(task: Any) -> bool:
    """Recognize the declared semantic protocol; metadata alone is insufficient.

    This is a route discriminator, not execution authority or a quality verdict.
    """
    if not isinstance(task, Mapping) or task.get("schema_version") != PM_TASK_INTENT_SCHEMA_VERSION:
        return False
    metadata = task.get("metadata")
    return (
        isinstance(metadata, Mapping)
        and metadata.get("planning_contract_version") == PM_TASK_INTENT_SCHEMA_VERSION
        and metadata.get("topology_authority") == "chief_engineer"
        and isinstance(task.get("product_goal"), str)
        and isinstance(task.get("required_requirements"), list)
        and isinstance(task.get("product_acceptance"), list)
        and isinstance(task.get("acceptance"), list)
    )


def pm_task_intent_payload(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild the product envelope without granting admission or execution."""
    if not tasks or not all(is_pm_task_intent(task) for task in tasks):
        raise PmTaskIntentError("$.tasks", "Semantic and legacy task protocols cannot be mixed")
    first = tasks[0]
    envelope = {
        "schema_version": PM_TASK_INTENT_SCHEMA_VERSION,
        "product_goal": first["product_goal"],
        "acceptance": first["product_acceptance"],
        "tasks": [],
    }
    for key in ("required_requirements", "optional_requirements", "explicit_constraints"):
        envelope[key] = first.get(key, [])
    first_advisory = first["metadata"].get("planning_advisory", {})
    if "project_advisory" in first_advisory:
        envelope["planning_advisory"] = deepcopy(first_advisory["project_advisory"])
    if "project_metadata" in first_advisory:
        envelope["project_metadata"] = deepcopy(first_advisory["project_metadata"])
    if "project_verification_recipe_hint" in first_advisory:
        envelope["verification_recipe_hint"] = deepcopy(first_advisory["project_verification_recipe_hint"])
    for i, task in enumerate(tasks):
        for key in (
            "product_goal",
            "product_acceptance",
            "required_requirements",
            "optional_requirements",
            "explicit_constraints",
        ):
            if task.get(key, []) != first.get(key, []):
                raise PmTaskIntentError(f"$.tasks[{i}].{key}", "Tasks disagree about the product contract")
        explicit = task["metadata"].get("explicit_technical_fields", [])
        row = {
            key: deepcopy(task[key])
            for key in ("id", "title", "goal", "acceptance", "depends_on", "description")
            if key in task
        }
        advisory = task["metadata"].get("planning_advisory", {})
        if any(advisory.get(key) != first_advisory.get(key) for key in ("project_metadata", "project_verification_recipe_hint", "project_advisory")):
            raise PmTaskIntentError(f"$.tasks[{i}].project_metadata", "Tasks disagree about read-only product advice")
        for key in ("verification_recipe_hint", "task_metadata", "scope", "task_advisory"):
            if key in advisory:
                row["planning_advisory" if key == "task_advisory" else key] = deepcopy(advisory[key])
        for key in _TECHNICAL_FIELDS:
            if key in explicit or task.get(key):
                row[key] = deepcopy(task[key])
        envelope["tasks"].append(row)
    return envelope


def validate_pm_task_intent_tasks(tasks: list[dict[str, Any]]) -> None:
    """Revalidate projected rows at public product admission; reject mixed protocols."""
    _validate(pm_task_intent_payload(tasks))


__all__ = [
    "PM_TASK_INTENT_SCHEMA_VERSION",
    "PmTaskIntentError",
    "PmTaskIntentV1",
    "is_pm_task_intent",
    "normalize_pm_task_intent",
    "pm_task_intent_payload",
    "project_pm_task_intent_draft",
    "validate_pm_task_intent_tasks",
]
