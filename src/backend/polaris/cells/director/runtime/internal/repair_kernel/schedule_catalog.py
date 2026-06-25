"""Runtime-owned schedule catalogs for deterministic repair migration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS = 1
_MAX_REPAIR_SCHEDULE_MAX_ROUNDS = 10


def _non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("repair schedule field must be non-empty")
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


@dataclass(frozen=True)
class MaterializationQualityRepairScheduleStep:
    """Internal scheduling metadata for one materialization-quality repair step."""

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


PostExecutionStepRunner = Callable[[PostExecutionRepairScheduleStep], Sequence[Mapping[str, Any]]]
MaterializationQualityStepRunner = Callable[[MaterializationQualityRepairScheduleStep], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class PostExecutionRepairScheduleRun:
    """Result of invoking migration runner callbacks through the runtime schedule."""

    ordered_steps: tuple[PostExecutionRepairScheduleStep, ...]
    tool_results: tuple[dict[str, Any], ...] = ()
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS
    rounds_run: int = 0
    convergence_status: str = "not_started"
    stopped_reason: str = "not_started"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item or {}) for item in self.tool_results))
        object.__setattr__(self, "max_rounds", _coerce_max_rounds(self.max_rounds))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(self, "convergence_status", _non_empty(self.convergence_status))
        object.__setattr__(self, "stopped_reason", _non_empty(self.stopped_reason))


@dataclass(frozen=True)
class MaterializationQualityRepairScheduleRun:
    """Result of invoking materialization-quality callbacks through the runtime schedule."""

    ordered_steps: tuple[MaterializationQualityRepairScheduleStep, ...]
    tool_results: tuple[dict[str, Any], ...] = ()
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS
    rounds_run: int = 0
    convergence_status: str = "not_started"
    stopped_reason: str = "not_started"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item or {}) for item in self.tool_results))
        object.__setattr__(self, "max_rounds", _coerce_max_rounds(self.max_rounds))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(self, "convergence_status", _non_empty(self.convergence_status))
        object.__setattr__(self, "stopped_reason", _non_empty(self.stopped_reason))


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


_MATERIALIZATION_QUALITY_REPAIR_SCHEDULE: tuple[MaterializationQualityRepairScheduleStep, ...] = (
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.hygiene_scaffold",
        language="multi",
        phase="hygiene",
        priority=0,
        source_tool="deterministic_materialization_hygiene_repair",
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.typescript_scaffold",
        language="typescript",
        phase="scaffold",
        priority=10,
        source_tool="deterministic_typescript_scaffold_materialization_repair",
        depends_on=("materialization.hygiene_scaffold",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.typescript_compiler",
        language="typescript",
        phase="compiler",
        priority=20,
        source_tool="deterministic_typescript_materialization_repair",
        depends_on=("materialization.typescript_scaffold",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.node_manifest",
        language="javascript",
        phase="manifest",
        priority=30,
        source_tool="deterministic_node_manifest_materialization_repair",
        depends_on=("materialization.typescript_compiler",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.rust_compiler",
        language="rust",
        phase="compiler",
        priority=40,
        source_tool="deterministic_rust_materialization_repair",
        depends_on=("materialization.node_manifest",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.target_runtime",
        language="multi",
        phase="runtime_smoke",
        priority=50,
        source_tool="deterministic_target_runtime_materialization_repair",
        depends_on=("materialization.rust_compiler",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.python_import",
        language="python",
        phase="compiler",
        priority=60,
        source_tool="deterministic_python_materialization_repair",
        depends_on=("materialization.target_runtime",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.go_import",
        language="go",
        phase="dependency_resolution",
        priority=70,
        source_tool="deterministic_go_materialization_repair",
        depends_on=("materialization.python_import",),
    ),
)


def post_execution_repair_schedule() -> tuple[PostExecutionRepairScheduleStep, ...]:
    """Return the runtime-owned dependency-aware post-execution repair schedule."""

    return _ordered_post_execution_schedule_steps(_POST_EXECUTION_REPAIR_SCHEDULE)


def materialization_quality_repair_schedule() -> tuple[MaterializationQualityRepairScheduleStep, ...]:
    """Return the runtime-owned materialization-quality repair schedule."""

    return _ordered_materialization_quality_schedule_steps(_MATERIALIZATION_QUALITY_REPAIR_SCHEDULE)


def run_post_execution_repair_schedule_callbacks(
    *,
    runner_step_ids: Sequence[str],
    runner: PostExecutionStepRunner,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> PostExecutionRepairScheduleRun:
    """Run migration callbacks in runtime-owned schedule order with bounded convergence."""

    ordered_steps = post_execution_repair_schedule()
    _validate_runner_bindings(ordered_steps=ordered_steps, runner_step_ids=runner_step_ids)
    tool_results: list[dict[str, Any]] = []
    rounds_run, convergence_status, stopped_reason = _run_scheduled_repair_rounds(
        ordered_steps=ordered_steps,
        runner=runner,
        max_rounds=max_rounds,
        tool_results=tool_results,
    )
    _annotate_convergence_result(
        tool_results,
        max_rounds=_coerce_max_rounds(max_rounds),
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )
    return PostExecutionRepairScheduleRun(
        ordered_steps=ordered_steps,
        tool_results=tuple(tool_results),
        max_rounds=max_rounds,
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )


def run_materialization_quality_repair_schedule_callbacks(
    *,
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunner,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> MaterializationQualityRepairScheduleRun:
    """Run materialization-quality callbacks in runtime-owned order with bounded convergence."""

    ordered_steps = materialization_quality_repair_schedule()
    _validate_materialization_quality_runner_bindings(ordered_steps=ordered_steps, runner_step_ids=runner_step_ids)
    tool_results: list[dict[str, Any]] = []
    rounds_run, convergence_status, stopped_reason = _run_scheduled_repair_rounds(
        ordered_steps=ordered_steps,
        runner=runner,
        max_rounds=max_rounds,
        tool_results=tool_results,
    )
    _annotate_convergence_result(
        tool_results,
        max_rounds=_coerce_max_rounds(max_rounds),
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )
    return MaterializationQualityRepairScheduleRun(
        ordered_steps=ordered_steps,
        tool_results=tuple(tool_results),
        max_rounds=max_rounds,
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )


def _run_scheduled_repair_rounds(
    *,
    ordered_steps: Sequence[PostExecutionRepairScheduleStep] | Sequence[MaterializationQualityRepairScheduleStep],
    runner: Callable[[Any], Sequence[Mapping[str, Any]]],
    max_rounds: int,
    tool_results: list[dict[str, Any]],
) -> tuple[int, str, str]:
    bounded_max_rounds = _coerce_max_rounds(max_rounds)
    seen_round_fingerprints: set[tuple[tuple[str, ...], ...]] = set()
    rounds_run = 0
    for round_number in range(1, bounded_max_rounds + 1):
        round_results: list[dict[str, Any]] = []
        for step in ordered_steps:
            step_results = [dict(item or {}) for item in runner(step)]
            for result in step_results:
                _annotate_tool_result(
                    result,
                    step,
                    round_number=round_number,
                    max_rounds=bounded_max_rounds,
                )
            round_results.extend(step_results)
        rounds_run = round_number
        if not round_results:
            stopped_reason = "no_repairs_applied" if not tool_results else "converged_no_repairs_applied"
            return rounds_run, "converged", stopped_reason
        fingerprint = _round_fingerprint(round_results)
        if fingerprint in seen_round_fingerprints:
            return rounds_run, "cycle_broken", "repeated_round_fingerprint"
        seen_round_fingerprints.add(fingerprint)
        tool_results.extend(round_results)
    return rounds_run, "max_rounds_reached", "max_rounds_reached"


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


def _ordered_materialization_quality_schedule_steps(
    steps: Sequence[MaterializationQualityRepairScheduleStep],
) -> tuple[MaterializationQualityRepairScheduleStep, ...]:
    completed: set[str] = set()
    pending = list(steps or ())
    ordered: list[MaterializationQualityRepairScheduleStep] = []
    while pending:
        ready = [step for step in pending if all(depends_on in completed for depends_on in step.depends_on)]
        if not ready:
            blocked = sorted(step.step_id for step in pending)
            raise RuntimeError(f"materialization quality repair step dependency cycle detected: {blocked}")
        ready.sort(key=lambda step: (step.priority, step.step_id))
        for step in ready:
            ordered.append(step)
            completed.add(step.step_id)
            pending.remove(step)
    return tuple(ordered)


def _validate_runner_bindings(
    *,
    ordered_steps: Sequence[PostExecutionRepairScheduleStep],
    runner_step_ids: Sequence[str],
) -> None:
    scheduled_step_ids = {step.step_id for step in ordered_steps}
    runner_ids = {str(step_id or "").strip() for step_id in runner_step_ids if str(step_id or "").strip()}
    missing_runner_step_ids = sorted(scheduled_step_ids - runner_ids)
    if missing_runner_step_ids:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {missing_runner_step_ids}")
    extra_runner_step_ids = sorted(runner_ids - scheduled_step_ids)
    if extra_runner_step_ids:
        raise RuntimeError(f"post-execution repair runner is not declared in runtime schedule: {extra_runner_step_ids}")


def _validate_materialization_quality_runner_bindings(
    *,
    ordered_steps: Sequence[MaterializationQualityRepairScheduleStep],
    runner_step_ids: Sequence[str],
) -> None:
    scheduled_step_ids = {step.step_id for step in ordered_steps}
    runner_ids = {str(step_id or "").strip() for step_id in runner_step_ids if str(step_id or "").strip()}
    missing_runner_step_ids = sorted(scheduled_step_ids - runner_ids)
    if missing_runner_step_ids:
        raise RuntimeError(f"materialization quality repair schedule has no runner binding: {missing_runner_step_ids}")
    extra_runner_step_ids = sorted(runner_ids - scheduled_step_ids)
    if extra_runner_step_ids:
        raise RuntimeError(
            f"materialization quality repair runner is not declared in runtime schedule: {extra_runner_step_ids}"
        )


def _coerce_max_rounds(value: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS
    return min(_MAX_REPAIR_SCHEDULE_MAX_ROUNDS, max(1, normalized))


def _round_fingerprint(tool_results: Sequence[dict[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(_tool_result_fingerprint(item) for item in tool_results))


def _tool_result_fingerprint(tool_result: Mapping[str, Any]) -> tuple[str, ...]:
    result = tool_result.get("result")
    payload = result if isinstance(result, dict) else {}
    return (
        str(tool_result.get("tool_name") or tool_result.get("tool") or ""),
        str(payload.get("source_tool") or ""),
        str(payload.get("file") or ""),
        str(payload.get("operation") or payload.get("action") or ""),
        str(payload.get("before_hash") or ""),
        str(payload.get("after_hash") or ""),
        str(payload.get("bridge_step_id") or ""),
    )


def _annotate_tool_result(
    tool_result: dict[str, Any],
    step: PostExecutionRepairScheduleStep | MaterializationQualityRepairScheduleStep,
    *,
    round_number: int,
    max_rounds: int,
) -> None:
    result = tool_result.get("result")
    payload = result if isinstance(result, dict) else {}
    if not payload:
        return
    payload.setdefault("bridge_step_id", step.step_id)
    payload.setdefault("language", step.language)
    payload.setdefault("phase", step.phase)
    payload.setdefault("priority", step.priority)
    payload.setdefault("depends_on", list(step.depends_on))
    payload.setdefault("round_number", round_number)
    payload.setdefault("max_rounds", max_rounds)
    payload.setdefault("scheduler_round_number", round_number)
    payload.setdefault("scheduler_max_rounds", max_rounds)
    revalidation = payload.get("revalidation")
    if isinstance(revalidation, dict):
        revalidation.setdefault("round_number", payload.get("round_number"))
        revalidation.setdefault("max_rounds", max_rounds)


def _annotate_convergence_result(
    tool_results: Sequence[dict[str, Any]],
    *,
    max_rounds: int,
    rounds_run: int,
    convergence_status: str,
    stopped_reason: str,
) -> None:
    for tool_result in tool_results:
        result = tool_result.get("result")
        payload = result if isinstance(result, dict) else {}
        if not payload:
            continue
        payload.setdefault("scheduler_rounds_run", rounds_run)
        payload.setdefault("convergence_status", convergence_status)
        payload.setdefault("convergence_stopped_reason", stopped_reason)
        revalidation = payload.get("revalidation")
        if isinstance(revalidation, dict):
            revalidation.setdefault("scheduler_rounds_run", rounds_run)
            revalidation.setdefault("convergence_status", convergence_status)
            revalidation.setdefault("convergence_stopped_reason", stopped_reason)
            revalidation.setdefault("max_rounds", max_rounds)


__all__ = [
    "DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS",
    "MaterializationQualityRepairScheduleRun",
    "MaterializationQualityRepairScheduleStep",
    "MaterializationQualityStepRunner",
    "PostExecutionRepairScheduleRun",
    "PostExecutionRepairScheduleStep",
    "PostExecutionStepRunner",
    "materialization_quality_repair_schedule",
    "post_execution_repair_schedule",
    "run_materialization_quality_repair_schedule_callbacks",
    "run_post_execution_repair_schedule_callbacks",
]
