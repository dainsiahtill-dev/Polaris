"""Tests for Context OS Policy grouping."""

from __future__ import annotations

import warnings
from dataclasses import FrozenInstanceError

import pytest
from polaris.kernelone.context.context_os.policies import (
    ArtifactPolicy,
    AttentionRuntimePolicy,
    CollectionLimitsPolicy,
    ContextWindowPolicy,
    InputValidationPolicy,
    StateFirstContextOSPolicy,
    TokenBudgetPolicy,
    WindowSizePolicy,
)


class TestSubPolicyDataclasses:
    """Tests that sub-policies are frozen dataclasses with slots."""

    def test_context_window_policy_is_frozen(self) -> None:
        """ContextWindowPolicy should be frozen."""
        policy = ContextWindowPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.model_context_window = 65536

    def test_context_window_policy_has_slots(self) -> None:
        """ContextWindowPolicy should have slots."""
        policy = ContextWindowPolicy()
        assert hasattr(policy, "__slots__")
        assert "model_context_window" in policy.__slots__

    def test_window_size_policy_is_frozen(self) -> None:
        """WindowSizePolicy should be frozen."""
        policy = WindowSizePolicy()
        with pytest.raises(FrozenInstanceError):
            policy.min_recent_messages_pinned = 5

    def test_artifact_policy_is_frozen(self) -> None:
        """ArtifactPolicy should be frozen."""
        policy = ArtifactPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.max_artifact_stubs = 8

    def test_collection_limits_policy_is_frozen(self) -> None:
        """CollectionLimitsPolicy should be frozen."""
        policy = CollectionLimitsPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.max_open_loops = 10

    def test_token_budget_policy_is_frozen(self) -> None:
        """TokenBudgetPolicy should be frozen."""
        policy = TokenBudgetPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.output_reserve_ratio = 0.2

    def test_input_validation_policy_is_frozen(self) -> None:
        """InputValidationPolicy should be frozen."""
        policy = InputValidationPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.max_messages = 2000

    def test_attention_runtime_policy_is_frozen(self) -> None:
        """AttentionRuntimePolicy should be frozen."""
        policy = AttentionRuntimePolicy()
        with pytest.raises(FrozenInstanceError):
            policy.enable_dialog_act = False


class TestStateFirstContextOSPolicySubPolicies:
    """Tests for StateFirstContextOSPolicy sub-policy grouping."""

    def test_default_sub_policies(self) -> None:
        """StateFirstContextOSPolicy should have default sub-policies."""
        policy = StateFirstContextOSPolicy()
        assert isinstance(policy.context_window, ContextWindowPolicy)
        assert isinstance(policy.window_size, WindowSizePolicy)
        assert isinstance(policy.artifact, ArtifactPolicy)
        assert isinstance(policy.collection_limits, CollectionLimitsPolicy)
        assert isinstance(policy.token_budget, TokenBudgetPolicy)
        assert isinstance(policy.input_validation, InputValidationPolicy)
        assert isinstance(policy.attention_runtime, AttentionRuntimePolicy)

    def test_sub_policy_values(self) -> None:
        """Sub-policies should have correct default values."""
        policy = StateFirstContextOSPolicy()
        assert policy.context_window.model_context_window == 128_000
        assert policy.context_window.default_history_window_messages == 8
        assert policy.context_window.max_active_window_messages == 18
        assert policy.window_size.min_recent_messages_pinned == 3
        assert policy.window_size.min_recent_floor == 3
        assert policy.artifact.artifact_char_threshold == 1200
        assert policy.artifact.artifact_token_threshold == 280
        assert policy.artifact.max_artifact_stubs == 4
        assert policy.collection_limits.max_episode_cards == 4
        assert policy.collection_limits.max_open_loops == 6
        assert policy.collection_limits.max_stable_facts == 8
        assert policy.collection_limits.max_decisions == 6
        assert policy.token_budget.output_reserve_ratio == 0.18
        assert policy.token_budget.tool_reserve_ratio == 0.10
        assert policy.token_budget.safety_margin_ratio == 0.05
        assert policy.token_budget.output_reserve_min == 1024
        assert policy.token_budget.tool_reserve_min == 512
        assert policy.token_budget.safety_margin_min == 2048
        assert policy.token_budget.retrieval_ratio == 0.12
        assert policy.token_budget.active_window_budget_ratio == 0.45
        assert policy.token_budget.p95_tool_result_tokens == 2048
        assert policy.token_budget.planned_retrieval_tokens == 1536
        assert policy.input_validation.max_messages == 1000
        assert policy.input_validation.max_message_size == 100_000
        assert policy.input_validation.max_total_input_size == 10_000_000
        assert policy.attention_runtime.enable_dialog_act is True
        assert policy.attention_runtime.prevent_seal_on_pending is True
        assert policy.attention_runtime.enable_attention_trace is True
        assert policy.attention_runtime.enable_seal_guard is True

    def test_custom_sub_policies(self) -> None:
        """StateFirstContextOSPolicy should accept custom sub-policies."""
        context_window = ContextWindowPolicy(model_context_window=65536)
        collection_limits = CollectionLimitsPolicy(max_open_loops=10)
        policy = StateFirstContextOSPolicy(
            context_window=context_window,
            collection_limits=collection_limits,
        )
        assert policy.context_window.model_context_window == 65536
        assert policy.collection_limits.max_open_loops == 10
        # Other defaults should remain
        assert policy.artifact.max_artifact_stubs == 4


