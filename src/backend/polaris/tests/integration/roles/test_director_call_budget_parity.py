"""Call ceilings survive task strategy projection into native provider payloads."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.director.tasking.public.execution_guidance import (
    apply_task_execution_strategy_overrides,
    resolve_task_execution_profile,
    resolve_task_execution_strategy,
)
from polaris.cells.roles.adapters.internal.director.adapter import (
    DirectorAdapter,
    _prepare_role_dialogue_context,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_facts import project_role_request_facts
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.infrastructure.llm.providers.openai_provider import _build_openai_chat_payload
from polaris.kernelone.llm.engine.contracts import ModelSpec


class _LargeModelCatalog:
    def resolve(self, provider_id: str, model: str, provider_cfg: Any = None) -> ModelSpec:
        del provider_cfg
        return ModelSpec(
            provider_id=provider_id,
            provider_type="openai_compat",
            model=model,
            max_context_tokens=1_000_000,
            max_output_tokens=128_000,
            supports_tools=True,
            supports_json_schema=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("stage", ["first_call", "empty_write_content_retry", "no_write_materialization_retry"])
async def test_forced_write_ceiling_reaches_native_payload(tmp_path: Path, stage: str, stream: bool) -> None:
    """A strategy refresh must not replace an admitted 7000 cap with 128000."""
    context, _ = _prepare_role_dialogue_context(
        {
            "target_files": ["src/service.ts"],
            "scope_paths": ["src/service.ts"],
            "task_execution_profile": {"task_type": "write_code"},
            "task_execution_contract": {"context_budget": {"output_budget_tokens": 128_000}},
            "director_empty_write_retry": {"target_files": ["src/service.ts"]},
        },
        timeout_seconds=660.0,
        stage_label=stage,
    )
    metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=0)
    DirectorAdapter._ensure_director_execution_profile(
        message="Implement the service.", context=context, metadata=metadata, workspace=str(tmp_path)
    )
    facts = project_role_request_facts(context_override=context, metadata=metadata)
    override = facts.context_override
    override["_transaction_kernel_prebuilt_messages"] = [
        {"role": "system", "content": "You are the Director execution role."},
        {"role": "user", "content": "Implement the service."},
    ]
    override["_transaction_kernel_forced_tool_definitions"] = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {
                    "type": "object",
                    "properties": {"file": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["file", "content"],
                },
            },
        }
    ]
    preparer = LLMRequestPreparer(workspace=str(tmp_path), formatter=None, model_catalog=_LargeModelCatalog())
    prepared = await preparer._prepare_llm_request(
        profile=SimpleNamespace(
            role_id="director",
            provider_id="provider-a",
            model="large-model",
            tool_policy=SimpleNamespace(whitelist=("write_file",)),
        ),
        system_prompt="You are the Director execution role.",
        context=SimpleNamespace(message="Implement the service.", domain="code", context_override=override),
        temperature=0.15,
        max_tokens=128_000,
        stream=stream,
    )
    native = _build_openai_chat_payload(
        prompt=prepared.ai_request.input,
        model="large-model",
        config=prepared.ai_request.options,
        stream=stream,
    )
    assert native["max_tokens"] == 7000
    assert prepared.ai_request.context["execution_budget"]["max_output_tokens"] == 7000
    assert metadata["llm_max_tokens"] == 7000
    assert native["tools"][0]["function"]["name"] == "write_file"
    assert prepared.ai_request.options["timeout"] == (660 if stage == "first_call" else 120)


@pytest.mark.parametrize(
    ("limits", "want"),
    [
        ({"llm_max_tokens": 2048}, 2048),
        ({"max_output_tokens": "3000"}, 3000),
        ({"max_tokens": 5000}, 5000),
        ({"llm_max_tokens": 7000, "max_output_tokens": 2000}, 2000),
        ({"llm_max_tokens": 200_000}, 128_000),
        ({"llm_max_tokens": True, "max_tokens": -1}, 128_000),
        ({"llm_max_tokens": "invalid", "max_output_tokens": 0}, 128_000),
        ({}, 128_000),
    ],
)
def test_strategy_defaults_cannot_enlarge_explicit_call_limit(limits: dict[str, Any], want: int) -> None:
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.ts"])
    strategy = resolve_task_execution_strategy(profile)
    context = dict(limits)
    metadata: dict[str, Any] = {}
    apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context["llm_max_tokens"] == want
    assert context["max_output_tokens"] == want
    assert metadata["llm_max_tokens"] == want
    # Call admission narrows sampling, not the immutable task/authorization contract.
    assert context["task_execution_contract"]["context_budget"]["output_budget_tokens"] == 128_000
    assert strategy.output_budget_tokens == 128_000
