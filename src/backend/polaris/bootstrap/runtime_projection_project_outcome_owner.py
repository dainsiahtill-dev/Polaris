"""Bootstrap adapter for authoritative non-Factory ProjectOutcome facts.

This composition adapter joins only public Chief Engineer, TaskRuntime, and
Run Ledger read models.  It owns no execution state and creates no completion
verdict; the ``runtime.projection`` cell remains the sole reducer and authority
binder.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public import (
    ProjectCompletionContractV1,
    QueryProjectCompletionContractV1,
    query_project_completion_contract,
)
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    RunLedgerProjectionResultV1,
    read_run_ledger_projection,
)
from polaris.cells.factory.pipeline.public import (
    GetFactoryTerminalTaskRuntimeProjectionQueryV1,
    get_factory_terminal_task_runtime_projection,
)
from polaris.cells.runtime.projection.public import (
    DeliveryAxisV1,
    ProjectOutcomeNonFactoryEvidenceRefsV1,
    ProjectOutcomeNonFactoryOwnerObservationV1,
    ProjectOutcomeNonFactoryOwnerProjectionHashesV1,
    ProjectOutcomeOwnerObservationV1Error,
    QaAxisV1,
    RunLedgerAxisV1,
    TaskBoundaryAxisV1,
    TaskRuntimeAxisV1,
)
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_project_outcome_non_factory_owner_observation_port,
)
from polaris.cells.runtime.task_runtime.public import (
    ObservableTaskRowsProjectionV1,
    query_observable_task_rows,
)

_REQUIRED_PHYSICAL_MODALITIES = frozenset({"code", "environment_prep", "command", "verifier"})


def _fail(error_code: str, message: str) -> ProjectOutcomeOwnerObservationV1Error:
    return ProjectOutcomeOwnerObservationV1Error(error_code, message)


def _mapping(value: object, *, error_code: str, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(error_code, f"{field_name} must be a mapping")
    return dict(value)


def _mapping_list(value: object, *, error_code: str, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise _fail(error_code, f"{field_name} must be a list or tuple")
    rows: list[dict[str, Any]] = []
    for item in value:
        rows.append(_mapping(item, error_code=error_code, field_name=field_name))
    return rows


def _tokens(value: object, *, error_code: str, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _fail(error_code, f"{field_name} must be a list or tuple")
    tokens: list[str] = []
    for item in value:
        if type(item) is not str:
            raise _fail(error_code, f"{field_name} entries must be exact strings")
        token = item.strip()
        if token:
            tokens.append(token)
    return tuple(sorted(set(tokens)))


def _sha256_token(value: object, *, error_code: str, field_name: str) -> str:
    token = str(value or "").strip().lower()
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise _fail(error_code, f"{field_name} must be a lowercase SHA-256 digest")
    return token


def _validate_task_runtime_rows(
    task_rows: tuple[dict[str, Any], ...],
    *,
    run_id: str,
) -> None:
    """Reject ambiguous or non-durable TaskRuntime owner identities."""

    task_ids: set[str] = set()
    for row in task_rows:
        task_id = str(row.get("task_id") or row.get("id") or "").strip()
        workflow_run_id = str(row.get("workflow_run_id") or row.get("run_id") or "").strip()
        factory_run_id = str(row.get("factory_run_id") or "").strip()
        fact_event_seq = row.get("fact_event_seq")
        if (
            not task_id
            or task_id in task_ids
            or not workflow_run_id
            or factory_run_id != run_id
            or type(fact_event_seq) is not int
            or fact_event_seq <= 0
        ):
            raise _fail(
                "project_outcome_task_runtime_task_identity_invalid",
                "TaskRuntime rows require unique task ids, exact Factory/run identities, "
                "and positive fact event sequences",
            )
        task_ids.add(task_id)


def _owner_task_identity(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    return str(
        metadata_map.get("external_task_id")
        or metadata_map.get("source_task_id")
        or row.get("external_task_id")
        or row.get("source_task_id")
        or row.get("pm_task_id")
        or row.get("task_id")
        or row.get("id")
        or ""
    ).strip()


def _task_runtime_authority_for_run(
    task_projection: ObservableTaskRowsProjectionV1,
    *,
    workspace: str,
    run_id: str,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Merge Factory's frozen terminal epoch with newer live repair facts."""

    live_authority = task_projection.to_authority_dict(factory_run_id=run_id)
    live_rows = live_authority.get("rows")
    candidates = [
        dict(row)
        for row in (live_rows if isinstance(live_rows, list) else ())
        if isinstance(row, Mapping)
        and str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
    ]
    frozen = get_factory_terminal_task_runtime_projection(
        GetFactoryTerminalTaskRuntimeProjectionQueryV1(
            workspace=workspace,
            factory_run_id=run_id,
        )
    )
    frozen_authority = dict(frozen.projection) if frozen is not None else None
    frozen_rows = frozen_authority.get("rows") if frozen_authority is not None else ()
    candidates[:0] = [
        dict(row) for row in (frozen_rows if isinstance(frozen_rows, list) else ()) if isinstance(row, Mapping)
    ]

    rows_by_owner: dict[str, dict[str, Any]] = {}
    for row in candidates:
        owner_task_id = _owner_task_identity(row)
        if not owner_task_id:
            continue
        existing = rows_by_owner.get(owner_task_id)
        if existing is None or int(row.get("fact_event_seq") or 0) >= int(existing.get("fact_event_seq") or 0):
            rows_by_owner[owner_task_id] = row
    rows = tuple(rows_by_owner[owner_task_id] for owner_task_id in sorted(rows_by_owner))

    authority = dict(frozen_authority or live_authority)
    authority.update(
        {
            "rows": [dict(row) for row in rows],
            "row_count": len(rows),
            "total_row_count": len(rows),
        }
    )
    return rows, authority


