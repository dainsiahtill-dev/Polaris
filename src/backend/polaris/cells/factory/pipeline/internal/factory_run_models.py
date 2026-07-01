"""Factory run data-contracts and shared cancel-registry foundation.

This is the leaf/foundation module for ``factory.pipeline``: it owns the
durable-run data contracts (``FactoryConfig`` / ``StageResult`` /
``FactoryRun``), the lifecycle ``FactoryRunStatus`` enum, the module-level
tuning constants, and the single shared in-process cancel-event registry.

Import-time side effect to preserve: the cancel-event registry
(``_FACTORY_CANCEL_EVENTS`` guarded by ``_FACTORY_CANCEL_EVENTS_GUARD``) must
stay a single instance. It lives here and ONLY here so that every importer
shares one registry.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class FactoryRunStatus(str, Enum):
    """Factory run lifecycle status."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"


def _require_factory_run_status(data: dict[str, Any]) -> FactoryRunStatus:
    """Return the persisted factory run status or reject a malformed snapshot."""
    if "status" not in data:
        raise ValueError("FactoryRun field 'status' is required")

    raw_status = data.get("status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        raise ValueError("FactoryRun field 'status' must be a non-empty string")

    try:
        return FactoryRunStatus(raw_status.strip())
    except ValueError as exc:
        supported = ", ".join(status.value for status in FactoryRunStatus)
        raise ValueError(
            f"FactoryRun field 'status' must be one of: {supported}",
        ) from exc


TERMINAL_RUN_STATUSES = {
    FactoryRunStatus.COMPLETED,
    FactoryRunStatus.FAILED,
    FactoryRunStatus.CANCELLED,
}

SUPPORTED_FACTORY_STAGES = {
    "docs_generation",
    "pm_planning",
    "chief_engineer_review",
    "director_dispatch",
    "quality_gate",
}

DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS = 15.0
_PM_DIRECTIVE_MAX_CHARS = 18_000
_PM_ORIGINAL_DIRECTIVE_MAX_CHARS = 8_000
_PM_ARCHITECT_DOC_MAX_CHARS = 5_000
_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS = 8_000
_WORKSPACE_VALIDATION_TIMEOUT_SECONDS = 240
_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS = 2.0
_QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING = "qa_llm_judgement_unavailable"
_PM_DIRECTIVE_META_LINE_PATTERN = re.compile(
    r"(提示词|system prompt|角色设定|no yapping|<thinking>|<tool_call>|tool_call)",
    re.IGNORECASE,
)
_PM_PLAN_META_DIAGNOSTIC_MARKERS = (
    "多个任务标题/goal 重复",
    "任务标题/goal 重复",
    "标题长度不足",
    "输出了非 JSON 内容",
    "Markdown 说明、分隔线、加粗文字",
    "待重写 PM 执行任务合同",
    "合规 JSON 格式",
    "仅 `requirements.md`，无实现文件",
    "被标记为 duplicated",
    "duplicate task title",
    "duplicated task",
    "title length",
    "non-json content",
    "invalid json output",
    "rewrite pm task contract",
)

_FACTORY_CANCEL_EVENTS_GUARD = threading.Lock()
_FACTORY_CANCEL_EVENTS: dict[str, set[asyncio.Event]] = {}


def _factory_cancel_key(workspace: str | Path, run_id: str) -> str:
    return f"{Path(workspace).resolve()}::{str(run_id).strip()}"


def _register_factory_cancel_event(workspace: str | Path, run_id: str) -> asyncio.Event:
    event = asyncio.Event()
    key = _factory_cancel_key(workspace, run_id)
    with _FACTORY_CANCEL_EVENTS_GUARD:
        events = _FACTORY_CANCEL_EVENTS.setdefault(key, set())
        events.add(event)
    return event


def _unregister_factory_cancel_event(workspace: str | Path, run_id: str, event: asyncio.Event) -> None:
    key = _factory_cancel_key(workspace, run_id)
    with _FACTORY_CANCEL_EVENTS_GUARD:
        events = _FACTORY_CANCEL_EVENTS.get(key)
        if not events:
            return
        events.discard(event)
        if not events:
            _FACTORY_CANCEL_EVENTS.pop(key, None)


def _signal_factory_cancel_event(workspace: str | Path, run_id: str) -> None:
    key = _factory_cancel_key(workspace, run_id)
    with _FACTORY_CANCEL_EVENTS_GUARD:
        events = list(_FACTORY_CANCEL_EVENTS.get(key, set()))
    for event in events:
        event.set()


def _factory_jetstream_fanout_timeout_seconds() -> float:
    raw = os.getenv("POLARIS_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS")
    if raw is None:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS
    try:
        return max(float(raw), 0.05)
    except ValueError:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS


@dataclass
class FactoryConfig:
    """Factory run configuration."""

    name: str
    description: str | None = None
    stages: list[str] = field(default_factory=list)
    auto_dispatch: bool = True
    checkpoint_interval: int = 300


@dataclass
class StageResult:
    """Result of a stage execution."""

    stage: str
    status: str
    output: str | None = None
    artifacts: list[str] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactoryRun:
    """A factory run with full audit trail."""

    id: str
    config: FactoryConfig
    status: FactoryRunStatus
    created_at: str
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    stages_completed: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    recovery_point: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "config": asdict(self.config),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stages_completed": self.stages_completed,
            "stages_failed": self.stages_failed,
            "recovery_point": self.recovery_point,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactoryRun:
        config = FactoryConfig(**data.get("config", {}))
        return cls(
            id=data["id"],
            config=config,
            status=_require_factory_run_status(data),
            created_at=data["created_at"],
            updated_at=data.get("updated_at"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            stages_completed=data.get("stages_completed", []),
            stages_failed=data.get("stages_failed", []),
            recovery_point=data.get("recovery_point"),
            metadata=data.get("metadata", {}),
        )


class FactoryStageExecutor(Protocol):
    """Execution adapter for concrete factory stages."""

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        """Execute *stage* for *run* with *context*."""
