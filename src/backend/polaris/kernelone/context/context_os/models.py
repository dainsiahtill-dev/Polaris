"""State-First Context OS models.

.. deprecated::
    This module is deprecated. Use models_v2.py (Pydantic v2 models) instead.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TypedDict, cast

from .models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    BudgetPlanV2 as BudgetPlan,
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    ContextSlicePlanV2 as ContextSlicePlan,
    ContextSliceSelectionV2 as ContextSliceSelection,
    DecisionEntryV2 as DecisionEntry,
    DialogActResultV2 as DialogActResult,
    EpisodeCardV2 as EpisodeCard,
    PendingFollowUpV2 as PendingFollowUp,
    RunCardV2 as RunCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from .snapshot_summary import SnapshotSummaryView

warnings.warn(
    "polaris.kernelone.context.context_os.models is deprecated. Use models_v2.py (Pydantic v2 models) instead.",
    DeprecationWarning,
    stacklevel=2,
)


class RoutingClass:
    CLEAR: str = "clear"
    PATCH: str = "patch"
    ARCHIVE: str = "archive"
    SUMMARIZE: str = "summarize"


class DialogAct:
    """Dialog act types for conversation classification.

    .. deprecated::
        Use models_v2.DialogAct (Enum) instead.

    These acts represent the semantic function of a user/assistant message
    in the conversational flow, enabling proper attention and intent tracking.
    """

    AFFIRM = "affirm"  # 确认: 需要/好的/可以/是
    DENY = "deny"  # 否定: 不/不要/不用
    PAUSE = "pause"  # 暂停: 先别/等一下
    REDIRECT = "redirect"  # 重定向: 改成另外一个
    CLARIFY = "clarify"  # 澄清: 什么意思/再说说
    COMMIT = "commit"  # 承诺: 就这样/确定/就这样吧
    CANCEL = "cancel"  # 取消: 取消/算了/不要了
    STATUS_ACK = "status_ack"  # 状态确认: 知道了/好的收到
    NOISE = "noise"  # 无意义/低信号
    UNKNOWN = "unknown"  # 未分类

    @classmethod
    def values(cls) -> tuple[str, ...]:
        warnings.warn(
            "DialogAct is deprecated. Use models_v2.DialogAct instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return (
            cls.AFFIRM,
            cls.DENY,
            cls.PAUSE,
            cls.REDIRECT,
            cls.CLARIFY,
            cls.COMMIT,
            cls.CANCEL,
            cls.STATUS_ACK,
            cls.NOISE,
            cls.UNKNOWN,
        )

    @classmethod
    def is_high_priority(cls, act: str) -> bool:
        """High-priority dialog acts that should never be treated as low-signal."""
        warnings.warn(
            "DialogAct.is_high_priority is deprecated. Use models_v2.DialogAct.is_high_priority instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return act in {
            cls.AFFIRM,
            cls.DENY,
            cls.PAUSE,
            cls.REDIRECT,
            cls.CLARIFY,
            cls.COMMIT,
            cls.CANCEL,
        }


class _PolicyEnvOverrides(TypedDict, total=False):
    """Precisely typed env override payload for :class:`StateFirstContextOSPolicy`.

    Mirrors exactly the int/bool fields that ``from_env`` is allowed to override.
    Float fields are intentionally excluded because ``from_env`` only converts
    ``bool`` and ``int`` env values. Used as a validated cast target so the
    runtime-validated overrides can be splatted into ``dataclasses.replace``
    under ``mypy --strict`` without ``Any`` or ``# type: ignore``.
    """

    # int fields
    model_context_window: int
    default_history_window_messages: int
    artifact_char_threshold: int
    artifact_token_threshold: int
    max_artifact_stubs: int
    max_episode_cards: int
    max_open_loops: int
    max_stable_facts: int
    max_decisions: int
    max_active_window_messages: int
    min_recent_messages_pinned: int
    p95_tool_result_tokens: int
    planned_retrieval_tokens: int
    min_recent_floor: int
    # bool fields
    enable_dialog_act: bool
    prevent_seal_on_pending: bool
    enable_attention_trace: bool
    enable_seal_guard: bool


@dataclass(frozen=True, slots=True)
class StateFirstContextOSPolicy:
    """Policy for State-First Context OS behavior.

    All policy values can be overridden by environment variables with the
    KERNELONE_CONTEXT_OS_ prefix (e.g., KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT).

    Use the `from_env()` factory method to create a policy instance that
    respects environment variable overrides.
    """

    def __post_init__(self) -> None:
        warnings.warn(
            "StateFirstContextOSPolicy dataclass is deprecated. Use models_v2.StateFirstContextOSPolicyV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    model_context_window: int = 128_000
    default_history_window_messages: int = 8
    artifact_char_threshold: int = 1200
    artifact_token_threshold: int = 280
    max_artifact_stubs: int = 4
    max_episode_cards: int = 4
    max_open_loops: int = 6
    max_stable_facts: int = 8
    max_decisions: int = 6
    max_active_window_messages: int = 18
    min_recent_messages_pinned: int = 3
    p95_tool_result_tokens: int = 2048
    planned_retrieval_tokens: int = 1536

    # === Token Budget Ratios (extracted from hardcoded values) ===
    # These control how the context window budget is divided
    output_reserve_ratio: float = 0.18  # 18% for model output
    tool_reserve_ratio: float = 0.10  # 10% for tool call results
    safety_margin_ratio: float = 0.05  # 5% safety buffer (Claude Code: max(2048, 0.05C))
    # Minimum reserves to prevent zero-budget edge cases
    output_reserve_min: int = 1024
    tool_reserve_min: int = 512
    safety_margin_min: int = 2048
    retrieval_ratio: float = 0.12  # 12% of input for retrieval
    # Active window budget ratio (for _collect_active_window token allocation)
    active_window_budget_ratio: float = 0.45  # 45% of input_budget for active window

    # === Attention Runtime Feature Switches (A1-A5 rollout) ===
    # Enable deterministic dialog act classification
    enable_dialog_act: bool = True
    # Minimum recent messages to hard-pin in active window
    min_recent_floor: int = 3
    # Prevent episode sealing when pending follow-up exists
    prevent_seal_on_pending: bool = True
    # Enable attention observability debug trace
    enable_attention_trace: bool = True
    # Enable seal guard blocking rules
    enable_seal_guard: bool = True

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
        import os
        from dataclasses import replace

        # Map of environment variable names to field names
        env_overrides: dict[str, str] = {
            "KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT": "enable_dialog_act",
            "KERNELONE_CONTEXT_OS_PREVENT_SEAL_ON_PENDING": "prevent_seal_on_pending",
            "KERNELONE_CONTEXT_OS_ENABLE_ATTENTION_TRACE": "enable_attention_trace",
            "KERNELONE_CONTEXT_OS_ENABLE_SEAL_GUARD": "enable_seal_guard",
            "KERNELONE_CONTEXT_OS_MIN_RECENT_FLOOR": "min_recent_floor",
            "KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW": "model_context_window",
            "KERNELONE_CONTEXT_OS_DEFAULT_HISTORY_WINDOW_MESSAGES": "default_history_window_messages",
            "KERNELONE_CONTEXT_OS_MAX_ACTIVE_WINDOW_MESSAGES": "max_active_window_messages",
            "KERNELONE_CONTEXT_OS_MIN_RECENT_MESSAGES_PINNED": "min_recent_messages_pinned",
            "KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS": "max_open_loops",
            "KERNELONE_CONTEXT_OS_MAX_STABLE_FACTS": "max_stable_facts",
            "KERNELONE_CONTEXT_OS_MAX_DECISIONS": "max_decisions",
            "KERNELONE_CONTEXT_OS_MAX_ARTIFACT_STUBS": "max_artifact_stubs",
            "KERNELONE_CONTEXT_OS_MAX_EPISODE_CARDS": "max_episode_cards",
            "KERNELONE_CONTEXT_OS_ARTIFACT_CHAR_THRESHOLD": "artifact_char_threshold",
            "KERNELONE_CONTEXT_OS_ARTIFACT_TOKEN_THRESHOLD": "artifact_token_threshold",
            "KERNELONE_CONTEXT_OS_P95_TOOL_RESULT_TOKENS": "p95_tool_result_tokens",
            "KERNELONE_CONTEXT_OS_PLANNED_RETRIEVAL_TOKENS": "planned_retrieval_tokens",
        }

        # Collect overrides
        kwargs: dict[str, bool | int] = {}
        for env_name, field_name in env_overrides.items():
            env_value = os.environ.get(env_name)
            if env_value is None:
                continue

            # Determine the field type and convert
            field_type = type(getattr(StateFirstContextOSPolicy, field_name))
            converted: bool | int
            try:
                if field_type is bool:
                    # Handle string boolean values
                    converted = env_value.lower() in ("true", "1", "yes", "on")
                elif field_type is int:
                    converted = int(env_value)
                else:
                    continue
                kwargs[field_name] = converted
            except (ValueError, TypeError):
                # Silently ignore invalid values to maintain stability
                pass

        # Create instance with overrides using replace().
        # The loop above only assigns keys whose corresponding policy field is
        # typed bool/int (and converts the value to match), so the runtime dict
        # always conforms to _PolicyEnvOverrides. Cast at this validated boundary
        # so replace() type-checks under mypy --strict without Any/type:ignore.
        if kwargs:
            overrides = cast(_PolicyEnvOverrides, kwargs)
            return replace(cls(), **overrides)
        return cls()


# === DEPRECATED: Compatibility aliases for v1 → v2 migration ===
# The v1 dataclasses have been removed: they had no constructor call sites, no
# deserialization consumer and no persisted-runtime reader, and the only one of
# them that still mattered (_ContextOSProjectionV1) carried a foundation ->
# Cell reverse dependency into roles.kernel metrics.
#
# Every public name below is now backed by the V2 model family exclusively, so
# the aliases are a plain re-export.  There is no V1 fallback left to protect,
# and a genuine import failure must surface instead of being silently
# swallowed by a suppressed ImportError.
#
# Migration guide:
#   OLD: from polaris.kernelone.context.context_os.models import ArtifactRecord
#   NEW: from polaris.kernelone.context.context_os.models_v2 import ArtifactRecordV2
#
__all__ = [
    "ArtifactRecord",
    "BudgetPlan",
    "ContextOSProjection",
    "ContextOSSnapshot",
    "ContextSlicePlan",
    "ContextSliceSelection",
    "DecisionEntry",
    "DialogAct",
    "DialogActResult",
    "EpisodeCard",
    "PendingFollowUp",
    "RunCard",
    "SnapshotSummaryView",
    "StateEntry",
    "StateFirstContextOSPolicy",
    "TaskStateView",
    "TranscriptEvent",
    "UserProfileState",
    "WorkingState",
]
