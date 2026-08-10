"""core methods for StressEngine (mixin)."""

# mypy: ignore-errors

import asyncio
import re
from pathlib import Path
from typing import Any, Self

import httpx

from ..observability import ObservabilityCollector
from ..project_pool import ProjectDefinition
from ..stress_path_policy import (
    default_stress_runtime_root,
    ensure_stress_runtime_root,
    ensure_stress_workspace_path,
)
from ..tracer import RuntimeTracer
from ._constants import (
    DEFAULT_CONTROL_PLANE_RETRY_ATTEMPTS,
    DEFAULT_CONTROL_PLANE_RETRY_BACKOFF_SECONDS,
    DEFAULT_MIN_NEW_CODE_FILES,
    DEFAULT_MIN_NEW_CODE_LINES,
    MAX_NON_LLM_CONTROL_PLANE_STALL_SECONDS,
    RETRYABLE_HTTP_STATUS_CODES,
)


class _StressEngineCoreMixin:
    def __init__(
        self,
        workspace: Path,
        backend_url: str = "",
        token: str = "",
        ramdisk_root: str | Path | None = None,
        factory_timeout: int = 3600,  # Factory 完整运行超时
        poll_interval: float = 5.0,  # 状态轮询间隔
        request_timeout: float = 10.0,
        control_plane_stall_timeout: float = 120.0,
        observability_request_timeout: float = 5.0,
        observability_snapshot_timeout: float = 12.0,
        observability_llm_timeout: float = 3.0,
        observability_max_task_probes: int = 8,
        observability_task_probe_concurrency: int = 4,
        trace_finalize_timeout: float = 8.0,
        min_new_code_files: int = DEFAULT_MIN_NEW_CODE_FILES,
        min_new_code_lines: int = DEFAULT_MIN_NEW_CODE_LINES,
        require_full_chain_evidence: bool = True,
        workspace_mode: str = "per_project",
        run_architect_stage: bool = True,
        run_chief_engineer_stage: bool = False,
        require_architect_stage: bool = False,
        require_chief_engineer_stage: bool = False,
        chain_profile: str = "court_strict",
    ) -> None:
        self.root_workspace = Path(workspace).resolve()
        self.workspace = self.root_workspace
        self.backend_url = str(backend_url or "").strip().rstrip("/")
        self.token = str(token or "").strip()
        self.ramdisk_root = ensure_stress_runtime_root(
            ramdisk_root or default_stress_runtime_root("tests-agent-stress-runtime")
        )
        self.factory_timeout = factory_timeout
        self.poll_interval = poll_interval
        self.request_timeout = max(float(request_timeout or 0.0), 0.5)
        self.control_plane_stall_timeout = min(
            max(float(control_plane_stall_timeout or 0.0), 5.0),
            MAX_NON_LLM_CONTROL_PLANE_STALL_SECONDS,
        )
        self.observability_request_timeout = max(float(observability_request_timeout or 0.0), 0.5)
        self.observability_snapshot_timeout = max(float(observability_snapshot_timeout or 0.0), 1.0)
        self.observability_llm_timeout = max(float(observability_llm_timeout or 0.0), 0.5)
        self.observability_max_task_probes = max(int(observability_max_task_probes or 0), 1)
        self.observability_task_probe_concurrency = max(int(observability_task_probe_concurrency or 0), 1)
        self.trace_finalize_timeout = max(float(trace_finalize_timeout or 0.0), 1.0)
        self.min_new_code_files = max(int(min_new_code_files or 0), 1)
        self.min_new_code_lines = max(int(min_new_code_lines or 0), 1)
        self.require_full_chain_evidence = bool(require_full_chain_evidence)
        normalized_workspace_mode = str(workspace_mode or "per_project").strip().lower()
        if normalized_workspace_mode not in {"per_project", "per_round"}:
            normalized_workspace_mode = "per_project"
        self.workspace_mode = normalized_workspace_mode
        self.run_architect_stage = bool(run_architect_stage)
        self.run_chief_engineer_stage = bool(run_chief_engineer_stage)
        self.require_architect_stage = bool(require_architect_stage)
        self.require_chief_engineer_stage = bool(require_chief_engineer_stage)

        normalized_chain_profile = str(chain_profile or "court_strict").strip().lower()
        if normalized_chain_profile != "court_strict":
            raise ValueError("tests.agent_stress only supports chain_profile='court_strict'")
        self.chain_profile = normalized_chain_profile
        if self.chain_profile == "court_strict":
            self.run_architect_stage = True
            self.require_architect_stage = True
            if not self.require_chief_engineer_stage:
                self.run_chief_engineer_stage = False

        # 路径回退计数（验收时必须为 0）
        self.path_fallback_count: int = 0
        self._current_round_path_fallback_before: int = 0

        # 创建带鉴权的 HTTP 客户端
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        timeout = httpx.Timeout(self.request_timeout, connect=min(self.request_timeout, 2.0))
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers)

        # 追踪器
        self.tracer: RuntimeTracer | None = None

        # 可观测性收集器 (为 AI Agent 提供详细诊断数据)
        self.collector: ObservabilityCollector | None = None

    @staticmethod
    def _sanitize_workspace_component(value: str) -> str:
        token = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
        return token or "project"

    def _resolve_round_workspace(self, round_number: int, project: ProjectDefinition) -> Path:
        project_token = self._sanitize_workspace_component(project.id)
        if self.workspace_mode == "per_round":
            folder_name = f"round-{round_number:03d}-{project_token}"
        else:
            folder_name = project_token
        candidate = self.root_workspace / "projects" / folder_name
        return ensure_stress_workspace_path(candidate)

    async def __aenter__(self) -> Self:
        self.tracer = RuntimeTracer(
            backend_url=self.backend_url,
            workspace=str(self.workspace),
            token=self.token,
            poll_interval=self.poll_interval,
            request_timeout=self.observability_request_timeout,
            final_sync_timeout=self.trace_finalize_timeout,
        )
        self.collector = ObservabilityCollector(
            backend_url=self.backend_url,
            token=self.token,
            request_timeout=self.observability_request_timeout,
            llm_events_timeout=self.observability_llm_timeout,
            snapshot_timeout=self.observability_snapshot_timeout,
            max_task_probes=self.observability_max_task_probes,
            task_probe_concurrency=self.observability_task_probe_concurrency,
        )
        await self.tracer.__aenter__()
        await self.collector.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.collector:
            await self.collector.__aexit__(*args)
        if self.tracer:
            await self.tracer.__aexit__(*args)
        if self.client:
            await self.client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        max_attempts: int = DEFAULT_CONTROL_PLANE_RETRY_ATTEMPTS,
    ) -> httpx.Response:
        request_timeout = max(float(timeout or self.request_timeout), 0.5)
        attempts = max(int(max_attempts or 0), 1)
        for attempt in range(1, attempts + 1):
            try:
                response = await self.client.request(
                    method.upper(),
                    url,
                    json=json_body,
                    params=params,
                    timeout=request_timeout,
                )
                if response.status_code in RETRYABLE_HTTP_STATUS_CODES and attempt < attempts:
                    delay = min(
                        DEFAULT_CONTROL_PLANE_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)),
                        2.0,
                    )
                    await asyncio.sleep(delay)
                    continue
                return response
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt >= attempts:
                    raise exc
                delay = min(
                    DEFAULT_CONTROL_PLANE_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)),
                    2.0,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("control_plane_retry_exhausted")
