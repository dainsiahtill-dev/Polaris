"""LLMEvent — Pydantic schema for LLM interaction audit.

Captures complete LLM call context:
- Model, provider, token usage, latency
- Strategy (primary/fallback) and provider switching
- Prompt/completion content (sanitized before storage)
- Error details with stack trace categorization
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Self

from polaris.kernelone.audit.omniscient.schemas.base import (
    AuditEvent,
    AuditPriority,
    EventDomain,
)
from pydantic import ConfigDict, Field, field_validator


class LLMStrategy(str, Enum):
    """LLM invocation strategy for audit tracking."""

    PRIMARY = "primary"  # Primary model selected
    FALLBACK = "fallback"  # Fallback after primary failure
    CACHE_HIT = "cache_hit"  # Cached response used
    RERANK = "rerank"  # Response reranked


class LLMFinishReason(str, Enum):
    """LLM completion finish reason."""

    STOP = "stop"  # Natural stop
    LENGTH = "length"  # Max tokens reached
    CONTENT_FILTER = "content_filter"  # Content filtered
    ERROR = "error"  # Error during generation


class LLMEvent(AuditEvent, frozen=True):  # type: ignore[call-arg]  # frozen=True inherited from AuditEvent model_config
    """LLM interaction audit event.

    Captures the full lifecycle of an LLM call for:
    - Cost analysis (token usage per model/provider)
    - Performance monitoring (latency, throughput)
    - Strategy effectiveness (fallback rates, cache hit rates)
    - Error pattern detection (model failures, provider outages)

    Attributes:
        model: Model name (e.g., "claude-3-sonnet-20240229").
        provider: Provider ID (e.g., "anthropic", "ollama").
        prompt_tokens: Input token count.
        completion_tokens: Output token count.
        total_tokens: Total tokens (computed).
        latency_ms: Wall-clock time in milliseconds.
        strategy: Invocation strategy (primary/fallback/cache).
        finish_reason: Why the LLM stopped generating.
        error: Error message if call failed.
        error_type: Error category for pattern detection.
        prompt_preview: First 500 chars of prompt (sanitized).
        completion_preview: First 500 chars of completion (sanitized).
        safety_flags: Content safety filter activations.
        thinking_enabled: Whether extended thinking was used.
        temperature: Temperature parameter used.
        max_tokens: Max tokens parameter requested.

    Example:
        event = LLMEvent(
            model="claude-3-sonnet-20240229",
            provider="anthropic",
            prompt_tokens=500,
            completion_tokens=200,
            latency_ms=1500.0,
            strategy=LLMStrategy.PRIMARY,
            finish_reason=LLMFinishReason.STOP,
            role="director",
            workspace="/path/to/workspace",
        )
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    # -------------------------------------------------------------------------
    # LLM-specific classification (override base)
    # -------------------------------------------------------------------------

    domain: EventDomain = Field(default=EventDomain.LLM)
    event_type: str = Field(default="llm_call")

    # -------------------------------------------------------------------------
    # Model/provider identification
    # -------------------------------------------------------------------------

    model: str = Field(
        default="",
        description="Model name (e.g., claude-3-sonnet-20240229)",
        max_length=128,
    )

    provider: str = Field(
        default="",
        description="Provider ID (e.g., anthropic, ollama)",
        max_length=64,
    )

    # -------------------------------------------------------------------------
    # Token usage
    # -------------------------------------------------------------------------

    prompt_tokens: int = Field(
        default=0,
        ge=0,
        description="Input token count",
    )

    completion_tokens: int = Field(
        default=0,
        ge=0,
        description="Output token count",
    )

    # -------------------------------------------------------------------------
    # Performance
    # -------------------------------------------------------------------------

    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock time in milliseconds",
    )

    first_token_latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Time to first token in milliseconds",
    )

    # -------------------------------------------------------------------------
    # Strategy and routing
    # -------------------------------------------------------------------------

    strategy: LLMStrategy = Field(
        default=LLMStrategy.PRIMARY,
        description="Invocation strategy",
    )

    fallback_model: str = Field(
        default="",
        description="Fallback model used if strategy is FALLBACK",
        max_length=128,
    )

    # -------------------------------------------------------------------------
    # Completion details
    # -------------------------------------------------------------------------

    finish_reason: LLMFinishReason | None = Field(
        default=None,
        description="Why the LLM stopped generating",
    )

    # -------------------------------------------------------------------------
    # Error tracking
    # -------------------------------------------------------------------------

    error: str = Field(
        default="",
        description="Error message if call failed",
        max_length=1024,
    )

    error_type: str = Field(
        default="",
        description="Error category for pattern detection",
        max_length=64,
    )

    # -------------------------------------------------------------------------
    # Content previews (sanitized before storage)
    # -------------------------------------------------------------------------

    prompt_preview: str = Field(
        default="",
        description="First 500 chars of prompt (sanitized)",
        max_length=4096,
    )

    completion_preview: str = Field(
        default="",
        description="First 500 chars of completion (sanitized)",
        max_length=4096,
    )

    # -------------------------------------------------------------------------
    # Safety and parameters
    # -------------------------------------------------------------------------

    safety_flags: list[str] = Field(
        default_factory=list,
        description="Content safety filter activations",
    )

    thinking_enabled: bool = Field(
        default=False,
        description="Whether extended thinking was used",
    )

    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="Temperature parameter",
    )

    max_tokens: int = Field(
        default=0,
        ge=0,
        description="Max tokens parameter requested",
    )

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("prompt_preview", "completion_preview", mode="after")
    @classmethod
    def _truncate_preview(cls, v: str) -> str:
        """Ensure previews are truncated to 500 chars."""
        return v[:500]

    # -------------------------------------------------------------------------
    # Computed fields
    # -------------------------------------------------------------------------

    @property
    def total_tokens(self) -> int:
        """Total token count (computed)."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def tokens_per_second(self) -> float:
        """Throughput in tokens per second."""
        if self.latency_ms <= 0:
            return 0.0
        return (self.total_tokens / self.latency_ms) * 1000

    @property
    def is_success(self) -> bool:
        """Whether the LLM call succeeded."""
        return self.error == "" and self.finish_reason != LLMFinishReason.ERROR

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to audit dict (extends base)."""
        base = super().to_audit_dict()
        base.update(
            {
                "domain": self.domain.value,
                "event_type": self.event_type,
                "model": self.model,
                "provider": self.provider,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "latency_ms": self.latency_ms,
                "first_token_latency_ms": self.first_token_latency_ms,
                "strategy": self.strategy.value,
                "fallback_model": self.fallback_model,
                "finish_reason": self.finish_reason.value if self.finish_reason else None,
                "error": self.error,
                "error_type": self.error_type,
                "prompt_preview": self.prompt_preview,
                "completion_preview": self.completion_preview,
                "safety_flags": list(self.safety_flags),
                "thinking_enabled": self.thinking_enabled,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "tokens_per_second": self.tokens_per_second,
                "is_success": self.is_success,
            }
        )
        return base

    @classmethod
    def from_audit_dict(cls, data: dict[str, Any]) -> Self:
        """Deserialize from audit dict."""
        # Parse strategy
        strategy = data.get("strategy", "primary")
        if isinstance(strategy, str):
            strategy = LLMStrategy(strategy.lower())

        # Parse finish_reason
        finish_reason = data.get("finish_reason")
        if finish_reason and isinstance(finish_reason, str):
            try:
                finish_reason = LLMFinishReason(finish_reason.lower())
            except ValueError:
                finish_reason = None

        return cls(
            event_id=data.get("event_id", ""),
            version=data.get("version", "3.0"),
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
            trace_id=data.get("trace_id", ""),
            run_id=data.get("run_id", ""),
            span_id=data.get("span_id", ""),
            parent_span_id=data.get("parent_span_id", ""),
            priority=AuditPriority[data.get("priority", "info").upper()],
            workspace=data.get("workspace", ""),
            role=data.get("role", ""),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            latency_ms=data.get("latency_ms", 0.0),
            first_token_latency_ms=data.get("first_token_latency_ms", 0.0),
            strategy=strategy,
            fallback_model=data.get("fallback_model", ""),
            finish_reason=finish_reason,
            error=data.get("error", ""),
            error_type=data.get("error_type", ""),
            prompt_preview=data.get("prompt_preview", ""),
            completion_preview=data.get("completion_preview", ""),
            safety_flags=list(data.get("safety_flags", [])),
            thinking_enabled=data.get("thinking_enabled", False),
            temperature=data.get("temperature", 0.0),
            max_tokens=data.get("max_tokens", 0),
            data=data.get("data", {}),
            correlation_context=data.get("correlation_context", {}),
        )

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        model: str,
        provider: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        strategy: LLMStrategy = LLMStrategy.PRIMARY,
        role: str = "",
        workspace: str = "",
        trace_id: str = "",
        run_id: str = "",
        **kwargs: Any,
    ) -> Self:
        """Factory to create an LLMEvent.

        Args:
            model: Model name.
            provider: Provider ID.
            prompt_tokens: Input token count.
            completion_tokens: Output token count.
            latency_ms: Wall-clock time in ms.
            strategy: Invocation strategy.
            role: Emitting role.
            workspace: Workspace path.
            trace_id: Correlation ID.
            run_id: Session ID.
            **kwargs: Additional fields.

        Returns:
            LLMEvent instance.
        """
        return cls(
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            strategy=strategy,
            role=role,
            workspace=workspace,
            trace_id=trace_id,
            run_id=run_id,
            **kwargs,
        )
