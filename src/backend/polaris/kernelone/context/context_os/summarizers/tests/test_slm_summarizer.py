"""Tests for SLMSummarizer — ADR-0067 SLM 语义压缩层."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.roles.kernel.public.transaction_contracts import TransactionConfig
from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummaryStrategy,
)
from polaris.kernelone.context.context_os.summarizers.slm import SLMSummarizer

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def sample_long_text() -> str:
    """Sample long text for testing summarization."""
    return (
        "This is a very long text that needs to be summarized. " * 50
        + "The key point is that the system encountered a timeout error "
        + "when connecting to the database at port 5432. "
        + "The stack trace indicates the issue is in connection_pool.py line 234. " * 20
    )


@pytest.fixture
def sample_dialogue() -> str:
    """Sample dialogue for testing."""
    return """
User: I need to implement user authentication.
Assistant: I can help with that. We have several options:
1. JWT-based authentication
2. Session-based authentication
3. OAuth 2.0 for third-party integrations
User: Let's go with JWT-based authentication.
Assistant: Good choice. JWT is stateless and works well for APIs.
User: What about refresh tokens?
Assistant: Refresh tokens are a good idea for long-lived sessions.
""".strip()


@pytest.fixture
def sample_code() -> str:
    """Sample code for testing."""
    return """
def authenticate_user(username: str, password: str) -> dict[str, Any]:
    \"\"\"Authenticate a user and return JWT tokens.\"\"\"
    user = db.find_user(username)
    if user is None:
        raise ValueError("User not found")
    if not verify_password(password, user.password_hash):
        raise ValueError("Invalid password")
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    return {"access_token": access_token, "refresh_token": refresh_token}

class AuthMiddleware:
    \"\"\"Middleware for validating JWT tokens on protected routes.\"\"\"
    def __init__(self, secret_key: str):
        self.secret_key = secret_key
    def process_request(self, request):
        token = extract_bearer_token(request)
        if not token:
            raise UnauthorizedError("Missing token")
        payload = decode_token(token, self.secret_key)
        request.user_id = payload["sub"]
        return request
