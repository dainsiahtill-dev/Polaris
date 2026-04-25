"""Tests for context_adapter module."""

from __future__ import annotations

from polaris.cells.roles.runtime.public.context_adapter import (
    augment_context_with_handoff_rehydration,
    augment_context_with_repo_intelligence,
    load_session_context_os_snapshot,
    merge_context_override,
    to_string_list,
)


class TestToStringList:
    """Tests for to_string_list helper."""

    def test_list_input(self):
        result = to_string_list(["alpha", "beta", "gamma"])
        assert result == ["alpha", "beta", "gamma"]

    def test_tuple_input(self):
        result = to_string_list(("alpha", "beta"))
        assert result == ["alpha", "beta"]

    def test_set_input(self):
        result = to_string_list({"alpha", "beta"})
        assert "alpha" in result
        assert "beta" in result

    def test_single_string(self):
        result = to_string_list("single")
        assert result == ["single"]

    def test_string_with_whitespace(self):
        result = to_string_list("  alpha  ")
        assert result == ["alpha"]

    def test_empty_list(self):
        result = to_string_list([])
        assert result == []

    def test_empty_string(self):
        result = to_string_list("")
        assert result == []

    def test_whitespace_only(self):
        result = to_string_list("   ")
        assert result == []

    def test_mixed_whitespace_items(self):
        result = to_string_list(["  alpha  ", "  beta", "gamma  "])
        assert result == ["alpha", "beta", "gamma"]


class TestMergeContextOverride:
    """Tests for merge_context_override function."""

    def test_empty_base(self):
        result = merge_context_override(None, {"key": "value"})
        assert result["key"] == "value"

    def test_empty_overlay(self):
        base = {"key": "value"}
        result = merge_context_override(base, None)
        assert result == base

    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = merge_context_override(base, overlay)
        assert result["a"] == 1
        assert result["b"] == 3
        assert result["c"] == 4

    def test_state_first_context_os_nested_merge(self):
        base = {
            "state_first_context_os": {
                "inner_key": {"existing": "value"},
                "top_key": "base_top",
            }
        }
        overlay = {
            "state_first_context_os": {
                "inner_key": {"new": "value"},
                "overlay_key": "new",
            }
        }
        result = merge_context_override(base, overlay)
        # overlay's inner_key should win, but preserve nested merge behavior
        assert "inner_key" in result["state_first_context_os"]
        assert "overlay_key" in result["state_first_context_os"]

    def test_cognitive_runtime_handoff_base_takes_precedence(self):
        # For cognitive_runtime_handoff, base values are merged last
        # (base overwrites overlay) - reverse of other keys
        base = {"cognitive_runtime_handoff": {"base_key": "base_value", "shared": "base"}}
        overlay = {"cognitive_runtime_handoff": {"overlay_key": "overlay_value", "shared": "overlay"}}
        result = merge_context_override(base, overlay)
        # Base takes precedence - shared key should be "base"
        assert result["cognitive_runtime_handoff"]["shared"] == "base"
        assert result["cognitive_runtime_handoff"]["base_key"] == "base_value"
        assert result["cognitive_runtime_handoff"]["overlay_key"] == "overlay_value"

    def test_both_empty(self):
        result = merge_context_override(None, None)
        assert result == {}

    def test_preserves_keys_not_in_overlay(self):
        base = {"keep_me": 1, "modify_me": 2}
        overlay = {"modify_me": 999}
        result = merge_context_override(base, overlay)
        assert result["keep_me"] == 1
        assert result["modify_me"] == 999


class TestAugmentContextWithRepoIntelligence:
    """Tests for augment_context_with_repo_intelligence function."""

    def test_non_code_domain_returns_unchanged(self):
        result = augment_context_with_repo_intelligence(
            workspace="/ws",
            domain="document",
            context={},
            metadata={},
        )
        assert result == {}

    def test_research_domain_triggers_repo_intel(self):
        # Note: This may fail to actually inject repo_intelligence since
        # get_repo_intelligence may not be available, but the function
        # should gracefully handle that case.
        result = augment_context_with_repo_intelligence(
            workspace="/ws",
            domain="research",
            context={"use_repo_intelligence": True},
            metadata={},
        )
        # Should not raise, even if repo_intel is unavailable
        assert isinstance(result, dict)

    def test_code_domain_without_flags_returns_unchanged(self):
        result = augment_context_with_repo_intelligence(
            workspace="/ws",
            domain="code",
            context={},
            metadata={},
        )
        # Without use_repo_intelligence flag or file/ident hints, returns unchanged
        assert result == {}

    def test_code_domain_with_chat_files(self):
        result = augment_context_with_repo_intelligence(
            workspace="/ws",
            domain="code",
            context={"chat_files": ["src/main.py"]},
            metadata={},
        )
        # Should attempt repo intelligence if files are mentioned
        assert isinstance(result, dict)


class TestLoadSessionContextOsSnapshot:
    """Tests for load_session_context_os_snapshot function."""

    def test_empty_session_id_returns_unchanged(self):
        result = load_session_context_os_snapshot(
            session_id="",
            workspace="/ws",
            role="director",
            context_override={},
        )
        assert result == {}

    def test_whitespace_session_id_returns_unchanged(self):
        result = load_session_context_os_snapshot(
            session_id="   ",
            workspace="/ws",
            role="director",
            context_override={},
        )
        assert result == {}

    def test_valid_session_id_returns_dict(self):
        # RoleSessionService may not be available, but function should
        # gracefully handle and return context_override unchanged
        result = load_session_context_os_snapshot(
            session_id="nonexistent-session-123",
            workspace="/ws",
            role="director",
            context_override={"existing": "value"},
        )
        # Should return the context_override (possibly augmented or unchanged)
        assert isinstance(result, dict)


class TestAugmentContextWithHandoffRehydration:
    """Tests for augment_context_with_handoff_rehydration function."""

    def test_no_handoff_id_returns_unchanged(self):
        result = augment_context_with_handoff_rehydration(
            workspace="/ws",
            role="director",
            session_id=None,
            context={},
            metadata={},
        )
        # No handoff_id in context or metadata, returns unchanged
        assert result == ({}, {})

    def test_handoff_id_in_metadata(self):
        result = augment_context_with_handoff_rehydration(
            workspace="/ws",
            role="director",
            session_id="session-1",
            context={},
            metadata={"handoff_id": "nonexistent-handoff"},
        )
        # Should gracefully handle missing handoff and return unchanged
        assert isinstance(result, tuple)
        ctx, meta = result
        assert isinstance(ctx, dict)
        assert isinstance(meta, dict)

    def test_handoff_id_in_context(self):
        result = augment_context_with_handoff_rehydration(
            workspace="/ws",
            role="director",
            session_id="session-1",
            context={"handoff_id": "nonexistent-handoff"},
            metadata={},
        )
        assert isinstance(result, tuple)

    def test_cognitive_runtime_handoff_payload(self):
        result = augment_context_with_handoff_rehydration(
            workspace="/ws",
            role="director",
            session_id=None,
            context={"cognitive_runtime_handoff": {"handoff_id": "handoff-123"}},
            metadata={},
        )
        assert isinstance(result, tuple)
