"""Tests for workflow_runtime internal task_trace module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.task_trace import (
    TaskTraceBuilder,
    sanitize_step_detail,
)


class TestSanitizeStepDetail:
    def test_empty(self) -> None:
        assert sanitize_step_detail("") == ""
        assert sanitize_step_detail(None) == ""  # type: ignore[arg-type]

    def test_truncate(self) -> None:
        long_str = "x" * 300
        result = sanitize_step_detail(long_str, max_length=50)
        # The string may be masked first (32+ alphanum chars), then truncated
        assert len(result) <= 50
        assert result.endswith("...") or "[MASKED]" in result

    def test_mask_api_key(self) -> None:
        detail = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize_step_detail(detail)
        assert "[MASKED]" in result

    def test_mask_long_token(self) -> None:
        detail = "token abcdefghijklmnopqrstuvwxyz123456"
        result = sanitize_step_detail(detail)
        assert "[MASKED]" in result


class TestTaskTraceBuilder:
    def test_build_increments_seq(self) -> None:
        builder = TaskTraceBuilder(run_id="r1", role="pm", task_id="t1")
        e1 = builder.build(phase="plan", step_kind="llm", step_title="title", step_detail="detail", status="started")
        e2 = builder.build(phase="plan", step_kind="llm", step_title="title", step_detail="detail", status="started")
        assert e1.seq == 1
        assert e2.seq == 2
        assert e1.run_id == "r1"
        assert e1.role == "pm"

    def test_to_ws_payload(self) -> None:
        builder = TaskTraceBuilder(run_id="r1", role="pm", task_id="t1")
        event = builder.build(phase="plan", step_kind="llm", step_title="t", step_detail="d", status="started")
        payload = builder.to_ws_payload(event)
        assert payload["type"] == "task_trace"
        assert payload["event"]["event_id"] == event.event_id
