"""Director runtime public service — _execution submodule."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping, cast

from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    RepairDiagnostic,
    RepairReceipt,
)
from polaris.cells.director.runtime.internal.repair_kernel.environment import (
    environment_prep_plans_from_requirements,
    environment_refresh_requirements_from_receipts,
)
from polaris.cells.director.runtime.internal.repair_kernel.receipt_projection import (
    build_repair_kernel_result_summary as _build_repair_kernel_result_summary,
)
from polaris.cells.director.runtime.internal.repair_kernel.registry import (
    build_repair_coverage_report,
    default_repair_rule_registry,
    repair_language_slots,
)
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    plan_runtime_repair,
    run_runtime_repair,
    runtime_repair_bindings,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.schedule_catalog import (
    DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
    MaterializationQualityRepairScheduleStep,
    PostExecutionRepairScheduleStep,
    materialization_quality_repair_schedule,
    post_execution_repair_schedule,
    run_materialization_quality_repair_schedule_callbacks,
    run_post_execution_repair_schedule_callbacks,
)
from polaris.cells.director.runtime.internal.repair_kernel.scheduler import RepairVerifierSnapshot
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairCallbackReceiptProjectionV1,
    DirectorRepairConvergenceResultV1,
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairCoverageReportV1,
    DirectorRepairDiagnosticCoverageV1,
    DirectorRepairKernelSummaryProjectionResultV1,
    DirectorRepairLanguageSlotsResultV1,
    DirectorRepairLanguageSlotV1,
    DirectorRepairMaterializationAllowedPathsResultV1,
    DirectorRepairMaterializationBridgeMetadataResultV1,
    DirectorRepairMaterializationPlanProbeResultV1,
    DirectorRepairMaterializationQualityFacadeResultV1,
    DirectorRepairMaterializationQualityScheduleResultV1,
    DirectorRepairMaterializationQualityScheduleRunResultV1,
    DirectorRepairMaterializationQualityStepV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPlanProbeItemV1,
    DirectorRepairPlanProbeResultV1,
    DirectorRepairPostExecutionScheduleResultV1,
    DirectorRepairPostExecutionScheduleRunResultV1,
    DirectorRepairPostExecutionStepV1,
    DirectorRepairResultV1,
    DirectorRepairVerifierSnapshotInputV1,
    DirectorTaskBoundaryQualityResultV1,
    PlanDirectorRepairCommandV1,
    ProjectDirectorRepairKernelSummaryV1,
    ProjectDirectorRepairMaterializationBridgeMetadataV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
    QueryDirectorRepairMaterializationAllowedPathsV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairPlanProbeV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
    RunDirectorRepairConvergenceCommandV1,
    RunDirectorTaskBoundaryQualityLoopCommandV1,
)
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

from ._core import (
    DeleteFileFn,
    DirectorRepairConvergenceVerifierFn,
    DirectorRepairRevalidatorFn,
    EditFileFn,
    MaterializationQualityStepRunnerV1,
    PostExecutionStepRunnerV1,
    WriteFileFn,
    _count_by_key,
    _ordered_unique,
    _PublicConvergenceVerifierError,
    _repair_execution_error_code,
)
from ._projections import (
    _attach_native_revalidation_evidence,
    _environment_prep_receipts_from_public_repair_receipts,
    _failed_public_convergence_result,
    _public_repair_diagnostics_from_command,
    _repair_diagnostics_from_quality_inputs,
    _runtime_artifact_quality_errors_from_command,
    _task_boundary_quality_metadata,
    _to_internal_advisor_notes,
    _to_internal_repair_diagnostic,
    _to_public_convergence_result,
    _to_public_environment_prep_plan,
    _to_public_repair_diagnostic,
    _to_public_repair_planning_result,
    _to_public_repair_receipt,
    _validate_public_convergence_verifier_evidence,
)


def query_director_repair_coverage(query: QueryDirectorRepairCoverageV1) -> DirectorRepairCoverageReportV1:
    """Return read-only repair-rule coverage for raw artifact-quality errors."""

    diagnostics = _repair_diagnostics_from_quality_inputs(query.artifact_quality_errors, query.artifact_quality_issues)
    report = build_repair_coverage_report(diagnostics)
    coverage_gaps_by_id = {
        str(gap.get("diagnostic_id") or ""): dict(gap)
        for gap in report.coverage_gaps
        if str(gap.get("diagnostic_id") or "").strip()
    }
    return DirectorRepairCoverageReportV1(
        schema_version="director.repair_coverage_report.v1",
        source="director.runtime.repair_kernel.registry",
        access="read_only",
        total_diagnostics=report.total_diagnostics,
        covered_diagnostic_count=report.covered_diagnostic_count,
        uncovered_diagnostic_count=report.uncovered_diagnostic_count,
        executable_runtime_plan_diagnostic_count=report.executable_runtime_plan_diagnostic_count,
        metadata_only_diagnostic_count=report.metadata_only_diagnostic_count,
        items=tuple(_project_director_repair_diagnostic_coverage(item, coverage_gaps_by_id) for item in report.items),
    )


def query_director_repair_plan_probe(query: QueryDirectorRepairPlanProbeV1) -> DirectorRepairPlanProbeResultV1:
    """Return read-only evidence that coverage-matched rules can produce concrete patches."""

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            query.artifact_quality_errors,
            artifact_quality_issues=query.artifact_quality_issues,
        )
    )
    candidate_source_tools = _plan_probe_candidate_source_tools(coverage, requested_source_tools=query.source_tools)
    probe_items: list[DirectorRepairPlanProbeItemV1] = []
    for source_tool in candidate_source_tools:
        matched_items = _coverage_items_for_source_tool(coverage, source_tool)
        matched_diagnostics = tuple(_repair_diagnostic_from_coverage_item(item) for item in matched_items)
        planning = plan_director_repair(
            PlanDirectorRepairCommandV1(
                source_tool=source_tool,
                base_files=query.base_files,
                diagnostics=matched_diagnostics,
                mode=query.mode,
                advisor_notes=query.advisor_notes,
                metadata={
                    **dict(query.metadata),
                    "public_entrypoint": "query_director_repair_plan_probe",
                    "read_only_plan_probe": True,
                },
            )
        )
        composition = planning.composition_summary.to_dict()
        patch_count = int(composition.get("patch_count") or 0)
        changed_paths = tuple(str(path) for path in composition.get("changed_paths") or ())
        status = _plan_probe_item_status(
            planning=planning,
            matched_diagnostic_count=len(matched_items),
            patch_count=patch_count,
            changed_paths=changed_paths,
        )
        probe_items.append(
            DirectorRepairPlanProbeItemV1(
                source_tool=source_tool,
                status=status,
                matched_diagnostic_ids=tuple(str(item.diagnostic.get("diagnostic_id") or "") for item in matched_items),
                matched_diagnostic_count=len(matched_items),
                patch_count=patch_count,
                changed_paths=changed_paths,
                planning_result=planning,
                error_code=planning.error_code,
                error_message=planning.error_message,
                metadata={
                    "coverage_status": "matched" if matched_items else "not_covered_by_source_tool",
                    "changed_patch_count": len(changed_paths),
                    "no_op_patch_count": max(0, patch_count - len(changed_paths)),
                    "plannable_requires_changed_patch": True,
                    "read_only_plan_probe": True,
                },
            )
        )

    plannable_source_tools = tuple(item.source_tool for item in probe_items if item.status == "covered_plannable")
    plannable_set = set(plannable_source_tools)
    covered_unplannable_diagnostics = tuple(
        dict(item.diagnostic)
        for item in coverage.items
        if _coverage_item_is_covered_unplannable(
            item,
            candidate_source_tools=candidate_source_tools,
            plannable_source_tools=plannable_set,
        )
    )
    covered_unplannable_source_tools = tuple(
        item.source_tool
        for item in probe_items
        if item.status not in {"covered_plannable", "not_covered_by_source_tool"}
    )
    uncovered_diagnostics = tuple(dict(item.diagnostic) for item in coverage.items if not item.known_rule_matched)
    status = _plan_probe_result_status(
        coverage=coverage,
        plannable_source_tools=plannable_source_tools,
        covered_unplannable_diagnostics=covered_unplannable_diagnostics,
        uncovered_diagnostics=uncovered_diagnostics,
    )
    return DirectorRepairPlanProbeResultV1(
        status=status,
        coverage_report=coverage,
        items=tuple(probe_items),
        plannable_source_tools=plannable_source_tools,
        covered_unplannable_source_tools=tuple(_ordered_unique(covered_unplannable_source_tools)),
        covered_unplannable_diagnostics=covered_unplannable_diagnostics,
        uncovered_diagnostics=uncovered_diagnostics,
        metadata={
            "public_entrypoint": "query_director_repair_plan_probe",
            "coverage_is_not_planning": True,
            "read_only_plan_probe": True,
            "candidate_source_tools": list(candidate_source_tools),
            "requested_source_tools": list(query.source_tools),
            "plannable_source_tool_count": len(plannable_source_tools),
            "covered_unplannable_diagnostic_count": len(covered_unplannable_diagnostics),
            "coverage_gap_count": len(uncovered_diagnostics),
        },
    )


def query_director_repair_materialization_allowed_paths(
    query: QueryDirectorRepairMaterializationAllowedPathsV1,
) -> DirectorRepairMaterializationAllowedPathsResultV1:
    """Return runtime-owned allowed paths for a materialization repair plan."""

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=query.source_tool,
            base_files=query.base_files,
            artifact_quality_errors=query.artifact_quality_errors,
            artifact_quality_issues=query.artifact_quality_issues,
            mode=query.mode,
            metadata={
                **dict(query.metadata),
                "public_entrypoint": "query_director_repair_materialization_allowed_paths",
                "read_only_allowed_paths_plan": True,
            },
        )
    )
    composition = planning.composition_summary.to_dict()
    changed_paths = tuple(str(path) for path in composition.get("changed_paths") or () if str(path or "").strip())
    base_paths = tuple(str(path) for path in query.base_files if str(path or "").strip())
    allowed_paths = _ordered_unique((*base_paths, *changed_paths))
    return DirectorRepairMaterializationAllowedPathsResultV1(
        source_tool=query.source_tool,
        planning_result=planning,
        base_paths=_ordered_unique(base_paths),
        changed_paths=_ordered_unique(changed_paths),
        allowed_paths=allowed_paths,
        metadata={
            "public_entrypoint": "query_director_repair_materialization_allowed_paths",
            "read_only_allowed_paths_plan": True,
            "base_path_count": len(base_paths),
            "changed_path_count": len(changed_paths),
            "allowed_path_count": len(allowed_paths),
        },
    )


def query_director_repair_materialization_plan_probe(
    query: QueryDirectorRepairMaterializationPlanProbeV1,
) -> DirectorRepairMaterializationPlanProbeResultV1:
    """Return materialization source tools proven by runtime coverage and planning."""

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            query.artifact_quality_errors,
            artifact_quality_issues=query.artifact_quality_issues,
        )
    )
    schedule_source_tools = _materialization_plan_probe_source_tools(step_id=query.step_id)
    requested_source_tools = _ordered_unique(query.source_tools) or schedule_source_tools
    if not query.artifact_quality_errors and not query.artifact_quality_issues:
        return DirectorRepairMaterializationPlanProbeResultV1(
            status="already_clean",
            coverage_report=coverage,
            requested_source_tools=requested_source_tools,
            base_file_count=len(query.base_files),
            metadata={
                **dict(query.metadata),
                "public_entrypoint": "query_director_repair_materialization_plan_probe",
                "read_only_plan_probe": True,
                "materialization_step_id": query.step_id,
                "materialization_schedule_source_tools": list(schedule_source_tools),
            },
        )
    candidate_source_tools = _materialization_candidate_source_tools_from_coverage(
        coverage,
        requested_source_tools=requested_source_tools,
    )
    if not candidate_source_tools and query.fallback_to_step_source_tools and coverage.total_diagnostics > 0:
        candidate_source_tools = requested_source_tools
    if not candidate_source_tools:
        status = (
            "coverage_gap_uncovered_diagnostics"
            if int(coverage.uncovered_diagnostic_count or 0) > 0
            else "stuck_no_materialization_runtime_source_tool"
        )
        return DirectorRepairMaterializationPlanProbeResultV1(
            status=status,
            coverage_report=coverage,
            requested_source_tools=requested_source_tools,
            candidate_source_tools=(),
            base_file_count=len(query.base_files),
            metadata={
                **dict(query.metadata),
                "public_entrypoint": "query_director_repair_materialization_plan_probe",
                "read_only_plan_probe": True,
                "coverage_is_not_planning": True,
                "materialization_step_id": query.step_id,
                "materialization_schedule_source_tools": list(schedule_source_tools),
                "fallback_to_step_source_tools": query.fallback_to_step_source_tools,
            },
        )
    plan_probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=query.artifact_quality_errors,
            artifact_quality_issues=query.artifact_quality_issues,
            base_files=query.base_files,
            source_tools=candidate_source_tools,
            mode=query.mode,
            advisor_notes=query.advisor_notes,
            metadata={
                **dict(query.metadata),
                "public_entrypoint": "query_director_repair_materialization_plan_probe",
                "read_only_plan_probe": True,
                "coverage_is_not_planning": True,
                "materialization_step_id": query.step_id,
                "materialization_schedule_source_tools": list(schedule_source_tools),
                "fallback_to_step_source_tools": query.fallback_to_step_source_tools,
            },
        )
    )
    return DirectorRepairMaterializationPlanProbeResultV1(
        status=plan_probe.status,
        coverage_report=coverage,
        plan_probe_result=plan_probe,
        requested_source_tools=requested_source_tools,
        candidate_source_tools=candidate_source_tools,
        plannable_source_tools=plan_probe.plannable_source_tools,
        base_file_count=len(query.base_files),
        metadata={
            **dict(query.metadata),
            "public_entrypoint": "query_director_repair_materialization_plan_probe",
            "read_only_plan_probe": True,
            "coverage_is_not_planning": True,
            "materialization_step_id": query.step_id,
            "materialization_schedule_source_tools": list(schedule_source_tools),
            "fallback_to_step_source_tools": query.fallback_to_step_source_tools,
            "candidate_source_tool_count": len(candidate_source_tools),
            "plannable_source_tool_count": len(plan_probe.plannable_source_tools),
        },
    )


def _materialization_candidate_source_tools_from_coverage(
    coverage: DirectorRepairCoverageReportV1,
    *,
    requested_source_tools: Sequence[str],
) -> tuple[str, ...]:
    requested = set(_ordered_unique(requested_source_tools))
    candidates: list[str] = []
    for item in coverage.items:
        if not item.executable_runtime_plan_matched:
            continue
        for source_tool in item.matched_source_tools:
            if source_tool in requested:
                candidates.append(source_tool)
    return _ordered_unique(candidates)


def _materialization_plan_probe_source_tools(*, step_id: str | None = None) -> tuple[str, ...]:
    steps = materialization_quality_repair_schedule()
    selected_steps = tuple(step for step in steps if step.step_id == step_id) if step_id else steps
    source_tools: list[str] = []
    for step in selected_steps:
        source_tools.extend(step.runtime_source_tools)
    return _ordered_unique(tuple(source_tools))


def project_director_repair_materialization_bridge_metadata(
    command: ProjectDirectorRepairMaterializationBridgeMetadataV1,
) -> DirectorRepairMaterializationBridgeMetadataResultV1:
    """Project materialization runtime-port metadata through the Director Runtime public boundary."""

    repair_kernel = dict(command.repair_kernel)
    coverage_preaudit = dict(command.coverage_preaudit)
    plan_probe_preaudit = dict(command.plan_probe_preaudit)
    materialization_runtime_probe = dict(plan_probe_preaudit.get("runtime_plan_probe") or {})
    repair_runtime_probe = dict(materialization_runtime_probe.get("runtime_plan_probe") or {})
    scheduler_bridge_evidence = dict(command.scheduler_bridge_evidence)
    repair_kernel_migration_debt = dict(command.repair_kernel_migration_debt)
    receipt_lifecycle_by_step = dict(command.receipt_lifecycle_by_step)
    dark_launch_comparison = dict(command.dark_launch_comparison)
    schedule_reconciliation = dict(command.schedule_reconciliation)
    covered_unplannable_count = _first_mapping_value(
        (plan_probe_preaudit, materialization_runtime_probe, repair_runtime_probe),
        key="covered_unplannable_diagnostic_count",
        default=0,
    )
    covered_unplannable_source_tools = _first_mapping_value(
        (plan_probe_preaudit, materialization_runtime_probe, repair_runtime_probe),
        key="covered_unplannable_source_tools",
        default=[],
    )
    summary = {
        "schema_version": "director.materialization_quality_runtime_ports.v1",
        "mode": "runtime_schedule_step_runner_adapter",
        "runtime_ports_module": "roles.adapters.internal.director.materialization_quality_runtime_ports",
        "adapter_strategy_host_removed": True,
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "ordered_step_ids": [step.step_id for step in command.ordered_steps],
        "runner_step_ids": list(schedule_reconciliation.get("runner_step_ids") or ()),
        "runner_binding_reconciliation": schedule_reconciliation,
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
        "director_runtime_public_summary_entrypoint": "project_director_repair_materialization_bridge_metadata",
        "scheduler_bridge_summary_owner": "director.runtime",
        "scheduler_bridge_evidence_source": "roles.adapters",
        "convergence_verifier_present": command.convergence_verifier_present,
        "receipt_count": repair_kernel.get("receipt_count", 0),
        "scheduler_bridge": scheduler_bridge_evidence,
        "repair_kernel_migration_debt": repair_kernel_migration_debt,
        "adapter_projection_debt": list(repair_kernel_migration_debt.get("adapter_projection_debt") or ()),
        "receipt_lifecycle_by_step": receipt_lifecycle_by_step,
        "coverage_preaudit_uncovered_diagnostic_count": coverage_preaudit.get("uncovered_diagnostic_count", 0),
        "coverage_preaudit_rule_discovery_required": coverage_preaudit.get("rule_discovery_required", False),
        "plan_probe_status": plan_probe_preaudit.get("status"),
        "plan_probe_covered_unplannable_diagnostic_count": covered_unplannable_count,
        "plan_probe_plannable_source_tools": plan_probe_preaudit.get("plannable_source_tools", []),
        "plan_probe_covered_unplannable_source_tools": covered_unplannable_source_tools,
        "dark_launch_cutover_ready": dark_launch_comparison.get("cutover_ready"),
        "dark_launch_cutover_blockers": dark_launch_comparison.get("cutover_blockers"),
        "coverage_uncovered_diagnostic_count": dict(repair_kernel.get("coverage_report") or {}).get(
            "uncovered_diagnostic_count",
            0,
        ),
    }
    return DirectorRepairMaterializationBridgeMetadataResultV1(summary=summary)


def _first_mapping_value(
    mappings: Sequence[Mapping[str, Any]],
    *,
    key: str,
    default: Any,
) -> Any:
    for mapping in mappings:
        if key in mapping:
            return mapping[key]
    return default


def _plan_probe_candidate_source_tools(
    coverage: DirectorRepairCoverageReportV1,
    *,
    requested_source_tools: Sequence[str],
) -> tuple[str, ...]:
    requested = _ordered_unique(tuple(str(item or "").strip() for item in requested_source_tools))
    if requested:
        return requested
    executable_source_tools = set(runtime_repair_source_tools())
    source_tools: list[str] = []
    for item in coverage.items:
        if not item.executable_runtime_plan_matched:
            continue
        source_tools.extend(
            source_tool for source_tool in item.matched_source_tools if source_tool in executable_source_tools
        )
    return tuple(_ordered_unique(source_tools))


def _coverage_items_for_source_tool(
    coverage: DirectorRepairCoverageReportV1,
    source_tool: str,
) -> tuple[DirectorRepairDiagnosticCoverageV1, ...]:
    return tuple(item for item in coverage.items if source_tool in item.matched_source_tools)


def _repair_diagnostic_from_coverage_item(item: DirectorRepairDiagnosticCoverageV1) -> RepairDiagnosticV1:
    diagnostic = dict(item.diagnostic)
    raw = str(diagnostic.get("raw") or "").strip()
    path = str(diagnostic.get("path") or "").strip() or None
    code = str(diagnostic.get("code") or "artifact_quality_issue").strip() or "artifact_quality_issue"
    message = str(diagnostic.get("message") or "").strip()
    source = str(diagnostic.get("source") or "artifact_quality").strip() or "artifact_quality"
    severity = str(diagnostic.get("severity") or "error").strip() or "error"
    metadata = diagnostic.get("metadata")
    metadata_payload = dict(metadata) if isinstance(metadata, Mapping) else {}
    if raw and "raw" not in metadata_payload:
        metadata_payload["raw"] = raw
    line = diagnostic.get("line")
    if isinstance(line, int) and line > 0 and "line" not in metadata_payload:
        metadata_payload["line"] = line
    column = diagnostic.get("column")
    if isinstance(column, int) and column > 0 and "column" not in metadata_payload:
        metadata_payload["column"] = column
    return RepairDiagnosticV1(
        source=source,
        code=code,
        message=message,
        path=path,
        severity=severity,
        metadata=metadata_payload,
    )


def _plan_probe_item_status(
    *,
    planning: DirectorRepairPlanningResultV1,
    matched_diagnostic_count: int,
    patch_count: int,
    changed_paths: Sequence[str],
) -> str:
    if matched_diagnostic_count <= 0:
        return "not_covered_by_source_tool"
    if planning.planned and planning.ok and patch_count > 0 and changed_paths:
        return "covered_plannable"
    if planning.error_code == "unsupported_repair_source_tool":
        return "unsupported_repair_source_tool"
    return "covered_unplannable"


def _coverage_item_is_covered_unplannable(
    item: DirectorRepairDiagnosticCoverageV1,
    *,
    candidate_source_tools: Sequence[str],
    plannable_source_tools: set[str],
) -> bool:
    if not item.known_rule_matched:
        return False
    candidate_set = set(candidate_source_tools)
    selected_matched = {source_tool for source_tool in item.matched_source_tools if source_tool in candidate_set}
    return bool(selected_matched) and selected_matched.isdisjoint(plannable_source_tools)


def _plan_probe_result_status(
    *,
    coverage: DirectorRepairCoverageReportV1,
    plannable_source_tools: Sequence[str],
    covered_unplannable_diagnostics: Sequence[Mapping[str, Any]],
    uncovered_diagnostics: Sequence[Mapping[str, Any]],
) -> str:
    if coverage.total_diagnostics == 0:
        return "already_clean"
    if uncovered_diagnostics:
        return "coverage_gap_uncovered_diagnostics"
    if covered_unplannable_diagnostics:
        return "coverage_matched_but_unplannable"
    if plannable_source_tools:
        return "covered_plannable"
    return "stuck_no_executable_runtime_plan"


def _project_director_repair_diagnostic_coverage(
    item: Any,
    coverage_gaps_by_id: Mapping[str, Mapping[str, Any]],
) -> DirectorRepairDiagnosticCoverageV1:
    coverage_payload = item.to_dict()
    diagnostic = dict(coverage_payload["diagnostic"])
    gap_payload = (
        dict(coverage_gaps_by_id.get(str(diagnostic.get("diagnostic_id") or ""), {}))
        if not item.known_rule_matched
        else {}
    )
    return DirectorRepairDiagnosticCoverageV1(
        diagnostic=diagnostic,
        known_rule_matched=item.known_rule_matched,
        executable_runtime_plan_matched=item.executable_runtime_plan_matched,
        metadata_only_match=item.metadata_only_match,
        matched_rule_ids=tuple(rule.rule_id for rule in item.matched_rules),
        matched_source_tools=tuple(str(value) for value in coverage_payload.get("matched_source_tools") or ()),
        runtime_plan_rule_ids=tuple(rule.rule_id for rule in item.matched_rules if rule.runtime_plan_available),
        archetypes=tuple(sorted({rule.archetype.value for rule in item.matched_rules})),
        phases=tuple(sorted({rule.phase for rule in item.matched_rules})),
        languages=tuple(sorted({rule.language for rule in item.matched_rules})),
        language=str(coverage_payload["language"]),
        diagnostic_archetype=str(coverage_payload["diagnostic_archetype"]),
        diagnostic_phase=str(coverage_payload["diagnostic_phase"]),
        diagnostic_language=str(coverage_payload["diagnostic_language"]),
        diagnostic_code=str(gap_payload.get("diagnostic_code") or diagnostic.get("code") or "unknown"),
        archetype_suggestion=str(coverage_payload["archetype_suggestion"]),
        phase_suggestion=str(coverage_payload["phase_suggestion"]),
        suggested_rule_family=str(coverage_payload["suggested_rule_family"]),
        reserved_slot_available=bool(coverage_payload.get("reserved_slot_available")),
        slot_status=str(coverage_payload.get("slot_status") or "reserved_slot_missing"),
        reserved_language_slot_matched=bool(gap_payload.get("reserved_language_slot_matched")),
        reserved_language_slot=dict(gap_payload.get("reserved_language_slot") or {}),
        reserved_repairer_module=str(gap_payload.get("reserved_repairer_module") or ""),
        reserved_slot_registration_policy=str(gap_payload.get("reserved_slot_registration_policy") or ""),
        recommended_next_owner=str(gap_payload.get("recommended_next_owner") or ""),
        recommended_route=str(coverage_payload.get("recommended_route") or gap_payload.get("recommended_route") or ""),
        handoff_recommendation=str(gap_payload.get("handoff_recommendation") or ""),
        llm_advisory_recommended=bool(gap_payload.get("llm_advisory_recommended")),
        agi_advisory_recommended=bool(gap_payload.get("agi_advisory_recommended")),
        authoritative_rule_registration_allowed=bool(gap_payload.get("authoritative_rule_registration_allowed")),
        recommended_registration_path=str(gap_payload.get("recommended_registration_path") or ""),
        coverage_status=str(coverage_payload.get("coverage_status") or "coverage_gap"),
        runtime_blockers=tuple(dict(item) for item in coverage_payload.get("runtime_blockers") or ()),
        runtime_blocker_reasons=tuple(str(item) for item in coverage_payload.get("runtime_blocker_reasons") or ()),
    )


def query_director_repair_language_slots(
    query: QueryDirectorRepairLanguageSlotsV1 | None = None,
) -> DirectorRepairLanguageSlotsResultV1:
    """Return read-only future language extension slots for deterministic repairs."""

    request = query or QueryDirectorRepairLanguageSlotsV1()
    slots = repair_language_slots()
    slot_languages = {slot.language for slot in slots}
    rules = default_repair_rule_registry().rules()
    authoritative_source_tools_by_language: dict[str, list[str]] = {}
    for rule in rules:
        if rule.language not in slot_languages:
            continue
        authoritative_source_tools_by_language.setdefault(rule.language, []).append(rule.source_tool)
    runtime_source_tools_by_language: dict[str, list[str]] = {}
    for binding in runtime_repair_bindings():
        language = str(binding["language"])
        if language not in slot_languages:
            continue
        runtime_source_tools_by_language.setdefault(language, []).append(str(binding["source_tool"]))

    def _implementation_status(language: str) -> str:
        if runtime_source_tools_by_language.get(language):
            return "executable_runtime"
        if authoritative_source_tools_by_language.get(language):
            return "metadata_rule_registered"
        return "reserved_only"

    def _slot_next_action(language: str) -> str:
        status = _implementation_status(language)
        if status == "executable_runtime":
            return "extend_existing_runtime_rule_with_bench_evidence"
        if status == "metadata_rule_registered":
            return "promote_metadata_rule_to_executable_runtime_binding"
        return "add_bench_verified_rule_metadata_then_runtime_binding"

    items = (
        tuple(
            DirectorRepairLanguageSlotV1(
                language=slot.language,
                aliases=slot.aliases,
                file_extensions=slot.file_extensions,
                file_names=slot.file_names,
                diagnostic_sources=slot.diagnostic_sources,
                preferred_archetypes=tuple(archetype.value for archetype in slot.preferred_archetypes),
                repairer_module=slot.repairer_module,
                implementation_status=_implementation_status(slot.language),
                registration_policy=slot.registration_policy,
                authoritative_source_tools=tuple(sorted(authoritative_source_tools_by_language.get(slot.language, ()))),
                executable_runtime_source_tools=tuple(sorted(runtime_source_tools_by_language.get(slot.language, ()))),
                notes=slot.notes,
                slot_owner_cell="director.runtime",
                bench_evidence_required=True,
                rule_authoring_status=_implementation_status(slot.language),
                next_action=_slot_next_action(slot.language),
            )
            for slot in slots
        )
        if request.include_items
        else ()
    )
    archetypes = sorted({archetype.value for slot in slots for archetype in slot.preferred_archetypes})
    extensions = sorted({extension for slot in slots for extension in slot.file_extensions})
    file_names = sorted({file_name for slot in slots for file_name in slot.file_names})
    rule_languages = sorted(authoritative_source_tools_by_language)
    runtime_languages = sorted(runtime_source_tools_by_language)
    reserved_only_languages = sorted({slot.language for slot in slots} - set(rule_languages))
    implementation_status_by_language = {slot.language: _implementation_status(slot.language) for slot in slots}
    implementation_status_counts = _count_by_key(
        [{"implementation_status": status} for status in implementation_status_by_language.values()],
        "implementation_status",
    )
    repairer_modules = {slot.language: slot.repairer_module for slot in slots}
    next_actions_by_language = {slot.language: _slot_next_action(slot.language) for slot in slots}
    return DirectorRepairLanguageSlotsResultV1(
        schema_version="director.repair_language_slots.v1",
        source="director.runtime.repair_kernel.registry",
        access="read_only",
        items=items,
        summary={
            "language_count": len(slots),
            "extension_count": len(extensions),
            "languages": [slot.language for slot in slots],
            "file_extensions": extensions,
            "file_names": file_names,
            "preferred_archetypes": archetypes,
            "authoritative_rule_languages": rule_languages,
            "authoritative_rule_language_count": len(rule_languages),
            "executable_runtime_languages": runtime_languages,
            "executable_runtime_language_count": len(runtime_languages),
            "reserved_only_languages": reserved_only_languages,
            "reserved_only_language_count": len(reserved_only_languages),
            "implementation_status_by_language": implementation_status_by_language,
            "implementation_status_counts": implementation_status_counts,
            "repairer_modules": repairer_modules,
            "next_actions_by_language": next_actions_by_language,
            "reserved_only_repairer_modules": {
                language: repairer_modules[language] for language in reserved_only_languages
            },
            "bench_driven_rule_addition_required": True,
        },
    )


def query_director_repair_post_execution_schedule(
    query: QueryDirectorRepairPostExecutionScheduleV1 | None = None,
) -> DirectorRepairPostExecutionScheduleResultV1:
    """Return the runtime-owned post-execution deterministic repair schedule."""

    request = query or QueryDirectorRepairPostExecutionScheduleV1()
    internal_steps = post_execution_repair_schedule()
    ordered_steps = tuple(_public_post_execution_step(step) for step in internal_steps)
    languages = sorted({step.language for step in ordered_steps})
    phases = sorted({step.phase for step in ordered_steps})
    priorities = sorted({step.priority for step in ordered_steps})
    executable_runtime_source_tools = [
        step.source_tool for step in ordered_steps if step.executable_runtime_source_tool
    ]
    callback_schedule_label_source_tools = [
        step.source_tool for step in ordered_steps if not step.executable_runtime_source_tool
    ]
    return DirectorRepairPostExecutionScheduleResultV1(
        schema_version="director.repair_post_execution_schedule.v1",
        source="director.runtime.repair_kernel.scheduler",
        access="read_only",
        items=ordered_steps if request.include_items else (),
        summary={
            "step_count": len(ordered_steps),
            "languages": languages,
            "phases": phases,
            "priorities": priorities,
            "ordered_step_ids": [step.step_id for step in ordered_steps],
            "source_tools": [step.source_tool for step in ordered_steps],
            "source_tool_kinds": [step.source_tool_kind for step in ordered_steps],
            "source_tool_kind_counts": {
                "callback_schedule_label": len(callback_schedule_label_source_tools),
                "executable_runtime": len(executable_runtime_source_tools),
            },
            "executable_runtime_source_tools": executable_runtime_source_tools,
            "callback_schedule_label_source_tools": callback_schedule_label_source_tools,
            "target_scheduler": "director.runtime.repair_kernel.scheduler",
            "runner_binding_owner": "roles.adapters",
            "adapter_projection_bridge": True,
            "adapter_callback_bridge": False,
            "runtime_schedule_authoritative": True,
            "default_max_rounds": DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
            "convergence_loop_owned_by": "director.runtime.repair_kernel.scheduler",
            "cycle_breaker": "repeated_round_fingerprint",
        },
    )


def run_director_post_execution_repair_schedule(
    *,
    runner_step_ids: Sequence[str],
    runner: PostExecutionStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> tuple[list[dict[str, Any]], tuple[DirectorRepairPostExecutionStepV1, ...]]:
    """Run migration callbacks through the runtime-owned post-execution schedule."""

    result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
        max_rounds=max_rounds,
    )
    return [dict(item) for item in result.tool_results], result.ordered_steps


def run_director_post_execution_repair_schedule_result(
    *,
    runner_step_ids: Sequence[str],
    runner: PostExecutionStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> DirectorRepairPostExecutionScheduleRunResultV1:
    """Run migration callbacks and expose runtime-owned summary projections."""

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=lambda step: runner(_public_post_execution_step(step)),
        max_rounds=max_rounds,
    )
    run_payload = run.to_dict()
    return DirectorRepairPostExecutionScheduleRunResultV1(
        schema_version="director.repair_post_execution_schedule_run_result.v1",
        source="director.runtime.repair_kernel.scheduler",
        ordered_steps=tuple(_public_post_execution_step(step) for step in run.ordered_steps),
        tool_results=tuple(dict(item) for item in run_payload["tool_results"]),
        # The result contract normalizes these constructor mappings to frozen DTOs in __post_init__.
        receipt_projections=cast(
            tuple[DirectorRepairCallbackReceiptProjectionV1, ...],
            tuple(dict(item) for item in run_payload["receipt_projections"]),
        ),
        summary=dict(run_payload["summary"]),
        max_rounds=int(run_payload["max_rounds"]),
        rounds_run=int(run_payload["rounds_run"]),
        convergence_status=str(run_payload["convergence_status"]),
        stopped_reason=str(run_payload["stopped_reason"]),
    )


def query_director_repair_materialization_quality_schedule(
    query: QueryDirectorRepairMaterializationQualityScheduleV1 | None = None,
) -> DirectorRepairMaterializationQualityScheduleResultV1:
    """Return the runtime-owned materialization-quality deterministic repair schedule."""

    request = query or QueryDirectorRepairMaterializationQualityScheduleV1()
    internal_steps = materialization_quality_repair_schedule()
    ordered_steps = tuple(_public_materialization_quality_step(step) for step in internal_steps)
    languages = sorted({step.language for step in ordered_steps})
    phases = sorted({step.phase for step in ordered_steps})
    priorities = sorted({step.priority for step in ordered_steps})
    executable_runtime_source_tools = [
        step.source_tool for step in ordered_steps if step.executable_runtime_source_tool
    ]
    callback_schedule_label_source_tools = [
        step.source_tool for step in ordered_steps if not step.executable_runtime_source_tool
    ]
    runtime_source_tools = _ordered_unique(
        tuple(source_tool for step in ordered_steps for source_tool in step.runtime_source_tools)
    )
    return DirectorRepairMaterializationQualityScheduleResultV1(
        schema_version="director.repair_materialization_quality_schedule.v1",
        source="director.runtime.repair_kernel.scheduler",
        access="read_only",
        items=ordered_steps if request.include_items else (),
        summary={
            "step_count": len(ordered_steps),
            "languages": languages,
            "phases": phases,
            "priorities": priorities,
            "ordered_step_ids": [step.step_id for step in ordered_steps],
            "source_tools": [step.source_tool for step in ordered_steps],
            "source_tool_kinds": [step.source_tool_kind for step in ordered_steps],
            "runtime_source_tools": list(runtime_source_tools),
            "runtime_source_tool_count": len(runtime_source_tools),
            "source_tool_kind_counts": {
                "callback_schedule_label": len(callback_schedule_label_source_tools),
                "executable_runtime": len(executable_runtime_source_tools),
            },
            "executable_runtime_source_tools": executable_runtime_source_tools,
            "callback_schedule_label_source_tools": callback_schedule_label_source_tools,
            "target_scheduler": "director.runtime.repair_kernel.scheduler",
            "runner_binding_owner": "roles.adapters",
            "adapter_projection_bridge": True,
            "adapter_callback_bridge": False,
            "runtime_schedule_authoritative": True,
            "default_max_rounds": DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
            "convergence_loop_owned_by": "director.runtime.repair_kernel.scheduler",
            "cycle_breaker": "repeated_round_fingerprint",
        },
    )


def run_director_materialization_quality_repair_schedule(
    *,
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> tuple[list[dict[str, Any]], tuple[DirectorRepairMaterializationQualityStepV1, ...]]:
    """Run materialization-quality callbacks through the runtime-owned schedule."""

    result = run_director_materialization_quality_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
        max_rounds=max_rounds,
    )
    return [dict(item) for item in result.tool_results], result.ordered_steps


def run_director_materialization_quality_repair_schedule_result(
    *,
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> DirectorRepairMaterializationQualityScheduleRunResultV1:
    """Run materialization callbacks and expose runtime-owned summary projections."""

    run = run_materialization_quality_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=lambda step: runner(_public_materialization_quality_step(step)),
        max_rounds=max_rounds,
    )
    run_payload = run.to_dict()
    return DirectorRepairMaterializationQualityScheduleRunResultV1(
        schema_version="director.repair_materialization_quality_schedule_run_result.v1",
        source="director.runtime.repair_kernel.scheduler",
        ordered_steps=tuple(_public_materialization_quality_step(step) for step in run.ordered_steps),
        tool_results=tuple(dict(item) for item in run_payload["tool_results"]),
        # The result contract normalizes these constructor mappings to frozen DTOs in __post_init__.
        receipt_projections=cast(
            tuple[DirectorRepairCallbackReceiptProjectionV1, ...],
            tuple(dict(item) for item in run_payload["receipt_projections"]),
        ),
        summary=dict(run_payload["summary"]),
        max_rounds=int(run_payload["max_rounds"]),
        rounds_run=int(run_payload["rounds_run"]),
        convergence_status=str(run_payload["convergence_status"]),
        stopped_reason=str(run_payload["stopped_reason"]),
    )


def run_director_materialization_quality_repair_facade(
    *,
    artifact_quality_errors: Sequence[str],
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunnerV1,
    artifact_quality_issues: Sequence[Mapping[str, Any]] = (),
    plan_probe_preaudit: Mapping[str, Any] | None = None,
    convergence_verifier_present: bool = False,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> DirectorRepairMaterializationQualityFacadeResultV1:
    """Run materialization-quality repairs through the runtime-owned facade."""

    coverage_preaudit = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            tuple(str(item) for item in artifact_quality_errors),
            artifact_quality_issues=tuple(dict(item) for item in artifact_quality_issues),
        )
    ).to_dict()
    runtime_schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    pre_run_reconciliation = _materialization_quality_schedule_reconciliation(
        runtime_steps=runtime_schedule.items,
        runner_step_ids=runner_step_ids,
    )
    if not pre_run_reconciliation["exact_match"]:
        raise RuntimeError(
            f"materialization quality repair runner bindings drift from runtime schedule: {pre_run_reconciliation}"
        )
    schedule_result = run_director_materialization_quality_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
        max_rounds=max_rounds,
    )
    ordered_steps = schedule_result.ordered_steps
    schedule_reconciliation = _materialization_quality_schedule_reconciliation(
        runtime_steps=runtime_schedule.items,
        runner_step_ids=runner_step_ids,
        result_steps=ordered_steps,
    )
    if not schedule_reconciliation["exact_match"]:
        raise RuntimeError(
            f"materialization quality repair schedule result drifted from runtime schedule: {schedule_reconciliation}"
        )
    schedule_payload = schedule_result.to_dict()
    tool_results = _project_materialization_facade_tool_results_with_runtime_metadata(
        [dict(item) for item in schedule_payload["tool_results"]],
        ordered_steps=ordered_steps,
    )
    receipt_projections = tuple(dict(item) for item in schedule_payload["receipt_projections"])
    source_tools = _ordered_unique(
        tuple(
            str(_materialization_result_payload(item).get("source_tool") or "")
            for item in tool_results
            if isinstance(item, dict)
        )
    )
    summary = {
        "schema_version": "director.materialization_quality_repair_facade_summary.v1",
        "stage": "deterministic_quality_repair",
        "attempted": bool(tool_results),
        "success": False,
        "revalidated": False,
        "success_reason": "repair_actions_require_quality_gate_rerun",
        "tool_results": len(tool_results),
        "write_tool_evidence": _materialization_facade_has_successful_write_tool(tool_results),
        "source_tools": list(source_tools),
        "coverage_preaudit": coverage_preaudit,
        "plan_probe_preaudit": dict(plan_probe_preaudit or {}),
        "schedule_summary": dict(schedule_payload["summary"]),
        "schedule_reconciliation": schedule_reconciliation,
        "runtime_facade_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "convergence_verifier_present": bool(convergence_verifier_present),
    }
    return DirectorRepairMaterializationQualityFacadeResultV1(
        schema_version="director.materialization_quality_repair_facade_result.v1",
        source="director.runtime.repair_kernel.materialization_quality_facade",
        ordered_steps=ordered_steps,
        tool_results=tuple(tool_results),
        receipt_projections=receipt_projections,
        coverage_preaudit=coverage_preaudit,
        plan_probe_preaudit=dict(plan_probe_preaudit or {}),
        schedule_summary=dict(schedule_payload["summary"]),
        schedule_reconciliation=schedule_reconciliation,
        summary=summary,
        max_rounds=schedule_result.max_rounds,
        rounds_run=schedule_result.rounds_run,
        convergence_status=schedule_result.convergence_status,
        stopped_reason=schedule_result.stopped_reason,
    )


def _materialization_quality_schedule_reconciliation(
    *,
    runtime_steps: Sequence[DirectorRepairMaterializationQualityStepV1],
    runner_step_ids: Sequence[str],
    result_steps: Sequence[DirectorRepairMaterializationQualityStepV1] | None = None,
) -> dict[str, Any]:
    runtime_step_ids = [step.step_id for step in runtime_steps]
    runner_ids = [str(step_id or "").strip() for step_id in runner_step_ids if str(step_id or "").strip()]
    result_step_ids = [step.step_id for step in result_steps] if result_steps is not None else []
    runtime_id_set = set(runtime_step_ids)
    runner_id_set = set(runner_ids)
    result_id_set = set(result_step_ids)
    runtime_has_unique_steps = len(runtime_step_ids) == len(runtime_id_set)
    runner_has_unique_steps = len(runner_ids) == len(runner_id_set)
    result_matches_runtime = result_steps is None or result_step_ids == runtime_step_ids
    return {
        "schema_version": "director.materialization_quality_schedule_reconciliation.v1",
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "runtime_step_ids": runtime_step_ids,
        "runner_step_ids": runner_ids,
        "schedule_result_step_ids": result_step_ids,
        "runtime_step_count": len(runtime_step_ids),
        "runner_step_count": len(runner_ids),
        "schedule_result_step_count": len(result_step_ids) if result_steps is not None else None,
        "runtime_has_unique_steps": runtime_has_unique_steps,
        "runner_has_unique_steps": runner_has_unique_steps,
        "runner_key_set_matches_runtime": runner_id_set == runtime_id_set,
        "runner_order_matches_runtime": runner_ids == runtime_step_ids,
        "schedule_result_matches_runtime": result_matches_runtime,
        "missing_runner_step_ids": sorted(runtime_id_set - runner_id_set),
        "extra_runner_step_ids": sorted(runner_id_set - runtime_id_set),
        "missing_schedule_result_step_ids": sorted(runtime_id_set - result_id_set) if result_steps is not None else [],
        "extra_schedule_result_step_ids": sorted(result_id_set - runtime_id_set) if result_steps is not None else [],
        "exact_match": (
            runtime_has_unique_steps
            and runner_has_unique_steps
            and runner_ids == runtime_step_ids
            and result_matches_runtime
        ),
    }


def _project_materialization_facade_tool_results_with_runtime_metadata(
    tool_results: Sequence[Mapping[str, Any]],
    *,
    ordered_steps: Sequence[DirectorRepairMaterializationQualityStepV1],
) -> list[dict[str, Any]]:
    steps_by_id = {step.step_id: step for step in ordered_steps}
    projected: list[dict[str, Any]] = []
    for item in tool_results:
        copied = dict(item)
        result = copied.get("result")
        payload = dict(result) if isinstance(result, Mapping) else None
        step: DirectorRepairMaterializationQualityStepV1 | None = None
        if payload is not None:
            step = steps_by_id.get(_materialization_payload_step_id(payload))
            if step is None and len(tuple(ordered_steps)) == 1:
                step = next(iter(ordered_steps))
            if step is not None:
                _apply_materialization_runtime_step_metadata(payload, step)
                copied["result"] = payload
        if step is not None:
            copied.setdefault("runtime_step_id", step.step_id)
            copied.setdefault("scheduler_step_id", step.step_id)
            copied.setdefault("bridge_step_id", step.step_id)
            copied.setdefault("phase", step.phase)
            copied.setdefault("priority", step.priority)
            copied.setdefault("depends_on", list(step.depends_on))
            copied["evidence_status"] = _materialization_tool_result_evidence_status(copied)
        else:
            copied.setdefault("evidence_status", "missing_evidence")
        projected.append(copied)
    return projected


def _apply_materialization_runtime_step_metadata(
    payload: dict[str, Any],
    step: DirectorRepairMaterializationQualityStepV1,
) -> None:
    payload["runtime_step_id"] = step.step_id
    payload["scheduler_step_id"] = step.step_id
    payload["bridge_step_id"] = step.step_id
    payload["language"] = step.language
    payload["phase"] = step.phase
    payload["priority"] = step.priority
    payload["depends_on"] = list(step.depends_on)
    payload["evidence_status"] = _materialization_payload_evidence_status(payload)


def _materialization_payload_step_id(payload: Mapping[str, Any]) -> str:
    for key in ("bridge_step_id", "step_id", "scheduler_step_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _materialization_result_payload(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return dict(result) if isinstance(result, Mapping) else {}


def _materialization_tool_result_evidence_status(tool_result: Mapping[str, Any]) -> str:
    result = tool_result.get("result")
    if isinstance(result, Mapping):
        status = _materialization_payload_evidence_status(result)
        if status != "missing_evidence":
            return status
    claimed = _materialization_claimed_evidence_status(tool_result)
    return claimed if claimed in {"missing_evidence", "failed_evidence"} else "missing_evidence"


def _materialization_payload_evidence_status(payload: Mapping[str, Any]) -> str:
    claimed = _materialization_claimed_evidence_status(payload)
    if claimed in {"missing_evidence", "failed_evidence", "resolved_evidence"}:
        return claimed
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, Mapping):
        claimed = _materialization_claimed_evidence_status(repair_kernel)
        if claimed in {"missing_evidence", "failed_evidence", "resolved_evidence"}:
            return claimed
    status = _materialization_evidence_mapping_status(_materialization_payload_revalidation_evidence(payload))
    if status != "missing_evidence":
        return status
    if isinstance(repair_kernel, Mapping):
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, list | tuple):
            receipt_statuses = [
                _materialization_payload_evidence_status(receipt)
                for receipt in receipts
                if isinstance(receipt, Mapping)
            ]
            return _materialization_aggregate_evidence_status(receipt_statuses)
    return "missing_evidence"


def _materialization_payload_revalidation_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("revalidation_evidence", "revalidation"):
        evidence = payload.get(key)
        if isinstance(evidence, Mapping) and evidence:
            return dict(evidence)
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, Mapping):
        kernel_evidence = repair_kernel.get("revalidation_evidence")
        if isinstance(kernel_evidence, Mapping) and kernel_evidence:
            return dict(kernel_evidence)
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, list | tuple):
            for receipt in receipts:
                if not isinstance(receipt, Mapping):
                    continue
                receipt_evidence = receipt.get("revalidation_evidence")
                if isinstance(receipt_evidence, Mapping) and receipt_evidence:
                    return dict(receipt_evidence)
    return {}


def _materialization_evidence_mapping_status(evidence: Any) -> str:
    if not isinstance(evidence, Mapping) or not evidence:
        return "missing_evidence"
    command = evidence.get("command") or evidence.get("verifier_command")
    exit_code = _materialization_optional_int(evidence.get("exit_code"))
    if exit_code is None:
        exit_code = _materialization_optional_int(evidence.get("revalidation_exit_code"))
    if not command or exit_code is None:
        return "missing_evidence"
    residual_count = _materialization_evidence_residual_count(evidence)
    errors_after = _materialization_optional_int(evidence.get("errors_after"))
    if errors_after is None:
        errors_after = _materialization_optional_int(evidence.get("errors_after_count"))
    if exit_code != 0:
        return "failed_evidence"
    if residual_count is not None and residual_count > 0:
        return "failed_evidence"
    if errors_after is not None and errors_after > 0:
        return "failed_evidence"
    return "resolved_evidence"


def _materialization_evidence_residual_count(evidence: Mapping[str, Any]) -> int | None:
    residual_count = _materialization_optional_int(evidence.get("revalidation_residual_count"))
    if residual_count is None:
        residual_count = _materialization_optional_int(evidence.get("residual_count"))
    if residual_count is None:
        residual_count = _materialization_optional_int(evidence.get("residual_diagnostic_count"))
    if residual_count is not None:
        return residual_count
    residual_ids = evidence.get("residual_diagnostic_ids")
    if isinstance(residual_ids, list | tuple | set):
        return len(residual_ids)
    return None


def _materialization_claimed_evidence_status(payload: Mapping[str, Any]) -> str:
    value = str(payload.get("evidence_status") or "").strip()
    if value in {"missing_evidence", "failed_evidence", "resolved_evidence"}:
        return value
    return ""


def _materialization_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _materialization_aggregate_evidence_status(statuses: Sequence[str]) -> str:
    normalized = [str(status or "").strip() for status in statuses if str(status or "").strip()]
    if not normalized:
        return "missing_evidence"
    if "failed_evidence" in normalized:
        return "failed_evidence"
    if "missing_evidence" in normalized:
        return "missing_evidence"
    if all(status == "resolved_evidence" for status in normalized):
        return "resolved_evidence"
    return "missing_evidence"


def _materialization_facade_has_successful_write_tool(tool_results: Sequence[Mapping[str, Any]]) -> bool:
    for item in tool_results:
        tool = str(item.get("tool") or item.get("tool_name") or "").strip()
        if tool not in WRITE_TOOLS:
            continue
        if item.get("ok") is False or item.get("success") is False:
            continue
        return True
    return False


def _public_post_execution_step(step: PostExecutionRepairScheduleStep) -> DirectorRepairPostExecutionStepV1:
    return DirectorRepairPostExecutionStepV1(
        step_id=step.step_id,
        language=step.language,
        phase=step.phase,
        priority=step.priority,
        source_tool=step.source_tool,
        source_tool_kind=step.source_tool_kind,
        executable_runtime_source_tool=step.executable_runtime_source_tool,
        runtime_source_tools=step.runtime_source_tools,
        depends_on=step.depends_on,
    )


def _public_materialization_quality_step(
    step: MaterializationQualityRepairScheduleStep,
) -> DirectorRepairMaterializationQualityStepV1:
    return DirectorRepairMaterializationQualityStepV1(
        step_id=step.step_id,
        language=step.language,
        phase=step.phase,
        priority=step.priority,
        source_tool=step.source_tool,
        source_tool_kind=step.source_tool_kind,
        executable_runtime_source_tool=step.executable_runtime_source_tool,
        runtime_source_tools=step.runtime_source_tools,
        depends_on=step.depends_on,
    )


def build_director_repair_kernel_summary(
    *,
    stage: str,
    tool_results: Sequence[dict[str, Any]],
    artifact_quality_errors: list[str] | None = None,
    artifact_quality_issues: Sequence[Mapping[str, Any]] = (),
    mode: str = "commit",
) -> dict[str, Any]:
    """Build a public repair-kernel summary for projected Director repair effects."""

    result = project_director_repair_kernel_summary(
        ProjectDirectorRepairKernelSummaryV1(
            stage=stage,
            tool_results=tuple(tool_results or ()),
            artifact_quality_errors=tuple(artifact_quality_errors or ()),
            artifact_quality_issues=tuple(dict(item) for item in artifact_quality_issues),
            mode=mode,
        )
    )
    return dict(result.summary)


def project_director_repair_kernel_summary(
    command: ProjectDirectorRepairKernelSummaryV1,
) -> DirectorRepairKernelSummaryProjectionResultV1:
    """Project existing write-tool results into the runtime repair kernel receipt shape."""

    summary = _build_repair_kernel_result_summary(
        stage=command.stage,
        tool_results=[dict(item) for item in command.tool_results],
        artifact_quality_errors=[str(item) for item in command.artifact_quality_errors if str(item or "").strip()],
        repair_diagnostics=_repair_diagnostics_from_quality_inputs(
            command.artifact_quality_errors,
            command.artifact_quality_issues,
        ),
        mode=command.mode,
    )
    return DirectorRepairKernelSummaryProjectionResultV1(
        schema_version="director.repair_kernel_summary_projection.v1",
        source="director.runtime.repair_kernel.receipt_projection",
        access="read_only",
        summary=summary,
    )


def plan_director_repair(command: PlanDirectorRepairCommandV1) -> DirectorRepairPlanningResultV1:
    """Plan a deterministic repair through the generic public runtime surface."""

    public_advisor_notes = tuple(command.advisor_notes or ())
    public_diagnostics = _public_repair_diagnostics_from_command(command)
    runtime_artifact_quality_errors = _runtime_artifact_quality_errors_from_command(command, public_diagnostics)
    planning = plan_runtime_repair(
        source_tool=command.source_tool,
        base_files=command.base_files,
        artifact_quality_errors=runtime_artifact_quality_errors,
        advisor_notes=_to_internal_advisor_notes(public_advisor_notes),
        mode=command.mode,
        repair_diagnostics=tuple(_to_internal_repair_diagnostic(item) for item in public_diagnostics),
    )
    return _to_public_repair_planning_result(
        planning,
        public_advisor_notes=public_advisor_notes,
        public_diagnostics=public_diagnostics,
    )


def run_director_repair(
    command: RunDirectorRepairCommandV1,
    *,
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
    revalidator: DirectorRepairRevalidatorFn | None = None,
) -> DirectorRepairResultV1:
    """Run a deterministic repair through the generic public runtime surface."""

    public_advisor_notes = tuple(command.advisor_notes or ())
    public_diagnostics = _public_repair_diagnostics_from_command(command)
    runtime_artifact_quality_errors = _runtime_artifact_quality_errors_from_command(command, public_diagnostics)
    internal_run = run_runtime_repair(
        source_tool=command.source_tool,
        workspace=command.workspace,
        base_files=command.base_files,
        artifact_quality_errors=runtime_artifact_quality_errors,
        writer=writer,
        editor=editor,
        deleter=deleter,
        allowed_paths=command.allowed_paths,
        advisor_notes=_to_internal_advisor_notes(public_advisor_notes),
        mode=command.mode,
        repair_diagnostics=tuple(_to_internal_repair_diagnostic(item) for item in public_diagnostics),
    )
    planning_result = _to_public_repair_planning_result(
        internal_run.planning,
        advisor_notes=public_advisor_notes,
        public_diagnostics=public_diagnostics,
    )
    metadata: dict[str, Any] = {"planning": planning_result.to_dict()}
    if internal_run.planning.error_code or internal_run.planning.error_message:
        metadata["planning_error"] = {
            "error_code": internal_run.planning.error_code,
            "error_message": internal_run.planning.error_message,
        }
    if internal_run.plan_decision is not None:
        metadata["plan_policy"] = internal_run.plan_decision.to_dict()
    if internal_run.composition_decision is not None:
        metadata["composition_policy"] = internal_run.composition_decision.to_dict()
    if internal_run.execution_result is not None:
        metadata["execution_error"] = internal_run.execution_result.error
        execution_error_code = _repair_execution_error_code(internal_run.execution_result.error)
        if execution_error_code is not None:
            metadata["execution_error_code"] = execution_error_code
        metadata["rolled_back"] = internal_run.execution_result.rolled_back

    if internal_run.execution_result is None:
        metadata["receipt_authority_policy"] = _repair_receipt_authority_policy(())
        return DirectorRepairResultV1(
            ok=False,
            error_code=internal_run.error_code,
            error_message=internal_run.error_message,
            metadata=metadata,
        )

    internal_receipt = internal_run.execution_result.receipt
    residual_diagnostics: tuple[RepairDiagnostic, ...] = ()
    revalidation_error_message: str | None = None
    if revalidator is not None:
        internal_receipt, residual_diagnostics, revalidation_error_message = _attach_native_revalidation_evidence(
            command,
            internal_receipt,
            revalidator,
        )

    receipt = _to_public_repair_receipt(internal_receipt)
    metadata["receipt_authority_policy"] = _repair_receipt_authority_policy((receipt,))
    revalidation_failed = revalidator is not None and receipt.status == "failed_revalidation"
    error_code = internal_run.error_code
    error_message = internal_run.error_message
    if revalidation_failed:
        error_code = "repair_revalidation_failed"
        error_message = revalidation_error_message or "Repair revalidation failed."
    return DirectorRepairResultV1(
        ok=bool(internal_run.execution_result.ok) and not revalidation_failed,
        receipts=(receipt,),
        residual_diagnostics=tuple(_to_public_repair_diagnostic(item) for item in residual_diagnostics),
        error_code=error_code,
        error_message=error_message,
        metadata=metadata,
    )


def _repair_receipt_authority_policy(receipts: Sequence[RepairReceiptV1]) -> dict[str, Any]:
    receipt_list = tuple(receipts or ())
    evidence_status_counts: dict[str, int] = {}
    receipt_status_counts: dict[str, int] = {}
    for receipt in receipt_list:
        evidence_status = str(receipt.evidence_status or "missing_evidence")
        receipt_status = str(receipt.status or "unknown")
        evidence_status_counts[evidence_status] = evidence_status_counts.get(evidence_status, 0) + 1
        receipt_status_counts[receipt_status] = receipt_status_counts.get(receipt_status, 0) + 1

    authoritative_receipt_ids = tuple(
        receipt.receipt_id
        for receipt in receipt_list
        if receipt.authoritative and receipt.status == "applied" and receipt.evidence_status == "resolved_evidence"
    )
    non_authoritative_receipt_ids = tuple(
        receipt.receipt_id
        for receipt in receipt_list
        if not receipt.authoritative or receipt.evidence_status != "resolved_evidence" or receipt.status != "applied"
    )
    missing_evidence_receipt_ids = tuple(
        receipt.receipt_id for receipt in receipt_list if receipt.evidence_status == "missing_evidence"
    )
    failed_evidence_receipt_ids = tuple(
        receipt.receipt_id for receipt in receipt_list if receipt.evidence_status == "failed_evidence"
    )
    authoritative_success = bool(receipt_list) and len(authoritative_receipt_ids) == len(receipt_list)
    return {
        "schema_version": "director.repair_receipt_authority_policy.v1",
        "policy": "authoritative_success_requires_applied_resolved_evidence",
        "authoritative_success": authoritative_success,
        "receipt_count": len(receipt_list),
        "authoritative_receipt_count": len(authoritative_receipt_ids),
        "non_authoritative_receipt_count": len(non_authoritative_receipt_ids),
        "missing_evidence_receipt_count": len(missing_evidence_receipt_ids),
        "failed_evidence_receipt_count": len(failed_evidence_receipt_ids),
        "resolved_evidence_receipt_count": evidence_status_counts.get("resolved_evidence", 0),
        "receipt_status_counts": receipt_status_counts,
        "evidence_status_counts": evidence_status_counts,
        "authoritative_receipt_ids": list(authoritative_receipt_ids),
        "non_authoritative_receipt_ids": list(non_authoritative_receipt_ids),
        "missing_evidence_receipt_ids": list(missing_evidence_receipt_ids),
        "failed_evidence_receipt_ids": list(failed_evidence_receipt_ids),
        "requires_revalidation": bool(missing_evidence_receipt_ids),
        "result_ok_is_write_success_only": not authoritative_success,
        "ledger_consumers_must_check_authoritative_success": True,
    }


def run_director_repair_convergence(
    command: RunDirectorRepairConvergenceCommandV1,
    *,
    writer: WriteFileFn,
    verifier: DirectorRepairConvergenceVerifierFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
) -> DirectorRepairConvergenceResultV1:
    """Run typed Director Runtime repair convergence through the public surface.

    The verifier is an adapter-supplied effect boundary. This function only
    converts the callback result into the internal verifier snapshot; it never
    runs verifier commands itself.
    """

    public_advisor_notes = tuple(command.advisor_notes or ())
    initial_diagnostics = _repair_diagnostics_from_quality_inputs(
        command.artifact_quality_errors,
        command.artifact_quality_issues,
    )

    def _verifier(round_number: int, receipts: tuple[RepairReceipt, ...]) -> RepairVerifierSnapshot:
        public_receipts = tuple(_to_public_repair_receipt(receipt) for receipt in receipts)
        environment_requirements = environment_refresh_requirements_from_receipts(
            tuple(receipt.to_dict() for receipt in public_receipts),
            workspace=command.workspace,
        )
        environment_plans = environment_prep_plans_from_requirements(
            environment_requirements,
            workspace=command.workspace,
            previous_prep_receipts=_environment_prep_receipts_from_public_repair_receipts(public_receipts),
        )
        public_environment_plans = tuple(_to_public_environment_prep_plan(plan.to_dict()) for plan in environment_plans)
        request = DirectorRepairConvergenceVerifierRequestV1(
            task_id=command.task_id,
            workspace=command.workspace,
            round_number=round_number,
            source_tools=command.source_tools,
            receipts=public_receipts,
            environment_prep_plans=public_environment_plans,
            max_rounds=command.max_rounds,
            metadata={
                "public_entrypoint": "run_director_repair_convergence",
                "effect_boundary": "adapter_supplied_verifier_callback_no_command_execution",
                "command_metadata": dict(command.metadata),
                "environment_prep_required": bool(public_environment_plans),
                "environment_refresh_requirement_count": len(environment_requirements),
                "environment_prep_plan_count": len(public_environment_plans),
            },
        )
        try:
            verifier_input = verifier(request)
        except Exception as exc:
            raise _PublicConvergenceVerifierError(
                f"Repair convergence verifier failed: {type(exc).__name__}: {exc}",
                metadata={
                    "verifier_failure_reason": "verifier_exception",
                    "verifier_error_type": type(exc).__name__,
                    "verifier_error": str(exc),
                    "round_number": round_number,
                },
            ) from exc

        if not isinstance(verifier_input, DirectorRepairVerifierSnapshotInputV1):
            raise _PublicConvergenceVerifierError(
                "Repair convergence verifier returned invalid evidence type.",
                metadata={
                    "verifier_failure_reason": "invalid_verifier_snapshot_type",
                    "verifier_result_type": type(verifier_input).__name__,
                    "round_number": round_number,
                },
            )

        _validate_public_convergence_verifier_evidence(verifier_input, round_number=round_number)
        diagnostics = _repair_diagnostics_from_quality_inputs(
            verifier_input.residual_artifact_quality_errors,
            verifier_input.residual_artifact_quality_issues,
        )
        environment_prep_receipts = tuple(receipt.to_dict() for receipt in verifier_input.environment_prep_receipts)
        verifier_metadata = dict(verifier_input.metadata)
        if environment_prep_receipts:
            verifier_metadata["environment_prep_receipts"] = list(environment_prep_receipts)
            verifier_metadata["environment_prep_receipt_count"] = len(environment_prep_receipts)
            verifier_metadata["environment_prep_failed_receipt_count"] = sum(
                1
                for receipt in environment_prep_receipts
                if str(receipt.get("status") or "") not in {"succeeded", "skipped_fresh"}
            )
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=verifier_input.command,
            exit_code=verifier_input.exit_code,
            raw_output_ref=verifier_input.raw_output_ref,
            metadata={
                **verifier_metadata,
                "public_entrypoint": "run_director_repair_convergence",
                "effect_boundary": "adapter_supplied_verifier_callback_no_command_execution",
                "environment_prep_required": bool(public_environment_plans),
                "environment_refresh_requirement_count": len(environment_requirements),
                "environment_prep_plan_count": len(public_environment_plans),
                "round_number": round_number,
            },
        )

    try:
        # Resolve via the package namespace so test monkeypatching of
        # ``runtime_public_service.run_runtime_repair_convergence`` (the package
        # __init__ re-export) is honored. Direct module-level import would bind
        # the unpatched implementation and bypass the patch.
        from polaris.cells.director.runtime.public import service as _public_service
        _run_runtime_repair_convergence = _public_service.run_runtime_repair_convergence
        internal_result = _run_runtime_repair_convergence(
            source_tools=command.source_tools,
            workspace=command.workspace,
            base_files=command.base_files,
            artifact_quality_errors=tuple(
                str(item) for item in command.artifact_quality_errors if str(item or "").strip()
            ),
            verifier=_verifier,
            writer=writer,
            editor=editor,
            deleter=deleter,
            allowed_paths=command.allowed_paths,
            advisor_notes=_to_internal_advisor_notes(public_advisor_notes),
            mode=command.mode,
            max_rounds=command.max_rounds,
            repair_diagnostics=initial_diagnostics,
        )
    except _PublicConvergenceVerifierError as exc:
        return _failed_public_convergence_result(
            command,
            status=exc.status,
            final_diagnostics=initial_diagnostics,
            error_code=exc.error_code,
            error_message=str(exc),
            metadata=exc.metadata,
            editor=editor,
            deleter=deleter,
        )
    except Exception as exc:  # noqa: BLE001 - public convergence boundary must not pretend success on runtime errors.
        return _failed_public_convergence_result(
            command,
            status="convergence_runtime_error",
            final_diagnostics=initial_diagnostics,
            error_code="convergence_runtime_error",
            error_message=f"Director repair convergence failed: {type(exc).__name__}: {exc}",
            metadata={
                "runtime_failure_reason": "internal_convergence_exception",
                "runtime_error_type": type(exc).__name__,
                "runtime_error": str(exc),
                "runtime_error_code": _repair_execution_error_code(str(exc)),
            },
            editor=editor,
            deleter=deleter,
        )

    return _to_public_convergence_result(command, internal_result, editor=editor, deleter=deleter)


def run_director_task_boundary_quality_loop(
    command: RunDirectorTaskBoundaryQualityLoopCommandV1,
    *,
    writer: WriteFileFn,
    verifier: DirectorRepairConvergenceVerifierFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
) -> DirectorTaskBoundaryQualityResultV1:
    """Validate one complete CE task boundary through coverage, plan probe, and convergence."""

    plan_probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=command.artifact_quality_errors,
            artifact_quality_issues=command.artifact_quality_issues,
            base_files=command.base_files,
            source_tools=command.source_tools,
            mode="shadow",
            advisor_notes=command.advisor_notes,
            metadata={
                **dict(command.metadata),
                "public_entrypoint": "run_director_task_boundary_quality_loop",
                "task_boundary_phase": "plan_probe",
            },
        )
    )
    boundary_metadata = _task_boundary_quality_metadata(command, plan_probe=plan_probe)
    if plan_probe.status == "already_clean":
        return DirectorTaskBoundaryQualityResultV1(
            task_id=command.task_id,
            ok=True,
            status="already_clean",
            plan_probe=plan_probe,
            metadata=boundary_metadata,
        )
    if not plan_probe.plannable_source_tools:
        return DirectorTaskBoundaryQualityResultV1(
            task_id=command.task_id,
            ok=False,
            status=plan_probe.status,
            plan_probe=plan_probe,
            metadata=boundary_metadata,
            error_code=plan_probe.status,
            error_message=f"Task boundary quality loop stopped before execution: {plan_probe.status}.",
        )
    if plan_probe.covered_unplannable_diagnostics or plan_probe.uncovered_diagnostics:
        return DirectorTaskBoundaryQualityResultV1(
            task_id=command.task_id,
            ok=False,
            status=plan_probe.status,
            plan_probe=plan_probe,
            metadata=boundary_metadata,
            error_code=plan_probe.status,
            error_message=f"Task boundary quality loop requires triage before convergence: {plan_probe.status}.",
        )

    convergence = run_director_repair_convergence(
        RunDirectorRepairConvergenceCommandV1(
            task_id=command.task_id,
            workspace=command.workspace,
            source_tools=plan_probe.plannable_source_tools,
            artifact_quality_errors=command.artifact_quality_errors,
            artifact_quality_issues=command.artifact_quality_issues,
            base_files=command.base_files,
            allowed_paths=command.allowed_paths,
            advisor_notes=command.advisor_notes,
            mode=command.mode,
            max_rounds=command.max_rounds,
            metadata={
                **dict(command.metadata),
                "public_entrypoint": "run_director_task_boundary_quality_loop",
                "task_boundary_phase": "convergence",
                "plan_probe_status": plan_probe.status,
                "plan_probe_plannable_source_tools": list(plan_probe.plannable_source_tools),
            },
        ),
        writer=writer,
        verifier=verifier,
        editor=editor,
        deleter=deleter,
    )
    status = "task_boundary_converged" if convergence.ok else f"task_boundary_{convergence.status}"
    return DirectorTaskBoundaryQualityResultV1(
        task_id=command.task_id,
        ok=convergence.ok,
        status=status,
        plan_probe=plan_probe,
        convergence_result=convergence,
        metadata={
            **boundary_metadata,
            "convergence_status": convergence.status,
            "convergence_ok": convergence.ok,
            "final_diagnostic_count": len(convergence.final_diagnostics),
            "receipt_count": len(convergence.receipts),
        },
        error_code=None if convergence.ok else convergence.error_code or convergence.status,
        error_message=None if convergence.ok else convergence.error_message,
    )
