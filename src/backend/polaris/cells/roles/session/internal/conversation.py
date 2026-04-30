"""Conversation models for persistent chat history.

对话会话持久化模型，支持多会话管理和历史消息存储。
支持 RoleSession 多宿主架构：workflow、electron_workbench、tui、cli 等。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, cast

from polaris.cells.roles.session.internal.storage_paths import (
    resolve_preferred_sqlite_path,
)
from polaris.cells.roles.session.public.contracts import (
    AttachmentMode,
    RoleHostKind,
    SessionState,
    SessionType,
)
from polaris.infrastructure.db.adapters import SqlAlchemyAdapter
from polaris.kernelone.db import KernelDatabase
from polaris.kernelone.utils.time_utils import utc_now as _utc_now
from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import NullPool

# SQLAlchemy's declarative_base() returns a dynamically created class
# that is valid as a base class for ORM models.
Base: Any = declarative_base()  # type: ignore[misc]


class Conversation(Base):
    """对话会话表

    存储对话元数据，支持多角色、多工作区。
    支持 RoleSession 多宿主架构：
    - host_kind: 宿主类型 (workflow, electron_workbench, tui, cli, api_server, headless)
    - session_type: 会话类型 (workflow_managed, standalone, workbench)
    - attachment_mode: 附着模式 (isolated, attached_readonly, attached_collaborative)
    """

    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=True, index=True)
    role = Column(String(50), nullable=False, index=True)  # pm, architect, director, qa
    workspace = Column(String(500), nullable=True, index=True)

    # === RoleSession 多宿主字段 ===
    # 宿主类型
    host_kind = Column(String(20), default=RoleHostKind.ELECTRON_WORKBENCH.value, nullable=False)
    # 会话类型
    session_type = Column(String(20), default=SessionType.WORKBENCH.value, nullable=False)
    # 附着模式
    attachment_mode = Column(String(20), default=AttachmentMode.ISOLATED.value, nullable=False)
    # 附着的工作流 Run ID
    attached_run_id = Column(String(36), nullable=True)
    # 附着的任务 ID
    attached_task_id = Column(String(36), nullable=True)
    # 能力配置 JSON
    capability_profile = Column(Text, nullable=True)
    # 会话状态
    state = Column(String(20), default=SessionState.ACTIVE.value, nullable=False)
    # === 结束 ===

    # 上下文配置（JSON 存储静态上下文如 workspace, task_count 等）
    context_config = Column(Text, nullable=True)

    # 消息计数（缓存，避免频繁查询）
    message_count = Column(Integer, default=0)

    # 时间戳
    created_at = Column(DateTime, default=_utc_now, nullable=False)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)

    # 软删除
    is_deleted = Column(Integer, default=0, nullable=False, index=True)

    # 关联消息
    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        order_by="ConversationMessage.sequence",
        cascade="all, delete-orphan",
    )

    # 关联附着关系
    attachments = relationship(
        "SessionAttachment",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_conversations_role_workspace", "role", "workspace"),
        Index("ix_conversations_updated_at", "updated_at"),
        Index("ix_conversations_host_kind", "host_kind"),
        Index("ix_conversations_session_type", "session_type"),
        Index("ix_conversations_state", "state"),
    )

    def to_dict(self, include_messages: bool = False, message_limit: int = 100) -> dict[str, Any]:
        """转换为字典"""

        result = {
            "id": self.id,
            "title": self.title,
            "role": self.role,
            "workspace": self.workspace,
            # RoleSession 字段
            "host_kind": self.host_kind,
            "session_type": self.session_type,
            "attachment_mode": self.attachment_mode,
            "attached_run_id": self.attached_run_id,
            "attached_task_id": self.attached_task_id,
            "capability_profile": _safe_json_loads(cast("str | None", self.capability_profile), None),
            "state": self.state,
            # 原有字段
            "context_config": _safe_json_loads(cast("str | None", self.context_config), {}),
            "message_count": self.message_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_messages and self.messages:
            msgs = [m.to_dict() for m in self.messages[:message_limit]]
            result["messages"] = msgs

        return result


def _safe_json_loads(value: str | None, default: Any) -> Any:
    """安全地解析 JSON 字符串"""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError) as e:
        import logging

        logging.getLogger(__name__).warning(f"Failed to parse JSON: {e}")
        return default


class ConversationMessage(Base):
    """对话消息表

    存储单条消息，支持用户、助手、系统角色。
    """

    __tablename__ = "conversation_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 消息序号（用于排序）
    sequence = Column(Integer, nullable=False, default=0)

    # 角色: user, assistant, system
    role = Column(String(20), nullable=False)

    # 内容
    content = Column(Text, nullable=False, default="")
    thinking = Column(Text, nullable=True)  # 思考过程

    # 元数据（JSON: 模型信息、token 使用等）
    meta = Column(Text, nullable=True)

    # 时间戳
    created_at = Column(DateTime, default=_utc_now, nullable=False)

    # 关联会话
    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (Index("ix_messages_conversation_seq", "conversation_id", "sequence"),)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""

        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "meta": _safe_json_loads(cast("str | None", self.meta), {}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# 数据库连接管理
_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_engine_lock = threading.Lock()
_kernel_db_lock = threading.Lock()
_kernel_db: KernelDatabase | None = None
_DEFAULT_CONVERSATIONS_DB_LOGICAL_PATH = "runtime/conversations/conversations.db"
_FALLBACK_CONVERSATIONS_DB_LOGICAL_PATH = "workspace/runtime/conversations/conversations.db"


def _default_database_url() -> str:
    """构建默认会话数据库 URL。"""
    resolved = resolve_preferred_sqlite_path(
        _get_kernel_db(),
        runtime_logical_path=_DEFAULT_CONVERSATIONS_DB_LOGICAL_PATH,
        workspace_fallback_logical_path=_FALLBACK_CONVERSATIONS_DB_LOGICAL_PATH,
    )
    # SQLAlchemy expects forward slashes in sqlite URLs on Windows.
    return f"sqlite:///{Path(resolved).as_posix()}"


def _normalize_database_url(database_url: str | None) -> str:
    """Normalize SQLite URLs through KernelOne path policy."""
    token = str(database_url or "").strip()
    if not token:
        return _default_database_url()
    if not token.startswith("sqlite:///"):
        return token

    raw = token[len("sqlite:///") :]
    if raw == ":memory:" or raw.startswith("file:"):
        return token

    path_part, sep, query = raw.partition("?")
    if path_part.startswith("runtime/"):
        resolved = resolve_preferred_sqlite_path(
            _get_kernel_db(),
            runtime_logical_path=path_part,
            workspace_fallback_logical_path=f"workspace/{path_part}",
        )
    else:
        resolved = _get_kernel_db().resolve_sqlite_path(path_part, ensure_parent=True)
    normalized = f"sqlite:///{Path(resolved).as_posix()}"
    if sep:
        normalized = f"{normalized}?{query}"
    return normalized


def _resolve_kernel_workspace() -> str:
    explicit_workspace = str(os.environ.get("KERNELONE_CONTEXT_ROOT") or "").strip()
    if explicit_workspace:
        return str(Path(explicit_workspace).resolve())
    return str(Path.cwd().resolve())


def _get_kernel_db() -> KernelDatabase:
    global _kernel_db
    if _kernel_db is None:
        with _kernel_db_lock:
            if _kernel_db is None:
                _kernel_db = KernelDatabase(
                    _resolve_kernel_workspace(),
                    sqlalchemy_adapter=SqlAlchemyAdapter(),
                    allow_unmanaged_absolute=False,
                )
    return _kernel_db


def get_engine(database_url: str | None = None) -> Any:
    """获取或创建数据库引擎（线程安全）"""
    global _engine
    resolved_database_url = _normalize_database_url(database_url)

    if _engine is None:
        with _engine_lock:
            # 双重检查锁定
            if _engine is None:
                # 为 SQLite 配置合适的连接池
                connect_args = {}
                pool_class = NullPool  # SQLite 使用 NullPool 避免连接池问题

                if "sqlite" in resolved_database_url:
                    connect_args = {"check_same_thread": False}

                _engine = _get_kernel_db().sqlalchemy(
                    resolved_database_url,
                    connect_args=connect_args,
                    pool_class=pool_class,
                    pool_pre_ping=True,
                )
                Base.metadata.create_all(bind=_engine)
    return _engine


def get_session_local() -> Any:
    """获取会话工厂（线程安全）"""
    global _SessionLocal
    engine = get_engine()

    if _SessionLocal is None:
        with _engine_lock:
            if _SessionLocal is None:
                _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _SessionLocal


def init_db() -> None:
    """初始化数据库（创建表）"""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def get_db() -> Any:
    """获取数据库会话（用于依赖注入）"""
    session_local = get_session_local()
    db = session_local()
    try:
        yield db
    finally:
        db.close()
