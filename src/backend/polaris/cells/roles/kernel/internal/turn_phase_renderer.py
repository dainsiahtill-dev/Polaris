"""
Turn Phase Renderer - CLI观测渲染器

核心职责：
1. 将turn事件转换为CLI友好的输出
2. 渲染状态转换
3. 显示工具执行进度
4. 渲染最终结果
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    ToolBatchEvent,
    TurnPhaseEvent,
)


@dataclass
class RenderConfig:
    """渲染配置"""

    show_thinking: bool = False  # 是否显示thinking内容
    show_timestamps: bool = True
    show_state_trajectory: bool = True
    compact_mode: bool = False


class TurnPhaseRenderer:
    """
    Turn阶段渲染器

    将事件流转换为CLI输出

    使用示例：
        renderer = TurnPhaseRenderer()
        async for line in renderer.render_events(event_stream):
            print(line)
    """

    # 状态emoji映射
    STATE_EMOJI = {
        "decision_requested": "🤔",
        "decision_completed": "📋",
        "tool_batch_started": "🔧",
        "tool_batch_completed": "✅",
        "finalization_requested": "📝",
        "finalization_completed": "✨",
        "workflow_handoff": "🔀",
        "completed": "🎉",
        "failed": "❌",
    }

    # 工具状态emoji
    TOOL_EMOJI = {"started": "⏳", "success": "✅", "error": "❌", "timeout": "⏱️"}

    def __init__(self, config: RenderConfig | None = None) -> None:
        self.config = config or RenderConfig()

    async def render_events(self, events: AsyncIterator) -> AsyncIterator[str]:
        """渲染事件流"""
        async for event in events:
            lines = self._render_event(event)
            for line in lines:
                yield line

    def _render_event(self, event) -> list[str]:
        """渲染单个事件"""
        if isinstance(event, TurnPhaseEvent):
            return self._render_phase_event(event)
        elif isinstance(event, ToolBatchEvent):
            return self._render_tool_event(event)
        elif isinstance(event, CompletionEvent):
            return self._render_completion(event)
        elif isinstance(event, ErrorEvent):
            return self._render_error(event)
        elif isinstance(event, ContentChunkEvent):
            return self._render_content(event)
        else:
            return []

    def _render_phase_event(self, event: TurnPhaseEvent) -> list[str]:
        """渲染阶段事件"""
        emoji = self.STATE_EMOJI.get(event.phase, "📌")
        phase_name = event.phase.replace("_", " ").title()

        lines = []

        if self.config.show_timestamps:
            ts = event.timestamp_ms % 100000  # 简化时间戳
            lines.append(f"[{ts:05d}] {emoji} {phase_name}")
        else:
            lines.append(f"{emoji} {phase_name}")

        # 显示元数据
        if event.metadata:
            if "tool_count" in event.metadata:
                lines.append(f"   └─ Tools: {event.metadata['tool_count']}")
            if "kind" in event.metadata:
                lines.append(f"   └─ Kind: {event.metadata['kind']}")
            if "finalize_mode" in event.metadata:
                lines.append(f"   └─ Finalize: {event.metadata['finalize_mode']}")

        return lines

    def _render_tool_event(self, event: ToolBatchEvent) -> list[str]:
        """渲染工具执行事件"""
        emoji = self.TOOL_EMOJI.get(event.status, "🔧")

        if self.config.compact_mode:
            return [f"{emoji} {event.tool_name}"]

        lines = [f"{emoji} {event.tool_name}"]

        if event.status == "started":
            lines.append("   └─ Executing...")
        elif event.status == "success":
            lines.append(f"   └─ Completed in {event.execution_time_ms}ms")
        elif event.status == "error":
            lines.append("   └─ Failed")
        elif event.status == "timeout":
            lines.append("   └─ Timed out")

        # 显示进度条
        progress = int(event.progress * 20)
        bar = "█" * progress + "░" * (20 - progress)
        lines.append(f"   └─ [{bar}] {int(event.progress * 100)}%")

        return lines

    def _render_completion(self, event: CompletionEvent) -> list[str]:
        """渲染完成事件"""
        status_emoji = "🎉" if event.status == "success" else ("🔀" if event.status == "handoff" else "❌")

        lines = []
        lines.append("")
        lines.append(f"{status_emoji} Turn Completed: {event.status.upper()}")
        lines.append(f"   ├─ Duration: {event.duration_ms}ms")
        lines.append(f"   ├─ LLM Calls: {event.llm_calls}")
        lines.append(f"   └─ Tool Calls: {event.tool_calls}")

        return lines

    def _render_error(self, event: ErrorEvent) -> list[str]:
        """渲染错误事件"""
        lines = []
        lines.append("")
        lines.append(f"❌ Error at {event.state_at_error}")
        lines.append(f"   └─ {event.error_type}: {event.message}")
        return lines

    def _render_content(self, event: ContentChunkEvent) -> list[str]:
        """渲染内容片段"""
        if event.is_thinking and not self.config.show_thinking:
            return []

        prefix = "💭 " if event.is_thinking else "📤 "
        return [f"{prefix}{event.chunk}"]

    def render_state_trajectory(self, states: list[str]) -> list[str]:
        """渲染状态轨迹"""
        if not self.config.show_state_trajectory or not states:
            return []

        lines = []
        lines.append("")
        lines.append("📍 State Trajectory:")

        for i, state in enumerate(states):
            emoji = self.STATE_EMOJI.get(state.lower(), "📌")
            prefix = "└─" if i == len(states) - 1 else "├─"
            lines.append(f"   {prefix} {emoji} {state}")

        return lines


class TurnMetricsFormatter:
    """Turn指标格式化器"""

    @staticmethod
    def format_duration(ms: int) -> str:
        """格式化耗时"""
        if ms < 1000:
            return f"{ms}ms"
        elif ms < 60000:
            return f"{ms / 1000:.1f}s"
        else:
            return f"{ms / 60000:.1f}m"

    @staticmethod
    def format_tokens(usage: dict) -> str:
        """格式化token使用"""
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        total = prompt + completion
        return f"{total} tokens (in:{prompt}, out:{completion})"

    @staticmethod
    def format_summary(result: dict) -> str:
        """格式化结果摘要"""
        metrics = result.get("metrics", {})

        parts = [
            f"Duration: {TurnMetricsFormatter.format_duration(metrics.get('duration_ms', 0))}",
            f"LLM Calls: {metrics.get('llm_calls', 0)}",
            f"Tool Calls: {metrics.get('tool_calls', 0)}",
        ]

        kind = result.get("kind", "")
        parts.insert(0, f"Kind: {kind}")

        return " | ".join(parts)


class AuditLogFormatter:
    """审计日志格式化器"""

    @staticmethod
    def format_ledger(ledger: dict) -> str:
        """格式化账本为可读日志"""
        lines = []
        lines.append("=" * 60)
        lines.append("AUDIT LOG")
        lines.append("=" * 60)

        lines.append(f"Turn ID: {ledger.get('turn_id', 'N/A')}")
        lines.append(f"Duration: {ledger.get('duration_ms', 0)}ms")
        lines.append(f"LLM Calls: {ledger.get('llm_calls', 0)}")
        lines.append(f"Tool Calls: {ledger.get('tool_calls', 0)}")
        lines.append(f"Completed: {ledger.get('completed', False)}")

        # LLM调用详情
        decisions = ledger.get("decisions", [])
        if decisions:
            lines.append("")
            lines.append("Decisions:")
            for i, d in enumerate(decisions, 1):
                lines.append(f"  {i}. Kind={d.get('kind')}, Mode={d.get('finalize_mode')}, Tools={d.get('tool_count')}")

        # 状态历史
        states = ledger.get("states", [])
        if states:
            lines.append("")
            lines.append("State History:")
            for state, ts in states:
                lines.append(f"  - {state} @ {ts}")

        lines.append("=" * 60)

        return "\n".join(lines)
