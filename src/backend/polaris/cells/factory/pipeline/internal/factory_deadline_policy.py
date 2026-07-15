"""Pure deadline admission and budget proofs for the Factory critical path.

All persisted durations in this module are integer seconds. Configuration
requirements round up and a live wall-clock horizon rounds down, so conversion
can never create budget. The module performs no I/O, reads no environment
variables, and mutates no runtime state.

Dependency validation and Director wave construction use stable Kahn traversals
in ``O(V + E)`` time and space. A wave is a deterministic, work-conserving batch
of at most ``max_parallelism`` ready tasks. The schedule is conservative rather
than an optimizer: extra waves may reserve more time, but never less.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_DEFAULT_DIRECTOR_MAX_PARALLELISM = 3


class FactoryDeadlineStageV1(str, Enum):
    """Factory stages governed by deadline admission."""

    CHIEF_ENGINEER_PORTFOLIO = "chief_engineer_portfolio"
    DIRECTOR_DISPATCH = "director_dispatch"


class FactoryDeadlineDispositionV1(str, Enum):
    """The only outcomes of a deadline admission decision."""

    EXECUTE = "execute"
    BLOCK = "block"


@dataclass(frozen=True)
class DirectorWaveScheduleV1:
    """Immutable bounded-parallelism projection of active Director tasks."""

    max_parallelism: int
    waves: tuple[tuple[str, ...], ...]
    valid: bool = True
    blockers: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "factory.director_wave_schedule.v1"

    def __post_init__(self) -> None:
        if self.valid and self.max_parallelism <= 0:
            raise ValueError("valid Director schedule requires positive max_parallelism")
        if any(not wave for wave in self.waves):
            raise ValueError("Director schedule waves must not be empty")
        if self.max_parallelism > 0 and any(len(wave) > self.max_parallelism for wave in self.waves):
            raise ValueError("Director schedule wave exceeds max_parallelism")
        flattened = tuple(task_id for wave in self.waves for task_id in wave)
        if len(flattened) != len(set(flattened)):
            raise ValueError("Director schedule cannot contain a task more than once")

    @property
    def wave_count(self) -> int:
        """Return the number of serial execution waves."""

        return len(self.waves)

    @property
    def task_count(self) -> int:
        """Return the number of tasks covered by the waves."""

        return sum(len(wave) for wave in self.waves)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe audit projection."""

        return {
            "schema_version": self.schema_version,
            "time_unit": "seconds",
            "max_parallelism": self.max_parallelism,
            "wave_count": self.wave_count,
            "task_count": self.task_count,
            "waves": [list(wave) for wave in self.waves],
            "valid": self.valid,
            "blockers": list(self.blockers),
        }


def _empty_wave_schedule() -> DirectorWaveScheduleV1:
    return DirectorWaveScheduleV1(
        max_parallelism=_DEFAULT_DIRECTOR_MAX_PARALLELISM,
        waves=(),
    )


