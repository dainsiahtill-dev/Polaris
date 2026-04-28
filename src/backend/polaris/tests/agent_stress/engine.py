"""压测引擎 - 纯 HTTP API 驱动

完全通过 Polaris HTTP API 执行压测：
- POST /v2/factory/runs     - 创建端到端运行
- GET /v2/factory/runs/{id} - 轮询状态
- GET /v2/director/tasks    - 任务血缘追踪
- GET /v2/factory/runs/{id}/events - Runtime 事件

禁止直接操作文件系统或调用内部 CLI 模块。
"""

import ast
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Self

import httpx
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots
from .paths import ensure_backend_root_on_syspath

ensure_backend_root_on_syspath()
from .contracts import (
    factory_failure_evidence,
    factory_failure_info,
    is_generic_failure_point,
    normalize_status,
    resolve_factory_stage_index,
)
from .observability import DiagnosticReport, ObservabilityCollector
from .project_pool import ProjectDefinition
from .stress_path_policy import (
    default_stress_runtime_root,
    ensure_stress_runtime_root,
    ensure_stress_workspace_path,
    runtime_layout_policy_violations,
)
from .tracer import RoundTrace, RuntimeTracer, TaskLineage
import contextlib

MAX_NON_LLM_CONTROL_PLANE_STALL_SECONDS = 120.0
DEFAULT_MIN_NEW_CODE_FILES = 2
DEFAULT_MIN_NEW_CODE_LINES = 80
DEFAULT_CONTROL_PLANE_RETRY_ATTEMPTS = 3
DEFAULT_CONTROL_PLANE_RETRY_BACKOFF_SECONDS = 0.5
RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
COMPLETED_ROLE_STATUSES = {"completed", "success", "done"}
FAILED_ROLE_STATUSES = {"failed", "error", "cancelled", "blocked", "timeout"}
FALLBACK_SCAFFOLD_SIGNATURES = (
    "Auto-generated starter entrypoint for Polaris stress workflow",
    "This scaffold was auto-generated because Director completed without file output",
    "Generated Project Scaffold",
    "Execute ready tasks",
)
PLACEHOLDER_CODE_SIGNATURES = (
    ("todo", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("fixme", re.compile(r"\bFIXME\b", re.IGNORECASE)),
    ("tbd", re.compile(r"\bTBD\b", re.IGNORECASE)),
    ("not_implemented", re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE)),
    ("empty_business_logic", re.compile(r"实现核心业务逻辑|核心逻辑待实现|业务逻辑待实现", re.IGNORECASE)),
    ("placeholder", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("stub", re.compile(r"\bstub\b", re.IGNORECASE)),
)
GENERIC_SCAFFOLD_MARKERS = (
    "项目主入口模块",
    "通用工具函数模块",
    "helpers 模块的单元测试",
    "def safe_divide(",
    "def parse_arguments(",
    "应用程序主入口点",
)
PYTHON_EMPTY_FUNCTION_FALLBACK_PATTERN = re.compile(
    r"def\s+(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)\s*:\s*\n[ \t]+(?:pass|\.{3}|#\s*\.{3})[ \t]*(?:\n|$)",
    re.MULTILINE,
)
JS_TS_EMPTY_FUNCTION_PATTERN = re.compile(
    r"(?:function\s+[A-Za-z_$][\w$]*\s*\([^)]*\)\s*\{\s*\}"
    r"|(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*\([^)]*\)\s*=>\s*\{\s*\})",
    re.MULTILINE,
)
DOMAIN_KEYWORD_STOPWORDS = {
    "app",
    "application",
    "code",
    "core",
    "data",
    "demo",
    "helper",
    "main",
    "module",
    "project",
    "script",
    "service",
    "system",
    "test",
    "tool",
    "unit",
    "utils",
    "项目",
    "功能",
    "工具",
    "模块",
    "应用",
    "测试",
    "系统",
    "配置",
    "管理",
    "脚本",
    "数据",
}
MIN_GENERIC_SCAFFOLD_MARKERS = 2
MIN_CROSS_PROJECT_DUPLICATE_FILES = 3
MIN_CROSS_PROJECT_DUPLICATE_RATIO = 0.8
STAGE_NAME_TO_CHAIN_ROLE = {
    "docs_generation": "architect",
    "pm_planning": "pm",
    "director_dispatch": "director",
    "quality_gate": "qa",
    "chief_engineer_review": "chief_engineer",
    "chief_engineer": "chief_engineer",
}
PROJECT_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".html",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
    ".kt",
    ".swift",
    ".php",
    ".rb",
    ".sh",
    ".ps1",
}
IGNORED_WORKSPACE_ROOTS = {
    ".polaris",
    "stress_reports",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}


class StageResult(Enum):
    """阶段执行结果"""

    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class StageExecution:
    """阶段执行记录"""

    stage_name: str
    result: StageResult
    start_time: str
    end_time: str
    duration_ms: int
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    artifacts: list[str] = field(default_factory=list)


@dataclass
class CodeFileSnapshot:
    """代码文件快照"""

    digest: str
    line_count: int


@dataclass
class RoundResult:
    """单轮压测结果"""

    round_number: int
    project: ProjectDefinition
    start_time: str
    entry_stage: str = "architect"
    end_time: str | None = None
    overall_result: str = "pending"  # PASS/FAIL/PARTIAL

    # Factory 运行 ID
    factory_run_id: str | None = None

    # 各阶段结果 (从 Factory 运行状态映射)
    architect_stage: StageExecution | None = None
    pm_stage: StageExecution | None = None
    chief_engineer_stage: StageExecution | None = None
    director_stage: StageExecution | None = None
    qa_stage: StageExecution | None = None

    # 追踪数据
    trace: RoundTrace | None = None

    # 失败分析
    failure_point: str = ""  # Polaris 哪一环失效
    failure_evidence: str = ""  # 失败证据
    root_cause: str = ""  # 根因分析

    # 诊断报告 (AI Agent 可据此修复 Polaris)
    diagnostic_report: DiagnosticReport | None = None
    observability_data: dict[str, Any] | None = None
    workspace_artifacts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        project: ProjectDefinition,
    ) -> "RoundResult":
        result = cls(
            round_number=int(payload.get("round_number") or 0),
            project=project,
            start_time=str(payload.get("start_time") or "").strip(),
            entry_stage=str(payload.get("entry_stage") or "architect").strip() or "architect",
            end_time=str(payload.get("end_time") or "").strip() or None,
            overall_result=str(payload.get("overall_result") or "pending"),
            factory_run_id=str(payload.get("factory_run_id") or "").strip() or None,
            failure_point=str(((payload.get("failure_analysis") or {}).get("failure_point")) or "").strip(),
            failure_evidence=str(((payload.get("failure_analysis") or {}).get("failure_evidence")) or "").strip(),
            root_cause=str(((payload.get("failure_analysis") or {}).get("root_cause")) or "").strip(),
        )
        stages_data = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
        for stage_name, stage_data in stages_data.items():
            if not isinstance(stage_data, dict):
                continue
            try:
                stage_result = StageResult(str(stage_data.get("result") or "failure"))
            except ValueError:
                stage_result = StageResult.FAILURE
            stage = StageExecution(
                stage_name=str(stage_data.get("stage_name") or stage_name),
                result=stage_result,
                start_time=str(stage_data.get("start_time") or "").strip(),
                end_time=str(stage_data.get("end_time") or "").strip(),
                duration_ms=int(stage_data.get("duration_ms") or 0),
                exit_code=int(stage_data.get("exit_code") or 0),
                stdout=str(stage_data.get("stdout") or "").strip(),
                stderr=str(stage_data.get("stderr") or "").strip(),
                error=str(stage_data.get("error") or "").strip(),
                artifacts=[str(item).strip() for item in (stage_data.get("artifacts") or []) if str(item).strip()],
            )
            setattr(result, f"{stage_name}_stage", stage)

        trace_payload = payload.get("trace")
        result.trace = (
            RoundTrace.from_dict(trace_payload) if isinstance(trace_payload, dict) and trace_payload else None
        )
        diagnostic_payload = payload.get("diagnostic_report")
        result.diagnostic_report = (
            DiagnosticReport.from_dict(diagnostic_payload)
            if isinstance(diagnostic_payload, dict) and diagnostic_payload
            else None
        )
        observability_data = payload.get("observability_data")
        result.observability_data = dict(observability_data) if isinstance(observability_data, dict) else None
        workspace_artifacts = payload.get("workspace_artifacts")
        result.workspace_artifacts = dict(workspace_artifacts) if isinstance(workspace_artifacts, dict) else {}
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "project": {
                "id": self.project.id,
                "name": self.project.name,
                "category": self.project.category.value,
            },
            "start_time": self.start_time,
            "entry_stage": self.entry_stage,
            "end_time": self.end_time,
            "overall_result": self.overall_result,
            "factory_run_id": self.factory_run_id,
            "stages": {
                "architect": self._stage_to_dict(self.architect_stage),
                "pm": self._stage_to_dict(self.pm_stage),
                "chief_engineer": self._stage_to_dict(self.chief_engineer_stage),
                "director": self._stage_to_dict(self.director_stage),
                "qa": self._stage_to_dict(self.qa_stage),
            },
            "trace": self.trace.to_dict() if self.trace else None,
            "failure_analysis": {
                "failure_point": self.failure_point,
                "failure_evidence": self.failure_evidence,
                "root_cause": self.root_cause,
            },
            "diagnostic_report": self._diagnostic_to_dict(self.diagnostic_report),
            "observability_data": self.observability_data,
            "workspace_artifacts": self.workspace_artifacts,
        }

    def _diagnostic_to_dict(self, report: DiagnosticReport | None) -> dict[str, Any] | None:
        if not report:
            return None
        return {
            "round_number": report.round_number,
            "factory_run_id": report.factory_run_id,
            "failure_category": report.failure_category.value,
            "failure_point": report.failure_point,
            "timestamp": report.timestamp,
            "summary": report.summary,
            "evidence": report.evidence,
            "root_cause_analysis": report.root_cause_analysis,
            "suggested_fixes": report.suggested_fixes,
            "related_logs": report.related_logs,
        }

    def _stage_to_dict(self, stage: StageExecution | None) -> dict[str, Any] | None:
        if not stage:
            return None
        return {
            "stage_name": stage.stage_name,
            "result": stage.result.value,
            "start_time": stage.start_time,
            "end_time": stage.end_time,
            "duration_ms": stage.duration_ms,
            "exit_code": stage.exit_code,
            "error": stage.error,
            "artifacts": stage.artifacts,
        }


