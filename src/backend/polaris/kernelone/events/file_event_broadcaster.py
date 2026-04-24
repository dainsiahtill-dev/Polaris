"""File event broadcaster - 统一文件变更事件广播

用于在文件被修改时广播事件到前端，支持实时 diff 显示。

所有文件写入操作都应该使用此模块来确保事件一致性。
"""

import difflib
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from polaris.kernelone.constants import BROADCAST_MAX_SIZE_BYTES
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter

_MESSAGE_BUS_TYPES: tuple[Any, Any] | None = None
_MESSAGE_BUS_TYPES_LOCK = Lock()
_PENDING_BROADCAST_TASKS: set[Any] = set()
_PENDING_BROADCAST_TASKS_LOCK = Lock()
logger = logging.getLogger(__name__)


def _get_fs_adapter():
    return get_default_adapter()


def _get_message_bus_imports():
    """延迟导入避免循环依赖"""
    global _MESSAGE_BUS_TYPES
    with _MESSAGE_BUS_TYPES_LOCK:
        if _MESSAGE_BUS_TYPES is None:
            from polaris.kernelone.events.message_bus import MessageBus, MessageType

            _MESSAGE_BUS_TYPES = (MessageBus, MessageType)
        return _MESSAGE_BUS_TYPES


def _track_broadcast_task(task: Any) -> None:
    with _PENDING_BROADCAST_TASKS_LOCK:
        _PENDING_BROADCAST_TASKS.add(task)

    def _cleanup(done_task: Any) -> None:
        with _PENDING_BROADCAST_TASKS_LOCK:
            _PENDING_BROADCAST_TASKS.discard(done_task)

    task.add_done_callback(_cleanup)


def shutdown_broadcast_tasks() -> int:
    """Cancel and clear all pending broadcast tasks.

    Returns:
        Number of cancelled tasks.
    """
    with _PENDING_BROADCAST_TASKS_LOCK:
        count = len(_PENDING_BROADCAST_TASKS)
        for task in list(_PENDING_BROADCAST_TASKS):
            task.cancel()
        _PENDING_BROADCAST_TASKS.clear()
        return count


def calculate_patch(old_content: str, new_content: str) -> str:
    """计算 unified diff 格式的 patch

    Args:
        old_content: 原始文件内容
        new_content: 新文件内容

    Returns:
        unified diff 格式的 patch 字符串
    """
    if not old_content:
        # 新文件 - 返回全部内容作为新增
        return new_content

    if old_content == new_content:
        return ""

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="a", tofile="b", lineterm=""))
    return "\n".join(diff)


def _calculate_line_stats(patch: str, operation: str) -> tuple[int, int, int]:
    """Compute added/deleted/modified line counts from unified diff text.

    When patch is not in unified diff form (e.g. create with raw content),
    fall back to counting raw lines as additions/deletions by operation.
    """
    text = str(patch or "")
    op = str(operation or "modify").strip().lower()
    if not text:
        return 0, 0, 0

    lines = text.splitlines()
    has_diff_markers = any(
        line.startswith("@@") or line.startswith("+++ ") or line.startswith("--- ") for line in lines
    )
    if not has_diff_markers:
        raw_count = len([line for line in lines if line.strip() != ""])
        if op == "delete":
            return 0, raw_count, 0
        return raw_count, 0, 0

    plus = 0
    minus = 0
    for line in lines:
        if not line:
            continue
        if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            plus += 1
            continue
        if line.startswith("-"):
            minus += 1

    modified = min(plus, minus)
    added = max(0, plus - modified)
    deleted = max(0, minus - modified)
    return added, deleted, modified


# File extension patterns to skip from broadcasting
_BROADCAST_SKIP_PATTERNS: tuple[str, ...] = (".tmp", ".log", ".cache", ".pyc", "__pycache__")
# Maximum file size (1MB) above which we skip broadcasting
_BROADCAST_MAX_SIZE: int = BROADCAST_MAX_SIZE_BYTES


