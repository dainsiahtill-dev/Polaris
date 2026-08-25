"""Adversarial tests for durable project-completion orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty
from typing import Any

import pytest
from polaris.cells.factory.verification_guard.public.contracts import (
    _PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN,
    ProjectCompletionDiagnosticsV1,
    ProjectCompletionDiagnosticV1,
)
from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_convergence import (
    ProjectCompletionConvergenceEngineV1,
    _workflow_id,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionActionCommandV1,
    ProjectCompletionActionReceiptV1,
    ProjectCompletionDispatchClaimV1,
    ProjectCompletionIdentityV1,
    project_completion_action_receipt_hash,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingAttemptObservationV1,
    ModelCeilingCandidateV1,
    ModelCeilingOwnerObservationV1,
    ModelCeilingQualificationV1,
    ModelCeilingTerminalResultV1,
    model_ceiling_attempt_request_binding_hash,
    qualify_model_ceiling,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling_bootstrap import (
    bind_model_ceiling_owner_observation_port,
    clear_model_ceiling_owner_observation_port,
)
from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    compose_project_completion_cursor,
)
from polaris.cells.runtime.projection.public.contracts import (
    _PROJECT_OUTCOME_AUTHORITY_BINDING_TOKEN,
    _PROJECT_OUTCOME_AUTHORITY_TOKEN,
    ChainAxisV1,
    DeliveryAxisV1,
    ProjectOutcomeAuthorityBindingV1,
    ProjectOutcomeEvidenceRefsV1,
    ProjectOutcomeNonFactoryEvidenceRefsV1,
    ProjectOutcomeNonFactoryOwnerProjectionHashesV1,
    ProjectOutcomeOwnerObservationV1Error,
    ProjectOutcomeV1,
    QaAxisV1,
    RecommendedDispositionV1,
    RunLedgerAxisV1,
    TaskBoundaryAxisV1,
    TaskRuntimeAxisV1,
)
from polaris.infrastructure.db.repositories.workflow_runtime_store import (
    _PROJECT_COMPLETION_CURSOR_AUTHORITY_TOKEN,
    SqliteRuntimeStore,
    WorkflowEventVersionConflictError,
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _identity(tmp_path: Path, *, project_id: str = "project-a") -> ProjectCompletionIdentityV1:
    return ProjectCompletionIdentityV1(
        workspace=str(tmp_path / "workspace"),
        project_id=project_id,
        run_id="run-1",
        completion_contract_hash=_hash("completion-contract"),
    )


def _owner_receipt(
    command: ProjectCompletionActionCommandV1,
    *,
    lease_id: str,
    settlement_id: str,
    status: str = "accepted",
) -> ProjectCompletionActionReceiptV1:
    effect_hash = _hash(f"effect:{command.action_id}")
    provisional = ProjectCompletionActionReceiptV1(
        identity=command.identity,
        action_id=command.action_id,
        handoff_id=command.action_id,
        diagnostic_id=command.diagnostic_id,
        owner_task_id=command.owner_task_id,
        status=status,
        lease_id=lease_id,
        settlement_id=settlement_id,
        effect_hash=effect_hash,
        receipt_hash=_hash("provisional-receipt"),
    )
    return replace(
        provisional,
        receipt_hash=project_completion_action_receipt_hash(
            identity=provisional.identity,
            action_id=provisional.action_id,
            handoff_id=provisional.handoff_id,
            diagnostic_id=provisional.diagnostic_id,
            owner_task_id=provisional.owner_task_id,
            status=provisional.status,
            lease_id=provisional.lease_id,
            settlement_id=provisional.settlement_id,
            effect_hash=provisional.effect_hash,
        ),
    )


def _binding(
    identity: ProjectCompletionIdentityV1,
    *,
    completed: bool,
    evidence_revision: str = "",
) -> ProjectOutcomeAuthorityBindingV1:
    hashes = ProjectOutcomeNonFactoryOwnerProjectionHashesV1(
        delivery=_hash(f"delivery{evidence_revision}"),
        qa=_hash(f"qa{evidence_revision}"),
        task_boundary=_hash(f"task-boundary{evidence_revision}"),
        task_runtime=_hash(f"task-runtime{evidence_revision}"),
        run_ledger=_hash(f"run-ledger{evidence_revision}"),
    )
    factory_hash = _hash(f"factory{evidence_revision}")
    non_factory_refs = ProjectOutcomeNonFactoryEvidenceRefsV1(
        delivery=(hashes.delivery,),
        qa=(hashes.qa,),
        task_boundary=(hashes.task_boundary,),
        task_runtime=(hashes.task_runtime,),
        run_ledger=(hashes.run_ledger,),
    )
    evidence_refs = ProjectOutcomeEvidenceRefsV1(
        delivery=non_factory_refs.delivery,
        chain=(factory_hash,),
        qa=non_factory_refs.qa,
        task_boundary=non_factory_refs.task_boundary,
        task_runtime=non_factory_refs.task_runtime,
        run_ledger=non_factory_refs.run_ledger,
    )
    if completed:
        qa = QaAxisV1.PASSED
        task_boundary = TaskBoundaryAxisV1.PASSED
        task_runtime = TaskRuntimeAxisV1.CONVERGED
        run_ledger = RunLedgerAxisV1.CLOSED
        blocking_axes: tuple[str, ...] = ()
        disposition = RecommendedDispositionV1.COMPLETE
    else:
        qa = QaAxisV1.FAILED
        task_boundary = TaskBoundaryAxisV1.FAILED
        task_runtime = TaskRuntimeAxisV1.NOT_CONVERGED
        run_ledger = RunLedgerAxisV1.NOT_CLOSED
        blocking_axes = ("qa", "run_ledger", "task_boundary", "task_runtime")
        disposition = RecommendedDispositionV1.REPAIR
    outcome = ProjectOutcomeV1(
        run_id=identity.run_id,
        delivery=DeliveryAxisV1.VERIFIED,
        chain=ChainAxisV1.COMPLETED,
        qa=qa,
        task_boundary=task_boundary,
        task_runtime=task_runtime,
        run_ledger=run_ledger,
        missing_required_modalities=(),
        failed_required_modalities=(),
        completion_candidate=completed,
        authority_bound=True,
        completed_verified=completed,
        recommended_disposition=disposition,
        evidence_refs=evidence_refs,
        reasons=(),
        blocking_axes=blocking_axes,
        task_count=2,
        completed_task_count=2,
        _authority_token=_PROJECT_OUTCOME_AUTHORITY_TOKEN,
    )
    return ProjectOutcomeAuthorityBindingV1(
        outcome=outcome,
        workspace=identity.workspace,
        project_id=identity.project_id,
        run_id=identity.run_id,
        completion_contract_hash=identity.completion_contract_hash,
        factory_chain_projection_hash=factory_hash,
        factory_chain_evidence_refs=(factory_hash,),
        non_factory_projection_hashes=hashes,
        non_factory_evidence_refs=non_factory_refs,
        _authority_token=_PROJECT_OUTCOME_AUTHORITY_BINDING_TOKEN,
    )


def _diagnostic(
    diagnostic_id: str = "diag-a",
    *,
    dependencies: tuple[str, ...] = (),
) -> ProjectCompletionDiagnosticV1:
    dependency_blocked = bool(dependencies)
    return ProjectCompletionDiagnosticV1(
        diagnostic_id=diagnostic_id,
        archetype="missing_owned_artifact",
        evidence_state="missing",
        primary_module_id="M03",
        obligation_id=f"obligation-{diagnostic_id}",
        owner_task_id=f"task-{diagnostic_id}",
        affected_target=f"src/{diagnostic_id}.py",
        owner_evidence_refs=(_hash(f"owner-{diagnostic_id}"),),
        retry_class="dependency_blocked" if dependency_blocked else "owner_rework",
        allowed_next_action="wait_for_dependencies" if dependency_blocked else "publish_owner_rework",
        dependency_ids=dependencies,
        repair_coverage="unknown",
        repair_source_tool=None,
        repair_coverage_evidence_ref=None,
        repair_coverage_evidence_hash=None,
        required_verifier_ids=(),
    )


def _diagnostics(
    identity: ProjectCompletionIdentityV1,
    diagnostics: tuple[ProjectCompletionDiagnosticV1, ...] = (_diagnostic(),),
) -> ProjectCompletionDiagnosticsV1:
    return ProjectCompletionDiagnosticsV1(
        workspace=identity.workspace,
        project_id=identity.project_id,
        run_id=identity.run_id,
        completion_contract_hash=identity.completion_contract_hash,
        owner_bundle_hash=_hash("owner-bundle"),
        diagnostics=diagnostics,
        passed_obligation_ids=(),
        missing_obligation_ids=tuple(item.obligation_id for item in diagnostics),
        failed_obligation_ids=(),
        non_blocking_obligation_ids=(),
        _authority_token=_PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN,
    )


class MutableOutcomePort:
    def __init__(self, binding: object, trace: list[str] | None = None) -> None:
        self.binding = binding
        self.calls = 0
        self.trace = trace

    async def query_project_completion_outcome(self, identity: ProjectCompletionIdentityV1) -> object:
        del identity
        self.calls += 1
        if self.trace is not None:
            self.trace.append("outcome")
        return self.binding


class MutableDiagnosticsPort:
    def __init__(self, diagnostics: object) -> None:
        self.diagnostics = diagnostics
        self.calls = 0

    async def query_project_completion_diagnostics(self, identity: ProjectCompletionIdentityV1) -> object:
        del identity
        self.calls += 1
        return self.diagnostics


class FailingOutcomePort:
    async def query_project_completion_outcome(self, identity: ProjectCompletionIdentityV1) -> object:
        del identity
        raise ProjectOutcomeOwnerObservationV1Error(
            "project_outcome_pm_contract_hash_mismatch",
            "Run Ledger capability is bound to a different PM contract",
        )


class MutableModelCeilingPort:
    def __init__(self, result: object = None) -> None:
        self.result = result
        self.calls = 0

    async def query_project_completion_model_ceiling(
        self,
        identity: ProjectCompletionIdentityV1,
        diagnostic_id: str,
    ) -> object:
        del identity, diagnostic_id
        self.calls += 1
        return self.result


class MutableModelCeilingOwnerObservationPort:
    def __init__(self, observation: ModelCeilingOwnerObservationV1) -> None:
        self.observation = observation

    def observe_model_ceiling(self, candidate: ModelCeilingCandidateV1) -> ModelCeilingOwnerObservationV1:
        del candidate
        return self.observation


def _sealed_model_ceiling(
    identity: ProjectCompletionIdentityV1,
    request: pytest.FixtureRequest,
) -> tuple[ModelCeilingTerminalResultV1, MutableModelCeilingOwnerObservationPort]:
    call_id = "call-model-ceiling"
    snapshot_ref = "a" * 24
    request_hash = _hash("context-request")
    request_freeze_id = "freeze-model-ceiling"
    semantic_request_hash = _hash("semantic-request")
    physical_wire_hash = _hash("physical-wire")
    composite_request_hash = _hash("composite-request")
    provider_request_id = "provider-request-model-ceiling"
    owner_request_binding_hash = model_ceiling_attempt_request_binding_hash(
        call_id=call_id,
        context_snapshot_ref=snapshot_ref,
        request_hash=request_hash,
        request_freeze_id=request_freeze_id,
        semantic_request_hash=semantic_request_hash,
        physical_wire_hash=physical_wire_hash,
        composite_request_hash=composite_request_hash,
        provider_request_id=provider_request_id,
        authority_attempt_ordinal=1,
        attempt_budget=1,
    )
    attempt = ModelCeilingAttemptObservationV1(
        lifecycle_event_hash=_hash("lifecycle"),
        material_effect_receipt_hash=_hash("material-effect"),
        verifier_receipt_hash=_hash("verifier-receipt"),
        repair_coverage_ref=_hash("repair-coverage"),
        workspace=identity.workspace,
        factory_run_id="factory-run-1",
        run_id=identity.run_id,
        project_id=identity.project_id,
        completion_contract_hash=identity.completion_contract_hash,
        diagnostic_id="diag-a",
        owner_task_id="task-diag-a",
        call_id=call_id,
        context_snapshot_ref=snapshot_ref,
        request_hash=request_hash,
        request_freeze_id=request_freeze_id,
        semantic_request_hash=semantic_request_hash,
        physical_wire_hash=physical_wire_hash,
        composite_request_hash=composite_request_hash,
        owner_request_binding_hash=owner_request_binding_hash,
        role_id="director",
        provider_id="provider-a",
        model="model-a",
        provider_request_id=provider_request_id,
        authority_attempt_ordinal=1,
        attempt_budget=1,
        terminal_status="completed",
        round_number=1,
        max_rounds=1,
        before_artifact_hash=_hash("artifact-before"),
        after_artifact_hash=_hash("artifact-after"),
        verifier_obligation_id="build",
        verifier_argv=("npm", "run", "build"),
        verifier_cwd=".",
        verifier_exit_code=2,
        verifier_timed_out=False,
        verifier_output_hash=_hash("verifier-output"),
        verifier_proof_satisfied=False,
        failure_semantic_class="model_capability_ceiling",
        failure_origin="artifact_semantic",
        provider_blocker_observed=False,
        control_plane_blocker_observed=False,
        environment_blocker_observed=False,
        sandbox_blocker_observed=False,
        executable_repair_available=False,
    )
    observation = ModelCeilingOwnerObservationV1(
        workspace=identity.workspace,
        project_id=identity.project_id,
        run_id=identity.run_id,
        factory_run_id="factory-run-1",
        completion_contract_hash=identity.completion_contract_hash,
        diagnostic_id="diag-a",
        provider_call_id=call_id,
        final_request_snapshot_ref=snapshot_ref,
        final_request_status="available",
        request_hash=request_hash,
        role_id="director",
        provider_id="provider-a",
        model="model-a",
        role_identity_ok=True,
        coverage_pass=True,
        required_refs=("completion_contract", "diagnostic_feedback"),
        included_refs=("completion_contract", "diagnostic_feedback"),
        missing_required_refs=(),
        required_tools=("edit_file", "execute_command"),
        available_tools=("edit_file", "execute_command"),
        actual_tools=("edit_file", "execute_command"),
        missing_required_tools=(),
        attempts=(attempt,),
    )
    owner = MutableModelCeilingOwnerObservationPort(observation)
    bind_model_ceiling_owner_observation_port(owner)
    request.addfinalizer(lambda: clear_model_ceiling_owner_observation_port(owner))
    candidate = ModelCeilingCandidateV1(
        workspace=identity.workspace,
        project_id=identity.project_id,
        run_id=identity.run_id,
        factory_run_id="factory-run-1",
        completion_contract_hash=identity.completion_contract_hash,
        diagnostic_id="diag-a",
        provider_call_id=call_id,
        final_request_snapshot_ref=snapshot_ref,
    )
    result = qualify_model_ceiling(candidate)
    assert result.terminal is True
    return result, owner


def _model_ceiling_result(
    identity: ProjectCompletionIdentityV1,
    *,
    diagnostic_id: str = "diag-a",
    qualified: bool = True,
    model: str = "model-a",
) -> ModelCeilingTerminalResultV1:
    qualification = (
        ModelCeilingQualificationV1(
            workspace=identity.workspace,
            project_id=identity.project_id,
            run_id=identity.run_id,
            factory_run_id="factory-run-1",
            completion_contract_hash=identity.completion_contract_hash,
            diagnostic_id=diagnostic_id,
            semantic_class="model_capability_ceiling",
            role_id="director",
            provider_id="provider-a",
            model=model,
            provider_call_id="call-a",
            request_hash="8" * 64,
            final_request_snapshot_ref="a" * 24,
            round_count=3,
            max_rounds=3,
            round_request_binding_hashes=(_hash("round-1"), _hash("round-2"), _hash("round-3")),
            evidence_refs=(_hash("model-ceiling-evidence"),),
        )
        if qualified
        else None
    )
    return ModelCeilingTerminalResultV1(
        workspace=identity.workspace,
        project_id=identity.project_id,
        run_id=identity.run_id,
        factory_run_id="factory-run-1",
        completion_contract_hash=identity.completion_contract_hash,
        diagnostic_id=diagnostic_id,
        status="MODEL_CEILING_QUALIFIED" if qualified else "CONTROL_PLANE_BLOCKED",
        routing_disposition="stop" if qualified else "park",
        reason_codes=("model_ceiling_qualified",) if qualified else ("owner_evidence_incomplete",),
        qualification=qualification,
    )


class IdempotentTaskMarketStyleActionPort:
    """Production-shape fake: action_id is the durable handoff/idempotency key."""

    def __init__(self, trace: list[str] | None = None) -> None:
        self.query_calls: list[str] = []
        self.dispatch_calls: list[str] = []
        self.effects: list[str] = []
        self.receipts: dict[str, ProjectCompletionActionReceiptV1] = {}
        self.trace = trace

    async def query_project_completion_action_receipt(
        self,
        command: ProjectCompletionActionCommandV1,
    ) -> ProjectCompletionActionReceiptV1 | None:
        self.query_calls.append(command.action_id)
        if self.trace is not None:
            self.trace.append("receipt")
        return self.receipts.get(command.action_id)

    async def dispatch_project_completion_action(
        self,
        command: ProjectCompletionActionCommandV1,
        claim: ProjectCompletionDispatchClaimV1,
    ) -> ProjectCompletionActionReceiptV1:
        assert command.handoff_id == command.action_id
        assert claim.action_id == command.action_id
        self.dispatch_calls.append(command.action_id)
        if self.trace is not None:
            self.trace.append("dispatch")
        receipt = self.receipts.get(command.action_id)
        if receipt is not None:
            return receipt
        self.effects.append(command.action_id)
        receipt = _owner_receipt(
            command,
            lease_id=claim.claim_id,
            settlement_id=f"settled-{command.diagnostic_id}",
        )
        self.receipts[command.action_id] = receipt
        return receipt


class AlwaysFailActionPort(IdempotentTaskMarketStyleActionPort):
    async def dispatch_project_completion_action(
        self,
        command: ProjectCompletionActionCommandV1,
        claim: ProjectCompletionDispatchClaimV1,
    ) -> ProjectCompletionActionReceiptV1:
        del command, claim
        raise RuntimeError("owner unavailable")


def _engine(
    store: SqliteRuntimeStore,
    outcome_port: Any,
    diagnostics_port: Any,
    action_port: Any | None = None,
    *,
    clock: Any | None = None,
    model_ceiling_port: Any | None = None,
) -> ProjectCompletionConvergenceEngineV1:
    return ProjectCompletionConvergenceEngineV1(
        cursor=compose_project_completion_cursor(store),
        outcome_port=outcome_port,
        diagnostics_port=diagnostics_port,
        action_port=action_port or IdempotentTaskMarketStyleActionPort(),
        model_ceiling_port=model_ceiling_port or MutableModelCeilingPort(),
        clock=clock,
    )


class SqliteDurableActionOwner:
    """Production-shaped action owner: action_id is the durable idempotency key."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS owner_effects (
                    action_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    completion_contract_hash TEXT NOT NULL,
                    diagnostic_id TEXT NOT NULL,
                    owner_task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_id TEXT NOT NULL,
                    settlement_id TEXT NOT NULL,
                    effect_hash TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL
                )"""
            )

    async def query_project_completion_action_receipt(
        self,
        command: ProjectCompletionActionCommandV1,
    ) -> ProjectCompletionActionReceiptV1 | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT status, lease_id, settlement_id, effect_hash, receipt_hash
                   FROM owner_effects WHERE action_id = ?""",
                (command.action_id,),
            ).fetchone()
        if row is None:
            return None
        status, lease_id, settlement_id, effect_hash, receipt_hash = row
        return ProjectCompletionActionReceiptV1(
            identity=command.identity,
            action_id=command.action_id,
            handoff_id=command.action_id,
            diagnostic_id=command.diagnostic_id,
            owner_task_id=command.owner_task_id,
            status=status,
            lease_id=lease_id,
            settlement_id=settlement_id,
            effect_hash=effect_hash,
            receipt_hash=receipt_hash,
        )

    async def dispatch_project_completion_action(
        self,
        command: ProjectCompletionActionCommandV1,
        claim: ProjectCompletionDispatchClaimV1,
    ) -> ProjectCompletionActionReceiptV1:
        receipt = _owner_receipt(
            command,
            lease_id=claim.claim_id,
            settlement_id=f"settled-{command.action_id}",
        )
        with sqlite3.connect(self._database_path, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO owner_effects (
                    action_id, workspace, project_id, run_id, completion_contract_hash,
                    diagnostic_id, owner_task_id, status, lease_id, settlement_id, effect_hash, receipt_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    command.action_id,
                    command.identity.workspace,
                    command.identity.project_id,
                    command.identity.run_id,
                    command.identity.completion_contract_hash,
                    command.diagnostic_id,
                    command.owner_task_id,
                    receipt.status,
                    receipt.lease_id,
                    receipt.settlement_id,
                    receipt.effect_hash,
                    receipt.receipt_hash,
                ),
            )
        persisted = await self.query_project_completion_action_receipt(command)
        assert persisted is not None
        return persisted


