"""Characterization tests for ``polaris.delivery.http.v2.director`` pure helpers.

These tests freeze the *current* behavior of the side-effect-free leaf helpers
that are about to be relocated into sibling modules during the lossless module
split. They reference every helper through the ``director`` module object so the
test keeps passing whether a helper lives in ``director`` directly or is
re-exported from a sibling module.

Scope: pure data-transform leaves only (no monkeypatchable runtime calls).
"""

from __future__ import annotations

from typing import Any

from polaris.delivery.http.v2 import director as d


def test_state_token_prefers_top_level_state() -> None:
    assert d._state_token({"state": "running"}) == "RUNNING"


def test_state_token_falls_back_to_nested_status_then_running_flag() -> None:
    assert d._state_token({"status": {"state": "idle"}}) == "IDLE"
    assert d._state_token({"running": True}) == "RUNNING"
    assert d._state_token({}) == "IDLE"


def test_flatten_director_status_normalizes_running_and_defaults() -> None:
    flattened = d._flatten_director_status({"running": True})
    assert flattened["running"] is True
    assert flattened["state"] == "RUNNING"
    assert flattened["status"] == {"state": "RUNNING"}
    assert flattened["source"] == "none"


def test_flatten_director_status_handles_none() -> None:
    flattened = d._flatten_director_status(None)
    assert flattened["running"] is False
    assert flattened["state"] == "IDLE"


def test_director_tasks_queued_from_metadata_int() -> None:
    result = type("R", (), {"metadata": {"tasks_queued": 3}})()
    assert d._director_tasks_queued(result, ["a", "b"]) == 3


def test_director_tasks_queued_from_metadata_task_ids() -> None:
    result = type("R", (), {"metadata": {"task_ids": ["x", "", "y"]}})()
    assert d._director_tasks_queued(result, ["a"]) == 2


def test_director_tasks_queued_fallback_to_requested() -> None:
    result = type("R", (), {"metadata": None})()
    assert d._director_tasks_queued(result, ["a", "b", "c"]) == 3


def test_director_run_task_ids_explicit_request_wins() -> None:
    ids, source = d._director_run_task_ids_from_diagnostics(object(), [" t1 ", "t2"])
    assert ids == ["t1", "t2"]
    assert source == "explicit_request"


def test_director_run_task_ids_from_diagnostics_blueprint_ready_only() -> None:
    tasks = type(
        "T",
        (),
        {"blueprint_ready_task_ids": ["b1", "b2"], "ready_task_ids": []},
    )()
    diagnostics = type("D", (), {"tasks": tasks})()
    ids, source = d._director_run_task_ids_from_diagnostics(diagnostics, [])
    assert ids == ["b1", "b2"]
    assert source == "diagnostics_blueprint_ready"


def test_director_run_task_ids_from_diagnostics_both_sources_dedup_to_mixed() -> None:
    # Both candidate sources contribute distinct ids -> labelled mixed.
    tasks = type(
        "T",
        (),
        {"blueprint_ready_task_ids": ["b1", "b2"], "ready_task_ids": ["r1"]},
    )()
    diagnostics = type("D", (), {"tasks": tasks})()
    ids, source = d._director_run_task_ids_from_diagnostics(diagnostics, [])
    assert ids == ["b1", "b2", "r1"]
    assert source == "diagnostics_mixed_ready"


def test_director_run_task_ids_from_diagnostics_ready_only() -> None:
    tasks = type(
        "T",
        (),
        {"blueprint_ready_task_ids": [], "ready_task_ids": ["r1", "r2"]},
    )()
    diagnostics = type("D", (), {"tasks": tasks})()
    ids, source = d._director_run_task_ids_from_diagnostics(diagnostics, [])
    assert ids == ["r1", "r2"]
    assert source == "diagnostics_ready"


