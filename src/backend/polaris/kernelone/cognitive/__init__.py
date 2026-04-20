"""Cognitive Life Form - Core runtime for cognitive capabilities."""

from polaris.kernelone.cognitive.context import (
    CognitiveContext,
    CognitiveSessionManager,
    ConversationTurn,
    get_session_manager,
)
from polaris.kernelone.cognitive.governance import CognitiveGovernance, VCResult
from polaris.kernelone.cognitive.governance_gate import CognitiveGovernanceGate
from polaris.kernelone.cognitive.middleware import (
    CognitiveMiddleware,
    get_cognitive_middleware,
    reset_cognitive_middleware,
)
from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator, CognitiveResponse
from polaris.kernelone.cognitive.personality.integrator import PersonalityIntegrator
from polaris.kernelone.cognitive.pipeline_coordinator import CognitivePipelineCoordinator
from polaris.kernelone.cognitive.types import ActingOutput, ClarityLevel, ExecutionPath, RiskLevel, ThinkingOutput

__all__ = [
    # Core types
    "ClarityLevel",
    "RiskLevel",
    "ExecutionPath",
    "ThinkingOutput",
    "ActingOutput",
    # Orchestrator
    "CognitiveOrchestrator",
    "CognitiveResponse",
    # Pipeline Coordinator
    "CognitivePipelineCoordinator",
    # Governance Gate
    "CognitiveGovernanceGate",
    # Context
    "CognitiveContext",
    "CognitiveSessionManager",
    "ConversationTurn",
    "get_session_manager",
    # Governance
    "CognitiveGovernance",
    "VCResult",
    # Personality
    "PersonalityIntegrator",
    # Middleware
    "CognitiveMiddleware",
    "get_cognitive_middleware",
    "reset_cognitive_middleware",
]
