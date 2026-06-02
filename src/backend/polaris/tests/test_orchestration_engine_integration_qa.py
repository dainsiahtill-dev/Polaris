import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_orchestration_engine():
    backend_root = Path(__file__).resolve().parents[1]
    for entry in (str(backend_root),):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return importlib.import_module("polaris.delivery.cli.pm.orchestration_engine")


def test_pm_workflow_wait_timeout_scales_above_short_director_result_timeout():
    mod = _load_orchestration_engine()

    timeout_seconds = mod._pm_workflow_wait_timeout_seconds(
        600,
        {
            "execution_mode": "parallel",
            "max_parallel_tasks": 3,
            "ready_timeout_seconds": 30,
            "claim_timeout_seconds": 30,
            "phase_timeout_seconds": 900,
            "complete_timeout_seconds": 30,
            "task_timeout_seconds": 3600,
        },
        task_count=3,
    )

    assert timeout_seconds > 600
    assert timeout_seconds == 3600.0


def test_pm_workflow_wait_timeout_keeps_disabled_timeout_disabled():
    mod = _load_orchestration_engine()

    assert mod._pm_workflow_wait_timeout_seconds(None, {}, task_count=3) is None


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
    assert Path(payload["runtime_result_path"]).is_file()


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
        verify_runner=lambda workspace: (True, "Integration verification passed: npm run test -- --watch=false", []),
    )

    assert payload["ran"] is True
    assert payload["passed"] is True
    assert payload["reason"] == "integration_qa_passed"
    assert payload["evidence_grade"] == "real_command_passed"
    stored = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert stored["passed"] is True
    assert stored["summary"] == "Integration verification passed: npm run test -- --watch=false"
    assert stored["evidence_grade"] == "real_command_passed"
    runtime_stored = json.loads(Path(payload["runtime_result_path"]).read_text(encoding="utf-8"))
    assert runtime_stored["reason"] == "integration_qa_passed"
    assert runtime_stored["evidence_grade"] == "real_command_passed"
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    qa_event = next(item for item in events if item.get("name") == "integration_qa_complete")
    qa_output = qa_event.get("output") if isinstance(qa_event.get("output"), dict) else {}
    assert qa_output["evidence_grade"] == "real_command_passed"
    assert qa_output["reason"] == "integration_qa_passed"
    dialogue_events = [
        json.loads(line)
        for line in (tmp_path / "dialogue.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    qa_dialogue = next(item for item in dialogue_events if item.get("speaker") == "QA")
    assert qa_dialogue["meta"]["evidence_grade"] == "real_command_passed"


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
    assert payload["evidence_grade"] == "real_command_failed"
    assert payload["errors"] == ["missing symbol"]
    assert Path(payload["runtime_result_path"]).is_file()


def test_integration_qa_marks_missing_node_dependencies_as_blocked(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    payload = mod.run_post_dispatch_integration_qa(
        args=SimpleNamespace(integration_qa=True),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00003b",
        iteration=3,
        tasks=[
            {
                "id": "TASK-A",
                "assigned_to": "Director",
                "status": "done",
            }
        ],
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        verify_runner=lambda workspace: (
            False,
            "Node dependencies are declared but not installed",
            ["Set KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK=1 to allow static fallback."],
        ),
    )

    assert payload["ran"] is True
    assert payload["passed"] is False
    assert payload["reason"] == "integration_qa_failed"
    assert payload["evidence_grade"] == "blocked_missing_dependencies"


def test_integration_qa_uses_default_runner_when_verify_runner_missing(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    shared_quality = importlib.import_module("polaris.cells.orchestration.pm_planning.public.service")
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
    assert Path(payload["runtime_result_path"]).is_file()


def test_workflow_dispatch_defers_integration_qa_until_terminal_state(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    workflow_config_mod = importlib.import_module("polaris.cells.orchestration.workflow_runtime.public.service")

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
        lambda workflow_input, config, **kwargs: SimpleNamespace(
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
    assert Path(qa_result["runtime_result_path"]).is_file()
    persisted = json.loads(Path(qa_result["result_path"]).read_text(encoding="utf-8"))
    assert persisted["reason"] == "workflow_execution_incomplete"
    runtime_persisted = json.loads(Path(qa_result["runtime_result_path"]).read_text(encoding="utf-8"))
    assert runtime_persisted["reason"] == "workflow_execution_incomplete"


def test_workflow_dispatch_marks_parent_failure_as_director_failure(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    workflow_config_mod = importlib.import_module("polaris.cells.orchestration.workflow_runtime.public.service")

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
        lambda workflow_input, config, **kwargs: SimpleNamespace(
            submitted=True,
            status="submitted",
            workflow_id="wf-failed",
            workflow_run_id="wf-failed",
            error="",
            details={},
        ),
    )
    monkeypatch.setattr(
        mod,
        "wait_for_workflow_completion_sync",
        lambda workflow_id, timeout_seconds, config: {
            "ok": True,
            "workflow_id": workflow_id,
            "status": "failed",
            "result": {"error": "PM task contract rejected"},
        },
    )
    monkeypatch.setattr(
        mod,
        "get_workflow_runtime_status",
        lambda workspace_full, cache_root_full: {"workflow_status": "failed"},
    )
    monkeypatch.setattr(
        mod,
        "summarize_workflow_tasks",
        lambda workflow_status, base_tasks, workspace, cache_root: {
            "tasks": [{"id": "TASK-001", "assigned_to": "Director", "status": "pending"}],
            "total": 1,
            "state": "queued",
        },
    )
    monkeypatch.setattr(mod, "persist_pm_payload", lambda **kwargs: None)
    monkeypatch.setattr(mod, "emit_event", lambda *args, **kwargs: None)

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
        run_id="pm-run-failed",
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
    assert outcome["exit_code"] == 1
    director_result = json.loads((tmp_path / "director.result.json").read_text(encoding="utf-8"))
    assert director_result["status"] == "failed"
    qa_result = outcome["integration_qa_result"]
    assert qa_result["ran"] is False
    assert qa_result["reason"] == "director_failures_present"


def test_workflow_dispatch_preserves_nested_failure_when_tasks_and_qa_pass(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    workflow_config_mod = importlib.import_module("polaris.cells.orchestration.workflow_runtime.public.service")

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
        lambda workflow_input, config, **kwargs: SimpleNamespace(
            submitted=True,
            status="submitted",
            workflow_id="wf-failed-empty",
            workflow_run_id="wf-failed-empty",
            error="",
            details={
                "final": {
                    "ok": True,
                    "workflow_id": "wf-failed-empty",
                    "status": "completed",
                    "result": {
                        "status": "completed",
                        "mode": "sequential",
                        "result": {
                            "run_id": "pm-run-failed-empty",
                            "tasks": [],
                            "director_status": "failed",
                            "qa_status": "director_failed",
                            "metadata": {"task_count": 1},
                        },
                    },
                }
            },
        ),
    )
    monkeypatch.setattr(
        mod,
        "wait_for_workflow_completion_sync",
        lambda workflow_id, timeout_seconds, config: {
            "ok": True,
            "workflow_id": workflow_id,
            "status": "failed",
            "result": {"error": ""},
        },
    )
    monkeypatch.setattr(
        mod,
        "get_workflow_runtime_status",
        lambda workspace_full, cache_root_full: {"workflow_status": "failed"},
    )
    monkeypatch.setattr(
        mod,
        "summarize_workflow_tasks",
        lambda workflow_status, base_tasks, workspace, cache_root: {
            "tasks": [{"id": "TASK-001", "assigned_to": "Director", "status": "completed"}],
            "total": 1,
            "completed": 1,
            "failed": 0,
            "blocked": 0,
            "active": 0,
            "pending": 0,
            "state": "completed",
        },
    )
    monkeypatch.setattr(mod, "persist_pm_payload", lambda **kwargs: None)
    monkeypatch.setattr(mod, "emit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mod,
        "run_post_dispatch_integration_qa",
        lambda **kwargs: {
            "ran": True,
            "passed": True,
            "reason": "integration_qa_passed",
            "result_path": "",
            "runtime_result_path": "",
        },
    )

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
        run_id="pm-run-failed-empty",
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
    assert outcome["exit_code"] == 1
    director_result = json.loads((tmp_path / "director.result.json").read_text(encoding="utf-8"))
    assert director_result["status"] == "failed"
    assert director_result["successes"] == 1
    assert director_result["failures"] == 1
    assert director_result["error"] == "director_failed"
    from polaris.cells.runtime.projection.internal.workflow_status import workflow_state_path

    state_path = workflow_state_path(str(workspace), str(cache_root))
    workflow_state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    assert workflow_state["workflow_status"] == "failed"
    assert workflow_state["stage"] == "failed"
    assert workflow_state["director_status"] == "failed"
    assert workflow_state["qa_status"] == "integration_qa_passed"
    assert workflow_state["details"]["integration_qa_result"]["passed"] is True
    assert workflow_state["details"]["director_result"]["failures"] == 1
    assert workflow_state["details"]["director_result"]["error"] == "director_failed"
    assert "reconciled_from_task_and_qa_evidence" not in outcome["engine_dispatch"]["summary"]
    nested_result = workflow_state["details"]["details"]["final"]["result"]["result"]
    assert nested_result["director_status"] == "failed"
    assert nested_result["qa_status"] == "director_failed"


def test_workflow_dispatch_projects_nested_director_failure_result(tmp_path, monkeypatch):
    mod = _load_orchestration_engine()
    workflow_config_mod = importlib.import_module("polaris.cells.orchestration.workflow_runtime.public.service")

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
        lambda workflow_input, config, **kwargs: SimpleNamespace(
            submitted=True,
            status="completed",
            workflow_id="wf-nested-failed",
            workflow_run_id="wf-nested-failed",
            error="",
            details={
                "final": {
                    "ok": True,
                    "workflow_id": "wf-nested-failed",
                    "status": "completed",
                    "result": {
                        "status": "completed",
                        "mode": "sequential",
                        "result": {
                            "run_id": "pm-run-nested",
                            "tasks": [],
                            "director_status": "failed",
                            "qa_status": "director_failed",
                            "metadata": {"task_count": 1},
                        },
                    },
                }
            },
        ),
    )
    monkeypatch.setattr(
        mod,
        "wait_for_workflow_completion_sync",
        lambda workflow_id, timeout_seconds, config: (_ for _ in ()).throw(
            AssertionError("final wait payload should be reused")
        ),
    )
    monkeypatch.setattr(
        mod,
        "get_workflow_runtime_status",
        lambda workspace_full, cache_root_full: {"workflow_status": "completed"},
    )
    monkeypatch.setattr(
        mod,
        "summarize_workflow_tasks",
        lambda workflow_status, base_tasks, workspace, cache_root: {
            "tasks": [{"id": "TASK-001", "assigned_to": "Director", "status": "pending"}],
            "total": 1,
            "state": "queued",
        },
    )
    monkeypatch.setattr(mod, "persist_pm_payload", lambda **kwargs: None)
    monkeypatch.setattr(mod, "emit_event", lambda *args, **kwargs: None)

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
        run_id="pm-run-nested",
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
    assert outcome["exit_code"] == 1
    director_result = json.loads((tmp_path / "director.result.json").read_text(encoding="utf-8"))
    assert director_result["status"] == "failed"
    assert director_result["failures"] == 1
    assert director_result["error"] == "director_failed"
    assert outcome["engine_dispatch"]["summary"]["workflow_domain_director_status"] == "failed"
    qa_result = outcome["integration_qa_result"]
    assert qa_result["ran"] is False
    assert qa_result["reason"] == "director_failures_present"