def _multiprocess_engine_worker(
    runtime_db: str,
    owner_db: str,
    workspace_root: str,
    barrier: Any,
    queue: Any,
) -> None:
    async def run() -> tuple[str, tuple[str, ...]]:
        root = Path(workspace_root)
        identity = _identity(root)
        engine = _engine(
            SqliteRuntimeStore(runtime_db, workspace=str(root)),
            MutableOutcomePort(_binding(identity, completed=False)),
            MutableDiagnosticsPort(_diagnostics(identity)),
            SqliteDurableActionOwner(owner_db),
        )
        barrier.wait()
        result = await engine.advance(AdvanceProjectCompletionCommandV1(identity=identity))
        return result.status, result.reason_codes

    try:
        queue.put(("ok", asyncio.run(run())))
    except BaseException as exc:  # noqa: BLE001 -- child must report failures to parent
        queue.put(("error", f"{type(exc).__name__}:{exc}"))


@pytest.mark.asyncio
async def test_owner_query_failure_preserves_typed_error_code(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    result = await _engine(
        SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path)),
        FailingOutcomePort(),
        MutableDiagnosticsPort(_diagnostics(identity)),
    ).advance(AdvanceProjectCompletionCommandV1(identity=identity))

    assert result.status == "control_plane_blocked"
    assert result.reason_codes == (
        "project_outcome_owner_query_failed",
        "project_outcome_pm_contract_hash_mismatch",
    )
    assert result.terminal is False


