"""Tests for RoleContextCompressor three-layer compression system.

Tests cover the complete compression pipeline:
  - micro_compact: replaces old tool results with placeholders
  - auto_compact: deterministic truncation preserving tool pairs
  - llm_compact: LLM-based continuity summary with deterministic fallback
  - compact_if_needed: three-layer integration entry point
  - build_continuity_summary_text: deterministic summary generation
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from polaris.kernelone.context.compaction import (
    CompactSnapshot,
    RoleContextCompressor,
    RoleContextIdentity,
    build_continuity_summary_text,
)


class TestRoleContextCompressorMicroCompact:
    """Tests for Layer 1: micro_compact."""

    def test_micro_compact_preserves_recent_tool_results(self) -> None:
        """micro_compact must preserve the most recent N tool results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.micro_compact_keep = 2

            long_content = "x" * 200
            messages = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": long_content},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_2", "name": "search_code"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_2", "content": long_content},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_3", "name": "write_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_3", "content": long_content},
                    ],
                },
            ]

            result = compressor.micro_compact(messages)

            # Latest 2 tool results should be preserved
            assert len(result) == 6
            # First tool_result (tu_1) should be compacted
            first_tr = result[1]["content"][0]
            assert first_tr.get("_compacted") is True
            assert "[Previous: used read_file" in first_tr["content"]
            # Second tool_result (tu_2) should be preserved
            second_tr = result[3]["content"][0]
            assert second_tr.get("_compacted") is None
            assert second_tr["content"] == long_content
            # Third tool_result (tu_3) should be preserved
            third_tr = result[5]["content"][0]
            assert third_tr.get("_compacted") is None
            assert third_tr["content"] == long_content

    def test_micro_compact_skips_when_under_threshold(self) -> None:
        """micro_compact must skip when tool result count <= keep threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.micro_compact_keep = 3

            messages = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "content_1"},
                    ],
                },
            ]

            result = compressor.micro_compact(messages)

            # Should return unchanged
            assert result == messages
            assert result[1]["content"][0].get("_compacted") is None

    def test_micro_compact_does_not_mutate_input_copies(self) -> None:
        """micro_compact must not mutate caller's standalone result dict (T3-7 fix)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.micro_compact_keep = 1

            original_content = "original_content" * 50
            standalone_result = {
                "type": "tool_result",
                "tool_use_id": "tu_1",
                "content": original_content,
            }
            messages = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [standalone_result],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_2", "name": "search_code"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_2",
                            "content": "preserved",
                        },
                    ],
                },
            ]

            result = compressor.micro_compact(messages)

            # Standalone result dict should NOT be mutated (T3-7 copy guarantee)
            assert standalone_result["content"] == original_content
            assert standalone_result.get("_compacted") is None
            # But the result in the returned messages should be compacted
            assert result[1]["content"][0]["content"] != original_content
            assert result[1]["content"][0].get("_compacted") is True

    def test_micro_compact_short_content_not_compacted(self) -> None:
        """micro_compact must not compact tool results under 100 chars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.micro_compact_keep = 0

            messages = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": "short",
                        },
                    ],
                },
            ]

            result = compressor.micro_compact(messages)

            # Short content should not be compacted
            assert result[1]["content"][0]["content"] == "short"
            assert result[1]["content"][0].get("_compacted") is None

    def test_micro_compact_status_mapping(self) -> None:
        """micro_compact must map tool result status correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.micro_compact_keep = 1

            long_content = "x" * 200
            messages = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": long_content,
                            "status": "success",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_2", "name": "write_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_2",
                            "content": long_content,
                            "status": "error",
                        },
                    ],
                },
            ]

            result = compressor.micro_compact(messages)

            # First tool_result should be compacted (status=success -> "ok")
            compacted = result[1]["content"][0]
            assert compacted.get("_compacted") is True
            assert "ok" in compacted["content"]
            # Second tool_result should be preserved
            assert result[3]["content"][0].get("_compacted") is None

            # Test error status with fresh messages
            messages2 = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": long_content,
                            "status": "error",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_2", "name": "search_code"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_2",
                            "content": long_content,
                            "status": "success",
                        },
                    ],
                },
            ]
            result2 = compressor.micro_compact(messages2)
            compacted2 = result2[1]["content"][0]
            assert compacted2.get("_compacted") is True
            assert "error" in compacted2["content"]