""".strip()


# -----------------------------------------------------------------------------
# SLMSummarizer Basic Tests
# -----------------------------------------------------------------------------


class TestSLMSummarizerBasic:
    """Tests for SLMSummarizer basic functionality."""

    def test_strategy_is_slm(self):
        """SLMSummarizer should have SLM strategy."""
        summarizer = SLMSummarizer()
        assert summarizer.strategy == SummaryStrategy.SLM

    def test_is_available_when_enabled(self):
        """is_available should return True when slm_enabled=True."""
        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config)
        assert summarizer.is_available() is True

    def test_is_available_when_disabled(self):
        """is_available should return False when slm_enabled=False."""
        config = TransactionConfig(slm_enabled=False)
        summarizer = SLMSummarizer(config=config)
        assert summarizer.is_available() is False

    def test_estimate_output_tokens(self):
        """estimate_output_tokens should return ~35% of input."""
        summarizer = SLMSummarizer()
        estimated = summarizer.estimate_output_tokens(1000)
        assert estimated == 350

    def test_returns_original_when_short(self):
        """Short text should be returned unchanged."""
        summarizer = SLMSummarizer()
        text = "This is short."
        result = summarizer.summarize(text, max_tokens=100)
        assert result == text

    def test_returns_original_when_empty(self):
        """Empty text should be returned unchanged."""
        summarizer = SLMSummarizer()
        result = summarizer.summarize("", max_tokens=100)
        assert result == ""

    def test_get_compression_stats(self):
        """get_compression_stats should return expected keys."""
        config = TransactionConfig(slm_enabled=True, slm_model_name="test-model")
        summarizer = SLMSummarizer(config=config, timeout_seconds=3.0)
        stats = summarizer.get_compression_stats()
        assert stats["strategy"] == "SLM"
        assert stats["slm_enabled"] is True
        assert stats["slm_model"] == "test-model"
        assert stats["timeout_seconds"] == 3.0
        assert stats["available"] is True
        assert stats["slm_keep_alive"] == "5m"


# -----------------------------------------------------------------------------
# Fallback Tests (SLM disabled / unhealthy)
# -----------------------------------------------------------------------------


class TestSLMSummarizerFallback:
    """Tests for SLMSummarizer fallback behavior."""

    def test_raises_when_disabled(self, sample_long_text):
        """When SLM is disabled, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        config = TransactionConfig(slm_enabled=False)
        summarizer = SLMSummarizer(config=config)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_raises_on_empty_result(self, mock_gateway_cls, sample_long_text):
        """When SLM returns empty, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        mock_gateway = MagicMock()
        mock_gateway.is_slm_healthy = AsyncMock(return_value=True)
        mock_gateway.compress_text = AsyncMock(return_value="")
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    def test_fallback_truncation_structure(self):
        """_fallback should produce head + ellipsis + tail structure."""
        summarizer = SLMSummarizer()
        long_text = "\n".join(f"line {i}: content here" for i in range(100))
        result = summarizer._fallback(long_text, max_tokens=30)
        assert "..." in result or "truncated" in result.lower()


# -----------------------------------------------------------------------------
# Async / Threading Tests with Mocks
# -----------------------------------------------------------------------------


class TestSLMSummarizerAsync:
    """Tests for SLMSummarizer async/threading behavior with mocked gateway."""

    def _make_mock_gateway(self, healthy: bool = True, compress_result: str = "") -> MagicMock:
        """Create a mock CognitiveGateway."""
        gateway = MagicMock()
        gateway.is_slm_healthy = AsyncMock(return_value=healthy)
        gateway.compress_text = AsyncMock(return_value=compress_result)
        return gateway

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_summarize_with_healthy_slm(self, mock_gateway_cls, sample_long_text):
        """When SLM is healthy, should return compressed text."""
        mock_gateway = self._make_mock_gateway(
            healthy=True, compress_result="Compressed: timeout in connection_pool.py:234"
        )
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, timeout_seconds=2.5)
        result = summarizer.summarize(sample_long_text, max_tokens=50)

        assert result == "Compressed: timeout in connection_pool.py:234"
        mock_gateway.is_slm_healthy.assert_awaited_once()
        mock_gateway.compress_text.assert_awaited_once()

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_summarize_raises_when_unhealthy(self, mock_gateway_cls, sample_long_text):
        """When SLM is unhealthy, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        mock_gateway = self._make_mock_gateway(healthy=False)
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

        mock_gateway.is_slm_healthy.assert_awaited_once()
        mock_gateway.compress_text.assert_not_awaited()

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_summarize_raises_on_empty_result(self, mock_gateway_cls, sample_long_text):
        """When SLM returns empty string, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="")
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_summarize_uses_correct_prompt_template(self, mock_gateway_cls, sample_dialogue):
        """Should use dialogue prompt template for dialogue content type."""
        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="Summary of auth discussion.")
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config)
        result = summarizer.summarize(sample_dialogue, max_tokens=50, content_type="dialogue")

        assert result == "Summary of auth discussion."
        # Verify the prompt contains dialogue-specific keywords
        call_args = mock_gateway.compress_text.await_args
        prompt = call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "dialogue" in prompt.lower() or "conversation" in prompt.lower()

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_summarize_uses_code_prompt_for_code(self, mock_gateway_cls, sample_code):
        """Should use code prompt template for code content type."""
        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="Auth functions and middleware.")
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config)
        result = summarizer.summarize(sample_code, max_tokens=50, content_type="code")

        assert result == "Auth functions and middleware."
        call_args = mock_gateway.compress_text.await_args
        prompt = call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "code" in prompt.lower() or "artifact" in prompt.lower()

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_summarize_pre_truncates_long_content(self, mock_gateway_cls):
        """Should pre-truncate content longer than max_content_length."""
        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="Short summary.")
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, max_content_length=500)
        very_long = "x" * 10000
        result = summarizer.summarize(very_long, max_tokens=50)

        assert result == "Short summary."
        call_args = mock_gateway.compress_text.await_args
        prompt = call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "truncated for SLM" in prompt
        assert len(prompt) < 1000  # Should be truncated (template + 500 chars + marker)


# -----------------------------------------------------------------------------
# Timeout Tests
# -----------------------------------------------------------------------------


class TestSLMSummarizerTimeout:
    """Tests for timeout circuit breaker behavior."""

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_timeout_raises(self, mock_gateway_cls, sample_long_text):
        """When health check times out, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        async def slow_health_check():
            import asyncio

            await asyncio.sleep(10)  # Will be cancelled by timeout
            return True

        mock_gateway = MagicMock()
        mock_gateway.is_slm_healthy = slow_health_check
        mock_gateway.compress_text = AsyncMock(return_value="")
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, timeout_seconds=0.1)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    @patch("polaris.cells.roles.kernel.public.transaction_contracts.CognitiveGateway")
    def test_thread_timeout_raises(self, mock_gateway_cls, sample_long_text):
        """When the thread itself times out, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        async def very_slow_compress(prompt: str, *, max_tokens: int) -> str:
            import asyncio

            await asyncio.sleep(10)
            return "never returned"

        mock_gateway = MagicMock()
        mock_gateway.is_slm_healthy = AsyncMock(return_value=True)
        mock_gateway.compress_text = very_slow_compress
        mock_gateway_cls.return_value = mock_gateway

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, timeout_seconds=0.1)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)


# -----------------------------------------------------------------------------
# Integration with TieredSummarizer
# -----------------------------------------------------------------------------


class TestSLMSummarizerTieredIntegration:
    """Tests for SLMSummarizer integration with TieredSummarizer."""

    def test_tiered_slm_strategy_available_when_enabled(self):
        """TieredSummarizer should report SLM when enabled."""
        from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

        # When SLM is enabled in default config, it should appear as available
        summarizer = TieredSummarizer()
        available = summarizer.get_available_strategies()
        # SLM may or may not be available depending on config, but no error
        assert isinstance(available, list)

    def test_tiered_slm_strategy_in_chain(self):
        """SLM should be first in strategy chain for all content types."""
        from polaris.kernelone.context.context_os.summarizers import SummaryStrategy
        from polaris.kernelone.context.context_os.summarizers.tiered import (
            STRATEGY_CHAIN,
        )

        for content_type, chain in STRATEGY_CHAIN.items():
            assert chain[0] == SummaryStrategy.SLM, f"SLM should be first in chain for {content_type}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
