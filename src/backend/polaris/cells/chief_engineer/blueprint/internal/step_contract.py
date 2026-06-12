"""Construction-step contract for weak-executor blueprints (ce-blueprint-tasks/1).

Three-tier decomposition (PM→CE→Director): the CE consumer claims a
``pending_design`` task and — instead of only advancing it — fissions it into
construction steps sized for an output-budget-constrained local Director.
This module owns the step schema and the deterministic quality gate that
keeps junk steps out of the task market (blueprint:
THREE_TIER_TASK_DECOMPOSITION_BLUEPRINT_20260612.md §4/§8/§9).

Gate thresholds are live-calibrated: factory-bench L2-11 r6/r7 proved >120
estimated lines per write cannot converge inside a 16k-window Director's
output ceiling, and L2-12 r3 confirmed prompt-level discipline alone yields
only partial obedience.
"""

from __future__ import annotations

from typing import Any

CE_BLUEPRINT_TASKS_SCHEMA_VERSION = "ce-blueprint-tasks/1"

_MAX_STEP_LINES = 120
_MAX_STEPS_PER_TASK = 24


def normalize_construction_step(raw: Any, *, parent_pm_task: str, index: int) -> dict[str, Any]:
    """Coerce one model-proposed step into the canonical contract shape."""
    record = raw if isinstance(raw, dict) else {}
    step_id = str(record.get("step_id") or f"S{index + 1}").strip()
    # Namespace under the parent: models return bare "S1" and bare ids collide
    # across sibling fissions on the shared market (live I3-r5).
    if not step_id.startswith(f"{parent_pm_task}-"):
        step_id = f"{parent_pm_task}-{step_id}"
    target_file = str(record.get("target_file") or record.get("file") or "").strip().replace("\\", "/")
    while target_file.startswith("./"):
        target_file = target_file[2:]
    try:
        est_lines = int(record.get("est_lines") or 0)
    except (TypeError, ValueError):
        est_lines = 0
    signatures = [str(item).strip() for item in (record.get("signatures") or []) if str(item).strip()]
    interface_names = [str(item).strip() for item in (record.get("interface_names") or []) if str(item).strip()]
    raw_verify = record.get("verify")
    if isinstance(raw_verify, (list, tuple)):
        # Cloud models drift between string and array verify shapes (live
        # I3-r10: a bare str() turned the array into Python-repr garbage and
        # poisoned every QA verify of the run). Join clauses into one
        # machine-runnable command.
        verify = " && ".join(str(part).strip() for part in raw_verify if str(part).strip())
    else:
        verify = str(raw_verify or "").strip()
    # depends_on must be namespaced identically to step_id: models emit bare
    # sibling references ("S1") and prefixing only step_id manufactures
    # "unknown step" gate failures on valid output (live I3-r8).
    depends_on = []
    for item in record.get("depends_on") or []:
        dep = str(item).strip()
        if not dep:
            continue
        if not dep.startswith(f"{parent_pm_task}-"):
            dep = f"{parent_pm_task}-{dep}"
        depends_on.append(dep)
    return {
        "step_id": step_id,
        "parent_pm_task": parent_pm_task,
        "target_file": target_file,
        "est_lines": est_lines,
        "signatures": signatures,
        "interface_names": interface_names,
        "verify": verify,
        "depends_on": depends_on,
        "title": str(record.get("title") or "").strip(),
    }


def validate_construction_steps(
    steps: list[dict[str, Any]],
    *,
    parent_pm_task: str,
) -> list[str]:
    """CE-stage circuit breaker: return blocking errors (empty = gate passes).

    Junk steps must never reach ``pending_exec`` — a malformed step burns
    ~10 minutes of local Director wall clock before failing.
    """
    errors: list[str] = []
    if not steps:
        return [f"{parent_pm_task}: blueprint produced no construction steps"]
    if len(steps) > _MAX_STEPS_PER_TASK:
        errors.append(f"{parent_pm_task}: too many steps ({len(steps)} > {_MAX_STEPS_PER_TASK})")
    seen_ids: set[str] = set()
    known_ids = {str(step.get("step_id") or "") for step in steps}
    for step in steps:
        step_id = str(step.get("step_id") or "").strip()
        label = step_id or "(missing step_id)"
        if not step_id:
            errors.append(f"{parent_pm_task}: step without step_id")
        elif step_id in seen_ids:
            errors.append(f"{label}: duplicate step_id")
        seen_ids.add(step_id)
        if not str(step.get("target_file") or "").strip():
            errors.append(f"{label}: step requires exactly one target_file")
        est_lines = int(step.get("est_lines") or 0)
        if est_lines <= 0:
            errors.append(f"{label}: est_lines must be a positive estimate")
        elif est_lines > _MAX_STEP_LINES:
            errors.append(
                f"{label}: est_lines {est_lines} exceeds the convergence ceiling ({_MAX_STEP_LINES}) — split the step"
            )
        if not str(step.get("verify") or "").strip():
            errors.append(f"{label}: step requires a machine-executable verify")
        if not step.get("signatures") and _target_requires_signatures(str(step.get("target_file") or "")):
            errors.append(f"{label}: step requires a signatures skeleton")
        for dep in step.get("depends_on") or []:
            if dep not in known_ids:
                errors.append(f"{label}: depends_on references unknown step {dep!r}")
            if dep == step_id:
                errors.append(f"{label}: step depends on itself")
    cycle_members = _find_dependency_cycle(steps)
    if cycle_members:
        errors.append(f"{parent_pm_task}: depends_on cycle among steps {sorted(cycle_members)} — break the cycle")
    return errors


def _find_dependency_cycle(steps: list[dict[str, Any]]) -> set[str]:
    """Kahn's algorithm: return the step_ids stuck in a depends_on cycle.

    The market readiness gate blocks a step until its deps resolve, so a
    cycle published to ``pending_exec`` deadlocks the whole cluster as
    permanently-unclaimable rows — it must be refused at the CE gate.
    """
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
    return {step_id for step_id, degree in indegree.items() if degree > 0}


_CODE_SIGNATURE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb")


def _target_requires_signatures(target_file: str) -> bool:
    """Doc/style steps (readme.md, style.css) have no signatures to declare —
    demanding one dead-letters legitimate fissions (live I3-r5: the README
    task died on "step requires a signatures skeleton")."""
    return target_file.lower().endswith(_CODE_SIGNATURE_SUFFIXES)


def build_blueprint_tasks_contract(
    *,
    parent_pm_task: str,
    blueprint_id: str,
    blueprint_path: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the market-facing fission contract (E1 triple binding)."""
    return {
        "schema_version": CE_BLUEPRINT_TASKS_SCHEMA_VERSION,
        "parent_task_id": parent_pm_task,
        "blueprint_id": blueprint_id,
        "blueprint_path": blueprint_path,
        "steps": steps,
        "step_count": len(steps),
    }
