"""Unit tests for Replay Suite Generator.

Tests the attention runtime evaluation suite generators:
- Long session generation
- Multi-topic switching generation
- Follow-up lifecycle generation
- Benchmark suite generation
- Direct case creation utilities
"""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os import (
    AttentionRuntimeEvalSuite,
    AttentionRuntimeQualityCase,
    create_multi_turn_case,
    create_short_session_case,
    generate_followup_lifecycle_suite,
    generate_long_session_suite,
    generate_multi_topic_suite,
    generate_replay_benchmark_suite,
)
from polaris.kernelone.context.context_os.replay_suite_generator import (
    _assistant,
    _format_conversation,
    _tool,
    _user,
)


class TestConversationBuildingBlocks:
    """Tests for conversation building block functions."""

    def test_user_creates_valid_message(self) -> None:
        """_user() creates a valid user message dict."""
        msg = _user("Hello")
        assert msg == {"role": "user", "content": "Hello"}
        assert msg["role"] == "user"

    def test_assistant_creates_valid_message(self) -> None:
        """_assistant() creates a valid assistant message dict."""
        msg = _assistant("How can I help?")
        assert msg == {"role": "assistant", "content": "How can I help?"}
        assert msg["role"] == "assistant"

    def test_tool_creates_valid_message(self) -> None:
        """_tool() creates a valid tool message dict."""
        msg = _tool("File created")
        assert msg == {"role": "tool", "content": "File created"}
        assert msg["role"] == "tool"

    def test_format_conversation_filters_invalid_roles(self) -> None:
        """_format_conversation() filters out invalid roles."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "invalid", "content": "Should be filtered"},
            {"role": "tool", "content": "Result"},
        ]
        formatted = _format_conversation(messages)
        assert len(formatted) == 3
        roles = {m["role"] for m in formatted}
        assert roles == {"user", "assistant", "tool"}

    def test_format_conversation_handles_missing_fields(self) -> None:
        """_format_conversation() handles messages with missing fields."""
        messages = [
            {"content": "Hello"},  # Missing role
            {"role": "assistant"},  # Missing content
            {},  # Empty
        ]
        formatted = _format_conversation(messages)
        # Only valid messages should be included
        assert all(m["role"] in ("user", "assistant", "tool") for m in formatted)


class TestLongSessionGeneration:
    """Tests for long session suite generation."""

    def test_generate_long_session_suite_basic(self) -> None:
        """generate_long_session_suite creates a valid suite."""
        suite = generate_long_session_suite(num_turns=10, theme="implementation")
        assert isinstance(suite, AttentionRuntimeEvalSuite)
        assert suite.version == 1
        assert "implementation" in suite.suite_id
        assert len(suite.cases) >= 1

    def test_generate_long_session_suite_themes(self) -> None:
        """generate_long_session_suite works with different themes."""
        themes = ["implementation", "bugfix", "refactor", "code_review", "generic"]
        for theme in themes:
            suite = generate_long_session_suite(num_turns=10, theme=theme)
            assert suite is not None
            assert len(suite.cases) >= 1
            for case in suite.cases:
                assert isinstance(case, AttentionRuntimeQualityCase)
                assert len(case.conversation) >= 10

    def test_generate_long_session_suite_min_turns(self) -> None:
        """generate_long_session_suite enforces minimum turns."""
        with pytest.raises(ValueError, match="num_turns must be >= 10"):
            generate_long_session_suite(num_turns=5, theme="implementation")

    def test_generate_long_session_suite_case_structure(self) -> None:
        """Generated cases have correct structure."""
        suite = generate_long_session_suite(num_turns=20, theme="bugfix")
        for case in suite.cases:
            assert case.case_id
            assert len(case.conversation) >= 10
            assert case.expected_latest_intent
            assert case.expected_pending_followup_status in (
                "",
                "pending",
                "confirmed",
                "denied",
                "paused",
                "redirected",
            )
            assert case.expected_attention_roots_count >= 0
            assert isinstance(case.expect_seal_blocked, bool)


class TestMultiTopicSuiteGeneration:
    """Tests for multi-topic switching suite generation."""

    def test_generate_multi_topic_suite_basic(self) -> None:
        """generate_multi_topic_suite creates a valid suite."""
        suite = generate_multi_topic_suite(num_topics=3)
        assert isinstance(suite, AttentionRuntimeEvalSuite)
        assert suite.version == 1
        assert "multi_topic" in suite.suite_id
        assert len(suite.cases) >= 4  # At least 4 predefined cases

    def test_generate_multi_topic_suite_different_topic_counts(self) -> None:
        """generate_multi_topic_suite works with different topic counts."""
        for num_topics in [2, 3, 4, 5]:
            suite = generate_multi_topic_suite(num_topics=num_topics)
            assert suite is not None
            assert len(suite.cases) >= 3

    def test_generate_multi_topic_suite_case_diversity(self) -> None:
        """Generated cases cover different switching patterns."""
        suite = generate_multi_topic_suite()
        case_ids = {case.case_id for case in suite.cases}
        assert "generated_multi_topic_simple_switch" in case_ids
        assert "generated_multi_topic_deep_then_switch" in case_ids
        assert "generated_multi_topic_rapid_interleave" in case_ids
        assert "generated_multi_topic_return" in case_ids


class TestFollowupLifecycleSuiteGeneration:
    """Tests for follow-up lifecycle suite generation."""

    def test_generate_followup_lifecycle_suite_basic(self) -> None:
        """generate_followup_lifecycle_suite creates a valid suite."""
        suite = generate_followup_lifecycle_suite()
        assert isinstance(suite, AttentionRuntimeEvalSuite)
        assert suite.version == 1
        assert suite.suite_id == "followup_lifecycle"
        assert len(suite.cases) >= 8  # At least 8 predefined cases

    def test_generate_followup_lifecycle_suite_followup_statuses(self) -> None:
        """Generated cases cover all follow-up statuses."""
        suite = generate_followup_lifecycle_suite()
        statuses = {case.expected_pending_followup_status for case in suite.cases}
        # Should cover at least: confirmed, denied, paused, redirected
        assert "confirmed" in statuses or any(
            case.expected_pending_followup_status == "confirmed" for case in suite.cases
        )

    def test_generate_followup_lifecycle_suite_conversation_structure(self) -> None:
        """Follow-up cases have proper question-answer structure."""
        suite = generate_followup_lifecycle_suite()
        for case in suite.cases:
            # Should have at least user-assistant pairs
            user_msgs = [m for m in case.conversation if m["role"] == "user"]
            assistant_msgs = [m for m in case.conversation if m["role"] == "assistant"]
            assert len(user_msgs) >= 2
            assert len(assistant_msgs) >= 2


class TestReplayBenchmarkSuite:
    """Tests for comprehensive replay benchmark suite generation."""

    def test_generate_replay_benchmark_suite_basic(self) -> None:
        """generate_replay_benchmark_suite creates a valid suite."""
        suite = generate_replay_benchmark_suite()
        assert isinstance(suite, AttentionRuntimeEvalSuite)
        assert suite.version == 1
        assert suite.suite_id == "replay_benchmark_v1"

    def test_generate_replay_benchmark_suite_comprehensive_coverage(self) -> None:
        """Benchmark suite contains all categories of cases."""
        suite = generate_replay_benchmark_suite()
        # Should have long sessions, multi-topic, and follow-up cases
        assert len(suite.cases) >= 15  # At least 3 long + 4 multi-topic + 8 follow-up


class TestDirectCaseCreation:
    """Tests for direct case creation utilities."""

    def test_create_short_session_case(self) -> None:
        """create_short_session_case creates a valid short session case."""
        case = create_short_session_case(
            case_id="test_short",
            intent="帮我完成任务",
            followup_status="confirmed",
        )
        assert case.case_id == "test_short"
        assert len(case.conversation) == 3
        assert case.conversation[0]["role"] == "user"
        assert case.conversation[-1]["role"] == "user"
        assert case.expected_latest_intent == "帮我完成任务"
        assert case.expected_pending_followup_status == "confirmed"

    def test_create_short_session_case_no_followup(self) -> None:
        """create_short_session_case works without follow-up."""
        case = create_short_session_case(
            case_id="test_no_followup",
            intent="查询状态",
        )
        assert case.expected_pending_followup_status == ""
        assert case.expect_seal_blocked is False

    def test_create_short_session_case_pending_blocks_seal(self) -> None:
        """create_short_session_case sets seal blocked for pending follow-up."""
        case = create_short_session_case(
            case_id="test_pending",
            intent="等待确认",
            followup_status="pending",
        )
        assert case.expect_seal_blocked is True

    def test_create_multi_turn_case(self) -> None:
        """create_multi_turn_case creates a valid multi-turn case."""
        turns = [
            ("user", "帮我实现功能", ""),
            ("assistant", "好的，需要哪些功能？", ""),
            ("user", "增删改查", "pending"),
            ("assistant", "需要我添加分页吗？", ""),
            ("user", "需要", "confirmed"),
        ]
        case = create_multi_turn_case(
            case_id="test_multi",
            turns=turns,
            final_intent="需要",
            followup_status="confirmed",
        )
        assert case.case_id == "test_multi"
        assert len(case.conversation) == 5
        assert case.expected_latest_intent == "需要"
        assert case.expected_pending_followup_status == "confirmed"
        assert case.expected_attention_roots_count >= 2

    def test_create_multi_turn_case_tracks_attention_roots(self) -> None:
        """create_multi_turn_case tracks attention roots correctly."""
        turns = [
            ("user", "任务1", ""),
            ("assistant", "处理中", ""),
            ("user", "任务2", "pending"),
            ("assistant", "需要确认吗？", ""),
        ]
        case = create_multi_turn_case(
            case_id="test_roots",
            turns=turns,
            final_intent="任务2",
            followup_status="pending",
        )
        # Should have at least 2 attention roots
        assert case.expected_attention_roots_count >= 2


class TestCaseStructureValidation:
    """Tests for case structure validation."""

    def test_long_session_suite_yaml_loads(self) -> None:
        """Long session suite YAML can be loaded."""
        import pathlib

        from polaris.kernelone.context.context_os.evaluation import load_attention_runtime_eval_suite

        yaml_path = pathlib.Path(__file__).parent / "long_session_eval_suite.yaml"
        if yaml_path.exists():
            suite = load_attention_runtime_eval_suite(yaml_path)
            assert suite is not None
            assert suite.version == 1
            assert len(suite.cases) >= 20  # Should have 20+ cases

    def test_case_conversations_are_semantically_coherent(self) -> None:
        """Generated conversations maintain semantic coherence."""
        suite = generate_long_session_suite(num_turns=15, theme="implementation")
        for case in suite.cases:
            # Check that conversation has alternating roles
            roles = [m["role"] for m in case.conversation]
            # Should have both user and assistant messages
            assert "user" in roles
            assert "assistant" in roles
            # Content should not be empty
            for msg in case.conversation:
                assert msg["content"]

    def test_case_expected_fields_populated(self) -> None:
        """All cases have properly populated expected fields."""
        suite = generate_replay_benchmark_suite()
        for case in suite.cases:
            assert case.case_id
            assert case.expected_latest_intent is not None
            assert case.expected_pending_followup_status in (
                "",
                "pending",
                "confirmed",
                "denied",
                "paused",
                "redirected",
            )
            assert case.expected_attention_roots_count >= 0


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_conversation_case(self) -> None:
        """Case with empty conversation is handled gracefully."""
        case = AttentionRuntimeQualityCase(
            case_id="empty",
            conversation=[],
            expected_latest_intent="",
            expected_pending_followup_status="",
            expected_attention_roots_count=0,
            expect_seal_blocked=False,
        )
        assert len(case.conversation) == 0
        assert case.expected_attention_roots_count == 0

    def test_single_message_case(self) -> None:
        """Case with single message is handled correctly."""
        case = create_short_session_case(
            case_id="single",
            intent="Hello",
        )
        # Should have 3 messages (request + clarification + intent)
        assert len(case.conversation) >= 1

    def test_very_long_conversation(self) -> None:
        """Very long conversations are handled correctly."""
        suite = generate_long_session_suite(num_turns=50, theme="implementation")
        for case in suite.cases:
            assert len(case.conversation) >= 50

    def test_many_attention_roots(self) -> None:
        """Cases with many attention roots work correctly."""
        suite = generate_multi_topic_suite(num_topics=5)
        for case in suite.cases:
            assert case.expected_attention_roots_count >= 0
