"""POL-62: the HTTP and causal audit share one canonical scoped ledger."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    RunLedger,
    append_run_ledger_event,
    read_run_ledger_projection,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    query_fact_events,
)
from polaris.cells.factory.pipeline.public import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.runtime.task_runtime.public.contracts import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.http.routers._factory_handlers.stage_ops import run_summary
from polaris.kernelone import storage
from polaris.kernelone.events.sourcing import file_store, segmented_file_store
from polaris.kernelone.fs import locked_regular_file
from polaris.kernelone.storage import layout


@pytest.fixture
def isolated_fact_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep real persistence/claim/reader; suppress only outbound notifications."""
    home = tmp_path / "diagnostic-authority"
    locks = str(home / "lock_authorities/v1")

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("POL75 offline regression forbids network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(locked_regular_file, "kernelone_home", lambda: str(home))
    for module in (locked_regular_file, segmented_file_store):
        monkeypatch.setattr(module, "default_platform_lock_root", lambda: locks)
    for module in (file_store, storage, layout):
        monkeypatch.setattr(module, "read_only_platform_lock_roots", lambda: (locks,))
    monkeypatch.setattr(TaskRuntimeService, "_publish_factory_execution_event", lambda *a, **k: False)
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    layout.clear_storage_roots_cache()
    layout._writable_base_cache.clear()
    yield
    layout.clear_storage_roots_cache()
    layout._writable_base_cache.clear()


@pytest.fixture(autouse=True)
def fact_streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_fact_authority: None) -> None:
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="pol62_scope_regression",
        )
    )


def _append_task_runtime_execution_fact(
    workspace: Path, *, run_id: str, factory_run_id: str, project_id: str, task_id: str
) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            run_id=run_id,
            task_id=task_id,
            payload={
                "run_id": run_id,
                "factory_run_id": factory_run_id,
                "factory_bench_project_id": project_id,
                "task_id": task_id,
            },
            strict_integrity=True,
        )
    )


def run() -> FactoryRun:
    return FactoryRun(
        id="factory-own",
        config=FactoryConfig(name="scope"),
        status=FactoryRunStatus.FAILED,
        created_at="2026-10-09T00:00:00Z",
        metadata={"factory_start_request": {"metadata": {"canonical_project_id": "P1"}}},
    )


def gate(workspace: Path, run_id: str, name: str) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            event={
                "event_type": "gate_evaluated",
                "gate": {"name": name, "ok": True},
                "job_token": {"run_id": run_id, "project_id": "P1"},
            },
        )
    )


def test_http_projection_joins_unique_children_and_excludes_foreign_scopes(tmp_path: Path) -> None:
    gate(tmp_path, "factory-own", "parent")
    for child, factory, project in (
        ("child-1", "factory-own", "P1"),
        ("child-1", "factory-own", "P1"),
        ("child-2", "factory-own", "P1"),
        ("foreign-factory", "factory-other", "P1"),
        ("foreign-project", "factory-own", "P2"),
    ):
        _append_task_runtime_execution_fact(
            tmp_path, run_id=child, factory_run_id=factory, project_id=project, task_id=child
        )
    for child in ("child-1", "child-2", "foreign-factory", "foreign-project"):
        gate(tmp_path, child, child)
    foreign_workspace = tmp_path / "other-workspace"
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(foreign_workspace),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="pol62_foreign_workspace",
        )
    )
    gate(foreign_workspace, "child-1", "foreign-workspace")

    bundle: dict = {}
    projection = run_summary._attach_control_plane_projection(bundle=bundle, run=run(), workspace=str(tmp_path))

    assert projection is not None, bundle.get("control_plane_projection_error")
    assert projection is bundle["run_ledger_projection"] is bundle["control_plane_projection"]
    assert projection["query_scope"] == {"run_id": "factory-own", "factory_run_id": "factory-own", "project_id": "P1"}
    assert projection["consumed_run_ids"] == ["factory-own", "child-1", "child-2"]
    assert projection["run_projection"]["gate_count"] == 3
    assert "foreign" not in json.dumps(projection)


