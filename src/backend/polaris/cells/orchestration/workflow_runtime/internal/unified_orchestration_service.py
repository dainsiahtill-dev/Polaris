"""统一编排服务实现

实现 OrchestrationService 接口，整合 RuntimeOrchestrator 和 Workflow 系统。

架构位置：核心层 (Core Layer)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.ports import (
    OrchestrationEventPublisher,
    OrchestrationRepository,
    OrchestrationService,
    RoleOrchestrationAdapter,
)
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    FileChangeStats,
    OrchestrationMode,
    OrchestrationRunRequest,
    OrchestrationSignal,
    OrchestrationSnapshot,
    PipelineSpec,
    PipelineTask,
    RunStatus,
    SignalRequest,
    TaskPhase,
    TaskSnapshot,
)
from polaris.cells.orchestration.workflow_runtime.internal.ui_state_contract import UIStateConverter
from polaris.cells.workspace.integrity.public import TaskFileChangeTracker

logger = logging.getLogger(__name__)

RoleAdapterFactory = Callable[[str, str], RoleOrchestrationAdapter]


def _bounded_result_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _bounded_nonnegative_int(value: Any, *, maximum: int = 1_000_000) -> int | None:
    """Project an untrusted counter without retaining arbitrary nested data."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    return parsed if 0 <= parsed <= maximum else None


def _bounded_claim_attempts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "attempt": _bounded_nonnegative_int(item.get("attempt")),
                "task_id": _bounded_result_text(item.get("task_id"), limit=96),
                "selection_source": _bounded_result_text(item.get("selection_source"), limit=120),
                "claimed": item.get("claimed") is True or item.get("success") is True,
                "reason": _bounded_result_text(item.get("reason"), limit=120),
                "resumed": item.get("resumed") is True,
                "session_id": _bounded_result_text(item.get("session_id"), limit=120),
            }
        )
    return rows


def _bounded_taskboard_claim_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    attempts = value.get("claim_attempts")
    return {
        "requested_task_id": _bounded_result_text(value.get("requested_task_id"), limit=96),
        "selected_task_id": _bounded_result_text(value.get("selected_task_id"), limit=96),
        "selection_source": _bounded_result_text(value.get("selection_source"), limit=120),
        "selected_from_board": value.get("selected_from_board") is True,
        "selected_subject": _bounded_result_text(value.get("selected_subject")),
        "board_claim_applied": value.get("board_claim_applied") is True,
        "claim_failure_reason": _bounded_result_text(value.get("claim_failure_reason"), limit=120),
        "claim_attempts": _bounded_claim_attempts(attempts),
    }


def _bounded_role_result_evidence(result: Any) -> dict[str, Any]:
    """Project role results into a small, auditable orchestration fact."""

    if not isinstance(result, Mapping):
        return {}
    evidence: dict[str, Any] = {
        "schema_version": "workflow-runtime.role-result-evidence/1",
        "task_id": _bounded_result_text(result.get("task_id"), limit=96),
        "success": result.get("success") is True,
    }
    for key in (
        "error_code",
        "failure_stage",
        "root_cause_hint",
        "control_plane_failure_code",
        "task_runtime_claim_failure_reason",
    ):
        token = _bounded_result_text(result.get(key), limit=160)
        if token:
            evidence[key] = token
    if result.get("task_runtime_claim_required") is True:
        evidence["task_runtime_claim_required"] = True
    claim_evidence = _bounded_taskboard_claim_evidence(result.get("task_runtime_claim_evidence"))
    if claim_evidence:
        evidence["task_runtime_claim_evidence"] = claim_evidence
    claim_attempts = _bounded_claim_attempts(result.get("task_runtime_claim_attempts"))
    if claim_attempts:
        evidence["task_runtime_claim_attempts"] = claim_attempts
    signals: list[dict[str, Any]] = []
    raw_signals = result.get("decision_signals")
    if isinstance(raw_signals, list):
        for item in raw_signals[:8]:
            if not isinstance(item, Mapping):
                continue
            signals.append(
                {
                    "code": _bounded_result_text(item.get("code"), limit=120),
                    "severity": _bounded_result_text(item.get("severity"), limit=32),
                    "detail": _bounded_result_text(item.get("detail")),
                    "claim_failure_reason": _bounded_result_text(
                        item.get("claim_failure_reason"),
                        limit=120,
                    ),
                    "claim_attempt_count": _bounded_nonnegative_int(
                        item.get("claim_attempt_count"),
                    ),
                }
            )
    if signals:
        evidence["decision_signals"] = signals
    return evidence


