"""Regression: mixed schema errors must not regenerate valid CE siblings."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.ce_schema_residual_patch import plan_ce_schema_residual_patch
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _capture_chief_engineer_lease_keepers,
    _executor,
    _factory_stage_context,
    _invalid_structured_transport_chief_engineer_result,
    _single_task_chief_engineer_result,
    _write_minimal_chief_engineer_plan,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def test_mixed_ce_schema_errors_use_bound_patch_and_preserve_valid_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break caught: mixed errors wrongly select complete portfolio regeneration."""
    executor = _executor(tmp_path)
    keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
    _write_minimal_chief_engineer_plan(executor)
    valid = deepcopy(_single_task_chief_engineer_result().metadata["structured_output"])
    base = deepcopy(valid)
    task_plans = base["construction_plan"].pop("task_plans")
    base["task_plans"] = deepcopy(task_plans)
    base["item"] = {"draft_note": "Unapproved appendix; CE must explicitly discard it."}
    first = _invalid_structured_transport_chief_engineer_result()
    first.output = ""
    first.error_message = "structured_output_payload_schema_mismatch:$:Additional properties are not allowed"
    first.metadata["tool_call"] = {"tool": "submit_structured_role_output", "arguments": base}
    base_hash = hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    patch = {
        "base_candidate_hash": base_hash,
        "edits": [
            {"operation": "add", "path": ["construction_plan", "task_plans"], "value": task_plans},
            {"operation": "remove", "path": ["item"]},
            {"operation": "remove", "path": ["task_plans"]},
        ],
    }
    patched_metadata = deepcopy(_single_task_chief_engineer_result().metadata)
    patched_metadata["structured_output"] = patch
    patched = RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace=str(tmp_path),
        output=json.dumps(patch, ensure_ascii=False),
        metadata=patched_metadata,
    )
    results = [first, patched]
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            # Failed current code may try another full reconstruction; return
            # an explicit failed result, never permit a real Provider call.
            return results.pop(0) if results else first

    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", Runtime)
    run = FactoryRun(
        id="factory-schema-residual-patch",
        config=FactoryConfig(name="schema-repair-test"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-01T08:00:00+00:00",
    )
    result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))
    assert result.status == "success"
    assert len(commands) == 2
    assert commands[1].structured_output_contract.schema_name == "chief_engineer_blueprint_portfolio_schema_patch"
    assert commands[1].context["chief_engineer_schema_repair_base_candidate_hash"] == base_hash
    assert all(keeper.is_alive is False for keeper in keepers)


def test_composed_draft_does_not_replace_physical_patch_evidence(tmp_path: Path) -> None:
    """Break caught: server composition masquerades as Provider's full portfolio."""
    executor = _executor(tmp_path)
    base = {"stable": "KEEP", "extra": "draft"}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"stable": {"type": "string"}},
        "required": ["stable"],
    }
    plan = plan_ce_schema_residual_patch(candidate=base, schema=schema)
    assert plan is not None
    patch = {
        "base_candidate_hash": hashlib.sha256(
            json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "edits": [{"operation": "remove", "path": ["extra"]}],
    }
    raw_tool = {"tool": "submit_structured_role_output", "arguments": deepcopy(patch)}
    result = RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace=str(tmp_path),
        output=json.dumps(patch),
        metadata={"structured_output": deepcopy(patch), "tool_call": deepcopy(raw_tool)},
    )
    composed = executor._compose_chief_engineer_schema_residual_patch_result(
        result=result, plan=plan, base_candidate=base
    )
    assert composed.ok is True
    assert json.loads(composed.output) == {"stable": "KEEP"}
    assert composed.metadata["tool_call"] == raw_tool
    assert result.metadata["structured_output"] == patch
    assert composed.metadata["chief_engineer_schema_repair_patch"] == patch
    receipt = composed.metadata["chief_engineer_schema_residual_patch_composition"]
    assert receipt["physical_provider_output_is_patch"] is True
    assert receipt["full_schema_validated"] is True
    assert receipt["semantic_authority_validated"] is False
    assert receipt["patch_hash"] != receipt["composed_candidate_hash"]
