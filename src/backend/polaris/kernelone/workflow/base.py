"""Runtime Backend Protocol - Polaris 工作流运行时统一接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS, MAX_WORKFLOW_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RuntimeSubmissionResult:
    """工作流提交结果"""

    submitted: bool
    status: str
    workflow_id: str = ""
    run_id: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowSnapshot:
    """工作流快照 - Query 只读语义"""

    workflow_id: str
    workflow_name: str
    status: str  # running, completed, failed, cancelled
    run_id: str
    start_time: str
    close_time: str | None = None
    result: dict[str, Any] | None = None
    pending_actions: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class RuntimeBackendPort(Protocol):
    """Runtime Backend Protocol - 支持自研 workflow runtime 实现。"""

    async def start_workflow(
        self,
        *,
        workflow_name: str,
        workflow_id: str,
        payload: dict[str, Any],
    ) -> RuntimeSubmissionResult:
        """启动工作流"""
        ...

    async def describe_workflow(self, workflow_id: str) -> WorkflowSnapshot:
        """查询工作流状态 - Query 只读语义"""
        ...

    async def query_workflow(
        self,
        workflow_id: str,
        query_name: str,
        *args: Any,
    ) -> dict[str, Any]:
        """执行工作流 Query - 只读，不应修改状态"""
        ...

    async def cancel_workflow(
        self,
        workflow_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """取消工作流"""
        ...

    async def signal_workflow(
        self,
        workflow_id: str,
        signal_name: str,
        signal_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送 Signal 到工作流"""
        ...


# Backward compatibility alias (deprecated, use RuntimeBackendPort)
RuntimeBackend = RuntimeBackendPort


@dataclass
class EmbeddedConfig:
    """自研 workflow runtime 配置。"""

    db_path: str = ":memory:"
    max_concurrent_workflows: int = 100
    max_concurrent_activities: int = 50
    default_activity_timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS  # seconds
    default_workflow_timeout: int = MAX_WORKFLOW_TIMEOUT_SECONDS  # seconds


@dataclass
class WorkflowConfig:
    """Workflow 运行时配置（保留兼容类型定义）。"""

    address: str = "localhost:7233"
    namespace: str = "default"
    task_queue: str = "polaris"
    tls_enabled: bool = False
