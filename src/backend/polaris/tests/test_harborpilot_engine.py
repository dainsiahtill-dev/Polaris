import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_loop_pm():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "scripts" / "loop-pm.py"
    if not module_path.is_file():
        raise RuntimeError(f"Failed to locate loop-pm.py: {module_path}")
    spec = importlib.util.spec_from_file_location("loop_pm", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-pm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_dispatch_director_tasks_with_multi_config(tmp_path):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-00001",
            "changed_files": [],
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="multi",
        max_directors=2,
        scheduling_policy="dag",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-00001",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-A",
                "title": "Task A",
                "goal": "Do A",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "dependencies": [],
            },
            {
                "id": "TASK-B",
                "title": "Task B",
                "goal": "Do B",
                "status": "todo",
                "priority": 2,
                "assigned_to": "Director",
                "dependencies": ["TASK-A"],
            },
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["summary"]["total"] == 2
    assert result["summary"]["successes"] == 2
    assert result["summary"]["failure_rate"] == 0.0
    assert result["summary"]["failure_rate_ok"] is True
    assert result["summary"]["degraded_to_single"] is True
    assert result["status_updates"] == {"TASK-A": "done", "TASK-B": "done"}
    assert len(result["summary"]["batches"]) == 2
    assert not result["hard_failure"]
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "completed"
    assert status_payload["roles"]["PM"]["status"] == "completed"
    assert status_payload["roles"]["Director"]["status"] == "completed"
    assert status_payload["roles"]["QA"]["status"] == "completed"