def test_director_run_task_ids_mixed_ready() -> None:
    tasks = type(
        "T",
        (),
        {"blueprint_ready_task_ids": ["b1"], "ready_task_ids": ["r1"]},
    )()
    diagnostics = type("D", (), {"tasks": tasks})()
    ids, source = d._director_run_task_ids_from_diagnostics(diagnostics, [])
    assert ids == ["b1", "r1"]
    assert source == "diagnostics_mixed_ready"


def test_director_run_task_ids_none() -> None:
    diagnostics = type("D", (), {"tasks": None})()
    assert d._director_run_task_ids_from_diagnostics(diagnostics, []) == ([], "none")


def test_as_dict() -> None:
    assert d._as_dict({"a": 1}) == {"a": 1}
    assert d._as_dict("nope") == {}
    assert d._as_dict(None) == {}


def test_text_or_none() -> None:
    assert d._text_or_none(None) is None
    assert d._text_or_none("  x ") == "x"
    assert d._text_or_none("   ") is None
    assert d._text_or_none(5) == "5"


def test_first_text() -> None:
    assert d._first_text(None, "", "  hit ", "second") == "hit"
    assert d._first_text(None, "") is None


def test_string_list_from_scalars_and_dicts() -> None:
    assert d._string_list(None) == []
    assert d._string_list("  one ") == ["one"]
    assert d._string_list(["a", "a", "b"]) == ["a", "b"]
    assert d._string_list([{"description": "desc"}, {"path": "p"}]) == ["desc", "p"]


def test_first_string_list() -> None:
    assert d._first_string_list(None, [], ["a"], ["b"]) == ["a"]
    assert d._first_string_list(None, []) == []


def test_normalize_task_status_token_aliases() -> None:
    assert d._normalize_task_status_token("") == "PENDING"
    assert d._normalize_task_status_token("todo") == "PENDING"
    assert d._normalize_task_status_token("in-progress") == "RUNNING"
    assert d._normalize_task_status_token("done") == "COMPLETED"
    assert d._normalize_task_status_token("canceled") == "CANCELLED"
    assert d._normalize_task_status_token("weird") == "WEIRD"


def test_task_id_from_row_priority() -> None:
    assert d._task_id_from_row({"id": "i", "task_id": "t"}) == "i"
    assert d._task_id_from_row({"pm_task_id": "pm"}) == "pm"
    assert d._task_id_from_row({"metadata": {"pm_task_id": "m"}}) == "m"
    assert d._task_id_from_row({}) == ""


def test_projection_source_for_task_rows_precedence() -> None:
    proj = type("P", (), {"workflow_archive": {"k": 1}})()
    assert d._projection_source_for_task_rows(proj) == "workflow_archive"
    proj2 = type("P", (), {"director_merged": {"k": 1}})()
    assert d._projection_source_for_task_rows(proj2) == "director_merged"
    proj3 = type("P", (), {"director_local": {"k": 1}})()
    assert d._projection_source_for_task_rows(proj3) == "director_local"
    assert d._projection_source_for_task_rows(object()) == "runtime_projection"


def test_with_task_projection_source_sets_metadata() -> None:
    row = {"id": "x", "metadata": {}}
    out = d._with_task_projection_source(row, fallback_source="fb")
    assert out["metadata"]["projection_source"] == "fb"
    # original not mutated
    assert row["metadata"] == {}


def test_with_task_projection_source_keeps_existing() -> None:
    row = {"id": "x", "metadata": {"projection_source": "keep"}}
    out = d._with_task_projection_source(row, fallback_source="fb")
    assert out["metadata"]["projection_source"] == "keep"


def test_task_details_status_and_worker_and_error() -> None:
    row: dict[str, Any] = {
        "status": "failed",
        "claimed_by": "w1",
        "error": "boom",
        "depends_on": ["d1"],
        "target_files": ["f.py"],
        "acceptance_criteria": ["a"],
        "metadata": {},
    }
    details = d._task_details(row)
    assert details["status"] == "FAILED"
    assert details["worker"] == "w1"
    assert details["error"] == "boom"
    assert details["dependencies"] == ["d1"]
    assert details["target_files"] == ["f.py"]
    assert details["acceptance"] == ["a"]


