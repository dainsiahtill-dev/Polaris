"""统一编排服务接口 (Orchestration Service Port)

定义编排服务的应用层接口，作为唯一提交与查询入口。
遵循端口-适配器模式（Hexagonal Architecture）。

架构位置：应用层端口 (Application Layer Port)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
        OrchestrationRunRequest,
        OrchestrationSnapshot,
        SignalRequest,
    )


class OrchestrationService(ABC):
    """统一编排服务接口

    这是编排系统的唯一应用层入口。所有编排操作（PM/Director/角色）
    都通过此接口提交。

    职责：
    - 接收编排请求（submit_run）
    - 查询运行状态（query_run）
    - 发送控制信号（signal_run）
    - 取消运行（cancel_run）

    实现注意：
    - 所有方法必须幂等
    - 所有状态变更必须持久化
    - 必须生成 correlation_id 用于追踪
    """

    @abstractmethod
    async def submit_run(
        self,
        request: OrchestrationRunRequest,
    ) -> OrchestrationSnapshot:
        """提交编排运行

        Args:
            request: 编排运行请求

        Returns:
            初始状态快照

        Raises:
            ValidationError: 请求校验失败
            OrchestrationError: 编排系统错误
        """
        ...

    @abstractmethod
    async def query_run(
        self,
        run_id: str,
    ) -> OrchestrationSnapshot | None:
        """查询运行状态

        Args:
            run_id: 运行标识

        Returns:
            当前状态快照，如果不存在返回 None
        """
        ...

    @abstractmethod
    async def query_run_tasks(
        self,
        run_id: str,
        task_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """查询运行任务详情

        Args:
            run_id: 运行标识
            task_ids: 特定任务ID列表，None 表示所有任务

        Returns:
            任务状态字典
        """
        ...

    @abstractmethod
    async def signal_run(
        self,
        run_id: str,
        signal: SignalRequest,
    ) -> OrchestrationSnapshot:
        """向运行发送控制信号

        Args:
            run_id: 运行标识
            signal: 信号请求

        Returns:
            更新后的状态快照

        Raises:
            NotFoundError: 运行不存在
            InvalidStateError: 当前状态不允许该信号
        """
        ...

    @abstractmethod
    async def cancel_run(
        self,
        run_id: str,
        force: bool = False,
    ) -> OrchestrationSnapshot:
        """取消运行

        Args:
            run_id: 运行标识
            force: 是否强制取消（不等待清理）

        Returns:
            最终状态快照
        """
        ...

    @abstractmethod
    async def list_runs(
        self,
        workspace: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OrchestrationSnapshot]:
        """列出运行

        Args:
            workspace: 按工作区过滤
            status: 按状态过滤
            limit: 返回数量限制
            offset: 分页偏移

        Returns:
            运行快照列表
        """
        ...


class OrchestrationEventPublisher(ABC):
    """编排事件发布端口

    用于将编排事件发布到外部系统（UI、日志、监控）。
    """

    @abstractmethod
    async def publish_snapshot(
        self,
        run_id: str,
        snapshot: OrchestrationSnapshot,
    ) -> None:
        """发布状态快照"""
        ...

    @abstractmethod
    async def publish_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """发布事件"""
        ...


class OrchestrationRepository(ABC):
    """编排存储库端口

    持久化编排状态和快照。
    """

    @abstractmethod
    async def save_snapshot(
        self,
        snapshot: OrchestrationSnapshot,
    ) -> None:
        """保存快照"""
        ...

    @abstractmethod
    async def get_snapshot(
        self,
        run_id: str,
    ) -> OrchestrationSnapshot | None:
        """获取快照"""
        ...

    @abstractmethod
    async def list_snapshots(
        self,
        workspace: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OrchestrationSnapshot]:
        """列出快照"""
        ...

    @abstractmethod
    async def save_request(
        self,
        request: OrchestrationRunRequest,
    ) -> None:
        """保存请求（用于恢复）"""
        ...

    @abstractmethod
    async def get_request(
        self,
        run_id: str,
    ) -> OrchestrationRunRequest | None:
        """获取请求"""
        ...


class RoleOrchestrationAdapter(ABC):
    """角色编排适配器端口

    每个角色（PM/Director/QA等）实现此接口接入统一编排系统。
    """

    @property
    @abstractmethod
    def role_id(self) -> str:
        """角色标识"""
        ...

    @abstractmethod
    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行任务

        Args:
            task_id: 任务标识
            input_data: 输入数据
            context: 执行上下文

        Returns:
            执行结果字典
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> list[str]:
        """获取角色能力列表"""
        ...


__all__ = [
    "OrchestrationEventPublisher",
    "OrchestrationRepository",
    "OrchestrationService",
    "RoleAdapterFactoryPort",
    "RoleOrchestrationAdapter",
]


class RoleAdapterFactoryPort:
    """Port for registering role adapters with the workflow runtime.

    This interface allows cells (e.g. roles.adapters) to register role adapters
    without directly depending on workflow_runtime internal implementations.
    The factory is injected by the orchestrator; cells only need to implement
    ``RoleOrchestrationAdapter`` and register via this port.
    """

    def register(self, role_id: str, adapter: RoleOrchestrationAdapter) -> None:
        """Register a role adapter for the given role_id.

        Args:
            role_id: The role identifier (e.g. "pm", "director", "qa").
            adapter: An adapter instance implementing ``RoleOrchestrationAdapter``.
        """
        raise NotImplementedError

    def get(self, role_id: str) -> RoleOrchestrationAdapter | None:
        """Retrieve the registered adapter for a role_id.

        Args:
            role_id: The role identifier.

        Returns:
            The registered adapter, or None if not registered.
        """
        raise NotImplementedError

    def list_registered(self) -> list[str]:
        """List all registered role IDs.

        Returns:
            List of registered role identifiers.
        """
        raise NotImplementedError