def broadcast_file_written(
    file_path: str,
    operation: str,
    content_size: int,
    task_id: str = "",
    patch: str = "",
    message_bus=None,
    worker_id: str = "standalone",
) -> bool:
    """广播文件写入事件到前端

    Args:
        file_path: 文件相对路径
        operation: 操作类型 (create/modify/delete)
        content_size: 文件大小（字节）
        task_id: 关联的任务 ID
        patch: diff patch 内容
        message_bus: MessageBus 实例（可选）
        worker_id: Worker ID

    Returns:
        是否成功广播
    """
    if not message_bus:
        return False

    # Skip broadcasting for certain file types and sizes
    file_lower = file_path.lower()
    if any(file_lower.endswith(ext) or ext in file_lower for ext in _BROADCAST_SKIP_PATTERNS):
        return False
    if content_size > _BROADCAST_MAX_SIZE:
        return False

    try:
        _message_bus_cls, _msg_type = _get_message_bus_imports()

        import asyncio

        payload = {
            "file_path": file_path,
            "operation": operation,
            "content_size": content_size,
            "task_id": task_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if patch:
            payload["patch"] = patch
        added_lines, deleted_lines, modified_lines = _calculate_line_stats(
            patch,
            operation,
        )
        payload["added_lines"] = int(added_lines)
        payload["deleted_lines"] = int(deleted_lines)
        payload["modified_lines"] = int(modified_lines)

        # 尝试获取 event loop
        try:
            loop = asyncio.get_running_loop()
            # 在运行中的事件循环中，创建任务
            task = loop.create_task(message_bus.broadcast(_msg_type.FILE_WRITTEN, f"worker-{worker_id}", payload))
            _track_broadcast_task(task)
            return True
        except RuntimeError:
            # Never broadcast across a foreign loop: MessageBus internals may
            # hold loop-bound locks/queues. Drop with explicit warning instead
            # of creating a new loop and risking cross-loop crashes.
            logger.warning(
                "Skip FILE_WRITTEN broadcast without running loop (file=%s, worker=%s)",
                file_path,
                worker_id,
            )
            return False

    except (RuntimeError, ValueError) as e:
        logger.warning("FileEventBroadcaster broadcast failed: %s", e)
        return False


def _build_workspace_fs(workspace: str) -> KernelFileSystem:
    return KernelFileSystem(str(Path(workspace).resolve()), _get_fs_adapter())


def _normalize_relative_path(fs: KernelFileSystem, file_path: str) -> str:
    token = str(file_path or "").strip()
    if not token:
        raise ValueError("file_path is required")
    return fs.to_workspace_relative_path(token)


def write_file_with_broadcast(
    workspace: str,
    file_path: str,
    content: str,
    message_bus=None,
    worker_id: str = "standalone",
    task_id: str = "",
) -> dict:
    """带广播的文件写入

    统一处理文件写入和事件广播，确保所有文件变更都能实时推送到前端。

    Args:
        workspace: 工作区根目录
        file_path: 文件相对路径
        content: 文件内容
        message_bus: MessageBus 实例（可选）
        worker_id: Worker ID
        task_id: 关联的任务 ID

    Returns:
        写入结果 {"ok": bool, "path": str, "bytes": int, "operation": str}
    """
    try:
        fs = _build_workspace_fs(workspace)
        rel_path = _normalize_relative_path(fs, file_path)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "path": file_path}

    # 读取旧内容用于计算 diff
    old_content = ""
    operation = "create"
    if fs.workspace_exists(rel_path):
        if not fs.workspace_is_file(rel_path):
            return {"ok": False, "error": "Path is not a file", "path": rel_path}
        old_content = fs.workspace_read_text(rel_path, encoding="utf-8")
        operation = "modify"

    # 计算 diff (before write to have old_content)
    patch = calculate_patch(old_content, content)

    # 先广播事件，失败时回滚不写入
    broadcast_ok = broadcast_file_written(
        file_path=rel_path,
        operation=operation,
        content_size=len(content),
        task_id=task_id,
        patch=patch,
        message_bus=message_bus,
        worker_id=worker_id,
    )
    if not broadcast_ok:
        return {"ok": False, "error": "Broadcast failed, write skipped", "path": rel_path}

    # 写入文件 (broadcast成功后才写入)
    fs.workspace_write_text(rel_path, content, encoding="utf-8")

    return {
        "ok": True,
        "path": rel_path,
        "bytes": len(content.encode("utf-8")),
        "operation": operation,
    }


def append_file_with_broadcast(
    workspace: str,
    file_path: str,
    content: str,
    message_bus=None,
    worker_id: str = "standalone",
    task_id: str = "",
) -> dict:
    """带广播的文件追加

    Args:
        workspace: 工作区根目录
        file_path: 文件相对路径
        content: 追加的内容
        message_bus: MessageBus 实例（可选）
        worker_id: Worker ID
        task_id: 关联的任务 ID

    Returns:
        写入结果 {"ok": bool, "path": str, "appended_bytes": int}
    """
    try:
        fs = _build_workspace_fs(workspace)
        rel_path = _normalize_relative_path(fs, file_path)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "path": file_path}

    # 读取旧内容
    old_content = ""
    if fs.workspace_exists(rel_path):
        if not fs.workspace_is_file(rel_path):
            return {"ok": False, "error": "Path is not a file", "path": rel_path}
        old_content = fs.workspace_read_text(rel_path, encoding="utf-8")

    # 追加内容
    fs.workspace_append_text(rel_path, content, encoding="utf-8")

    new_content = old_content + content

    # 计算 diff（新增部分）
    patch = calculate_patch(old_content, new_content)

    # 广播事件
    broadcast_file_written(
        file_path=rel_path,
        operation="modify",
        content_size=len(new_content),
        task_id=task_id,
        patch=patch,
        message_bus=message_bus,
        worker_id=worker_id,
    )

    return {
        "ok": True,
        "path": rel_path,
        "appended_bytes": len(content.encode("utf-8")),
    }


