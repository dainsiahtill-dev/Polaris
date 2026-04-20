"""Session Attachment Model - 会话附着关系表

用于记录 Workbench Session 与 Workflow Run/Task 的附着关系。
支持三种附着模式：
- isolated: 完全隔离，Workbench 独立工作
- attached_readonly: 只读附着，可读取 Workflow 上下文但不能修改
- attached_collaborative: 协作附着，可读取和写入 Workflow 状态
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from .conversation import Base, _utc_now


class SessionAttachment(Base):
    """会话附着关系表

    记录 Workbench Session 与 Workflow Run/Task 的附着关系。
    一个 session 可以有多个 attachment（历史记录），但同时只能有一个 active。
    """

    __tablename__ = "session_attachments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 附着的工作流 Run ID
    run_id = Column(String(36), nullable=True, index=True)
    # 附着的任务 ID
    task_id = Column(String(36), nullable=True, index=True)

    # 附着模式: isolated, attached_readonly, attached_collaborative
    mode = Column(String(20), nullable=False, default="isolated")

    # 是否为当前活跃附着
    is_active = Column(String(1), default="1", nullable=False)

    # 附着原因/备注
    note = Column(String(500), nullable=True)

    # 时间戳
    attached_at = Column(DateTime, default=_utc_now, nullable=False)
    detached_at = Column(DateTime, nullable=True)

    # 关联会话
    session = relationship("Conversation", back_populates="attachments")

    __table_args__ = (
        Index("ix_attachments_session_active", "session_id", "is_active"),
        Index("ix_attachments_run_id", "run_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "mode": self.mode,
            "is_active": self.is_active == "1",
            "note": self.note,
            "attached_at": self.attached_at.isoformat() if self.attached_at else None,
            "detached_at": self.detached_at.isoformat() if self.detached_at else None,
        }