class TestBackwardCompatibleProperties:
    """Tests for backward-compatible property accessors."""

    def test_model_context_window_deprecated(self) -> None:
        """Accessing policy.model_context_window should emit DeprecationWarning."""
        policy = StateFirstContextOSPolicy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            value = policy.model_context_window
            assert value == 128_000
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "model_context_window" in str(w[0].message)
            assert "context_window" in str(w[0].message)

    def test_max_open_loops_deprecated(self) -> None:
        """Accessing policy.max_open_loops should emit DeprecationWarning."""
        policy = StateFirstContextOSPolicy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            value = policy.max_open_loops
            assert value == 6
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "max_open_loops" in str(w[0].message)
            assert "collection_limits" in str(w[0].message)

    def test_max_artifact_stubs_deprecated(self) -> None:
        """Accessing policy.max_artifact_stubs should emit DeprecationWarning."""
        policy = StateFirstContextOSPolicy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            value = policy.max_artifact_stubs
            assert value == 4
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "max_artifact_stubs" in str(w[0].message)
            assert "artifact" in str(w[0].message)

    def test_enable_dialog_act_deprecated(self) -> None:
        """Accessing policy.enable_dialog_act should emit DeprecationWarning."""
        policy = StateFirstContextOSPolicy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            value = policy.enable_dialog_act
            assert value is True
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "enable_dialog_act" in str(w[0].message)
            assert "attention_runtime" in str(w[0].message)

    def test_all_deprecated_properties_exist(self) -> None:
        """All original policy fields should have deprecated property accessors."""
        policy = StateFirstContextOSPolicy()
        deprecated_fields = [
            "model_context_window",
            "default_history_window_messages",
            "max_active_window_messages",
            "min_recent_messages_pinned",
            "min_recent_floor",
            "artifact_char_threshold",
            "artifact_token_threshold",
            "max_artifact_stubs",
            "max_episode_cards",
            "max_open_loops",
            "max_stable_facts",
            "max_decisions",
            "output_reserve_ratio",
            "tool_reserve_ratio",
            "safety_margin_ratio",
            "output_reserve_min",
            "tool_reserve_min",
            "safety_margin_min",
            "retrieval_ratio",
            "active_window_budget_ratio",
            "p95_tool_result_tokens",
            "planned_retrieval_tokens",
            "max_messages",
            "max_message_size",
            "max_total_input_size",
            "enable_dialog_act",
            "prevent_seal_on_pending",
            "enable_attention_trace",
            "enable_seal_guard",
        ]
        for field_name in deprecated_fields:
            assert hasattr(policy, field_name), f"Missing property: {field_name}"
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                getattr(policy, field_name)  # Should not raise


