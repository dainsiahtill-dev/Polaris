"""Task trace domain model tests."""
import pytest
import sys
from pathlib import Path

# Add src/backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "backend"))

from app.orchestration.events.task_trace import (
    TaskTraceEvent,
    TaskTraceBuilder,
    sanitize_step_detail,
)


def test_task_trace_builder():
    """测试 TaskTraceBuilder 构建事件."""
    builder = TaskTraceBuilder(run_id="test-run", role="director", task_id="task-1")
    event = builder.build(
        phase="planning",
        step_kind="phase",
        step_title="Planning started",
        step_detail="Starting planning phase",
        status="started",
    )
    assert event.run_id == "test-run"
    assert event.role == "director"
    assert event.task_id == "task-1"
    assert event.phase == "planning"
    assert event.step_kind == "phase"
    assert event.step_title == "Planning started"
    assert event.step_detail == "Starting planning phase"
    assert event.status == "started"
    assert event.seq == 1
    assert event.event_id is not None
    assert event.ts is not None


def test_task_trace_builder_sequence():
    """测试 TaskTraceBuilder 序列号递增."""
    builder = TaskTraceBuilder(run_id="test-run", role="pm", task_id="task-1")

    event1 = builder.build(
        phase="planning",
        step_kind="phase",
        step_title="Step 1",
        step_detail="Detail 1",
        status="started",
    )
    event2 = builder.build(
        phase="executing",
        step_kind="tool",
        step_title="Step 2",
        step_detail="Detail 2",
        status="running",
    )
    event3 = builder.build(
        phase="completed",
        step_kind="phase",
        step_title="Step 3",
        step_detail="Detail 3",
        status="completed",
    )

    assert event1.seq == 1
    assert event2.seq == 2
    assert event3.seq == 3


def test_task_trace_builder_to_ws_payload():
    """测试 TaskTraceBuilder 转换为 WebSocket payload."""
    builder = TaskTraceBuilder(run_id="test-run", role="director", task_id="task-1")
    event = builder.build(
        phase="planning",
        step_kind="phase",
        step_title="Planning started",
        step_detail="Starting planning phase",
        status="started",
        attempt=1,
        visibility="debug",
        custom_ref="value123",
    )

    payload = builder.to_ws_payload(event)

    assert payload["type"] == "task_trace"
    assert payload["event"]["run_id"] == "test-run"
    assert payload["event"]["role"] == "director"
    assert payload["event"]["phase"] == "planning"
    assert payload["event"]["status"] == "started"
    assert payload["event"]["attempt"] == 1
    assert payload["event"]["visibility"] == "debug"
    assert payload["event"]["refs"]["custom_ref"] == "value123"


def test_sanitize_step_detail_utf8():
    """测试 UTF-8 处理."""
    detail = "测试中文内容"
    result = sanitize_step_detail(detail)
    assert result == detail


def test_sanitize_step_detail_emoji():
    """测试 Emoji 处理."""
    detail = "测试 Emoji \ud83c\udf89 庆祝"
    result = sanitize_step_detail(detail)
    assert "\ud83c\udf89" in result or result == "测试 Emoji  庆祝"


def test_sanitize_step_detail_masking_api_key():
    """测试 API key 敏感信息掩码."""
    detail = "API key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
    result = sanitize_step_detail(detail)
    assert "[MASKED]" in result
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result


def test_sanitize_step_detail_masking_token():
    """测试 Token 敏感信息掩码."""
    detail = "Token: abcdefghijklmnopqrstuvwxyz123456"
    result = sanitize_step_detail(detail)
    assert "[MASKED]" in result
    assert "abcdefghijklmnopqrstuvwxyz123456" not in result


def test_sanitize_step_detail_truncate():
    """测试截断."""
    # Use mixed content to avoid masking (not all alphanumeric)
    detail = "Executing task step with detailed information. " * 10
    result = sanitize_step_detail(detail, max_length=100)
    assert len(result) <= 100
    assert result.endswith("...")


def test_sanitize_step_detail_truncate_default():
    """测试默认截断长度."""
    # Use mixed content to avoid masking (not all alphanumeric)
    detail = "Executing task step with detailed information. " * 20
    result = sanitize_step_detail(detail)
    assert len(result) <= 280
    assert result.endswith("...")


def test_sanitize_step_detail_empty():
    """测试空字符串处理."""
    assert sanitize_step_detail("") == ""
    assert sanitize_step_detail(None) == ""


def test_sanitize_step_detail_short():
    """测试短字符串不截断."""
    detail = "Short message"
    result = sanitize_step_detail(detail)
    assert result == detail


def test_task_trace_event_defaults():
    """测试 TaskTraceEvent 默认值."""
    event = TaskTraceEvent(
        event_id="test-id",
        run_id="run-1",
        role="pm",
        task_id="task-1",
        seq=1,
        phase="planning",
        step_kind="phase",
        step_title="Test",
        step_detail="Detail",
        status="started",
    )
    assert event.attempt == 0
    assert event.visibility == "summary"
    assert event.ts == ""
    assert event.refs == {}
