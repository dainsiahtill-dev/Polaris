"""Pipeline runner: orchestrates the 7-stage projection pipeline."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
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

from .contracts import (
    PipelineInput,
    PipelineOutput,
)
from .stages import (
    ArtifactSelector,
    BudgetPlanner,
    Canonicalizer,
    EpisodeSealer,
    StatePatcher,
    TranscriptMerger,
    WindowCollector,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.domain_adapters import ContextDomainAdapter
    from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Orchestrates the 7-stage projection pipeline.

    Stages
    =====
    1. TranscriptMerger   - Merge existing transcript with new messages
    2. Canonicalizer      - Dialog act classification, routing, artifact offload
    3. StatePatcher       - Extract state hints and build WorkingState
    4. BudgetPlanner      - Compute token budgets and validate invariants
    5. WindowCollector    - Collect pinned active window events (with decision logging)
    6. EpisodeSealer      - Seal closed episodes based on active window
    7. ArtifactSelector   - Select artifacts and episodes for prompt injection

    ContextOS 3.0 Enhancement
    ========================
    - Every projection produces a ProjectionReport (Audit/Replay Layer)
    - WindowCollector logs every include/exclude/compress decision
    - Decision logs are mandatory (not optional audit)
    """

    def __init__(
        self,
        policy: StateFirstContextOSPolicy,
        domain_adapter: ContextDomainAdapter | None = None,
        resolved_context_window: int = 128000,
    ) -> None:
        self._policy = policy
        self._domain_adapter = domain_adapter
        self._resolved_context_window = resolved_context_window

        # Initialize all 7 stages
        self._merger = TranscriptMerger(domain_adapter=domain_adapter)
        self._canonicalizer = Canonicalizer(policy=policy, domain_adapter=domain_adapter)
        self._patcher = StatePatcher(policy=policy, domain_adapter=domain_adapter)
        self._budget_planner = BudgetPlanner(policy=policy, resolved_context_window=resolved_context_window)
        self._window_collector = WindowCollector(policy=policy)
        self._episode_sealer = EpisodeSealer(policy=policy, domain_adapter=domain_adapter)
        self._artifact_selector = ArtifactSelector(policy=policy)

    def run(
        self,
        inp: PipelineInput,
        adapter_id: str = "",
        decision_log: ContextDecisionLog | None = None,
    ) -> tuple[PipelineOutput, ProjectionReport]:
        """Run the pipeline and return output + decision report.

        Args:
            inp: Pipeline input (messages + existing snapshot)
            adapter_id: Adapter identifier
            decision_log: Optional decision log (creates new one if None)

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

        # Stage 4: BudgetPlanner
        t0 = time.monotonic()
        budget_out = self._run_stage("BudgetPlanner", self._budget_planner.process, patcher_out, canon_out)
        stage_durations["BudgetPlanner"] = (time.monotonic() - t0) * 1000

        # Stage 5: WindowCollector (with decision logging)
        t0 = time.monotonic()
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

    def _run_stage(self, stage_name: str, stage_fn: Callable[..., Any], *args: Any) -> Any:
        """Run a pipeline stage.

        TODO(P2): Add per-stage fallback factories so that failures return
        correctly-typed empty outputs rather than propagating exceptions.
        Current pipeline assumes all stages succeed; partial degradation
        requires designing fallback objects for each output type.
        """
        return stage_fn(*args)

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
