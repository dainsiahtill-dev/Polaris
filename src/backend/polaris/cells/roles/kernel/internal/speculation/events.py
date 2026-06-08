from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 进程内事件订阅者（观测性 seam）。emit() 在写结构化日志的同时，把事件扇出给
# 每个已注册 sink。评测矩阵、Prometheus/statsd 导出器等都可经此订阅，而无需
# 把 speculation 指标穿过 turn 结果契约逐层透传。
_SINKS: list[Callable[[SpeculationEvent], None]] = []


def subscribe(sink: Callable[[SpeculationEvent], None]) -> Callable[[], None]:
    """注册一个事件 sink，返回一个用于注销的可调用对象（幂等）."""
    _SINKS.append(sink)

    def _unsubscribe() -> None:
        with contextlib.suppress(ValueError):
            _SINKS.remove(sink)

    return _unsubscribe


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
    """输出结构化推测执行日志，并扇出给所有已注册 sink."""
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1000):03d}Z",
        "level": "INFO",
        **{k: v for k, v in asdict(event).items() if v is not None},
    }
    logger.info("%s", json.dumps(payload, ensure_ascii=False, default=str))
    # 扇出给订阅者；单个 sink 异常不得影响主流程与其他 sink。
    for sink in list(_SINKS):
        try:
            sink(event)
        except Exception:  # noqa: BLE001 - 观测性 sink 必须永不抛出到内核
            logger.warning("speculation event sink raised", exc_info=True)
