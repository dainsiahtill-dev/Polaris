from __future__ import annotations

from polaris.cells.director.tasking.internal.execution_contract import build_task_execution_contract
from polaris.cells.director.tasking.internal.execution_envelope import build_execution_envelope
from polaris.cells.director.tasking.public.contracts import (
    TaskExecutionProfileV1,
    TaskExecutionStrategyV1,
)


def test_build_execution_envelope_binds_contracts_and_capability() -> None:
    profile = TaskExecutionProfileV1(
        task_type="bugfix",
        phase="repair",
        language="python",
        target_files=("src/main.py",),
        scope_paths=("src",),
    )
    strategy = TaskExecutionStrategyV1(
        temperature=0.05,
        output_budget_tokens=64_000,
        input_budget_tokens=96_000,
        evidence_requirements=("pm_task_contract", "chief_engineer_blueprint"),
        target_files=profile.target_files,
        scope_paths=profile.scope_paths,
    )
    contract = build_task_execution_contract(
        profile,
        strategy,
        metadata={
            "delivery_depth_contract": {"schema_version": "polaris.delivery_depth_contract.v1"},
            "selected_libraries": ["pytest"],
        },
    )
    envelope = build_execution_envelope(
        workspace="/workspace",
        task_id="TASK-1",
        run_id="run-1",
        trace_id="trace-1",
        profile=profile,
        strategy=strategy,
        contract=contract,
        metadata={
            "pm_contract_hash": "pm-hash",
            "blueprint_hash": "blueprint-hash",
            "ce_handoff_decision": {"allowed": True, "decision_hash": "handoff-hash"},
            "ce_handoff_decision_hash": "handoff-hash",
            "execution_profile_hash": "profile-hash",
            "job_token": {
                "token_id": "job-1",
                "allowed_paths": ["src/main.py", "src"],
                "target_files": ["src/main.py"],
            },
            "model": "test-model",
            "tool_choice": "auto",
            "context_snapshot_ref": "runtime/contexts/aa/bb",
        },
        created_at="2026-06-27T00:00:00Z",
    )

    payload = envelope.to_dict()
    assert payload["schema_version"] == "polaris.execution_envelope.v1"
    assert payload["envelope_id"].startswith("exec-env-")
    assert payload["envelope_hash"]
    assert payload["pm_contract"]["hash"] == "pm-hash"
    assert payload["ce_blueprint"]["hash"] == "blueprint-hash"
    assert payload["handoff_decision"] == {"ref": "", "hash": "handoff-hash", "allowed": True}
    assert payload["execution_profile"]["hash"] == "profile-hash"
    assert payload["authorization"]["capability_token_ref"] == "job-1"
    assert payload["authorization"]["allowed_write_paths"] == ["src/main.py", "src"]
    assert payload["model_policy"]["temperature"] == 0.05
    assert payload["model_policy"]["max_tokens"] == 64_000
    assert payload["audit_policy"]["final_provider_request_required"] is True


def test_build_execution_envelope_marks_missing_evidence() -> None:
    profile = TaskExecutionProfileV1(target_files=("src/main.py",))
    strategy = TaskExecutionStrategyV1(target_files=profile.target_files)
    contract = build_task_execution_contract(profile, strategy, metadata={})

    envelope = build_execution_envelope(
        workspace="/workspace",
        task_id="TASK-2",
        run_id="run-2",
        trace_id="trace-2",
        profile=profile,
        strategy=strategy,
        contract=contract,
        metadata={},
        created_at="2026-06-27T00:00:00Z",
    )

    payload = envelope.to_dict()
    assert payload["pm_contract"]["hash"] == "missing:pm_contract_hash"
    assert payload["ce_blueprint"]["hash"] == "missing:blueprint_hash"
    assert payload["handoff_decision"]["hash"] == "missing:handoff_decision_hash"
    assert payload["handoff_decision"]["allowed"] is False
    assert payload["authorization"]["capability_token_hash"] == "missing:capability_token"
