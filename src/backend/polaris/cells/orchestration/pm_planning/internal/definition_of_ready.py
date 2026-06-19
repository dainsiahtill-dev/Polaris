"""Generic Project Management **Definition-of-Ready (DoR)** evaluator.

A real senior PM does not hand a task to the team until it is *ready*: the goal
is clear, acceptance is measurable, scope is concrete, effort is estimated,
dependencies are declared and resolvable, and risk has been considered. This
module provides a pure, deterministic, §8-clean evaluator that scores a PM task
contract against those rules.

It is **additive** and **wired to nothing on the live path** by default, so the
regression floor cannot move. Future wiring (behind a default-off gate) can call
:func:`evaluate_definition_of_ready` to block handoff on unready tasks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .task_quality_gate import (
    _has_executable_or_file_acceptance_anchor,
    _has_measurable_acceptance_anchor,
    _is_concrete_pm_scope_path,
)

__all__ = [
    "DorCheckResult",
    "evaluate_definition_of_ready",
]


#: Field names that may carry an effort/size estimate, in descending precedence.
_ESTIMATE_FIELD_NAMES: tuple[str, ...] = (
    "estimated_effort",
    "estimated_hours",
    "effort",
    "estimate",
    "size",
    "story_points",
    "points",
)

#: Field names that may carry acceptance criteria, in descending precedence.
_ACCEPTANCE_FIELD_NAMES: tuple[str, ...] = (
    "acceptance_criteria",
    "acceptance",
    "criteria",
    "done_when",
)

#: Field names that may carry risk assessment, in descending precedence.
_RISK_FIELD_NAMES: tuple[str, ...] = (
    "risk_ids",
    "risk_id",
    "risks",
    "risk_assessment",
)


@dataclass(frozen=True)
class DorCheckResult:
    """Per-task Definition-of-Ready check result (immutable, JSON-safe)."""

    task_id: str = ""
    ready: bool = False
    has_title: bool = False
    has_goal: bool = False
    has_measurable_acceptance: bool = False
    has_concrete_scope: bool = False
    has_estimate: bool = False
    has_valid_dependencies: bool = True
    has_risk_assessment: bool = False
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "task_id": self.task_id,
            "ready": self.ready,
            "has_title": self.has_title,
            "has_goal": self.has_goal,
            "has_measurable_acceptance": self.has_measurable_acceptance,
            "has_concrete_scope": self.has_concrete_scope,
            "has_estimate": self.has_estimate,
            "has_valid_dependencies": self.has_valid_dependencies,
            "has_risk_assessment": self.has_risk_assessment,
            "missing": list(self.missing),
        }


def _extract_task_id(task: Mapping[str, Any]) -> str:
    """Return a non-empty task id, fall back to a deterministic position key."""
    task_id = str(task.get("id") or task.get("task_id") or "").strip()
    return task_id or "(unnamed)"


def _coerce_string_list(value: Any) -> list[str]:
    """Coerce a string-or-list value to a list of non-empty strings."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _collect_acceptance_items(task: Mapping[str, Any]) -> list[str]:
    """Collect acceptance items from any recognized field."""
    for field_name in _ACCEPTANCE_FIELD_NAMES:
        value = task.get(field_name)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return [value.strip()]
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
    return []


def _has_estimate(task: Mapping[str, Any]) -> bool:
    """Return True when the task carries a non-zero effort/size estimate."""
    for field_name in _ESTIMATE_FIELD_NAMES:
        value = task.get(field_name)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if value > 0:
                return True
            continue
        if isinstance(value, str):
            stripped = value.strip().lower()
            if not stripped:
                continue
            # Accept numeric strings and size-class tokens.
            if stripped in {"xs", "s", "m", "l", "xl", "xxl"}:
                return True
            try:
                if float(stripped) > 0:
                    return True
            except ValueError:
                continue
    return False


def _has_concrete_scope(task: Mapping[str, Any]) -> bool:
    """Return True when at least one scope_path is a concrete PM path."""
    scope_paths = _coerce_string_list(task.get("scope_paths"))
    if not scope_paths and isinstance(task.get("scope_paths"), str) and task.get("scope_paths"):
        scope_paths = [str(task.get("scope_paths")).strip()]
    return any(_is_concrete_pm_scope_path(path) for path in scope_paths)


def _collect_dependency_refs(task: Mapping[str, Any]) -> list[str]:
    """Collect dependency refs with the same precedence as dependency_validator.

    Mirrors ``dependency_validator._normalize_dep_list``: read ``depends_on``
    first and fall back to ``dependencies``. The PM/dispatch pipeline emits
    dependencies under ``depends_on``; reading only ``dependencies`` would let a
    task with dangling/cyclic ``depends_on`` refs pass the DoR gate (fail-open).
    """
    raw = task.get("depends_on")
    if not isinstance(raw, list):
        raw = task.get("dependencies")
    return _coerce_string_list(raw)