@dataclass(frozen=True)
class TaskDependencyScheduleV1:
    """Validated PM DAG plus its bounded-parallel Director schedule."""

    task_ids: tuple[str, ...]
    active_task_ids: tuple[str, ...]
    critical_path_task_count: int
    dependency_edge_count: int
    valid: bool = True
    blockers: tuple[str, ...] = field(default_factory=tuple)
    director_wave_schedule: DirectorWaveScheduleV1 = field(default_factory=_empty_wave_schedule)
    schema_version: str = "factory.task_dependency_schedule.v1"

    @property
    def wave_count(self) -> int:
        """Return the bounded-parallel Director wave count."""

        return self.director_wave_schedule.wave_count

    @property
    def waves(self) -> tuple[tuple[str, ...], ...]:
        """Return the immutable Director waves."""

        return self.director_wave_schedule.waves

    @property
    def max_parallelism(self) -> int:
        """Return the Director concurrency bound used for scheduling."""

        return self.director_wave_schedule.max_parallelism

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe projection for audit evidence."""

        return {
            "schema_version": self.schema_version,
            "task_ids": list(self.task_ids),
            "active_task_ids": list(self.active_task_ids),
            "critical_path_task_count": self.critical_path_task_count,
            "dependency_edge_count": self.dependency_edge_count,
            "wave_count": self.wave_count,
            "max_parallelism": self.max_parallelism,
            "valid": self.valid,
            "blockers": list(self.blockers),
            "director_wave_schedule": self.director_wave_schedule.to_dict(),
        }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _required_seconds(name: str, value: object) -> int:
    normalized = _finite_number(value)
    if normalized is None or normalized < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return math.ceil(normalized)


@dataclass(frozen=True)
class FactoryDeadlineBudgetPolicyV1:
    """Integer-second stage requirements supplied by Factory configuration."""

    chief_engineer_min_start_seconds: int
    director_first_task_min_seconds: int
    director_followup_task_min_seconds: int
    quality_gate_reserved_seconds: int
    safety_seconds: int
    director_settlement_barrier_seconds: int = 5
    schema_version: str = "factory.deadline_budget_policy.v1"

    def __post_init__(self) -> None:
        for name in (
            "chief_engineer_min_start_seconds",
            "director_first_task_min_seconds",
            "director_followup_task_min_seconds",
            "quality_gate_reserved_seconds",
            "safety_seconds",
            "director_settlement_barrier_seconds",
        ):
            object.__setattr__(self, name, _required_seconds(name, getattr(self, name)))


@dataclass(frozen=True)
class BudgetUnitV1:
    """One mandatory, integer-second reservation in a Factory run plan."""

    name: str
    seconds: int
    task_ids: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "factory.budget_unit.v1"

    def __post_init__(self) -> None:
        normalized_name = self.name.strip()
        if not normalized_name:
            raise ValueError("budget unit name must not be empty")
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError(f"budget unit {normalized_name!r} contains duplicate task ids")
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "seconds", _required_seconds("seconds", self.seconds))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe audit projection."""

        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "seconds": self.seconds,
            "task_ids": list(self.task_ids),
        }


@dataclass(frozen=True)
class RunBudgetPlanV1:
    """Proof that mandatory budget units fit inside one Factory horizon."""

    stage: FactoryDeadlineStageV1
    horizon_seconds: int | None
    units: tuple[BudgetUnitV1, ...]
    valid: bool = True
    blockers: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = "factory.run_budget_plan.v1"

    def __post_init__(self) -> None:
        if self.horizon_seconds is not None:
            object.__setattr__(
                self,
                "horizon_seconds",
                _required_seconds("horizon_seconds", self.horizon_seconds),
            )
        unit_names = tuple(unit.name for unit in self.units)
        if len(unit_names) != len(set(unit_names)):
            raise ValueError("run budget plan requires unique unit names")
        if self.valid and self.blockers:
            raise ValueError("valid run budget plan cannot contain blockers")

    @property
    def required_seconds(self) -> int:
        """Return the sum of all mandatory reservations."""

        return sum(unit.seconds for unit in self.units)

    @property
    def conserved(self) -> bool:
        """Return whether the valid plan fits its horizon."""

        return self.valid and (self.horizon_seconds is None or self.required_seconds <= self.horizon_seconds)

    @property
    def surplus_seconds(self) -> int | None:
        """Return non-negative horizon budget left after all reservations."""

        if self.horizon_seconds is None:
            return None
        return max(0, self.horizon_seconds - self.required_seconds)

    @property
    def shortfall_seconds(self) -> int:
        """Return the missing budget needed to conserve the plan."""

        if self.horizon_seconds is None:
            return 0
        return max(0, self.required_seconds - self.horizon_seconds)

    @property
    def covered_task_ids(self) -> tuple[str, ...]:
        """Return task ids covered by Director wave budget units."""

        return tuple(dict.fromkeys(task_id for unit in self.units for task_id in unit.task_ids))

    def to_dict(self) -> dict[str, Any]:
        """Return the machine-checkable conservation proof."""

        return {
            "schema_version": self.schema_version,
            "time_unit": "seconds",
            "stage": self.stage.value,
            "horizon_seconds": self.horizon_seconds,
            "required_seconds": self.required_seconds,
            "surplus_seconds": self.surplus_seconds,
            "shortfall_seconds": self.shortfall_seconds,
            "conserved": self.conserved,
            "valid": self.valid,
            "blockers": list(self.blockers),
            "covered_task_ids": list(self.covered_task_ids),
            "units": [unit.to_dict() for unit in self.units],
        }