def test_engine_preflight_auto_creates_plan_and_dispatches(tmp_path):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-00011",
            "changed_files": [],
        }
        result_path = Path(args.director_result_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-00011",
        "pm_iteration": 11,
        "tasks": [
            {
                "id": "TASK-PLAN-AUTO",
                "title": "Task plan auto-create",
                "goal": "Exercise preflight auto-fix path",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["hard_failure"] is False
    assert result["summary"]["successes"] == 1
    preflight = result["summary"]["preflight"]
    assert preflight["ok"] is True
    assert "contracts/plan.md" in preflight.get("autofixed", [])
    assert Path(preflight["resolved_plan_path"]).is_file()
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "completed"


def test_engine_needs_continue_is_non_terminal(tmp_path):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "needs_continue",
            "task_id": task_id,
            "run_id": "pm-00012",
            "changed_files": ["src/server/index.ts"],
            "continue_reason": "Missing target files: ['src/server/rooms.ts']",
            "build_round_index": 1,
            "build_round_budget": 4,
            "progress_delta": {"trend": "improving", "is_stalled": False},
            "soft_check": {
                "missing_targets": ["src/server/rooms.ts"],
                "unresolved_imports": [],
                "verify_ready": False,
            },
            "last_missing_targets": ["src/server/rooms.ts"],
            "last_unresolved_imports": [],
        }
        result_path = Path(args.director_result_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
        qa_mode="blocking",
    )

    payload = {
        "run_id": "pm-00012",
        "pm_iteration": 12,
        "tasks": [
            {
                "id": "TASK-NEEDS-CONTINUE",
                "title": "Continue task",
                "goal": "Complete missing modules",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
        progress_payload_paths=[str(pm_tasks_path)],
    )

    assert result["hard_failure"] is False
    assert result["status_updates"]["TASK-NEEDS-CONTINUE"] == "needs_continue"
    assert result["summary"]["needs_continue"] == 1
    persisted = json.loads(pm_tasks_path.read_text(encoding="utf-8"))
    persisted_task = persisted["tasks"][0]
    assert persisted_task["status"] == "needs_continue"
    assert persisted_task["build_round_index"] == 1
    assert persisted_task["last_missing_targets"] == ["src/server/rooms.ts"]
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "completed"


def test_engine_preflight_failure_is_reported(tmp_path):
    loop_pm = _load_loop_pm()
    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=lambda *a, **k: 0)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(tmp_path / "runtime.events.jsonl"),
        pm_task_path=str(tmp_path / "missing_pm_tasks.json"),
        plan_path=str(tmp_path / "missing_plan.md"),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-00002",
        "pm_iteration": 2,
        "tasks": [
            {
                "id": "TASK-X",
                "title": "Task X",
                "goal": "Do X",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
    )

    assert result["hard_failure"] is True
    preflight = result["summary"]["preflight"]
    assert preflight["ok"] is False
    assert "contracts/pm_tasks.contract.json" in preflight["missing"]
    assert "contracts/plan.md" not in preflight["missing"]
    assert "contracts/plan.md" in preflight.get("autofixed", [])
    assert Path(preflight["resolved_plan_path"]).is_file()
    assert result["records"] == []


def test_engine_qa_contract_failure_blocks_task(tmp_path, monkeypatch):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-10001",
            "changed_files": [],
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.delenv("KERNELONE_QA_UI_PLUGIN_ENABLED", raising=False)

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-10001",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-QA-FAIL",
                "title": "Task QA gate fail",
                "goal": "Require changed files gate",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "qa_contract": {
                    "task_type": "backend_api",
                    "hard_gates": [{"kind": "changed_files_min", "min": 1}],
                    "retry_policy": {"max_director_retries": 3},
                },
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["status_updates"]["TASK-QA-FAIL"] == "blocked"
    record = result["records"][0]
    assert record["error_code"] == "QA_CONTRACT_FAIL"
    assert record["qa_result"]["verdict"] == "FAIL"
    assert record["qa_retry_count"] == 1
    assert result["hard_failure"] is True
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "failed"
    assert status_payload["error"] == "DIRECTOR_DISPATCH_BLOCKED"


def test_engine_delivery_floor_fails_thin_stress_project(tmp_path, monkeypatch):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-floor-fail",
            "changed_files": ["src/main.go"],
            "patch_risk": {
                "factors": {"files_changed_count": 1, "lines_added": 8},
            },
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.setenv("KERNELONE_DELIVERY_FLOOR_ENABLED", "1")

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )
    stress_workspace = tmp_path / "polaris_stress" / "round-099-small-floor"
    stress_workspace.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": "pm-floor-fail",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-FLOOR-FAIL",
                "title": "Tiny delivery",
                "goal": "Implement minimal skeleton only",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "target_files": ["src/main.go"],
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(stress_workspace),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["hard_failure"] is True
    floor = result["summary"].get("delivery_floor") or {}
    assert floor.get("enabled") is True
    assert floor.get("passed") is False
    assert floor.get("scale") == "small"
    assert floor.get("metrics", {}).get("code_files") == 1
    assert floor.get("metrics", {}).get("code_lines") == 8
    assert floor.get("metrics", {}).get("test_files") == 0
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "failed"
    assert status_payload["error"] == "DELIVERY_FLOOR_NOT_MET"


def test_engine_delivery_floor_passes_substantive_stress_project(tmp_path, monkeypatch):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-floor-pass",
            "changed_files": [
                "src/main.go",
                "src/service.go",
                "tests/service_test.go",
            ],
            "patch_risk": {
                "factors": {"files_changed_count": 3, "lines_added": 72},
            },
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.setenv("KERNELONE_DELIVERY_FLOOR_ENABLED", "1")

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )
    stress_workspace = tmp_path / "polaris_stress" / "round-100-small-floor"
    stress_workspace.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": "pm-floor-pass",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-FLOOR-PASS",
                "title": "Substantive delivery",
                "goal": "Implement service with tests",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "target_files": ["src/main.go", "src/service.go", "tests/service_test.go"],
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(stress_workspace),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["hard_failure"] is False
    floor = result["summary"].get("delivery_floor") or {}
    assert floor.get("enabled") is True
    assert floor.get("passed") is True
    assert floor.get("metrics", {}).get("code_files") == 3
    assert floor.get("metrics", {}).get("code_lines") == 72
    assert floor.get("metrics", {}).get("test_files") == 1
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "completed"
    assert status_payload["error"] == ""


def test_engine_delivery_floor_uses_workspace_lines_and_target_files_when_result_is_sparse(
    tmp_path, monkeypatch
):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-floor-sparse",
            "changed_files": ["src/main.go"],
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.setenv("KERNELONE_DELIVERY_FLOOR_ENABLED", "1")

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )
    stress_workspace = tmp_path / "polaris_stress" / "round-101-small-floor"
    (stress_workspace / "src").mkdir(parents=True, exist_ok=True)
    (stress_workspace / "tests").mkdir(parents=True, exist_ok=True)
    (stress_workspace / "src" / "main.go").write_text(
        "package main\n\n" + "\n".join(f"func m{i}() {{}}" for i in range(30)) + "\n",
        encoding="utf-8",
    )
    (stress_workspace / "src" / "service.go").write_text(
        "package main\n\n" + "\n".join(f"func s{i}() {{}}" for i in range(25)) + "\n",
        encoding="utf-8",
    )
    (stress_workspace / "tests" / "service_test.go").write_text(
        "package tests\n\n" + "\n".join(f"func TestA{i}() {{}}" for i in range(10)) + "\n",
        encoding="utf-8",
    )

    payload = {
        "run_id": "pm-floor-sparse",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-FLOOR-SPARSE",
                "title": "Sparse director result",
                "goal": "Fallback should recover delivery-floor metrics",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "target_files": ["src/main.go", "src/service.go", "tests/service_test.go"],
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(stress_workspace),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["hard_failure"] is False
    floor = result["summary"].get("delivery_floor") or {}
    assert floor.get("enabled") is True
    assert floor.get("passed") is True
    assert floor.get("metrics", {}).get("code_files") == 3
    assert int(floor.get("metrics", {}).get("code_lines") or 0) >= 40
    assert floor.get("metrics", {}).get("test_files") == 1
    status_payload = json.loads(runtime_engine_status.read_text(encoding="utf-8"))
    assert status_payload["phase"] == "completed"
    assert status_payload["error"] == ""


def test_engine_ui_task_without_plugin_uses_rules_v1(tmp_path, monkeypatch):
    """UI tasks without UI plugin gracefully degrade to rules_v1 QA instead of failing."""
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-10002",
            "changed_files": ["src/app.tsx"],
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.delenv("KERNELONE_QA_UI_PLUGIN_ENABLED", raising=False)

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-10002",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-UI-INC",
                "title": "UI task without plugin",
                "goal": "Render a canvas background",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "qa_contract": {
                    "task_type": "ui_canvas",
                    "hard_gates": ["director_status_success"],
                    "retry_policy": {"max_director_retries": 2},
                },
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
    )

    # UI task without plugin should gracefully degrade to rules_v1 and PASS
    assert result["status_updates"]["TASK-UI-INC"] == "done"
    record = result["records"][0]
    assert not record["error_code"]  # Empty string or None both mean no error
    assert record["qa_result"]["verdict"] == "PASS"
    assert "no_ui_plugin_configured" not in str(record.get("failure_detail", ""))
    assert not record.get("qa_coordination_pending")
    assert result["hard_failure"] is False


def test_engine_qa_failed_final_writes_human_queue(tmp_path, monkeypatch):
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-10003",
            "changed_files": [],
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.delenv("KERNELONE_QA_UI_PLUGIN_ENABLED", raising=False)

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-10003",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-QA-FINAL",
                "title": "QA final fail",
                "goal": "Trigger failed_final path",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "qa_retry_count": 1,
                "qa_contract": {
                    "task_type": "backend_api",
                    "hard_gates": [{"kind": "changed_files_min", "min": 1}],
                    "retry_policy": {"max_director_retries": 2},
                },
            }
        ],
    }

    run_dir = tmp_path / "run"
    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(run_dir),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
    )

    record = result["records"][0]
    assert record["pm_status"] == "blocked"
    assert record["error_code"] == "QA_FAILED_FINAL"
    assert record["qa_failed_final"] is True
    assert payload["tasks"][0]["qa_failed_final"] is True

    human_queue_path = run_dir / "engine" / "queues" / "human_queue.jsonl"
    assert human_queue_path.is_file()
    lines = [
        line for line in human_queue_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert lines, "human queue should have at least one entry"
    entry = json.loads(lines[-1])
    assert entry["task_id"] == "TASK-QA-FINAL"
    assert entry["error_code"] == "QA_FAILED_FINAL"


def test_engine_tri_council_round_limit_escalates_to_architect(tmp_path, monkeypatch):
    """Tri-council round limit should escalate to Architect, not request_human."""
    loop_pm = _load_loop_pm()

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        # Return success but with empty changed_files to fail changed_files_present gate
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-10004",
            "changed_files": [],  # Empty to fail changed_files_min gate
        }
        path = Path(args.director_result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_QA_MODE", "blocking")
    monkeypatch.delenv("KERNELONE_QA_UI_PLUGIN_ENABLED", raising=False)
    monkeypatch.setenv("KERNELONE_TRI_COUNCIL_ENABLED", "1")
    monkeypatch.setenv("KERNELONE_TRI_COUNCIL_MAX_ROUNDS", "2")

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Test Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    dialogue_path = runtime_dir / "events" / "dialogue.transcript.jsonl"
    dialogue_path.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-10004",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-TRI-LIMIT",
                "title": "Task with failing gate to trigger tri-council",
                "goal": "Trigger multiple QA failures to reach tri-council limit",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "tri_council_round_count": 1,  # Already 1 round done
                "qa_retry_count": 4,  # Near max retries to trigger tri-council quickly
                "qa_contract": {
                    "task_type": "generic",
                    "hard_gates": [{"kind": "changed_files_min", "min": 1}],  # Will fail with empty changed_files
                    "retry_policy": {"max_director_retries": 5},
                    "coordination": {
                        "enabled": True,
                        "max_rounds": 2,  # Will hit limit at round 2
                        "triggers": ["qa_fail", "complex_task"],
                    },
                },
            }
        ],
    }

    run_dir = tmp_path / "run"
    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(run_dir),
        pm_payload=payload,
        events_path="",
        dialogue_path=str(dialogue_path),
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
    )

    record = result["records"][0]
    # After graceful degradation: escalate_to_architect instead of request_human
    assert record["pm_status"] == "blocked"
    assert record["error_code"] == "QA_CONTRACT_FAIL"  # Director failure = QA FAIL
    assert record.get("qa_failed_final") is not True  # Not final - escalated instead
    tri_council = record.get("tri_council") if isinstance(record.get("tri_council"), dict) else {}
    assert tri_council.get("action") == "escalate_to_architect"
    assert tri_council.get("reason") == "tri_council_round_limit_reached"
    assert record.get("escalate_to_role") == "Architect"


