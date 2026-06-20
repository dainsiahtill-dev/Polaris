"""Generic, platform-clean PM task quality gate.

This module holds the domain-agnostic quality-gate entry points:
:func:`evaluate_pm_task_quality`, :func:`autofix_pm_contract_for_quality`,
:func:`check_quality_promote_candidate`, and :func:`get_quality_gate_config`.

The CLAUDE.md §8 game/card3d domain-contract behavior lives in
:mod:`domain_contracts` and is invoked from here only through that module's
``_domain_contracts_enabled``-gated detectors/synthesizers, so default behavior
is byte-identical to the original module. Bodies are moved verbatim (lossless
decomposition).
"""

from __future__ import annotations

import os
import re
from typing import Any

from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    DependencyCycleError,
    validate_dependency_dag,
)
from polaris.cells.orchestration.pm_planning.internal.quality_gate.domain_contracts import (
    _CARD3D_PM_REQUIRED_DOMAINS,
    _GAME_PM_MIN_TASKS,
    _GAME_PM_REQUIRED_DOMAINS,
    _append_missing_card3d_domain_tasks,
    _append_missing_game_domain_tasks,
    _attach_workspace_game_context_if_needed,
    _card3d_tests_task_has_placeholder_cleanup_contract,
    _covered_card3d_domains,
    _covered_game_domains,
    _has_forbidden_game_dependency_policy,
    _has_fragile_game_acceptance,
    _is_card3d_pm_contract,
    _is_game_pm_contract,
    _missing_card3d_required_test_targets,
    _primary_task_evidence_path,
    _remove_card3d_policy_incompatible_tasks_in_place,
    _remove_game_policy_incompatible_tasks_in_place,
    _repair_card3d_tests_task_contract,
    _sanitize_fragile_game_acceptance_in_place,
    _sanitize_game_dependency_policy_in_place,
)
from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
    _PM_ACTION_TOKENS,
    _append_deterministic_scaffold_residue_cleanup_task,
    _collect_task_scope_paths,
    _contains_prompt_leakage,
    _dedupe_text_items,
    _drop_unknown_dependency_refs_in_place,
    _has_executable_or_file_acceptance_anchor,
    _has_measurable_acceptance_anchor,
    _has_placeholder_or_manifest_only_acceptance,
    _is_concrete_pm_scope_path,
    _is_directory_scope_evidenced,
    _is_file_like_pm_scope_path,
    _is_workspace_bound_concrete_path,
    _normalize_dep_list,
    _normalize_dependency_refs_in_place,
    _normalize_path,
    _normalize_path_list,
    _normalize_text,
    _sanitize_pm_task_paths_in_place,
    _steer_single_file_ui_tasks_in_place,
    _strip_unfulfillable_vendored_targets_in_place,
    _title_is_too_short,
    _unknown_dependency_refs,
)

_PM_CONTRACT_GOVERNANCE_EXACT_MARKERS = (
    "补全文件级路径",
    "调整输出格式",
    "上一版 PM 合同未通过质量门禁",
    "每个任务必须含 goal/scope/steps/acceptance",
)
_PM_CONTRACT_PATH_FIELD_RE = re.compile(r"(?:scope_paths|target_files)", re.IGNORECASE)
_PM_CONTRACT_JSON_OUTPUT_RE = re.compile(r"仅输出\s*JSON|JSON\s*对象", re.IGNORECASE)
_PM_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
_PM_DOCUMENTATION_ONLY_SUFFIXES = (".md", ".markdown", ".mdx", ".txt")


def _contains_pm_contract_governance_task(text: str) -> bool:
    source = _normalize_text(text)
    if not source:
        return False
    lower = source.lower()
    if any(marker in source for marker in _PM_CONTRACT_GOVERNANCE_EXACT_MARKERS):
        return True
    if _PM_CONTRACT_PATH_FIELD_RE.search(source) and (
        "director" in lower
        or "chiefengineer" in lower
        or "chief engineer" in lower
        or "真实相对路径" in source
        or "自然语言描述" in source
    ):
        return any(marker in source for marker in ("必须", "禁止", "requires", "require"))
    if _PM_CONTRACT_JSON_OUTPUT_RE.search(source):
        return any(marker in lower for marker in ("markdown", "代码块", "额外文字", "只输出", "禁止"))
    return False


