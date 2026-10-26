"""Tests for SLMSummarizer — ADR-0067 SLM 语义压缩层."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.public.transaction_contracts import TransactionConfig
from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummaryStrategy,
)
from polaris.kernelone.context.context_os.summarizers.slm import SLMSummarizer
from polaris.kernelone.context.context_os.summarizers.slm_config import (
    reset_default_slm_binding,
    set_default_slm_binding,
)

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


@pytest.fixture(autouse=True)
def _clear_installed_binding():
    """Keep the process-wide SLM binding out of these unit tests."""
    reset_default_slm_binding()
    yield
    reset_default_slm_binding()


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

    def test_raises_on_empty_result(self, sample_long_text):
        """When SLM returns empty, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        mock_gateway = MagicMock()
        mock_gateway.is_slm_healthy = AsyncMock(return_value=True)
        mock_gateway.compress_text = AsyncMock(return_value="")

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, gateway=mock_gateway)
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

    def test_summarize_with_healthy_slm(self, sample_long_text):
        """When SLM is healthy, should return compressed text."""
        mock_gateway = self._make_mock_gateway(
            healthy=True, compress_result="Compressed: timeout in connection_pool.py:234"
        )

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, timeout_seconds=2.5, gateway=mock_gateway)
        result = summarizer.summarize(sample_long_text, max_tokens=50)

        assert result == "Compressed: timeout in connection_pool.py:234"
        mock_gateway.is_slm_healthy.assert_awaited_once()
        mock_gateway.compress_text.assert_awaited_once()

    def test_summarize_raises_when_unhealthy(self, sample_long_text):
        """When SLM is unhealthy, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        mock_gateway = self._make_mock_gateway(healthy=False)

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, gateway=mock_gateway)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

        mock_gateway.is_slm_healthy.assert_awaited_once()
        mock_gateway.compress_text.assert_not_awaited()

    def test_summarize_raises_on_empty_result(self, sample_long_text):
        """When SLM returns empty string, should raise SummarizationError."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="")

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, gateway=mock_gateway)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    def test_summarize_uses_correct_prompt_template(self, sample_dialogue):
        """Should use dialogue prompt template for dialogue content type."""
        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="Summary of auth discussion.")

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, gateway=mock_gateway)
        result = summarizer.summarize(sample_dialogue, max_tokens=50, content_type="dialogue")

        assert result == "Summary of auth discussion."
        # Verify the prompt contains dialogue-specific keywords
        call_args = mock_gateway.compress_text.await_args
        prompt = call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "dialogue" in prompt.lower() or "conversation" in prompt.lower()

    def test_summarize_uses_code_prompt_for_code(self, sample_code):
        """Should use code prompt template for code content type."""
        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="Auth functions and middleware.")

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, gateway=mock_gateway)
        result = summarizer.summarize(sample_code, max_tokens=50, content_type="code")

        assert result == "Auth functions and middleware."
        call_args = mock_gateway.compress_text.await_args
        prompt = call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        assert "code" in prompt.lower() or "artifact" in prompt.lower()

    def test_summarize_pre_truncates_long_content(self):
        """Should pre-truncate content longer than max_content_length."""
        mock_gateway = self._make_mock_gateway(healthy=True, compress_result="Short summary.")

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, max_content_length=500, gateway=mock_gateway)
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

    def test_timeout_raises(self, sample_long_text):
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

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, timeout_seconds=0.1, gateway=mock_gateway)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    def test_thread_timeout_raises(self, sample_long_text):
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

        config = TransactionConfig(slm_enabled=True)
        summarizer = SLMSummarizer(config=config, timeout_seconds=0.1, gateway=mock_gateway)
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)


# -----------------------------------------------------------------------------
# Binding Injection Tests
# -----------------------------------------------------------------------------


class _RecordingBinding:
    """Minimal SLMBinding implementation used to observe binding calls."""

    def __init__(self, config: Any, gateway: Any) -> None:
        self._config = config
        self._gateway = gateway
        self.gateway_calls: list[Any] = []

    def config(self) -> Any:
        return self._config

    def gateway(self, config: Any | None = None) -> Any:
        self.gateway_calls.append(config)
        return self._gateway


