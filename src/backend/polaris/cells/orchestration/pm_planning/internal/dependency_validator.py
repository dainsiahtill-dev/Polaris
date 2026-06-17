"""Dependency DAG validation + critical-path scheduling for ``orchestration.pm_planning``.

``validate_dependency_dag`` enforces acyclicity. ``compute_schedule`` turns the same
task DAG into a Critical Path Method (CPM) schedule — earliest/latest start, slack,
the critical path, topological levels, and the makespan — so a real project-manager
view (which work is on the critical path, which has slack) is available to
prioritization and status/ETA reporting. Both are pure, deterministic, I/O-free, and
carry no project-specific (business) logic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

# Effort-size vocabulary used only as a fallback when a task carries a t-shirt size
# instead of a numeric estimate. Generic, project-agnostic (§8): no domain meaning.
_SIZE_CLASS_WEIGHTS: dict[str, float] = {
    "xs": 0.5,
    "s": 1.0,
    "m": 2.0,
    "l": 4.0,
    "xl": 8.0,
    "xxl": 16.0,
}
_DEFAULT_TASK_WEIGHT = 1.0
_SLACK_TOLERANCE = 1e-9


class DependencyCycleError(ValueError):
    """Raised when task dependencies contain a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular dependency detected: {' -> '.join(cycle)}")


def _normalize_task_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_dep_list(task: dict[str, Any]) -> list[str]:
    raw = task.get("depends_on")
    if not isinstance(raw, list):
        raw = task.get("dependencies")
    if not isinstance(raw, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = _normalize_task_id(item)
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def validate_dependency_dag(tasks: list[dict[str, Any]]) -> None:
    """Validate that the given task list forms a DAG.

    External dependencies are allowed and skipped when the referenced task id
    does not exist inside ``tasks``.
    """

    task_ids = [_normalize_task_id(task.get("id")) for task in tasks if isinstance(task, dict)]
    known_ids = [task_id for task_id in task_ids if task_id]
    adjacency: dict[str, list[str]] = {task_id: [] for task_id in known_ids}
    in_degree: dict[str, int] = dict.fromkeys(known_ids, 0)

    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = _normalize_task_id(task.get("id"))
        if not task_id or task_id not in adjacency:
            continue
        for dep in _normalize_dep_list(task):
            if dep not in adjacency:
                continue
            adjacency[dep].append(task_id)
            in_degree[task_id] += 1

    queue = deque([task_id for task_id, degree in in_degree.items() if degree == 0])
    sorted_order: list[str] = []

    while queue:
        current = queue.popleft()
        sorted_order.append(current)
        for neighbor in adjacency.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_order) == len(adjacency):
        return

    remaining = {task_id for task_id in adjacency if task_id not in sorted_order}
    raise DependencyCycleError(_extract_cycle(adjacency, remaining))


def _extract_cycle(adjacency: dict[str, list[str]], nodes: set[str]) -> list[str]:
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        if node in stack:
            cycle_start = stack.index(node)
            return [*stack[cycle_start:], node]
        if node in visited:
            return None

        visited.add(node)
        stack.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in nodes:
                continue
            cycle = dfs(neighbor)
            if cycle is not None:
                return cycle
        stack.pop()
        return None

    for candidate in nodes:
        cycle = dfs(candidate)
        if cycle is not None:
            return cycle
    start = next(iter(nodes))
    return [start, start]


def _task_weight(task: dict[str, Any]) -> float:
    """Best-effort CPM weight for a task, in abstract effort units.

    Accepts a positive number, a t-shirt size class (xs/s/m/l/xl/xxl), or a numeric
    string under any of ``estimated_effort``/``estimated_hours``/``effort``/
    ``estimate``; falls back to a unit weight when no usable estimate is present.
    Pure and project-agnostic (no business semantics).
    """
    for key in ("estimated_effort", "estimated_hours", "effort", "estimate"):
        raw = task.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            if raw > 0:
                return float(raw)
            continue
        token = str(raw).strip().lower()
        if not token:
            continue
        if token in _SIZE_CLASS_WEIGHTS:
            return _SIZE_CLASS_WEIGHTS[token]
        try:
            value = float(token)
        except ValueError:
            continue
        if value > 0:
            return value
    return _DEFAULT_TASK_WEIGHT


