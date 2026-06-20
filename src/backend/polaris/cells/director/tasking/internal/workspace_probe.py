"""Workspace filesystem probe collaborator for the Director worker.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 4). ``WorkspaceProbe`` owns every workspace-relative filesystem read the
worker performs while orchestrating a task: path resolution, target-file
snapshots / change detection, code-generation round progress markers, file
signatures and generated-artifact quality scanning.

The marker round-trip and file-signature semantics MUST stay byte-identical to
the original implementation; the bodies below are moved verbatim.

This module depends only on the standard library + kernelone (``PathSecurityError``,
``scan_workspace_artifact_quality``, ``KernelFileSystem`` via the lazy
``_workspace_fs`` helper) + domain (``Task``). It MUST NOT import
``code_generation_engine`` / ``file_apply_service`` at module top.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

from polaris.domain.entities import Task
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.quality.artifact_quality import scan_workspace_artifact_quality


def _workspace_fs(workspace: str) -> Any:
    from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

    return KernelFileSystem(str(workspace), get_default_adapter())


class WorkspaceProbe:
    """Read-only workspace filesystem probe bound to a single workspace root."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def generated_artifact_quality_error(self, files_created: list[dict[str, Any]], *, phase: str) -> str | None:
        """Return a fail-closed quality error for generated artifact receipts."""
        relative_paths = self.generated_artifact_paths(files_created)
        if not relative_paths:
            return f"{phase} quality gate failed: no changed files to evaluate"

        missing_paths: list[str] = []
        for path in relative_paths:
            full_path = self.resolve_workspace_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                missing_paths.append(path)
        if missing_paths:
            return f"{phase} quality gate failed: changed file receipts missing on disk: {', '.join(missing_paths[:6])}"

        errors = scan_workspace_artifact_quality(self.workspace, relative_paths=relative_paths)
        if errors:
            return f"{phase} quality gate failed: {'; '.join(errors[:6])}"
        return None

    @staticmethod
    def generated_artifact_paths(files_created: list[dict[str, Any]]) -> list[str]:
        """Extract stable workspace-relative paths from generated file receipts."""
        paths: list[str] = []
        seen: set[str] = set()
        for item in files_created:
            if not isinstance(item, dict) or bool(item.get("deleted")):
                continue
            raw_path = item.get("path") or item.get("file")
            path = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def resolve_workspace_file_path(self, relative_path: str) -> str | None:
        """Resolve a workspace-relative file path without allowing traversal."""
        path = str(relative_path or "").strip()
        if not path or os.path.isabs(path):
            return None
        workspace_abs = os.path.abspath(self.workspace)
        full_path = os.path.abspath(os.path.join(workspace_abs, path))
        try:
            if os.path.commonpath([workspace_abs, full_path]) != workspace_abs:
                return None
        except ValueError:
            return None
        return full_path

    def snapshot_workspace_files(self, paths: list[str]) -> dict[str, tuple[bool, int, int]]:
        """Capture initial target-file signatures for over-completion detection."""
        snapshot: dict[str, tuple[bool, int, int]] = {}
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in snapshot:
                continue
            full_path = self.resolve_workspace_file_path(path)
            if full_path is None:
                continue
            try:
                stat = os.stat(full_path)
            except OSError:
                snapshot[path] = (False, -1, -1)
            else:
                snapshot[path] = (True, int(stat.st_size), int(stat.st_mtime_ns))
        return snapshot

    def round_files_changed_since(
        self,
        paths: list[str],
        initial_signatures: dict[str, tuple[bool, int, int]],
    ) -> bool:
        """Return true when every path in a round was created or changed."""
        normalized = [str(path or "").strip() for path in paths if str(path or "").strip()]
        if not normalized:
            return False
        for path in normalized:
            full_path = self.resolve_workspace_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                return False
            try:
                stat = os.stat(full_path)
            except OSError:
                return False
            current = (True, int(stat.st_size), int(stat.st_mtime_ns))
            if current == initial_signatures.get(path, (False, -1, -1)):
                return False
        return True

    def collect_existing_file_records(self, paths: list[str]) -> list[dict[str, str]]:
        """Return lightweight records for existing workspace files."""
        records: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            full_path = self.resolve_workspace_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                continue
            seen.add(path)
            records.append({"path": path, "content": ""})
        return records

    def code_generation_round_marker_path(self, task: Task, round_index: int) -> str:
        """Return the persisted progress marker path for one task round."""
        raw_task_id = str(getattr(task, "id", "") or getattr(task, "subject", "") or "task")
        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_task_id).strip("._") or "task"
        return os.path.join(
            self.workspace,
            ".polaris",
            "runtime",
            "director",
            "codegen_progress",
            safe_task_id,
            f"round-{round_index:03d}.json",
        )

    def workspace_file_signature(self, relative_path: str) -> dict[str, int | str] | None:
        """Return a stable file signature for a workspace-relative file."""
        fs = _workspace_fs(self.workspace)
        try:
            full_path = fs.resolve_workspace_path(relative_path)
        except (OSError, PathSecurityError, ValueError):
            return None
        if not os.path.isfile(full_path):
            return None
        try:
            stat = os.stat(full_path)
            content = fs.workspace_read_text(relative_path, encoding="utf-8")
        except OSError:
            return None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": digest,
        }

    def write_code_generation_round_marker(
        self,
        task: Task,
        round_index: int,
        round_paths: list[str],
    ) -> None:
        """Persist successful round progress so process-level retries can resume."""
        normalized_paths = [str(path or "").strip() for path in round_paths if str(path or "").strip()]
        if not normalized_paths:
            return
        signatures: dict[str, dict[str, int | str]] = {}
        for path in normalized_paths:
            signature = self.workspace_file_signature(path)
            if signature is None:
                return
            signatures[path] = signature
        marker_path = self.code_generation_round_marker_path(task, round_index)
        payload = {
            "schema_version": 1,
            "task_id": str(getattr(task, "id", "") or ""),
            "round_index": round_index,
            "target_files": normalized_paths,
            "signatures": signatures,
            "timestamp_epoch": time.time(),
        }
        fs = _workspace_fs(self.workspace)
        fs.workspace_write_text(
            marker_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def code_generation_round_marker_satisfied(
        self,
        task: Task,
        round_index: int,
        round_paths: list[str],
    ) -> bool:
        """Return whether a previous process already completed this round."""
        normalized_paths = [str(path or "").strip() for path in round_paths if str(path or "").strip()]
        if not normalized_paths:
            return False
        marker_path = self.code_generation_round_marker_path(task, round_index)
        try:
            fs = _workspace_fs(self.workspace)
            payload = json.loads(fs.workspace_read_text(marker_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
            return False
        target_files = payload.get("target_files")
        signatures = payload.get("signatures")
        if target_files != normalized_paths or not isinstance(signatures, dict):
            return False
        for path in normalized_paths:
            expected = signatures.get(path)
            current = self.workspace_file_signature(path)
            if not isinstance(expected, dict) or current != expected:
                return False
        return True