def _adapter_task_id_for_role(
    *,
    role_id: str,
    workflow_task_id: str,
    role_metadata: dict[str, Any],
) -> str:
    """Resolve the physical adapter task id without leaking workflow ids."""

    normalized_role = str(role_id or "").strip().lower()
    fallback = str(workflow_task_id or "").strip()
    if normalized_role != "director":
        return fallback

    for key in ("task_id", "pm_task_id", "external_task_id", "source_task_id"):
        value = str(role_metadata.get(key) or "").strip()
        if value:
            return value
    return fallback


def _adapter_input_for_role(
    *,
    role_id: str,
    adapter_task_id: str,
    role_input: str,
    role_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build adapter input with Director TaskBoard ids made explicit."""

    payload: dict[str, Any] = {
        "input": role_input,
        "metadata": role_metadata,
    }
    if str(role_id or "").strip().lower() != "director":
        return payload

    resolved_task_id = str(adapter_task_id or "").strip()
    if not resolved_task_id:
        return payload
    payload["task_id"] = resolved_task_id
    payload.setdefault("pm_task_id", str(role_metadata.get("pm_task_id") or resolved_task_id).strip())
    payload.setdefault("external_task_id", str(role_metadata.get("external_task_id") or resolved_task_id).strip())
    payload.setdefault("source_task_id", str(role_metadata.get("source_task_id") or resolved_task_id).strip())
    return payload


def _adapter_context_for_task(
    *,
    run_id: str,
    workspace: str,
    task: PipelineTask,
    role_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Project one role task's authoritative orchestration context."""

    return {
        "run_id": run_id,
        "workspace": workspace,
        "timeout_seconds": task.timeout_seconds,
        "metadata": role_metadata,
    }


class InMemoryOrchestrationRepository(OrchestrationRepository):
    """内存存储实现（用于开发和测试）"""

    def __init__(self) -> None:
        self._snapshots: dict[str, OrchestrationSnapshot] = {}
        self._requests: dict[str, OrchestrationRunRequest] = {}
        self._lock = asyncio.Lock()

    async def save_snapshot(self, snapshot: OrchestrationSnapshot) -> None:
        async with self._lock:
            self._snapshots[snapshot.run_id] = snapshot

    async def get_snapshot(self, run_id: str) -> OrchestrationSnapshot | None:
        async with self._lock:
            return self._snapshots.get(run_id)

    async def save_request(self, request: OrchestrationRunRequest) -> None:
        """持久化请求供恢复使用"""
        async with self._lock:
            self._requests[request.run_id] = request

    async def get_request(self, run_id: str) -> OrchestrationRunRequest | None:
        """恢复请求"""
        async with self._lock:
            return self._requests.get(run_id)

    async def list_snapshots(
        self,
        workspace: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OrchestrationSnapshot]:
        async with self._lock:
            results = list(self._snapshots.values())

            if workspace:
                results = [s for s in results if s.workspace == workspace]
            if status:
                results = [s for s in results if s.status.value == status]

            # Sort by created_at desc
            results.sort(key=lambda s: s.created_at or datetime.min, reverse=True)

            return results[offset : offset + limit]


class LoggingEventPublisher(OrchestrationEventPublisher):
    """日志事件发布实现"""

    async def publish_snapshot(self, run_id: str, snapshot: OrchestrationSnapshot) -> None:
        logger.info(f"[Orchestration:{run_id}] Status={snapshot.status.value}, Progress={snapshot.overall_progress}%")

    async def publish_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        logger.info(f"[Orchestration:{run_id}] Event={event_type}, Payload={payload}")


class UnifiedOrchestrationService(OrchestrationService):
    """统一编排服务实现

    整合 Process Runtime 和 Workflow Runtime，提供统一执行面。

    特性：
    - 统一任务状态管理
    - 支持 chat/workflow 两种模式
    - 角色插件化执行
    - 完整的可观测性
    """

    def __init__(
        self,
        repository: OrchestrationRepository | None = None,
        event_publisher: OrchestrationEventPublisher | None = None,
        role_adapters: list[RoleOrchestrationAdapter] | None = None,
        role_adapter_factory: RoleAdapterFactory | None = None,
    ) -> None:
        self._repo = repository or InMemoryOrchestrationRepository()
        self._publisher = event_publisher or LoggingEventPublisher()
        self._adapters: dict[str, RoleOrchestrationAdapter] = {}
        self._active_runs: dict[str, asyncio.Task] = {}
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._role_adapter_factory = role_adapter_factory

        # 注册角色适配器
        if role_adapters:
            for adapter in role_adapters:
                self._adapters[adapter.role_id] = adapter

    def register_role_adapter(self, adapter: RoleOrchestrationAdapter) -> None:
        """注册角色适配器"""
        self._adapters[adapter.role_id] = adapter
        logger.info(f"Registered role adapter: {adapter.role_id}")

    def set_role_adapter_factory(self, factory: RoleAdapterFactory | None) -> None:
        """Set the application-owned role adapter factory."""
        self._role_adapter_factory = factory

    async def submit_run(
        self,
        request: OrchestrationRunRequest,
    ) -> OrchestrationSnapshot:
        """提交编排运行"""
        # 1. 统一契约规范化 (role_entries -> pipeline_spec)
        request = self._canonicalize_workflow_request(request)

        # 2. 零信任校验
        errors = request.validate()
        if errors:
            raise ValidationError(f"Request validation failed: {errors}")

        # 3. 确保角色适配器可用
        self._ensure_role_adapters(request)

        # 4. 持久化请求（用于恢复）
        await self._repo.save_request(request)

        # 5. 创建初始快照
        snapshot = self._create_initial_snapshot(request)
        await self._repo.save_snapshot(snapshot)
        await self._publisher.publish_snapshot(request.run_id, snapshot)

        # 6. 启动执行
        if request.mode == OrchestrationMode.WORKFLOW:
            task = asyncio.create_task(
                self._execute_workflow(request.run_id),
                name=f"orchestration-{request.run_id}",
            )
        else:  # CHAT mode
            task = asyncio.create_task(
                self._execute_chat(request.run_id),
                name=f"orchestration-chat-{request.run_id}",
            )

        self._active_runs[request.run_id] = task
        self._run_locks[request.run_id] = asyncio.Lock()

        # 添加完成回调以清理
        task.add_done_callback(lambda t: self._cleanup_run(request.run_id))

        return snapshot

    async def query_run(self, run_id: str) -> OrchestrationSnapshot | None:
        """查询运行状态"""
        return await self._repo.get_snapshot(run_id)

    async def query_run_tasks(
        self,
        run_id: str,
        task_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """查询任务详情"""
        snapshot = await self._repo.get_snapshot(run_id)
        if not snapshot:
            return {"error": "Run not found"}

        tasks = snapshot.tasks
        if task_ids:
            tasks = {k: v for k, v in tasks.items() if k in task_ids}

        return {
            "run_id": run_id,
            "status": snapshot.status.value,
            "tasks": {k: v.to_dict() for k, v in tasks.items()},
            "count": len(tasks),
        }

    async def signal_run(
        self,
        run_id: str,
        signal: SignalRequest,
    ) -> OrchestrationSnapshot:
        """发送控制信号"""
        snapshot = await self._repo.get_snapshot(run_id)
        if not snapshot:
            raise NotFoundError(f"Run not found: {run_id}")

        lock = self._run_locks.get(run_id)
        if not lock:
            raise InvalidStateError(f"Run {run_id} is not active")

        async with lock:
            # 处理信号
            if signal.signal == OrchestrationSignal.CANCEL:
                return await self._handle_cancel(run_id, force=False)
            elif signal.signal == OrchestrationSignal.RETRY:
                return await self._handle_retry(run_id, signal.task_id)
            elif signal.signal == OrchestrationSignal.SKIP:
                return await self._handle_skip(run_id, signal.task_id)
            else:
                raise InvalidSignalError(f"Unsupported signal: {signal.signal}")

    async def cancel_run(
        self,
        run_id: str,
        force: bool = False,
    ) -> OrchestrationSnapshot:
        """取消运行"""
        return await self.signal_run(
            run_id,
            SignalRequest(signal=OrchestrationSignal.CANCEL),
        )

    async def list_runs(
        self,
        workspace: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OrchestrationSnapshot]:
        """列出运行"""
        return await self._repo.list_snapshots(workspace, status, limit, offset)

    # =========================================================================
    # 内部执行逻辑
    # =========================================================================

    def _coerce_positive_int(self, value: Any, default: int) -> int:
        """将输入转换为正整数，失败时返回默认值"""
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return default
        return candidate if candidate > 0 else default

    def _canonicalize_workflow_request(
        self,
        request: OrchestrationRunRequest,
    ) -> OrchestrationRunRequest:
        """将 role_entries 规范化为 pipeline_spec（Workflow 模式）"""
        if request.mode != OrchestrationMode.WORKFLOW:
            return request
        if request.pipeline_spec is not None:
            return request
        if not request.role_entries:
            return request

        task_timeout_seconds = self._coerce_positive_int(
            request.metadata.get("timeout_seconds"),
            3600,
        )
        global_timeout_seconds = self._coerce_positive_int(
            request.metadata.get("global_timeout_seconds"),
            7200,
        )
        execution_mode = str(request.metadata.get("execution_mode") or "").strip().lower()
        parallel_limit = self._coerce_positive_int(
            request.metadata.get("max_parallel_tasks", request.metadata.get("max_workers")),
            1,
        )
        director_parallel_fanout = (
            execution_mode == "parallel"
            and len(request.role_entries) > 1
            and all(str(entry.role_id or "").strip().lower() == "director" for entry in request.role_entries)
        )
        pipeline_max_concurrency = parallel_limit if director_parallel_fanout else 1

        tasks: list[PipelineTask] = []
        previous_task_id: str | None = None
        for i, entry in enumerate(request.role_entries):
            task_id = f"task-{i}-{entry.role_id}"
            depends_on = [] if director_parallel_fanout else [previous_task_id] if previous_task_id else []
            tasks.append(
                PipelineTask(
                    task_id=task_id,
                    role_entry=entry,
                    depends_on=depends_on,
                    max_concurrency=pipeline_max_concurrency,
                    timeout_seconds=task_timeout_seconds,
                )
            )
            previous_task_id = task_id

        pipeline_spec = PipelineSpec(
            tasks=tasks,
            max_concurrency=pipeline_max_concurrency,
            global_timeout_seconds=global_timeout_seconds,
        )

        return replace(request, pipeline_spec=pipeline_spec)

    def _ensure_role_adapters(self, request: OrchestrationRunRequest) -> None:
        """确保请求涉及的角色都有适配器"""
        role_ids: set[str] = set()
        if request.pipeline_spec:
            for task in request.pipeline_spec.tasks:
                role_ids.add(task.role_entry.role_id)
        else:
            for entry in request.role_entries:
                role_ids.add(entry.role_id)

        if not role_ids:
            return

        workspace = str(request.workspace)

        for role_id in role_ids:
            adapter = self._adapters.get(role_id)
            adapter_workspace = getattr(adapter, "workspace", None) if adapter else None
            # Treat adapters without explicit workspace binding as reusable.
            # This keeps unit-test and in-memory adapters valid without forcing
            # a factory dependency for every submit_run call.
            if adapter and (adapter_workspace is None or str(adapter_workspace).strip() == workspace):
                continue

            if self._role_adapter_factory is None:
                raise ValidationError(f"Role adapter factory not configured for role {role_id}")

            try:
                self._adapters[role_id] = self._role_adapter_factory(role_id, workspace)
            except (RuntimeError, ValueError) as e:
                raise ValidationError(f"Failed to create adapter for role {role_id}: {e}") from e

    def _create_initial_snapshot(
        self,
        request: OrchestrationRunRequest,
    ) -> OrchestrationSnapshot:
        """创建初始快照"""
        snapshot = OrchestrationSnapshot(
            run_id=request.run_id,
            workspace=str(request.workspace),
            mode=request.mode.value,
            status=RunStatus.PENDING,
            current_phase=TaskPhase.INIT,
        )

        # 构建任务列表
        if request.pipeline_spec:
            for task in request.pipeline_spec.tasks:
                snapshot.tasks[task.task_id] = TaskSnapshot(
                    task_id=task.task_id,
                    status=RunStatus.PENDING,
                    phase=TaskPhase.INIT,
                    role_id=task.role_entry.role_id,
                )
        elif request.role_entries:
            for i, entry in enumerate(request.role_entries):
                task_id = f"task-{i}-{entry.role_id}"
                snapshot.tasks[task_id] = TaskSnapshot(
                    task_id=task_id,
                    status=RunStatus.PENDING,
                    phase=TaskPhase.INIT,
                    role_id=entry.role_id,
                )

        return snapshot

    async def _execute_workflow(self, run_id: str) -> None:
        """执行 Workflow 模式"""
        snapshot = await self._repo.get_snapshot(run_id)
        if not snapshot:
            logger.error(f"Run {run_id} not found")
            return

        # 获取请求
        request = await self._get_request_from_snapshot(snapshot)
        if not request:
            logger.error(f"Request not found for run {run_id}")
            snapshot.status = RunStatus.FAILED
            snapshot.current_phase = TaskPhase.COMPLETED
            snapshot.completed_at = datetime.now(timezone.utc)
            await self._update_snapshot(snapshot)
            return
        if not request.pipeline_spec:
            logger.error(f"No pipeline spec for run {run_id}")
            snapshot.status = RunStatus.FAILED
            snapshot.current_phase = TaskPhase.COMPLETED
            snapshot.completed_at = datetime.now(timezone.utc)
            await self._update_snapshot(snapshot)
            return

        pipeline = request.pipeline_spec
        completed_tasks: set[str] = set()
        failed_tasks: set[str] = set()

        snapshot.status = RunStatus.RUNNING
        snapshot.current_phase = TaskPhase.EXECUTING
        await self._update_snapshot(snapshot)

        try:
            while len(completed_tasks) + len(failed_tasks) < len(pipeline.tasks):
                # 找到就绪任务（依赖已满足）
                ready_tasks = self._find_ready_tasks(pipeline, completed_tasks, failed_tasks)

                if not ready_tasks:
                    if failed_tasks:
                        # 有任务失败导致阻塞
                        snapshot.status = RunStatus.BLOCKED
                        await self._update_snapshot(snapshot)
                        break
                    # 所有任务完成
                    break

                # 限制并发
                max_concurrent = min(pipeline.max_concurrency, len(ready_tasks))
                batch = ready_tasks[:max_concurrent]

                # 并行执行批次
                results = await asyncio.gather(
                    *[self._execute_task(run_id, task) for task in batch],
                    return_exceptions=True,
                )

                # 处理结果
                for task, result in zip(batch, results, strict=False):
                    if isinstance(result, Exception):
                        logger.exception(f"Task {task.task_id} failed: {result}")
                        failed_tasks.add(task.task_id)
                        await self._update_task_status(run_id, task.task_id, RunStatus.FAILED, str(result))
                    else:
                        task_success = True
                        task_error: str | None = None
                        if isinstance(result, dict):
                            task_success = bool(result.get("success", True))
                            task_error = str(result.get("error") or "").strip() or None
                        if task_success:
                            completed_tasks.add(task.task_id)
                        else:
                            failed_tasks.add(task.task_id)
                            await self._update_task_status(
                                run_id,
                                task.task_id,
                                RunStatus.FAILED,
                                task_error or "Role adapter returned success=false",
                            )

                # 更新整体进度
                snapshot.overall_progress = len(completed_tasks) / len(pipeline.tasks) * 100
                await self._update_snapshot(snapshot)

            # 最终状态
            if failed_tasks:
                snapshot.status = RunStatus.FAILED
            else:
                snapshot.status = RunStatus.COMPLETED
                snapshot.current_phase = TaskPhase.COMPLETED

            snapshot.completed_at = datetime.now(timezone.utc)
            await self._update_snapshot(snapshot)
            self._persist_director_result_if_terminal(request, snapshot)

            # Phase 3.1: Trigger run archive on terminal state (async, non-blocking)
            # Archive source: runtime/runs/<run_id>/*
            self._trigger_run_archive(run_id, snapshot.status)

        except asyncio.CancelledError:
            snapshot.status = RunStatus.CANCELLED
            await self._update_snapshot(snapshot)
            # Trigger archive for cancelled runs as well
            self._trigger_run_archive(run_id, RunStatus.CANCELLED)
            raise

    async def _execute_chat(self, run_id: str) -> None:
        """执行 Chat 模式"""
        # Chat 模式是交互式的，由外部驱动
        # 这里只设置初始状态
        snapshot = await self._repo.get_snapshot(run_id)
        if snapshot:
            snapshot.status = RunStatus.RUNNING
            snapshot.current_phase = TaskPhase.PLANNING
            await self._update_snapshot(snapshot)

    async def _execute_task(
        self,
        run_id: str,
        task: PipelineTask,
    ) -> dict[str, Any]:
        """执行单个任务"""
        # 更新任务状态
        await self._update_task_status(run_id, task.task_id, RunStatus.RUNNING)

        # 获取角色适配器
        adapter = self._adapters.get(task.role_entry.role_id)  # type: ignore[misc]
        if not adapter:
            raise RuntimeError(f"No adapter for role: {task.role_entry.role_id}")

        # 初始化文件变更追踪
        workspace = str(task.role_entry.scope_paths[0]) if task.role_entry.scope_paths else "."
        file_tracker: Any = TaskFileChangeTracker(workspace, task.task_id)
        file_tracker.start()

        try:
            role_metadata = dict(task.role_entry.metadata) if isinstance(task.role_entry.metadata, dict) else {}
            # RoleEntry metadata is authoritative per-role execution context,
            # not merely adapter input decoration.  Keep it attached to the
            # adapter context so downstream Role Runtime can consume trusted
            # budgets and structured final-request evidence without rebuilding
            # either from prompt text.
            context = _adapter_context_for_task(
                run_id=run_id,
                workspace=workspace,
                task=task,
                role_metadata=role_metadata,
            )
            binding_override = role_metadata.get("binding_override")
            if not isinstance(binding_override, dict):
                binding_override = None
            adapter_task_id = _adapter_task_id_for_role(
                role_id=task.role_entry.role_id,
                workflow_task_id=task.task_id,
                role_metadata=role_metadata,
            )
            adapter_input = _adapter_input_for_role(
                role_id=task.role_entry.role_id,
                adapter_task_id=adapter_task_id,
                role_input=task.role_entry.input,
                role_metadata=role_metadata,
            )
            # 角色执行隔离到独立工作线程，避免阻塞控制面事件循环。
            result = await asyncio.to_thread(
                self._run_role_adapter_in_worker,
                adapter,
                adapter_task_id,
                adapter_input,
                context,
                binding_override,
            )

            # 获取文件变更
            file_changes = file_tracker.finish()

            # 更新任务状态（包含文件变更）
            await self._update_task_status_with_changes(
                run_id=run_id,
                task_id=task.task_id,
                status=RunStatus.COMPLETED if result.get("success") else RunStatus.FAILED,
                file_changes=file_changes,
                error_message=result.get("error"),
                result_evidence=_bounded_role_result_evidence(result),
            )

            return result

        except (RuntimeError, ValueError) as e:
            # 获取已发生的文件变更
            file_changes = file_tracker.finish()

            await self._update_task_status_with_changes(
                run_id=run_id,
                task_id=task.task_id,
                status=RunStatus.FAILED,
                file_changes=file_changes,
                error_message=str(e),
            )
            raise

    @staticmethod
    def _run_role_adapter_in_worker(
        adapter: RoleOrchestrationAdapter,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
        binding_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run adapter execution in an isolated event loop on a worker thread."""
        override_role = "director"
        if binding_override:
            from polaris.kernelone.llm import runtime_config

            override_role = str(binding_override.get("role") or "director").strip() or "director"
            runtime_config.set_role_binding_override(
                override_role,
                provider_id=str(binding_override.get("provider_id") or binding_override.get("provider") or ""),
                model=str(binding_override.get("model") or binding_override.get("model_id") or ""),
                binding_id=str(binding_override.get("binding_id") or ""),
                fanout_locked=True,
            )
        try:
            return asyncio.run(
                adapter.execute(  # type: ignore[misc]
                    task_id=task_id,
                    input_data=input_data,
                    context=context,
                )
            )
        finally:
            if binding_override:
                from polaris.kernelone.llm import runtime_config

                runtime_config.clear_role_provider_override(override_role)

    def _find_ready_tasks(
        self,
        pipeline: PipelineSpec,
        completed: set[str],
        failed: set[str],
    ) -> list[PipelineTask]:
        """找到依赖已满足的就绪任务"""
        ready = []
        for task in pipeline.tasks:
            if task.task_id in completed or task.task_id in failed:
                continue

            # 检查依赖
            deps_satisfied = all(dep in completed for dep in task.depends_on)
            if deps_satisfied:
                ready.append(task)

        return ready

    def _persist_director_result_if_terminal(
        self,
        request: OrchestrationRunRequest,
        snapshot: OrchestrationSnapshot,
    ) -> None:
        """Reconcile Director-only task-runtime completion into the canonical result artifact."""

        pipeline = request.pipeline_spec
        if pipeline is None or not pipeline.tasks:
            return
        role_ids = {str(task.role_entry.role_id or "").strip().lower() for task in pipeline.tasks}
        if role_ids != {"director"}:
            return
        if snapshot.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.CANCELLED}:
            return
        try:
            from polaris.cells.orchestration.workflow_runtime.internal.director_result_artifacts import (
                persist_director_result_from_runtime,
            )

            persist_director_result_from_runtime(workspace=str(request.workspace), run_id=str(request.run_id))
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Failed to reconcile Director result artifact for run_id=%s workspace=%s",
                request.run_id,
                request.workspace,
                exc_info=True,
            )

    async def _update_task_status(
        self,
        run_id: str,
        task_id: str,
        status: RunStatus,
        error_message: str | None = None,
    ) -> None:
        """更新任务状态"""
        snapshot = await self._repo.get_snapshot(run_id)
        if not snapshot:
            return

        task = snapshot.tasks.get(task_id)
        if not task:
            return

        task.status = status
        task.updated_at = datetime.now(timezone.utc)

        if status == RunStatus.RUNNING and not task.started_at:
            task.started_at = datetime.now(timezone.utc)

        if status.is_terminal():
            task.completed_at = datetime.now(timezone.utc)

        if error_message:
            task.error_message = error_message
            task.error_category = self._categorize_error(error_message)

        await self._update_snapshot(snapshot)

    async def _update_snapshot(self, snapshot: OrchestrationSnapshot) -> None:
        """更新并发布快照"""
        snapshot.updated_at = datetime.now(timezone.utc)
        await self._repo.save_snapshot(snapshot)
        await self._publisher.publish_snapshot(snapshot.run_id, snapshot)

    def _categorize_error(self, error: str) -> str:
        """错误分类"""
        error_lower = error.lower()
        if "timeout" in error_lower:
            return "timeout"
        elif "permission" in error_lower or "access" in error_lower:
            return "permission"
        elif "network" in error_lower or "connection" in error_lower:
            return "network"
        elif "validation" in error_lower:
            return "validation"
        else:
            return "runtime"

    async def _get_request_from_snapshot(
        self,
        snapshot: OrchestrationSnapshot,
    ) -> OrchestrationRunRequest | None:
        """从快照恢复请求"""
        return await self._repo.get_request(snapshot.run_id)

    async def _handle_cancel(
        self,
        run_id: str,
        force: bool = False,
    ) -> OrchestrationSnapshot:
        """处理取消信号"""
        task = self._active_runs.get(run_id)
        if task and not task.done():
            task.cancel()

        snapshot = await self._repo.get_snapshot(run_id)
        if snapshot:
            snapshot.status = RunStatus.CANCELLED
            await self._update_snapshot(snapshot)
            return snapshot

        # Return a default cancelled snapshot if none exists
        return OrchestrationSnapshot(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            tasks={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def _handle_retry(
        self,
        run_id: str,
        task_id: str | None,
    ) -> OrchestrationSnapshot:
        """处理重试信号"""
        # 实际实现需要重新调度任务
        snapshot = await self._repo.get_snapshot(run_id)
        if snapshot:
            return snapshot

        # Return a default snapshot if none exists
        return OrchestrationSnapshot(
            run_id=run_id,
            status=RunStatus.PENDING,
            tasks={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def _handle_skip(
        self,
        run_id: str,
        task_id: str | None,
    ) -> OrchestrationSnapshot:
        """处理跳过信号"""
        if task_id:
            await self._update_task_status(run_id, task_id, RunStatus.CANCELLED)

        snapshot = await self._repo.get_snapshot(run_id)
        if snapshot:
            return snapshot

        # Return a default snapshot if none exists
        return OrchestrationSnapshot(
            run_id=run_id,
            status=RunStatus.CANCELLED,  # Use CANCELLED instead of non-existent SKIPPED
            tasks={},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def _update_task_status_with_changes(
        self,
        run_id: str,
        task_id: str,
        status: RunStatus,
        file_changes,
        error_message: str | None = None,
        result_evidence: dict[str, Any] | None = None,
    ) -> None:
        """更新任务状态并包含文件变更信息"""
        snapshot = await self._repo.get_snapshot(run_id)
        if not snapshot:
            return

        task = snapshot.tasks.get(task_id)
        if not task:
            return

        task.status = status
        task.updated_at = datetime.now(timezone.utc)

        # 更新文件变更统计
        task.file_changes = FileChangeStats(
            created=file_changes.created,
            modified=file_changes.modified,
            deleted=file_changes.deleted,
            lines_added=file_changes.lines_added,
            lines_removed=file_changes.lines_removed,
            lines_changed=file_changes.lines_changed,
        )

        if status == RunStatus.RUNNING and not task.started_at:
            task.started_at = datetime.now(timezone.utc)

        if status.is_terminal():
            task.completed_at = datetime.now(timezone.utc)

        if error_message:
            task.error_message = error_message
            task.error_category = self._categorize_error(error_message)
        if result_evidence is not None:
            task.result_evidence = dict(result_evidence)

        await self._update_snapshot(snapshot)

    def _trigger_run_archive(self, run_id: str, status: RunStatus) -> None:
        """Trigger async archiving of a runtime run on terminal state.

        This is non-blocking - archiving happens in background.
        Archive source: runtime/runs/<run_id>/*

        Args:
            run_id: The run ID to archive
            status: The terminal status (completed, failed, cancelled, blocked, timeout)
        """
        # Map RunStatus to archive reason
        reason_map = {
            RunStatus.COMPLETED: "completed",
            RunStatus.FAILED: "failed",
            RunStatus.CANCELLED: "cancelled",
            RunStatus.BLOCKED: "blocked",
            RunStatus.TIMEOUT: "timeout",
        }
        reason = reason_map.get(status, "completed")

        try:
            import os

            from polaris.cells.archive.run_archive.public.service import trigger_run_archive

            # Try to get workspace from environment variable first
            workspace = os.environ.get("KERNELONE_WORKSPACE")
            if not workspace:
                # Fallback to current directory.
                workspace = "."

            if workspace:
                trigger_run_archive(
                    workspace=workspace,
                    run_id=run_id,
                    reason=reason,
                    status=status.value,
                )
                logger.debug("Triggered archive for run %s (status=%s)", run_id, status.value)
        except (RuntimeError, ValueError) as e:
            # Log error but don't block the main flow
            logger.warning("Failed to trigger archive for run %s: %s", run_id, e)

    def _cleanup_run(self, run_id: str) -> None:
        """清理运行资源"""
        self._active_runs.pop(run_id, None)
        self._run_locks.pop(run_id, None)

    # Phase 7: UI 状态接口
    async def get_ui_state(self, run_id: str) -> dict[str, Any] | None:
        """获取 UI 状态（用于前端展示）"""
        snapshot = await self._repo.get_snapshot(run_id)
        if not snapshot:
            return None

        ui_state = UIStateConverter.from_orchestration_snapshot(snapshot)
        ui_state.latency_ms = UIStateConverter.calculate_latency(ui_state)

        return ui_state.to_dict()


# ============================================================================
# 异常类
# ============================================================================


class OrchestrationError(Exception):
    """编排错误基类"""

    pass


class ValidationError(OrchestrationError):
    """校验错误"""

    pass


class NotFoundError(OrchestrationError):
    """未找到错误"""

    pass


class InvalidStateError(OrchestrationError):
    """无效状态错误"""

    pass


class InvalidSignalError(OrchestrationError):
    """无效信号错误"""

    pass


# ============================================================================
# 单例管理
# ============================================================================

_service_instance: UnifiedOrchestrationService | None = None
_service_lock = asyncio.Lock()
_role_adapter_factory: RoleAdapterFactory | None = None


def configure_orchestration_role_adapter_factory(
    factory: RoleAdapterFactory | None,
) -> None:
    """Configure the application-owned role adapter factory for the singleton."""
    global _role_adapter_factory
    _role_adapter_factory = factory
    if _service_instance is not None:
        _service_instance.set_role_adapter_factory(factory)


def get_orchestration_role_adapter_factory() -> RoleAdapterFactory:
    """Return the registered role adapter factory.

    The factory is registered by the owning cell (``roles.adapters``) at import
    time via :func:`configure_orchestration_role_adapter_factory`.  Runtime call
    sites obtain adapters through this getter instead of importing the concrete
    ``create_role_adapter`` from ``roles.adapters`` directly, which would invert
    the dependency direction (delivery/orchestration -> roles).

    Returns:
        The registered factory callable ``(role_id, workspace) -> adapter``.

    Raises:
        RuntimeError: If no factory has been registered yet.  This is
            fail-closed by design: a missing registration is a wiring error
            and must surface loudly rather than silently fall back to a
            cross-cell import.
    """
    factory = _role_adapter_factory
    if factory is None:
        raise RuntimeError(
            "Role adapter factory is not configured. Ensure the owning cell "
            "(polaris.cells.roles.adapters) has been imported so it can register "
            "its factory via configure_orchestration_role_adapter_factory()."
        )
    return factory


async def get_orchestration_service() -> UnifiedOrchestrationService:
    """获取编排服务单例"""
    global _service_instance
    if _service_instance is None:
        async with _service_lock:
            if _service_instance is None:
                _service_instance = UnifiedOrchestrationService(
                    role_adapter_factory=_role_adapter_factory,
                )
    return _service_instance


def reset_orchestration_service() -> None:
    """重置服务（测试用）"""
    global _service_instance
    _service_instance = None


__all__ = [
    "InMemoryOrchestrationRepository",
    "InvalidSignalError",
    "InvalidStateError",
    "LoggingEventPublisher",
    "NotFoundError",
    "OrchestrationError",
    "UnifiedOrchestrationService",
    "ValidationError",
    "configure_orchestration_role_adapter_factory",
    "get_orchestration_role_adapter_factory",
    "get_orchestration_service",
    "reset_orchestration_service",
]
