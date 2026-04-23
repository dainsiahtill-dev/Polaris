"""PM Task Quality Gate Module.

This module provides PM task quality evaluation, validation, and autofix capabilities.
It is designed to be testable as pure functions without side effects.
"""

from __future__ import annotations

import os
import re
from typing import Any

from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    DependencyCycleError,
    validate_dependency_dag,
)

_PM_PROMPT_LEAK_TOKENS = (
    "you are ",
    "角色设定",
    "system prompt",
    "no yapping",
    "<thinking>",
    "<tool_call>",
    "提示词",
)
_PM_ACTION_TOKENS = (
    "build",
    "implement",
    "define",
    "design",
    "write",
    "create",
    "refactor",
    "verify",
    "构建",
    "实现",
    "设计",
    "编写",
    "重构",
    "验证",
)
_PM_MEASURABLE_COMMAND_RE = re.compile(
    r"\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_ASSERT_RE = re.compile(
    r"\b(verify|assert|expect|should|must|returns?|response|status|校验|验证|断言|应当|必须)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_RESULT_RE = re.compile(
    r"\b(200|201|202|204|400|401|403|404|409|422|500|pass|fail|true|false|ok|error)\b|[<>]=?\s*\d+|\b\d+\s*(ms|s|sec|seconds?|分钟|小时|days?)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\w.\-]+[\\/][\w.\-/\\]+)",
)
_PM_MEASURABLE_BACKTICK_RE = re.compile(r"`[^`]{2,}`")


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_path_list(value: Any) -> list[str]:
    if isinstance(value, str):
        entries = [segment.strip() for segment in value.split(",") if segment.strip()]
    elif isinstance(value, list):
        entries = [str(item).strip() for item in value if str(item).strip()]
    else:
        entries = []
    normalized: list[str] = []
    for item in entries:
        token = str(item).strip().replace("\\", "/")
        token = token.lstrip("./")
        token = re.sub(r"/+", "/", token)
        if token:
            normalized.append(token)
    return normalized


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    token = re.sub(r"^[A-Za-z]:/", "", token)
    token = token.lstrip("./").strip("/")
    token = re.sub(r"/+", "/", token)
    return token.lower()


