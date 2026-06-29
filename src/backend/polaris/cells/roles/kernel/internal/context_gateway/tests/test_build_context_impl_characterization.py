"""Characterization tests for ``RoleContextGateway._build_context_impl``.

These freeze the CURRENT observable behavior of the hot context-assembly path
before the G8 god-method decomposition (blueprint REMAINING_04_gateway-py.md
Sec.6 coverage gaps). They are intentionally behavior-locking, not aspirational:
- final ``BudgetExceededError`` diagnostic (raise + limit/requested fields);
- the guaranteed-fit / 3-stage compression last resort;
- context_override vs system_prompt insert ordering;
- the full ``ContextResult.metadata`` key contract (20 keys);
- the ``history_tool_fallback`` branch + truncation marker;
- the reserved-system-prompt-exceeds-budget early raise.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.context_gateway.gateway import RoleContextGateway
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest
from polaris.kernelone.errors import BudgetExceededError


def _profile(*, max_context_tokens: int = 128000, compression_strategy: str = "none") -> MagicMock:
    profile = MagicMock()
    profile.context_policy = MagicMock()
    profile.context_policy.max_history_turns = 8
    profile.context_policy.max_context_tokens = max_context_tokens
    profile.context_policy.include_project_structure = False
    profile.context_policy.include_task_history = False
    profile.context_policy.include_blueprint_overview = False
    profile.context_policy.include_verdict_history = False
    profile.context_policy.include_repo_identity = False
    profile.context_policy.include_scout_anchors = False
    profile.context_policy.include_blueprint_step = False
    profile.context_policy.compression_strategy = compression_strategy
    profile.context_policy.recon_mode = False
    profile.context_domain = None
    profile.provider_id = "test_provider"
    profile.model = "test_model"
    profile.role_id = "director"
    profile.display_name = "Director"
    return profile


# ── Full ContextResult.metadata key contract ────────────────────────────────

_EXPECTED_METADATA_KEYS = {
    "raw_history_tokens",
    "projected_tokens",
    "final_tokens",
    "active_window_size",
    "route_counts",
    "state_first_mode_active",
    "gateway_compression_applied",
    "context_sources",
    "strategy_override_applied",
    "strategy_override_sources",
    "recent_window_messages",
    "base_recent_window_messages",
    "context_budget_trigger_pct",
    "effective_context_budget_tokens",
    "budget_pressure_detected",
    "context_decision_hints",
    "projection_adaptive_weights",
    "capability_profile_ref",
    "projection_id",
    "context_result_id",
}


class TestMetadataContract:
    async def test_metadata_has_exactly_the_documented_keys(self) -> None:
        gateway = RoleContextGateway(_profile(), workspace=".")
        request = ContextRequest(message="hello", history=(("user", "hi"),))

        result = await gateway.build_context(request)

        assert set(result.metadata.keys()) == _EXPECTED_METADATA_KEYS

    async def test_metadata_consistency_with_top_level_fields(self) -> None:
        gateway = RoleContextGateway(_profile(), workspace=".")
        request = ContextRequest(message="hello", history=(("user", "hi"),))

        result = await gateway.build_context(request)

        assert result.metadata["final_tokens"] == result.token_estimate
        assert result.metadata["gateway_compression_applied"] == result.compression_applied
        assert list(result.metadata["context_sources"]) == list(result.context_sources)
        assert result.metadata["capability_profile_ref"] is None
        assert result.metadata["context_decision_hints"]["source"] == "roles.kernel.context_gateway"


# ── context_override vs system_prompt insert ordering ────────────────────────


class TestInsertOrdering:
    async def test_system_prompt_precedes_context_override(self) -> None:
        gateway = RoleContextGateway(_profile(), workspace=".")
        request = ContextRequest(
            message="hello",
            history=(("user", "hi"),),
            context_override={"project_brief": "build a snake game"},
        )

        result = await gateway.build_context(request, system_prompt="ROLE SYS")

        # system_prompt is prepended LAST → index 0; context_override message
        # was inserted at index 0 earlier → now index 1.
        assert result.messages[0]["role"] == "system"
        assert result.messages[0]["content"] == "ROLE SYS"
        override_msg = result.messages[1]
        assert override_msg.get("name") == "context_override"
        assert "project_brief" in override_msg["content"]
        assert "context_override" in result.context_sources
        assert "role_system_prompt" in result.context_sources

    async def test_context_override_without_system_prompt_is_first(self) -> None:
        gateway = RoleContextGateway(_profile(), workspace=".")
        request = ContextRequest(
            message="hello",
            history=(("user", "hi"),),
            context_override={"project_brief": "build a snake game"},
        )

        result = await gateway.build_context(request)

        assert result.messages[0].get("name") == "context_override"


class TestBlueprintOverview:
    async def test_context_override_ce_blueprint_prevents_degraded_blueprint_fallback(self) -> None:
        profile = _profile()
        profile.context_policy.include_blueprint_overview = True
        gateway = RoleContextGateway(profile, workspace=".")
        request = ContextRequest(
            message="Implement package.json",
            task_id="1",
            context_override={
                "ce_blueprint": {
                    "schema_version": "chief_engineer.blueprint.v1",
                    "blueprint_id": "ce_TASK-1-foundation",
                    "task_id": "TASK-1-foundation",
                    "summary": "Chief Engineer blueprint for TASK-1-foundation",
                    "objective": "Deliver the JavaScript package manifest.",
                    "target_files": ["package.json"],
                    "acceptance_criteria": ["package.json exists"],
                    "execution_checklist": ["Create only package.json"],
                    "handoff_ready": True,
                }
            },
        )

        result = await gateway.build_context(request, system_prompt="ROLE SYS")
        text = "\n".join(str(message.get("content") or "") for message in result.messages)

        assert "【蓝图/技术架构（降级）】" not in text
        assert "无 CE 蓝图可用" not in text
        assert "【蓝图/技术架构】" in text
        assert "Chief Engineer blueprint for TASK-1-foundation" in text

    async def test_context_override_skips_ce_blueprint_for_different_logical_task(self) -> None:
        profile = _profile()
        profile.context_policy.include_blueprint_overview = True
        gateway = RoleContextGateway(profile, workspace=".")
        request = ContextRequest(
            message="Implement core modules",
            task_id="2",
            context_override={
                "metadata": {
                    "external_task_id": "TASK-1-source-core",
                    "task_id": "TASK-1-source-core",
                },
                "ce_blueprint": {
                    "schema_version": "chief_engineer.blueprint.v1",
                    "blueprint_id": "ce_TASK-2",
                    "task_id": "TASK-2",
                    "summary": "Chief Engineer blueprint for TASK-2",
                    "objective": "Write validation scripts and README.",
                    "target_files": ["package.json", "tests/test_product.py", "README.md"],
                    "acceptance_criteria": ["python validation passes"],
                },
                "task_contract": {
                    "ce_blueprint": {
                        "schema_version": "chief_engineer.blueprint.v1",
                        "blueprint_id": "ce_TASK-1-source-core",
                        "task_id": "TASK-1-source-core",
                        "summary": "Chief Engineer blueprint for TASK-1-source-core",
                        "objective": "Write only the core source modules.",
                        "target_files": ["src/engine/rules.js", "src/engine/runner.js"],
                        "acceptance_criteria": ["core source files exist"],
                    },
                },
            },
        )

        result = await gateway.build_context(request, system_prompt="ROLE SYS")
        text = "\n".join(str(message.get("content") or "") for message in result.messages)

        assert "Chief Engineer blueprint for TASK-2" not in text
        assert "python validation passes" not in text
        assert "Chief Engineer blueprint for TASK-1-source-core" in text
        assert "core source files exist" in text


# ── history_tool_fallback branch + truncation marker ─────────────────────────


class TestHistoryToolFallback:
    async def test_tool_message_fallback_and_truncation(self) -> None:
        gateway = RoleContextGateway(_profile(), workspace=".")
        big_tool_output = "X" * 5000
        request = ContextRequest(
            message="hello",
            history=(
                ("user", "do a thing"),
                ("tool", big_tool_output),
            ),
        )

        result = await gateway.build_context(request)

        assert "history_tool_fallback" in result.context_sources
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert tool_msgs, "expected a tool fallback message"
        joined = "".join(str(m.get("content", "")) for m in tool_msgs)
        assert "CONTEXT_TRUNCATED" in joined

    async def test_no_tool_fallback_when_no_tool_messages(self) -> None:
        gateway = RoleContextGateway(_profile(), workspace=".")
        request = ContextRequest(message="hello", history=(("user", "do a thing"),))

        result = await gateway.build_context(request)

        assert "history_tool_fallback" not in result.context_sources


# ── reserved system prompt exceeds budget → early raise ──────────────────────


class TestReservedSystemPromptGuard:
    async def test_oversized_system_prompt_raises_budget_exceeded(self) -> None:
        # Tiny policy budget so a moderate system prompt alone overflows it.
        gateway = RoleContextGateway(_profile(max_context_tokens=1024), workspace=".")
        gateway._enforcement_budget_tokens = 50
        request = ContextRequest(message="hello", history=(("user", "hi"),))

        with pytest.raises(BudgetExceededError) as excinfo:
            await gateway.build_context(request, system_prompt="word " * 2000)

        err = excinfo.value
        assert getattr(err, "limit", None) == 50
        assert getattr(err, "requested", 0) > 50
        assert getattr(err, "current", None) == 0


# ── final BudgetExceededError diagnostic (assembled context too large) ───────


class TestFinalBudgetGuard:
    async def test_assembled_context_over_budget_raises_with_diagnostic(self) -> None:
        gateway = RoleContextGateway(_profile(max_context_tokens=4000), workspace=".")

        # Force the assembled token estimate above the enforcement budget AFTER
        # the compression stages, so the FINAL guard (not the earlier stages)
        # fires. Neutering both compression paths means a deliberately oversized
        # message survives every trim and trips the final per-message-breakdown
        # BudgetExceededError. Compression returns the input unchanged.
        budget = gateway._enforcement_budget_tokens
        huge = "token " * (budget * 2)

        def _passthrough_limit(messages: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int]:
            return messages, gateway._token_estimator.estimate(messages)

        def _passthrough_compress(
            messages: list[dict[str, Any]], current_tokens: int
        ) -> tuple[list[dict[str, Any]], int]:
            return messages, current_tokens

        def _passthrough_emergency(messages: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
            return messages

        with (
            patch.object(
                gateway._compression_engine,
                "emergency_truncate_with_limit",
                side_effect=_passthrough_limit,
            ),
            patch.object(
                gateway._compression_engine,
                "apply_compression",
                side_effect=_passthrough_compress,
            ),
            patch.object(
                gateway._compression_engine,
                "emergency_truncate",
                side_effect=_passthrough_emergency,
            ),
        ):
            request_big = ContextRequest(message=huge, history=(("user", huge),))
            with pytest.raises(BudgetExceededError) as excinfo:
                await gateway.build_context(request_big)

        err = excinfo.value
        assert getattr(err, "limit", None) == budget
        assert getattr(err, "current", None) == budget


# ── guaranteed-fit last resort (system-heavy projection still over budget) ───


class TestGuaranteedFitLastResort:
    async def test_compression_applied_flag_when_over_budget(self) -> None:
        # truncate strategy + a tiny budget forces the compression/last-resort
        # path; the assembly must end at-or-below the enforcement budget.
        gateway = RoleContextGateway(
            _profile(max_context_tokens=2000, compression_strategy="truncate"),
            workspace=".",
        )
        long_turns = tuple(("user", "tell me about state " + ("blah " * 200)) for _ in range(6))
        request = ContextRequest(message="hello", history=long_turns)

        result = await gateway.build_context(request)

        # Whatever the exact compression path, the final assembly is within the
        # enforcement budget and never raises for this in-range case.
        assert result.token_estimate <= gateway._enforcement_budget_tokens