@pytest.mark.parametrize("case", ["missing_identity", "no_facts", "compat_only", "corrupt"])
def test_http_projection_rejects_missing_identity_facts_and_corruption(tmp_path: Path, case: str) -> None:
    current = run()
    if case == "missing_identity":
        current.metadata.clear()
        gate(tmp_path, current.id, "must-not-be-used")
    elif case == "compat_only":
        ledger = RunLedger(tmp_path, run_id=current.id)
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        ledger.path.write_text(
            json.dumps({"event_type": "gate_evaluated", "gate": {"ok": True}}) + "\n", encoding="utf-8"
        )
    elif case == "corrupt":
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream="execution.control_plane",
                run_id=current.id,
                event_type="legacy.recorded",
                source="legacy_writer",
                payload={"event": {"run_id": current.id}},
            )
        )
    bundle: dict = {}

    projection = run_summary._attach_control_plane_projection(bundle=bundle, run=current, workspace=str(tmp_path))

    assert projection is None
    assert "run_ledger_projection" not in bundle
    errors = bundle["control_plane_projection_error"]["errors"]
    assert any(item["code"] == "RUN_LEDGER_PROJECTION_UNAVAILABLE" for item in errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger_available", [True, False])
async def test_causal_audit_consumes_same_projection_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ledger_available: bool
) -> None:
    from polaris.delivery.http.routers import factory

    current = run()
    if ledger_available:
        gate(tmp_path, current.id, "parent")
        _append_task_runtime_execution_fact(
            tmp_path, run_id="child", factory_run_id=current.id, project_id="P1", task_id="TASK-1"
        )
        gate(tmp_path, "child", "child")
    service = SimpleNamespace(get_run=AsyncMock(return_value=current), get_run_events=AsyncMock(return_value=[]))
    monkeypatch.setattr(factory, "_resolve_workspace", lambda *_: str(tmp_path))
    monkeypatch.setattr(factory, "_get_service", lambda *_: service)
    monkeypatch.setattr(factory, "_list_run_artifacts", lambda **_: [])
    causal = AsyncMock(return_value=SimpleNamespace(status="ok", payload={}, error_code=None, error_message=None))
    monkeypatch.setattr(factory, "query_exact_run_causal_audit", causal)

    result = await factory._get_factory_run_audit_bundle_core(current.id, 100, SimpleNamespace())

    if ledger_available:
        causal.assert_awaited_once()
        query = causal.await_args.args[0]
        assert query.project_id == "P1"
        assert query.preloaded_run_ledger_projection == result.run_ledger_projection
        assert query.preloaded_run_ledger_projection["query_scope"]["factory_run_id"] == current.id
    else:
        causal.assert_not_awaited()
        assert result.exact_run_causal_audit_error["error_code"] == "scoped_run_ledger_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("planning", [True, False])
async def test_real_frozen_routes_require_pm_planning_at_http_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planning: bool
) -> None:
    from polaris.cells.roles.kernel.tests.test_pol62_binding_qualification import _routes
    from polaris.delivery.http.routers import factory

    query, _runs = _routes(tmp_path)
    current = run()
    current.id = query.factory_run_id
    current.metadata["stage_results"] = {
        "pm_planning": {
            "metadata": {
                "contract_origin": "provider_planning" if planning else "role_probe",
                "real_planning_qualified": planning,
            }
        }
    }
    service = SimpleNamespace(get_run=AsyncMock(return_value=current), get_run_events=AsyncMock(return_value=[]))
    monkeypatch.setattr(factory, "_resolve_workspace", lambda *_: str(tmp_path))
    monkeypatch.setattr(factory, "_get_service", lambda *_: service)
    monkeypatch.setattr(factory, "_list_run_artifacts", lambda **_: [])
    monkeypatch.setattr(
        factory,
        "query_exact_run_causal_audit",
        AsyncMock(return_value=SimpleNamespace(status="ok", payload={}, error_code=None, error_message=None)),
    )
    result = await factory._get_factory_run_audit_bundle_core(current.id, 100, SimpleNamespace())
    assert result.role_route_qualification["ok"] is planning
    assert result.role_route_qualification["terminal_events_observed"] == 5
    if not planning:
        assert "pm_provider_planning_evidence_missing" in result.role_route_qualification["errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pre_qa_failure", "qa_started", "completed"])