def test_row_requires_blueprint_evidence_routes() -> None:
    assert d._row_requires_blueprint_evidence({"route": "direct_to_director"}, source="workflow") is False
    assert d._row_requires_blueprint_evidence({"route": "chief_blueprint_required"}, source="local") is True
    assert d._row_requires_blueprint_evidence({"blueprint_required": True}, source="local") is True
    assert d._row_requires_blueprint_evidence({}, source="workflow") is True
    assert d._row_requires_blueprint_evidence({}, source="local") is False


def test_path_is_within() -> None:
    from pathlib import Path

    assert d._path_is_within(Path("/tmp/a/b"), "/tmp/a") is True
    assert d._path_is_within(Path("/tmp/a"), "/tmp/a") is True
    assert d._path_is_within(Path("/tmp/other"), "/tmp/a") is False
    assert d._path_is_within(Path("/tmp/a"), "") is False


def test_blueprint_reference_values() -> None:
    details = {"blueprint_id": " id ", "blueprint_path": "p", "runtime_blueprint_path": ""}
    assert d._blueprint_reference_values(details) == ("id", "p", "")


def test_task_identity_tokens() -> None:
    assert d._task_identity_tokens("t1", {"pm_task_id": "pm"}) == {"t1", "pm"}
    assert d._task_identity_tokens("", {}) == set()


def test_payload_task_identity_values_nested() -> None:
    payload = {
        "task_id": "t1",
        "context": {"pm_task_id": "pm"},
        "task_updates": [{"id": "u1"}],
    }
    values = d._payload_task_identity_values(payload)
    assert {"t1", "pm", "u1"}.issubset(values)


def test_blueprint_payload_is_traceability_only() -> None:
    assert d._blueprint_payload_is_traceability_only({"traceability_only": True}) is True
    assert d._blueprint_payload_is_traceability_only({"source": "pm_dispatch.traceability.x"}) is True
    assert d._blueprint_payload_is_traceability_only({}) is False


def test_blueprint_handoff_missing_fields() -> None:
    assert d._blueprint_handoff_missing_fields({}) == [
        "target_files",
        "acceptance_criteria",
        "execution_checklist",
    ]
    complete = {
        "target_files": ["f"],
        "acceptance_criteria": ["a"],
        "execution_checklist": ["s"],
    }
    assert d._blueprint_handoff_missing_fields(complete) == []


def test_blueprint_payload_is_handoff_ready() -> None:
    complete = {
        "target_files": ["f"],
        "acceptance_criteria": ["a"],
        "execution_checklist": ["s"],
    }
    assert d._blueprint_payload_is_handoff_ready(complete) is True
    assert d._blueprint_payload_is_handoff_ready({}) is False
    blocked = dict(complete, contract_completeness={"handoff_ready": False})
    assert d._blueprint_payload_is_handoff_ready(blocked) is False


def test_blueprint_payload_matches_task() -> None:
    payload = {
        "task_id": "t1",
        "target_files": ["f"],
        "acceptance_criteria": ["a"],
        "execution_checklist": ["s"],
    }
    assert d._blueprint_payload_matches_task(payload, {"t1"}) is True
    assert d._blueprint_payload_matches_task(payload, {"other"}) is False
    assert d._blueprint_payload_matches_task(payload, set()) is False
    failed = dict(payload, status="failed")
    assert d._blueprint_payload_matches_task(failed, {"t1"}) is False


def test_worker_id_from_row() -> None:
    assert d._worker_id_from_row({"id": "i"}) == "i"
    assert d._worker_id_from_row({"worker_id": "w"}) == "w"
    assert d._worker_id_from_row({"name": "n"}) == "n"
    assert d._worker_id_from_row({}) == ""


