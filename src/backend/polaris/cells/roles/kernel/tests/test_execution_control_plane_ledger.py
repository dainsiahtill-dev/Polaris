from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_executor import TransactionTurnExecutor
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.public.turn_contracts import (
    FinalizeMode,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
from polaris.cells.roles.profile.public.service import (
    RoleExecutionMode,
    RoleToolPolicy,
    RoleTurnRequest,
)


class _DroppedToolDispatchKernel:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.context_gateway_config_factory = None
        self._output_parser = None
        self._injected_output_parser = SimpleNamespace(
            parse_thinking=lambda value: SimpleNamespace(clean_content=value, thinking=None),
            extract_json=lambda _value: None,
        )

    def build_transaction_kernel(self, _role: str, _profile: Any, _request: Any) -> Any:
        class _TransactionKernel:
            async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
                ledger = SimpleNamespace(
                    llm_calls=[
                        {
                            "metadata": {
                                "context_snapshot_ref": "runtime/contexts/aa/context-snapshot.json",
                            }
                        }
                    ],
                    anomaly_flags=[
                        {
                            "type": "TOOL_DISPATCH_DROPPED",
                            "native_tool_calls_count": 1,
                            "provider_response_hash": "provider-response-hash",
                        }
                    ],
                )
                exc = RuntimeError("tool_dispatch_dropped: provider emitted a tool call but none dispatched")
                exc.turn_ledger = ledger  # type: ignore[attr-defined]
                raise exc

        return _TransactionKernel()


class _SuccessfulNoMaterializationKernel(_DroppedToolDispatchKernel):
    def build_transaction_kernel(self, _role: str, _profile: Any, _request: Any) -> Any:
        class _TransactionKernel:
            async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
                ledger = TurnLedger(turn_id="turn-success")
                ledger.record_decision(
                    TurnDecision(
                        turn_id=TurnId("turn-success"),
                        kind=TurnDecisionKind.FINAL_ANSWER,
                        visible_message="done",
                        finalize_mode=FinalizeMode.NONE,
                        domain="code",
                    )
                )
                return {
                    "kind": "final_answer",
                    "visible_content": "done",
                    "metrics": {"llm_calls": 1, "tool_calls": 0},
                    "ledger": ledger,
                }

        return _TransactionKernel()


class _DroppedToolDispatchStreamKernel(_DroppedToolDispatchKernel):
    def build_transaction_kernel(self, _role: str, _profile: Any, _request: Any) -> Any:
        class _TransactionKernel:
            async def execute_stream(self, _turn_id: str, *_args: Any, **_kwargs: Any) -> Any:
                if False:  # pragma: no cover - marks this as an async generator
                    yield None
                ledger = SimpleNamespace(
                    llm_calls=[
                        {
                            "metadata": {
                                "context_snapshot_ref": "runtime/contexts/aa/stream-context-snapshot.json",
                            }
                        }
                    ],
                    anomaly_flags=[
                        {
                            "type": "TOOL_DISPATCH_DROPPED",
                            "native_tool_calls_count": 1,
                            "provider_response_hash": "stream-provider-response-hash",
                        }
                    ],
                )
                exc = RuntimeError("tool_dispatch_dropped: stream provider emitted a tool call but none dispatched")
                exc.turn_ledger = ledger  # type: ignore[attr-defined]
                raise exc

        return _TransactionKernel()


class _SuccessfulStreamNoMaterializationKernel(_SuccessfulNoMaterializationKernel):
    def build_transaction_kernel(self, _role: str, _profile: Any, _request: Any) -> Any:
        class _TransactionKernel:
            async def execute_stream(self, turn_id: str, *_args: Any, **_kwargs: Any) -> Any:
                yield CompletionEvent(
                    turn_id=turn_id,
                    status="success",
                    llm_calls=1,
                    tool_calls=0,
                    monitoring={
                        "context_snapshot_ref": "runtime/contexts/aa/stream-success-context.json",
                    },
                )

        return _TransactionKernel()


class _ForcedWriteZeroDispatchStreamCompletionKernel(_DroppedToolDispatchKernel):
    """Stream turn that completes 'successfully' although the final request forced a write."""

    def build_transaction_kernel(self, _role: str, _profile: Any, _request: Any) -> Any:
        class _TransactionKernel:
            async def execute_stream(self, turn_id: str, *_args: Any, **_kwargs: Any) -> Any:
                yield CompletionEvent(
                    turn_id=turn_id,
                    status="success",
                    llm_calls=1,
                    tool_calls=0,
                    monitoring=cast(
                        "dict[str, float]",
                        {
                            "context_snapshot_ref": "runtime/contexts/aa/stream-forced-write-context.json",
                            "native_tool_calls_count": 0,
                            "final_request_context_audit": {
                                "final_request_evidence_coverage": {
                                    "required_tools": ["write_file"],
                                },
                            },
                        },
                    ),
                )

        return _TransactionKernel()


class _SuccessfulStreamCompletedTurnKernel(_DroppedToolDispatchKernel):
    """Normal completed stream turn without boundary obligations or forced writes."""

    def build_transaction_kernel(self, _role: str, _profile: Any, _request: Any) -> Any:
        class _TransactionKernel:
            async def execute_stream(self, turn_id: str, *_args: Any, **_kwargs: Any) -> Any:
                yield CompletionEvent(
                    turn_id=turn_id,
                    status="success",
                    llm_calls=1,
                    tool_calls=0,
                    monitoring=cast(
                        "dict[str, float]",
                        {
                            "context_snapshot_ref": "runtime/contexts/aa/stream-normal-context.json",
                        },
                    ),
                )

        return _TransactionKernel()


class _NoopPublisher:
    async def publish_stream_event(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


@pytest.fixture(autouse=True)
def _route_transaction_factory_to_test_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(kernel: Any, role: str, profile: Any, request: Any) -> Any:
        return kernel.build_transaction_kernel(role, profile, request)

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.kernel.transaction_turn_executor.create_transaction_kernel",
        _factory,
    )


@pytest.mark.asyncio
async def test_role_execution_dropped_tool_dispatch_commits_ledger_and_blocks_qa(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write src/index.js"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate src/index.js",
        task_id="TASK-1",
        run_id="run-dropped",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )

    result = await TransactionTurnExecutor(_DroppedToolDispatchKernel(str(tmp_path))).execute_turn(  # type: ignore[arg-type]
        "director",
        profile,  # type: ignore[arg-type]
        request,
        "system prompt",
        SimpleNamespace(full_hash="fingerprint"),
        "observer-run",
        None,
    )

    assert result.is_complete is False
    assert result.metadata["tool_dispatch_dropped"] is True

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-dropped")
    ).projection

    assert projection["ok"] is False
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["events"][0]["provider_response_hash"] == "provider-response-hash"
    assert projection["task_boundary"]["latest"]["failure_class"] == "TOOL_DISPATCH_DROPPED"