@dataclass(frozen=True)
class Schedule:
    """A Critical Path Method schedule over a task DAG (times in effort units).

    ``levels`` is the topological depth (0 = no dependencies). ``slack`` is
    ``latest_start - earliest_start``; the ``critical_path`` is the slack-zero chain
    in topological order. ``makespan`` is the project duration (max earliest finish).
    """

    order: tuple[str, ...]
    levels: dict[str, int]
    weights: dict[str, float]
    earliest_start: dict[str, float]
    earliest_finish: dict[str, float]
    latest_start: dict[str, float]
    latest_finish: dict[str, float]
    slack: dict[str, float]
    critical_path: tuple[str, ...]
    makespan: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": list(self.order),
            "levels": dict(self.levels),
            "weights": dict(self.weights),
            "earliest_start": dict(self.earliest_start),
            "earliest_finish": dict(self.earliest_finish),
            "latest_start": dict(self.latest_start),
            "latest_finish": dict(self.latest_finish),
            "slack": dict(self.slack),
            "critical_path": list(self.critical_path),
            "makespan": self.makespan,
        }


def compute_schedule(tasks: list[dict[str, Any]]) -> Schedule:
    """Compute a Critical Path Method schedule over the task dependency DAG.

    Times are abstract effort units (per-task weight from :func:`_task_weight`).
    External dependency refs (ids not present in ``tasks``) and self-edges are
    skipped, matching :func:`validate_dependency_dag`. Deterministic for a given
    input. Raises :class:`DependencyCycleError` if the dependencies contain a cycle.
    """

    task_by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = _normalize_task_id(task.get("id"))
        if task_id and task_id not in task_by_id:
            task_by_id[task_id] = task

    known_ids = list(task_by_id)
    if not known_ids:
        return Schedule(
            order=(),
            levels={},
            weights={},
            earliest_start={},
            earliest_finish={},
            latest_start={},
            latest_finish={},
            slack={},
            critical_path=(),
            makespan=0.0,
        )

    deps_of: dict[str, list[str]] = {task_id: [] for task_id in known_ids}
    dependents_of: dict[str, list[str]] = {task_id: [] for task_id in known_ids}
    in_degree: dict[str, int] = dict.fromkeys(known_ids, 0)
    for task_id, task in task_by_id.items():
        for dep in _normalize_dep_list(task):
            if dep not in task_by_id or dep == task_id or dep in deps_of[task_id]:
                continue
            deps_of[task_id].append(dep)
            dependents_of[dep].append(task_id)
            in_degree[task_id] += 1

    # Kahn topological order; sorted frontier keeps the result deterministic.
    remaining_degree = dict(in_degree)
    queue: deque[str] = deque(sorted(task_id for task_id, degree in in_degree.items() if degree == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        newly_ready: list[str] = []
        for dependent in dependents_of[current]:
            remaining_degree[dependent] -= 1
            if remaining_degree[dependent] == 0:
                newly_ready.append(dependent)
        queue.extend(sorted(newly_ready))

    if len(order) != len(known_ids):
        remaining = {task_id for task_id in known_ids if task_id not in set(order)}
        raise DependencyCycleError(_extract_cycle(dependents_of, remaining))

    weights = {task_id: _task_weight(task_by_id[task_id]) for task_id in known_ids}

    # Forward pass: earliest start/finish + topological level.
    earliest_start: dict[str, float] = {}
    earliest_finish: dict[str, float] = {}
    levels: dict[str, int] = {}
    for task_id in order:
        deps = deps_of[task_id]
        earliest_start[task_id] = max((earliest_finish[dep] for dep in deps), default=0.0)
        earliest_finish[task_id] = earliest_start[task_id] + weights[task_id]
        levels[task_id] = max((levels[dep] + 1 for dep in deps), default=0)

    makespan = max(earliest_finish.values(), default=0.0)

    # Backward pass: latest start/finish (over the reverse topological order).
    latest_finish: dict[str, float] = {}
    latest_start: dict[str, float] = {}
    for task_id in reversed(order):
        dependents = dependents_of[task_id]
        latest_finish[task_id] = min((latest_start[s] for s in dependents), default=makespan)
        latest_start[task_id] = latest_finish[task_id] - weights[task_id]

    # Round the exposed slack so float-subtraction noise (e.g. a 1.3-unit estimate)
    # cannot leak: a consumer testing slack == 0 must still see exact zero for a
    # critical task. The critical_path itself is selected with _SLACK_TOLERANCE.
    slack = {task_id: round(latest_start[task_id] - earliest_start[task_id], 6) for task_id in known_ids}
    critical_path = tuple(task_id for task_id in order if abs(slack[task_id]) <= _SLACK_TOLERANCE)

    return Schedule(
        order=tuple(order),
        levels=levels,
        weights=weights,
        earliest_start=earliest_start,
        earliest_finish=earliest_finish,
        latest_start=latest_start,
        latest_finish=latest_finish,
        slack=slack,
        critical_path=critical_path,
        makespan=makespan,
    )


__all__ = [
    "DependencyCycleError",
    "Schedule",
    "compute_schedule",
    "validate_dependency_dag",
]