async def test_http_route_obligations_use_server_reached_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from polaris.cells.roles.kernel.tests.test_pol62_binding_qualification import _routes
    from polaris.delivery.http.routers import factory

    query, _ = _routes(tmp_path, "unreached_qa")
    current = run()
    current.id = query.factory_run_id
    current.stages_completed = ["pm_planning", "chief_engineer_review"]
    current.stages_failed = ["director_dispatch"]
    current.metadata["current_stage"] = "quality_gate" if phase == "qa_started" else "director_dispatch"
    if phase == "completed":
        current.status = FactoryRunStatus.COMPLETED
    current.metadata["stage_results"] = {
        "pm_planning": {"metadata": {"contract_origin": "provider_planning", "real_planning_qualified": True}}
    }
    service = SimpleNamespace(get_run=AsyncMock(return_value=current), get_run_events=AsyncMock(return_value=[]))
    monkeypatch.setattr(factory, "_resolve_workspace", lambda *_: str(tmp_path))
    monkeypatch.setattr(factory, "_get_service", lambda *_: service)
    monkeypatch.setattr(factory, "_list_run_artifacts", lambda **_: [])
    monkeypatch.setattr(
        factory,
        "query_exact_run_causal_audit",
        AsyncMock(return_value=SimpleNamespace(status="ok", payload={}, error_code=None, error_message=None)),
    )
    result = await factory._get_factory_run_audit_bundle_core(current.id, 100, SimpleNamespace())
    route = result.role_route_qualification
    assert route["ok"] is (phase == "pre_qa_failure")
    assert route["roles"]["qa"]["status"] == ("not_reached" if phase == "pre_qa_failure" else "missing")
    assert route["roles"]["qa"]["ok"] is False
    assert current.status == (FactoryRunStatus.COMPLETED if phase == "completed" else FactoryRunStatus.FAILED)


def _canonical_query(workspace: Path) -> ReadRunLedgerProjectionQueryV1:
    return ReadRunLedgerProjectionQueryV1(
        workspace=str(workspace),
        run_id="factory-own",
        factory_run_id="factory-own",
        project_id="P1",
        canonical_only=True,
    )


def _runtime_facts(workspace: Path) -> list[dict[str, Any]]:
    return list(
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="task_runtime.execution",
                strict_integrity=True,
                limit=1000,
            )
        ).events
    )


def _real_bound_task(workspace: Path, *, initially_bound: bool = True) -> tuple[TaskRuntimeService, dict[str, Any]]:
    runtime = TaskRuntimeService(str(workspace))
    metadata = {"factory_bench_project_id": "P1"}
    if initially_bound:
        metadata["factory_run_id"] = "factory-own"
    row = runtime.ensure_task_row(external_task_id="TASK-1", subject="POL75 diagnostic", metadata=metadata)
    bound = runtime.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id=str(row["id"]),
            factory_run_id="factory-own",
        )
    )
    assert bound.ok is True
    return runtime, row


def _real_claim(runtime: TaskRuntimeService, row: dict[str, Any]) -> dict[str, Any]:
    claim: dict[str, Any] = runtime.claim_execution(
        row["id"],
        worker_id="offline-director",
        role_id="director",
        run_id="child-real",
        metadata={"factory_bench_project_id": "P1"},
    )
    assert claim["success"] is True
    return claim


def _diagnostic_control_plane(workspace: Path) -> None:
    # Synthetic CP fixtures exercise the real reader, never native qualification.
    gate(workspace, "factory-own", "synthetic-parent")
    gate(workspace, "child-real", "synthetic-child")


