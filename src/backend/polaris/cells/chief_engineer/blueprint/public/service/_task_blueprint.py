"""Task blueprint generation and status queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from polaris.cells.director.tasking.public.service import (
    build_director_execution_profile_snapshot,
)

from ...internal.architecture_decisions import (
    infer_architecture_decisions,
    merge_architecture_decisions,
    normalize_architecture_decisions,
    selected_libraries_from_decisions,
)
from ...internal.blueprint_persistence import BlueprintPersistence
from ...internal.ce_consumer import _control_plane_job_token
from ..contracts import (
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    TaskBlueprintResultV1,
)
from ._governance import (
    attach_governance_to_blueprint,
)
from ._helpers import (
    _apply_delivery_depth_test_targets,
    _blueprint_contract_fields,
    _blueprint_declared_file_paths,
    _blueprint_hash,
    _blueprint_path,
    _contract_completeness,
    _existing_target_files_from_payload,
    _latest_blueprint_for_task,
    _mapping,
    _merge_existing_target_file_summaries,
    _merge_string_lists,
    _module_interface_contract,
    _needs_workspace_interface_snapshot,
    _normalize_llm_blueprint_overlay,
    _safe_token,
    _target_files_from_context,
    _tuple_from_payload,
    _utc_now,
    _workspace_existing_target_file_summaries,
)
from ._portfolio import (
    _project_blueprint_portfolio_context,
)


def generate_task_blueprint(command: GenerateTaskBlueprintCommandV1) -> TaskBlueprintResultV1:
    """Generate and persist a task-level Chief Engineer blueprint."""

    now = _utc_now()
    blueprint_id = f"ce_{_safe_token(command.task_id)}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    context = dict(command.context)
    constraints = dict(command.constraints)
    contract_fields = _blueprint_contract_fields(context)
    target_files = _target_files_from_context(context)
    blueprint_portfolio_projection = _project_blueprint_portfolio_context(
        context,
        task_id=command.task_id,
        target_files=target_files,
    )
    title = str(context.get("task_title") or context.get("title") or command.objective).strip()
    summary = f"Chief Engineer blueprint for {command.task_id}: {command.objective}"
    acceptance_criteria = list(contract_fields["acceptance_criteria"])
    execution_checklist = list(contract_fields["execution_checklist"])
    scope_paths = list(contract_fields["scope_paths"])
    dependencies = list(contract_fields["dependencies"])
    delivery_plan_document = dict(contract_fields["delivery_plan_document"])
    delivery_depth_contract = dict(contract_fields["delivery_depth_contract"])
    llm_blueprint_overlay = _normalize_llm_blueprint_overlay(command.llm_blueprint)
    llm_declared_target_files = _blueprint_declared_file_paths(_mapping(command.llm_blueprint).get("construction_plan"))
    if llm_declared_target_files:
        llm_blueprint_overlay["projected_target_files"] = llm_declared_target_files[:32]
        llm_blueprint_overlay["projected_target_file_authority"] = "advisory_only_not_scope_authority"
        unpromoted = [path for path in llm_declared_target_files if path not in target_files]
        if unpromoted:
            llm_blueprint_overlay["advisory_target_files_not_promoted"] = unpromoted[:32]
    _apply_delivery_depth_test_targets(
        target_files=target_files,
        scope_paths=scope_paths,
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
        delivery_depth_contract=delivery_depth_contract,
        context=context,
    )
    inferred_decisions = infer_architecture_decisions(
        objective=command.objective,
        context=context,
        constraints=constraints,
        target_files=target_files,
        scope_paths=scope_paths,
        dependencies=dependencies,
    )
    architecture_decisions = merge_architecture_decisions(
        tuple(contract_fields["architecture_decisions"]),
        inferred_decisions,
    )
    architecture_decision_payloads = [decision.to_dict() for decision in architecture_decisions]
    selected_libraries = list(selected_libraries_from_decisions(architecture_decisions))
    workspace_existing_target_files = (
        _workspace_existing_target_file_summaries(command.workspace)
        if _needs_workspace_interface_snapshot(target_files)
        else []
    )
    merged_existing_target_files = _merge_existing_target_file_summaries(
        context.get("existing_target_files"),
        workspace_existing_target_files,
    )
    if merged_existing_target_files:
        context["existing_target_files"] = merged_existing_target_files
    module_interface_contract = _module_interface_contract(
        target_files=target_files,
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
        context=context,
    )
    contract_completeness = _contract_completeness(
        objective=command.objective,
        title=title,
        target_files=target_files,
        scope_paths=scope_paths,
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
        delivery_depth_contract=delivery_depth_contract,
        delivery_plan_document=delivery_plan_document,
        llm_blueprint_overlay=llm_blueprint_overlay,
    )
    interface_conflicts = list(module_interface_contract.get("interface_conflicts") or [])
    if interface_conflicts:
        blocker = "module_interface_contract owner conflict: " + "; ".join(
            f"{item.get('planned_path')} conflicts with actual owner {item.get('actual_owner_path')}"
            for item in interface_conflicts[:4]
            if isinstance(item, dict)
        )
        semantic_blockers = list(contract_completeness.get("semantic_blockers") or [])
        if blocker not in semantic_blockers:
            semantic_blockers.append(blocker)
        contract_completeness["semantic_blockers"] = semantic_blockers
        contract_completeness["handoff_ready"] = False
        semantic_alignment = contract_completeness.get("semantic_alignment")
        if isinstance(semantic_alignment, dict):
            semantic_alignment["ready"] = False
            blockers = list(semantic_alignment.get("blockers") or [])
            if blocker not in blockers:
                blockers.append(blocker)
            semantic_alignment["blockers"] = blockers
    context["acceptance_criteria"] = acceptance_criteria
    context["execution_checklist"] = execution_checklist
    context["target_files"] = target_files
    context["scope_paths"] = scope_paths
    context["dependencies"] = dependencies
    context["architecture_decisions"] = architecture_decision_payloads
    context["selected_libraries"] = selected_libraries
    if module_interface_contract:
        context.setdefault("module_interface_contract", module_interface_contract)
    if delivery_plan_document:
        context.setdefault("delivery_plan_document", delivery_plan_document)
    if delivery_depth_contract:
        context.setdefault("delivery_depth_contract", delivery_depth_contract)
    if llm_blueprint_overlay:
        context.setdefault("llm_blueprint_overlay", llm_blueprint_overlay)
    recommendations = (
        "Validate PM acceptance criteria before Director execution.",
        "Keep implementation scope within the recorded target files.",
        "Verify delivery_depth_contract behavior rules and edge cases before marking the task complete.",
    )
    risks = tuple(_merge_string_lists(contract_fields["risks"], llm_blueprint_overlay.get("risk_flags")))
    if "pm_task_contract" in context:
        pm_contract_hash = _blueprint_hash(dict(contract_fields["task"]))
        context["pm_contract_hash"] = pm_contract_hash
        context["contract_hash"] = pm_contract_hash
    else:
        pm_contract_hash = str(context.get("pm_contract_hash") or context.get("contract_hash") or "").strip()
        if not pm_contract_hash:
            pm_contract_hash = _blueprint_hash(dict(contract_fields["task"]))
    profile_metadata = {
        **context,
        "contract_hash": pm_contract_hash,
        "pm_contract_hash": pm_contract_hash,
        "target_files": target_files,
        "scope_paths": scope_paths,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
    }
    profile_snapshot = build_director_execution_profile_snapshot(
        subject=title,
        description=command.objective,
        metadata=profile_metadata,
        target_files=target_files,
        scope_paths=scope_paths,
        workspace=command.workspace,
    )
    director_execution_profile = dict(profile_snapshot["profile"])
    execution_profile_hash = str(profile_snapshot["profile_hash"])
    execution_profile_ref = str(profile_snapshot["profile_ref"])
    payload: dict[str, Any] = {
        "schema_version": "chief_engineer.blueprint.v1",
        "role": "ChiefEngineer",
        "blueprint_id": blueprint_id,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "title": title,
        "objective": command.objective,
        "summary": summary,
        "status": "generated",
        "source": "chief_engineer.generate_task_blueprint",
        "target_files": target_files,
        "scope_paths": scope_paths,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "dependencies": dependencies,
        "architecture_decisions": architecture_decision_payloads,
        "selected_libraries": selected_libraries,
        "existing_target_files": merged_existing_target_files,
        "module_interface_contract": module_interface_contract,
        "delivery_plan_document": delivery_plan_document,
        "delivery_depth_contract": delivery_depth_contract,
        "behavior_contract": _mapping(delivery_depth_contract.get("behavior_contract")),
        "constraints": constraints,
        "context": context,
        "pm_task": contract_fields["task"],
        "pm_contract_hash": pm_contract_hash,
        "contract_hash": pm_contract_hash,
        **blueprint_portfolio_projection,
        "director_execution_profile": director_execution_profile,
        "task_execution_profile": director_execution_profile,
        "execution_profile_ref": execution_profile_ref,
        "execution_profile_hash": execution_profile_hash,
        "director_execution_profile_hash": execution_profile_hash,
        "task_execution_profile_hash": execution_profile_hash,
        "llm_blueprint": llm_blueprint_overlay,
        "ce_handoff": {
            "schema_version": "chief_engineer.handoff_context.v1",
            "llm_blueprint_consumed": bool(llm_blueprint_overlay),
            "llm_blueprint_authority": "advisory_only",
            "contract_authority": "pm_task_contract",
            "scope_authority": "runtime_target_files_or_declared_scopes",
        },
        "contract_completeness": contract_completeness,
        "handoff_ready": bool(contract_completeness["handoff_ready"]),
        "recommendations": list(recommendations),
        "risks": list(risks),
        "created_at": now,
        "updated_at": now,
    }

    # Governance determines whether the blueprint may be handed off. Compute it
    # in memory so an allowed blueprint cannot reach disk before its authoritative
    # target-file ownership facts have been durably recorded and verified.
    attach_governance_to_blueprint(command.workspace, blueprint_id, payload, persist=False)
    if bool(payload.get("handoff_ready")):
        import sys as _sys

        _sys.modules[__package__].record_task_file_owners(
            command.workspace,
            str(context.get("cache_root") or ""),
            target_files,
            task_id=command.task_id,
        )
    blueprint_hash = _blueprint_hash(payload)
    payload["blueprint_hash"] = blueprint_hash
    if bool(payload.get("handoff_ready")):
        job_token = _control_plane_job_token(
            workspace=command.workspace,
            task_id=command.task_id,
            payload={
                **context,
                "run_id": command.run_id,
                "factory_run_id": str(context.get("factory_run_id") or command.run_id).strip(),
                "project_id": str(
                    context.get("project_id") or context.get("factory_bench_project_id") or command.task_id
                ).strip(),
            },
            blueprint_id=blueprint_id,
            blueprint_path=_blueprint_path(blueprint_id),
            blueprint_hash=blueprint_hash,
            contract_hash=pm_contract_hash,
            target_files=target_files,
            scope_paths=scope_paths,
            acceptance_criteria=acceptance_criteria,
            project_type=str(director_execution_profile.get("project_type") or "").strip(),
            language=str(director_execution_profile.get("language") or "").strip(),
        )
        payload["job_token"] = job_token
        payload["capability_token"] = job_token
    BlueprintPersistence(command.workspace).save(blueprint_id, payload)

    return TaskBlueprintResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        status="generated",
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        blueprint_hash=blueprint_hash,
        summary=summary,
        recommendations=recommendations,
        risks=risks,
        target_files=tuple(target_files),
        acceptance_criteria=tuple(acceptance_criteria),
        execution_checklist=tuple(execution_checklist),
        scope_paths=tuple(scope_paths),
        objective=command.objective,
        dependencies=tuple(dependencies),
        architecture_decisions=architecture_decisions,
        selected_libraries=tuple(selected_libraries),
        existing_target_files=tuple(dict(item) for item in merged_existing_target_files if isinstance(item, dict)),
        module_interface_contract=module_interface_contract,
    )


def get_blueprint_status(query: GetBlueprintStatusQueryV1) -> TaskBlueprintResultV1:
    """Return the latest persisted Chief Engineer blueprint status for a task."""

    persistence = BlueprintPersistence(query.workspace, ensure_directory=False)
    match = _latest_blueprint_for_task(
        persistence,
        task_id=query.task_id,
        run_id=query.run_id,
    )
    if match is None:
        return TaskBlueprintResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            status="missing",
            summary="No Chief Engineer blueprint has been generated for this task.",
        )

    blueprint_id, payload = match
    status = str(payload.get("status") or "generated").strip() or "generated"
    blueprint_hash = str(payload.get("blueprint_hash") or "").strip() or _blueprint_hash(payload)
    return TaskBlueprintResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        status=status,
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        blueprint_hash=blueprint_hash,
        summary=str(payload.get("summary") or "").strip(),
        recommendations=_tuple_from_payload(payload.get("recommendations")),
        risks=_tuple_from_payload(payload.get("risks")),
        # D-05: Rich blueprint fields for Director context injection
        target_files=_tuple_from_payload(payload.get("target_files")),
        acceptance_criteria=_tuple_from_payload(payload.get("acceptance_criteria")),
        execution_checklist=_tuple_from_payload(payload.get("execution_checklist")),
        scope_paths=_tuple_from_payload(payload.get("scope_paths")),
        objective=str(payload.get("objective") or "").strip(),
        dependencies=_tuple_from_payload(payload.get("dependencies")),
        architecture_decisions=normalize_architecture_decisions(payload.get("architecture_decisions")),
        selected_libraries=_tuple_from_payload(payload.get("selected_libraries")),
        existing_target_files=_existing_target_files_from_payload(payload),
        module_interface_contract=_mapping(payload.get("module_interface_contract")),
    )
