"""Orchestration Command Service - Single write path for PM/Director/Factory execution.

This module provides a unified command execution layer that consolidates all
orchestration operations (PM, Director, Factory) into a single entry point.

Architecture:
- Single entry point for all orchestration commands
- Unified run ID generation
- Consistent error handling and logging
- Integration with UnifiedOrchestrationService

Phase 4 Implementation: Single Execution Write Path
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public import validate_director_handoff_from_payload
from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationMode,
    OrchestrationRunRequest,
    RoleEntrySpec,
    get_orchestration_service,
)

# Import and register role adapters to ensure they're available for factory flows
# (API routes already do this, but factory flow needs it too)
from polaris.cells.roles.adapters.public.service import register_all_adapters
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM, DEFAULT_MAX_WORKERS
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path

# Re-export for backwards compatibility - import from polaris.kernelone.constants
_DEFAULT_MAX_WORKERS = DEFAULT_MAX_WORKERS

logger = logging.getLogger(__name__)

_CANONICAL_FACTORY_TASK_CHAIN = (
    "pm_planning",
    "chief_engineer_review",
    "director_dispatch",
    "quality_gate",
)
_FACTORY_STAGE_ALIASES = {
    "architect": "docs_generation",
    "architecture": "docs_generation",
    "docs": "docs_generation",
    "documents": "docs_generation",
    "pm": "pm_planning",
    "planning": "pm_planning",
    "chief": "chief_engineer_review",
    "ce": "chief_engineer_review",
    "chief_engineer": "chief_engineer_review",
    "chief-engineer": "chief_engineer_review",
    "blueprint": "chief_engineer_review",
    "director": "director_dispatch",
    "dispatch": "director_dispatch",
    "qa": "quality_gate",
    "verification": "quality_gate",
    "quality": "quality_gate",
}
_TASK_TEXT_FILE_TOKEN_RE = re.compile(
    r"(?<![\w./-])"
    r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|txt|toml|json|md|html|js|ts|tsx|jsx|css|yaml|yml)"
    r"(?=$|[\s,;:，。；：、)\]}]|[.](?:\s|$))"
)


def _coerce_metadata_overrides(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    overrides: dict[str, Any] = {}
    for key, item in value.items():
        token = str(key or "").strip()
        if not token:
            continue
        overrides[token] = item
    return overrides


def _canonical_factory_stage_sequence(requested_stages: Any) -> list[str]:
    """Return the unique PM -> Chief Engineer -> Director factory task chain."""

    raw_stages = requested_stages if isinstance(requested_stages, (list, tuple)) else []
    normalized: list[str] = []
    for item in raw_stages:
        token = str(item or "").strip()
        if not token:
            continue
        stage = _FACTORY_STAGE_ALIASES.get(token.lower(), token)
        if stage not in normalized:
            normalized.append(stage)

    stages: list[str] = []
    if "docs_generation" in normalized:
        stages.append("docs_generation")
    stages.extend(_CANONICAL_FACTORY_TASK_CHAIN)
    return stages


def _task_id_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("id") or payload.get("task_id") or payload.get("pm_task_id") or "").strip()


def _validated_chief_engineer_handoff(
    workspace: str,
    payload: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    validation = validate_director_handoff_from_payload(workspace, payload)
    decision_payload_raw = validation.get("decision_payload")
    decision_payload: dict[str, Any] = decision_payload_raw if isinstance(decision_payload_raw, dict) else {}
    return bool(validation.get("allowed")), str(validation.get("reason") or ""), decision_payload


def _has_chief_engineer_handoff(payload: dict[str, Any], *, workspace: str = "") -> bool:
    if not workspace:
        return False
    return _validated_chief_engineer_handoff(workspace, payload)[0]


def _safe_path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _find_chief_engineer_blueprint_for_task(workspace: str, task_id: str) -> Path | None:
    task_token = str(task_id or "").strip()
    if not task_token:
        return None
    roots: list[Path] = []
    try:
        roots.append(Path(resolve_runtime_path(str(workspace), "runtime/blueprints")).resolve())
    except (OSError, RuntimeError, ValueError):
        logger.debug("Could not resolve runtime blueprints path for workspace=%s", workspace, exc_info=True)
    try:
        roots.append(Path(resolve_logical_path(str(workspace), "workspace/blueprints")).resolve())
    except (OSError, RuntimeError, ValueError):
        logger.debug("Could not resolve workspace blueprints path for workspace=%s", workspace, exc_info=True)
    roots.append((Path(workspace) / ".polaris" / "blueprints").resolve())

    seen: set[Path] = set()
    candidates: list[Path] = []
    for root in roots:
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        for path in root.glob("ce_*.json"):
            name = path.name
            if name == f"ce_{task_token}.json" or name.startswith(f"ce_{task_token}_"):
                candidates.append(path.resolve())
    if not candidates:
        return None
    candidates.sort(key=_safe_path_mtime, reverse=True)
    return candidates[0]


def _attach_chief_engineer_handoff_from_workspace(workspace: str, payload: dict[str, Any]) -> dict[str, Any]:
    task_id = _task_id_from_payload(payload)
    if not task_id:
        return payload
    if _has_chief_engineer_handoff(payload, workspace=workspace):
        return payload
    blueprint_path = _find_chief_engineer_blueprint_for_task(workspace, task_id)
    if blueprint_path is None:
        return payload
    candidate_id = blueprint_path.stem
    candidate_payload = dict(payload)
    candidate_metadata_raw = candidate_payload.get("metadata")
    candidate_metadata: dict[str, Any] = (
        dict(candidate_metadata_raw) if isinstance(candidate_metadata_raw, dict) else {}
    )
    candidate_metadata.setdefault("blueprint_id", candidate_id)
    candidate_metadata.setdefault("chief_engineer_blueprint_id", candidate_id)
    candidate_payload["metadata"] = candidate_metadata
    allowed, reason, decision_payload = _validated_chief_engineer_handoff(workspace, candidate_payload)
    if not allowed:
        logger.warning(
            "Ignoring invalid Chief Engineer blueprint handoff for task %s from %s: %s",
            task_id,
            blueprint_path,
            reason,
        )
        return payload

    updated = dict(payload)
    metadata_raw = updated.get("metadata")
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    metadata.setdefault("blueprint_id", candidate_id)
    metadata.setdefault("chief_engineer_blueprint_id", candidate_id)
    metadata.setdefault("chief_engineer_handoff_id", candidate_id)
    metadata.setdefault("blueprint_path", str(blueprint_path))
    metadata.setdefault("runtime_blueprint_path", str(blueprint_path))
    metadata.setdefault("handoff_ready", True)
    metadata.setdefault("handoff_source", "chief_engineer_blueprint_file")
    metadata.setdefault("handoff_decision", decision_payload)
    updated["metadata"] = metadata
    return updated


def _pm_task_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract PM task rows from the persisted PM task contract payload."""

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        tasks_value = payload.get("tasks")
        rows = tasks_value if isinstance(tasks_value, list) else []
    else:
        rows = []
    return [_augment_task_delivery_targets(dict(item)) for item in rows if isinstance(item, dict)]