def test_engine_dispatch_budget_preserves_dependency_closure(tmp_path, monkeypatch):
    loop_pm = _load_loop_pm()
    dispatched_task_ids = []

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        task = kwargs.get("task") if isinstance(kwargs.get("task"), dict) else {}
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        dispatched_task_ids.append(task_id)
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-dependency-budget",
            "changed_files": [],
        }
        result_path = Path(args.director_result_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    monkeypatch.setenv("KERNELONE_ENGINE_MAX_TASKS_PER_ITERATION", "2")
    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Dependency Budget Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v1",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-dependency-budget",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-2",
                "title": "Second task",
                "goal": "Depends on first",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "dependencies": ["TASK-1"],
            },
            {
                "id": "TASK-3",
                "title": "Third task",
                "goal": "Depends on second",
                "status": "todo",
                "priority": 2,
                "assigned_to": "Director",
                "dependencies": ["TASK-2"],
            },
            {
                "id": "TASK-1",
                "title": "First task",
                "goal": "Dependency root",
                "status": "todo",
                "priority": 3,
                "assigned_to": "Director",
                "dependencies": [],
            },
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["hard_failure"] is False
    assert result["summary"]["total"] == 2
    assert result["summary"]["successes"] == 2
    assert result["status_updates"] == {"TASK-1": "done", "TASK-2": "done"}
    assert dispatched_task_ids == ["TASK-1", "TASK-2"]
    filters = result["summary"].get("stability_filters") or {}
    assert int(filters.get("budget_limited") or 0) == 1
    assert int(filters.get("dependency_blocked") or 0) == 0


