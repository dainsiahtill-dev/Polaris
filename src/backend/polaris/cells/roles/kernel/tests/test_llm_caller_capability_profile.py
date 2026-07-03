"""ResolvedActorCapabilityProfile hot-path coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal import context_gateway as context_gateway_module
from polaris.cells.roles.kernel.internal.llm_caller import request_preparer as request_preparer_module
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import build_final_request_context_audit_for_request
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.kernelone.context.contracts import TurnEngineContextResult
from polaris.kernelone.llm.engine.contracts import ModelSpec


class _ModelCatalog:
    def resolve(
        self,
        provider_id: str,
        model: str,
        provider_cfg: dict[str, object] | None = None,
    ) -> ModelSpec:
        del provider_cfg
        return ModelSpec(
            provider_id=provider_id,
            provider_type="openai_compat",
            model=model,
            max_context_tokens=16384,
            max_output_tokens=4096,
            tokenizer="qwen_chatml",
            supports_tools=True,
            supports_json_schema=True,
            execution_profile="compact",
            tool_schema_profile="slim",
        )


class _NoToolModelCatalog:
    def resolve(
        self,
        provider_id: str,
        model: str,
        provider_cfg: dict[str, object] | None = None,
    ) -> ModelSpec:
        del provider_cfg
        return ModelSpec(
            provider_id=provider_id,
            provider_type="openai_compat",
            model=model,
            max_context_tokens=16384,
            max_output_tokens=4096,
            tokenizer="qwen_chatml",
            supports_tools=False,
            supports_json_schema=False,
        )


@pytest.mark.asyncio
async def test_prepare_request_includes_resolved_actor_capability_profile(tmp_path: Path) -> None:
    request_preparer = LLMRequestPreparer(workspace=str(tmp_path), formatter=None, model_catalog=None)
    request_preparer._model_catalog = _ModelCatalog()
    profile = SimpleNamespace(
        role_id="director",
        provider_id="provider-a",
        model="qwen-16k",
        tool_policy=SimpleNamespace(whitelist=("read_file",)),
    )
    context = SimpleNamespace(
        message="implement the task",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "user", "content": "implement the task"},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        },
    )

    prepared = await request_preparer._prepare_llm_request(
        profile=profile,
        system_prompt="system prompt",
        context=context,
        temperature=0.2,
        max_tokens=1024,
        stream=False,
    )

    capability_profile = prepared.ai_request.context["capability_profile"]
    assert capability_profile == prepared.capability_profile
    assert capability_profile["schema_version"] == 1
    assert capability_profile["role_id"] == "director"
    assert capability_profile["provider_id"] == "provider-a"
    assert capability_profile["provider_type"] == "openai_compat"
    assert capability_profile["model"] == "qwen-16k"
    assert capability_profile["model_window_tokens"] == 16384
    assert capability_profile["model_output_limit_tokens"] == 4096
    assert capability_profile["requested_max_tokens"] == 1024
    assert capability_profile["supports_native_tools"] is True
    assert capability_profile["supports_json_schema"] is True
    assert capability_profile["supports_stream_native_tools"] is True
    assert capability_profile["execution_profile"] == "compact"
    assert capability_profile["tool_schema_profile"] == "slim"
    assert capability_profile["tool_count"] == 1
    assert capability_profile["native_tool_mode"] == "native_tools"
    assert capability_profile["response_format_mode"] == "plain_text"
    assert capability_profile["sources"] == (
        "roles.profile",
        "kernelone.llm.model_catalog",
        "roles.kernel.llm_caller.request_options",
    )


@pytest.mark.asyncio
async def test_prepare_request_preserves_final_request_evidence_context(tmp_path: Path) -> None:
    request_preparer = LLMRequestPreparer(workspace=str(tmp_path), formatter=None, model_catalog=None)
    request_preparer._model_catalog = _ModelCatalog()
    module_interface_contract = {
        "schema_version": "chief_engineer.module_interface_contract.v1",
        "source": "test",
        "modules": [
            {
                "path": "src/engine/rules.js",
                "planned_public_symbols": ["scoreWish"],
            }
        ],
    }
    ce_blueprint = {
        "schema_version": "chief_engineer.blueprint.v1",
        "target_files": ["src/engine/rules.js"],
        "execution_checklist": ["Materialize only the listed target files."],
        "module_interface_contract": module_interface_contract,
    }
    pm_contract = {
        "schema_version": "pm.task_contract.v1",
        "task_id": "TASK-1-source-core",
        "target_files": ["src/engine/rules.js"],
        "acceptance_criteria": ["module exports scoreWish"],
    }
    profile = SimpleNamespace(
        role_id="director",
        provider_id="provider-a",
        model="qwen-16k",
        tool_policy=SimpleNamespace(whitelist=("write_file",)),
    )
    context = SimpleNamespace(
        message="[mode:materialize] repair src/engine/rules.js",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "repair src/engine/rules.js"},
            ],
            "pm_contract": pm_contract,
            "ce_blueprint": ce_blueprint,
            "module_interface_contract": module_interface_contract,
            "final_request_evidence_required": True,
            "required_evidence": [
                "ce_blueprint",
                "architecture_or_file_plan",
                "module_interface_contract",
            ],
        },
    )

    prepared = await request_preparer._prepare_llm_request(
        profile=profile,
        system_prompt="system prompt",
        context=context,
        temperature=0.2,
        max_tokens=1024,
        stream=False,
    )

    request_context = prepared.ai_request.context
    assert request_context["pm_contract"] == pm_contract
    assert request_context["ce_blueprint"] == ce_blueprint
    assert request_context["module_interface_contract"] == module_interface_contract
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=profile,
    )
    evidence = audit["final_request_evidence_coverage"]
    assert evidence["pass"] is True
    assert "pm_contract" in evidence["included_refs"]
    assert "ce_blueprint" in evidence["included_refs"]
    assert "architecture_or_file_plan" in evidence["included_refs"]
    assert "module_interface_contract" in evidence["included_refs"]


@pytest.mark.asyncio
async def test_prepare_request_passes_capability_profile_to_context_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request_preparer = LLMRequestPreparer(workspace=str(tmp_path), formatter=None, model_catalog=None)
    request_preparer._model_catalog = _ModelCatalog()
    captured: dict[str, Any] = {}

    class _Gateway:
        def __init__(self, profile: Any, workspace: str) -> None:
            captured["profile"] = profile
            captured["workspace"] = workspace

        async def build_context(
            self,
            request: Any,
            *,
            system_prompt: str | None = None,
        ) -> TurnEngineContextResult:
            captured["request"] = request
            captured["system_prompt"] = system_prompt
            return TurnEngineContextResult(
                messages=({"role": "user", "content": "implement the task"},),
                token_estimate=5,
                metadata={"source": "fake_gateway"},
            )

    monkeypatch.setattr(context_gateway_module, "RoleContextGateway", _Gateway)
    profile = SimpleNamespace(
        role_id="director",
        provider_id="provider-a",
        model="qwen-16k",
        tool_policy=SimpleNamespace(whitelist=("read_file",)),
    )
    context = SimpleNamespace(
        message="implement the task",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        },
    )

    prepared = await request_preparer._prepare_llm_request(
        profile=profile,
        system_prompt="system prompt",
        context=context,
        temperature=0.2,
        max_tokens=1024,
        stream=False,
    )

    projected_request = captured["request"]
    assert projected_request is not context
    metadata = projected_request.context_override["metadata"]
    assert metadata["capability_profile"] == prepared.capability_profile
    assert metadata["capability_profile"]["provider_id"] == "provider-a"
    assert metadata["capability_profile"]["model_window_tokens"] == 16384
    assert metadata["capability_profile"]["tool_count"] == 1


@pytest.mark.asyncio
async def test_prepare_request_preserves_native_tool_schemas_for_fallback(tmp_path: Path) -> None:
    request_preparer = LLMRequestPreparer(workspace=str(tmp_path), formatter=None, model_catalog=None)
    request_preparer._model_catalog = _ModelCatalog()
    tool_schema = {
        "type": "function",
        "function": {
            "name": "read_file",
            "parameters": {"type": "object"},
        },
    }
    profile = SimpleNamespace(
        role_id="director",
        provider_id="provider-a",
        model="qwen-16k",
        tool_policy=SimpleNamespace(whitelist=("read_file",)),
    )
    context = SimpleNamespace(
        message="read README",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "user", "content": "read README"},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [tool_schema],
        },
    )

    prepared = await request_preparer._prepare_llm_request(
        profile=profile,
        system_prompt="system prompt",
        context=context,
        temperature=0.2,
        max_tokens=1024,
        stream=False,
    )

    assert prepared.request_options["tools"] == [tool_schema]
    assert prepared.native_tool_schemas == [tool_schema]


@pytest.mark.asyncio
async def test_prepare_request_preserves_tool_schemas_for_text_fallback_when_native_tools_unsupported(
    tmp_path: Path,
) -> None:
    request_preparer = LLMRequestPreparer(workspace=str(tmp_path), formatter=None, model_catalog=None)
    request_preparer._model_catalog = _NoToolModelCatalog()
    profile = SimpleNamespace(
        role_id="director",
        provider_id="provider-a",
        model="qwen-16k",
        tool_policy=SimpleNamespace(whitelist=("read_file",)),
    )
    context = SimpleNamespace(
        message="read README",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "user", "content": "read README"},
            ],
            "allow_native_tool_text_fallback": True,
        },
    )

    prepared = await request_preparer._prepare_llm_request(
        profile=profile,
        system_prompt="system prompt",
        context=context,
        temperature=0.2,
        max_tokens=1024,
        stream=False,
    )

    assert prepared.native_tool_mode == "native_tools_unavailable"
    assert prepared.native_tool_schemas
    assert "tools" not in prepared.request_options