def test_worker_row_matches_id() -> None:
    assert d._worker_row_matches_id({"id": "i"}, "i") is True
    assert d._worker_row_matches_id({"name": "n"}, "n") is True
    assert d._worker_row_matches_id({"id": "i"}, "x") is False
    assert d._worker_row_matches_id({"id": "i"}, "") is False


def test_worker_payload_from_object_dict() -> None:
    assert d._worker_payload_from_object({"id": "x"}) == {"id": "x"}


def test_worker_payload_from_object_attrs() -> None:
    worker = type("W", (), {"id": "w1", "name": "n", "status": "idle", "healthy": True})()
    payload = d._worker_payload_from_object(worker)
    assert payload["id"] == "w1"
    assert payload["status"] == "idle"


def test_worker_rows_from_payload_list() -> None:
    payload = {"workers": [{"id": "w1"}, {"no_id": True}, {"worker_id": "w2"}]}
    rows = d._worker_rows_from_payload(payload)
    assert [d._worker_id_from_row(r) for r in rows] == ["w1", "w2"]


def test_worker_rows_from_payload_dict_keyed() -> None:
    payload = {"workers": {"w1": {"status": "idle"}}}
    rows = d._worker_rows_from_payload(payload)
    assert rows[0]["id"] == "w1"


def test_cancel_success_payload_bool() -> None:
    assert d._cancel_success_payload("t1", True) == {"ok": True, "task_id": "t1"}
    assert d._cancel_success_payload("t1", False) is None


def test_cancel_success_payload_dict() -> None:
    assert d._cancel_success_payload("t1", {"cancelled": True}) == {
        "cancelled": True,
        "ok": True,
        "task_id": "t1",
    }
    assert d._cancel_success_payload("t1", {"status": "running"}) is None


def test_cancel_failure_detail() -> None:
    assert d._cancel_failure_detail({"error": "no"}) == "no"
    assert d._cancel_failure_detail({}) == "Task cannot be cancelled"
    assert d._cancel_failure_detail(None) == "Task cannot be cancelled"


def test_task_row_matches_id() -> None:
    row = {"id": "i", "metadata": {"pm_task_id": "pm"}}
    assert d._task_row_matches_id(row, "i") is True
    assert d._task_row_matches_id(row, "pm") is True
    assert d._task_row_matches_id(row, "nope") is False
    assert d._task_row_matches_id(row, "") is False


def test_is_workflow_shell_task() -> None:
    shell = {"id": "task-abc-director", "metadata": {}}
    assert d._is_workflow_shell_task(shell) is True
    real = {"id": "task-abc-director", "pm_task_id": "pm", "metadata": {}}
    assert d._is_workflow_shell_task(real) is False
    other = {"id": "regular", "metadata": {}}
    assert d._is_workflow_shell_task(other) is False


def test_merge_task_rows_by_identity_merges_overlay() -> None:
    primary = [{"id": "t1", "status": "PENDING", "metadata": {"a": 1}}]
    overlay = [{"id": "t1", "status": "RUNNING", "metadata": {"b": 2}}]
    merged = d._merge_task_rows_by_identity(primary, overlay)
    assert len(merged) == 1
    assert merged[0]["status"] == "RUNNING"
    assert merged[0]["metadata"] == {"a": 1, "b": 2}


def test_merge_task_rows_by_identity_appends_new() -> None:
    primary = [{"id": "t1"}]
    overlay = [{"id": "t2"}]
    merged = d._merge_task_rows_by_identity(primary, overlay)
    assert {d._task_id_from_row(r) for r in merged} == {"t1", "t2"}


def test_merge_task_rows_by_identity_empty_sides() -> None:
    assert d._merge_task_rows_by_identity([], [{"id": "x"}]) == [{"id": "x"}]
    assert d._merge_task_rows_by_identity([{"id": "x"}], []) == [{"id": "x"}]


