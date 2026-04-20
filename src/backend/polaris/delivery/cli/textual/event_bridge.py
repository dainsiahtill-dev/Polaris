"""Textual Console 事件流集成

将 Textual TUI 与 Polaris RoleConsoleHost 事件流连接。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from polaris.delivery.cli.textual.console import PolarisTextualConsole


@dataclass
class EventStreamConfig:
    """事件流配置"""

    workspace: str
    role: str
    session_id: str | None = None
    debug_enabled: bool = True


class TextualEventBridge:
    """Textual Console 与事件流的桥接器

    将 RoleConsoleHost 的事件流转换为 Textual TUI 的消息。
    """

    def __init__(
        self,
        app: PolarisTextualConsole,
        config: EventStreamConfig,
    ) -> None:
        self.app = app
        self.config = config
        self._is_processing = False

    async def process_stream(
        self,
        stream: AsyncGenerator[Mapping[str, Any], None],
    ) -> None:
        """处理事件流

        Args:
            stream: 事件流生成器
        """
        self._is_processing = True

        try:
            async for event in stream:
                await self._process_event(event)
        finally:
            self._is_processing = False

    async def _process_event(self, event: Mapping[str, Any]) -> None:
        """处理单个事件

        Args:
            event: 事件数据
        """
        event_type = str(event.get("type", "")).strip()
        data = event.get("data", {})

        if event_type == "content_chunk":
            content = str(data.get("content", ""))
            if content:
                self.app.add_message(content, "assistant")

        elif event_type == "thinking_chunk":
            content = str(data.get("content", ""))
            if content:
                self.app.add_debug(
                    category="llm",
                    label="thinking",
                    source="model",
                    payload={"thinking": content},
                )

        elif event_type == "tool_call":
            tool = data.get("tool", "unknown")
            args = data.get("args", {})
            self.app.add_tool_call(tool, args if isinstance(args, dict) else None)

        elif event_type == "tool_result":
            tool = data.get("tool", "unknown")
            result = data.get("result", {})
            success = data.get("success", True)
            self.app.add_tool_result(tool, result, success)

        elif event_type == "debug":
            # DEBUG 事件
            payload = data.get("payload", data)
            category = payload.get("category", "debug")
            label = payload.get("label", "event")
            source = payload.get("source", "")
            tags = payload.get("tags", {})
            content = payload.get("payload", payload)

            self.app.add_debug(
                category=category,
                label=label,
                source=source,
                tags=tags if isinstance(tags, dict) else {},
                payload=content,
            )

        elif event_type == "error":
            error_text = str(data.get("error", data.get("message", "Unknown error")))
            self.app.add_error(error_text)

        elif event_type == "complete":
            # 完成事件
            thinking = str(data.get("thinking", ""))
            content = str(data.get("content", ""))

            if thinking and not content:
                # 只有 thinking 没有 content
                self.app.add_debug(
                    category="llm",
                    label="thinking",
                    source="model",
                    payload={"thinking": thinking},
                )

            if content:
                self.app.add_message(content, "assistant")

        # =============================================================================
        # Cognitive Events (for thinking process visualization)
        # =============================================================================

        elif event_type == "thinking_phase":
            # Thinking phase event
            phase = str(data.get("phase", ""))
            content = str(data.get("content", ""))
            confidence = data.get("confidence", 0.5)
            self.app.add_debug(
                category="cognitive",
                label=f"thinking_phase:{phase}",
                source="cognitive_orchestrator",
                payload={
                    "phase": phase,
                    "content": content,
                    "confidence": confidence,
                },
            )

        elif event_type == "reflection":
            # Reflection event
            reflection_type = str(data.get("reflection_type", ""))
            insights = data.get("insights", [])
            knowledge_gaps = data.get("knowledge_gaps", [])
            self.app.add_debug(
                category="cognitive",
                label=f"reflection:{reflection_type}",
                source="cognitive_orchestrator",
                payload={
                    "type": reflection_type,
                    "insights": insights,
                    "knowledge_gaps": knowledge_gaps,
                },
            )

        elif event_type == "evolution":
            # Evolution event
            trigger_type = str(data.get("trigger_type", ""))
            adaptation = str(data.get("adaptation", ""))
            learning_recorded = data.get("learning_recorded", False)
            self.app.add_debug(
                category="cognitive",
                label="evolution",
                source="cognitive_orchestrator",
                payload={
                    "trigger": trigger_type,
                    "adaptation": adaptation,
                    "learning_recorded": learning_recorded,
                },
            )

        elif event_type == "belief_change":
            # Belief change event
            belief_key = str(data.get("belief_key", ""))
            old_value = data.get("old_value", 0.0)
            new_value = data.get("new_value", 0.0)
            reason = str(data.get("reason", ""))
            self.app.add_debug(
                category="cognitive",
                label="belief_change",
                source="cognitive_orchestrator",
                payload={
                    "belief": belief_key,
                    "old_value": old_value,
                    "new_value": new_value,
                    "reason": reason,
                },
            )

        elif event_type == "confidence_calibration":
            # Confidence calibration event
            original = data.get("original_confidence", 0.0)
            calibrated = data.get("calibrated_confidence", 0.0)
            factor = data.get("calibration_factor", 1.0)
            self.app.add_debug(
                category="cognitive",
                label="confidence_calibration",
                source="cognitive_orchestrator",
                payload={
                    "original": original,
                    "calibrated": calibrated,
                    "factor": factor,
                },
            )

        elif event_type == "perception_completed":
            # Perception completed event
            intent_type = str(data.get("intent_type", ""))
            confidence = data.get("confidence", 0.0)
            uncertainty = data.get("uncertainty_score", 0.0)
            self.app.add_debug(
                category="cognitive",
                label="perception",
                source="cognitive_orchestrator",
                payload={
                    "intent_type": intent_type,
                    "confidence": confidence,
                    "uncertainty": uncertainty,
                },
            )

        elif event_type == "reasoning_completed":
            # Reasoning completed event
            reasoning_type = str(data.get("reasoning_type", ""))
            conclusion = str(data.get("conclusion", ""))
            blockers = data.get("blockers", [])
            self.app.add_debug(
                category="cognitive",
                label="reasoning",
                source="cognitive_orchestrator",
                payload={
                    "type": reasoning_type,
                    "conclusion": conclusion,
                    "blockers": blockers,
                },
            )

        elif event_type == "intent_detected":
            # Intent detected event
            intent_type = str(data.get("intent_type", ""))
            surface_intent = str(data.get("surface_intent", ""))
            confidence = data.get("confidence", 0.0)
            self.app.add_debug(
                category="cognitive",
                label="intent",
                source="cognitive_orchestrator",
                payload={
                    "intent_type": intent_type,
                    "surface_intent": surface_intent,
                    "confidence": confidence,
                },
            )

        elif event_type == "critical_thinking":
            # Critical thinking event
            analysis_type = str(data.get("analysis_type", ""))
            findings = data.get("findings", [])
            risk_level = str(data.get("risk_level", "low"))
            self.app.add_debug(
                category="cognitive",
                label="critical_thinking",
                source="cognitive_orchestrator",
                payload={
                    "type": analysis_type,
                    "findings": findings,
                    "risk_level": risk_level,
                },
            )

        elif event_type == "cautious_execution":
            # Cautious execution event
            execution_path = str(data.get("execution_path", ""))
            requires_confirmation = data.get("requires_confirmation", False)
            stakes_level = str(data.get("stakes_level", "low"))
            self.app.add_debug(
                category="cognitive",
                label="execution",
                source="cognitive_orchestrator",
                payload={
                    "path": execution_path,
                    "requires_confirmation": requires_confirmation,
                    "stakes_level": stakes_level,
                },
            )

        elif event_type == "value_alignment":
            # Value alignment event
            action = str(data.get("action", ""))
            verdict = str(data.get("verdict", ""))
            conflicts = data.get("conflicts", [])
            overall_score = data.get("overall_score", 0.0)
            self.app.add_debug(
                category="cognitive",
                label="value_alignment",
                source="cognitive_orchestrator",
                payload={
                    "action": action,
                    "verdict": verdict,
                    "conflicts": conflicts,
                    "score": overall_score,
                },
            )


def create_event_bridge(
    app: PolarisTextualConsole,
    config: EventStreamConfig,
) -> TextualEventBridge:
    """创建事件桥接器

    Args:
        app: Textual 应用实例
        config: 事件流配置

    Returns:
        事件桥接器实例
    """
    return TextualEventBridge(app, config)


# =============================================================================
# 辅助函数
# =============================================================================


def format_payload_as_json(payload: Any) -> str:
    """格式化 payload 为 JSON 字符串

    Args:
        payload: 任意数据

    Returns:
        格式化的 JSON 字符串
    """
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, indent=2)
    elif isinstance(payload, str):
        return payload
    elif payload is None:
        return ""
    else:
        return json.dumps({"value": payload}, ensure_ascii=False, indent=2)


def extract_debug_info(event: Mapping[str, Any]) -> dict[str, Any]:
    """从事件中提取 DEBUG 信息

    Args:
        event: 事件数据

    Returns:
        DEBUG 信息字典
    """
    data = event.get("data", {})
    payload = data.get("payload", data)

    return {
        "category": str(payload.get("category", "debug")),
        "label": str(payload.get("label", "event")),
        "source": str(payload.get("source", "")),
        "tags": payload.get("tags", {}),
        "content": payload.get("payload", payload),
    }


# =============================================================================
# 模拟事件流（用于测试）
# =============================================================================


async def simulate_debug_stream(
    app: PolarisTextualConsole,
    count: int = 5,
) -> None:
    """模拟 DEBUG 事件流

    Args:
        app: Textual 应用实例
        count: 模拟消息数量
    """
    import random

    categories = ["fs", "llm", "tool", "kernel", "runtime"]
    labels = ["read", "write", "execute", "request", "response"]

    for i in range(count):
        await asyncio.sleep(0.5)

        category = random.choice(categories)
        label = random.choice(labels)

        app.add_debug(
            category=category,
            label=label,
            source="simulator",
            tags={"index": i},
            payload={
                "id": i,
                "status": "ok",
                "data": f"Sample data {i}",
            },
        )
