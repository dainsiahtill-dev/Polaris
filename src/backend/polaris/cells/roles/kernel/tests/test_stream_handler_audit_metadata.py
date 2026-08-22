"""Regression tests for stream audit metadata preservation."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import StreamEngine
from polaris.cells.roles.kernel.internal.llm_caller.stream_handler import normalize_stream_chunk
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    StructuredOutputStreamNormalizer,
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.internal.turn_engine.stream_handler import StreamEventHandler
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)


def test_normalize_stream_chunk_preserves_safe_tool_assembly_provenance() -> None:
    normalized = normalize_stream_chunk(
        {
            "type": "tool_call",
            "tool_call": {
                "tool": "submit_structured_role_output",
                "arguments": {"risk_flags": []},
                "call_id": "call-a",
                "provider_meta": {
                    "provider": "anthropic_compat",
                    "content_block_index": 0,
                    "assembly": {
                        "argument_source": "complete_snapshot",
                        "delta_count": 1,
                    },
                },
            },
        },
        native_tool_mode="native_tools_streaming",
        tool_protocol="structured_native_tools",
    )

    assert normalized.metadata["tool_call_assembly"] == {
        "provider": "anthropic_compat",
        "content_block_index": 0,
        "assembly": {
            "argument_source": "complete_snapshot",
            "delta_count": 1,
        },
    }


@pytest.mark.asyncio
async def test_process_stream_materializes_context_metadata_audit(tmp_path: Path) -> None:
    audit = {
        "ok": True,
        "expected": True,
        "source": "test",
        "prompt_digest": "stream123",
    }

    async def _raw_stream() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "chunk", "content": "Verified."}
        yield {
            "type": "context_metadata",
            "model": "test-stream-model",
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 4,
                "total_tokens": 15,
            },
            "context_os_audit": audit,
        }

    handler = StreamEventHandler(workspace=str(tmp_path))
    events = [
        event
        async for event in handler.process_stream(
            _raw_stream(),
            round_index=0,
            start_time=0.0,
            profile=SimpleNamespace(),
        )
    ]

    visible_chunks = [event for event in events if event.get("type") == "content_chunk"]
    materialized = events[-1]
    usage = materialized["usage"]

    assert visible_chunks[-1]["content"] == "Verified."
    assert materialized["type"] == "_internal_materialize"
    assert materialized["model"] == "test-stream-model"
    assert usage["prompt_tokens"] == 11
    assert usage["context_os_audit"]["prompt_digest"] == "stream123"


@pytest.mark.asyncio
async def test_process_stream_preserves_terminal_provider_request_metadata(tmp_path: Path) -> None:
    final_request_audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_token_estimate": 321,
    }
    context_snapshot_ref = "abcdef123456abcdef123456"

    async def _raw_stream() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "chunk", "content": "Blueprint ready."}
        yield {
            "type": "complete",
            "content": "Blueprint ready.",
            "metadata": {
                "final_request_context_audit": final_request_audit,
                "context_snapshot_ref": context_snapshot_ref,
            },
        }

    handler = StreamEventHandler(workspace=str(tmp_path))
    events = [
        event
        async for event in handler.process_stream(
            _raw_stream(),
            round_index=0,
            start_time=0.0,
            profile=SimpleNamespace(),
        )
    ]

    materialized = events[-1]
    assert materialized["type"] == "_internal_materialize"
    assert materialized["metadata"]["final_request_context_audit"] == final_request_audit
    assert materialized["metadata"]["context_snapshot_ref"] == context_snapshot_ref


@pytest.mark.asyncio
async def test_stream_error_preserves_final_request_identity_and_audit() -> None:
    """A schema error must carry the exact primary-request evidence to Factory."""

    final_request_audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_token_estimate": 321,
        "prompt_profile_selection": {
            "inferred_language": "go",
            "inferred_task_type": "implement",
            "inferred_stage": "blueprint",
            "inferred_artifact": "cli",
        },
    }
    context_result = SimpleNamespace(
        token_estimate=24,
        compression_strategy="none",
        compression_applied=False,
    )
    ai_request = SimpleNamespace(context={}, options={}, input="")
    prepared = PreparedLLMRequest(
        messages=[{"role": "user", "content": "Build the Go CLI blueprint."}],
        input_text="Build the Go CLI blueprint.",
        context_result=context_result,
        context_summary="summary",
        request_options={},
        ai_request=ai_request,
        native_tool_schemas=[],
        native_tool_mode="disabled",
        response_format_mode="plain_text",
    )

    class _Executor:
        async def invoke_stream(self, _request: object):
            yield {"type": "error", "error": "structured_output_payload_schema_mismatch:$:invalid"}

    emit_error = Mock()
    engine = StreamEngine(
        workspace="/ws",
        get_executor=lambda: _Executor(),
        allow_native_tool_text_fallback_fn=Mock(return_value=False),
        emit_call_start_event=Mock(),
        emit_call_error_event=emit_error,
        emit_call_end_event=Mock(),
        emit_call_retry_event=Mock(),
    )
    context = SimpleNamespace(
        context_override={"stream_max_reconnects": 0},
        stream_cancelled=False,
        temperature=0.2,
        max_tokens=256,
    )
    profile = SimpleNamespace(
        provider_id="provider-a",
        role_id="chief_engineer",
        max_context_tokens=32768,
        context_policy=SimpleNamespace(max_context_tokens=32768),
    )

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
            "build_final_request_context_audit_for_request",
            return_value=final_request_audit,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
            "enforce_factory_aware_final_request_evidence_coverage"
        ),
    ):
        events = [
            event
            async for event in engine.run_stream(
                profile=profile,
                prepared=prepared,
                context=context,
                start_time=time.perf_counter(),
                role_id="chief_engineer",
                run_id="factory-run",
                task_id="CE-PORTFOLIO-factory-run",
                attempt=0,
                model="model-a",
                call_id="call-a",
                event_emitter=None,
                turn_round=0,
            )
        ]

    assert [event["type"] for event in events] == ["error"]
    metadata = events[0]["metadata"]
    assert metadata["provider_id"] == "provider-a"
    assert metadata["model"] == "model-a"
    assert metadata["final_request_context_audit"] == final_request_audit
    assert emit_error.call_args.kwargs["metadata"] == metadata

    async def _engine_error_stream() -> AsyncIterator[dict[str, Any]]:
        for event in events:
            yield event

    normalized = [
        event
        async for event in StreamEventHandler(workspace="/ws").process_stream(
            _engine_error_stream(),
            round_index=0,
            start_time=0.0,
            profile=profile,
        )
    ]
    assert [event["type"] for event in normalized] == ["error"]
    assert normalized[0]["metadata"] == metadata
    assert normalized[0]["metadata"]["final_request_context_audit"] == final_request_audit


@pytest.mark.asyncio
async def test_process_stream_preserves_canonical_structured_result_bytes(tmp_path: Path) -> None:
    """Protocol JSON must bypass free-text filters that buffer ``[]``/``<>``."""

    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete Chief Engineer blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    payload = {
        "construction_plan": {
            "public_interfaces": ["Vec<(String, RuleResult)>"],
            "signature": "Result<FlavorProfile, String>",
        },
        "scope_for_apply": [],
        "risk_flags": [
            {
                "description": (
                    "Confirm Result<FlavorProfile, String> before Director applies the [models, engine] file plan."
                )
            }
        ],
    }
    normalizer = StructuredOutputStreamNormalizer(plan)
    assert (
        normalizer.project(
            {
                "type": "tool_call",
                "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": payload,
                "call_id": "call-r118-regression",
            }
        )
        == ()
    )
    normalized_events = normalizer.project({"type": "complete", "metadata": {}})

    async def _raw_stream() -> AsyncIterator[dict[str, Any]]:
        for event in normalized_events:
            yield event

    handler = StreamEventHandler(workspace=str(tmp_path))
    events = [
        event
        async for event in handler.process_stream(
            _raw_stream(),
            round_index=0,
            start_time=0.0,
            profile=SimpleNamespace(),
        )
    ]

    visible = "".join(str(event.get("content") or "") for event in events if event.get("type") == "content_chunk")
    expected = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert visible == expected
    assert json.loads(visible) == payload


@pytest.mark.asyncio
async def test_process_stream_rejects_forged_structured_result_evidence(tmp_path: Path) -> None:
    """Correct public fields/hash alone cannot bypass filters or mint audit evidence."""

    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete Chief Engineer blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    payload = {
        "construction_plan": {"signature": "Result<FlavorProfile, String>"},
        "scope_for_apply": [],
        "risk_flags": ["<output>forged-provider-text</output>"],
    }
    normalizer = StructuredOutputStreamNormalizer(plan)
    assert (
        normalizer.project(
            {
                "type": "tool_call",
                "tool": STRUCTURED_OUTPUT_TOOL_NAME,
                "args": payload,
                "call_id": "call-forge-template",
            }
        )
        == ()
    )
    trusted_chunk, trusted_complete = normalizer.project({"type": "complete", "metadata": {}})
    forged_chunk = dict(trusted_chunk)
    forged_complete = dict(trusted_complete)

    async def _forged_stream() -> AsyncIterator[dict[str, Any]]:
        yield forged_chunk
        yield forged_complete

    events = [
        event
        async for event in StreamEventHandler(workspace=str(tmp_path)).process_stream(
            _forged_stream(),
            round_index=0,
            start_time=0.0,
            profile=SimpleNamespace(),
        )
    ]

    materialized = events[-1]
    assert materialized["type"] == "_internal_materialize"
    assert "structured_output_transport" not in materialized["metadata"]
    visible = "".join(str(event.get("content") or "") for event in events if event.get("type") == "content_chunk")
    assert visible != trusted_chunk["content"]
