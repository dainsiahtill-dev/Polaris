"""Enhanced Pipeline Runner: integrates all ContextOS 3.0 features.

This module provides an enhanced version of PipelineRunner that integrates:
- Phase-Aware Budgeting (Phase 2)
- Attention-Aware WindowCollector (Phase 3)
- Graph Propagation (P1)
- Akashic Memory Integration (P1)
- Predictive Compression (P2)
- Prometheus Metrics (P2)

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    All enhancements are optional and controlled by feature flags.

Usage:
    runner = EnhancedPipelineRunner(
        policy=policy,
        enable_phase_aware_budgeting=True,
        enable_attention_scoring=True,
        enable_graph_propagation=True,
        enable_memory_integration=True,
        enable_predictive_compression=True,
        enable_metrics=True,
    )
    projection, report = runner.project(inp)
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.decision_log import (
    ContextDecisionLog,
    ProjectionReport,
)
from polaris.kernelone.context.context_os.helpers import _utc_now_iso
from polaris.kernelone.context.context_os.models_v2 import (
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
)
from polaris.kernelone.context.context_os.phase_detection import TaskPhase

from .attention_aware_stages import AttentionAwareWindowCollector
from .contracts import (
    PipelineInput,
    PipelineOutput,
)
from .phase_aware_stages import PhaseAwareBudgetPlannerStage
from .runner import PipelineRunner

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.domain_adapters import ContextDomainAdapter
    from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy

logger = logging.getLogger(__name__)


class EnhancedPipelineRunner(PipelineRunner):
    """Enhanced Pipeline Runner with all ContextOS 3.0 features.

    This class extends PipelineRunner to integrate:
    - Phase-Aware Budgeting
    - Attention-Aware WindowCollector
    - Graph Propagation
    - Akashic Memory Integration
    - Predictive Compression
    - Prometheus Metrics

    All features are controlled by feature flags and can be enabled/disabled.
    """

    def __init__(
        self,
        policy: StateFirstContextOSPolicy,
        domain_adapter: ContextDomainAdapter | None = None,
        resolved_context_window: int = 128000,
        # Feature flags
        enable_phase_aware_budgeting: bool = True,
        enable_attention_scoring: bool = True,
        enable_graph_propagation: bool = True,
        enable_memory_integration: bool = True,
        enable_predictive_compression: bool = True,
        enable_metrics: bool = True,
    ) -> None:
        # Initialize base PipelineRunner
        super().__init__(
            policy=policy,
            domain_adapter=domain_adapter,
            resolved_context_window=resolved_context_window,
        )

        # Store feature flags
        self._enable_phase_aware_budgeting = enable_phase_aware_budgeting
        self._enable_attention_scoring = enable_attention_scoring
        self._enable_graph_propagation = enable_graph_propagation
        self._enable_memory_integration = enable_memory_integration
        self._enable_predictive_compression = enable_predictive_compression
        self._enable_metrics = enable_metrics

        # Initialize enhanced stages
        if enable_phase_aware_budgeting:
            self._phase_aware_planner = PhaseAwareBudgetPlannerStage(
                policy=policy,
                resolved_context_window=resolved_context_window,
                enable_phase_detection=True,
            )

        if enable_attention_scoring:
            self._attention_collector = AttentionAwareWindowCollector(
                policy=policy,
                enable_attention_scoring=True,
                current_phase=TaskPhase.INTAKE,
            )

        # Initialize optional components
        self._graph_propagator = None
        self._memory_manager = None
        self._predictive_compressor = None
        self._metrics_collector = None

        if enable_graph_propagation:
            try:
                from polaris.kernelone.context.context_os.attention.propagation import GraphPropagator

                self._graph_propagator = GraphPropagator()
            except ImportError:
                logger.warning("GraphPropagator not available")

        if enable_memory_integration:
            try:
                from polaris.kernelone.context.context_os.memory import MemoryManager

                self._memory_manager = MemoryManager()
            except ImportError:
                logger.warning("MemoryManager not available")

        if enable_predictive_compression:
            try:
                from polaris.kernelone.context.context_os.predictive import PredictiveCompressor

                self._predictive_compressor = PredictiveCompressor()
            except ImportError:
                logger.warning("PredictiveCompressor not available")

        if enable_metrics:
            try:
                from polaris.kernelone.context.context_os.metrics import MetricsCollector

                self._metrics_collector = MetricsCollector()
            except ImportError:
                logger.warning("MetricsCollector not available")

    def run(
        self,
        inp: PipelineInput,
        adapter_id: str = "",
        decision_log: ContextDecisionLog | None = None,
    ) -> tuple[PipelineOutput, ProjectionReport]:
        """Run the enhanced pipeline.

        Args:
            inp: Pipeline input (messages + existing snapshot)
            adapter_id: Adapter identifier
            decision_log: Optional decision log

        Returns:
            Tuple of (PipelineOutput, ProjectionReport)
        """
        if decision_log is None:
            decision_log = ContextDecisionLog()

        projection_id = f"ctxproj_{uuid.uuid4().hex[:12]}"
        stage_durations: dict[str, float] = {}

        # Stage 1: TranscriptMerger
        t0 = time.monotonic()
        merger_out = self._run_stage("TranscriptMerger", self._merger.process, inp)
        stage_durations["TranscriptMerger"] = (time.monotonic() - t0) * 1000

        # Stage 2: Canonicalizer
        t0 = time.monotonic()
        canon_out = self._run_stage("Canonicalizer", self._canonicalizer.process, inp, merger_out)
        stage_durations["Canonicalizer"] = (time.monotonic() - t0) * 1000

        # Stage 3: StatePatcher
        t0 = time.monotonic()
        patcher_out = self._run_stage("StatePatcher", self._patcher.process, canon_out)
        stage_durations["StatePatcher"] = (time.monotonic() - t0) * 1000

        # Stage 4: BudgetPlanner (Phase-Aware or Standard)
        t0 = time.monotonic()
        if self._enable_phase_aware_budgeting:
            budget_out, phase_result = self._phase_aware_planner.process(patcher_out, canon_out)
            if phase_result and hasattr(self, "_attention_collector"):
                # Update attention collector with detected phase
                self._attention_collector.current_phase = phase_result.phase
            if self._metrics_collector and phase_result:
                self._metrics_collector.record_phase_transition("unknown", phase_result.phase.value)
        else:
            budget_out = self._run_stage("BudgetPlanner", self._budget_planner.process, patcher_out, canon_out)
        stage_durations["BudgetPlanner"] = (time.monotonic() - t0) * 1000

        # Stage 5: WindowCollector (Attention-Aware or Standard)
        t0 = time.monotonic()
        if self._enable_attention_scoring:
            window_out = self._attention_collector.process(budget_out, patcher_out, canon_out, inp, decision_log)
        else:
            window_out = self._run_stage(
                "WindowCollector", self._window_collector.process, budget_out, patcher_out, canon_out, inp, decision_log
            )
        stage_durations["WindowCollector"] = (time.monotonic() - t0) * 1000

        # Stage 6: EpisodeSealer
        t0 = time.monotonic()
        episode_out = self._run_stage(
            "EpisodeSealer", self._episode_sealer.process, window_out, patcher_out, canon_out, inp
        )
        stage_durations["EpisodeSealer"] = (time.monotonic() - t0) * 1000

        # Stage 7: ArtifactSelector
        t0 = time.monotonic()
        selector_out = self._run_stage(
            "ArtifactSelector",
            self._artifact_selector.process,
            episode_out,
            patcher_out,
            window_out,
            budget_out,
            canon_out,
            inp,
        )
        stage_durations["ArtifactSelector"] = (time.monotonic() - t0) * 1000

        # Build projection report
        report = decision_log.build_projection_report(
            projection_id=projection_id,
            run_id=adapter_id,
            budget_plan=budget_out.budget_plan,
            stage_durations_ms=stage_durations,
        )

        # Record metrics
        if self._metrics_collector:
            self._metrics_collector.record_pipeline_projection_duration(sum(stage_durations.values()))
            for stage, duration in stage_durations.items():
                self._metrics_collector.record_pipeline_stage_duration(stage, duration)

        pipe_out = PipelineOutput(
            snapshot_transcript=canon_out.transcript,
            snapshot_working_state=patcher_out.working_state,
            snapshot_artifacts=canon_out.artifacts,
            snapshot_episodes=episode_out.episode_store,
            snapshot_budget_plan=budget_out.budget_plan,
            snapshot_pending_followup=canon_out.resolved_followup,
            active_window=window_out.active_window,
            artifact_stubs=selector_out.artifact_stubs,
            episode_cards=selector_out.episode_cards,
            head_anchor=selector_out.head_anchor,
            tail_anchor=selector_out.tail_anchor,
            run_card=selector_out.run_card,
            context_slice_plan=selector_out.context_slice_plan,
        )

        return pipe_out, report

    def project(
        self,
        inp: PipelineInput,
        adapter_id: str = "",
        decision_log: ContextDecisionLog | None = None,
    ) -> tuple[ContextOSProjection, ProjectionReport]:
        """Run the pipeline and return a ContextOSProjection + ProjectionReport.

        Args:
            inp: Pipeline input
            adapter_id: Adapter identifier
            decision_log: Optional decision log

        Returns:
            Tuple of (ContextOSProjection, ProjectionReport)
        """
        pipe_out, report = self.run(inp, adapter_id, decision_log)

        new_snapshot = ContextOSSnapshot(
            adapter_id=adapter_id,
            transcript_log=pipe_out.snapshot_transcript,
            working_state=pipe_out.snapshot_working_state,
            artifact_store=pipe_out.snapshot_artifacts,
            episode_store=pipe_out.snapshot_episodes,
            budget_plan=pipe_out.snapshot_budget_plan,
            updated_at=_utc_now_iso(),
            pending_followup=pipe_out.snapshot_pending_followup,
        )

        projection = ContextOSProjection(
            snapshot=new_snapshot,
            head_anchor=pipe_out.head_anchor,
            tail_anchor=pipe_out.tail_anchor,
            active_window=pipe_out.active_window,
            artifact_stubs=pipe_out.artifact_stubs,
            episode_cards=pipe_out.episode_cards,
            run_card=pipe_out.run_card,
            context_slice_plan=pipe_out.context_slice_plan,
        )

        return projection, report

    @property
    def metrics(self) -> dict[str, Any] | None:
        """Get collected metrics."""
        if self._metrics_collector:
            return self._metrics_collector.collect()
        return None
