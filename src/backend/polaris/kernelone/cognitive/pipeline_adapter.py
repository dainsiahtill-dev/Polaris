"""CognitivePipelineAdapter - Bridges CognitiveOrchestrator to CognitivePipelinePort.

只调用编排器的感知+治理阶段（不执行完整管道），
以避免与 TurnEngine 的工具执行循环冲突。

设计决策：
    - pre_turn_cognitive_check: 仅执行 Perception + Governance 预检，
      不触发 Reasoning/Execution/Evolution（这些由 TurnEngine 自身管理）
    - post_tool_cognitive_assess: 仅触发 Evolution 的自我反思学习，
      不执行完整认知管道
    - 所有认知管道故障都被静默降级，不应阻断 TurnEngine 的正常执行
"""

from __future__ import annotations

import logging

from polaris.kernelone.cognitive.contracts import (
    CognitiveAssessResult,
    CognitivePreCheckResult,
)
from polaris.kernelone.cognitive.evolution.models import TriggerType
from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator

logger = logging.getLogger(__name__)


class CognitivePipelineAdapter:
    """将 CognitiveOrchestrator 适配为 CognitivePipelinePort。

    Usage:
        orchestrator = CognitiveOrchestrator()
        adapter = CognitivePipelineAdapter(orchestrator)

        # TurnEngine 注入点
        pre_check = await adapter.pre_turn_cognitive_check(
            message="Read the file", session_id="s1", role_id="director",
        )
        if not pre_check.should_proceed:
            # governance blocked, skip LLM call
            ...

        assess = await adapter.post_tool_cognitive_assess(
            tool_name="read_file", tool_result="...", session_id="s1",
        )
    """

    def __init__(self, orchestrator: CognitiveOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def pre_turn_cognitive_check(
        self,
        message: str,
        session_id: str,
        role_id: str,
    ) -> CognitivePreCheckResult:
        """执行感知+治理预检，不执行完整管道。

        流程：
        1. Perception: 解析意图和置信度
        2. Governance: pre_perception + post_perception 验证

        失败时返回 should_proceed=True（fail-open），认知管道故障不应阻断 TurnEngine。
        """
        try:
            # Step 1: Perception
            intent_graph, _uncertainty = await self._orchestrator._perception.process(
                message,
                session_id=session_id,
            )
            surface_intent = intent_graph.nodes[0] if intent_graph.nodes else None
            intent_type = surface_intent.intent_type if surface_intent else "unknown"
            confidence = float(surface_intent.confidence) if surface_intent else 0.0

            # Step 2: Governance pre-checks
            if self._orchestrator._governance is not None:
                pre_result = await self._orchestrator._governance.verify_pre_perception(message)
                if pre_result.status == "FAIL":
                    return CognitivePreCheckResult(
                        should_proceed=False,
                        governance_verdict="FAIL",
                        confidence=0.0,
                        block_reason=pre_result.message,
                    )

                post_result = await self._orchestrator._governance.verify_post_perception(
                    intent_type,
                    confidence,
                )
                if post_result.status == "FAIL":
                    return CognitivePreCheckResult(
                        should_proceed=False,
                        governance_verdict="FAIL",
                        confidence=confidence,
                        block_reason=post_result.message,
                    )

            return CognitivePreCheckResult(
                should_proceed=True,
                governance_verdict="PASS",
                confidence=confidence,
            )
        except Exception:  # noqa: BLE001
            # 认知管道故障不应阻断 TurnEngine — fail-open
            logger.debug(
                "[CognitivePipelineAdapter] pre_turn_cognitive_check failed, proceeding (fail-open)",
                exc_info=True,
            )
            return CognitivePreCheckResult(
                should_proceed=True,
                governance_verdict="ERROR",
                confidence=1.0,
            )

    async def post_tool_cognitive_assess(
        self,
        tool_name: str,
        tool_result: str,
        session_id: str,
    ) -> CognitiveAssessResult:
        """工具执行后评估 — 触发进化学习。

        仅调用 Evolution 层的 process_trigger，不执行完整认知管道。
        失败时静默降级（返回默认的 should_continue=True）。
        """
        try:
            if self._orchestrator._evolution is not None:
                await self._orchestrator._evolution.process_trigger(
                    trigger_type=TriggerType.SELF_REFLECTION,
                    content=f"Tool {tool_name}: {tool_result[:200]}",
                    context=f"session={session_id}",
                )
            return CognitiveAssessResult(
                quality_score=1.0,
                should_continue=True,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "[CognitivePipelineAdapter] post_tool_cognitive_assess failed, continuing (fail-open)",
                exc_info=True,
            )
            return CognitiveAssessResult(
                quality_score=1.0,
                should_continue=True,
                assessment_note="evolution_failed",
            )