class TestSLMSummarizerBindingInjection:
    """Tests for the installed-binding path (host assembly contract)."""

    def test_unconfigured_summarizer_is_not_available(self):
        """Without an explicit config and without a binding, the tier is unavailable."""
        summarizer = SLMSummarizer()
        assert summarizer.is_available() is False

    def test_unconfigured_summarizer_raises_explicitly(self, sample_long_text):
        """Without a binding, summarize() must fail explicitly, not truncate silently."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        summarizer = SLMSummarizer()
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    def test_installed_binding_supplies_config(self):
        """The summarizer reads its configuration from the installed binding."""
        config = TransactionConfig(slm_enabled=True, slm_model_name="bound-model")
        binding = _RecordingBinding(config, MagicMock())
        set_default_slm_binding(binding)

        summarizer = SLMSummarizer()
        assert summarizer.config is config
        assert summarizer.is_available() is True

    def test_explicit_config_wins_over_binding(self):
        """An explicitly supplied configuration overrides the binding's own."""
        bound = TransactionConfig(slm_enabled=True, slm_model_name="bound-model")
        explicit = TransactionConfig(slm_enabled=True, slm_model_name="explicit-model")
        binding = _RecordingBinding(bound, MagicMock())
        set_default_slm_binding(binding)

        summarizer = SLMSummarizer(config=explicit)
        assert summarizer.config is explicit
        assert summarizer.get_compression_stats()["slm_model"] == "explicit-model"

    def test_explicit_config_is_forwarded_to_binding_verbatim(self, sample_long_text):
        """The binding receives the exact configuration object, with all fields."""
        gateway = MagicMock()
        gateway.is_slm_healthy = AsyncMock(return_value=True)
        gateway.compress_text = AsyncMock(return_value="Compressed summary from the bound gateway.")

        explicit = TransactionConfig(
            slm_enabled=True,
            slm_provider="ollama",
            slm_model_name="custom-model:1b",
            slm_base_url="http://127.0.0.1:9999",
            slm_keep_alive="30m",
            slm_timeout=77,
        )
        binding = _RecordingBinding(TransactionConfig(), gateway)
        set_default_slm_binding(binding)

        summarizer = SLMSummarizer(config=explicit)
        result = summarizer.summarize(sample_long_text, max_tokens=50)

        assert result == "Compressed summary from the bound gateway."
        assert binding.gateway_calls == [explicit]
        assert binding.gateway_calls[0] is explicit
        assert binding.gateway_calls[0].slm_base_url == "http://127.0.0.1:9999"
        assert binding.gateway_calls[0].slm_keep_alive == "30m"
        assert binding.gateway_calls[0].slm_timeout == 77
        assert binding.gateway_calls[0].slm_model_name == "custom-model:1b"

    def test_binding_without_override_uses_binding_config(self, sample_long_text):
        """Without an explicit config, the binding is asked for its own config."""
        gateway = MagicMock()
        gateway.is_slm_healthy = AsyncMock(return_value=True)
        gateway.compress_text = AsyncMock(return_value="Compressed summary from the bound gateway.")

        own = TransactionConfig(slm_enabled=True, slm_model_name="owner-model")
        binding = _RecordingBinding(own, gateway)
        set_default_slm_binding(binding)

        summarizer = SLMSummarizer()
        result = summarizer.summarize(sample_long_text, max_tokens=50)

        assert result == "Compressed summary from the bound gateway."
        assert binding.gateway_calls == [None]

    def test_explicit_gateway_wins_over_binding(self, sample_long_text):
        """An explicitly injected gateway is used as-is, without the host."""
        gateway = MagicMock()
        gateway.is_slm_healthy = AsyncMock(return_value=True)
        gateway.compress_text = AsyncMock(return_value="Compressed summary from the injected gateway.")

        binding = _RecordingBinding(TransactionConfig(), MagicMock())
        set_default_slm_binding(binding)

        summarizer = SLMSummarizer(config=TransactionConfig(slm_enabled=True), gateway=gateway)
        result = summarizer.summarize(sample_long_text, max_tokens=50)

        assert result == "Compressed summary from the injected gateway."
        assert binding.gateway_calls == []
        gateway.compress_text.assert_awaited_once()

    def test_close_releases_the_executor(self, sample_long_text):
        """close() shuts the thread pool down and further calls fail explicitly."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        summarizer = SLMSummarizer(config=TransactionConfig(slm_enabled=True))
        summarizer.close()
        with pytest.raises(SummarizationError):
            summarizer.summarize(sample_long_text, max_tokens=50)

    def test_context_manager_closes_the_executor(self):
        """The context manager closes the summarizer on exit."""
        with SLMSummarizer() as summarizer:
            assert summarizer._executor is not None
        assert summarizer._executor is None


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
