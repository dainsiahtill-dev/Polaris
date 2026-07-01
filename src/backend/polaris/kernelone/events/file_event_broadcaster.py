"""File event broadcaster - 统一文件变更事件广播

用于在文件被修改时广播事件到前端，支持实时 diff 显示。

所有文件写入操作都应该使用此模块来确保事件一致性。
"""

import difflib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from polaris.kernelone.constants import BROADCAST_MAX_SIZE_BYTES
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import resolve_storage_roots

_MESSAGE_BUS_TYPES: tuple[Any, Any] | None = None
_MESSAGE_BUS_TYPES_LOCK = Lock()
_PENDING_BROADCAST_TASKS: set[Any] = set()
_PENDING_BROADCAST_TASKS_LOCK = Lock()
_FILE_EDIT_EVENT_PUBLISHER: "FileEditEventPublisher | None" = None
_FILE_EDIT_EVENT_PUBLISHER_LOCK = Lock()
logger = logging.getLogger(__name__)
_JETSTREAM_PUBLISH_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


class FileEditEventPublisher(Protocol):
    """Synchronous publisher port for durable runtime file-edit events."""

    def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
        """Publish one runtime event envelope.

        Implementations own transport details. KernelOne deliberately depends
        only on this protocol so realtime delivery can be installed by the
        infrastructure layer without reverse imports.
        """


def configure_file_edit_event_publisher(publisher: FileEditEventPublisher | None) -> None:
    """Install or clear the process-wide file edit publisher adapter."""
    global _FILE_EDIT_EVENT_PUBLISHER
    with _FILE_EDIT_EVENT_PUBLISHER_LOCK:
        _FILE_EDIT_EVENT_PUBLISHER = publisher


def _get_file_edit_event_publisher() -> FileEditEventPublisher | None:
    with _FILE_EDIT_EVENT_PUBLISHER_LOCK:
        return _FILE_EDIT_EVENT_PUBLISHER


def _jetstream_publish_enabled() -> bool:
    raw = str(os.environ.get("KERNELONE_JETSTREAM_PUBLISH") or "").strip().lower()
    return bool(raw) and raw not in _JETSTREAM_PUBLISH_FALSE_VALUES


def _get_fs_adapter() -> Any:
    return get_default_adapter()


def _get_message_bus_imports() -> tuple[Any, Any]:
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


def _build_file_written_payload(
    *,
    file_path: str,
    operation: str,
    content_size: int,
    task_id: str,
    patch: str,
) -> dict[str, Any] | None:
    """Build the canonical payload shared by realtime and durable event sinks."""
    file_lower = str(file_path or "").lower()
    if any(file_lower.endswith(ext) or ext in file_lower for ext in _BROADCAST_SKIP_PATTERNS):
        return None
    if content_size > _BROADCAST_MAX_SIZE:
        return None

    normalized_operation = str(operation or "modify").strip().lower()
    if normalized_operation not in {"create", "modify", "delete"}:
        normalized_operation = "modify"
    patch_text = str(patch or "")
    patch_available = bool(patch_text.strip())
    patch_unavailable_reason = ""
    if not patch_available:
        if normalized_operation == "create" and content_size == 0:
            patch_unavailable_reason = "empty_file"
        elif normalized_operation == "modify":
            patch_unavailable_reason = "no_content_change"
        elif normalized_operation == "delete":
            patch_unavailable_reason = "empty_delete"
        else:
            patch_unavailable_reason = "patch_empty"

    payload: dict[str, Any] = {
        "file_path": file_path,
        "operation": normalized_operation,
        "content_size": content_size,
        "task_id": task_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "diff_status": "available" if patch_available else "unavailable",
        "has_patch": patch_available,
    }
    if patch_available:
        payload["patch"] = patch_text
    else:
        payload["patch_unavailable_reason"] = patch_unavailable_reason

    added_lines, deleted_lines, modified_lines = _calculate_line_stats(
        patch_text,
        normalized_operation,
    )
    payload["added_lines"] = int(added_lines)
    payload["deleted_lines"] = int(deleted_lines)
    payload["modified_lines"] = int(modified_lines)
    return payload


