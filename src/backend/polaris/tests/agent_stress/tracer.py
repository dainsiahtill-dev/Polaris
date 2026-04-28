"""运行时追踪器 - 任务血缘、Runtime 事件、QA 结论追踪

追踪 Polaris 完整主链路的执行过程，收集：
1. Task Board 状态变化 (使用 /v2/director/tasks)
2. Factory runs 状态
3. QA 审查结论

使用当前正式 API:
- GET /v2/director/tasks
- GET /v2/factory/runs/{run_id}
- GET /v2/factory/runs/{run_id}/events
"""

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Self

from .paths import ensure_backend_root_on_syspath

ensure_backend_root_on_syspath()

import httpx
from .contracts import (
    director_task_claimed_by,
    director_task_id,
    director_task_pm_task_id,
    factory_gate_name,
    normalize_status,
    summarize_director_task_result,
)

MAX_FACTORY_RUNS_TRACKED = 64


class EventType(Enum):
    """事件类型"""

    TASK_CREATED = "task_created"
    TASK_CLAIMED = "task_claimed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    FACTORY_RUN_STARTED = "factory_run_started"
    FACTORY_RUN_COMPLETED = "factory_run_completed"
    FACTORY_RUN_FAILED = "factory_run_failed"
    QA_REVIEW = "qa_review"
    RUNTIME_ERROR = "runtime_error"


@dataclass
class TaskLineage:
    """任务血缘节点"""

    task_id: str
    subject: str
    status: str
    created_by: str = ""  # pm/architect/chief_engineer/director
    parent_task: str | None = None
    child_tasks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    created_at: str = ""
    completed_at: str | None = None
    result_summary: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    pm_task_id: str | None = None  # Director 任务关联到 PM 任务的 ID

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskLineage":
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            subject=str(payload.get("subject") or "").strip(),
            status=normalize_status(payload.get("status")),
            created_by=str(payload.get("created_by") or "").strip(),
            parent_task=str(payload.get("parent_task") or "").strip() or None,
            child_tasks=[str(item).strip() for item in (payload.get("child_tasks") or []) if str(item).strip()],
            dependencies=[str(item).strip() for item in (payload.get("dependencies") or []) if str(item).strip()],
            created_at=str(payload.get("created_at") or "").strip(),
            completed_at=str(payload.get("completed_at") or "").strip() or None,
            result_summary=str(payload.get("result_summary") or "").strip(),
            evidence_refs=[str(item).strip() for item in (payload.get("evidence_refs") or []) if str(item).strip()],
            pm_task_id=str(payload.get("pm_task_id") or "").strip() or None,
        )


