"""DEO-2C tests for one visible deferred-repair follow-up batch."""

from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairStrategyCatalogV1,
    plan_director_repair,
    query_director_repair_strategy_catalog,
)
from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
    DeferredRepairEffectSynthesizer,
    DeferredRequestReplayFence,
)
from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
from polaris.cells.roles.kernel.internal.transaction.deferred_repair_followup import (
    DeferredCommandEffectSynthesizer,
    build_deferred_repair_followup,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.public import (
    create_deferred_director_command_request,
    create_deferred_director_repair_request,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1


def _typescript_import_specifier_source_tool() -> str:
    items = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1()).to_dict()["items"]
    return str(
        next(
            item["source_tool"]
            for item in items
            if item["source_tool"] == "deterministic_typescript_import_specifier_keyword_repair"
        )
    )


def _attempt(workspace: Path) -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace=workspace.resolve().as_posix(),
        task_id=91,
        external_task_id="task-deferred-followup",
        session_id="session-deferred-followup",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-deferred-followup",
        lease_expires_at="2099-01-01T00:00:00Z",
    )


def _request(workspace: Path):
    original = 'import {\n  Reputation,\n  export type ReputationTier,\n} from "./Reputation";\n'
    command = PlanDirectorRepairCommandV1(
        source_tool=_typescript_import_specifier_source_tool(),
        base_files={"src/models/Market.ts": original},
        artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
        deterministic_only=True,
    )
    planning = plan_director_repair(command)
    assert planning.effect_plan is not None
    attempt = _attempt(workspace)
    return create_deferred_director_repair_request(
        workspace=attempt.workspace,
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
        planning_command=command,
        planning_result=planning,
        allowed_paths=("src/models/Market.ts",),
    )


def test_build_followup_extracts_success_only_sanitizes_requests_and_partitions_inventory(tmp_path: Path) -> None:
    request = _request(tmp_path)
    ignored = _request(tmp_path)
    object.__setattr__(ignored, "request_id", "ignored-failed-request")
    receipts = [
        {
            "batch_id": "primary-batch",
            "raw_results": [{"status": "success", "result": {"request": request}}],
            "results": [
                {
                    "status": "success",
                    "result": {"nested": {"deferred_request": request}},
                },
                {
                    "status": "error",
                    "result": {"deferred_request": ignored},
                },
            ],
        }
    ]

    followup = build_deferred_repair_followup(
        receipts,
        primary_batch_id="primary-batch",
        turn_id="turn-1",
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
        synthesizer=DeferredRepairEffectSynthesizer(),
    )

    assert followup is not None
    assert followup.batch_id != "primary-batch"
    assert followup.batch_id.startswith("primary-batch:deferred-repair:")
    assert len(followup.inventory_invocations) == 2
    assert len(followup.dispatch_batch.serial_writes) == 1
    assert tuple(str(item.call_id) for item in followup.dispatch_batch.serial_writes) == followup.forward_call_ids
    assert len(followup.rollback_call_ids) == 1
    assert followup.rollback_activation_by_call_id == ((followup.rollback_call_ids[0], followup.forward_call_ids[0]),)
    assert {call_id for call_id, _ in followup.effect_bindings_by_call_id} == {
        *followup.forward_call_ids,
        *followup.rollback_call_ids,
    }
    assert set(followup.forward_call_ids + followup.rollback_call_ids) == {
        str(item.call_id) for item in followup.inventory_invocations
    }
    assert "deferred_request" not in receipts[0]["results"][0]["result"]["nested"]
    assert "deferred_request" not in receipts[0]["results"][1]["result"]
    assert "request" not in receipts[0]["raw_results"][0]["result"]


@pytest.mark.parametrize("container_factory", (list, tuple))
def test_build_followup_collects_direct_typed_requests_from_sequences(
    tmp_path: Path,
    container_factory: type[list] | type[tuple],
) -> None:
    request = _request(tmp_path)
    receipts = [
        {
            "results": [
                {
                    "status": "success",
                    "result": container_factory((request,)),
                }
            ]
        }
    ]

    followup = build_deferred_repair_followup(
        receipts,
        primary_batch_id="primary-sequence",
        turn_id="turn-sequence",
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
        synthesizer=DeferredRepairEffectSynthesizer(),
    )

    assert followup is not None
    assert followup.request_ids == (request.request_id,)
    assert receipts[0]["results"][0]["result"] == container_factory(())


