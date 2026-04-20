"""Protocol Execution Audit System.

审计事件记录：原始格式 -> 归一化 IR -> 应用结果的完整链路。
支持证据链追踪、失败重试分析、性能监控。

This module is product-agnostic: it uses the workspace metadata directory name
injected by the bootstrap layer and checks KERNELONE_* env vars with POLARIS_*
fallback for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .protocol_kernel import FileOperation, OperationResult

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

logger = logging.getLogger(__name__)


class AuditEventType(Enum):
    """审计事件类型."""

    PARSE_START = "parse_start"
    PARSE_COMPLETE = "parse_complete"
    PARSE_ERROR = "parse_error"
    VALIDATION_START = "validation_start"
    VALIDATION_COMPLETE = "validation_complete"
    APPLY_START = "apply_start"
    APPLY_COMPLETE = "apply_complete"
    OPERATION_START = "operation_start"
    OPERATION_COMPLETE = "operation_complete"
    OPERATION_ERROR = "operation_error"
    ROLLBACK = "rollback"


@dataclass
class AuditEvent:
    """单个审计事件."""

    event_type: str
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""
    operation_hash: str = ""

    # 事件详情
    details: dict[str, Any] = field(default_factory=dict)

    # 元数据
    source_line: int = 0
    original_format: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "event_type": self.event_type,
            "timestamp": datetime.fromtimestamp(self.timestamp).isoformat(),
            "session_id": self.session_id,
            "operation_hash": self.operation_hash,
            "details": self.details,
            "source_line": self.source_line,
            "original_format": self.original_format,
        }


@dataclass
class OperationAuditTrail:
    """单个操作的审计追踪."""

    operation_hash: str
    path: str
    edit_type: str

    # 原始记录
    original_text_preview: str = ""  # 前200字符
    original_format: str = ""
    source_line: int = 0

    # 归一化记录
    normalized_search_hash: str = ""
    normalized_replace_hash: str = ""

    # 执行前状态
    pre_apply_file_hash: str = ""
    pre_apply_line_count: int = 0
    pre_apply_exists: bool = False

    # 执行后状态
    post_apply_file_hash: str = ""
    post_apply_line_count: int = 0

    # 结果
    success: bool = False
    error_code: str = ""
    error_message: str = ""
    changed: bool = False

    # 时间戳
    started_at: float = 0.0
    completed_at: float = 0.0

    @property
    def duration_ms(self) -> float:
        """执行耗时（毫秒）."""
        if self.completed_at > self.started_at:
            return (self.completed_at - self.started_at) * 1000
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "operation_hash": self.operation_hash,
            "path": self.path,
            "edit_type": self.edit_type,
            "original_format": self.original_format,
            "source_line": self.source_line,
            "pre_apply": {
                "file_hash": self.pre_apply_file_hash,
                "line_count": self.pre_apply_line_count,
                "exists": self.pre_apply_exists,
            },
            "post_apply": {
                "file_hash": self.post_apply_file_hash,
                "line_count": self.post_apply_line_count,
            },
            "result": {
                "success": self.success,
                "error_code": self.error_code,
                "error_message": self.error_message,
                "changed": self.changed,
                "duration_ms": round(self.duration_ms, 2),
            },
        }


@dataclass
class SessionAuditLog:
    """会话级审计日志."""

    session_id: str
    workspace: str
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    # 输入记录
    original_text_hash: str = ""
    original_text_length: int = 0

    # 操作记录
    operation_trails: list[OperationAuditTrail] = field(default_factory=list)
    events: list[AuditEvent] = field(default_factory=list)

    # 汇总
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    skipped_operations: int = 0

    def add_event(self, event: AuditEvent) -> None:
        """添加审计事件."""
        event.session_id = self.session_id
        self.events.append(event)

    def add_trail(self, trail: OperationAuditTrail) -> None:
        """添加操作追踪."""
        self.operation_trails.append(trail)
        if trail.success:
            if trail.changed:
                self.successful_operations += 1
            else:
                self.skipped_operations += 1
        else:
            self.failed_operations += 1

    def complete(self) -> None:
        """标记会话完成."""
        self.completed_at = time.time()
        self.total_operations = len(self.operation_trails)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "timing": {
                "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
                "completed_at": datetime.fromtimestamp(self.completed_at).isoformat() if self.completed_at else None,
                "duration_ms": round((self.completed_at - self.started_at) * 1000, 2) if self.completed_at else None,
            },
            "input": {
                "text_hash": self.original_text_hash,
                "text_length": self.original_text_length,
            },
            "summary": {
                "total": self.total_operations,
                "successful": self.successful_operations,
                "failed": self.failed_operations,
                "skipped": self.skipped_operations,
            },
            "operations": [t.to_dict() for t in self.operation_trails],
            "events": [e.to_dict() for e in self.events],
        }

    def to_json(self, indent: int = 2) -> str:
        """转换为JSON字符串."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class AuditLogger:
    """审计日志管理器."""

    def __init__(self, workspace: str = "") -> None:
        self.workspace = workspace
        self._sessions: dict[str, SessionAuditLog] = {}
        self._enabled = (
            os.environ.get("KERNELONE_PROTOCOL_AUDIT") or os.environ.get("POLARIS_PROTOCOL_AUDIT", "true")
        ).lower() in ("true", "1", "yes")
        metadata_dir = get_workspace_metadata_dir_name()
        self._log_dir = Path(workspace) / metadata_dir / "audit" if workspace else Path(metadata_dir) / "audit"

    def start_session(self, session_id: str, original_text: str = "") -> SessionAuditLog:
        """开始新的审计会话."""
        import hashlib

        session = SessionAuditLog(
            session_id=session_id,
            workspace=self.workspace,
            original_text_hash=hashlib.sha256(original_text.encode()).hexdigest()[:16],
            original_text_length=len(original_text),
        )
        self._sessions[session_id] = session

        session.add_event(
            AuditEvent(
                event_type=AuditEventType.PARSE_START.value,
                session_id=session_id,
                details={"text_length": len(original_text)},
            )
        )

        return session

    def end_session(self, session_id: str) -> SessionAuditLog | None:
        """结束审计会话并保存."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        session.complete()

        if self._enabled:
            self._persist_session(session)

        return session

    def _persist_session(self, session: SessionAuditLog) -> None:
        """持久化会话日志."""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{session.session_id}.json"
            log_path = self._log_dir / filename

            with open(log_path, "w", encoding="utf-8") as f:
                f.write(session.to_json())

            logger.debug(f"[Audit] Session logged: {log_path}")

        except (RuntimeError, ValueError) as e:
            logger.warning(f"[Audit] Failed to persist session: {e}")

    def get_session(self, session_id: str) -> SessionAuditLog | None:
        """获取会话日志."""
        return self._sessions.get(session_id)

    def create_operation_trail(
        self,
        operation: FileOperation,
        original_text: str,
    ) -> OperationAuditTrail:
        """创建操作追踪记录."""
        import hashlib

        # 计算hash
        search_hash = ""
        if operation.search:
            search_hash = hashlib.sha256(operation.search.encode()).hexdigest()[:8]

        replace_hash = ""
        if operation.replace:
            replace_hash = hashlib.sha256(operation.replace.encode()).hexdigest()[:8]

        return OperationAuditTrail(
            operation_hash=operation.compute_hash(),
            path=operation.path,
            edit_type=operation.edit_type.name,
            original_text_preview=original_text[:200],
            original_format=operation.original_format,
            source_line=operation.source_line,
            normalized_search_hash=search_hash,
            normalized_replace_hash=replace_hash,
            started_at=time.time(),
        )

    def record_pre_apply_state(self, trail: OperationAuditTrail, full_path: str) -> None:
        """记录执行前状态."""
        import hashlib

        trail.pre_apply_exists = os.path.exists(full_path)

        if trail.pre_apply_exists and os.path.isfile(full_path):
            try:
                with open(full_path, encoding="utf-8") as f:
                    content = f.read()
                trail.pre_apply_file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                trail.pre_apply_line_count = len(content.splitlines())
            except OSError as e:
                logger.debug(
                    "[Audit] Failed to read pre-apply file state for %s: %s",
                    full_path,
                    str(e),
                )

    def record_post_apply_state(self, trail: OperationAuditTrail, full_path: str) -> None:
        """记录执行后状态."""
        import hashlib

        if os.path.exists(full_path) and os.path.isfile(full_path):
            try:
                with open(full_path, encoding="utf-8") as f:
                    content = f.read()
                trail.post_apply_file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                trail.post_apply_line_count = len(content.splitlines())
            except OSError as e:
                logger.debug(
                    "[Audit] Failed to read post-apply file state for %s: %s",
                    full_path,
                    str(e),
                )

    def record_result(
        self,
        trail: OperationAuditTrail,
        result: OperationResult,
    ) -> None:
        """记录执行结果."""
        trail.completed_at = time.time()
        trail.success = result.success
        trail.changed = result.changed
        trail.error_code = result.error_code.value if result.error_code else ""
        trail.error_message = result.error_message


# 全局审计日志实例
_global_audit_logger: AuditLogger | None = None


def get_audit_logger(workspace: str = "") -> AuditLogger:
    """获取全局审计日志器."""
    global _global_audit_logger
    if _global_audit_logger is None:
        _global_audit_logger = AuditLogger(workspace)
    return _global_audit_logger


def set_audit_logger(logger: AuditLogger) -> None:
    """设置全局审计日志器."""
    global _global_audit_logger
    _global_audit_logger = logger


__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    "OperationAuditTrail",
    "SessionAuditLog",
    "get_audit_logger",
    "set_audit_logger",
]
