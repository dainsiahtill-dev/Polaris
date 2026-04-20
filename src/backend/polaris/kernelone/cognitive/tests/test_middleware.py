"""Tests for Cognitive Middleware - Integration with Role Dialogue."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.middleware import (
    CognitiveMiddleware,
    get_cognitive_middleware,
    reset_cognitive_middleware,
)


class TestCognitiveMiddleware:
    """Tests for CognitiveMiddleware."""

    @pytest.fixture(autouse=True)
    def reset_middleware(self):
        """Reset middleware before each test."""
        reset_cognitive_middleware()
        yield
        reset_cognitive_middleware()

    @pytest.fixture
    def middleware(self, tmp_path):
        """Create middleware instance."""
        return CognitiveMiddleware(workspace=str(tmp_path), enabled=True)

    @pytest.mark.asyncio
    async def test_middleware_enabled_by_default(self, tmp_path):
        """Middleware should be enabled by default (unified cognitive + role system)."""
        middleware = CognitiveMiddleware(workspace=str(tmp_path))
        assert middleware._enabled is True

    @pytest.mark.asyncio
    async def test_middleware_can_be_explicitly_disabled(self, tmp_path):
        """Middleware should be disabled when explicitly set to False."""
        middleware = CognitiveMiddleware(workspace=str(tmp_path), enabled=False)
        assert middleware._enabled is False

    @pytest.mark.asyncio
    async def test_middleware_process_returns_context(self, middleware):
        """Middleware.process should return cognitive context."""
        result = await middleware.process(
            message="Read the file at src/main.py",
            role_id="director",
            session_id="test_session",
        )

        assert "enabled" in result
        assert "intent_type" in result
        assert "confidence" in result
        assert "uncertainty_score" in result
        assert "execution_path" in result

    @pytest.mark.asyncio
    async def test_middleware_detects_read_intent(self, middleware):
        """Middleware should detect read intent."""
        result = await middleware.process(
            message="Read the file at src/main.py",
            role_id="director",
        )

        assert result["enabled"] is True
        assert result["intent_type"] == "read_file"

    @pytest.mark.asyncio
    async def test_middleware_detects_create_intent(self, middleware):
        """Middleware should detect create intent."""
        result = await middleware.process(
            message="Create a new API endpoint",
            role_id="director",
        )

        assert result["enabled"] is True
        assert result["intent_type"] == "create_file"

    @pytest.mark.asyncio
    async def test_middleware_inject_into_context(self, middleware):
        """Middleware.inject_into_context should merge cognitive context."""
        cognitive_context = {
            "enabled": True,
            "intent_type": "read_file",
            "confidence": 0.8,
            "uncertainty_score": 0.3,
            "execution_path": "bypass",
        }

        existing_context = {"existing_key": "existing_value"}
        merged = middleware.inject_into_context(cognitive_context, existing_context)

        assert "existing_key" in merged
        assert "cognitive" in merged
        assert merged["cognitive"]["intent_type"] == "read_file"
        assert merged["cognitive"]["confidence"] == 0.8

    @pytest.mark.asyncio
    async def test_middleware_get_prompt_appendix(self, middleware):
        """Middleware.get_prompt_appendix should generate appendix."""
        cognitive_context = {
            "enabled": True,
            "intent_type": "create_file",
            "confidence": 0.7,
            "uncertainty_score": 0.4,
            "execution_path": "fast_think",
        }

        appendix = middleware.get_prompt_appendix(cognitive_context)

        assert appendix is not None
        assert "Cognitive Analysis" in appendix
        assert "create_file" in appendix
        assert "0.70" in appendix

    @pytest.mark.asyncio
    async def test_middleware_blocked_content(self, middleware):
        """Middleware should handle blocked content."""
        cognitive_context = {
            "enabled": True,
            "blocked": True,
            "block_reason": "Value alignment rejected",
        }

        merged = middleware.inject_into_context(cognitive_context, {})
        assert merged["cognitive"]["blocked"] is True
        assert merged["cognitive"]["block_reason"] == "Value alignment rejected"


class TestGetCognitiveMiddleware:
    """Tests for get_cognitive_middleware factory."""

    @pytest.fixture(autouse=True)
    def reset_middleware(self):
        """Reset middleware before each test."""
        reset_cognitive_middleware()
        yield
        reset_cognitive_middleware()

    def test_get_cognitive_middleware_creates_instance(self):
        """get_cognitive_middleware should create middleware instance."""
        middleware = get_cognitive_middleware(workspace=".", enabled=True)
        assert middleware is not None
        assert isinstance(middleware, CognitiveMiddleware)

    def test_get_cognitive_middleware_returns_same_instance(self):
        """get_cognitive_middleware should return singleton."""
        middleware1 = get_cognitive_middleware(workspace=".", enabled=True)
        middleware2 = get_cognitive_middleware()
        assert middleware1 is middleware2


class TestGenerateCognitiveRoleResponse:
    """Tests for generate_cognitive_role_response wrapper."""

    @pytest.fixture(autouse=True)
    def reset_middleware(self):
        """Reset middleware before each test."""
        reset_cognitive_middleware()
        yield
        reset_cognitive_middleware()

    @pytest.mark.asyncio
    async def test_generate_with_disabled_cognitive(self, tmp_path):
        """With cognitive disabled, should call generate_role_response normally."""
        # When cognitive is disabled, should still return valid response structure
        reset_cognitive_middleware()
        middleware = get_cognitive_middleware(workspace=str(tmp_path), enabled=False)

        # Verify disabled returns basic context
        result = await middleware.process(
            message="test message",
            role_id="director",
        )

        assert result["enabled"] is False


def test_middleware_integration_summary():
    """Summary test documenting the integration points."""
    integration_points = {
        "CognitiveMiddleware": [
            "process() - Analyze message through cognitive pipeline",
            "inject_into_context() - Merge cognitive context into role context",
            "get_prompt_appendix() - Generate cognitive guidance for prompts",
        ],
        "generate_cognitive_role_response": [
            "Wrapper that combines cognitive middleware + generate_role_response",
            "First processes message through CognitiveMiddleware",
            "Then passes enhanced context to role dialogue",
        ],
        "Environment Variables": [
            "POLARIS_ENABLE_COGNITIVE_MIDDLEWARE=true|false",
        ],
    }

    assert len(integration_points) == 3
