"""DEO-2C tests for pure deferred Director repair synthesis."""

from __future__ import annotations

from dataclasses import replace

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairStrategyCatalogV1,
    plan_director_repair,
    query_director_repair_strategy_catalog,
)
from polaris.cells.roles.kernel.internal.deferred_repair_effects import (
    DeferredRepairEffectSynthesizer,
    build_deferred_repair_planning_payload,
)
from polaris.cells.roles.kernel.public import (
    DeferredDirectorRepairEffectBindingV1,
    DeferredDirectorRepairRequestV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation
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


def _attempt(
    *, workspace: str = "/tmp/polaris-deferred-repair", external_task_id: str = "task-1"
) -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace=workspace,
        task_id=41,
        external_task_id=external_task_id,
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-1",
        lease_expires_at="2099-01-01T00:00:00Z",
    )


def _command(*, content: str | None = None) -> PlanDirectorRepairCommandV1:
    original = content or 'import {\n  Reputation,\n  export type ReputationTier,\n} from "./Reputation";\n'
    return PlanDirectorRepairCommandV1(
        source_tool=_typescript_import_specifier_source_tool(),
        base_files={"src/models/Market.ts": original},
        artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
        mode="commit",
    )


def _request(
    *,
    command: PlanDirectorRepairCommandV1 | None = None,
    planning_payload_command: PlanDirectorRepairCommandV1 | None = None,
    attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    request_id: str = "deferred-repair-request-1",
) -> DeferredDirectorRepairRequestV1:
    command = command or _command()
    planning = plan_director_repair(command)
    assert planning.effect_plan is not None
    execution_attempt = attempt or _attempt()
    return DeferredDirectorRepairRequestV1(
        request_id=request_id,
        workspace=execution_attempt.workspace,
        task_id=execution_attempt.external_task_id,
        execution_attempt=execution_attempt,
        plan=planning.effect_plan,
        planning_payload_json=build_deferred_repair_planning_payload(planning_payload_command or command),
        allowed_paths=("src/models/Market.ts",),
    )


def test_deferred_repair_synthesis_replans_and_returns_only_synthetic_tool_invocations() -> None:
    request = _request()
    synthesis = DeferredRepairEffectSynthesizer().synthesize(
        request,
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
    )

    assert synthesis.ok is True
    assert synthesis.error_code is None
    assert all(type(invocation) is ToolInvocation for invocation in synthesis.forward_invocations)
    assert all(type(invocation) is ToolInvocation for invocation in synthesis.rollback_invocations)
    assert len(synthesis.forward_invocations) == 1
    assert len(synthesis.rollback_invocations) == 1
    assert synthesis.forward_invocations[0].tool_name == "edit_file"
    assert synthesis.rollback_invocations[0].tool_name == "write_file"
    assert str(synthesis.forward_invocations[0].call_id).startswith("deferred-repair-")
    assert synthesis.forward_invocations[0].call_id != request.plan.effects[0].call_id
    assert synthesis.rollback_activation_by_call_id == (
        (str(synthesis.rollback_invocations[0].call_id), str(synthesis.forward_invocations[0].call_id)),
    )
    assert {call_id for call_id, _ in synthesis.effect_bindings_by_call_id} == {
        str(synthesis.forward_invocations[0].call_id),
        str(synthesis.rollback_invocations[0].call_id),
    }
    for call_id, binding in synthesis.effect_bindings_by_call_id:
        assert binding.tool_call_id == call_id
        assert binding.request_hash == request.request_hash
        assert binding.plan_hash == request.plan.plan_hash
    first_binding = synthesis.effect_bindings_by_call_id[0][1]
    drifted_binding = DeferredDirectorRepairEffectBindingV1(
        request_id=first_binding.request_id,
        request_hash=first_binding.request_hash,
        plan_hash=first_binding.plan_hash,
        effect=replace(first_binding.effect, expected_before_hash="c" * 64),
    )
    assert drifted_binding.tool_call_id != first_binding.tool_call_id
    assert synthesis.plan_hash == request.plan.plan_hash


