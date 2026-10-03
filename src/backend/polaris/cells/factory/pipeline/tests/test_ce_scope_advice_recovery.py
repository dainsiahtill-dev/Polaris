"""Structured advisory scope must not strand otherwise recoverable CE plans."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from polaris.cells.chief_engineer.blueprint.public import normalize_chief_engineer_portfolio_tool_arguments
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def _payload() -> dict:
    return {
        "construction_plan": {
            "task_plans": {
                "TASK-1": {
                    "behavior_invariant_refs": [],
                    "scope_for_apply": [{"path": "src/a.py", "op": "read", "content": "原样保留"}],
                }
            },
            "TASK-2": {
                "behavior_invariant_refs": [],
                "scope_for_apply": {"item": [{"file": "src/b.py", "op": "read"}]},
            },
            "project_interface_contract": {"provider_declarations": [], "consumer_declarations": []},
            "shared_behavior_contract": {"invariants": []},
        },
        "project_completion_contract": {"obligations": {"artifacts": [], "entrypoints": [], "verification": []}},
        "risk_flags": [],
    }


def _hash(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_existing_scope_mapping_preserved_during_transactional_plan_recovery() -> None:
    payload = _payload()
    before = deepcopy(payload)
    recovered = normalize_chief_engineer_portfolio_tool_arguments(payload, authoritative_task_ids=("TASK-1", "TASK-2"))
    assert recovered.recovered is True
    plans = recovered.payload["construction_plan"]["task_plans"]
    assert set(plans) == {"TASK-1", "TASK-2"}
    assert plans["TASK-1"]["scope_for_apply"] == [{"path": "src/a.py", "op": "read", "content": "原样保留"}]
    assert plans["TASK-2"]["scope_for_apply"] == [{"file": "src/b.py", "op": "read"}]
    assert "TASK-2" not in recovered.payload["construction_plan"]
    assert payload == before


def test_factory_recovery_refreshes_carried_candidate_and_hash_without_grants() -> None:
    payload = _payload()
    metadata = {
        "tool_call": {"arguments": deepcopy(payload)},
        "chief_engineer_schema_repair_base_candidate": deepcopy(payload),
        "chief_engineer_schema_repair_base_candidate_hash": _hash(payload),
        "job_token": {"token_id": "original", "allowed_write_paths": ["src/a.py"]},
    }
    before = deepcopy(metadata)
    prior = RoleExecutionResultV1(
        ok=False,
        status="failed",
        role="chief_engineer",
        workspace="fixture",
        metadata=metadata,
        error_code="output_validation_failed",
    )
    owner = SimpleNamespace(
        workspace="fixture",
        _chief_engineer_structured_output_contract=OrchestrationStageExecutor._chief_engineer_structured_output_contract,
    )
    recovered = OrchestrationStageExecutor._recover_chief_engineer_portfolio_structural_result(
        owner, result=prior, portfolio_task_ids=("TASK-1", "TASK-2")
    )
    assert recovered.ok is True
    current = recovered.metadata["structured_output"]
    assert recovered.metadata["chief_engineer_schema_repair_base_candidate"] == current
    assert recovered.metadata["chief_engineer_schema_repair_base_candidate_hash"] == _hash(current)
    assert recovered.metadata["job_token"] == before["job_token"]
    assert (
        OrchestrationStageExecutor._chief_engineer_schema_repair_base_candidate(
            recovered, portfolio_task_ids=("TASK-1", "TASK-2")
        )
        == current
    )
    assert prior.metadata == before


@pytest.mark.parametrize("attack", ["reference_mapping", "conflicting_lifted_plan", "invalid_scope_scalar"])
def test_scope_mapping_support_does_not_relax_ambiguous_or_typed_fields(attack: str) -> None:
    payload = _payload()
    if attack == "reference_mapping":
        payload["construction_plan"]["task_plans"]["TASK-1"]["behavior_invariant_refs"] = [{"id": "INV-1"}]
    elif attack == "conflicting_lifted_plan":
        payload["construction_plan"]["task_plans"]["TASK-2"] = {
            "behavior_invariant_refs": [],
            "scope_for_apply": ["src/other.py"],
        }
    else:
        payload["construction_plan"]["task_plans"]["TASK-1"]["scope_for_apply"] = [12]
    before = deepcopy(payload)
    recovered = normalize_chief_engineer_portfolio_tool_arguments(payload, authoritative_task_ids=("TASK-1", "TASK-2"))
    assert recovered.recovered is False
    assert recovered.payload == before
    assert payload == before


def test_empty_advisory_plans_remain_valid_without_inventing_task_overlays() -> None:
    payload = _payload()
    payload["construction_plan"]["task_plans"] = {}
    del payload["construction_plan"]["TASK-2"]
    prior = RoleExecutionResultV1(
        ok=True, status="completed", role="chief_engineer", workspace="fixture", metadata={"structured_output": payload}
    )
    owner = SimpleNamespace(
        workspace="fixture",
        _chief_engineer_structured_output_contract=OrchestrationStageExecutor._chief_engineer_structured_output_contract,
    )
    result = OrchestrationStageExecutor._recover_chief_engineer_portfolio_structural_result(
        owner, result=prior, portfolio_task_ids=("TASK-1", "TASK-2")
    )
    assert result is prior
    assert result.metadata["structured_output"]["construction_plan"]["task_plans"] == {}


def test_relocated_foreign_task_is_still_rejected_by_full_factory_schema() -> None:
    payload = _payload()
    payload["construction_plan"]["FOREIGN"] = payload["construction_plan"].pop("TASK-2")
    prior = RoleExecutionResultV1(
        ok=False,
        status="failed",
        role="chief_engineer",
        workspace="fixture",
        metadata={"tool_call": {"arguments": payload}},
        error_code="output_validation_failed",
    )
    owner = SimpleNamespace(
        workspace="fixture",
        _chief_engineer_structured_output_contract=OrchestrationStageExecutor._chief_engineer_structured_output_contract,
    )
    result = OrchestrationStageExecutor._recover_chief_engineer_portfolio_structural_result(
        owner, result=prior, portfolio_task_ids=("TASK-1", "TASK-2")
    )
    assert result is prior
    assert result.ok is False
