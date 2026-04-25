"""Evidence Bundle Service (Cell Implementation).

Responsible for creating, storing, and querying EvidenceBundles (collections
of changes, test results, and performance snapshots).

状态管理策略：
- EvidenceBundleService 是无状态的服务对象（所有状态落在文件系统）。
- 不维护任何模块级实例；调用方通过 create_evidence_bundle_service() 按需
  创建，或通过 DI 注入。测试可直接实例化 EvidenceBundleService() 而无需
  清理副作用。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from polaris.domain.entities.evidence_bundle import (
    ChangeType,
    EvidenceBundle,
    FileChange,
    PerfEvidence,
    SourceType,
    TestRunEvidence,
)
from polaris.domain.language import detect_language
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)

# External storage threshold: 100KB
EXTERNAL_STORAGE_THRESHOLD = 100 * 1024


def _get_fs_adapter() -> Any:
    return get_default_adapter()


def _get_bundle_storage_path(workspace: str, bundle_id: str) -> Path:
    """Get bundle storage path."""
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    return Path(workspace) / get_workspace_metadata_dir_name() / "bundles" / bundle_id


def _get_bundle_index_path(workspace: str) -> Path:
    """Get bundle index path."""
    return Path(resolve_runtime_path(workspace, "runtime/evidence_index.jsonl"))


def _workspace_fs(workspace: str) -> KernelFileSystem:
    return KernelFileSystem(str(Path(workspace).resolve()), _get_fs_adapter())


def _workspace_rel(fs: KernelFileSystem, absolute_or_relative_path: Path | str) -> str:
    return fs.to_workspace_relative_path(str(absolute_or_relative_path))


class EvidenceBundleService:
    """Service for managing EvidenceBundles."""

    def create_from_working_tree(
        self,
        workspace: str,
        base_sha: str,
        source_type: SourceType,
        source_run_id: str | None = None,
        source_task_id: str | None = None,
        source_goal_id: str | None = None,
        test_results: TestRunEvidence | None = None,
        performance_snapshot: PerfEvidence | None = None,
    ) -> EvidenceBundle:
        """Create an evidence bundle from current working tree changes."""
        workspace_root = str(Path(workspace).resolve())
        fs = _workspace_fs(workspace_root)
        bundle_id = str(uuid.uuid4())
        storage_path = _get_bundle_storage_path(workspace_root, bundle_id)

        change_set = self._collect_working_tree_changes(workspace_root, base_sha)

        # Use immutable pattern: create new FileChange objects instead of mutating
        processed_changes = []
        for change in change_set:
            if change.is_large_patch and change.patch:
                patch_file = storage_path / f"{change.path.replace('/', '__')}.patch"
                patch_rel = _workspace_rel(fs, patch_file)
                fs.workspace_write_text(patch_rel, change.patch, encoding="utf-8")
                processed_changes.append(replace(change, patch_ref=patch_rel, patch=None))
            else:
                processed_changes.append(change)
        change_set = processed_changes

        bundle = EvidenceBundle(
            bundle_id=bundle_id,
            workspace=workspace_root,
            base_sha=base_sha,
            head_sha=None,
            working_tree_dirty=True,
            change_set=change_set,
            source_type=source_type,
            source_run_id=source_run_id,
            source_task_id=source_task_id,
            source_goal_id=source_goal_id,
            test_results=test_results,
            performance_snapshot=performance_snapshot,
        )

        self._save_bundle(bundle, storage_path)
        self._update_index(workspace, bundle)

        return bundle

    def create_from_director_run(
        self,
        workspace: str,
        run_id: str,
        task_results: list[dict],
        base_sha: str | None = None,
    ) -> EvidenceBundle:
        """Create an evidence bundle from director run results."""
        workspace_root = str(Path(workspace).resolve())
        fs = _workspace_fs(workspace_root)
        bundle_id = str(uuid.uuid4())
        storage_path = _get_bundle_storage_path(workspace_root, bundle_id)

        if base_sha is None:
            base_sha = self._get_current_commit(workspace_root)

        change_set = self._collect_changes_from_tasks(workspace_root, task_results)

        # Use immutable pattern: create new FileChange objects instead of mutating
        processed_changes = []
        for change in change_set:
            if change.is_large_patch and change.patch:
                patch_file = storage_path / f"{change.path.replace('/', '__')}.patch"
                patch_rel = _workspace_rel(fs, patch_file)
                fs.workspace_write_text(patch_rel, change.patch, encoding="utf-8")
                processed_changes.append(replace(change, patch_ref=patch_rel, patch=None))
            else:
                processed_changes.append(change)
        change_set = processed_changes

        bundle = EvidenceBundle(
            bundle_id=bundle_id,
            workspace=workspace_root,
            base_sha=base_sha,
            head_sha=None,
            working_tree_dirty=True,
            change_set=change_set,
            source_type=SourceType.DIRECTOR_RUN,
            source_run_id=run_id,
        )

        self._save_bundle(bundle, storage_path)
        self._update_index(workspace, bundle)

        return bundle

    def get_bundle(self, workspace: str, bundle_id: str) -> EvidenceBundle | None:
        """Get bundle details."""
        workspace_root = str(Path(workspace).resolve())
        fs = _workspace_fs(workspace_root)
        storage_path = _get_bundle_storage_path(workspace_root, bundle_id)
        bundle_file = storage_path / "bundle.json"
        bundle_rel = _workspace_rel(fs, bundle_file)

        if not fs.workspace_exists(bundle_rel) or not fs.workspace_is_file(bundle_rel):
            return None

        try:
            data = json.loads(fs.workspace_read_text(bundle_rel, encoding="utf-8"))
            bundle = EvidenceBundle.from_dict(data)

            # Use immutable pattern: create new FileChange objects with loaded patches
            processed_changes = []
            for change in bundle.change_set:
                if change.patch_ref and not change.patch:
                    patch_ref = str(change.patch_ref).strip()
                    if patch_ref and fs.workspace_exists(patch_ref) and fs.workspace_is_file(patch_ref):
                        loaded_patch = fs.workspace_read_text(patch_ref, encoding="utf-8")
                        processed_changes.append(replace(change, patch=loaded_patch))
                    else:
                        processed_changes.append(change)
                else:
                    processed_changes.append(change)

            if processed_changes != list(bundle.change_set):
                bundle = replace(bundle, change_set=processed_changes)

            return bundle
        except Exception as e:
            logger.error(
                "Failed to load bundle %s: %s",
                bundle_id,
                e,
                exc_info=True,
            )
            return None

    def list_bundles(
        self,
        workspace: str,
        source_type: SourceType | None = None,
        source_run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List bundles metadata from index."""
        workspace_root = str(Path(workspace).resolve())
        fs = _workspace_fs(workspace_root)
        index_path = _get_bundle_index_path(workspace_root)
        index_rel = _workspace_rel(fs, index_path)
        if not fs.workspace_exists(index_rel) or not fs.workspace_is_file(index_rel):
            return []

        results = []
        raw_lines = fs.workspace_read_text(index_rel, encoding="utf-8").splitlines()
        for line in raw_lines:
            text = line.strip()
            if not text:
                continue
            try:
                entry = json.loads(text)
                if source_type and entry.get("source_type") != source_type.value:
                    continue
                if source_run_id and entry.get("source_run_id") != source_run_id:
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            except json.JSONDecodeError:
                continue

        return results

    def _collect_working_tree_changes(self, workspace: str, base_sha: str) -> list[FileChange]:
        """Collect working tree changes via git diff."""
        changes: list[FileChange] = []
        cmd_svc = CommandExecutionService(workspace)
        try:
            # Get changed files
            request = CommandRequest(
                executable="git",
                args=["diff", "--name-status", base_sha],
                cwd=workspace,
                timeout_seconds=30,
            )
            result = cmd_svc.run(request)
            if not result.get("ok") or result.get("returncode", -1) != 0:
                return changes

            for line in result.get("stdout", "").strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status = parts[0][0]
                path = parts[1]
                change_type = {
                    "M": ChangeType.MODIFIED,
                    "A": ChangeType.ADDED,
                    "D": ChangeType.DELETED,
                    "R": ChangeType.RENAMED,
                }.get(status, ChangeType.MODIFIED)

                # Get patch for this file
                patch_request = CommandRequest(
                    executable="git",
                    args=["diff", base_sha, "--", path],
                    cwd=workspace,
                    timeout_seconds=30,
                )
                patch_result = cmd_svc.run(patch_request)
                patch = patch_result.get("stdout") if patch_result.get("ok") else None

                lines_added = 0
                lines_deleted = 0
                if patch:
                    for diff_line in patch.split("\n"):
                        if diff_line.startswith("+") and not diff_line.startswith("+++"):
                            lines_added += 1
                        elif diff_line.startswith("-") and not diff_line.startswith("---"):
                            lines_deleted += 1

                changes.append(
                    FileChange(
                        path=path,
                        change_type=change_type,
                        patch=patch,
                        language=detect_language(path),
                        lines_added=lines_added,
                        lines_deleted=lines_deleted,
                    )
                )
        except Exception as e:
            logger.error(
                "Failed to collect working tree changes from workspace %s against base %s: %s",
                workspace,
                base_sha,
                e,
                exc_info=True,
            )
        return changes

    def _collect_changes_from_tasks(self, workspace: str, task_results: list[dict]) -> list[FileChange]:
        """Collect changes from task results."""
        changes = []
        seen_files = set()

        for task in task_results:
            for file_change in task.get("file_changes", []):
                path = file_change.get("path")
                if not path or path in seen_files:
                    continue
                seen_files.add(path)

                changes.append(
                    FileChange(
                        path=path,
                        change_type=ChangeType(file_change.get("change_type", "modified")),
                        patch=file_change.get("patch"),
                        language=detect_language(path),
                        lines_added=file_change.get("lines_added", 0),
                        lines_deleted=file_change.get("lines_deleted", 0),
                    )
                )
        return changes

    def _save_bundle(self, bundle: EvidenceBundle, storage_path: Path) -> None:
        """Save bundle to disk."""
        fs = _workspace_fs(bundle.workspace)
        bundle_file = storage_path / "bundle.json"
        bundle_rel = _workspace_rel(fs, bundle_file)
        fs.workspace_write_text(bundle_rel, bundle.to_json() + "\n", encoding="utf-8")

    def _update_index(self, workspace: str, bundle: EvidenceBundle) -> None:
        """Update bundle index."""
        workspace_root = str(Path(workspace).resolve())
        fs = _workspace_fs(workspace_root)
        index_path = _get_bundle_index_path(workspace_root)
        index_rel = _workspace_rel(fs, index_path)

        entry = {
            "bundle_id": bundle.bundle_id,
            "created_at": bundle.created_at.isoformat(),
            "workspace": bundle.workspace,
            "base_sha": bundle.base_sha,
            "source_type": bundle.source_type.value,
            "source_run_id": bundle.source_run_id,
            "affected_files": bundle.affected_files,
        }

        fs.workspace_append_text(
            index_rel,
            json.dumps(entry, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _get_current_commit(self, workspace: str) -> str:
        """Get current HEAD commit SHA.

        Returns the SHA string on success.  On failure, returns the sentinel
        value ``"sha-unknown"`` and logs a warning so callers and auditors can
        detect that the baseline is unreliable rather than silently trusting a
        generic ``"unknown"`` string.
        """
        cmd_svc = CommandExecutionService(workspace)
        try:
            request = CommandRequest(
                executable="git",
                args=["rev-parse", "HEAD"],
                cwd=workspace,
                timeout_seconds=10,
            )
            result = cmd_svc.run(request)
            if result.get("ok") and result.get("returncode", -1) == 0:
                sha = result.get("stdout", "").strip()
                if sha:
                    return sha
            logger.warning(
                "git rev-parse HEAD returned non-zero or empty output in workspace %s; "
                "bundle base_sha will be set to 'sha-unknown'",
                workspace,
            )
        except (OSError, ValueError):
            logger.warning(
                "Failed to resolve HEAD SHA for workspace %s; bundle base_sha will be set to 'sha-unknown'",
                workspace,
                exc_info=True,
            )
        return "sha-unknown"


def create_evidence_bundle_service() -> EvidenceBundleService:
    """工厂函数：创建一个新的 EvidenceBundleService 实例。

    EvidenceBundleService 是无状态的——所有持久数据均落在文件系统，因此
    按需创建实例没有任何资源开销。调用方（应用层、DI 容器）负责管理实例
    生命周期；测试直接使用 ``EvidenceBundleService()`` 即可，无需清理全局
    状态。

    Returns:
        EvidenceBundleService: 全新的服务实例。
    """
    return EvidenceBundleService()


# 向后兼容别名（旧名称已不再持有全局单例，每次调用均返回新实例）
get_evidence_bundle_service = create_evidence_bundle_service
