"""State-First Context OS exports.

This package provides the canonical session-level context operating substrate:
truth-log projection, structured working state, artifact offload, episode
sealing, budget planning, and memory restore/query primitives.

ContextOS 3.0 Enhancements:
- Context Decision Log (Audit/Replay Layer)
- Multi-Resolution Store (Phase 1)
- Phase-Aware Budgeting (Phase 2)
- Attention Scoring V1 (Phase 3)
"""

from __future__ import annotations

from .attention import AttentionScorer, CandidateRanker, ReasonCodeGenerator
from .attention.embeddings import EmbeddingProvider
from .attention.graph import Edge, EdgeType, EventGraph
from .attention.propagation import GraphPropagator, PropagationConfig, PropagationResult
from .classifier import DialogActClassifier
from .decision_log import (
    AttentionScore,
    ContextDecision,
    ContextDecisionLog,
    ContextDecisionType,
    ProjectionReport,
    ReasonCode,
    create_decision,
)
from .domain_adapters import (
    CodeContextDomainAdapter,
    ContextDomainAdapter,
    DomainRoutingDecision,
    DomainStatePatchHints,
    GenericContextDomainAdapter,
    get_context_domain_adapter,
)
from .evaluation import (
    AttentionObservabilityTrace,
    AttentionRuntimeEvalReport,
    AttentionRuntimeEvalSuite,
    # Attention Runtime evaluation
    AttentionRuntimeMetrics,
    AttentionRuntimeQualityCase,
    AttentionRuntimeQualityResult,
    ContextOSGateFailure,
    ContextOSQualityCase,
    ContextOSQualityResult,
    ContextOSQualitySummary,
    ContextOSRolloutGatePolicy,
    ContextOSRolloutGateResult,
    evaluate_attention_runtime_case,
    evaluate_attention_runtime_suite,
    evaluate_context_os_case,
    evaluate_context_os_rollout_gate,
    evaluate_context_os_suite,
    generate_attention_runtime_report,
    load_attention_runtime_eval_suite,
    validate_attention_runtime_report_schema,
)
from .introspection import summarize_context_os_payload
from .invariants import ContextOSInvariantViolationError, validate_context_os_persisted_projection
from .memory import ConflictChecker, ConflictStatus, MemoryCandidate, MemoryCandidateProvider, MemoryManager
from .metrics import MetricsCollector, MetricsExporter
from .metrics_collector import (
    CognitiveRuntimeMetrics,
    CognitiveRuntimeMetricsCollectionResult,
    CognitiveRuntimeMetricsCollector,
    collect_cognitive_runtime_metrics,
)
from .models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    BudgetPlanV2 as BudgetPlan,
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    ContextSlicePlanV2 as ContextSlicePlan,
    ContextSliceSelectionV2 as ContextSliceSelection,
    DecisionEntryV2 as DecisionEntry,
    DialogAct,
    DialogActResultV2 as DialogActResult,
    EpisodeCardV2 as EpisodeCard,
    PendingFollowUpV2 as PendingFollowUp,
    RoutingClassEnum as RoutingClass,
    RunCardV2 as RunCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from .multi_resolution_store import (
    MultiResolutionContent,
    MultiResolutionStore,
    ResolutionEntry,
    ResolutionLevel,
    create_extractive_content,
    create_structured_content,
    create_stub_content,
)
from .phase_budget_planner import (
    BudgetProfile,
    PhaseAwareBudgetPlan,
    PhaseAwareBudgetPlanner,
)
from .phase_detection import (
    TaskPhase,
    TaskPhaseDetector,
)
from .pipeline.attention_aware_stages import AttentionAwareWindowCollector
from .pipeline.enhanced_runner import EnhancedPipelineRunner
from .pipeline.phase_aware_stages import PhaseAwareBudgetPlannerStage
from .policies import StateFirstContextOSPolicy
from .predictive import PredictionResult, PredictionStrategy, PredictiveCompressor
from .replay_suite_generator import (
    create_multi_turn_case,
    create_short_session_case,
    generate_followup_lifecycle_suite,
    generate_long_session_suite,
    generate_multi_topic_suite,
    generate_replay_benchmark_suite,
)
from .runtime import StateFirstContextOS
from .schemas import (
    REPORT_SCHEMA,
    SUITE_SCHEMA,
    ValidationResult,
    validate_report_file,
    validate_suite_file,
)

