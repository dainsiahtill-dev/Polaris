"""Runtime-owned schedule catalog for post-execution repair migration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


def _non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("post-execution repair schedule field must be non-empty")
    return normalized


@dataclass(frozen=True)
class PostExecutionRepairScheduleStep:
    """Internal scheduling metadata for one post-execution repair step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _non_empty(self.step_id))
        object.__setattr__(self, "language", _non_empty(self.language))
        object.__setattr__(self, "phase", _non_empty(self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _non_empty(self.source_tool))
        object.__setattr__(self, "depends_on", tuple(str(item) for item in self.depends_on if str(item or "").strip()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "language": self.language,
            "phase": self.phase,
            "priority": self.priority,
            "source_tool": self.source_tool,
            "depends_on": list(self.depends_on),
        }


_POST_EXECUTION_REPAIR_SCHEDULE: tuple[PostExecutionRepairScheduleStep, ...] = (
    PostExecutionRepairScheduleStep(
        step_id="go.module_import",
        language="go",
        phase="dependency_resolution",
        priority=0,
        source_tool="deterministic_go_module_import_repair",
    ),
    PostExecutionRepairScheduleStep(
        step_id="rust.post_execution_convergence",
        language="rust",
        phase="multi_phase_convergence",
        priority=0,
        source_tool="deterministic_rust_post_repair",
    ),
    PostExecutionRepairScheduleStep(
        step_id="cpp.post_execution",
        language="cpp",
        phase="post_execution",
        priority=1,
        source_tool="deterministic_cpp_post_repair",
    ),
    PostExecutionRepairScheduleStep(
        step_id="java.post_execution",
        language="java",
        phase="post_execution",
        priority=1,
        source_tool="deterministic_java_post_repair",
    ),
)


def post_execution_repair_schedule() -> tuple[PostExecutionRepairScheduleStep, ...]:
    """Return the runtime-owned dependency-aware post-execution repair schedule."""

    return _ordered_post_execution_schedule_steps(_POST_EXECUTION_REPAIR_SCHEDULE)


def _ordered_post_execution_schedule_steps(
    steps: Sequence[PostExecutionRepairScheduleStep],
) -> tuple[PostExecutionRepairScheduleStep, ...]:
    completed: set[str] = set()
    pending = list(steps or ())
    ordered: list[PostExecutionRepairScheduleStep] = []
    while pending:
        ready = [step for step in pending if all(depends_on in completed for depends_on in step.depends_on)]
        if not ready:
            blocked = sorted(step.step_id for step in pending)
            raise RuntimeError(f"post-execution repair step dependency cycle detected: {blocked}")
        ready.sort(key=lambda step: (step.priority, step.step_id))
        for step in ready:
            ordered.append(step)
            completed.add(step.step_id)
            pending.remove(step)
    return tuple(ordered)


__all__ = [
    "PostExecutionRepairScheduleStep",
    "post_execution_repair_schedule",
]
