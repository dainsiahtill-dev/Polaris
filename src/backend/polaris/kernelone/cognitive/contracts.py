"""Cognitive Protocol Ports - Define boundaries between layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PerceptionPort(Protocol):
    """Perception layer processes raw input into IntentGraph."""

    async def process(self, message: str, context: Any) -> Any: ...


@runtime_checkable
class ReasoningPort(Protocol):
    """Reasoning layer analyzes IntentGraph with critical thinking."""

    async def analyze(self, intent_graph: Any, context: Any) -> Any: ...


@runtime_checkable
class ExecutionPort(Protocol):
    """Execution layer runs actions with risk management."""

    async def execute(self, decision: Any, context: Any) -> Any: ...


@runtime_checkable
class EvolutionPort(Protocol):
    """Evolution layer tracks belief changes over time."""

    async def evolve(self, experience: Any) -> Any: ...


@runtime_checkable
class CognitiveOrchestratorProtocol(Protocol):
    """Top-level cognitive orchestrator protocol."""

    async def process(
        self,
        message: str,
        session_id: str,
        role_id: str,
    ) -> Any: ...


# ── CognitivePipelinePort: TurnEngine integration protocol ─────────────────


@dataclass(frozen=True)
class CognitivePreCheckResult:
    """认知预检结果 — TurnEngine 在调用 LLM 前执行认知预检的返回值。"""

    should_proceed: bool
    adjusted_prompt: str | None = None
    governance_verdict: str = "PASS"
    confidence: float = 1.0
    block_reason: str | None = None


@dataclass(frozen=True)
class CognitiveAssessResult:
    """认知评估结果 — TurnEngine 在工具执行后执行认知评估的返回值。"""

    quality_score: float = 1.0
    should_continue: bool = True
    evolution_trigger: str | None = None
    assessment_note: str = ""


@runtime_checkable
class CognitivePipelinePort(Protocol):
    """认知管道端口协议 — TurnEngine 通过此协议接入认知能力。

    该协议定义了 TurnEngine 在关键执行节点上调用认知管道的两个钩子：
    1. pre_turn_cognitive_check: 在 LLM 调用前执行感知+治理预检
    2. post_tool_cognitive_assess: 在工具执行后执行评估+进化学习

    实现类：
    - CognitivePipelineAdapter: 桥接 CognitiveOrchestrator 到此协议
    """

    async def pre_turn_cognitive_check(
        self,
        message: str,
        session_id: str,
        role_id: str,
    ) -> CognitivePreCheckResult:
        """TurnEngine 在调用 LLM 前执行认知预检。"""
        ...

    async def post_tool_cognitive_assess(
        self,
        tool_name: str,
        tool_result: str,
        session_id: str,
    ) -> CognitiveAssessResult:
        """TurnEngine 在工具执行后执行认知评估。"""
        ...