@pytest.mark.asyncio
async def test_only_sealed_owner_binding_can_complete_and_replay_requeries_owner(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    outcome_port = MutableOutcomePort(_binding(identity, completed=True))
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(store, outcome_port, MutableDiagnosticsPort(_diagnostics(identity)))

    first = await engine.advance(AdvanceProjectCompletionCommandV1(identity=identity))
    second = await engine.advance(AdvanceProjectCompletionCommandV1(identity=identity))

    assert first.status == second.status == "completed_verified"
    assert outcome_port.calls == 2
    execution = await store.get_execution(first.workflow_id)
    assert execution is not None and execution.status == "completed"


@pytest.mark.asyncio
async def test_completed_terminal_rebinds_when_current_owner_evidence_stays_verified(tmp_path: Path) -> None:
    """Stronger replacement receipts must not invalidate a still-green outcome."""

    identity = _identity(tmp_path)
    outcome_port = MutableOutcomePort(_binding(identity, completed=True))
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(store, outcome_port, MutableDiagnosticsPort(_diagnostics(identity)))
    command = AdvanceProjectCompletionCommandV1(identity=identity)

    first = await engine.advance(command)
    outcome_port.binding = _binding(identity, completed=True, evidence_revision="-stronger-receipts")
    rebound = await engine.advance(command)
    replay = await engine.advance(command)

    assert first.status == rebound.status == replay.status == "completed_verified"
    assert rebound.terminal is True
    assert rebound.reason_codes == ("owner_binding_revalidated",)
    assert replay.terminal is True
    assert replay.reason_codes == ("owner_binding_revalidated",)
    assert replay.event_seq == rebound.event_seq
    execution = await store.get_execution(first.workflow_id)
    assert execution is not None and execution.status == "completed"


@pytest.mark.asyncio
async def test_forged_generic_completed_event_cannot_override_incomplete_owner(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    workflow_id = _workflow_id(identity)
    await store.create_execution(
        workflow_id,
        "project_completion_convergence.v1",
        {
            "identity": identity.as_payload(),
            "limits": {
                "max_actions": 8,
                "max_dispatch_attempts": 3,
                "max_no_progress_observations": 3,
                "dispatch_lease_seconds": 120,
            },
        },
    )
    forged_payload = {
        "identity": identity.as_payload(),
        "status": "completed_verified",
        "reason_codes": [],
        "owner_binding_hash": _hash("forged"),
    }
    with pytest.raises(PermissionError, match="typed cursor authority"):
        await store.append_event(
            workflow_id,
            "project_completion.terminal.v1",
            forged_payload,
            expected_previous_seq=1,
        )
    await store.append_event(
        workflow_id,
        "project_completion.terminal.v1",
        forged_payload,
        expected_previous_seq=1,
        _authority_token=_PROJECT_COMPLETION_CURSOR_AUTHORITY_TOKEN,
    )
    await store.update_execution(
        workflow_id,
        status="completed",
        result={"status": "completed_verified"},
        close_time="2026-08-09T00:00:00+00:00",
    )
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
    )

    result = await engine.advance(command)

    assert result.status == "control_plane_blocked"
    assert result.reason_codes == ("terminal_owner_binding_revalidation_failed",)
    assert result.terminal is False
    execution = await store.get_execution(workflow_id)
    assert execution is not None
    assert execution.status == "running"
    assert execution.close_time is None


@pytest.mark.asyncio
async def test_pending_receipt_settles_before_new_terminal_observation_and_effect_is_once(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    trace: list[str] = []
    outcome_port = MutableOutcomePort(_binding(identity, completed=False), trace)
    diagnostics_port = MutableDiagnosticsPort(_diagnostics(identity))

    class SimulatedProcessDeath(BaseException):
        pass

    class CrashAfterOwnerEffect(IdempotentTaskMarketStyleActionPort):
        crashed = False

        async def dispatch_project_completion_action(
            self,
            command: ProjectCompletionActionCommandV1,
            claim: ProjectCompletionDispatchClaimV1,
        ) -> ProjectCompletionActionReceiptV1:
            receipt = await super().dispatch_project_completion_action(command, claim)
            if not self.crashed:
                self.crashed = True
                raise SimulatedProcessDeath
            return receipt

    action_port = CrashAfterOwnerEffect(trace)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(store, outcome_port, diagnostics_port, action_port)
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    with pytest.raises(SimulatedProcessDeath):
        await engine.advance(command)

    outcome_port.binding = _binding(identity, completed=True)
    trace.clear()
    settled = await engine.advance(command)

    assert settled.status == "waiting"
    assert trace == ["receipt"]
    assert len(action_port.effects) == 1
    assert len(action_port.dispatch_calls) == 1
    completed = await engine.advance(command)
    assert completed.status == "completed_verified"


@pytest.mark.asyncio
async def test_active_dispatch_lease_prevents_duplicate_side_effect(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    class DeathBeforeEffect(IdempotentTaskMarketStyleActionPort):
        async def dispatch_project_completion_action(
            self,
            command: ProjectCompletionActionCommandV1,
            claim: ProjectCompletionDispatchClaimV1,
        ) -> ProjectCompletionActionReceiptV1:
            self.dispatch_calls.append(command.action_id)
            raise KeyboardInterrupt

    port = DeathBeforeEffect()
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        port,
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity, dispatch_lease_seconds=60)
    with pytest.raises(KeyboardInterrupt):
        await engine.advance(command)

    result = await engine.advance(command)

    assert result.reason_codes == ("dispatch_claim_active",)
    assert len(port.dispatch_calls) == 1


@pytest.mark.asyncio
async def test_late_receipt_from_expired_claim_settles_after_new_claim_without_duplicate_effect(
    tmp_path: Path,
) -> None:
    """A late physical commit remains valid when its durable claim is no longer latest."""

    identity = _identity(tmp_path)
    now = [datetime(2026, 8, 9, tzinfo=UTC)]
    first_dispatch_started = asyncio.Event()
    release_first_dispatch = asyncio.Event()

    class LateFirstClaimOwner(IdempotentTaskMarketStyleActionPort):
        async def dispatch_project_completion_action(
            self,
            command: ProjectCompletionActionCommandV1,
            claim: ProjectCompletionDispatchClaimV1,
        ) -> ProjectCompletionActionReceiptV1:
            self.dispatch_calls.append(claim.claim_id)
            if len(self.dispatch_calls) == 1:
                first_dispatch_started.set()
                await release_first_dispatch.wait()
                self.effects.append(command.action_id)
                receipt = _owner_receipt(
                    command,
                    lease_id=claim.claim_id,
                    settlement_id="late-first-claim-effect",
                )
                self.receipts[command.action_id] = receipt
                return receipt
            raise RuntimeError("replacement claim lost the owner race")

    owner = LateFirstClaimOwner()
    outcome = MutableOutcomePort(_binding(identity, completed=False))
    diagnostics = MutableDiagnosticsPort(_diagnostics(identity))
    database_path = str(tmp_path / "late-receipt.db")
    command = AdvanceProjectCompletionCommandV1(identity=identity, dispatch_lease_seconds=1)
    first_engine = _engine(
        SqliteRuntimeStore(database_path, workspace=str(tmp_path)),
        outcome,
        diagnostics,
        owner,
        clock=lambda: now[0],
    )
    second_engine = _engine(
        SqliteRuntimeStore(database_path, workspace=str(tmp_path)),
        outcome,
        diagnostics,
        owner,
        clock=lambda: now[0],
    )

    first_attempt = asyncio.create_task(first_engine.advance(command))
    await asyncio.wait_for(first_dispatch_started.wait(), timeout=1)
    now[0] += timedelta(seconds=2)

    replacement = await second_engine.advance(command)
    assert replacement.reason_codes == ("owner_action_dispatch_failed",)
    release_first_dispatch.set()
    settled = await first_attempt
    assert settled.reason_codes == ("owner_action_receipt_committed",)
    assert owner.effects == [settled.action_id]
    assert len(owner.dispatch_calls) == 2

    events = await SqliteRuntimeStore(database_path, workspace=str(tmp_path)).get_events(settled.workflow_id)
    claims = [event for event in events if event.event_type == "project_completion.dispatch_claimed.v1"]
    committed = next(event for event in events if event.event_type == "project_completion.action_committed.v1")
    assert len(claims) == 2
    assert committed.payload["lease_id"] == claims[0].payload["claim_id"]

    outcome.binding = _binding(identity, completed=True)
    completed = await second_engine.advance(command)
    assert completed.status == "completed_verified"
    assert owner.effects == [settled.action_id]


@pytest.mark.asyncio
async def test_preexisting_owner_receipt_without_claim_fails_closed(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    class PreexistingReceiptPort(IdempotentTaskMarketStyleActionPort):
        async def query_project_completion_action_receipt(
            self,
            command: ProjectCompletionActionCommandV1,
        ) -> ProjectCompletionActionReceiptV1:
            self.query_calls.append(command.action_id)
            return _owner_receipt(
                command,
                status="already_applied",
                lease_id="owner-existing-lease",
                settlement_id="owner-existing-settlement",
            )

    port = PreexistingReceiptPort()
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    result = await _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        port,
    ).advance(AdvanceProjectCompletionCommandV1(identity=identity))

    assert result.reason_codes == ("owner_receipt_without_durable_claim",)
    assert port.dispatch_calls == []
    events = await store.get_events(result.workflow_id)
    assert [event.event_type for event in events if event.event_type.startswith("project_completion.")] == [
        "project_completion.action_reserved.v1"
    ]


@pytest.mark.asyncio
async def test_failed_claim_retries_same_action_id_with_new_claim(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    class FailOncePort(IdempotentTaskMarketStyleActionPort):
        failed = False

        async def dispatch_project_completion_action(
            self,
            command: ProjectCompletionActionCommandV1,
            claim: ProjectCompletionDispatchClaimV1,
        ) -> ProjectCompletionActionReceiptV1:
            if not self.failed:
                self.failed = True
                self.dispatch_calls.append(command.action_id)
                raise RuntimeError("transient")
            return await super().dispatch_project_completion_action(command, claim)

    port = FailOncePort()
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        port,
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity)

    first = await engine.advance(command)
    second = await engine.advance(command)

    assert first.reason_codes == ("owner_action_dispatch_failed",)
    assert second.reason_codes == ("owner_action_receipt_committed",)
    assert second.next_action == "publish_owner_rework"
    assert len(set(port.dispatch_calls)) == 1
    assert len(port.effects) == 1
    events = await store.get_events(second.workflow_id)
    claims = [event for event in events if event.event_type == "project_completion.dispatch_claimed.v1"]
    assert [event.payload["attempt_ordinal"] for event in claims] == [1, 2]


@pytest.mark.asyncio
async def test_attempt_exhaustion_abandons_pending_then_parks_without_terminal(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    class AlwaysFailPort(IdempotentTaskMarketStyleActionPort):
        async def dispatch_project_completion_action(
            self,
            command: ProjectCompletionActionCommandV1,
            claim: ProjectCompletionDispatchClaimV1,
        ) -> ProjectCompletionActionReceiptV1:
            del command, claim
            raise RuntimeError("down")

    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        AlwaysFailPort(),
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity, max_dispatch_attempts=1)
    await engine.advance(command)
    abandoned = await engine.advance(command)
    parked = await engine.advance(command)
    replayed_park = await engine.advance(command)

    assert abandoned.status == "waiting"
    assert abandoned.reason_codes == ("pending_action_abandoned_before_terminal",)
    assert parked.status == "control_plane_blocked"
    assert parked.terminal is False
    assert parked.next_action == "publish_owner_rework"
    assert parked.reason_codes == (
        "dispatch_attempt_budget_exhausted",
        "model_ceiling_decision_unavailable",
    )
    assert replayed_park == parked
    events = await store.get_events(parked.workflow_id)
    event_types = [event.event_type for event in events]
    assert "project_completion.action_abandoned.v1" in event_types
    assert "project_completion.terminal.v1" not in event_types


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_reason"),
    [
        ({"status": "MODEL_CEILING_QUALIFIED", "terminal": True}, "model_ceiling_owner_wrong_type"),
        (None, "model_ceiling_decision_unavailable"),
    ],
)
async def test_budget_exhaustion_rejects_forged_or_missing_model_ceiling(
    tmp_path: Path,
    decision: object,
    expected_reason: str,
) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        AlwaysFailActionPort(),
        model_ceiling_port=MutableModelCeilingPort(decision),
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity, max_dispatch_attempts=1)
    await engine.advance(command)
    await engine.advance(command)

    parked = await engine.advance(command)

    assert parked.status == "control_plane_blocked"
    assert parked.terminal is False
    assert parked.next_action == "publish_owner_rework"
    assert parked.reason_codes == ("dispatch_attempt_budget_exhausted", expected_reason)
    events = await store.get_events(parked.workflow_id)
    assert all(event.event_type != "project_completion.terminal.v1" for event in events)