@pytest.mark.parametrize("initially_bound", [True, False], ids=["materialized", "first-factory-binding"])
def test_pol75_actual_declarations_do_not_poison_real_claim(tmp_path: Path, initially_bound: bool) -> None:
    runtime, row = _real_bound_task(tmp_path, initially_bound=initially_bound)
    declarations = _runtime_facts(tmp_path)
    assert [fact["event_type"] for fact in declarations] == ["created", "materialized", "factory_run_bound"]
    assert all(fact["payload"]["session_id"] == fact["payload"]["run_id"] == "" for fact in declarations)
    _real_claim(runtime, row)
    _diagnostic_control_plane(tmp_path)
    before = _runtime_facts(tmp_path)
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    original_bytes = path.read_bytes()

    projection = read_run_ledger_projection(_canonical_query(tmp_path)).projection

    assert projection["available"] is True
    assert projection["consumed_run_ids"] == ["factory-own", "child-real"]
    assert projection["run_projection"]["gate_count"] == 2
    assert projection["canonical_evidence"]["captured_heads"]["task_runtime.execution"] == len(before)
    assert _runtime_facts(tmp_path) == before
    assert path.read_bytes() == original_bytes
    bundle: dict[str, Any] = {}
    assert run_summary._attach_control_plane_projection(bundle=bundle, run=run(), workspace=str(tmp_path)) is not None


@pytest.mark.parametrize("lifecycle", ["bound", "updated", "reset", "historical-update-reset"])
def test_pol75_declarations_alone_never_qualify_child(tmp_path: Path, lifecycle: str) -> None:
    runtime, row = _real_bound_task(tmp_path)
    if lifecycle in {"updated", "historical-update-reset"}:
        metadata: dict[str, Any] = {"ordinary_note": "diagnostic"}
        if lifecycle == "historical-update-reset":
            metadata.update(
                workflow_run_id="unclaimed-history",
                runtime_execution={
                    "run_id": "unclaimed-history",
                    "session_id": "historical-session",
                    "attempt": 2,
                    "effective_status": "pending",
                },
            )
        assert runtime.update_task_row(row["id"], metadata=metadata) is not None
    if lifecycle in {"reset", "historical-update-reset"}:
        reset = runtime.reset_records(factory_run_id="factory-own")
        assert reset["ok"] is True and reset["tombstone_count"] == 1
    before = _runtime_facts(tmp_path)
    _diagnostic_control_plane(tmp_path)
    with pytest.raises(ValueError, match="canonical_factory_child_lineage_missing"):
        read_run_ledger_projection(_canonical_query(tmp_path))
    assert _runtime_facts(tmp_path) == before


@pytest.mark.parametrize("after_claim", [False, True], ids=["pre-session", "historical-runtime"])
def test_pol75_real_ordinary_update_and_governed_reset_preserve_lineage(tmp_path: Path, after_claim: bool) -> None:
    runtime, row = _real_bound_task(tmp_path)
    if not after_claim:
        assert runtime.update_task_row(row["id"], metadata={"ordinary_note": "before"}) is not None
    claim = _real_claim(runtime, row)
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    settled = runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(tmp_path),
            identity=identity,
            outcome="suspended",
            summary="offline fixture",
        )
    )
    assert settled["success"] is True
    if after_claim:
        assert runtime.update_task_row(row["id"], metadata={"ordinary_note": "after"}) is not None
    reset = runtime.reset_records(factory_run_id="factory-own")
    assert reset["ok"] is True and reset["tombstone_count"] == 1
    facts = _runtime_facts(tmp_path)
    update = next(f["payload"] for f in facts if f["event_type"] == "updated")
    tombstone = facts[-1]["payload"]
    assert facts[-1]["event_type"] == "runtime_reset_removed"
    assert update["session_id"] == tombstone["session_id"] == ""
    assert update["run_id"] == ("child-real" if after_claim else "")
    assert tombstone["run_id"] == "child-real"
    assert tombstone["task_row_snapshot"]["metadata"]["runtime_execution"]["session_id"] == identity.session_id
    _diagnostic_control_plane(tmp_path)

    projection = read_run_ledger_projection(_canonical_query(tmp_path)).projection

    assert projection["consumed_run_ids"] == ["factory-own", "child-real"]
    assert _runtime_facts(tmp_path) == facts


