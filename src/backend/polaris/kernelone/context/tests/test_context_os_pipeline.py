"""Unit tests for the StateFirstContextOS projection pipeline."""

from __future__ import annotations

from dataclasses import replace

import pytest
from polaris.kernelone.context.context_os.models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    BudgetPlanV2 as BudgetPlan,
    RunCardV2 as RunCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.context_os.pipeline import (
    ArtifactSelector,
    ArtifactSelectorOutput,
    BudgetPlanner,
    BudgetPlannerOutput,
    Canonicalizer,
    CanonicalizerOutput,
    EpisodeSealer,
    EpisodeSealerOutput,
    PipelineInput,
    PipelineRunner,
    StatePatcher,
    StatePatcherOutput,
    TranscriptMerger,
    TranscriptMergerOutput,
    WindowCollector,
    WindowCollectorOutput,
)
from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def policy() -> StateFirstContextOSPolicy:
    return StateFirstContextOSPolicy()


@pytest.fixture
def empty_pipeline_input() -> PipelineInput:
    return PipelineInput(messages=[])


@pytest.fixture
def sample_transcript_event() -> TranscriptEvent:
    return TranscriptEvent(
        event_id="evt_001",
        sequence=0,
        role="user",
        kind="user_turn",
        route="",
        content="Hello, world!",
        source_turns=("t0",),
        artifact_id=None,
        created_at="2026-04-11T00:00:00Z",
        metadata={},
    )


@pytest.fixture
def sample_working_state() -> WorkingState:
    return WorkingState(
        user_profile=UserProfileState(
            preferences=(),
            style=(),
            persistent_facts=(),
        ),
        task_state=TaskStateView(
            current_goal=StateEntry(
                entry_id="entry_001",
                path="task_state.current_goal",
                value="Test goal",
                source_turns=("t0",),
                updated_at="2026-04-11T00:00:00Z",
                confidence=0.96,
            ),
            accepted_plan=(),
            open_loops=(),
            blocked_on=(),
            deliverables=(),
        ),
        decision_log=(),
        active_entities=(),
        active_artifacts=(),
        temporal_facts=(),
        state_history=(),
    )


@pytest.fixture
def sample_budget_plan() -> BudgetPlan:
    return BudgetPlan(
        model_context_window=128000,
        output_reserve=23040,
        tool_reserve=5120,
        safety_margin=6400,
        input_budget=92440,
        retrieval_budget=1024,
        soft_limit=50842,
        hard_limit=66557,
        emergency_limit=78574,
        current_input_tokens=1000,
        expected_next_input_tokens=25000,
        p95_tool_result_tokens=500,
        planned_retrieval_tokens=1024,
        validation_error="",
    )


# ---------------------------------------------------------------------------
# Stage 1: TranscriptMerger Tests
# ---------------------------------------------------------------------------


class TestTranscriptMerger:
    def test_process_empty_input(self, empty_pipeline_input: PipelineInput) -> None:
        merger = TranscriptMerger()
        result = merger.process(empty_pipeline_input)
        assert isinstance(result, TranscriptMergerOutput)
        assert result.transcript == ()

    def test_process_with_messages(self, empty_pipeline_input: PipelineInput) -> None:
        inp = replace(
            empty_pipeline_input,
            messages=[
                {"role": "user", "content": "Hello", "sequence": "0"},
                {"role": "assistant", "content": "Hi there!", "sequence": "1"},
            ],
        )
        merger = TranscriptMerger()
        result = merger.process(inp)
        assert len(result.transcript) == 2
        roles = {evt.role for evt in result.transcript}
        assert roles == {"user", "assistant"}

    def test_process_merges_with_existing(
        self,
        empty_pipeline_input: PipelineInput,
        sample_transcript_event: TranscriptEvent,
    ) -> None:
        inp = replace(
            empty_pipeline_input,
            existing_snapshot_transcript=(sample_transcript_event,),
            messages=[{"role": "user", "content": "New message", "sequence": "1"}],
        )
        merger = TranscriptMerger()
        result = merger.process(inp)
        assert len(result.transcript) == 2

    def test_process_tool_calls_extracted(
        self,
        empty_pipeline_input: PipelineInput,
    ) -> None:
        inp = replace(
            empty_pipeline_input,
            messages=[
                {
                    "role": "assistant",
                    "content": "Let me check that.",
                    "sequence": "0",
                    "metadata": {
                        "tool_calls": [{"name": "read_file", "id": "call_001", "arguments": {"path": "/test.py"}}]
                    },
                }
            ],
        )
        merger = TranscriptMerger()
        result = merger.process(inp)
        # Should have the main message plus tool_call event
        assert len(result.transcript) >= 1
        tool_call_events = [e for e in result.transcript if e.kind == "tool_call"]
        assert len(tool_call_events) >= 1


