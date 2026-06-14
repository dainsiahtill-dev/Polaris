"""Live-path wiring for the small-window active-window budget (I3-r17 DEFECT 1).

The F5 patch fixed only ``scheduler._collect_active_window`` — a path reached
ONLY via ``get_state()`` introspection. The LIVE projection runs through the
pipeline ``WindowCollector`` / ``AttentionAwareWindowCollector``, which kept the
old large-window formula with NO small-window escalation. These tests construct
the LIVE collectors and assert they route the RESOLVED window
(``budget_plan.model_context_window``) through the canonical
``budget_math.active_window_token_budget`` — i.e. the small-window protection
actually runs in production, not just on the introspection path.

They FAIL on the pre-fix code (which never called the helper at all), closing the
false-green that hand-injected-window unit tests left open.
"""

from __future__ import annotations

from polaris.kernelone.context.context_os.decision_log import ContextDecisionLog
from polaris.kernelone.context.context_os.models_v2 import (
    BudgetPlanV2,
    StateEntryV2,
    TaskStateViewV2,
    TranscriptEventV2,
    UserProfileStateV2,
    WorkingStateV2,
)
from polaris.kernelone.context.context_os.pipeline import (
    attention_aware_stages as attn_module,
    stages as stages_module,
)
from polaris.kernelone.context.context_os.pipeline.attention_aware_stages import (
    AttentionAwareWindowCollector,
)
from polaris.kernelone.context.context_os.pipeline.contracts import (
    BudgetPlannerOutput,
    CanonicalizerOutput,
    PipelineInput,
    StatePatcherOutput,
)
from polaris.kernelone.context.context_os.pipeline.stages import WindowCollector
from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy


def _budget_plan_16k() -> BudgetPlanV2:
    # model_context_window is the ModelCatalog-resolved 16k qwen window.
    return BudgetPlanV2(
        model_context_window=16_000,
        input_budget=9472,
        soft_limit=5209,
        hard_limit=6819,
        emergency_limit=8056,
    )


def _transcript() -> tuple[TranscriptEventV2, ...]:
    return (
        TranscriptEventV2(
            event_id="evt_root",
            sequence=0,
            role="user",
            content="root task contract",
            kind="user_turn",
            route="PATCH",
            source_turns=("t0",),
        ),
    )


def _working_state() -> WorkingStateV2:
    return WorkingStateV2(
        task_state=TaskStateViewV2(
            current_goal=StateEntryV2(
                entry_id="goal_root",
                path="task_state.current_goal",
                value="root task contract",
                source_turns=("t0",),
                confidence=0.9,
            ),
            accepted_plan=(),
            open_loops=(),
            blocked_on=(),
            deliverables=(),
        ),
        user_profile=UserProfileStateV2(),
        active_entities=(),
        active_artifacts=(),
        decision_log=(),
    )


def _capture_window(monkeypatch, module) -> dict[str, int]:
    captured: dict[str, int] = {}

    def _fake(*, model_context_window, input_budget, soft_limit, hard_limit, base_ratio) -> int:
        captured["model_context_window"] = model_context_window
        return 4096

    monkeypatch.setattr(module, "active_window_token_budget", _fake)
    return captured


def _run(collector) -> None:
    collector.process(
        BudgetPlannerOutput(budget_plan=_budget_plan_16k()),
        StatePatcherOutput(working_state=_working_state()),
        CanonicalizerOutput(transcript=_transcript(), artifacts=()),
        PipelineInput(messages=[]),
        ContextDecisionLog(),
    )


class TestLiveWindowCollectorWiring:
    def test_live_window_collector_routes_resolved_window(self, monkeypatch) -> None:
        captured = _capture_window(monkeypatch, stages_module)
        _run(WindowCollector(policy=StateFirstContextOSPolicy()))
        # The LIVE collector must feed the resolved 16k window to the canonical
        # helper — NOT the policy 128k default, and NOT the old inline formula.
        assert captured["model_context_window"] == 16_000

    def test_attention_window_collector_routes_resolved_window(self, monkeypatch) -> None:
        captured = _capture_window(monkeypatch, attn_module)
        _run(AttentionAwareWindowCollector(policy=StateFirstContextOSPolicy(), enable_attention_scoring=False))
        assert captured["model_context_window"] == 16_000
