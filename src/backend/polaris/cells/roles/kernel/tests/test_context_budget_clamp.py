"""ADR-0090 I4: enforcement budget = min(role policy, model window × 0.85).

The static role yaml budget (chief_engineer 12k) could exceed a small local
model's window, and the role system prompt was injected AFTER enforcement by a
second projection pass — both overflow the provider on 8k/16k local models.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest


def _profile(max_context_tokens: int = 12000) -> MagicMock:
    profile = MagicMock()
    profile.context_policy = MagicMock()
    profile.context_policy.max_history_turns = 8
    profile.context_policy.max_context_tokens = max_context_tokens
    profile.context_policy.include_project_structure = False
    profile.context_policy.include_task_history = False
    profile.context_policy.compression_strategy = "truncate"
    profile.context_domain = None
    profile.provider_id = "test_provider"
    profile.model = "test_model"
    profile.role_id = "director"
    profile.display_name = "Director"
    return profile


def _budget_with_window(gateway: RoleContextGateway, window: int | Exception) -> int:
    target = type(gateway._context_os)
    if isinstance(window, Exception):
        prop = PropertyMock(side_effect=window)
    else:
        prop = PropertyMock(return_value=window)
    with patch.object(target, "resolved_context_window", new_callable=lambda: prop):
        return gateway._compute_enforcement_budget()


class TestEnforcementBudgetClamp:
    def test_large_window_keeps_policy_budget(self) -> None:
        gateway = RoleContextGateway(_profile(12000), workspace=".")

        assert _budget_with_window(gateway, 128000) == 12000

    def test_small_window_clamps_below_policy(self) -> None:
        gateway = RoleContextGateway(_profile(12000), workspace=".")

        # chief_engineer-style 12k policy on an 8k model: 8192 * 0.85 = 6963
        assert _budget_with_window(gateway, 8192) == int(8192 * 0.85)

    def test_resolution_failure_falls_back_to_policy(self) -> None:
        gateway = RoleContextGateway(_profile(12000), workspace=".")

        assert _budget_with_window(gateway, ValueError("unknown binding")) == 12000

    def test_zero_window_falls_back_to_policy(self) -> None:
        gateway = RoleContextGateway(_profile(12000), workspace=".")

        assert _budget_with_window(gateway, 0) == 12000

    def test_floor_never_raises_budget_above_policy(self) -> None:
        gateway = RoleContextGateway(_profile(1000), workspace=".")

        # window == policy fallback: 850 clamp is floored back to the policy
        # value, never to the 1024 global floor (which would EXCEED policy).
        assert _budget_with_window(gateway, 1000) == 1000


class TestSystemPromptBudgetedPrepend:
    async def test_system_prompt_prepended_and_reserved(self) -> None:
        gateway = RoleContextGateway(_profile(12000), workspace=".")
        request = ContextRequest(message="hello", history=[("user", "hi")])

        plain = await gateway.build_context(request)
        prompted = await gateway.build_context(request, system_prompt="ROLE SYSTEM PROMPT " * 50)

        assert prompted.messages[0]["role"] == "system"
        assert "ROLE SYSTEM PROMPT" in prompted.messages[0]["content"]
        assert plain.messages[0] != prompted.messages[0]

        plain_budget = int(plain.metadata.get("effective_context_budget_tokens", 0))
        prompted_budget = int(prompted.metadata.get("effective_context_budget_tokens", 0))
        assert 0 < prompted_budget < plain_budget
