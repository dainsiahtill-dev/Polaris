from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

def _load_policy_contract_module():
    roots = [
        Path(__file__).resolve().parents[1],  # current repo root
        Path(__file__).resolve().parents[2],  # compatibility root used by legacy tests
    ]
    candidates = []
    for repo_root in roots:
        candidates.extend(
            [
                repo_root / "src" / "backend" / "core" / "polaris_loop" / "policy_contract.py",
                repo_root / "polaris" / "modules" / "polaris-loop" / "policy_contract.py",
            ]
        )
    for module_path in candidates:
        if not module_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("policy_contract", module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules["policy_contract"] = module
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Failed to load policy_contract.py")


_policy_contract = _load_policy_contract_module()
HP_PIPELINE = _policy_contract.HP_PIPELINE
PolicyContractError = _policy_contract.PolicyContractError
PolicyRuntime = _policy_contract.PolicyRuntime


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


def test_hp_create_blueprint_requires_contract(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    with pytest.raises(PolicyContractError):
        runtime.hp_create_blueprint(
            blueprint_path=".polaris/docs/blueprints/plan_test.md",
            mode="S1",
            budget={"changed_loc": 10},
        )


def test_policy_check_requires_approval(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    runtime.hp_start_run("demo", ["ac1"])
    runtime.hp_create_blueprint(".polaris/docs/blueprints/plan_test.md", "S1", {"changed_loc": 10})

    with pytest.raises(PolicyContractError):
        runtime.hp_phase_transition("policy_check", "should block")

    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/plan_test.md")
    event = runtime.hp_phase_transition("policy_check", "approved")
    assert event["phase"] == "policy_check"


def test_full_hp_pipeline_with_verify_and_finalize(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    runtime.hp_start_run("build feature", ["ac1", "ac2"])
    runtime.hp_create_blueprint(".polaris/docs/blueprints/plan_test.md", "S2", {"changed_loc": 42})
    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/plan_test.md")
    runtime.hp_phase_transition("policy_check", "policy ok")
    runtime.hp_create_snapshot("snap_001", ".polaris/snapshots/snap_001/index.json")
    runtime.hp_allow_implementation("impl-token-1", runtime_behavior_change=True)
    runtime.hp_run_verify("nonce_1", ".polaris/runtime/verification_nonce_1.log", exit_code=0, evidence_run=True)
    runtime.hp_finalize_run("success", "finalized")

    rows = _read_jsonl(Path(runtime.events_path))
    phases = [
        row["phase"]
        for row in rows
        if row.get("type") == "phase_transition" and row.get("run_id") == runtime.state.run_id
    ]
    assert phases == HP_PIPELINE


def test_finalize_requires_verify(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    runtime.hp_start_run("demo", ["ac1"])
    runtime.hp_create_blueprint(".polaris/docs/blueprints/plan_test.md", "S1", {"changed_loc": 10})
    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/plan_test.md")
    runtime.hp_phase_transition("policy_check", "ok")
    runtime.hp_create_snapshot("snap_001", ".polaris/snapshots/snap_001/index.json")
    runtime.hp_allow_implementation("impl-token-2")

    with pytest.raises(PolicyContractError):
        runtime.hp_finalize_run("success", "should block")


def test_hp_allow_implementation_requires_token(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    runtime.hp_start_run("demo", ["ac1"])
    runtime.hp_create_blueprint(".polaris/docs/blueprints/plan_test.md", "S1", {"changed_loc": 10})
    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/plan_test.md")
    runtime.hp_phase_transition("policy_check", "ok")
    runtime.hp_create_snapshot("snap_001", ".polaris/snapshots/snap_001/index.json")

    with pytest.raises(PolicyContractError):
        runtime.hp_allow_implementation("")


def test_hp_run_verify_requires_verification_log_pattern(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    runtime.hp_start_run("demo", ["ac1"])
    runtime.hp_create_blueprint(".polaris/docs/blueprints/plan_test.md", "S1", {"changed_loc": 10})
    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/plan_test.md")
    runtime.hp_phase_transition("policy_check", "ok")
    runtime.hp_allow_implementation("impl-token-3")

    with pytest.raises(PolicyContractError):
        runtime.hp_run_verify("nonce_1", ".polaris/runtime/evidence.log", exit_code=0, evidence_run=True)


def test_s0_special_handling_allows_snapshot_skip(tmp_path: Path) -> None:
    runtime = PolicyRuntime.create(tmp_path)
    runtime.hp_start_run("hotfix typo", ["ac1"])
    runtime.hp_create_blueprint(
        ".polaris/docs/blueprints/plan_hotfix.md",
        "S0",
        {"changed_loc": 6},
        special_handling=True,
    )
    runtime.hp_record_approval(True, ref=".polaris/docs/blueprints/plan_hotfix.md")
    runtime.hp_phase_transition("policy_check", "ok")
    implementation_event = runtime.hp_allow_implementation("impl-hotfix-1")

    assert implementation_event["phase"] == "implementation"
    assert runtime.state.meta.get("special_handling") is True
    assert runtime.state.meta.get("snapshot_skipped") is True

    rows = _read_jsonl(Path(runtime.events_path))
    snapshot_rows = [row for row in rows if row.get("phase") == "snapshot"]
    assert snapshot_rows
    assert snapshot_rows[-1].get("snapshot_skip_reason") == "s0_special_handling_snapshot_optional"
