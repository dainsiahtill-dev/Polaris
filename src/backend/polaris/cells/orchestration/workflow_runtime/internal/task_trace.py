"""任务步骤追踪事件域模型."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class TaskTraceEvent:
    """任务追踪事件."""

    event_id: str
    run_id: str
    role: str  # pm|director|qa|architect|chief_engineer
    task_id: str
    seq: int
    phase: str  # planning|analyzing|executing|verify|report|completed|failed
    step_kind: str  # phase|llm|tool|validation|retry|system
    step_title: str
    step_detail: str
    status: str  # started|running|completed|failed|skipped
    attempt: int = 0
    visibility: str = "summary"  # summary|debug
    ts: str = ""
    refs: dict = field(default_factory=dict)


class TaskTraceBuilder:
    """任务追踪事件构建器."""

    def __init__(self, run_id: str, role: str, task_id: str) -> None:
        self._run_id = run_id
        self._role = role
        self._task_id = task_id
        self._seq = 0

    def build(
        self,
        phase: str,
        step_kind: str,
        step_title: str,
        step_detail: str,
        status: str,
        attempt: int = 0,
        visibility: str = "summary",
        **refs,
    ) -> TaskTraceEvent:
        """构建任务追踪事件."""
        self._seq += 1
        return TaskTraceEvent(
            event_id=str(uuid.uuid4()),
            run_id=self._run_id,
            role=self._role,
            task_id=self._task_id,
            seq=self._seq,
            phase=phase,
            step_kind=step_kind,
            step_title=step_title,
            step_detail=sanitize_step_detail(step_detail),
            status=status,
            attempt=attempt,
            visibility=visibility,
            ts=datetime.now(timezone.utc).isoformat(),
            refs=refs,
        )

    def to_ws_payload(self, event: TaskTraceEvent) -> dict:
        """转换为 WebSocket payload 格式."""
        return {
            "type": "task_trace",
            "event": {
                "event_id": event.event_id,
                "run_id": event.run_id,
                "role": event.role,
                "task_id": event.task_id,
                "seq": event.seq,
                "phase": event.phase,
                "step_kind": event.step_kind,
                "step_title": event.step_title,
                "step_detail": event.step_detail,
                "status": event.status,
                "attempt": event.attempt,
                "visibility": event.visibility,
                "ts": event.ts,
                "refs": event.refs,
            },
        }


def sanitize_step_detail(detail: str, max_length: int = 280) -> str:
    """清理步骤详情，处理 UTF-8 并截断."""
    if not detail:
        return ""

    # 确保 UTF-8 编码安全
    try:
        detail = detail.encode("utf-8", errors="ignore").decode("utf-8")
    except (UnicodeError, AttributeError):
        detail = str(detail)

    # 敏感信息掩码（如 API keys, tokens）
    import re

    detail = re.sub(r"[a-zA-Z0-9]{32,}", "[MASKED]", detail)
    detail = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[MASKED]", detail)

    # 截断过长内容
    if len(detail) > max_length:
        detail = detail[: max_length - 3] + "..."

    return detail
