"""文件变更追踪器 (File Change Tracker)

实时追踪任务执行期间的文件变更，提供 C/M/D 文件数和 +/-/* 行数统计。

架构位置：核心编排基础设施 (Core Orchestration Infrastructure)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest

logger = logging.getLogger(__name__)


@dataclass
class FileChange:
    """单个文件变更"""

    path: str
    change_type: str  # added, modified, deleted
    lines_added: int = 0
    lines_removed: int = 0
    lines_changed: int = 0  # 对于 modified 文件


@dataclass
class FileChangeSnapshot:
    """文件变更快照"""

    created: int = 0
    modified: int = 0
    deleted: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    lines_changed: int = 0
    files: list[FileChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "modified": self.modified,
            "deleted": self.deleted,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "lines_changed": self.lines_changed,
            "file_count": len(self.files),
        }


class FileChangeTracker:
    """文件变更追踪器

    使用 git diff 统计文件变更，支持:
    - 任务开始前/后对比
    - 实时更新（通过定时采样）
    - C/M/D 文件计数
    - +/-/* 行数统计
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).resolve()
        self._baseline: dict[str, str] | None = None  # 文件哈希基线
        self._is_git_repo: bool | None = None
        self._cmd_svc = CommandExecutionService(str(self.workspace))

    def _check_git_repo(self) -> bool:
        """检查是否为 git 仓库"""
        if self._is_git_repo is None:
            git_dir = self.workspace / ".git"
            self._is_git_repo = git_dir.exists()
        return self._is_git_repo

    def capture_baseline(self) -> None:
        """捕获基线状态"""
        if not self._check_git_repo():
            self._baseline = self._capture_filesystem_baseline()
        else:
            self._baseline = self._capture_git_baseline()

    def _capture_git_baseline(self) -> dict[str, str]:
        """使用 git 捕获基线"""
        try:
            request = CommandRequest(
                executable="git",
                args=["ls-files", "--stage"],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)
            if not result.get("ok") or result.get("returncode", -1) != 0:
                return {}

            baseline: dict[str, str] = {}
            stdout = result.get("stdout", "")
            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                # 格式: <mode> <object> <stage>\t<file>
                parts = line.split("\t")
                if len(parts) == 2:
                    meta, filepath = parts
                    hash_val = meta.split()[1] if len(meta.split()) > 1 else ""
                    baseline[filepath] = hash_val

            return baseline
        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to capture git baseline for workspace %s",
                self.workspace,
                exc_info=True,
            )
            return {}

    def _capture_filesystem_baseline(self) -> dict[str, str]:
        """使用文件系统捕获基线"""
        baseline = {}
        try:
            for root, _, files in os.walk(self.workspace):
                # 跳过隐藏目录和 runtime 目录
                if any(part.startswith(".") for part in Path(root).parts):
                    continue
                if ".polaris" in root or ".polaris" in root or "__pycache__" in root:
                    continue

                for filename in files:
                    filepath = Path(root) / filename
                    try:
                        stat = filepath.stat()
                        rel_path = str(filepath.relative_to(self.workspace))
                        baseline[rel_path] = f"{stat.st_mtime}_{stat.st_size}"
                    except OSError as exc:
                        logger.warning(
                            "Could not stat file %s during filesystem baseline: %s",
                            filepath,
                            exc,
                        )
                        continue
        except OSError:
            logger.warning(
                "Failed to walk workspace %s for filesystem baseline",
                self.workspace,
                exc_info=True,
            )

        return baseline

    def get_changes(self) -> FileChangeSnapshot:
        """获取相对于基线的变更"""
        if self._baseline is None:
            self.capture_baseline()
            return FileChangeSnapshot()

        if self._check_git_repo():
            return self._get_git_changes()
        else:
            return self._get_filesystem_changes()

    def _get_git_changes(self) -> FileChangeSnapshot:
        """使用 git diff 获取变更"""
        snapshot = FileChangeSnapshot()

        try:
            # Get git diff --numstat output
            request = CommandRequest(
                executable="git",
                args=["diff", "--numstat", "HEAD"],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)

            if not result.get("ok") or result.get("returncode", -1) != 0:
                return snapshot

            stdout = result.get("stdout", "")

            # Get deleted files once for all files (batch operation)
            deleted_files = self._get_deleted_files()

            # Get untracked (new) files once for all files (batch operation)
            new_files = self._get_new_files()

            # Parse numstat output
            # 格式: <added>\t<removed>\t<file>
            for line in stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) != 3:
                    continue

                added_str, removed_str, filepath = parts

                try:
                    added = int(added_str) if added_str != "-" else 0
                    removed = int(removed_str) if removed_str != "-" else 0
                except ValueError:
                    continue

                # Determine change type from pre-fetched sets
                change_type = self._classify_change_from_sets(filepath, deleted_files, new_files)

                change = FileChange(
                    path=filepath,
                    change_type=change_type,
                    lines_added=added,
                    lines_removed=removed,
                    lines_changed=min(added, removed),  # 同时存在于旧新版本的行
                )

                snapshot.files.append(change)
                snapshot.lines_added += added
                snapshot.lines_removed += removed
                snapshot.lines_changed += change.lines_changed

                if change_type == "added":
                    snapshot.created += 1
                elif change_type == "deleted":
                    snapshot.deleted += 1
                else:
                    snapshot.modified += 1

        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to compute git changes for workspace %s; returning empty snapshot",
                self.workspace,
                exc_info=True,
            )

        return snapshot

    def _get_deleted_files(self) -> set[str]:
        """Get set of deleted files in a single git command."""
        try:
            request = CommandRequest(
                executable="git",
                args=["diff", "--name-only", "--diff-filter=D", "HEAD"],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)
            if result.get("ok") and result.get("returncode", -1) == 0:
                stdout = result.get("stdout", "")
                return {line.strip() for line in stdout.strip().split("\n") if line.strip()}
        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to fetch deleted files list for workspace %s",
                self.workspace,
                exc_info=True,
            )
        return set()

    def _get_new_files(self) -> set[str]:
        """Get set of new (untracked) files in a single git command."""
        try:
            request = CommandRequest(
                executable="git",
                args=["ls-files", "--others", "--exclude-standard"],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)
            if result.get("ok") and result.get("returncode", -1) == 0:
                stdout = result.get("stdout", "")
                return {line.strip() for line in stdout.strip().split("\n") if line.strip()}
        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to fetch new (untracked) files list for workspace %s",
                self.workspace,
                exc_info=True,
            )
        return set()

    def _get_tracked_files(self) -> set[str]:
        """Get set of tracked files in a single git command."""
        try:
            request = CommandRequest(
                executable="git",
                args=["ls-files"],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)
            if result.get("ok") and result.get("returncode", -1) == 0:
                stdout = result.get("stdout", "")
                return {line.strip() for line in stdout.strip().split("\n") if line.strip()}
        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to fetch tracked files list for workspace %s",
                self.workspace,
                exc_info=True,
            )
        return set()

    def _classify_change_from_sets(self, filepath: str, deleted_files: set[str], new_files: set[str]) -> str:
        """Classify file change type using pre-fetched sets.

        This avoids spawning subprocesses per file, reducing overhead from
        O(n) subprocess calls to O(1) set lookups.
        """
        if filepath in deleted_files:
            return "deleted"
        if filepath in new_files:
            return "added"
        return "modified"

    def _classify_change(self, filepath: str) -> str:
        """分类文件变更类型 (legacy method, kept for compatibility).

        Note: This method spawns 2 subprocesses per call. For batch operations,
        use _get_git_changes() which uses _classify_change_from_sets() instead.
        """
        try:
            # Check if file is in index
            request = CommandRequest(
                executable="git",
                args=["ls-files", "--error-unmatch", filepath],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)

            if not result.get("ok") or result.get("returncode", -1) != 0:
                # 文件不在索引中，是新增
                return "added"

            # Check if file is deleted
            request = CommandRequest(
                executable="git",
                args=["diff", "--name-only", "--diff-filter=D", "HEAD"],
                cwd=str(self.workspace),
                timeout_seconds=30,
            )
            result = self._cmd_svc.run(request)
            stdout = result.get("stdout", "") if result.get("ok") else ""

            if filepath in stdout:
                return "deleted"

            return "modified"
        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to classify change type for file %s in workspace %s; defaulting to 'modified'",
                filepath,
                self.workspace,
                exc_info=True,
            )
            return "modified"

    def _get_filesystem_changes(self) -> FileChangeSnapshot:
        """使用文件系统比较获取变更"""
        snapshot = FileChangeSnapshot()

        try:
            current = self._capture_filesystem_baseline()
            baseline = self._baseline
            if baseline is None:
                return snapshot

            # 找出新增和修改的文件
            for path, hash_val in current.items():
                if path not in baseline:
                    snapshot.created += 1
                    snapshot.files.append(
                        FileChange(
                            path=path,
                            change_type="added",
                        )
                    )
                elif baseline.get(path) != hash_val:
                    snapshot.modified += 1
                    snapshot.files.append(
                        FileChange(
                            path=path,
                            change_type="modified",
                        )
                    )

            # 找出删除的文件
            for path in baseline:
                if path not in current:
                    snapshot.deleted += 1
                    snapshot.files.append(
                        FileChange(
                            path=path,
                            change_type="deleted",
                        )
                    )

        except (RuntimeError, ValueError):
            logger.warning(
                "Failed to compute filesystem changes for workspace %s; returning partial snapshot",
                self.workspace,
                exc_info=True,
            )

        return snapshot

    def reset(self) -> None:
        """重置基线"""
        self._baseline = None


class TaskFileChangeTracker:
    """任务级别的文件变更追踪器"""

    def __init__(self, workspace: str, task_id: str) -> None:
        self.workspace = workspace
        self.task_id = task_id
        self.tracker = FileChangeTracker(workspace)
        self._started = False

    def start(self) -> None:
        """开始追踪"""
        self.tracker.capture_baseline()
        self._started = True

    def get_progress(self) -> FileChangeSnapshot:
        """获取当前进度"""
        if not self._started:
            self.start()
            return FileChangeSnapshot()

        return self.tracker.get_changes()

    def finish(self) -> FileChangeSnapshot:
        """结束追踪并返回最终变更"""
        result = self.get_progress()
        self.tracker.reset()
        self._started = False
        return result


__all__ = [
    "FileChange",
    "FileChangeSnapshot",
    "FileChangeTracker",
    "TaskFileChangeTracker",
]
