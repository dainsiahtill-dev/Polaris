import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_orchestration_engine():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "src" / "backend" / "scripts"
    project_root = repo_root / "src" / "backend"
    loop_module_dir = project_root / "core" / "polaris_loop"
    for entry in (str(scripts_dir), str(project_root), str(loop_module_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return importlib.import_module("pm.orchestration_engine")


def test_integration_qa_skips_when_director_tasks_pending(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = mod.run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00001",
        iteration=1,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "needs_continue",
            }
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
    )

    assert payload["ran"] is False
    assert payload["passed"] is None
    assert payload["reason"] == "pending_director_tasks"
    assert Path(payload["result_path"]).is_file()


def test_integration_qa_runs_and_passes_when_all_director_tasks_done(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = mod.run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00002",
        iteration=2,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        verify_runner=lambda workspace: (True, "integration checks passed", []),
    )

    assert payload["ran"] is True
    assert payload["passed"] is True
    assert payload["reason"] == "integration_qa_passed"
    stored = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert stored["passed"] is True
    assert stored["summary"] == "integration checks passed"


def test_integration_qa_runs_and_fails_on_verify_error(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = mod.run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00003",
        iteration=3,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            },
            {
                "id": "TASK-B",
                "assigned_to": "Director",
                "status": "done",
            },
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        verify_runner=lambda workspace: (False, "compile failed", ["missing symbol"]),
    )

    assert payload["ran"] is True
    assert payload["passed"] is False
    assert payload["reason"] == "integration_qa_failed"
    assert payload["errors"] == ["missing symbol"]


def test_integration_qa_uses_default_runner_when_verify_runner_missing(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    shared_quality = importlib.import_module("app.orchestration.shared_quality")
    monkeypatch.setattr(
        shared_quality,
        "run_integration_verify_runner",
        lambda workspace: (True, "default runner passed", []),
    )

    payload = mod.run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00004",
        iteration=4,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        verify_runner=None,
    )

    assert payload["ran"] is True
    assert payload["passed"] is True
    assert payload["reason"] == "integration_qa_passed"


def test_integration_qa_skips_for_docs_only_stage(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = mod.run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00005",
        iteration=5,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
                "target_files": ["workspace/docs/product/architecture.md"],
                "context_files": ["workspace/docs/product/requirements.md"],
            }
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        docs_stage={"enabled": True, "active_doc_path": "workspace/docs/product/requirements.md"},
    )

    assert payload["ran"] is False
    assert payload["passed"] is None
    assert payload["reason"] == "docs_stage_docs_only"


def test_workflow_dispatch_defers_integration_qa_until_terminal_state(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    workflow_config_mod = importlib.import_module("app.orchestration.config")

    monkeypatch.setattr(
        workflow_config_mod.WorkflowConfig,
        "from_env",
        classmethod(
            lambda cls, force_enable=False: SimpleNamespace(
                task_queue="unit-queue",
                namespace="unit-namespace",
            )
        ),
    )
    monkeypatch.setattr(
        mod,
        "resolve_director_dispatch_tasks",
        lambda workspace_full, tasks: (list(tasks), {"source": "unit"}),
    )
    monkeypatch.setattr(
        mod,
        "submit_pm_workflow_sync",
        lambda workflow_input, config: SimpleNamespace(
            submitted=True,
            status="submitted",
            workflow_id="wf-001",
            workflow_run_id="wf-run-001",
            error="",
            details={},
        ),
    )
    monkeypatch.setattr(
        mod,
        "wait_for_workflow_completion_sync",
        lambda workflow_id, timeout_seconds, config: {"error": "workflow_wait_timeout"},
    )
    monkeypatch.setattr(
        mod,
        "get_workflow_runtime_status",
        lambda workspace_full, cache_root_full: {"workflow_status": "running"},
    )
    monkeypatch.setattr(
        mod,
        "summarize_workflow_tasks",
        lambda workflow_status, base_tasks, workspace, cache_root: {
            "tasks": [{"id": "TASK-001", "status": "in_progress"}],
            "total": 1,
            "state": "running",
        },
    )
    monkeypatch.setattr(mod, "persist_pm_payload", lambda **kwargs: None)
    monkeypatch.setattr(mod, "emit_event", lambda *args, **kwargs: None)

    qa_called = {"value": False}

    def _unexpected_qa_call(**kwargs):
        qa_called["value"] = True
        raise AssertionError("run_post_dispatch_integration_qa should not execute")

    monkeypatch.setattr(mod, "run_post_dispatch_integration_qa", _unexpected_qa_call)

    class _Engine:
        def update_role_status(self, *args, **kwargs):
            return None

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "cache"
    workspace.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        director_result_timeout=1,
        director_type="auto",
        director_path="src/backend/scripts/loop-director.py",
        director_timeout=1,
        director_model="",
        prompt_profile="",
        director_workflow_execution_mode="parallel",
        director_max_parallel_tasks=1,
        director_ready_timeout_seconds=1,
        director_claim_timeout_seconds=1,
        director_phase_timeout_seconds=1,
        director_complete_timeout_seconds=1,
        director_task_timeout_seconds=1,
        integration_qa=True,
    )

    outcome = mod._run_dispatch_pipeline_with_workflow(
        args=args,
        engine=_Engine(),
        workspace_full=str(workspace),
        cache_root_full=str(cache_root),
        run_dir=str(run_dir),
        run_id="pm-run-001",
        iteration=1,
        normalized={
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "demo",
                    "assigned_to": "Director",
                    "status": "todo",
                }
            ]
        },
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        runtime_pm_tasks_full=str(tmp_path / "runtime_pm_tasks.json"),
        pm_out_full=str(tmp_path / "pm_out.json"),
        run_pm_tasks=str(tmp_path / "run_pm_tasks.json"),
        run_director_result=str(tmp_path / "director.result.json"),
        docs_stage={"enabled": False},
    )

    assert outcome["used"] is True
    assert qa_called["value"] is False
    qa_result = outcome["integration_qa_result"]
    assert qa_result["ran"] is False
    assert qa_result["reason"] == "workflow_execution_incomplete"
    assert Path(qa_result["result_path"]).is_file()
    persisted = json.loads(Path(qa_result["result_path"]).read_text(encoding="utf-8"))
    assert persisted["reason"] == "workflow_execution_incomplete"
