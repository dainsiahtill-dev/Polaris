"""DEO-2B Task 8 Director production composition-root regressions."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.roles.adapters.internal.director import adapter as director_adapter_module
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
    caller_context = {
        "task_id": context_task_id,
        "run_id": "RUN-8",
        "session_id": "session-TASK-8",
        "task_runtime_guard": True,
        "job_token": {
            "token_id": "job-TASK-8",
            "capability_audit": {"ok": True, "issues": []},
            "allowed_write_paths": ["src/main.rs"],
        },
        "task_runtime_execution_attempt_authority": create_task_runtime_execution_attempt_authority(execution_attempt),
    }
    result = await adapter._invoke_role_runtime_session(
        "implement the task",
        context=caller_context,
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
    envelope = caller_context["director_execution_envelope"]
    assert envelope["authorization"]["capability_token_ref"] == "job-TASK-8"
    assert caller_context["execution_envelope_hash"] == envelope["envelope_hash"]


@pytest.mark.asyncio
async def test_director_timeout_boundary_projects_execution_authority_to_caller_context(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    envelope = {
        "schema_version": "polaris.execution_envelope.v1",
        "envelope_hash": "a" * 64,
        "authorization": {"capability_token_ref": "job-1"},
    }

    async def fake_dialogue(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        assert isinstance(context, dict)
        context["director_execution_envelope"] = dict(envelope)
        context["execution_envelope_hash"] = envelope["envelope_hash"]
        return {"content": "done", "success": True}

    monkeypatch.setattr(adapter, "_invoke_role_dialogue", fake_dialogue)
    caller_context: dict[str, Any] = {"job_token": {"token_id": "job-1"}}

    result = await adapter._invoke_role_dialogue_with_timeout(
        "implement",
        context=caller_context,
        timeout_seconds=30,
        stage_label="first_call",
    )

    assert result["success"] is True
    assert caller_context["director_execution_envelope"] == envelope
    assert caller_context["execution_envelope_hash"] == "a" * 64


@pytest.mark.asyncio
async def test_director_timeout_boundary_reserves_settlement_grace_without_extending_provider_budget(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    observed: dict[str, Any] = {}

    async def fake_dialogue(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        assert isinstance(context, dict)
        observed["provider_timeout"] = context["director_role_call_timeout_budget"]["timeout_seconds"]
        return {"content": "done", "success": True, "tool_results": [{"tool": "write_file"}]}

    async def capture_wait_for(awaitable: Any, timeout: float) -> Any:
        observed["watchdog_timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(adapter, "_invoke_role_dialogue", fake_dialogue)
    monkeypatch.setattr(director_adapter_module.asyncio, "wait_for", capture_wait_for)

    result = await adapter._invoke_role_dialogue_with_timeout(
        "repair",
        context={},
        timeout_seconds=30,
        stage_label="quality_repair",
    )

    assert result["success"] is True
    assert observed["provider_timeout"] == 30.0
    assert observed["watchdog_timeout"] == 45.0


@pytest.mark.asyncio
async def test_director_timeout_boundary_rejects_execution_envelope_with_mismatched_job_token(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def fake_dialogue(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        assert isinstance(context, dict)
        context["director_execution_envelope"] = {
            "schema_version": "polaris.execution_envelope.v1",
            "envelope_hash": "e" * 64,
            "authorization": {"capability_token_ref": "job-other"},
        }
        context["execution_envelope_hash"] = "e" * 64
        return {"content": "done", "success": True}

    monkeypatch.setattr(adapter, "_invoke_role_dialogue", fake_dialogue)
    caller_context: dict[str, Any] = {"job_token": {"token_id": "job-1"}}

    result = await adapter._invoke_role_dialogue_with_timeout(
        "implement",
        context=caller_context,
        timeout_seconds=30,
        stage_label="first_call",
    )

    assert result["success"] is True
    assert "director_execution_envelope" not in caller_context
    assert "execution_envelope_hash" not in caller_context


def _strict_leaf_authority_context() -> dict[str, Any]:
    token = {
        "schema_version": 1,
        "token_id": "job-1",
        "capability_audit": {"ok": True, "issues": []},
        "allowed_write_paths": ["src/main.rs"],
        "allowed_read_paths": ["src/main.rs", "src/lib.rs"],
    }
    token_hash = stable_hash(token)
    return {
        "job_token": token,
        "control_plane_job_token": token,
        "capability_token": token,
        "capability_token_hash": token_hash,
        "director_execution_envelope": {
            "envelope_hash": "b" * 64,
            "authorization": {
                "capability_token_ref": "job-1",
                "capability_token_hash": token_hash,
                "allowed_write_paths": ["src/main.rs"],
            },
        },
    }


def _nested_leaf_authority_context() -> dict[str, Any]:
    context = _strict_leaf_authority_context()
    token_hash = context["capability_token_hash"]
    context["metadata"] = {
        "job_token": context.pop("job_token"),
        "control_plane_job_token": context.pop("control_plane_job_token"),
        "capability_token": context.pop("capability_token"),
        "capability_token_hash": token_hash,
        "director_execution_envelope": context.pop("director_execution_envelope"),
    }
    return context


def test_deferred_repair_authority_composes_matching_job_token_and_execution_envelope() -> None:
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        _capability_token_from_context,
    )

    token = _capability_token_from_context(_strict_leaf_authority_context())

    assert token is not None
    assert token["token_id"] == "job-1"
    assert token["execution_envelope_hash"] == "b" * 64
    assert token["allowed_write_paths"] == ["src/main.rs"]


def test_deferred_repair_authority_accepts_nested_projection_bound_to_canonical_root_hash() -> None:
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        _capability_token_from_context,
    )

    token = _capability_token_from_context(_nested_leaf_authority_context())

    assert token is not None
    assert token["token_id"] == "job-1"


def test_deferred_repair_read_dependency_never_expands_write_scope() -> None:
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        _capability_scope_from_context,
    )
    context = _strict_leaf_authority_context()
    token = context["job_token"]
    assert isinstance(token, dict)
    token["allowed_write_paths"] = ["src/consumer.py"]
    token["allowed_read_paths"] = ["src/consumer.py", "src/provider.py"]
    token_hash = stable_hash(token)
    context["capability_token_hash"] = token_hash
    envelope = context["director_execution_envelope"]
    assert isinstance(envelope, dict)
    authorization = envelope["authorization"]
    assert isinstance(authorization, dict)
    authorization["capability_token_ref"] = "job-1"
    authorization["capability_token_hash"] = token_hash
    authorization["allowed_write_paths"] = ["src/consumer.py"]
    context["scope_paths"] = ["src/consumer.py", "src/provider.py"]
    context["allowed_paths"] = ["src/consumer.py", "src/provider.py"]

    scope = _capability_scope_from_context(context)
    assert scope == ("src/consumer.py",)
    assert "src/provider.py" not in scope


@pytest.mark.parametrize(
    "defect",
    (
        "single_alias",
        "absent_schema",
        "bad_token_hash",
        "envelope_hash_mismatch",
        "envelope_token_hash_mismatch",
        "alias_content_mismatch",
    ),
)
def test_deferred_repair_authority_rejects_unbound_or_mismatched_envelope(
    defect: str,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        _capability_token_from_context,
    )

    context = _strict_leaf_authority_context()
    token = context["job_token"]
    envelope = context["director_execution_envelope"]
    assert isinstance(token, dict) and isinstance(envelope, dict)
    authorization = envelope["authorization"]
    assert isinstance(authorization, dict)
    if defect == "single_alias":
        context.pop("control_plane_job_token")
        context.pop("capability_token")
    elif defect == "absent_schema":
        token.pop("schema_version")
        token_hash = stable_hash(token)
        context["capability_token_hash"] = token_hash
        authorization["capability_token_hash"] = token_hash
    elif defect == "bad_token_hash":
        context["capability_token_hash"] = "0" * 64
    elif defect == "envelope_hash_mismatch":
        token["execution_envelope_hash"] = "f" * 64
        token_hash = stable_hash(token)
        context["capability_token_hash"] = token_hash
        authorization["capability_token_hash"] = token_hash
    elif defect == "envelope_token_hash_mismatch":
        authorization["capability_token_hash"] = "0" * 64
    else:
        context["capability_token"] = {**token, "source": "forged"}

    assert _capability_token_from_context(context) is None


@pytest.mark.parametrize(
    "defect",
    ("root_hash_missing", "root_hash_bad", "nested_hash_missing", "nested_hash_bad"),
)
def test_nested_authority_cannot_bypass_canonical_root_token_hash(defect: str) -> None:
    from polaris.cells.roles.adapters.internal.director.deferred_repair_commit_bridge import (
        _capability_token_from_context,
    )

    context = _nested_leaf_authority_context()
    metadata = context["metadata"]
    assert isinstance(metadata, dict)
    if defect == "root_hash_missing":
        context.pop("capability_token_hash")
    elif defect == "root_hash_bad":
        context["capability_token_hash"] = "0" * 64
    elif defect == "nested_hash_missing":
        metadata.pop("capability_token_hash")
    else:
        metadata["capability_token_hash"] = "0" * 64

    assert _capability_token_from_context(context) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority_defect",
    (
        "root_hash_missing",
        "root_hash_bad",
        "malformed_nested_alias",
        "malformed_nested_envelope",
        "malformed_root_alias",
        "malformed_root_envelope",
    ),
)
async def test_invalid_leaf_authority_never_mutates_on_actual_deferred_commit_path(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    authority_defect: str,
) -> None:
    from polaris.cells.roles.adapters.internal.director import (
        deferred_repair_commit_bridge as bridge,
    )

    target = tmp_path / "src" / "main.rs"
    target.parent.mkdir(parents=True)
    target.write_text("fn main() {}\n", encoding="utf-8")
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path.resolve()),
        task_id=1,
        external_task_id="task-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="worker-1",
        run_id="run-1",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    context = (
        _nested_leaf_authority_context()
        if authority_defect.startswith("root_hash") or authority_defect.startswith("malformed_root")
        else _strict_leaf_authority_context()
    )
    if authority_defect == "root_hash_missing":
        context.pop("capability_token_hash")
    elif authority_defect == "root_hash_bad":
        context["capability_token_hash"] = "0" * 64
    elif authority_defect == "malformed_nested_alias":
        token = context["job_token"]
        context["metadata"] = {
            "job_token": "malformed-token",
            "control_plane_job_token": token,
            "capability_token": token,
            "capability_token_hash": context["capability_token_hash"],
        }
    elif authority_defect == "malformed_nested_envelope":
        context["context_override"] = {
            "capability_token_hash": context["capability_token_hash"],
            "director_execution_envelope": ["malformed-envelope"],
        }
    elif authority_defect == "malformed_root_alias":
        context["job_token"] = "malformed-token"
    else:
        context["director_execution_envelope"] = ["malformed-envelope"]

    def unexpected_port_construction(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("invalid authority must fail before physical port construction")

    monkeypatch.setattr(bridge, "create_director_effect_policy_snapshot_port", unexpected_port_construction)
    monkeypatch.setattr(bridge, "create_directed_effect_fence_ports", unexpected_port_construction)
    monkeypatch.setattr(bridge, "create_director_directed_effect_mutation_port", unexpected_port_construction)

    receipts = await bridge.commit_materialization_deferred_repairs(
        workspace=attempt.workspace,
        tool_results=[{"success": True, "result": {"status": "deferred_repair_effects_pending"}}],
        execution_attempt=attempt,
        execution_attempt_authority=create_task_runtime_execution_attempt_authority(attempt),
        context=context,
    )

    assert receipts == []
    assert target.read_text(encoding="utf-8") == "fn main() {}\n"
