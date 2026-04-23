"""RoleSession Service - 统一角色会话服务

负责管理 RoleSession 的生命周期：
- 创建/恢复 session
- 为 session 分配存储目录
- 持久化 transcript / audit / artifacts
- 管理 host_kind
- 管理 attachment_mode

这是 Role Kernel 的核心服务，所有宿主（Workflow、Electron Workbench、TUI、CLI、API Server）
都通过此服务创建和管理会话。

扩展功能 (M3 补全):
- 会话持久化: 使用 SessionPersistenceService 写入文件系统
- TTL 自动清理: 使用 SessionTTLCleanupService 清理过期快照
- 生命周期事件发布: 使用 SessionEventPublisher 发布事件
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from polaris.cells.roles.session.internal.conversation import ConversationMessage
    from sqlalchemy.orm import Session as DbSession

from polaris.cells.roles.session.internal.conversation import (
    Conversation,
    get_session_local,
)
from polaris.cells.roles.session.internal.session_attachment import SessionAttachment
from polaris.cells.roles.session.internal.session_persistence import (
    SessionEventPublisher,
    SessionPersistenceService,
    SessionTTLCleanupService,
)
from polaris.cells.roles.session.public.contracts import (
    AttachmentMode,
    RoleHostKind,
    SessionState,
    SessionType,
)
from polaris.kernelone.context.context_os import (
    ContextOSInvariantViolation,
    validate_context_os_persisted_projection,
)
from polaris.kernelone.context.context_os.rehydration import rehydrate_persisted_context_os_payload

logger = logging.getLogger(__name__)


# 全局持久化服务实例（延迟初始化）
_persistence_service: SessionPersistenceService | None = None
_ttl_cleanup_service: SessionTTLCleanupService | None = None
_event_publisher: SessionEventPublisher | None = None


def _get_persistence_service(workspace: str | None = None) -> SessionPersistenceService:
    """获取或创建全局持久化服务实例"""
    global _persistence_service
    if _persistence_service is None:
        workspace = (
            workspace
            or os.environ.get("KERNELONE_CONTEXT_ROOT")
            or os.getcwd()
        )
        _persistence_service = SessionPersistenceService(workspace)
    return _persistence_service


def _get_ttl_cleanup_service(workspace: str | None = None) -> SessionTTLCleanupService:
    """获取或创建全局 TTL 清理服务实例"""
    global _ttl_cleanup_service
    if _ttl_cleanup_service is None:
        persistence = _get_persistence_service(workspace)
        _ttl_cleanup_service = SessionTTLCleanupService(persistence)
    return _ttl_cleanup_service


def _get_event_publisher(workspace: str | None = None) -> SessionEventPublisher:
    """获取或创建全局事件发布器实例"""
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = SessionEventPublisher(workspace=workspace)
    return _event_publisher


class RoleSessionService:
    """统一角色会话服务

    提供 RoleSession 的完整生命周期管理。

    扩展功能 (M3 补全):
    - 会话持久化: 使用 KernelOne Storage Port 写入文件系统
    - TTL 自动清理: 清理过期会话快照
    - 生命周期事件发布: 发布 session_created, session_updated, session_ended 等事件
    """

    def __init__(
        self,
        db: DbSession | None = None,
        *,
        workspace: str | None = None,
        enable_persistence: bool = True,
        enable_events: bool = True,
    ) -> None:
        """初始化服务

        Args:
            db: 可选的数据库会话。如果不提供，会创建新的会话。
            workspace: 工作区路径（用于持久化和事件发布）
            enable_persistence: 是否启用会话持久化
            enable_events: 是否启用事件发布
        """
        self._db = db
        self._owns_session = db is None
        self._workspace = workspace
        self._enable_persistence = enable_persistence
        self._enable_events = enable_events
        self._persistence: SessionPersistenceService | None = None
        self._event_publisher: SessionEventPublisher | None = None

    @property
    def workspace(self) -> str:
        """获取工作区路径"""
        if self._workspace is None:
            self._workspace = (
                os.environ.get("KERNELONE_CONTEXT_ROOT") or os.getcwd()
            )
        return self._workspace

    @property
    def db(self) -> DbSession:
        """获取数据库会话"""
        if self._db is None:
            self._db = get_session_local()()
        return self._db

    @property
    def persistence(self) -> SessionPersistenceService | None:
        """获取持久化服务"""
        if self._persistence is None and self._enable_persistence:
            self._persistence = _get_persistence_service(self.workspace)
        return self._persistence

    @property
    def event_publisher(self) -> SessionEventPublisher | None:
        """获取事件发布器"""
        if self._event_publisher is None and self._enable_events:
            self._event_publisher = _get_event_publisher(self.workspace)
        return self._event_publisher

    def close(self) -> None:
        """关闭数据库会话"""
        if self._owns_session and self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _persist_session(self, session: Conversation) -> bool:
        """持久化会话到文件系统

        Args:
            session: 会话对象

        Returns:
            是否成功
        """
        if self.persistence is None:
            return False
        try:
            return self.persistence.persist_session(session)
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to persist session: id=%s, error=%s",
                session.id,
                e,
                exc_info=True,
            )
            return False

    def _publish_session_created(self, session: Conversation) -> None:
        """发布会话创建事件"""
        if self.event_publisher is None:
            return
        try:
            self.event_publisher.publish_session_created(
                session_id=str(session.id),
                role=str(session.role),
                host_kind=str(session.host_kind),
                workspace=str(session.workspace) if session.workspace else None,
                metadata={
                    "session_type": str(session.session_type),
                    "attachment_mode": str(session.attachment_mode),
                },
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to publish session_created event: id=%s, error=%s",
                session.id,
                e,
                exc_info=True,
            )

    def _publish_session_updated(self, session: Conversation, changes: dict[str, Any]) -> None:
        """发布会话更新事件"""
        if self.event_publisher is None:
            return
        try:
            self.event_publisher.publish_session_updated(
                session_id=str(session.id),
                changes=changes,
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to publish session_updated event: id=%s, error=%s",
                session.id,
                e,
                exc_info=True,
            )

    def _publish_session_ended(self, session: Conversation, reason: str = "normal") -> None:
        """发布会话结束事件"""
        if self.event_publisher is None:
            return
        try:
            self.event_publisher.publish_session_ended(
                session_id=str(session.id),
                reason=reason,
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to publish session_ended event: id=%s, error=%s",
                session.id,
                e,
                exc_info=True,
            )

    def _publish_message_added(self, session_id: str, message_role: str, message_count: int) -> None:
        """发布消息添加事件"""
        if self.event_publisher is None:
            return
        try:
            self.event_publisher.publish_session_message_added(
                session_id=session_id,
                message_role=message_role,
                message_count=message_count,
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to publish message_added event: session_id=%s, error=%s",
                session_id,
                e,
                exc_info=True,
            )

    def _sanitize_context_config(self, context_config: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate `state_first_context_os` boundary before persistence."""
        if context_config is None:
            return None
        sanitized = dict(context_config)
        if "state_first_context_os" not in sanitized:
            return sanitized
        payload = sanitized.get("state_first_context_os")
        if payload is None:
            sanitized.pop("state_first_context_os", None)
            return sanitized
        try:
            validated = validate_context_os_persisted_projection(payload)
        except ContextOSInvariantViolation as exc:
            raise ValueError(f"invalid state_first_context_os projection: {exc}") from exc
        sanitized["state_first_context_os"] = dict(validated or {})
        return sanitized

    # ==================== Session CRUD ====================

    def create_session(
        self,
        role: str,
        host_kind: str = RoleHostKind.ELECTRON_WORKBENCH.value,
        workspace: str | None = None,
        session_type: str = SessionType.WORKBENCH.value,
        attachment_mode: str = AttachmentMode.ISOLATED.value,
        title: str | None = None,
        context_config: dict[str, Any] | None = None,
        capability_profile: dict[str, Any] | None = None,
    ) -> Conversation:
        """创建新会话

        Args:
            role: 角色标识 (pm, architect, director, qa, chief_engineer)
            host_kind: 宿主类型
            workspace: 工作区路径
            session_type: 会话类型
            attachment_mode: 附着模式
            title: 会话标题
            context_config: 初始上下文配置
            capability_profile: 能力配置

        Returns:
            Conversation: 创建的会话对象
        """
        context_config = self._sanitize_context_config(context_config)
        session = Conversation(
            id=str(uuid.uuid4()),
            role=role,
            host_kind=host_kind,
            session_type=session_type,
            attachment_mode=attachment_mode,
            workspace=workspace,
            title=title or f"{role} - {host_kind} session",
            context_config=json.dumps(context_config) if context_config else None,
            capability_profile=json.dumps(capability_profile) if capability_profile else None,
            state=SessionState.ACTIVE.value,
        )

        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)

        logger.info(
            f"Created role session: id={session.id}, role={role}, host_kind={host_kind}, session_type={session_type}"
        )

        # M3: 持久化会话快照
        self._persist_session(session)

        # M3: 发布会话创建事件
        self._publish_session_created(session)

        return session

    def get_session(self, session_id: str) -> Conversation | None:
        """获取会话

        Args:
            session_id: 会话 ID

        Returns:
            Conversation 或 None
        """
        return self.db.query(Conversation).filter(Conversation.id == session_id).first()

    def get_sessions(
        self,
        role: str | None = None,
        host_kind: str | None = None,
        workspace: str | None = None,
        session_type: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        """列出会话

        Args:
            role: 角色过滤
            host_kind: 宿主类型过滤
            workspace: 工作区过滤
            session_type: 会话类型过滤
            state: 状态过滤
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            会话列表
        """
        query = self.db.query(Conversation).filter(Conversation.is_deleted == 0)

        if role:
            query = query.filter(Conversation.role == role)
        if host_kind:
            query = query.filter(Conversation.host_kind == host_kind)
        if workspace:
            query = query.filter(Conversation.workspace == workspace)
        if session_type:
            query = query.filter(Conversation.session_type == session_type)
        if state:
            query = query.filter(Conversation.state == state)

        return query.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit).all()

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        context_config: dict[str, Any] | None = None,
        capability_profile: dict[str, Any] | None = None,
        state: str | None = None,
    ) -> Conversation | None:
        """更新会话

        Args:
            session_id: 会话 ID
            title: 新标题
            context_config: 新上下文配置
            capability_profile: 新能力配置
            state: 新状态

        Returns:
            更新后的会话或 None
        """
        session = self.get_session(session_id)
        if not session:
            return None

        # 记录变更
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = {"old": str(session.title), "new": str(title)}
            session.title = title  # type: ignore
        if context_config is not None:
            context_config = self._sanitize_context_config(context_config)
            changes["context_config"] = True
            session.context_config = json.dumps(context_config)  # type: ignore
        if capability_profile is not None:
            changes["capability_profile"] = True
            session.capability_profile = json.dumps(capability_profile)  # type: ignore
        if state is not None:
            changes["state"] = {"old": str(session.state), "new": str(state)}
            session.state = state  # type: ignore

        self.db.commit()
        self.db.refresh(session)

        # M3: 持久化会话快照
        if changes:
            self._persist_session(session)

            # M3: 发布会话更新事件
            self._publish_session_updated(session, changes)

        return session

    def get_context_config_dict(self, session_id: str) -> dict[str, Any] | None:
        """Read one session's context_config as a dictionary."""
        session = self.get_session(session_id)
        if not session:
            return None
        if not session.context_config:
            return {}
        try:
            payload = json.loads(str(session.context_config))
        except json.JSONDecodeError:
            logger.warning("Invalid context_config JSON for session %s", session_id)
            return {}
        if not isinstance(payload, dict):
            logger.warning(
                "Invalid context_config payload type for session %s: %s",
                session_id,
                type(payload).__name__,
            )
            return {}
        return dict(payload)

    def get_context_os_snapshot(self, session_id: str) -> dict[str, Any] | None:
        """Read the persisted State-First Context OS snapshot for one session."""
        context_config = self.get_context_config_dict(session_id)
        if context_config is None:
            return None
        snapshot = context_config.get("state_first_context_os")
        if not isinstance(snapshot, dict):
            return None
        session_turn_events = context_config.get("session_turn_events")
        if isinstance(session_turn_events, list):
            snapshot = (
                rehydrate_persisted_context_os_payload(
                    snapshot,
                    session_turn_events=[dict(item) for item in session_turn_events if isinstance(item, dict)],
                )
                or snapshot
            )
        try:
            validated = validate_context_os_persisted_projection(snapshot)
        except ContextOSInvariantViolation as exc:
            logger.warning(
                "Rejected invalid state_first_context_os projection for session %s: %s",
                session_id,
                exc,
            )
            return None
        return dict(validated or {})

    def update_context_os_snapshot(
        self,
        session_id: str,
        snapshot: dict[str, Any] | None,
    ) -> Conversation | None:
        """Persist the State-First Context OS snapshot back into context_config."""
        context_config = self.get_context_config_dict(session_id)
        if context_config is None:
            return None
        updated = dict(context_config)
        if snapshot is None:
            updated.pop("state_first_context_os", None)
        else:
            try:
                validated = validate_context_os_persisted_projection(snapshot)
            except ContextOSInvariantViolation as exc:
                raise ValueError(f"invalid state_first_context_os projection: {exc}") from exc
            updated["state_first_context_os"] = dict(validated or {})
        return self.update_session(session_id, context_config=updated)

    def delete_session(self, session_id: str, soft: bool = True) -> bool:
        """删除会话

        Args:
            session_id: 会话 ID
            soft: 是否软删除（默认 True）

        Returns:
            是否成功
        """
        session = self.get_session(session_id)

        if not session:
            return False

        # M3: 发布会话结束事件（在状态变更前）
        self._publish_session_ended(session, reason="deleted" if soft else "hard_deleted")

        if soft:
            session.is_deleted = 1  # type: ignore
            session.state = SessionState.ARCHIVED.value  # type: ignore
            self.db.commit()
        else:
            self.db.delete(session)

            self.db.commit()

        logger.info(f"Deleted role session: id={session_id}, soft={soft}")
        return True

    # ==================== Attachment ====================

    def attach_session(
        self,
        session_id: str,
        run_id: str | None = None,
        task_id: str | None = None,
        mode: str = AttachmentMode.ATTACHED_READONLY.value,
        note: str | None = None,
    ) -> SessionAttachment | None:
        """将会话附着到工作流

        Args:
            session_id: 会话 ID
            run_id: 工作流 Run ID
            task_id: 任务 ID
            mode: 附着模式
            note: 备注

        Returns:
            SessionAttachment 或 None
        """
        session = self.get_session(session_id)
        if not session:
            return None

        # 先解除所有当前活跃的附着
        self._deactivate_attachments(session_id)

        # 创建新附着
        attachment = SessionAttachment(
            id=str(uuid.uuid4()),
            session_id=session_id,
            run_id=run_id,
            task_id=task_id,
            mode=mode,
            note=note,
            is_active="1",
        )

        # 更新会话的附着字段
        session.attached_run_id = run_id  # type: ignore
        session.attached_task_id = task_id  # type: ignore
        session.attachment_mode = mode  # type: ignore

        self.db.add(attachment)
        self.db.commit()
        self.db.refresh(attachment)

        logger.info(f"Attached session {session_id} to run={run_id}, task={task_id}, mode={mode}")

        # M3: 持久化会话快照
        self._persist_session(session)

        return attachment

    def detach_session(self, session_id: str) -> bool:
        """解除会话的工作流附着

        Args:
            session_id: 会话 ID

        Returns:
            是否成功
        """
        session = self.get_session(session_id)
        if not session:
            return False

        # 标记所有活跃附着为非活跃
        self._deactivate_attachments(session_id)

        # 更新会话字段
        session.attached_run_id = None  # type: ignore
        session.attached_task_id = None  # type: ignore
        session.attachment_mode = AttachmentMode.ISOLATED.value  # type: ignore

        self.db.commit()

        logger.info(f"Detached session {session_id}")

        # M3: 持久化会话快照
        self._persist_session(session)

        return True

    def _deactivate_attachments(self, session_id: str) -> None:
        """将所有活跃附着设为非活跃"""
        self.db.query(SessionAttachment).filter(
            SessionAttachment.session_id == session_id,
            SessionAttachment.is_active == "1",
        ).update({"is_active": "0", "detached_at": _utc_now()})

    def get_active_attachment(self, session_id: str) -> SessionAttachment | None:
        """获取会话的当前活跃附着

        Args:
            session_id: 会话 ID

        Returns:
            SessionAttachment 或 None
        """
        return (
            self.db.query(SessionAttachment)
            .filter(
                SessionAttachment.session_id == session_id,
                SessionAttachment.is_active == "1",
            )
            .first()
        )

    def get_session_attachments(self, session_id: str) -> list[SessionAttachment]:
        """获取会话的所有附着历史

        Args:
            session_id: 会话 ID

        Returns:
            附着列表
        """
        return (
            self.db.query(SessionAttachment)
            .filter(SessionAttachment.session_id == session_id)
            .order_by(SessionAttachment.attached_at.desc())
            .all()
        )

    # ==================== Capabilities ====================

    def get_capabilities(self, session_id: str) -> dict[str, Any] | None:
        """获取会话的能力配置

        Args:
            session_id: 会话 ID

        Returns:
            能力配置字典或 None
        """
        session = self.get_session(session_id)
        if not session or not session.capability_profile:
            return None

        try:
            return json.loads(str(session.capability_profile))
        except json.JSONDecodeError:
            return None

    def set_capabilities(self, session_id: str, capabilities: dict[str, Any]) -> Conversation | None:
        """设置会话的能力配置

        Args:
            session_id: 会话 ID
            capabilities: 能力配置

        Returns:
            更新后的会话或 None
        """
        return self.update_session(session_id, capability_profile=capabilities)

    # ==================== Strategy Override (WS2) ====================

    def get_strategy_override(self, session_id: str) -> dict[str, Any] | None:
        """从 session context_config 读取 session 级策略覆盖。

        Returns:
            strategy_override dict 或 None（若未设置）
        """
        session = self.get_session(session_id)
        if not session or not session.context_config:
            return None
        try:
            cfg: dict[str, Any] = json.loads(str(session.context_config))
            override = cfg.get("strategy_override")
            return dict(override) if isinstance(override, dict) else None
        except json.JSONDecodeError:
            return None

    def set_strategy_override(self, session_id: str, override: dict[str, Any]) -> Conversation | None:
        """持久化 session 级策略覆盖到 context_config。

        Reads the existing context_config, merges strategy_override into it,
        and writes it back atomically.

        Args:
            session_id: 会话 ID
            override: strategy override 参数 dict

        Returns:
            更新后的会话或 None
        """
        session = self.get_session(session_id)
        if not session:
            return None
        # Merge into existing context_config
        existing: dict[str, Any] = {}
        if session.context_config:
            try:
                # session.context_config is Column[str]; str() returns the value at runtime
                existing = json.loads(str(session.context_config))  # type: ignore[arg-type]
            except json.JSONDecodeError:
                existing = {}
        existing["strategy_override"] = dict(override)

        return self.update_session(session_id, context_config=existing)

    # ==================== Messages ====================

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        thinking: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Conversation | None:
        """添加消息到会话

        Args:
            session_id: 会话 ID
            role: 消息角色 (user, assistant, system)
            content: 消息内容
            thinking: 思考过程
            meta: 元数据

        Returns:
            更新后的会话或 None
        """
        from polaris.cells.roles.session.internal.conversation import ConversationMessage

        session = self.get_session(session_id)
        if not session:
            return None

        # 获取最新消息序号
        last_message = (
            self.db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == session_id)
            .order_by(ConversationMessage.sequence.desc())
            .first()
        )

        next_seq = (last_message.sequence + 1) if last_message else 0

        # 创建消息
        message = ConversationMessage(
            id=str(uuid.uuid4()),
            conversation_id=session_id,
            sequence=next_seq,
            role=role,
            content=content,
            thinking=thinking,
            meta=json.dumps(meta) if meta else None,
        )

        # 更新会话计数和时间
        session.message_count = (session.message_count or 0) + 1  # type: ignore

        self.db.add(message)
        self.db.commit()

        # M3: 发布消息添加事件
        # session.message_count is Column[int]; cast to int for event publishing
        self._publish_message_added(session_id, role, int(session.message_count or 0))  # type: ignore[arg-type]

        return session

    def get_messages(self, session_id: str, limit: int = 100, offset: int = 0) -> list[ConversationMessage]:
        """获取会话的消息

        Args:
            session_id: 会话 ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            消息列表
        """
        from polaris.cells.roles.session.internal.conversation import ConversationMessage

        return (
            self.db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == session_id)
            .order_by(ConversationMessage.sequence.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    # ==================== Export ====================

    def export_session(self, session_id: str, include_messages: bool = True) -> dict[str, Any] | None:
        """导出会话数据

        Args:
            session_id: 会话 ID
            include_messages: 是否包含消息

        Returns:
            会话数据字典或 None
        """
        session = self.get_session(session_id)
        if not session:
            return None

        result = session.to_dict(include_messages=include_messages)

        # 添加附着信息
        attachments = self.get_session_attachments(session_id)
        result["attachments"] = [a.to_dict() for a in attachments]

        return result

    # ==================== TTL Cleanup (M3) ====================

    def cleanup_expired_sessions(self) -> list[str]:
        """清理所有过期的会话快照

        Returns:
            被清理的会话 ID 列表
        """
        try:
            cleanup_service = _get_ttl_cleanup_service(self.workspace)
            return cleanup_service.cleanup_expired()
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to cleanup expired sessions: error=%s",
                e,
                exc_info=True,
            )
            return []

    # ==================== Factory Methods ====================

    @classmethod
    def create_workbench_session(
        cls,
        role: str,
        workspace: str | None = None,
        title: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Conversation:
        """创建 Workbench 会话的便捷方法

        Args:
            role: 角色标识
            workspace: 工作区路径
            title: 会话标题
            context: 初始上下文

        Returns:
            创建的会话
        """
        with cls(workspace=workspace) as service:
            return service.create_session(
                role=role,
                host_kind=RoleHostKind.ELECTRON_WORKBENCH.value,
                workspace=workspace,
                session_type=SessionType.WORKBENCH.value,
                attachment_mode=AttachmentMode.ISOLATED.value,
                title=title,
                context_config=context,
            )

    @classmethod
    def create_workflow_session(
        cls,
        role: str,
        workspace: str,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> Conversation:
        """创建 Workflow 会话的便捷方法

        Args:
            role: 角色标识
            workspace: 工作区路径
            run_id: 工作流 Run ID
            task_id: 任务 ID

        Returns:
            创建的会话
        """
        with cls(workspace=workspace) as service:
            session = service.create_session(
                role=role,
                host_kind=RoleHostKind.WORKFLOW.value,
                workspace=workspace,
                session_type=SessionType.WORKFLOW_MANAGED.value,
                attachment_mode=AttachmentMode.ATTACHED_COLLABORATIVE.value,
                title=f"{role} - workflow session",
            )

            if run_id or task_id:
                service.attach_session(
                    session.id,
                    run_id=run_id,
                    task_id=task_id,
                    mode=AttachmentMode.ATTACHED_COLLABORATIVE.value,
                )

            return session

    @classmethod
    def find_or_create_ad_hoc(
        cls,
        role: str,
        workspace: str,
        host_kind: str = RoleHostKind.API_SERVER.value,
    ) -> Conversation:
        """查找或创建临时会话（用于旧 API 兼容）

        Args:
            role: 角色标识
            workspace: 工作区路径
            host_kind: 宿主类型

        Returns:
            找到或创建的会话
        """
        with cls(workspace=workspace) as service:
            # 查找最近的活跃会话
            sessions = service.get_sessions(
                role=role,
                workspace=workspace,
                host_kind=host_kind,
                state=SessionState.ACTIVE.value,
                limit=1,
            )

            if sessions:
                return sessions[0]

            # 创建新会话
            return service.create_session(
                role=role,
                host_kind=host_kind,
                workspace=workspace,
                session_type=SessionType.STANDALONE.value,
            )

    @classmethod
    def create_ad_hoc_session(
        cls,
        role: str,
        workspace: str,
        host_kind: str = RoleHostKind.API_SERVER.value,
        *,
        title: str | None = None,
        context_config: dict[str, Any] | None = None,
        capability_profile: dict[str, Any] | None = None,
    ) -> Conversation:
        """Create a fresh standalone ad-hoc session without reusing prior active sessions.

        This is the canonical host behavior for new interactive turns when no
        explicit resume/session_id has been requested.
        """
        with cls(workspace=workspace) as service:
            return service.create_session(
                role=role,
                host_kind=host_kind,
                workspace=workspace,
                session_type=SessionType.STANDALONE.value,
                title=title,
                context_config=context_config,
                capability_profile=capability_profile,
            )
