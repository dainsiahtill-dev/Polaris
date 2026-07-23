"""DEO-2B Task 8 Director production composition-root regressions."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority


class _PolicyPort:
    async def capture_baseline_snapshot(self, request: Any) -> Any:
        raise AssertionError(request)

    async def snapshot(self, request: Any) -> Any:
        raise AssertionError(request)

    async def capture_current_policy_evidence(self, request: Any) -> Any:
        raise AssertionError(request)

    def bind_member(self, request: Any) -> Any:
        raise AssertionError(request)

    async def revalidate(self, request: Any) -> Any:
        raise AssertionError(request)


class _FenceAdmin:
    def register(self, context: Any) -> Any:
        raise AssertionError(context)

    def release_batch(self, batch_id: str, execution_attempt: Any) -> Any:
        raise AssertionError((batch_id, execution_attempt))


class _FenceConsume:
    def consume(self, context: Any) -> Any:
        raise AssertionError(context)


class _MutationPort:
    async def execute_mutation(
        self,
        context: Any,
        normalized_tool_name: str,
        normalized_arguments: Any,
        repair_effect_binding: Any = None,
    ) -> Any:
        raise AssertionError((context, normalized_tool_name, normalized_arguments, repair_effect_binding))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context_task_id", "expected_runtime_task_id"),
    (("91", "TASK-8"), ("TASK-DRIFT", "TASK-DRIFT")),
)
async def test_director_root_builds_one_exact_deo_dependency_bundle(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    context_task_id: str,
    expected_runtime_task_id: str,
) -> None:
    from polaris.cells.roles.adapters.public import (
        directed_effect_policy_service,
        directed_effect_service as mutation_service,
    )
    from polaris.cells.roles.kernel.public import directed_effect_service as fence_service
    from polaris.cells.roles.runtime.public import service as runtime_service

    policy = _PolicyPort()
    fence_admin = _FenceAdmin()
    fence_consume = _FenceConsume()
    mutation = _MutationPort()
    sequence: list[str] = []
    captured: dict[str, Any] = {}

    def create_policy(workspace: str) -> _PolicyPort:
        assert workspace == str(tmp_path)
        sequence.append("policy")
        return policy

    def create_fence() -> Any:
        sequence.append("fence")
        return type("FencePorts", (), {"admin": fence_admin, "consume": fence_consume})()

    def create_mutation(*, workspace: str, policy_snapshot_port: Any, fence_consume_port: Any) -> _MutationPort:
        assert workspace == str(tmp_path)
        assert policy_snapshot_port is policy
        assert fence_consume_port is fence_consume
        sequence.append("mutation")
        return mutation

    class _Runtime:
        def __init__(self, *, directed_effect_runtime: Any, directed_effect_required: bool) -> None:
            captured["dependencies"] = directed_effect_runtime
            captured["required"] = directed_effect_required

        async def execute_role_session(self, command: Any) -> RoleExecutionResultV1:
            captured["command"] = command
            return RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="director",
                workspace=str(tmp_path),
                session_id=command.session_id,
                task_id=command.task_id,
                run_id=command.run_id,
                output="done",
            )

    monkeypatch.setattr(directed_effect_policy_service, "create_director_effect_policy_snapshot_port", create_policy)
    monkeypatch.setattr(fence_service, "create_directed_effect_fence_ports", create_fence)
    monkeypatch.setattr(mutation_service, "create_director_directed_effect_mutation_port", create_mutation)
    monkeypatch.setattr(runtime_service, "RoleRuntimeService", _Runtime)

    adapter = DirectorAdapter(workspace=str(tmp_path))
    execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=tmp_path.resolve().as_posix(),
        task_id=91,
        external_task_id="TASK-8",
        session_id="session-TASK-8",
        attempt=1,
        role_id="director",
        worker_id="director",
        run_id="RUN-8",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    result = await adapter._invoke_role_runtime_session(
        "implement the task",
        context={
            "task_id": context_task_id,
            "run_id": "RUN-8",
            "session_id": "session-TASK-8",
            "task_runtime_guard": True,
            "task_runtime_execution_attempt_authority": create_task_runtime_execution_attempt_authority(
                execution_attempt
            ),
        },
        max_retries=1,
    )

    dependencies = captured["dependencies"]
    assert sequence == ["policy", "fence", "mutation"]
    assert dependencies.policy_snapshot_port is policy
    assert dependencies.fence_admin_port is fence_admin
    assert dependencies.mutation_port is mutation
    assert not hasattr(dependencies, "fence_consume_port")
    assert not hasattr(dependencies, "execution_context")
    assert not hasattr(dependencies, "claim_grant")
    assert captured["required"] is True
    assert captured["command"].task_id == expected_runtime_task_id
    assert captured["command"].session_id == "session-TASK-8"
    assert captured["command"].run_id == "RUN-8"
    assert result["success"] is True
