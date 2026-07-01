"""ContextOverflowError 异常测试

测试目标: P1-1 ContextOverflowError 正确抛出

验证:
1. 当 token 超出限制且无法进一步压缩时, ContextOverflowError 被正确抛出
2. 压缩策略能够将 token 降低到限制以下
3. 当压缩不足时, 紧急回退机制被触发
"""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.profile.internal.schema import (
    RoleContextPolicy,
    RoleProfile,
)
from polaris.kernelone.context.contracts import TurnEngineContextRequest


class TestContextOverflowGuard:
    """测试 ContextOverflowError 异常"""

    def _create_gateway_and_request(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 8000,
        compression_strategy: Literal["summarize", "truncate", "sliding_window"] = "sliding_window",
    ) -> tuple[Any, TurnEngineContextRequest]:
        """Helper to create gateway with mock profile and request."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway

        # Create a mock profile with the given context policy
        context_policy = RoleContextPolicy(
            max_context_tokens=max_tokens,
            max_history_turns=10,
            include_project_structure=False,
            include_code_snippets=False,
            include_task_history=False,
            compression_strategy=compression_strategy,
        )

        mock_profile = MagicMock(spec=RoleProfile)
        mock_profile.role_id = "test-role"
        mock_profile.display_name = "Test Role"
        mock_profile.context_policy = context_policy

        gateway = RoleContextGateway(profile=mock_profile, workspace=".")

        # Create request
        request = TurnEngineContextRequest(
            message="test message",
            history=(),
            task_id=None,
            strategy_receipt=None,
            context_os_snapshot=None,
        )

        return gateway, request

    @pytest.mark.asyncio
    async def test_overflow_error_raised_when_tokens_exceed_limit(self) -> None:
        """P1-1-1: ContextOverflowError 应在 token 超限且无法压缩时抛出"""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.errors import BudgetExceededError
        from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

        # Monkeypatch model catalog to provide context window
        _resolve_ctx = ModelCatalog._resolve_context_window
        _resolve_out = ModelCatalog._resolve_output_limit
        ModelCatalog._resolve_context_window = lambda self, *a, **kw: 128000
        ModelCatalog._resolve_output_limit = lambda self, *a, **kw: 4096
        try:
            # Create gateway with very low token limit
            context_policy = RoleContextPolicy(
                max_context_tokens=100,  # Very low limit
                max_history_turns=10,
                include_project_structure=False,
                include_code_snippets=False,
                include_task_history=False,
                compression_strategy="truncate",
            )

            mock_profile = MagicMock(spec=RoleProfile)
            mock_profile.role_id = "test-role"
            mock_profile.display_name = "Test Role"
            mock_profile.context_policy = context_policy

            gateway = RoleContextGateway(profile=mock_profile, workspace=".")

            # Create messages that will definitely overflow
            large_content = "x" * 10000  # 10000 characters
            messages = [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": large_content},
                {"role": "assistant", "content": large_content},
                {"role": "tool", "content": large_content},
            ]

            # Patch _emergency_fallback to return messages that still exceed limit
            with patch.object(
                gateway._compression_engine,
                "emergency_fallback",
                return_value=(messages, 150),  # Still exceeds 100 limit
            ):
                request = TurnEngineContextRequest(
                    message="test",
                    history=(),
                    task_id=None,
                    strategy_receipt=None,
                    context_os_snapshot=None,
                )

                with pytest.raises(BudgetExceededError):
                    await gateway.build_context(request)

        finally:
            ModelCatalog._resolve_context_window = _resolve_ctx
            ModelCatalog._resolve_output_limit = _resolve_out

    @pytest.mark.asyncio
    async def test_compression_reduces_tokens_below_limit(self) -> None:
        """P1-1-2: 压缩策略应能将 token 降低到限制以下"""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

        _resolve_ctx = ModelCatalog._resolve_context_window
        _resolve_out = ModelCatalog._resolve_output_limit
        ModelCatalog._resolve_context_window = lambda self, *a, **kw: 128000
        ModelCatalog._resolve_output_limit = lambda self, *a, **kw: 4096
        try:
            # Create gateway with moderate token limit
            context_policy = RoleContextPolicy(
                max_context_tokens=500,
                max_history_turns=10,
                include_project_structure=False,
                include_code_snippets=False,
                include_task_history=False,
                compression_strategy="sliding_window",
            )

            mock_profile = MagicMock(spec=RoleProfile)
            mock_profile.role_id = "test-role"
            mock_profile.display_name = "Test Role"
            mock_profile.context_policy = context_policy

            gateway = RoleContextGateway(profile=mock_profile, workspace=".")

            # Request with history that will be processed
            request = TurnEngineContextRequest(
                message="test",
                history=(),
                task_id=None,
                strategy_receipt=None,
                context_os_snapshot=None,
            )

            # Mock token estimation and compression at their owning collaborators.
            with (
                patch.object(gateway._token_estimator, "estimate", side_effect=[2000, 2000, 2000]),
                patch.object(
                    gateway._compression_engine,
                    "apply_compression",
                    return_value=([{"role": "user", "content": "compressed"}], 350),
                ),
            ):
                # Should NOT raise, compression should work
                result = await gateway.build_context(request)
                assert result.token_estimate <= 500

        finally:
            ModelCatalog._resolve_context_window = _resolve_ctx
            ModelCatalog._resolve_output_limit = _resolve_out

    @pytest.mark.asyncio
    async def test_emergency_fallback_when_compression_insufficient(self) -> None:
        """P1-1-3: 当压缩不足时, 紧急回退机制应被触发"""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

        _resolve_ctx = ModelCatalog._resolve_context_window
        _resolve_out = ModelCatalog._resolve_output_limit
        ModelCatalog._resolve_context_window = lambda self, *a, **kw: 128000
        ModelCatalog._resolve_output_limit = lambda self, *a, **kw: 4096
        try:
            # Create gateway with very low limit
            context_policy = RoleContextPolicy(
                max_context_tokens=200,
                max_history_turns=10,
                include_project_structure=False,
                include_code_snippets=False,
                include_task_history=False,
                compression_strategy="truncate",
            )

            mock_profile = MagicMock(spec=RoleProfile)
            mock_profile.role_id = "test-role"
            mock_profile.display_name = "Test Role"
            mock_profile.context_policy = context_policy

            gateway = RoleContextGateway(profile=mock_profile, workspace=".")

            # Messages that overflow
            messages = [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "x" * 500},
                {"role": "assistant", "content": "y" * 500},
            ]

            request = TurnEngineContextRequest(
                message="test",
                history=(),
                task_id=None,
                strategy_receipt=None,
                context_os_snapshot=None,
            )

            # Mock owner collaborators to simulate overflow and recovery.
            with (
                patch.object(gateway._token_estimator, "estimate", return_value=1000),
                patch.object(
                    gateway._compression_engine,
                    "apply_compression",
                    return_value=(messages, 800),
                ),
                patch.object(
                    gateway._compression_engine,
                    "emergency_truncate_with_limit",
                    return_value=(messages[:1], 100),
                ),
            ):
                # Should not raise because emergency fallback brings it under
                result = await gateway.build_context(request)

                # Verify result is returned (emergency fallback worked)
                assert result is not None
                assert len(result.messages) >= 1

        finally:
            ModelCatalog._resolve_context_window = _resolve_ctx
            ModelCatalog._resolve_output_limit = _resolve_out


class TestCompressionStrategies:
    """测试不同压缩策略的行为"""

    @staticmethod
    def _engine(max_context_tokens: int, compression_strategy: str) -> Any:
        from pathlib import Path

        from polaris.cells.roles.kernel.internal.context_gateway.compression_engine import CompressionEngine
        from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import TokenEstimator
        from polaris.kernelone.context.history_materialization import SessionContinuityStrategy
        from polaris.kernelone.llm.reasoning import ReasoningStripper

        return CompressionEngine(
            max_context_tokens=max_context_tokens,
            compression_strategy=compression_strategy,
            max_history_turns=10,
            token_estimator=TokenEstimator(),
            continuity_strategy=SessionContinuityStrategy(),
            profile=MagicMock(),
            workspace=Path("."),
            reasoning_stripper=ReasoningStripper(),
        )

    def test_summarize_strategy(self) -> None:
        """测试 summarize 压缩策略"""
        engine = self._engine(max_context_tokens=300, compression_strategy="summarize")

        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "line 1"},
            {"role": "assistant", "content": "line 2"},
            {"role": "tool", "content": "line 3"},
            {"role": "user", "content": "line 4"},
        ]

        compressed, tokens = engine.apply_compression(messages, 1000)

        # Should reduce tokens
        assert tokens < 1000
        assert len(compressed) >= 1

    def test_truncate_strategy(self) -> None:
        """测试 truncate 压缩策略"""
        engine = self._engine(max_context_tokens=300, compression_strategy="truncate")

        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 1000},
        ]

        _compressed, tokens = engine.apply_compression(messages, 2000)

        # Should reduce tokens
        assert tokens < 2000

    def test_sliding_window_strategy(self) -> None:
        """测试 sliding_window 压缩策略"""
        engine = self._engine(max_context_tokens=300, compression_strategy="sliding_window")

        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
        ]

        _compressed, tokens = engine.apply_compression(messages, 2000)

        # Should reduce tokens through sliding window
        assert tokens < 2000
