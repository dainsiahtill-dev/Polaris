"""LLM Adapter - Connects cognitive engines to LLM infrastructure."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class LLMInvocationError(Exception):
    """Raised when LLM invocation fails."""

    def __init__(self, message: str, original_error: Exception | None = None) -> None:
        super().__init__(message)
        self.original_error = original_error


class LLMInvoker(Protocol):
    """Protocol for LLM invocation in cognitive engines."""

    async def invoke(self, prompt: str, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class LLMInvocation:
    """Structured LLM invocation request."""

    prompt: str
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None


@dataclass
class AIResponse:
    """Parsed LLM response for cognitive processing."""

    content: str
    reasoning: str | None = None
    confidence: float | None = None
    raw: dict | None = None


class AIExecutorAdapter:
    """
    Adapter wrapping AIExecutor to LLMInvoker protocol.

    This allows cognitive engines to use LLM capabilities through
    the standard Polaris LLM infrastructure.
    """

    def __init__(
        self,
        workspace: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> None:
        self._workspace = workspace or "."
        self._model = model
        self._temperature = temperature
        self._executor: Any = None

    async def invoke(self, prompt: str, **kwargs: Any) -> str:
        """
        Invoke LLM with the given prompt.

        Returns the text content of the LLM response.
        """
        # Lazy import to avoid circular dependency
        try:
            from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
            from polaris.kernelone.llm.engine.executor import AIExecutor

            if self._executor is None:
                self._executor = AIExecutor(workspace=self._workspace)

            options: dict[str, Any] = {}
            if kwargs.get("temperature") is not None:
                options["temperature"] = kwargs["temperature"]
            if kwargs.get("max_tokens") is not None:
                options["max_tokens"] = kwargs["max_tokens"]

            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role="cognitive",
                input=prompt,
                model=kwargs.get("model", self._model),
                options=options,
            )

            response = await self._executor.invoke(request)
            return response.content

        except (RuntimeError, ValueError) as e:
            logger.warning("LLM invocation failed: %s (%r)", type(e).__name__, e)
            raise LLMInvocationError(f"LLM invocation failed: {type(e).__name__}", original_error=e) from e


class RuleBasedFallback:
    """
    Fallback that uses rule-based analysis when LLM is unavailable.

    This ensures cognitive engines can still function without LLM.
    """

    async def invoke(self, prompt: str, **kwargs: Any) -> str:
        """Return a rule-based response."""
        return "[Rule-based analysis - LLM not available]"


def create_llm_adapter(
    workspace: str | None = None,
    use_llm: bool = True,
    model: str | None = None,
) -> LLMInvoker:
    """
    Factory to create appropriate LLM adapter.

    Args:
        workspace: Workspace path for LLM operations
        use_llm: Whether to use real LLM or fallback
        model: Specific model to use

    Returns:
        LLMInvoker implementation
    """
    if not use_llm:
        return RuleBasedFallback()

    return AIExecutorAdapter(workspace=workspace, model=model)
