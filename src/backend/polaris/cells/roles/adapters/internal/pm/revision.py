"""Private PM model edits over an immutable, unqualified original candidate.

This is a derivation layer, not a task authority or a generic JSON patch API.
The caller must still run the complete parser, normalization and quality gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_generated_outputs

from .normalization import _validate_pm_verification_commands
from .parsing import _complete_pm_tasks, _decode_complete_pm_payload, _PMContractError

_SCHEMA = "pm.task_revision.v1"
_OVERVIEW = (
    "id",
    "title",
    "goal",
    "scope_paths",
    "target_files",
    "depends_on",
    "dependencies",
    "phase",
    "assigned_to",
    "execution_backend",
    "verification_commands",
)
# Display and authority are deliberately separate. Routing/provenance is never editable.
_SET_TYPES = {
    "title": "nonempty_string",
    "goal": "nonempty_string",
    "phase": "nonempty_string",
    "scope_paths": "string_array",
    "target_files": "string_array",
    "depends_on": "string_array",
    "dependencies": "string_array",
    "verification_commands": "verifier_array",
}
_DIAGNOSTIC_TYPES = {
    "description": "nonempty_string",
    "scope": "scope_value",
    "steps": "nonempty_string_array",
    "acceptance": "nonempty_string_array",
    "acceptance_criteria": "nonempty_string_array",
    "context_files": "string_array",
    "generated_outputs": "generated_outputs_array",
}
_ADD_FIELDS = frozenset(
    (
        *_OVERVIEW,
        *_DIAGNOSTIC_TYPES,
        "dependencies",
        "execution_checklist",
        "metadata",
        "projection",
        "delivery_plan_document",
        "delivery_depth_contract",
        "behavior_contract",
        "backlog_ref",
    )
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error(message: str, path: str = "$", **details: Any) -> _PMContractError:
    return _PMContractError({"code": "pm_revision_invalid", "path": path, "message": message, **details})


def _canonical_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is not None


@dataclass(frozen=True)
class PMRevisionBase:
    """All retained state is immutable text; properties return independent copies."""

    response: str
    tasks_json: str
    identity_json: str
    view_json: str
    base_hash: str

    @property
    def tasks(self) -> list[dict[str, Any]]:
        return json.loads(self.tasks_json)  # type: ignore[no-any-return]

    @property
    def identity(self) -> dict[str, Any]:
        return json.loads(self.identity_json)  # type: ignore[no-any-return]

    @property
    def view(self) -> dict[str, Any]:
        return json.loads(self.view_json)  # type: ignore[no-any-return]


def make_revision_base(
    response: str,
    *,
    workspace: str | None,
    run_id: str | None,
    task_id: str | None,
    directive: str,
    quality: dict[str, Any],
    projection_hint: dict[str, Any] | None = None,
) -> PMRevisionBase | None:
    """Only complete shape-valid drafts with stable original IDs are editable.

    No task is deletable by default. No existing gate supplies a trusted full-task
    deletion permission, so a model cannot obtain it by requesting removal.
    """
    # Check the caller's identity before Path.resolve can turn an empty workspace
    # into cwd. Standalone calls keep their original full re-answer protocol.
    if any(not isinstance(value, str) or not value.strip() for value in (workspace, run_id, task_id)):
        return None
    assert isinstance(workspace, str)
    try:
        paths: list[str] = []
        tasks = _complete_pm_tasks(_decode_complete_pm_payload(response), source_paths=paths)
        ids = [task.get("id") for task in tasks]
        if any(not _canonical_id(value) for value in ids) or len(set(ids)) != len(ids):
            return None
        tasks_json = canonical_json(tasks)
    except (_PMContractError, ValueError, TypeError):
        return None
    identity = {
        "workspace": str(Path(workspace).resolve()),
        "run_id": run_id,
        "task_id": task_id,
        "directive_hash": content_hash(directive),
        "original_response_hash": content_hash(response),
        "projection_hint": projection_hint or {},
    }
    overview = []
    manifest_tasks = {}
    raw_diagnostics = quality.get("contract_diagnostics")
    diagnostics = raw_diagnostics if isinstance(raw_diagnostics, list) else []
    semantic_fields = _semantic_revision_fields(tasks, paths, quality, diagnostics)
    if semantic_fields is None:
        return None
    artifact_mode = any(
        isinstance(d, dict)
        and str(d.get("code", "")).startswith(("artifact_", "generated_output_", "pm_plan_language_mismatch"))
        for d in diagnostics
    )
    diagnosed_fields: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        for candidate in diagnostic.get("proposal_task_ids", []):
            if candidate in {task["id"] for task in tasks}:
                diagnosed_fields.setdefault(candidate, set()).update(
                    {"generated_outputs", "depends_on", "verification_commands"}
                )
        for task, source_path in zip(tasks, paths, strict=True):
            if str(diagnostic.get("path", "")).startswith(source_path + "."):
                field = str(diagnostic["path"])[len(source_path) + 1 :].split("[", 1)[0].split(".", 1)[0]
                diagnosed_fields.setdefault(task["id"], set()).add(field)
                diagnosed_fields[task["id"]].update(diagnostic.get("repair_fields", []))
    source_graph_ids = {identity for identity, fields in diagnosed_fields.items() if "generated_outputs" in fields}
    if artifact_mode:
        pending = list(source_graph_ids)
        by_id = {task["id"]: task for task in tasks}
        while pending:
            owner = by_id.get(pending.pop(), {})
            dependencies = owner.get("depends_on", owner.get("dependencies", []))
            related = list(dependencies) if isinstance(dependencies, list) else []
            outputs = owner.get("generated_outputs", [])
            for output in outputs if isinstance(outputs, list) else []:
                if not isinstance(output, dict):
                    continue
                inputs = output.get("inputs", [])
                for item in inputs if isinstance(inputs, list) else []:
                    if isinstance(item, dict):
                        related.append(item.get("task_id"))
            for dependency in related:
                if not isinstance(dependency, str):
                    continue
                if dependency not in source_graph_ids:
                    source_graph_ids.add(dependency)
                    pending.append(dependency)
    for task, source_path in zip(tasks, paths, strict=True):
        set_fields = dict(_SET_TYPES)
        row = {field: task[field] for field in _OVERVIEW if field in task}
        for field, type_name in _DIAGNOSTIC_TYPES.items():
            if (
                field in diagnosed_fields.get(task["id"], set())
                or field in semantic_fields.get(task["id"], set())
                or any(
                    isinstance(d, dict)
                    and (
                        d.get("path") == f"{source_path}.{field}"
                        or str(d.get("path", "")).startswith(f"{source_path}.{field}[")
                    )
                    for d in diagnostics
                )
            ):
                set_fields[field] = type_name
                if field in task:
                    row[field] = task[field]
        if artifact_mode:
            affected = diagnosed_fields.get(task["id"], set()) | semantic_fields.get(task["id"], set())
            set_fields = {key: value for key, value in set_fields.items() if key in affected}
            # The source graph is needed to author generated input bindings;
            # unrelated titles and immutable routing do not need redisplay.
            read_only_fields = {"id", "depends_on", "dependencies"}
            if task["id"] in source_graph_ids:
                read_only_fields.update({"target_files", "verification_commands"})
            row = {field: value for field, value in task.items() if field in read_only_fields | set(set_fields)}
        absent_candidates = set(set_fields) if artifact_mode else {*_OVERVIEW, *set_fields}
        row["absent_fields"] = sorted(absent_candidates - task.keys())
        if artifact_mode and not row["absent_fields"]:
            del row["absent_fields"]
        append_fields = {
            field: "nonempty_string_array" for field in ("steps", "acceptance") if isinstance(task.get(field), list)
        }
        if artifact_mode:
            append_fields = {}
        overview.append(row)
        if not artifact_mode or set_fields or append_fields:
            manifest_tasks[task["id"]] = {"set": set_fields, "append": append_fields}
    manifest = {"tasks": manifest_tasks, "remove_task_ids": []}
    base_hash = content_hash(
        canonical_json(
            {
                "schema_version": _SCHEMA,
                "identity": identity,
                "tasks": tasks,
                "edit_manifest": manifest,
            }
        )
    )
    view = {"base_hash": base_hash, "tasks": overview, "edit_manifest": manifest}
    if isinstance(quality.get("task_fission"), dict):
        view["task_fission_readonly"] = quality["task_fission"]
    return PMRevisionBase(response, tasks_json, canonical_json(identity), canonical_json(view), base_hash)


def _semantic_revision_fields(
    tasks: list[dict[str, Any]],
    paths: list[str],
    quality: dict[str, Any],
    diagnostics: list[Any],
) -> dict[str, set[str]] | None:
    """Localize only exact program-generated quality findings, never model prose.

    The adapter normalizes ``acceptance or acceptance_criteria`` into both aliases
    before the public quality gate. Match its original source precedence, not the
    gate's derived alias. Unknown findings (including autofix-created task IDs)
    select the existing full re-answer before any correction call.
    """
    acceptance_errors = (
        "acceptance criteria is missing",
        "acceptance permits placeholder or manifest-only execution",
        "acceptance uses fragile random-sequence assertions",
        "acceptance requires executable command or file evidence",
    )
    known_findings: dict[str, tuple[str, str]] = {}
    for task in tasks:
        field = "acceptance" if task.get("acceptance") else "acceptance_criteria"
        if not task.get("acceptance") and not task.get("acceptance_criteria"):
            field = "acceptance"
        for message in acceptance_errors:
            known_findings[f"{task['id']}: {message}"] = (task["id"], field)
    verifier_errors = {
        "verification_commands_invalid",
        "verification_commands_missing",
        "verification_environment_prep_missing",
        "verification_delivery_gate_missing",
        "verification_entrypoint_missing",
    }
    diagnostic_codes = {
        d.get("code")
        for d in diagnostics
        if isinstance(d, dict)
        and isinstance(d.get("code"), str)
        and any(str(d.get("path", "")).startswith(path + ".") for path in paths)
    }
    fields: dict[str, set[str]] = {}
    artifact_mode = any(
        isinstance(d, dict)
        and str(d.get("code", "")).startswith(("artifact_", "generated_output_", "pm_plan_language_mismatch"))
        for d in diagnostics
    )
    critical = quality.get("critical_issues", [])
    if not isinstance(critical, list):
        return None
    for issue in critical:
        if not isinstance(issue, str):
            return None
        finding = known_findings.get(issue)
        if finding is not None:
            task_id, field = finding
            fields.setdefault(task_id, set()).add(field)
        elif issue.startswith("factory_typescript_contract_missing:"):
            missing = issue.split(":", 1)[1].split(",")
            # The existing general manifest explicitly permits source target
            # and scope edits for all originals, plus complete new tasks. It can
            # cover these exact structural omissions without guessing an owner.
            # A narrowed output-graph manifest cannot. HTML entry topology also
            # needs a full answer until the program supplies exact localization.
            if (
                artifact_mode
                or not missing
                or set(missing) - {"package.json", "tsconfig.json", "README.md", "*.ts", "*.test.ts", "task_count>=2"}
            ):
                return None
            for task in tasks:
                fields.setdefault(task["id"], set()).update({"target_files", "scope_paths"})
        elif issue in verifier_errors:
            if artifact_mode and not any(
                isinstance(d, dict)
                and any(str(d.get("path", "")).startswith(path + ".verification_commands") for path in paths)
                for d in diagnostics
            ):
                return None
        elif issue in diagnostic_codes:
            continue
        else:
            return None
    return fields


def _strings(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _validate_value(field: str, value: Any, type_name: str) -> None:
    valid = False
    if type_name == "nonempty_string":
        valid = isinstance(value, str) and bool(value.strip())
    elif type_name in {"string_array", "nonempty_string_array"}:
        valid = _strings(value, nonempty=type_name == "nonempty_string_array")
    elif type_name == "scope_value":
        valid = (isinstance(value, str) and bool(value.strip())) or _strings(value, nonempty=True)
    elif type_name == "verifier_array":
        _, diagnostics = _validate_pm_verification_commands(value, path="$.revision.verification_commands")
        valid = isinstance(value, list) and not diagnostics
    elif type_name == "generated_outputs_array":
        _, diagnostics = validate_generated_outputs(value, path="$.revision.generated_outputs")
        valid = isinstance(value, list) and not diagnostics
    if not valid:
        raise _error(f"Invalid {type_name} value for {field}")
    if field in {"scope_paths", "target_files", "context_files"}:
        for path in value:
            if (
                path != path.strip()
                or "\\" in path
                or path.startswith("/")
                or ":" in path
                or any(part in {"", "..", "."} for part in path.split("/"))
                or str(PurePosixPath(path)) != path
            ):
                raise _error(f"Noncanonical relative path in {field}: {path!r}", repairable=False)
    if field in {"depends_on", "dependencies"} and (
        any(not _canonical_id(dep) for dep in value) or len(value) != len(set(value))
    ):
        raise _error("Dependencies must contain unique canonical IDs")


def _validate_added_task(task: Any) -> None:
    if not isinstance(task, dict) or task.keys() - _ADD_FIELDS:
        raise _error("New task must use the complete original task contract, with known fields")
    required = {"id", "title", "goal", "scope_paths", "target_files", "steps", "acceptance", "verification_commands"}
    if required - task.keys() or not _canonical_id(task["id"]):
        raise _error("New task needs a unique ID and a complete task contract")
    _complete_pm_tasks({"tasks": [task]})
    for field in (*_SET_TYPES, *_DIAGNOSTIC_TYPES):
        if field in task:
            _validate_value(field, task[field], {**_SET_TYPES, **_DIAGNOSTIC_TYPES}[field])
    # Reuse the existing opt-in routing boundary: an appended task cannot become
    # the first projection task or override the caller's trusted projection hint.
    role = str(task.get("assigned_to", "Director")).lower().replace(" ", "_")
    if role not in {"director", "chiefengineer", "chief_engineer", "qa"}:
        raise _error("Unsupported new task role")
    if task.get("execution_backend", "code_edit") != "code_edit" or task.get("projection"):
        raise _error("New tasks cannot expand projection/backend routing authority")
    metadata = task.get("metadata", {})
    if any(key in metadata for key in ("execution_backend", "projection", "assigned_to")):
        raise _error("New task metadata cannot override routing authority")


def _validate_dependencies(tasks: list[dict[str, Any]]) -> None:
    """Validate original as well as edited dependencies before normalization."""
    dependencies: dict[str, list[str]] = {}
    for task in tasks:
        if "depends_on" in task and "dependencies" in task and task["depends_on"] != task["dependencies"]:
            raise _error("Conflicting dependency aliases")
        deps = task.get("depends_on", task.get("dependencies", []))
        _validate_value("depends_on", deps, "string_array")
        dependencies[task["id"]] = deps
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id not in dependencies:
            raise _error(f"Unknown dependency {task_id}")
        if task_id in visiting:
            raise _error(f"Cyclic dependency at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep in dependencies[task_id]:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in dependencies:
        visit(task_id)


def apply_revision(base: PMRevisionBase, response: str) -> dict[str, Any]:
    """Validate the whole edit, then derive a fresh complete raw candidate.

    Failure never changes base, accepts a prefix, falls back to a replacement
    plan, or grants task execution. Dependencies and verifier values cannot be
    repaired implicitly by normalization. Authorized array set+append operations
    compose in that order on a private copy; retained provider text stays raw.
    """
    if not response.strip().startswith("{"):
        raise _error("Revision must be one bare complete JSON object")
    patch = _decode_complete_pm_payload(response)
    canonical_json(patch)  # rejects non-finite values, including in nested objects
    if not isinstance(patch, dict) or set(patch) != {
        "schema_version",
        "base_hash",
        "updates",
        "add_tasks",
        "remove_task_ids",
    }:
        raise _error("Unexpected revision envelope fields")
    if patch["schema_version"] != _SCHEMA or patch["base_hash"] != base.base_hash:
        raise _error(
            "Revision schema/base does not match the retained local candidate", "$.base_hash", repairable=False
        )
    if any(not isinstance(patch[key], list) for key in ("updates", "add_tasks", "remove_task_ids")):
        raise _error("Revision operation containers must be arrays")
    originals = base.tasks
    by_id = {task["id"]: task for task in originals}
    manifest = base.view["edit_manifest"]
    removed = patch["remove_task_ids"]
    if (
        not _strings(removed)
        or len(set(removed)) != len(removed)
        or any(task_id not in manifest["remove_task_ids"] for task_id in removed)
    ):
        raise _error("Task removal is not authorized by the server manifest", "$.remove_task_ids", repairable=False)
    updates: dict[str, dict[str, Any]] = {}
    for update_index, update in enumerate(patch["updates"]):
        if (
            not isinstance(update, dict)
            or update.keys() - {"task_id", "set", "append"}
            or not _canonical_id(update.get("task_id"))
        ):
            raise _error("Unknown update shape or task ID")
        task_id = update["task_id"]
        if task_id not in by_id or task_id in updates or task_id in removed:
            raise _error(
                "Unknown, duplicate or conflicting update target",
                f"$.updates[{update_index}].task_id",
                task_id=task_id,
                repairable=False,
            )
        sets, appends = update.get("set", {}), update.get("append", {})
        if not isinstance(sets, dict) or not isinstance(appends, dict) or not (sets or appends):
            raise _error("Update must contain explicit set/append fields")
        permission = manifest["tasks"].get(task_id)
        if not isinstance(permission, dict):
            raise _error(f"Task {task_id!r} is not server-authorized for revision")
        for kind, fields in (("set", sets), ("append", appends)):
            for field, value in fields.items():
                field_path = f"$.updates[{update_index}].{kind}.{field}"
                if field not in permission[kind]:
                    raise _error(
                        f"Field {field!r} is not server-authorized for {kind}",
                        field_path,
                        task_id=task_id,
                        field=field,
                        operation=kind,
                        repairable=any(field in permission[other] for other in ("set", "append") if other != kind),
                        authorized_operations=[other for other in ("set", "append") if field in permission[other]],
                    )
                try:
                    _validate_value(field, value, permission[kind][field])
                except _PMContractError as exc:
                    raise _PMContractError(
                        {**exc.diagnostic, "path": field_path, "task_id": task_id, "field": field, "operation": kind}
                    ) from exc
        if "acceptance" in appends and "acceptance_criteria" in sets:
            raise _error("Conflicting acceptance alias operations")
        # Validate each original operation and its permission before composing.
        # Never mutate the decoded provider edit or infer an unauthorized append.
        normalized_sets, normalized_appends = dict(sets), dict(appends)
        for field in sorted(sets.keys() & appends.keys()):
            if not isinstance(sets[field], list) or not isinstance(appends[field], list):
                raise _error("Only arrays may use set and append on the same field")
            combined = [*sets[field], *appends[field]]
            _validate_value(field, combined, permission["set"][field])
            normalized_sets[field] = combined
            del normalized_appends[field]
        updates[task_id] = {"task_id": task_id, "set": normalized_sets, "append": normalized_appends}
    added_ids: set[str] = set()
    for task in patch["add_tasks"]:
        _validate_added_task(task)
        if task["id"] in by_id or task["id"] in added_ids:
            raise _error("New task ID conflicts with an original or new task (no ID recycling)")
        added_ids.add(task["id"])
    # All operation shapes/permissions are valid. Work only on fresh decoded copies.
    merged = []
    for task in originals:
        if task["id"] in removed:
            continue
        update = updates.get(task["id"], {})
        for field, value in update.get("set", {}).items():
            task[field] = value
        for field, value in update.get("append", {}).items():
            task[field] = [*task[field], *value]
        merged.append(task)
    merged.extend(patch["add_tasks"])
    _complete_pm_tasks({"tasks": merged})
    _validate_dependencies(merged)
    return {"tasks": merged}


def revision_progress_state(candidate: dict[str, Any] | None, quality: dict[str, Any]) -> tuple[str, str]:
    """Fingerprint diagnosed operands, never unrelated changes in model prose."""
    diagnostics = quality.get("contract_diagnostics", [])
    critical = quality.get("critical_issues", [])
    diagnosis = {"critical_issues": critical, "contract_diagnostics": diagnostics}
    diagnosis_hash = content_hash(canonical_json(diagnosis))
    if candidate is None:
        return "no_valid_candidate", diagnosis_hash
    tasks = candidate["tasks"]
    by_id = {task["id"]: task for task in tasks}
    fields: dict[str, set[str]] = {}

    def include(task_id: str, names: set[str]) -> None:
        if task_id in by_id:
            fields.setdefault(task_id, set()).update(names)

    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        path = str(diagnostic.get("path", ""))
        match = re.match(r"^\$\.tasks\[(\d+)\]\.([a-z_]+)", path)
        task_id = diagnostic.get("task_id")
        provenance = diagnostic.get("provenance", {})
        if task_id not in by_id and isinstance(provenance, dict):
            task_id = provenance.get("original_task_id")
        if task_id not in by_id and match and int(match[1]) < len(tasks):
            task_id = tasks[int(match[1])]["id"]
        field = diagnostic.get("field") or (match[2] if match else None)
        if isinstance(task_id, str) and isinstance(field, str):
            names = {field}
            repair_fields = diagnostic.get("repair_fields", [])
            if isinstance(repair_fields, list):
                names.update(name for name in repair_fields if isinstance(name, str))
            include(task_id, names)
    verifier_issues = {
        "verification_commands_invalid",
        "verification_commands_missing",
        "verification_environment_prep_missing",
        "verification_delivery_gate_missing",
        "verification_entrypoint_missing",
    }
    for issue in critical:
        if issue in verifier_issues:
            for task_id in by_id:
                include(task_id, {"verification_commands"})
        elif isinstance(issue, str) and issue.startswith("factory_typescript_contract_missing:"):
            for task_id in by_id:
                include(task_id, {"target_files", "scope_paths"})
        elif issue == "pm_revision_dependencies_invalid":
            for task_id in by_id:
                include(task_id, {"depends_on", "dependencies"})
        elif isinstance(issue, str):
            task_id, separator, message = issue.partition(": ")
            if separator and message.startswith("acceptance "):
                include(task_id, {"acceptance", "acceptance_criteria"})
    operands = [
        {"task_id": task_id, "fields": {name: by_id[task_id].get(name) for name in sorted(names)}}
        for task_id, names in sorted(fields.items())
    ]
    # A threshold-only quality failure can improve its measured score; unresolved
    # concrete diagnostics still require a change in their own operands.
    state: dict[str, Any] = {"operands": operands}
    if not critical and not diagnostics:
        state["score"] = quality.get("score", 0)
    return content_hash(canonical_json(state)), diagnosis_hash


def revision_derivation(
    base: PMRevisionBase,
    response: str,
    *,
    raw_merged_candidate: dict[str, Any] | None,
    normalized_plan: list[dict[str, Any]],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Keep actual replies, immutable base, raw derivation and normalization distinct."""
    try:
        revision = _decode_complete_pm_payload(response)
        canonical_json(revision)
    except (_PMContractError, ValueError):
        revision = None
    normalized = {"tasks": normalized_plan}
    return {
        "schema_version": "pm.task_revision_derivation.v1",
        "identity": base.identity,
        "base_hash": base.base_hash,
        "base": {"tasks": base.tasks},
        "original_response": base.response,
        "original_response_hash": content_hash(base.response),
        "revision_response": response,
        "revision_response_hash": content_hash(response),
        "revision": revision,
        "edit_manifest": base.view["edit_manifest"],
        "raw_merged_candidate": raw_merged_candidate,
        "raw_merged_candidate_hash": content_hash(canonical_json(raw_merged_candidate))
        if raw_merged_candidate
        else None,
        "normalized_plan": normalized,
        "normalized_plan_hash": content_hash(canonical_json(normalized)),
        "normalization_source": "existing_pm_parser_normalization_autofix",
        "merged_candidate_is_original_provider_response": False,
        "quality": quality,
    }
