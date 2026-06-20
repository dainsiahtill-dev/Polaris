"""Characterization tests for the Director task-diagnostics + blueprint-IO cluster.

Freezes the current behavior of ``_task_diagnostics_from_rows``,
``_blueprint_artifact_state`` and the blueprint payload loaders prior to their
relocation into sibling modules. All references go through the ``director``
module object so the suite is agnostic to where the helpers physically live, and
patchable collaborators (``BlueprintPersistence``) are patched on the ``director``
module namespace exactly as the production routes resolve them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.delivery.http.v2 import director as d


def test_task_diagnostics_counts_by_status() -> None:
    rows: list[dict[str, Any]] = [
        {"id": "t1", "status": "pending", "metadata": {}},
        {"id": "t2", "status": "claimed", "metadata": {}},
        {"id": "t3", "status": "running", "metadata": {}},
        {"id": "t4", "status": "failed", "metadata": {}},
        {"id": "t5", "status": "completed", "metadata": {}},
        {"id": "t6", "status": "cancelled", "metadata": {}},
    ]
    section = d._task_diagnostics_from_rows(rows, "local")
    assert section.total == 6
    assert section.pending == 1
    assert section.claimed == 1
    # CLAIMED also increments running.
    assert section.running == 2
    assert section.failed == 1
    assert section.completed == 1
    assert section.cancelled == 1
    assert section.ready_task_ids == ["t1"]


def test_task_diagnostics_blocks_pending_with_unmet_failed_dependency() -> None:
    rows: list[dict[str, Any]] = [
        {"id": "dep", "status": "failed", "metadata": {}},
        {"id": "t1", "status": "pending", "depends_on": ["dep"], "metadata": {}},
    ]
    section = d._task_diagnostics_from_rows(rows, "local")
    assert section.failed == 1
    assert section.blocked == 1
    assert "t1" in section.blocked_task_ids
    assert section.ready_task_ids == []


def test_task_diagnostics_ready_when_dependency_completed() -> None:
    rows: list[dict[str, Any]] = [
        {"id": "dep", "status": "completed", "metadata": {}},
        {"id": "t1", "status": "pending", "depends_on": ["dep"], "metadata": {}},
    ]
    section = d._task_diagnostics_from_rows(rows, "local")
    assert section.completed == 1
    assert section.blocked == 0
    assert section.ready_task_ids == ["t1"]


def test_task_diagnostics_empty_rows() -> None:
    section = d._task_diagnostics_from_rows([], "empty")
    assert section.total == 0
    assert section.ok is False
    assert section.source == "empty"


def test_blueprint_artifact_state_missing_without_evidence(monkeypatch, tmp_path) -> None:
    # No blueprint references on the task, and no matching payloads -> missing.
    class _NoPayloads:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def list_all(self) -> list[str]:
            return []

    monkeypatch.setattr(d, "BlueprintPersistence", _NoPayloads)
    state = d._blueprint_artifact_state(
        workspace=str(tmp_path),
        cache_root="",
        task_id="t1",
        details={},
    )
    assert state == "missing"


def test_blueprint_artifact_state_valid_via_matching_payload(monkeypatch, tmp_path) -> None:
    matching_payload = {
        "task_id": "t1",
        "target_files": ["f"],
        "acceptance_criteria": ["a"],
        "execution_checklist": ["s"],
    }

    class _OnePayload:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def list_all(self) -> list[str]:
            return ["bp-1"]

        def load(self, blueprint_id: str) -> dict[str, Any]:
            return matching_payload

    monkeypatch.setattr(d, "BlueprintPersistence", _OnePayload)
    state = d._blueprint_artifact_state(
        workspace=str(tmp_path),
        cache_root="",
        task_id="t1",
        details={},
    )
    assert state == "valid"


def test_blueprint_artifact_state_invalid_when_referenced_id_does_not_match(monkeypatch, tmp_path) -> None:
    class _WrongPayload:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def load(self, blueprint_id: str) -> dict[str, Any]:
            return {
                "task_id": "other",
                "target_files": ["f"],
                "acceptance_criteria": ["a"],
                "execution_checklist": ["s"],
            }

    monkeypatch.setattr(d, "BlueprintPersistence", _WrongPayload)
    state = d._blueprint_artifact_state(
        workspace=str(tmp_path),
        cache_root="",
        task_id="t1",
        details={"blueprint_id": "bp-1"},
    )
    assert state == "invalid"


def test_load_blueprint_payload_by_id_none_on_empty() -> None:
    assert d._load_blueprint_payload_by_id("ws", "") is None


def test_load_blueprint_payload_by_path_reads_json(tmp_path) -> None:
    payload = {"hello": "world"}
    bp = tmp_path / "bp.json"
    bp.write_text(json.dumps(payload), encoding="utf-8")
    loaded = d._load_blueprint_payload_by_path(str(tmp_path), "", str(bp))
    assert loaded == payload


def test_resolve_blueprint_path_within_workspace(tmp_path) -> None:
    target = tmp_path / "sub" / "bp.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    resolved = d._resolve_blueprint_path(str(tmp_path), "", "sub/bp.json")
    assert resolved == Path(target).resolve()


def test_resolve_blueprint_path_outside_workspace_rejected(tmp_path) -> None:
    assert d._resolve_blueprint_path(str(tmp_path), "", "/etc/passwd") is None
