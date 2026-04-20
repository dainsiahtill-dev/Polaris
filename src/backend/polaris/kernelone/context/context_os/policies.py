"""Policy grouping for State-First Context OS.

This module defines structured policy sub-classes that group related configuration
for StateFirstContextOSPolicy. Each sub-policy is a frozen dataclass with slots
for type safety and memory efficiency.

Backward Compatibility:
    StateFirstContextOSPolicy provides deprecated property accessors that proxy
    to sub-policy fields. Accessing policy.max_open_loops will still work but
    emits a DeprecationWarning.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# === Sub-Policy Dataclasses ===

# Policy group: Context Window Management


@dataclass(frozen=True, slots=True)
class ContextWindowPolicy:
    """Policy for context window management."""

    model_context_window: int = 128_000
    default_history_window_messages: int = 8
    max_active_window_messages: int = 18


# Policy group: Window Size Management


@dataclass(frozen=True, slots=True)
class WindowSizePolicy:
    """Policy for window sizing."""

    min_recent_messages_pinned: int = 3
    min_recent_floor: int = 3


# Policy group: Artifact Management


@dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    """Policy for artifact handling."""

    artifact_char_threshold: int = 1200
    artifact_token_threshold: int = 280
    max_artifact_stubs: int = 4


# Policy group: Collection Limits


@dataclass(frozen=True, slots=True)
class CollectionLimitsPolicy:
    """Policy for collection size limits."""

    max_episode_cards: int = 4
    max_open_loops: int = 6
    max_stable_facts: int = 8
    max_decisions: int = 6


# Policy group: Token Budget Configuration


@dataclass(frozen=True, slots=True)
class TokenBudgetPolicy:
    """Policy for token budget allocation."""

    output_reserve_ratio: float = 0.18  # 18% for model output
    tool_reserve_ratio: float = 0.10  # 10% for tool call results
    safety_margin_ratio: float = 0.05  # 5% safety buffer
    output_reserve_min: int = 1024
    tool_reserve_min: int = 512
    safety_margin_min: int = 2048
    retrieval_ratio: float = 0.12  # 12% of input for retrieval
    active_window_budget_ratio: float = 0.45  # 45% of input_budget for active window
    p95_tool_result_tokens: int = 2048
    planned_retrieval_tokens: int = 1536


# Policy group: Input Validation (P1 Security/Stability)


@dataclass(frozen=True, slots=True)
class InputValidationPolicy:
    """Policy for input validation limits."""

    max_messages: int = 1000  # Maximum messages per project() call
    max_message_size: int = 100_000  # Maximum bytes per message content (100KB)
    max_total_input_size: int = 10_000_000  # Maximum total input bytes (10MB)


# Policy group: Attention Runtime Feature Switches


@dataclass(frozen=True, slots=True)
class AttentionRuntimePolicy:
    """Policy for attention runtime features."""

    enable_dialog_act: bool = True
    prevent_seal_on_pending: bool = True
    enable_attention_trace: bool = True
    enable_seal_guard: bool = True


# === Main Policy Class with Backward-Compatible Properties ===


@dataclass(frozen=True)
class StateFirstContextOSPolicy:
    """Policy for State-First Context OS behavior.

    All policy values can be overridden by environment variables with the
    KERNELONE_CONTEXT_OS_ prefix (e.g., KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT).

    Use the `from_env()` factory method to create a policy instance that
    respects environment variable overrides.

    Input Validation Limits (P1 Security/Stability):
        max_messages: Maximum number of messages allowed per project() call (default: 1000).
            Exceeding this limit raises ValueError to prevent OOM from unbounded list growth.
        max_message_size: Maximum size in bytes per message content (default: 100_000 = 100KB).
            Exceeding this limit raises ValueError to prevent large content attacks.
        max_total_input_size: Maximum total input size in bytes (default: 10_000_000 = 10MB).
            Exceeding this limit raises ValueError to prevent total memory exhaustion.
    """

    # Sub-policy fields (immutable, frozen)
    context_window: ContextWindowPolicy = field(default_factory=ContextWindowPolicy)
    window_size: WindowSizePolicy = field(default_factory=WindowSizePolicy)
    artifact: ArtifactPolicy = field(default_factory=ArtifactPolicy)
    collection_limits: CollectionLimitsPolicy = field(default_factory=CollectionLimitsPolicy)
    token_budget: TokenBudgetPolicy = field(default_factory=TokenBudgetPolicy)
    input_validation: InputValidationPolicy = field(default_factory=InputValidationPolicy)
    attention_runtime: AttentionRuntimePolicy = field(default_factory=AttentionRuntimePolicy)

    # === Backward-compatible property accessors (deprecated) ===

    @property
    def model_context_window(self) -> int:
        """Deprecated: Use policy.context_window.model_context_window."""
        warnings.warn(
            "policy.model_context_window is deprecated, use policy.context_window.model_context_window",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.context_window.model_context_window

    @property
    def default_history_window_messages(self) -> int:
        """Deprecated: Use policy.context_window.default_history_window_messages."""
        warnings.warn(
            "policy.default_history_window_messages is deprecated, "
            "use policy.context_window.default_history_window_messages",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.context_window.default_history_window_messages

    @property
    def max_active_window_messages(self) -> int:
        """Deprecated: Use policy.context_window.max_active_window_messages."""
        warnings.warn(
            "policy.max_active_window_messages is deprecated, use policy.context_window.max_active_window_messages",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.context_window.max_active_window_messages

    @property
    def min_recent_messages_pinned(self) -> int:
        """Deprecated: Use policy.window_size.min_recent_messages_pinned."""
        warnings.warn(
            "policy.min_recent_messages_pinned is deprecated, use policy.window_size.min_recent_messages_pinned",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.window_size.min_recent_messages_pinned

    @property
    def min_recent_floor(self) -> int:
        """Deprecated: Use policy.window_size.min_recent_floor."""
        warnings.warn(
            "policy.min_recent_floor is deprecated, use policy.window_size.min_recent_floor",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.window_size.min_recent_floor

    @property
    def artifact_char_threshold(self) -> int:
        """Deprecated: Use policy.artifact.artifact_char_threshold."""
        warnings.warn(
            "policy.artifact_char_threshold is deprecated, use policy.artifact.artifact_char_threshold",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.artifact.artifact_char_threshold

    @property
    def artifact_token_threshold(self) -> int:
        """Deprecated: Use policy.artifact.artifact_token_threshold."""
        warnings.warn(
            "policy.artifact_token_threshold is deprecated, use policy.artifact.artifact_token_threshold",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.artifact.artifact_token_threshold

    @property
    def max_artifact_stubs(self) -> int:
        """Deprecated: Use policy.artifact.max_artifact_stubs."""
        warnings.warn(
            "policy.max_artifact_stubs is deprecated, use policy.artifact.max_artifact_stubs",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.artifact.max_artifact_stubs

    @property
    def max_episode_cards(self) -> int:
        """Deprecated: Use policy.collection_limits.max_episode_cards."""
        warnings.warn(
            "policy.max_episode_cards is deprecated, use policy.collection_limits.max_episode_cards",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.collection_limits.max_episode_cards

    @property
    def max_open_loops(self) -> int:
        """Deprecated: Use policy.collection_limits.max_open_loops."""
        warnings.warn(
            "policy.max_open_loops is deprecated, use policy.collection_limits.max_open_loops",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.collection_limits.max_open_loops

    @property
    def max_stable_facts(self) -> int:
        """Deprecated: Use policy.collection_limits.max_stable_facts."""
        warnings.warn(
            "policy.max_stable_facts is deprecated, use policy.collection_limits.max_stable_facts",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.collection_limits.max_stable_facts

    @property
    def max_decisions(self) -> int:
        """Deprecated: Use policy.collection_limits.max_decisions."""
        warnings.warn(
            "policy.max_decisions is deprecated, use policy.collection_limits.max_decisions",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.collection_limits.max_decisions

    @property
    def output_reserve_ratio(self) -> float:
        """Deprecated: Use policy.token_budget.output_reserve_ratio."""
        warnings.warn(
            "policy.output_reserve_ratio is deprecated, use policy.token_budget.output_reserve_ratio",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.output_reserve_ratio

    @property
    def tool_reserve_ratio(self) -> float:
        """Deprecated: Use policy.token_budget.tool_reserve_ratio."""
        warnings.warn(
            "policy.tool_reserve_ratio is deprecated, use policy.token_budget.tool_reserve_ratio",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.tool_reserve_ratio

    @property
    def safety_margin_ratio(self) -> float:
        """Deprecated: Use policy.token_budget.safety_margin_ratio."""
        warnings.warn(
            "policy.safety_margin_ratio is deprecated, use policy.token_budget.safety_margin_ratio",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.safety_margin_ratio

    @property
    def output_reserve_min(self) -> int:
        """Deprecated: Use policy.token_budget.output_reserve_min."""
        warnings.warn(
            "policy.output_reserve_min is deprecated, use policy.token_budget.output_reserve_min",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.output_reserve_min

    @property
    def tool_reserve_min(self) -> int:
        """Deprecated: Use policy.token_budget.tool_reserve_min."""
        warnings.warn(
            "policy.tool_reserve_min is deprecated, use policy.token_budget.tool_reserve_min",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.tool_reserve_min

    @property
    def safety_margin_min(self) -> int:
        """Deprecated: Use policy.token_budget.safety_margin_min."""
        warnings.warn(
            "policy.safety_margin_min is deprecated, use policy.token_budget.safety_margin_min",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.safety_margin_min

    @property
    def retrieval_ratio(self) -> float:
        """Deprecated: Use policy.token_budget.retrieval_ratio."""
        warnings.warn(
            "policy.retrieval_ratio is deprecated, use policy.token_budget.retrieval_ratio",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.retrieval_ratio

    @property
    def active_window_budget_ratio(self) -> float:
        """Deprecated: Use policy.token_budget.active_window_budget_ratio."""
        warnings.warn(
            "policy.active_window_budget_ratio is deprecated, use policy.token_budget.active_window_budget_ratio",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.active_window_budget_ratio

    @property
    def p95_tool_result_tokens(self) -> int:
        """Deprecated: Use policy.token_budget.p95_tool_result_tokens."""
        warnings.warn(
            "policy.p95_tool_result_tokens is deprecated, use policy.token_budget.p95_tool_result_tokens",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.p95_tool_result_tokens

    @property
    def planned_retrieval_tokens(self) -> int:
        """Deprecated: Use policy.token_budget.planned_retrieval_tokens."""
        warnings.warn(
            "policy.planned_retrieval_tokens is deprecated, use policy.token_budget.planned_retrieval_tokens",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.token_budget.planned_retrieval_tokens

    @property
    def max_messages(self) -> int:
        """Deprecated: Use policy.input_validation.max_messages."""
        warnings.warn(
            "policy.max_messages is deprecated, use policy.input_validation.max_messages",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.input_validation.max_messages

    @property
    def max_message_size(self) -> int:
        """Deprecated: Use policy.input_validation.max_message_size."""
        warnings.warn(
            "policy.max_message_size is deprecated, use policy.input_validation.max_message_size",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.input_validation.max_message_size

    @property
    def max_total_input_size(self) -> int:
        """Deprecated: Use policy.input_validation.max_total_input_size."""
        warnings.warn(
            "policy.max_total_input_size is deprecated, use policy.input_validation.max_total_input_size",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.input_validation.max_total_input_size

    @property
    def enable_dialog_act(self) -> bool:
        """Deprecated: Use policy.attention_runtime.enable_dialog_act."""
        warnings.warn(
            "policy.enable_dialog_act is deprecated, use policy.attention_runtime.enable_dialog_act",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.attention_runtime.enable_dialog_act

    @property
    def prevent_seal_on_pending(self) -> bool:
        """Deprecated: Use policy.attention_runtime.prevent_seal_on_pending."""
        warnings.warn(
            "policy.prevent_seal_on_pending is deprecated, use policy.attention_runtime.prevent_seal_on_pending",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.attention_runtime.prevent_seal_on_pending

    @property
    def enable_attention_trace(self) -> bool:
        """Deprecated: Use policy.attention_runtime.enable_attention_trace."""
        warnings.warn(
            "policy.enable_attention_trace is deprecated, use policy.attention_runtime.enable_attention_trace",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.attention_runtime.enable_attention_trace

    @property
    def enable_seal_guard(self) -> bool:
        """Deprecated: Use policy.attention_runtime.enable_seal_guard."""
        warnings.warn(
            "policy.enable_seal_guard is deprecated, use policy.attention_runtime.enable_seal_guard",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.attention_runtime.enable_seal_guard

    # === Factory Methods ===

    @classmethod
    def from_env(cls) -> StateFirstContextOSPolicy:
        """Create a policy instance with environment variable overrides.

        Environment variables follow the pattern:
        KERNELONE_CONTEXT_OS_<FIELD_NAME> (uppercase with underscores)

        Examples:
            KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT=false
            KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS=10
            KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW=65536

        Returns:
            A new StateFirstContextOSPolicy instance with overrides applied.
        """
        # Map of environment variable names to (sub_policy_name, field_name)
        env_overrides: dict[str, tuple[str, str, type]] = {
            "KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT": ("attention_runtime", "enable_dialog_act", bool),
            "KERNELONE_CONTEXT_OS_PREVENT_SEAL_ON_PENDING": ("attention_runtime", "prevent_seal_on_pending", bool),
            "KERNELONE_CONTEXT_OS_ENABLE_ATTENTION_TRACE": ("attention_runtime", "enable_attention_trace", bool),
            "KERNELONE_CONTEXT_OS_ENABLE_SEAL_GUARD": ("attention_runtime", "enable_seal_guard", bool),
            "KERNELONE_CONTEXT_OS_MIN_RECENT_FLOOR": ("window_size", "min_recent_floor", int),
            "KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW": ("context_window", "model_context_window", int),
            "KERNELONE_CONTEXT_OS_DEFAULT_HISTORY_WINDOW_MESSAGES": (
                "context_window",
                "default_history_window_messages",
                int,
            ),
            "KERNELONE_CONTEXT_OS_MAX_ACTIVE_WINDOW_MESSAGES": (
                "context_window",
                "max_active_window_messages",
                int,
            ),
            "KERNELONE_CONTEXT_OS_MIN_RECENT_MESSAGES_PINNED": (
                "window_size",
                "min_recent_messages_pinned",
                int,
            ),
            "KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS": ("collection_limits", "max_open_loops", int),
            "KERNELONE_CONTEXT_OS_MAX_STABLE_FACTS": ("collection_limits", "max_stable_facts", int),
            "KERNELONE_CONTEXT_OS_MAX_DECISIONS": ("collection_limits", "max_decisions", int),
            "KERNELONE_CONTEXT_OS_MAX_ARTIFACT_STUBS": ("artifact", "max_artifact_stubs", int),
            "KERNELONE_CONTEXT_OS_MAX_EPISODE_CARDS": ("collection_limits", "max_episode_cards", int),
            "KERNELONE_CONTEXT_OS_ARTIFACT_CHAR_THRESHOLD": ("artifact", "artifact_char_threshold", int),
            "KERNELONE_CONTEXT_OS_ARTIFACT_TOKEN_THRESHOLD": ("artifact", "artifact_token_threshold", int),
            "KERNELONE_CONTEXT_OS_P95_TOOL_RESULT_TOKENS": ("token_budget", "p95_tool_result_tokens", int),
            "KERNELONE_CONTEXT_OS_PLANNED_RETRIEVAL_TOKENS": ("token_budget", "planned_retrieval_tokens", int),
            "KERNELONE_CONTEXT_OS_OUTPUT_RESERVE_RATIO": ("token_budget", "output_reserve_ratio", float),
            "KERNELONE_CONTEXT_OS_TOOL_RESERVE_RATIO": ("token_budget", "tool_reserve_ratio", float),
            "KERNELONE_CONTEXT_OS_SAFETY_MARGIN_RATIO": ("token_budget", "safety_margin_ratio", float),
            "KERNELONE_CONTEXT_OS_RETRIEVAL_RATIO": ("token_budget", "retrieval_ratio", float),
            "KERNELONE_CONTEXT_OS_ACTIVE_WINDOW_BUDGET_RATIO": (
                "token_budget",
                "active_window_budget_ratio",
                float,
            ),
            "KERNELONE_CONTEXT_OS_MAX_MESSAGES": ("input_validation", "max_messages", int),
            "KERNELONE_CONTEXT_OS_MAX_MESSAGE_SIZE": ("input_validation", "max_message_size", int),
            "KERNELONE_CONTEXT_OS_MAX_TOTAL_INPUT_SIZE": ("input_validation", "max_total_input_size", int),
        }

        # Build initial sub-policy overrides
        sub_policy_overrides: dict[str, dict[str, Any]] = {
            "context_window": {},
            "window_size": {},
            "artifact": {},
            "collection_limits": {},
            "token_budget": {},
            "input_validation": {},
            "attention_runtime": {},
        }

        for env_name, (sub_policy_name, field_name, field_type) in env_overrides.items():
            env_value = os.environ.get(env_name)
            if env_value is None:
                continue

            try:
                converted: bool | int | float
                if field_type is bool:
                    converted = env_value.lower() in ("true", "1", "yes", "on")
                elif field_type is int:
                    converted = int(env_value)
                elif field_type is float:
                    converted = float(env_value)
                else:
                    continue
                sub_policy_overrides[sub_policy_name][field_name] = converted
            except (ValueError, TypeError):
                # Silently ignore invalid values to maintain stability
                pass

        # Build sub-policies with overrides
        # Note: Using direct default values because slots=True dataclasses
        # return field descriptors when accessing class attributes
        context_window = ContextWindowPolicy(
            model_context_window=sub_policy_overrides["context_window"].get(
                "model_context_window",
                128_000,  # Default value
            ),
            default_history_window_messages=sub_policy_overrides["context_window"].get(
                "default_history_window_messages",
                8,  # Default value
            ),
            max_active_window_messages=sub_policy_overrides["context_window"].get(
                "max_active_window_messages",
                18,  # Default value
            ),
        )

        window_size = WindowSizePolicy(
            min_recent_messages_pinned=sub_policy_overrides["window_size"].get(
                "min_recent_messages_pinned",
                3,  # Default value
            ),
            min_recent_floor=sub_policy_overrides["window_size"].get(
                "min_recent_floor",
                3,  # Default value
            ),
        )

        artifact = ArtifactPolicy(
            artifact_char_threshold=sub_policy_overrides["artifact"].get(
                "artifact_char_threshold",
                1200,  # Default value
            ),
            artifact_token_threshold=sub_policy_overrides["artifact"].get(
                "artifact_token_threshold",
                280,  # Default value
            ),
            max_artifact_stubs=sub_policy_overrides["artifact"].get(
                "max_artifact_stubs",
                4,  # Default value
            ),
        )

        collection_limits = CollectionLimitsPolicy(
            max_episode_cards=sub_policy_overrides["collection_limits"].get(
                "max_episode_cards",
                4,  # Default value
            ),
            max_open_loops=sub_policy_overrides["collection_limits"].get(
                "max_open_loops",
                6,  # Default value
            ),
            max_stable_facts=sub_policy_overrides["collection_limits"].get(
                "max_stable_facts",
                8,  # Default value
            ),
            max_decisions=sub_policy_overrides["collection_limits"].get(
                "max_decisions",
                6,  # Default value
            ),
        )

        token_budget = TokenBudgetPolicy(
            output_reserve_ratio=sub_policy_overrides["token_budget"].get(
                "output_reserve_ratio",
                0.18,  # Default value
            ),
            tool_reserve_ratio=sub_policy_overrides["token_budget"].get(
                "tool_reserve_ratio",
                0.10,  # Default value
            ),
            safety_margin_ratio=sub_policy_overrides["token_budget"].get(
                "safety_margin_ratio",
                0.05,  # Default value
            ),
            output_reserve_min=sub_policy_overrides["token_budget"].get(
                "output_reserve_min",
                1024,  # Default value
            ),
            tool_reserve_min=sub_policy_overrides["token_budget"].get(
                "tool_reserve_min",
                512,  # Default value
            ),
            safety_margin_min=sub_policy_overrides["token_budget"].get(
                "safety_margin_min",
                2048,  # Default value
            ),
            retrieval_ratio=sub_policy_overrides["token_budget"].get(
                "retrieval_ratio",
                0.12,  # Default value
            ),
            active_window_budget_ratio=sub_policy_overrides["token_budget"].get(
                "active_window_budget_ratio",
                0.45,  # Default value
            ),
            p95_tool_result_tokens=sub_policy_overrides["token_budget"].get(
                "p95_tool_result_tokens",
                2048,  # Default value
            ),
            planned_retrieval_tokens=sub_policy_overrides["token_budget"].get(
                "planned_retrieval_tokens",
                1536,  # Default value
            ),
        )

        input_validation = InputValidationPolicy(
            max_messages=sub_policy_overrides["input_validation"].get(
                "max_messages",
                1000,  # Default value
            ),
            max_message_size=sub_policy_overrides["input_validation"].get(
                "max_message_size",
                100_000,  # Default value
            ),
            max_total_input_size=sub_policy_overrides["input_validation"].get(
                "max_total_input_size",
                10_000_000,  # Default value
            ),
        )

        attention_runtime = AttentionRuntimePolicy(
            enable_dialog_act=sub_policy_overrides["attention_runtime"].get(
                "enable_dialog_act",
                True,  # Default value
            ),
            prevent_seal_on_pending=sub_policy_overrides["attention_runtime"].get(
                "prevent_seal_on_pending",
                True,  # Default value
            ),
            enable_attention_trace=sub_policy_overrides["attention_runtime"].get(
                "enable_attention_trace",
                True,  # Default value
            ),
            enable_seal_guard=sub_policy_overrides["attention_runtime"].get(
                "enable_seal_guard",
                True,  # Default value
            ),
        )

        return cls(
            context_window=context_window,
            window_size=window_size,
            artifact=artifact,
            collection_limits=collection_limits,
            token_budget=token_budget,
            input_validation=input_validation,
            attention_runtime=attention_runtime,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert policy to dictionary for serialization."""
        return {
            "context_window": {
                "model_context_window": self.context_window.model_context_window,
                "default_history_window_messages": self.context_window.default_history_window_messages,
                "max_active_window_messages": self.context_window.max_active_window_messages,
            },
            "window_size": {
                "min_recent_messages_pinned": self.window_size.min_recent_messages_pinned,
                "min_recent_floor": self.window_size.min_recent_floor,
            },
            "artifact": {
                "artifact_char_threshold": self.artifact.artifact_char_threshold,
                "artifact_token_threshold": self.artifact.artifact_token_threshold,
                "max_artifact_stubs": self.artifact.max_artifact_stubs,
            },
            "collection_limits": {
                "max_episode_cards": self.collection_limits.max_episode_cards,
                "max_open_loops": self.collection_limits.max_open_loops,
                "max_stable_facts": self.collection_limits.max_stable_facts,
                "max_decisions": self.collection_limits.max_decisions,
            },
            "token_budget": {
                "output_reserve_ratio": self.token_budget.output_reserve_ratio,
                "tool_reserve_ratio": self.token_budget.tool_reserve_ratio,
                "safety_margin_ratio": self.token_budget.safety_margin_ratio,
                "output_reserve_min": self.token_budget.output_reserve_min,
                "tool_reserve_min": self.token_budget.tool_reserve_min,
                "safety_margin_min": self.token_budget.safety_margin_min,
                "retrieval_ratio": self.token_budget.retrieval_ratio,
                "active_window_budget_ratio": self.token_budget.active_window_budget_ratio,
                "p95_tool_result_tokens": self.token_budget.p95_tool_result_tokens,
                "planned_retrieval_tokens": self.token_budget.planned_retrieval_tokens,
            },
            "input_validation": {
                "max_messages": self.input_validation.max_messages,
                "max_message_size": self.input_validation.max_message_size,
                "max_total_input_size": self.input_validation.max_total_input_size,
            },
            "attention_runtime": {
                "enable_dialog_act": self.attention_runtime.enable_dialog_act,
                "prevent_seal_on_pending": self.attention_runtime.prevent_seal_on_pending,
                "enable_attention_trace": self.attention_runtime.enable_attention_trace,
                "enable_seal_guard": self.attention_runtime.enable_seal_guard,
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StateFirstContextOSPolicy:
        """Create policy from dictionary (inverse of to_dict)."""
        if not isinstance(payload, dict):
            return cls()

        context_window_data = payload.get("context_window", {})
        window_size_data = payload.get("window_size", {})
        artifact_data = payload.get("artifact", {})
        collection_limits_data = payload.get("collection_limits", {})
        token_budget_data = payload.get("token_budget", {})
        input_validation_data = payload.get("input_validation", {})
        attention_runtime_data = payload.get("attention_runtime", {})

        # Build sub-policies with defaults
        # Note: Using direct default values because slots=True dataclasses
        # return field descriptors when accessing class attributes
        context_window = ContextWindowPolicy(
            model_context_window=_safe_int(
                context_window_data.get("model_context_window"),
                128_000,  # Default value
            ),
            default_history_window_messages=_safe_int(
                context_window_data.get("default_history_window_messages"),
                8,  # Default value
            ),
            max_active_window_messages=_safe_int(
                context_window_data.get("max_active_window_messages"),
                18,  # Default value
            ),
        )

        window_size = WindowSizePolicy(
            min_recent_messages_pinned=_safe_int(
                window_size_data.get("min_recent_messages_pinned"),
                3,  # Default value
            ),
            min_recent_floor=_safe_int(
                window_size_data.get("min_recent_floor"),
                3,  # Default value
            ),
        )

        artifact = ArtifactPolicy(
            artifact_char_threshold=_safe_int(
                artifact_data.get("artifact_char_threshold"),
                1200,  # Default value
            ),
            artifact_token_threshold=_safe_int(
                artifact_data.get("artifact_token_threshold"),
                280,  # Default value
            ),
            max_artifact_stubs=_safe_int(
                artifact_data.get("max_artifact_stubs"),
                4,  # Default value
            ),
        )

        collection_limits = CollectionLimitsPolicy(
            max_episode_cards=_safe_int(
                collection_limits_data.get("max_episode_cards"),
                4,  # Default value
            ),
            max_open_loops=_safe_int(
                collection_limits_data.get("max_open_loops"),
                6,  # Default value
            ),
            max_stable_facts=_safe_int(
                collection_limits_data.get("max_stable_facts"),
                8,  # Default value
            ),
            max_decisions=_safe_int(
                collection_limits_data.get("max_decisions"),
                6,  # Default value
            ),
        )

        token_budget = TokenBudgetPolicy(
            output_reserve_ratio=_safe_float(
                token_budget_data.get("output_reserve_ratio"),
                0.18,  # Default value
            ),
            tool_reserve_ratio=_safe_float(
                token_budget_data.get("tool_reserve_ratio"),
                0.10,  # Default value
            ),
            safety_margin_ratio=_safe_float(
                token_budget_data.get("safety_margin_ratio"),
                0.05,  # Default value
            ),
            output_reserve_min=_safe_int(
                token_budget_data.get("output_reserve_min"),
                1024,  # Default value
            ),
            tool_reserve_min=_safe_int(
                token_budget_data.get("tool_reserve_min"),
                512,  # Default value
            ),
            safety_margin_min=_safe_int(
                token_budget_data.get("safety_margin_min"),
                2048,  # Default value
            ),
            retrieval_ratio=_safe_float(
                token_budget_data.get("retrieval_ratio"),
                0.12,  # Default value
            ),
            active_window_budget_ratio=_safe_float(
                token_budget_data.get("active_window_budget_ratio"),
                0.45,  # Default value
            ),
            p95_tool_result_tokens=_safe_int(
                token_budget_data.get("p95_tool_result_tokens"),
                2048,  # Default value
            ),
            planned_retrieval_tokens=_safe_int(
                token_budget_data.get("planned_retrieval_tokens"),
                1536,  # Default value
            ),
        )

        input_validation = InputValidationPolicy(
            max_messages=_safe_int(
                input_validation_data.get("max_messages"),
                1000,  # Default value
            ),
            max_message_size=_safe_int(
                input_validation_data.get("max_message_size"),
                100_000,  # Default value
            ),
            max_total_input_size=_safe_int(
                input_validation_data.get("max_total_input_size"),
                10_000_000,  # Default value
            ),
        )

        attention_runtime = AttentionRuntimePolicy(
            enable_dialog_act=bool(attention_runtime_data.get("enable_dialog_act", True)),
            prevent_seal_on_pending=bool(attention_runtime_data.get("prevent_seal_on_pending", True)),
            enable_attention_trace=bool(attention_runtime_data.get("enable_attention_trace", True)),
            enable_seal_guard=bool(attention_runtime_data.get("enable_seal_guard", True)),
        )

        return cls(
            context_window=context_window,
            window_size=window_size,
            artifact=artifact,
            collection_limits=collection_limits,
            token_budget=token_budget,
            input_validation=input_validation,
            attention_runtime=attention_runtime,
        )
