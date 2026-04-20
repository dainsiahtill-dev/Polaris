"""RollbackGuard system for DirectorTaskWorkflow.

Provides two rollback strategies:
- RollbackGuard: Director-level memory snapshot for parallel mode.
- GitStashRollbackGuard: git stash fallback for serial degrade mode.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_STASH_LOCK = threading.Lock()


def _resolve_safe_path(workspace: Path, file_path: str) -> Path | None:
    try:
        target = (workspace / file_path).resolve()
        workspace_resolved = workspace.resolve()
        if workspace_resolved not in target.parents and target != workspace_resolved:
            return None
        return target
    except (OSError, ValueError):
        return None


class RollbackGuard:
    """Director-level memory snapshot (parallel mode)."""

    def __init__(self, workspace: str) -> None:
        self._workspace = Path(workspace)
        self._snapshots: dict[str, dict[str, str | None]] = {}

    async def snapshot_for_director(self, director_id: str, files: list[str]) -> None:
        """Read each file and store original content in memory."""
        snapshot: dict[str, str | None] = {}
        for file_path in files:
            target = _resolve_safe_path(self._workspace, file_path)
            if target is None:
                logger.warning(
                    "RollbackGuard: path traversal blocked for %s in %s",
                    file_path,
                    director_id,
                )
                continue
            if target.is_dir():
                logger.warning(
                    "RollbackGuard: skipping directory %s for %s",
                    file_path,
                    director_id,
                )
                continue
            try:
                content = target.read_text(encoding="utf-8")
                snapshot[file_path] = content
            except FileNotFoundError:
                snapshot[file_path] = None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RollbackGuard: failed to snapshot %s for %s: %s",
                    file_path,
                    director_id,
                    exc,
                )
                snapshot[file_path] = None
        self._snapshots[director_id] = snapshot

    async def rollback_director(self, director_id: str) -> bool:
        """Restore each file to its snapshot content."""
        snapshot = self._snapshots.pop(director_id, None)
        if not snapshot:
            return False
        for file_path, content in snapshot.items():
            target = _resolve_safe_path(self._workspace, file_path)
            if target is None:
                logger.warning(
                    "RollbackGuard: path traversal blocked during rollback for %s in %s",
                    file_path,
                    director_id,
                )
                continue
            try:
                if content is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RollbackGuard: failed to restore %s for %s: %s",
                    file_path,
                    director_id,
                    exc,
                )
        return True

    def discard_snapshot(self, director_id: str) -> None:
        """Remove the snapshot entry without restoring."""
        self._snapshots.pop(director_id, None)

    def has_snapshot(self, director_id: str) -> bool:
        """Check whether a snapshot exists for the given director."""
        return director_id in self._snapshots


class GitStashRollbackGuard:
    """Git stash fallback (serial degrade mode)."""

    def __init__(self, workspace: str) -> None:
        self._workspace = Path(workspace)

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self._workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _find_stash_index(self, task_id: str) -> str | None:
        message = f"polaris-snapshot-{task_id}"
        result = self._run_git(["stash", "list"])
        for line in result.stdout.splitlines():
            if message in line:
                parts = line.split(":", 1)
                if parts:
                    return parts[0].strip()
        return None

    def create_snapshot(self, task_id: str) -> str | None:
        """Run git stash push for the given task."""
        message = f"polaris-snapshot-{task_id}"
        with _GIT_STASH_LOCK:
            result = self._run_git(["stash", "push", "-u", "-m", message])
            if result.returncode != 0:
                logger.warning(
                    "GitStashRollbackGuard: stash push failed for %s: %s",
                    task_id,
                    result.stderr.strip(),
                )
                return None
        return message

    def rollback(self, task_id: str) -> bool:
        """Find stash by message and pop it."""
        with _GIT_STASH_LOCK:
            stash_index = self._find_stash_index(task_id)
            if stash_index is None:
                return False
            result = self._run_git(["stash", "pop", stash_index])
            if result.returncode != 0:
                logger.warning(
                    "GitStashRollbackGuard: stash pop failed for %s: %s",
                    task_id,
                    result.stderr.strip(),
                )
                return False
        return True

    def discard_snapshot(self, task_id: str) -> bool:
        """Find stash by message and drop it."""
        with _GIT_STASH_LOCK:
            stash_index = self._find_stash_index(task_id)
            if stash_index is None:
                return False
            result = self._run_git(["stash", "drop", stash_index])
            if result.returncode != 0:
                logger.warning(
                    "GitStashRollbackGuard: stash drop failed for %s: %s",
                    task_id,
                    result.stderr.strip(),
                )
                return False
        return True


def create_rollback_guard(
    workspace: str,
    director_pool_mode: bool = True,
) -> RollbackGuard | GitStashRollbackGuard:
    """Factory function to create the appropriate rollback guard."""
    if director_pool_mode:
        return RollbackGuard(workspace)
    return GitStashRollbackGuard(workspace)
