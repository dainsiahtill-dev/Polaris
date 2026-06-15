"""Pipeline runner: orchestrates the 7-stage projection pipeline."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.decision_log import (
    ContextDecisionLog,
    ContextDecisionType,
    ProjectionReport,
    ReasonCode,
    build_context_result_id,
    create_decision,
)
from polaris.kernelone.context.context_os.helpers import _utc_now_iso
from polaris.kernelone.context.context_os.models_v2 import (
    BudgetPlanV2 as BudgetPlan,
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    ContextSlicePlanV2 as ContextSlicePlan,
    RunCardV2 as RunCard,
    WorkingStateV2 as WorkingState,
)

from .contracts import (
    ArtifactSelectorOutput,
    BudgetPlannerOutput,
    CanonicalizerOutput,
    EpisodeSealerOutput,
    PipelineInput,
    PipelineOutput,
    StageResult,
    StatePatcherOutput,
    TranscriptMergerOutput,
    WindowCollectorOutput,
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
        context_result_id = build_context_result_id(projection_id)
        stage_durations: dict[str, float] = {}
        stage_results: list[StageResult[Any]] = []

        # Stage 1: TranscriptMerger
        t0 = time.monotonic()
        merger_result = self._run_stage(
            "TranscriptMerger",
            self._merger.process,
            inp,
            fallback_factory=lambda _exc: self._fallback_merger(inp),
            decision_log=decision_log,
        )
        stage_results.append(merger_result)
        merger_out = merger_result.value
        stage_durations["TranscriptMerger"] = (time.monotonic() - t0) * 1000

        # Stage 2: Canonicalizer
        t0 = time.monotonic()
        canon_result = self._run_stage(
            "Canonicalizer",
            self._canonicalizer.process,
            inp,
            merger_out,
            fallback_factory=lambda _exc: self._fallback_canonicalizer(inp, merger_out),
            decision_log=decision_log,
        )
        stage_results.append(canon_result)
        canon_out = canon_result.value
        stage_durations["Canonicalizer"] = (time.monotonic() - t0) * 1000

        # Stage 3: StatePatcher
        t0 = time.monotonic()
        patcher_result = self._run_stage(
            "StatePatcher",
            self._patcher.process,
            canon_out,
            fallback_factory=lambda _exc: self._fallback_state_patcher(),
            decision_log=decision_log,
        )
        stage_results.append(patcher_result)
        patcher_out = patcher_result.value
        stage_durations["StatePatcher"] = (time.monotonic() - t0) * 1000

        # Stage 4: BudgetPlanner
        t0 = time.monotonic()
        budget_result = self._run_stage(
            "BudgetPlanner",
            self._budget_planner.process,
            patcher_out,
            canon_out,
            fallback_factory=lambda _exc: self._fallback_budget_planner(),
            decision_log=decision_log,
        )
        stage_results.append(budget_result)
        budget_out = budget_result.value
        stage_durations["BudgetPlanner"] = (time.monotonic() - t0) * 1000

        # Stage 5: WindowCollector (with decision logging)
        t0 = time.monotonic()
        window_result = self._run_stage(
            "WindowCollector",
            self._window_collector.process,
            budget_out,
            patcher_out,
            canon_out,
            inp,
            decision_log,
            fallback_factory=lambda _exc: self._fallback_window_collector(inp, canon_out),
            decision_log=decision_log,
        )
        stage_results.append(window_result)
        window_out = window_result.value
        stage_durations["WindowCollector"] = (time.monotonic() - t0) * 1000

        # Stage 6: EpisodeSealer
        t0 = time.monotonic()
        episode_result = self._run_stage(
            "EpisodeSealer",
            self._episode_sealer.process,
            window_out,
            patcher_out,
            canon_out,
            inp,
            fallback_factory=lambda _exc: self._fallback_episode_sealer(inp),
            decision_log=decision_log,
        )
        stage_results.append(episode_result)
        episode_out = episode_result.value
        stage_durations["EpisodeSealer"] = (time.monotonic() - t0) * 1000

        # Stage 7: ArtifactSelector
        t0 = time.monotonic()
        selector_result = self._run_stage(
            "ArtifactSelector",
            self._artifact_selector.process,
            episode_out,
            patcher_out,
            window_out,
            budget_out,
            canon_out,
            inp,
            fallback_factory=lambda _exc: self._fallback_artifact_selector(episode_out),
            decision_log=decision_log,
        )
        stage_results.append(selector_result)
        selector_out = selector_result.value
        stage_durations["ArtifactSelector"] = (time.monotonic() - t0) * 1000
        stage_fallbacks = tuple(result.stage_name for result in stage_results if result.fallback_used)
        errors = tuple(
            {
                "stage": result.stage_name,
                "error_type": result.error_type,
                "error_message": result.error_message,
            }
            for result in stage_results
            if result.fallback_used
        )

        # Build projection report
        report = decision_log.build_projection_report(
            projection_id=projection_id,
            context_result_id=context_result_id,
            run_id=adapter_id,
            budget_plan=budget_out.budget_plan,
            stage_durations_ms=stage_durations,
            errors=errors,
            stage_fallbacks=stage_fallbacks,
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

    def _run_stage(
        self,
        stage_name: str,
        stage_fn: Callable[..., Any],
        *args: Any,
        fallback_factory: Callable[[BaseException], Any] | None = None,
        decision_log: ContextDecisionLog | None = None,
    ) -> StageResult[Any]:
        """Run a pipeline stage and return a typed result wrapper."""
        try:
            return StageResult(stage_name=stage_name, value=stage_fn(*args))
        except Exception as exc:
            if fallback_factory is None:
                raise
            logger.warning("ContextOS pipeline stage %s failed; using fallback: %s", stage_name, exc)
            fallback_value = fallback_factory(exc)
            if decision_log is not None:
                decision_log.record(
                    create_decision(
                        decision_type=ContextDecisionType.EXCLUDE,
                        target_event_id=None,
                        reason="pipeline_stage_fallback",
                        reason_codes=(ReasonCode.PIPELINE_STAGE_FAILED,),
                        content_source="contextos.pipeline",
                        resolution_used="fallback",
                        explanation=f"{stage_name} failed with {type(exc).__name__}: {exc}",
                    )
                )
            return StageResult(
                stage_name=stage_name,
                value=fallback_value,
                ok=False,
                fallback_used=True,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def _fallback_merger(self, inp: PipelineInput) -> TranscriptMergerOutput:
        return TranscriptMergerOutput(transcript=tuple(inp.existing_snapshot_transcript))

    def _fallback_canonicalizer(
        self,
        inp: PipelineInput,
        merger_out: TranscriptMergerOutput,
    ) -> CanonicalizerOutput:
        return CanonicalizerOutput(
            transcript=tuple(merger_out.transcript),
            artifacts=tuple(inp.existing_snapshot_artifacts),
            resolved_followup=inp.current_pending_followup,
        )

    def _fallback_state_patcher(self) -> StatePatcherOutput:
        return StatePatcherOutput(working_state=WorkingState())

    def _fallback_budget_planner(self) -> BudgetPlannerOutput:
        window = max(1, int(self._resolved_context_window or self._policy.context_window.model_context_window or 1))
        output_reserve = min(max(256, int(window * self._policy.token_budget.output_reserve_ratio)), window // 3)
        tool_reserve = min(max(128, int(window * self._policy.token_budget.tool_reserve_ratio)), window // 4)
        safety_margin = min(max(128, int(window * self._policy.token_budget.safety_margin_ratio)), window // 5)
        input_budget = max(0, window - output_reserve - tool_reserve - safety_margin)
        return BudgetPlannerOutput(
            budget_plan=BudgetPlan(
                model_context_window=window,
                output_reserve=output_reserve,
                tool_reserve=tool_reserve,
                safety_margin=safety_margin,
                input_budget=input_budget,
                retrieval_budget=max(0, int(input_budget * self._policy.token_budget.retrieval_ratio)),
                soft_limit=max(0, int(input_budget * 0.55)),
                hard_limit=max(0, int(input_budget * 0.72)),
                emergency_limit=max(0, int(input_budget * 0.85)),
                current_input_tokens=0,
                expected_next_input_tokens=0,
                p95_tool_result_tokens=self._policy.token_budget.p95_tool_result_tokens,
                planned_retrieval_tokens=self._policy.token_budget.planned_retrieval_tokens,
                validation_error="pipeline fallback budget",
            )
        )

    def _fallback_window_collector(
        self,
        inp: PipelineInput,
        canon_out: CanonicalizerOutput,
    ) -> WindowCollectorOutput:
        limit = max(
            1, min(self._policy.context_window.max_active_window_messages, int(inp.recent_window_messages or 1))
        )
        return WindowCollectorOutput(active_window=tuple(canon_out.transcript[-limit:]))

    def _fallback_episode_sealer(self, inp: PipelineInput) -> EpisodeSealerOutput:
        return EpisodeSealerOutput(episode_store=tuple(inp.existing_snapshot_episodes))

    def _fallback_artifact_selector(self, episode_out: EpisodeSealerOutput) -> ArtifactSelectorOutput:
        limit = max(0, int(self._policy.collection_limits.max_episode_cards))
        return ArtifactSelectorOutput(
            artifact_stubs=(),
            episode_cards=tuple(episode_out.episode_store[:limit]),
            head_anchor="",
            tail_anchor="",
            run_card=RunCard(),
            context_slice_plan=ContextSlicePlan(),
        )

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
