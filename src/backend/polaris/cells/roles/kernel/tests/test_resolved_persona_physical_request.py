"""Resolved prompt profiles also own the full provider-visible role persona."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.kernel.prompt_assembly import build_system_prompt_for_request
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder
from polaris.cells.roles.kernel.internal.structured_output_transport import resolve_structured_output_transport
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.kernel.tests.test_prompt_builder_cache import _make_profile
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest
from polaris.kernelone.llm.engine.provider_native_request import project_factory_provider_native_request


async def _native_request(builder: PromptBuilder, *, workspace: str, context: dict, message: str, appendix: str = ""):
    profile = _make_profile(template_id="chief_engineer", role_id="chief_engineer")
    profile.provider_id = "anthropic_compat"
    profile.provider_type = "anthropic_compat"
    profile.model = "MiniMax-M3"
    profile.max_context_tokens = 1_000_000
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=workspace,
        message=message,
        history=[],
        context_override=context,
    )
    # Real role composition and profile selection, including language signals
    # from the Node verification environment that accompany schema repair.
    system = build_system_prompt_for_request(
        prompt_builder=builder,
        profile=profile,
        request=request,
        prompt_appendix=appendix,
        workspace=workspace,
    )
    context["_transaction_kernel_prebuilt_messages"] = [
        {"role": "system", "content": system},
        {"role": "system", "content": "Caller policy: preserve immutable PM paths and command authority."},
        {"role": "user", "content": message},
    ]
    context[STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY] = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_required_root_patch",
        description="Merge only missing required portfolio roots; fully revalidate the retained candidate.",
        json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "construction_plan": {"type": "object"},
                "project_completion_contract": {"type": "object"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "project_completion_contract", "risk_flags"],
        },
    ).to_context_projection()
    transport = resolve_structured_output_transport(context)
    assert transport is not None
    context["_transaction_kernel_forced_tool_definitions"] = [transport.tool_definition]
    context["_transaction_kernel_forced_tool_choice"] = transport.tool_choice
    prepared = await LLMRequestPreparer(workspace=workspace)._prepare_llm_request(
        profile=profile,
        system_prompt=system,
        context=SimpleNamespace(message=message, domain="code", context_override=context),
        temperature=0.0,
        max_tokens=60_000,
        stream=False,
    )
    native = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload={
            "model": profile.model,
            "messages": prepared.messages,
            "tools": prepared.request_options.get("tools") or [],
            "tool_choice": prepared.request_options.get("tool_choice"),
            "response_format": prepared.request_options.get("response_format"),
            "temperature": prepared.request_options["temperature"],
            "max_tokens": prepared.request_options["max_tokens"],
            "stream": False,
        },
        provider_config={"base_url": "https://provider.test/anthropic"},
    )
    assert native is not None
    body = json.loads(native.expected_body_json)
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request, prepared=prepared, profile=profile
    )
    return body, audit


@pytest.mark.asyncio
async def test_repair_native_role_definition_uses_retained_primary_profile(tmp_path) -> None:
    builder = PromptBuilder(workspace=str(tmp_path))
    primary, primary_audit = await _native_request(
        builder,
        workspace=str(tmp_path),
        context={"language": "typescript", "task_type": "implement", "artifact": "web"},
        message="Produce the TypeScript Chief Engineer web blueprint.",
    )
    selection = primary_audit["prompt_profile_selection"]
    assert "TypeScript 前端/全栈架构师" in PromptBuilder._extract_role_definition_block(primary["system"])
    repair, repair_audit = await _native_request(
        builder,
        workspace=str(tmp_path),
        context={
            "language": selection["inferred_language"],
            "task_type": selection["inferred_task_type"],
            "artifact": selection["inferred_artifact"],
            "prompt_stage": selection["inferred_stage"],
        },
        message="Repair exactly the missing construction_plan/project_completion_contract/risk_flags root members.",
        appendix="Node.js JavaScript verification: tests/core.mjs scripts/verify.mjs package.json",
    )
    assert (
        selection["inferred_language"] == repair_audit["prompt_profile_selection"]["inferred_language"] == "typescript"
    )
    for body in (primary, repair):
        role = PromptBuilder._extract_role_definition_block(body["system"])
        assert "TypeScript 前端/全栈架构师" in role
        assert "JavaScript/Node.js 资深工程师" not in role
        assert "你是 Polaris 体系中的 **Chief Engineer**" in role
        assert "工作流程类型: contract_to_blueprint" in body["system"]
        assert PromptBuilder.SECURITY_BOUNDARY in body["system"]
        assert "Caller policy: preserve immutable PM paths and command authority." in body["system"]
        assert body["tools"][0]["name"] == "submit_structured_role_output"
        assert body["tool_choice"] == {"type": "tool", "name": "submit_structured_role_output"}


def test_unknown_resolved_language_keeps_recipe_instead_of_reinferring_node() -> None:
    builder = PromptBuilder()
    profile = _make_profile(template_id="chief_engineer", role_id="chief_engineer")
    prompt = builder.build_system_prompt(
        profile,
        message="Repair Node.js JavaScript tests/core.mjs package.json",
        language="unregistered-language",
        include_working_memory_contract=False,
    )
    role = PromptBuilder._extract_role_definition_block(prompt)
    assert "你是 Polaris 体系中的 **Chief Engineer**" in role
    assert "JavaScript/Node.js 资深工程师" not in role
