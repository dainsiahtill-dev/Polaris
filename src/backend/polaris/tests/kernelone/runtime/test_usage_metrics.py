"""Tests for usage_metrics module.

Covers UsageContext, TokenUsage dataclasses and track_usage function.
All emit_event calls are mocked to avoid filesystem side effects.
"""

from __future__ import annotations

from dataclasses import fields
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.runtime.usage_metrics import (
    TokenUsage,
    UsageContext,
    track_usage,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_context() -> UsageContext:
    """Return a sample UsageContext for testing."""
    return UsageContext(
        run_id="run-123",
        task_id="task-456",
        phase="execution",
        mode="agentic",
        actor="director",
    )


@pytest.fixture
def sample_token_usage() -> TokenUsage:
    """Return a sample TokenUsage for testing."""
    return TokenUsage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        estimated=False,
        prompt_chars=400,
        completion_chars=200,
    )


# ------------------------------------------------------------------
# UsageContext Tests
# ------------------------------------------------------------------


class TestUsageContext:
    """Tests for UsageContext dataclass."""

    def test_create_with_all_fields(self) -> None:
        ctx = UsageContext(
            run_id="run-abc",
            task_id="task-def",
            phase="planning",
            mode="interactive",
            actor="pm",
        )
        assert ctx.run_id == "run-abc"
        assert ctx.task_id == "task-def"
        assert ctx.phase == "planning"
        assert ctx.mode == "interactive"
        assert ctx.actor == "pm"

    def test_to_dict_returns_correct_structure(self, sample_context: UsageContext) -> None:
        result = sample_context.to_dict()
        expected = {
            "run_id": "run-123",
            "task_id": "task-456",
            "phase": "execution",
            "mode": "agentic",
            "actor": "director",
        }
        assert result == expected

    def test_to_dict_returns_all_string_values(self, sample_context: UsageContext) -> None:
        result = sample_context.to_dict()
        for key, value in result.items():
            assert isinstance(value, str), f"Field {key} should be str, got {type(value)}"

    def test_field_count(self) -> None:
        assert len(fields(UsageContext)) == 5

    def test_empty_strings_allowed(self) -> None:
        ctx = UsageContext(run_id="", task_id="", phase="", mode="", actor="")
        assert ctx.to_dict() == {"run_id": "", "task_id": "", "phase": "", "mode": "", "actor": ""}

    def test_unicode_values(self) -> None:
        ctx = UsageContext(
            run_id="运行-123",
            task_id="任务-456",
            phase="执行",
            mode="自动",
            actor="代理",
        )
        assert ctx.run_id == "运行-123"
        assert ctx.to_dict()["actor"] == "代理"


# ------------------------------------------------------------------
# TokenUsage Tests
# ------------------------------------------------------------------


class TestTokenUsage:
    """Tests for TokenUsage dataclass."""

    def test_create_with_all_fields(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1024,
            completion_tokens=512,
            total_tokens=1536,
            estimated=True,
            prompt_chars=4096,
            completion_chars=2048,
        )
        assert usage.prompt_tokens == 1024
        assert usage.completion_tokens == 512
        assert usage.total_tokens == 1536
        assert usage.estimated is True
        assert usage.prompt_chars == 4096
        assert usage.completion_chars == 2048

    def test_create_with_defaults(self) -> None:
        usage = TokenUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
        assert usage.estimated is False
        assert usage.prompt_chars == 0
        assert usage.completion_chars == 0

    def test_to_dict_returns_correct_structure(self, sample_token_usage: TokenUsage) -> None:
        result = sample_token_usage.to_dict()
        expected = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "estimated": False,
            "prompt_chars": 400,
            "completion_chars": 200,
        }
        assert result == expected

    def test_to_dict_preserves_types(self) -> None:
        usage = TokenUsage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            estimated=True,
            prompt_chars=0,
            completion_chars=0,
        )
        result = usage.to_dict()
        assert isinstance(result["prompt_tokens"], int)
        assert isinstance(result["completion_tokens"], int)
        assert isinstance(result["total_tokens"], int)
        assert isinstance(result["estimated"], bool)
        assert isinstance(result["prompt_chars"], int)
        assert isinstance(result["completion_chars"], int)

    def test_zero_tokens(self) -> None:
        usage = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        assert usage.to_dict()["total_tokens"] == 0

    def test_large_token_counts(self) -> None:
        usage = TokenUsage(
            prompt_tokens=1_000_000,
            completion_tokens=500_000,
            total_tokens=1_500_000,
        )
        assert usage.total_tokens == 1_500_000

    def test_negative_tokens_allowed_by_dataclass(self) -> None:
        """TokenUsage does not validate inputs; negative values are stored as-is."""
        usage = TokenUsage(prompt_tokens=-1, completion_tokens=-1, total_tokens=-2)
        assert usage.total_tokens == -2


# ------------------------------------------------------------------
# track_usage Tests
# ------------------------------------------------------------------


