"""Cognitive Pipeline Coordinator - Orchestrates the main cognitive pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone.cognitive.context import CognitiveContext, ConversationTurn
from polaris.kernelone.cognitive.evolution.engine import EvolutionEngine
from polaris.kernelone.cognitive.evolution.models import TriggerType
from polaris.kernelone.cognitive.execution.cautious_policy import CautiousExecutionPolicy
from polaris.kernelone.cognitive.execution.pipeline import CognitivePipeline, CognitivePipelineResult
from polaris.kernelone.cognitive.governance_gate import CognitiveGovernanceGate
from polaris.kernelone.cognitive.hitl import ExecutionPlan, HumanInterventionQueue, InterventionDecision
from polaris.kernelone.cognitive.llm_adapter import create_llm_adapter
from polaris.kernelone.cognitive.perception.engine import PerceptionLayer
from polaris.kernelone.cognitive.perception.models import UncertaintyAssessment
from polaris.kernelone.cognitive.personality.integrator import PersonalityIntegrator
from polaris.kernelone.cognitive.personality.posture import InteractionPosture, select_posture_for_intent
from polaris.kernelone.cognitive.personality.traits import get_trait_profile_for_role
from polaris.kernelone.cognitive.reasoning.engine import CriticalThinkingEngine
from polaris.kernelone.cognitive.reasoning.meta_cognition import (
    MetaCognitionEngine,
    MetaCognitionSnapshot,
    ReflectIntent,
)
from polaris.kernelone.cognitive.telemetry import CognitiveTelemetry
from polaris.kernelone.cognitive.types import ClarityLevel, ExecutionPath
from polaris.kernelone.events.context_events import ContextEvent, EventType
from polaris.kernelone.events.typed import (
    CautiousExecutionEvent,
    CriticalThinkingEvent,
    EvolutionEvent,
    IntentDetectedEvent,
    PerceptionCompletedEvent,
    ReasoningCompletedEvent,
    ReflectionEvent,
    ThinkingPhaseEvent,
    ValueAlignmentEvent,
    emit_event,
)

if TYPE_CHECKING:
    from polaris.kernelone.cognitive.reasoning.meta_cognition import ReflectionOutput


@dataclass
class PipelineContext:
    """Context passed through the pipeline stages."""

    message: str
    session_id: str
    role_id: str
    ctx: CognitiveContext
    workspace: str | None
    intent_type: str = "unknown"
    confidence: float = 0.0
    uncertainty: UncertaintyAssessment | None = None
    reasoning_chain: Any = None
    meta_cognition: Any = None
    posture: InteractionPosture = InteractionPosture.TRANSPARENT_REASONING
    trait_profile: Any = None
    use_llm: bool = False
    enable_evolution: bool = False
    enable_personality: bool = False
    enable_value_alignment: bool = False
    evolution: EvolutionEngine | None = None
    value_alignment: Any = None
    personality: PersonalityIntegrator | None = None
    hitl: HumanInterventionQueue | None = None


@dataclass
class PipelineResult:
    """Result from the cognitive pipeline coordinator."""

    response_content: str
    execution_path: ExecutionPath
    confidence: float
    clarity_level: ClarityLevel
    intent_type: str
    uncertainty_score: float
    actions_taken: tuple[str, ...]
    verification_needed: bool
    blocked: bool
    block_reason: str | None
    conversation_turn: ConversationTurn | None
    metadata: dict[str, object]
    error_type: str | None
    retryable: bool
    blocked_tools: tuple[str, ...]


class CognitivePipelineCoordinator:
    """
    Coordinates the main cognitive pipeline.

    Handles:
    - Perception → Reasoning → Decision → Thinking → Acting → Evolution
    - HITL workflow
    - Response building with personality influence
    """

    def __init__(
        self,
        workspace: str,
        governance_gate: CognitiveGovernanceGate,
        telemetry: CognitiveTelemetry,
        hitl: HumanInterventionQueue,
        evolution: EvolutionEngine | None,
        personality: PersonalityIntegrator | None,
        value_alignment: Any,
        use_llm: bool,
        enable_evolution: bool,
        enable_personality: bool,
        enable_value_alignment: bool,
    ) -> None:
        self._workspace = workspace
        self._governance_gate = governance_gate
        self._telemetry = telemetry
        self._hitl = hitl
        self._evolution = evolution
        self._personality = personality
        self._value_alignment = value_alignment
        self._use_llm = use_llm
        self._enable_evolution = enable_evolution
        self._enable_personality = enable_personality
        self._enable_value_alignment = enable_value_alignment

        self._perception = PerceptionLayer()
        self._reasoning = CriticalThinkingEngine(llm_invoker=create_llm_adapter(workspace=workspace, use_llm=use_llm))
        self._meta = MetaCognitionEngine(llm_invoker=create_llm_adapter(workspace=workspace, use_llm=use_llm))
        self._policy = CautiousExecutionPolicy()
        self._pipeline = CognitivePipeline(workspace=workspace)
        self._llm = create_llm_adapter(workspace=workspace, use_llm=use_llm)

        self._fallback_count = 0
        self._hitl_timeout_count = 0

    async def execute(self, pipeline_ctx: PipelineContext) -> PipelineResult:
        """Execute the complete cognitive pipeline."""
        with self._telemetry.start_span(
            "cognitive.process",
            {"session_id": pipeline_ctx.session_id, "role_id": pipeline_ctx.role_id},
        ):
            acting = getattr(self._pipeline, "_acting", None)
            if acting is not None and callable(getattr(acting, "reset_for_new_turn", None)):
                acting.reset_for_new_turn()

            return await self._execute_pipeline(pipeline_ctx)

    async def _execute_pipeline(self, ctx: PipelineContext) -> PipelineResult:
        """Internal pipeline execution."""
        intent_type = "unknown"
        confidence = 0.0
        uncertainty: UncertaintyAssessment | None = None
        reasoning_chain = None
        meta_cognition = None
        llm_reflection: ReflectionOutput | None = None
        posture = InteractionPosture.TRANSPARENT_REASONING
        trait_profile = get_trait_profile_for_role(ctx.role_id)

        with self._telemetry.start_span("perception.process", {"session_id": ctx.session_id}):
            intent_graph, uncertainty = await self._perception.process(ctx.message, ctx.session_id)

        surface_intent = intent_graph.nodes[0] if intent_graph.nodes else None
        intent_type = surface_intent.intent_type if surface_intent else "unknown"
        confidence = surface_intent.confidence if surface_intent else 0.0

        await emit_event(
            PerceptionCompletedEvent.create(
                intent_type=intent_type,
                confidence=confidence,
                uncertainty_score=uncertainty.uncertainty_score,
                workspace=self._workspace,
            )
        )

        await emit_event(
            IntentDetectedEvent.create(
                intent_type=intent_type,
                surface_intent=intent_type,
                confidence=confidence,
                workspace=self._workspace,
            )
        )

        if self._use_llm:
            reasoning_chain = await self._reasoning.analyze_with_llm(
                conclusion=ctx.message,
                intent_chain=intent_graph.chains[0] if intent_graph.chains else None,
            )
        else:
            reasoning_chain = await self._reasoning.analyze(
                conclusion=ctx.message,
                intent_chain=intent_graph.chains[0] if intent_graph.chains else None,
            )

        if reasoning_chain is not None:
            conclusion = ""
            try:
                raw = reasoning_chain.conclusion
                conclusion = raw if isinstance(raw, str) else str(raw) if raw else ""
            except AttributeError:
                conclusion = ""

            blockers: list[str] = []
            try:
                raw_blockers = reasoning_chain.blockers
                if isinstance(raw_blockers, (list, tuple)):
                    blockers = [str(b) for b in raw_blockers]
                elif raw_blockers:
                    blockers = []
            except AttributeError:
                blockers = []

            probability = 0.5
            try:
                six_q = reasoning_chain.six_questions
                if six_q is not None:
                    raw_prob = six_q.conclusion_probability
                    try:
                        probability = float(raw_prob)
                    except (ValueError, TypeError):
                        probability = 0.5
            except AttributeError:
                probability = 0.5

            await emit_event(
                ReasoningCompletedEvent.create(
                    reasoning_type="six_questions",
                    conclusion=conclusion,
                    blockers=blockers,
                    workspace=self._workspace,
                )
            )

            await emit_event(
                CriticalThinkingEvent.create(
                    analysis_type="risk_assessment",
                    findings=[f"probability: {probability}"],
                    risk_level=self._get_stakes_level(intent_type),
                    workspace=self._workspace,
                )
            )

        if self._use_llm:
            task_result_for_reflect = {
                "success": True,
                "quality": 0.5,
                "unexpected": [],
            }
            intent_for_reflect = cast(
                ReflectIntent,
                {
                    "graph_id": intent_type,
                    "intent_type": intent_type,
                },
            )
            llm_reflection = await self._meta.reflect_with_llm(task_result_for_reflect, intent_for_reflect)
            meta_cognition = MetaCognitionSnapshot(
                knowledge_boundary_confidence=0.5,
                reasoning_chain_summary=f"LLM reflection on {intent_type}",
                output_confidence=0.5,
                knowledge_domains=tuple(llm_reflection.patterns_identified),
                knowledge_gaps=llm_reflection.knowledge_gaps,
                corrections_made=tuple(llm_reflection.rules_learned),
            )
        else:
            meta_cognition = await self._meta.audit_thought_process(
                reasoning_chain=reasoning_chain,
                assumptions=tuple(a.text for a in reasoning_chain.six_questions.assumptions) if reasoning_chain else (),
            )

        insights: list[str] = []
        try:
            raw = meta_cognition.reasoning_chain_summary
            if isinstance(raw, str):
                insights = [raw]
            elif raw:
                try:
                    insights = [str(raw)]
                except (ValueError, TypeError):
                    insights = []
        except AttributeError:
            insights = []

        knowledge_gaps: list[str] = []
        try:
            raw_gaps = meta_cognition.knowledge_gaps
            if isinstance(raw_gaps, (list, tuple)):
                knowledge_gaps = [str(g) for g in raw_gaps if g]
        except AttributeError:
            knowledge_gaps = []

        meta_confidence = 0.5
        try:
            raw_conf = meta_cognition.output_confidence
            if isinstance(raw_conf, (int, float)):
                meta_confidence = float(raw_conf)
        except AttributeError:
            meta_confidence = 0.5

        await emit_event(
            ReflectionEvent.create(
                reflection_type="meta_cognition",
                insights=insights,
                knowledge_gaps=knowledge_gaps,
                workspace=self._workspace,
            )
        )

        await emit_event(
            ThinkingPhaseEvent.create(
                phase="meta_cognition",
                content="Thought process audit completed",
                confidence=meta_confidence,
                intent_type=intent_type,
                workspace=self._workspace,
            )
        )

        posture_guidance = select_posture_for_intent(
            intent_type=intent_type,
            role_id=ctx.role_id,
            stakes_level=self._get_stakes_level(intent_type),
            uncertainty_level=uncertainty.uncertainty_score,
        )
        posture = posture_guidance.primary_posture

        value_alignment_unsafe = False
        value_alignment_reason = ""
        value_alignment_confidence = 0.0
        val_score = 0.0
        val_conflicts: list[str] = []

        if self._value_alignment and self._enable_value_alignment:
            value_result = await self._value_alignment.evaluate(
                action=ctx.message,
                user_intent=intent_type,
            )

            try:
                conf_val = value_result.confidence
                value_alignment_confidence = float(conf_val) if conf_val is not None else 0.0
            except AttributeError:
                try:
                    value_alignment_confidence = value_result.overall_score
                except AttributeError:
                    value_alignment_confidence = 0.0

            value_alignment_unsafe = (
                getattr(value_result, "is_unsafe", False) or value_result.final_verdict == "REJECTED"
            )
            try:
                conflicts = value_result.conflicts
                value_alignment_reason = getattr(value_result, "reason", None) or "; ".join(conflicts)
            except AttributeError:
                value_alignment_reason = getattr(value_result, "reason", "")

            try:
                val_conflicts = list(value_result.conflicts)
            except AttributeError:
                val_conflicts = []
            try:
                val_score = value_result.overall_score
            except AttributeError:
                val_score = 0.0

            await emit_event(
                ValueAlignmentEvent.create(
                    action=ctx.message[:100],
                    verdict=value_result.final_verdict,
                    conflicts=val_conflicts,
                    overall_score=val_score,
                    workspace=self._workspace,
                )
            )

            if value_alignment_unsafe and value_alignment_confidence > 0.8:
                _ = ContextEvent.create(
                    EventType.ALIGNMENT_BLOCK,
                    duration_ms=0.0,
                    metadata={
                        "reason": value_alignment_reason,
                        "confidence": value_alignment_confidence,
                        "intent_type": intent_type,
                    },
                )
                blocked_turn = self._build_blocked_turn(
                    ctx=ctx,
                    intent_type=intent_type,
                    confidence=0.0,
                    response=f"BLOCKED: Value alignment unsafe - {value_alignment_reason}",
                    block_reason=f"Alignment: {value_alignment_reason}",
                )
                return PipelineResult(
                    response_content=f"BLOCKED: Value alignment unsafe - {value_alignment_reason}",
                    execution_path=ExecutionPath.BYPASS,
                    confidence=0.0,
                    clarity_level=ClarityLevel.FUZZY,
                    intent_type=intent_type,
                    uncertainty_score=1.0,
                    actions_taken=(),
                    verification_needed=True,
                    blocked=True,
                    block_reason=f"Alignment: {value_alignment_reason}",
                    conversation_turn=blocked_turn,
                    metadata={
                        "session_id": ctx.session_id,
                        "role_id": ctx.role_id,
                        "value_score": val_score,
                        "alignment_confidence": value_alignment_confidence,
                    },
                    error_type=None,
                    retryable=True,
                    blocked_tools=(),
                )

        pre_exec_recommendation = await self._policy.evaluate(
            intent_graph=intent_graph,
            reasoning_chain=reasoning_chain,
            uncertainty=uncertainty,
        )

        self._governance_gate.check_l2_understanding(
            intent_type=intent_type,
            uncertainty=uncertainty,
            execution_path=pre_exec_recommendation.path.value,
        )

        risk_val = 0.0
        try:
            risk_val = float(pre_exec_recommendation.risk_level.value)
        except (AttributeError, ValueError, TypeError):
            risk_val = 0.0

        execution_plan = ExecutionPlan(
            id=str(uuid.uuid4()),
            action=ctx.message[:200] if ctx.message else "unknown",
            intent=intent_type,
            risk_level=risk_val,
            metadata={
                "role_id": ctx.role_id,
                "session_id": ctx.session_id,
                "confidence": confidence,
            },
        )

        hitl_decision = await self._hitl.request_approval(execution_plan)

        if hitl_decision == InterventionDecision.TIMEOUT:
            self._hitl_timeout_count += 1
            return await self._fallback_shadow_mode(
                ctx=ctx,
                intent_type=intent_type,
                confidence=confidence,
                uncertainty=uncertainty,
            )
        elif hitl_decision == InterventionDecision.REJECTED:
            blocked_turn = self._build_blocked_turn(
                ctx=ctx,
                intent_type=intent_type,
                confidence=confidence,
                response="BLOCKED: Human rejected the execution plan",
                block_reason="Human rejected",
            )
            return PipelineResult(
                response_content="BLOCKED: Human rejected the execution plan",
                execution_path=ExecutionPath.BYPASS,
                confidence=confidence,
                clarity_level=ClarityLevel.FUZZY,
                intent_type=intent_type,
                uncertainty_score=uncertainty.uncertainty_score,
                actions_taken=(),
                verification_needed=False,
                blocked=True,
                block_reason="Human rejected",
                conversation_turn=blocked_turn,
                metadata={"session_id": ctx.session_id, "role_id": ctx.role_id},
                error_type=None,
                retryable=True,
                blocked_tools=(),
            )

        pipeline_result = await self._pipeline.execute(
            message=ctx.message,
            intent_graph=intent_graph,
            uncertainty=uncertainty,
            reasoning_chain=reasoning_chain,
            meta_cognition=meta_cognition,
        )

        await emit_event(
            CautiousExecutionEvent.create(
                execution_path=pre_exec_recommendation.path.value,
                requires_confirmation=pre_exec_recommendation.requires_user_confirmation,
                stakes_level=self._get_stakes_level(intent_type),
                workspace=self._workspace,
            )
        )

        if pipeline_result.blocked:
            self._telemetry.record_event(
                "pipeline.blocked",
                {"block_reason": pipeline_result.block_reason or "unknown"},
            )
        else:
            self._telemetry.record_event(
                "pipeline.completed",
                {"path_taken": pipeline_result.path_taken.value},
            )

        if self._enable_evolution and self._evolution:
            reflection_output: ReflectionOutput | None = None
            if self._meta is not None and self._use_llm and llm_reflection is not None:
                reflection_output = llm_reflection
            elif self._meta is not None:
                task_result_for_reflect = {
                    "success": pipeline_result is not None,
                    "quality": 0.5,
                    "unexpected": [],
                }
                intent_for_reflect_fallback = cast(
                    ReflectIntent,
                    {
                        "graph_id": intent_type,
                        "intent_type": intent_type,
                    },
                )
                reflection_output = await self._meta.reflect(task_result_for_reflect, intent_for_reflect_fallback)
            if reflection_output is not None:
                await self._evolution.evolve_from_reflection(reflection_output)
            else:
                await self._evolution.process_trigger(
                    trigger_type=TriggerType.SELF_REFLECTION,
                    content=f"Processed: {intent_type}",
                    context=f"confidence={confidence}",
                )

            await emit_event(
                EvolutionEvent.create(
                    trigger_type="self_reflection",
                    adaptation=f"Processed: {intent_type}",
                    learning_recorded=True,
                    workspace=self._workspace,
                )
            )

        self._governance_gate.check_l1_truthfulness(
            uncertainty_score=uncertainty.uncertainty_score,
            confidence=confidence,
            reasoning_contradicted=False,
        )

        self._governance_gate.check_l3_evolution(
            has_error=pipeline_result.blocked,
            evolution_recorded=self._enable_evolution,
            consecutive_failures=0,
        )

        response_content = self._build_response(pipeline_result, posture, uncertainty)

        if self._enable_personality and self._personality:
            response_content = self._personality.apply_posture_to_response(
                response=response_content,
                posture=posture,
                uncertainty_score=uncertainty.uncertainty_score,
                confidence=confidence,
            )

        turn = ConversationTurn(
            turn_id=f"turn_{len(ctx.ctx.conversation_history) + 1}",
            role_id=ctx.role_id,
            message=ctx.message,
            intent_type=intent_type,
            confidence=confidence,
            execution_path=pipeline_result.path_taken.value,
            response=response_content,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self._telemetry.record_event(
            "cognitive.response_generated",
            {
                "intent_type": intent_type,
                "execution_path": pipeline_result.path_taken.value,
                "blocked": pipeline_result.blocked,
            },
        )

        if self._perception is not None:
            predicted_uncertain = uncertainty.uncertainty_score > 0.6
            actual_correct = confidence > 0.5 and not pipeline_result.blocked
            self._perception._quantifier.record_outcome(
                predicted_uncertainty=uncertainty.uncertainty_score,
                was_correct=not (predicted_uncertain and not actual_correct),
            )

        return PipelineResult(
            response_content=response_content,
            execution_path=pipeline_result.path_taken,
            confidence=confidence,
            clarity_level=self._derive_clarity(pipeline_result),
            intent_type=intent_type,
            uncertainty_score=uncertainty.uncertainty_score,
            actions_taken=pipeline_result.acting_output.actions_taken if pipeline_result.acting_output else (),
            verification_needed=pipeline_result.acting_output.verification_needed
            if pipeline_result.acting_output
            else False,
            blocked=pipeline_result.blocked,
            block_reason=pipeline_result.block_reason,
            conversation_turn=turn,
            metadata={
                "session_id": ctx.session_id,
                "role_id": ctx.role_id,
                "posture": posture.value,
                "trait_profile": trait_profile.dominant_trait.value if trait_profile else None,
            },
            error_type=pipeline_result.error_type,
            retryable=pipeline_result.retryable,
            blocked_tools=pipeline_result.blocked_tools,
        )

    def _build_blocked_turn(
        self,
        ctx: PipelineContext,
        intent_type: str,
        confidence: float,
        response: str,
        block_reason: str,
    ) -> ConversationTurn:
        """Build a blocked conversation turn."""
        return ConversationTurn(
            turn_id=f"turn_{len(ctx.ctx.conversation_history) + 1}",
            role_id=ctx.role_id,
            message=ctx.message,
            intent_type=intent_type,
            confidence=confidence,
            execution_path=ExecutionPath.BYPASS.value,
            response=response,
            timestamp=datetime.now(timezone.utc).isoformat(),
            blocked=True,
            block_reason=block_reason,
        )

    def _build_response(
        self,
        pipeline_result: CognitivePipelineResult,
        posture: InteractionPosture,
        uncertainty: UncertaintyAssessment | None,
    ) -> str:
        """Build response content from pipeline result."""
        if pipeline_result.blocked:
            return f"BLOCKED: {pipeline_result.block_reason}"

        if pipeline_result.acting_output:
            content = pipeline_result.acting_output.content
            if posture == InteractionPosture.TRANSPARENT_REASONING and pipeline_result.thinking_output:
                content = f"[Thinking] {pipeline_result.thinking_output.content}\n\n[Action] {content}"
            return content

        if pipeline_result.thinking_output:
            return pipeline_result.thinking_output.content

        return "No output generated"

    def _derive_clarity(self, pipeline_result: CognitivePipelineResult) -> ClarityLevel:
        """Derive clarity level from pipeline result."""
        if pipeline_result.thinking_output:
            return pipeline_result.thinking_output.clarity_level
        return ClarityLevel.FUZZY

    async def _fallback_shadow_mode(
        self,
        ctx: PipelineContext,
        intent_type: str,
        confidence: float,
        uncertainty: UncertaintyAssessment | None,
    ) -> PipelineResult:
        """Fallback to shadow mode when HITL times out."""
        self._fallback_count += 1

        self._telemetry.record_event(
            "cognitive.fallback.shadow_mode",
            {
                "intent_type": intent_type,
                "fallback_count": self._fallback_count,
                "hitl_timeout_count": self._hitl_timeout_count,
            },
        )

        shadow_response = (
            f"[SHADOW MODE] HITL timeout - action deferred for safety. "
            f"Intent: {intent_type}, Confidence: {confidence:.2f}"
        )

        blocked_turn = ConversationTurn(
            turn_id=f"turn_{len(ctx.ctx.conversation_history) + 1}",
            role_id=ctx.role_id,
            message=ctx.message,
            intent_type=intent_type,
            confidence=confidence,
            execution_path=ExecutionPath.BYPASS.value,
            response=shadow_response,
            timestamp=datetime.now(timezone.utc).isoformat(),
            blocked=False,
            block_reason=None,
        )

        return PipelineResult(
            response_content=shadow_response,
            execution_path=ExecutionPath.BYPASS,
            confidence=confidence,
            clarity_level=ClarityLevel.FUZZY,
            intent_type=intent_type,
            uncertainty_score=uncertainty.uncertainty_score if uncertainty else 0.0,
            actions_taken=("shadow_mode_deferred",),
            verification_needed=True,
            blocked=False,
            block_reason=None,
            conversation_turn=blocked_turn,
            metadata={
                "session_id": ctx.session_id,
                "role_id": ctx.role_id,
                "fallback": True,
                "fallback_count": self._fallback_count,
                "hitl_timeout_count": self._hitl_timeout_count,
            },
            error_type=None,
            retryable=True,
            blocked_tools=(),
        )

    def _get_stakes_level(self, intent_type: str) -> str:
        """Determine stakes level based on intent type."""
        high_stakes = {"delete_file", "execute_command", "modify_file"}
        medium_stakes = {"create_file", "test", "plan"}

        if intent_type in high_stakes:
            return "high"
        elif intent_type in medium_stakes:
            return "medium"
        return "low"
