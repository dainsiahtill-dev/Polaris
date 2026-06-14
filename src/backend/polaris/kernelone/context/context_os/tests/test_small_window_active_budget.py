"""Small-context-window active-window budget (live I3-r16).

A local 16k qwen got an active-window token budget of ~4262 — below a
~4330-token task prompt — so the MANDATORY root event was truncated and the
model returned empty output 74% of the time. The active window must not be
starved on a small window; large cloud windows stay on the conservative ratio.
"""

from __future__ import annotations

from polaris.kernelone.context.context_os.runtime import scheduler as scheduler_module
from polaris.kernelone.context.context_os.runtime.scheduler import (
    _SMALL_CONTEXT_WINDOW_TOKENS,
    _active_window_token_budget,
    _ContextOSSchedulerMixin,
)


def _plan_16k() -> dict[str, int]:
    window = 16384
    out = max(1024, int(window * 0.18))
    tool = max(512, int(window * 0.10))
    safety = max(2048, int(window * 0.05))
    inp = max(1024, window - out - tool - safety)
    return {
        "input_budget": inp,
        "soft_limit": max(512, int(inp * 0.55)),
        "hard_limit": max(768, int(inp * 0.72)),
    }


class TestSmallWindowBudget:
    def test_small_window_gets_larger_active_budget(self) -> None:
        p = _plan_16k()
        budget = _active_window_token_budget(
            model_context_window=16384,
            input_budget=p["input_budget"],
            soft_limit=p["soft_limit"],
            hard_limit=p["hard_limit"],
            base_ratio=0.45,
        )
        # the ~4330-token task prompt must fit with headroom for recent turns
        assert budget >= 4330
        assert budget == p["hard_limit"]  # capped by hard_limit, not the tight soft_limit

    def test_old_ratio_would_have_starved_the_root(self) -> None:
        p = _plan_16k()
        old = max(512, min(p["soft_limit"], int(p["input_budget"] * 0.45)))
        new = _active_window_token_budget(
            model_context_window=16384,
            input_budget=p["input_budget"],
            soft_limit=p["soft_limit"],
            hard_limit=p["hard_limit"],
            base_ratio=0.45,
        )
        assert new > old  # the fix strictly increases the small-window budget

    def test_large_window_unchanged(self) -> None:
        # 128k cloud window keeps the conservative ratio + soft-limit cap
        window = 128_000
        out = int(window * 0.18)
        inp = window - out - int(window * 0.10) - int(window * 0.05)
        soft = int(inp * 0.55)
        hard = int(inp * 0.72)
        base = _active_window_token_budget(
            model_context_window=window,
            input_budget=inp,
            soft_limit=soft,
            hard_limit=hard,
            base_ratio=0.45,
        )
        assert base == max(512, min(soft, int(inp * 0.45)))

    def test_threshold_boundary(self) -> None:
        # exactly at the threshold counts as small
        assert _SMALL_CONTEXT_WINDOW_TOKENS == 32_000
        p = _plan_16k()
        at = _active_window_token_budget(
            model_context_window=_SMALL_CONTEXT_WINDOW_TOKENS,
            input_budget=p["input_budget"],
            soft_limit=p["soft_limit"],
            hard_limit=p["hard_limit"],
            base_ratio=0.45,
        )
        assert at == p["hard_limit"]


class _WiringScheduler(_ContextOSSchedulerMixin):
    """Minimal carrier for the mixin's _collect_active_window.

    The mixin declares ``_sequences_from_turns`` as an injected attribute (the
    concrete StateFirstContextOS provides it), so the double supplies a no-op.
    """

    def __init__(self, policy: object) -> None:
        self.policy = policy

    def _sequences_from_turns(self, source_turns: object) -> set[int]:
        return set()


class TestSchedulerReadsResolvedWindow:
    """Regression lock for I3-r17: the small-window budget fix must read the
    RESOLVED window from the budget plan, not the raw policy field.

    The gateway builds StateFirstContextOS without a ``policy=`` argument, so
    ``self.policy.context_window.model_context_window`` stays at the 128k default
    even when the live Director model is a 16k qwen. If the scheduler read that
    stale field (the original bug), the ``<=32000`` branch would never fire and
    the budget would stay at the starved 4262. The helper-level tests above pass
    a window by hand and CANNOT catch that wiring gap — this test does, by going
    through the real ``_collect_active_window`` and capturing the window argument.
    """

    def test_active_window_uses_budget_plan_window_not_policy(self, monkeypatch) -> None:
        from polaris.kernelone.context.context_os.models_v2 import (
            BudgetPlanV2,
            StateEntryV2,
            TaskStateViewV2,
            TranscriptEventV2,
            UserProfileStateV2,
            WorkingStateV2,
        )
        from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy

        captured: dict[str, int] = {}

        def _capture(*, model_context_window, input_budget, soft_limit, hard_limit, base_ratio) -> int:
            captured["model_context_window"] = model_context_window
            return 4096

        monkeypatch.setattr(scheduler_module, "_active_window_token_budget", _capture)

        policy = StateFirstContextOSPolicy()
        # The stale default that made the branch dead in production.
        assert policy.context_window.model_context_window == 128_000

        scheduler = _WiringScheduler(policy)
        plan = BudgetPlanV2(
            model_context_window=16_000,  # ModelCatalog-resolved 16k qwen window
            input_budget=9472,
            soft_limit=5209,
            hard_limit=6819,
            emergency_limit=8056,
        )
        transcript = (
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
        working_state = WorkingStateV2(
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

        scheduler._collect_active_window(
            transcript=transcript,
            working_state=working_state,
            recent_window_messages=4,
            budget_plan=plan,
        )

        # The resolved 16k window must reach the budget helper — NOT the policy 128k.
        assert captured["model_context_window"] == 16_000