class TestRoleContextCompressorFindToolPairs:
    """Tests for _find_tool_pair_indices."""

    def test_finds_matching_tool_pairs(self) -> None:
        """_find_tool_pair_indices must find matching tool_use/tool_result pairs."""
        compressor = RoleContextCompressor(workspace=".", role_name="test")

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "result_1"},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_2", "name": "write_file"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_2", "content": "result_2"},
                ],
            },
        ]

        protected = compressor._find_tool_pair_indices(messages)

        # All 4 indices should be protected
        assert protected == {0, 1, 2, 3}

    def test_skips_unmatched_tool_results(self) -> None:
        """_find_tool_pair_indices must not protect unmatched tool_results."""
        compressor = RoleContextCompressor(workspace=".", role_name="test")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "orphan", "content": "no match"},
                ],
            },
        ]

        protected = compressor._find_tool_pair_indices(messages)

        assert protected == set()

    def test_skips_tool_use_without_result(self) -> None:
        """_find_tool_pair_indices must handle tool_use without matching result."""
        compressor = RoleContextCompressor(workspace=".", role_name="test")

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                ],
            },
            {"role": "user", "content": "no tool result here"},
        ]

        protected = compressor._find_tool_pair_indices(messages)

        assert protected == set()

    def test_finds_pairs_within_window(self) -> None:
        """_find_tool_pair_indices must find pairs within the 4-message window."""
        compressor = RoleContextCompressor(workspace=".", role_name="test")

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                ],
            },
            {"role": "user", "content": "intermediate 1"},
            {"role": "user", "content": "intermediate 2"},
            {"role": "user", "content": "intermediate 3"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"},
                ],
            },
        ]

        protected = compressor._find_tool_pair_indices(messages)

        # Should find the pair (indices 0 and 4)
        assert 0 in protected
        assert 4 in protected

    def test_skips_pairs_beyond_window(self) -> None:
        """_find_tool_pair_indices must not find pairs beyond the 4-message window."""
        compressor = RoleContextCompressor(workspace=".", role_name="test")

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                ],
            },
            {"role": "user", "content": "intermediate 1"},
            {"role": "user", "content": "intermediate 2"},
            {"role": "user", "content": "intermediate 3"},
            {"role": "user", "content": "intermediate 4"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"},
                ],
            },
        ]

        protected = compressor._find_tool_pair_indices(messages)

        # Should NOT find the pair (too far apart)
        assert protected == set()


class TestRoleContextCompressorAutoCompact:
    """Tests for Layer 2: auto_compact."""

    def test_auto_compact_keeps_system_messages(self) -> None:
        """auto_compact must always preserve system messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "msg1"},
                {"role": "user", "content": "msg2"},
                {"role": "user", "content": "msg3"},
                {"role": "user", "content": "msg4"},
                {"role": "user", "content": "msg5"},
            ]

            result = compressor.auto_compact(messages, target_tokens=0)

            # System message must be preserved
            assert result[0]["role"] == "system"

    def test_auto_compact_does_not_split_tool_pairs(self) -> None:
        """auto_compact must not split tool_use/tool_result pairs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "msg1"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"},
                    ],
                },
                {"role": "user", "content": "msg2"},
            ]

            # Very low target to force removal
            result = compressor.auto_compact(messages, target_tokens=1)

            # Either both tool messages exist, or both are removed
            tool_indices = [
                i for i, m in enumerate(result) if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            ]
            if tool_indices:
                # If assistant tool_use exists, its result must also exist
                has_result = any(
                    m.get("role") == "user"
                    and isinstance(m.get("content"), list)
                    and any(isinstance(p, dict) and p.get("tool_use_id") == "tu_1" for p in (m.get("content") or []))
                    for m in result
                )
                assert has_result, "tool_use exists but matching tool_result was removed"

    def test_auto_compact_stops_when_all_protected(self) -> None:
        """auto_compact must stop when all remaining messages are protected pairs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "system", "content": "sys"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_2", "name": "write_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_2", "content": "result2"},
                    ],
                },
            ]

            # Low target but all non-system messages are protected pairs
            result = compressor.auto_compact(messages, target_tokens=1)

            # Should keep system + all protected pairs (can't remove them)
            assert len(result) >= 5

    def test_auto_compact_noop_when_under_budget(self) -> None:
        """auto_compact must return unchanged when under budget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "msg1"},
            ]

            result = compressor.auto_compact(messages, target_tokens=100000)

            assert result == messages


