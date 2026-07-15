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
from polaris.cells.roles.runtime.public import service as role_runtime_service_module
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleTaskCommandV1,
    RoleRuntimeError,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
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


def test_task_request_projects_only_canonical_execution_attempt(tmp_path: Path) -> None:
    identity = _claim_attempt(tmp_path)
    command = _command(tmp_path, identity)
    request = RoleRuntimeService._build_task_request(command)

    assert command.session_id == identity.session_id
    assert request.metadata["task_runtime_session_id"] == identity.session_id
    assert request.metadata["task_runtime_execution_attempt"] == identity.to_record()


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


@pytest.mark.asyncio
async def test_task_execution_projects_result_and_evidence_from_canonical_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _claim_attempt(tmp_path)
    command = _command(tmp_path, identity)
    runtime = RoleRuntimeService()
    request = RoleRuntimeService._build_task_request(command)
    evidence_call: dict[str, object] = {}

    class FakeKernel:
        async def run(self, _role: str, _request: object) -> object:
            return object()

    async def fake_prepare(_command: ExecuteRoleTaskCommandV1) -> object:
        return request

    def fake_emit(**kwargs: object) -> tuple[object, ...]:
        evidence_call.update(kwargs)
        return ()

    monkeypatch.setattr(runtime, "_get_kernel", lambda _workspace: FakeKernel())
    monkeypatch.setattr(runtime, "_prepare_task_request", fake_prepare)
    monkeypatch.setattr(runtime, "_emit_cognitive_runtime_shadow_artifacts", fake_emit)
    monkeypatch.setattr(
        role_runtime_service_module,
        "_to_contract_result",
        lambda **kwargs: dict(kwargs),
    )
    monkeypatch.setattr(
        role_runtime_service_module,
        "_cognitive_runtime_result_patch",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        role_runtime_service_module,
        "_with_result_metadata_patch",
        lambda result, _patch: result,
    )

    result = await runtime.execute_role_task(command)

    assert result["session_id"] == identity.session_id
    assert evidence_call["session_id"] == identity.session_id
    assert request.metadata["task_runtime_session_id"] == identity.session_id


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


@pytest.mark.asyncio
async def test_execute_role_task_validates_persisted_attempt_before_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _claim_attempt(tmp_path)
    runtime = RoleRuntimeService()
    calls: list[object] = []

    class FakeKernel:
        async def run(self, _role: str, request: object) -> object:
            calls.append(request)
            raise RuntimeError("kernel reached")

    monkeypatch.setattr(runtime, "_get_kernel", lambda _workspace: FakeKernel())

    with pytest.raises(RuntimeError, match="kernel reached"):
        await runtime.execute_role_task(_command(tmp_path, identity))
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["forged", "stale", "expired", "misaligned"])
async def test_invalid_execution_attempt_never_creates_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    identity = _claim_attempt(tmp_path)
    command = _command(tmp_path, identity)
    runtime = RoleRuntimeService()
    kernel_calls: list[str] = []
    monkeypatch.setattr(runtime, "_get_kernel", lambda _workspace: kernel_calls.append("called"))

    if case == "forged":
        command = _command(tmp_path, replace(identity, session_id="tx-forged"))
    elif case == "stale":
        suspended = TaskRuntimeService(str(tmp_path)).settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path),
                identity=identity,
                outcome="suspended",
                summary="make the attempt stale",
                lock_timeout_seconds=5.0,
            )
        )
        assert suspended["success"] is True
    elif case == "expired":
        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.validate_task_runtime_execution_attempt",
            lambda query: TaskRuntimeExecutionAttemptValidationVerdictV1(
                valid=False,
                code="session_lease_expired",
                workspace=query.workspace,
                identity=query.identity,
                evidence={"reason": "test expired persisted session verdict"},
            ),
        )
    else:
        command = ExecuteRoleTaskCommandV1(
            role="director",
            task_id="TASK-OTHER",
            workspace=str(tmp_path),
            objective="implement the governed task",
            run_id="run-41",
            execution_attempt=identity,
        )

    with pytest.raises(RoleRuntimeError) as error:
        await runtime.execute_role_task(command)

    expected_code = (
        "task_runtime_execution_attempt_command_mismatch"
        if case == "misaligned"
        else "task_runtime_execution_attempt_invalid"
    )
    assert error.value.code == expected_code
    assert kernel_calls == []