@pytest.mark.parametrize("status", ["pending", "blocked", "ready", "PENDING", " READY "])
def test_pol75_real_non_execution_status_update_is_a_declaration(tmp_path: Path, status: str) -> None:
    runtime, row = _real_bound_task(tmp_path)
    assert runtime.update_task_row(row["id"], status=status) is not None
    _diagnostic_control_plane(tmp_path)
    with pytest.raises(ValueError, match="canonical_factory_child_lineage_missing"):
        read_run_ledger_projection(_canonical_query(tmp_path))
    if status == "blocked":
        assert runtime.update_task_row(row["id"], status="pending") is not None
    _real_claim(runtime, row)
    before = _runtime_facts(tmp_path)
    projection = read_run_ledger_projection(_canonical_query(tmp_path)).projection
    assert projection["consumed_run_ids"] == ["factory-own", "child-real"]
    assert _runtime_facts(tmp_path) == before


def _append_diagnostic_fact(workspace: Path, payload: dict[str, Any], **overrides: Any) -> None:
    # Deliberately malformed, digest-valid diagnostic facts test reader rejection.
    arguments = {
        "workspace": str(workspace),
        "stream": "task_runtime.execution",
        "event_type": payload["event_type"],
        "source": "runtime.task_runtime",
        "run_id": payload.get("run_id") or None,
        "task_id": payload.get("task_id") or None,
        "payload": payload,
        "strict_integrity": True,
    }
    arguments.update(overrides)
    append_fact_event(AppendFactEventCommandV1(**arguments))


@pytest.mark.parametrize("kind", ["created", "materialized", "factory_run_bound", "updated", "runtime_reset_removed"])
@pytest.mark.parametrize("invalid_transition", ["missing", "empty", "whitespace", "integer", "null", "list", "boolean"])
def test_pol75_native_declaration_requires_payload_transition_identity(
    tmp_path: Path, kind: str, invalid_transition: str
) -> None:
    runtime, row = _real_bound_task(tmp_path)
    if kind == "updated":
        assert runtime.update_task_row(row["id"], metadata={"note": "diagnostic"}) is not None
    claim = _real_claim(runtime, row)
    if kind == "runtime_reset_removed":
        identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
        assert (
            runtime.settle_execution_attempt(
                SettleTaskRuntimeExecutionAttemptCommandV1(
                    workspace=str(tmp_path), identity=identity, outcome="suspended", summary="diagnostic"
                )
            )["success"]
            is True
        )
        assert runtime.reset_records(factory_run_id="factory-own")["ok"] is True
    _diagnostic_control_plane(tmp_path)
    facts = _runtime_facts(tmp_path)
    declaration = next(fact for fact in facts if fact["event_type"] == kind)
    payload = deepcopy(declaration["payload"])
    assert isinstance(payload["transition_id"], str) and payload["transition_id"].strip()
    assert declaration["event_id"] != payload["transition_id"]
    projection = read_run_ledger_projection(_canonical_query(tmp_path)).projection
    assert projection["query_scope"] == {"run_id": "factory-own", "factory_run_id": "factory-own", "project_id": "P1"}
    assert projection["consumed_run_ids"] == ["factory-own", "child-real"]
    assert projection["canonical_evidence"]["captured_heads"]["task_runtime.execution"] == len(facts)
    if invalid_transition == "missing":
        payload.pop("transition_id")
    else:
        payload["transition_id"] = {
            "empty": "",
            "whitespace": " \t",
            "integer": 42,
            "null": None,
            "list": [],
            "boolean": True,
        }[invalid_transition]
    _append_diagnostic_fact(tmp_path, payload)
    before = _runtime_facts(tmp_path)
    event_root = tmp_path / ".polaris/runtime/events"
    original_bytes = {str(path): path.read_bytes() for path in event_root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="canonical_lineage_declaration_transition_invalid"):
        read_run_ledger_projection(_canonical_query(tmp_path))
    assert _runtime_facts(tmp_path) == before
    assert {str(path): path.read_bytes() for path in event_root.rglob("*") if path.is_file()} == original_bytes


