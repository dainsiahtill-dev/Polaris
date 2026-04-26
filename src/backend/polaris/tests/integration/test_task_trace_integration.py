"""Task trace integration tests."""
import pytest
import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

# Add src/backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "backend"))

from application.message_bus import MessageBus, MessageType


@pytest.mark.asyncio
async def test_message_bus_task_trace():
    """测试 MessageBus TASK_TRACE 广播."""
    message_bus = MessageBus()
    received = []

    def handler(message):
        received.append(message)

    message_bus.subscribe(MessageType.TASK_TRACE, handler)

    await message_bus.broadcast(
        MessageType.TASK_TRACE,
        "test",
        {"event": {"task_id": "task-1", "phase": "planning"}},
    )

    # Wait for async processing
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0].type == MessageType.TASK_TRACE
    assert received[0].sender == "test"
    assert received[0].payload["event"]["task_id"] == "task-1"
    assert received[0].payload["event"]["phase"] == "planning"


@pytest.mark.asyncio
async def test_message_bus_task_trace_multiple_subscribers():
    """测试多个订阅者接收 TASK_TRACE."""
    message_bus = MessageBus()
    received1 = []
    received2 = []

    def handler1(message):
        received1.append(message)

    def handler2(message):
        received2.append(message)

    message_bus.subscribe(MessageType.TASK_TRACE, handler1)
    message_bus.subscribe(MessageType.TASK_TRACE, handler2)

    await message_bus.broadcast(
        MessageType.TASK_TRACE,
        "test",
        {"event": {"task_id": "task-1", "phase": "executing"}},
    )

    await asyncio.sleep(0.1)

    assert len(received1) == 1
    assert len(received2) == 1


@pytest.mark.asyncio
async def test_message_bus_task_trace_unsubscribe():
    """测试取消订阅 TASK_TRACE."""
    message_bus = MessageBus()
    received = []

    def handler(message):
        received.append(message)

    message_bus.subscribe(MessageType.TASK_TRACE, handler)

    # First broadcast
    await message_bus.broadcast(
        MessageType.TASK_TRACE,
        "test",
        {"event": {"task_id": "task-1"}},
    )

    await asyncio.sleep(0.05)
    assert len(received) == 1

    # Unsubscribe
    message_bus.unsubscribe(MessageType.TASK_TRACE, handler)

    # Second broadcast
    await message_bus.broadcast(
        MessageType.TASK_TRACE,
        "test",
        {"event": {"task_id": "task-2"}},
    )

    await asyncio.sleep(0.05)
    # Should still be 1, not 2
    assert len(received) == 1


@pytest.mark.asyncio
async def test_message_bus_task_trace_history():
    """测试 TASK_TRACE 历史记录."""
    message_bus = MessageBus()

    # Broadcast multiple messages
    for i in range(5):
        await message_bus.broadcast(
            MessageType.TASK_TRACE,
            "test",
            {"event": {"task_id": f"task-{i}", "seq": i}},
        )

    await asyncio.sleep(0.1)

    # Get history
    history = await message_bus.get_history(MessageType.TASK_TRACE, limit=3)
    assert len(history) == 3


@pytest.mark.asyncio
async def test_director_workflow_emits_trace():
    """测试 Director 工作流发出追踪事件."""
    from app.orchestration.events.task_trace import TaskTraceBuilder

    # Create a trace builder for director
    builder = TaskTraceBuilder(run_id="director-run-1", role="director", task_id="task-123")

    # Simulate workflow phases
    events = []

    # Phase 1: Planning
    event1 = builder.build(
        phase="planning",
        step_kind="phase",
        step_title="Planning task execution",
        step_detail="Analyzing task requirements and creating execution plan",
        status="started",
    )
    events.append(event1)

    # Phase 2: LLM Call
    event2 = builder.build(
        phase="analyzing",
        step_kind="llm",
        step_title="LLM analysis",
        step_detail="Sending request to LLM for code analysis",
        status="running",
    )
    events.append(event2)

    # Phase 3: Tool execution
    event3 = builder.build(
        phase="executing",
        step_kind="tool",
        step_title="Executing file write",
        step_detail="Writing code to src/example.py",
        status="running",
        refs={"current_file": "src/example.py"},
    )
    events.append(event3)

    # Phase 4: Validation
    event4 = builder.build(
        phase="verify",
        step_kind="validation",
        step_title="Validating changes",
        step_detail="Running syntax check on modified files",
        status="running",
    )
    events.append(event4)

    # Phase 5: Complete
    event5 = builder.build(
        phase="completed",
        step_kind="phase",
        step_title="Task completed",
        step_detail="All steps executed successfully",
        status="completed",
    )
    events.append(event5)

    # Verify events
    assert len(events) == 5
    assert all(e.run_id == "director-run-1" for e in events)
    assert all(e.role == "director" for e in events)
    assert all(e.task_id == "task-123" for e in events)

    # Verify sequence
    for i, event in enumerate(events):
        assert event.seq == i + 1

    # Verify phases
    assert events[0].phase == "planning"
    assert events[1].phase == "analyzing"
    assert events[2].phase == "executing"
    assert events[3].phase == "verify"
    assert events[4].phase == "completed"

    # Verify WebSocket payload format
    payload = builder.to_ws_payload(event5)
    assert payload["type"] == "task_trace"
    assert payload["event"]["phase"] == "completed"


