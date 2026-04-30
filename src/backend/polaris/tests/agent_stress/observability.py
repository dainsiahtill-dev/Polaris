"""可观测性增强模块 - 为 AI Agent 提供丰富的诊断信息

捕获 Polaris 运行时的详细状态，帮助 AI Agent 诊断问题。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Self

import httpx

from .contracts import (
    director_task_id,
    director_task_pm_task_id,
    director_task_workflow_run_id,
    event_identity,
    event_kind,
    event_payload,
    event_timestamp,
    factory_failure_evidence,
    factory_failure_info,
    llm_event_identity,
    llm_event_success,
    normalize_status,
)

MAX_LLM_CALL_RECORDS = 2000
MAX_STAGE_TRANSITIONS = 1000
MAX_TOOL_EXECUTIONS = 2000
MAX_ERROR_EVENTS = 1000
MAX_RAW_SNAPSHOTS = 120
MAX_COLLECTION_WARNINGS = 500


class FailureCategory(Enum):
    """失败分类 - 便于 AI Agent 快速定位问题类型"""

    LLM_UNAVAILABLE = "llm_unavailable"  # LLM 服务不可用
    LLM_TIMEOUT = "llm_timeout"  # LLM 调用超时
    LLM_FORMAT_ERROR = "llm_format_error"  # LLM 输出格式错误
    PROMPT_LEAKAGE = "prompt_leakage"  # 提示词泄漏
    TOOL_EXECUTION_FAILED = "tool_execution_failed"  # 工具执行失败
    TASK_DESERIALIZATION_FAILED = "task_deserialization_failed"  # 任务反序列化失败
    WORKFLOW_EXECUTION_ERROR = "workflow_execution_error"  # 工作流执行错误
    RUNTIME_CRASH = "runtime_crash"  # 运行时崩溃
    RESOURCE_EXHAUSTED = "resource_exhausted"  # 资源耗尽
    CONFIGURATION_ERROR = "configuration_error"  # 配置错误
    UNKNOWN = "unknown"  # 未知错误


@dataclass
class LLMCallRecord:
    """LLM 调用记录"""

    call_id: str
    role: str
    timestamp: str
    request_prompt: str = ""
    response_text: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    success: bool = True
    error_message: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StageTransition:
    """阶段转换记录"""

    from_phase: str
    to_phase: str
    timestamp: str
    duration_ms: int = 0
    success: bool = True
    error_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecution:
    """工具执行记录"""

    tool_name: str
    timestamp: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    error_message: str = ""
    duration_ms: int = 0


@dataclass
class DiagnosticReport:
    """诊断报告 - AI Agent 可据此分析问题"""

    round_number: int
    factory_run_id: str
    failure_category: FailureCategory
    failure_point: str
    timestamp: str
    summary: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    root_cause_analysis: str = ""
    suggested_fixes: list[str] = field(default_factory=list)
    related_logs: list[str] = field(default_factory=list)
    raw_api_responses: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiagnosticReport | None":
        if not isinstance(payload, dict) or not payload:
            return None
        raw_category = str(payload.get("failure_category") or "").strip()
        try:
            category = FailureCategory(raw_category)
        except ValueError:
            category = FailureCategory.UNKNOWN
        return cls(
            round_number=int(payload.get("round_number") or 0),
            factory_run_id=str(payload.get("factory_run_id") or "").strip(),
            failure_category=category,
            failure_point=str(payload.get("failure_point") or "").strip(),
            timestamp=str(payload.get("timestamp") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            evidence=list(payload.get("evidence") or []),
            root_cause_analysis=str(payload.get("root_cause_analysis") or "").strip(),
            suggested_fixes=[str(item).strip() for item in (payload.get("suggested_fixes") or []) if str(item).strip()],
            related_logs=[str(item).strip() for item in (payload.get("related_logs") or []) if str(item).strip()],
            raw_api_responses=dict(payload.get("raw_api_responses") or {}),
        )


class ObservabilityCollector:
    """可观测性数据收集器

    收集 Polaris 运行的详细数据，包括：
    - LLM 调用历史
    - 阶段转换
    - 工具执行
    - 错误事件
    - 原始 API 响应

    所有数据以结构化 JSON 保存，便于 AI Agent 解析。
    """

    def __init__(
        self,
        backend_url: str = "",
        token: str = "",
        request_timeout: float = 5.0,
        llm_events_timeout: float = 3.0,
        snapshot_timeout: float = 12.0,
        max_task_probes: int = 8,
        task_probe_concurrency: int = 4,
    ) -> None:
        self.backend_url = str(backend_url or "").strip().rstrip("/")
        self.token = str(token or "").strip()
        self.request_timeout = max(float(request_timeout or 0.0), 0.5)
        self.llm_events_timeout = max(float(llm_events_timeout or 0.0), 0.5)
        self.snapshot_timeout = max(float(snapshot_timeout or 0.0), 1.0)
        self.max_task_probes = max(int(max_task_probes or 0), 1)
        self.task_probe_concurrency = max(int(task_probe_concurrency or 0), 1)
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        timeout = httpx.Timeout(self.request_timeout, connect=min(self.request_timeout, 2.0))
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers)

        # 数据存储
        self.llm_calls: list[LLMCallRecord] = []
        self.stage_transitions: list[StageTransition] = []
        self.tool_executions: list[ToolExecution] = []
        self.error_events: list[dict[str, Any]] = []
        self.raw_snapshots: list[dict[str, Any]] = []
        self.collection_warnings: list[str] = []

        self._current_round: int | None = None
        self._factory_run_id: str | None = None
        self._seen_error_events: set[str] = set()
        self._seen_tool_events: set[str] = set()
        self._seen_stage_transitions: set[str] = set()
        self._seen_llm_events: set[str] = set()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.client.aclose()

    def start_collection(self, round_number: int, factory_run_id: str):
        """开始收集"""
        self._current_round = round_number
        self._factory_run_id = factory_run_id
        self.llm_calls.clear()
        self.stage_transitions.clear()
        self.tool_executions.clear()
        self.error_events.clear()
        self.raw_snapshots.clear()
        self.collection_warnings.clear()
        self._seen_error_events.clear()
        self._seen_tool_events.clear()
        self._seen_stage_transitions.clear()
        self._seen_llm_events.clear()

    @staticmethod
    def _append_with_limit(target: list[Any], item: Any, limit: int) -> None:
        target.append(item)
        if len(target) > limit:
            del target[: len(target) - limit]

    async def capture_full_snapshot(self) -> dict[str, Any]:
        """捕获完整状态快照"""
        if not self._factory_run_id:
            return {}

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "round": self._current_round,
            "factory_run_id": self._factory_run_id,
        }

        try:
            await asyncio.wait_for(
                self._capture_snapshot_inner(snapshot),
                timeout=self.snapshot_timeout,
            )
        except asyncio.TimeoutError:
            warning = f"snapshot timeout after {self.snapshot_timeout:.1f}s (factory_run_id={self._factory_run_id})"
            self._append_with_limit(self.collection_warnings, warning, MAX_COLLECTION_WARNINGS)
            snapshot["capture_timeout"] = warning
        except (RuntimeError, httpx.HTTPError) as e:
            # RuntimeError: async gather failure
            # httpx.HTTPError: network errors from httpx calls
            # We intentionally do NOT catch asyncio.CancelledError here.
            warning = f"snapshot capture error: {type(e).__name__}: {e}"
            self._append_with_limit(self.collection_warnings, warning, MAX_COLLECTION_WARNINGS)
            snapshot["capture_error"] = str(e)
        self._append_with_limit(self.raw_snapshots, snapshot, MAX_RAW_SNAPSHOTS)
        return snapshot

    async def _capture_snapshot_inner(self, snapshot: dict[str, Any]) -> None:
        run_path = f"/v2/factory/runs/{self._factory_run_id}"
        events_path = f"/v2/factory/runs/{self._factory_run_id}/events"
        tasks_path = "/v2/director/tasks"
        run_result, events_result, tasks_result = await asyncio.gather(
            self._safe_get_json(run_path, timeout=self.request_timeout),
            self._safe_get_json(events_path, timeout=self.request_timeout),
            self._safe_get_json(tasks_path, timeout=self.request_timeout),
        )

        self._merge_snapshot_payload(snapshot, "factory_run", run_result)
        self._merge_snapshot_payload(snapshot, "events", events_result, list_key="events")
        self._merge_snapshot_payload(snapshot, "director_tasks", tasks_result, list_key="tasks")

        events = snapshot.get("events", [])
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                if normalize_status(event.get("level")) in {"error", "critical"}:
                    event_key = event_identity(event)
                    if event_key not in self._seen_error_events:
                        self._seen_error_events.add(event_key)
                        self._append_with_limit(self.error_events, event, MAX_ERROR_EVENTS)

            self._extract_tool_executions(events)
            self._extract_stage_transitions(events)

        await self._capture_llm_events(snapshot)

    async def _safe_get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request_timeout = max(float(timeout or self.request_timeout), 0.5)
        url = f"{self.backend_url}{path}"
        try:
            response = await self.client.get(url, params=params or None, timeout=request_timeout)
            payload = response.json() if response.status_code == 200 else None
            return {
                "ok": response.status_code == 200,
                "status_code": response.status_code,
                "payload": payload,
            }
        except httpx.HTTPError as exc:
            # httpx.HTTPError covers all HTTP-level errors (connection,
            # timeout, protocol errors, etc.). We intentionally do NOT catch
            # CancelledError so it propagates to the caller.
            message = f"{path} -> {type(exc).__name__}: {exc}"
            self._append_with_limit(self.collection_warnings, message, MAX_COLLECTION_WARNINGS)
            return {
                "ok": False,
                "status_code": None,
                "payload": None,
                "error": message,
            }

    def _merge_snapshot_payload(
        self,
        snapshot: dict[str, Any],
        field_name: str,
        result: dict[str, Any],
        *,
        list_key: str = "",
    ) -> None:
        if result.get("ok"):
            payload = result.get("payload")
            if list_key:
                values = payload.get(list_key, []) if isinstance(payload, dict) else payload
                if isinstance(values, list):
                    snapshot[field_name] = values
            elif isinstance(payload, dict):
                snapshot[field_name] = payload
        elif result.get("error"):
            snapshot.setdefault("collection_errors", []).append(str(result["error"]))

    async def _capture_llm_events(self, snapshot: dict[str, Any]):
        """捕获 LLM 事件"""
        tasks = snapshot.get("director_tasks", [])
        if not isinstance(tasks, list):
            return

        lineage_tasks = [task for task in tasks if isinstance(task, dict) and director_task_pm_task_id(task)]
        tasks_to_probe = (lineage_tasks or tasks)[: self.max_task_probes]
        semaphore = asyncio.Semaphore(self.task_probe_concurrency)

        async def fetch_task_events(task: dict[str, Any]) -> list[dict[str, Any]]:
            task_id = director_task_id(task)
            if not task_id:
                return []

            params: dict[str, Any] = {}
            workflow_run_id = director_task_workflow_run_id(task)
            if workflow_run_id:
                params["run_id"] = workflow_run_id

            async with semaphore:
                result = await self._safe_get_json(
                    f"/v2/director/tasks/{task_id}/llm-events",
                    params=params or None,
                    timeout=self.llm_events_timeout,
                )
            payload = result.get("payload")
            if not result.get("ok"):
                snapshot.setdefault("llm_capture_errors", []).append(
                    result.get("error") or f"llm-events failed for {task_id}"
                )
                return []
            events = payload.get("events", []) if isinstance(payload, dict) else []
            return events if isinstance(events, list) else []

        try:
            groups = await asyncio.gather(
                *(fetch_task_events(task) for task in tasks_to_probe if isinstance(task, dict))
            )
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            # asyncio.gather raises ExceptionGroup (multiple failures) or Exception (single failure).
            # BaseException excludes SystemExit/KeyboardInterrupt while catching all relevant
            # task-result exceptions. CancelledError is handled separately above.
            snapshot["llm_capture_error"] = f"{type(e).__name__}: {e}"
            return

        all_llm_events: list[dict[str, Any]] = []
        for group in groups:
            all_llm_events.extend(group)

        snapshot["llm_events"] = all_llm_events
        if len(tasks_to_probe) < len(lineage_tasks or tasks):
            snapshot["llm_probe_truncated"] = {
                "probed_tasks": len(tasks_to_probe),
                "available_tasks": len(lineage_tasks or tasks),
            }

        for event in all_llm_events:
            if not isinstance(event, dict):
                continue
            event_key = llm_event_identity(event)
            if event_key in self._seen_llm_events:
                continue
            self._seen_llm_events.add(event_key)

            record = LLMCallRecord(
                call_id=event.get("call_id", ""),
                role=event.get("role", ""),
                timestamp=event_timestamp(event),
                request_prompt=str(event.get("prompt") or ""),
                response_text=str(event.get("response") or event.get("content") or ""),
                model=event.get("model", ""),
                provider=event.get("provider", ""),
                latency_ms=event.get("latency_ms", 0),
                success=llm_event_success(event),
                error_message=str(event.get("error") or ""),
                token_usage=event.get("token_usage", {}),
                tool_calls=event.get("tool_calls", []),
            )
            self._append_with_limit(self.llm_calls, record, MAX_LLM_CALL_RECORDS)
            self._extract_tool_executions_from_llm_event(event, event_key)

    def _extract_tool_executions_from_llm_event(self, event: dict[str, Any], event_key: str) -> None:
        event_type = normalize_status(event.get("event_type") or event.get("type"))
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        timestamp = event_timestamp(event)

        if event_type in {"tool_execute", "tool_result"}:
            tool_name = str(
                metadata.get("tool_name") or event.get("tool_name") or metadata.get("tool") or event.get("tool") or ""
            ).strip()
            if tool_name:
                tool_event_key = f"llm:{event_key}:{event_type}:{tool_name}"
                if tool_event_key not in self._seen_tool_events:
                    self._seen_tool_events.add(tool_event_key)
                    self._append_with_limit(
                        self.tool_executions,
                        ToolExecution(
                            tool_name=tool_name,
                            timestamp=timestamp,
                            arguments=metadata.get("args", {}) if isinstance(metadata.get("args"), dict) else {},
                            result=str(metadata.get("result") or ""),
                            success=bool(metadata.get("success", event_type != "tool_result")),
                            error_message=str(metadata.get("error") or event.get("error") or ""),
                            duration_ms=int(metadata.get("duration_ms") or event.get("duration_ms") or 0),
                        ),
                        MAX_TOOL_EXECUTIONS,
                    )

        tool_calls = event.get("tool_calls")
        if not isinstance(tool_calls, list):
            return

        for index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            tool_name = str(call.get("tool") or call.get("name") or call.get("tool_name") or "").strip()
            if not tool_name:
                continue
            tool_event_key = f"llm:{event_key}:tool_calls:{index}:{tool_name}"
            if tool_event_key in self._seen_tool_events:
                continue
            self._seen_tool_events.add(tool_event_key)
            self._append_with_limit(
                self.tool_executions,
                ToolExecution(
                    tool_name=tool_name,
                    timestamp=timestamp,
                    arguments=call.get("args", {}) if isinstance(call.get("args"), dict) else {},
                    result=str(call.get("result") or ""),
                    success=bool(call.get("success", True)),
                    error_message=str(call.get("error") or ""),
                    duration_ms=int(call.get("duration_ms") or 0),
                ),
                MAX_TOOL_EXECUTIONS,
            )

    def _extract_tool_executions(self, events: list[dict[str, Any]]):
        """从事件提取工具执行"""
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = event_kind(event)
            payload = event_payload(event)
            if "tool" in kind:
                event_key = event_identity(event)
                if event_key in self._seen_tool_events:
                    continue
                self._seen_tool_events.add(event_key)
                try:
                    duration_ms = int(payload.get("duration_ms") or event.get("duration_ms") or 0)
                except (ValueError, TypeError):
                    # ValueError: string to int conversion error
                    # TypeError: None or non-int passed to int()
                    duration_ms = 0
                exec_record = ToolExecution(
                    tool_name=str(payload.get("tool_name") or payload.get("tool") or event.get("tool_name") or kind),
                    timestamp=event_timestamp(event),
                    arguments=payload.get("arguments", payload.get("args", {}))
                    if isinstance(payload.get("arguments", payload.get("args", {})), dict)
                    else {},
                    result=str(payload.get("result") or event.get("result") or event.get("message") or "")[:1000],
                    success=kind != "tool_error"
                    and normalize_status(event.get("level")) not in {"error", "critical"}
                    and bool(payload.get("success", True)),
                    error_message=str(payload.get("error") or event.get("error") or ""),
                    duration_ms=duration_ms,
                )
                self._append_with_limit(self.tool_executions, exec_record, MAX_TOOL_EXECUTIONS)

    def _extract_stage_transitions(self, events: list[dict[str, Any]]):
        """从事件提取阶段转换"""
        last_phase = None
        last_event_id = None

        for event in events:
            if not isinstance(event, dict):
                continue

            payload = event_payload(event)
            phase = str(event.get("phase") or event.get("stage") or payload.get("stage") or "").strip()
            timestamp = event_timestamp(event)
            current_event_id = event_identity(event)

            if phase and phase != last_phase and last_phase:
                transition_key = f"{last_event_id or ''}->{current_event_id}:{last_phase}->{phase}"
                if transition_key in self._seen_stage_transitions:
                    last_phase = phase
                    last_event_id = current_event_id
                    continue
                self._seen_stage_transitions.add(transition_key)
                transition = StageTransition(
                    from_phase=last_phase,
                    to_phase=phase,
                    timestamp=timestamp,
                )
                self._append_with_limit(self.stage_transitions, transition, MAX_STAGE_TRANSITIONS)

            if phase:
                last_phase = phase
                last_event_id = current_event_id

    def analyze_failure(self, factory_status: dict[str, Any]) -> DiagnosticReport:
        """分析失败原因，生成诊断报告"""
        failure_info = factory_failure_info(factory_status)
        failure_point = str(failure_info.get("failure_point") or failure_info.get("phase") or "unknown")

        # 分类失败
        category = self._classify_failure(failure_info, failure_point)

        # 收集证据
        evidence = self._collect_evidence(factory_status, failure_info, failure_point)

        # 根因分析
        root_cause = self._analyze_root_cause(category, failure_info, failure_point)

        # 建议修复
        suggested_fixes = self._suggest_fixes(category, failure_info, failure_point)

        # 相关日志
        related_logs = self._find_related_logs(category, failure_point)

        return DiagnosticReport(
            round_number=self._current_round or 0,
            factory_run_id=self._factory_run_id or "",
            failure_category=category,
            failure_point=failure_point,
            timestamp=datetime.now().isoformat(),
            summary=(f"Factory run failed at '{failure_point}': {failure_info.get('detail') or 'Unknown error'}"),
            evidence=evidence,
            root_cause_analysis=root_cause,
            suggested_fixes=suggested_fixes,
            related_logs=related_logs,
            raw_api_responses={
                "factory_status": factory_status,
                "factory_failure": failure_info,
                "recent_errors": self.error_events[-5:] if self.error_events else [],
            },
        )

    def _classify_failure(
        self,
        failure: dict[str, Any],
        phase: str,
    ) -> FailureCategory:
        """分类失败类型"""
        message = normalize_status(failure.get("detail"))
        error_type = normalize_status(failure.get("code"))

        # LLM 相关
        if "llm" in message or "llm" in error_type:
            if "timeout" in message:
                return FailureCategory.LLM_TIMEOUT
            elif "unavailable" in message or "connection" in message:
                return FailureCategory.LLM_UNAVAILABLE
            elif "format" in message or "parse" in message or "json" in message:
                return FailureCategory.LLM_FORMAT_ERROR
            return FailureCategory.LLM_UNAVAILABLE

        # 提示词泄漏
        if "leak" in message or "prompt" in message:
            return FailureCategory.PROMPT_LEAKAGE

        # 工具执行
        if "tool" in message or "tool" in error_type:
            return FailureCategory.TOOL_EXECUTION_FAILED

        # 任务反序列化
        if "deserial" in message or "parse" in message:
            return FailureCategory.TASK_DESERIALIZATION_FAILED

        # 工作流
        if "workflow" in message or "phase" in error_type or "not found" in message:
            return FailureCategory.WORKFLOW_EXECUTION_ERROR

        # 运行时崩溃
        if "crash" in message or "panic" in message or "exception" in message:
            return FailureCategory.RUNTIME_CRASH

        # 资源
        if "memory" in message or "disk" in message or "resource" in message:
            return FailureCategory.RESOURCE_EXHAUSTED

        # 配置
        if "config" in message or "setting" in message:
            return FailureCategory.CONFIGURATION_ERROR

        return FailureCategory.UNKNOWN

    def _collect_evidence(
        self,
        factory_status: dict[str, Any],
        failure: dict[str, Any],
        phase: str,
    ) -> list[dict[str, Any]]:
        """收集失败证据"""
        evidence = []

        # 失败信息
        evidence.append(
            {
                "type": "failure_info",
                "phase": phase,
                "code": failure.get("code", ""),
                "message": failure.get("detail", ""),
                "recoverable": bool(failure.get("recoverable")),
            }
        )
        enriched_evidence = factory_failure_evidence(factory_status)
        if enriched_evidence:
            evidence.append(
                {
                    "type": "failure_evidence",
                    "summary": enriched_evidence,
                }
            )

        # 最近的 LLM 调用
        if self.llm_calls:
            recent_calls = [c for c in self.llm_calls if not c.success]
            if recent_calls:
                evidence.append(
                    {
                        "type": "failed_llm_calls",
                        "count": len(recent_calls),
                        "calls": [
                            {
                                "role": c.role,
                                "model": c.model,
                                "error": c.error_message,
                                "response_preview": c.response_text[:500] if c.response_text else "",
                            }
                            for c in recent_calls[-3:]
                        ],
                    }
                )

        # 最近的错误事件
        if self.error_events:
            evidence.append(
                {
                    "type": "error_events",
                    "count": len(self.error_events),
                    "events": self.error_events[-5:],
                }
            )

        # 工具执行失败
        failed_tools = [t for t in self.tool_executions if not t.success]
        if failed_tools:
            evidence.append(
                {
                    "type": "failed_tool_executions",
                    "count": len(failed_tools),
                    "tools": [
                        {
                            "name": t.tool_name,
                            "error": t.error_message,
                            "arguments": t.arguments,
                        }
                        for t in failed_tools[-3:]
                    ],
                }
            )

        return evidence

    def _analyze_root_cause(
        self,
        category: FailureCategory,
        failure: dict[str, Any],
        phase: str,
    ) -> str:
        """分析根因"""
        detail = normalize_status(failure.get("detail"))
        templates = {
            FailureCategory.LLM_UNAVAILABLE: f"Phase '{phase}' failed because the LLM service is unavailable. "
            "This could be due to: 1) LLM provider service down, 2) Invalid API key, "
            "3) Network connectivity issues.",
            FailureCategory.LLM_TIMEOUT: f"Phase '{phase}' timed out waiting for LLM response. "
            "The model may be overloaded or the prompt too complex.",
            FailureCategory.LLM_FORMAT_ERROR: f"Phase '{phase}' received malformed output from LLM. "
            "The model failed to produce valid JSON/tool calls.",
            FailureCategory.PROMPT_LEAKAGE: f"Phase '{phase}' may have leaked system prompts in its output.",
            FailureCategory.TOOL_EXECUTION_FAILED: f"Phase '{phase}' failed when executing a tool. "
            "The tool arguments may be invalid or the tool crashed.",
            FailureCategory.TASK_DESERIALIZATION_FAILED: f"Phase '{phase}' produced output that couldn't be parsed into tasks. "
            "The LLM output format doesn't match the expected schema.",
            FailureCategory.WORKFLOW_EXECUTION_ERROR: f"Phase '{phase}' encountered a workflow execution error. "
            "The workflow state machine may have an invalid transition.",
            FailureCategory.RUNTIME_CRASH: f"Phase '{phase}' caused a runtime crash. "
            "This is likely a bug in Polaris code.",
            FailureCategory.RESOURCE_EXHAUSTED: f"Phase '{phase}' failed due to resource exhaustion. "
            "The system may be running low on memory or disk space.",
            FailureCategory.CONFIGURATION_ERROR: f"Phase '{phase}' failed due to configuration error. "
            "Check Polaris settings and LLM provider configuration.",
            FailureCategory.UNKNOWN: f"Phase '{phase}' failed with an unknown error. Manual investigation required.",
        }
        if category == FailureCategory.WORKFLOW_EXECUTION_ERROR and "not found" in detail:
            return (
                f"Phase '{phase}' failed because Polaris referenced an upstream run or workflow object "
                "that no longer exists. This points to broken run lineage, stale IDs, or an orchestration "
                "state mismatch inside Polaris."
            )

        return templates.get(category, templates[FailureCategory.UNKNOWN])

    def _suggest_fixes(self, category: FailureCategory, failure: dict[str, Any], phase: str) -> list[str]:
        """建议修复方案"""
        fixes = {
            FailureCategory.LLM_UNAVAILABLE: [
                "Check LLM provider status page for outages",
                "Verify API keys are valid and not expired",
                "Test network connectivity to LLM provider",
                "Consider configuring fallback LLM providers",
            ],
            FailureCategory.LLM_TIMEOUT: [
                "Increase timeout settings in Polaris config",
                "Reduce prompt complexity or context length",
                "Switch to faster LLM model",
                "Check LLM provider rate limits",
            ],
            FailureCategory.LLM_FORMAT_ERROR: [
                "Review prompt templates for clarity",
                "Add stronger format constraints to prompts",
                "Implement retry with prompt variation",
                "Consider using a model with better instruction following",
            ],
            FailureCategory.PROMPT_LEAKAGE: [
                "Review prompt construction for leaked system messages",
                "Add output validation to filter system content",
                "Update system prompt boundaries",
            ],
            FailureCategory.TOOL_EXECUTION_FAILED: [
                "Check tool input validation",
                "Add error handling in tool executor",
                "Review tool permissions and sandbox settings",
                "Check tool dependencies are installed",
            ],
            FailureCategory.TASK_DESERIALIZATION_FAILED: [
                "Strengthen output schema validation",
                "Add retry with clearer format instructions",
                "Review task structure definitions",
                "Add example outputs to prompts",
            ],
            FailureCategory.WORKFLOW_EXECUTION_ERROR: [
                "Review workflow state machine transitions",
                "Check phase-specific error handling",
                "Add pre-condition validation for each phase",
            ],
            FailureCategory.RUNTIME_CRASH: [
                "Check Polaris logs for stack traces",
                "Review recent code changes",
                "Check for race conditions in async code",
                "Verify Python version compatibility",
            ],
            FailureCategory.RESOURCE_EXHAUSTED: [
                "Free up disk space",
                "Increase memory limits",
                "Restart Polaris to clear memory leaks",
                "Check for zombie processes",
            ],
            FailureCategory.CONFIGURATION_ERROR: [
                "Verify all required settings are present",
                "Check workspace path is valid and writable",
                "Validate LLM provider configuration",
                "Review environment variables",
            ],
            FailureCategory.UNKNOWN: [
                "Review full logs for more context",
                "Enable debug logging and retry",
                "Check Polaris GitHub issues for similar problems",
                "Report issue with full diagnostic data",
            ],
        }

        return fixes.get(category, fixes[FailureCategory.UNKNOWN])

    def _find_related_logs(self, category: FailureCategory, phase: str) -> list[str]:
        """查找相关日志位置"""
        logs = []

        # 根据类别提示查看特定日志
        if category in (FailureCategory.LLM_UNAVAILABLE, FailureCategory.LLM_TIMEOUT):
            logs.append("Polaris backend logs (llm/role_dialogue)")
            logs.append("LLM provider dashboard/logs")

        if category == FailureCategory.TOOL_EXECUTION_FAILED:
            logs.append("Polaris tool execution logs")
            logs.append("Factory run events (/v2/factory/runs/{id}/events)")

        if category == FailureCategory.RUNTIME_CRASH:
            logs.append("Polaris stderr output")
            logs.append("System logs (journalctl/syslog)")

        logs.append(f"Factory run details: GET /v2/factory/runs/{self._factory_run_id}")
        logs.append("Director tasks: GET /v2/director/tasks")

        return logs

    def to_dict(self) -> dict[str, Any]:
        """导出所有观测数据"""
        return {
            "round": self._current_round,
            "factory_run_id": self._factory_run_id,
            "llm_calls": [
                {
                    "call_id": c.call_id,
                    "role": c.role,
                    "timestamp": c.timestamp,
                    "model": c.model,
                    "provider": c.provider,
                    "latency_ms": c.latency_ms,
                    "success": c.success,
                    "error_message": c.error_message,
                    "token_usage": c.token_usage,
                    "tool_calls_count": len(c.tool_calls),
                }
                for c in self.llm_calls
            ],
            "stage_transitions": [
                {
                    "from": t.from_phase,
                    "to": t.to_phase,
                    "timestamp": t.timestamp,
                    "success": t.success,
                }
                for t in self.stage_transitions
            ],
            "tool_executions": [
                {
                    "tool_name": t.tool_name,
                    "timestamp": t.timestamp,
                    "success": t.success,
                    "error_message": t.error_message,
                    "duration_ms": t.duration_ms,
                }
                for t in self.tool_executions
            ],
            "error_events": self.error_events,
            "collection_warnings": self.collection_warnings,
            "statistics": {
                "total_llm_calls": len(self.llm_calls),
                "failed_llm_calls": sum(1 for c in self.llm_calls if not c.success),
                "total_tool_executions": len(self.tool_executions),
                "failed_tool_executions": sum(1 for t in self.tool_executions if not t.success),
                "total_errors": len(self.error_events),
                "stage_transition_count": len(self.stage_transitions),
            },
        }
