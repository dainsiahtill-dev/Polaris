"""ADR-0090 I4.4: caller-injected context-window fallback beats the 128k default.

An unresolvable provider/model binding used to fall back to the 128k policy
default, poisoning stage-1 selection on small local models (selection assumed a
huge window, then enforcement blind-truncated). The gateway now injects the
role policy budget as the fallback; resolution failures must degrade, never
raise (the spec table raises ValueError for unknown bindings).
"""

from __future__ import annotations

from polaris.kernelone.context.context_os.runtime.engine import StateFirstContextOS


class TestContextWindowFallbackInjection:
    def test_unresolvable_binding_uses_injected_fallback(self) -> None:
        context_os = StateFirstContextOS(
            provider_id="no_such_provider",
            model="no_such_model",
            workspace=".",
            fallback_context_window=8000,
        )

        assert context_os.resolved_context_window == 8000

    def test_unresolvable_binding_without_fallback_uses_policy_default(self) -> None:
        context_os = StateFirstContextOS(
            provider_id="no_such_provider",
            model="no_such_model",
            workspace=".",
        )

        assert context_os.resolved_context_window == context_os.policy.context_window.model_context_window

    def test_resolution_never_raises_for_unknown_binding(self) -> None:
        context_os = StateFirstContextOS(
            provider_id="garbage-9999",
            model="garbage-model-int4",
            workspace=".",
            fallback_context_window=4096,
        )

        assert context_os.resolved_context_window > 0

    def test_non_positive_fallback_ignored(self) -> None:
        context_os = StateFirstContextOS(
            provider_id="no_such_provider",
            model="no_such_model",
            workspace=".",
            fallback_context_window=0,
        )

        assert context_os.resolved_context_window == context_os.policy.context_window.model_context_window


class TestResolveThenFreezePolicyWindow:
    """DEFECT 1 SSoT (I3-r17): the resolved window is written BACK into the policy
    at construction, so policy.context_window.model_context_window agrees with
    resolved_context_window. Before this, the gateway built ContextOS without a
    policy=, leaving the policy frozen at the 128k default while only BudgetPlan
    carried the truth — so any reader of the policy window saw a stale 128k.
    """

    def test_resolved_window_rebinds_policy_window(self) -> None:
        context_os = StateFirstContextOS(
            provider_id="no_such_provider",
            model="no_such_model",
            workspace=".",
            fallback_context_window=16000,
        )

        # The policy window must equal the resolved window (16k), NOT the 128k default.
        assert context_os.resolved_context_window == 16000
        assert context_os.policy.context_window.model_context_window == 16000

    def test_unresolvable_binding_stays_consistent_at_default(self) -> None:
        context_os = StateFirstContextOS(workspace=".")

        assert context_os.resolved_context_window == context_os.policy.context_window.model_context_window == 128_000

    def test_rebind_preserves_other_policy_fields(self) -> None:
        # dataclasses.replace must rebind ONLY context_window, leaving the rest
        # of the (frozen) policy intact.
        baseline = StateFirstContextOS(workspace=".")
        rebound = StateFirstContextOS(
            provider_id="no_such_provider",
            model="no_such_model",
            workspace=".",
            fallback_context_window=16000,
        )

        assert rebound.policy.token_budget == baseline.policy.token_budget
        assert rebound.policy.attention_runtime == baseline.policy.attention_runtime
        assert rebound.policy.window_size == baseline.policy.window_size
        assert rebound.policy.context_window.model_context_window == 16000