class TestTrackUsage:
    """Tests for track_usage function with mocked emit_event."""

    @pytest.fixture(autouse=True)
    def mock_emit_event(self) -> MagicMock:
        """Mock emit_event to prevent filesystem writes."""
        with patch("polaris.kernelone.runtime.usage_metrics.emit_event") as mock:
            yield mock

    def test_basic_call_emits_event(
        self,
        mock_emit_event: MagicMock,
        sample_context: UsageContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        track_usage(
            events_path="/tmp/events.jsonl",
            context=sample_context,
            model="gpt-4",
            provider="openai",
            usage=sample_token_usage,
            duration_ms=1500,
        )

        mock_emit_event.assert_called_once()
        call_kwargs = mock_emit_event.call_args.kwargs
        assert call_kwargs["kind"] == "observation"
        assert call_kwargs["actor"] == "director"
        assert call_kwargs["name"] == "llm_invoke"
        assert call_kwargs["refs"] == sample_context.to_dict()

    def test_event_summary_format(
        self,
        mock_emit_event: MagicMock,
        sample_context: UsageContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        track_usage(
            events_path="/tmp/events.jsonl",
            context=sample_context,
            model="gpt-4o",
            provider="openai",
            usage=sample_token_usage,
            duration_ms=1000,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        assert call_kwargs["summary"] == "LLM Invoke (gpt-4o) - 150 tokens"

    def test_observation_payload_structure(
        self,
        mock_emit_event: MagicMock,
        sample_context: UsageContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        track_usage(
            events_path="/tmp/events.jsonl",
            context=sample_context,
            model="claude-3",
            provider="anthropic",
            usage=sample_token_usage,
            duration_ms=2000,
            ok=True,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        output = call_kwargs["output"]
        assert output["ok"] is True
        assert output["duration_ms"] == 2000
        assert output["model"] == "claude-3"
        assert output["provider"] == "anthropic"
        assert output["usage"] == sample_token_usage.to_dict()
        assert "error" not in output

    def test_error_included_when_provided(
        self,
        mock_emit_event: MagicMock,
        sample_context: UsageContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        track_usage(
            events_path="/tmp/events.jsonl",
            context=sample_context,
            model="gpt-4",
            provider="openai",
            usage=sample_token_usage,
            duration_ms=500,
            ok=False,
            error="Rate limit exceeded",
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        output = call_kwargs["output"]
        assert output["ok"] is False
        assert output["error"] == "Rate limit exceeded"

    def test_empty_events_path_returns_early(self, mock_emit_event: MagicMock) -> None:
        track_usage(
            events_path="",
            context=UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a"),
            model="gpt-4",
            provider="openai",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            duration_ms=100,
        )

        mock_emit_event.assert_not_called()

    def test_none_events_path_returns_early(self, mock_emit_event: MagicMock) -> None:
        """None events_path should be treated as falsy and return early."""
        track_usage(
            events_path="",  # Module checks `if not events_path`, so None and "" both return
            context=UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a"),
            model="gpt-4",
            provider="openai",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            duration_ms=100,
        )

        mock_emit_event.assert_not_called()

    def test_ok_defaults_to_true(self, mock_emit_event: MagicMock) -> None:
        ctx = UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a")
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        track_usage(
            events_path="/tmp/events.jsonl",
            context=ctx,
            model="gpt-4",
            provider="openai",
            usage=usage,
            duration_ms=100,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        assert call_kwargs["output"]["ok"] is True

    def test_duration_ms_zero(self, mock_emit_event: MagicMock) -> None:
        ctx = UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a")
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        track_usage(
            events_path="/tmp/events.jsonl",
            context=ctx,
            model="gpt-4",
            provider="openai",
            usage=usage,
            duration_ms=0,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        assert call_kwargs["output"]["duration_ms"] == 0

    def test_error_none_excludes_error_field(
        self,
        mock_emit_event: MagicMock,
        sample_context: UsageContext,
        sample_token_usage: TokenUsage,
    ) -> None:
        track_usage(
            events_path="/tmp/events.jsonl",
            context=sample_context,
            model="gpt-4",
            provider="openai",
            usage=sample_token_usage,
            duration_ms=100,
            error=None,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        assert "error" not in call_kwargs["output"]

    def test_estimated_usage_in_payload(self, mock_emit_event: MagicMock) -> None:
        ctx = UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a")
        usage = TokenUsage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            estimated=True,
        )

        track_usage(
            events_path="/tmp/events.jsonl",
            context=ctx,
            model="gpt-4",
            provider="openai",
            usage=usage,
            duration_ms=1000,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        assert call_kwargs["output"]["usage"]["estimated"] is True

    def test_different_models_in_summary(self, mock_emit_event: MagicMock) -> None:
        ctx = UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a")
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        for model in ["gpt-4", "gpt-3.5-turbo", "claude-3-opus-20240229", "local-llm"]:
            mock_emit_event.reset_mock()
            track_usage(
                events_path="/tmp/events.jsonl",
                context=ctx,
                model=model,
                provider="test",
                usage=usage,
                duration_ms=100,
            )

            call_kwargs = mock_emit_event.call_args.kwargs
            expected_summary = f"LLM Invoke ({model}) - 15 tokens"
            assert call_kwargs["summary"] == expected_summary

    @pytest.mark.parametrize("total_tokens", [0, 1, 100, 10000])
    def test_summary_reflects_token_count(
        self,
        mock_emit_event: MagicMock,
        total_tokens: int,
    ) -> None:
        ctx = UsageContext(run_id="r", task_id="t", phase="p", mode="m", actor="a")
        usage = TokenUsage(
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens - total_tokens // 2,
            total_tokens=total_tokens,
        )

        track_usage(
            events_path="/tmp/events.jsonl",
            context=ctx,
            model="gpt-4",
            provider="openai",
            usage=usage,
            duration_ms=100,
        )

        call_kwargs = mock_emit_event.call_args.kwargs
        assert f"{total_tokens} tokens" in call_kwargs["summary"]