class TestRoleContextCompressorLLMCompact:
    """Tests for Layer 3: llm_compact."""

    def test_llm_compact_fallback_without_llm_client(self) -> None:
        """llm_compact must use deterministic fallback when LLM client unavailable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            # No llm_client set

            messages = [
                {"role": "user", "content": "Implement login feature"},
                {"role": "assistant", "content": "I'll help you implement login."},
            ]
            identity = RoleContextIdentity(
                role_id="test_1",
                role_type="developer",
                goal="Implement login",
            )

            compressed, snapshot = compressor.llm_compact(messages, identity)

            # Should return compressed messages with summary
            assert len(compressed) == 2
            assert compressed[0]["role"] == "user"
            assert "Context compressed for continuity" in compressed[0]["content"]
            assert isinstance(snapshot, CompactSnapshot)
            assert snapshot.method == "deterministic"
            assert snapshot.original_tokens > 0
            assert snapshot.compressed_tokens > 0

    def test_llm_compact_includes_identity(self) -> None:
        """llm_compact must include role identity in re-injection prompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "user", "content": "test"},
            ]
            identity = RoleContextIdentity(
                role_id="task_123",
                role_type="architect",
                goal="Design auth system",
                acceptance_criteria=["OAuth2 support", "Session management"],
                scope=["auth/*", "models/user.py"],
                current_phase="designing",
            )

            compressed, _snapshot = compressor.llm_compact(messages, identity)

            reinjection = compressed[0]["content"]
            assert "architect" in reinjection
            assert "task_123" in reinjection
            assert "Design auth system" in reinjection
            assert "OAuth2 support" in reinjection
            assert "auth/*" in reinjection
            assert "designing" in reinjection

    def test_llm_compact_saves_transcript(self) -> None:
        """llm_compact must save full transcript to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ]
            identity = RoleContextIdentity(role_id="t1", role_type="dev")

            _compressed, snapshot = compressor.llm_compact(messages, identity)

            # Transcript file should exist
            assert snapshot.transcript_path
            assert Path(snapshot.transcript_path).exists()

    def test_llm_compact_computes_hashes(self) -> None:
        """llm_compact must compute original and summary hashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")

            messages = [
                {"role": "user", "content": "test message"},
            ]
            identity = RoleContextIdentity(role_id="t1", role_type="dev")

            _compressed, snapshot = compressor.llm_compact(messages, identity)

            assert snapshot.original_hash
            assert snapshot.summary_hash
            assert snapshot.original_hash != snapshot.summary_hash