def _extract_declared_scope_paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    for match in _PM_BACKTICK_PATH_RE.finditer(text or ""):
        raw = match.group(1)
        for item in re.split(r"[\s,，、]+", raw):
            normalized = _normalize_path(item)
            if not normalized or normalized in paths:
                continue
            if _is_concrete_pm_scope_path(normalized):
                paths.append(normalized)
    return paths


def _task_paths_cover_declared_scope(declared_path: str, task_paths: list[str]) -> bool:
    declared = _normalize_path(declared_path)
    if not declared:
        return True
    normalized_paths = [_normalize_path(path) for path in task_paths if _normalize_path(path)]
    if declared in normalized_paths:
        return True
    declared_prefix = f"{declared}/"
    if any(path.startswith(declared_prefix) for path in normalized_paths):
        return True
    declared_parent = _normalize_path(os.path.dirname(declared))
    return bool(declared_parent and declared_parent in normalized_paths)


def _file_scope_paths_missing_from_targets(task: dict[str, Any]) -> list[str]:
    target_files = [
        _normalize_path(path)
        for path in _normalize_path_list(task.get("target_files") or [])
        if _is_file_like_pm_scope_path(path)
    ]
    if not target_files:
        return []
    target_set = set(target_files)
    missing: list[str] = []
    for path in _normalize_path_list(task.get("scope_paths") or []):
        normalized = _normalize_path(path)
        if not normalized or normalized in target_set or not _is_file_like_pm_scope_path(normalized):
            continue
        if normalized not in missing:
            missing.append(normalized)
    return missing


def _is_documentation_only_task_scope(task: dict[str, Any]) -> bool:
    paths = _collect_task_scope_paths(task)
    file_paths = [_normalize_path(path) for path in paths if _is_file_like_pm_scope_path(path)]
    if not file_paths:
        return False
    for path in file_paths:
        basename = os.path.basename(path).lower()
        if basename.startswith("readme"):
            continue
        if path.endswith(_PM_DOCUMENTATION_ONLY_SUFFIXES):
            continue
        return False
    return True