class TestToDictFromDict:
    """Tests for to_dict and from_dict methods."""

    def test_to_dict_returns_dict(self) -> None:
        """to_dict should return a dictionary."""
        policy = StateFirstContextOSPolicy()
        result = policy.to_dict()
        assert isinstance(result, dict)
        assert "context_window" in result
        assert "window_size" in result
        assert "artifact" in result
        assert "collection_limits" in result
        assert "token_budget" in result
        assert "input_validation" in result
        assert "attention_runtime" in result

    def test_to_dict_values(self) -> None:
        """to_dict should return correct values."""
        policy = StateFirstContextOSPolicy()
        result = policy.to_dict()
        assert result["context_window"]["model_context_window"] == 128_000
        assert result["collection_limits"]["max_open_loops"] == 6
        assert result["attention_runtime"]["enable_dialog_act"] is True

    def test_from_dict_returns_policy(self) -> None:
        """from_dict should return a StateFirstContextOSPolicy instance."""
        data = {
            "context_window": {"model_context_window": 65536},
            "window_size": {"min_recent_messages_pinned": 5},
            "artifact": {"max_artifact_stubs": 8},
            "collection_limits": {"max_open_loops": 12},
            "token_budget": {"output_reserve_ratio": 0.20},
            "input_validation": {"max_messages": 2000},
            "attention_runtime": {"enable_dialog_act": False},
        }
        policy = StateFirstContextOSPolicy.from_dict(data)
        assert isinstance(policy, StateFirstContextOSPolicy)
        assert policy.context_window.model_context_window == 65536
        assert policy.window_size.min_recent_messages_pinned == 5
        assert policy.artifact.max_artifact_stubs == 8
        assert policy.collection_limits.max_open_loops == 12
        assert policy.token_budget.output_reserve_ratio == 0.20
        assert policy.input_validation.max_messages == 2000
        assert policy.attention_runtime.enable_dialog_act is False

    def test_from_dict_with_defaults(self) -> None:
        """from_dict with empty dict should return default policy."""
        policy = StateFirstContextOSPolicy.from_dict({})
        assert isinstance(policy, StateFirstContextOSPolicy)
        assert policy.context_window.model_context_window == 128_000
        assert policy.collection_limits.max_open_loops == 6

    def test_from_dict_not_dict(self) -> None:
        """from_dict with non-dict should return default policy."""
        policy = StateFirstContextOSPolicy.from_dict(None)
        assert isinstance(policy, StateFirstContextOSPolicy)
        assert policy.context_window.model_context_window == 128_000

    def test_roundtrip(self) -> None:
        """to_dict followed by from_dict should produce equivalent policy."""
        policy = StateFirstContextOSPolicy(
            context_window=ContextWindowPolicy(model_context_window=65536),
            collection_limits=CollectionLimitsPolicy(max_open_loops=12),
        )
        data = policy.to_dict()
        restored = StateFirstContextOSPolicy.from_dict(data)
        assert restored.context_window.model_context_window == policy.context_window.model_context_window
        assert restored.collection_limits.max_open_loops == policy.collection_limits.max_open_loops


class TestFromEnv:
    """Tests for from_env factory method."""

    def test_from_env_returns_policy(self) -> None:
        """from_env should return a StateFirstContextOSPolicy instance."""
        import os

        # Clear any existing env vars
        for key in list(os.environ.keys()):
            if key.startswith("KERNELONE_CONTEXT_OS_"):
                del os.environ[key]

        policy = StateFirstContextOSPolicy.from_env()
        assert isinstance(policy, StateFirstContextOSPolicy)

    def test_from_env_with_overrides(self) -> None:
        """from_env should apply environment variable overrides."""
        import os

        # Set env vars
        os.environ["KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW"] = "65536"
        os.environ["KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS"] = "12"
        os.environ["KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT"] = "false"

        try:
            policy = StateFirstContextOSPolicy.from_env()
            assert policy.context_window.model_context_window == 65536
            assert policy.collection_limits.max_open_loops == 12
            assert policy.attention_runtime.enable_dialog_act is False
        finally:
            # Clean up
            del os.environ["KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW"]
            del os.environ["KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS"]
            del os.environ["KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT"]

    def test_from_env_invalid_values_ignored(self) -> None:
        """from_env should ignore invalid environment variable values."""
        import os

        os.environ["KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW"] = "not_a_number"

        try:
            policy = StateFirstContextOSPolicy.from_env()
            # Should use default value
            assert policy.context_window.model_context_window == 128_000
        finally:
            del os.environ["KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW"]
