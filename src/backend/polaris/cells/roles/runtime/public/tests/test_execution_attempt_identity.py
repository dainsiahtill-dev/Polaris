"""Public role-runtime tests for canonical TaskRuntime execution attempts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleTaskCommandV1,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService


def _bootstrap_task_runtime_fact_stream(workspace: Path) -> None:
    """Establish the static FactStream authority required by TaskRuntime event I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="roles-runtime-execution-attempt-identity-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _claim_attempt(tmp_path: Path) -> TaskRuntimeExecutionAttemptIdentityV1:
    _bootstrap_task_runtime_fact_stream(tmp_path)
    task_runtime = TaskRuntimeService(str(tmp_path))
    row = task_runtime.ensure_task_row(
        external_task_id="TASK-41",
        subject="Task 41",
        description="implement canonical execution attempt consumption",
    )
    task_id = task_runtime.normalize_task_id(row["id"])
    assert task_id is not None
    claim = task_runtime.claim_execution(
        task_id,
        worker_id="director-worker",
        role_id="director",
        run_id="run-41",
        external_task_id="TASK-41",
    )
    assert claim["success"] is True
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])


def _command(tmp_path: Path, identity: TaskRuntimeExecutionAttemptIdentityV1) -> ExecuteRoleTaskCommandV1:
    return ExecuteRoleTaskCommandV1(
        role="director",
        task_id="TASK-41",
        workspace=str(tmp_path),
        objective="implement the governed task",
        run_id="run-41",
        execution_attempt=identity,
    )


@pytest.mark.asyncio
async def test_retired_legacy_role_scenarios_stay_closed(tmp_path: Path) -> None:
    """The legacy role scenarios have no live subject after the retirement.

    These scenarios drove the legacy role execution kernel, which is
    retired: a new engineering run has exactly one owner, the engineering run
    service. The scenarios are replaced by the retirement contract, so the
    legacy write entry cannot silently come back.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
        assert_legacy_task_execution_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        session_id="retired-legacy-role-scenarios",
        workspace=str(tmp_path),
    )
    await assert_legacy_task_execution_entry_is_closed(
        task_id="retired-legacy-role-scenarios",
        workspace=str(tmp_path),
    )


def test_task_request_projects_only_canonical_execution_attempt(tmp_path: Path) -> None:
    identity = _claim_attempt(tmp_path)
    command = replace(_command(tmp_path, identity), stream=True, timeout_seconds=73)
    request = RoleRuntimeService._build_task_request(command)

    assert command.session_id == identity.session_id
    assert request.metadata["task_runtime_session_id"] == identity.session_id
    assert request.metadata["task_runtime_execution_attempt"] == identity.to_record()
    assert request.metadata["stream"] is True
    assert request.context_override["llm_call_timeout_seconds"] == 73
    assert request.context_override["request_timeout_seconds"] == 73
    assert request.context_override["timeout_seconds"] == 73


def test_task_request_projects_typed_structured_output_contract(tmp_path: Path) -> None:
    identity = _claim_attempt(tmp_path)
    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )
    command = replace(
        _command(tmp_path, identity),
        role="chief_engineer",
        structured_output_contract=contract,
    )

    request = RoleRuntimeService._build_task_request(command)

    assert request.context_override[STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY] == (contract.to_context_projection())


def test_task_request_rejects_structured_output_context_drift(tmp_path: Path) -> None:
    identity = _claim_attempt(tmp_path)
    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {"construction_plan": {"type": "object"}},
            "required": ["construction_plan"],
        },
    )
    command = replace(
        _command(tmp_path, identity),
        role="chief_engineer",
        context={STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: {"schema_name": "drift"}},
        structured_output_contract=contract,
    )

    with pytest.raises(ValueError, match="structured_output_contract_context_drift"):
        RoleRuntimeService._build_task_request(command)


def test_task_command_rejects_mismatched_session_before_kernel(tmp_path: Path) -> None:
    identity = _claim_attempt(tmp_path)
    kernel_calls: list[str] = []

    with pytest.raises(ValueError, match=r"must equal execution_attempt\.session_id"):
        ExecuteRoleTaskCommandV1(
            role="director",
            task_id="TASK-41",
            workspace=str(tmp_path),
            objective="implement the governed task",
            run_id="run-41",
            session_id="role-chat-session",
            execution_attempt=identity,
        )

    assert kernel_calls == []


def test_task_command_rejects_raw_execution_authority_metadata(tmp_path: Path) -> None:
    identity = _claim_attempt(tmp_path)

    with pytest.raises(ValueError, match="must use typed execution_attempt"):
        ExecuteRoleTaskCommandV1(
            role="director",
            task_id="TASK-41",
            workspace=str(tmp_path),
            objective="implement the governed task",
            metadata={"task_runtime_session_id": identity.session_id},
            execution_attempt=identity,
        )
