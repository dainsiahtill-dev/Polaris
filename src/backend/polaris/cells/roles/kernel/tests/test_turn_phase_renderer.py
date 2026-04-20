"""
Tests for Turn Phase Renderer

验证：
1. 阶段事件渲染
2. 工具执行事件渲染
3. 完成事件渲染
4. 错误事件渲染
5. 格式化工具
"""

from polaris.cells.roles.kernel.internal.turn_phase_renderer import (
    AuditLogFormatter,
    RenderConfig,
    TurnMetricsFormatter,
    TurnPhaseRenderer,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    ToolBatchEvent,
    TurnPhaseEvent,
)


class TestPhaseEventRendering:
    """测试阶段事件渲染"""

    def test_renders_decision_requested(self) -> None:
        """渲染决策请求"""
        renderer = TurnPhaseRenderer()
        event = TurnPhaseEvent.create("turn_1", "decision_requested")

        lines = renderer._render_phase_event(event)

        assert len(lines) >= 1
        assert "Decision Requested" in lines[0]
        assert "🤔" in lines[0]

    def test_renders_decision_completed(self) -> None:
        """渲染决策完成"""
        renderer = TurnPhaseRenderer()
        event = TurnPhaseEvent.create(
            "turn_1", "decision_completed", {"kind": "tool_batch", "finalize_mode": "llm_once"}
        )

        lines = renderer._render_phase_event(event)

        assert any("tool_batch" in line for line in lines)
        assert any("llm_once" in line for line in lines)

    def test_renders_tool_batch_started(self) -> None:
        """渲染工具批次开始"""
        renderer = TurnPhaseRenderer()
        event = TurnPhaseEvent.create("turn_1", "tool_batch_started", {"tool_count": 3})

        lines = renderer._render_phase_event(event)

        assert any("Tools: 3" in line for line in lines)

    def test_hides_timestamps_when_disabled(self) -> None:
        """隐藏时间戳"""
        config = RenderConfig(show_timestamps=False)
        renderer = TurnPhaseRenderer(config)
        event = TurnPhaseEvent.create("turn_1", "decision_requested")

        lines = renderer._render_phase_event(event)

        # 不应包含时间戳
        assert not any("[" in line and "]" in line for line in lines)

    def test_shows_state_trajectory(self) -> None:
        """显示状态轨迹"""
        renderer = TurnPhaseRenderer()
        states = ["CONTEXT_BUILT", "DECISION_REQUESTED", "DECISION_DECODED", "COMPLETED"]

        lines = renderer.render_state_trajectory(states)

        assert len(lines) > 0
        output = " ".join(lines)
        assert "State Trajectory" in output
        assert all(state in output for state in states)


class TestToolEventRendering:
    """测试工具事件渲染"""

    def test_renders_tool_started(self) -> None:
        """渲染工具开始"""
        renderer = TurnPhaseRenderer()
        event = ToolBatchEvent(
            turn_id="turn_1",
            batch_id="batch_1",
            tool_name="read_file",
            call_id="call_1",
            status="started",
            progress=0.0,
        )

        lines = renderer._render_tool_event(event)

        assert any("read_file" in line for line in lines)
        assert any("⏳" in line for line in lines)

    def test_renders_tool_success(self) -> None:
        """渲染工具成功"""
        renderer = TurnPhaseRenderer()
        event = ToolBatchEvent(
            turn_id="turn_1",
            batch_id="batch_1",
            tool_name="read_file",
            call_id="call_1",
            status="success",
            progress=1.0,
            execution_time_ms=150,
        )

        lines = renderer._render_tool_event(event)

        assert any("150ms" in line for line in lines)
        assert any("✅" in line for line in lines)

    def test_renders_tool_error(self) -> None:
        """渲染工具错误"""
        renderer = TurnPhaseRenderer()
        event = ToolBatchEvent(
            turn_id="turn_1", batch_id="batch_1", tool_name="read_file", call_id="call_1", status="error", progress=0.5
        )

        lines = renderer._render_tool_event(event)

        assert any("❌" in line for line in lines)
        assert any("Failed" in line for line in lines)

    def test_compact_mode(self) -> None:
        """紧凑模式"""
        config = RenderConfig(compact_mode=True)
        renderer = TurnPhaseRenderer(config)
        event = ToolBatchEvent(
            turn_id="turn_1",
            batch_id="batch_1",
            tool_name="read_file",
            call_id="call_1",
            status="started",
            progress=0.5,
        )

        lines = renderer._render_tool_event(event)

        assert len(lines) == 1