def _contains_prompt_leakage(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    if not lowered:
        return False
    return any(token in lowered for token in _PM_PROMPT_LEAK_TOKENS)


def _has_measurable_acceptance_anchor(acceptance_items: list[str]) -> bool:
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if _PM_MEASURABLE_BACKTICK_RE.search(normalized):
            return True
        if _PM_MEASURABLE_COMMAND_RE.search(normalized):
            return True
        has_assert = bool(_PM_MEASURABLE_ASSERT_RE.search(normalized))
        has_observable = bool(_PM_MEASURABLE_RESULT_RE.search(normalized) or _PM_MEASURABLE_PATH_RE.search(normalized))
        if has_assert and has_observable:
            return True
    return False


def evaluate_pm_task_quality(
    normalized: dict[str, Any],
    docs_stage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate PM task quality and return quality report.

    Args:
        normalized: Normalized PM task payload with 'tasks' key
        docs_stage: Optional docs stage configuration

    Returns:
        Quality report with score, issues, warnings, and summary
    """
    tasks_raw = normalized.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    critical_issues: list[str] = []
    warnings: list[str] = []
    seen_signatures: set[str] = set()
    low_action_count = 0
    phase_count = 0
    dependency_task_count = 0
    checklist_task_count = 0
    measurable_acceptance_task_count = 0
    docs_section_task_count = 0
    backlog_trace_task_count = 0

    docs_stage_dict: dict[str, Any] = docs_stage if isinstance(docs_stage, dict) else {}
    docs_enabled = bool(docs_stage_dict.get("enabled"))
    active_doc = _normalize_path(docs_stage_dict.get("active_doc_path", ""))
    active_dir = _normalize_path(os.path.dirname(active_doc)) if active_doc else ""

    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            critical_issues.append(f"task[{index}]: task payload is not an object")
            continue
        task_id = str(task.get("id") or f"TASK-{index}").strip()
        title = _normalize_text(task.get("title"))
        goal = _normalize_text(task.get("goal"))
        description = _normalize_text(task.get("description"))
        backlog_ref = _normalize_text(task.get("backlog_ref"))
        signature = _normalize_text(f"{title.lower()}::{goal.lower()}")
        combined_text = " ".join([title, goal, description, backlog_ref]).strip()

        if signature:
            if signature in seen_signatures:
                critical_issues.append(f"{task_id}: duplicated title/goal signature")
            seen_signatures.add(signature)

        if len(title) < 10:
            warnings.append(f"{task_id}: title is too short")
        if len(goal) < 18:
            warnings.append(f"{task_id}: goal is too short")
        if _contains_prompt_leakage(combined_text):
            critical_issues.append(f"{task_id}: detected role/prompt leakage markers in task content")

        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task.get("acceptance")
        acceptance_items = [_normalize_text(item) for item in (acceptance or []) if _normalize_text(item)]
        if not acceptance_items:
            critical_issues.append(f"{task_id}: acceptance criteria is missing")
        elif not _has_measurable_acceptance_anchor(acceptance_items):
            warnings.append(f"{task_id}: acceptance criteria lacks measurable anchors")
        else:
            measurable_acceptance_task_count += 1

        lowered_task = combined_text.lower()
        if not any(token in lowered_task for token in _PM_ACTION_TOKENS):
            low_action_count += 1
            warnings.append(f"{task_id}: action signal is weak")

        phase = str(task.get("phase") or "").strip().lower()
        if phase:
            phase_count += 1

        deps = task.get("depends_on")
        if not isinstance(deps, list):
            deps = task.get("dependencies")
        if isinstance(deps, list) and any(_normalize_text(item) for item in deps):
            dependency_task_count += 1

        checklist = task.get("execution_checklist")
        if isinstance(checklist, list) and any(_normalize_text(item) for item in checklist):
            checklist_task_count += 1
        else:
            warnings.append(f"{task_id}: missing execution_checklist")

        if backlog_ref:
            backlog_trace_task_count += 1

        assigned_to = str(task.get("assigned_to") or "").strip()
        if assigned_to in {"Director", "ChiefEngineer"}:
            task_paths = _normalize_path_list(task.get("scope_paths") or [])
            task_paths.extend(_normalize_path_list(task.get("target_files") or []))
            task_paths.extend(_normalize_path_list(task.get("context_files") or []))
            if not task_paths:
                critical_issues.append(f"{task_id}: assignee {assigned_to} requires explicit scope")
            elif docs_enabled and active_doc:
                out_of_scope: list[str] = []
                for path in task_paths:
                    normalized_path = _normalize_path(path)
                    if not normalized_path:
                        continue
                    if normalized_path == active_doc:
                        continue
                    if active_dir and normalized_path == active_dir:
                        continue
                    if active_dir and normalized_path.startswith(active_dir + "/"):
                        continue
                    out_of_scope.append(path)
                if out_of_scope:
                    critical_issues.append(f"{task_id}: docs-stage scope violation ({', '.join(out_of_scope[:3])})")

        if docs_enabled:
            metadata_raw = task.get("metadata")
            metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
            sections_raw = metadata.get("doc_sections")
            sections = sections_raw if isinstance(sections_raw, list) else []
            if sections and any(_normalize_text(item) for item in sections):
                docs_section_task_count += 1
            else:
                warnings.append(f"{task_id}: docs-stage task missing metadata.doc_sections")

    task_count = len(tasks)
    if task_count == 0:
        critical_issues.append("PM returned zero tasks")
    unique_ratio = len(seen_signatures) / float(task_count) if task_count > 0 else 1.0
    if task_count >= 2 and unique_ratio < 0.67:
        critical_issues.append(f"task list is overly repetitive (unique_signature_ratio={unique_ratio:.2f})")
    if task_count > 0 and low_action_count == task_count:
        critical_issues.append("all tasks are low-action/generic and not execution-ready")
    if task_count >= 2 and phase_count == 0:
        critical_issues.append("task list missing phase hints")
    if task_count >= 2 and checklist_task_count == 0:
        critical_issues.append("task list missing execution_checklist")
    if task_count >= 2 and dependency_task_count == 0:
        critical_issues.append("task list missing dependency chain")
    if task_count >= 2 and measurable_acceptance_task_count == 0:
        critical_issues.append("acceptance criteria are not measurable")
    if docs_enabled and task_count < 2:
        critical_issues.append("docs-stage decomposition requires at least 2 tasks")
    if docs_enabled and task_count >= 2 and docs_section_task_count == 0:
        critical_issues.append("docs-stage tasks missing metadata.doc_sections")
    if docs_enabled and task_count >= 2 and backlog_trace_task_count < max(1, task_count // 2):
        critical_issues.append("docs-stage tasks missing backlog traceability")
    if task_count >= 2:
        typed_tasks = [task for task in tasks if isinstance(task, dict)]
        try:
            validate_dependency_dag(typed_tasks)
        except DependencyCycleError as exc:
            critical_issues.append(f"circular dependency detected: {' -> '.join(exc.cycle)}")

    score = 100
    score -= min(60, len(critical_issues) * 12)
    score -= min(30, len(warnings) * 3)
    score = max(0, score)
    summary = (
        f"tasks={task_count}; critical={len(critical_issues)}; warnings={len(warnings)}; "
        f"unique_ratio={unique_ratio:.2f}; phase_tasks={phase_count}; dep_tasks={dependency_task_count}; "
        f"checklist_tasks={checklist_task_count}; measurable_accept_tasks={measurable_acceptance_task_count}; "
        f"doc_section_tasks={docs_section_task_count}; backlog_trace_tasks={backlog_trace_task_count}; score={score}"
    )
    return {
        "ok": len(critical_issues) == 0,
        "score": score,
        "task_count": task_count,
        "unique_signature_ratio": unique_ratio,
        "critical_issues": critical_issues,
        "warnings": warnings,
        "summary": summary,
    }


def autofix_pm_contract_for_quality(
    normalized: dict[str, Any],
    *,
    workspace_full: str,
) -> dict[str, int]:
    """Attempt to autofix PM contract quality issues.

    This function adds missing phases, checklists, dependencies, and acceptance criteria
    to tasks that lack them.

    Args:
        normalized: Normalized PM task payload
        workspace_full: Absolute path to workspace

    Returns:
        Statistics about what was added
    """
    from polaris.cells.orchestration.pm_planning.internal.shared_quality import detect_integration_verify_command

    tasks_raw = normalized.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    stats: dict[str, int] = {
        "task_count": len(tasks) if tasks else 0,
        "phases_added": 0,
        "checklists_added": 0,
        "deps_added": 0,
        "acceptance_added": 0,
        "descriptions_added": 0,
    }
    if not tasks:
        return stats

    verify_command = detect_integration_verify_command(workspace_full)
    normalized_tasks = [task for task in tasks if isinstance(task, dict)]
    has_dependency = False

    for index, task in enumerate(normalized_tasks, start=1):
        if not isinstance(task, dict):
            continue

        if not task.get("phase"):
            phases = ["requirements", "implementation", "verification"]
            phase = phases[(index - 1) % len(phases)]
            task["phase"] = phase
            stats["phases_added"] += 1

        if not task.get("execution_checklist"):
            task["execution_checklist"] = [
                "Read existing code and understand context",
                "Implement the required changes",
                "Run tests to verify correctness",
            ]
            stats["checklists_added"] += 1

        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task.get("acceptance")
        acceptance_items = acceptance if isinstance(acceptance, list) else []

        if not acceptance_items:
            task_type = str(task.get("type") or task.get("assigned_to") or "").lower()
            if "docs" in task_type or "document" in task_type:
                task["acceptance_criteria"] = [
                    "Documentation compiles without errors",
                    "All sections are present and properly formatted",
                ]
            else:
                task["acceptance_criteria"] = [
                    "Code compiles successfully",
                    f"Run `{verify_command}` passes",
                ]
            stats["acceptance_added"] += 1

        description = task.get("description")
        if not description or len(str(description).strip()) < 20:
            title = str(task.get("title") or "").strip()
            task["description"] = f"Execute {title} according to acceptance criteria"
            stats["descriptions_added"] += 1

    if len(normalized_tasks) > 1 and not has_dependency:
        prev_task_id = None
        for task in normalized_tasks:
            if prev_task_id:
                deps = task.get("depends_on")
                if not isinstance(deps, list):
                    deps = task.get("dependencies")
                deps = deps if isinstance(deps, list) else []
                if prev_task_id not in deps:
                    deps.append(prev_task_id)
                    if "depends_on" in task:
                        task["depends_on"] = deps
                    elif "dependencies" in task:
                        task["dependencies"] = deps
                    else:
                        task["depends_on"] = deps
                    stats["deps_added"] += 1
            prev_task_id = str(task.get("id") or "").strip()
            if not prev_task_id:
                task["id"] = f"TASK-{normalized_tasks.index(task) + 1}"
                prev_task_id = task["id"]

    normalized["tasks"] = normalized_tasks
    return stats


def check_quality_promote_candidate(
    quality_report: dict[str, Any],
    *,
    mode: str = "strict",
    min_score: int = 80,
    max_retries: int = 3,
    retry_count: int = 0,
) -> tuple[bool, str]:
    """Determine if a quality candidate should be promoted.

    Args:
        quality_report: Quality report from evaluate_pm_task_quality
        mode: Quality mode - "off", "warn", or "strict"
        min_score: Minimum score threshold
        max_retries: Maximum retry attempts
        retry_count: Current retry count

    Returns:
        Tuple of (should_promote, reason)
    """
    if mode == "off":
        return True, "quality gate disabled"

    is_ok = quality_report.get("ok", False)
    score = quality_report.get("score", 0)
    critical_count = len(quality_report.get("critical_issues", []))
    warning_count = len(quality_report.get("warnings", []))

    if mode == "strict":
        if not is_ok:
            return False, f"strict mode: {critical_count} critical issues found"
        if score < min_score:
            return False, f"strict mode: score {score} below minimum {min_score}"
        if critical_count > 0:
            return False, f"strict mode: {critical_count} critical issues"
        return True, f"strict mode: passed (score={score})"

    if mode == "warn":
        if not is_ok and retry_count < max_retries:
            return False, f"warn mode: retry needed ({critical_count} critical, {warning_count} warnings)"
        if not is_ok:
            return True, f"warn mode: forced promotion after {max_retries} retries"
        if score < min_score:
            return True, f"warn mode: score {score} below threshold but allowing"
        return True, f"warn mode: passed (score={score}, warnings={warning_count})"

    return True, f"unknown mode: {mode}"


def get_quality_gate_config() -> dict[str, Any]:
    """Get quality gate configuration from environment.

    Returns:
        Configuration dict with mode, min_score, and max_retries
    """
    import os

    mode = str(os.environ.get("KERNELONE_PM_TASK_QUALITY_MODE", "strict")).strip().lower()
    if mode not in ("off", "warn", "strict"):
        mode = "strict"

    min_score_raw = os.environ.get("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", "80")
    try:
        min_score = max(0, min(100, int(min_score_raw)))
    except ValueError:
        min_score = 80

    max_retries_raw = os.environ.get("KERNELONE_PM_TASK_QUALITY_RETRIES", "3")
    try:
        max_retries = max(0, int(max_retries_raw))
    except ValueError:
        max_retries = 3

    return {
        "mode": mode,
        "min_score": min_score,
        "max_retries": max_retries,
    }