@pytest.mark.asyncio
async def test_unqualified_sealed_model_ceiling_parks_and_preserves_next_action(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    decision = _model_ceiling_result(identity, qualified=False)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        AlwaysFailActionPort(),
        model_ceiling_port=MutableModelCeilingPort(decision),
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity, max_dispatch_attempts=1)
    await engine.advance(command)
    await engine.advance(command)

    parked = await engine.advance(command)

    assert parked.status == "control_plane_blocked"
    assert parked.terminal is False
    assert parked.next_action == "publish_owner_rework"
    assert parked.reason_codes == (
        "dispatch_attempt_budget_exhausted",
        "model_ceiling_owner_revalidation_failed",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_reason"),
    [
        (
            lambda identity: AdvanceProjectCompletionCommandV1(identity=identity, max_actions=1),
            "action_budget_exhausted",
        ),
        (
            lambda identity: AdvanceProjectCompletionCommandV1(
                identity=identity,
                max_no_progress_observations=2,
            ),
            "no_progress_budget_exhausted",
        ),
    ],
)
async def test_action_and_no_progress_budgets_park_without_faking_model_ceiling(
    tmp_path: Path,
    command: Any,
    expected_reason: str,
) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
    )
    advance = command(identity)
    await engine.advance(advance)

    parked = await engine.advance(advance)
    if expected_reason == "no_progress_budget_exhausted":
        assert parked.status == "waiting"
        parked = await engine.advance(advance)

    assert parked.status == "control_plane_blocked"
    assert parked.terminal is False
    assert parked.next_action == "publish_owner_rework"
    assert parked.reason_codes == (expected_reason, "model_ceiling_decision_unavailable")
    events = await store.get_events(parked.workflow_id)
    assert all(event.event_type != "project_completion.terminal.v1" for event in events)


