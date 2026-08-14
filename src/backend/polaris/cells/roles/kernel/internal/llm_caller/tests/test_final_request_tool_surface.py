"""Regression tests for physical final-request tool authority."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.final_request_tool_surface import (
    assert_native_tool_call_in_final_request_surface,
    assert_tool_in_final_request_surface,
    final_request_allowed_tool_names,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import StreamEngine


def _tool(name: str) -> dict[str, object]:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def test_physical_forced_tool_overrides_broad_semantic_surface() -> None:
    prepared = SimpleNamespace(
        request_options={"tools": [_tool("read_file"), _tool("edit_file")]},
        native_tool_schemas=[_tool("read_file"), _tool("edit_file")],
    )
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    assert final_request_allowed_tool_names(active_request=active_request, prepared=prepared) == {"edit_file"}
    assert_tool_in_final_request_surface(
        tool_name="edit_file",
        active_request=active_request,
        prepared=prepared,
    )

    with pytest.raises(
        RuntimeError,
        match=r"provider_tool_surface_violation: requested=read_file; allowed=edit_file",
    ):
        assert_tool_in_final_request_surface(
            tool_name="read_file",
            active_request=active_request,
            prepared=prepared,
        )


def test_tool_alias_is_normalized_before_authorization() -> None:
    prepared = SimpleNamespace(request_options={}, native_tool_schemas=[])
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    assert_tool_in_final_request_surface(
        tool_name="edit-file",
        active_request=active_request,
        prepared=prepared,
    )


def test_edit_file_empty_search_is_rejected_before_physical_execution() -> None:
    """A forced mutation may not satisfy the turn with a no-effect edit."""

    prepared = SimpleNamespace(request_options={}, native_tool_schemas=[])
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"provider_tool_surface_violation: requested=edit_file; "
            r"allowed=edit_file; invalid=empty_search"
        ),
    ):
        assert_tool_in_final_request_surface(
            tool_name="edit_file",
            tool_arguments={"file": "engine/rules.go", "search": "", "replace": "fixed"},
            active_request=active_request,
            prepared=prepared,
        )


def test_top_level_native_edit_file_empty_search_is_rejected_before_physical_execution() -> None:
    """Normalized top-level envelopes receive the same argument guard as OpenAI envelopes."""

    prepared = SimpleNamespace(request_options={}, native_tool_schemas=[])
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"provider_tool_surface_violation: requested=edit_file; "
            r"allowed=edit_file; invalid=empty_search"
        ),
    ):
        assert_native_tool_call_in_final_request_surface(
            native_tool_call={
                "name": "edit_file",
                "arguments": {"file": "engine/rules.go", "search": "", "replace": "fixed"},
            },
            active_request=active_request,
            prepared=prepared,
        )


def test_edit_file_non_empty_search_remains_authorized() -> None:
    prepared = SimpleNamespace(request_options={}, native_tool_schemas=[])
    active_request = SimpleNamespace(
        options={
            "tools": [_tool("edit_file")],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        }
    )

    assert_tool_in_final_request_surface(
        tool_name="edit_file",
        tool_arguments={"file": "engine/rules.go", "search": "old", "replace": "new"},
        active_request=active_request,
        prepared=prepared,
    )


def test_no_final_tool_surface_rejects_provider_tool_call() -> None:
    prepared = SimpleNamespace(request_options={"tools": []}, native_tool_schemas=[])
    active_request = SimpleNamespace(options={"tools": []})

    with pytest.raises(
        RuntimeError,
        match=r"provider_tool_surface_violation: requested=read_file; allowed=<none>",
    ):
        assert_tool_in_final_request_surface(
            tool_name="read_file",
            active_request=active_request,
            prepared=prepared,
        )


@pytest.mark.asyncio
async def test_stream_rejects_out_of_surface_call_before_emitting_tool_event() -> None:
    """A provider cannot execute ``read_file`` when only ``edit_file`` was sent."""

    edit_tool = _tool("edit_file")
    context_result = SimpleNamespace(
        token_estimate=16,
        compression_strategy="none",
        compression_applied=False,
    )
    active_request = SimpleNamespace(
        context={"chat_messages": [{"role": "user", "content": "repair engine/rules.go"}]},
        options={
            "tools": [edit_tool],
            "tool_choice": {"type": "function", "function": {"name": "edit_file"}},
        },
        input="repair engine/rules.go",
    )
    prepared = PreparedLLMRequest(
        messages=[{"role": "user", "content": "repair engine/rules.go"}],
        input_text="repair engine/rules.go",
        context_result=context_result,
        context_summary="repair",
        request_options=dict(active_request.options),
        ai_request=active_request,
        native_tool_schemas=[edit_tool],
        native_tool_mode="native_tools_streaming",
        response_format_mode="plain_text",
    )

    class _Executor:
        async def invoke_stream(self, _request: object):
            yield {
                "type": "tool_call",
                "tool_call": {
                    "tool": "read_file",
                    "args": {"path": "engine/rules.go"},
                    "call_id": "call-read",
                },
            }

    engine = StreamEngine(
        workspace="/ws",
        get_executor=lambda: _Executor(),
        allow_native_tool_text_fallback_fn=Mock(return_value=False),
        emit_call_start_event=Mock(),
        emit_call_error_event=Mock(),
        emit_call_end_event=Mock(),
        emit_call_retry_event=Mock(),
    )
    context = SimpleNamespace(
        context_override={"stream_max_reconnects": 0},
        stream_cancelled=False,
        temperature=0.2,
        max_tokens=128,
    )

    with patch(
        "polaris.cells.roles.kernel.internal.llm_caller.stream_engine.build_final_request_context_audit_for_request",
        return_value={"final_request_token_estimate": 16},
    ):
        events = [
            event
            async for event in engine.run_stream(
                profile=SimpleNamespace(provider_id="provider", role_id="director"),
                prepared=prepared,
                context=context,
                start_time=time.perf_counter(),
                role_id="director",
                run_id="run-1",
                task_id="task-1",
                attempt=0,
                model="model",
                call_id="call-1",
                event_emitter=None,
                turn_round=0,
            )
        ]

    assert not any(event.get("type") == "tool_call" for event in events)
    assert [event.get("error") for event in events if event.get("type") == "error"] == [
        "provider_tool_surface_violation: requested=read_file; allowed=edit_file"
    ]
