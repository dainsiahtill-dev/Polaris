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

import ast
import math
import operator
import re
from collections.abc import Callable
from typing import Any

from polaris.kernelone.quality.step_verify import normalize_step_verify

CE_BLUEPRINT_TASKS_SCHEMA_VERSION = "ce-blueprint-tasks/1"

_MAX_STEP_LINES = 120
_MAX_STEPS_PER_TASK = 24
_VERIFY_ARITHMETIC_CALL_RE = re.compile(
    r"\b(?:calculate|evaluate|eval(?:uate)?_expression|parse_and_evaluate)\s*"
    r"\(\s*(?P<quote>['\"])(?P<expr>[0-9+\-*/().\s]{1,120})(?P=quote)\s*\)"
)
_VERIFY_GREP_NUMBER_RE = re.compile(
    r"\bgrep\b(?:\s+-[A-Za-z]+)*(?:\s+--)?\s*(?P<quote>['\"])(?P<expected>-?\d+(?:\.\d+)?)(?P=quote)"
)
_ARITHMETIC_EXPR_RE = re.compile(r"^[0-9+\-*/().\s]+$")
_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


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
    verify = normalize_step_verify(record.get("verify"))
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


def _target_file_shape_error(target_file: str) -> str:
    """Reject targets that are not a single clean relative path.

    A glob, comma list, or absolute path passing this gate would (a) burn a
    Director attempt on an unwritable contract and (b) be refused by the
    executor-side enum pinning, leaving the step permanently unguided —
    malformed targets must be fixed by the CE corrective re-ask instead.
    """
    if any(ch in target_file for ch in ("*", "?", "[", "]", ",", " ", "\t", "\n")):
        return f"target_file {target_file!r} must be a single relative file path (no globs/lists)"
    if target_file.startswith(("/", "~")) or ".." in target_file.split("/"):
        return f"target_file {target_file!r} must stay inside the workspace (relative, no '..')"
    return ""


def _safe_eval_arithmetic_expr(expr: str) -> float | None:
    if not expr or not _ARITHMETIC_EXPR_RE.fullmatch(expr) or "**" in expr:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def eval_node(node: ast.AST) -> float | None:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp):
            operand = eval_node(node.operand)
            unary_func = _UNARY_OPS.get(type(node.op))
            if operand is None or unary_func is None:
                return None
            return float(unary_func(operand))
        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            binary_func = _BIN_OPS.get(type(node.op))
            if left is None or right is None or binary_func is None:
                return None
            if isinstance(node.op, ast.Div) and math.isclose(right, 0.0, abs_tol=1e-12):
                return None
            return float(binary_func(left, right))
        return None

    return eval_node(tree)


def _verify_arithmetic_oracle_error(verify_text: str) -> str:
    """Reject only obvious self-contradictory arithmetic smoke checks.

    Live L1-01 produced a construction-step verify that asserted
    ``1+2*(3-4)/5 == -0.2``. The product implementation was correct (0.6), but
    the bad oracle caused a false materialization failure. This guard is narrow
    and fail-open: it only checks pure arithmetic string literals piped into a
    calculator/evaluator-style function and a numeric grep oracle.
    """
    call_match = _VERIFY_ARITHMETIC_CALL_RE.search(verify_text)
    grep_match = _VERIFY_GREP_NUMBER_RE.search(verify_text)
    if call_match is None or grep_match is None:
        return ""
    actual = _safe_eval_arithmetic_expr(call_match.group("expr"))
    if actual is None:
        return ""
    try:
        expected = float(grep_match.group("expected"))
    except ValueError:
        return ""
    if math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
        return ""
    return (
        f"verify arithmetic oracle mismatch: expression {call_match.group('expr')!r} "
        f"evaluates to {actual:g}, but grep expects {expected:g}"
    )


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
        target_file = str(step.get("target_file") or "").strip()
        if not target_file:
            errors.append(f"{label}: step requires exactly one target_file")
        else:
            shape_error = _target_file_shape_error(target_file)
            if shape_error:
                errors.append(f"{label}: {shape_error}")
        est_lines = int(step.get("est_lines") or 0)
        if est_lines <= 0:
            errors.append(f"{label}: est_lines must be a positive estimate")
        elif est_lines > _MAX_STEP_LINES:
            errors.append(
                f"{label}: est_lines {est_lines} exceeds the convergence ceiling ({_MAX_STEP_LINES}) — split the step"
            )
        verify_text = str(step.get("verify") or "").strip()
        if not verify_text:
            errors.append(f"{label}: step requires a machine-executable verify")
        elif oracle_error := _verify_arithmetic_oracle_error(verify_text):
            errors.append(f"{label}: {oracle_error}")
        elif _target_requires_signatures(target_file) and _verify_is_all_hollow(step, verify_text):
            # I3-r21: an existence-only verify (test -f / wc / filename-grep) lets a
            # code step "resolve" on a placeholder stub that never ran the real logic.
            # A code target must carry at least one structural clause (syntax check,
            # behaviour, or a grep for a declared signature symbol).
            errors.append(
                f"{label}: verify for a code target is all-hollow (existence/line-count/marker only) — "
                f"add a structural clause (e.g. 'node --check {target_file}' / 'py_compile', or "
                f"a grep for a declared signature symbol)"
            )
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


def _verify_is_all_hollow(step: dict[str, Any], verify_text: str) -> bool:
    """True when a code step's verify is existence/line-count/marker-only.

    Delegates to the KernelOne verify SSoT, seeded with the step's declared
    signature + interface tokens so a grep for a real symbol counts as
    structural. Fail-OPEN: an unrecognized verify shape is never flagged.
    """
    from polaris.kernelone.quality.step_verify import verify_is_all_hollow

    signature_tokens: set[str] = set()
    for key in ("signatures", "interface_names"):
        for item in step.get(key) or []:
            token = str(item or "").strip()
            if token:
                signature_tokens.add(token)
    return verify_is_all_hollow(verify_text, signature_tokens=signature_tokens)


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
