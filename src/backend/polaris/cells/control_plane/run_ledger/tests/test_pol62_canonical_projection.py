"""Qualification consumes verified lineage and CP, never compatibility rows."""

import json
from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    append_run_ledger_event,
    read_run_ledger_projection,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
)
from polaris.delivery.http.routers._factory_handlers.stage_ops import run_summary
from polaris.delivery.http.tests.test_pol62_factory_audit_scope import run


@pytest.fixture(autouse=True)
def streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="pol62_canonical",
        )
    )


def gate(root: Path, run_id: str, project: str = "P1") -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(root),
            run_id=run_id,
            event={
                "event_type": "gate_evaluated",
                "gate": {"ok": True},
                "job_token": {"run_id": run_id, "project_id": project},
            },
        )
    )


def lineage(root: Path, factory: str = "factory-own", project: str = "P1", task: str = "TASK-1") -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(root),
            stream="task_runtime.execution",
            event_type="created",
            source="runtime.task_runtime",
            run_id="child",
            task_id=task,
            strict_integrity=True,
            payload={
                "run_id": "child",
                "task_id": task,
                "factory_run_id": factory,
                "factory_bench_project_id": project,
            },
        )
    )


@pytest.mark.parametrize(
    "case",
    [
        "native",
        "catalog",
        "multi_task",
        "foreign_task",
        "foreign_project",
        "wrong_event",
        "foreign_workspace",
        "foreign_token_task",
        "foreign_token_run",
        "inner_foreign_task",
        "inner_foreign_run",
        "inner_other_valid_task",
        "inner_missing_identity",
    ],
)
def test_native_task_boundary_scope_is_bound_to_verified_task_lineage(tmp_path: Path, case: str) -> None:
    gate(tmp_path, "factory-own")
    lineage(tmp_path)
    if case in {"multi_task", "inner_other_valid_task"}:
        lineage(tmp_path, task="TASK-2")
    task = "TASK-foreign" if case == "foreign_task" else "TASK-1"
    event = {
        "event_type": "gate_evaluated" if case == "wrong_event" else "task_boundary_verdict",
        "run_id": "child",
        "task_id": task,
        "job_token": {
            "run_id": "child",
            "task_id": task,
            "project_id": "P1" if case == "catalog" else ("P2" if case == "foreign_project" else task),
        },
        "task_boundary_verdict": {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "task_id": "TASK-foreign"
            if case == "inner_foreign_task"
            else ("TASK-2" if case == "inner_other_valid_task" else task),
            "run_id": "foreign-run" if case == "inner_foreign_run" else "child",
            "status": "completed_verified",
            "ok": True,
            "failure_class": "PASSED",
        },
    }
    if case == "inner_missing_identity":
        event["task_boundary_verdict"].pop("task_id")
        event["task_boundary_verdict"].pop("run_id")
    if case == "foreign_workspace":
        event["workspace"] = str(tmp_path / "foreign")
    if case in {"foreign_token_task", "foreign_token_run"}:
        event["job_token"]["project_id"] = "P1"
        event["job_token"]["task_id" if case == "foreign_token_task" else "run_id"] = "foreign"
    append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id="child", event=event))
    query = ReadRunLedgerProjectionQueryV1(
        workspace=str(tmp_path),
        run_id="factory-own",
        factory_run_id="factory-own",
        project_id="P1",
        canonical_only=True,
    )
    if case in {"native", "catalog", "multi_task", "inner_missing_identity"}:
        projection = read_run_ledger_projection(query).projection
        assert projection["available"] is True
        assert projection["consumed_run_ids"] == ["factory-own", "child"]
        assert projection["task_boundary"]["verdict_count"] == 1
        assert set(projection["task_boundary"]["latest_by_task"]) == {"TASK-1"}
        assert projection["task_boundary"]["latest"]["run_id"] == "child"
    else:
        with pytest.raises((RuntimeError, ValueError)):
            read_run_ledger_projection(query)


