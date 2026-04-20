"""Dependency DAG validation for ``orchestration.pm_planning``."""

from __future__ import annotations

from collections import deque
from typing import Any


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


__all__ = ["DependencyCycleError", "validate_dependency_dag"]
