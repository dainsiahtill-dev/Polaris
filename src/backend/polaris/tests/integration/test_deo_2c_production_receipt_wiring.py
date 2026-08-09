"""DEO-2C production composition across roles.kernel and roles.adapters."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_policy_revalidation_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    hash_directed_effect_target_state_components,
)
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    defer_director_command_with_director_tools,
    run_runtime_repair_with_director_tools,
)
from polaris.cells.roles.adapters.public import create_director_directed_effect_mutation_port
from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
    DeferredRepairEffectSynthesizer,
    DeferredRequestReplayFence,
)
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import DirectedEffectLifecycleService
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.internal.transaction.deferred_repair_followup import (
    DeferredCommandEffectSynthesizer,
    DeferredRepairFollowupV1,
    build_deferred_repair_followup,
)
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnId,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_contracts import _PolicyPort as _ExecutionPolicyDelegate
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import (
    _authority,
    _candidate,
    _job_restriction_evidence,
    _RecordingPolicyPort,
    _setup_attempt,
)
from polaris.cells.runtime.task_runtime.public import (
    GetDirectedEffectOperationQueryV1,
    get_directed_effect_operation,
)


async def _reject_non_mutation_execution(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Prove directed writes cannot escape through the generic executor lane."""

    raise AssertionError(f"directed effect escaped mutation port: tool={tool_name!r}, arguments={arguments!r}")


class _WorkspaceObservingPolicyPort(_RecordingPolicyPort):
    """Test policy delegate that physically re-observes the mutation target."""

    def __init__(self, workspace: Path) -> None:
        super().__init__(events=[])
        self._workspace = workspace

    async def revalidate(self, request: Any) -> Any:
        snapshot = request.bound_snapshot.snapshot
        baseline = snapshot.baseline_target_state_evidence
        if baseline.is_no_file_state:
            return await _ExecutionPolicyDelegate().revalidate(request)
        path = self._workspace / baseline.target_path
        exists = path.is_file()
        before_content_hash = sha256(path.read_bytes()).hexdigest() if exists else baseline.before_content_hash
        target_state_hash = hash_directed_effect_target_state_components(
            target_path=baseline.target_path,
            exists=exists,
            before_content_hash=before_content_hash,
            minimal_content_evidence=baseline.minimal_content_evidence,
            agents_policy_hash=baseline.agents_policy_hash,
            is_no_file_state=False,
        )
        target = DirectorEffectTargetStateEvidenceV1(
            target_path=baseline.target_path,
            exists=exists,
            before_content_hash=before_content_hash,
            minimal_content_evidence=baseline.minimal_content_evidence,
            agents_policy_hash=baseline.agents_policy_hash,
            target_state_hash=target_state_hash,
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
            current_target_state_hash=target_state_hash,
            current_normalized_operation_hash=snapshot.normalized_operation_hash,
            target_observation_performed=True,
            current_evidence_hash=evidence_hash,
        )


def _adapter_receipt(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"results": [{"status": "success", "result": result}], "raw_results": []}]


def _followup_synthesizers() -> tuple[DeferredRepairEffectSynthesizer, DeferredCommandEffectSynthesizer]:
    fence = DeferredRequestReplayFence()
    return DeferredRepairEffectSynthesizer(fence), DeferredCommandEffectSynthesizer(fence)