@pytest.mark.asyncio
async def test_raw_model_ceiling_status_cannot_append_terminal(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    await engine._ensure_execution(_workflow_id(identity), command)

    result = await engine._append_terminal(
        identity,
        _workflow_id(identity),
        1,
        status="model_ceiling",
        reason_codes=("caller_claimed_model_ceiling",),
        diagnostic_id="diag-a",
    )

    assert result.status == "control_plane_blocked"
    assert result.reason_codes == ("model_ceiling_sealed_result_required",)
    assert result.terminal is False
    events = await store.get_events(result.workflow_id)
    assert all(event.event_type != "project_completion.terminal.v1" for event in events)


@pytest.mark.asyncio
async def test_exact_public_model_ceiling_object_cannot_become_terminal(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    decision_port = MutableModelCeilingPort(_model_ceiling_result(identity))
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        AlwaysFailActionPort(),
        model_ceiling_port=decision_port,
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity, max_dispatch_attempts=1)
    await engine.advance(command)
    await engine.advance(command)

    parked = await engine.advance(command)

    assert parked.status == "control_plane_blocked"
    assert parked.terminal is False
    assert parked.reason_codes == (
        "dispatch_attempt_budget_exhausted",
        "model_ceiling_owner_revalidation_failed",
    )
    events = await store.get_events(parked.workflow_id)
    assert all(event.event_type != "project_completion.terminal.v1" for event in events)


@pytest.mark.asyncio
async def test_model_ceiling_port_object_tamper_stays_non_terminal(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    decision_port = MutableModelCeilingPort(_model_ceiling_result(identity))
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        AlwaysFailActionPort(),
        model_ceiling_port=decision_port,
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity, max_dispatch_attempts=1)
    await engine.advance(command)
    await engine.advance(command)
    parked = await engine.advance(command)
    assert parked.status == "control_plane_blocked"
    assert parked.terminal is False

    decision_port.result = _model_ceiling_result(identity, model="different-model")
    replayed = await engine.advance(command)

    assert replayed.status == "control_plane_blocked"
    assert replayed.reason_codes == parked.reason_codes
    assert replayed.terminal is False


@pytest.mark.asyncio
async def test_true_sealed_model_ceiling_appends_replays_and_owner_drift_invalidates(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    identity = _identity(tmp_path)
    sealed, owner = _sealed_model_ceiling(identity, request)
    decision_port = MutableModelCeilingPort(sealed)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        model_ceiling_port=decision_port,
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    workflow_id = _workflow_id(identity)
    await engine._ensure_execution(workflow_id, command)

    appended = await engine._append_terminal(
        identity,
        workflow_id,
        1,
        status="model_ceiling",
        reason_codes=("model_ceiling_qualified",),
        diagnostic_id="diag-a",
        model_ceiling_result=sealed,
    )

    assert appended.status == "model_ceiling"
    assert appended.terminal is True
    replayed = await engine.advance(command)
    assert replayed == appended

    original_attempt = owner.observation.attempts[0]
    changed_semantic_request_hash = _hash("semantic-request-drift")
    changed_binding_hash = model_ceiling_attempt_request_binding_hash(
        call_id=original_attempt.call_id,
        context_snapshot_ref=original_attempt.context_snapshot_ref,
        request_hash=original_attempt.request_hash,
        request_freeze_id=original_attempt.request_freeze_id,
        semantic_request_hash=changed_semantic_request_hash,
        physical_wire_hash=original_attempt.physical_wire_hash,
        composite_request_hash=original_attempt.composite_request_hash,
        provider_request_id=original_attempt.provider_request_id,
        authority_attempt_ordinal=original_attempt.authority_attempt_ordinal,
        attempt_budget=original_attempt.attempt_budget,
    )
    changed_attempt = replace(
        original_attempt,
        semantic_request_hash=changed_semantic_request_hash,
        owner_request_binding_hash=changed_binding_hash,
    )
    owner.observation = replace(
        owner.observation,
        attempts=(changed_attempt,),
    )
    refreshed = qualify_model_ceiling(
        ModelCeilingCandidateV1(
            workspace=sealed.workspace,
            project_id=sealed.project_id,
            run_id=sealed.run_id,
            factory_run_id=sealed.factory_run_id,
            completion_contract_hash=sealed.completion_contract_hash,
            diagnostic_id=sealed.diagnostic_id,
            provider_call_id=changed_attempt.call_id,
            final_request_snapshot_ref=changed_attempt.context_snapshot_ref,
        )
    )
    assert refreshed.terminal is True
    decision_port.result = refreshed

    invalidated = await engine.advance(command)

    assert invalidated.status == "control_plane_blocked"
    assert invalidated.terminal is False
    assert invalidated.reason_codes == ("terminal_owner_binding_revalidation_failed",)


@pytest.mark.asyncio
async def test_generic_event_forgery_is_rejected_and_corrupt_commit_cannot_replace_owner_receipt(
    tmp_path: Path,
) -> None:
    identity = _identity(tmp_path)

    class AlwaysFailPort(IdempotentTaskMarketStyleActionPort):
        async def dispatch_project_completion_action(
            self,
            command: ProjectCompletionActionCommandV1,
            claim: ProjectCompletionDispatchClaimV1,
        ) -> ProjectCompletionActionReceiptV1:
            del command, claim
            raise RuntimeError("owner unavailable")

    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    first = await _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        AlwaysFailPort(),
    ).advance(command)
    events = await store.get_events(first.workflow_id)
    reserved = next(event for event in events if event.event_type == "project_completion.action_reserved.v1")
    claimed = next(event for event in events if event.event_type == "project_completion.dispatch_claimed.v1")
    forged_payload = {
        "identity": identity.as_payload(),
        "action_id": reserved.payload["action_id"],
        "handoff_id": reserved.payload["handoff_id"],
        "diagnostic_id": reserved.payload["diagnostic_id"],
        "owner_task_id": reserved.payload["owner_task_id"],
        "owner_snapshot_hash": reserved.payload["owner_snapshot_hash"],
        "owner_bundle_hash": reserved.payload["owner_bundle_hash"],
        "receipt_hash": _hash("forged-receipt"),
        "lease_id": claimed.payload["claim_id"],
        "settlement_id": "forged-settlement",
    }
    with pytest.raises(PermissionError, match="typed cursor authority"):
        await store.append_event(
            first.workflow_id,
            "project_completion.action_committed.v1",
            forged_payload,
            expected_previous_seq=events[-1].seq,
        )
    # Simulate already-corrupt/legacy storage through the private capability;
    # replay must still consult the owner instead of trusting cursor history.
    await store.append_event(
        first.workflow_id,
        "project_completion.action_committed.v1",
        forged_payload,
        expected_previous_seq=events[-1].seq,
        _authority_token=_PROJECT_COMPLETION_CURSOR_AUTHORITY_TOKEN,
    )

    replay = await _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=True)),
        MutableDiagnosticsPort(_diagnostics(identity)),
        IdempotentTaskMarketStyleActionPort(),
    ).advance(command)

    assert replay.status == "control_plane_blocked"
    assert replay.reason_codes == ("workflow_identity_or_event_drift",)
    assert replay.terminal is False


