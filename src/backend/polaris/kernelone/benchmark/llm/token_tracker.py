"""Token Consumption Tracking Framework.

This module provides comprehensive token usage tracking and cost estimation
for LLM API calls across different models and providers.

Example
-------
    from polaris.kernelone.benchmark.llm.token_tracker import TokenTracker, TokenConsumptionRecord

    # Define pricing (model -> (prompt_cost_per_1k, completion_cost_per_1k))
    pricing = {
        "claude-3-opus": (0.015, 0.075),
        "claude-3-sonnet": (0.003, 0.015),
        "gpt-4": (0.03, 0.06),
    }

    tracker = TokenTracker(pricing)

    # Track usage
    record = tracker.track("claude-3-opus", {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
    })
    print(f"Cost: ${record.cost_estimate_usd:.4f}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeAlias

# ------------------------------------------------------------------
# Type Aliases
# ------------------------------------------------------------------

ModelName: TypeAlias = str
PromptTokens: TypeAlias = int
CompletionTokens: TypeAlias = int
TotalTokens: TypeAlias = int


# ------------------------------------------------------------------
# Pricing Configuration
# ------------------------------------------------------------------

# Default pricing for common models (USD per 1K tokens)
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic models
    "claude-3-opus": (0.015, 0.075),
    "claude-3-sonnet": (0.003, 0.015),
    "claude-3-haiku": (0.00025, 0.00125),
    "claude-3.5-sonnet": (0.003, 0.015),
    "claude-3.5-haiku": (0.0008, 0.004),
    # OpenAI models
    "gpt-4": (0.03, 0.06),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    # Google models
    "gemini-pro": (0.00125, 0.00375),
    "gemini-ultra": (0.007, 0.021),
    # Local/default
    "default": (0.001, 0.002),
}


# ------------------------------------------------------------------
# Data Models
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class TokenConsumptionRecord:
    """Token consumption record for a single LLM call.

    Attributes:
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        total_tokens: Total tokens consumed.
        cost_estimate_usd: Estimated cost in USD.
        model: The model used for this call.
        timestamp: ISO timestamp of the call.
        provider: The provider name (e.g., "anthropic", "openai").
    """

    prompt_tokens: PromptTokens
    completion_tokens: CompletionTokens
    total_tokens: TotalTokens
    cost_estimate_usd: float
    model: ModelName
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provider: str = "unknown"

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0:
            object.__setattr__(self, "prompt_tokens", 0)
        if self.completion_tokens < 0:
            object.__setattr__(self, "completion_tokens", 0)
        if self.total_tokens < 0:
            object.__setattr__(self, "total_tokens", 0)
        if self.cost_estimate_usd < 0.0:
            object.__setattr__(self, "cost_estimate_usd", 0.0)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_estimate_usd": round(self.cost_estimate_usd, 6),
            "model": self.model,
            "timestamp": self.timestamp,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenConsumptionRecord:
        """Create from dictionary."""
        return cls(
            prompt_tokens=max(0, int(data.get("prompt_tokens", 0))),
            completion_tokens=max(0, int(data.get("completion_tokens", 0))),
            total_tokens=max(0, int(data.get("total_tokens", 0))),
            cost_estimate_usd=max(0.0, float(data.get("cost_estimate_usd", 0.0))),
            model=str(data.get("model", "unknown")),
            timestamp=str(data.get("timestamp", "")),
            provider=str(data.get("provider", "unknown")),
        )


@dataclass(frozen=True, kw_only=True)
class AggregatedUsageStats:
    """Aggregated usage statistics over multiple calls.

    Attributes:
        total_prompt_tokens: Total prompt tokens across all calls.
        total_completion_tokens: Total completion tokens.
        total_tokens: Total tokens consumed.
        total_cost_usd: Total estimated cost.
        call_count: Number of API calls.
        model_breakdown: Per-model token breakdown.
        timestamp: When this aggregation was computed.
    """

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    call_count: int = 0
    model_breakdown: dict[str, dict[str, int | float]] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def average_tokens_per_call(self) -> float:
        """Average tokens per API call."""
        if self.call_count == 0:
            return 0.0
        return self.total_tokens / self.call_count

    @property
    def average_cost_per_call(self) -> float:
        """Average cost per API call in USD."""
        if self.call_count == 0:
            return 0.0
        return self.total_cost_usd / self.call_count

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "call_count": self.call_count,
            "average_tokens_per_call": round(self.average_tokens_per_call, 2),
            "average_cost_per_call": round(self.average_cost_per_call, 6),
            "model_breakdown": self.model_breakdown,
            "timestamp": self.timestamp,
        }


# ------------------------------------------------------------------
# Token Tracker
# ------------------------------------------------------------------


class TokenTracker:
    """Token consumption tracker with cost estimation.

    This class tracks token usage across multiple LLM calls and
    provides aggregated statistics and cost analysis.

    Attributes:
        pricing: Model pricing map (model -> (prompt_cost_per_1k, completion_cost_per_1k)).
        provider_hints: Optional hints for provider detection from model names.
    """

    def __init__(
        self,
        pricing: dict[str, tuple[float, float]] | None = None,
        provider_hints: dict[str, str] | None = None,
    ) -> None:
        self._pricing = dict(pricing) if pricing else dict(DEFAULT_PRICING)
        self._provider_hints = dict(provider_hints) if provider_hints else self._default_provider_hints()
        self._records: list[TokenConsumptionRecord] = []

    @staticmethod
    def _default_provider_hints() -> dict[str, str]:
        """Default provider detection hints."""
        return {
            "anthropic": "anthropic",
            "claude": "anthropic",
            "openai": "openai",
            "gpt": "openai",
            "google": "google",
            "gemini": "google",
            "azure": "azure",
            "vertex": "google",
        }

    def track(
        self,
        model: str,
        usage: dict[str, int],
        provider: str | None = None,
    ) -> TokenConsumptionRecord:
        """Track token usage for a single LLM call.

        Args:
            model: The model name.
            usage: Token usage dictionary with prompt_tokens, completion_tokens, total_tokens.
            provider: Optional provider name override.

        Returns:
            TokenConsumptionRecord with usage and cost data.
        """
        prompt_tokens = max(0, int(usage.get("prompt_tokens", 0)))
        completion_tokens = max(0, int(usage.get("completion_tokens", 0)))
        total_tokens = max(0, int(usage.get("total_tokens", prompt_tokens + completion_tokens)))

        # Calculate cost
        cost = self._calculate_cost(model, prompt_tokens, completion_tokens)

        # Detect provider
        detected_provider = provider or self._detect_provider(model)

        record = TokenConsumptionRecord(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_estimate_usd=cost,
            model=model,
            provider=detected_provider,
        )

        self._records.append(record)
        return record

    def track_from_response(
        self,
        model: str,
        response: Any,  # LLM API response object
        provider: str | None = None,
    ) -> TokenConsumptionRecord:
        """Track token usage from an LLM API response.

        Args:
            model: The model name.
            response: The API response object.
            provider: Optional provider name override.

        Returns:
            TokenConsumptionRecord with usage and cost data.
        """
        # Try to extract usage from response
        usage = self._extract_usage(response)
        return self.track(model, usage, provider)

    def _extract_usage(self, response: Any) -> dict[str, int]:
        """Extract usage dict from API response."""
        # Try common response formats
        if hasattr(response, "usage"):
            usage = response.usage
            if hasattr(usage, "_dict"):
                return usage._dict
            if hasattr(usage, "model_dump"):
                return usage.model_dump()
            if isinstance(usage, dict):
                return usage

        if isinstance(response, dict):
            if "usage" in response:
                return response["usage"]
            # Try direct fields
            return {
                "prompt_tokens": response.get("prompt_tokens", 0),
                "completion_tokens": response.get("completion_tokens", 0),
                "total_tokens": response.get("total_tokens", 0),
            }

        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Calculate cost for token usage."""
        # Get pricing for model (try exact match first, then partial match)
        prompt_cost_per_1k, completion_cost_per_1k = self._get_pricing(model)

        prompt_cost = (prompt_tokens / 1000) * prompt_cost_per_1k
        completion_cost = (completion_tokens / 1000) * completion_cost_per_1k

        return prompt_cost + completion_cost

    def _get_pricing(self, model: str) -> tuple[float, float]:
        """Get pricing for a model."""
        # Try exact match
        if model in self._pricing:
            return self._pricing[model]

        # Try lowercase match
        model_lower = model.lower()
        for known_model, price in self._pricing.items():
            if known_model.lower() in model_lower or model_lower in known_model.lower():
                return price

        # Fall back to default pricing
        return self._pricing.get("default", (0.001, 0.002))

    def _detect_provider(self, model: str) -> str:
        """Detect provider from model name."""
        model_lower = model.lower()
        for hint, provider in self._provider_hints.items():
            if hint in model_lower:
                return provider
        return "unknown"

    def get_records(self) -> list[TokenConsumptionRecord]:
        """Get all tracked records."""
        return list(self._records)

    def get_aggregated_stats(self) -> AggregatedUsageStats:
        """Get aggregated usage statistics."""
        total_prompt = 0
        total_completion = 0
        total_cost = 0.0
        model_stats: dict[str, dict[str, int | float]] = {}

        for record in self._records:
            total_prompt += record.prompt_tokens
            total_completion += record.completion_tokens
            total_cost += record.cost_estimate_usd

            if record.model not in model_stats:
                model_stats[record.model] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "call_count": 0,
                }

            stats = model_stats[record.model]
            stats["prompt_tokens"] += record.prompt_tokens
            stats["completion_tokens"] += record.completion_tokens
            stats["total_tokens"] += record.total_tokens
            stats["cost_usd"] += record.cost_estimate_usd
            stats["call_count"] = int(stats["call_count"]) + 1

        return AggregatedUsageStats(
            total_prompt_tokens=total_prompt,
            total_completion_tokens=total_completion,
            total_tokens=total_prompt + total_completion,
            total_cost_usd=total_cost,
            call_count=len(self._records),
            model_breakdown=model_stats,
        )

    def reset(self) -> None:
        """Reset all tracked records."""
        self._records.clear()

    def get_model_cost(self, model: str) -> float:
        """Get total cost for a specific model."""
        return sum(r.cost_estimate_usd for r in self._records if r.model == model)

    def get_provider_cost(self, provider: str) -> float:
        """Get total cost for a specific provider."""
        return sum(r.cost_estimate_usd for r in self._records if r.provider == provider)