async def _execute_followup(
    *,
    followup: DeferredRepairFollowupV1,
    attempt: Any,
    policy_port: Any,
    prepared_out: list[Any] | None = None,
) -> list[Any]:
    """Prepare the exact synthesized inventory and execute only its forward partition."""

    binding_by_call_id = dict(followup.effect_bindings_by_call_id)
    candidates = []
    restrictions = []
    for ordinal, invocation in enumerate(followup.inventory_invocations):
        call_id = str(invocation.call_id)
        binding = binding_by_call_id.get(call_id)
        is_command = invocation.tool_name == "execute_command"
        if is_command:
            target_path = ""
            target_exists = False
            target_before_content_hash = "0" * 64
        else:
            assert binding is not None
            target_path = str(binding.effect.target_path)
            target_exists = bool(binding.effect.exists_before)
            target_before_content_hash = str(binding.effect.expected_before_hash)
        candidate = _candidate(
            attempt,
            ordinal=ordinal,
            tool_call_id=call_id,
            normalized_tool_name=invocation.tool_name,
            normalized_arguments=tuple(sorted(dict(invocation.arguments).items())),
            target_path=target_path,
            target_exists=target_exists,
            target_before_content_hash=target_before_content_hash,
            is_no_file_state=is_command,
            turn_id="turn-adapter-followup",
            batch_id=followup.batch_id,
            allowed_commands=("python",) if is_command else (),
            allowed_paths=() if is_command else ("src/",),
        )
        candidates.append(candidate)
        restrictions.append(
            (
                call_id,
                _job_restriction_evidence(
                    allowed_commands=("python",) if is_command else (),
                    allowed_paths=() if is_command else ("src/",),
                ),
            )
        )
    authority = _authority(attempt)
    lifecycle_result = DirectedEffectLifecycleService(policy_snapshot_port=policy_port).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-adapter-followup",
        batch_id=followup.batch_id,
        candidates=tuple(candidates),
    )
    assert lifecycle_result.status == "ready"
    prepared = lifecycle_result.prepared_batch
    assert prepared is not None
    if prepared_out is not None:
        prepared_out.append(prepared)
    fence_ports = create_directed_effect_fence_ports()
    mutation_port = create_director_directed_effect_mutation_port(
        workspace=attempt.workspace,
        policy_snapshot_port=policy_port,
        fence_consume_port=fence_ports.consume,
    )
    runtime = ToolBatchRuntime(
        executor=_reject_non_mutation_execution,
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=mutation_port,
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(restrictions),
        directed_effect_dispatch_call_ids=followup.forward_call_ids,
        directed_effect_abort_call_ids=followup.rollback_call_ids,
        directed_effect_repair_bindings_by_call_id=followup.effect_bindings_by_call_id,
        directed_effect_rollback_activation_by_call_id=followup.rollback_activation_by_call_id,
    )
    return await runtime.execute_batch(
        followup.dispatch_batch,
        TurnId("turn-adapter-followup"),
    )


@pytest.mark.asyncio
async def test_production_mutation_port_receipt_reaches_tool_batch_runtime(tmp_path: Path) -> None:
    """Real TaskRuntime claim, fence, physical effect, and receipt remain one kernel flow."""

    workspace = str(tmp_path / "workspace")
    attempt = _setup_attempt(workspace)
    candidate = _candidate(attempt, ordinal=0)

    class _ExecutionPolicyPort(_RecordingPolicyPort):
        async def revalidate(self, request: Any) -> Any:
            return await _ExecutionPolicyDelegate().revalidate(request)

    policy_port = _ExecutionPolicyPort(events=[])
    authority = _authority(attempt)
    lifecycle_result = DirectedEffectLifecycleService(policy_snapshot_port=policy_port).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=(candidate,),
    )
    assert lifecycle_result.status == "ready"
    prepared = lifecycle_result.prepared_batch
    assert prepared is not None
    member = prepared.prepared_members[0].member
    fence_ports = create_directed_effect_fence_ports()
    mutation_port = create_director_directed_effect_mutation_port(
        workspace=prepared.execution_attempt.workspace,
        policy_snapshot_port=policy_port,
        fence_consume_port=fence_ports.consume,
    )
    runtime = ToolBatchRuntime(
        executor=_reject_non_mutation_execution,
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=mutation_port,
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=((member.tool_call_id, _job_restriction_evidence()),),
        directed_effect_dispatch_call_ids=(member.tool_call_id,),
    )
    invocation = ToolInvocation(
        call_id=ToolCallId(member.tool_call_id),
        tool_name=member.normalized_tool_name,
        arguments={"content": "after\n", "path": "src/a.py"},
    )

    receipts = await runtime.execute_batch(
        ToolBatch(
            batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=[invocation],
            serial_writes=[invocation],
        ),
        TurnId("turn-production-receipt"),
    )

    assert len(receipts) == 1
    assert receipts[0].success_count == 1, {
        key: receipts[0].raw_results[0].get(key) for key in ("error", "error_code", "message", "result", "metadata")
    }
    raw_result = receipts[0].raw_results[0]
    assert raw_result["status"] == "success"
    assert raw_result["tool_name"] == "write_file"
    assert raw_result["effect_receipt"]["authoritative"] is True
    assert raw_result["effect_receipt"]["durable"] is True
    assert raw_result["effect_receipt_commit"]["code"] == "receipt_committed"
    assert raw_result["effect_receipt"]["tool_call_id"] == member.tool_call_id
    assert raw_result["effect_receipt"]["physical_result_hash"]
    assert (Path(workspace) / "src" / "a.py").read_text(encoding="utf-8") == "after\n"


