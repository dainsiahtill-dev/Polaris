import importlib
import sys
from pathlib import Path


def _load_engine_module():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "src" / "backend" / "scripts"
    project_root = repo_root / "src" / "backend"
    loop_module_dir = project_root / "core" / "polaris_loop"
    for entry in (str(scripts_dir), str(project_root), str(loop_module_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return importlib.import_module("pm.polaris_engine")


def test_tri_council_is_deferred_before_retry_threshold(tmp_path, monkeypatch):
    mod = _load_engine_module()
    monkeypatch.setenv("POLARIS_TRI_COUNCIL_START_RETRY", "5")

    payload = mod._run_tri_council_round(
        stage="post_qa_failure",
        workspace_full=str(tmp_path),
        task={},
        qa_contract={"task_type": "generic"},
        qa_result={"failed_gates": ["hard[1]:director_status_success"]},
        qa_verdict="FAIL",
        task_root=str(tmp_path / "task"),
        run_dir=str(tmp_path / "run"),
        run_id="pm-00001",
        pm_iteration=1,
        task_id="TASK-1",
        task_title="Sample Task",
        events_path=str(tmp_path / "events.jsonl"),
        dialogue_path=str(tmp_path / "dialogue.jsonl"),
        director_status="success",
        changed_files=["src/main.py"],
        coordination_policy={"enabled": True, "max_rounds": 2, "triggers": ["qa_fail"]},
        error_code="QA_CONTRACT_FAIL",
        failure_detail="failed",
        qa_retry_count=4,
        max_director_retries=5,
    )
    assert payload == {}


def test_tri_council_starts_after_retry_threshold(tmp_path, monkeypatch):
    mod = _load_engine_module()
    monkeypatch.setenv("POLARIS_TRI_COUNCIL_START_RETRY", "5")

    payload = mod._run_tri_council_round(
        stage="post_qa_failure",
        workspace_full=str(tmp_path),
        task={},
        qa_contract={"task_type": "generic"},
        qa_result={"failed_gates": ["hard[1]:director_status_success"]},
        qa_verdict="FAIL",
        task_root=str(tmp_path / "task"),
        run_dir=str(tmp_path / "run"),
        run_id="pm-00002",
        pm_iteration=2,
        task_id="TASK-2",
        task_title="Sample Task",
        events_path=str(tmp_path / "events.jsonl"),
        dialogue_path=str(tmp_path / "dialogue.jsonl"),
        director_status="success",
        changed_files=["src/main.py"],
        coordination_policy={"enabled": True, "max_rounds": 2, "triggers": ["qa_fail"]},
        error_code="QA_CONTRACT_FAIL",
        failure_detail="failed",
        qa_retry_count=5,
        max_director_retries=5,
    )
    assert isinstance(payload, dict)
    assert payload.get("action") == "retry_with_fix"
