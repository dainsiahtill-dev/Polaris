"""
Standardized Mock Strategies for LLM Benchmark Testing

Provides deterministic mock providers and responses for reproducible testing.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@dataclass
class MockLLMResponse:
    """
    Standardized mock LLM response.

    Usage:
        mock = MockLLMResponse(
            text="Hello, world!",
            tokens_used=50,
            latency_ms=100.0,
        )
        provider_response = mock.to_provider_response()
    """

    text: str = "Mocked response"
    tokens_used: int = 100
    latency_ms: float = 50.0
    model: str = "mock-gpt-4"
    seed: int = 42
    finish_reason: str = "stop"

    def to_provider_response(self) -> dict[str, Any]:
        """
        Convert to OpenAI-compatible provider response format.

        Returns:
            Provider response dictionary
        """
        prompt_tokens = self.tokens_used // 2
        completion_tokens = self.tokens_used - prompt_tokens

        return {
            "choices": [
                {
                    "message": {"content": self.text, "role": "assistant"},
                    "finish_reason": self.finish_reason,
                    "index": 0,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": self.tokens_used,
            },
            "model": self.model,
            "latency_ms": self.latency_ms,
            "created": 1234567890,
            "id": f"mock-{self.seed}",
        }

    def to_stream_chunk(self, chunk_text: str | None = None) -> dict[str, Any]:
        """
        Generate a streaming chunk response.

        Args:
            chunk_text: Text for this chunk. If None, yields characters.

        Returns:
            Streaming chunk dictionary
        """
        content = chunk_text or self.text
        return {
            "choices": [
                {
                    "delta": {"content": content, "role": "assistant"},
                    "finish_reason": None,
                    "index": 0,
                }
            ],
            "model": self.model,
        }


class DeterministicMockProvider:
    """
    Deterministic mock provider with controlled response sequences.

    Usage:
        responses = [
            MockLLMResponse(text="First response", seed=1),
            MockLLMResponse(text="Second response", seed=2),
        ]
        provider = DeterministicMockProvider(responses)

        # Get next response deterministically
        response = provider.get_next_response()
    """

    def __init__(
        self,
        responses: list[MockLLMResponse],
        seed: int = 42,
    ) -> None:
        """
        Initialize mock provider.

        Args:
            responses: List of mock responses in order
            seed: Seed for deterministic call ordering
        """
        self.responses = responses
        self._seed = seed
        self._rng = random.Random(seed)
        self._call_index = 0

    def reset(self) -> None:
        """Reset call counter to zero."""
        self._call_index = 0
        self._rng = random.Random(self._seed)

    def get_next_response(self) -> dict[str, Any]:
        """
        Get next response in sequence (deterministic).

        Returns:
            Provider response dictionary
        """
        index = self._call_index % len(self.responses)
        self._call_index += 1
        return self.responses[index].to_provider_response()

    def get_response_at(self, index: int) -> dict[str, Any]:
        """
        Get response at specific index (for deterministic lookup).

        Args:
            index: Response index

        Returns:
            Provider response dictionary
        """
        return self.responses[index % len(self.responses)].to_provider_response()

    def mock_chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """
        Mock chat completion interface.

        Args:
            messages: Chat messages

        Returns:
            Provider response
        """
        return self.get_next_response()

    async def mock_stream(
        self,
        messages: list[dict[str, str]],
        delay_per_token: float = 0.001,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Mock streaming chat completion.

        Args:
            messages: Chat messages
            delay_per_token: Delay between tokens

        Yields:
            Stream chunks
        """
        response = self.get_next_response()
        content = response["choices"][0]["message"]["content"]

        for char in content:
            yield {
                "choices": [
                    {
                        "delta": {"content": char, "role": "assistant"},
                        "finish_reason": None,
                        "index": 0,
                    }
                ],
                "model": response["model"],
            }
            await asyncio.sleep(delay_per_token)

        # Send final chunk
        yield {
            "choices": [
                {
                    "delta": {},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            "model": response["model"],
        }


@dataclass
class MockBenchmarkCase:
    """Complete mock benchmark case with inputs and expected outputs."""

    case_id: str
    input_prompt: str
    expected_response: str
    expected_tokens: int = 100
    mock_responses: list[MockLLMResponse] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_provider_response(self) -> dict[str, Any]:
        """Generate provider response for this case."""
        if self.mock_responses:
            return self.mock_responses[0].to_provider_response()
        return MockLLMResponse(
            text=self.expected_response,
            tokens_used=self.expected_tokens,
        ).to_provider_response()


class MockProviderBuilder:
    """
    Builder for constructing mock providers with complex scenarios.

    Usage:
        provider = (
            MockProviderBuilder()
            .add_response("Hello!", tokens=10)
            .add_response("How can I help?", tokens=15)
            .with_error_rate(0.0)  # Never fail
            .build()
        )
    """

    def __init__(self, seed: int = 42) -> None:
        self._responses: list[MockLLMResponse] = []
        self._seed = seed
        self._error_rate: float = 0.0

    def add_response(
        self,
        text: str,
        tokens: int = 100,
        latency_ms: float = 50.0,
        model: str = "mock-gpt-4",
    ) -> MockProviderBuilder:
        """Add a mock response to the sequence."""
        self._responses.append(
            MockLLMResponse(
                text=text,
                tokens_used=tokens,
                latency_ms=latency_ms,
                model=model,
                seed=len(self._responses),
            )
        )
        return self

    def add_error_response(
        self,
        error_message: str = "Mock error",
        error_code: int = 500,
    ) -> MockProviderBuilder:
        """Add an error response."""
        self._responses.append(
            MockLLMResponse(
                text=f"Error: {error_message}",
                tokens_used=0,
            )
        )
        return self

    def with_error_rate(self, rate: float) -> MockProviderBuilder:
        """
        Set error injection rate (0.0 to 1.0).

        Args:
            rate: Probability of returning error response
        """
        self._error_rate = max(0.0, min(1.0, rate))
        return self

    def repeat(self, times: int) -> MockProviderBuilder:
        """
        Repeat current response sequence.

        Args:
            times: Number of times to repeat
        """
        original = list(self._responses)
        for _ in range(times - 1):
            for resp in original:
                self._responses.append(
                    MockLLMResponse(
                        text=resp.text,
                        tokens_used=resp.tokens_used,
                        latency_ms=resp.latency_ms,
                        model=resp.model,
                        seed=resp.seed,  # Preserve original seed for determinism
                    )
                )
        return self

    def build(self) -> DeterministicMockProvider:
        """Build the mock provider."""
        return DeterministicMockProvider(
            responses=self._responses,
            seed=self._seed,
        )
