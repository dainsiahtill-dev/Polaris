"""Task-boundary verdict contracts for execution control-plane projections."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


def evaluate_task_boundary_verdict(
    *,
    workspace: str | Path,
    task_id: str,
    run_id: str = "",
    target_files: list[str] | tuple[str, ...] | None = None,
    completed_artifacts: list[str] | tuple[str, ...] | None = None,
    downstream_pending_artifacts: list[str] | tuple[str, ...] | None = None,
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> TaskBoundaryVerdictV1:
    """Evaluate task-boundary completeness from deterministic workspace facts."""

    workspace_path = Path(workspace).expanduser().resolve()
    targets = tuple(_string_list(target_files))
    completed = tuple(_string_list(completed_artifacts))
    downstream = tuple(_string_list(downstream_pending_artifacts))
    evidence = tuple(_string_list(evidence_refs))
    dispatch = dict(tool_dispatch or {})

    if bool(dispatch.get("dropped")) or str(dispatch.get("status") or "").strip() == "dropped":
        return TaskBoundaryVerdictV1(
            task_id=str(task_id or "").strip(),
            run_id=str(run_id or "").strip(),
            status="tool_dispatch_dropped",
            ok=False,
            failure_class="TOOL_DISPATCH_DROPPED",
            responsible_layer="execution_control_plane",
            reason="Provider emitted tool calls, but no authoritative tool dispatch receipt was committed",
            target_files=targets,
            completed_artifacts=completed,
            downstream_pending_artifacts=downstream,
            tool_dispatch=dispatch,
            evidence_refs=evidence,
        )

    missing_targets = tuple(path for path in targets if not _path_exists(workspace_path, path))
    if missing_targets:
        return TaskBoundaryVerdictV1(
            task_id=str(task_id or "").strip(),
            run_id=str(run_id or "").strip(),
            status="incomplete_materialization",
            ok=False,
            failure_class="INCOMPLETE_MATERIALIZATION",
            responsible_layer="director",
            reason="Required target files were not materialized",
            target_files=targets,
            missing_target_files=missing_targets,
            completed_artifacts=completed,
            downstream_pending_artifacts=downstream,
            tool_dispatch=dispatch,
            evidence_refs=evidence,
        )

    known_artifacts = set(targets) | set(completed) | set(downstream)
    missing_entrypoints = tuple(
        entrypoint
        for entrypoint in _package_json_entrypoints(workspace_path)
        if not _path_exists(workspace_path, entrypoint) and entrypoint not in known_artifacts
    )
    if missing_entrypoints:
        return TaskBoundaryVerdictV1(
            task_id=str(task_id or "").strip(),
            run_id=str(run_id or "").strip(),
            status="missing_entrypoint_target",
            ok=False,
            failure_class="MISSING_ENTRYPOINT_TARGET",
            responsible_layer="task_boundary",
            reason="Manifest references local entrypoint files that are neither complete nor declared downstream",
            target_files=targets,
            missing_entrypoint_targets=missing_entrypoints,
            completed_artifacts=completed,
            downstream_pending_artifacts=downstream,
            tool_dispatch=dispatch,
            evidence_refs=evidence,
        )

    return build_completed_task_boundary_verdict(
        task_id=task_id,
        run_id=run_id,
        target_files=targets,
        evidence_refs=evidence,
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
    "evaluate_task_boundary_verdict",
    "normalize_task_boundary_verdict",
]