def test_engine_single_task_contract_strips_runtime_dependencies(tmp_path):
    loop_pm = _load_loop_pm()
    observed_contract = {}

    def _fake_runner(args, workspace_full, iteration, **kwargs):
        contract = json.loads(Path(args.pm_task_path).read_text(encoding="utf-8"))
        observed_contract["payload"] = contract
        task = contract["tasks"][0]
        task_id = str(task.get("id") or "").strip() or "UNKNOWN"
        payload = {
            "schema_version": 1,
            "status": "success",
            "task_id": task_id,
            "run_id": "pm-single-contract",
            "changed_files": [],
        }
        result_path = Path(args.director_result_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=_fake_runner)

    runtime_dir = tmp_path / ".polaris" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    plan_path = runtime_dir / "contracts" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# Single Task Contract Plan\n", encoding="utf-8")
    pm_tasks_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    pm_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    pm_tasks_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    runtime_engine_status = runtime_dir / "status" / "engine.status.json"
    runtime_engine_status.parent.mkdir(parents=True, exist_ok=True)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(runtime_dir / "events" / "runtime.events.jsonl"),
        pm_task_path=str(pm_tasks_path),
        plan_path=str(plan_path),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v2",
        prompt_profile="generic",
    )

    payload = {
        "run_id": "pm-single-contract",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-DEP-PARENT",
                "title": "Parent task",
                "goal": "Already completed upstream task",
                "status": "done",
                "priority": 1,
                "assigned_to": "Director",
            },
            {
                "id": "TASK-DEP-CHILD",
                "title": "Child task",
                "goal": "Run with upstream dependency",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "depends_on": ["TASK-DEP-PARENT"],
                "dependencies": ["TASK-DEP-PARENT"],
                "deps": ["TASK-DEP-PARENT", "TASK-DEP-PARENT"],
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
        events_path="",
        plan_path=str(plan_path),
        pm_tasks_paths=[str(pm_tasks_path)],
        runtime_status_path=str(runtime_engine_status),
    )

    assert result["hard_failure"] is False
    assert result["status_updates"]["TASK-DEP-CHILD"] == "done"
    dispatched_payload = observed_contract.get("payload") or {}
    dispatched_task = (dispatched_payload.get("tasks") or [{}])[0]
    assert dispatched_task.get("depends_on") == []
    assert dispatched_task.get("dependencies") == []
    assert dispatched_task.get("deps") == []
    metadata = (
        dispatched_task.get("metadata")
        if isinstance(dispatched_task.get("metadata"), dict)
        else {}
    )
    assert metadata.get("engine_dispatch_depends_on") == ["TASK-DEP-PARENT"]
    dispatch_meta = (
        dispatched_payload.get("engine_dispatch")
        if isinstance(dispatched_payload.get("engine_dispatch"), dict)
        else {}
    )
    assert dispatch_meta.get("depends_on") == ["TASK-DEP-PARENT"]


