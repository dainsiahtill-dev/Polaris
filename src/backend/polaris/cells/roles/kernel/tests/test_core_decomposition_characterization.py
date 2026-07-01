"""Characterization tests for RoleExecutionKernel core.py decomposition.

These tests pin the CURRENT observable behavior of the core run/stream
coordinators after extraction into sibling collaborator modules. They are
behavior-preserving guards:

- ``execute_transaction_kernel_stream`` event-translation matrix
  (TurnPhaseEvent / ContentChunkEvent / ToolBatchEvent / CompletionEvent /
  ErrorEvent -> emitted dicts), including the failed-completion early-return
  and the finalization-chunk reset semantics.
- ``run`` retry / quality loop branches: tool-only turn pass, validation
  failure retry-then-exhaust, error pass-through (no retry), and success.

They intentionally drive the real coordinator methods while stubbing only the
TransactionKernel boundary (``create_transaction_kernel`` / the
RoleContextGateway), so the mapping stays byte-stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.turn_execution import execute_transaction_kernel_stream
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    FinalizationEvent,
    ToolBatchEvent,
    TurnPhaseEvent,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult


def _patch_transaction_kernel_factory(return_value: Any) -> Any:
    return patch(
        "polaris.cells.roles.kernel.internal.kernel.turn_execution.create_transaction_kernel",
        return_value=return_value,
    )


@dataclass
class _MockProfile:
    role_id: str = "pm"
    version: str = "1.0"
    model: str = "test-model"
    provider_id: str = "openai"
    tool_policy: Any = field(default_factory=lambda: MagicMock(policy_id="tp1", whitelist=["read_file"]))


@dataclass
class _MockFingerprint:
    full_hash: str = "abc123"


@dataclass
class _MockRequest:
    message: str = "hello"
    history: list[tuple[str, str]] = field(default_factory=list)
    max_retries: int = 0
    validate_output: bool = False
    task_id: str | None = None
    run_id: str | None = "run_123"
    workspace: str = "."
    prompt_appendix: str = ""
    system_prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    context_override: dict[str, Any] | None = field(default_factory=lambda: {"context_os_snapshot": {}})
    tool_results: list[dict[str, Any]] = field(default_factory=list)


def _context_gateway(token_estimate: int = 37) -> MagicMock:
    return MagicMock(
        build_context=AsyncMock(
            return_value=SimpleNamespace(
                messages=[{"role": "user", "content": "hi"}],
                token_estimate=token_estimate,
                metadata={},
            )
        ),
        record_projection_outcome=MagicMock(return_value={"route_weight": 0.31}),
    )


def _event_emitter() -> SimpleNamespace:
    return SimpleNamespace(
        resolve_observer_run_id=lambda _role, run_id: str(run_id or "run_123"),
        emit_runtime_llm_event=MagicMock(),
    )


# ──────────────────────────────────────────────────────────────────────────
# Stream event-translation matrix
# ──────────────────────────────────────────────────────────────────────────


async def _drive_stream(kernel: RoleExecutionKernel, events: list[Any]) -> list[dict[str, Any]]:
    async def _fake_execute_stream(*_a: Any, **_k: Any) -> Any:
        for event in events:
            yield event

    tk = MagicMock(execute_stream=_fake_execute_stream)
    gateway = _context_gateway()
    uep = SimpleNamespace(publish_stream_event=AsyncMock())
    with (
        _patch_transaction_kernel_factory(return_value=tk),
        patch(
            "polaris.cells.roles.kernel.public.service.RoleContextGateway",
            return_value=gateway,
        ),
    ):
        collected: list[dict[str, Any]] = []
        async for event_dict in execute_transaction_kernel_stream(
            kernel,
            role="pm",
            profile=_MockProfile(),
            request=_MockRequest(),
            system_prompt="sys",
            fingerprint=_MockFingerprint(),
            stream_run_id="run_123",
            uep_publisher=uep,  # type: ignore[arg-type]
        ):
            collected.append(event_dict)
    return collected


class TestStreamEventTranslationMatrix:
    @pytest.mark.asyncio
    async def test_turn_phase_event_maps_to_phase_dict(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [TurnPhaseEvent(turn_id="t1", phase="decision_requested", timestamp_ms=1, metadata={"k": "v"})],
        )
        assert out == [{"type": "decision_requested", "turn_id": "t1", "metadata": {"k": "v"}}]

    @pytest.mark.asyncio
    async def test_content_chunk_visible_and_thinking(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [
                ContentChunkEvent(turn_id="t1", chunk="hello "),
                ContentChunkEvent(turn_id="t1", chunk="brain", is_thinking=True),
                ContentChunkEvent(turn_id="t1", chunk="world"),
            ],
        )
        assert out[0] == {"type": "content_chunk", "content": "hello ", "turn_id": "t1"}
        assert out[1] == {"type": "thinking_chunk", "content": "brain", "turn_id": "t1"}
        assert out[2] == {"type": "content_chunk", "content": "world", "turn_id": "t1"}

    @pytest.mark.asyncio
    async def test_finalization_chunk_resets_accumulated_content(self) -> None:
        # is_finalization=True chunk REPLACES (not appends) the accumulated content.
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [
                ContentChunkEvent(turn_id="t1", chunk="draft draft"),
                ContentChunkEvent(turn_id="t1", chunk="FINAL", is_finalization=True),
                CompletionEvent(turn_id="t1", status="success", duration_ms=5, llm_calls=1, tool_calls=0),
            ],
        )
        complete = next(e for e in out if e["type"] == "complete")
        assert complete["content"] == "FINAL"

    @pytest.mark.asyncio
    async def test_tool_batch_started_and_success(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [
                ToolBatchEvent(
                    turn_id="t1",
                    batch_id="b1",
                    tool_name="write_file",
                    call_id="c1",
                    status="started",
                    progress=0.0,
                    arguments={"path": "a.txt"},
                ),
                ToolBatchEvent(
                    turn_id="t1",
                    batch_id="b1",
                    tool_name="write_file",
                    call_id="c1",
                    status="success",
                    progress=1.0,
                    result="ok",
                ),
            ],
        )
        assert out[0]["type"] == "tool_call"
        assert out[0]["tool"] == "write_file"
        assert out[0]["args"] == {"path": "a.txt"}
        assert out[1]["type"] == "tool_result"
        assert out[1]["status"] == "success"
        assert out[1]["result"] == "ok"

    @pytest.mark.asyncio
    async def test_finalization_event_is_skipped(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [
                FinalizationEvent(turn_id="t1", mode="llm_once"),
                CompletionEvent(turn_id="t1", status="success", duration_ms=1, llm_calls=1, tool_calls=0),
            ],
        )
        assert [e["type"] for e in out] == ["complete"]

    @pytest.mark.asyncio
    async def test_completion_success_emits_complete_with_result(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [
                ContentChunkEvent(turn_id="t1", chunk="answer"),
                CompletionEvent(turn_id="t1", status="success", duration_ms=12, llm_calls=2, tool_calls=1),
            ],
        )
        complete = out[-1]
        assert complete["type"] == "complete"
        assert complete["status"] == "success"
        assert complete["content"] == "answer"
        assert complete["duration_ms"] == 12
        assert isinstance(complete["result"], RoleTurnResult)
        assert complete["result"].content == "answer"
        assert complete["result"].execution_stats["transaction_kernel"] is True

    @pytest.mark.asyncio
    async def test_completion_failed_maps_to_error_and_returns(self) -> None:
        # A failed/suspended completion maps to a SINGLE error event and stops the stream.
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [
                CompletionEvent(turn_id="t1", status="failed", error="boom"),
                # This trailing event must NOT be emitted (early return on failed).
                ContentChunkEvent(turn_id="t1", chunk="late"),
            ],
        )
        assert len(out) == 1
        assert out[0]["type"] == "error"
        assert out[0]["error"] == "boom"
        assert out[0]["error_type"] == "stream_execution_failed"

    @pytest.mark.asyncio
    async def test_error_event_maps_to_error_dict(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        out = await _drive_stream(
            kernel,
            [ErrorEvent(turn_id="t1", error_type="provider_error", message="nope")],
        )
        assert out == [{"type": "error", "error": "nope", "error_type": "provider_error", "turn_id": "t1"}]


# ──────────────────────────────────────────────────────────────────────────
# run() retry / quality loop
# ──────────────────────────────────────────────────────────────────────────


def _make_run_kernel(profile: _MockProfile, quality_checker: Any | None = None) -> RoleExecutionKernel:
    kernel = RoleExecutionKernel(workspace=".", quality_checker=quality_checker)  # type: ignore[arg-type]
    kernel.registry = MagicMock(get_profile_or_raise=MagicMock(return_value=profile))
    prompt_builder = SimpleNamespace(
        build_system_prompt=lambda _profile, _appendix, **_kwargs: "system-prompt",
        build_fingerprint=lambda _profile, _appendix: _MockFingerprint(),
        build_retry_prompt=lambda _system_prompt, _quality_result, _attempt: "retry-prompt",
    )
    kernel.inject_prompt_builder(prompt_builder)  # type: ignore[arg-type]
    return kernel


class TestRunRetryQualityLoop:
    @pytest.mark.asyncio
    async def test_run_error_result_passes_through_without_retry(self) -> None:
        profile = _MockProfile()
        kernel = _make_run_kernel(profile)
        te_result = RoleTurnResult(
            content="partial",
            error="kernel exploded",
            is_complete=False,
            execution_stats={"transaction_kernel": True},
        )
        turn_mock = AsyncMock(return_value=te_result)
        with (
            patch(
                "polaris.cells.roles.kernel.internal.kernel.core.build_context_request",
                return_value=MagicMock(),
            ),
            patch("polaris.cells.roles.kernel.internal.kernel.core.execute_transaction_kernel_turn", new=turn_mock),
            patch.object(kernel, "_get_event_emitter", _event_emitter),
        ):
            result = await kernel.run("pm", _MockRequest(max_retries=3, validate_output=True))
        assert result.error == "kernel exploded"
        assert result.is_complete is False
        # Error path does NOT retry.
        assert turn_mock.await_count == 1
        assert result.execution_stats["kernel_repair_exhausted"] is True

    @pytest.mark.asyncio
    async def test_run_tool_only_turn_passes_quality_without_content(self) -> None:
        profile = _MockProfile()
        kernel = _make_run_kernel(profile)
        te_result = RoleTurnResult(
            content="",
            tool_calls=[{"tool": "write_file"}],
            tool_results=[{"tool": "write_file", "success": True}],
            is_complete=True,
            execution_stats={"transaction_kernel": True},
        )
        with (
            patch(
                "polaris.cells.roles.kernel.internal.kernel.core.build_context_request",
                return_value=MagicMock(),
            ),
            patch(
                "polaris.cells.roles.kernel.internal.kernel.core.execute_transaction_kernel_turn",
                new=AsyncMock(return_value=te_result),
            ),
            patch.object(kernel, "_get_event_emitter", _event_emitter),
        ):
            result = await kernel.run("pm", _MockRequest(validate_output=True))
        assert result.error is None
        assert result.is_complete is True
        assert result.quality_score == 100.0

    @pytest.mark.asyncio
    async def test_run_validation_failure_retries_then_exhausts(self) -> None:
        profile = _MockProfile()
        te_result = RoleTurnResult(
            content="bad output",
            is_complete=True,
            execution_stats={"transaction_kernel": True},
        )
        failing_quality = SimpleNamespace(
            success=False,
            errors=["too short"],
            suggestions=["expand"],
            data={},
            quality_score=10.0,
            quality_passed=False,
        )
        quality_checker = SimpleNamespace(validate_output=lambda *_a, **_k: failing_quality)
        kernel = _make_run_kernel(profile, quality_checker)
        turn_mock = AsyncMock(return_value=te_result)
        with (
            patch(
                "polaris.cells.roles.kernel.internal.kernel.core.build_context_request",
                return_value=MagicMock(),
            ),
            patch("polaris.cells.roles.kernel.internal.kernel.core.execute_transaction_kernel_turn", new=turn_mock),
            patch.object(kernel, "_get_event_emitter", _event_emitter),
        ):
            result = await kernel.run("pm", _MockRequest(max_retries=2, validate_output=True))
        # max_retries=2 -> attempts 0,1,2 = 3 invocations before exhaustion.
        assert turn_mock.await_count == 3
        assert result.is_complete is True
        assert "验证失败" in str(result.error)
        assert result.execution_stats["kernel_repair_exhausted"] is True
        assert result.execution_stats["kernel_repair_retry_count"] == 3

    @pytest.mark.asyncio
    async def test_run_validation_success_returns_structured_output(self) -> None:
        profile = _MockProfile()
        te_result = RoleTurnResult(
            content="good output",
            is_complete=True,
            execution_stats={"transaction_kernel": True},
        )
        passing_quality = SimpleNamespace(
            success=True,
            errors=[],
            suggestions=[],
            data={"parsed": "value"},
            quality_score=95.0,
            quality_passed=True,
        )
        quality_checker = SimpleNamespace(validate_output=lambda *_a, **_k: passing_quality)
        kernel = _make_run_kernel(profile, quality_checker)
        with (
            patch(
                "polaris.cells.roles.kernel.internal.kernel.core.build_context_request",
                return_value=MagicMock(),
            ),
            patch(
                "polaris.cells.roles.kernel.internal.kernel.core.execute_transaction_kernel_turn",
                new=AsyncMock(return_value=te_result),
            ),
            patch.object(kernel, "_get_event_emitter", _event_emitter),
        ):
            result = await kernel.run("pm", _MockRequest(validate_output=True))
        assert result.error is None
        assert result.quality_score == 95.0
        assert result.structured_output == {"parsed": "value"}
        assert result.execution_stats["kernel_repair_exhausted"] is False