def test_raw_typed_request_nodes_consume_scan_budget(tmp_path: Path) -> None:
    request = _request(tmp_path)
    receipts = [{"raw_results": [{"requests": [request] * 5000}]}]

    with pytest.raises(RuntimeError, match="deo_deferred_request_scan_capacity_exceeded"):
        build_deferred_repair_followup(
            receipts,
            primary_batch_id="primary-capacity",
            turn_id="turn-capacity",
            expected_workspace=request.workspace,
            expected_task_id=request.task_id,
            expected_execution_attempt=request.execution_attempt,
            synthesizer=DeferredRepairEffectSynthesizer(),
        )


def test_build_followup_rejects_replay_and_never_schedules_second_round(tmp_path: Path) -> None:
    request = _request(tmp_path)
    synthesizer = DeferredRepairEffectSynthesizer()

    def _receipts() -> list[dict]:
        return [{"batch_id": "primary", "results": [{"status": "success", "result": {"request": request}}]}]

    first = build_deferred_repair_followup(
        _receipts(),
        primary_batch_id="primary",
        turn_id="turn-1",
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
        synthesizer=synthesizer,
    )
    assert first is not None

    with pytest.raises(RuntimeError, match="deo_deferred_repair_request_replayed"):
        build_deferred_repair_followup(
            _receipts(),
            primary_batch_id="primary",
            turn_id="turn-1",
            expected_workspace=request.workspace,
            expected_task_id=request.task_id,
            expected_execution_attempt=request.execution_attempt,
            synthesizer=synthesizer,
        )