@pytest.mark.asyncio
async def test_stream_role_execution_dropped_tool_dispatch_commits_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write src/index.js"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate src/index.js",
        task_id="TASK-1",
        run_id="run-stream-dropped",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )

    with pytest.raises(RuntimeError, match="tool_dispatch_dropped"):
        async for _event in TransactionTurnExecutor(_DroppedToolDispatchStreamKernel(str(tmp_path))).execute_stream(  # type: ignore[arg-type]
            "director",
            profile,  # type: ignore[arg-type]
            request,
            "system prompt",
            SimpleNamespace(full_hash="fingerprint"),
            "observer-run",
            _NoopPublisher(),  # type: ignore[arg-type]
        ):
            pass

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-stream-dropped")
    ).projection

    assert projection["ok"] is False
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["events"][0]["provider_response_hash"] == "stream-provider-response-hash"
    assert projection["task_boundary"]["latest"]["failure_class"] == "TOOL_DISPATCH_DROPPED"


@pytest.mark.asyncio
async def test_role_execution_successful_turn_with_missing_target_commits_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write src/index.js"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate src/index.js",
        task_id="TASK-1",
        run_id="run-missing-target",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )

    result = await TransactionTurnExecutor(_SuccessfulNoMaterializationKernel(str(tmp_path))).execute_turn(  # type: ignore[arg-type]
        "director",
        profile,  # type: ignore[arg-type]
        request,
        "system prompt",
        SimpleNamespace(full_hash="fingerprint"),
        "observer-run",
        None,
    )

    assert result.is_complete is False
    assert result.error is not None
    assert result.error.startswith("task_boundary_failed:incomplete_materialization")

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-missing-target")
    ).projection

    assert projection["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert projection["task_boundary"]["latest"]["missing_target_files"] == ["src/index.js"]


@pytest.mark.asyncio
async def test_role_execution_successful_turn_with_missing_entrypoint_commits_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write package.json"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"node src/index.js"}}',
        encoding="utf-8",
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate package.json",
        task_id="TASK-1",
        run_id="run-missing-entrypoint",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["package.json"],
        },
    )

    result = await TransactionTurnExecutor(_SuccessfulNoMaterializationKernel(str(tmp_path))).execute_turn(  # type: ignore[arg-type]
        "director",
        profile,  # type: ignore[arg-type]
        request,
        "system prompt",
        SimpleNamespace(full_hash="fingerprint"),
        "observer-run",
        None,
    )

    assert result.is_complete is False
    assert result.error is not None
    assert result.error.startswith("task_boundary_failed:missing_entrypoint_target")

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-missing-entrypoint")
    ).projection

    assert projection["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert projection["task_boundary"]["latest"]["missing_entrypoint_targets"] == ["src/index.js"]


@pytest.mark.asyncio
async def test_stream_role_execution_successful_turn_with_missing_target_commits_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write src/index.js"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate src/index.js",
        task_id="TASK-1",
        run_id="run-stream-missing-target",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )

    events = [
        event
        async for event in TransactionTurnExecutor(
            _SuccessfulStreamNoMaterializationKernel(str(tmp_path))
        ).execute_stream(  # type: ignore[arg-type]
            "director",
            profile,  # type: ignore[arg-type]
            request,
            "system prompt",
            SimpleNamespace(full_hash="fingerprint"),
            "observer-run",
            _NoopPublisher(),  # type: ignore[arg-type]
        )
    ]

    assert any(event.get("type") == "error" and event.get("error_type") == "task_boundary_failed" for event in events)
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-stream-missing-target")
    ).projection

    assert projection["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert projection["task_boundary"]["latest"]["missing_target_files"] == ["src/index.js"]


@pytest.mark.asyncio
async def test_stream_role_execution_successful_turn_with_missing_entrypoint_commits_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write package.json"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"node src/index.js"}}',
        encoding="utf-8",
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate package.json",
        task_id="TASK-1",
        run_id="run-stream-missing-entrypoint",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["package.json"],
        },
    )

    events = [
        event
        async for event in TransactionTurnExecutor(
            _SuccessfulStreamNoMaterializationKernel(str(tmp_path))
        ).execute_stream(  # type: ignore[arg-type]
            "director",
            profile,  # type: ignore[arg-type]
            request,
            "system prompt",
            SimpleNamespace(full_hash="fingerprint"),
            "observer-run",
            _NoopPublisher(),  # type: ignore[arg-type]
        )
    ]

    assert any(event.get("type") == "error" and event.get("error_type") == "task_boundary_failed" for event in events)
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-stream-missing-entrypoint")
    ).projection

    assert projection["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert projection["task_boundary"]["latest"]["missing_entrypoint_targets"] == ["src/index.js"]


@pytest.mark.asyncio
async def test_stream_forced_write_zero_dispatch_fails_with_dropped_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """Stream completion with a forced write and zero dispatch must surface tool_dispatch_dropped."""

    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "write src/index.js"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="[mode:materialize]\nCreate src/index.js",
        task_id="TASK-1",
        run_id="run-stream-forced-write-dropped",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )

    events = [
        event
        async for event in TransactionTurnExecutor(
            _ForcedWriteZeroDispatchStreamCompletionKernel(str(tmp_path))  # type: ignore[arg-type]
        ).execute_stream(
            "director",
            profile,  # type: ignore[arg-type]
            request,
            "system prompt",
            SimpleNamespace(full_hash="fingerprint"),
            "observer-run",
            _NoopPublisher(),  # type: ignore[arg-type]
        )
    ]

    assert any(event.get("type") == "error" and event.get("error_type") == "tool_dispatch_dropped" for event in events)
    assert all(event.get("type") != "complete" for event in events)

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-stream-forced-write-dropped")
    ).projection

    assert projection["ok"] is False
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["events"][0]["status"] == "dropped"
    assert projection["task_boundary"]["latest"]["failure_class"] == "TOOL_DISPATCH_DROPPED"


