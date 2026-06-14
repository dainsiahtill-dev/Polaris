"""ResolvedActorCapabilityProfile hot-path coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.llm_caller import caller as caller_module
from polaris.cells.roles.kernel.internal.llm_caller.caller import LLMCaller
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
        )


@pytest.mark.asyncio
async def test_prepare_request_includes_resolved_actor_capability_profile(tmp_path: Path) -> None:
    caller = LLMCaller(workspace=str(tmp_path), enable_cache=False, emit_deprecation_warning=False)
    caller._model_catalog = _ModelCatalog()
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
            caller_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "user", "content": "implement the task"},
            ],
            caller_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [
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

    prepared = await caller._prepare_llm_request(
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
    assert capability_profile["tool_count"] == 1
    assert capability_profile["native_tool_mode"] == "native_tools"
    assert capability_profile["response_format_mode"] == "plain_text"
    assert capability_profile["sources"] == (
        "roles.profile",
        "kernelone.llm.model_catalog",
        "roles.kernel.llm_caller.request_options",
    )
