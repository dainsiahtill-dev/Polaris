"""Runtime Backend Adapter.

本模块提供统一运行时接口，并保留兼容层同步 API。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.config import WorkflowConfig
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.workflow.base import EmbeddedConfig, RuntimeBackend, RuntimeBackendPort

logger = logging.getLogger(__name__)


async def _get_runtime_backend(config: EmbeddedConfig) -> RuntimeBackendPort:
    """Load runtime backend factory lazily to avoid import-time circular references."""
    from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime import (
        get_runtime,
    )

    return await get_runtime(config)


@dataclass
class WorkflowResult:
    """工作流执行结果。"""

    workflow_id: str
    run_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


class RuntimeBackendAdapter:
    """运行时后端适配器。"""

    def __init__(self) -> None:
        self._runtime: RuntimeBackendPort | None = None
        self._engine: Any = None
        self._running = False
        self._db_path = ""

    @staticmethod
    def _resolve_runtime_db_path() -> str:
        explicit = str(os.environ.get("POLARIS_RUNTIME_DB") or "").strip()
        candidate = explicit
        if not candidate:
            runtime_root = str(os.environ.get("POLARIS_RUNTIME_ROOT") or "").strip()
            cache_root = str(os.environ.get("POLARIS_RUNTIME_CACHE_ROOT") or "").strip()
            context_root = str(os.environ.get("POLARIS_CONTEXT_ROOT") or "").strip()
            if runtime_root:
                base_dir = runtime_root
            elif cache_root:
                base_dir = cache_root
            elif context_root:
                base_dir = os.path.join(context_root, ".polaris", "runtime")
            else:
                base_dir = os.path.join(tempfile.gettempdir(), "polaris-runtime")
            candidate = os.path.join(base_dir, "state", "workflow.runtime.db")
        token = os.path.expandvars(os.path.expanduser(candidate))
        if token == ":memory:":
            return token
        resolved = os.path.abspath(token)
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return resolved

    async def start(self) -> None:
        """启动适配器。"""
        resolved_db_path = self._resolve_runtime_db_path()
        if self._running:
            if str(self._db_path or "").strip() == str(resolved_db_path or "").strip():
                return
            await self.stop()
        # 从环境变量和工作区上下文推导可写数据库路径，避免相对路径导致启动失败。
        db_path = resolved_db_path
        config = EmbeddedConfig(
            db_path=db_path,
            max_concurrent_workflows=100,
            max_concurrent_activities=50,
        )
        runtime = await _get_runtime_backend(config)
        self._runtime = runtime
        self._engine = runtime if hasattr(runtime, "_activity_runner") else None
        self._running = True
        self._db_path = db_path
        logger.info("RuntimeBackendAdapter started with self-hosted workflow runtime")

    async def stop(self) -> None:
        """停止适配器。"""
        if not self._running:
            return
        if self._engine is not None:
            await self._engine.stop()
        self._runtime = None
        self._engine = None
        self._running = False
        self._db_path = ""
        logger.info("RuntimeBackendAdapter stopped")

    def _require_runtime(self) -> RuntimeBackend:
        if self._runtime is None:
            raise RuntimeError("Runtime adapter is not started")
        return self._runtime

    async def submit_workflow(
        self,
        workflow_name: str,
        workflow_id: str,
        payload: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """提交任意工作流。"""
        runtime = self._require_runtime()
        normalized_name = str(workflow_name or "").strip()
        normalized_id = str(workflow_id or "").strip()
        if not normalized_name or not normalized_id:
            raise ValueError("workflow_name and workflow_id are required")
        submission = await runtime.start_workflow(
            workflow_name=normalized_name,
            workflow_id=normalized_id,
            payload=payload if isinstance(payload, dict) else {},
        )
        status = str(submission.status or "").strip().lower()
        if status == "started":
            status = "running"
        return WorkflowResult(
            workflow_id=submission.workflow_id,
            run_id=submission.run_id,
            status=status or "unknown",
            result=submission.details if isinstance(submission.details, dict) else {},
            error=str(submission.error or "").strip() or None,
        )

    async def submit_pm_workflow(
        self,
        workspace: str,
        message: str | None = None,
    ) -> WorkflowResult:
        """提交 PM 工作流（轻量兼容入口）。"""
        normalized_workspace = str(workspace or "").strip()
        if not normalized_workspace:
            raise ValueError("workspace is required")
        workspace_token = re.sub(r"[^a-zA-Z0-9_-]+", "-", normalized_workspace).strip("-")
        if not workspace_token:
            workspace_token = "workspace"
        run_token = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        workflow_id = f"polaris-pm-{workspace_token}-{run_token}"
        payload = {
            "workspace": normalized_workspace,
            "run_id": run_token,
            "precomputed_payload": {},
            "metadata": {"message": str(message or "").strip()},
        }
        return await self.submit_workflow("pm_workflow", workflow_id, payload)

    async def describe_workflow(self, workflow_id: str) -> dict[str, Any]:
        """查询工作流状态。"""
        runtime = self._require_runtime()
        snapshot = await runtime.describe_workflow(str(workflow_id or "").strip())
        return {
            "workflow_id": snapshot.workflow_id,
            "workflow_name": snapshot.workflow_name,
            "status": snapshot.status,
            "run_id": snapshot.run_id,
            "start_time": snapshot.start_time,
            "close_time": snapshot.close_time,
            "result": snapshot.result,
        }

    async def query_workflow(
        self,
        workflow_id: str,
        query_name: str,
        *args: Any,
    ) -> dict[str, Any]:
        """Query 工作流。"""
        runtime = self._require_runtime()
        return await runtime.query_workflow(str(workflow_id or "").strip(), query_name, *args)

    async def cancel_workflow(
        self,
        workflow_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """取消工作流。"""
        runtime = self._require_runtime()
        return await runtime.cancel_workflow(str(workflow_id or "").strip(), reason)

    async def signal_workflow(
        self,
        workflow_id: str,
        signal_name: str,
        signal_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发送 Signal。"""
        runtime = self._require_runtime()
        return await runtime.signal_workflow(
            str(workflow_id or "").strip(),
            signal_name,
            signal_args,
        )

    async def execute_activity_sync(
        self,
        activity_name: str,
        workflow_id: str,
        input: dict[str, Any],
    ) -> Any:
        """同步执行 Activity。"""
        if self._engine is None or not hasattr(self._engine, "_activity_runner"):
            raise RuntimeError("Workflow engine is not initialized")

        activity_id = f"{workflow_id}-{activity_name}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        await self._engine._activity_runner.submit_activity(
            activity_id=activity_id,
            activity_name=str(activity_name or "").strip(),
            workflow_id=str(workflow_id or "").strip(),
            input=input if isinstance(input, dict) else {},
        )

        deadline = asyncio.get_running_loop().time() + 60.0
        while True:
            status = await self._engine._activity_runner.get_activity_status(activity_id)
            if status is not None:
                token = str(status.status or "").strip().lower()
                if token == "completed":
                    return status.result
                if token in {"failed", "cancelled"}:
                    raise RuntimeError(f"Activity `{activity_name}` failed: {str(status.error or '').strip()}")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Activity timed out after 60s")
            await asyncio.sleep(0.05)


