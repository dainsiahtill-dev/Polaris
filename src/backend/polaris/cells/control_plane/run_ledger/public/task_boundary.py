"""Task-boundary verdict contracts for execution control-plane projections."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

_LOCAL_ENTRYPOINT_SUFFIXES = (
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".go",
    ".rs",
    ".html",
)


def _clean_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    return token.strip("/")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    rows: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = _clean_path(item)
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _path_exists(workspace: Path, relative_path: str) -> bool:
    token = _clean_path(relative_path)
    if not token:
        return False
    try:
        resolved = (workspace / token).resolve()
        root = workspace.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return resolved.exists()


def _package_json_entrypoints(workspace: Path) -> list[str]:
    manifest = workspace / "package.json"
    if not manifest.is_file():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    candidates: list[str] = []
    for key in ("main", "module", "browser"):
        value = data.get(key)
        if isinstance(value, str):
            candidates.append(value)
    bin_value = data.get("bin")
    if isinstance(bin_value, str):
        candidates.append(bin_value)
    elif isinstance(bin_value, dict):
        candidates.extend(str(value) for value in bin_value.values() if isinstance(value, str))

    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for script in scripts.values():
            if not isinstance(script, str):
                continue
            try:
                parts = shlex.split(script)
            except ValueError:
                parts = script.split()
            for part in parts:
                token = part.strip().strip("'\"")
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", token):
                    continue
                if token.startswith("-"):
                    continue
                if token.endswith(_LOCAL_ENTRYPOINT_SUFFIXES) or ("/" in token and "." in Path(token).name):
                    candidates.append(token)

    rows: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = _clean_path(candidate)
        if not token or token.startswith("node_modules/"):
            continue
        if token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _html_script_entrypoints(workspace: Path) -> list[str]:
    candidates: list[str] = []
    for html_path in workspace.rglob("*.html"):
        relative = _clean_path(html_path.relative_to(workspace))
        if relative.startswith((".polaris/", "runtime/", "node_modules/")):
            continue
        try:
            content = html_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", content, re.IGNORECASE):
            src = _clean_path(match.group(1).split("#", 1)[0].split("?", 1)[0])
            if not src or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", src):
                continue
            candidates.append(_clean_path(html_path.parent.relative_to(workspace) / src))
    return _dedupe_paths(candidates)


def _go_entrypoints(workspace: Path) -> list[str]:
    if not (workspace / "go.mod").is_file():
        return []
    if (workspace / "main.go").is_file() or any(workspace.glob("cmd/*/main.go")):
        return []
    return ["main.go"]


def _pyproject_entrypoints(workspace: Path) -> list[str]:
    manifest = workspace / "pyproject.toml"
    if not manifest.is_file():
        return []
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return []
    candidates: list[str] = []
    for value in scripts.values():
        if not isinstance(value, str):
            continue
        module = value.split(":", 1)[0].strip().replace(".", "/")
        if module:
            candidates.append(f"{module}.py")
    return _dedupe_paths(candidates)


def _dedupe_paths(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = _clean_path(value)
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _workspace_entrypoint_targets(workspace: Path) -> list[str]:
    return _dedupe_paths(
        [
            *_package_json_entrypoints(workspace),
            *_html_script_entrypoints(workspace),
            *_go_entrypoints(workspace),
            *_pyproject_entrypoints(workspace),
        ]
    )


def _required_items_not_satisfied(
    *,
    required: tuple[str, ...],
    present: tuple[str, ...],
    failed: tuple[str, ...],
    explicit_missing: tuple[str, ...],
) -> tuple[str, ...]:
    """Return required obligations with no present or failed evidence."""

    observed = set(present) | set(failed)
    missing: list[str] = [item for item in required if item not in observed]
    for item in explicit_missing:
        if item not in observed and item not in missing:
            missing.append(item)
    return tuple(missing)


@dataclass(frozen=True)
class TaskBoundaryVerdictV1:
    """Canonical task-boundary completion verdict.

    The verdict is intentionally platform-level: it explains whether one task
    can be considered materially complete before QA tries to classify product
    behavior. It is not a repair plan and does not mutate TaskBoard state.
    """

    task_id: str
    status: str
    ok: bool
    failure_class: str
    responsible_layer: str
    reason: str
    run_id: str = ""
    target_files: tuple[str, ...] = field(default_factory=tuple)
    missing_target_files: tuple[str, ...] = field(default_factory=tuple)
    missing_entrypoint_targets: tuple[str, ...] = field(default_factory=tuple)
    downstream_pending_artifacts: tuple[str, ...] = field(default_factory=tuple)
    completed_artifacts: tuple[str, ...] = field(default_factory=tuple)
    blocked_dependencies: tuple[str, ...] = field(default_factory=tuple)
    required_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    present_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    missing_required_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    failed_required_evidence_modalities: tuple[str, ...] = field(default_factory=tuple)
    required_verifiers: tuple[str, ...] = field(default_factory=tuple)
    completed_verifiers: tuple[str, ...] = field(default_factory=tuple)
    missing_required_verifiers: tuple[str, ...] = field(default_factory=tuple)
    failed_required_verifiers: tuple[str, ...] = field(default_factory=tuple)
    tool_dispatch: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "polaris.task_boundary_verdict.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "ok": bool(self.ok),
            "failure_class": self.failure_class,
            "responsible_layer": self.responsible_layer,
            "reason": self.reason,
            "target_files": list(self.target_files),
            "missing_target_files": list(self.missing_target_files),
            "missing_entrypoint_targets": list(self.missing_entrypoint_targets),
            "downstream_pending_artifacts": list(self.downstream_pending_artifacts),
            "completed_artifacts": list(self.completed_artifacts),
            "blocked_dependencies": list(self.blocked_dependencies),
            "required_evidence_modalities": list(self.required_evidence_modalities),
            "present_evidence_modalities": list(self.present_evidence_modalities),
            "missing_required_evidence_modalities": list(self.missing_required_evidence_modalities),
            "failed_required_evidence_modalities": list(self.failed_required_evidence_modalities),
            "required_verifiers": list(self.required_verifiers),
            "completed_verifiers": list(self.completed_verifiers),
            "missing_required_verifiers": list(self.missing_required_verifiers),
            "failed_required_verifiers": list(self.failed_required_verifiers),
            "tool_dispatch": dict(self.tool_dispatch),
            "evidence_refs": list(self.evidence_refs),
        }


def build_completed_task_boundary_verdict(
    *,
    task_id: str,
    run_id: str = "",
    target_files: list[str] | tuple[str, ...] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Return a completed task-boundary verdict."""

    targets = tuple(_string_list(target_files))
    return TaskBoundaryVerdictV1(
        task_id=str(task_id or "").strip(),
        run_id=str(run_id or "").strip(),
        status="completed_verified",
        ok=True,
        failure_class="PASSED",
        responsible_layer="execution_control_plane",
        reason="Task boundary materialization and entrypoint obligations are satisfied",
        target_files=targets,
        evidence_refs=tuple(_string_list(evidence_refs)),
    )