# ---------------------------------------------------------------------------
# Stage 2: Canonicalizer Tests
# ---------------------------------------------------------------------------


class TestCanonicalizer:
    def test_process_empty_transcript(
        self,
        policy: StateFirstContextOSPolicy,
        empty_pipeline_input: PipelineInput,
    ) -> None:
        merger_out = TranscriptMergerOutput(transcript=())
        canon = Canonicalizer(policy=policy)
        result = canon.process(empty_pipeline_input, merger_out)
        assert isinstance(result, CanonicalizerOutput)
        assert result.transcript == ()
        assert result.artifacts == ()

    def test_process_adds_routing(
        self,
        policy: StateFirstContextOSPolicy,
        empty_pipeline_input: PipelineInput,
        sample_transcript_event: TranscriptEvent,
    ) -> None:
        merger_out = TranscriptMergerOutput(transcript=(sample_transcript_event,))
        canon = Canonicalizer(policy=policy)
        result = canon.process(empty_pipeline_input, merger_out)
        # All events should have a route assigned
        for evt in result.transcript:
            assert evt.route != "" or evt.role in ("tool",)


# ---------------------------------------------------------------------------
# Stage 3: StatePatcher Tests
# ---------------------------------------------------------------------------


class TestStatePatcher:
    def test_process_empty_transcript(
        self,
        policy: StateFirstContextOSPolicy,
    ) -> None:
        canon_out = CanonicalizerOutput(
            transcript=(),
            artifacts=(),
            resolved_followup=None,
        )
        patcher = StatePatcher(policy=policy)
        result = patcher.process(canon_out)
        assert isinstance(result, StatePatcherOutput)
        assert isinstance(result.working_state, WorkingState)


# ---------------------------------------------------------------------------
# Stage 4: BudgetPlanner Tests
# ---------------------------------------------------------------------------


class TestBudgetPlanner:
    def test_process_computes_budget(
        self,
        policy: StateFirstContextOSPolicy,
        sample_working_state: WorkingState,
    ) -> None:
        canon_out = CanonicalizerOutput(
            transcript=(),
            artifacts=(),
            resolved_followup=None,
        )
        patcher_out = StatePatcherOutput(working_state=sample_working_state)
        planner = BudgetPlanner(policy=policy, resolved_context_window=128000)
        result = planner.process(patcher_out, canon_out)
        assert isinstance(result, BudgetPlannerOutput)
        assert isinstance(result.budget_plan, BudgetPlan)
        assert result.budget_plan.model_context_window == 128000
        assert result.budget_plan.validation_error == ""


# ---------------------------------------------------------------------------
# Stage 5: WindowCollector Tests
# ---------------------------------------------------------------------------


class TestWindowCollector:
    def test_process_empty_transcript(
        self,
        policy: StateFirstContextOSPolicy,
        sample_working_state: WorkingState,
        sample_budget_plan: BudgetPlan,
        empty_pipeline_input: PipelineInput,
    ) -> None:
        canon_out = CanonicalizerOutput(
            transcript=(),
            artifacts=(),
            resolved_followup=None,
        )
        patcher_out = StatePatcherOutput(working_state=sample_working_state)
        budget_out = BudgetPlannerOutput(budget_plan=sample_budget_plan)
        collector = WindowCollector(policy=policy)
        result = collector.process(budget_out, patcher_out, canon_out, empty_pipeline_input)
        assert isinstance(result, WindowCollectorOutput)
        assert result.active_window == ()


# ---------------------------------------------------------------------------
# Stage 6: EpisodeSealer Tests
# ---------------------------------------------------------------------------


class TestEpisodeSealer:
    def test_process_empty_episodes(
        self,
        policy: StateFirstContextOSPolicy,
        sample_working_state: WorkingState,
        empty_pipeline_input: PipelineInput,
    ) -> None:
        canon_out = CanonicalizerOutput(
            transcript=(),
            artifacts=(),
            resolved_followup=None,
        )
        patcher_out = StatePatcherOutput(working_state=sample_working_state)
        window_out = WindowCollectorOutput(active_window=())
        sealer = EpisodeSealer(policy=policy)
        result = sealer.process(window_out, patcher_out, canon_out, empty_pipeline_input)
        assert isinstance(result, EpisodeSealerOutput)
        assert result.episode_store == ()


# ---------------------------------------------------------------------------
# Stage 7: ArtifactSelector Tests
# ---------------------------------------------------------------------------