@pytest.mark.parametrize("case", ["missing", "legacy", "malformed", "foreign", "missing_child_cp"])
def test_http_cannot_qualify_unverified_or_missing_lineage(tmp_path: Path, case: str) -> None:
    gate(tmp_path, "factory-own")
    if case != "missing":
        lineage(tmp_path, factory="other" if case == "foreign" else "factory-own")
    if case != "missing_child_cp":
        gate(tmp_path, "child")
    path = tmp_path / ".polaris/runtime/events/task_runtime.execution.jsonl"
    if case == "legacy":
        row = json.loads(path.read_text(encoding="utf-8"))
        row.pop("integrity_digest", None)
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    if case == "malformed":
        path.write_text('{"bad":\n', encoding="utf-8")
    bundle: dict = {}
    result = run_summary._attach_control_plane_projection(bundle=bundle, run=run(), workspace=str(tmp_path))
    assert result is None
    assert "run_ledger_projection" not in bundle


def test_canonical_projection_captures_verified_parent_and_children(tmp_path: Path) -> None:
    gate(tmp_path, "factory-own")
    lineage(tmp_path)
    gate(tmp_path, "child")
    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="factory-own",
            factory_run_id="factory-own",
            project_id="P1",
            canonical_only=True,
        )
    ).projection
    assert result["available"] is True
    assert result["consumed_run_ids"] == ["factory-own", "child"]
    assert result["run_projection"]["gate_count"] == 2
    assert result["canonical_evidence"]["strict_integrity"] is True
    assert len(result["canonical_evidence"]["captured_hash"]) == 64


@pytest.mark.parametrize(
    "case", ["foreign_project", "foreign_workspace", "conflicting_owner", "corrupt_cp", "wrong_source"]
)
def test_canonical_projection_rejects_scope_or_integrity_conflicts(tmp_path: Path, case: str) -> None:
    gate(tmp_path, "factory-own")
    lineage(tmp_path)
    gate(tmp_path, "child", project="foreign" if case == "foreign_project" else "P1")
    if case == "conflicting_owner":
        lineage(tmp_path, factory="foreign-factory")
    if case in {"foreign_workspace", "wrong_source"}:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="claimed",
                source="foreign-writer" if case == "wrong_source" else "runtime.task_runtime",
                run_id="child",
                task_id="TASK-1",
                payload={
                    "run_id": "child",
                    "task_id": "TASK-1",
                    "factory_run_id": "factory-own",
                    "factory_bench_project_id": "P1",
                    "workspace": str(tmp_path / "foreign") if case == "foreign_workspace" else str(tmp_path),
                },
                strict_integrity=True,
            )
        )
    if case == "corrupt_cp":
        path = tmp_path / ".polaris/runtime/events/execution.control_plane.jsonl"
        path.write_text("{broken}\n", encoding="utf-8")
    with pytest.raises((RuntimeError, ValueError)):
        read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=str(tmp_path),
                run_id="factory-own",
                factory_run_id="factory-own",
                project_id="P1",
                canonical_only=True,
            )
        )


def test_duplicate_verified_lineage_deduplicates_child_projection(tmp_path: Path) -> None:
    gate(tmp_path, "factory-own")
    gate(tmp_path, "child")
    lineage(tmp_path)
    lineage(tmp_path)
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="factory-own",
            factory_run_id="factory-own",
            project_id="P1",
            canonical_only=True,
        )
    ).projection
    assert projection["consumed_run_ids"] == ["factory-own", "child"]
    assert projection["run_projection"]["gate_count"] == 2


def test_unrelated_canonical_cp_cannot_bless_compatibility_projection(tmp_path: Path) -> None:
    gate(tmp_path, "factory-own")
    lineage(tmp_path)
    gate(tmp_path, "child")
    cp = tmp_path / ".polaris/runtime/events/execution.control_plane.jsonl"
    cp.write_text("", encoding="utf-8")
    gate(tmp_path, "foreign", project="P2")
    # The compatibility file remains diagnostic history, not qualification.
    historical = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path), run_id="factory-own", factory_run_id="factory-own", project_id="P1"
        )
    ).projection
    assert historical["available"] is True
    assert historical["run_projection"]["gate_count"] == 2
    with pytest.raises((RuntimeError, ValueError)):
        read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=str(tmp_path),
                run_id="factory-own",
                factory_run_id="factory-own",
                project_id="P1",
                canonical_only=True,
            )
        )
