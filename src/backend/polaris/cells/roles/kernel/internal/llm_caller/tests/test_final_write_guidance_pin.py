"""Final request assembly owns required guidance binding across every retry."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.llm_caller import request_preparer as module
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import build_final_request_context_audit_for_request
from polaris.cells.roles.kernel.internal.llm_caller.request_facts import project_role_request_facts
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.tests.test_role_turn_request_fact_projection import _profile
from polaris.kernelone.audit.task_write_guidance import project_task_write_guidance
from polaris.kernelone.tool_execution.forced_tool_surface import build_forced_tool_surface


@pytest.mark.asyncio
async def test_final_preparer_restores_legitimate_guidance_lost_by_bootstrap() -> None:
    envelope = {"authorization": {"allowed_write_paths": ["main.go"], "target_files": ["main.go"]}}
    guidance = project_task_write_guidance(envelope, ["main.go", "README.md"])
    override = {
        "director_execution_envelope": envelope,
        "target_files": ["main.go", "README.md"],
        "task_write_guidance": guidance,
        module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
            {"role": "system", "content": "WRITE RETRY MODE: bootstrap reads completed."},
            {"role": "user", "content": "Repair main.go using the supplied source."},
        ],
        module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: build_forced_tool_surface(
            ["edit_file"], pin_write_paths=["main.go"]
        ),
        module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: {"type": "function", "function": {"name": "edit_file"}},
    }
    before = deepcopy(override)
    profile = _profile("director")
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=profile,
        system_prompt="You are Director.",
        context=SimpleNamespace(
            message="Repair main.go using the supplied source.", domain="code", context_override=override
        ),
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request, prepared=prepared, profile=profile
    )
    assert not any(f["code"].startswith("task_write_guidance") for f in audit["context_quality"]["findings"])
    assert prepared.messages[-1]["role"] == "user"
    assert prepared.ai_request.context["task_write_guidance"] == guidance
    assert override == before


@pytest.mark.parametrize("kind", ["scope_mismatch", "control_contaminated", "missing_reference", "foreign_reference"])
def test_final_pin_does_not_legitimize_invalid_guidance(kind: str) -> None:
    messages = [{"role": "user", "content": "Repair main.go"}]
    envelope = {"authorization": {"allowed_write_paths": ["main.go"], "target_files": ["main.go"]}}
    targets = ["main.go", "README.md"]
    guidance = project_task_write_guidance(envelope, targets)
    if kind == "scope_mismatch":
        guidance["write_targets"] = ["foreign.go"]
    elif kind == "control_contaminated":
        guidance["lease_id"] = "internal-only"
    elif kind == "missing_reference":
        guidance["reference_only_targets"] = []
    else:
        guidance["reference_only_targets"] = ["foreign_reference.md"]
    override = {"director_execution_envelope": envelope, "target_files": targets, "task_write_guidance": guidance}
    assert module._ensure_task_write_guidance_message_bound(messages, override, role_id="director") == messages


def test_declared_project_inventory_survives_projection_without_granting_its_references() -> None:
    envelope = {"authorization": {"allowed_write_paths": ["main.go"], "target_files": ["main.go"]}}
    inventory = ["main.go", "README.md"]
    guidance = project_task_write_guidance(envelope, inventory)
    projection = project_role_request_facts(
        context_override={"director_execution_envelope": envelope, "target_files": ["main.go"]},
        metadata={"project_declared_target_files": inventory, "task_write_guidance": guidance},
    )
    assert projection.context_override["project_declared_target_files"] == inventory
    messages = [{"role": "user", "content": "Repair main.go"}]
    pinned = module._ensure_task_write_guidance_message_bound(messages, projection.context_override, role_id="director")
    assert len(pinned) == 2
    assert '"reference_only_targets": ["README.md"]' in pinned[0]["content"]
    assert '"write_targets": ["main.go"]' in pinned[0]["content"]
    assert projection.context_override["director_execution_envelope"] == envelope
