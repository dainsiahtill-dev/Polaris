"""Runtime Factory - 自研 workflow 运行时工厂。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from polaris.kernelone.workflow.activity_runner import ActivityRunner
from polaris.kernelone.workflow.base import EmbeddedConfig, RuntimeBackend, RuntimeBackendPort
from polaris.kernelone.workflow.engine import WorkflowEngine
from polaris.kernelone.workflow.task_queue import TaskQueueManager
from polaris.kernelone.workflow.timer_wheel import TimerWheel

if TYPE_CHECKING:
    from polaris.infrastructure.db.repositories.workflow_runtime_store import SqliteRuntimeStore

logger = logging.getLogger(__name__)


class RuntimeFactory:
    """运行时工厂 - 仅支持自研 workflow 内核。"""

    _instance: RuntimeBackendPort | None = None
    _runtime_type: Literal["workflow"] | None = None

    @classmethod
    async def create_runtime(
        cls,
        runtime_type: Literal["workflow"] = "workflow",
        config: EmbeddedConfig | None = None,
    ) -> RuntimeBackend:
        """Create runtime instance.

        Args:
            runtime_type: Runtime type ("workflow")
            config: Runtime configuration

        Returns:
            RuntimeBackend instance
        """
        if cls._instance and cls._runtime_type == runtime_type:
            logger.info(f"Reusing existing {runtime_type} runtime")
            return cls._instance

        # Shutdown existing instance
        if cls._instance:
            await cls.shutdown_runtime()

        if runtime_type != "workflow":
            raise ValueError(f"Unknown runtime type: {runtime_type}")
        cls._instance = await cls._create_workflow(config or EmbeddedConfig())

        cls._runtime_type = runtime_type
        logger.info(f"Created {runtime_type} runtime")
        return cls._instance

    @classmethod
    async def _create_workflow(cls, config: EmbeddedConfig) -> RuntimeBackend:
        """Create self-hosted workflow runtime."""
        from polaris.cells.orchestration.workflow_engine.public.contracts import CellHandlerRegistry
        from polaris.infrastructure.db.repositories.workflow_runtime_store import SqliteRuntimeStore

        from .activity_registry import get_activity_registry
        from .workflow_registry import get_workflow_registry

        store: SqliteRuntimeStore = SqliteRuntimeStore(config.db_path)
        timer_wheel = TimerWheel()
        task_queue_manager = TaskQueueManager()
        activity_runner = ActivityRunner(config.max_concurrent_activities)

        handler_registry = CellHandlerRegistry(
            workflows=get_workflow_registry(),  # type: ignore[arg-type]
            activities=get_activity_registry(),  # type: ignore[arg-type]
        )
        engine = WorkflowEngine(
            store=store,  # type: ignore[arg-type]
            timer_wheel=timer_wheel,
            task_queue_manager=task_queue_manager,
            activity_runner=activity_runner,
            handler_registry=handler_registry,
        )

        await cls._register_workflows_and_activities(engine)

        await engine.start()
        return engine

    @classmethod
    async def _register_workflows_and_activities(cls, engine: WorkflowEngine) -> None:
        """Register workflow and activity handlers."""
        from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities import (
            director_activities as _director_activities,
            list_registered_activities,
            pm_activities as _pm_activities,
            qa_activities as _qa_activities,
        )

        from ..workflows import (
            director_task_workflow as _director_task_workflow,
            director_workflow as _director_workflow,
            pm_workflow as _pm_workflow,
            qa_workflow as _qa_workflow,
        )
        from .activity_registry import get_activity_registry
        from .workflow_registry import get_workflow_registry

        # 触发模块加载以注册活动和工作流
        _ = (
            _pm_activities,
            _director_activities,
            _qa_activities,
            _pm_workflow,
            _director_workflow,
            _director_task_workflow,
            _qa_workflow,
        )

        # 收集所有已注册的活动名称
        registered_activity_names: set[str] = set()

        # 从基础注册表获取活动
        for name, handler in list_registered_activities().items():
            token = str(name or "").strip()
            if token and callable(handler):
                engine.register_activity(token, handler)
                registered_activity_names.add(token)

        # 从活动注册表获取活动（避免重复注册）
        activity_registry = get_activity_registry()
        for name in activity_registry.list_activities():
            if name in registered_activity_names:
                continue  # 跳过已注册的活动
            definition = activity_registry.get(name)
            if definition is None or not callable(definition.handler):
                continue
            token = str(name or "").strip()
            if not token:
                continue
            engine.register_activity(token, definition.handler)
            registered_activity_names.add(token)

        # 注册工作流
        registered_workflow_names: set[str] = set()
        workflow_registry = get_workflow_registry()
        for name in workflow_registry.list_workflows():
            wf_definition = workflow_registry.get(name)
            if wf_definition is None or not callable(wf_definition.handler):
                continue
            token = str(name or "").strip()
            if not token:
                continue
            engine.register_workflow(token, wf_definition.handler)
            registered_workflow_names.add(token)

        logger.info(
            "Registered workflows and activities (workflows=%d, activities=%d)",
            len(registered_workflow_names),
            len(registered_activity_names),
        )

    @classmethod
    async def get_runtime(cls) -> RuntimeBackend | None:
        """Get current runtime instance."""
        return cls._instance

    @classmethod
    async def shutdown_runtime(cls) -> None:
        """Shutdown runtime."""
        if cls._instance:
            if hasattr(cls._instance, "stop"):
                await cls._instance.stop()
            cls._instance = None
            cls._runtime_type = None
            logger.info("Runtime shutdown complete")

    @classmethod
    def get_runtime_type(cls) -> Literal["workflow"] | None:
        """Get runtime type."""
        return cls._runtime_type


async def get_runtime(config: EmbeddedConfig | None = None) -> RuntimeBackend:
    """Get runtime instance."""
    return await RuntimeFactory.create_runtime("workflow", config)