@pytest.mark.asyncio
async def test_pm_workflow_emits_trace():
    """测试 PM 工作流发出追踪事件."""
    from app.orchestration.events.task_trace import TaskTraceBuilder

    # Create a trace builder for pm
    builder = TaskTraceBuilder(run_id="pm-run-1", role="pm", task_id="epic-456")

    # Simulate PM workflow
    events = []

    # Phase 1: Task analysis
    event1 = builder.build(
        phase="planning",
        step_kind="phase",
        step_title="Analyzing user request",
        step_detail="Parsing task description and identifying requirements",
        status="started",
    )
    events.append(event1)

    # Phase 2: Task breakdown
    event2 = builder.build(
        phase="planning",
        step_kind="llm",
        step_title="Breaking down tasks",
        step_detail="Using LLM to decompose epic into subtasks",
        status="running",
    )
    events.append(event2)

    # Phase 3: Task creation
    event3 = builder.build(
        phase="executing",
        step_kind="tool",
        step_title="Creating task board entries",
        step_detail="Adding 5 subtasks to task board",
        status="running",
        related_task_ids=["subtask-1", "subtask-2", "subtask-3"],
    )
    events.append(event3)

    # Phase 4: Report
    event4 = builder.build(
        phase="report",
        step_kind="phase",
        step_title="Generating task summary",
        step_detail="Creating execution plan and priority queue",
        status="running",
    )
    events.append(event4)

    # Phase 5: Complete
    event5 = builder.build(
        phase="completed",
        step_kind="phase",
        step_title="Planning complete",
        step_detail="Task breakdown finished, ready for execution",
        status="completed",
    )
    events.append(event5)

    # Verify events
    assert len(events) == 5
    assert all(e.run_id == "pm-run-1" for e in events)
    assert all(e.role == "pm" for e in events)
    assert all(e.task_id == "epic-456" for e in events)

    # Verify refs (passed as kwargs, flattened)
    assert events[2].refs.get("related_task_ids") == ["subtask-1", "subtask-2", "subtask-3"]


@pytest.mark.asyncio
async def test_task_trace_with_retry():
    """测试带重试的任务追踪."""
    from app.orchestration.events.task_trace import TaskTraceBuilder

    builder = TaskTraceBuilder(run_id="retry-run", role="director", task_id="task-retry")

    # First attempt - failed
    event1 = builder.build(
        phase="executing",
        step_kind="tool",
        step_title="Executing tool",
        step_detail="Attempt 1: Writing file",
        status="failed",
        attempt=0,
        refs={"error_code": "WRITE_ERROR"},
    )

    # Retry attempt
    event2 = builder.build(
        phase="executing",
        step_kind="retry",
        step_title="Retrying after failure",
        step_detail="Attempt 2: Retrying file write with backoff",
        status="running",
        attempt=1,
    )

    # Success
    event3 = builder.build(
        phase="executing",
        step_kind="tool",
        step_title="Tool execution successful",
        step_detail="File written successfully on retry",
        status="completed",
        attempt=1,
    )

    assert event1.attempt == 0
    assert event1.status == "failed"
    assert event2.attempt == 1
    assert event2.step_kind == "retry"
    assert event3.attempt == 1
    assert event3.status == "completed"


@pytest.mark.asyncio
async def test_task_trace_visibility():
    """测试任务追踪可见性设置."""
    from app.orchestration.events.task_trace import TaskTraceBuilder

    builder = TaskTraceBuilder(run_id="visibility-run", role="director", task_id="task-vis")

    # Summary visibility (default)
    event1 = builder.build(
        phase="planning",
        step_kind="phase",
        step_title="Planning",
        step_detail="Planning phase",
        status="started",
        visibility="summary",
    )

    # Debug visibility
    event2 = builder.build(
        phase="analyzing",
        step_kind="llm",
        step_title="LLM raw output",
        step_detail="Detailed LLM response for debugging",
        status="running",
        visibility="debug",
    )

    assert event1.visibility == "summary"
    assert event2.visibility == "debug"
