"""Thinking Engine - 结构化思考过程输出

负责生成结构化的 thinking 事件，让用户看到 AI 的思考过程。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ThinkingPhase(Enum):
    """思考阶段"""

    IDLE = "idle"
    UNDERSTANDING = "understanding"  # 理解问题
    PLANNING = "planning"  # 制定计划
    EXECUTING = "executing"  # 执行中
    REVIEWING = "reviewing"  # 审查中
    COMPLETED = "completed"  # 完成


class IntentType(Enum):
    """意图类型"""

    ANALYZE = "analyze"  # 分析
    PLAN = "plan"  # 计划
    EXECUTE = "execute"  # 执行
    REVIEW = "review"  # 审查
    ANSWER = "answer"  # 回答


@dataclass
class PlanStep:
    """计划步骤"""

    step: int
    total: int
    label: str
    status: str = "pending"  # pending, running, completed, failed


@dataclass
class ThinkingContext:
    """思考上下文"""

    phase: ThinkingPhase = ThinkingPhase.IDLE
    current_intent: IntentType | None = None
    intent_target: str = ""
    progress: int = 0

    plan_steps: list[PlanStep] = field(default_factory=list)
    current_plan_index: int = 0

    decisions: list[dict[str, str]] = field(default_factory=list)

    tool_status: dict[str, str] = field(default_factory=dict)  # tool_name -> status


class ThinkingEngine:
    """思考引擎 - 管理结构化思考过程"""

    def __init__(self, role: str) -> None:
        self.role = role
        self.context = ThinkingContext()
        self._events: list[dict[str, Any]] = []

    def start_understanding(self, query: str) -> dict[str, Any]:
        """开始理解阶段"""
        self.context.phase = ThinkingPhase.UNDERSTANDING
        self.context.current_intent = IntentType.ANALYZE
        self.context.progress = 0
        self.context.intent_target = query[:100]  # 截断

        event = self._create_intent_event()
        self._events.append(event)
        return event

    def start_planning(self, steps: list[str]) -> dict[str, Any]:
        """开始计划阶段"""
        self.context.phase = ThinkingPhase.PLANNING
        self.context.current_intent = IntentType.PLAN
        self.context.progress = 20

        # 创建计划步骤
        self.context.plan_steps = [PlanStep(step=i + 1, total=len(steps), label=step) for i, step in enumerate(steps)]
        self.context.current_plan_index = 0

        event = self._create_plan_event()
        self._events.append(event)
        return event

    def update_plan_step(self, step_index: int, status: str = "running") -> dict[str, Any] | None:
        """更新计划步骤状态"""
        if step_index < len(self.context.plan_steps):
            self.context.plan_steps[step_index].status = status

            # 更新进度
            completed = sum(1 for s in self.context.plan_steps if s.status == "completed")
            self.context.progress = 20 + (completed * 80 // len(self.context.plan_steps))

            event = self._create_plan_event()
            self._events.append(event)
            return event
        return None

    def add_decision(self, content: str, reason: str) -> dict[str, Any]:
        """添加决策"""
        decision = {"content": content, "reason": reason}
        self.context.decisions.append(decision)

        event = {
            "type": "decision",
            "data": {
                "content": content,
                "reason": reason,
            },
        }
        self._events.append(event)
        return event

    def set_tool_status(self, tool_name: str, status: str) -> dict[str, Any]:
        """设置工具状态"""
        self.context.tool_status[tool_name] = status

        event = {
            "type": "tool_status",
            "data": {
                "tool": tool_name,
                "status": status,
            },
        }
        self._events.append(event)
        return event

    def complete(self) -> dict[str, Any]:
        """完成思考"""
        self.context.phase = ThinkingPhase.COMPLETED
        self.context.progress = 100

        event = {
            "type": "thinking_complete",
            "data": {
                "phase": "completed",
                "progress": 100,
            },
        }
        self._events.append(event)
        return event

    def _create_intent_event(self) -> dict[str, Any]:
        """创建意图事件"""
        return {
            "type": "intent",
            "data": {
                "current": self.context.current_intent.value if self.context.current_intent else "idle",
                "target": self.context.intent_target,
                "progress": self.context.progress,
            },
        }

    def _create_plan_event(self) -> dict[str, Any]:
        """创建计划进度事件"""
        steps_data = []
        for _i, step in enumerate(self.context.plan_steps):
            steps_data.append(
                {
                    "step": step.step,
                    "label": step.label,
                    "status": step.status,
                }
            )

        current_step = None
        if self.context.current_plan_index < len(self.context.plan_steps):
            current_step = self.context.plan_steps[self.context.current_plan_index].label

        return {
            "type": "plan_progress",
            "data": {
                "steps": steps_data,
                "current": current_step,
                "progress": self.context.progress,
            },
        }

    def get_pending_events(self) -> list[dict[str, Any]]:
        """获取待发送的事件"""
        events = self._events
        self._events = []
        return events

    def reset(self) -> None:
        """重置思考上下文"""
        self.context = ThinkingContext()
        self._events = []


# 全局缓存
_thinking_engines: dict[str, ThinkingEngine] = {}


def get_thinking_engine(role: str, reset: bool = False) -> ThinkingEngine:
    """获取指定角色的思考引擎"""
    if role not in _thinking_engines or reset:
        _thinking_engines[role] = ThinkingEngine(role)
    return _thinking_engines[role]