def _augment_task_delivery_targets(row: dict[str, Any]) -> dict[str, Any]:
    """Include explicit file tokens from task text in Director write scope."""

    explicit_files = _extract_explicit_task_file_tokens(row)
    if not explicit_files:
        return row

    for field_name in ("target_files", "scope_paths"):
        existing = row.get(field_name)
        values = [str(item).strip() for item in existing] if isinstance(existing, list) else []
        if isinstance(existing, str) and existing.strip():
            values = [existing.strip()]
        seen = {item for item in values if item}
        for item in explicit_files:
            if item not in seen:
                values.append(item)
                seen.add(item)
        if values:
            row[field_name] = values
    return row


def _extract_explicit_task_file_tokens(row: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for field_name in (
        "goal",
        "description",
        "steps",
        "acceptance",
        "acceptance_criteria",
        "execution_checklist",
        "scope",
    ):
        raw_value = row.get(field_name)
        values = raw_value if isinstance(raw_value, (list, tuple, set)) else [raw_value]
        for value in values:
            text = str(value or "")
            for match in _TASK_TEXT_FILE_TOKEN_RE.finditer(text):
                token = _normalize_task_file_token(match.group(0))
                if token and token not in seen:
                    tokens.append(token)
                    seen.add(token)
    return tokens


def _normalize_task_file_token(value: str) -> str:
    token = str(value or "").replace("\\", "/").strip().strip("`'\"")
    while token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if not token or token.endswith("/"):
        return ""
    lowered = token.lower()
    if lowered.startswith(("http://", "https://")):
        return ""
    parts = tuple(part for part in token.split("/") if part)
    if not parts or any(part in {"", ".."} for part in parts):
        return ""
    if parts[0] in {".git", ".polaris", "runtime"}:
        return ""
    return token


def _task_identity_values(task: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    metadata_raw = task.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    for source in (task, metadata):
        for key in ("id", "task_id", "pm_task_id", "source_task_id", "external_task_id"):
            token = str(source.get(key) or "").strip()
            if token:
                values.add(token)
    return values


def _candidate_pm_task_payload_paths(workspace: str) -> list[Path]:
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []

    paths: list[Path] = []
    for resolver, rel_path in (
        (resolve_runtime_path, "runtime/contracts/pm_tasks.contract.json"),
        (resolve_runtime_path, "runtime/tasks/plan.json"),
        (resolve_logical_path, "workspace/plans/latest.plan.json"),
    ):
        try:
            paths.append(Path(resolver(workspace_token, rel_path)).resolve())
        except (OSError, RuntimeError, ValueError):
            logger.debug(
                "Could not resolve PM task payload path workspace=%s rel_path=%s",
                workspace_token,
                rel_path,
                exc_info=True,
            )

    workspace_root = Path(workspace_token).resolve()
    paths.append(workspace_root / ".polaris" / "plans" / "latest.plan.json")

    seen: set[Path] = set()
    unique_paths: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique_paths.append(path)
    return unique_paths


def _load_pm_task_contract_rows(workspace: str) -> list[dict[str, Any]]:
    """Read persisted PM task payloads from contracts, runtime plans, or Factory mirrors."""

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []
    for payload_path in _candidate_pm_task_payload_paths(workspace_token):
        if not payload_path.is_file():
            continue
        try:
            with payload_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            logger.debug("Could not read PM task payload path=%s", payload_path, exc_info=True)
            continue
        rows = _pm_task_rows_from_payload(payload)
        if rows:
            return rows
    return []


def _select_pm_task_payloads(workspace: str, task_ids: list[str]) -> list[dict[str, Any]]:
    """Return PM task payloads matching requested Director task IDs."""

    requested_ids = [str(item).strip() for item in task_ids if str(item).strip()]
    if not requested_ids:
        return []
    rows = _load_pm_task_contract_rows(workspace)
    if not rows:
        return []

    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        for token in _task_identity_values(row):
            by_id.setdefault(token, row)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for requested_id in requested_ids:
        matched_row = by_id.get(requested_id)
        if matched_row is None:
            continue
        primary_id = str(matched_row.get("id") or matched_row.get("task_id") or requested_id).strip() or requested_id
        if primary_id in seen:
            continue
        seen.add(primary_id)
        selected.append(_attach_chief_engineer_handoff_from_workspace(workspace, dict(matched_row)))
    return selected


def _director_role_entry_metadata(
    *,
    task_id: str,
    task_payload: dict[str, Any] | None,
    metadata_overrides: dict[str, Any],
) -> dict[str, Any]:
    """Build flattened Director role metadata from a PM task payload."""

    payload = dict(task_payload or {})
    nested_metadata_raw = payload.get("metadata")
    nested_metadata: dict[str, Any] = nested_metadata_raw if isinstance(nested_metadata_raw, dict) else {}
    metadata: dict[str, Any] = {}
    metadata.update(payload)
    metadata.update(dict(nested_metadata))
    metadata.update(metadata_overrides)
    normalized_task_id = str(
        metadata.get("task_id") or metadata.get("pm_task_id") or metadata.get("id") or task_id
    ).strip()
    metadata["task_id"] = normalized_task_id
    metadata["pm_task_id"] = normalized_task_id
    metadata.setdefault("source_task_id", normalized_task_id)
    metadata.setdefault("external_task_id", normalized_task_id)
    metadata.setdefault("director_task_source", "chief_engineer_handoff")
    metadata.setdefault("source", "chief_engineer_handoff")
    return metadata


def _director_role_entry_input(task_id: str, task_payload: dict[str, Any] | None) -> str:
    """Build Director user message with full PM task contract injection (D-09)."""
    payload = task_payload or {}
    title = str(payload.get("title") or payload.get("subject") or task_id).strip() or task_id
    goal = str(payload.get("goal") or payload.get("description") or "").strip()

    # Base message
    if goal and goal != title:
        base = f"Execute PM task {task_id}: {title}\nGoal: {goal}"
    else:
        base = f"Execute PM task {task_id}: {title}"

    # Build PM task contract section (D-09)
    contract_section = _build_pm_task_contract_section(payload)
    if contract_section:
        return f"{base}\n\n{contract_section}"
    return base


def _build_pm_task_contract_section(payload: dict[str, Any]) -> str:
    """Build structured PM task contract section for Director prompt context (D-09).

    Extracts acceptance_criteria, execution_checklist, target_files, scope_paths,
    and dependencies from the task payload and formats them as a clear section
    for the Director's user message.

    Args:
        payload: PM task payload dict (from pipeline_ports / task board row).

    Returns:
        Formatted contract section string, or empty string if no structured
        fields are present.
    """
    sections: list[str] = []

    # Acceptance criteria
    acceptance = payload.get("acceptance_criteria") or []
    if isinstance(acceptance, list) and acceptance:
        items = [str(item).strip() for item in acceptance if str(item).strip()]
        if items:
            criteria_text = "\n".join(f"- {item}" for item in items)
            sections.append(f"验收标准:\n{criteria_text}")

    # Execution checklist
    checklist = payload.get("execution_checklist") or []
    if isinstance(checklist, list) and checklist:
        items = [str(item).strip() for item in checklist if str(item).strip()]
        if items:
            checklist_text = "\n".join(f"- {item}" for item in items)
            sections.append(f"执行步骤:\n{checklist_text}")

    # Target files
    target_files = payload.get("target_files") or []
    if isinstance(target_files, list) and target_files:
        files = [str(f).strip() for f in target_files if str(f).strip()]
        if files:
            sections.append(f"目标文件: {', '.join(files)}")

    # Scope paths
    scope_paths = payload.get("scope_paths") or []
    if isinstance(scope_paths, list) and scope_paths:
        paths = [str(p).strip() for p in scope_paths if str(p).strip()]
        if paths:
            sections.append(f"作用域: {', '.join(paths)}")

    # Dependencies (supports both "dependencies" and "depends_on" keys)
    dependencies = payload.get("dependencies") or payload.get("depends_on") or []
    if isinstance(dependencies, list) and dependencies:
        deps = [str(d).strip() for d in dependencies if str(d).strip()]
        if deps:
            sections.append(f"依赖: {', '.join(deps)}")

    if not sections:
        return ""

    header = "【PM 任务合同】"
    return f"{header}\n" + "\n".join(sections)


@dataclass
class CommandResult:
    """Unified command execution result.

    Attributes:
        run_id: Unique run identifier
        status: Run status ("pending", "running", "completed", "failed", "not_implemented")
        message: Optional status message
        reason_code: Optional error/reason code for programmatic handling
        stage_results: Optional dict of stage execution results
        started_at: ISO format timestamp when run started
        completed_at: ISO format timestamp when run completed
        artifacts: Optional list of artifact dictionaries
        metadata: Optional dict of additional metadata
    """

    run_id: str
    status: str
    message: str | None = None
    reason_code: str | None = None
    stage_results: dict | None = None
    started_at: str | None = None
    completed_at: str | None = None
    artifacts: list[dict] | None = None
    metadata: dict | None = None


@dataclass
class PMRunOptions:
    """Options for PM run execution.

    Attributes:
        run_type: Type of PM run ("full", "architect", "pm")
        directive: Optional directive/requirement text
        run_director: Legacy flag that enables the PM -> Chief Engineer -> Director chain
        director_iterations: Number of Director iterations
    """

    run_type: str = "full"
    directive: str = ""
    run_director: bool = False
    director_iterations: int = 2


@dataclass
class DirectorRunOptions:
    """Options for Director run execution.

    Attributes:
        task_filter: Optional filter for task selection
        max_workers: Maximum parallel workers
        execution_mode: Execution mode ("serial", "parallel")
    """

    task_filter: str | None = None
    max_workers: int = field(default_factory=lambda: _DEFAULT_MAX_WORKERS)
    execution_mode: str = "parallel"


@dataclass
class FactoryRunOptions:
    """Options for Factory run execution.

    Attributes:
        config: Factory configuration dictionary
        auto_start: Whether to auto-start the run
    """

    config: dict[str, Any] = field(default_factory=dict)
    auto_start: bool = True


class OrchestrationCommandService:
    """Single entry point for all orchestration commands.

    This service provides unified execution paths for:
    - PM (Project Manager) runs
    - Director runs
    - Factory runs

    All execution goes through UnifiedOrchestrationService for consistency.
    """

    def __init__(self, settings: Any) -> None:
        """Initialize the command service.

        Args:
            settings: Application settings object
        """
        self.settings = settings
        self._active_runs: dict[str, dict] = {}

    async def execute_pm_run(
        self,
        workspace: str,
        run_type: str = "full",
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute PM run - unified entry point for PM orchestration.

        This is the ONLY entry point for PM execution. All PM runs must go
        through this method to ensure consistent run ID generation,
        logging, and error handling.

        Args:
            workspace: Workspace path
            run_type: Type of run ("full", "architect", "pm")
            options: Optional execution options dictionary

        Returns:
            CommandResult with run details and status

        Example:
            result = await service.execute_pm_run(
                workspace=".",
                run_type="architect",
                options={"directive": "Implement login feature"}
            )
        """
        run_id = self._generate_run_id("pm")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        pm_options = PMRunOptions(
            run_type=run_type,
            directive=opts.get("directive", ""),
            run_director=opts.get("run_director", False),
            director_iterations=opts.get("director_iterations", 2),
        )
        metadata_overrides = _coerce_metadata_overrides(opts.get("metadata"))

        try:
            # Get unified orchestration service and ensure adapters are registered
            service = await get_orchestration_service()
            register_all_adapters(service)

            # Determine role based on run_type
            role_id = "architect" if run_type == "architect" else "pm"

            # Build role entries
            # NOTE: scope_paths must include workspace for adapter path resolution
            # (PM adapter uses scope_paths[0] as workspace for resolve_runtime_path)
            role_entries = [
                RoleEntrySpec(
                    role_id=role_id,
                    input=pm_options.directive or f"Execute {run_type} phase",
                    scope_paths=[workspace],
                    metadata=metadata_overrides,
                )
            ]

            # Build orchestration request
            orch_request = OrchestrationRunRequest(
                run_id=run_id,
                workspace=Path(workspace),
                mode=OrchestrationMode.WORKFLOW,
                role_entries=role_entries,
                metadata={
                    "run_type": run_type,
                    "run_director": pm_options.run_director,
                    "director_iterations": pm_options.director_iterations,
                    "command_source": "orchestration_command_service",
                    **metadata_overrides,
                },
            )

            # Validate request
            errors = orch_request.validate()
            if errors:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Validation failed: {errors}",
                    reason_code="VALIDATION_FAILED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            # Submit run
            snapshot = await service.submit_run(orch_request)

            # Track active run
            self._active_runs[run_id] = {
                "workspace": workspace,
                "role": role_id,
                "started_at": started_at,
            }

            return CommandResult(
                run_id=run_id,
                status=snapshot.status.value,
                message=f"PM {run_type} run started",
                started_at=started_at,
                artifacts=[],
            )

        except (RuntimeError, ValueError) as e:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=str(e),
                reason_code="PM_RUN_FAILED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def execute_director_run(
        self,
        workspace: str,
        tasks: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute Director run - unified entry point for Director orchestration.

        This is the ONLY entry point for Director execution. All Director runs
        must go through this method to ensure consistent execution semantics.

        Args:
            workspace: Workspace path
            tasks: Optional list of specific task IDs to execute
            options: Optional execution options dictionary

        Returns:
            CommandResult with run details and status

        Example:
            result = await service.execute_director_run(
                workspace=".",
                tasks=["task-1", "task-2"],
                options={"max_workers": 3}
            )
        """
        run_id = self._generate_run_id("director")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        task_ids = [str(item).strip() for item in list(tasks or []) if str(item).strip()]
        director_options = DirectorRunOptions(
            task_filter=opts.get("task_filter"),
            max_workers=opts.get("max_workers", DEFAULT_DIRECTOR_MAX_PARALLELISM),
            execution_mode=opts.get("execution_mode", "parallel"),
        )
        metadata_overrides = _coerce_metadata_overrides(opts.get("metadata"))

        try:
            # Get unified orchestration service and ensure adapters are registered
            service = await get_orchestration_service()
            register_all_adapters(service)

            selected_task_payloads = _select_pm_task_payloads(workspace, task_ids)
            if selected_task_payloads:
                invalid_handoff_reasons: list[str] = []
                for task_payload in selected_task_payloads:
                    handoff_payload = dict(task_payload)
                    handoff_payload.update(metadata_overrides)
                    allowed, reason, _decision_payload = _validated_chief_engineer_handoff(workspace, handoff_payload)
                    if not allowed:
                        task_label = _task_id_from_payload(task_payload) or "<unknown>"
                        invalid_handoff_reasons.append(f"{task_label}: {reason}")
                if invalid_handoff_reasons:
                    return CommandResult(
                        run_id=run_id,
                        status="failed",
                        message=(
                            "Director run requires valid Chief Engineer blueprint/handoff evidence; "
                            + "; ".join(invalid_handoff_reasons)
                        ),
                        reason_code="CHIEF_ENGINEER_HANDOFF_REQUIRED",
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                role_entries = []
                for task_payload in selected_task_payloads:
                    task_id = str(
                        task_payload.get("id") or task_payload.get("task_id") or task_payload.get("pm_task_id") or ""
                    ).strip()
                    if not task_id:
                        continue
                    role_entries.append(
                        RoleEntrySpec(
                            role_id="director",
                            input=_director_role_entry_input(task_id, task_payload),
                            scope_paths=[workspace],
                            metadata=_director_role_entry_metadata(
                                task_id=task_id,
                                task_payload=task_payload,
                                metadata_overrides=metadata_overrides,
                            ),
                        )
                    )
            else:
                allowed, reason, _decision_payload = _validated_chief_engineer_handoff(workspace, metadata_overrides)
                if not allowed:
                    return CommandResult(
                        run_id=run_id,
                        status="failed",
                        message=f"Director run requires valid Chief Engineer blueprint/handoff evidence: {reason}",
                        reason_code="CHIEF_ENGINEER_HANDOFF_REQUIRED",
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                # Build role entries
                input_text = director_options.task_filter or "Execute ready tasks"
                if task_ids:
                    input_text = f"Execute tasks: {', '.join(task_ids)}"

                role_entries = [
                    RoleEntrySpec(
                        role_id="director",
                        input=input_text,
                        scope_paths=[workspace],
                        metadata=_director_role_entry_metadata(
                            task_id=task_ids[0] if len(task_ids) == 1 else "director",
                            task_payload=None,
                            metadata_overrides=metadata_overrides,
                        )
                        if task_ids
                        else metadata_overrides,
                    )
                ]

            # Build orchestration request
            orch_request = OrchestrationRunRequest(
                run_id=run_id,
                workspace=Path(workspace),
                mode=OrchestrationMode.WORKFLOW,
                role_entries=role_entries,
                metadata={
                    "tasks": task_ids,
                    "pm_task_payloads": selected_task_payloads,
                    "max_workers": director_options.max_workers,
                    "execution_mode": director_options.execution_mode,
                    "command_source": "orchestration_command_service",
                    **metadata_overrides,
                },
            )

            # Validate request
            errors = orch_request.validate()
            if errors:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Validation failed: {errors}",
                    reason_code="VALIDATION_FAILED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            # Submit run
            snapshot = await service.submit_run(orch_request)
            snapshot_task_ids = list(snapshot.tasks.keys())
            queued_task_ids = snapshot_task_ids or task_ids

            # Track active run
            self._active_runs[run_id] = {
                "workspace": workspace,
                "role": "director",
                "started_at": started_at,
            }

            return CommandResult(
                run_id=run_id,
                status=snapshot.status.value,
                message=f"Director started in {director_options.execution_mode} mode",
                started_at=started_at,
                artifacts=[],
                metadata={
                    "tasks_queued": len(queued_task_ids),
                    "task_ids": queued_task_ids,
                    "snapshot_task_ids": snapshot_task_ids,
                    "requested_task_ids": task_ids,
                },
            )

        except (RuntimeError, ValueError) as e:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=str(e),
                reason_code="DIRECTOR_RUN_FAILED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def execute_qa_run(
        self,
        workspace: str,
        target: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute QA run - unified entry point for QA orchestration."""
        run_id = self._generate_run_id("qa")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        input_text = opts.get("input") or target or "Run quality gate checks on completed tasks"

        try:
            service = await get_orchestration_service()

            role_entries = [
                RoleEntrySpec(
                    role_id="qa",
                    input=input_text,
                    scope_paths=[workspace],
                )
            ]

            orch_request = OrchestrationRunRequest(
                run_id=run_id,
                workspace=Path(workspace),
                mode=OrchestrationMode.WORKFLOW,
                role_entries=role_entries,
                metadata={
                    "command_source": "orchestration_command_service",
                    "qa_target": target or "",
                },
            )

            errors = orch_request.validate()
            if errors:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Validation failed: {errors}",
                    reason_code="VALIDATION_FAILED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            snapshot = await service.submit_run(orch_request)

            self._active_runs[run_id] = {
                "workspace": workspace,
                "role": "qa",
                "started_at": started_at,
            }

            return CommandResult(
                run_id=run_id,
                status=snapshot.status.value,
                message="QA run started",
                started_at=started_at,
                artifacts=[],
            )

        except (RuntimeError, ValueError) as e:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=str(e),
                reason_code="QA_RUN_FAILED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def execute_factory_run(
        self,
        workspace: str,
        config: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute Factory run - unified entry point for Factory orchestration.

        This is the entry point for Factory execution. It delegates to
        FactoryRunService for actual execution.

        Args:
            workspace: Workspace path
            config: Optional Factory configuration
            options: Optional execution options dictionary

        Returns:
            CommandResult with run details and status

        Example:
            result = await service.execute_factory_run(
                workspace=".",
                config={"stages": ["docs", "pm", "chief_engineer", "director"]}
            )
        """
        run_id = self._generate_run_id("factory")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        factory_options = FactoryRunOptions(
            config=config or {},
            auto_start=opts.get("auto_start", True),
        )

        # Use FactoryRunService for actual execution
        try:
            from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService

            factory_service = FactoryRunService(workspace=Path(workspace))

            requested_stages = opts.get(
                "stages",
                ["docs_generation", *_CANONICAL_FACTORY_TASK_CHAIN],
            )
            stages = _canonical_factory_stage_sequence(requested_stages)
            config = FactoryConfig(
                name=f"orch_factory_{run_id}",
                description="Factory run from orchestration command",
                stages=stages,
                auto_dispatch=factory_options.auto_start,
            )

            run = await factory_service.create_run(config)

            if factory_options.auto_start:
                await factory_service.start_run(run.id)

            return CommandResult(
                run_id=run.id,
                status=run.status,
                message=f"Factory run created: {run.status}",
                started_at=run.started_at or started_at,
                artifacts=[],
            )
        except (RuntimeError, ValueError) as e:
            logger.error(f"Factory run creation failed: {e}")
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=f"Factory run failed: {e}",
                reason_code="FACTORY_RUN_CREATION_FAILED",
                started_at=started_at,
                artifacts=[],
            )

    def _generate_run_id(self, prefix: str = "run") -> str:
        """Generate unique run ID.

        Args:
            prefix: ID prefix ("pm", "director", "factory")

        Returns:
            Unique run identifier string
        """
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def get_run_status(self, run_id: str) -> CommandResult | None:
        """Get status of an active run.

        Args:
            run_id: Run identifier

        Returns:
            CommandResult if run exists, None otherwise
        """
        if run_id in self._active_runs:
            run_info = self._active_runs[run_id]
            return CommandResult(
                run_id=run_id,
                status=run_info.get("status", "unknown"),
                message=run_info.get("message"),
                started_at=run_info.get("started_at"),
            )
        return None

    @staticmethod
    def _trim_error_text(value: str | None, limit: int = 240) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    @classmethod
    def _build_failed_task_summaries(cls, snapshot: Any) -> list[dict[str, Any]]:
        failed_statuses = {"failed", "blocked", "cancelled", "timeout"}
        rows: list[dict[str, Any]] = []
        tasks = getattr(snapshot, "tasks", {})
        if not isinstance(tasks, dict):
            return rows

        for task in tasks.values():
            status_obj = getattr(task, "status", None)
            status = str(getattr(status_obj, "value", status_obj) or "").strip().lower()
            if status not in failed_statuses:
                continue
            row = {
                "task_id": str(getattr(task, "task_id", "") or "").strip(),
                "role_id": str(getattr(task, "role_id", "") or "").strip(),
                "status": status,
                "error_category": str(getattr(task, "error_category", "") or "").strip() or None,
                "error_message": cls._trim_error_text(getattr(task, "error_message", None)),
                "updated_at": (
                    updated_at.isoformat() if (updated_at := getattr(task, "updated_at", None)) is not None else None
                ),
            }
            rows.append(row)

        rows.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        causality_priority = {
            "failed": 0,
            "cancelled": 0,
            "timeout": 0,
            "blocked": 1,
        }
        rows.sort(key=lambda item: causality_priority.get(str(item.get("status") or ""), 99))
        return rows

    @staticmethod
    def _build_task_status_counts(snapshot: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        tasks = getattr(snapshot, "tasks", {})
        if not isinstance(tasks, dict):
            return counts
        for task in tasks.values():
            status_obj = getattr(task, "status", None)
            status = str(getattr(status_obj, "value", status_obj) or "").strip().lower()
            if not status:
                continue
            counts[status] = counts.get(status, 0) + 1
        return counts

    async def query_run_status(self, run_id: str) -> CommandResult:
        """Query orchestration snapshot for authoritative run status."""
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)
        if not snapshot:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=f"Run {run_id} not found",
                reason_code="RUN_NOT_FOUND",
            )

        run_status = str(snapshot.status.value or "").strip().lower()
        failed_tasks = self._build_failed_task_summaries(snapshot)
        message = f"Run status: {snapshot.status.value}"
        if run_status in {"failed", "blocked", "cancelled", "timeout"} and failed_tasks:
            primary = failed_tasks[0]
            task_ref = str(primary.get("task_id") or "unknown_task").strip()
            role_ref = str(primary.get("role_id") or "unknown_role").strip()
            error_ref = str(primary.get("error_message") or primary.get("error_category") or "unknown_error").strip()
            message = f"Run status: {snapshot.status.value} | failed_task={task_ref} ({role_ref}) | error={error_ref}"

        return CommandResult(
            run_id=run_id,
            status=snapshot.status.value,
            message=message,
            started_at=snapshot.created_at.isoformat() if snapshot.created_at else None,
            completed_at=snapshot.completed_at.isoformat() if snapshot.completed_at else None,
            metadata={
                "current_phase": snapshot.current_phase.value,
                "overall_progress": snapshot.overall_progress,
                "task_count": len(snapshot.tasks),
                "task_status_counts": self._build_task_status_counts(snapshot),
                "failed_task_count": len(failed_tasks),
                "failed_tasks": failed_tasks[:20],
            },
        )

    def list_active_runs(self, workspace: str | None = None) -> list[dict[str, Any]]:
        """List active runs, optionally filtered by workspace.

        Args:
            workspace: Optional workspace filter

        Returns:
            List of active run information dictionaries
        """
        runs = []
        for run_id, info in self._active_runs.items():
            if workspace is None or info.get("workspace") == workspace:
                runs.append({"run_id": run_id, **info})
        return runs

    def clear_completed_runs(self) -> int:
        """Clear completed/failed runs from tracking.

        Returns:
            Number of runs cleared
        """
        to_clear = [
            run_id
            for run_id, info in self._active_runs.items()
            if info.get("status") in ("completed", "failed", "cancelled")
        ]
        for run_id in to_clear:
            del self._active_runs[run_id]
        return len(to_clear)


# ============================================================================
# Convenience Functions
# ============================================================================


async def execute_pm_command(
    workspace: str,
    run_type: str = "full",
    options: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> CommandResult:
    """Convenience function to execute PM command without instantiating service.

    Args:
        workspace: Workspace path
        run_type: Type of PM run
        options: Optional execution options
        settings: Optional settings object

    Returns:
        CommandResult with execution status
    """
    service = OrchestrationCommandService(settings)
    return await service.execute_pm_run(workspace, run_type, options)


async def execute_director_command(
    workspace: str,
    tasks: list[str] | None = None,
    options: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> CommandResult:
    """Convenience function to execute Director command without instantiating service.

    Args:
        workspace: Workspace path
        tasks: Optional list of task IDs
        options: Optional execution options
        settings: Optional settings object

    Returns:
        CommandResult with execution status
    """
    service = OrchestrationCommandService(settings)
    return await service.execute_director_run(workspace, tasks, options)


async def execute_factory_command(
    workspace: str,
    config: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> CommandResult:
    """Convenience function to execute Factory command without instantiating service.

    Args:
        workspace: Workspace path
        config: Optional Factory configuration
        options: Optional execution options
        settings: Optional settings object

    Returns:
        CommandResult with execution status
    """
    service = OrchestrationCommandService(settings)
    return await service.execute_factory_run(workspace, config, options)


__all__ = [
    "CommandResult",
    "DirectorRunOptions",
    "FactoryRunOptions",
    "OrchestrationCommandService",
    "PMRunOptions",
    "execute_director_command",
    "execute_factory_command",
    "execute_pm_command",
]
