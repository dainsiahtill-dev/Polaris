"""Deterministic task-boundary hardening for CE construction steps.

The LLM fission pass owns semantic decomposition, but it can still publish
mixed artifact roles as independent leaves: manifests, source modules, tests,
and docs may then race each other on the task market. This module adds only the
minimal ordering constraints needed for task-boundary quality loops to converge.
It never invents files or symbols.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

_BOUNDARY_ENV = "KERNELONE_CE_TASK_BOUNDARY_HARDENING"
_DISABLED_VALUES = {"off", "none", "disabled", "false", "0"}

_ROLE_PHASE = {
    "manifest": 0,
    "config": 1,
    "source": 2,
    "test": 3,
    "docs": 4,
}

_MANIFEST_NAMES = {
    "cargo.toml",
    "composer.json",
    "gemfile",
    "go.mod",
    "mix.exs",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}

_CONFIG_NAMES = {
    ".babelrc",
    ".eslintrc",
    ".prettierrc",
    "babel.config.js",
    "eslint.config.js",
    "jest.config.js",
    "jest.config.ts",
    "rollup.config.js",
    "tailwind.config.js",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
    "vitest.config.js",
    "vitest.config.ts",
    "webpack.config.js",
}

_TEST_SUFFIXES = (
    ".spec.js",
    ".spec.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".test.js",
    ".test.jsx",
    ".test.ts",
    ".test.tsx",
    "_test.go",
)

_DOC_SUFFIXES = (".md", ".rst", ".adoc", ".txt")


def _boundary_hardening_enabled() -> bool:
    return os.environ.get(_BOUNDARY_ENV, "").strip().lower() not in _DISABLED_VALUES


def _norm_target(raw: Any) -> str:
    target = str(raw or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


def _artifact_role(target: str) -> str:
    path = _norm_target(target)
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    parts = [part for part in low.split("/") if part]
    if name in _MANIFEST_NAMES:
        return "manifest"
    if name in _CONFIG_NAMES or name.endswith((".config.js", ".config.cjs", ".config.mjs", ".config.ts")):
        return "config"
    if "tests" in parts or "test" in parts or "__tests__" in parts:
        return "test"
    if name.startswith("test_") or name.endswith(_TEST_SUFFIXES):
        return "test"
    if name.startswith("readme") or "docs" in parts or name.endswith(_DOC_SUFFIXES):
        return "docs"
    return "source"


def _append_unique(values: list[str], candidates: list[str]) -> bool:
    changed = False
    for candidate in candidates:
        if candidate and candidate not in values:
            values.append(candidate)
            changed = True
    return changed


def _has_cycle(steps: list[dict[str, Any]]) -> bool:
    known = {str(step.get("step_id") or "") for step in steps}
    indegree: dict[str, int] = dict.fromkeys(known, 0)
    dependents: dict[str, list[str]] = {step_id: [] for step_id in known}
    for step in steps:
        step_id = str(step.get("step_id") or "")
        for dep in step.get("depends_on") or []:
            if dep in known and dep != step_id:
                indegree[step_id] += 1
                dependents[dep].append(step_id)
    queue = [step_id for step_id, degree in indegree.items() if degree == 0]
    while queue:
        current = queue.pop()
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    return any(degree > 0 for degree in indegree.values())


def harden_task_boundary_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add deterministic artifact-role ordering to construction steps.

    The function is conservative and fail-open:
    - a single role is returned unchanged;
    - existing semantic dependencies are preserved;
    - only nearest preceding artifact phase dependencies are added;
    - a generated cycle returns the original list.
    """
    if not _boundary_hardening_enabled() or len(steps) < 2:
        return steps

    role_by_step: dict[str, str] = {}
    phase_by_step: dict[str, int] = {}
    target_terminal_by_phase: dict[int, dict[str, str]] = defaultdict(dict)
    phase_order: list[int] = []

    for step in steps:
        step_id = str(step.get("step_id") or "").strip()
        target = _norm_target(step.get("target_file"))
        if not step_id or not target:
            return steps
        role = _artifact_role(target)
        phase = _ROLE_PHASE[role]
        role_by_step[step_id] = role
        phase_by_step[step_id] = phase
        target_terminal_by_phase[phase][target] = step_id
        if phase not in phase_order:
            phase_order.append(phase)

    if len(set(phase_by_step.values())) < 2:
        return steps

    phase_terminal_ids: dict[int, list[str]] = {}
    for phase, target_map in target_terminal_by_phase.items():
        terminals = list(dict.fromkeys(target_map.values()))
        if terminals:
            phase_terminal_ids[phase] = terminals

    sorted_phases = sorted(phase_terminal_ids)
    next_dependency_phase: dict[int, int] = {}
    for phase in sorted_phases:
        earlier = [candidate for candidate in sorted_phases if candidate < phase]
        if earlier:
            next_dependency_phase[phase] = max(earlier)

    changed = False
    hardened: list[dict[str, Any]] = []
    for step in steps:
        cloned = dict(step)
        step_id = str(cloned.get("step_id") or "")
        phase = phase_by_step[step_id]
        role = role_by_step[step_id]
        cloned["artifact_role"] = role
        cloned["task_boundary_phase"] = phase
        deps = [str(item) for item in (cloned.get("depends_on") or []) if str(item or "").strip()]
        dep_phase = next_dependency_phase.get(phase)
        if dep_phase is not None:
            required = [dep for dep in phase_terminal_ids.get(dep_phase, []) if dep != step_id]
            changed = _append_unique(deps, required) or changed
        if deps != list(cloned.get("depends_on") or []):
            cloned["depends_on"] = deps
        if (
            cloned.get("artifact_role") != step.get("artifact_role")
            or cloned.get("task_boundary_phase") != step.get("task_boundary_phase")
        ):
            changed = True
        hardened.append(cloned)

    if not changed or _has_cycle(hardened):
        return steps
    return hardened


__all__ = ["harden_task_boundary_steps"]