# ------------------------------------------------------------------
# Budget Tracker
# ------------------------------------------------------------------


@dataclass
class BudgetAlert:
    """Alert when budget threshold is reached."""

    threshold_percent: float
    current_cost: float
    budget_limit: float
    model: str


class BudgetTracker:
    """Budget tracker with alerting capabilities.

    This class extends TokenTracker with budget monitoring
    and threshold alerts.
    """

    def __init__(
        self,
        budget_limit_usd: float,
        pricing: dict[str, tuple[float, float]] | None = None,
        warning_threshold: float = 0.8,
    ) -> None:
        self._budget_limit = budget_limit_usd
        self._warning_threshold = warning_threshold
        self._tracker = TokenTracker(pricing)
        self._alerts: list[BudgetAlert] = []

    @property
    def budget_limit(self) -> float:
        """Get the budget limit."""
        return self._budget_limit

    @property
    def spent(self) -> float:
        """Get total spent."""
        return self._tracker.get_aggregated_stats().total_cost_usd

    @property
    def remaining(self) -> float:
        """Get remaining budget."""
        return max(0.0, self._budget_limit - self.spent)

    @property
    def usage_percent(self) -> float:
        """Get usage percentage."""
        if self._budget_limit == 0:
            return 0.0
        return self.spent / self._budget_limit

    def track(
        self,
        model: str,
        usage: dict[str, int],
        provider: str | None = None,
    ) -> TokenConsumptionRecord:
        """Track usage and check budget."""
        record = self._tracker.track(model, usage, provider)

        # Check if we should trigger alert
        if self.usage_percent >= self._warning_threshold:
            alert = BudgetAlert(
                threshold_percent=self._warning_threshold,
                current_cost=self.spent,
                budget_limit=self._budget_limit,
                model=model,
            )
            self._alerts.append(alert)

        return record

    def is_within_budget(self, additional_cost: float = 0.0) -> bool:
        """Check if additional cost would be within budget."""
        return (self.spent + additional_cost) <= self._budget_limit

    def get_alerts(self) -> list[BudgetAlert]:
        """Get all budget alerts."""
        return list(self._alerts)

    def reset(self) -> None:
        """Reset tracker and alerts."""
        self._tracker.reset()
        self._alerts.clear()