__all__ = [
    # Schema validation
    "REPORT_SCHEMA",
    "SUITE_SCHEMA",
    "ArtifactRecord",
    # Attention Scoring (ContextOS 3.0 Phase 3)
    "AttentionAwareWindowCollector",
    "AttentionRuntimeEvalReport",
    "AttentionRuntimeEvalSuite",
    # Attention Runtime evaluation
    "AttentionRuntimeMetrics",
    "AttentionRuntimeQualityCase",
    "AttentionRuntimeQualityResult",
    "AttentionScore",
    "AttentionScorer",
    "BudgetPlan",
    "BudgetProfile",
    "CandidateRanker",
    "CodeContextDomainAdapter",
    # Cognitive Runtime metrics collection
    "CognitiveRuntimeMetrics",
    "CognitiveRuntimeMetricsCollectionResult",
    "CognitiveRuntimeMetricsCollector",
    "ConflictChecker",
    "ConflictStatus",
    # Context Decision Log (ContextOS 3.0 Phase 0)
    "ContextDecision",
    "ContextDecisionLog",
    "ContextDecisionType",
    "ContextDomainAdapter",
    "ContextOSGateFailure",
    "ContextOSInvariantViolationError",
    "ContextOSProjection",
    "ContextOSQualityCase",
    "ContextOSQualityResult",
    "ContextOSQualitySummary",
    "ContextOSRolloutGatePolicy",
    "ContextOSRolloutGateResult",
    "ContextOSSnapshot",
    "ContextSlicePlan",
    "ContextSliceSelection",
    "DecisionEntry",
    "DialogAct",
    "DialogActClassifier",
    "DialogActResult",
    "DomainRoutingDecision",
    "DomainStatePatchHints",
    "Edge",
    "EdgeType",
    "EmbeddingProvider",
    "EnhancedPipelineRunner",
    "EpisodeCard",
    "EventGraph",
    "GenericContextDomainAdapter",
    "GraphPropagator",
    # Memory (ContextOS 3.0 P1)
    "MemoryCandidate",
    "MemoryCandidateProvider",
    "MemoryManager",
    # Metrics (ContextOS 3.0 P2)
    "MetricsCollector",
    "MetricsExporter",
    # Multi-Resolution Store (ContextOS 3.0 Phase 1)
    "MultiResolutionContent",
    "MultiResolutionStore",
    "PendingFollowUp",
    # Phase-Aware Budgeting (ContextOS 3.0 Phase 2)
    "PhaseAwareBudgetPlan",
    "PhaseAwareBudgetPlanner",
    "PhaseAwareBudgetPlannerStage",
    "PredictionResult",
    "PredictionStrategy",
    "PredictiveCompressor",
    "ProjectionReport",
    "PropagationConfig",
    "PropagationResult",
    "ReasonCode",
    "ReasonCodeGenerator",
    "ResolutionEntry",
    "ResolutionLevel",
    "RoutingClass",
    "RunCard",
    "StateEntry",
    "StateFirstContextOS",
    "StateFirstContextOSPolicy",
    "TaskPhase",
    "TaskPhaseDetector",
    "TaskStateView",
    "TranscriptEvent",
    "UserProfileState",
    "ValidationResult",
    "WorkingState",
    "collect_cognitive_runtime_metrics",
    # Decision log factory
    "create_decision",
    # Multi-Resolution helpers
    "create_extractive_content",
    # Replay suite generation
    "create_multi_turn_case",
    "create_short_session_case",
    "create_structured_content",
    "create_stub_content",
    "evaluate_attention_runtime_case",
    "evaluate_attention_runtime_suite",
    "evaluate_context_os_case",
    "evaluate_context_os_rollout_gate",
    "evaluate_context_os_suite",
    "generate_attention_runtime_report",
    "generate_followup_lifecycle_suite",
    "generate_long_session_suite",
    "generate_multi_topic_suite",
    "generate_replay_benchmark_suite",
    "get_context_domain_adapter",
    "load_attention_runtime_eval_suite",
    "summarize_context_os_payload",
    "validate_attention_runtime_report_schema",
    "validate_context_os_persisted_projection",
    "validate_report_file",
    "validate_suite_file",
]