@pytest.mark.asyncio
async def test_stream_normal_completed_turn_unaffected_by_lifecycle_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A normal completed stream turn without forced writes still completes cleanly."""

    class _ContextGateway:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def build_context(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                messages=[{"role": "user", "content": "summarize the repository"}],
                token_estimate=128,
            )

        def record_projection_outcome(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.public.service.RoleContextGateway",
        _ContextGateway,
    )

    profile = SimpleNamespace(
        role_id="director",
        provider_id="test-provider",
        model="test-model",
        version="test-profile-v1",
        tool_policy=RoleToolPolicy(
            whitelist=["write_file"],
            allow_code_write=True,
        ),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.WORKFLOW,
        workspace=str(tmp_path),
        message="Summarize the repository",
        task_id="TASK-1",
        run_id="run-stream-normal-complete",
        context_override={},
    )

    events = [
        event
        async for event in TransactionTurnExecutor(_SuccessfulStreamCompletedTurnKernel(str(tmp_path))).execute_stream(  # type: ignore[arg-type]
            "director",
            profile,  # type: ignore[arg-type]
            request,
            "system prompt",
            SimpleNamespace(full_hash="fingerprint"),
            "observer-run",
            _NoopPublisher(),  # type: ignore[arg-type]
        )
    ]

    assert not any(event.get("type") == "error" for event in events)
    assert events[-1]["type"] == "complete"
    assert events[-1]["result"].error is None
    assert events[-1]["result"].is_complete is True