@pytest.mark.parametrize(
    "case",
    [
        "session-without-run",
        "declaration-session",
        "envelope-run",
        "envelope-task",
        "missing-task",
        "snapshot-task",
        "snapshot-factory",
        "snapshot-project",
        "project-alias",
        "snapshot-run",
        "foreign-workspace",
        "foreign-source",
        "event-kind",
        "missing-snapshot",
        "missing-session",
        "missing-run",
        "bad-run-type",
        "bad-session-type",
        "terminal-update",
        "executing-update",
        "bad-update-status-type",
        "ungoverned-reset",
        "reset-factory",
        "unknown-missing-run",
        "claimed-missing-run",
        "terminal-missing-run",
    ],
)
def test_pol75_malformed_declarations_and_execution_facts_fail_closed(tmp_path: Path, case: str) -> None:
    runtime, row = _real_bound_task(tmp_path)
    payload = deepcopy(_runtime_facts(tmp_path)[-1]["payload"])
    _real_claim(runtime, row)
    _diagnostic_control_plane(tmp_path)
    overrides: dict[str, Any] = {}
    if case == "session-without-run":
        payload["session_id"] = "invalid-session"
    elif case == "declaration-session":
        payload.update(run_id="child-real", session_id="invalid-session")
    elif case == "envelope-run":
        overrides["run_id"] = "foreign-run"
    elif case == "envelope-task":
        overrides["task_id"] = "foreign-task"
    elif case == "missing-task":
        payload["task_id"] = ""
    elif case == "snapshot-task":
        payload["task_row_snapshot"]["id"] = 999
    elif case in {"snapshot-factory", "snapshot-project"}:
        key = "factory_run_id" if case == "snapshot-factory" else "factory_bench_project_id"
        payload["task_row_snapshot"]["metadata"][key] = "foreign"
    elif case == "project-alias":
        payload["project_id"] = "P1"
        payload["factory_bench_project_id"] = "foreign"
    elif case == "snapshot-run":
        payload["run_id"] = "child-real"
    elif case == "foreign-workspace":
        payload["workspace"] = str(tmp_path / "foreign")
    elif case == "foreign-source":
        overrides["source"] = "foreign-writer"
    elif case == "event-kind":
        overrides["event_type"] = "created"
    elif case.startswith("missing-"):
        payload.pop(
            {"missing-snapshot": "task_row_snapshot", "missing-session": "session_id", "missing-run": "run_id"}[case]
        )
    elif case in {"bad-run-type", "bad-session-type"}:
        payload["run_id" if case == "bad-run-type" else "session_id"] = []
    elif case in {"terminal-update", "executing-update"}:
        payload["event_type"] = "updated"
        payload["details"] = {"status": "completed" if case == "terminal-update" else "in_progress"}
    elif case == "bad-update-status-type":
        payload["event_type"] = "updated"
        payload["details"] = {"status": []}
    elif case in {"ungoverned-reset", "reset-factory"}:
        payload.update(event_type="runtime_reset_removed", status="removed", execution_state="removed")
        payload["details"] = {} if case == "ungoverned-reset" else {"reset_factory_run_id": "foreign"}
    else:
        payload["event_type"] = {
            "unknown-missing-run": "unknown-kind",
            "claimed-missing-run": "claimed",
            "terminal-missing-run": "completed",
        }[case]
    _append_diagnostic_fact(tmp_path, payload, **overrides)
    before = _runtime_facts(tmp_path)

    with pytest.raises(ValueError, match="canonical_lineage_"):
        read_run_ledger_projection(_canonical_query(tmp_path))
    assert _runtime_facts(tmp_path) == before


@pytest.mark.parametrize("corruption", ["missing-digest", "corrupt-digest"])
def test_pol75_declaration_integrity_is_checked_before_exclusion(tmp_path: Path, corruption: str) -> None:
    runtime, row = _real_bound_task(tmp_path)
    _real_claim(runtime, row)
    _diagnostic_control_plane(tmp_path)
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    if corruption == "missing-digest":
        first.pop("integrity_digest")
    else:
        first["payload"]["subject"] = "tampered"
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(RuntimeError):
        read_run_ledger_projection(_canonical_query(tmp_path))
    assert path.read_bytes() == original