@pytest.mark.asyncio
async def test_sequential_production_receipts_do_not_stale_the_next_claim(tmp_path: Path) -> None:
    """Each receipt advances the shared DEO stream before the next write claims it."""

    workspace_path = tmp_path / "workspace-sequential"
    workspace = str(workspace_path)
    attempt = _setup_attempt(workspace)
    paths = ("src/one.py", "src/two.py")
    candidates = tuple(
        _candidate(
            attempt,
            ordinal=index,
            normalized_arguments=(("content", f"value-{index}\n"), ("path", path)),
            target_path=path,
            target_exists=False,
            target_before_content_hash="0" * 64,
            turn_id="turn-sequential",
            batch_id="batch-sequential",
        )
        for index, path in enumerate(paths)
    )
    policy_port = _WorkspaceObservingPolicyPort(workspace_path)
    authority = _authority(attempt)
    lifecycle_result = DirectedEffectLifecycleService(policy_snapshot_port=policy_port).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-sequential",
        batch_id="batch-sequential",
        candidates=candidates,
    )
    assert lifecycle_result.status == "ready"
    prepared = lifecycle_result.prepared_batch
    assert prepared is not None
    members = tuple(item.member for item in prepared.prepared_members)
    fence_ports = create_directed_effect_fence_ports()
    mutation_port = create_director_directed_effect_mutation_port(
        workspace=workspace,
        policy_snapshot_port=policy_port,
        fence_consume_port=fence_ports.consume,
    )
    runtime = ToolBatchRuntime(
        executor=_reject_non_mutation_execution,
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=mutation_port,
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(
            (member.tool_call_id, _job_restriction_evidence(allowed_paths=("src/",))) for member in members
        ),
        directed_effect_dispatch_call_ids=tuple(member.tool_call_id for member in members),
    )
    invocations = [
        ToolInvocation(
            call_id=ToolCallId(member.tool_call_id),
            tool_name=member.normalized_tool_name,
            arguments={"content": f"value-{index}\n", "path": paths[index]},
        )
        for index, member in enumerate(members)
    ]

    receipts = await runtime.execute_batch(
        ToolBatch(
            batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=invocations,
            serial_writes=invocations,
        ),
        TurnId("turn-sequential"),
    )

    assert [receipt.success_count for receipt in receipts] == [1, 1]
    assert [receipt.raw_results[0]["directed_effect_claim_status"] for receipt in receipts] == [
        "claimed",
        "claimed",
    ]
    for index, path in enumerate(paths):
        assert (workspace_path / path).read_text(encoding="utf-8") == f"value-{index}\n"


@pytest.mark.asyncio
async def test_adapter_repair_request_reaches_production_mutation_receipt(tmp_path: Path) -> None:
    """Actual adapter planning must reach TaskRuntime/fence/mutation receipt without a mock writer."""

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    attempt = _setup_attempt(str(workspace_path))
    source = workspace_path / "src" / "models" / "weather.py"
    source.parent.mkdir(parents=True)
    source_content = "class Weather:\n    pass\n"
    source.write_text(source_content, encoding="utf-8")
    adapter_results = run_runtime_repair_with_director_tools(
        None,
        workspace_path=workspace_path,
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
        source_tool="deterministic_python_missing_module_alias_repair",
        base_files={"src/models/weather.py": source_content},
        artifact_quality_errors=("ModuleNotFoundError: No module named 'weather'",),
        allowed_paths=("src/weather.py",),
    )
    assert len(adapter_results) == 1 and adapter_results[0]["success"] is True
    repair_synthesizer, command_synthesizer = _followup_synthesizers()
    followup = build_deferred_repair_followup(
        _adapter_receipt(adapter_results[0]),
        primary_batch_id="primary-adapter-repair",
        turn_id="turn-adapter-followup",
        expected_workspace=attempt.workspace,
        expected_task_id=attempt.external_task_id,
        expected_execution_attempt=attempt,
        synthesizer=repair_synthesizer,
        command_synthesizer=command_synthesizer,
    )
    assert followup is not None
    assert len(followup.forward_call_ids) == 1
    assert len(followup.rollback_call_ids) == 1

    receipts = await _execute_followup(
        followup=followup,
        attempt=attempt,
        policy_port=_WorkspaceObservingPolicyPort(workspace_path),
    )

    assert receipts[0].success_count == 1, {
        key: receipts[0].raw_results[0].get(key) for key in ("error", "error_code", "message", "result", "metadata")
    }
    raw_result = receipts[0].raw_results[0]
    assert raw_result["status"] == "success"
    assert raw_result["effect_receipt"]["tool_call_id"] == followup.forward_call_ids[0]
    assert raw_result["effect_receipt"]["physical_result_hash"]
    assert "from models.weather import *" in (workspace_path / "src" / "weather.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_stale_deferred_repair_terminalizes_claim_without_physical_effect(tmp_path: Path) -> None:
    """A second stale repair wave must not leave ``EFFECT_STARTED`` forever."""

    workspace_path = tmp_path / "workspace-stale-repair"
    workspace_path.mkdir()
    attempt = _setup_attempt(str(workspace_path))
    source = workspace_path / "src" / "models" / "weather.py"
    source.parent.mkdir(parents=True)
    source_content = "class Weather:\n    pass\n"
    source.write_text(source_content, encoding="utf-8")
    adapter_results = run_runtime_repair_with_director_tools(
        None,
        workspace_path=workspace_path,
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
        source_tool="deterministic_python_missing_module_alias_repair",
        base_files={"src/models/weather.py": source_content},
        artifact_quality_errors=("ModuleNotFoundError: No module named 'weather'",),
        allowed_paths=("src/weather.py",),
    )
    repair_synthesizer, command_synthesizer = _followup_synthesizers()
    followup = build_deferred_repair_followup(
        _adapter_receipt(adapter_results[0]),
        primary_batch_id="primary-stale-adapter-repair",
        turn_id="turn-stale-adapter-followup",
        expected_workspace=attempt.workspace,
        expected_task_id=attempt.external_task_id,
        expected_execution_attempt=attempt,
        synthesizer=repair_synthesizer,
        command_synthesizer=command_synthesizer,
    )
    assert followup is not None
    # Earlier wave landed target after this repair plan was composed.
    target = workspace_path / "src" / "weather.py"
    target.write_text("already repaired\n", encoding="utf-8")
    prepared_out: list[Any] = []

    receipts = await _execute_followup(
        followup=followup,
        attempt=attempt,
        policy_port=_WorkspaceObservingPolicyPort(workspace_path),
        prepared_out=prepared_out,
    )

    assert receipts[0].failure_count == 1
    raw_result = receipts[0].raw_results[0]
    assert raw_result["status"] == "error"
    assert raw_result["error"] == "deo_target_state_drift"
    assert target.read_text(encoding="utf-8") == "already repaired\n"
    prepared = prepared_out[0]
    primary = prepared.prepared_members[0].member
    operation = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=attempt.workspace,
            task_id=attempt.task_id,
            execution_attempt=attempt,
            parent_binding=prepared.parent_binding,
            tool_call_id=primary.tool_call_id,
            effect_id=primary.effect_id,
        )
    )
    assert operation.ok is True
    assert operation.state == "DEAD_LETTER"