class TestRoleContextCompressorCompactIfNeeded:
    """Tests for three-layer integration: compact_if_needed."""

    def test_compact_if_needed_noop_when_under_threshold(self) -> None:
        """compact_if_needed must skip when under token threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.token_threshold = 100000  # Very high threshold

            messages = [
                {"role": "user", "content": "short"},
            ]
            identity = RoleContextIdentity(role_id="t1", role_type="dev")

            result, snapshot = compressor.compact_if_needed(messages, identity)

            # Should return unchanged with no snapshot
            assert result[0]["content"] == "short"
            assert snapshot is None

    def test_compact_if_needed_applies_micro_compact_always(self) -> None:
        """compact_if_needed must always apply micro_compact first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.token_threshold = 100000
            compressor.micro_compact_keep = 1

            long_content = "x" * 200
            messages = [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_1", "name": "read_file"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": long_content,
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "tu_2", "name": "search_code"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_2",
                            "content": long_content,
                        },
                    ],
                },
            ]
            identity = RoleContextIdentity(role_id="t1", role_type="dev")

            result, _snapshot = compressor.compact_if_needed(messages, identity)

            # micro_compact should have been applied (keep=1 means oldest is compacted)
            assert result[1]["content"][0].get("_compacted") is True
            # Latest tool result should be preserved
            assert result[3]["content"][0].get("_compacted") is None

    def test_compact_if_needed_forces_compact(self) -> None:
        """compact_if_needed must compact when force_compact=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.token_threshold = 1  # Very low to force layer 3

            messages = [
                {"role": "user", "content": "test message for forced compaction"},
                {"role": "assistant", "content": "response one"},
                {"role": "user", "content": "another message here"},
                {"role": "assistant", "content": "response two"},
            ]
            identity = RoleContextIdentity(role_id="t1", role_type="dev")

            _result, snapshot = compressor.compact_if_needed(messages, identity, force_compact=True)

            # Should have been compacted via layer 3 (llm_compact fallback)
            assert snapshot is not None
            assert snapshot.method in ("llm", "deterministic")
            assert snapshot.original_tokens > 0

    def test_compact_if_needed_target_tokens_override(self) -> None:
        """compact_if_needed must respect target_tokens override."""
        with tempfile.TemporaryDirectory() as tmpdir:
            compressor = RoleContextCompressor(workspace=tmpdir, role_name="test")
            compressor.token_threshold = 100000

            messages = [
                {"role": "user", "content": "a " * 1000},  # Long message
            ]
            identity = RoleContextIdentity(role_id="t1", role_type="dev")

            # Force compaction with low target
            _result, snapshot = compressor.compact_if_needed(messages, identity, force_compact=True, target_tokens=10)

            assert snapshot is not None
            assert snapshot.compressed_tokens < snapshot.original_tokens


class TestBuildContinuitySummaryText:
    """Tests for build_continuity_summary_text."""

    def test_empty_messages_returns_empty(self) -> None:
        """build_continuity_summary_text must return empty for empty input."""
        result = build_continuity_summary_text([])
        assert result == ""

    def test_filters_low_signal_messages(self) -> None:
        """build_continuity_summary_text must filter low-signal messages when mixed with high-signal."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "Fix the bug in auth.py where login fails"},
            {"role": "user", "content": "thanks"},
        ]

        result = build_continuity_summary_text(messages)

        # Should preserve high-signal message and note omissions
        assert "Context continuity summary" in result
        assert "auth.py" in result
        assert "Omitted low-signal or redundant turns" in result

    def test_preserves_high_signal_messages(self) -> None:
        """build_continuity_summary_text must preserve high-signal messages."""
        messages = [
            {"role": "user", "content": "Fix the bug in auth.py where login fails"},
            {"role": "assistant", "content": "I'll investigate auth.py and fix the login bug."},
        ]

        result = build_continuity_summary_text(messages)

        assert "auth.py" in result
        assert "bug" in result.lower()

    def test_includes_identity(self) -> None:
        """build_continuity_summary_text must include role identity."""
        messages = [
            {"role": "user", "content": "Implement feature X"},
        ]
        identity = RoleContextIdentity(
            role_type="developer",
            goal="Implement feature X",
            acceptance_criteria=["Tests pass", "No regressions"],
        )

        result = build_continuity_summary_text(messages, identity)

        assert "developer" in result
        assert "Implement feature X" in result
        assert "Tests pass" in result

    def test_focus_is_included(self) -> None:
        """build_continuity_summary_text must include focus topic."""
        messages = [
            {"role": "user", "content": "Fix the bug"},
        ]

        result = build_continuity_summary_text(messages, focus="authentication module")

        assert "authentication module" in result

    def test_respects_max_chars(self) -> None:
        """build_continuity_summary_text must respect max_chars limit."""
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(100)]

        result = build_continuity_summary_text(messages, max_chars=100)

        assert len(result) <= 103  # Allow for "..." truncation

    def test_handles_content_from_message_field(self) -> None:
        """build_continuity_summary_text must handle 'message' field fallback."""
        messages = [
            {"role": "user", "message": "This uses message field"},
        ]

        result = build_continuity_summary_text(messages)

        assert "message field" in result

    def test_preserves_code_paths(self) -> None:
        """build_continuity_summary_text must give bonus to messages with code paths."""
        messages = [
            {"role": "user", "content": "Check src/main.py for the issue"},
            {"role": "assistant", "content": "ok"},
        ]

        result = build_continuity_summary_text(messages)

        # Code path message should be preserved
        assert "src/main.py" in result


class TestRoleContextIdentity:
    """Tests for RoleContextIdentity."""

    def test_legacy_field_synchronization(self) -> None:
        """RoleContextIdentity must sync legacy and new fields."""
        identity = RoleContextIdentity(role_id="abc123")

        assert identity.task_id == "abc123"

    def test_from_task_creates_identity(self) -> None:
        """from_task must create identity from task dict."""
        task = {
            "id": "task_1",
            "goal": "Fix login",
            "acceptance_criteria": ["Works on mobile"],
            "write_scope": ["auth/*"],
        }

        identity = RoleContextIdentity.from_task(task, role_type="developer")

        assert identity.role_id == "task_1"
        assert identity.goal == "Fix login"
        assert identity.acceptance_criteria == ["Works on mobile"]
        assert identity.scope == ["auth/*"]

    def test_from_role_state_creates_identity(self) -> None:
        """from_role_state must create identity from role state."""
        identity = RoleContextIdentity.from_role_state(
            role_name="director",
            goal="Refactor codebase",
            scope=["src/core"],
        )

        assert identity.role_type == "director"
        assert identity.goal == "Refactor codebase"
        assert identity.scope == ["src/core"]

    def test_scope_write_scope_sync(self) -> None:
        """RoleContextIdentity must sync scope and write_scope."""
        identity = RoleContextIdentity(scope=["a.py", "b.py"])

        assert identity.write_scope == ["a.py", "b.py"]

        identity2 = RoleContextIdentity(write_scope=["c.py"])
        assert identity2.scope == ["c.py"]