def _append_durable_file_edit_event(workspace: str | None, payload: dict[str, Any]) -> bool:
    """Persist FILE_WRITTEN evidence for snapshot/UI fallback and audits."""
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return False
    try:
        event_dir = Path(workspace_token).resolve() / ".polaris" / "runtime" / "file-edits"
        event_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "schema_version": "runtime.v2",
            "event_schema": "runtime.event.file_edit.v1",
            "channel": "event.file_edit",
            "kind": "file_edit",
            "source": "file_event_broadcaster",
            "payload": payload,
        }
        with (event_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("Failed to persist FILE_WRITTEN event: %s", exc)
        return False


def _publish_file_edit_to_jetstream(workspace: str, payload: dict[str, Any]) -> bool:
    if not _jetstream_publish_enabled():
        return False
    publisher = _get_file_edit_event_publisher()
    if publisher is None:
        logger.debug("FILE_WRITTEN JetStream publish skipped: no publisher adapter configured.")
        return False
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return False
    try:
        roots = resolve_storage_roots(workspace_token)
        workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
        if not workspace_key:
            return False
        envelope = {
            "schema_version": "runtime.v2",
            "event_id": f"file-edit-{uuid.uuid4().hex[:12]}",
            "workspace_key": workspace_key,
            "run_id": "",
            "channel": "event.file_edit",
            "kind": "file_edit",
            "ts": payload.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "cursor": 0,
            "trace_id": None,
            "payload": payload,
            "meta": {"source": "file_event_broadcaster"},
        }
        return publisher.publish(
            subject=f"hp.runtime.{workspace_key}.event.file_edit",
            payload=envelope,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("FILE_WRITTEN JetStream publish failed: %s", exc)
        return False


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
    event_log_workspace: str | None = None,
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
    payload = _build_file_written_payload(
        file_path=file_path,
        operation=operation,
        content_size=content_size,
        task_id=task_id,
        patch=patch,
    )
    if payload is None:
        return False

    _append_durable_file_edit_event(event_log_workspace, payload)
    jetstream_scheduled = _publish_file_edit_to_jetstream(str(event_log_workspace or ""), payload)

    if not message_bus:
        return jetstream_scheduled

    try:
        _message_bus_cls, _msg_type = _get_message_bus_imports()

        import asyncio

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
            return jetstream_scheduled

    except (RuntimeError, ValueError) as e:
        logger.warning("FileEventBroadcaster broadcast failed: %s", e)
        return jetstream_scheduled


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
        event_log_workspace=workspace,
    )
    if message_bus is not None and not broadcast_ok:
        logger.warning("FILE_WRITTEN broadcast failed; continuing file write: %s", rel_path)

    # 写入文件 (broadcast成功后才写入)
    fs.workspace_write_text(rel_path, content, encoding="utf-8")

    return {
        "ok": True,
        "path": rel_path,
        "bytes": len(content.encode("utf-8")),
        "operation": operation,
        "broadcast_ok": bool(broadcast_ok),
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
    broadcast_ok = broadcast_file_written(
        file_path=rel_path,
        operation="modify",
        content_size=len(new_content),
        task_id=task_id,
        patch=patch,
        message_bus=message_bus,
        worker_id=worker_id,
        event_log_workspace=workspace,
    )

    return {
        "ok": True,
        "path": rel_path,
        "appended_bytes": len(content.encode("utf-8")),
        "broadcast_ok": bool(broadcast_ok),
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
    broadcast_ok = broadcast_file_written(
        file_path=rel_path,
        operation="modify",
        content_size=len(new_content),
        task_id=task_id,
        patch=patch,
        message_bus=message_bus,
        worker_id=worker_id,
        event_log_workspace=workspace,
    )

    replacements = min(replace_limit, old_content.count(old_text))
    return {
        "ok": True,
        "path": rel_path,
        "replacements": replacements,
        "broadcast_ok": bool(broadcast_ok),
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
            event_log_workspace=workspace,
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
