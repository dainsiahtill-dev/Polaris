from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SpeculationEvent:
    """统一推测执行事件模型."""

    event_type: str
    turn_id: str
    stream_id: str | None = None
    call_id: str | None = None
    candidate_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    spec_key: str | None = None
    policy_mode: str | None = None
    side_effect: str | None = None
    cost_class: str | None = None
    stability_score: float | None = None
    parse_state: str | None = None
    action: str | None = None
    reason: str | None = None
    latency_ms: int | None = None
    saved_ms: int | None = None
    queue_pressure: float | None = None
    cpu_pressure: float | None = None
    abandonment_ratio: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def emit(event: SpeculationEvent) -> None:
    """输出结构化推测执行日志."""
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1000):03d}Z",
        "level": "INFO",
        **{k: v for k, v in asdict(event).items() if v is not None},
    }
    logger.info("%s", json.dumps(payload, ensure_ascii=False, default=str))