@pytest.mark.asyncio
async def test_creation_budgets_are_frozen_and_drift_fails_closed(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity)),
    )
    await engine.advance(AdvanceProjectCompletionCommandV1(identity=identity, max_actions=2))

    result = await engine.advance(AdvanceProjectCompletionCommandV1(identity=identity, max_actions=3))

    assert result.status == "control_plane_blocked"
    assert result.reason_codes == ("frozen_convergence_budget_drift",)
    assert result.terminal is False


@pytest.mark.asyncio
async def test_terminal_event_survives_projection_failure_and_replay_repairs_it(tmp_path: Path) -> None:
    identity = _identity(tmp_path)

    class FailProjectionOnceStore(SqliteRuntimeStore):
        fail_once = True

        async def update_execution(self, *args: Any, **kwargs: Any) -> None:
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("projection crash")
            await super().update_execution(*args, **kwargs)

    store = FailProjectionOnceStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    engine = _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=True)),
        MutableDiagnosticsPort(_diagnostics(identity)),
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    with pytest.raises(RuntimeError, match="projection crash"):
        await engine.advance(command)

    repaired = await engine.advance(command)

    assert repaired.status == "completed_verified"
    execution = await store.get_execution(repaired.workflow_id)
    assert execution is not None and execution.status == "completed"