def test_build_followup_admits_attempt_bound_command_and_rejects_replay(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    request = create_deferred_director_command_request(
        workspace=attempt.workspace,
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
        command="python -m pytest -q",
        timeout_seconds=90,
        purpose="10_step_verify_000",
    )
    command_synthesizer = DeferredCommandEffectSynthesizer()

    def _receipts() -> list[dict]:
        return [
            {
                "batch_id": "primary",
                "results": [{"status": "success", "result": {"deferred_request": request}}],
            }
        ]

    followup = build_deferred_repair_followup(
        _receipts(),
        primary_batch_id="primary",
        turn_id="turn-command",
        expected_workspace=attempt.workspace,
        expected_task_id=attempt.external_task_id,
        expected_execution_attempt=attempt,
        synthesizer=DeferredRepairEffectSynthesizer(),
        command_synthesizer=command_synthesizer,
    )

    assert followup is not None
    assert len(followup.inventory_invocations) == 1
    invocation = followup.inventory_invocations[0]
    assert invocation.tool_name == "execute_command"
    assert invocation.arguments == {"command": "python -m pytest -q", "timeout": 90, "shell": False}
    assert followup.effect_bindings_by_call_id == ()
    assert followup.request_ids == (request.request_id,)

    with pytest.raises(RuntimeError, match="deo_deferred_command_request_replayed"):
        build_deferred_repair_followup(
            _receipts(),
            primary_batch_id="primary",
            turn_id="turn-command",
            expected_workspace=attempt.workspace,
            expected_task_id=attempt.external_task_id,
            expected_execution_attempt=attempt,
            synthesizer=DeferredRepairEffectSynthesizer(),
            command_synthesizer=command_synthesizer,
        )


def test_repair_and_command_share_one_bounded_replay_fence(tmp_path: Path) -> None:
    repair_request = _request(tmp_path)
    attempt = repair_request.execution_attempt
    command_request = create_deferred_director_command_request(
        workspace=attempt.workspace,
        task_id=attempt.external_task_id,
        execution_attempt=attempt,
        command="python -m pytest -q",
        purpose="bounded-command",
    )
    fence = DeferredRequestReplayFence(capacity=1)
    repair_synthesizer = DeferredRepairEffectSynthesizer(_replay_fence=fence)
    command_synthesizer = DeferredCommandEffectSynthesizer(_replay_fence=fence)

    first = build_deferred_repair_followup(
        [{"results": [{"status": "success", "result": {"request": repair_request}}]}],
        primary_batch_id="primary",
        turn_id="turn-repair",
        expected_workspace=attempt.workspace,
        expected_task_id=attempt.external_task_id,
        expected_execution_attempt=attempt,
        synthesizer=repair_synthesizer,
        command_synthesizer=command_synthesizer,
    )
    assert first is not None

    with pytest.raises(RuntimeError, match="deo_deferred_command_fence_capacity"):
        build_deferred_repair_followup(
            [{"results": [{"status": "success", "result": {"request": command_request}}]}],
            primary_batch_id="primary",
            turn_id="turn-command",
            expected_workspace=attempt.workspace,
            expected_task_id=attempt.external_task_id,
            expected_execution_attempt=attempt,
            synthesizer=repair_synthesizer,
            command_synthesizer=command_synthesizer,
        )


def test_deferred_request_extraction_rejects_excessive_nesting(tmp_path: Path) -> None:
    request = _request(tmp_path)
    nested: object = {"request": request}
    for _ in range(40):
        nested = {"nested": nested}

    with pytest.raises(RuntimeError, match="deo_deferred_request_nesting_depth_exceeded"):
        build_deferred_repair_followup(
            [{"results": [{"status": "success", "result": nested}]}],
            primary_batch_id="primary",
            turn_id="turn-depth",
            expected_workspace=request.workspace,
            expected_task_id=request.task_id,
            expected_execution_attempt=request.execution_attempt,
            synthesizer=DeferredRepairEffectSynthesizer(),
        )


@pytest.mark.asyncio
async def test_executor_followup_is_visible_distinct_and_uses_normal_prepare_then_runtime(tmp_path: Path) -> None:
    request = _request(tmp_path)
    receipts = [{"batch_id": "primary", "results": [{"status": "success", "result": {"request": request}}]}]
    executor = object.__new__(ToolBatchExecutor)
    executor.directed_effect_execution_attempt = request.execution_attempt
    executor._deferred_repair_synthesizer = DeferredRepairEffectSynthesizer()
    emitted: list[object] = []
    executor.emit_event = emitted.append
    observed: dict[str, object] = {}

    async def _prepare(self: object, **kwargs: object):
        observed["prepare"] = kwargs
        return list(kwargs["invocations"]), object()  # type: ignore[arg-type]

    def _check(self: object, invocations: object, turn_id: str) -> None:
        observed["policy"] = (invocations, turn_id)

    class _Runtime:
        async def execute_batch(self, batch: object, turn_id: object):
            observed["execute"] = (batch, turn_id)
            return [
                {
                    "batch_id": "runtime-call-receipt",
                    "results": [{"status": "success", "result": {"changed": True}}],
                    "success_count": 1,
                    "failure_count": 0,
                    "pending_async_count": 0,
                    "has_pending_async": False,
                }
            ]

    def _build(self: object, workspace: str, **kwargs: object):
        observed["runtime"] = (workspace, kwargs)
        return _Runtime()

    executor._prepare_directed_effect_dispatch = MethodType(_prepare, executor)
    executor._check_effect_policy = MethodType(_check, executor)
    executor._build_tool_batch_runtime = MethodType(_build, executor)
    ledger = SimpleNamespace(tool_batch_count=1, state_history=[])

    followup_receipts = await executor._execute_deferred_repair_followup(
        receipts_as_dicts=receipts,
        primary_batch_id="primary",
        workspace=request.workspace,
        turn_id="turn-1",
        ledger=ledger,
        cancel_token=CancelToken(),
    )

    assert ledger.tool_batch_count == 2
    assert ledger.state_history[-1][0] == "DEFERRED_REPAIR_FOLLOWUP_SCHEDULED"
    assert len(observed["prepare"]["invocations"]) == 2  # type: ignore[index]
    runtime_kwargs = observed["runtime"][1]  # type: ignore[index]
    assert len(runtime_kwargs["directed_effect_dispatch_call_ids"]) == 1
    assert len(runtime_kwargs["directed_effect_abort_call_ids"]) == 1
    assert len(runtime_kwargs["directed_effect_repair_bindings_by_call_id"]) == 2
    assert runtime_kwargs["directed_effect_rollback_activation_by_call_id"] == (
        (
            runtime_kwargs["directed_effect_abort_call_ids"][0],
            runtime_kwargs["directed_effect_dispatch_call_ids"][0],
        ),
    )
    assert followup_receipts[0]["deferred_repair_followup_batch_id"].startswith("primary:deferred-repair:")
    assert followup_receipts[0]["deferred_repair_request_ids"] == [request.request_id]
    assert "request" not in receipts[0]["results"][0]["result"]
    assert len(emitted) == 1