@pytest.mark.parametrize("foreign", ["factory", "project", "workspace"])
def test_pol75_out_of_scope_declarations_preserve_isolation(tmp_path: Path, foreign: str) -> None:
    runtime, row = _real_bound_task(tmp_path)
    payload = deepcopy(_runtime_facts(tmp_path)[-1]["payload"])
    _real_claim(runtime, row)
    _diagnostic_control_plane(tmp_path)
    if foreign == "workspace":
        other = tmp_path / "other-workspace"
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(other),
                streams=("task_runtime.execution",),
                maintenance_reason="offline foreign fixture",
            )
        )
        _append_diagnostic_fact(other, payload)
    else:
        payload["factory_run_id" if foreign == "factory" else "factory_bench_project_id"] = "foreign"
        _append_diagnostic_fact(tmp_path, payload, source="foreign-writer", task_id="foreign-task")
    projection = read_run_ledger_projection(_canonical_query(tmp_path)).projection
    assert projection["consumed_run_ids"] == ["factory-own", "child-real"]


def test_pol75_conflicting_execution_ancestry_remains_rejected(tmp_path: Path) -> None:
    runtime, row = _real_bound_task(tmp_path)
    _real_claim(runtime, row)
    payload = deepcopy(_runtime_facts(tmp_path)[-1]["payload"])
    payload["factory_run_id"] = "foreign-factory"
    _append_diagnostic_fact(tmp_path, payload)
    _diagnostic_control_plane(tmp_path)
    with pytest.raises(ValueError, match="canonical_factory_child_lineage_conflict"):
        read_run_ledger_projection(_canonical_query(tmp_path))


@pytest.mark.parametrize(
    "case",
    [
        "historical-session",
        "historical-run",
        "historical-task",
        "metadata-workflow",
        "envelope-correlation",
        "metadata-workspace",
        "snapshot-workspace",
        "snapshot-status",
        "claimed-session-missing",
        "claimed-session-empty",
        "claimed-correlation",
        "claimed-schema-unknown",
        "claimed-schema-missing",
        "claimed-task-missing",
        "terminal-session-empty",
    ],
)
def test_pol75_supplied_historical_and_execution_identities_cannot_contradict(tmp_path: Path, case: str) -> None:
    runtime, row = _real_bound_task(tmp_path)
    claim = _real_claim(runtime, row)
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    assert (
        runtime.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path),
                identity=identity,
                outcome="suspended",
                summary="diagnostic history",
            )
        )["success"]
        is True
    )
    assert runtime.update_task_row(row["id"], metadata={"note": "history"}) is not None
    facts = _runtime_facts(tmp_path)
    execution_case = case.startswith("claimed-") or case == "terminal-session-empty"
    kind = "suspended" if case == "terminal-session-empty" else ("claimed" if execution_case else "updated")
    payload = deepcopy(next(f["payload"] for f in facts if f["event_type"] == kind))
    overrides: dict[str, Any] = {"correlation_id": payload["session_id"] or None}
    snapshot = payload["task_row_snapshot"]
    if case == "historical-session":
        snapshot["metadata"]["runtime_execution"]["session_id"] = "foreign-session"
    elif case == "historical-run":
        snapshot["metadata"]["runtime_execution"]["run_id"] = "foreign-run"
    elif case == "historical-task":
        snapshot["metadata"]["runtime_execution"]["task_id"] = 999
    elif case == "metadata-workflow":
        snapshot["metadata"]["workflow_run_id"] = "foreign-run"
    elif case in {"envelope-correlation", "claimed-correlation"}:
        overrides["correlation_id"] = "foreign-session"
    elif case == "metadata-workspace":
        snapshot["metadata"]["workspace"] = str(tmp_path / "foreign")
    elif case == "snapshot-workspace":
        snapshot["workspace"] = str(tmp_path / "foreign")
    elif case == "snapshot-status":
        snapshot["status"] = "completed"
    elif case == "claimed-session-missing":
        payload.pop("session_id")
    elif case == "claimed-schema-unknown":
        payload["schema_version"] = "unknown-native-schema"
        payload["session_id"] = ""
        overrides["correlation_id"] = None
    elif case == "claimed-schema-missing":
        payload.pop("schema_version")
        payload["session_id"] = ""
        overrides["correlation_id"] = None
    elif case == "claimed-task-missing":
        payload.pop("task_id")
    else:
        payload["session_id"] = ""
        overrides["correlation_id"] = None
    _append_diagnostic_fact(tmp_path, payload, **overrides)
    _diagnostic_control_plane(tmp_path)
    with pytest.raises(ValueError, match="canonical_lineage_"):
        read_run_ledger_projection(_canonical_query(tmp_path))