_adapter: RuntimeBackendAdapter | None = None


async def get_adapter() -> RuntimeBackendAdapter:
    """获取全局适配器实例。"""
    global _adapter
    if _adapter is None:
        _adapter = RuntimeBackendAdapter()
    return _adapter


async def start_adapter() -> None:
    """启动全局适配器。"""
    adapter = await get_adapter()
    await adapter.start()


async def stop_adapter() -> None:
    """停止全局适配器。"""
    global _adapter
    if _adapter is not None:
        await _adapter.stop()
        _adapter = None


def _run_sync(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        raise RuntimeError("Sync runtime adapter API cannot run inside an active event loop.")
    return loop.run_until_complete(coro)


async def _describe_workflow_sync_async(
    workflow_id: str,
) -> dict[str, Any]:
    adapter = await get_adapter()
    if not adapter._running:
        await adapter.start()
    return await adapter.describe_workflow(workflow_id)


async def _query_workflow_sync_async(
    workflow_id: str,
    query_name: str,
    *args: Any,
) -> dict[str, Any]:
    adapter = await get_adapter()
    if not adapter._running:
        await adapter.start()
    return await adapter.query_workflow(workflow_id, query_name, *args)


async def _submit_pm_workflow_sync_async(
    workspace: str,
    message: str | None = None,
) -> WorkflowResult:
    adapter = await get_adapter()
    if not adapter._running:
        await adapter.start()
    return await adapter.submit_pm_workflow(workspace, message)


def submit_pm_workflow_sync(
    workspace: str,
    message: str | None = None,
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """提交 PM 工作流（同步接口）。"""
    runtime_config = config or WorkflowConfig.from_env()
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "status": "disabled",
            "workflow_id": "",
            "run_id": "",
            "error": "workflow_runtime_disabled",
        }
    try:
        result = _run_sync(_submit_pm_workflow_sync_async(workspace, message))
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "status": "failed",
            "workflow_id": "",
            "run_id": "",
            "error": str(exc),
        }
    return {
        "ok": True,
        "workflow_id": result.workflow_id,
        "run_id": result.run_id,
        "status": result.status,
        "result": result.result if isinstance(result.result, dict) else {},
        "error": str(result.error or "").strip(),
    }


def describe_workflow_sync(
    workflow_id: str,
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """查询工作流（同步接口）。"""
    runtime_config = config or WorkflowConfig.from_env()
    normalized_id = str(workflow_id or "").strip()
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": "workflow_runtime_disabled",
        }
    try:
        payload = _run_sync(_describe_workflow_sync_async(normalized_id))
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "workflow_id": normalized_id, "error": str(exc)}
    return {"ok": True, **payload}


def query_workflow_sync(
    workflow_id: str,
    query_name: str,
    *args: Any,
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """Query 工作流（同步接口）。"""
    runtime_config = config or WorkflowConfig.from_env()
    normalized_id = str(workflow_id or "").strip()
    normalized_query = str(query_name or "").strip()
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "query_name": normalized_query,
            "error": "workflow_runtime_disabled",
        }
    try:
        payload = _run_sync(_query_workflow_sync_async(normalized_id, normalized_query, *args))
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "query_name": normalized_query,
            "error": str(exc),
        }
    if isinstance(payload, dict) and str(payload.get("error") or "").strip():
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "query_name": normalized_query,
            "error": str(payload.get("error")).strip(),
        }
    return {
        "ok": True,
        "workflow_id": normalized_id,
        "query_name": normalized_query,
        "payload": payload if isinstance(payload, dict) else {},
    }