class TestCompletionRendering:
    """测试完成事件渲染"""

    def test_renders_success_completion(self) -> None:
        """渲染成功完成"""
        renderer = TurnPhaseRenderer()
        event = CompletionEvent(turn_id="turn_1", status="success", duration_ms=1500, llm_calls=2, tool_calls=3)

        lines = renderer._render_completion(event)
        output = "\n".join(lines)

        assert "SUCCESS" in output
        assert "2" in output  # LLM calls
        assert "3" in output  # Tool calls

    def test_renders_handoff_completion(self) -> None:
        """渲染移交完成"""
        renderer = TurnPhaseRenderer()
        event = CompletionEvent(turn_id="turn_1", status="handoff", duration_ms=500, llm_calls=1, tool_calls=0)

        lines = renderer._render_completion(event)

        assert any("HANDOFF" in line for line in lines)
        assert "🔀" in " ".join(lines)

    def test_renders_workflow_handoff_phase(self) -> None:
        """渲染 workflow_handoff 阶段"""
        renderer = TurnPhaseRenderer()
        event = TurnPhaseEvent.create(
            "turn_1",
            "workflow_handoff",
            {"kind": "handoff_workflow", "tool_count": 1},
        )

        lines = renderer._render_phase_event(event)
        output = "\n".join(lines)

        assert "Workflow Handoff" in output
        assert "🔀" in output
        assert "handoff_workflow" in output
        assert "Tools: 1" in output


class TestErrorRendering:
    """测试错误事件渲染"""

    def test_renders_error(self) -> None:
        """渲染错误"""
        renderer = TurnPhaseRenderer()
        event = ErrorEvent(
            turn_id="turn_1",
            error_type="ValueError",
            message="Invalid tool arguments",
            state_at_error="TOOL_BATCH_EXECUTING",
        )

        lines = renderer._render_error(event)

        assert any("ValueError" in line for line in lines)
        assert any("Invalid tool arguments" in line for line in lines)
        assert any("TOOL_BATCH_EXECUTING" in line for line in lines)


class TestMetricsFormatter:
    """测试指标格式化"""

    def test_format_duration_ms(self) -> None:
        """格式化毫秒"""
        assert TurnMetricsFormatter.format_duration(500) == "500ms"
        assert TurnMetricsFormatter.format_duration(1000) == "1.0s"
        assert TurnMetricsFormatter.format_duration(65000) == "1.1m"

    def test_format_tokens(self) -> None:
        """格式化tokens"""
        result = TurnMetricsFormatter.format_tokens({"prompt_tokens": 100, "completion_tokens": 50})
        assert "150" in result
        assert "in:100" in result
        assert "out:50" in result

    def test_format_summary(self) -> None:
        """格式化摘要"""
        result = {"kind": "tool_batch_with_receipt", "metrics": {"duration_ms": 2000, "llm_calls": 2, "tool_calls": 3}}

        summary = TurnMetricsFormatter.format_summary(result)

        assert "tool_batch_with_receipt" in summary
        assert "2.0s" in summary
        assert "2" in summary
        assert "3" in summary


class TestAuditLogFormatter:
    """测试审计日志格式化"""

    def test_format_ledger(self) -> None:
        """格式化账本"""
        ledger = {
            "turn_id": "turn_1",
            "duration_ms": 1500,
            "llm_calls": 2,
            "tool_calls": 3,
            "completed": True,
            "decisions": [{"kind": "TOOL_BATCH", "finalize_mode": "LLM_ONCE", "tool_count": 2}],
            "states": [("CONTEXT_BUILT", 1000), ("DECISION_REQUESTED", 1100)],
        }

        log = AuditLogFormatter.format_ledger(ledger)

        assert "turn_1" in log
        assert "1500ms" in log
        assert "TOOL_BATCH" in log
        assert "CONTEXT_BUILT" in log


class TestContentRendering:
    """测试内容渲染"""

    def test_renders_visible_content(self) -> None:
        """渲染可见内容"""
        renderer = TurnPhaseRenderer()
        event = ContentChunkEvent(turn_id="turn_1", chunk="Hello, world!", is_thinking=False)

        lines = renderer._render_content(event)

        assert any("Hello, world!" in line for line in lines)
        assert "📤" in " ".join(lines)

    def test_hides_thinking_by_default(self) -> None:
        """默认隐藏thinking"""
        renderer = TurnPhaseRenderer()
        event = ContentChunkEvent(turn_id="turn_1", chunk="Let me think...", is_thinking=True)

        lines = renderer._render_content(event)

        assert len(lines) == 0

    def test_shows_thinking_when_enabled(self) -> None:
        """启用时显示thinking"""
        config = RenderConfig(show_thinking=True)
        renderer = TurnPhaseRenderer(config)
        event = ContentChunkEvent(turn_id="turn_1", chunk="Let me think...", is_thinking=True)

        lines = renderer._render_content(event)

        assert any("Let me think" in line for line in lines)