@pytest.mark.asyncio
async def test_owner_lookalike_cannot_complete(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    lookalike = type("Lookalike", (), {"outcome": type("Outcome", (), {"completed_verified": True})()})()
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))

    result = await _engine(
        store,
        MutableOutcomePort(lookalike),
        MutableDiagnosticsPort(_diagnostics(identity)),
    ).advance(AdvanceProjectCompletionCommandV1(identity=identity))

    assert result.status == "control_plane_blocked"
    assert result.reason_codes == ("project_outcome_owner_wrong_type",)
    assert result.terminal is False


@pytest.mark.asyncio
async def test_incomplete_outcome_without_owner_diagnostic_fails_closed(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    result = await _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity, ())),
    ).advance(AdvanceProjectCompletionCommandV1(identity=identity))

    assert result.status == "control_plane_blocked"
    assert result.reason_codes == ("incomplete_outcome_without_ready_owner_diagnostic",)
    assert result.terminal is False
    events = await store.get_events(result.workflow_id)
    assert all(event.event_type != "project_completion.terminal.v1" for event in events)


@pytest.mark.asyncio
async def test_ready_diagnostic_selection_and_handoff_id_are_deterministic(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    ready = _diagnostic("diag-a")
    blocked = _diagnostic("diag-b", dependencies=("diag-a",))
    port = IdempotentTaskMarketStyleActionPort()
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))

    result = await _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=False)),
        MutableDiagnosticsPort(_diagnostics(identity, (blocked, ready))),
        port,
    ).advance(AdvanceProjectCompletionCommandV1(identity=identity))

    assert result.diagnostic_id == "diag-a"
    assert port.effects == [result.action_id]
    events = await store.get_events(result.workflow_id)
    reserved = next(event for event in events if event.event_type == "project_completion.action_reserved.v1")
    assert reserved.payload["action_id"] == reserved.payload["handoff_id"]