@pytest.mark.asyncio
async def test_adapter_command_request_reaches_physical_receipt_and_replay_fence(tmp_path: Path) -> None:
    """Actual adapter command deferral must execute once through the same bounded authority chain."""

    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    attempt = _setup_attempt(str(workspace_path))
    adapter_result = defer_director_command_with_director_tools(
        workspace_path=workspace_path,
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
        command="python --version",
        purpose="integration_verification",
    )
    assert adapter_result["success"] is True
    repair_synthesizer, command_synthesizer = _followup_synthesizers()
    followup = build_deferred_repair_followup(
        _adapter_receipt(adapter_result),
        primary_batch_id="primary-adapter-command",
        turn_id="turn-adapter-followup",
        expected_workspace=attempt.workspace,
        expected_task_id=attempt.external_task_id,
        expected_execution_attempt=attempt,
        synthesizer=repair_synthesizer,
        command_synthesizer=command_synthesizer,
    )
    assert followup is not None
    assert len(followup.forward_call_ids) == 1
    assert followup.rollback_call_ids == ()

    class _ExecutionPolicyPort(_RecordingPolicyPort):
        async def revalidate(self, request: Any) -> Any:
            return await _ExecutionPolicyDelegate().revalidate(request)

    receipts = await _execute_followup(
        followup=followup,
        attempt=attempt,
        policy_port=_ExecutionPolicyPort(events=[]),
    )

    assert receipts[0].success_count == 1
    raw_result = receipts[0].raw_results[0]
    assert raw_result["status"] == "success"
    assert raw_result["tool_name"] == "execute_command"
    assert raw_result["effect_receipt"]["physical_result_hash"]
    with pytest.raises(RuntimeError, match="deo_deferred_command_request_replayed"):
        build_deferred_repair_followup(
            _adapter_receipt(
                defer_director_command_with_director_tools(
                    workspace_path=workspace_path,
                    task_id=attempt.external_task_id,
                    execution_attempt=attempt,
                    command="python --version",
                    purpose="integration_verification",
                )
            ),
            primary_batch_id="primary-adapter-command-replay",
            turn_id="turn-adapter-followup",
            expected_workspace=attempt.workspace,
            expected_task_id=attempt.external_task_id,
            expected_execution_attempt=attempt,
            synthesizer=repair_synthesizer,
            command_synthesizer=command_synthesizer,
        )
