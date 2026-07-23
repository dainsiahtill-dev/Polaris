"""DEO-2B Task 7 guarded-session attempt validation regressions."""

from __future__ import annotations

import pytest
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    RoleRuntimeError,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    create_task_runtime_execution_attempt_authority,
)


def _identity() -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=7,
        external_task_id="TASK-7",
        session_id="session-7",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-7",
        lease_expires_at="2030-01-01T00:00:00+00:00",
    )


def _command(
    *,
    stream: bool,
    authority: object | None,
    role: str = "director",
    session_id: str = "session-7",
    workspace: str = "/workspace",
    run_id: str = "run-7",
    task_id: str = "TASK-7",
) -> ExecuteRoleSessionCommandV1:
    context: dict[str, object] = {"task_runtime_guard": True}
    if authority is not None:
        context["task_runtime_execution_attempt_authority"] = authority
    return ExecuteRoleSessionCommandV1(
        role=role,
        session_id=session_id,
        workspace=workspace,
        user_message="execute the guarded task",
        run_id=run_id,
        task_id=task_id,
        context=context,
        stream=stream,
    )


async def _invoke(
    runtime: RoleRuntimeService,
    command: ExecuteRoleSessionCommandV1,
    entrypoint: str,
) -> object:
    if entrypoint == "controller":
        return await runtime.create_transaction_controller(command)
    return await runtime.execute_role_session(command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entrypoint", "stream"),
    (("session", False), ("session", True), ("controller", False)),
)
async def test_guarded_session_validates_attempt_before_request_or_kernel(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    stream: bool,
) -> None:
    runtime = RoleRuntimeService()
    later_calls: list[str] = []

    def forbidden_kernel(_workspace: str) -> object:
        later_calls.append("kernel")
        raise AssertionError("kernel accessed before attempt validation")

    async def forbidden_request(*_args: object, **_kwargs: object) -> object:
        later_calls.append("request")
        raise AssertionError("request prepared before attempt validation")

    monkeypatch.setattr(runtime, "_get_kernel", forbidden_kernel)
    monkeypatch.setattr(runtime, "_prepare_session_request", forbidden_request)

    with pytest.raises(RoleRuntimeError) as raised:
        await _invoke(runtime, _command(stream=stream, authority=None), entrypoint)

    assert raised.value.code == "deo_execution_attempt_missing"
    assert later_calls == []


def test_guarded_session_rejects_identity_mismatch_and_invalid_taskruntime_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    authority = create_task_runtime_execution_attempt_authority(identity)
    runtime = RoleRuntimeService()

    mismatch = runtime._validate_directed_effect_session_attempt(
        _command(stream=False, authority=authority, run_id="other-run")
    )
    assert mismatch.status == "denied"
    assert mismatch.error_code == "deo_execution_attempt_mismatch"

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.validate_task_runtime_execution_attempt",
        lambda query: TaskRuntimeExecutionAttemptValidationVerdictV1(
            valid=False,
            code="session_lease_expired",
            workspace=query.workspace,
            identity=query.identity,
        ),
    )
    invalid = runtime._validate_directed_effect_session_attempt(_command(stream=False, authority=authority))
    assert invalid.status == "denied"
    assert invalid.error_code == "deo_execution_attempt_invalid"


def test_guarded_session_rejects_every_command_identity_mismatch() -> None:
    identity = _identity()
    authority = create_task_runtime_execution_attempt_authority(identity)
    commands = (
        _command(stream=False, authority=authority, workspace="/other"),
        _command(stream=False, authority=authority, task_id="OTHER"),
        _command(stream=False, authority=authority, run_id="other-run"),
        _command(stream=False, authority=authority, role="chief_engineer"),
        _command(stream=False, authority=authority, session_id="other-session"),
    )

    for command in commands:
        result = RoleRuntimeService._validate_directed_effect_session_attempt(command)
        assert result.status == "denied"
        assert result.error_code == "deo_execution_attempt_mismatch"


def test_guarded_session_rejects_closed_authority_snapshot() -> None:
    identity = _identity()

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        return TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=True,
            code="settled",
            workspace=command.workspace,
            identity=command.identity,
            outcome=command.outcome,
        )

    authority = create_task_runtime_execution_attempt_authority(identity, settle=settle)
    assert authority.settle(outcome="failed", summary="close before validation").success

    result = RoleRuntimeService._validate_directed_effect_session_attempt(_command(stream=False, authority=authority))

    assert result.status == "denied"
    assert result.error_code == "deo_execution_attempt_invalid"


def test_guarded_session_rejects_malformed_taskruntime_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = _identity()
    authority = create_task_runtime_execution_attempt_authority(identity)
    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.validate_task_runtime_execution_attempt",
        lambda _query: object(),
    )

    result = RoleRuntimeService._validate_directed_effect_session_attempt(_command(stream=False, authority=authority))

    assert result.status == "denied"
    assert result.error_code == "deo_execution_attempt_invalid"


@pytest.mark.asyncio
async def test_direct_stream_validates_before_request() -> None:
    runtime = RoleRuntimeService()
    events = [
        event
        async for event in runtime.stream_chat_turn(
            _command(stream=True, authority=None),
        )
    ]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "valid TaskRuntime execution attempt" in events[0]["error"]


@pytest.mark.asyncio
async def test_unguarded_session_remains_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RoleRuntimeService()
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="chat-session",
        workspace="/workspace",
        user_message="ordinary chat",
        stream=False,
    )
    observed: list[str] = []

    class _Kernel:
        async def run(self, _role: str, _request: object) -> object:
            observed.append("run")
            raise RuntimeError("stop after compatibility boundary")

    async def request(*_args: object, **_kwargs: object) -> object:
        observed.append("request")
        return object()

    monkeypatch.setattr(runtime, "_get_kernel", lambda _workspace: _Kernel())
    monkeypatch.setattr(runtime, "_prepare_session_request", request)

    with pytest.raises(RuntimeError, match="compatibility boundary"):
        await runtime.execute_role_session(command)
    assert observed == ["request", "run"] or observed == ["run", "request"]
