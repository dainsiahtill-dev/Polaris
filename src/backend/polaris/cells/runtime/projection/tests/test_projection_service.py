"""Unit tests for `runtime.projection` cell - RuntimeProjectionService and helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    append_run_ledger_event,
    build_tool_call_lifecycle_receipt,
)
from polaris.cells.events.fact_stream.public.service import AppendFactEventCommandV1, append_fact_event
from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationMode,
    OrchestrationSnapshot,
    RunStatus,
    TaskPhase,
    TaskSnapshot,
)
from polaris.cells.runtime.projection.internal import runtime_projection_service as projection_service
from polaris.cells.runtime.projection.internal.runtime_projection_service import (
    ProjectionCache,
    RuntimeProjection,
    RuntimeProjectionService,
    TaskSource,
    _extract_run_ledger_run_id,
    _parse_engine_updated_at,
    _read_run_ledger_projection_for_run,
    _safe_int,
    _state_token,
    _task_boundary_execution_state,
    _task_totals,
    _workflow_has_live_rows,
    build_snapshot_payload_from_projection,
    get_active_director_orchestration_status,
    load_runtime_task_rows,
    merge_director_status,
    select_task_rows,
    select_task_rows_from_projection,
)
from polaris.cells.runtime.projection.internal.status_snapshot_builder import (
    _parse_engine_updated_at as _parse_status_snapshot_updated_at,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.workspace.integrity.public.service import write_workspace_status

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Helper function unit tests
# =============================================================================


class TestSafeInt:
    def test_positive_integer(self) -> None:
        assert _safe_int(42) == 42

    def test_negative_integer_becomes_zero(self) -> None:
        assert _safe_int(-5) == 0

    def test_float_rounds_down(self) -> None:
        assert _safe_int(3.9) == 3

    def test_string_int(self) -> None:
        assert _safe_int("123") == 123

    def test_non_numeric_returns_zero(self) -> None:
        assert _safe_int("abc") == 0

    def test_none_returns_zero(self) -> None:
        assert _safe_int(None) == 0


class TestStateToken:
    def test_top_level_state(self) -> None:
        payload = {"state": "running"}
        assert _state_token(payload) == "RUNNING"

    def test_nested_status_state(self) -> None:
        payload = {"status": {"state": "idle"}}
        assert _state_token(payload) == "IDLE"

    def test_empty_payload(self) -> None:
        assert _state_token({}) == ""
        assert _state_token(None) == ""

    def test_top_level_takes_precedence(self) -> None:
        payload = {"state": "running", "status": {"state": "idle"}}
        assert _state_token(payload) == "RUNNING"


class TestRunLedgerProjectionBinding:
    def test_extract_run_ledger_run_id_prefers_explicit_workflow_status(self) -> None:
        assert (
            _extract_run_ledger_run_id(
                {"status": {"run_id": "nested-run"}},
                {"metrics": {"workflow_id": "local-workflow"}},
            )
            == "nested-run"
        )
        assert _extract_run_ledger_run_id({"metrics": {"workflow_id": "wf-1"}}) == "wf-1"

    def test_read_run_ledger_projection_without_run_id_does_not_use_latest_runs(self) -> None:
        projection = _read_run_ledger_projection_for_run("/tmp/polaris-test-workspace", "")

        assert projection["available"] is False
        assert projection["missing_required_run_id"] is True

    def test_whitespace_normalized(self) -> None:
        payload = {"state": "  RUNNING  "}
        assert _state_token(payload) == "RUNNING"


class TestTaskTotals:
    def test_direct_tasks(self) -> None:
        payload = {"tasks": {"total": 10, "by_status": {"IN_PROGRESS": 3}}}
        total, active = _task_totals(payload)
        assert total == 10
        assert active == 3

    def test_nested_status_tasks(self) -> None:
        payload = {"status": {"tasks": {"total": 5, "by_status": {"RUNNING": 2, "CLAIMED": 1}}}}
        total, active = _task_totals(payload)
        assert total == 5
        assert active == 3

    def test_inactive_tasks_not_counted(self) -> None:
        payload = {"tasks": {"total": 8, "by_status": {"PENDING": 5, "COMPLETED": 3}}}
        total, active = _task_totals(payload)
        assert total == 8
        assert active == 0

    def test_missing_tasks_returns_zero(self) -> None:
        assert _task_totals({}) == (0, 0)
        assert _task_totals(None) == (0, 0)


class TestWorkflowHasLiveRows:
    def test_has_live_rows(self) -> None:
        payload = {"tasks": {"task_rows": [{"status": "RUNNING"}, {"status": "COMPLETED"}]}}
        assert _workflow_has_live_rows(payload) is True

    def test_no_live_rows(self) -> None:
        payload = {"tasks": {"task_rows": [{"status": "PENDING"}, {"status": "PENDING"}]}}
        assert _workflow_has_live_rows(payload) is False

    def test_empty_rows(self) -> None:
        assert _workflow_has_live_rows({"tasks": {"task_rows": []}}) is False
        assert _workflow_has_live_rows(None) is False


class TestParseEngineUpdatedAt:
    def test_accepts_space_separated_timestamp(self) -> None:
        assert _parse_engine_updated_at("2026-05-07 15:36:07") is not None

    def test_accepts_iso_z_timestamp(self) -> None:
        assert _parse_engine_updated_at("2026-05-07T15:36:07Z") is not None

    def test_status_snapshot_builder_accepts_iso_z_timestamp(self) -> None:
        assert _parse_status_snapshot_updated_at("2026-05-07T15:36:07Z") is not None


# =============================================================================
# merge_director_status tests
# =============================================================================


class TestMergeDirectorStatus:
    def test_local_takes_precedence_when_running_with_tasks(self) -> None:
        local = {"running": True, "mode": "v2_service", "metrics": {"workflow_id": "wf-1"}, "tasks": {"total": 5}}
        workflow = {"running": False, "state": "queued", "workflow_id": "wf-1"}
        result = merge_director_status(local, workflow)
        assert result["source"] == "v2_service"
        assert result["running"] is True

    def test_workflow_source_when_local_not_running(self) -> None:
        local = {"running": False}
        workflow = {"running": True, "state": "RUNNING", "workflow_id": "wf-2"}
        result = merge_director_status(local, workflow)
        assert result["source"] == "workflow"
        assert result["workflow_id"] == "wf-2"

    def test_empty_workflow_uses_local(self) -> None:
        local = {"running": True, "source": "v2_service"}
        result = merge_director_status(local, None)
        assert result["source"] == "v2_service"

    def test_both_empty_returns_source_none(self) -> None:
        result = merge_director_status(None, None)
        # Returns a dict with 'source: none', not empty dict
        assert result == {"source": "none"}

    def test_token_budget_merged(self) -> None:
        local = {"running": False, "token_budget": {"used": 100}}
        workflow = {"running": False, "token_budget": {"limit": 1000}}
        result = merge_director_status(local, workflow)
        assert result["token_budget"]["used"] == 100
        assert result["token_budget"]["limit"] == 1000

    def test_local_running_overrides_workflow_state(self) -> None:
        local = {"running": True, "state": "RUNNING"}
        workflow = {"running": False, "state": "queued"}
        result = merge_director_status(local, workflow)
        assert result["state"] == "RUNNING"

    def test_run_ledger_tool_dispatch_dropped_overrides_status(self) -> None:
        local = {"running": False, "state": "IDLE"}
        workflow = {"running": False, "state": "IDLE"}
        projection = {
            "available": True,
            "ok": False,
            "status": "failed",
            "detail": "tool dispatch dropped",
            "tool_lifecycle": {
                "ok": False,
                "dropped_count": 1,
                "events": [{"dispatch_status": "dropped"}],
            },
            "task_boundary": {},
        }

        result = merge_director_status(local, workflow, run_ledger_projection=projection)

        assert result["source"] == "run_ledger_projection"
        assert result["state"] == "FAILED_PLATFORM"
        assert result["running"] is False
        assert result["execution_state"] == "FAILED_PLATFORM"
        assert result["error_code"] == "tool_dispatch_dropped"
        assert "events" not in result["run_ledger_projection"]["tool_lifecycle"]

    def test_run_ledger_tool_lifecycle_failure_overrides_status(self) -> None:
        local = {"running": False, "state": "IDLE"}
        workflow = {"running": False, "state": "IDLE"}
        projection = {
            "available": True,
            "ok": False,
            "status": "failed",
            "detail": "tool lifecycle failed",
            "tool_lifecycle": {
                "ok": False,
                "dropped_count": 0,
                "failed_count": 1,
                "events": [
                    {
                        "failed": True,
                        "failure_class": "MISSING_EFFECT_RECEIPT",
                        "reason": "write_file success lacked an effect receipt",
                    }
                ],
            },
            "task_boundary": {},
        }

        result = merge_director_status(local, workflow, run_ledger_projection=projection)

        assert result["source"] == "run_ledger_projection"
        assert result["state"] == "FAILED_PLATFORM"
        assert result["running"] is False
        assert result["execution_state"] == "FAILED_PLATFORM"
        assert result["error_code"] == "missing_effect_receipt"
        assert result["last_error"] == "write_file success lacked an effect receipt"

    def test_run_ledger_task_boundary_failure_overrides_status(self) -> None:
        local = {"running": False, "state": "IDLE"}
        workflow = {"running": False, "state": "IDLE"}
        projection = {
            "available": True,
            "ok": False,
            "status": "failed",
            "detail": "task boundary failed",
            "tool_lifecycle": {"ok": True, "dropped_count": 0},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "ok": False,
                    "failure_class": "INCOMPLETE_MATERIALIZATION",
                    "reason": "target files were not written",
                },
            },
        }

        result = merge_director_status(local, workflow, run_ledger_projection=projection)

        assert result["source"] == "run_ledger_projection"
        assert result["state"] == "FAILED_ARTIFACT"
        assert result["running"] is False
        assert result["execution_state"] == "FAILED_ARTIFACT"
        assert result["error_code"] == "incomplete_materialization"
        assert result["last_error"] == "target files were not written"

    def test_run_ledger_task_boundary_platform_failure_overrides_status(self) -> None:
        local = {"running": False, "state": "IDLE"}
        workflow = {"running": False, "state": "IDLE"}
        projection = {
            "available": True,
            "ok": False,
            "status": "failed",
            "detail": "task boundary failed",
            "tool_lifecycle": {"ok": True, "dropped_count": 0},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "ok": False,
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                    "reason": "tool call lifecycle dropped",
                },
            },
        }

        result = merge_director_status(local, workflow, run_ledger_projection=projection)

        assert result["source"] == "run_ledger_projection"
        assert result["state"] == "FAILED_PLATFORM"
        assert result["execution_state"] == "FAILED_PLATFORM"
        assert result["error_code"] == "tool_dispatch_dropped"

    def test_run_ledger_ok_projection_keeps_existing_status_source(self) -> None:
        local = {"running": False, "state": "IDLE", "source": "v2_service"}
        workflow = {"running": False, "state": "IDLE"}
        projection = {
            "available": True,
            "ok": True,
            "status": "ready",
            "detail": "run ledger projection 1 project(s), 0 failed",
            "tool_lifecycle": {"ok": True, "dropped_count": 0},
            "task_boundary": {"ok": True, "latest": {"ok": True}},
        }

        result = merge_director_status(local, workflow, run_ledger_projection=projection)

        assert result["source"] == "workflow"
        assert result["execution_state"] == "COMPLETED_VERIFIED"
        assert result["run_ledger_projection"]["ok"] is True


def test_snapshot_derived_prefers_director_execution_state_over_nested_status() -> None:
    projection = RuntimeProjection(
        director_merged={
            "running": False,
            "state": "FAILED_PLATFORM",
            "execution_state": "FAILED_PLATFORM",
            "status": {"state": "IDLE"},
        }
    )

    snapshot = build_snapshot_payload_from_projection(projection, workspace="")

    assert snapshot["snapshot_derived"]["director_status"] == "FAILED_PLATFORM"


def test_snapshot_task_rows_apply_run_ledger_task_boundary_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        projection_service,
        "load_runtime_task_rows",
        lambda workspace: [
            {
                "id": "TASK-1",
                "task_id": "TASK-1",
                "status": "RUNNING",
                "running": True,
                "metadata": {"source": "runtime_task_file"},
            }
        ],
    )
    projection = RuntimeProjection(
        director_merged={
            "run_ledger_projection": {
                "task_boundary": {
                    "latest": {
                        "task_id": "TASK-1",
                        "ok": False,
                        "failure_class": "INCOMPLETE_MATERIALIZATION",
                        "responsible_layer": "director",
                        "reason": "target files were not written",
                    }
                }
            }
        }
    )

    snapshot = build_snapshot_payload_from_projection(projection, workspace=str(tmp_path))

    assert snapshot["tasks"][0]["status"] == "FAILED_ARTIFACT"
    assert snapshot["tasks"][0]["running"] is False
    assert snapshot["tasks"][0]["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert snapshot["tasks"][0]["error_message"] == "target files were not written"
    assert snapshot["tasks"][0]["metadata"]["status_source"] == "run_ledger_projection"


def test_snapshot_task_rows_project_run_ledger_boundary_when_rows_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        projection_service,
        "load_runtime_task_rows",
        lambda workspace: [],
    )
    projection = RuntimeProjection(
        director_merged={
            "run_ledger_projection": {
                "task_boundary": {
                    "latest": {
                        "task_id": "TASK-2",
                        "ok": False,
                        "failure_class": "TOOL_DISPATCH_DROPPED",
                        "responsible_layer": "platform",
                        "reason": "provider emitted native tool calls but dispatch was dropped",
                    }
                }
            }
        }
    )

    snapshot = build_snapshot_payload_from_projection(projection, workspace=str(tmp_path))

    assert snapshot["tasks"][0]["task_id"] == "TASK-2"
    assert snapshot["tasks"][0]["status"] == "FAILED_PLATFORM"
    assert snapshot["tasks"][0]["running"] is False
    assert snapshot["tasks"][0]["metadata"]["source"] == "run_ledger_projection"
    assert snapshot["tasks"][0]["responsible_layer"] == "platform"
    assert snapshot["tasks"][0]["error_message"] == "provider emitted native tool calls but dispatch was dropped"


def test_snapshot_task_rows_project_task_runtime_execution_facts_when_rows_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        projection_service,
        "load_runtime_task_rows",
        lambda workspace: [],
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-3",
            run_id="run-3",
            payload={
                "task_id": "TASK-3",
                "run_id": "run-3",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-3",
                "claimed_by": "director-worker",
                "last_claimed_by": "director-worker",
                "attempt": 2,
                "resume_count": 1,
                "resume_state": "resumed",
                "resume_available": False,
                "lease_expires_at": "2026-07-04T00:00:00+00:00",
                "last_heartbeat_at": "2026-07-03T23:59:00+00:00",
            },
        )
    )
    projection = RuntimeProjection()

    snapshot = build_snapshot_payload_from_projection(projection, workspace=str(tmp_path))

    assert snapshot["tasks"][0]["task_id"] == "TASK-3"
    assert snapshot["tasks"][0]["status"] == "in_progress"
    assert snapshot["tasks"][0]["running"] is True
    assert snapshot["tasks"][0]["session_id"] == "session-3"
    assert snapshot["tasks"][0]["claim_attempt"] == 2
    assert snapshot["tasks"][0]["resume_count"] == 1
    assert snapshot["tasks"][0]["metadata"]["source"] == "task_runtime.execution_fact"


def test_snapshot_task_rows_overlay_existing_rows_with_task_runtime_execution_facts(tmp_path: Path) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="failed",
            source="runtime.task_runtime",
            task_id="TASK-4",
            run_id="run-4",
            payload={
                "task_id": "TASK-4",
                "run_id": "run-4",
                "event_type": "failed",
                "status": "failed",
                "execution_state": "failed",
                "session_id": "session-4",
                "attempt": 1,
                "resume_count": 0,
                "last_error": "director execution failed",
            },
        )
    )
    projection = RuntimeProjection(
        task_rows=[
            {
                "id": "TASK-4",
                "task_id": "TASK-4",
                "status": "RUNNING",
                "running": True,
                "metadata": {"source": "workflow"},
            }
        ]
    )

    snapshot = build_snapshot_payload_from_projection(projection, workspace=str(tmp_path))

    assert snapshot["tasks"][0]["task_id"] == "TASK-4"
    assert snapshot["tasks"][0]["status"] == "failed"
    assert snapshot["tasks"][0]["running"] is False
    assert snapshot["tasks"][0]["last_error"] == "director execution failed"
    assert snapshot["tasks"][0]["metadata"]["status_source"] == "task_runtime.execution_fact"
    assert snapshot["tasks"][0]["metadata"]["previous_status"] == "RUNNING"


def test_snapshot_completed_count_includes_completed_verified_rows(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        workflow_archive={"tasks": {"by_status": {"COMPLETED_VERIFIED": 1}}},
        task_rows=[{"id": "TASK-1", "status": "COMPLETED_VERIFIED"}],
    )

    snapshot = build_snapshot_payload_from_projection(projection, workspace=str(tmp_path))

    assert snapshot["snapshot_derived"]["workflow_completed_tasks"] == 1
    assert snapshot["pm_state"]["completed_task_count"] == 1


def test_snapshot_task_rows_normalize_run_ledger_task_boundary_failure_class(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        projection_service,
        "load_runtime_task_rows",
        lambda workspace: [
            {
                "id": "TASK-1",
                "task_id": "TASK-1",
                "status": "RUNNING",
                "running": True,
                "metadata": {"source": "runtime_task_file"},
            }
        ],
    )
    projection = RuntimeProjection(
        director_merged={
            "run_ledger_projection": {
                "task_boundary": {
                    "latest": {
                        "task_id": "TASK-1",
                        "ok": False,
                        "failure_class": "scope_mismatch",
                        "responsible_layer": "chief_engineer",
                        "reason": "scope mismatch",
                    }
                }
            }
        }
    )

    snapshot = build_snapshot_payload_from_projection(projection, workspace=str(tmp_path))

    assert snapshot["tasks"][0]["status"] == "BLOCKED_WITH_REASON"
    assert snapshot["tasks"][0]["failure_class"] == "BLUEPRINT_SCOPE_MISMATCH"
    assert snapshot["tasks"][0]["metadata"]["run_ledger_task_boundary"]["failure_class"] == "scope_mismatch"


def test_task_boundary_execution_state_uses_shared_qa_failure_taxonomy() -> None:
    assert _task_boundary_execution_state("incomplete_materialization") == "FAILED_ARTIFACT"
    assert _task_boundary_execution_state("missing_entrypoint_target") == "FAILED_ARTIFACT"
    assert _task_boundary_execution_state("implementation_defect") == "FAILED_ARTIFACT"
    assert _task_boundary_execution_state("tool_dispatch_dropped") == "FAILED_PLATFORM"
    assert _task_boundary_execution_state("execution_evidence_missing") == "BLOCKED_WITH_REASON"
    assert _task_boundary_execution_state("dependency_not_unlocked") == "BLOCKED_WITH_REASON"


# =============================================================================
# select_task_rows tests
# =============================================================================


class TestSelectTaskRows:
    def test_runtime_projection_rows_used_when_local_projection_missing(self) -> None:
        runtime_rows = [{"id": "task-1", "status": "RUNNING"}]
        local = {"running": True}
        rows, source = select_task_rows(runtime_rows, local)
        assert rows == runtime_rows
        assert source == TaskSource.TASK_RUNTIME

    def test_runtime_task_rows_preferred_over_workflow_rows(self) -> None:
        workflow_rows = [{"id": "workflow-1", "status": "RUNNING"}]
        runtime_rows = [{"id": "runtime-1", "status": "pending"}]
        local = {
            "running": False,
            "state": "IDLE",
            "status": {
                "tasks": {
                    "projection_source": "runtime.task_runtime",
                    "task_rows": runtime_rows,
                }
            },
        }

        rows, source = select_task_rows(workflow_rows, local)

        assert rows == runtime_rows
        assert source == TaskSource.TASK_RUNTIME

    def test_local_live_when_workflow_empty(self) -> None:
        local = {
            "running": True,
            "state": "RUNNING",
            "status": {"tasks": {"task_rows": [{"id": "local-1", "status": "RUNNING"}]}},
        }
        rows, source = select_task_rows([], local)
        assert rows == [{"id": "local-1", "status": "RUNNING"}]
        assert source == TaskSource.LOCAL_LIVE

    def test_empty_when_nothing_available(self) -> None:
        rows, source = select_task_rows(None, None)
        assert rows == []
        assert source == TaskSource.NONE

    def test_workflow_none_vs_empty_list(self) -> None:
        """None should be treated as empty, not as having content."""
        local = {"running": True, "state": "RUNNING", "status": {"tasks": {"task_rows": [{"id": "x"}]}}}
        rows, source = select_task_rows(None, local)
        assert rows == [{"id": "x"}]
        assert source == TaskSource.LOCAL_LIVE


# =============================================================================
# ProjectionCache tests
# =============================================================================


class TestProjectionCache:
    def test_cache_set_and_get(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        proj = RuntimeProjection()
        cache.set("test-workspace", proj)
        retrieved = cache.get("test-workspace")
        assert retrieved is proj

    def test_cache_miss_for_unknown_workspace(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        assert cache.get("unknown") is None

    def test_cache_expires_after_ttl(self) -> None:
        cache = ProjectionCache(ttl_seconds=0.0)  # Immediate expiry
        proj = RuntimeProjection()
        cache.set("test-workspace", proj)
        # Immediately expired
        assert cache.get("test-workspace") is None

    def test_cache_invalidate(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        proj = RuntimeProjection()
        cache.set("test-workspace", proj)
        cache.invalidate("test-workspace")
        assert cache.get("test-workspace") is None

    def test_cache_clear(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        cache.set("ws1", RuntimeProjection())
        cache.set("ws2", RuntimeProjection())
        cache.clear()
        assert cache.get("ws1") is None
        assert cache.get("ws2") is None

    def test_cache_empty_workspace_rejected(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        cache.set("", RuntimeProjection())
        assert cache.get("") is None


# =============================================================================
# select_task_rows_from_projection tests
# =============================================================================


class TestSelectTaskRowsFromProjection:
    def test_projection_task_rows_returned_when_available(self) -> None:
        proj = RuntimeProjection(
            workflow_archive={"tasks": [{"id": "wf-task-1"}]},
            director_local={"running": False},
            task_rows=[{"id": "runtime-task-1"}],
        )
        rows = select_task_rows_from_projection(proj)
        assert rows == [{"id": "runtime-task-1"}]

    def test_workflow_archive_rows_not_returned_as_live_tasks(self) -> None:
        proj = RuntimeProjection(
            workflow_archive={"tasks": [{"id": "wf-task-1"}]},
            director_local={"running": False},
        )
        rows = select_task_rows_from_projection(proj)
        assert rows == []

    def test_local_rows_when_workflow_empty(self) -> None:
        proj = RuntimeProjection(
            workflow_archive={},
            director_local={
                "running": True,
                "state": "RUNNING",
                "status": {"tasks": {"task_rows": [{"id": "local-task-1"}]}},
            },
        )
        rows = select_task_rows_from_projection(proj)
        assert rows == [{"id": "local-task-1"}]

    def test_empty_when_nothing_available(self) -> None:
        proj = RuntimeProjection()
        rows = select_task_rows_from_projection(proj)
        assert rows == []


# =============================================================================
# RuntimeProjectionService.build() tests (sync, no I/O)
# =============================================================================


class TestRuntimeProjectionServiceBuild:
    def test_build_returns_runtime_projection(self, tmp_path: Path) -> None:
        """Sanity: build() returns a RuntimeProjection with expected fields."""
        ProjectionCache(ttl_seconds=60.0)
        proj = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        assert isinstance(proj, RuntimeProjection)
        # Fields should be present (even if empty)
        assert isinstance(proj.pm_local, dict)
        assert isinstance(proj.director_local, dict)

    def test_build_with_custom_cache(self, tmp_path: Path) -> None:
        ProjectionCache(ttl_seconds=60.0)
        proj1 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=True,
        )
        # Second call with same cache should hit cache
        proj2 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=True,
        )
        # Same instance returned from cache
        assert proj1 is proj2

    def test_build_cache_disabled(self, tmp_path: Path) -> None:
        ProjectionCache(ttl_seconds=60.0)
        proj1 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        proj2 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        # Different instances when cache disabled
        assert proj1 is not proj2


# =============================================================================
# RuntimeProjection dataclass tests
# =============================================================================


class TestRuntimeProjectionDataclass:
    def test_default_fields(self) -> None:
        proj = RuntimeProjection()
        assert proj.pm_local == {}
        assert proj.director_local == {}
        assert proj.workflow_archive is None
        assert proj.engine_fallback is None
        assert proj.court_state == {}
        assert proj.snapshot == {}
        assert proj.memory is None
        assert proj.success_stats == {}
        assert proj.anthro_state is None
        assert proj.lancedb == {}
        assert proj.resident is None
        assert proj.task_source == TaskSource.NONE
        assert proj.task_rows == []

    def test_custom_fields(self) -> None:
        custom = {"running": True, "pid": 12345}
        proj = RuntimeProjection(
            pm_local=custom,
            task_source=TaskSource.TASK_RUNTIME,
            task_rows=[{"id": "task-1"}],
        )
        assert proj.pm_local == custom
        assert proj.task_source == TaskSource.TASK_RUNTIME
        assert proj.task_rows == [{"id": "task-1"}]


# =============================================================================
# build_snapshot_payload_from_projection tests
# =============================================================================


class TestBuildSnapshotPayloadFromProjection:
    def test_factory_latest_plan_populates_tasks_when_runtime_contract_missing(self, tmp_path: Path) -> None:
        plan_dir = tmp_path / ".polaris" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "latest.plan.json").write_text(
            """
            {
              "generated_at": "2026-06-19T00:00:00Z",
              "source": "factory",
              "tasks": [
                {
                  "id": "TASK-1",
                  "title": "实现本地记账 GUI",
                  "goal": "交付可运行的 Tkinter 记账软件",
                  "scope_paths": ["src/main.py"],
                  "target_files": ["src/main.py"],
                  "steps": ["实现入口", "实现界面"],
                  "acceptance_criteria": ["python src/main.py 可启动"],
                  "assigned_to": "Director"
                }
              ]
            }
            """,
            encoding="utf-8",
        )

        payload = build_snapshot_payload_from_projection(
            RuntimeProjection(),
            workspace=str(tmp_path),
            cache_root=tmp_path,
        )

        assert payload["tasks"][0]["id"] == "TASK-1"
        assert payload["tasks"][0]["title"] == "实现本地记账 GUI"
        assert payload["tasks"][0]["assigned_to"] == "Director"

    def test_factory_blueprints_enrich_latest_plan_tasks(self, tmp_path: Path) -> None:
        plan_dir = tmp_path / ".polaris" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "latest.plan.json").write_text(
            """
            {
              "tasks": [
                {
                  "id": "TASK-1",
                  "title": "实现本地记账 GUI",
                  "goal": "交付可运行的 Tkinter 记账软件",
                  "assigned_to": "Director"
                }
              ]
            }
            """,
            encoding="utf-8",
        )
        blueprint_dir = tmp_path / ".polaris" / "blueprints"
        blueprint_dir.mkdir(parents=True)
        blueprint_path = blueprint_dir / "ce_TASK-1_20260619000000000000.json"
        blueprint_path.write_text(
            """
            {
              "blueprint_id": "ce_TASK-1_20260619000000000000",
              "task_id": "TASK-1",
              "title": "实现本地记账 GUI",
              "summary": "Chief Engineer blueprint for TASK-1",
              "status": "generated",
              "target_files": ["src/main.py"],
              "handoff_ready": true
            }
            """,
            encoding="utf-8",
        )

        payload = build_snapshot_payload_from_projection(
            RuntimeProjection(),
            workspace=str(tmp_path),
            cache_root=tmp_path,
        )

        task = payload["tasks"][0]
        assert task["blueprint_id"] == "ce_TASK-1_20260619000000000000"
        assert task["runtime_blueprint_path"].endswith("ce_TASK-1_20260619000000000000.json")
        assert task["blueprint_summary"] == "Chief Engineer blueprint for TASK-1"
        assert task["handoff_ready"] is True

    def test_factory_blueprint_target_files_extend_existing_plan_scope(self, tmp_path: Path) -> None:
        plan_dir = tmp_path / ".polaris" / "plans"
        plan_dir.mkdir(parents=True)
        (plan_dir / "latest.plan.json").write_text(
            """
            {
              "tasks": [
                {
                  "id": "TASK-2",
                  "title": "Build browser simulation",
                  "goal": "Implement the browser entrypoint",
                  "target_files": ["package.json", "src/web.ts"],
                  "scope_paths": ["package.json", "src/web.ts"],
                  "assigned_to": "Director"
                }
              ]
            }
            """,
            encoding="utf-8",
        )
        blueprint_dir = tmp_path / ".polaris" / "blueprints"
        blueprint_dir.mkdir(parents=True)
        (blueprint_dir / "ce_TASK-2_20260619000000000000.json").write_text(
            """
            {
              "blueprint_id": "ce_TASK-2_20260619000000000000",
              "task_id": "TASK-2",
              "summary": "Chief Engineer blueprint for TASK-2",
              "status": "generated",
              "target_files": [
                "package.json",
                "src/web.ts",
                "src/engine/types.ts",
                "src/engine/simulation.ts"
              ],
              "scope_paths": ["src/engine/types.ts", "src/engine/simulation.ts"],
              "handoff_ready": true
            }
            """,
            encoding="utf-8",
        )

        payload = build_snapshot_payload_from_projection(
            RuntimeProjection(),
            workspace=str(tmp_path),
            cache_root=tmp_path,
        )

        task = payload["tasks"][0]
        assert task["target_files"] == [
            "package.json",
            "src/web.ts",
            "src/engine/types.ts",
            "src/engine/simulation.ts",
        ]
        assert task["scope_paths"] == [
            "package.json",
            "src/web.ts",
            "src/engine/types.ts",
            "src/engine/simulation.ts",
        ]

    def test_docs_ready_projection_overrides_stale_workspace_status(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        write_workspace_status(
            str(tmp_path),
            status="NEEDS_DOCS_INIT",
            reason="stale status from before docs init",
            actions=["INIT_DOCS_WIZARD"],
        )

        payload = build_snapshot_payload_from_projection(
            RuntimeProjection(),
            workspace=str(tmp_path),
            cache_root=tmp_path,
        )

        assert payload["docs_present"] is True
        assert payload["workspace_status"]["status"] == "READY"
        assert payload["workspace_status"]["source"] == "runtime_projection"

    def test_docs_missing_projection_marks_workspace_not_initialized(self, tmp_path: Path) -> None:
        payload = build_snapshot_payload_from_projection(
            RuntimeProjection(),
            workspace=str(tmp_path),
            cache_root=tmp_path,
        )

        assert payload["docs_present"] is False
        assert payload["workspace_status"]["status"] == "NEEDS_DOCS_INIT"
        assert payload["workspace_status"]["source"] == "runtime_projection"


# =============================================================================
# load_runtime_task_rows tests
# =============================================================================


class TestLoadRuntimeTaskRows:
    def test_empty_workspace_returns_empty(self) -> None:
        rows = load_runtime_task_rows("")
        assert rows == []

    def test_workspace_with_no_state_owner_returns_empty(self, tmp_path: Path) -> None:
        # No task_runtime state files exist in this tmp_path
        rows = load_runtime_task_rows(str(tmp_path))
        # Should return empty list (no exception)
        assert rows == []


# =============================================================================
# Async build tests (sanity - no I/O)
# =============================================================================


@pytest.mark.asyncio
class TestRuntimeProjectionServiceBuildAsync:
    async def test_build_async_returns_projection(self, tmp_path: Path) -> None:
        proj = await RuntimeProjectionService.build_async(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        assert isinstance(proj, RuntimeProjection)

    async def test_build_async_merges_active_director_orchestration_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        task_runtime = TaskRuntimeService(str(tmp_path))
        task_runtime.create_task_row(subject="Runtime row")
        snapshot = OrchestrationSnapshot(
            run_id="director-active123",
            workspace=str(tmp_path),
            mode=OrchestrationMode.WORKFLOW.value,
            status=RunStatus.RUNNING,
            current_phase=TaskPhase.EXECUTING,
            tasks={
                "task-0-director": TaskSnapshot(
                    task_id="task-0-director",
                    status=RunStatus.RUNNING,
                    phase=TaskPhase.EXECUTING,
                    role_id="director",
                )
            },
        )

        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_pm_local_status",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_director_local_status",
            AsyncMock(return_value={"running": False, "status": {"state": "IDLE"}}),
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_director_status",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service._list_recent_orchestration_runs",
            AsyncMock(return_value=[snapshot]),
        )

        proj = await RuntimeProjectionService.build_async(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )

        assert proj.director_merged["state"] == "RUNNING"
        assert proj.director_merged["running"] is True
        assert proj.director_merged["source"] == "workflow"
        assert proj.task_source == TaskSource.TASK_RUNTIME
        assert proj.workflow_archive is not None
        assert proj.workflow_archive["tasks"]["projection_source"] == "runtime.task_runtime"
        assert proj.workflow_archive["raw_workflow_status"]["workflow_tasks"]["projection_source"] == (
            "orchestration.workflow_runtime"
        )
        assert proj.workflow_archive["raw_workflow_status"]["workflow_tasks"]["task_rows"][0]["id"] == (
            "task-0-director"
        )
        assert proj.task_rows[0]["subject"] == "Runtime row"

    async def test_active_director_status_ignores_other_workspaces(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        snapshot = OrchestrationSnapshot(
            run_id="director-other123",
            workspace=str(tmp_path / "other"),
            mode=OrchestrationMode.WORKFLOW.value,
            status=RunStatus.RUNNING,
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service._list_recent_orchestration_runs",
            AsyncMock(return_value=[snapshot]),
        )

        payload = await get_active_director_orchestration_status(str(tmp_path))

        assert payload is None

    async def test_build_async_run_ledger_dropped_dispatch_overrides_local_idle(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        lifecycle = build_tool_call_lifecycle_receipt(
            run_id="director-run-1",
            task_id="TASK-1",
            turn_id="turn-1",
            role="director",
            provider_response_hash="provider-response-hash",
            native_tool_calls_count=1,
            decoded_tool_calls_count=0,
            dispatched_tool_calls_count=0,
            receipts=[],
            dispatch_status="dropped",
            failure_class="TOOL_DISPATCH_DROPPED",
        ).to_dict()
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="director-run-1",
                event={
                    "event_type": "gate_evaluated",
                    "stage": "director",
                    "gate": {"name": "director", "ok": True, "summary": "director started"},
                    "job_token": {
                        "token_id": "token-1",
                        "run_id": "director-run-1",
                        "task_id": "TASK-1",
                        "project_id": "TASK-1",
                        "capability_audit": {"ok": True, "issues": []},
                        "gate_policy": {},
                    },
                    "physical_evidence": {},
                },
            )
        )
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="director-run-1",
                event={
                    "event_type": "tool_call_lifecycle",
                    "stage": "director_tool_dispatch",
                    "task_id": "TASK-1",
                    "run_id": "director-run-1",
                    "job_token": {
                        "token_id": "token-1",
                        "run_id": "director-run-1",
                        "task_id": "TASK-1",
                        "project_id": "TASK-1",
                        "capability_audit": {"ok": True, "issues": []},
                        "gate_policy": {},
                    },
                    "tool_call_lifecycle_receipt": lifecycle,
                },
            )
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_pm_local_status",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_director_local_status",
            AsyncMock(return_value={"running": False, "state": "IDLE", "source": "v2_service"}),
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_director_status",
            AsyncMock(
                return_value={
                    "running": False,
                    "state": "IDLE",
                    "workflow_id": "director-run-1",
                    "status": {"run_id": "director-run-1", "state": "IDLE"},
                }
            ),
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.projection.internal.runtime_projection_service._list_recent_orchestration_runs",
            AsyncMock(return_value=[]),
        )

        projection = await RuntimeProjectionService.build_async(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )

        assert projection.director_merged["source"] == "run_ledger_projection"
        assert projection.director_merged["state"] == "FAILED_PLATFORM"
        assert projection.director_merged["execution_state"] == "FAILED_PLATFORM"
        assert projection.director_merged["error_code"] == "tool_dispatch_dropped"
        assert projection.director_merged["run_ledger_projection"]["tool_lifecycle"]["dropped_count"] == 1