def test_task_row_from_object_dict() -> None:
    out = d._task_row_from_object({"id": "x", "metadata": {}})
    assert out["id"] == "x"
    assert out["metadata"]["projection_source"] == "director_local"


def test_task_rows_from_local_tasks() -> None:
    rows = d._task_rows_from_local_tasks([{"id": "a", "metadata": {}}, {"id": "b", "metadata": {}}])
    assert [r["id"] for r in rows] == ["a", "b"]


def test_task_response_from_row() -> None:
    row = {
        "id": "t1",
        "subject": "subj",
        "description": "desc",
        "status": "running",
        "priority": "high",
        "claimed_by": "w1",
        "metadata": {},
    }
    resp = d._task_response_from_row(row)
    assert resp.id == "t1"
    assert resp.status == "RUNNING"
    assert resp.worker == "w1"


def test_director_snapshot_status_and_count() -> None:
    snap = type("S", (), {"status": "completed", "tasks": [1, 2, 3]})()
    assert d._director_snapshot_status(snap) == "completed"
    assert d._director_snapshot_task_count(snap) == 3
    assert d._director_snapshot_task_count(object()) == 0


def test_director_orchestration_response() -> None:
    snap = type("S", (), {"run_id": "r1", "status": "running", "workspace": "ws", "tasks": [1]})()
    resp = d._director_orchestration_response(snap)
    assert resp.run_id == "r1"
    assert resp.status == "running"
    assert resp.tasks_queued == 1
    assert resp.message == "Status: running"


def test_role_payload_case_insensitive() -> None:
    payload = {"roles": {"Director": {"ready": True}}}
    assert d._role_payload(payload, "director") == {"ready": True}
    assert d._role_payload(payload, "pm") == {}


def test_worker_diagnostics_from_workers() -> None:
    workers = [
        {"status": "idle"},
        {"status": "busy", "current_task_id": "t1"},
        {"status": "error", "healthy": False},
    ]
    section = d._worker_diagnostics_from_workers(workers)
    assert section.total == 3
    assert section.idle == 1
    assert section.busy == 1
    assert section.unhealthy == 1
    assert section.healthy == 2
    assert section.active_task_ids == ["t1"]
    assert section.ok is False


def test_director_diagnostic_issues_no_tasks() -> None:
    from polaris.delivery.http.v2.director_models import (
        DirectorDiagnosticsLLMSection,
        DirectorDiagnosticsStatusSection,
        DirectorDiagnosticsTaskSection,
        DirectorDiagnosticsWorkerSection,
    )

    status_section = DirectorDiagnosticsStatusSection(ok=True, state="IDLE", running=False)
    task_section = DirectorDiagnosticsTaskSection(ok=False, source="empty", total=0)
    worker_section = DirectorDiagnosticsWorkerSection(ok=False, total=0)
    llm_section = DirectorDiagnosticsLLMSection(ok=True, state="ready")
    issues = d._director_diagnostic_issues(status_section, task_section, worker_section, llm_section)
    assert "director_no_tasks" in issues
    assert "director_no_workers" in issues


def test_director_execution_blockers_llm_not_ready() -> None:
    from polaris.delivery.http.v2.director_models import (
        DirectorDiagnosticsLLMSection,
        DirectorDiagnosticsStatusSection,
        DirectorDiagnosticsTaskSection,
        DirectorDiagnosticsWorkerSection,
    )

    status_section = DirectorDiagnosticsStatusSection(ok=True, state="IDLE", running=False)
    task_section = DirectorDiagnosticsTaskSection(
        ok=True, source="workflow", total=1, ready_to_execute=1, ready_task_ids=["t1"]
    )
    worker_section = DirectorDiagnosticsWorkerSection(ok=True, total=1, idle=1)
    llm_section = DirectorDiagnosticsLLMSection(ok=False, state="blocked")
    blockers = d._director_execution_blockers(status_section, task_section, worker_section, llm_section)
    assert blockers == ["director_llm_not_ready"]
