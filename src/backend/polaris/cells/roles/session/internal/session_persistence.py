"""Session Persistence Service - 会话状态持久化服务

负责将会话状态持久化到 KernelOne Storage:
- 会话快照序列化到文件系统
- 使用 KernelOne Storage Port 确保路径策略合规
- UTF-8 编码确保文本正确处理

这是 RoleSession 的补充服务，提供文件系统级别的持久化能力。
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from polaris.kernelone._runtime_config import resolve_env_int
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.utils import utc_now as _utc_now, utc_now_str as _utc_now_str

if TYPE_CHECKING:
    from polaris.cells.roles.session.internal.conversation import Conversation

logger = logging.getLogger(__name__)

# 默认会话快照保留天数
_DEFAULT_SESSION_TTL_DAYS = 30
_SESSION_SNAPSHOT_PREFIX = "session_snapshot"
_SNAPSHOT_VERSION = 1


class SessionPersistenceService:
    """会话持久化服务

    提供会话状态的文件系统持久化能力:
    - 将活跃会话快照保存到 runtime/sessions/
    - 支持会话状态恢复
    - 遵循 KernelOne Storage 路径策略
    """

    def __init__(
        self,
        workspace: str,
        *,
        storage_prefix: str = "runtime/sessions",
        ttl_days: int | None = None,
    ) -> None:
        """初始化会话持久化服务

        Args:
            workspace: 工作区路径
            storage_prefix: 存储路径前缀 (logical path)
            ttl_days: 会话快照保留天数，默认 30 天
        """
        self._workspace = str(workspace or os.getcwd())
        self._storage_prefix = storage_prefix
        self._ttl_days = ttl_days or _DEFAULT_SESSION_TTL_DAYS
        self._fs: KernelFileSystem | None = None
        self._default_adapter = get_default_adapter()
        self._manifest_lock = threading.Lock()  # 保护 manifest 并发更新

    @property
    def workspace(self) -> str:
        """获取工作区路径"""
        return self._workspace

    @property
    def fs(self) -> KernelFileSystem:
        """获取 KernelFileSystem 实例（延迟初始化）"""
        if self._fs is None:
            self._fs = KernelFileSystem(self._workspace, self._default_adapter)
        return self._fs

    def _get_snapshot_path(self, session_id: str) -> str:
        """获取会话快照路径

        Args:
            session_id: 会话 ID

        Returns:
            快照文件的 logical path
        """
        return f"{self._storage_prefix}/{_SESSION_SNAPSHOT_PREFIX}_{session_id}.json"

    def _get_manifest_path(self) -> str:
        """获取会话清单路径"""
        return f"{self._storage_prefix}/_manifest.json"

    def persist_session(self, session: Conversation) -> bool:
        """将会话状态持久化到文件系统

        Args:
            session: 会话对象

        Returns:
            是否成功
        """
        try:
            snapshot_data = self._serialize_session(session)
            logical_path = self._get_snapshot_path(str(session.id))

            # 使用 write_json 确保数据完整性（UTF-8 编码由 Adapter 保证）
            self.fs.write_json(logical_path, snapshot_data)

            logger.debug(
                "Persisted session snapshot: id=%s, path=%s",
                session.id,
                logical_path,
            )
            return True

        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to persist session snapshot: id=%s, error=%s",
                session.id,
                e,
                exc_info=True,
            )
            return False

    def _serialize_session(self, session: Conversation) -> dict[str, Any]:
        """将会话对象序列化为字典

        Args:
            session: 会话对象

        Returns:
            序列化的会话数据
        """
        return {
            "version": _SNAPSHOT_VERSION,
            "session_id": session.id,
            "title": session.title,
            "role": session.role,
            "workspace": session.workspace,
            "host_kind": session.host_kind,
            "session_type": session.session_type,
            "attachment_mode": session.attachment_mode,
            "attached_run_id": session.attached_run_id,
            "attached_task_id": session.attached_task_id,
            "capability_profile": session.capability_profile,
            "state": session.state,
            "context_config": session.context_config,
            "message_count": session.message_count,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            "persisted_at": _utc_now_str(),
        }

    def load_session_snapshot(self, session_id: str) -> dict[str, Any] | None:
        """从文件系统加载会话快照

        Args:
            session_id: 会话 ID

        Returns:
            会话快照数据或 None
        """
        try:
            logical_path = self._get_snapshot_path(session_id)
            data = self.fs.read_json(logical_path)
            return data

        except FileNotFoundError:
            logger.debug("Session snapshot not found: id=%s", session_id)
            return None
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to load session snapshot: id=%s, error=%s",
                session_id,
                e,
                exc_info=True,
            )
            return None

    def delete_session_snapshot(self, session_id: str) -> bool:
        """删除会话快照

        Args:
            session_id: 会话 ID

        Returns:
            是否成功
        """
        try:
            logical_path = self._get_snapshot_path(session_id)
            self.fs.remove(logical_path)
            logger.debug("Deleted session snapshot: id=%s", session_id)
            return True

        except FileNotFoundError:
            logger.debug("Session snapshot not found for deletion: id=%s", session_id)
            return True
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Failed to delete session snapshot: id=%s, error=%s",
                session_id,
                e,
                exc_info=True,
            )
            return False

    def list_session_snapshots(self) -> list[dict[str, Any]]:
        """列出所有会话快照

        Returns:
            快照元数据列表
        """
        try:
            manifest_path = self._get_manifest_path()
            manifest = self.fs.read_json(manifest_path)
            return manifest.get("snapshots", [])

        except FileNotFoundError:
            return []
        except (RuntimeError, ValueError) as e:
            logger.warning("Failed to list session snapshots: error=%s", e, exc_info=True)
            return []

    def update_manifest(self, session_id: str, metadata: dict[str, Any]) -> None:
        """更新会话清单 (线程安全)

        Args:
            session_id: 会话 ID
            metadata: 快照元数据

        Note:
            使用 threading.Lock 保护 manifest 的读取-修改-写入操作，
            防止 TOCTOU (Time-of-check to time-of-use) 竞态条件。
        """
        with self._manifest_lock:
            try:
                manifest: dict[str, Any] = {"snapshots": []}
                try:
                    manifest_path = self._get_manifest_path()
                    existing = self.fs.read_json(manifest_path)
                    if isinstance(existing, dict) and "snapshots" in existing:
                        manifest = existing
                except FileNotFoundError:
                    pass

                # 更新或添加快照条目
                snapshots = manifest.get("snapshots", [])
                existing_idx = None
                for i, snap in enumerate(snapshots):
                    if snap.get("session_id") == session_id:
                        existing_idx = i
                        break

                entry = {
                    "session_id": session_id,
                    "updated_at": _utc_now_str(),
                    **metadata,
                }

                if existing_idx is not None:
                    snapshots[existing_idx] = entry
                else:
                    snapshots.append(entry)

                manifest["snapshots"] = snapshots
                manifest["updated_at"] = _utc_now_str()

                # write_json 使用 UTF-8 编码并自动创建父目录
                manifest_path = self._get_manifest_path()
                self.fs.write_json(manifest_path, manifest)

            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Failed to update session manifest: error=%s",
                    e,
                    exc_info=True,
                )


class SessionTTLCleanupService:
    """会话 TTL 清理服务

    负责清理过期的会话快照:
    - 基于创建时间清理过期快照
    - 使用环境变量配置 TTL
    - 支持手动触发和定时清理
    """

    def __init__(
        self,
        persistence_service: SessionPersistenceService,
        *,
        ttl_days: int | None = None,
    ) -> None:
        """初始化 TTL 清理服务

        Args:
            persistence_service: 会话持久化服务
            ttl_days: TTL 天数，默认从环境变量或 30 天
        """
        self._persistence = persistence_service
        self._ttl_days = ttl_days or resolve_env_int("SESSION_SNAPSHOT_TTL_DAYS") or _DEFAULT_SESSION_TTL_DAYS

    @property
    def ttl_days(self) -> int:
        """获取 TTL 天数"""
        return self._ttl_days

    def cleanup_expired(self) -> list[str]:
        """清理所有过期的会话快照

        Returns:
            被清理的会话 ID 列表
        """
        cleaned: list[str] = []
        cutoff = _utc_now()

        try:
            snapshots = self._persistence.list_session_snapshots()

            for snapshot in snapshots:
                persisted_at_str = snapshot.get("persisted_at") or snapshot.get("updated_at")
                if not persisted_at_str:
                    continue

                try:
                    # 解析 ISO 格式时间
                    if persisted_at_str.endswith("Z"):
                        persisted_at_str = persisted_at_str[:-1] + "+00:00"
                    persisted_at = datetime.fromisoformat(persisted_at_str)

                    # 检查是否过期
                    age = cutoff - persisted_at.replace(tzinfo=cutoff.tzinfo)
                    if age.days > self._ttl_days:
                        session_id = snapshot.get("session_id")
                        if session_id and self._persistence.delete_session_snapshot(session_id):
                            cleaned.append(session_id)
                            logger.debug(
                                "Cleaned up expired session snapshot: id=%s, age_days=%d",
                                session_id,
                                age.days,
                            )

                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Invalid timestamp in snapshot: error=%s, snapshot=%s",
                        e,
                        snapshot,
                    )
                    continue

        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Session TTL cleanup failed: error=%s",
                e,
                exc_info=True,
            )

        if cleaned:
            logger.info(
                "Session TTL cleanup completed: cleaned=%d, ttl_days=%d",
                len(cleaned),
                self._ttl_days,
            )

        return cleaned

    def cleanup_session(self, session_id: str) -> bool:
        """清理单个会话快照

        Args:
            session_id: 会话 ID

        Returns:
            是否成功
        """
        return self._persistence.delete_session_snapshot(session_id)


class SessionEventPublisher:
    """会话生命周期事件发布器

    负责发布会话生命周期事件:
    - session_created: 会话创建
    - session_updated: 会话更新
    - session_ended: 会话结束
    - session_expired: 会话过期

    使用 KernelOne Events Port 确保事件格式合规。
    """

    def __init__(
        self,
        event_path: str | None = None,
        *,
        workspace: str | None = None,
    ) -> None:
        """初始化事件发布器

        Args:
            event_path: 事件日志路径 (logical path)
            workspace: 工作区路径
        """
        self._event_path = event_path or "runtime/sessions/events"
        self._workspace = workspace
        self._event_seq = 0

    @staticmethod
    def _utc_now() -> str:
        """返回当前 UTC 时间 ISO 格式字符串"""
        return _utc_now_str()

    def publish_session_created(
        self,
        session_id: str,
        role: str,
        host_kind: str,
        workspace: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """发布会话创建事件

        Args:
            session_id: 会话 ID
            role: 角色
            host_kind: 宿主类型
            workspace: 工作区路径
            metadata: 额外元数据
        """
        self._emit_event(
            name="session_created",
            kind="action",
            actor="System",
            summary=f"Session {session_id} created for role {role}",
            refs={
                "session_id": session_id,
                "role": role,
                "host_kind": host_kind,
                "workspace": workspace,
            },
            input={
                "session_id": session_id,
                "role": role,
                "host_kind": host_kind,
                "workspace": workspace,
                **(metadata or {}),
            },
        )

    def publish_session_updated(
        self,
        session_id: str,
        changes: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """发布会话更新事件

        Args:
            session_id: 会话 ID
            changes: 变更内容
            metadata: 额外元数据
        """
        self._emit_event(
            name="session_updated",
            kind="action",
            actor="System",
            summary=f"Session {session_id} updated",
            refs={"session_id": session_id},
            input={
                "session_id": session_id,
                "changes": changes,
                **(metadata or {}),
            },
        )

    def publish_session_ended(
        self,
        session_id: str,
        reason: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """发布会话结束事件

        Args:
            session_id: 会话 ID
            reason: 结束原因
            metadata: 额外元数据
        """
        self._emit_event(
            name="session_ended",
            kind="action",
            actor="System",
            summary=f"Session {session_id} ended: {reason}",
            refs={"session_id": session_id},
            input={
                "session_id": session_id,
                "reason": reason,
                **(metadata or {}),
            },
        )

    def publish_session_expired(
        self,
        session_id: str,
        ttl_days: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """发布会话过期事件

        Args:
            session_id: 会话 ID
            ttl_days: TTL 天数
            metadata: 额外元数据
        """
        self._emit_event(
            name="session_expired",
            kind="action",
            actor="System",
            summary=f"Session {session_id} expired after {ttl_days} days",
            refs={"session_id": session_id},
            input={
                "session_id": session_id,
                "ttl_days": ttl_days,
                **(metadata or {}),
            },
        )

    def publish_session_message_added(
        self,
        session_id: str,
        message_role: str,
        message_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """发布消息添加事件

        Args:
            session_id: 会话 ID
            message_role: 消息角色
            message_count: 消息计数
            metadata: 额外元数据
        """
        self._emit_event(
            name="session_message_added",
            kind="action",
            actor="System",
            summary=f"Message added to session {session_id}",
            refs={"session_id": session_id},
            input={
                "session_id": session_id,
                "message_role": message_role,
                "message_count": message_count,
                **(metadata or {}),
            },
        )

    def _emit_event(
        self,
        name: str,
        kind: str,
        actor: str,
        summary: str,
        refs: dict[str, Any],
        input: dict[str, Any],
        ok: bool | None = True,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """发布事件到文件系统

        Args:
            name: 事件名称
            kind: 事件类型 (action/observation)
            actor: 执行者
            summary: 摘要
            refs: 引用数据
            input: 输入数据
            ok: 是否成功
            output: 输出数据
            error: 错误信息
        """
        try:
            self._event_seq += 1

            # 对于 action 事件，使用 emit_session_event()
            if kind == "action":
                from polaris.kernelone.events import emit_session_event

                workspace = self._workspace or os.getcwd()
                session_id = refs.get("session_id", "")

                emit_session_event(
                    workspace=workspace,
                    event_name=name,
                    session_id=session_id,
                    payload=input or {},
                    actor=actor,
                )
            else:
                # 对于 observation 事件，继续使用 emit_event
                from polaris.kernelone.events import emit_event

                event_path = self._event_path
                if self._workspace:
                    from polaris.kernelone.storage import resolve_runtime_path

                    full_path = resolve_runtime_path(self._workspace, event_path)
                    event_path = full_path

                emit_event(
                    event_path=event_path,
                    kind=kind,
                    actor=actor,
                    name=name,
                    summary=summary,
                    refs=refs,
                    input=input if kind == "action" else None,
                    ok=ok if kind == "observation" else None,
                    output=output if kind == "observation" else None,
                    error=error,
                )

        except ImportError:
            # 如果 KernelOne Events 不可用，记录警告但不阻塞
            logger.warning(
                "KernelOne Events not available, event not published: name=%s",
                name,
            )
        except (RuntimeError, ValueError) as e:
            # 事件发布失败不应阻塞主流程
            logger.warning(
                "Failed to publish session event: name=%s, error=%s",
                name,
                e,
                exc_info=True,
            )


__all__ = [
    "_DEFAULT_SESSION_TTL_DAYS",
    "_SESSION_SNAPSHOT_PREFIX",
    "SessionEventPublisher",
    "SessionPersistenceService",
    "SessionTTLCleanupService",
]