class TestArtifactSelector:
    def test_process_empty_inputs(
        self,
        policy: StateFirstContextOSPolicy,
        sample_working_state: WorkingState,
        empty_pipeline_input: PipelineInput,
    ) -> None:
        patcher_out = StatePatcherOutput(working_state=sample_working_state)
        window_out = WindowCollectorOutput(active_window=())
        budget_out = BudgetPlannerOutput(
            budget_plan=BudgetPlan(
                model_context_window=128000,
                output_reserve=23040,
                tool_reserve=5120,
                safety_margin=6400,
                input_budget=92440,
                retrieval_budget=1024,
                soft_limit=50842,
                hard_limit=66557,
                emergency_limit=78574,
                current_input_tokens=0,
                expected_next_input_tokens=1000,
                p95_tool_result_tokens=500,
                planned_retrieval_tokens=1024,
                validation_error="",
            )
        )
        episode_out = EpisodeSealerOutput(episode_store=())
        canon_out = CanonicalizerOutput(
            transcript=(),
            artifacts=(),
            resolved_followup=None,
        )
        selector = ArtifactSelector(policy=policy)
        result = selector.process(
            episode_out,
            patcher_out,
            window_out,
            budget_out,
            canon_out,
            empty_pipeline_input,
        )
        assert isinstance(result, ArtifactSelectorOutput)
        assert result.artifact_stubs == ()
        assert result.episode_cards == ()
        assert isinstance(result.run_card, RunCard)

    def test_process_long_active_artifact_uses_private_metadata_field(
        self,
        policy: StateFirstContextOSPolicy,
        sample_working_state: WorkingState,
        empty_pipeline_input: PipelineInput,
    ) -> None:
        long_artifact = ArtifactRecord(
            artifact_id="artifact_long_001",
            artifact_type="tool_result",
            mime_type="text/plain",
            token_count=2000,
            char_count=10000,
            peek="x" * 128,
            content="x" * 10000,
            source_event_ids=("evt_001",),
            metadata={},
        )
        working_state = sample_working_state.model_copy(update={"active_artifacts": ("artifact_long_001",)})
        patcher_out = StatePatcherOutput(working_state=working_state)
        window_out = WindowCollectorOutput(active_window=())
        budget_out = BudgetPlannerOutput(
            budget_plan=BudgetPlan(
                model_context_window=128000,
                output_reserve=23040,
                tool_reserve=5120,
                safety_margin=6400,
                input_budget=92440,
                retrieval_budget=1024,
                soft_limit=50842,
                hard_limit=66557,
                emergency_limit=78574,
                current_input_tokens=0,
                expected_next_input_tokens=1000,
                p95_tool_result_tokens=500,
                planned_retrieval_tokens=1024,
                validation_error="",
            )
        )
        episode_out = EpisodeSealerOutput(episode_store=())
        canon_out = CanonicalizerOutput(
            transcript=(),
            artifacts=(long_artifact,),
            resolved_followup=None,
        )

        selector = ArtifactSelector(policy=policy)
        result = selector.process(
            episode_out,
            patcher_out,
            window_out,
            budget_out,
            canon_out,
            empty_pipeline_input,
        )

        assert len(result.artifact_stubs) == 1
        stub = result.artifact_stubs[0]
        assert "truncated" in dict(stub.metadata)
        assert dict(stub.metadata)["truncated"] is True


# ---------------------------------------------------------------------------
# PipelineRunner Integration Tests
# ---------------------------------------------------------------------------


class TestPipelineRunner:
    def test_runner_empty_input(self, policy: StateFirstContextOSPolicy) -> None:
        runner = PipelineRunner(policy=policy)
        inp = PipelineInput(messages=[])
        projection = runner.project(inp)
        assert projection is not None
        assert projection.snapshot is not None
        assert projection.head_anchor == ""

    def test_runner_with_messages(self, policy: StateFirstContextOSPolicy) -> None:
        runner = PipelineRunner(policy=policy)
        inp = PipelineInput(
            messages=[
                {"role": "user", "content": "Hello, world!", "sequence": "0"},
            ]
        )
        projection = runner.project(inp)
        assert projection is not None
        assert len(projection.snapshot.transcript_log) == 1
        assert projection.snapshot.transcript_log[0].content == "Hello, world!"

    def test_runner_pipeline_available(self, policy: StateFirstContextOSPolicy) -> None:
        """PipelineRunner is the default and only projection path."""
        from polaris.kernelone.context.context_os.runtime import StateFirstContextOS

        os = StateFirstContextOS(policy=policy)
        assert os._get_pipeline_runner() is not None