@pytest.mark.asyncio
async def test_concurrent_engines_emit_one_reservation_claim_and_effect(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    barrier = asyncio.Event()

    class RacingOutcomePort(MutableOutcomePort):
        async def query_project_completion_outcome(self, identity: ProjectCompletionIdentityV1) -> object:
            self.calls += 1
            if self.calls >= 2:
                barrier.set()
            await barrier.wait()
            return self.binding

    outcome_port = RacingOutcomePort(_binding(identity, completed=False))
    diagnostics_port = MutableDiagnosticsPort(_diagnostics(identity))
    action_port = IdempotentTaskMarketStyleActionPort()
    db_path = str(tmp_path / "runtime.db")
    store_a = SqliteRuntimeStore(db_path, workspace=str(tmp_path))
    store_b = SqliteRuntimeStore(db_path, workspace=str(tmp_path))
    command = AdvanceProjectCompletionCommandV1(identity=identity)

    await asyncio.gather(
        _engine(store_a, outcome_port, diagnostics_port, action_port).advance(command),
        _engine(store_b, outcome_port, diagnostics_port, action_port).advance(command),
    )

    events = await store_a.get_events(_workflow_id(identity))
    assert sum(event.event_type == "project_completion.action_reserved.v1" for event in events) == 1
    assert sum(event.event_type == "project_completion.dispatch_claimed.v1" for event in events) == 1
    assert len(action_port.effects) == 1


def test_forked_engines_with_durable_owner_emit_one_physical_effect(tmp_path: Path) -> None:
    runtime_db = str(tmp_path / "runtime-multiprocess.db")
    owner_db = str(tmp_path / "owner-multiprocess.db")
    SqliteDurableActionOwner(owner_db)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_multiprocess_engine_worker,
            args=(runtime_db, owner_db, str(tmp_path), barrier, result_queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    results = [result_queue.get(timeout=3) for _ in processes]
    assert all(kind == "ok" for kind, _payload in results), results

    with sqlite3.connect(owner_db) as connection:
        effect_count = connection.execute("SELECT COUNT(*) FROM owner_effects").fetchone()[0]
    assert effect_count == 1

    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(runtime_db, workspace=str(tmp_path))
    events = asyncio.run(store.get_events(_workflow_id(identity)))
    assert sum(event.event_type == "project_completion.action_reserved.v1" for event in events) == 1
    assert sum(event.event_type == "project_completion.dispatch_claimed.v1" for event in events) == 1
    assert sum(event.event_type == "project_completion.action_committed.v1" for event in events) == 1


@pytest.mark.asyncio
async def test_advance_result_is_sealed_against_dataclasses_replace(tmp_path: Path) -> None:
    identity = _identity(tmp_path)
    store = SqliteRuntimeStore(str(tmp_path / "runtime.db"), workspace=str(tmp_path))
    result = await _engine(
        store,
        MutableOutcomePort(_binding(identity, completed=True)),
        MutableDiagnosticsPort(_diagnostics(identity)),
    ).advance(AdvanceProjectCompletionCommandV1(identity=identity))

    with pytest.raises(TypeError):
        replace(result, status="completed_verified")


def _multiprocess_cas_worker(
    db_path: str,
    workspace: str,
    barrier: Any,
    result_queue: Any,
) -> None:
    store = SqliteRuntimeStore(db_path, workspace=workspace)
    barrier.wait()
    try:
        asyncio.run(store.append_event("wf-mp", "candidate", {}, expected_previous_seq=1))
    except WorkflowEventVersionConflictError:
        result_queue.put("conflict")
    except BaseException as exc:  # noqa: BLE001 - report child-process failures to parent
        result_queue.put(f"error:{type(exc).__name__}:{exc}")
    else:
        result_queue.put("success")


@pytest.mark.asyncio
async def test_sqlite_expected_previous_seq_is_real_multiprocess_cas(tmp_path: Path) -> None:
    db_path = str(tmp_path / "runtime.db")
    store = SqliteRuntimeStore(db_path, workspace=str(tmp_path))
    await store.create_execution("wf-mp", "project_completion", {"identity": {"project_id": "p"}})
    # ``fork`` keeps this checked-in test independent from pytest's synthetic
    # module name while still exercising two real OS processes and SQLite
    # connections against the same durable cursor.
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(3)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_multiprocess_cas_worker,
            args=(db_path, str(tmp_path), barrier, result_queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    barrier.wait(timeout=20)
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    results: list[str] = []
    for _ in processes:
        try:
            results.append(result_queue.get(timeout=5))
        except Empty as exc:
            raise AssertionError("multiprocessing CAS worker produced no result") from exc
    assert sorted(results) == ["conflict", "success"]