def test_deferred_repair_request_requires_canonical_exact_bindings() -> None:
    request = _request()
    assert len(request.request_hash) == 64

    with pytest.raises(TypeError, match="allowed_paths must be an immutable tuple"):
        replace(request, allowed_paths=["src/models/Market.ts"])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="planning_payload_json must be canonical"):
        replace(request, planning_payload_json=f" {request.planning_payload_json}")

    with pytest.raises(ValueError, match="workspace must match execution_attempt"):
        replace(request, workspace="/tmp/other-workspace")

    with pytest.raises(ValueError, match="task_id must match execution_attempt"):
        replace(request, task_id="task-other")


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    (
        ("workspace", "/tmp/other-workspace", "deo_deferred_repair_workspace_mismatch"),
        ("task_id", "task-other", "deo_deferred_repair_task_mismatch"),
        ("execution_attempt", _attempt(external_task_id="task-2"), "deo_deferred_repair_attempt_mismatch"),
    ),
)
def test_deferred_repair_synthesis_rejects_expected_binding_drift(
    field: str,
    value: object,
    error_code: str,
) -> None:
    request = _request()
    expected = {
        "expected_workspace": request.workspace,
        "expected_task_id": request.task_id,
        "expected_execution_attempt": request.execution_attempt,
    }
    expected[f"expected_{field}"] = value
    synthesis = DeferredRepairEffectSynthesizer().synthesize(request, **expected)  # type: ignore[arg-type]

    assert synthesis.ok is False
    assert synthesis.error_code == error_code
    assert synthesis.forward_invocations == ()
    assert synthesis.rollback_invocations == ()


def test_deferred_repair_synthesis_rejects_plan_hash_drift_without_tool_invocations() -> None:
    request = _request()
    object.__setattr__(request.plan, "plan_hash", "0" * 64)

    synthesis = DeferredRepairEffectSynthesizer().synthesize(
        request,
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
    )

    assert synthesis.ok is False
    assert synthesis.error_code == "deo_deferred_repair_plan_hash_mismatch"
    assert synthesis.forward_invocations == ()


def test_deferred_repair_synthesis_rejects_request_hash_drift() -> None:
    request = _request()
    object.__setattr__(request, "request_hash", "0" * 64)

    synthesis = DeferredRepairEffectSynthesizer().synthesize(
        request,
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
    )

    assert synthesis.ok is False
    assert synthesis.error_code == "deo_deferred_repair_request_hash_mismatch"
    assert synthesis.forward_invocations == ()


def test_deferred_repair_synthesis_rejects_replan_mismatch() -> None:
    mismatched_payload = _command(content='import {\n  Reputation,\n  export type OtherTier,\n} from "./Reputation";\n')
    request = _request(planning_payload_command=mismatched_payload)

    synthesis = DeferredRepairEffectSynthesizer().synthesize(
        request,
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
    )

    assert synthesis.ok is False
    assert synthesis.error_code == "deo_deferred_repair_replan_mismatch"
    assert synthesis.forward_invocations == ()


def test_deferred_repair_request_is_consumed_once() -> None:
    request = _request()
    synthesizer = DeferredRepairEffectSynthesizer()
    first = synthesizer.synthesize(
        request,
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
    )
    second = synthesizer.synthesize(
        request,
        expected_workspace=request.workspace,
        expected_task_id=request.task_id,
        expected_execution_attempt=request.execution_attempt,
    )

    assert first.ok is True
    assert second.ok is False
    assert second.error_code == "deo_deferred_repair_request_replayed"
    assert second.forward_invocations == ()


def test_batch_synthesis_failure_does_not_consume_earlier_valid_request() -> None:
    valid = _request(request_id="batch-valid")
    invalid = _request(
        request_id="batch-invalid",
        planning_payload_command=_command(
            content='import {\n  Reputation,\n  export type OtherTier,\n} from "./Reputation";\n'
        ),
    )
    synthesizer = DeferredRepairEffectSynthesizer()

    batch = synthesizer.synthesize_batch(
        (valid, invalid),
        expected_workspace=valid.workspace,
        expected_task_id=valid.task_id,
        expected_execution_attempt=valid.execution_attempt,
    )
    retried = synthesizer.synthesize(
        valid,
        expected_workspace=valid.workspace,
        expected_task_id=valid.task_id,
        expected_execution_attempt=valid.execution_attempt,
    )

    assert batch[0].ok is True
    assert batch[1].error_code == "deo_deferred_repair_replan_mismatch"
    assert retried.ok is True


def test_batch_synthesis_rejects_same_path_across_requests_without_consuming() -> None:
    first = _request(request_id="same-path-first")
    second = _request(request_id="same-path-second")
    synthesizer = DeferredRepairEffectSynthesizer()

    batch = synthesizer.synthesize_batch(
        (first, second),
        expected_workspace=first.workspace,
        expected_task_id=first.task_id,
        expected_execution_attempt=first.execution_attempt,
    )
    retried = synthesizer.synthesize(
        first,
        expected_workspace=first.workspace,
        expected_task_id=first.task_id,
        expected_execution_attempt=first.execution_attempt,
    )

    assert {result.error_code for result in batch} == {"deo_deferred_repair_target_conflict"}
    assert retried.ok is True