def build_deferred_followup_task_boundary_verdict(
    *,
    task_id: str,
    run_id: str = "",
    reason: str = "",
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Return a verdict for work that requires another governed execution step."""

    return TaskBoundaryVerdictV1(
        task_id=str(task_id or "").strip(),
        run_id=str(run_id or "").strip(),
        status="deferred_followup_required",
        ok=False,
        failure_class="DEFERRED_FOLLOWUP_REQUIRED",
        responsible_layer="execution_control_plane",
        reason=str(reason or "Execution requires a governed follow-up workflow before completion").strip(),
        evidence_refs=tuple(_string_list(evidence_refs)),
    )


def evaluate_task_boundary_verdict(
    *,
    workspace: str | Path,
    task_id: str,
    run_id: str = "",
    target_files: list[str] | tuple[str, ...] | None = None,
    completed_artifacts: list[str] | tuple[str, ...] | None = None,
    downstream_pending_artifacts: list[str] | tuple[str, ...] | None = None,
    blocked_dependencies: list[str] | tuple[str, ...] | None = None,
    required_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    present_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    missing_required_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    failed_required_evidence_modalities: list[str] | tuple[str, ...] | None = None,
    required_verifiers: list[str] | tuple[str, ...] | None = None,
    completed_verifiers: list[str] | tuple[str, ...] | None = None,
    missing_required_verifiers: list[str] | tuple[str, ...] | None = None,
    failed_required_verifiers: list[str] | tuple[str, ...] | None = None,
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Evaluate task-boundary completeness from deterministic workspace facts."""

    workspace_path = Path(workspace).expanduser().resolve()
    targets = tuple(_string_list(target_files))
    completed = tuple(_string_list(completed_artifacts))
    downstream = tuple(_string_list(downstream_pending_artifacts))
    blocked = tuple(_string_list(blocked_dependencies))
    required_evidence = tuple(_string_list(required_evidence_modalities))
    present_evidence = tuple(_string_list(present_evidence_modalities))
    failed_evidence = tuple(_string_list(failed_required_evidence_modalities))
    missing_evidence = _required_items_not_satisfied(
        required=required_evidence,
        present=present_evidence,
        failed=failed_evidence,
        explicit_missing=tuple(_string_list(missing_required_evidence_modalities)),
    )
    required_verifier_names = tuple(_string_list(required_verifiers))
    completed_verifier_names = tuple(_string_list(completed_verifiers))
    failed_verifier_names = tuple(_string_list(failed_required_verifiers))
    missing_verifier_names = _required_items_not_satisfied(
        required=required_verifier_names,
        present=completed_verifier_names,
        failed=failed_verifier_names,
        explicit_missing=tuple(_string_list(missing_required_verifiers)),
    )
    evidence = tuple(_string_list(evidence_refs))
    dispatch = dict(tool_dispatch or {})
    base_kwargs: dict[str, Any] = {
        "task_id": str(task_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "target_files": targets,
        "completed_artifacts": completed,
        "downstream_pending_artifacts": downstream,
        "blocked_dependencies": blocked,
        "required_evidence_modalities": required_evidence,
        "present_evidence_modalities": present_evidence,
        "missing_required_evidence_modalities": missing_evidence,
        "failed_required_evidence_modalities": failed_evidence,
        "required_verifiers": required_verifier_names,
        "completed_verifiers": completed_verifier_names,
        "missing_required_verifiers": missing_verifier_names,
        "failed_required_verifiers": failed_verifier_names,
        "tool_dispatch": dispatch,
        "evidence_refs": evidence,
    }

    if bool(dispatch.get("dropped")) or str(dispatch.get("status") or "").strip() == "dropped":
        return TaskBoundaryVerdictV1(
            status="tool_dispatch_dropped",
            ok=False,
            failure_class="TOOL_DISPATCH_DROPPED",
            responsible_layer="execution_control_plane",
            reason="Provider emitted tool calls, but no authoritative tool dispatch receipt was committed",
            **base_kwargs,
        )

    if blocked:
        return TaskBoundaryVerdictV1(
            status="dependency_not_unlocked",
            ok=False,
            failure_class="DEPENDENCY_NOT_UNLOCKED",
            responsible_layer="execution_control_plane",
            reason="Task dependencies are not unlocked for completion",
            **base_kwargs,
        )

    missing_targets = tuple(path for path in targets if not _path_exists(workspace_path, path))
    if missing_targets:
        return TaskBoundaryVerdictV1(
            status="incomplete_materialization",
            ok=False,
            failure_class="INCOMPLETE_MATERIALIZATION",
            responsible_layer="director",
            reason="Required target files were not materialized",
            missing_target_files=missing_targets,
            **base_kwargs,
        )

    known_artifacts = set(targets) | set(completed) | set(downstream)
    missing_entrypoints = tuple(
        entrypoint
        for entrypoint in _workspace_entrypoint_targets(workspace_path)
        if not _path_exists(workspace_path, entrypoint) and entrypoint not in known_artifacts
    )
    if missing_entrypoints:
        return TaskBoundaryVerdictV1(
            status="missing_entrypoint_target",
            ok=False,
            failure_class="MISSING_ENTRYPOINT_TARGET",
            responsible_layer="task_boundary",
            reason="Manifest references local entrypoint files that are neither complete nor declared downstream",
            missing_entrypoint_targets=missing_entrypoints,
            **base_kwargs,
        )

    if missing_evidence:
        return TaskBoundaryVerdictV1(
            status="execution_evidence_missing",
            ok=False,
            failure_class="EXECUTION_EVIDENCE_MISSING",
            responsible_layer="director",
            reason="Required execution evidence was not committed",
            **base_kwargs,
        )

    if failed_evidence:
        return TaskBoundaryVerdictV1(
            status="required_evidence_failed",
            ok=False,
            failure_class="IMPLEMENTATION_DEFECT",
            responsible_layer="director",
            reason="Required execution evidence was committed but failed",
            **base_kwargs,
        )

    if missing_verifier_names:
        return TaskBoundaryVerdictV1(
            status="required_verifier_missing",
            ok=False,
            failure_class="EXECUTION_EVIDENCE_MISSING",
            responsible_layer="director",
            reason="Required verifier execution was not committed",
            **base_kwargs,
        )

    if failed_verifier_names:
        return TaskBoundaryVerdictV1(
            status="required_verifier_failed",
            ok=False,
            failure_class="IMPLEMENTATION_DEFECT",
            responsible_layer="director",
            reason="Required verifier execution failed",
            **base_kwargs,
        )

    return TaskBoundaryVerdictV1(
        status="completed_verified",
        ok=True,
        failure_class="PASSED",
        responsible_layer="execution_control_plane",
        reason="Task boundary materialization, entrypoint, evidence, and verifier obligations are satisfied",
        **base_kwargs,
    )


def normalize_task_boundary_verdict(value: Any) -> dict[str, Any]:
    """Return a safe task-boundary verdict mapping."""

    if isinstance(value, TaskBoundaryVerdictV1):
        return value.to_dict()
    if isinstance(value, dict):
        payload = dict(value)
        payload.setdefault("schema_version", "polaris.task_boundary_verdict.v1")
        payload.setdefault("status", "unknown")
        payload.setdefault("ok", False)
        payload.setdefault("failure_class", "TASK_BOUNDARY_UNKNOWN")
        payload.setdefault("responsible_layer", "execution_control_plane")
        payload.setdefault("reason", "Task boundary verdict was incomplete")
        return payload
    return {
        "schema_version": "polaris.task_boundary_verdict.v1",
        "status": "unknown",
        "ok": False,
        "failure_class": "TASK_BOUNDARY_UNKNOWN",
        "responsible_layer": "execution_control_plane",
        "reason": "Task boundary verdict was missing",
    }


__all__ = [
    "TaskBoundaryVerdictV1",
    "build_completed_task_boundary_verdict",
    "build_deferred_followup_task_boundary_verdict",
    "evaluate_task_boundary_verdict",
    "normalize_task_boundary_verdict",
]
