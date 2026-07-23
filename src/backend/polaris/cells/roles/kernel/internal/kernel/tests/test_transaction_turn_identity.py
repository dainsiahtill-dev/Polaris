from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.kernel import transaction_turn_executor as executor_module
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import (
    _assert_task_runtime_guard_allows_tool,
    _resolve_turn_transition_id,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_executor import TransactionTurnExecutor
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import (
    TransactionIdentityError,
    _bind_transaction_attempt,
    _require_bound_transaction_attempt,
    _start_transaction_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.outcome_commit import TURN_OUTCOME_STREAM
from polaris.cells.roles.profile.public.service import RoleTurnRequest
from polaris.cells.runtime.task_runtime.public import (
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeService,
    create_task_runtime_execution_attempt_authority,
)


def _request(
    workspace: Path,
    *,
    metadata: dict[str, object] | None = None,
) -> RoleTurnRequest:
    request_metadata = {"execution_attempt_id": "execution-1"} if metadata is None else metadata
    return RoleTurnRequest(
        workspace=str(workspace),
        message="produce the task outcome",
        run_id="factory-run-1",
        task_id="TASK-1",
        metadata=dict(request_metadata),
    )


def _bind(request: RoleTurnRequest, workspace: Path, *, role: str = "chief_engineer") -> str:
    invocation_id = _start_transaction_invocation(
        request,
        role=role,
        workspace=str(workspace),
    )
    return _bind_transaction_attempt(request, invocation_id=invocation_id, attempt=0)


def _bootstrap_fact_stream(workspace: Path) -> None:
    """Provision the explicit FactStream authority required by strict I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="transaction-turn-identity-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _claim_task_runtime_attempt(
    workspace: Path,
) -> tuple[TaskRuntimeService, TaskRuntimeExecutionAttemptIdentityV1]:
    workspace.mkdir(exist_ok=True)
    _bootstrap_fact_stream(workspace)
    runtime = TaskRuntimeService(str(workspace))
    task_id = int(runtime.create_task_row(subject="canonical heartbeat")["id"])
    claim = runtime.claim_execution(
        task_id,
        worker_id="director-worker",
        role_id="director",
        run_id="run-1",
        external_task_id="TASK-1",
        selection_source="canonical-heartbeat-test",
    )
    assert claim["success"] is True
    return runtime, TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])


def test_public_authority_renews_atomically_and_rejects_stale_settlement(tmp_path: Path) -> None:
    runtime, initial = _claim_task_runtime_attempt(tmp_path / "workspace")
    authority = create_task_runtime_execution_attempt_authority(initial)

    heartbeat = authority.heartbeat(
        lease_ttl_seconds=120,
        lock_timeout_seconds=0.5,
        context_summary="authority-regression",
    )
    assert heartbeat.success is True
    renewed = heartbeat.identity
    assert isinstance(renewed, TaskRuntimeExecutionAttemptIdentityV1)

    stale = runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=initial.workspace,
            identity=initial,
            outcome="completed",
            summary="stale identity",
            lock_timeout_seconds=0.5,
        )
    )
    settled = authority.settle(outcome="completed", summary="renewed identity", lock_timeout_seconds=0.5)

    assert stale["success"] is False
    assert stale["code"] == "lease_version_mismatch"
    assert settled.success is True
    assert settled.identity == renewed


def test_transaction_tool_guard_uses_one_public_authority_for_renewal_and_terminal_race() -> None:
    initial = TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=1,
        external_task_id="TASK-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-1",
        lease_expires_at="2030-01-01T00:00:00+00:00",
    )
    heartbeat_entered = threading.Event()
    release_heartbeat = threading.Event()
    settled_identities: list[TaskRuntimeExecutionAttemptIdentityV1] = []
    renewed = replace(initial, lease_expires_at="2030-01-01T00:02:00+00:00")

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        heartbeat_entered.set()
        assert release_heartbeat.wait(timeout=1.0)
        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=renewed,
        )

    def settle(command: SettleTaskRuntimeExecutionAttemptCommandV1) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        settled_identities.append(command.identity)
        return TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=True,
            code="settled",
            workspace=command.workspace,
            identity=command.identity,
            outcome=command.outcome,
        )

    authority = create_task_runtime_execution_attempt_authority(initial, heartbeat=heartbeat, settle=settle)
    request = SimpleNamespace(
        context_override={
            "task_runtime_guard": True,
            "task_runtime_execution_attempt_authority": authority,
        },
        metadata={},
    )
    heartbeat_thread = threading.Thread(target=lambda: _assert_task_runtime_guard_allows_tool(request))
    terminal_result: dict[str, object] = {}
    terminal_thread = threading.Thread(
        target=lambda: terminal_result.setdefault("result", authority.settle(outcome="completed", summary="done"))
    )
    heartbeat_thread.start()
    assert heartbeat_entered.wait(timeout=1.0)
    terminal_thread.start()
    release_heartbeat.set()
    heartbeat_thread.join(timeout=1.0)
    terminal_thread.join(timeout=1.0)

    assert not heartbeat_thread.is_alive()
    assert not terminal_thread.is_alive()
    assert settled_identities == [renewed]
    result = terminal_result["result"]
    assert isinstance(result, TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1)
    assert result.success is True
    with pytest.raises(RuntimeError, match="heartbeat_rejected:authority_closed"):
        _assert_task_runtime_guard_allows_tool(request)


def test_transaction_tool_guard_continuously_renews_and_chat_is_unaffected() -> None:
    initial = TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=1,
        external_task_id="TASK-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-1",
        lease_expires_at="2030-01-01T00:00:00+00:00",
    )
    renewed = TaskRuntimeExecutionAttemptIdentityV1(
        **{**initial.to_record(), "lease_expires_at": "2030-01-01T00:02:00+00:00"}
    )
    renewed_again = TaskRuntimeExecutionAttemptIdentityV1(
        **{**initial.to_record(), "lease_expires_at": "2030-01-01T00:04:00+00:00"}
    )
    received: list[TaskRuntimeExecutionAttemptIdentityV1] = []

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        received.append(command.identity)
        next_identity = renewed if len(received) == 1 else renewed_again
        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=next_identity,
        )

    authority = create_task_runtime_execution_attempt_authority(initial, heartbeat=heartbeat)
    request = SimpleNamespace(
        context_override={
            "task_runtime_guard": True,
            "task_runtime_execution_attempt_authority": authority,
        },
        metadata={},
    )

    _assert_task_runtime_guard_allows_tool(request)
    _assert_task_runtime_guard_allows_tool(request)

    assert received == [initial, renewed]
    assert authority.snapshot().identity == renewed_again

    _assert_task_runtime_guard_allows_tool(SimpleNamespace(context_override={}, metadata={}))
    with pytest.raises(RuntimeError, match="missing task_runtime_execution_attempt_authority"):
        _assert_task_runtime_guard_allows_tool(
            SimpleNamespace(context_override={"task_runtime_guard": True}, metadata={})
        )


def test_same_persistent_request_replay_restores_identical_attempt_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = _request(workspace)
    original_transition = _bind(original, workspace)
    persisted_metadata = json.loads(json.dumps(original.metadata, ensure_ascii=False))
    persisted_metadata.pop("execution_attempt_id")

    replay = _request(workspace, metadata=persisted_metadata)
    replay_invocation = _start_transaction_invocation(
        replay,
        role="chief_engineer",
        workspace=str(workspace),
    )
    replay_transition = _bind_transaction_attempt(
        replay,
        invocation_id=replay_invocation,
        attempt=0,
    )

    assert replay_invocation == original.metadata["transaction_invocation_id"]
    assert replay_transition == original_transition
    assert replay.metadata["transaction_invocation_identity"] == original.metadata["transaction_invocation_identity"]
    assert replay.metadata["transaction_attempt_identity"] == original.metadata["transaction_attempt_identity"]


def test_two_new_executions_with_same_run_and_task_do_not_merge(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _request(workspace, metadata={"execution_attempt_id": "execution-1"})
    second = _request(workspace, metadata={"execution_attempt_id": "execution-2"})

    assert _bind(first, workspace) != _bind(second, workspace)


def test_same_execution_crash_replay_with_explicit_scope_is_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = _request(workspace, metadata={"turn_request_id": "request-1"})
    replay = _request(workspace, metadata={"turn_request_id": "request-1"})

    assert _bind(first, workspace) == _bind(replay, workspace)


def test_different_transaction_attempts_never_share_transition_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace)
    invocation_id = _start_transaction_invocation(
        request,
        role="chief_engineer",
        workspace=str(workspace),
    )

    first = _bind_transaction_attempt(request, invocation_id=invocation_id, attempt=0)
    second = _bind_transaction_attempt(request, invocation_id=invocation_id, attempt=1)

    assert first != second


@pytest.mark.parametrize("metadata", [{}, {"session_id": "chat-session-1"}])
def test_new_invocation_without_authoritative_execution_scope_fails_closed(
    tmp_path: Path,
    metadata: dict[str, object],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(TransactionIdentityError, match="run/task/session fallback is forbidden"):
        _start_transaction_invocation(
            _request(workspace, metadata=metadata),
            role="chief_engineer",
            workspace=str(workspace),
        )


def test_persisted_replay_rejects_stale_execution_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = _request(workspace, metadata={"execution_attempt_id": "execution-1"})
    _bind(original, workspace)
    persisted_metadata = json.loads(json.dumps(original.metadata, ensure_ascii=False))
    persisted_metadata["execution_attempt_id"] = "execution-stale"

    with pytest.raises(TransactionIdentityError, match="execution_scope_id"):
        _start_transaction_invocation(
            _request(workspace, metadata=persisted_metadata),
            role="chief_engineer",
            workspace=str(workspace),
        )


def test_task_runtime_execution_scope_requires_matching_session_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        metadata={
            "task_runtime_session_id": "claim-1",
            "session_id": "claim-stale",
        },
    )

    with pytest.raises(TransactionIdentityError, match="session_id disagrees"):
        _start_transaction_invocation(
            request,
            role="director",
            workspace=str(workspace),
        )


def test_director_task_runtime_claim_is_a_stable_execution_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    metadata: dict[str, object] = {
        "task_runtime_session_id": "tx-claim-1",
        "session_id": "tx-claim-1",
        "runtime_execution": {"session_id": "tx-claim-1"},
    }
    first = _request(workspace, metadata=metadata)
    replay = _request(workspace, metadata=metadata)

    assert _bind(first, workspace, role="director") == _bind(replay, workspace, role="director")
    identity = first.metadata["transaction_invocation_identity"]
    assert identity["execution_scope_kind"] == "task_runtime_session_id"
    assert identity["execution_scope_id"] == "tx-claim-1"


def test_factory_ce_shaped_request_without_execution_attempt_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        metadata={"source": "factory_stage_executor.chief_engineer_portfolio_review"},
    )

    with pytest.raises(TransactionIdentityError, match="first-class stable execution identity"):
        _start_transaction_invocation(
            request,
            role="chief_engineer",
            workspace=str(workspace),
        )


def test_transition_resolver_rejects_stale_compatibility_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace)
    transition_id = _bind(request, workspace)

    request.metadata["turn_transition_id"] = f"{transition_id}-stale"

    with pytest.raises(TransactionIdentityError, match="cannot override the bound transaction attempt"):
        _resolve_turn_transition_id(request)


def test_transition_resolver_accepts_only_equal_compatibility_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace)
    transition_id = _bind(request, workspace)
    request.metadata["turn_transition_id"] = transition_id

    assert _resolve_turn_transition_id(request) == transition_id


def test_transition_resolver_rejects_unproduced_compatibility_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace, metadata={"turn_transition_id": "stale-transition"})

    with pytest.raises(TransactionIdentityError, match="has no bound transaction attempt producer"):
        _resolve_turn_transition_id(request)


def test_bound_attempt_rejects_tampered_typed_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace)
    _bind(request, workspace)
    request.metadata["transaction_attempt_id"] = "txi_tampered-0"

    with pytest.raises(TransactionIdentityError, match="does not match invocation and attempt"):
        _require_bound_transaction_attempt(request)


def test_legacy_bound_attempt_is_migrated_to_typed_metadata_at_executor_boundary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        metadata={
            "execution_attempt_id": "legacy-execution-1",
            "transaction_invocation_id": "legacy-invocation",
            "transaction_attempt": 0,
            "transaction_attempt_id": "legacy-invocation-0",
        },
    )

    identity = _require_bound_transaction_attempt(
        request,
        role="chief_engineer",
        workspace=str(workspace),
    )

    assert identity.transition_id == "legacy-invocation-0"
    assert request.metadata["transaction_invocation_identity"]["derivation"] == "persisted_metadata"
    assert request.metadata["transaction_attempt_identity"] == identity.to_record()


@pytest.mark.asyncio
async def test_execute_turn_rejects_unbound_identity_before_llm_tool_or_fact_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_fact_stream(workspace)
    request = _request(workspace, metadata={})
    kernel = RoleExecutionKernel.create_default(workspace=str(workspace))
    create_kernel = MagicMock()
    prepare_invocation = AsyncMock()
    monkeypatch.setattr(executor_module, "create_transaction_kernel", create_kernel)
    monkeypatch.setattr(executor_module, "build_transaction_invocation_setup", prepare_invocation)

    with pytest.raises(TransactionIdentityError, match="transaction_identity_unbound"):
        await TransactionTurnExecutor(kernel).execute_turn(
            role="chief_engineer",
            profile=SimpleNamespace(),  # type: ignore[arg-type]
            request=request,
            system_prompt="system",
            fingerprint=object(),
            observer_run_id="factory-run-1",
            response_schema=None,
        )

    create_kernel.assert_not_called()
    prepare_invocation.assert_not_awaited()
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream=TURN_OUTCOME_STREAM,
            limit=10,
        )
    )
    assert facts.total == 0


@pytest.mark.asyncio
async def test_execute_stream_rejects_missing_execution_scope_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_fact_stream(workspace)
    request = _request(workspace, metadata={"session_id": "chat-session-only"})
    kernel = RoleExecutionKernel.create_default(workspace=str(workspace))
    create_kernel = MagicMock()
    prepare_invocation = AsyncMock()
    monkeypatch.setattr(executor_module, "create_transaction_kernel", create_kernel)
    monkeypatch.setattr(executor_module, "build_transaction_invocation_setup", prepare_invocation)

    stream = TransactionTurnExecutor(kernel).execute_stream(
        role="chief_engineer",
        profile=SimpleNamespace(),  # type: ignore[arg-type]
        request=request,
        system_prompt="system",
        fingerprint=object(),
        stream_run_id="factory-run-1",
        uep_publisher=SimpleNamespace(),  # type: ignore[arg-type]
    )
    with pytest.raises(TransactionIdentityError, match="run/task/session fallback is forbidden"):
        await anext(stream)

    create_kernel.assert_not_called()
    prepare_invocation.assert_not_awaited()
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream=TURN_OUTCOME_STREAM,
            limit=10,
        )
    )
    assert facts.total == 0