@dataclass(frozen=True)
class FactoryDeadlineAdmissionV1:
    """Immutable admission decision backed by a run-budget proof."""

    stage: FactoryDeadlineStageV1
    disposition: FactoryDeadlineDispositionV1
    reason: str
    requested_timeout_seconds: int
    timeout_seconds: int
    execution_timeout_seconds: int
    settlement_timeout_seconds: int
    remaining_seconds: int | None
    available_for_stage_seconds: int | None
    minimum_start_budget_seconds: int
    reserved_downstream_seconds: int
    critical_path_task_count: int
    budget_plan: RunBudgetPlanV1
    dependency_schedule: TaskDependencyScheduleV1
    reservation_breakdown: Mapping[str, int] = field(default_factory=dict)
    schema_version: str = "factory.deadline_admission.v1"

    def __post_init__(self) -> None:
        if self.budget_plan.stage is not self.stage:
            raise ValueError("budget plan stage must match admission stage")
        if self.disposition is FactoryDeadlineDispositionV1.EXECUTE:
            if not self.budget_plan.conserved:
                raise ValueError("EXECUTE requires a conserved budget plan")
            if self.timeout_seconds <= 0 or self.reason:
                raise ValueError("EXECUTE requires a positive timeout and no blocker reason")
            if self.execution_timeout_seconds <= 0:
                raise ValueError("EXECUTE requires a positive execution timeout")
            if self.settlement_timeout_seconds < 0:
                raise ValueError("settlement timeout must not be negative")
            if self.execution_timeout_seconds + self.settlement_timeout_seconds != self.timeout_seconds:
                raise ValueError("execution and settlement timeouts must conserve the stage lease")
        else:
            if self.timeout_seconds != 0 or not self.reason:
                raise ValueError("BLOCK requires a zero timeout and a blocker reason")
            if self.execution_timeout_seconds != 0 or self.settlement_timeout_seconds != 0:
                raise ValueError("BLOCK cannot retain execution or settlement time")

    @property
    def executable(self) -> bool:
        """Return whether the governed stage may start real work."""

        return self.disposition is FactoryDeadlineDispositionV1.EXECUTE

    @property
    def required_budget_seconds(self) -> int:
        """Return the budget required by the proof."""

        return self.budget_plan.required_seconds

    @property
    def budget_conserved(self) -> bool:
        """Return whether every mandatory budget unit is conserved."""

        return self.budget_plan.conserved

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe audit projection."""

        return {
            "schema_version": self.schema_version,
            "time_unit": "seconds",
            "stage": self.stage.value,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "requested_timeout_seconds": self.requested_timeout_seconds,
            "timeout_seconds": self.timeout_seconds,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "settlement_timeout_seconds": self.settlement_timeout_seconds,
            "remaining_seconds": self.remaining_seconds,
            "available_for_stage_seconds": self.available_for_stage_seconds,
            "minimum_start_budget_seconds": self.minimum_start_budget_seconds,
            "reserved_downstream_seconds": self.reserved_downstream_seconds,
            "critical_path_task_count": self.critical_path_task_count,
            "required_budget_seconds": self.required_budget_seconds,
            "budget_conserved": self.budget_conserved,
            "reservation_breakdown": dict(self.reservation_breakdown),
            "budget_plan": self.budget_plan.to_dict(),
            "dependency_schedule": self.dependency_schedule.to_dict(),
        }


def _normalize_identifier(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, int):
        return str(value)
    return None


def _identifier_tuple(
    value: object,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    if value is None:
        return (), (), False
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        rows: Sequence[object] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        rows = value
    else:
        return (), (), True

    normalized: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    invalid = False
    for row in rows:
        identifier = _normalize_identifier(row)
        if identifier is None:
            invalid = True
            continue
        if identifier in seen:
            if identifier not in duplicates:
                duplicates.append(identifier)
            continue
        seen.add(identifier)
        normalized.append(identifier)
    return tuple(normalized), tuple(duplicates), invalid


def _append_blocker(
    blockers: list[str],
    blocker_set: set[str],
    blocker: str,
) -> None:
    if blocker not in blocker_set:
        blockers.append(blocker)
        blocker_set.add(blocker)


def _cyclic_task_ids(
    task_ids: tuple[str, ...],
    dependencies: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    indegree = {task_id: len(dependencies.get(task_id, ())) for task_id in task_ids}
    children: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    for task_id in task_ids:
        for dependency in dependencies.get(task_id, ()):
            children[dependency].append(task_id)
    ready = deque(task_id for task_id in task_ids if indegree[task_id] == 0)
    processed = 0
    while ready:
        task_id = ready.popleft()
        processed += 1
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if processed == len(task_ids):
        return ()
    return tuple(task_id for task_id in task_ids if indegree[task_id] > 0)


def _build_director_waves(
    active_task_ids: tuple[str, ...],
    dependencies: Mapping[str, tuple[str, ...]],
    *,
    max_parallelism: int,
) -> tuple[tuple[tuple[str, ...], ...], int]:
    active_set = set(active_task_ids)
    active_dependencies = {
        task_id: tuple(dependency for dependency in dependencies.get(task_id, ()) if dependency in active_set)
        for task_id in active_task_ids
    }
    indegree = {task_id: len(active_dependencies[task_id]) for task_id in active_task_ids}
    children: dict[str, list[str]] = {task_id: [] for task_id in active_task_ids}
    for task_id in active_task_ids:
        for dependency in active_dependencies[task_id]:
            children[dependency].append(task_id)

    ready = deque(task_id for task_id in active_task_ids if indegree[task_id] == 0)
    depth = dict.fromkeys(active_task_ids, 1)
    waves: list[tuple[str, ...]] = []
    while ready:
        wave = tuple(ready.popleft() for _ in range(min(max_parallelism, len(ready))))
        waves.append(wave)
        for task_id in wave:
            for child in children[task_id]:
                depth[child] = max(depth[child], depth[task_id] + 1)
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
    return tuple(waves), max(depth.values(), default=0)


def _normalize_positive_integer(value: object) -> int | None:
    normalized = _finite_number(value)
    if normalized is None or normalized <= 0 or not normalized.is_integer():
        return None
    return int(normalized)


def build_task_dependency_schedule(
    tasks: Sequence[Mapping[str, Any]],
    *,
    active_task_ids: Sequence[str] | None = None,
    max_parallelism: int = _DEFAULT_DIRECTOR_MAX_PARALLELISM,
) -> TaskDependencyScheduleV1:
    """Validate a PM DAG and build bounded Director waves in ``O(V + E)``.

    Duplicate identifiers or dependencies, unknown references, malformed rows,
    invalid parallelism, and cycles invalidate the entire schedule. Cycle
    validation covers the full PM graph, while wave construction covers only
    active tasks; dependencies on inactive tasks are treated as already met.
    """

    blockers: list[str] = []
    blocker_set: set[str] = set()
    resolved_parallelism = _normalize_positive_integer(max_parallelism)
    if resolved_parallelism is None:
        _append_blocker(blockers, blocker_set, "invalid_max_parallelism")

    ordered_ids: list[str] = []
    task_rows: dict[str, Mapping[str, Any]] = {}
    duplicate_task_ids: list[str] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, Mapping):
            _append_blocker(
                blockers,
                blocker_set,
                f"invalid_task_row:{index}",
            )
            continue
        raw_task_id = task.get("id") if "id" in task else task.get("task_id")
        task_id = _normalize_identifier(raw_task_id)
        if task_id is None:
            _append_blocker(
                blockers,
                blocker_set,
                f"invalid_task_id:{index}",
            )
            continue
        if task_id in task_rows:
            if task_id not in duplicate_task_ids:
                duplicate_task_ids.append(task_id)
            continue
        ordered_ids.append(task_id)
        task_rows[task_id] = task
    if duplicate_task_ids:
        _append_blocker(
            blockers,
            blocker_set,
            "duplicate_task_ids:" + ",".join(duplicate_task_ids),
        )

    ordered_id_tuple = tuple(ordered_ids)
    known_ids = set(ordered_id_tuple)
    dependencies: dict[str, tuple[str, ...]] = {}
    for task_id in ordered_id_tuple:
        task = task_rows[task_id]
        raw_dependencies = task.get("depends_on") if "depends_on" in task else task.get("dependencies")
        declared, duplicate_dependencies, invalid_dependencies = _identifier_tuple(raw_dependencies)
        if invalid_dependencies:
            _append_blocker(
                blockers,
                blocker_set,
                f"invalid_dependencies:{task_id}",
            )
        if duplicate_dependencies:
            _append_blocker(
                blockers,
                blocker_set,
                f"duplicate_dependencies:{task_id}:" + ",".join(duplicate_dependencies),
            )
        unknown_dependencies = tuple(dependency for dependency in declared if dependency not in known_ids)
        if unknown_dependencies:
            _append_blocker(
                blockers,
                blocker_set,
                f"unknown_dependencies:{task_id}:" + ",".join(unknown_dependencies),
            )
        dependencies[task_id] = tuple(dependency for dependency in declared if dependency in known_ids)

    if active_task_ids is None:
        active = ordered_id_tuple
    else:
        requested_active, duplicate_active, invalid_active = _identifier_tuple(active_task_ids)
        if invalid_active:
            _append_blocker(
                blockers,
                blocker_set,
                "invalid_active_task_ids",
            )
        if duplicate_active:
            _append_blocker(
                blockers,
                blocker_set,
                "duplicate_active_task_ids:" + ",".join(duplicate_active),
            )
        unknown_active = tuple(task_id for task_id in requested_active if task_id not in known_ids)
        if unknown_active:
            _append_blocker(
                blockers,
                blocker_set,
                "unknown_active_task_ids:" + ",".join(unknown_active),
            )
        requested_active_set = set(requested_active)
        active = tuple(task_id for task_id in ordered_id_tuple if task_id in requested_active_set)

    cyclic_ids = _cyclic_task_ids(ordered_id_tuple, dependencies)
    if cyclic_ids:
        _append_blocker(
            blockers,
            blocker_set,
            "dependency_cycle:" + ",".join(cyclic_ids),
        )

    active_set = set(active)
    dependency_edge_count = sum(
        1 for task_id in active for dependency in dependencies.get(task_id, ()) if dependency in active_set
    )
    valid = not blockers
    if valid:
        assert resolved_parallelism is not None
        waves, critical_path = _build_director_waves(
            active,
            dependencies,
            max_parallelism=resolved_parallelism,
        )
        wave_schedule = DirectorWaveScheduleV1(
            max_parallelism=resolved_parallelism,
            waves=waves,
        )
    else:
        critical_path = len(active)
        wave_schedule = DirectorWaveScheduleV1(
            max_parallelism=resolved_parallelism or 0,
            waves=(),
            valid=False,
            blockers=tuple(blockers),
        )

    return TaskDependencyScheduleV1(
        task_ids=ordered_id_tuple,
        active_task_ids=active,
        critical_path_task_count=critical_path,
        dependency_edge_count=dependency_edge_count,
        valid=valid,
        blockers=tuple(blockers),
        director_wave_schedule=wave_schedule,
    )


def _normalize_admission_inputs(
    *,
    remaining_seconds: float | None,
    requested_timeout_seconds: int,
) -> tuple[int | None, int, tuple[str, ...]]:
    blockers: list[str] = []
    requested_timeout = _normalize_positive_integer(requested_timeout_seconds)
    if requested_timeout is None:
        blockers.append("requested_timeout_seconds_must_be_positive_integer")
        requested_timeout = 0

    if remaining_seconds is None:
        horizon = None
    else:
        normalized_remaining = _finite_number(remaining_seconds)
        if normalized_remaining is None or normalized_remaining < 0:
            blockers.append("remaining_seconds_must_be_finite_non_negative")
            horizon = 0
        else:
            horizon = math.floor(normalized_remaining)
    return horizon, requested_timeout, tuple(blockers)


def _invalid_admission(
    *,
    stage: FactoryDeadlineStageV1,
    reason: str,
    blockers: tuple[str, ...],
    requested_timeout_seconds: int,
    remaining_seconds: int | None,
    minimum_start_budget_seconds: int,
    dependency_schedule: TaskDependencyScheduleV1,
) -> FactoryDeadlineAdmissionV1:
    budget_plan = RunBudgetPlanV1(
        stage=stage,
        horizon_seconds=remaining_seconds,
        units=(),
        valid=False,
        blockers=blockers,
    )
    return FactoryDeadlineAdmissionV1(
        stage=stage,
        disposition=FactoryDeadlineDispositionV1.BLOCK,
        reason=reason,
        requested_timeout_seconds=requested_timeout_seconds,
        timeout_seconds=0,
        execution_timeout_seconds=0,
        settlement_timeout_seconds=0,
        remaining_seconds=remaining_seconds,
        available_for_stage_seconds=(0 if remaining_seconds is not None else None),
        minimum_start_budget_seconds=minimum_start_budget_seconds,
        reserved_downstream_seconds=0,
        critical_path_task_count=dependency_schedule.critical_path_task_count,
        budget_plan=budget_plan,
        dependency_schedule=dependency_schedule,
    )


def _director_wave_units(
    dependency_schedule: TaskDependencyScheduleV1,
    policy: FactoryDeadlineBudgetPolicyV1,
) -> tuple[BudgetUnitV1, ...]:
    return tuple(
        BudgetUnitV1(
            name=f"director_wave_{index}",
            seconds=(
                policy.director_first_task_min_seconds if index == 1 else policy.director_followup_task_min_seconds
            )
            + policy.director_settlement_barrier_seconds,
            task_ids=wave,
        )
        for index, wave in enumerate(dependency_schedule.waves, start=1)
    )


def _qa_and_safety_units(
    policy: FactoryDeadlineBudgetPolicyV1,
) -> tuple[BudgetUnitV1, BudgetUnitV1]:
    return (
        BudgetUnitV1(
            name="qa_finalization",
            seconds=policy.quality_gate_reserved_seconds,
        ),
        BudgetUnitV1(name="safety", seconds=policy.safety_seconds),
    )


def resolve_chief_engineer_portfolio_admission(
    *,
    remaining_seconds: float | None,
    requested_timeout_seconds: int,
    dependency_schedule: TaskDependencyScheduleV1,
    policy: FactoryDeadlineBudgetPolicyV1,
) -> FactoryDeadlineAdmissionV1:
    """Admit one full project-level CE call only if the whole run fits.

    The proof reserves the requested CE operation cap, every bounded-parallel
    Director wave, QA/finalization, and safety. Complexity is ``O(V)`` after the
    dependency schedule has been built.
    """

    horizon, requested_timeout, input_blockers = _normalize_admission_inputs(
        remaining_seconds=remaining_seconds,
        requested_timeout_seconds=requested_timeout_seconds,
    )
    if input_blockers:
        return _invalid_admission(
            stage=FactoryDeadlineStageV1.CHIEF_ENGINEER_PORTFOLIO,
            reason="invalid_factory_deadline_input",
            blockers=input_blockers,
            requested_timeout_seconds=requested_timeout,
            remaining_seconds=horizon,
            minimum_start_budget_seconds=policy.chief_engineer_min_start_seconds,
            dependency_schedule=dependency_schedule,
        )
    if not dependency_schedule.valid:
        return _invalid_admission(
            stage=FactoryDeadlineStageV1.CHIEF_ENGINEER_PORTFOLIO,
            reason="invalid_pm_task_dependency_schedule",
            blockers=dependency_schedule.blockers,
            requested_timeout_seconds=requested_timeout,
            remaining_seconds=horizon,
            minimum_start_budget_seconds=policy.chief_engineer_min_start_seconds,
            dependency_schedule=dependency_schedule,
        )
    if not dependency_schedule.active_task_ids:
        return _invalid_admission(
            stage=FactoryDeadlineStageV1.CHIEF_ENGINEER_PORTFOLIO,
            reason="empty_pm_task_dependency_schedule",
            blockers=("no_active_pm_tasks",),
            requested_timeout_seconds=requested_timeout,
            remaining_seconds=horizon,
            minimum_start_budget_seconds=policy.chief_engineer_min_start_seconds,
            dependency_schedule=dependency_schedule,
        )

    director_units = _director_wave_units(dependency_schedule, policy)
    downstream_units = (*director_units, *_qa_and_safety_units(policy))
    reserved_downstream = sum(unit.seconds for unit in downstream_units)
    available_for_stage = None if horizon is None else horizon - reserved_downstream
    allocated_timeout = (
        requested_timeout if available_for_stage is None else min(requested_timeout, max(0, available_for_stage))
    )
    budget_plan = RunBudgetPlanV1(
        stage=FactoryDeadlineStageV1.CHIEF_ENGINEER_PORTFOLIO,
        horizon_seconds=horizon,
        units=(
            BudgetUnitV1(
                name="chief_engineer_project_call",
                seconds=allocated_timeout,
            ),
            *downstream_units,
        ),
    )
    breakdown = {
        "director_first_wave": (director_units[0].seconds if director_units else 0),
        "director_followup_waves": sum(unit.seconds for unit in director_units[1:]),
        "qa_finalization": policy.quality_gate_reserved_seconds,
        "safety": policy.safety_seconds,
    }

    if requested_timeout < policy.chief_engineer_min_start_seconds:
        budget_plan = RunBudgetPlanV1(
            stage=FactoryDeadlineStageV1.CHIEF_ENGINEER_PORTFOLIO,
            horizon_seconds=horizon,
            units=budget_plan.units,
            valid=False,
            blockers=("requested_timeout_below_chief_engineer_minimum",),
        )
        disposition = FactoryDeadlineDispositionV1.BLOCK
        reason = "invalid_factory_deadline_input"
        timeout_seconds = 0
    elif allocated_timeout < policy.chief_engineer_min_start_seconds:
        disposition = FactoryDeadlineDispositionV1.BLOCK
        reason = "insufficient_factory_deadline_for_chief_engineer_portfolio"
        timeout_seconds = 0
    elif not budget_plan.conserved:
        disposition = FactoryDeadlineDispositionV1.BLOCK
        reason = "factory_deadline_budget_not_conserved"
        timeout_seconds = 0
    else:
        disposition = FactoryDeadlineDispositionV1.EXECUTE
        reason = ""
        timeout_seconds = allocated_timeout

    return FactoryDeadlineAdmissionV1(
        stage=FactoryDeadlineStageV1.CHIEF_ENGINEER_PORTFOLIO,
        disposition=disposition,
        reason=reason,
        requested_timeout_seconds=requested_timeout,
        timeout_seconds=timeout_seconds,
        execution_timeout_seconds=timeout_seconds,
        settlement_timeout_seconds=0,
        remaining_seconds=horizon,
        available_for_stage_seconds=available_for_stage,
        minimum_start_budget_seconds=policy.chief_engineer_min_start_seconds,
        reserved_downstream_seconds=reserved_downstream,
        critical_path_task_count=dependency_schedule.critical_path_task_count,
        budget_plan=budget_plan,
        dependency_schedule=dependency_schedule,
        reservation_breakdown=breakdown,
    )


def resolve_director_dispatch_admission(
    *,
    remaining_seconds: float | None,
    requested_timeout_seconds: int,
    dependency_schedule: TaskDependencyScheduleV1,
    first_materialization_pending: bool,
    policy: FactoryDeadlineBudgetPolicyV1,
) -> FactoryDeadlineAdmissionV1:
    """Admit one Director wave while conserving all future run budget.

    The minimum-start requirement is only an admission threshold. On EXECUTE,
    the budget plan records the actual granted operation timeout, which may be
    larger, plus every future Director wave, QA/finalization, and safety.
    Complexity is ``O(V)`` in the number of scheduled tasks.
    """

    minimum_start = (
        policy.director_first_task_min_seconds
        if first_materialization_pending
        else policy.director_followup_task_min_seconds
    )
    minimum_stage_lease = minimum_start + policy.director_settlement_barrier_seconds
    horizon, requested_timeout, input_blockers = _normalize_admission_inputs(
        remaining_seconds=remaining_seconds,
        requested_timeout_seconds=requested_timeout_seconds,
    )
    if input_blockers:
        return _invalid_admission(
            stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
            reason="invalid_factory_deadline_input",
            blockers=input_blockers,
            requested_timeout_seconds=requested_timeout,
            remaining_seconds=horizon,
            minimum_start_budget_seconds=minimum_start,
            dependency_schedule=dependency_schedule,
        )
    if not dependency_schedule.valid:
        return _invalid_admission(
            stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
            reason="invalid_pm_task_dependency_schedule",
            blockers=dependency_schedule.blockers,
            requested_timeout_seconds=requested_timeout,
            remaining_seconds=horizon,
            minimum_start_budget_seconds=minimum_start,
            dependency_schedule=dependency_schedule,
        )
    if not dependency_schedule.waves:
        return _invalid_admission(
            stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
            reason="no_active_director_tasks",
            blockers=("no_active_director_tasks",),
            requested_timeout_seconds=requested_timeout,
            remaining_seconds=horizon,
            minimum_start_budget_seconds=minimum_start,
            dependency_schedule=dependency_schedule,
        )

    future_wave_units = tuple(
        BudgetUnitV1(
            name=f"director_future_wave_{index}",
            seconds=(policy.director_followup_task_min_seconds + policy.director_settlement_barrier_seconds),
            task_ids=wave,
        )
        for index, wave in enumerate(dependency_schedule.waves[1:], start=2)
    )
    downstream_units = (*future_wave_units, *_qa_and_safety_units(policy))
    reserved_downstream = sum(unit.seconds for unit in downstream_units)
    available_for_stage = None if horizon is None else horizon - reserved_downstream
    breakdown = {
        "future_director_waves": sum(unit.seconds for unit in future_wave_units),
        "qa_finalization": policy.quality_gate_reserved_seconds,
        "safety": policy.safety_seconds,
        "minimum_execution": minimum_start,
        "minimum_stage_lease": minimum_stage_lease,
    }

    if requested_timeout < minimum_stage_lease:
        budget_plan = RunBudgetPlanV1(
            stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
            horizon_seconds=horizon,
            units=(
                BudgetUnitV1(
                    name="director_current_wave",
                    seconds=requested_timeout,
                    task_ids=dependency_schedule.waves[0],
                ),
                *downstream_units,
            ),
            valid=False,
            blockers=("requested_timeout_below_director_stage_lease_minimum",),
        )
        disposition = FactoryDeadlineDispositionV1.BLOCK
        reason = "invalid_factory_deadline_input"
        timeout_seconds = 0
        execution_timeout_seconds = 0
        settlement_timeout_seconds = 0
    else:
        allocated_timeout = (
            requested_timeout if available_for_stage is None else min(requested_timeout, max(0, available_for_stage))
        )
        if allocated_timeout < minimum_stage_lease:
            budget_plan = RunBudgetPlanV1(
                stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
                horizon_seconds=horizon,
                units=(
                    BudgetUnitV1(
                        name="director_current_wave",
                        seconds=minimum_stage_lease,
                        task_ids=dependency_schedule.waves[0],
                    ),
                    *downstream_units,
                ),
            )
            disposition = FactoryDeadlineDispositionV1.BLOCK
            reason = "insufficient_factory_deadline_for_director_dispatch"
            timeout_seconds = 0
            execution_timeout_seconds = 0
            settlement_timeout_seconds = 0
        else:
            budget_plan = RunBudgetPlanV1(
                stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
                horizon_seconds=horizon,
                units=(
                    BudgetUnitV1(
                        name="director_current_wave",
                        seconds=allocated_timeout,
                        task_ids=dependency_schedule.waves[0],
                    ),
                    *downstream_units,
                ),
            )
            disposition = FactoryDeadlineDispositionV1.EXECUTE
            reason = ""
            timeout_seconds = allocated_timeout
            settlement_timeout_seconds = min(
                policy.director_settlement_barrier_seconds,
                max(0, timeout_seconds - 1),
            )
            execution_timeout_seconds = timeout_seconds - settlement_timeout_seconds

    breakdown.update(
        {
            "current_wave_execution": execution_timeout_seconds,
            "current_wave_settlement": settlement_timeout_seconds,
        }
    )

    return FactoryDeadlineAdmissionV1(
        stage=FactoryDeadlineStageV1.DIRECTOR_DISPATCH,
        disposition=disposition,
        reason=reason,
        requested_timeout_seconds=requested_timeout,
        timeout_seconds=timeout_seconds,
        execution_timeout_seconds=execution_timeout_seconds,
        settlement_timeout_seconds=settlement_timeout_seconds,
        remaining_seconds=horizon,
        available_for_stage_seconds=available_for_stage,
        minimum_start_budget_seconds=minimum_start,
        reserved_downstream_seconds=reserved_downstream,
        critical_path_task_count=dependency_schedule.critical_path_task_count,
        budget_plan=budget_plan,
        dependency_schedule=dependency_schedule,
        reservation_breakdown=breakdown,
    )


__all__ = [
    "BudgetUnitV1",
    "DirectorWaveScheduleV1",
    "FactoryDeadlineAdmissionV1",
    "FactoryDeadlineBudgetPolicyV1",
    "FactoryDeadlineDispositionV1",
    "FactoryDeadlineStageV1",
    "RunBudgetPlanV1",
    "TaskDependencyScheduleV1",
    "build_task_dependency_schedule",
    "resolve_chief_engineer_portfolio_admission",
    "resolve_director_dispatch_admission",
]
