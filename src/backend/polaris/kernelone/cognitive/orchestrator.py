"""Cognitive Orchestrator - Top-level entry point for Cognitive Life Form."""

from __future__ import annotations

from dataclasses import dataclass, field

from polaris.kernelone.cognitive.config import (
    COGNITIVE_ENABLE_EVOLUTION,
    COGNITIVE_ENABLE_GOVERNANCE,
    COGNITIVE_ENABLE_PERSONALITY,
    COGNITIVE_ENABLE_TELEMETRY,
    COGNITIVE_ENABLE_VALUE_ALIGNMENT,
    COGNITIVE_USE_LLM,
)
from polaris.kernelone.cognitive.context import (
    CognitiveContext,
    ConversationTurn,
    get_session_manager,
)
from polaris.kernelone.cognitive.evolution.engine import EvolutionEngine
from polaris.kernelone.cognitive.evolution.store import EvolutionStore
from polaris.kernelone.cognitive.governance_gate import CognitiveGovernanceGate
from polaris.kernelone.cognitive.hitl import HumanInterventionQueue
from polaris.kernelone.cognitive.llm_adapter import LLMInvoker, create_llm_adapter
from polaris.kernelone.cognitive.personality.integrator import PersonalityIntegrator
from polaris.kernelone.cognitive.pipeline_coordinator import CognitivePipelineCoordinator, PipelineContext
from polaris.kernelone.cognitive.telemetry import CognitiveTelemetry
from polaris.kernelone.cognitive.types import ClarityLevel, ExecutionPath


@dataclass(frozen=True)
class CognitiveResponse:
    """Final response from the cognitive orchestrator."""

    content: str
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
    metadata: dict[str, object] = field(default_factory=dict)
    error_type: str | None = None
    retryable: bool = True
    blocked_tools: tuple[str, ...] = field(default_factory=tuple)


class CognitiveOrchestrator:
    """
     Top-level orchestrator for Cognitive Life Form.

     Acts as a facade that delegates to:
     - CognitiveGovernanceGate: handles all governance checks
     - CognitivePipelineCoordinator: orchestrates the main pipeline

    串联 perception → reasoning → decision → thinking → acting → evolution

     Usage:
         orchestrator = CognitiveOrchestrator()
         response = await orchestrator.process(
             message="Create a new API endpoint",
             session_id="session_123",
             role_id="director",
         )
    """

    def __init__(
        self,
        workspace: str | None = None,
        enable_evolution: bool | None = None,
        enable_personality: bool | None = None,
        enable_value_alignment: bool | None = None,
        enable_governance: bool | None = None,
        llm_invoker: LLMInvoker | None = None,
        use_llm: bool | None = None,
        enable_telemetry: bool | None = None,
    ) -> None:
        self._workspace = workspace or "."

        self._enable_evolution = enable_evolution if enable_evolution is not None else COGNITIVE_ENABLE_EVOLUTION
        self._enable_personality = (
            enable_personality if enable_personality is not None else COGNITIVE_ENABLE_PERSONALITY
        )
        self._enable_value_alignment = (
            enable_value_alignment if enable_value_alignment is not None else COGNITIVE_ENABLE_VALUE_ALIGNMENT
        )
        self._enable_governance = enable_governance if enable_governance is not None else COGNITIVE_ENABLE_GOVERNANCE
        self._use_llm = use_llm if use_llm is not None else COGNITIVE_USE_LLM
        self._enable_telemetry = enable_telemetry if enable_telemetry is not None else COGNITIVE_ENABLE_TELEMETRY

        self._telemetry = CognitiveTelemetry(enabled=self._enable_telemetry)

        self._governance_gate = CognitiveGovernanceGate(enabled=self._enable_governance)

        self._llm = llm_invoker or create_llm_adapter(workspace=self._workspace, use_llm=self._use_llm)

        self._evolution: EvolutionEngine | None = None
        if self._enable_evolution:
            store = EvolutionStore(self._workspace)
            self._evolution = EvolutionEngine(store)

        self._personality = PersonalityIntegrator() if self._enable_personality else None

        self._value_alignment: IAlignmentService | None = None
        if self._enable_value_alignment:
            try:
                # ACGA 2.0: Use IAlignmentService port via adapter
                from polaris.kernelone.ports import IAlignmentService
                from polaris.cells.adapters.kernelone import AlignmentServiceAdapter

                self._value_alignment = AlignmentServiceAdapter()
            except ImportError:
                self._value_alignment = None

        self._hitl = HumanInterventionQueue(timeout_seconds=15)

        self._sessions = get_session_manager(workspace=self._workspace)

        self._coordinator = CognitivePipelineCoordinator(
            workspace=self._workspace,
            governance_gate=self._governance_gate,
            telemetry=self._telemetry,
            hitl=self._hitl,
            evolution=self._evolution,
            personality=self._personality,
            value_alignment=self._value_alignment,
            use_llm=self._use_llm,
            enable_evolution=self._enable_evolution,
            enable_personality=self._enable_personality,
            enable_value_alignment=self._enable_value_alignment,
        )

    async def process(
        self,
        message: str,
        session_id: str = "default",
        role_id: str = "director",
        workspace: str | None = None,
    ) -> CognitiveResponse:
        """
        Process a message through the complete cognitive pipeline.

        Args:
            message: The user's message
            session_id: Session identifier for context persistence
            role_id: The role processing the message (pm, architect, chief_engineer, director, qa, scout)
            workspace: Workspace path for file operations

        Returns:
            CognitiveResponse with the processed result
        """
        ctx = self._sessions.get_or_create_session(session_id, role_id)

        pipeline_ctx = PipelineContext(
            message=message,
            session_id=session_id,
            role_id=role_id,
            ctx=ctx,
            workspace=workspace or self._workspace,
            use_llm=self._use_llm,
            enable_evolution=self._enable_evolution,
            enable_personality=self._enable_personality,
            enable_value_alignment=self._enable_value_alignment,
            evolution=self._evolution,
            value_alignment=self._value_alignment,
            personality=self._personality,
            hitl=self._hitl,
        )

        result = await self._coordinator.execute(pipeline_ctx)

        if result.conversation_turn is not None:
            self._sessions.update_session(session_id, result.conversation_turn)

        return CognitiveResponse(
            content=result.response_content,
            execution_path=result.execution_path,
            confidence=result.confidence,
            clarity_level=result.clarity_level,
            intent_type=result.intent_type,
            uncertainty_score=result.uncertainty_score,
            actions_taken=result.actions_taken,
            verification_needed=result.verification_needed,
            blocked=result.blocked,
            block_reason=result.block_reason,
            conversation_turn=result.conversation_turn,
            metadata=result.metadata,
            error_type=result.error_type,
            retryable=result.retryable,
            blocked_tools=result.blocked_tools,
        )

    def get_session(self, session_id: str) -> CognitiveContext | None:
        """Get cognitive context for a session."""
        return self._sessions.get_session(session_id)

    def reset_session(self, session_id: str) -> None:
        """Reset a session."""
        self._sessions.delete_session(session_id)