def test_engine_no_dependency_closed_task_fails_closed(tmp_path):
    loop_pm = _load_loop_pm()
    config = loop_pm.EngineRuntimeConfig(
        director_execution_mode="single",
        max_directors=1,
        scheduling_policy="priority",
    )
    engine = loop_pm.PolarisEngine(config, director_runner=lambda *a, **k: 0)

    args = SimpleNamespace(
        run_director=True,
        director_result_path=str(tmp_path / "unused_result.json"),
        director_events_path=str(tmp_path / "runtime.events.jsonl"),
        pm_task_path=str(tmp_path / "missing_pm_tasks.json"),
        plan_path=str(tmp_path / "missing_plan.md"),
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
        director_timeout=0,
        director_show_output=False,
        director_model="",
        director_path="src/backend/scripts/loop-director.py",
        director_type="v2",
        prompt_profile="generic",
    )
    payload = {
        "run_id": "pm-dependency-closed",
        "pm_iteration": 1,
        "tasks": [
            {
                "id": "TASK-BLOCKED",
                "title": "Blocked by absent dependency",
                "goal": "Should fail closed before dispatch",
                "status": "todo",
                "priority": 1,
                "assigned_to": "Director",
                "depends_on": ["TASK-MISSING-UPSTREAM"],
            }
        ],
    }

    result = engine.dispatch_director_tasks(
        args=args,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
        pm_payload=payload,
    )

    assert result["hard_failure"] is True
    assert result["summary"]["dispatch_blocked"] is True
    assert result["summary"]["blocked"] == 1