class StressEngine:
    """压测引擎 - 纯 HTTP API 驱动

    只使用 Polaris 对外暴露的 HTTP API：
    - /settings                 - 配置 workspace
    - /v2/factory/runs          - 创建/查询 Factory 运行
    - /v2/director/tasks        - 任务状态查询
    - /v2/factory/runs/{id}/events - 运行时事件
    """

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

    async def run_round(
        self,
        round_number: int,
        project: ProjectDefinition,
        remediation_notes: str = "",
        start_from_override: str = "",
    ) -> RoundResult:
        """执行单轮压测 - 通过 Factory API 端到端驱动"""
        self.workspace = self._resolve_round_workspace(round_number, project)
        self.workspace.mkdir(parents=True, exist_ok=True)
        requested_entry_stage = self._normalize_entry_stage(start_from_override)
        if not str(start_from_override or "").strip():
            requested_entry_stage = "architect" if self.run_architect_stage else "pm"

        print(f"\n{'=' * 80}")
        print(f"压测轮次 #{round_number}: {project.name}")
        print(f"类别: {project.category.value} | 复杂度: {project.complexity_level}/5")
        print(f"增强特性: {[e.value for e in project.enhancements]}")
        print(f"主链入口: {requested_entry_stage}")
        print(f"项目工作区: {self.workspace}")
        print("=" * 80)

        result = RoundResult(
            round_number=round_number,
            project=project,
            start_time=datetime.now().isoformat(),
            entry_stage=requested_entry_stage,
        )
        baseline_snapshot = self._collect_workspace_code_files()
        self._current_round_path_fallback_before = int(self.path_fallback_count)

        # Step 1: 配置 workspace
        if not await self._configure_workspace():
            result.overall_result = "FAIL"
            result.failure_point = "engine"
            result.root_cause = "无法配置 workspace"
            return await self._finalize_round(result)

        # Step 2: 创建 Factory 运行
        factory_run = await self._create_factory_run(
            project,
            remediation_notes=remediation_notes,
            start_from=result.entry_stage,
        )
        if not factory_run:
            result.overall_result = "FAIL"
            result.failure_point = "engine"
            result.root_cause = "无法创建 Factory 运行"
            return await self._finalize_round(result)

        result.factory_run_id = factory_run.get("run_id")
        print(f"[Factory] Run ID: {result.factory_run_id}")

        # Step 3: 启动追踪和可观测性收集
        self.tracer.start_round(
            round_number=round_number,
            project_id=project.id,
            project_name=project.name,
            factory_run_id=result.factory_run_id,
        )
        if self.collector:
            self.collector.start_collection(round_number, result.factory_run_id)

        # Step 4: 轮询 Factory 运行直到完成
        final_status = await self._poll_factory_run(result.factory_run_id, result)

        # Step 5: 根据 Factory 结果设置整体结果
        if final_status == "completed":
            result.overall_result = "PASS"
        elif final_status == "completed_with_warnings":
            result.overall_result = "PARTIAL"
        else:
            result.overall_result = "FAIL"

        self._enforce_project_output_gate(result, baseline_snapshot)
        return await self._finalize_round(result)

    def _collect_workspace_code_files(self, root: Path | None = None) -> dict[str, CodeFileSnapshot]:
        root = Path(root or self.workspace)
        if not root.exists() or not root.is_dir():
            return {}
        code_files: dict[str, CodeFileSnapshot] = {}
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(root)
                if not rel.parts:
                    continue
                if rel.parts[0] in IGNORED_WORKSPACE_ROOTS:
                    continue
                if any(part in IGNORED_WORKSPACE_ROOTS for part in rel.parts):
                    continue
                if path.suffix.lower() in PROJECT_CODE_EXTENSIONS:
                    try:
                        content = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError, PermissionError):
                        # 文件读取失败（权限、编码、IO错误）跳过该文件
                        continue
                    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    line_count = len(content.splitlines())
                    code_files[rel.as_posix()] = CodeFileSnapshot(
                        digest=digest,
                        line_count=line_count,
                    )
        except (OSError, PermissionError) as e:
            # 文件系统错误：记录日志后返回空字典
            print(f"[engine] Failed to collect workspace files: {type(e).__name__}: {e}")
            return {}
        return code_files

    @staticmethod
    def _build_project_domain_keywords(project: ProjectDefinition) -> list[str]:
        keywords: set[str] = set()
        sources = [
            project.id.replace("-", " "),
            project.name,
            project.description,
            *project.stress_focus,
        ]
        for source in sources:
            lowered = str(source or "").strip().lower()
            if not lowered:
                continue
            for token in re.findall(r"[a-zA-Z]{3,}", lowered):
                normalized = token.lower()
                if normalized in DOMAIN_KEYWORD_STOPWORDS:
                    continue
                keywords.add(normalized)
            for token in re.findall(r"[\u4e00-\u9fff]{3,}", lowered):
                normalized = token.lower()
                if normalized in DOMAIN_KEYWORD_STOPWORDS:
                    continue
                keywords.add(normalized)
        return sorted(keywords)

    def _detect_cross_project_duplicate_files(
        self,
        *,
        effective_files: list[str],
        current_snapshot: dict[str, CodeFileSnapshot],
    ) -> list[dict[str, Any]]:
        if self.workspace_mode != "per_project":
            return []
        if len(effective_files) < MIN_CROSS_PROJECT_DUPLICATE_FILES:
            return []
        projects_root = self.root_workspace / "projects"
        if not projects_root.exists() or not projects_root.is_dir():
            return []

        current_workspace = self.workspace.resolve()
        findings: list[dict[str, Any]] = []
        min_duplicate_files = min(MIN_CROSS_PROJECT_DUPLICATE_FILES, len(effective_files))

        for sibling in projects_root.iterdir():
            if not sibling.is_dir():
                continue
            sibling_resolved = sibling.resolve()
            if sibling_resolved == current_workspace:
                continue
            sibling_snapshot = self._collect_workspace_code_files(root=sibling_resolved)
            if not sibling_snapshot:
                continue
            matched_files = [
                rel_path
                for rel_path in effective_files
                if rel_path in current_snapshot
                and rel_path in sibling_snapshot
                and current_snapshot[rel_path].digest == sibling_snapshot[rel_path].digest
            ]
            if len(matched_files) < min_duplicate_files:
                continue
            match_ratio = len(matched_files) / len(effective_files)
            if match_ratio < MIN_CROSS_PROJECT_DUPLICATE_RATIO:
                continue
            findings.append(
                {
                    "project": sibling_resolved.name,
                    "matched_file_count": len(matched_files),
                    "match_ratio": round(match_ratio, 3),
                    "matched_files": matched_files[:20],
                }
            )

        findings.sort(
            key=lambda item: (
                float(item.get("match_ratio") or 0.0),
                int(item.get("matched_file_count") or 0),
            ),
            reverse=True,
        )
        return findings

    @staticmethod
    def _normalize_entry_stage(entry_stage: str | None) -> str:
        token = str(entry_stage or "").strip().lower()
        if token in {"architect", "pm", "director"}:
            return token
        return "architect"

    def _resolve_round_entry_stage(self, result: RoundResult) -> str:
        stage_token = self._normalize_entry_stage(getattr(result, "entry_stage", "architect"))
        if stage_token != "architect":
            return stage_token
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        chain_policy = (
            workspace_artifacts.get("chain_policy") if isinstance(workspace_artifacts.get("chain_policy"), dict) else {}
        )
        candidate = str(chain_policy.get("entry_stage") or workspace_artifacts.get("entry_stage") or "").strip()
        normalized = self._normalize_entry_stage(candidate)
        return normalized or "architect"

    def _expected_chain_roles(self, *, entry_stage: str | None = None) -> list[str]:
        normalized_entry = self._normalize_entry_stage(entry_stage)

        base_roles: list[str] = []
        if self.run_architect_stage or self.require_architect_stage:
            base_roles.append("architect")
        base_roles.extend(["pm", "director", "qa"])

        if normalized_entry not in base_roles:
            return base_roles
        start_index = base_roles.index(normalized_entry)
        return base_roles[start_index:]

    @staticmethod
    def _dedupe_preserve_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            ordered.append(token)
        return ordered

    @staticmethod
    def _dedupe_resolved_artifacts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        ordered: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            artifact = str(item.get("artifact") or "").strip()
            resolved_by = str(item.get("resolved_by") or "").strip()
            resolved_path = str(item.get("resolved_path") or "").strip()
            if not artifact or not resolved_path:
                continue
            key = f"{artifact}|{resolved_by}|{resolved_path}"
            if key in seen:
                continue
            seen.add(key)
            ordered.append(
                {
                    "artifact": artifact,
                    "resolved_by": resolved_by,
                    "resolved_path": resolved_path,
                }
            )
        return ordered

    def _is_path_in_trusted_root(self, path: Path, run_id: str) -> bool:
        """绝对路径受信根目录校验

        受信根：
        - {workspace}/.polaris
        - {workspace}/.polaris/factory/{run_id}
        - runtime project root 及其 runtime 根目录
        """
        try:
            resolved = path.resolve()
            workspace_resolved = self.workspace.resolve()
            roots = resolve_storage_roots(
                str(workspace_resolved),
                ramdisk_root=str(self.ramdisk_root),
            )
            runtime_root = Path(roots.runtime_project_root).resolve()
            runtime_project_root = runtime_root.parent.resolve()

            # 受信任路径前缀
            trusted_roots = [
                workspace_resolved / ".polaris",
                workspace_resolved / ".polaris" / "factory" / run_id,
                runtime_project_root,
                runtime_root,
            ]

            for trusted in trusted_roots:
                try:
                    resolved.relative_to(trusted)
                    return True
                except ValueError:
                    continue
            return False
        except (OSError, TypeError):
            # 路径解析失败（无效路径或系统错误）
            return False

    def _resolve_stage_artifact_path(self, run_id: str, relative_path: str) -> dict[str, Any] | None:
        """解析阶段 artifact 路径

        返回包含元数据的字典：
        {
            "path": Path,
            "resolved_by": "logical_path" | ".polaris_factory" | ".polaris_artifacts",
            "resolved_path": str
        }
        """
        rel = str(relative_path or "").strip().replace("\\", "/")
        if not rel:
            return None
        normalized_rel = rel.lstrip("/")
        candidates: list[tuple[Path, str]] = []

        # Task A2: 绝对路径受信根目录校验
        if Path(rel).is_absolute():
            abs_path = Path(rel)
            if self._is_path_in_trusted_root(abs_path, run_id):
                candidates.append((abs_path, "absolute_trusted"))
            else:
                # 不受信的绝对路径直接拒绝
                return None
        else:
            # Task A1: 收紧路径解析候选列表（仅逻辑路径 + .polaris 受限路径）
            # 尝试逻辑路径
            try:
                logical_path = Path(
                    resolve_logical_path(
                        str(self.workspace),
                        normalized_rel,
                        ramdisk_root=str(self.ramdisk_root),
                    )
                )
                candidates.append((logical_path, "logical_path"))
            except (OSError, ValueError):
                # 逻辑路径解析失败：忽略此候选
                pass

            # .polaris/factory/{run_id}
            candidates.append(
                (
                    self.workspace / ".polaris" / "factory" / run_id / Path(normalized_rel),
                    ".polaris_factory",
                )
            )
            # .polaris/factory/{run_id}/artifacts
            candidates.append(
                (
                    self.workspace / ".polaris" / "factory" / run_id / "artifacts" / Path(normalized_rel),
                    ".polaris_artifacts",
                )
            )
            # .polaris（仅此受限路径）
            candidates.append((self.workspace / ".polaris" / Path(normalized_rel), ".polaris"))
            # 注意：已删除 self.workspace / Path(normalized_rel) 不受控兜底

        seen: set[str] = set()
        fallback_count = 0
        # 以第一个候选的类型作为基准类型（期望的类型）
        first_resolve_type = candidates[0][1] if candidates else None
        for candidate, resolve_type in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)

            # Task A3: 统计回退次数（与基准类型不同的路径都算回退）
            if first_resolve_type and resolve_type != first_resolve_type:
                fallback_count += 1

            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    self.path_fallback_count += fallback_count
                    return {
                        "path": candidate,
                        "resolved_by": resolve_type,
                        "resolved_path": str(candidate),
                    }
                if candidate.is_dir():
                    if any(path.is_file() and path.stat().st_size > 0 for path in candidate.rglob("*")):
                        self.path_fallback_count += fallback_count
                        return {
                            "path": candidate,
                            "resolved_by": resolve_type,
                            "resolved_path": str(candidate),
                        }
            except (OSError, PermissionError):
                # 文件系统错误：跳过此候选路径
                continue
        return None

    def _extract_chain_stage_evidence(
        self,
        events: list[dict[str, Any]],
        *,
        run_id: str,
        expected_role_order: list[str] | None = None,
    ) -> dict[str, Any]:
        observed_role_sequence: list[str] = []
        stages: dict[str, dict[str, Any]] = {}

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip()
            result_payload = event.get("result") if isinstance(event.get("result"), dict) else {}
            stage_name = str(event.get("stage") or "").strip()
            if not stage_name:
                stage_name = str(result_payload.get("stage") or "").strip()
            role = STAGE_NAME_TO_CHAIN_ROLE.get(stage_name)
            if not role:
                continue
            stage = stages.setdefault(
                role,
                {
                    "stage_names": [],
                    "statuses": [],
                    "declared_artifacts": [],
                    "existing_artifacts": [],
                    "resolved_artifacts": [],
                    "missing_artifacts": [],
                },
            )
            stage["stage_names"].append(stage_name)
            if event_type == "stage_started":
                observed_role_sequence.append(role)
            if event_type != "stage_completed":
                continue
            status = normalize_status(result_payload.get("status") or event.get("status"))
            if status:
                stage["statuses"].append(status)
            artifacts_raw = result_payload.get("artifacts")
            artifacts = artifacts_raw if isinstance(artifacts_raw, list) else []
            for artifact in artifacts:
                rel = str(artifact or "").strip()
                if not rel:
                    continue
                stage["declared_artifacts"].append(rel)
                resolved = self._resolve_stage_artifact_path(run_id, rel)
                if resolved is None:
                    stage["missing_artifacts"].append(rel)
                else:
                    stage["existing_artifacts"].append(resolved["resolved_path"])
                    stage["resolved_artifacts"].append(
                        {
                            "artifact": rel,
                            "resolved_by": resolved["resolved_by"],
                            "resolved_path": resolved["resolved_path"],
                        }
                    )

        for payload in stages.values():
            payload["stage_names"] = self._dedupe_preserve_order(payload["stage_names"])
            payload["statuses"] = self._dedupe_preserve_order(payload["statuses"])
            payload["declared_artifacts"] = self._dedupe_preserve_order(payload["declared_artifacts"])
            payload["existing_artifacts"] = self._dedupe_preserve_order(payload["existing_artifacts"])
            payload["resolved_artifacts"] = self._dedupe_resolved_artifacts(payload["resolved_artifacts"])
            payload["missing_artifacts"] = self._dedupe_preserve_order(payload["missing_artifacts"])

        return {
            "expected_role_order": (
                self._dedupe_preserve_order(expected_role_order or []) or self._expected_chain_roles()
            ),
            "observed_role_order": self._dedupe_preserve_order(observed_role_sequence),
            "stages": stages,
        }

    async def _capture_chain_stage_evidence(self, result: RoundResult) -> None:
        run_id = str(result.factory_run_id or "").strip()
        if not run_id:
            return
        try:
            events = await self._fetch_factory_events(run_id)
        except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError) as exc:
            workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
            workspace_artifacts["chain_stage_evidence"] = {
                "error": f"fetch_factory_events_failed: {type(exc).__name__}: {exc}",
            }
            result.workspace_artifacts = workspace_artifacts
            return

        expected_roles = self._expected_chain_roles(entry_stage=self._resolve_round_entry_stage(result))
        chain_evidence = self._extract_chain_stage_evidence(
            events,
            run_id=run_id,
            expected_role_order=expected_roles,
        )
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        chain_evidence["path_contract"] = {
            "path_fallback_count": int(workspace_artifacts.get("path_fallback_count") or 0),
            "pass": bool(workspace_artifacts.get("path_contract_ok") is True),
        }
        workspace_artifacts["chain_stage_evidence"] = chain_evidence
        result.workspace_artifacts = workspace_artifacts

    def _validate_pm_task_contract(self, result: RoundResult, stage_evidence: dict[str, Any]) -> str | None:
        stages = stage_evidence.get("stages") if isinstance(stage_evidence.get("stages"), dict) else {}
        pm_stage = stages.get("pm") if isinstance(stages.get("pm"), dict) else {}
        existing_artifacts = (
            pm_stage.get("existing_artifacts") if isinstance(pm_stage.get("existing_artifacts"), list) else []
        )
        plan_candidates = [
            Path(path)
            for path in existing_artifacts
            if str(path).replace("\\", "/").lower().endswith("tasks/plan.json")
        ]
        if not plan_candidates:
            return "pm_plan_missing_artifact"

        plan_path = plan_candidates[0]
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"pm_plan_invalid_json:{plan_path}"

        tasks: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            raw_tasks = payload.get("tasks")
            if isinstance(raw_tasks, list):
                tasks = [item for item in raw_tasks if isinstance(item, dict)]
            elif isinstance(payload.get("plan"), dict):
                nested_tasks = payload.get("plan", {}).get("tasks")
                if isinstance(nested_tasks, list):
                    tasks = [item for item in nested_tasks if isinstance(item, dict)]
        if not tasks:
            return f"pm_plan_empty_tasks:{plan_path}"

        def has_field(task: dict[str, Any], keys: tuple[str, ...], *, require_list: bool = False) -> bool:
            for key in keys:
                value = task.get(key)
                if require_list and isinstance(value, list) and len(value) > 0:
                    return True
                if not require_list and str(value or "").strip():
                    return True
            return False

        invalid_tasks = 0
        for task in tasks:
            has_goal = has_field(task, ("goal", "title", "objective", "目标"))
            has_scope = has_field(task, ("scope", "范围", "作用域"))
            has_steps = has_field(task, ("steps", "implementation_steps", "执行步骤"), require_list=True)
            has_acceptance = has_field(
                task,
                ("acceptance", "acceptance_criteria", "验收标准", "可测验收"),
                require_list=True,
            )
            if not (has_goal and has_scope and has_steps and has_acceptance):
                invalid_tasks += 1
        if invalid_tasks > 0:
            return f"pm_plan_incomplete_tasks:{invalid_tasks}/{len(tasks)}"
        return None

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(parsed, 0)

    @staticmethod
    def _parse_json_file(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _extract_dispatch_metrics(self, chain_stage_evidence: dict[str, Any]) -> dict[str, Any]:
        stages = chain_stage_evidence.get("stages") if isinstance(chain_stage_evidence.get("stages"), dict) else {}
        director_stage = stages.get("director") if isinstance(stages.get("director"), dict) else {}
        existing_artifacts = (
            director_stage.get("existing_artifacts")
            if isinstance(director_stage.get("existing_artifacts"), list)
            else []
        )

        completed_statuses = {"completed", "success", "done"}
        failed_statuses = {"failed", "error", "cancelled", "blocked", "timeout"}
        best: dict[str, Any] = {
            "task_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "task_status_counts": {},
            "dispatch_log": "",
            "failed_tasks": [],
        }

        for candidate_raw in existing_artifacts:
            candidate_path = Path(str(candidate_raw or "").strip())
            if not candidate_path.exists() or not candidate_path.is_file():
                continue
            normalized = candidate_path.as_posix().lower()
            if not normalized.endswith("/dispatch/log.json"):
                continue
            payload = self._parse_json_file(candidate_path)
            if not payload:
                continue

            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            task_status_counts = (
                metadata.get("task_status_counts")
                if isinstance(metadata.get("task_status_counts"), dict)
                else (payload.get("task_status_counts") if isinstance(payload.get("task_status_counts"), dict) else {})
            )
            summarized_total = sum(self._safe_int(v) for v in task_status_counts.values())
            explicit_total = self._safe_int(
                metadata.get("task_count") if "task_count" in metadata else payload.get("task_count")
            )
            task_count = max(explicit_total, summarized_total)

            completed_count = 0
            failed_count = 0
            for status_name, status_count in task_status_counts.items():
                normalized_status = normalize_status(status_name)
                count = self._safe_int(status_count)
                if normalized_status in completed_statuses:
                    completed_count += count
                elif normalized_status in failed_statuses:
                    failed_count += count

            failed_tasks = metadata.get("failed_tasks") if isinstance(metadata.get("failed_tasks"), list) else []
            if failed_tasks and failed_count <= 0:
                failed_count = len([item for item in failed_tasks if isinstance(item, dict)])
            if task_count <= 0 and failed_count > 0:
                task_count = failed_count

            if task_count > int(best.get("task_count") or 0):
                best = {
                    "task_count": task_count,
                    "completed_count": min(completed_count, task_count),
                    "failed_count": min(failed_count, task_count),
                    "task_status_counts": dict(task_status_counts),
                    "dispatch_log": str(candidate_path),
                    "failed_tasks": [item for item in failed_tasks if isinstance(item, dict)],
                }

        return best

    def _backfill_trace_from_dispatch_artifact(
        self,
        result: RoundResult,
        chain_stage_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = self._extract_dispatch_metrics(chain_stage_evidence)
        task_count = self._safe_int(metrics.get("task_count"))
        if task_count <= 0:
            return {}

        completed_count = min(self._safe_int(metrics.get("completed_count")), task_count)
        failed_count = min(self._safe_int(metrics.get("failed_count")), task_count)

        if result.trace is None:
            result.trace = RoundTrace(
                round_number=result.round_number,
                project_id=result.project.id,
                project_name=result.project.name,
                start_time=result.start_time,
                factory_run_id=result.factory_run_id,
            )
        trace_obj = result.trace
        trace_obj.total_tasks = max(self._safe_int(trace_obj.total_tasks), task_count)
        trace_obj.completed_tasks = max(self._safe_int(trace_obj.completed_tasks), completed_count)
        trace_obj.failed_tasks = max(self._safe_int(trace_obj.failed_tasks), failed_count)

        if not isinstance(trace_obj.tasks, dict):
            trace_obj.tasks = {}

        if len(trace_obj.tasks) == 0:
            failed_tasks = metrics.get("failed_tasks") if isinstance(metrics.get("failed_tasks"), list) else []
            for item in failed_tasks:
                task_id = str(item.get("task_id") or "").strip()
                if not task_id:
                    continue
                trace_obj.tasks[task_id] = TaskLineage(
                    task_id=task_id,
                    subject=str(item.get("subject") or "director task").strip() or "director task",
                    status=normalize_status(item.get("status") or "failed"),
                    created_by=str(item.get("role_id") or "director").strip() or "director",
                    created_at=str(item.get("updated_at") or result.end_time or result.start_time).strip(),
                    result_summary=str(item.get("error_message") or "").strip(),
                    pm_task_id=str(item.get("pm_task_id") or "").strip() or None,
                )
            synthetic_needed = max(trace_obj.total_tasks - len(trace_obj.tasks), 0)
            for index in range(synthetic_needed):
                synthetic_id = f"dispatch-task-{index + 1}"
                if synthetic_id in trace_obj.tasks:
                    continue
                synthetic_status = "completed" if index < trace_obj.completed_tasks else "failed"
                trace_obj.tasks[synthetic_id] = TaskLineage(
                    task_id=synthetic_id,
                    subject="director dispatch synthesized task",
                    status=synthetic_status,
                    created_by="director",
                    created_at=result.end_time or result.start_time,
                    result_summary="backfilled from dispatch/log.json metadata",
                )

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        workspace_artifacts["trace_backfill"] = {
            "source": "dispatch_log_metadata",
            "dispatch_log": str(metrics.get("dispatch_log") or ""),
            "task_count": trace_obj.total_tasks,
            "completed_tasks": trace_obj.completed_tasks,
            "failed_tasks": trace_obj.failed_tasks,
        }
        result.workspace_artifacts = workspace_artifacts
        return {
            "task_count": trace_obj.total_tasks,
            "completed_tasks": trace_obj.completed_tasks,
            "failed_tasks": trace_obj.failed_tasks,
        }

    def _extract_tool_executions_from_adapter_logs(self) -> list[dict[str, Any]]:
        log_dir = Path(resolve_runtime_path(str(self.workspace), "runtime/roles/director/logs"))
        if not log_dir.exists() or not log_dir.is_dir():
            return []

        candidates = sorted(
            [path for path in log_dir.glob("adapter_debug_*.jsonl") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return []

        extracted: list[dict[str, Any]] = []
        seen: set[str] = set()

        def append_tool_item(timestamp: str, item: dict[str, Any]) -> None:
            tool_name = str(item.get("tool") or item.get("source_tool") or "").strip()
            if not tool_name:
                return
            file_path = str(item.get("file") or "").strip()
            success = bool(item.get("success", False))
            error_message = str(item.get("error") or "").strip()
            dedupe_key = "|".join([timestamp, tool_name, file_path, str(success), error_message])
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            extracted.append(
                {
                    "tool_name": tool_name,
                    "timestamp": timestamp,
                    "success": success,
                    "error_message": error_message,
                    "duration_ms": self._safe_int(item.get("duration_ms")),
                }
            )

        for path in candidates:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, PermissionError):
                continue
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                event_name = str(entry.get("event") or "").strip().lower()
                if event_name not in {"first_tool_results", "retry_tool_results", "workspace_diff"}:
                    continue
                payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                timestamp = str(entry.get("timestamp") or "").strip()
                items = payload.get("items") if isinstance(payload.get("items"), list) else []
                for item in items:
                    if isinstance(item, dict):
                        append_tool_item(timestamp, item)
                summary_items = payload.get("tool_summary") if isinstance(payload.get("tool_summary"), list) else []
                for item in summary_items:
                    if isinstance(item, dict):
                        append_tool_item(timestamp, item)

        return extracted

    def _backfill_observability_from_director_logs(self, result: RoundResult) -> int:
        extracted = self._extract_tool_executions_from_adapter_logs()
        if not extracted:
            return 0

        observability = result.observability_data if isinstance(result.observability_data, dict) else {}
        existing = (
            observability.get("tool_executions") if isinstance(observability.get("tool_executions"), list) else []
        )
        merged: list[dict[str, Any]] = [item for item in existing if isinstance(item, dict)]
        seen_keys: set[str] = set()
        for item in merged:
            key = "|".join(
                [
                    str(item.get("timestamp") or ""),
                    str(item.get("tool_name") or ""),
                    str(item.get("success") or ""),
                    str(item.get("error_message") or ""),
                ]
            )
            seen_keys.add(key)
        for item in extracted:
            key = "|".join(
                [
                    str(item.get("timestamp") or ""),
                    str(item.get("tool_name") or ""),
                    str(item.get("success") or ""),
                    str(item.get("error_message") or ""),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(item)

        observability["tool_executions"] = merged
        stats = observability.get("statistics") if isinstance(observability.get("statistics"), dict) else {}
        stats["total_tool_executions"] = len(merged)
        stats["failed_tool_executions"] = sum(1 for item in merged if not bool(item.get("success", False)))
        observability["statistics"] = stats

        warnings = (
            observability.get("collection_warnings")
            if isinstance(observability.get("collection_warnings"), list)
            else []
        )
        warnings.append("tool_executions backfilled from director adapter_debug logs")
        observability["collection_warnings"] = warnings[-50:]
        result.observability_data = observability

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        workspace_artifacts["observability_backfill"] = {
            "source": "director_adapter_debug_logs",
            "tool_execution_count": len(merged),
        }
        result.workspace_artifacts = workspace_artifacts
        return len(merged)

    def _enforce_project_output_gate(
        self,
        result: RoundResult,
        baseline_snapshot: dict[str, CodeFileSnapshot],
    ) -> None:
        baseline_snapshot = baseline_snapshot if isinstance(baseline_snapshot, dict) else {}
        current_snapshot = self._collect_workspace_code_files()
        current_files = set(current_snapshot.keys())
        baseline_files = set(baseline_snapshot.keys())
        new_files = sorted(current_files - baseline_files)
        modified_files = sorted(
            path
            for path in current_files
            if path in baseline_snapshot and current_snapshot[path].digest != baseline_snapshot[path].digest
        )
        effective_files = sorted(set(new_files + modified_files))
        (
            new_code_line_count,
            fallback_files,
            placeholder_markers,
            generic_scaffold_markers,
            domain_keywords,
            domain_keyword_hits,
        ) = self._inspect_new_code_files(
            effective_files,
            project=result.project,
        )
        cross_project_duplicates = self._detect_cross_project_duplicate_files(
            effective_files=effective_files,
            current_snapshot=current_snapshot,
        )
        result.workspace_artifacts = {
            "workspace": str(self.workspace),
            "baseline_code_file_count": len(baseline_snapshot),
            "code_file_count": len(current_snapshot),
            "new_code_file_count": len(effective_files),
            "truly_new_code_file_count": len(new_files),
            "modified_code_file_count": len(modified_files),
            "new_code_line_count": new_code_line_count,
            "new_code_files_sample": effective_files[:30],
            "truly_new_code_files_sample": new_files[:30],
            "modified_code_files_sample": modified_files[:30],
            "fallback_scaffold_detected": bool(fallback_files),
            "fallback_scaffold_files": fallback_files[:30],
            "placeholder_markers": placeholder_markers[:30],
            "generic_scaffold_markers": generic_scaffold_markers[:30],
            "domain_keywords": domain_keywords[:30],
            "domain_keyword_hits": domain_keyword_hits[:30],
            "cross_project_duplicate_projects": cross_project_duplicates[:10],
            "quality_gate": {
                "min_new_code_files": self.min_new_code_files,
                "min_new_code_lines": self.min_new_code_lines,
                "min_generic_scaffold_markers": MIN_GENERIC_SCAFFOLD_MARKERS,
                "min_cross_project_duplicate_files": MIN_CROSS_PROJECT_DUPLICATE_FILES,
                "min_cross_project_duplicate_ratio": MIN_CROSS_PROJECT_DUPLICATE_RATIO,
            },
            "chain_policy": {
                "run_architect_stage": self.run_architect_stage,
                "run_chief_engineer_stage": self.run_chief_engineer_stage,
                "require_architect_stage": self.require_architect_stage,
                "require_chief_engineer_stage": self.require_chief_engineer_stage,
                "entry_stage": self._resolve_round_entry_stage(result),
            },
        }

        if result.overall_result not in {"PASS", "PARTIAL"}:
            return
        if len(current_snapshot) == 0:
            self._set_quality_failure(
                result,
                failure_point="project_output_missing",
                root_cause=("Factory lifecycle completed but workspace contains no generated project code files"),
                failure_evidence=(
                    f"workspace={self.workspace} baseline_code_files={len(baseline_snapshot)} current_code_files=0"
                ),
            )
            return
        if len(effective_files) == 0:
            self._set_quality_failure(
                result,
                failure_point="project_output_stagnant",
                root_cause=("Factory lifecycle completed but this round did not produce any new project code files"),
                failure_evidence=(
                    f"workspace={self.workspace} baseline_code_files={len(baseline_snapshot)} "
                    f"current_code_files={len(current_snapshot)} new_or_modified_code_files=0"
                ),
            )
            return
        if fallback_files:
            self._set_quality_failure(
                result,
                failure_point="project_output_fallback_scaffold",
                root_cause=(
                    "Director fallback scaffold was detected; this round did not produce authentic project code"
                ),
                failure_evidence=(f"workspace={self.workspace} fallback_scaffold_files={fallback_files[:10]}"),
            )
            return
        if cross_project_duplicates:
            duplicate_summary = cross_project_duplicates[0]
            self._set_quality_failure(
                result,
                failure_point="project_output_cross_project_duplication",
                root_cause=(
                    "Generated project code is substantially duplicated from another project workspace, "
                    "indicating template-style output instead of project-specific implementation"
                ),
                failure_evidence=(
                    f"workspace={self.workspace} duplicate_project={duplicate_summary.get('project')} "
                    f"matched_file_count={duplicate_summary.get('matched_file_count')} "
                    f"match_ratio={duplicate_summary.get('match_ratio')}"
                ),
            )
            return
        if len(generic_scaffold_markers) >= MIN_GENERIC_SCAFFOLD_MARKERS:
            self._set_quality_failure(
                result,
                failure_point="project_output_generic_scaffold",
                root_cause=(
                    "Generated project output matches a known generic scaffold pattern and lacks "
                    "project-specific implementation depth"
                ),
                failure_evidence=(
                    f"workspace={self.workspace} generic_scaffold_markers={generic_scaffold_markers[:10]}"
                ),
            )
            return
        if placeholder_markers:
            self._set_quality_failure(
                result,
                failure_point="project_output_placeholder_code",
                root_cause=(
                    "Generated project output contains placeholder markers (TODO/FIXME/stub) "
                    "instead of completed business logic"
                ),
                failure_evidence=(f"workspace={self.workspace} placeholder_markers={placeholder_markers[:10]}"),
            )
            return
        if domain_keywords and not domain_keyword_hits:
            self._set_quality_failure(
                result,
                failure_point="project_output_not_project_specific",
                root_cause=(
                    "Generated project output does not contain detectable domain keywords "
                    "for the requested project, indicating weak requirement grounding"
                ),
                failure_evidence=(
                    f"workspace={self.workspace} expected_keywords={domain_keywords[:12]} matched_keywords=[]"
                ),
            )
            return
        if len(effective_files) < self.min_new_code_files:
            self._set_quality_failure(
                result,
                failure_point="project_output_too_sparse",
                root_cause=("Generated project output is too sparse and does not satisfy the stress quality baseline"),
                failure_evidence=(
                    f"workspace={self.workspace} new_or_modified_code_files={len(effective_files)} "
                    f"required_min_new_code_files={self.min_new_code_files}"
                ),
            )
            return
        if new_code_line_count < self.min_new_code_lines:
            self._set_quality_failure(
                result,
                failure_point="project_output_too_small",
                root_cause=("Generated project code size is below the minimum quality threshold"),
                failure_evidence=(
                    f"workspace={self.workspace} new_code_line_count={new_code_line_count} "
                    f"required_min_new_code_lines={self.min_new_code_lines}"
                ),
            )

    def _inspect_new_code_files(
        self,
        new_code_files: list[str],
        *,
        project: ProjectDefinition,
    ) -> tuple[int, list[str], list[str], list[str], list[str], list[str]]:
        line_count = 0
        fallback_hits: list[str] = []
        placeholder_markers: set[str] = set()
        generic_scaffold_markers: set[str] = set()
        domain_keywords = self._build_project_domain_keywords(project)
        domain_keyword_hits: set[str] = set()
        for rel_path in new_code_files:
            file_path = self.workspace / Path(rel_path)
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, PermissionError, UnicodeDecodeError):
                continue
            line_count += len(content.splitlines())
            if any(signature in content for signature in FALLBACK_SCAFFOLD_SIGNATURES):
                fallback_hits.append(rel_path)
            lowered_content = content.lower()
            searchable_text = f"{rel_path.lower()}\n{lowered_content}"
            for label, pattern in PLACEHOLDER_CODE_SIGNATURES:
                if pattern.search(content):
                    placeholder_markers.add(f"{rel_path}:{label}")
            for marker in GENERIC_SCAFFOLD_MARKERS:
                if marker.lower() in lowered_content:
                    generic_scaffold_markers.add(f"{rel_path}:{marker}")
            for keyword in domain_keywords:
                if keyword and keyword in searchable_text:
                    domain_keyword_hits.add(keyword)
        return (
            line_count,
            sorted(fallback_hits),
            sorted(placeholder_markers),
            sorted(generic_scaffold_markers),
            domain_keywords,
            sorted(domain_keyword_hits),
        )

    @staticmethod
    def _set_quality_failure(
        result: RoundResult,
        *,
        failure_point: str,
        root_cause: str,
        failure_evidence: str,
    ) -> None:
        result.overall_result = "FAIL"
        result.failure_point = failure_point
        result.root_cause = root_cause
        result.failure_evidence = failure_evidence

    async def _configure_workspace(self) -> bool:
        """通过 API 配置 workspace"""
        try:
            workspace = ensure_stress_workspace_path(self.workspace)
            url = f"{self.backend_url}/settings"

            # 先获取当前设置
            response = await self._request_with_retry(
                "GET",
                url,
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                print(f"[settings] 获取设置失败: HTTP {response.status_code}")
                return False

            def _path_equals(left: str, right: str) -> bool:
                if not left or not right:
                    return False
                try:
                    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
                except (OSError, ValueError):
                    # 路径解析失败时降级为字符串比较
                    return str(left).strip().lower() == str(right).strip().lower()

            expected_workspace = str(workspace)
            expected_ramdisk = str(self.ramdisk_root)
            payload = {
                "workspace": expected_workspace,
                "ramdisk_root": expected_ramdisk,
            }
            layout_url = f"{self.backend_url}/runtime/storage-layout"

            last_issue = ""
            for attempt in range(1, 4):
                response = await self._request_with_retry(
                    "POST",
                    url,
                    json_body=payload,
                    timeout=self.request_timeout,
                )
                if response.status_code != 200:
                    last_issue = f"update_settings_http_{response.status_code}"
                    if attempt < 3:
                        continue
                    print(f"[settings] 更新 workspace 失败: HTTP {response.status_code}")
                    return False

                verify_settings = await self._request_with_retry(
                    "GET",
                    url,
                    timeout=self.request_timeout,
                )
                if verify_settings.status_code != 200:
                    last_issue = f"verify_settings_http_{verify_settings.status_code}"
                    if attempt < 3:
                        continue
                    print(f"[settings] 校验 settings 失败: HTTP {verify_settings.status_code}")
                    return False
                settings_payload = verify_settings.json()
                effective_workspace = str(settings_payload.get("workspace") or "").strip()
                effective_ramdisk = str(settings_payload.get("ramdisk_root") or "").strip()
                if not _path_equals(effective_workspace, expected_workspace):
                    last_issue = f"workspace_not_applied:expected={expected_workspace}, actual={effective_workspace}"
                    if attempt < 3:
                        continue
                if not _path_equals(effective_ramdisk, expected_ramdisk):
                    last_issue = f"ramdisk_not_applied:expected={expected_ramdisk}, actual={effective_ramdisk}"
                    if attempt < 3:
                        continue

                layout_response = await self._request_with_retry(
                    "GET",
                    layout_url,
                    timeout=self.request_timeout,
                )
                if layout_response.status_code != 200:
                    last_issue = f"runtime_storage_layout_http_{layout_response.status_code}"
                    if attempt < 3:
                        continue
                    print(f"[settings] 获取 runtime/storage-layout 失败: HTTP {layout_response.status_code}")
                    return False

                layout = layout_response.json()
                violations = runtime_layout_policy_violations(layout)
                if violations:
                    last_issue = "; ".join(violations)
                    if attempt < 3:
                        continue
                    print("[settings] Runtime/storage layout does not satisfy stress path policy: " + last_issue)
                    return False

                self.workspace = workspace
                print(
                    f"[settings] Workspace 已配置: {self.workspace} | "
                    f"Ramdisk: {self.ramdisk_root} | Runtime Root: {layout.get('runtime_root', '')}"
                )
                return True

            print(f"[settings] 配置 workspace 未生效: {last_issue or 'unknown'}")
            return False

        except ValueError as e:
            print(f"[settings] 路径策略校验失败: {e}")
            return False
        except (OSError, httpx.HTTPError) as e:
            print(f"[settings] 配置 workspace 失败: {e}")
            return False

    async def _create_factory_run(
        self,
        project: ProjectDefinition,
        *,
        remediation_notes: str = "",
        start_from: str = "",
    ) -> dict[str, Any] | None:
        """通过 API 创建 Factory 运行"""
        try:
            url = f"{self.backend_url}/v2/factory/runs"

            # 构建 directive
            directive = self._build_directive(
                project,
                remediation_notes=remediation_notes,
            )

            payload = {
                "workspace": str(self.workspace),
                "directive": directive,
                "start_from": self._normalize_entry_stage(start_from)
                if str(start_from or "").strip()
                else ("architect" if self.run_architect_stage else "pm"),
                "run_director": True,
                "run_chief_engineer": self.run_chief_engineer_stage,
                "director_iterations": 1,
                "loop": False,
            }

            response = await self._request_with_retry(
                "POST",
                url,
                json_body=payload,
                timeout=self.request_timeout,
            )

            if response.status_code != 200:
                print(f"[factory] 创建运行失败: HTTP {response.status_code}")
                print(f"[factory] 响应: {response.text[:500]}")
                return None

            return response.json()

        except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
            print(f"[factory] 创建运行异常: {e}")
            return None

    def _build_directive(self, project: ProjectDefinition, remediation_notes: str = "") -> str:
        """构建 Factory 运行的 directive"""
        enhancements_desc = "\n".join([f"- {e.value}" for e in project.enhancements])
        focus_desc = "\n".join([f"- {item}" for item in project.stress_focus])
        domain_keywords = self._build_project_domain_keywords(project)
        domain_keyword_hint = ", ".join(domain_keywords[:8]) if domain_keywords else project.id
        ascii_domain_keywords = [token for token in domain_keywords if re.fullmatch(r"[a-z0-9_-]+", token)]
        path_keyword_hint = (
            ", ".join(ascii_domain_keywords[:3]) if ascii_domain_keywords else project.id.replace("-", "_")
        )

        tech_requirements = [
            f"- 复杂度等级: {project.complexity_level}/5",
        ]
        if project.requires_backend:
            tech_requirements.append("- 需要后端 API 支持")
        if project.requires_websocket:
            tech_requirements.append("- 需要 WebSocket / SSE 实时通信")
        if project.requires_encryption:
            tech_requirements.append("- 需要加密/安全处理")

        delivery_baseline = "\n".join(
            [
                (
                    f"- 至少 {self.min_new_code_files} 个代码文件，总代码行数不少于 "
                    f"{self.min_new_code_lines} 行（含测试/脚本/配置）。"
                ),
                "- 至少包含: 一个核心模块目录、一个测试目录、一个配置文件、一个可运行入口或脚本。",
                "- 必须包含单元测试；若涉及后端/接口，请补充集成测试。",
                "- 输出必须是可运行代码，不得只输出计划/说明。",
                "- 若当前工作区已有内容，请在其基础上新增模块/测试以满足基线。",
                "- 严禁占位实现（TODO/FIXME/NotImplemented/stub/空壳 main+helpers 模板）；若出现视为失败。",
                f"- 代码命名与核心逻辑必须体现项目领域关键词（示例: {domain_keyword_hint}）。",
                f"- 至少一个核心代码文件路径或模块名必须包含项目关键词（示例: {path_keyword_hint}）。",
            ]
        )
        remediation = str(remediation_notes or "").strip()
        remediation_section = ""
        if remediation:
            remediation_section = (
                "## 上轮失败复盘（必须修复）\n"
                f"{remediation}\n\n"
                "你必须先逐条修复上述失败证据，再补充新功能，禁止重复提交相同模板代码。\n\n"
            )

        return f"""# {project.name}

## 需求描述
{project.description}

## 增强特性
{enhancements_desc}

## 压测重点
{focus_desc}

## 技术要求
{chr(10).join(tech_requirements)}

## 交付基线（硬性）
{delivery_baseline}

{remediation_section}## 验收标准
1. 核心功能完整可用
2. 增强特性全部落地
3. 代码与测试通过基础质量检查
4. 交付基线全部满足

请使用 Polaris 的标准流程完成此项目。
"""

    async def _poll_factory_run(
        self,
        run_id: str,
        result: RoundResult,
    ) -> str:
        """轮询 Factory 运行状态"""
        start_time = time.time()
        last_observed_success_at = start_time
        last_progress_signal_at = start_time
        last_progress_signature = ""
        last_runtime_activity_at = start_time
        last_runtime_activity_signature = ""
        last_stall_deferral_notice_at = 0.0
        last_phase = None
        snapshot_counter = 0
        rate_limit_cooldown = self.poll_interval

        print(f"[factory] 开始轮询运行状态 (timeout: {self.factory_timeout}s)")
        runtime_signature, _ = self._collect_runtime_activity_signature(run_id)
        if runtime_signature:
            last_runtime_activity_signature = runtime_signature
            last_runtime_activity_at = time.time()

        while time.time() - start_time < self.factory_timeout:
            sleep_seconds = self.poll_interval
            request_budget_seconds = max(self.request_timeout + 1.0, 2.0)
            try:
                url = f"{self.backend_url}/v2/factory/runs/{run_id}"
                response = await asyncio.wait_for(
                    self._request_with_retry(
                        "GET",
                        url,
                        timeout=self.request_timeout,
                        max_attempts=1,
                    ),
                    timeout=request_budget_seconds,
                )

                if response.status_code != 200:
                    if response.status_code == 429:
                        retry_after_raw = str(response.headers.get("Retry-After") or "").strip()
                        retry_after = 0.0
                        if retry_after_raw:
                            try:
                                retry_after = max(float(retry_after_raw), 0.0)
                            except (TypeError, ValueError):
                                retry_after = 0.0
                        rate_limit_cooldown = min(
                            max(rate_limit_cooldown * 1.8, self.poll_interval, retry_after),
                            30.0,
                        )
                        sleep_seconds = rate_limit_cooldown
                        print(f"[factory] 查询状态被限流(429), cooldown={sleep_seconds:.1f}s")
                    else:
                        print(f"[factory] 查询状态失败: HTTP {response.status_code}")
                else:
                    status = response.json()
                    last_observed_success_at = time.time()
                    rate_limit_cooldown = self.poll_interval
                    phase = status.get("phase")
                    lifecycle = status.get("status")
                    progress = status.get("progress", 0)
                    progress_signature = json.dumps(
                        {
                            "phase": phase,
                            "status": lifecycle,
                            "progress": progress,
                            "updated_at": status.get("updated_at"),
                            "stages_completed": status.get("stages_completed"),
                            "current_stage_started_at": (
                                status.get("metadata", {}).get("current_stage_started_at")
                                if isinstance(status.get("metadata"), dict)
                                else None
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if progress_signature != last_progress_signature:
                        last_progress_signature = progress_signature
                        last_progress_signal_at = time.time()
                    runtime_signature, runtime_last_mtime = self._collect_runtime_activity_signature(run_id)
                    if runtime_signature and runtime_signature != last_runtime_activity_signature:
                        last_runtime_activity_signature = runtime_signature
                        last_runtime_activity_at = time.time()
                    elif runtime_last_mtime > 0:
                        # 文件系统时间戳可用于补偿 signature 哈希偶发相同的场景。
                        last_runtime_activity_at = max(last_runtime_activity_at, runtime_last_mtime)

                    # 只在阶段变化时打印
                    if phase != last_phase:
                        print(f"[factory] Phase: {phase} | Status: {lifecycle} | Progress: {progress}%")
                        last_phase = phase

                    # 更新各阶段执行记录
                    self._update_stage_executions(status, result)

                    # 定期捕获可观测性快照
                    snapshot_counter += 1
                    if snapshot_counter % 6 == 0 and self.collector:  # 每 6 次轮询 (约 30s) 捕获一次
                        await self._capture_snapshot_with_budget("periodic")

                    # 检查是否完成
                    if lifecycle in ("completed", "failed", "cancelled"):
                        print(f"[factory] 运行结束: {lifecycle}")

                        # 最终快照
                        if self.collector:
                            await self._capture_snapshot_with_budget("final")

                        # 获取失败信息
                        if lifecycle == "failed":
                            failure = factory_failure_info(status)
                            result.failure_point = str(
                                failure.get("failure_point")
                                or failure.get("stage")
                                or failure.get("phase")
                                or "unknown"
                            )
                            result.failure_evidence = factory_failure_evidence(status)
                            result.root_cause = failure.get("detail", "Factory 运行失败")

                            # 生成诊断报告
                            if self.collector:
                                diagnostic = self.collector.analyze_failure(status)
                                result.diagnostic_report = diagnostic
                                result.root_cause = diagnostic.root_cause_analysis

                        return lifecycle

                    if (
                        lifecycle not in ("completed", "failed", "cancelled")
                        and time.time() - last_progress_signal_at >= self.control_plane_stall_timeout
                    ):
                        now_ts = time.time()
                        stagnant_seconds = int(now_ts - last_progress_signal_at)
                        runtime_inactive_seconds = int(now_ts - last_runtime_activity_at)
                        if runtime_inactive_seconds < int(self.control_plane_stall_timeout):
                            if now_ts - last_stall_deferral_notice_at >= max(self.poll_interval, 5.0):
                                print(
                                    "[factory] 阶段状态静止但运行时仍有活动，继续等待: "
                                    f"phase={phase}, status={lifecycle}, progress={progress}%, "
                                    f"status_stagnant={stagnant_seconds}s, runtime_inactive={runtime_inactive_seconds}s"
                                )
                                last_stall_deferral_notice_at = now_ts
                        else:
                            print(
                                "[factory] 非 LLM 控制面阻塞: "
                                f"阶段状态 {stagnant_seconds}s 无进展且运行时 {runtime_inactive_seconds}s 无活动 "
                                f"(phase={phase}, status={lifecycle}, progress={progress}%)"
                            )
                            cancel_reason = (
                                f"agent_stress_non_llm_blocked: phase={phase}, status={lifecycle}, progress={progress}"
                            )
                            await self._cancel_factory_run(run_id, reason=cancel_reason)
                            result.failure_point = "factory_stage_stalled"
                            result.root_cause = (
                                f"Factory phase '{phase}' remained unchanged for {stagnant_seconds}s and runtime "
                                f"activity was idle for {runtime_inactive_seconds}s (status={lifecycle}, "
                                f"progress={progress}%), exceeding non-LLM budget "
                                f"{self.control_plane_stall_timeout:.0f}s"
                            )
                            result.failure_evidence = (
                                "No progress change in phase/status/progress/updated_at and no runtime "
                                f"activity within {self.control_plane_stall_timeout:.0f}s"
                            )
                            return "blocked"

            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:
                print(f"[factory] 轮询异常: {e}")

            if time.time() - last_observed_success_at >= self.control_plane_stall_timeout:
                print(f"[factory] 非 LLM 控制面阻塞: {self.control_plane_stall_timeout:.0f}s 内未获得有效状态响应")
                await self._cancel_factory_run(
                    run_id,
                    reason=(
                        "agent_stress_status_observation_timeout:"
                        f" no successful status response for {self.control_plane_stall_timeout:.0f}s"
                    ),
                )
                result.failure_point = "factory_status_observation_blocked"
                result.root_cause = (
                    "Factory status observation exceeded the non-LLM control-plane "
                    f"budget of {self.control_plane_stall_timeout:.0f}s"
                )
                result.failure_evidence = (
                    f"No successful GET /v2/factory/runs/{{id}} response within {self.control_plane_stall_timeout:.0f}s"
                )
                return "blocked"

            await asyncio.sleep(sleep_seconds)

        # 超时
        print(f"[factory] 运行超时 ({self.factory_timeout}s)")
        result.failure_point = "factory_timeout"
        result.root_cause = f"Factory 运行超时 ({self.factory_timeout}s)"
        return "timeout"

    async def _cancel_factory_run(self, run_id: str, *, reason: str) -> bool:
        """在阻塞时取消 Factory run，避免僵尸任务影响后续轮次。"""
        run_token = str(run_id or "").strip()
        if not run_token:
            return False
        try:
            url = f"{self.backend_url}/v2/factory/runs/{run_token}/control"
            response = await self._request_with_retry(
                "POST",
                url,
                json_body={
                    "action": "cancel",
                    "reason": str(reason or "").strip()[:240],
                },
                timeout=self.request_timeout,
                max_attempts=1,
            )
            if response.status_code == 200:
                print(f"[factory] 已取消阻塞运行: {run_token}")
                return True
            print(
                f"[factory] 取消阻塞运行失败: run={run_token}, http={response.status_code}, body={response.text[:240]}"
            )
            return False
        except (httpx.HTTPError, json.JSONDecodeError, OSError) as exc:
            print(f"[factory] 取消阻塞运行异常: run={run_token}, error={exc}")
            return False

    def _collect_runtime_activity_signature(self, run_id: str) -> tuple[str, float]:
        """收集运行时活动签名，用于区分“真阻塞”和“仍有执行活动”。

        返回:
            (signature, latest_mtime_epoch)
        """
        run_token = str(run_id or "").strip()
        if not run_token:
            return "", 0.0

        hp_root = Path(self.workspace) / ".polaris"
        if not hp_root.exists():
            return "", 0.0

        candidates: list[Path] = [
            hp_root / "factory" / run_token / "run.json",
            hp_root / "factory" / run_token / "events" / "events.jsonl",
        ]

        role_logs = hp_root / "runtime" / "roles"
        if role_logs.exists():
            with contextlib.suppress(OSError):
                candidates.extend(role_logs.glob("*/logs/*.jsonl"))

        runtime_events = hp_root / "runtime" / "events"
        if runtime_events.exists():
            with contextlib.suppress(OSError):
                candidates.extend(runtime_events.glob("*.jsonl"))

        signature_rows: list[list[Any]] = []
        latest_mtime = 0.0
        for path in candidates:
            try:
                if not path.exists() or not path.is_file():
                    continue
                stat = path.stat()
            except (OSError, PermissionError):
                continue
            latest_mtime = max(latest_mtime, float(stat.st_mtime))
            try:
                rel = path.relative_to(hp_root).as_posix()
            except ValueError:
                rel = str(path)
            signature_rows.append([rel, int(stat.st_mtime_ns), int(stat.st_size)])

        if not signature_rows:
            return "", latest_mtime

        signature_rows.sort(key=lambda row: str(row[0]))
        return json.dumps(signature_rows, ensure_ascii=False, separators=(",", ":")), latest_mtime

    def _update_stage_executions(self, status: dict[str, Any], result: RoundResult):
        """从 Factory 状态更新各阶段执行记录"""
        lifecycle = normalize_status(status.get("status"))
        roles = status.get("roles", {}) if isinstance(status.get("roles"), dict) else {}
        gates = status.get("gates", []) if isinstance(status.get("gates"), list) else []
        created_at = str(status.get("created_at") or result.start_time).strip() or result.start_time
        completed_at = str(status.get("completed_at") or "").strip()
        observed_at = completed_at or datetime.now().isoformat()
        current_index = resolve_factory_stage_index(status)

        def calc_duration(start: str, end: str) -> int:
            try:
                start_dt = self._parse_iso_timestamp(start)
                end_dt = self._parse_iso_timestamp(end)
                if not start_dt or not end_dt:
                    return 0
                return int((end_dt - start_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                return 0

        def ensure_stage(
            attr_name: str,
            stage_name: str,
            default_start: str,
        ) -> StageExecution:
            stage = getattr(result, attr_name)
            if stage is None:
                stage = StageExecution(
                    stage_name=stage_name,
                    result=StageResult.PENDING,
                    start_time=default_start,
                    end_time=observed_at,
                    duration_ms=0,
                )
                setattr(result, attr_name, stage)
            return stage

        def stage_outcome(stage_index: int) -> StageResult:
            if stage_index < 0:
                return StageResult.PENDING
            if current_index is None:
                if lifecycle == "completed":
                    return StageResult.SUCCESS if stage_index == 0 else StageResult.PENDING
                if lifecycle in {"failed", "cancelled"}:
                    return StageResult.FAILURE if stage_index == 0 else StageResult.PENDING
                return StageResult.PENDING
            if lifecycle == "completed":
                if stage_index == 3 and any(normalize_status(g.get("status")) == "failed" for g in gates):
                    return StageResult.PARTIAL
                return StageResult.SUCCESS if stage_index <= current_index else StageResult.PENDING
            if lifecycle in {"failed", "cancelled"}:
                if stage_index < current_index:
                    return StageResult.SUCCESS
                if stage_index == current_index:
                    if stage_index == 3 and any(normalize_status(g.get("status")) == "failed" for g in gates):
                        return StageResult.FAILURE
                    return StageResult.FAILURE
                return StageResult.PENDING
            if stage_index < current_index:
                return StageResult.SUCCESS
            return StageResult.PENDING

        entry_stage = self._resolve_round_entry_stage(result)
        stage_specs = [
            ("architect_stage", "architect", 0, created_at, entry_stage == "architect"),
            ("pm_stage", "pm", 1, observed_at, entry_stage in {"architect", "pm"}),
            (
                "chief_engineer_stage",
                "chief_engineer",
                -1,
                observed_at,
                self.run_chief_engineer_stage and entry_stage in {"architect", "pm"},
            ),
            ("director_stage", "director", 2, observed_at, entry_stage in {"architect", "pm", "director"}),
            ("qa_stage", "qa", 3, observed_at, entry_stage in {"architect", "pm", "director"}),
        ]

        role_status_by_stage = {
            "architect": normalize_status((roles.get("architect") or {}).get("status")),
            "pm": normalize_status((roles.get("pm") or {}).get("status")),
            "chief_engineer": normalize_status((roles.get("chief_engineer") or {}).get("status")),
            "director": normalize_status((roles.get("director") or {}).get("status")),
        }
        for attr_name, stage_name, stage_index, default_start, stage_enabled in stage_specs:
            should_track = (current_index is not None and stage_index <= current_index) or getattr(
                result, attr_name
            ) is not None
            if not stage_enabled:
                should_track = bool(getattr(result, attr_name))

            role_status = role_status_by_stage.get(stage_name, "")
            if role_status:
                should_track = True
            if stage_name == "qa" and gates:
                should_track = True
            if lifecycle in {"completed", "failed", "cancelled"} and stage_name == "architect" and stage_enabled:
                should_track = True

            if not should_track:
                continue

            stage = ensure_stage(attr_name, stage_name, default_start)
            stage.end_time = observed_at
            outcome = stage_outcome(stage_index)

            if role_status in COMPLETED_ROLE_STATUSES:
                outcome = StageResult.SUCCESS
            elif role_status in FAILED_ROLE_STATUSES:
                outcome = StageResult.FAILURE
            elif stage_name == "qa" and gates and lifecycle == "completed":
                outcome = (
                    StageResult.PARTIAL
                    if any(normalize_status(g.get("status")) == "failed" for g in gates)
                    else StageResult.SUCCESS
                )

            stage.result = outcome
            stage.duration_ms = calc_duration(stage.start_time, stage.end_time)

    async def _backfill_stage_timings(self, result: RoundResult) -> None:
        if not self.require_full_chain_evidence:
            return
        if result.overall_result not in {"PASS", "PARTIAL"}:
            return
        run_id = str(result.factory_run_id or "").strip()
        if not run_id:
            return
        try:
            events = await self._fetch_factory_events(run_id)
        except (httpx.HTTPError, OSError, json.JSONDecodeError) as exc:
            print(f"[factory] 获取运行事件失败: {exc}")
            return
        timings = self._extract_stage_timings(events)
        if not timings:
            return
        self._apply_stage_timings(result, timings)

    async def _fetch_factory_events(self, run_id: str) -> list[dict[str, Any]]:
        url = f"{self.backend_url}/v2/factory/runs/{run_id}/events"
        response = await self._request_with_retry(
            "GET",
            url,
            timeout=self.request_timeout,
            params={"limit": 500},
        )
        if response.status_code != 200:
            print(f"[factory] 获取运行事件失败: HTTP {response.status_code}")
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def _extract_stage_timings(self, events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        stage_map = {
            "docs_generation": "architect",
            "pm_planning": "pm",
            "director_dispatch": "director",
            "quality_gate": "qa",
        }
        timings: dict[str, dict[str, datetime]] = {}

        def update(role: str, key: str, raw_ts: Any, pick_earliest: bool) -> None:
            ts = str(raw_ts or "").strip()
            if not ts:
                return
            dt = self._parse_iso_timestamp(ts)
            if not dt:
                return
            existing = timings.get(role, {}).get(key)
            if existing is None:
                timings.setdefault(role, {})[key] = dt
                return
            if pick_earliest and dt < existing:
                timings[role][key] = dt
            if not pick_earliest and dt > existing:
                timings[role][key] = dt

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip()
            stage_raw = str(event.get("stage") or "").strip()
            result_payload = event.get("result") if isinstance(event.get("result"), dict) else {}
            if not stage_raw:
                stage_raw = str(result_payload.get("stage") or "").strip()
            role = stage_map.get(stage_raw)
            if not role:
                continue
            if event_type == "stage_started":
                update(role, "start", event.get("timestamp"), True)
            elif event_type == "stage_completed":
                update(role, "start", result_payload.get("started_at") or event.get("timestamp"), True)
                update(role, "end", result_payload.get("completed_at") or event.get("timestamp"), False)

        finalized: dict[str, dict[str, str]] = {}
        for role, times in timings.items():
            start_dt = times.get("start")
            end_dt = times.get("end")
            if not start_dt and not end_dt:
                continue
            finalized[role] = {
                "start": start_dt.isoformat() if start_dt else "",
                "end": end_dt.isoformat() if end_dt else "",
            }
        return finalized

    def _apply_stage_timings(self, result: RoundResult, timings: dict[str, dict[str, str]]) -> None:
        stage_attr_map = {
            "architect": "architect_stage",
            "pm": "pm_stage",
            "chief_engineer": "chief_engineer_stage",
            "director": "director_stage",
            "qa": "qa_stage",
        }

        def calc_duration(start: str, end: str) -> int:
            try:
                start_dt = self._parse_iso_timestamp(start)
                end_dt = self._parse_iso_timestamp(end)
                if not start_dt or not end_dt:
                    return 0
                return int((end_dt - start_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                return 0

        for role_name, timing in timings.items():
            attr_name = stage_attr_map.get(role_name)
            if not attr_name:
                continue
            stage = getattr(result, attr_name)
            if stage is None:
                stage = StageExecution(
                    stage_name=role_name,
                    result=StageResult.SUCCESS,
                    start_time=timing.get("start") or result.start_time,
                    end_time=timing.get("end") or result.end_time or result.start_time,
                    duration_ms=0,
                )
                setattr(result, attr_name, stage)
            if timing.get("start"):
                stage.start_time = timing["start"]
            if timing.get("end"):
                stage.end_time = timing["end"]
            stage.duration_ms = calc_duration(stage.start_time, stage.end_time)
            if stage.duration_ms <= 0 and stage.start_time and stage.end_time:
                stage.duration_ms = 1

    async def _finalize_round(self, result: RoundResult) -> RoundResult:
        """完成轮次"""
        result.end_time = datetime.now().isoformat()
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        path_fallback_delta = max(
            int(self.path_fallback_count) - int(self._current_round_path_fallback_before),
            0,
        )
        workspace_artifacts["path_fallback_count"] = int(path_fallback_delta)
        workspace_artifacts["path_contract_ok"] = bool(path_fallback_delta == 0)
        result.workspace_artifacts = workspace_artifacts

        # 停止追踪并获取数据
        trace: RoundTrace | None = None
        if self.tracer:
            try:
                trace = await asyncio.wait_for(
                    self.tracer.complete_round(result.overall_result),
                    timeout=self.trace_finalize_timeout + 1.0,
                )
            except asyncio.TimeoutError:
                print(
                    f"[tracer] Complete round timed out after "
                    f"{self.trace_finalize_timeout + 1.0:.1f}s; using partial trace"
                )
                trace = self.tracer.current_round
            except (OSError, RuntimeError, ValueError) as e:
                print(f"[tracer] Complete round failed: {type(e).__name__}: {e}")
                trace = self.tracer.current_round
        result.trace = trace

        # 如果没有明确的失败点，从追踪数据分析
        if (not result.failure_point or is_generic_failure_point(result.failure_point)) and trace:
            failures = trace.get_failure_analysis()
            if failures:
                first_failure = failures[0]
                if not result.failure_point or is_generic_failure_point(result.failure_point):
                    result.failure_point = first_failure.get("type", "unknown")
                if not result.failure_evidence:
                    result.failure_evidence = str(first_failure)[:500]

        if result.diagnostic_report:
            if not result.failure_point or is_generic_failure_point(result.failure_point):
                result.failure_point = result.diagnostic_report.failure_point
            if not result.root_cause:
                result.root_cause = result.diagnostic_report.root_cause_analysis
            if not result.failure_evidence:
                result.failure_evidence = result.diagnostic_report.summary

        # 保存可观测性数据
        if self.collector:
            try:
                result.observability_data = self.collector.to_dict()
            except (TypeError, ValueError, AttributeError) as e:
                result.observability_data = {
                    "serialization_error": f"{type(e).__name__}: {e}",
                }
        self._normalize_optional_chain_stages(result)
        await self._backfill_stage_timings(result)
        await self._capture_chain_stage_evidence(result)
        self._enforce_chain_evidence_gate(result)

        print(f"\n[Result] Round #{result.round_number}: {result.overall_result}")
        if result.failure_point:
            print(f"  Failure Point: {result.failure_point}")
            print(f"  Root Cause: {result.root_cause[:200]}...")

        # 打印诊断报告摘要 (如果失败)
        if result.diagnostic_report and result.overall_result == "FAIL":
            print(f"  失败分类: {result.diagnostic_report.failure_category.value}")
            print("  建议修复:")
            for i, fix in enumerate(result.diagnostic_report.suggested_fixes[:3], 1):
                print(f"    {i}. {fix}")

        return result

    def _normalize_optional_chain_stages(self, result: RoundResult) -> None:
        now = result.end_time or datetime.now().isoformat()
        entry_stage = self._resolve_round_entry_stage(result)
        if result.architect_stage is None and entry_stage != "architect":
            result.architect_stage = StageExecution(
                stage_name="architect",
                result=StageResult.SKIPPED,
                start_time=result.start_time,
                end_time=now,
                duration_ms=0,
                error=f"architect stage skipped by retry policy (entry_stage={entry_stage})",
            )
        elif result.architect_stage is None and (not self.run_architect_stage or not self.require_architect_stage):
            reason = (
                "architect stage disabled by stress chain policy"
                if not self.run_architect_stage
                else "architect stage optional and no direct factory-stage evidence was observed"
            )
            result.architect_stage = StageExecution(
                stage_name="architect",
                result=StageResult.SKIPPED,
                start_time=result.start_time,
                end_time=now,
                duration_ms=0,
                error=reason,
            )
        if result.pm_stage is None and entry_stage == "director":
            result.pm_stage = StageExecution(
                stage_name="pm",
                result=StageResult.SKIPPED,
                start_time=result.start_time,
                end_time=now,
                duration_ms=0,
                error="pm stage skipped by retry policy (entry_stage=director)",
            )
        if result.chief_engineer_stage is None:
            if self.run_chief_engineer_stage:
                result.chief_engineer_stage = StageExecution(
                    stage_name="chief_engineer",
                    result=StageResult.SKIPPED,
                    start_time=result.start_time,
                    end_time=now,
                    duration_ms=0,
                    error=(
                        "chief_engineer stage requested but no direct factory-stage "
                        "evidence was observed from public API"
                    ),
                )
            else:
                result.chief_engineer_stage = StageExecution(
                    stage_name="chief_engineer",
                    result=StageResult.SKIPPED,
                    start_time=result.start_time,
                    end_time=now,
                    duration_ms=0,
                    error="chief_engineer stage disabled by stress chain policy",
                )

    def _enforce_chain_evidence_gate(self, result: RoundResult) -> None:
        """强化链路证据门禁 - 校验阶段顺序、产物、耗时"""
        if not self.require_full_chain_evidence:
            return
        existing_failure = normalize_status(result.failure_point)
        if result.overall_result == "FAIL" and existing_failure and not is_generic_failure_point(existing_failure):
            return
        if str(result.failure_point or "").strip() in {
            "engine",
            "factory_timeout",
            "factory_status_observation_blocked",
        }:
            return

        # === B2: court_strict 模式硬化 ===
        # 1. 顺序校验：实际顺序必须匹配 chain_profile 定义的顺序
        # 2. 阶段产物校验：每个阶段必须有 artifact 产出
        # 3. 阶段耗时校验：单阶段超时触发告警

        entry_stage = self._resolve_round_entry_stage(result)
        expected_roles = self._expected_chain_roles(entry_stage=entry_stage)
        architect_required = "architect" in expected_roles
        pm_required = "pm" in expected_roles
        chief_required = "chief_engineer" in expected_roles
        director_required = "director" in expected_roles
        qa_required = "qa" in expected_roles

        # === B2 强化：court_strict 模式下缺少 architect 阶段直接 FAIL ===
        if self.chain_profile == "court_strict" and architect_required:
            if result.architect_stage is None:
                self._set_quality_failure(
                    result,
                    failure_point="chain_stage_sequence_invalid",
                    root_cause=("court_strict mode requires architect stage but no evidence was observed"),
                    failure_evidence=(
                        f"chain_profile={self.chain_profile}; architect_required=True; "
                        f"architect_stage=None; run_id={result.factory_run_id}"
                    ),
                )
                return
            if result.architect_stage.result != StageResult.SUCCESS:
                self._set_quality_failure(
                    result,
                    failure_point="chain_stage_sequence_invalid",
                    root_cause=("court_strict mode requires architect stage to complete successfully"),
                    failure_evidence=(
                        f"chain_profile={self.chain_profile}; architect_result={result.architect_stage.result.value}; "
                        f"run_id={result.factory_run_id}"
                    ),
                )
                return

        stage_requirements = [
            ("pm", result.pm_stage, {StageResult.SUCCESS}, pm_required),
            ("director", result.director_stage, {StageResult.SUCCESS}, director_required),
            ("qa", result.qa_stage, {StageResult.SUCCESS, StageResult.PARTIAL}, qa_required),
            (
                "architect",
                result.architect_stage,
                ({StageResult.SUCCESS} if architect_required else {StageResult.SUCCESS, StageResult.SKIPPED}),
                architect_required,
            ),
            (
                "chief_engineer",
                result.chief_engineer_stage,
                ({StageResult.SUCCESS} if chief_required else {StageResult.SUCCESS, StageResult.SKIPPED}),
                chief_required,
            ),
        ]
        missing_stage_evidence: list[str] = []
        for stage_name, stage, accepted_results, is_required in stage_requirements:
            if stage is None:
                if is_required:
                    missing_stage_evidence.append(f"{stage_name}=missing")
                continue
            if stage.result not in accepted_results:
                if is_required or stage.result != StageResult.SKIPPED:
                    missing_stage_evidence.append(f"{stage_name}={stage.result.value}")
                continue
            if stage.result == StageResult.SKIPPED:
                continue

            # === B2 强化：阶段耗时校验（单阶段超时告警）===
            # 单阶段超过 600s (10分钟) 触发告警
            stage_timeout_ms = 600000  # 10 minutes
            if stage.duration_ms > stage_timeout_ms:
                missing_stage_evidence.append(f"{stage_name}=timeout_exceeded({stage.duration_ms}ms)")

            if stage.duration_ms <= 0:
                missing_stage_evidence.append(f"{stage_name}=zero_duration")

        if missing_stage_evidence:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_evidence_missing",
                root_cause=(
                    "Factory lifecycle reported success but configured chain evidence is incomplete or inconsistent"
                ),
                failure_evidence="; ".join(missing_stage_evidence),
            )
            return

        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        chain_stage_evidence = (
            workspace_artifacts.get("chain_stage_evidence")
            if isinstance(workspace_artifacts.get("chain_stage_evidence"), dict)
            else {}
        )
        expected_order = (
            chain_stage_evidence.get("expected_role_order")
            if isinstance(chain_stage_evidence.get("expected_role_order"), list)
            else expected_roles
        )
        observed_order = (
            chain_stage_evidence.get("observed_role_order")
            if isinstance(chain_stage_evidence.get("observed_role_order"), list)
            else []
        )
        stages = chain_stage_evidence.get("stages") if isinstance(chain_stage_evidence.get("stages"), dict) else {}

        if not observed_order:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_sequence_invalid",
                root_cause=(
                    "Factory run lacks observable stage transition evidence, "
                    "cannot verify required chain order architect->pm->director->qa"
                ),
                failure_evidence=(
                    f"expected_order={expected_order}; observed_order=[]; run_id={result.factory_run_id}"
                ),
            )
            return

        if observed_order != expected_order:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_sequence_invalid",
                root_cause=("Observed execution stages do not match required main-chain order"),
                failure_evidence=(
                    f"expected_order={expected_order}; observed_order={observed_order}; run_id={result.factory_run_id}"
                ),
            )
            return

        path_fallback_count = int(workspace_artifacts.get("path_fallback_count") or 0)
        if path_fallback_count > 0:
            self._set_quality_failure(
                result,
                failure_point="path_contract_violation",
                root_cause=(
                    "Artifact resolution used fallback path candidates; "
                    "path contract requires logical path first-hit without fallback"
                ),
                failure_evidence=f"path_fallback_count={path_fallback_count}",
            )
            return

        # === B2 强化：阶段产物校验 ===
        artifact_issues: list[str] = []
        for role in expected_order:
            stage_payload = stages.get(role) if isinstance(stages.get(role), dict) else {}
            declared = (
                stage_payload.get("declared_artifacts")
                if isinstance(stage_payload.get("declared_artifacts"), list)
                else []
            )
            existing = (
                stage_payload.get("existing_artifacts")
                if isinstance(stage_payload.get("existing_artifacts"), list)
                else []
            )
            missing = (
                stage_payload.get("missing_artifacts")
                if isinstance(stage_payload.get("missing_artifacts"), list)
                else []
            )
            if not declared:
                artifact_issues.append(f"{role}=declared_artifacts_missing")
                continue
            if not existing:
                artifact_issues.append(f"{role}=existing_artifacts_missing")
            if missing:
                artifact_issues.append(f"{role}=missing_artifacts:{','.join(missing[:3])}")
        if artifact_issues:
            self._set_quality_failure(
                result,
                failure_point="chain_stage_artifacts_missing",
                root_cause=(
                    "Factory stage completion claimed artifacts, but required chain artifacts were not materialized"
                ),
                failure_evidence="; ".join(artifact_issues),
            )
            return

        if pm_required:
            pm_contract_issue = self._validate_pm_task_contract(result, chain_stage_evidence)
            if pm_contract_issue:
                self._set_quality_failure(
                    result,
                    failure_point="pm_contract_incomplete",
                    root_cause=(
                        "PM stage artifacts do not provide executable task contracts "
                        "with goal/scope/steps/acceptance fields"
                    ),
                    failure_evidence=pm_contract_issue,
                )
                return

        trace_stats = result.trace.to_dict().get("statistics", {}) if result.trace else {}
        total_tasks = int(trace_stats.get("total_tasks") or 0)
        new_code_file_count = int(workspace_artifacts.get("new_code_file_count") or 0)
        new_code_line_count = int(workspace_artifacts.get("new_code_line_count") or 0)
        if total_tasks <= 0:
            backfilled_trace = self._backfill_trace_from_dispatch_artifact(result, chain_stage_evidence)
            if backfilled_trace:
                trace_stats = result.trace.to_dict().get("statistics", {}) if result.trace else {}
                total_tasks = int(trace_stats.get("total_tasks") or 0)
        if total_tasks <= 0:
            self._set_quality_failure(
                result,
                failure_point="chain_trace_missing_tasks",
                root_cause=("Round has no traced task lineage; cannot prove PM->Director task handoff"),
                failure_evidence=(
                    f"trace.statistics.total_tasks={total_tasks}; "
                    f"workspace.new_code_file_count={new_code_file_count}; "
                    f"workspace.new_code_line_count={new_code_line_count}"
                ),
            )
            return

        obs_stats: dict[str, Any] = {}
        if isinstance(result.observability_data, dict):
            obs_raw = result.observability_data.get("statistics")
            obs_stats = obs_raw if isinstance(obs_raw, dict) else {}
        total_tool_executions = int(obs_stats.get("total_tool_executions") or 0)
        if total_tool_executions <= 0:
            backfilled_tools = self._backfill_observability_from_director_logs(result)
            if backfilled_tools > 0 and isinstance(result.observability_data, dict):
                refreshed = result.observability_data.get("statistics")
                obs_stats = refreshed if isinstance(refreshed, dict) else {}
                total_tool_executions = int(obs_stats.get("total_tool_executions") or backfilled_tools)
        if total_tool_executions <= 0:
            self._set_quality_failure(
                result,
                failure_point="chain_observability_missing_tools",
                root_cause=(
                    "Round has no observable Director tool execution evidence; chain success cannot be trusted"
                ),
                failure_evidence=(
                    f"observability.statistics.total_tool_executions={total_tool_executions}; "
                    f"workspace.new_code_file_count={new_code_file_count}; "
                    f"workspace.new_code_line_count={new_code_line_count}"
                ),
            )

    async def _capture_snapshot_with_budget(self, label: str) -> None:
        """在预算内捕获可观测性快照，避免拖死整轮压测。"""
        if not self.collector:
            return
        timeout_budget = self.observability_snapshot_timeout + 1.0
        try:
            await asyncio.wait_for(
                self.collector.capture_full_snapshot(),
                timeout=timeout_budget,
            )
        except asyncio.TimeoutError:
            print(
                f"[observability] {label} snapshot timed out after {timeout_budget:.1f}s; continuing with partial data"
            )
        except (OSError, RuntimeError, ValueError) as e:
            print(f"[observability] {label} snapshot failed: {type(e).__name__}: {e}")

    def generate_project_report(self, result: RoundResult) -> str:
        """生成项目级报告"""
        lines = [
            f"# 压测报告 - Round #{result.round_number}: {result.project.name}",
            "",
            f"- **项目**: {result.project.name}",
            f"- **类别**: {result.project.category.value}",
            f"- **结果**: {result.overall_result}",
            f"- **Factory Run**: `{result.factory_run_id}`",
            f"- **耗时**: {self._format_duration(result.start_time, result.end_time)}",
            "",
            "## 阶段执行",
            "",
        ]

        stages = [
            ("架构设计", result.architect_stage),
            ("任务规划", result.pm_stage),
            ("技术分析", result.chief_engineer_stage),
            ("代码执行", result.director_stage),
            ("质量审查", result.qa_stage),
        ]

        for name, stage in stages:
            if stage:
                icon = self._result_icon(stage.result)
                lines.append(f"- {icon} **{name}**: {stage.result.value} ({stage.duration_ms}ms)")
                if stage.error:
                    lines.append(f"  - 错误: {stage.error[:100]}")

        lines.extend(
            [
                "",
                "## 追踪统计",
                "",
            ]
        )

        if result.trace:
            stats = result.trace.to_dict().get("statistics", {})
            lines.extend(
                [
                    f"- 总任务数: {stats.get('total_tasks', 0)}",
                    f"- 完成任务: {stats.get('completed_tasks', 0)}",
                    f"- 失败任务: {stats.get('failed_tasks', 0)}",
                    f"- Factory Runs: {stats.get('total_factory_runs', 0)}",
                ]
            )

        if result.failure_point:
            lines.extend(
                [
                    "",
                    "## 失败分析",
                    "",
                    f"- **失效环节**: {result.failure_point}",
                    f"- **根因**: {result.root_cause}",
                    "",
                    "### 证据",
                    "",
                    "```",
                    result.failure_evidence[:1000],
                    "```",
                ]
            )

        return "\n".join(lines)

    def _result_icon(self, result: StageResult) -> str:
        return {
            StageResult.SUCCESS: "✅",
            StageResult.PARTIAL: "⚠️",
            StageResult.FAILURE: "❌",
            StageResult.TIMEOUT: "⏱️",
            StageResult.SKIPPED: "⏭️",
        }.get(result, "❓")

    def _format_duration(self, start: str, end: str | None) -> str:
        if not end:
            return "unknown"
        try:
            start_dt = self._parse_iso_timestamp(start)
            end_dt = self._parse_iso_timestamp(end)
            if not start_dt or not end_dt:
                return "unknown"
            delta = end_dt - start_dt
            return f"{delta.total_seconds():.1f}s"
        except (ValueError, TypeError):
            return "unknown"

    @staticmethod
    def _parse_iso_timestamp(raw: str | None) -> datetime | None:
        token = str(raw or "").strip()
        if not token:
            return None
        if token.endswith("Z"):
            token = token[:-1] + "+00:00"
        parsed = datetime.fromisoformat(token)
        if parsed.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo
            return parsed.replace(tzinfo=local_tz or timezone.utc).astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _is_python_docstring_stmt(node: ast.stmt) -> bool:
        if not isinstance(node, ast.Expr):
            return False
        value = node.value
        if isinstance(value, ast.Constant):
            return isinstance(value.value, str)
        legacy_str_node = getattr(ast, "Str", None)
        return bool(legacy_str_node) and isinstance(value, legacy_str_node)

    @staticmethod
    def _ast_expr_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return str(node.id or "")
        if isinstance(node, ast.Attribute):
            parent = StressEngine._ast_expr_name(node.value)
            return f"{parent}.{node.attr}" if parent else str(node.attr or "")
        if isinstance(node, ast.Subscript):
            return StressEngine._ast_expr_name(node.value)
        if isinstance(node, ast.Call):
            return StressEngine._ast_expr_name(node.func)
        return ""

    @classmethod
    def _is_protocol_or_abstract_function(
        cls,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_map: dict[ast.AST, ast.AST],
    ) -> bool:
        for decorator in node.decorator_list:
            decorator_name = cls._ast_expr_name(decorator)
            if decorator_name.endswith("abstractmethod"):
                return True

        parent = parent_map.get(node)
        if isinstance(parent, ast.ClassDef):
            for base in parent.bases:
                base_name = cls._ast_expr_name(base)
                if base_name.endswith("Protocol") or base_name.endswith("ABC"):
                    return True
        return False

    @classmethod
    def _extract_empty_python_functions(cls, content: str) -> list[str]:
        """Extract Python function names whose bodies are effectively empty."""
        try:
            module = ast.parse(content)
        except SyntaxError:
            return []

        parent_map: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(module):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent

        matches: list[str] = []
        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if cls._is_protocol_or_abstract_function(node, parent_map):
                continue
            body = list(node.body or [])
            if body and cls._is_python_docstring_stmt(body[0]):
                body = body[1:]
            if not body:
                matches.append(f"{node.name}(docstring_only)")
                continue
            if len(body) != 1:
                continue
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                matches.append(f"{node.name}(pass)")
                continue
            if isinstance(stmt, ast.Expr):
                value = stmt.value
                legacy_ellipsis_node = getattr(ast, "Ellipsis", None)
                is_legacy_ellipsis = bool(legacy_ellipsis_node) and isinstance(value, legacy_ellipsis_node)
                if (isinstance(value, ast.Constant) and value.value is Ellipsis) or is_legacy_ellipsis:
                    matches.append(f"{node.name}(ellipsis)")

        # 去重并保持顺序，避免重复命中同一个函数。
        return list(dict.fromkeys(matches))

    @classmethod
    def _extract_empty_function_matches(cls, content: str, suffix: str) -> list[str]:
        normalized_suffix = str(suffix or "").strip().lower()
        if normalized_suffix == ".py":
            python_matches = cls._extract_empty_python_functions(content)
            if python_matches:
                return python_matches
            return [
                (match.group("name") or "").strip()
                for match in PYTHON_EMPTY_FUNCTION_FALLBACK_PATTERN.finditer(content)
                if (match.group("name") or "").strip()
            ]
        if normalized_suffix in {".js", ".jsx", ".ts", ".tsx"}:
            return [match.group(0).strip() for match in JS_TS_EMPTY_FUNCTION_PATTERN.finditer(content)]
        return []

    def _post_batch_code_audit(self, projects: list, sample_size: int = 3, seed: int | None = None) -> dict:
        """批后随机抽查审计

        固定随机种子（可复现）
        随机抽取 N 个项目，每个项目随机抽取 M 个代码文件
        检查：模板占位、重复代码、TODO/FIXME、未完成函数

        Args:
            projects: 项目列表 (RoundResult 列表)
            sample_size: 随机抽查的项目数量
            seed: 随机种子，用于可复现审计

        Returns:
            dict: 审计结果
            {
                "sample_audits": [...],
                "failed_rules_hit": [...],
                "evidence_paths": [...],
            }
        """
        import random

        rng = random.Random(seed)

        # 随机抽取样本
        sampled_projects = rng.sample(projects, min(sample_size, len(projects)))

        sample_audits = []
        failed_rules_hit = []
        evidence_paths = []

        # 定义审计规则
        audit_rules = {
            "todo_fixme": {
                "pattern": re.compile(r"\b(TODO|FIXME|TBD)\b", re.IGNORECASE),
                "severity": "high",
            },
            "not_implemented": {
                "pattern": re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE),
                "severity": "high",
            },
            "stub_placeholder": {
                "pattern": re.compile(r"\b(stub|placeholder|实现核心业务逻辑|核心逻辑待实现)\b", re.IGNORECASE),
                "severity": "medium",
            },
            "empty_function": {
                "severity": "medium",
            },
            "generic_scaffold": {
                "patterns": [
                    "项目主入口模块",
                    "通用工具函数模块",
                    "helpers 模块的单元测试",
                    "def safe_divide(",
                    "def parse_arguments(",
                    "应用程序主入口点",
                ],
                "severity": "high",
            },
        }

        for project_result in sampled_projects:
            project_workspace = (
                project_result.workspace_artifacts.get("workspace")
                if isinstance(project_result.workspace_artifacts, dict)
                else None
            )

            if not project_workspace:
                continue

            workspace_path = Path(project_workspace)
            if not workspace_path.exists():
                continue

            # 收集代码文件
            code_files = []
            try:
                for path in workspace_path.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(workspace_path)
                    if rel.parts and rel.parts[0] in IGNORED_WORKSPACE_ROOTS:
                        continue
                    if path.suffix.lower() in PROJECT_CODE_EXTENSIONS:
                        code_files.append(path)
            except (OSError, PermissionError):
                continue

            if not code_files:
                continue

            # 随机抽取 M 个代码文件
            max_files_per_project = min(5, len(code_files))
            sampled_files = rng.sample(code_files, max_files_per_project)

            project_audit = {
                "project_id": project_result.project.id,
                "project_name": project_result.project.name,
                "files_audited": len(sampled_files),
                "violations": [],
            }

            for code_file in sampled_files:
                try:
                    content = code_file.read_text(encoding="utf-8")
                except (OSError, PermissionError, UnicodeDecodeError):
                    continue

                rel_path = code_file.relative_to(workspace_path)
                evidence_paths.append(str(code_file))

                # 检查各规则
                for rule_name, rule_def in audit_rules.items():
                    violations_found = []

                    if rule_name == "empty_function":
                        empty_matches = self._extract_empty_function_matches(content, code_file.suffix)
                        if empty_matches:
                            violations_found.append(
                                {
                                    "rule": rule_name,
                                    "matches": empty_matches[:5],
                                    "severity": rule_def["severity"],
                                }
                            )

                    if "pattern" in rule_def:
                        matches = rule_def["pattern"].findall(content)
                        if matches:
                            violations_found.append(
                                {
                                    "rule": rule_name,
                                    "matches": matches[:5],  # 限制匹配数量
                                    "severity": rule_def["severity"],
                                }
                            )

                    if rule_name == "generic_scaffold":
                        for marker in rule_def["patterns"]:
                            if marker.lower() in content.lower():
                                violations_found.append(
                                    {
                                        "rule": rule_name,
                                        "marker": marker,
                                        "severity": rule_def["severity"],
                                    }
                                )

                    if violations_found:
                        project_audit["violations"].append(
                            {
                                "file": str(rel_path),
                                "violations": violations_found,
                            }
                        )

                        # 记录失败的规则
                        for v in violations_found:
                            rule_id = f"{project_result.project.id}:{rel_path}:{v['rule']}"
                            if rule_id not in [r.get("rule_id") for r in failed_rules_hit]:
                                failed_rules_hit.append(
                                    {
                                        "rule_id": rule_id,
                                        "project_id": project_result.project.id,
                                        "file": str(rel_path),
                                        "rule": v["rule"],
                                        "severity": v["severity"],
                                    }
                                )

            sample_audits.append(project_audit)

        return {
            "sample_audits": sample_audits,
            "failed_rules_hit": failed_rules_hit,
            "evidence_paths": evidence_paths,
            "audit_metadata": {
                "sample_size": len(sampled_projects),
                "total_projects_audited": len(sample_audits),
                "seed": seed,
            },
        }
