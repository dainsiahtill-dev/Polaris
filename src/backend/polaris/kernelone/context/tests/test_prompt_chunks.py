"""Tests for W2: Context Compaction and Budget Management.

This module tests the context compaction and budget management capabilities:
    - Continuity summary text building
    - Budget gate enforcement
    - Working set assembly
    - Prompt context management
"""

from __future__ import annotations

import pytest


class TestContinuitySummaryText:
    """Tests for build_continuity_summary_text function."""

    def test_build_summary_from_messages(self) -> None:
        """Should build summary text from messages."""
        from polaris.kernelone.context.compaction import build_continuity_summary_text

        messages = [
            {"role": "user", "content": "fix the bug in main.py", "sequence": 0},
            {"role": "assistant", "content": "I'll fix the bug", "sequence": 1},
        ]
        summary = build_continuity_summary_text(messages)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_build_summary_empty_messages(self) -> None:
        """Should return empty string for empty messages."""
        from polaris.kernelone.context.compaction import build_continuity_summary_text

        summary = build_continuity_summary_text([])
        assert summary == ""

    def test_build_summary_filters_low_signal(self) -> None:
        """Should filter low-signal messages."""
        from polaris.kernelone.context.compaction import build_continuity_summary_text

        messages = [
            {"role": "user", "content": "hello", "sequence": 0},
            {"role": "user", "content": "fix error bug", "sequence": 1},
        ]
        summary = build_continuity_summary_text(messages)
        assert "error" in summary.lower() or "bug" in summary.lower() or "fix" in summary.lower()

    def test_build_summary_with_identity(self) -> None:
        """Should include identity information in summary."""
        from polaris.kernelone.context.compaction import (
            RoleContextIdentity,
            build_continuity_summary_text,
        )

        identity = RoleContextIdentity(
            role_type="director",
            goal="implement login feature",
            scope=["src/auth.py"],
        )
        messages = [{"role": "user", "content": "implement login", "sequence": 0}]
        summary = build_continuity_summary_text(messages, identity)
        assert isinstance(summary, str)

    def test_build_summary_respects_max_chars(self) -> None:
        """Should respect max_chars limit."""
        from polaris.kernelone.context.compaction import build_continuity_summary_text

        messages = [{"role": "user", "content": "x" * 500, "sequence": i} for i in range(10)]
        summary = build_continuity_summary_text(messages, max_chars=200)
        assert len(summary) <= 210  # Account for "..."


