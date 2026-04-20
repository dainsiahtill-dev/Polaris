from __future__ import annotations

import os
import sys
from types import SimpleNamespace

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_ROOT = os.path.join(BACKEND_ROOT, "scripts")
CORE_ROOT = os.path.join(BACKEND_ROOT, "core", "polaris_loop")
for candidate in (BACKEND_ROOT, SCRIPTS_ROOT, CORE_ROOT):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight import (
    run_pre_dispatch_chief_engineer,  # noqa: E402
)
from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
    run_chief_engineer_preflight,  # noqa: E402
)
from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path  # noqa: E402


def test_run_pre_dispatch_chief_engineer_uses_direct_analysis_runner(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    captured: dict[str, object] = {}

    def _fake_runner(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": 1,
            "role": "ChiefEngineer",
            "ran": True,
            "hard_failure": False,
            "reason": "chief_engineer_updated",
            "summary": "ChiefEngineer updated blueprint for 1 director task(s).",
            "task_update_count": 1,
            "task_updates": [{"task_id": "TASK-A"}],
            "task_update_map": {"TASK-A": {"task_id": "TASK-A"}},
            "stats": {"director_task_count": 1},
        }

    run_events = resolve_artifact_path(
        str(tmp_path),
        "",
        "runtime/events/runtime.events.jsonl",
    )
    dialogue_full = resolve_artifact_path(
        str(tmp_path),
        "",
        "runtime/events/dialogue.transcript.jsonl",
    )

    result = run_pre_dispatch_chief_engineer(
        args=SimpleNamespace(),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-100",
        pm_iteration=7,
        tasks=[{"id": "TASK-A", "assigned_to": "Director", "status": "todo"}],
        run_events=run_events,
        dialogue_full=dialogue_full,
        analysis_runner=_fake_runner,
    )

    assert captured["workspace_full"] == str(tmp_path)
    assert captured["pm_iteration"] == 7
    assert captured["tasks"][0]["id"] == "TASK-A"
    assert result["hard_failure"] is False
    assert result["task_update_count"] == 1
    assert str(result["blueprint_path"]).replace("\\", "/").endswith(
        "contracts/chief_engineer.blueprint.json"
    )
    assert str(result["runtime_blueprint_path"]).replace("\\", "/").endswith(
        "runtime/contracts/chief_engineer.blueprint.json"
    )


def test_dispatch_pipeline_run_chief_engineer_preflight_delegates_to_canonical_entry(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def _fake_preflight(**kwargs):
        captured.update(kwargs)
        return {"ran": True, "hard_failure": False, "summary": "ok"}

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.run_pre_dispatch_chief_engineer",
        _fake_preflight,
    )

    run_events = resolve_artifact_path(
        str(tmp_path),
        "",
        "runtime/events/runtime.events.jsonl",
    )
    dialogue_full = resolve_artifact_path(
        str(tmp_path),
        "",
        "runtime/events/dialogue.transcript.jsonl",
    )

    result = run_chief_engineer_preflight(
        args=SimpleNamespace(),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(tmp_path / "run"),
        run_id="pm-101",
        pm_iteration=8,
        tasks=[{"id": "TASK-B"}],
        run_events=run_events,
        dialogue_full=dialogue_full,
    )

    assert result == {"ran": True, "hard_failure": False, "summary": "ok"}
    assert captured["pm_iteration"] == 8
    assert captured["tasks"][0]["id"] == "TASK-B"