def _validate_run_ledger_commitments(
    *,
    ledger: Mapping[str, Any],
    project: Mapping[str, Any],
    run_projection: Mapping[str, Any],
    capability: Mapping[str, Any],
    gates: list[dict[str, Any]],
    owner_missing: tuple[str, ...],
    owner_failed: tuple[str, ...],
) -> None:
    """Validate committed, internally consistent public Run Ledger evidence."""

    if (
        ledger.get("source") != "run_ledger_projection"
        or ledger.get("migration_ledgers_included") is not False
        or run_projection.get("source") != "run_ledger"
        or type(run_projection.get("event_count")) is not int
        or int(run_projection["event_count"]) <= 0
        or type(run_projection.get("gate_count")) is not int
        or int(run_projection["gate_count"]) != len(gates)
        or not gates
    ):
        raise _fail(
            "project_outcome_run_ledger_projection_not_committed",
            "Run Ledger projection must be current, non-migrated, non-empty, and event-backed",
        )

    job_token_ids = _tokens(
        capability.get("job_token_ids"),
        error_code="invalid_project_outcome_run_ledger_capability",
        field_name="run_ledger.run_projection.capability.job_token_ids",
    )
    latest_token_id = str(capability.get("latest_token_id") or "").strip()
    if (
        capability.get("ok") is not True
        or capability.get("issues") not in ([], ())
        or not job_token_ids
        or not latest_token_id
        or latest_token_id not in job_token_ids
    ):
        raise _fail(
            "project_outcome_run_ledger_gate_evidence_uncommitted",
            "Run Ledger capability must identify the committed JobToken set",
        )
    _sha256_token(
        capability.get("latest_contract_hash"),
        error_code="invalid_project_outcome_run_ledger_capability",
        field_name="run_ledger.run_projection.capability.latest_contract_hash",
    )
    _sha256_token(
        capability.get("latest_blueprint_hash"),
        error_code="invalid_project_outcome_run_ledger_capability",
        field_name="run_ledger.run_projection.capability.latest_blueprint_hash",
    )
    for gate in gates:
        content_id = str(gate.get("content_id") or "").strip()
        append_id = str(gate.get("append_id") or "").strip()
        gate_token_id = str(gate.get("job_token_id") or "").strip()
        if (not content_id and not append_id) or not gate_token_id or gate_token_id not in job_token_ids:
            raise _fail(
                "project_outcome_run_ledger_gate_evidence_uncommitted",
                "Every Run Ledger gate requires a committed event reference and bound JobToken",
            )

    expected_missing = set(owner_missing)
    expected_failed = set(owner_failed)
    state_projections: tuple[tuple[str, Mapping[str, Any]], ...] = (
        ("run_ledger", ledger),
        ("run_ledger.project", project),
        (
            "run_ledger.run_projection.evidence_policy",
            _mapping(
                run_projection.get("evidence_policy"),
                error_code="invalid_project_outcome_evidence_policy",
                field_name="run_ledger.run_projection.evidence_policy",
            ),
        ),
        (
            "run_ledger.project.evidence_policy",
            _mapping(
                project.get("evidence_policy"),
                error_code="invalid_project_outcome_evidence_policy",
                field_name="run_ledger.project.evidence_policy",
            ),
        ),
    )
    for field_prefix, projection in state_projections:
        projected_missing = set(
            _tokens(
                projection.get("missing_required_modalities"),
                error_code="invalid_project_outcome_missing_required_modalities",
                field_name=f"{field_prefix}.missing_required_modalities",
            )
        )
        projected_failed = set(
            _tokens(
                projection.get("failed_required_modalities"),
                error_code="invalid_project_outcome_failed_required_modalities",
                field_name=f"{field_prefix}.failed_required_modalities",
            )
        )
        if projected_missing != expected_missing or projected_failed != expected_failed:
            raise _fail(
                "project_outcome_required_modality_projection_mismatch",
                "Run Ledger required-modality states disagree across public projections",
            )