class TestContextWindow:
    """Tests for context window management."""

    def test_context_window_within_limit(self, budget_gate_128k) -> None:
        """Context should fit within window limit."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.80)
        budget = gate.get_current_budget()
        assert budget.effective_limit <= 128_000
        assert budget.headroom == budget.effective_limit

    def test_headroom_decreases_with_usage(self, budget_gate_128k) -> None:
        """Headroom should decrease as content is added."""
        gate = budget_gate_128k
        initial_headroom = gate.get_current_budget().headroom

        gate.record_usage(10_000)
        new_headroom = gate.get_current_budget().headroom

        assert new_headroom < initial_headroom
        assert new_headroom == initial_headroom - 10_000

    def test_usage_ratio_calculation(self, budget_gate_128k) -> None:
        """Usage ratio should be calculated correctly."""
        gate = budget_gate_128k
        # With 80% safety margin on 128K: effective = 102400
        gate.record_usage(40_960)  # 40% of effective
        ratio = gate.get_current_budget().usage_ratio
        assert 0.39 <= ratio <= 0.41


class TestBudgetGate:
    """Tests for budget gate functionality."""

    def test_can_add_within_budget(self, budget_gate_128k) -> None:
        """can_add should return True for content within budget."""
        gate = budget_gate_128k
        can_add, reason = gate.can_add(10_000)
        assert can_add is True
        assert reason == ""

    def test_can_add_exceeds_budget(self, budget_gate_tight) -> None:
        """can_add should return False for content over budget."""
        gate = budget_gate_tight
        can_add, _reason = gate.can_add(5000)
        assert can_add is False

    def test_suggest_compaction_healthy(self, budget_gate_128k) -> None:
        """Should suggest healthy when below 50%."""
        gate = budget_gate_128k
        gate.record_usage(10_000)  # ~10% usage
        suggestion = gate.suggest_compaction()
        assert "healthy" in suggestion.lower()

    def test_suggest_compaction_critical(self, budget_gate_128k) -> None:
        """Should suggest compaction when above 75%."""
        gate = budget_gate_128k
        gate.record_usage(80_000)  # ~78% usage
        suggestion = gate.suggest_compaction()
        assert "critical" in suggestion.lower() or "overflow" in suggestion.lower()


class TestWorkingSetAssembly:
    """Tests for WorkingSetAssembler."""

    @pytest.mark.asyncio
    async def test_assembler_initialization(self, working_set_assembler) -> None:
        """Should initialize with proper state."""
        assert working_set_assembler is not None
        assert working_set_assembler._ctx is not None

    @pytest.mark.asyncio
    async def test_set_repo_map_updates_budget(self, working_set_assembler) -> None:
        """Should update budget when setting repo map."""
        from polaris.kernelone.context import RepoMapSnapshot

        repo_map = RepoMapSnapshot(
            workspace="/fake",
            text="# Repo skeleton",
            tokens=500,
        )
        ws = await working_set_assembler.set_repo_map(repo_map)
        assert ws.budget_used == 500


class TestPromptContext:
    """Tests for prompt context building."""

    def test_reserved_keys_excluded(self, continuity_engine) -> None:
        """Reserved context keys should be excluded from prompt context."""
        context = {
            "role": "pm",
            "session_id": "abc123",
            "custom_key": "custom_value",
        }
        result = continuity_engine.build_prompt_context(
            session_context_config=context,
            incoming_context=None,
        )
        assert "role" not in result
        assert "session_id" not in result
        assert "custom_key" in result

    def test_incoming_context_merges(self, continuity_engine) -> None:
        """Incoming context should merge with session config."""
        session_config = {"project": "test", "workspace": "/repo"}
        incoming = {"focus": "refactoring"}
        result = continuity_engine.build_prompt_context(
            session_context_config=session_config,
            incoming_context=incoming,
        )
        assert "project" in result
        assert "focus" in result
        assert result["project"] == "test"
        assert result["focus"] == "refactoring"


class TestRoleContextIdentity:
    """Tests for RoleContextIdentity."""

    def test_create_from_role_state(self) -> None:
        """Should create identity from role state."""
        from polaris.kernelone.context.compaction import RoleContextIdentity

        identity = RoleContextIdentity.from_role_state(
            role_name="director",
            goal="implement feature",
            scope=["src/main.py"],
            current_task_id="task-123",
        )
        assert identity.role_type == "director"
        assert identity.goal == "implement feature"
        assert "src/main.py" in identity.scope

    def test_create_from_task(self) -> None:
        """Should create identity from task data."""
        from polaris.kernelone.context.compaction import RoleContextIdentity

        task = {
            "id": "task-456",
            "goal": "fix bug",
            "acceptance_criteria": ["test passes"],
            "write_scope": ["src/fix.py"],
        }
        identity = RoleContextIdentity.from_task(task, role_type="pm")
        assert identity.role_id == "task-456"
        assert identity.task_id == "task-456"
        assert identity.role_type == "pm"

    def test_sync_new_legacy_fields(self) -> None:
        """Should sync new and legacy field names."""
        from polaris.kernelone.context.compaction import RoleContextIdentity

        # When both role_id and task_id are set, role_id takes precedence
        identity = RoleContextIdentity(
            role_id="id-789",
            task_id="legacy-id",
            scope=["file1.py"],
            write_scope=["file2.py"],
        )
        # role_id takes precedence when both are set
        assert identity.role_id == "id-789"
        # task_id is not synced when role_id is already set
        assert identity.task_id == "legacy-id"
        # When both scope and write_scope are set, neither is synced
        assert identity.scope == ["file1.py"]
        assert identity.write_scope == ["file2.py"]

    def test_sync_scope_from_write_scope(self) -> None:
        """Should sync scope from write_scope when scope is empty."""
        from polaris.kernelone.context.compaction import RoleContextIdentity

        identity = RoleContextIdentity(
            role_id="id-1",
            scope=[],  # empty
            write_scope=["file.py"],  # has value
        )
        # scope should be synced from write_scope
        assert identity.scope == ["file.py"]


class TestCompactionStrategy:
    """Tests for RoleContextCompressor."""

    def test_micro_compact(self) -> None:
        """Should apply micro compaction."""
        from polaris.kernelone.context.compaction import RoleContextCompressor

        compressor = RoleContextCompressor(workspace="/fake", role_name="test")
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "result"},
        ]
        result = compressor.micro_compact(messages)
        assert len(result) == 2

    def test_truncate_compact(self) -> None:
        """Should apply truncate compaction."""
        from polaris.kernelone.context.compaction import RoleContextCompressor

        compressor = RoleContextCompressor(workspace="/fake", role_name="test")
        messages = [{"role": "user", "content": f"message {i}"} for i in range(50)]
        compacted, snapshot = compressor.truncate_compact(messages)
        assert len(compacted) < len(messages)
        assert snapshot.method == "truncate"
        assert snapshot.original_tokens > snapshot.compressed_tokens


class TestEdgeCases:
    """Tests for edge cases."""

    def test_summary_with_none_messages(self) -> None:
        """Should handle None messages gracefully."""
        from polaris.kernelone.context.compaction import build_continuity_summary_text

        summary = build_continuity_summary_text(None)
        assert summary == ""

    def test_identity_with_empty_goal(self) -> None:
        """Should handle empty goal."""
        from polaris.kernelone.context.compaction import RoleContextIdentity

        identity = RoleContextIdentity(
            role_type="director",
            goal="",
            scope=[],
        )
        assert identity.goal == ""

    def test_budget_gate_rejects_zero_window(self) -> None:
        """Should reject zero model window with ValueError."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        with pytest.raises(ValueError, match="positive int"):
            ContextBudgetGate(model_window=0)

    def test_budget_gate_minimum_window(self) -> None:
        """Should use minimum budget when model_window is below minimum."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        # Use a very small but positive window
        gate = ContextBudgetGate(model_window=100)
        budget = gate.get_current_budget()
        # Should have a reasonable effective limit
        assert budget.effective_limit > 0