def evaluate_pm_task_quality(
    normalized: dict[str, Any],
    docs_stage: dict[str, Any] | None = None,
    workspace_full: str | None = None,
) -> dict[str, Any]:
    """Evaluate PM task quality and return quality report.

    Args:
        normalized: Normalized PM task payload with 'tasks' key
        docs_stage: Optional docs stage configuration
        workspace_full: Optional current workspace root. Falls back to
            normalized["workspace"] when available.

    Returns:
        Quality report with score, issues, warnings, and summary
    """
    tasks_raw = normalized.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    task_count = len(tasks)
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
    effective_workspace = str(workspace_full or normalized.get("workspace") or "").strip()
    docs_enabled = bool(docs_stage_dict.get("enabled"))
    active_doc = _normalize_path(docs_stage_dict.get("active_doc_path", ""))
    active_dir = _normalize_path(os.path.dirname(active_doc)) if active_doc else ""
    is_card3d_contract = _is_card3d_pm_contract(normalized, tasks)
    is_game_contract = _is_game_pm_contract(normalized, tasks)

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

        if _title_is_too_short(title):
            warnings.append(f"{task_id}: title is too short")
        if len(goal) < 18:
            warnings.append(f"{task_id}: goal is too short")
        if _contains_prompt_leakage(combined_text):
            critical_issues.append(f"{task_id}: detected role/prompt leakage markers in task content")
        if _contains_pm_contract_governance_task(combined_text):
            critical_issues.append(f"{task_id}: task describes PM contract governance instead of product delivery")
        if (is_card3d_contract or is_game_contract) and _has_forbidden_game_dependency_policy(task):
            critical_issues.append(f"{task_id}: game task violates no-external-dependency policy")

        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task.get("acceptance")
        acceptance_items = [_normalize_text(item) for item in (acceptance or []) if _normalize_text(item)]
        if not acceptance_items:
            critical_issues.append(f"{task_id}: acceptance criteria is missing")
        elif _has_placeholder_or_manifest_only_acceptance(acceptance_items):
            critical_issues.append(f"{task_id}: acceptance permits placeholder or manifest-only execution")
        elif _has_fragile_game_acceptance(acceptance_items):
            critical_issues.append(f"{task_id}: acceptance uses fragile random-sequence assertions")
        elif not _has_executable_or_file_acceptance_anchor(acceptance_items):
            critical_issues.append(f"{task_id}: acceptance requires executable command or file evidence")
        elif not _has_measurable_acceptance_anchor(acceptance_items):
            warnings.append(f"{task_id}: acceptance criteria lacks measurable anchors")
        else:
            measurable_acceptance_task_count += 1

        task_paths = _collect_task_scope_paths(task)
        concrete_workspace_paths = [
            path for path in task_paths if _is_workspace_bound_concrete_path(path, effective_workspace)
        ]
        invalid_scope_paths = [path for path in task_paths if path not in concrete_workspace_paths]
        evidenced_directory_paths = [
            path
            for path in invalid_scope_paths
            if _is_directory_scope_evidenced(path, concrete_workspace_paths, effective_workspace)
        ]
        if evidenced_directory_paths:
            concrete_workspace_paths.extend(evidenced_directory_paths)
            invalid_scope_paths = [path for path in invalid_scope_paths if path not in evidenced_directory_paths]
        if not task_paths:
            critical_issues.append(f"{task_id}: task requires explicit scope")
        elif not concrete_workspace_paths:
            critical_issues.append(f"{task_id}: task requires concrete workspace-bound scope paths")
        elif invalid_scope_paths:
            critical_issues.append(
                f"{task_id}: scope paths must stay inside workspace ({', '.join(invalid_scope_paths[:3])})"
            )
        declared_scope_paths = _extract_declared_scope_paths_from_text(combined_text)
        missing_declared_scope_paths = [
            path for path in declared_scope_paths if not _task_paths_cover_declared_scope(path, task_paths)
        ]
        if missing_declared_scope_paths:
            critical_issues.append(
                f"{task_id}: described scope paths missing from target_files/scope_paths "
                f"({', '.join(missing_declared_scope_paths[:3])})"
            )

        checklist = task.get("execution_checklist")
        checklist_items = []
        if isinstance(checklist, list):
            checklist_items = [_normalize_text(item) for item in checklist if _normalize_text(item)]

        action_text = " ".join([combined_text, " ".join(checklist_items)]).strip()
        lowered_task = action_text.lower()
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

        if checklist_items:
            checklist_task_count += 1
        else:
            warnings.append(f"{task_id}: missing execution_checklist")

        if backlog_ref:
            backlog_trace_task_count += 1

        assigned_to = str(task.get("assigned_to") or "").strip()
        assigned_key = assigned_to.lower().replace("-", "_").replace(" ", "_")
        if assigned_key in {"director", "chiefengineer", "chief_engineer"}:
            if acceptance_items and not _has_executable_or_file_acceptance_anchor(acceptance_items):
                critical_issues.append(
                    f"{task_id}: assignee {assigned_to or assigned_key} requires executable command or file evidence in acceptance"
                )
            concrete_task_paths = [path for path in task_paths if _is_concrete_pm_scope_path(path)]
            non_path_entries = [
                path
                for path in task_paths
                if path not in concrete_task_paths
                and not _is_directory_scope_evidenced(path, concrete_task_paths, effective_workspace)
            ]
            if task_paths and not concrete_task_paths:
                critical_issues.append(f"{task_id}: assignee {assigned_to} requires concrete relative scope paths")
            elif non_path_entries:
                warnings.append(f"{task_id}: non-path scope entries ignored ({', '.join(non_path_entries[:2])})")
            if (
                assigned_key == "director"
                and index == 1
                and task_count >= 2
                and not docs_enabled
                and _is_documentation_only_task_scope(task)
            ):
                critical_issues.append(f"{task_id}: first product-delivery Director task cannot be documentation-only")
            elif docs_enabled and active_doc:
                out_of_scope: list[str] = []
                for path in concrete_task_paths:
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
            if assigned_key == "director" and "target_files" in task:
                target_files = _normalize_path_list(task.get("target_files") or [])
                file_targets = [path for path in target_files if _is_file_like_pm_scope_path(path)]
                file_scopes = [path for path in concrete_task_paths if _is_file_like_pm_scope_path(path)]
                if not file_targets and not file_scopes:
                    critical_issues.append(f"{task_id}: Director task requires file-level target_files or scope_paths")
                missing_file_scope_targets = _file_scope_paths_missing_from_targets(task)
                if missing_file_scope_targets:
                    critical_issues.append(
                        f"{task_id}: file-level scope_paths missing from target_files "
                        f"({', '.join(missing_file_scope_targets[:3])})"
                    )

        if docs_enabled:
            metadata_raw = task.get("metadata")
            metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
            sections_raw = metadata.get("doc_sections")
            sections = sections_raw if isinstance(sections_raw, list) else []
            if sections and any(_normalize_text(item) for item in sections):
                docs_section_task_count += 1
            else:
                warnings.append(f"{task_id}: docs-stage task missing metadata.doc_sections")

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
    if is_card3d_contract:
        if task_count < _GAME_PM_MIN_TASKS:
            critical_issues.append(f"card3d PM decomposition requires at least {_GAME_PM_MIN_TASKS} tasks")
        covered_domains = _covered_card3d_domains(tasks, effective_workspace)
        missing_domains = [domain for domain in _CARD3D_PM_REQUIRED_DOMAINS if domain not in covered_domains]
        if missing_domains:
            critical_issues.append(f"card3d PM decomposition missing domains: {', '.join(missing_domains)}")
        missing_test_targets = _missing_card3d_required_test_targets(tasks)
        if missing_test_targets:
            critical_issues.append(
                "card3d tests task must target all required test files: " + ", ".join(missing_test_targets)
            )
        if (
            "tests" not in missing_domains
            and not missing_test_targets
            and not _card3d_tests_task_has_placeholder_cleanup_contract(tasks)
        ):
            critical_issues.append(
                "card3d tests task must require replacing/removing trivial arithmetic placeholder tests"
            )
    elif is_game_contract:
        if task_count < _GAME_PM_MIN_TASKS:
            critical_issues.append(f"game PM decomposition requires at least {_GAME_PM_MIN_TASKS} tasks")
        covered_domains = _covered_game_domains(tasks, effective_workspace)
        missing_domains = [domain for domain in _GAME_PM_REQUIRED_DOMAINS if domain not in covered_domains]
        if missing_domains:
            critical_issues.append(f"game PM decomposition missing domains: {', '.join(missing_domains)}")
    if task_count >= 2:
        typed_tasks = [task for task in tasks if isinstance(task, dict)]
        critical_issues.extend(_unknown_dependency_refs(typed_tasks))
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
        "deps_normalized": 0,
        "acceptance_added": 0,
        "acceptance_hardened": 0,
        "acceptance_sanitized": 0,
        "descriptions_added": 0,
        "game_domain_tasks_added": 0,
        "card3d_domain_tasks_added": 0,
        "card3d_test_contract_repairs": 0,
        "game_context_attached": 0,
        "game_dependency_policy_sanitized": 0,
        "game_policy_tasks_removed": 0,
        "paths_normalized": 0,
        "seed_residue_cleanup_tasks_added": 0,
        "vendored_targets_stripped": 0,
        "single_file_ui_tasks_steered": 0,
    }
    if not tasks:
        return stats

    verify_command = detect_integration_verify_command(workspace_full)
    normalized_tasks = [task for task in tasks if isinstance(task, dict)]
    stats["paths_normalized"] += _sanitize_pm_task_paths_in_place(normalized_tasks, workspace_full)
    stats["vendored_targets_stripped"] += _strip_unfulfillable_vendored_targets_in_place(normalized_tasks)
    stats["single_file_ui_tasks_steered"] += _steer_single_file_ui_tasks_in_place(normalized_tasks)
    if _attach_workspace_game_context_if_needed(normalized, normalized_tasks, workspace_full):
        stats["game_context_attached"] += 1
    is_card3d_contract = _is_card3d_pm_contract(normalized, normalized_tasks)
    if is_card3d_contract:
        normalized.setdefault("_quality_gate_card3d_context", "card3d task_or_workspace_hints")
        stats["game_policy_tasks_removed"] += _remove_card3d_policy_incompatible_tasks_in_place(
            normalized_tasks,
            workspace_full,
        )
        if stats["game_policy_tasks_removed"]:
            stats["deps_normalized"] += _drop_unknown_dependency_refs_in_place(normalized_tasks)
        for task in normalized_tasks:
            stats["game_dependency_policy_sanitized"] += _sanitize_game_dependency_policy_in_place(task, verify_command)
    elif _is_game_pm_contract(normalized, normalized_tasks):
        stats["game_policy_tasks_removed"] += _remove_game_policy_incompatible_tasks_in_place(
            normalized_tasks,
            workspace_full,
        )
        if stats["game_policy_tasks_removed"]:
            stats["deps_normalized"] += _drop_unknown_dependency_refs_in_place(normalized_tasks)
        for task in normalized_tasks:
            stats["game_dependency_policy_sanitized"] += _sanitize_game_dependency_policy_in_place(task, verify_command)
    stats["deps_normalized"] += _normalize_dependency_refs_in_place(normalized_tasks)
    stats["card3d_domain_tasks_added"] += _append_missing_card3d_domain_tasks(
        normalized,
        normalized_tasks,
        workspace_full=workspace_full,
        verify_command=verify_command,
    )
    if is_card3d_contract:
        stats["card3d_test_contract_repairs"] += _repair_card3d_tests_task_contract(normalized_tasks)
    stats["game_domain_tasks_added"] += _append_missing_game_domain_tasks(
        normalized,
        normalized_tasks,
        workspace_full=workspace_full,
        verify_command=verify_command,
    )
    stats["task_count"] = len(normalized_tasks)
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

        stats["acceptance_sanitized"] += _sanitize_fragile_game_acceptance_in_place(task, verify_command)
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
        elif not _has_executable_or_file_acceptance_anchor([str(item) for item in acceptance_items]):
            evidence_path = _primary_task_evidence_path(task, workspace_full)
            evidence_ref = evidence_path if "/" in evidence_path else f"./{evidence_path}"
            task["acceptance_criteria"] = _dedupe_text_items(
                [*[str(item) for item in acceptance_items if str(item).strip()], f"verify {evidence_ref} exists"]
            )
            stats["acceptance_hardened"] += 1

        description = task.get("description")
        if not description or len(str(description).strip()) < 20:
            title = str(task.get("title") or "").strip()
            task["description"] = f"Execute {title} according to acceptance criteria"
            stats["descriptions_added"] += 1

        deps = task.get("depends_on")
        if not isinstance(deps, list):
            deps = task.get("dependencies")
        if _normalize_dep_list(deps):
            has_dependency = True

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

    stats["seed_residue_cleanup_tasks_added"] += _append_deterministic_scaffold_residue_cleanup_task(
        normalized,
        normalized_tasks,
        workspace_full=workspace_full,
        verify_command=verify_command,
    )
    stats["task_count"] = len(normalized_tasks)
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