def _canonical_hash(*, axis: str, identity: Mapping[str, str], payload: object) -> str:
    envelope = {
        "schema_version": "bootstrap.project-outcome-owner-projection/1",
        "axis": axis,
        "identity": dict(identity),
        "payload": payload,
    }
    try:
        raw = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail(
            "project_outcome_owner_projection_not_canonical",
            f"Owner projection for {axis} is not canonical JSON: {exc}",
        ) from exc
    return hashlib.sha256(raw).hexdigest()


def _modality_state(modalities: Mapping[str, Any], name: str) -> tuple[bool, bool]:
    raw = modalities.get(name)
    if not isinstance(raw, Mapping):
        return False, False
    present = int(raw.get("present") or 0) > 0
    ok = present and int(raw.get("ok") or 0) > 0 and int(raw.get("failed") or 0) == 0
    return present, ok


def _entrypoint_state(gates: list[dict[str, Any]]) -> tuple[bool, bool]:
    present = False
    ok = False
    for gate in gates:
        modalities = gate.get("evidence_modalities")
        if not isinstance(modalities, Mapping):
            continue
        browser = modalities.get("browser")
        if isinstance(browser, Mapping) and bool(browser.get("present")):
            present = True
            ok = ok or bool(browser.get("ok"))
        command = modalities.get("command")
        if isinstance(command, Mapping):
            metadata = command.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else {}
            if str(metadata_map.get("entrypoint_kind") or "").strip():
                present = True
                ok = ok or (bool(command.get("present")) and bool(command.get("ok")))
    return present, ok


def _qa_gate_state(gates: list[dict[str, Any]]) -> tuple[bool, bool]:
    qa_gates = [
        gate
        for gate in gates
        if str(gate.get("stage") or "").strip().lower() == "qa" or str(gate.get("name") or "").strip().lower() == "qa"
    ]
    if not qa_gates:
        return False, False
    return True, all(
        bool(gate.get("ok"))
        and bool(gate.get("capability_ok"))
        and not gate.get("missing_required_evidence_modalities")
        and not gate.get("failed_required_evidence_modalities")
        for gate in qa_gates
    )


def _validate_task_boundary(
    *,
    task_boundary: Mapping[str, Any],
    expected_task_ids: tuple[str, ...],
) -> tuple[TaskBoundaryAxisV1, tuple[str, ...]]:
    reasons: list[str] = []
    latest_by_task = task_boundary.get("latest_by_task")
    latest = latest_by_task if isinstance(latest_by_task, Mapping) else {}
    expected_tasks = set(expected_task_ids)
    if set(latest) != expected_tasks:
        reasons.append("task_boundary_task_set_mismatch")
    for task_id in expected_task_ids:
        verdict = latest.get(task_id)
        if not isinstance(verdict, Mapping):
            continue
        if not bool(verdict.get("ok")) or str(verdict.get("status") or "").strip() != "completed_verified":
            reasons.append(f"task_boundary_not_completed_verified:{task_id}")
        verdict_run_id = str(verdict.get("run_id") or "").strip()
        if not verdict_run_id:
            reasons.append(f"task_boundary_run_identity_mismatch:{task_id}")
        for field_name in (
            "missing_target_files",
            "missing_entrypoint_targets",
            "unresolved_local_imports",
            "artifact_semantic_mismatches",
            "downstream_pending_artifacts",
            "blocked_dependencies",
            "missing_required_evidence_modalities",
            "failed_required_evidence_modalities",
            "missing_required_verifiers",
            "failed_required_verifiers",
        ):
            if verdict.get(field_name):
                reasons.append(f"task_boundary_{field_name}:{task_id}")
        if not verdict.get("evidence_refs"):
            reasons.append(f"task_boundary_evidence_refs_missing:{task_id}")
    if not bool(task_boundary.get("ok")) or task_boundary.get("failed"):
        reasons.append("task_boundary_owner_projection_failed")
    return (
        TaskBoundaryAxisV1.PASSED if not reasons else TaskBoundaryAxisV1.FAILED,
        tuple(sorted(set(reasons))),
    )


