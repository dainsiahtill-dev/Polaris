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
