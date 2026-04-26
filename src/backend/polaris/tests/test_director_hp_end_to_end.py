from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO_ROOT / "src" / "backend" / "core" / "polaris_loop"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


def _load_module(module_name: str, rel_path: str):
    module_path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module: {rel_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


policy_contract = _load_module(
    "policy_contract",
    "src/backend/core/polaris_loop/policy_contract.py",
)
director_evidence = _load_module(
    "director_evidence",
    "src/backend/core/polaris_loop/director_evidence.py",
)

PolicyRuntime = policy_contract.PolicyRuntime
HP_PIPELINE = policy_contract.HP_PIPELINE


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_director_hp_end_to_end_with_verification_log(tmp_path: Path) -> None:
    workspace = tmp_path
    run_id = "pm-99999"
    runtime = PolicyRuntime.create(workspace, run_id=run_id, actor="Director")
    runtime.hp_start_run("demo-goal", ["ac1"])
    runtime.hp_create_blueprint(".polaris/docs/blueprints/demo.md", "S1", {"changed_loc": 12})
    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/demo.md", reason="gate_allow")
    runtime.hp_phase_transition("policy_check", "policy ok")
    runtime.hp_allow_implementation("impl-token-1", runtime_behavior_change=True)

    state = SimpleNamespace(
        workspace_full=str(workspace),
        current_task_id="TASK-1",
        current_run_id=run_id,
        log_full=str(workspace / ".polaris" / "runtime" / "logs" / "director.runlog.md"),
    )
    verification_log = director_evidence.write_verification_log(
        state,
        nonce="nonce_1",
        acceptance=True,
        qa_summary="qa pass",
        qa_next="none",
        evidence_path="",
        tool_rounds=1,
        total_lines_read=10,
    )
    assert verification_log is not None
    assert str(verification_log).endswith("verification_nonce_1.log")

    runtime.hp_run_verify("nonce_1", str(verification_log), exit_code=0, evidence_run=True)
    runtime.hp_finalize_run("success", "done")

    rows = _read_jsonl(Path(runtime.events_path))
    phases = [
        row["phase"]
        for row in rows
        if row.get("type") == "phase_transition" and row.get("run_id") == run_id
    ]
    assert phases == HP_PIPELINE