class ProjectOutcomeNonFactoryOwnerObservationAdapter:
    """Join exact TaskRuntime and Run Ledger public owner projections."""

    async def observe_project_outcome_non_factory(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectOutcomeNonFactoryOwnerObservationV1:
        canonical_workspace = str(Path(workspace).expanduser().resolve())
        identity = {
            "workspace": canonical_workspace,
            "project_id": project_id,
            "run_id": run_id,
            "completion_contract_hash": completion_contract_hash,
        }

        completion_contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=canonical_workspace,
                project_id=project_id,
                run_id=run_id,
                contract_hash=completion_contract_hash,
            )
        )
        if type(completion_contract) is not ProjectCompletionContractV1:
            raise _fail(
                "invalid_project_outcome_completion_contract_type",
                "Chief Engineer owner query must return an exact ProjectCompletionContractV1",
            )
        if (
            completion_contract.project_id,
            completion_contract.run_id,
            completion_contract.contract_hash,
        ) != (project_id, run_id, completion_contract_hash):
            raise _fail(
                "project_outcome_completion_contract_identity_mismatch",
                "Chief Engineer completion contract does not match the requested authority identity",
            )

        task_projection = query_observable_task_rows(canonical_workspace)
        if type(task_projection) is not ObservableTaskRowsProjectionV1:
            raise _fail(
                "invalid_project_outcome_task_runtime_projection_type",
                "TaskRuntime owner query must return an exact ObservableTaskRowsProjectionV1",
            )
        if task_projection.workspace != canonical_workspace:
            raise _fail(
                "project_outcome_task_runtime_workspace_mismatch",
                "TaskRuntime projection workspace does not match the requested workspace",
            )
        if not task_projection.authoritative or task_projection.degraded:
            raise _fail(
                "project_outcome_task_runtime_projection_degraded",
                "TaskRuntime projection must be authoritative and non-degraded",
            )
        task_rows, task_authority = _task_runtime_authority_for_run(
            task_projection,
            workspace=canonical_workspace,
            run_id=run_id,
        )
        if not task_rows:
            raise _fail(
                "project_outcome_task_runtime_rows_empty",
                "TaskRuntime projection has no rows bound to the exact Factory run",
            )
        _validate_task_runtime_rows(task_rows, run_id=run_id)
        if int(task_authority.get("row_count") or 0) != len(task_rows):
            raise _fail(
                "project_outcome_task_runtime_row_count_mismatch",
                "TaskRuntime authority projection row count is inconsistent",
            )

        ledger_result = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=canonical_workspace,
                run_id=run_id,
                factory_run_id=run_id,
                project_id=project_id,
                include_migration_ledgers=False,
            )
        )
        if type(ledger_result) is not RunLedgerProjectionResultV1:
            raise _fail(
                "invalid_project_outcome_run_ledger_projection_type",
                "Run Ledger owner query must return an exact RunLedgerProjectionResultV1",
            )
        ledger = _mapping(
            ledger_result.projection,
            error_code="invalid_project_outcome_run_ledger_projection",
            field_name="run_ledger_projection",
        )
        expected_scope = {
            "run_id": run_id,
            "factory_run_id": run_id,
            "project_id": project_id,
        }
        if ledger.get("query_scope") != expected_scope:
            raise _fail(
                "project_outcome_run_ledger_scope_mismatch",
                "Run Ledger projection does not match the exact run/project scope",
            )
        projects = _mapping_list(
            ledger.get("projects"),
            error_code="invalid_project_outcome_run_ledger_projects",
            field_name="run_ledger.projects",
        )
        if len(projects) != 1 or str(projects[0].get("project_id") or "").strip() != project_id:
            raise _fail(
                "project_outcome_run_ledger_project_mismatch",
                "Run Ledger projection must contain exactly the requested project",
            )
        project = projects[0]
        if not bool(ledger.get("available")) or int(ledger.get("projected") or 0) != 1:
            raise _fail(
                "project_outcome_run_ledger_projection_empty",
                "Run Ledger projection must be available and non-empty",
            )

        run_projection = _mapping(
            ledger.get("run_projection"),
            error_code="invalid_project_outcome_run_ledger_projection",
            field_name="run_ledger.run_projection",
        )
        capability = _mapping(
            run_projection.get("capability"),
            error_code="invalid_project_outcome_run_ledger_capability",
            field_name="run_ledger.run_projection.capability",
        )
        consumed_run_ids = _tokens(
            ledger.get("consumed_run_ids"),
            error_code="invalid_project_outcome_consumed_run_ids",
            field_name="run_ledger.consumed_run_ids",
        )
        current_project_run_ids = {
            str(row.get("workflow_run_id") or row.get("run_id") or "").strip()
            for row in task_rows
            if _owner_task_identity(row) in completion_contract.covered_task_ids
        }
        current_project_run_ids.discard("")
        current_project_run_ids.discard(run_id)
        if run_id not in consumed_run_ids or not current_project_run_ids.issubset(consumed_run_ids):
            raise _fail(
                "project_outcome_consumed_run_ids_mismatch",
                "Run Ledger must consume the Factory parent and every current Director child run",
            )

        evidence_policy = _mapping(
            ledger.get("evidence_policy"),
            error_code="invalid_project_outcome_evidence_policy",
            field_name="run_ledger.evidence_policy",
        )
        owner_missing = _tokens(
            evidence_policy.get("missing_required_modalities"),
            error_code="invalid_project_outcome_missing_required_modalities",
            field_name="evidence_policy.missing_required_modalities",
        )
        owner_failed = _tokens(
            evidence_policy.get("failed_required_modalities"),
            error_code="invalid_project_outcome_failed_required_modalities",
            field_name="evidence_policy.failed_required_modalities",
        )
        if set(owner_missing).intersection(owner_failed):
            raise _fail(
                "project_outcome_required_modality_state_conflict",
                "A required modality cannot be both missing and failed",
            )
        modalities = _mapping(
            ledger.get("evidence_modalities"),
            error_code="invalid_project_outcome_evidence_modalities",
            field_name="run_ledger.evidence_modalities",
        )
        gates = _mapping_list(
            run_projection.get("gates"),
            error_code="invalid_project_outcome_run_ledger_gates",
            field_name="run_ledger.run_projection.gates",
        )
        _validate_run_ledger_commitments(
            ledger=ledger,
            project=project,
            run_projection=run_projection,
            capability=capability,
            gates=gates,
            owner_missing=owner_missing,
            owner_failed=owner_failed,
        )

        effective_missing = set(owner_missing)
        effective_failed = set(owner_failed)
        delivery_missing = {
            item for item in owner_missing if item in _REQUIRED_PHYSICAL_MODALITIES or item == "entrypoint"
        }
        delivery_failed = {
            item for item in owner_failed if item in _REQUIRED_PHYSICAL_MODALITIES or item == "entrypoint"
        }
        reasons: list[str] = []
        for modality in sorted(_REQUIRED_PHYSICAL_MODALITIES):
            present, ok = _modality_state(modalities, modality)
            if not present:
                effective_missing.add(modality)
                delivery_missing.add(modality)
                reasons.append(f"{modality}_evidence_missing")
            elif not ok:
                effective_failed.add(modality)
                delivery_failed.add(modality)
                reasons.append(f"{modality}_evidence_failed")
        entrypoint_present, entrypoint_ok = _entrypoint_state(gates)
        if not entrypoint_present:
            effective_missing.add("entrypoint")
            delivery_missing.add("entrypoint")
            reasons.append("entrypoint_evidence_missing")
        elif not entrypoint_ok:
            effective_failed.add("entrypoint")
            delivery_failed.add("entrypoint")
            reasons.append("entrypoint_evidence_failed")
        qa_present, qa_ok = _qa_gate_state(gates)
        if not qa_present:
            effective_missing.add("qa")
            reasons.append("qa_evidence_missing")
        elif not qa_ok:
            effective_failed.add("qa")
            reasons.append("qa_evidence_failed")
        effective_missing.difference_update(effective_failed)
        delivery_missing.difference_update(delivery_failed)

        completed_rows = tuple(
            row
            for row in task_rows
            if str(row.get("execution_state") or row.get("status") or "").strip().lower() == "completed"
        )
        task_runtime_axis = (
            TaskRuntimeAxisV1.CONVERGED if len(completed_rows) == len(task_rows) else TaskRuntimeAxisV1.NOT_CONVERGED
        )
        if task_runtime_axis is TaskRuntimeAxisV1.NOT_CONVERGED:
            reasons.append("task_runtime_not_converged")

        task_boundary = _mapping(
            ledger.get("task_boundary"),
            error_code="invalid_project_outcome_task_boundary_projection",
            field_name="run_ledger.task_boundary",
        )
        task_boundary_axis, boundary_reasons = _validate_task_boundary(
            task_boundary=task_boundary,
            expected_task_ids=completion_contract.covered_task_ids,
        )
        reasons.extend(boundary_reasons)

        code_present, code_ok = _modality_state(modalities, "code")
        delivery_ready = code_ok and not delivery_missing and not delivery_failed
        delivery_axis = (
            DeliveryAxisV1.VERIFIED
            if delivery_ready
            else DeliveryAxisV1.PRESENT_UNVERIFIED
            if code_present
            else DeliveryAxisV1.MISSING
        )
        qa_axis = QaAxisV1.PASSED if qa_present and qa_ok else QaAxisV1.FAILED if qa_present else QaAxisV1.NOT_RUN
        run_ledger_closed = (
            bool(ledger.get("ok"))
            and str(ledger.get("status") or "").strip() == "ready"
            and bool(run_projection.get("ok"))
            and bool(run_projection.get("integrity_ok"))
            and bool(run_projection.get("outcome_ok"))
            and bool(capability.get("ok"))
            and not effective_missing
            and not effective_failed
            and qa_axis is QaAxisV1.PASSED
            and task_boundary_axis is TaskBoundaryAxisV1.PASSED
        )
        run_ledger_axis = RunLedgerAxisV1.CLOSED if run_ledger_closed else RunLedgerAxisV1.NOT_CLOSED

        task_runtime_hash = _canonical_hash(axis="task_runtime", identity=identity, payload=task_authority)
        delivery_hash = _canonical_hash(
            axis="delivery",
            identity=identity,
            payload={"evidence_modalities": modalities, "gates": gates},
        )
        qa_hash = _canonical_hash(axis="qa", identity=identity, payload={"gates": gates})
        task_boundary_hash = _canonical_hash(
            axis="task_boundary",
            identity=identity,
            payload=task_boundary,
        )
        run_ledger_hash = _canonical_hash(axis="run_ledger", identity=identity, payload=ledger)
        hashes = ProjectOutcomeNonFactoryOwnerProjectionHashesV1(
            delivery=delivery_hash,
            qa=qa_hash,
            task_boundary=task_boundary_hash,
            task_runtime=task_runtime_hash,
            run_ledger=run_ledger_hash,
        )
        refs = ProjectOutcomeNonFactoryEvidenceRefsV1(
            delivery=(delivery_hash,),
            qa=(qa_hash,),
            task_boundary=(task_boundary_hash,),
            task_runtime=(task_runtime_hash,),
            run_ledger=(run_ledger_hash,),
        )
        return ProjectOutcomeNonFactoryOwnerObservationV1(
            workspace=canonical_workspace,
            project_id=project_id,
            run_id=run_id,
            completion_contract_hash=completion_contract_hash,
            delivery=delivery_axis,
            qa=qa_axis,
            task_boundary=task_boundary_axis,
            task_runtime=task_runtime_axis,
            run_ledger=run_ledger_axis,
            evidence_refs=refs,
            projection_hashes=hashes,
            missing_required_modalities=tuple(sorted(effective_missing)),
            failed_required_modalities=tuple(sorted(effective_failed)),
            reasons=tuple(sorted(set(reasons))),
            task_count=len(task_rows),
            completed_task_count=len(completed_rows),
        )


PROJECT_OUTCOME_NON_FACTORY_OWNER_OBSERVATION_ADAPTER = ProjectOutcomeNonFactoryOwnerObservationAdapter()


def configure_runtime_projection_project_outcome_owner() -> None:
    """Bind the singleton adapter into runtime projection during bootstrap."""
    bind_project_outcome_non_factory_owner_observation_port(PROJECT_OUTCOME_NON_FACTORY_OWNER_OBSERVATION_ADAPTER)


__all__ = [
    "PROJECT_OUTCOME_NON_FACTORY_OWNER_OBSERVATION_ADAPTER",
    "ProjectOutcomeNonFactoryOwnerObservationAdapter",
    "configure_runtime_projection_project_outcome_owner",
]