def _has_risk_assessment(task: Mapping[str, Any]) -> bool:
    """Return True when the task explicitly links risk or contains risk text."""
    for field_name in _RISK_FIELD_NAMES:
        value = task.get(field_name)
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if value:
                return True
            continue
        if isinstance(value, str):
            if value.strip():
                return True
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) > 0:
            return True
    return False


def _evaluate_task_dor(
    task: Mapping[str, Any],
    *,
    known_ids: set[str],
    require_risk_assessment: bool,
) -> DorCheckResult:
    """Evaluate one task against the DoR rules.

    Pure, deterministic, never raises.
    """
    task_id = _extract_task_id(task)

    title = str(task.get("title") or "").strip()
    goal = str(task.get("goal") or task.get("description") or "").strip()

    acceptance_items = _collect_acceptance_items(task)
    has_measurable_acceptance = _has_executable_or_file_acceptance_anchor(
        acceptance_items
    ) or _has_measurable_acceptance_anchor(acceptance_items)

    has_title = bool(title)
    has_goal = bool(goal)
    has_concrete_scope = _has_concrete_scope(task)
    has_estimate = _has_estimate(task)
    has_risk_assessment = _has_risk_assessment(task)

    dependency_refs = _collect_dependency_refs(task)
    unknown = [ref for ref in dependency_refs if ref not in known_ids] if dependency_refs else []
    has_valid_dependencies = len(unknown) == 0

    missing: list[str] = []
    if not has_title:
        missing.append("title")
    if not has_goal:
        missing.append("goal")
    if not has_measurable_acceptance:
        missing.append("measurable_acceptance")
    if not has_concrete_scope:
        missing.append("concrete_scope")
    if not has_estimate:
        missing.append("estimate")
    if not has_valid_dependencies:
        missing.append("valid_dependencies")
    if require_risk_assessment and not has_risk_assessment:
        missing.append("risk_assessment")

    ready = len(missing) == 0

    return DorCheckResult(
        task_id=task_id,
        ready=ready,
        has_title=has_title,
        has_goal=has_goal,
        has_measurable_acceptance=has_measurable_acceptance,
        has_concrete_scope=has_concrete_scope,
        has_estimate=has_estimate,
        has_valid_dependencies=has_valid_dependencies,
        has_risk_assessment=has_risk_assessment,
        missing=tuple(missing),
    )


def evaluate_definition_of_ready(
    tasks: Sequence[Mapping[str, Any]],
    *,
    require_risk_assessment: bool = False,
) -> dict[str, Any]:
    """Evaluate a PM task contract against generic Definition-of-Ready rules.

    Args:
        tasks: The PM task contract task list (each item is a task dict).
        require_risk_assessment: If True, a task is not ready unless it links
            risk ids or contains explicit risk assessment. Default False keeps
            the gate usable before risk management is fully wired.

    Returns:
        A JSON-safe report dict with per-task results and aggregate counts.
        Never raises; malformed task entries are skipped in the aggregate and
        reported as not-ready.
    """
    clean_tasks: list[dict[str, Any]] = []
    for task in tasks:
        if isinstance(task, Mapping):
            clean_tasks.append(dict(task))

    known_ids: set[str] = set()
    for task in clean_tasks:
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
        if task_id:
            known_ids.add(task_id)

    results: list[DorCheckResult] = []
    for task in clean_tasks:
        try:
            result = _evaluate_task_dor(
                task,
                known_ids=known_ids,
                require_risk_assessment=require_risk_assessment,
            )
            results.append(result)
        except (AttributeError, TypeError, ValueError, OSError):
            # Fail-closed: any unexpected evaluation error marks the task not-ready.
            task_id = _extract_task_id(task)
            results.append(
                DorCheckResult(
                    task_id=task_id,
                    ready=False,
                    missing=("evaluation_error",),
                )
            )

    ready_count = sum(1 for r in results if r.ready)
    total_tasks = len(results)
    not_ready_count = total_tasks - ready_count

    aggregate_missing: dict[str, int] = {}
    for result in results:
        for item in result.missing:
            aggregate_missing[item] = aggregate_missing.get(item, 0) + 1

    return {
        "ok": ready_count == total_tasks and total_tasks > 0,
        "ready_count": ready_count,
        "not_ready_count": not_ready_count,
        "total_tasks": total_tasks,
        "require_risk_assessment": require_risk_assessment,
        "tasks": [result.to_dict() for result in results],
        "aggregate_missing": aggregate_missing,
    }
