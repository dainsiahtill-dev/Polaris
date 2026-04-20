import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "src" / "backend" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from pm.qa_auditor import evaluate_qa_contract


def _base_contract():
    return {
        "schema_version": 1,
        "plugin": "rules_v1",
        "plugin_hint": "rules_v1",
        "task_type": "generic",
        "hard_gates": [
            "director_status_success",
            {
                "kind": "verify_command_success",
                "command": 'python -c "import sys; sys.exit(3)"',
                "timeout_seconds": 30,
                "working_dir": "workspace",
            },
        ],
        "regression_gates": [],
        "evidence_required": [],
        "retry_policy": {"max_director_retries": 5},
        "coordination": {"enabled": True, "max_rounds": 2, "triggers": ["qa_fail"]},
    }


def test_verify_gate_can_be_deferred_by_context(tmp_path):
    contract = _base_contract()
    context = {
        "task": {"assigned_to": "Director", "title": "Implement service"},
        "director_status": "success",
        "changed_files": ["src/service.py"],
        "allow_verify_commands": False,
        "verify_deferred_reason": "defer_verify_until_final_retry",
    }
    result = evaluate_qa_contract(
        contract=contract,
        context=context,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
    )

    assert result["verdict"] == "PASS"
    verify_runs = result.get("verify_runs")
    assert isinstance(verify_runs, list)
    assert any(bool(item.get("deferred")) for item in verify_runs if isinstance(item, dict))


def test_verify_gate_fails_when_command_fails(tmp_path):
    contract = _base_contract()
    context = {
        "task": {"assigned_to": "Director", "title": "Implement service"},
        "director_status": "success",
        "changed_files": ["src/service.py"],
        "allow_verify_commands": True,
    }
    result = evaluate_qa_contract(
        contract=contract,
        context=context,
        workspace_full=str(tmp_path),
        run_dir=str(tmp_path / "run"),
    )

    assert result["verdict"] == "FAIL"
    assert any("verify_command_success" in gate for gate in result.get("failed_gates", []))
    verify_runs = result.get("verify_runs")
    assert isinstance(verify_runs, list)
    assert any(int(item.get("exit_code", 0)) == 3 for item in verify_runs if isinstance(item, dict))