@dataclass
class FactoryRun:
    """Factory 运行记录"""

    run_id: str
    status: str
    goal: str = ""
    created_at: str = ""
    completed_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FactoryRun":
        return cls(
            run_id=str(payload.get("run_id") or "").strip(),
            status=normalize_status(payload.get("status")),
            goal=str(payload.get("goal") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
            completed_at=str(payload.get("completed_at") or "").strip() or None,
            events=list(payload.get("events") or []),
            artifacts=[str(item).strip() for item in (payload.get("artifacts") or []) if str(item).strip()],
        )


@dataclass
class QAConclusion:
    """QA 审查结论"""

    review_id: str
    timestamp: str
    verdict: str  # PASS/FAIL/PARTIAL
    confidence: str
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    checklist_results: dict[str, bool] = field(default_factory=dict)
    risks: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QAConclusion":
        return cls(
            review_id=str(payload.get("review_id") or "").strip(),
            timestamp=str(payload.get("timestamp") or "").strip(),
            verdict=str(payload.get("verdict") or "").strip(),
            confidence=str(payload.get("confidence") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            findings=list(payload.get("findings") or []),
            metrics=dict(payload.get("metrics") or {}),
            checklist_results=dict(payload.get("checklist_results") or {}),
            risks=list(payload.get("risks") or []),
        )


@dataclass
class RoundTrace:
    """单轮压测追踪数据"""

    round_number: int
    project_id: str
    project_name: str
    start_time: str
    end_time: str | None = None
    status: str = "running"  # running/completed/failed
    factory_run_id: str | None = None  # 关联的 Factory run ID

    # 追踪数据
    tasks: dict[str, TaskLineage] = field(default_factory=dict)
    factory_runs: list[FactoryRun] = field(default_factory=list)
    qa_conclusions: list[QAConclusion] = field(default_factory=list)

    # 统计
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_factory_runs: int = 0
    completed_factory_runs: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RoundTrace":
        tasks_payload = payload.get("tasks") if isinstance(payload.get("tasks"), dict) else {}
        stats = payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {}
        return cls(
            round_number=int(payload.get("round_number") or 0),
            project_id=str(payload.get("project_id") or "").strip(),
            project_name=str(payload.get("project_name") or "").strip(),
            start_time=str(payload.get("start_time") or "").strip(),
            end_time=str(payload.get("end_time") or "").strip() or None,
            status=str(payload.get("status") or "running").strip(),
            factory_run_id=str(payload.get("factory_run_id") or "").strip() or None,
            tasks={
                str(key): TaskLineage.from_dict(value)
                for key, value in tasks_payload.items()
                if isinstance(value, dict)
            },
            factory_runs=[
                FactoryRun.from_dict(item) for item in (payload.get("factory_runs") or []) if isinstance(item, dict)
            ],
            qa_conclusions=[
                QAConclusion.from_dict(item) for item in (payload.get("qa_conclusions") or []) if isinstance(item, dict)
            ],
            total_tasks=int(stats.get("total_tasks") or payload.get("total_tasks") or 0),
            completed_tasks=int(stats.get("completed_tasks") or payload.get("completed_tasks") or 0),
            failed_tasks=int(stats.get("failed_tasks") or payload.get("failed_tasks") or 0),
            total_factory_runs=int(stats.get("total_factory_runs") or payload.get("total_factory_runs") or 0),
            completed_factory_runs=int(
                stats.get("completed_factory_runs") or payload.get("completed_factory_runs") or 0
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "factory_run_id": self.factory_run_id,
            "tasks": {k: self._task_to_dict(v) for k, v in self.tasks.items()},
            "factory_runs": [self._factory_run_to_dict(r) for r in self.factory_runs],
            "qa_conclusions": [self._qa_to_dict(q) for q in self.qa_conclusions],
            "statistics": {
                "total_tasks": self.total_tasks,
                "completed_tasks": self.completed_tasks,
                "failed_tasks": self.failed_tasks,
                "total_factory_runs": self.total_factory_runs,
                "completed_factory_runs": self.completed_factory_runs,
            },
        }

    def get_task_dag(self) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        for task_id, task in self.tasks.items():
            nodes.append(
                {
                    "id": task_id,
                    "label": task.subject,
                    "status": task.status,
                    "actor": task.created_by,
                }
            )

            for dep in task.dependencies:
                edges.append(
                    {
                        "from": dep,
                        "to": task_id,
                        "type": "dependency",
                    }
                )

            if task.parent_task:
                edges.append(
                    {
                        "from": task.parent_task,
                        "to": task_id,
                        "type": "parent_child",
                    }
                )

            if task.pm_task_id:
                edges.append(
                    {
                        "from": task.pm_task_id,
                        "to": task_id,
                        "type": "pm_director_link",
                    }
                )

        return {"nodes": nodes, "edges": edges}

    def get_failure_analysis(self) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []

        for task_id, task in self.tasks.items():
            if task.status in {"failed", "error", "cancelled", "blocked", "timeout"}:
                failures.append(
                    {
                        "type": "task_failure",
                        "task_id": task_id,
                        "subject": task.subject,
                        "result_summary": task.result_summary,
                    }
                )

        for run in self.factory_runs:
            if run.status in {"failed", "error", "cancelled"}:
                error_events = [
                    event
                    for event in run.events
                    if isinstance(event, dict) and normalize_status(event.get("level")) == "error"
                ]
                failures.append(
                    {
                        "type": "factory_failure",
                        "run_id": run.run_id,
                        "goal": run.goal,
                        "error_events": error_events[:3],
                    }
                )

        for qa in self.qa_conclusions:
            if normalize_status(qa.verdict) == "fail":
                failures.append(
                    {
                        "type": "qa_failure",
                        "review_id": qa.review_id,
                        "summary": qa.summary,
                        "findings_count": len(qa.findings),
                    }
                )

        return failures

    def _task_to_dict(self, task: TaskLineage) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "subject": task.subject,
            "status": task.status,
            "created_by": task.created_by,
            "parent_task": task.parent_task,
            "child_tasks": task.child_tasks,
            "dependencies": task.dependencies,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "result_summary": task.result_summary,
            "evidence_refs": task.evidence_refs,
            "pm_task_id": task.pm_task_id,
        }

    def _factory_run_to_dict(self, run: FactoryRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "status": run.status,
            "goal": run.goal,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "event_count": len(run.events),
            "artifacts": run.artifacts,
        }

    def _qa_to_dict(self, qa: QAConclusion) -> dict[str, Any]:
        return {
            "review_id": qa.review_id,
            "timestamp": qa.timestamp,
            "verdict": qa.verdict,
            "confidence": qa.confidence,
            "summary": qa.summary,
            "findings": qa.findings,
            "metrics": qa.metrics,
            "checklist_results": qa.checklist_results,
            "risks": qa.risks,
        }


class RuntimeTracer:
    """运行时追踪器

    使用当前正式 API:
    - /v2/director/tasks
    - /v2/factory/runs/{run_id}
    - /v2/factory/runs/{run_id}/events
    """

    def __init__(
        self,
        backend_url: str = "",
        workspace: str = "",
        token: str = "",
        poll_interval: float = 2.0,
        request_timeout: float = 5.0,
        final_sync_timeout: float = 8.0,
        factory_sync_interval: float = 15.0,
        qa_sync_interval: float = 20.0,
        rate_limit_backoff_cap: float = 30.0,
    ) -> None:
        self.backend_url = str(backend_url or "").strip().rstrip("/")
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd()
        self.poll_interval = poll_interval
        self.request_timeout = max(float(request_timeout or 0.0), 0.5)
        self.final_sync_timeout = max(float(final_sync_timeout or 0.0), 1.0)
        self.factory_sync_interval = max(float(factory_sync_interval or 0.0), self.poll_interval)
        self.qa_sync_interval = max(float(qa_sync_interval or 0.0), self.poll_interval)
        self.rate_limit_backoff_cap = max(float(rate_limit_backoff_cap or 0.0), self.poll_interval)

        # 创建带鉴权的客户端
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {str(token).strip()}"
        timeout = httpx.Timeout(self.request_timeout, connect=min(self.request_timeout, 2.0))
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers)

        self.current_round: RoundTrace | None = None
        self._stop_event = asyncio.Event()
        self._trace_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # 保护 current_round 的并发访问
        self._next_director_tasks_sync_at: float = 0.0
        self._next_factory_sync_at: float = 0.0
        self._next_qa_sync_at: float = 0.0
        self._director_rate_limit_backoff: float = self.poll_interval
        self._factory_rate_limit_backoff: float = self.poll_interval
        self._qa_rate_limit_backoff: float = self.poll_interval

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()
        await self.client.aclose()

    def start_round(
        self,
        round_number: int,
        project_id: str,
        project_name: str,
        factory_run_id: str | None = None,
    ) -> RoundTrace:
        """开始追踪新一轮"""
        self.current_round = RoundTrace(
            round_number=round_number,
            project_id=project_id,
            project_name=project_name,
            start_time=datetime.now().isoformat(),
            factory_run_id=factory_run_id,
        )
        now = time.monotonic()
        self._next_director_tasks_sync_at = now
        self._next_factory_sync_at = now
        self._next_qa_sync_at = now
        self._director_rate_limit_backoff = self.poll_interval
        self._factory_rate_limit_backoff = self.poll_interval
        self._qa_rate_limit_backoff = self.poll_interval

        # 启动后台追踪任务
        self._stop_event.clear()
        self._trace_task = asyncio.create_task(self._trace_loop())

        return self.current_round

    async def stop(self):
        """停止追踪"""
        self._stop_event.set()
        trace_task = self._trace_task
        self._trace_task = None
        if trace_task:
            try:
                await asyncio.wait_for(trace_task, timeout=self.final_sync_timeout)
            except asyncio.TimeoutError:
                trace_task.cancel()
                with suppress(asyncio.CancelledError):
                    await trace_task

    async def complete_round(self, status: str = "completed") -> RoundTrace:
        """完成当前轮次"""
        await self.stop()

        if self.current_round:
            self.current_round.end_time = datetime.now().isoformat()
            self.current_round.status = status

            # 最终同步
            try:
                await asyncio.wait_for(self._sync_all(), timeout=self.final_sync_timeout)
            except asyncio.TimeoutError:
                print(f"[tracer] Final sync timed out after {self.final_sync_timeout:.1f}s; returning partial trace")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Catch-all for sync failures (network, parse, etc.) - do not crash the tracer.
                print(f"[tracer] Final sync failed: {type(e).__name__}: {e}")

        return self.current_round

    async def _trace_loop(self):
        """后台追踪循环"""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._sync_periodic(), timeout=self.final_sync_timeout)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                print(f"[tracer] Sync timed out after {self.final_sync_timeout:.1f}s")
            except Exception as e:
                # Catch-all for sync errors - do not crash the trace loop.
                print(f"[tracer] Sync error: {e}")

            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval,
                )

    async def _sync_periodic(self):
        """运行中低频同步，降低控制面负载。"""
        if not self.current_round:
            return
        await self._sync_director_tasks()
        await self._sync_factory_runs()
        await self._sync_qa_conclusions()

    def _next_backoff(self, current: float) -> float:
        return min(max(current * 1.8, self.poll_interval), self.rate_limit_backoff_cap)

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        if not raw:
            return 0.0
        try:
            return max(float(raw), 0.0)
        except (TypeError, ValueError):
            return 0.0

    async def _sync_all(self):
        """同步所有数据"""
        if not self.current_round:
            return

        sync_steps = [
            ("director_tasks", self._sync_director_tasks()),
            ("factory_runs", self._sync_factory_runs()),
            ("qa_conclusions", self._sync_qa_conclusions()),
        ]
        results = await asyncio.gather(
            *(coroutine for _, coroutine in sync_steps),
            return_exceptions=True,
        )
        for (name, _), outcome in zip(sync_steps, results, strict=False):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                print(f"[tracer] {name} sync failed: {type(outcome).__name__}: {outcome}")

    async def _sync_director_tasks(self):
        """同步 Director 任务状态"""
        try:
            now = time.monotonic()
            if now < self._next_director_tasks_sync_at:
                return
            url = f"{self.backend_url}/v2/director/tasks"
            response = await self.client.get(
                url,
                timeout=self.request_timeout,
            )

            if response.status_code != 200:
                if response.status_code == 429:
                    retry_after = self._retry_after_seconds(response)
                    self._director_rate_limit_backoff = self._next_backoff(self._director_rate_limit_backoff)
                    cooldown = min(
                        max(retry_after, self._director_rate_limit_backoff),
                        self.rate_limit_backoff_cap,
                    )
                    self._next_director_tasks_sync_at = time.monotonic() + cooldown
                    print(f"[tracer] Director tasks API rate limited (429), cooldown={cooldown:.1f}s")
                    return
                self._next_director_tasks_sync_at = time.monotonic() + self.poll_interval
                print(f"[tracer] Director tasks API error: HTTP {response.status_code}")
                return
            self._director_rate_limit_backoff = self.poll_interval
            self._next_director_tasks_sync_at = time.monotonic() + self.poll_interval

            data = response.json()
            tasks = data.get("tasks", []) if isinstance(data, dict) else data

            if not isinstance(tasks, list):
                print(f"[tracer] Unexpected tasks format: {type(tasks)}")
                return

            async with self._lock:
                if not self.current_round:
                    return

                parsed_tasks: list[TaskLineage] = []
                for task_data in tasks:
                    if not isinstance(task_data, dict):
                        continue

                    task_id = director_task_id(task_data)
                    if not task_id:
                        continue

                    metadata = task_data.get("metadata", {})
                    metadata_dict = metadata if isinstance(metadata, dict) else {}
                    pm_task_id = director_task_pm_task_id(task_data)

                    task = TaskLineage(
                        task_id=task_id,
                        subject=str(task_data.get("subject") or "").strip(),
                        status=normalize_status(task_data.get("status")),
                        created_by=director_task_claimed_by(task_data),
                        parent_task=str(task_data.get("parent_id") or metadata_dict.get("parent_id") or "").strip()
                        or None,
                        dependencies=[
                            str(dep).strip()
                            for dep in (task_data.get("blocked_by") or metadata_dict.get("blocked_by") or [])
                            if str(dep).strip()
                        ],
                        created_at=str(task_data.get("created_at") or metadata_dict.get("created_at") or "").strip(),
                        completed_at=str(
                            task_data.get("completed_at") or metadata_dict.get("completed_at") or ""
                        ).strip()
                        or None,
                        result_summary=summarize_director_task_result(task_data),
                        evidence_refs=[
                            str(item).strip()
                            for item in (metadata_dict.get("evidence_refs") or [])
                            if str(item).strip()
                        ],
                        pm_task_id=pm_task_id or None,
                    )
                    parsed_tasks.append(task)

                lineage_tasks = [task for task in parsed_tasks if task.pm_task_id]
                active_tasks = lineage_tasks or parsed_tasks
                if active_tasks:
                    merged_tasks = dict(self.current_round.tasks)
                    for task in active_tasks:
                        previous = merged_tasks.get(task.task_id)
                        if previous:
                            if not task.pm_task_id:
                                task.pm_task_id = previous.pm_task_id
                            if not task.created_at:
                                task.created_at = previous.created_at
                            if not task.parent_task:
                                task.parent_task = previous.parent_task
                            if not task.dependencies:
                                task.dependencies = previous.dependencies
                        merged_tasks[task.task_id] = task
                    self.current_round.tasks = merged_tasks

                # 更新统计
                self.current_round.total_tasks = len(self.current_round.tasks)
                self.current_round.completed_tasks = sum(
                    1 for t in self.current_round.tasks.values() if t.status in {"completed", "success", "done"}
                )
                self.current_round.failed_tasks = sum(
                    1
                    for t in self.current_round.tasks.values()
                    if t.status in {"failed", "error", "cancelled", "blocked", "timeout"}
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Catch-all for sync failures (network, parse, state errors, etc.)
            print(f"[tracer] Failed to sync director tasks: {type(e).__name__}: {e}")

    async def _sync_factory_runs(self):
        """同步 Factory runs"""
        try:
            now = time.monotonic()
            if now < self._next_factory_sync_at:
                return
            # 在锁内获取引用，避免竞态条件
            async with self._lock:
                current_round = self.current_round
                if not current_round:
                    return
                factory_run_id = current_round.factory_run_id

            # 如果有特定的 factory_run_id，优先追踪该 run
            if factory_run_id:
                await self._sync_specific_factory_run(factory_run_id)
            else:
                # 获取最近的 Factory runs
                url = f"{self.backend_url}/v2/factory/runs"
                response = await self.client.get(url, params={"limit": 10}, timeout=self.request_timeout)

                if response.status_code != 200:
                    if response.status_code == 429:
                        retry_after = self._retry_after_seconds(response)
                        self._factory_rate_limit_backoff = self._next_backoff(self._factory_rate_limit_backoff)
                        cooldown = min(
                            max(retry_after, self._factory_rate_limit_backoff),
                            self.rate_limit_backoff_cap,
                        )
                        self._next_factory_sync_at = time.monotonic() + cooldown
                        return
                    self._next_factory_sync_at = time.monotonic() + self.factory_sync_interval
                    return

                data = response.json()
                runs = data.get("runs", []) if isinstance(data, dict) else data

                if not isinstance(runs, list):
                    return

                # 在锁内更新数据
                async with self._lock:
                    if not self.current_round:
                        return

                    for run_data in runs:
                        if not isinstance(run_data, dict):
                            continue

                        run_id = run_data.get("run_id") or run_data.get("id")
                        if not run_id:
                            continue

                        # 检查是否已存在
                        existing = [r for r in self.current_round.factory_runs if r.run_id == run_id]
                        if existing:
                            # 更新状态
                            run = existing[0]
                            run.status = normalize_status(run_data.get("status")) or run.status
                            run.completed_at = str(run_data.get("completed_at") or "").strip() or run.completed_at
                        else:
                            # 新增
                            run = FactoryRun(
                                run_id=run_id,
                                status=normalize_status(run_data.get("status")) or "unknown",
                                goal=str(run_data.get("goal") or "").strip(),
                                created_at=str(run_data.get("created_at") or "").strip(),
                                completed_at=str(run_data.get("completed_at") or "").strip() or None,
                            )
                            self.current_round.factory_runs.append(run)
                    if len(self.current_round.factory_runs) > MAX_FACTORY_RUNS_TRACKED:
                        self.current_round.factory_runs = self.current_round.factory_runs[-MAX_FACTORY_RUNS_TRACKED:]

                    # 更新统计
                    self.current_round.total_factory_runs = len(self.current_round.factory_runs)
                    self.current_round.completed_factory_runs = sum(
                        1 for r in self.current_round.factory_runs if r.status in ("completed", "success")
                    )

                # 在锁外获取 events，但需要重新获取引用
                async with self._lock:
                    if not self.current_round:
                        return
                    runs_copy = list(self.current_round.factory_runs)

                for run in runs_copy:
                    await self._sync_factory_events(run)
                self._factory_rate_limit_backoff = self.factory_sync_interval
                self._next_factory_sync_at = time.monotonic() + self.factory_sync_interval

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Catch-all for sync failures (network, parse, state errors).
            print(f"[tracer] Failed to sync factory runs: {type(e).__name__}: {e}")

    async def _sync_specific_factory_run(self, run_id: str):
        """同步特定的 Factory run"""
        try:
            url = f"{self.backend_url}/v2/factory/runs/{run_id}"
            response = await self.client.get(url, timeout=self.request_timeout)

            if response.status_code != 200:
                if response.status_code == 429:
                    retry_after = self._retry_after_seconds(response)
                    self._factory_rate_limit_backoff = self._next_backoff(self._factory_rate_limit_backoff)
                    cooldown = min(
                        max(retry_after, self._factory_rate_limit_backoff),
                        self.rate_limit_backoff_cap,
                    )
                    self._next_factory_sync_at = time.monotonic() + cooldown
                return

            run_data = response.json()

            async with self._lock:
                if not self.current_round:
                    return

                # 检查是否已存在
                existing = [r for r in self.current_round.factory_runs if r.run_id == run_id]
                if existing:
                    # 更新状态
                    run = existing[0]
                    run.status = normalize_status(run_data.get("status")) or run.status
                    run.completed_at = str(run_data.get("completed_at") or "").strip() or run.completed_at
                else:
                    # 新增
                    run = FactoryRun(
                        run_id=run_id,
                        status=normalize_status(run_data.get("status")) or "unknown",
                        goal=str(run_data.get("goal") or "").strip(),
                        created_at=str(run_data.get("created_at") or "").strip(),
                        completed_at=str(run_data.get("completed_at") or "").strip() or None,
                    )
                    self.current_round.factory_runs.append(run)
                    if len(self.current_round.factory_runs) > MAX_FACTORY_RUNS_TRACKED:
                        self.current_round.factory_runs = self.current_round.factory_runs[-MAX_FACTORY_RUNS_TRACKED:]

                # 更新统计
                self.current_round.total_factory_runs = len(self.current_round.factory_runs)
                self.current_round.completed_factory_runs = sum(
                    1 for item in self.current_round.factory_runs if item.status in ("completed", "success")
                )

            # 获取 events
            await self._sync_factory_events(run)
            self._factory_rate_limit_backoff = self.factory_sync_interval
            self._next_factory_sync_at = time.monotonic() + self.factory_sync_interval

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Catch-all for sync failures (network, parse, state errors).
            print(f"[tracer] Failed to sync specific factory run {run_id}: {type(e).__name__}: {e}")

    async def _sync_factory_events(self, run: FactoryRun):
        """同步 Factory run events"""
        try:
            url = f"{self.backend_url}/v2/factory/runs/{run.run_id}/events"
            response = await self.client.get(url, timeout=self.request_timeout)

            if response.status_code == 200:
                data = response.json()
                events = data.get("events", []) if isinstance(data, dict) else data
                if isinstance(events, list):
                    run.events = events

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Catch-all for event sync failures (network, parse, state errors).
            print(f"[tracer] Failed to sync factory events for {run.run_id}: {type(e).__name__}: {e}")

    async def _sync_qa_conclusions(self):
        """同步 QA 结论 (从 Factory run 的 gates 字段)"""
        try:
            now = time.monotonic()
            if now < self._next_qa_sync_at:
                return
            # 在锁内获取引用
            async with self._lock:
                if not self.current_round or not self.current_round.factory_run_id:
                    return
                factory_run_id = self.current_round.factory_run_id

            # 从 Factory run 获取 QA 门禁结果
            url = f"{self.backend_url}/v2/factory/runs/{factory_run_id}"
            response = await self.client.get(url, timeout=self.request_timeout)

            if response.status_code != 200:
                if response.status_code == 429:
                    retry_after = self._retry_after_seconds(response)
                    self._qa_rate_limit_backoff = self._next_backoff(self._qa_rate_limit_backoff)
                    cooldown = min(
                        max(retry_after, self._qa_rate_limit_backoff),
                        self.rate_limit_backoff_cap,
                    )
                    self._next_qa_sync_at = time.monotonic() + cooldown
                    return
                self._next_qa_sync_at = time.monotonic() + self.qa_sync_interval
                return

            run_data = response.json()
            gates = run_data.get("gates", [])

            if not gates:
                return

            # 构建 QA 结论
            async with self._lock:
                if not self.current_round:
                    return

                # 从 gates 判断整体 verdict
                failed_gates = [g for g in gates if normalize_status(g.get("status")) == "failed"]
                verdict = "FAIL" if failed_gates else "PASS"

                qa = QAConclusion(
                    review_id=f"qa_{self.current_round.factory_run_id}",
                    timestamp=datetime.now().isoformat(),
                    verdict=verdict,
                    confidence="high" if verdict == "PASS" else "medium",
                    summary=f"QA gates: {len(gates)} total, {len(failed_gates)} failed",
                    findings=[
                        {
                            "gate_name": factory_gate_name(g),
                            "status": g.get("status", ""),
                            "message": g.get("message", ""),
                        }
                        for g in gates
                    ],
                    metrics={"total_gates": len(gates), "failed_gates": len(failed_gates)},
                    checklist_results={
                        factory_gate_name(g): normalize_status(g.get("status")) == "passed" for g in gates
                    },
                    risks=[],
                )

                # 去重添加
                existing_ids = {q.review_id for q in self.current_round.qa_conclusions}
                if qa.review_id not in existing_ids:
                    self.current_round.qa_conclusions.append(qa)
            self._qa_rate_limit_backoff = self.qa_sync_interval
            self._next_qa_sync_at = time.monotonic() + self.qa_sync_interval

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Catch-all for QA sync failures (network, parse, state errors).
            print(f"[tracer] Failed to sync QA conclusions: {type(e).__name__}: {e}")

    def get_task_dag(self) -> dict[str, Any]:
        """获取任务 DAG 结构"""
        if not self.current_round:
            return {}
        return self.current_round.get_task_dag()

    def get_failure_analysis(self) -> list[dict[str, Any]]:
        """获取失败分析"""
        if not self.current_round:
            return []
        return self.current_round.get_failure_analysis()
