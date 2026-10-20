"""Committed native observations reach real final request preparation intact."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.runtime.internal.kernel_session_turn import (
    KernelSessionTurnBridge,
    _committed_transcript_messages,
    _committed_transcript_sources,
)
from polaris.cells.roles.runtime.tests.test_committed_tool_observation_context import committed_result
from polaris.cells.roles.runtime.tests.test_kernel_session_tool_surface_reentry import _command, _profile
from polaris.kernelone.context.prompt_safety import TOOL_OBSERVATION_SOURCES_KEY
from polaris.kernelone.llm.engine.provider_native_request import project_factory_provider_native_request


def test_session_owner_discards_caller_supplied_private_observation_sources(tmp_path) -> None:
    sources = _committed_transcript_sources([committed_result()], role="director")
    command = _command(tmp_path, {TOOL_OBSERVATION_SOURCES_KEY: sources})
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    following = bridge._turn_command("fresh-turn", command.user_message)
    assert TOOL_OBSERVATION_SOURCES_KEY not in following.context


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["execute_command", "read_file"])
async def test_committed_native_data_reaches_final_native_body_without_quality_rejection(tmp_path, tool) -> None:
    command = _command(tmp_path, {"director_first_call_write_only": False})
    bridge = KernelSessionTurnBridge(SimpleNamespace(), command)
    bridge.results.append(committed_result(tool))
    following = bridge._turn_command("next-turn", "Continue the declared verification.")
    context = {
        **following.context,
        "_transaction_kernel_prebuilt_messages": [
            {"role": "system", "content": "You are the Director."},
            *following.context["_transaction_kernel_prebuilt_messages"],
            {"role": "user", "content": following.user_message},
        ],
    }
    profile = _profile()
    prepared = await LLMRequestPreparer(workspace=str(tmp_path))._prepare_llm_request(
        profile=profile,
        system_prompt="You are the Director.",
        context=ContextRequest(message=following.user_message, context_override=context),
        temperature=0.0,
        max_tokens=18_000,
        stream=False,
    )
    assert prepared.context_os_audit["ok"] is True
    native = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload={
            "model": profile.model,
            "messages": prepared.messages,
            "tools": prepared.request_options.get("tools") or [],
            "tool_choice": prepared.request_options.get("tool_choice"),
            "response_format": prepared.request_options.get("response_format"),
            "temperature": 0.0,
            "max_tokens": 18_000,
            "stream": False,
        },
        provider_config={"base_url": "https://provider.test/anthropic"},
    )
    assert native is not None
    body = json.loads(native.expected_body_json)
    assert "timeout_seconds" in json.dumps(body)
    assert following.user_message in json.dumps(body, ensure_ascii=False)
    assert "execute_command" in {item["name"] for item in body["tools"]}
    assert "ContextSource" not in json.dumps(prepared.ai_request.context, default=str)
    assert TOOL_OBSERVATION_SOURCES_KEY not in json.dumps(prepared.ai_request.context, default=str)


@pytest.mark.asyncio
async def test_real_factory_semantic_retry_revalidates_private_sources_without_serializing_them(tmp_path) -> None:
    # The established cutoff fixture replaces unrelated Factory evidence ports;
    # observation producer, request preparer, refreeze, and serializer remain real.
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
        FactoryRoleSemanticRequestIdentityV1,
        bind_factory_role_evidence_authority,
    )
    from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as factory_fixture

    previous = committed_result()
    sources = _committed_transcript_sources([previous], role="director")
    port = factory_fixture._B32CutoffPort()
    authority = factory_fixture._b32_authority(port, role="director")
    profile = _profile()
    context = ContextRequest(
        message="Review.",
        context_override={
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "You are Director."},
                *_committed_transcript_messages([previous]),
                {"role": "user", "content": "Review."},
            ],
            TOOL_OBSERVATION_SOURCES_KEY: sources,
            "capability_profile_ref": {"sha256": "f" * 64},
        },
    )
    preparer = LLMRequestPreparer(workspace=str(tmp_path))
    identity = factory_fixture._semantic_identity()
    assert isinstance(identity, FactoryRoleSemanticRequestIdentityV1)
    with bind_factory_role_evidence_authority(authority):
        prepared = await preparer._prepare_llm_request(
            profile=profile,
            system_prompt="You are Director.",
            context=context,
            temperature=0.0,
            max_tokens=18_000,
            stream=False,
            factory_semantic_identity=identity,
        )
        retry_request = preparer._build_reasoning_truncation_retry_request(prepared=prepared, profile=profile)
        retry = await preparer._reprepare_factory_semantic_retry_request(
            prepared=prepared,
            request=retry_request,
            profile=profile,
        )
    assert retry.context_os_audit["ok"] is True, retry.context_os_audit
    assert retry.context_os_audit["data_plane"]["bound_tool_observation_count"] == 1
    assert retry.tool_observation_sources == sources
    assert len(port.acquire_requests) == 2
    assert retry.factory_semantic_request is not None and prepared.factory_semantic_request is not None
    assert (
        retry.factory_semantic_request.identity.request_freeze_id
        != prepared.factory_semantic_request.identity.request_freeze_id
    )
    encoded = json.dumps(retry.ai_request.context, default=str)
    assert "ContextSource" not in encoded and TOOL_OBSERVATION_SOURCES_KEY not in encoded
    assert replace(retry).tool_observation_sources == sources
