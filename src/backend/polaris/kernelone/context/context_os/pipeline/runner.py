"""Pipeline runner: orchestrates the 7-stage projection pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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
    5. WindowCollector    - Collect pinned active window events
    6. EpisodeSealer      - Seal closed episodes based on active window
    7. ArtifactSelector   - Select artifacts and episodes for prompt injection
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
    ) -> PipelineOutput:
        merger_out = self._run_stage("TranscriptMerger", self._merger.process, inp)
        canon_out = self._run_stage("Canonicalizer", self._canonicalizer.process, inp, merger_out)
        patcher_out = self._run_stage("StatePatcher", self._patcher.process, canon_out)
        budget_out = self._run_stage("BudgetPlanner", self._budget_planner.process, patcher_out, canon_out)
        window_out = self._run_stage(
            "WindowCollector", self._window_collector.process, budget_out, patcher_out, canon_out, inp
        )
        episode_out = self._run_stage(
            "EpisodeSealer", self._episode_sealer.process, window_out, patcher_out, canon_out, inp
        )
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

        return PipelineOutput(
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
    ) -> ContextOSProjection:
        """Run the pipeline and return a ContextOSProjection."""
        pipe_out = self.run(inp, adapter_id)

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

        return ContextOSProjection(
            snapshot=new_snapshot,
            head_anchor=pipe_out.head_anchor,
            tail_anchor=pipe_out.tail_anchor,
            active_window=pipe_out.active_window,
            artifact_stubs=pipe_out.artifact_stubs,
            episode_cards=pipe_out.episode_cards,
            run_card=pipe_out.run_card,
            context_slice_plan=pipe_out.context_slice_plan,
        )
