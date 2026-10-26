"""Kernel-owned synthetic scenarios, outside every production import surface.

Decoded inputs/read responses are explicit fixture controls. File-prewrite
policy/snapshot inputs deliberately reach physical rejection; separate controls
exercise production policy. TaskRuntime, DEO, mutation, recovery and durable
positive owner commits use their original owners.
Returned public values are observations, never Native qualification or authority.
Real module facts belong solely to synthetic experiments, not Native or Bench
project delivery receipts. External engineering reports never enter product SSoT.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, TypedDict

from polaris.cells.control_plane.run_ledger.public import build_tool_call_lifecycle_receipt
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.director.runtime.public import (
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_policy_revalidation_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    hash_directed_effect_target_state_components,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamProvenanceV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.adapters.public.directed_effect_policy_service import (
    create_director_effect_policy_snapshot_port,
)
from polaris.cells.roles.adapters.public.directed_effect_service import create_director_directed_effect_mutation_port
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import (
    DirectedEffectLifecycleCandidateV1,
    DirectedEffectLifecycleService,
)
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import (
    DirectedEffectAuthoritativePolicyGuardRequestV1,
    DirectedEffectPolicyGuard,
)
from polaris.cells.roles.kernel.internal.kernel import task_boundary
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.internal.transaction import outcome_commit
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import merge_batch_receipts
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    BatchReceipt,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import (
    _candidate,
    _job_restriction_evidence,
    _RecordingPolicyPort,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
    create_task_runtime_execution_attempt_authority,
    settle_task_runtime_execution_attempt_typed,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_workspace_runtime_identity


class _OwnerCommitInputs(TypedDict):
    workspace: str
    run_id: str
    task_id: str
    transition_id: str
    ledger: TurnLedger
    result: dict[str, Any]
    completion_status: str
    session_id: str


def claim_synthetic_attempt(
    workspace: str,
    *,
    factory_run_id: str | None = None,
) -> TaskRuntimeExecutionAttemptIdentityV1:
    """Create one actual private attempt using public FactStream/TaskRuntime."""
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="synthetic RunLedger owner handoff test",
        )
    )
    service = TaskRuntimeService(workspace)
    task_id = int(service.create_task_row(subject="synthetic RunLedger owner handoff")["id"])
    if factory_run_id is not None:
        binding = bind_runtime_task_to_factory_run(
            BindRuntimeTaskToFactoryRunCommandV1(
                workspace=workspace,
                task_id=str(task_id),
                factory_run_id=factory_run_id,
            )
        )
        assert binding.ok, binding
    claim = service.claim_execution(
        task_id,
        worker_id="worker",
        role_id="director",
        run_id="run",
        external_task_id="DEO-LIFECYCLE",
        selection_source="test",
    )
    assert claim["success"] is True
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])


async def produce_readonly_owner_case(
    root: Path,
    *,
    count: int = 3,
    reordered: bool = False,
    mixed_serializers: bool = False,
    recommit: bool = False,
    protocol: str = "v3",
    schedule: str = "parallel",
    read_prefix: str = "private fixture ",
):
    """Only tool I/O is a fake; invocation classification, receipts and owner are real."""
    attempt = claim_synthetic_attempt(str(root))
    if protocol == "v2":
        from polaris.cells.events.fact_stream.public import (
            AppendFactEventCommandV1,
            BootstrapFactStreamWorkspaceCommandV1,
            FactStreamProvenanceV1,
            append_fact_event,
            bootstrap_fact_stream_workspace,
        )

        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(root),
                streams=("roles.kernel.turn_outcomes",),
                maintenance_reason="private legacy namespace fixture",
            )
        )
        # A labeled non-owner setup event selects the existing V2 protocol.
        # Every owner outcome below still comes from real commit_turn_result.
        provenance = FactStreamProvenanceV1(
            workspace=str(root),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id="private-namespace-seed",
            transition_id="private-namespace-seed",
        )
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(root),
                stream="roles.kernel.turn_outcomes",
                event_type="private_namespace_seed",
                source="private_test_setup",
                run_id=attempt.run_id,
                task_id=attempt.external_task_id,
                payload={"private_test_setup": True},
                provenance=provenance,
                strict_integrity=True,
                idempotency_key="private-namespace-seed",
            )
        )
    name = "file_exists" if count == 9 else "read_file"
    invocations = [
        ToolInvocation(
            call_id=ToolCallId(f"opaque-call-{index}"),
            tool_name=name,
            raw_tool_name=name,
            arguments={"file_path": f"private-{index}.txt"},
            native_arguments={"file_path": f"private-{index}.txt"},
        )
        for index in range(count)
    ]
    turn = TurnId("opaque-layout-turn")
    declared = ToolBatch(
        batch_id=BatchId("logical-declaration"),
        invocations=invocations,
        parallel_readonly=invocations if schedule == "parallel" else invocations[:-1] if schedule == "mixed" else [],
        readonly_serial=invocations if schedule == "serial" else invocations[-1:] if schedule == "mixed" else [],
    )
    assert bool(declared.readonly_serial) is (schedule != "parallel")
    calls = []

    async def private_read(tool: str, arguments: dict[str, Any]):
        calls.append((tool, dict(arguments)))
        return (
            {"exists": False, "path": arguments["file_path"]}
            if tool == "file_exists"
            else {"content": read_prefix + arguments["file_path"]}
        )

    emitted = await ToolBatchRuntime(executor=private_read).execute_batch(declared, turn)
    assert len(emitted) == count and len(calls) == count
    components = [item.to_dict() for item in emitted]
    if mixed_serializers:
        # Existing role-owner parser is a complete serializer, not ad-hoc field stripping.
        components = [
            BatchReceipt.model_validate(item).model_dump(mode="json") if index % 2 else item
            for index, item in enumerate(components)
        ]
    order = [*range(1, count), 0] if reordered else list(range(count))
    components = [components[index] for index in order]
    merged = merge_batch_receipts(components)
    assert merged is not None
    ledger = TurnLedger(turn_id=str(turn))
    ledger.record_invocation_batch(str(declared.batch_id), invocations)
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "index": index,
            "call_id": str(invocation.call_id),
            "tool_name": name,
            "arguments_hash": stable_hash(invocation.native_arguments),
        }
        for index, invocation in enumerate(invocations)
    ]
    ledger.record_decision(
        TurnDecision(
            turn_id=turn,
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="private read",
            tool_batch=declared,
            finalize_mode=FinalizeMode.NONE,
            domain="code",
            metadata={"native_tool_call_envelopes": envelopes, "provider_response_hash": "b" * 64},
        )
    )
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id=str(turn),
        role="director",
        native_tool_calls_count=count,
        decoded_tool_calls_count=count,
        dispatched_tool_calls_count=count,
        receipts=components,
        native_tool_call_envelopes=envelopes,
        provider_response_hash="b" * 64,
    ).to_dict()
    args: _OwnerCommitInputs = {
        "workspace": str(root),
        "run_id": attempt.run_id,
        "task_id": attempt.external_task_id,
        "transition_id": "opaque-layout-transition",
        "ledger": ledger,
        "result": {"batch_receipt": merged},
        "completion_status": "success",
        "session_id": attempt.session_id,
    }
    durable = outcome_commit.commit_turn_result(**args)
    commit_receipts = [durable.receipt]
    if recommit:
        commit_receipts.append(outcome_commit.commit_turn_result(**args).receipt)
    return {
        "attempt": attempt,
        "components": components,
        "merged": merged,
        "lifecycle": lifecycle,
        "outcome": durable.outcome,
        "commit_receipt": durable.receipt,
        "sealed_turn": durable.sealed_turn,
        "commit_receipts": tuple(commit_receipts),
        "calls": calls,
        "invocations": invocations,
    }


async def produce_readonly_completion_order_case(tmp_path: Path) -> tuple[list[int], list[str]]:
    claim_synthetic_attempt(str(tmp_path))
    entered: asyncio.Queue[int] = asyncio.Queue()
    completed: asyncio.Queue[int] = asyncio.Queue()
    release = [asyncio.Event() for _ in range(3)]
    invocations = [
        ToolInvocation(
            call_id=ToolCallId(f"opaque-{i}"),
            tool_name="read_file",
            arguments={"slot": i},
            native_arguments={"slot": i},
        )
        for i in range(3)
    ]

    async def read(tool: str, args: dict[str, Any]):
        slot = args["slot"]
        await entered.put(slot)
        await release[slot].wait()
        await completed.put(slot)
        return {"content": str(slot)}

    running = asyncio.create_task(
        ToolBatchRuntime(executor=read).execute_batch(
            ToolBatch(batch_id=BatchId("gather-parent"), invocations=invocations, parallel_readonly=invocations),
            TurnId("gather-turn"),
        )
    )
    for _ in range(3):
        await entered.get()
    physical = []
    for slot in (1, 2, 0):
        release[slot].set()
        physical.append(await completed.get())
    returned = await running
    return physical, [item.to_dict()["results"][0]["call_id"] for item in returned]


async def produce_readonly_source_owner_case(root: Path, *, factory_run_id: str) -> dict[str, Any]:
    attempt = claim_synthetic_attempt(str(root))
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(root), task_id=str(attempt.task_id), factory_run_id=factory_run_id
        )
    ).ok
    fs = KernelFileSystem(str(root), get_default_adapter())
    fs.workspace_write_text("source.txt", "actual readonly source", encoding="utf-8")

    async def read(name: str, arguments: dict[str, Any]):
        assert name == "read_file"
        return {"content": fs.workspace_read_text(arguments["file_path"], encoding="utf-8")}

    invocation = ToolInvocation(
        call_id=ToolCallId("read-call"),
        tool_name="read_file",
        raw_tool_name="read_file",
        arguments={"file_path": "source.txt"},
        native_arguments={"file_path": "source.txt"},
    )
    turn = TurnId("read-turn")
    batch_definition = ToolBatch(batch_id=BatchId("read-batch"), invocations=[invocation], readonly_serial=[invocation])
    receipts = await ToolBatchRuntime(executor=read).execute_batch(batch_definition, turn)
    batch = receipts[0].to_dict()
    assert batch["results"][0]["status"] == "success"
    assert batch["results"][0]["result"]["content"] == "actual readonly source"
    ledger = TurnLedger(turn_id=str(turn))
    batch_definition = batch_definition.model_copy(update={"batch_id": BatchId(batch["batch_id"])})
    ledger.record_invocation_batch(batch["batch_id"], [invocation])
    ledger.record_decision(
        TurnDecision(
            turn_id=turn,
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="read source",
            tool_batch=batch_definition,
            finalize_mode=FinalizeMode.NONE,
            domain="code",
            metadata={
                "native_tool_call_envelopes": [
                    {
                        "schema_version": "native_tool_call_envelope.v1",
                        "index": 0,
                        "call_id": "read-call",
                        "tool_name": "read_file",
                        "arguments_hash": stable_hash({"file_path": "source.txt"}),
                    }
                ]
            },
        )
    )
    durable = outcome_commit.commit_turn_result(
        workspace=str(root),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        transition_id="readonly-failed-product",
        ledger=ledger,
        result={"batch_receipt": batch},
        completion_status="success",
        session_id=attempt.session_id,
    )
    return {"attempt": attempt, "batch": batch, "outcome": durable.outcome, "commit_receipt": durable.receipt}


async def produce_failed_cli_owner_case(root: Path) -> dict[str, Any]:
    """Run one private exit2 command and persist its original canonical owner."""
    attempt = claim_synthetic_attempt(str(root))
    filesystem = KernelFileSystem(str(root), get_default_adapter())
    filesystem.workspace_write_text(
        "verify.py", 'print("synthetic actual verifier failed"); raise SystemExit(2)\n', encoding="utf-8"
    )
    filesystem.workspace_write_text("AGENTS.md", "# Synthetic component workspace\n", encoding="utf-8")
    command = "python verify.py"
    authority = create_task_runtime_execution_attempt_authority(attempt)
    profile = RoleProfile(
        role_id="director",
        display_name="Synthetic Director",
        description="No provider",
        tool_policy=RoleToolPolicy(whitelist=["execute_command"], allow_command_execution=True),
    )
    token = {
        "token_id": "synthetic-failed-cli",
        "execution_envelope_hash": "a" * 64,
        "capability_audit_ok": True,
        "allowed_write_paths": [],
        "allowed_commands": [command],
    }
    gateway = RoleToolGateway(
        profile,
        str(root),
        session_id=attempt.session_id,
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        capability_scope=(),
        capability_token=token,
    )
    policy = create_director_effect_policy_snapshot_port(str(root))
    invocation = ToolInvocation(
        call_id=ToolCallId("failed-cli-call"),
        tool_name="execute_command",
        raw_tool_name="execute_command",
        arguments={"command": command, "timeout": 30},
        native_arguments={"command": command, "timeout": 30},
    )
    turn = TurnId("turn-failed-cli")
    batch_definition = ToolBatch(
        batch_id=BatchId("batch-failed-cli"), invocations=[invocation], serial_writes=[invocation]
    )
    verdict = await DirectedEffectPolicyGuard(gateway, policy).evaluate_authoritative(
        DirectedEffectAuthoritativePolicyGuardRequestV1(
            invocation=invocation,
            workspace=str(root),
            inventory_ordinal=0,
            execution_attempt=attempt,
            turn_id=str(turn),
            batch_id=str(batch_definition.batch_id),
        )
    )
    assert verdict.status == "authorized", verdict
    assert verdict.preflight is not None and verdict.snapshot is not None and verdict.authorization_binding is not None
    assert verdict.current_job_token_restriction_evidence is not None
    prepared_result = DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id=str(turn),
        batch_id=str(batch_definition.batch_id),
        candidates=(
            DirectedEffectLifecycleCandidateV1(
                preflight=verdict.preflight,
                snapshot=verdict.snapshot,
                authorization_binding=verdict.authorization_binding,
            ),
        ),
    )
    assert prepared_result.status == "ready", prepared_result
    prepared = prepared_result.prepared_batch
    assert prepared is not None
    fences = create_directed_effect_fence_ports()
    mutation = create_director_directed_effect_mutation_port(
        workspace=str(root), policy_snapshot_port=policy, fence_consume_port=fences.consume
    )

    async def reject_generic_execution(tool: str, arguments: dict[str, Any]) -> Any:
        raise AssertionError(f"synthetic directed command escaped real mutation owner: {tool}")

    runtime = ToolBatchRuntime(
        executor=reject_generic_execution,
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy, fence_admin_port=fences.admin, mutation_port=mutation
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=(
            (str(invocation.call_id), verdict.current_job_token_restriction_evidence),
        ),
        directed_effect_dispatch_call_ids=(str(invocation.call_id),),
    )
    receipts = await runtime.execute_batch(batch_definition, turn)
    batch = receipts[0].to_dict()
    assert batch["raw_results"][0]["result"]["exit_code"] == 2, batch
    ledger = TurnLedger(turn_id=str(turn))
    ledger.record_invocation_batch(batch["batch_id"], [invocation])
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "index": 0,
            "call_id": str(invocation.call_id),
            "tool_name": "execute_command",
            "arguments_hash": stable_hash(invocation.native_arguments),
        }
    ]
    ledger.record_decision(
        TurnDecision(
            turn_id=turn,
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="Synthetic failed CLI, no delivery claim",
            tool_batch=batch_definition,
            finalize_mode=FinalizeMode.NONE,
            domain="code",
            metadata={"native_tool_call_envelopes": envelopes},
        )
    )
    durable = outcome_commit.commit_turn_result(
        workspace=str(root),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        transition_id="failed-cli-transition",
        ledger=ledger,
        result={"batch_receipt": batch},
        completion_status="failed",
        failure_reason="synthetic actual exit2",
        session_id=attempt.session_id,
    )
    settled = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(root),
            identity=prepared.execution_attempt,
            outcome="failed",
            summary="Synthetic actual exit2; not Native acceptance",
        )
    )
    assert settled.success, settled
    return {
        "attempt": prepared.execution_attempt,
        "batch": batch,
        "outcome": durable.outcome,
        "commit_receipt": durable.receipt,
        "sealed_turn": durable.sealed_turn,
    }


class _FilePolicyInputs(_RecordingPolicyPort):
    """Synthetic policy inputs; physical target observation is real and KFS-bound.

    The production preview rejects an invalid range before physical execution.
    This fixture deliberately admits it to exercise the editing owner's typed
    rejection and its canonical Kernel commit, without claiming policy acceptance.
    """

    def __init__(self, root: Path) -> None:
        super().__init__(events=[])
        self._filesystem = KernelFileSystem(str(root), get_default_adapter())

    async def revalidate(self, request: Any) -> DirectorEffectPolicyRevalidationResultV1:
        snapshot = request.bound_snapshot.snapshot
        baseline = snapshot.baseline_target_state_evidence
        content_hash = sha256(self._filesystem.workspace_read_bytes(baseline.target_path)).hexdigest()
        target_hash = hash_directed_effect_target_state_components(
            target_path=baseline.target_path,
            exists=True,
            before_content_hash=content_hash,
            minimal_content_evidence=baseline.minimal_content_evidence,
            agents_policy_hash=baseline.agents_policy_hash,
            is_no_file_state=False,
        )
        target = DirectorEffectTargetStateEvidenceV1(
            target_path=baseline.target_path,
            exists=True,
            before_content_hash=content_hash,
            minimal_content_evidence=baseline.minimal_content_evidence,
            agents_policy_hash=baseline.agents_policy_hash,
            target_state_hash=target_hash,
            is_no_file_state=False,
        )
        evidence_hash = hash_directed_effect_policy_revalidation_evidence(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version=snapshot.policy_version,
            current_policy_hash=snapshot.policy_hash,
            current_target_state_evidence=target,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
        )
        return DirectorEffectPolicyRevalidationResultV1(
            status="allowed",
            allowed=True,
            error_code=None,
            current_policy_version=snapshot.policy_version,
            current_policy_hash=snapshot.policy_hash,
            current_target_state_evidence=target,
            current_target_state_hash=target_hash,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
            current_evidence_hash=evidence_hash,
        )


async def _execute_file_owner_batch(
    root: Path,
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    *,
    arguments: dict[str, Any],
    native_arguments: dict[str, Any],
    turn: str,
    batch_id: str,
    call_id: str,
    production_policy: bool = False,
) -> tuple[TaskRuntimeExecutionAttemptIdentityV1, dict[str, Any], ToolInvocation, ToolBatch]:
    """Execute real editing owners with explicit synthetic or production policy inputs."""
    authority = create_task_runtime_execution_attempt_authority(attempt)
    profile = RoleProfile(
        role_id="director",
        display_name="Synthetic Director",
        description="No provider",
        tool_policy=RoleToolPolicy(whitelist=["edit_blocks"], allow_code_write=True),
    )
    scope = ("src/a.txt",) if production_policy else ("src/",)
    gateway = RoleToolGateway(
        profile,
        str(root),
        session_id=attempt.session_id,
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        capability_scope=scope,
        capability_token={
            "token_id": "synthetic-file-prewrite",
            "execution_envelope_hash": "a" * 64,
            "capability_audit_ok": True,
            "allowed_write_paths": list(scope),
            "allowed_commands": [],
        },
    )
    policy = create_director_effect_policy_snapshot_port(str(root)) if production_policy else _FilePolicyInputs(root)
    invocation = ToolInvocation(
        call_id=ToolCallId(call_id),
        tool_name="edit_blocks",
        raw_tool_name="edit_blocks",
        arguments=arguments,
        native_arguments=native_arguments,
    )
    definition = ToolBatch(batch_id=BatchId(batch_id), invocations=[invocation], serial_writes=[invocation])
    allowed, refusal = gateway.check_tool_permission("edit_blocks", native_arguments)
    assert allowed, refusal
    candidate = _candidate(
        attempt,
        ordinal=0,
        tool_call_id=call_id,
        normalized_tool_name="edit_blocks",
        normalized_arguments=tuple(sorted(arguments.items())),
        target_path="src/a.txt",
        target_before_content_hash=sha256(
            KernelFileSystem(str(root), get_default_adapter()).workspace_read_bytes("src/a.txt")
        ).hexdigest(),
        turn_id=turn,
        batch_id=batch_id,
        allowed_paths=scope,
    )
    restrictions = _job_restriction_evidence(allowed_paths=scope)
    if production_policy:
        verdict = await DirectedEffectPolicyGuard(gateway, policy).evaluate_authoritative(
            DirectedEffectAuthoritativePolicyGuardRequestV1(
                invocation=invocation,
                workspace=str(root),
                inventory_ordinal=0,
                execution_attempt=attempt,
                turn_id=turn,
                batch_id=batch_id,
            )
        )
        assert verdict.status == "authorized", verdict
        assert verdict.preflight is not None and verdict.snapshot is not None
        assert verdict.authorization_binding is not None
        assert verdict.current_job_token_restriction_evidence is not None
        candidate = DirectedEffectLifecycleCandidateV1(
            preflight=verdict.preflight,
            snapshot=verdict.snapshot,
            authorization_binding=verdict.authorization_binding,
        )
        restrictions = verdict.current_job_token_restriction_evidence
    ready = DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id=turn,
        batch_id=batch_id,
        candidates=(candidate,),
    )
    assert ready.status == "ready", ready
    prepared = ready.prepared_batch
    assert prepared is not None
    fences = create_directed_effect_fence_ports()
    mutation = create_director_directed_effect_mutation_port(
        workspace=str(root),
        policy_snapshot_port=policy,
        fence_consume_port=fences.consume,
    )

    async def reject_generic_execution(tool: str, _arguments: dict[str, Any]) -> Any:
        raise AssertionError(f"synthetic file call escaped real mutation owner: {tool}")

    runtime = ToolBatchRuntime(
        executor=reject_generic_execution,
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy,
            fence_admin_port=fences.admin,
            mutation_port=mutation,
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=((call_id, restrictions),),
        directed_effect_dispatch_call_ids=(call_id,),
    )
    receipts = await runtime.execute_batch(definition, TurnId(turn))
    return prepared.execution_attempt, receipts[0].to_dict(), invocation, definition


def _file_owner_ledger(
    root: Path,
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    batch: dict[str, Any],
    invocation: ToolInvocation,
    definition: ToolBatch,
) -> TurnLedger:
    ledger = TurnLedger(turn_id=batch["turn_id"])
    ledger.record_invocation_batch(batch["batch_id"], [invocation])
    ledger.record_decision(
        TurnDecision(
            turn_id=TurnId(batch["turn_id"]),
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="Synthetic file owner result; no delivery claim",
            tool_batch=definition,
            finalize_mode=FinalizeMode.NONE,
            domain="code",
            metadata={
                "native_tool_call_envelopes": [
                    {
                        "schema_version": "native_tool_call_envelope.v1",
                        "index": 0,
                        "call_id": str(invocation.call_id),
                        "tool_name": invocation.tool_name,
                        "arguments_hash": stable_hash(invocation.native_arguments),
                    }
                ]
            },
        )
    )
    return ledger


def _commit_file_owner_batch(
    root: Path,
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    batch: dict[str, Any],
    invocation: ToolInvocation,
    definition: ToolBatch,
) -> outcome_commit.DurableTurnCommit:
    """The existing committer alone creates the authoritative owner outcome."""
    ledger = _file_owner_ledger(root, attempt, batch, invocation, definition)
    return outcome_commit.commit_turn_result(
        workspace=str(root),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        transition_id=batch["turn_id"] + ":owner-commit",
        ledger=ledger,
        result={"batch_receipt": batch},
        completion_status="failed" if batch["failure_count"] else "success",
        failure_reason="Synthetic file result; not project acceptance" if batch["failure_count"] else None,
        session_id=attempt.session_id,
    )


def _append_synthetic_corrupt_file_owner(
    root: Path,
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
    batch: dict[str, Any],
    invocation: ToolInvocation,
    definition: ToolBatch,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    """Forge only a rejection fixture, never a positive owner commit or receipt.

    Its unmodified input comes from the real ledger projection. The callback
    preserves the original exact mismatches at the cold validation boundary.
    """
    ledger = _file_owner_ledger(root, attempt, batch, invocation, definition)
    execution = outcome_commit._execution_from_result(ledger=ledger, result={"batch_receipt": batch})
    outcome = ledger.to_turn_outcome(
        run_id=attempt.run_id,
        decision=ledger.final_decision,
        execution=execution,
    ).model_dump(mode="json", exclude={"commit_ref"})
    original_hash = stable_hash(outcome)
    mutate(outcome)
    assert stable_hash(outcome) != original_hash, "synthetic negative requires an actual owner-input corruption"
    turn = batch["turn_id"]
    transition = "synthetic-corrupt-file-prewrite-transition"
    provenance = FactStreamProvenanceV1(
        workspace=str(root),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id=turn,
        transition_id=transition,
    )
    storage = resolve_workspace_runtime_identity(str(root))
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(root),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel.transaction",
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            correlation_id=turn,
            provenance=provenance,
            strict_integrity=True,
            idempotency_key=":".join(
                (
                    "synthetic-corrupt-file-owner",
                    storage.token,
                    attempt.run_id,
                    attempt.external_task_id,
                    turn,
                    transition,
                )
            ),
            payload={
                "schema_version": "roles.kernel.turn_outcome_fact.v2",
                "run_id": attempt.run_id,
                "task_id": attempt.external_task_id,
                "turn_id": turn,
                "transition_id": transition,
                "provenance": provenance.to_record(),
                "storage_identity": storage.to_record(),
                "session_binding": {"session_id": attempt.session_id},
                "outcome": outcome,
                "outcome_hash": stable_hash(outcome),
            },
        )
    )


async def produce_file_prewrite_owner_case(
    root: Path,
    *,
    aliases: bool = False,
    settle: bool = True,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    expect_unchanged: bool = True,
    factory_run_id: str | None = None,
) -> dict[str, Any]:
    """Observe physical rejection under synthetic policy inputs, never admission.

    The default case uses the real canonical committer. ``mutate`` instead
    creates an explicitly synthetic corrupt owner input, never a commit receipt.
    """
    attempt = claim_synthetic_attempt(str(root), factory_run_id=factory_run_id)
    filesystem = KernelFileSystem(str(root), get_default_adapter())
    filesystem.workspace_write_text("src/a.txt", "alpha\nbeta\n", encoding="utf-8")
    filesystem.workspace_write_text("AGENTS.md", "# Synthetic file component workspace\n", encoding="utf-8")
    arguments = {"file": "src/a.txt", "start": 7, "end": 9, "replace": ""}
    native = {"file": "src/a.txt", "startLine": 7, "endLine": 9, "newText": ""} if aliases else dict(arguments)
    attempt, batch, invocation, definition = await _execute_file_owner_batch(
        root,
        attempt,
        arguments=arguments,
        native_arguments=native,
        turn="file-prewrite-turn",
        batch_id="file-prewrite-batch",
        call_id="file-prewrite-call",
    )
    public_commit = {}
    if mutate is None:
        durable = _commit_file_owner_batch(root, attempt, batch, invocation, definition)
        public_commit = {
            "outcome": durable.outcome,
            "commit_receipt": durable.receipt,
            "sealed_turn": durable.sealed_turn,
        }
    else:
        _append_synthetic_corrupt_file_owner(root, attempt, batch, invocation, definition, mutate)
    settled = None
    if settle:
        settled = settle_task_runtime_execution_attempt_typed(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(root),
                identity=attempt,
                outcome="failed",
                summary="Private file rejection; delivery failed",
            )
        )
    if expect_unchanged:
        assert filesystem.workspace_read_text("src/a.txt", encoding="utf-8") == "alpha\nbeta\n"
    return {
        "attempt": attempt,
        "batch": batch,
        "settled": settled,
        "synthetic_owner_corruption": mutate is not None,
        **public_commit,
    }


async def continue_file_edit_owner_case(
    root: Path,
    attempt: TaskRuntimeExecutionAttemptIdentityV1,
) -> dict[str, Any]:
    """Execute and commit the original legal next batch under the same owner."""
    arguments = {"file": "src/a.txt", "blocks": "<<<< SEARCH\nbeta\n====\nBETA\n>>>> REPLACE\n"}
    attempt, batch, invocation, definition = await _execute_file_owner_batch(
        root,
        attempt,
        arguments=arguments,
        native_arguments=dict(arguments),
        turn="file-followup-turn",
        batch_id="file-followup-batch",
        call_id="file-followup-call",
    )
    durable = _commit_file_owner_batch(root, attempt, batch, invocation, definition)
    return {
        "attempt": attempt,
        "batch": batch,
        "outcome": durable.outcome,
        "commit_receipt": durable.receipt,
        "sealed_turn": durable.sealed_turn,
    }


async def produce_file_policy_guard_case(root: Path, *, legal: bool) -> dict[str, Any]:
    """Production Gateway/PolicyGuard control, separate from synthetic policy inputs."""
    attempt = claim_synthetic_attempt(str(root))
    filesystem = KernelFileSystem(str(root), get_default_adapter())
    filesystem.workspace_write_text("src/a.txt", "alpha\nbeta\n", encoding="utf-8")
    filesystem.workspace_write_text("AGENTS.md", "# Synthetic policy component workspace\n", encoding="utf-8")
    arguments = {
        "file": "src/a.txt",
        "start": 2 if legal else 7,
        "end": 2 if legal else 9,
        "replace": "BETA" if legal else "",
    }
    if legal:
        attempt, batch, invocation, definition = await _execute_file_owner_batch(
            root,
            attempt,
            arguments=arguments,
            native_arguments=dict(arguments),
            turn="file-policy-turn",
            batch_id="file-policy-batch",
            call_id="file-policy-call",
            production_policy=True,
        )
        durable = _commit_file_owner_batch(root, attempt, batch, invocation, definition)
        return {
            "status": "authorized",
            "batch": batch,
            "outcome": durable.outcome,
            "commit_receipt": durable.receipt,
            "sealed_turn": durable.sealed_turn,
        }
    profile = RoleProfile(
        role_id="director",
        display_name="Synthetic Director",
        description="No provider",
        tool_policy=RoleToolPolicy(whitelist=["edit_blocks"], allow_code_write=True),
    )
    gateway = RoleToolGateway(
        profile,
        str(root),
        session_id=attempt.session_id,
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        capability_scope=("src/a.txt",),
        capability_token={
            "token_id": "synthetic-file-prewrite",
            "execution_envelope_hash": "a" * 64,
            "capability_audit_ok": True,
            "allowed_write_paths": ["src/a.txt"],
            "allowed_commands": [],
        },
    )
    before = {str(path): filesystem.workspace_read_bytes(str(path)) for path in root.rglob("*") if path.is_file()}
    verdict = await DirectedEffectPolicyGuard(
        gateway,
        create_director_effect_policy_snapshot_port(str(root)),
    ).evaluate_authoritative(
        DirectedEffectAuthoritativePolicyGuardRequestV1(
            invocation=ToolInvocation(
                call_id=ToolCallId("file-policy-call"),
                tool_name="edit_blocks",
                raw_tool_name="edit_blocks",
                arguments=arguments,
                native_arguments=dict(arguments),
            ),
            workspace=str(root),
            inventory_ordinal=0,
            execution_attempt=attempt,
            turn_id="file-policy-turn",
            batch_id="file-policy-batch",
        )
    )
    after = {str(path): filesystem.workspace_read_bytes(str(path)) for path in root.rglob("*") if path.is_file()}
    return {"status": verdict.status, "error_code": verdict.error_code, "before": before, "after": after}


def produce_task_boundary_case(workspace: Path, *, task_id: str, run_id: str, scenario: str) -> dict[str, Any]:
    """Produce only the two fixed synthetic transitions via the original owner."""
    if scenario == "deferred_followup":
        return task_boundary.append_deferred_followup_task_boundary_verdict(
            workspace=str(workspace), task_id=task_id, run_id=run_id, reason="needs_followup_workflow"
        )
    if scenario == "newer_missing_verifier":
        result = task_boundary.append_director_task_boundary_verdict(
            role="director",
            workspace=str(workspace),
            task_id=task_id,
            run_id=run_id,
            context_override={
                "required_write_targets": ["artifact-0.txt"],
                "required_verifiers": ["newer-unexecuted-verifier"],
            },
            tool_results=[],
        )
        assert result is not None
        return result
    raise ValueError("unsupported synthetic task boundary scenario")