def replace_in_file_with_broadcast(
    workspace: str,
    file_path: str,
    old_text: str,
    new_text: str,
    count: int = -1,
    message_bus=None,
    worker_id: str = "standalone",
    task_id: str = "",
) -> dict:
    """带广播的文本替换

    Args:
        workspace: 工作区根目录
        file_path: 文件相对路径
        old_text: 要替换的文本
        new_text: 替换后的文本
        count: 替换次数 (-1 表示全部)
        message_bus: MessageBus 实例（可选）
        worker_id: Worker ID
        task_id: 关联的任务 ID

    Returns:
        替换结果 {"ok": bool, "path": str, "replacements": int}
    """
    try:
        fs = _build_workspace_fs(workspace)
        rel_path = _normalize_relative_path(fs, file_path)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "path": file_path}

    # 读取旧内容
    if not fs.workspace_exists(rel_path) or not fs.workspace_is_file(rel_path):
        return {"ok": False, "error": "File not found", "path": rel_path}

    old_content = fs.workspace_read_text(rel_path, encoding="utf-8")
    if str(old_text or "") == "":
        return {"ok": False, "error": "old_text must not be empty", "path": rel_path}

    # 执行替换
    replace_limit = old_content.count(old_text) if count == -1 else max(0, int(count))
    new_content = old_content.replace(old_text, new_text, replace_limit)

    if new_content == old_content:
        return {"ok": False, "error": "No replacements made", "path": file_path}

    # 写入文件
    fs.workspace_write_text(rel_path, new_content, encoding="utf-8")

    # 计算 diff
    patch = calculate_patch(old_content, new_content)

    # 广播事件
    broadcast_file_written(
        file_path=rel_path,
        operation="modify",
        content_size=len(new_content),
        task_id=task_id,
        patch=patch,
        message_bus=message_bus,
        worker_id=worker_id,
    )

    replacements = min(replace_limit, old_content.count(old_text))
    return {
        "ok": True,
        "path": rel_path,
        "replacements": replacements,
    }


def apply_patch_with_broadcast(
    workspace: str,
    target_file: str,
    patch: str,
    message_bus=None,
    worker_id: str = "standalone",
    task_id: str = "",
) -> dict:
    """带广播的 patch 应用

    Args:
        workspace: 工作区根目录
        target_file: 目标文件路径
        patch: patch 内容（unified diff 格式）
        message_bus: MessageBus 实例（可选）
        worker_id: Worker ID
        task_id: 关联的任务 ID

    Returns:
        应用结果 {"ok": bool, "file": str}
    """
    try:
        fs = _build_workspace_fs(workspace)
        rel_path = _normalize_relative_path(fs, target_file)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "file": target_file}

    # 读取旧内容
    old_content = ""
    if fs.workspace_exists(rel_path) and fs.workspace_is_file(rel_path):
        old_content = fs.workspace_read_text(rel_path, encoding="utf-8")
    else:
        return {"ok": False, "error": "File not found", "file": rel_path}

    # 解析 patch 并应用
    try:
        lines = old_content.splitlines()
        patch_lines = patch.splitlines()

        # 简单 patch 应用（处理 + 和 - 行）
        for line in patch_lines:
            if line.startswith("+") and not line.startswith("+++"):
                lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                for idx, line_item in enumerate(lines):
                    if line_item == line[1:]:
                        lines.pop(idx)
                        break

        new_content = "\n".join(lines)

        # 写入文件
        fs.workspace_write_text(rel_path, new_content, encoding="utf-8")

        # 计算 diff
        diff_patch = calculate_patch(old_content, new_content)

        # 计算行统计
        added_lines, deleted_lines, modified_lines = _calculate_line_stats(
            diff_patch,
            "modify",
        )

        # 广播事件
        broadcast_file_written(
            file_path=rel_path,
            operation="modify",
            content_size=len(new_content),
            task_id=task_id,
            patch=diff_patch,
            message_bus=message_bus,
            worker_id=worker_id,
        )

        return {
            "ok": True,
            "file": rel_path,
            "applied": True,
            "added_lines": added_lines,
            "deleted_lines": deleted_lines,
            "modified_lines": modified_lines,
        }
    except Exception as e:
        logger.error(
            "apply_patch_with_broadcast failed for file=%s: %s",
            rel_path,
            e,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": str(e),
            "file": rel_path,
        }
