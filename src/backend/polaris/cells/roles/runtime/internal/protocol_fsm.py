"""
Protocol Module - Unified RequestID + FSM Protocol

参考 learn-claude-code s10: Team Protocols
统一所有跨角色协调协议：计划审批、关机、人工接管、FinOps 门禁

FSM 状态机:
    [pending] --approve--> [approved]
    [pending] --reject---> [rejected]

协议类型:
    - plan_approval: 计划审批（高风险操作前审查）
    - shutdown: 优雅关机请求
    - takeover: 人工接管请求
    - budget_check: 预算/Token 门禁
    - policy_check: 策略合规检查
"""

import contextlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.text_ops import open_text_log_append, write_text_atomic
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)


class ProtocolType(str, Enum):
    PLAN_APPROVAL = "plan_approval"
    SHUTDOWN = "shutdown"
    TAKEOVER = "takeover"
    BUDGET_CHECK = "budget_check"
    POLICY_CHECK = "policy_check"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ProtocolRequest:
    request_id: str
    protocol_type: ProtocolType
    from_role: str
    to_role: str
    content: Any
    status: RequestStatus = RequestStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class ProtocolFSM:
    """
    统一协议状态机

    所有跨角色协调都通过同一个 FSM 框架:
    - plan_approval: PM/Director 提交计划，QA/Architect 审批
    - shutdown: 任何角色请求其他角色优雅关机
    - takeover: 请求人工接管
    - budget_check: CFO (户部) 预算审批
    - policy_check: PolicyGate 策略检查
    """

    def __init__(self, workspace: str | None = None, max_completed_requests: int = 1000) -> None:
        self._requests: dict[str, ProtocolRequest] = {}
        self._lock = threading.RLock()
        self._workspace = workspace
        self._max_completed_requests = max_completed_requests

    def create_request(
        self, protocol_type: ProtocolType, from_role: str, to_role: str, content: Any, metadata: dict | None = None
    ) -> str:
        """创建新请求，返回 request_id"""
        with self._lock:
            request_id = str(uuid.uuid4())[:8]
            request = ProtocolRequest(
                request_id=request_id,
                protocol_type=protocol_type,
                from_role=from_role,
                to_role=to_role,
                content=content,
                metadata=metadata or {},
            )
            self._requests[request_id] = request
            self._persist_request(request)
            return request_id

    def _cleanup_completed_requests_locked(self) -> None:
        """清理已完成的请求，只保留最近的（调用方必须持有锁）."""
        completed = [
            (rid, req)
            for rid, req in self._requests.items()
            if req.status in (RequestStatus.APPROVED, RequestStatus.REJECTED)
        ]
        if len(completed) > self._max_completed_requests:
            completed.sort(key=lambda x: x[1].updated_at, reverse=True)
            for rid, _ in completed[self._max_completed_requests :]:
                del self._requests[rid]

    def _cleanup_completed_requests(self) -> None:
        """清理已完成的请求，只保留最近的."""
        with self._lock:
            self._cleanup_completed_requests_locked()

    def approve(self, request_id: str, approver: str, notes: str = "") -> bool:
        """批准请求"""
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                return False
            if request.status != RequestStatus.PENDING:
                return False

            request.status = RequestStatus.APPROVED
            request.updated_at = time.time()
            request.metadata["approver"] = approver
            request.metadata["notes"] = notes
            self._persist_request(request)
            self._cleanup_completed_requests_locked()
            return True

    def reject(self, request_id: str, rejecter: str, reason: str) -> bool:
        """拒绝请求"""
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                return False
            if request.status != RequestStatus.PENDING:
                return False

            request.status = RequestStatus.REJECTED
            request.updated_at = time.time()
            request.metadata["rejecter"] = rejecter
            request.metadata["reason"] = reason
            self._persist_request(request)
            self._cleanup_completed_requests_locked()
            return True

    def get_status(self, request_id: str) -> RequestStatus | None:
        """获取请求状态"""
        with self._lock:
            request = self._requests.get(request_id)
            return request.status if request else None

    def get_request(self, request_id: str) -> ProtocolRequest | None:
        """获取完整请求"""
        with self._lock:
            return self._requests.get(request_id)

    def list_pending(
        self, protocol_type: ProtocolType | None = None, to_role: str | None = None
    ) -> list[ProtocolRequest]:
        """列出待处理请求"""
        with self._lock:
            results = [r for r in self._requests.values() if r.status == RequestStatus.PENDING]
            if protocol_type:
                results = [r for r in results if r.protocol_type == protocol_type]
            if to_role:
                results = [r for r in results if r.to_role == to_role]
            return results

    def _persist_request(self, request: ProtocolRequest) -> None:
        """持久化请求到磁盘"""
        if not self._workspace:
            return

        protocol_dir = Path(resolve_runtime_path(self._workspace, "runtime/state/protocols"))
        protocol_dir.mkdir(parents=True, exist_ok=True)

        file_path = protocol_dir / f"{request.request_id}.json"
        data = {
            "request_id": request.request_id,
            "protocol_type": request.protocol_type.value,
            "from_role": request.from_role,
            "to_role": request.to_role,
            "content": request.content,
            "status": request.status.value,
            "created_at": request.created_at,
            "updated_at": request.updated_at,
            "metadata": request.metadata,
        }
        write_text_atomic(str(file_path), json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_persistent(self) -> None:
        """从磁盘加载历史请求"""
        if not self._workspace:
            return

        protocol_dir = Path(resolve_runtime_path(self._workspace, "runtime/state/protocols"))
        if not protocol_dir.exists():
            return

        for file_path in protocol_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                request = ProtocolRequest(
                    request_id=data["request_id"],
                    protocol_type=ProtocolType(data["protocol_type"]),
                    from_role=data["from_role"],
                    to_role=data["to_role"],
                    content=data["content"],
                    status=RequestStatus(data["status"]),
                    created_at=data["created_at"],
                    updated_at=data["updated_at"],
                    metadata=data.get("metadata", {}),
                )
                with self._lock:
                    self._requests[request.request_id] = request
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                logger.debug(f"Failed to load protocol request from {file_path}: {e}")


class ProtocolBus:
    """
    协议消息总线

    基于 JSONL 的异步消息队列，支持:
    - 点对点消息
    - 广播消息
    - 请求/响应配对
    """

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = workspace
        self._inbox_dir: Path | None = None
        if workspace:
            self._inbox_dir = Path(resolve_runtime_path(workspace, "runtime/inbox"))
            self._inbox_dir.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        from_role: str,
        to_role: str,
        content: str,
        msg_type: str = "message",
        request_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """发送消息"""
        msg = {
            "type": msg_type,
            "from": from_role,
            "to": to_role,
            "content": content,
            "request_id": request_id,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }

        if self._inbox_dir:
            inbox_path = self._inbox_dir / f"{to_role}.jsonl"
            with open_text_log_append(str(inbox_path)) as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        return f"Sent {msg_type} to {to_role}"

    def broadcast(self, from_role: str, content: str, msg_type: str = "broadcast") -> str:
        """广播消息到所有角色"""
        roles = ["PM", "Director", "Architect", "ChiefEngineer", "CFO", "HR", "QA", "Auditor"]
        for role in roles:
            if role != from_role:
                self.send(from_role, role, content, msg_type)
        return f"Broadcast to {len(roles) - 1} roles"

    def read_inbox(self, role: str, drain: bool = True) -> list[dict]:
        """读取收件箱"""
        if not self._inbox_dir:
            return []

        inbox_path = self._inbox_dir / f"{role}.jsonl"
        fs = KernelFileSystem(str(inbox_path.parent), get_default_adapter())
        if not fs.workspace_exists(inbox_path.name):
            return []

        messages = []
        try:
            content = fs.workspace_read_text(inbox_path.name, encoding="utf-8")
            for line in content.strip().splitlines():
                if line:
                    with contextlib.suppress(json.JSONDecodeError):
                        messages.append(json.loads(line))
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to load protocol messages: {e}")

        if drain and messages:
            write_text_atomic(str(inbox_path), "", encoding="utf-8")

        return messages


def create_protocol_fsm(workspace: str | None = None) -> ProtocolFSM:
    """创建协议 FSM 实例"""
    fsm = ProtocolFSM(workspace)
    fsm.load_persistent()
    return fsm


def create_protocol_bus(workspace: str | None = None) -> ProtocolBus:
    """创建协议总线实例"""
    return ProtocolBus(workspace)
