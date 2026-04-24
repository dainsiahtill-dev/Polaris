"""State-First Context OS exports.

This package provides the canonical session-level context operating substrate:
truth-log projection, structured working state, artifact offload, episode
sealing, budget planning, and memory restore/query primitives.
"""

from __future__ import annotations

from .classifier import DialogActClassifier
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
from .policies import StateFirstContextOSPolicy
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
    "REPORT_SCHEMA",
    # Schema validation
    "SUITE_SCHEMA",
    "ArtifactRecord",
    "AttentionObservabilityTrace",
    "AttentionRuntimeEvalReport",
    "AttentionRuntimeEvalSuite",
    # Attention Runtime evaluation
    "AttentionRuntimeMetrics",
    "AttentionRuntimeQualityCase",
    "AttentionRuntimeQualityResult",
    "BudgetPlan",
    "CodeContextDomainAdapter",
    # Cognitive Runtime metrics collection
    "CognitiveRuntimeMetrics",
    "CognitiveRuntimeMetricsCollectionResult",
    "CognitiveRuntimeMetricsCollector",
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
    "EpisodeCard",
    "GenericContextDomainAdapter",
    "PendingFollowUp",
    "RoutingClass",
    "RunCard",
    "StateEntry",
    "StateFirstContextOS",
    "StateFirstContextOSPolicy",
    "TaskStateView",
    "TranscriptEvent",
    "UserProfileState",
    "ValidationResult",
    "WorkingState",
    "collect_cognitive_runtime_metrics",
    # Replay suite generation
    "create_multi_turn_case",
    "create_short_session_case",
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
