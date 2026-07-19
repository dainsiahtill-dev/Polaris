from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.kernelone.events.sourcing.segmented_file_store import SegmentedJsonlEventStore
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1


def _bootstrap(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="final_provider_attempt_lifecycle_test",
        )
    )


def _attempt(
    *,
    verification_scope: str = "factory",
    scope_id: str = "factory-run-1",
) -> FrozenFinalProviderAttemptV1:
    return FrozenFinalProviderAttemptV1(
        provider_request_id="provider-request-1",
        request_freeze_id="freeze-1",
        factory_run_id=scope_id if verification_scope == "factory" else "",
        scope_id=scope_id,
        run_id="run-1",
        turn_id="turn-1",
        call_id="call-1",
        role="director",
        provider="openai",
        model="model-1",
        attempt_number=1,
        verification_scope=verification_scope,
        semantic_request_hash="a" * 64,
        physical_wire_hash="b" * 64,
        composite_request_hash="c" * 64,
        dispatch_view={},
        durable_view={},
    )


def test_terminal_without_authoritative_start_fails_closed(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    with pytest.raises(RuntimeError, match="start is missing or ambiguous"):
        lifecycle.append_terminal(
            _attempt(),
            context_snapshot_ref="d" * 24,
            pin_hash="e" * 64,
            status="failed",
        )
    assert lifecycle.query_strict() == ()


def test_duplicate_terminal_is_idempotent_only_for_the_same_terminal_fact(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    lifecycle.append_start(attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    lifecycle.append_terminal(
        attempt,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    lifecycle.append_terminal(
        attempt,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]

    with pytest.raises((RuntimeError, ValueError)):
        lifecycle.append_terminal(
            attempt,
            context_snapshot_ref="d" * 24,
            pin_hash="e" * 64,
            status="failed",
            error="semantic conflict",
        )


def test_terminal_healthy_path_recovers_start_by_locator_without_full_ledger_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()

    def _scan_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("healthy lifecycle append must not run strict head/full scan")

    monkeypatch.setattr(lifecycle, "query_strict", _scan_forbidden)
    monkeypatch.setattr(SegmentedJsonlEventStore, "head", _scan_forbidden)
    monkeypatch.setattr(SegmentedJsonlEventStore, "_full_scan_and_rebuild_locked", _scan_forbidden)
    lifecycle.append_start(attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    lifecycle.append_terminal(
        attempt,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    monkeypatch.undo()
    replay = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    ).query_strict()
    assert [item["event_type"] for item in replay] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]


def test_role_session_ledger_is_separate_and_cannot_satisfy_factory_scope(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    factory = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    role_session = StrictProviderAttemptLifecycleStore.for_role_session(
        workspace=str(tmp_path),
        role_session_id="role-session-1",
    )
    factory_attempt = _attempt()
    role_attempt = _attempt(verification_scope="role_session", scope_id="role-session-1")

    with pytest.raises(RuntimeError, match="scope"):
        role_session.append_start(factory_attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    with pytest.raises(RuntimeError, match="scope"):
        factory.append_start(role_attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    role_session.append_start(role_attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    role_session.append_terminal(
        role_attempt,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    assert factory.query_strict() == ()
    assert [item["event_type"] for item in role_session.query_strict()] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]


def test_lifecycle_rejects_malformed_refs_pin_hash_and_terminal_status_before_append(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()

    with pytest.raises(ValueError, match="context_snapshot_ref"):
        lifecycle.append_start(attempt, context_snapshot_ref="D" * 24, pin_hash="e" * 64)
    with pytest.raises(ValueError, match="pin_hash"):
        lifecycle.append_start(attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 63)
    assert lifecycle.query_strict() == ()

    lifecycle.append_start(attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    with pytest.raises(ValueError, match="terminal status"):
        lifecycle.append_terminal(
            attempt,
            context_snapshot_ref="d" * 24,
            pin_hash="e" * 64,
            status="success",
        )
    assert [item["event_type"] for item in lifecycle.query_strict()] == ["provider_attempt.started"]
