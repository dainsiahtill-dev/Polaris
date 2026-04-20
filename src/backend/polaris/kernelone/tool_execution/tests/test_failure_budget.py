"""Tests for FailureBudget."""

from __future__ import annotations

from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier
from polaris.kernelone.tool_execution.failure_budget import FailureBudget, FailureDecision, FailureResult


class TestFailureBudget:
    """Tests for FailureBudget."""

    def setup_method(self) -> None:
        """Set up fresh test fixtures for each test."""
        self.classifier = ToolErrorClassifier()
        self.budget = FailureBudget()
        # Clear any cached patterns to ensure test isolation
        self.classifier.clear_cache()

    def test_first_failure_is_allowed(self) -> None:
        """Test that first failure returns ALLOW."""
        pattern = self.classifier.classify("precision_edit", "no matches found")
        result = self.budget.record_failure(pattern)

        assert isinstance(result, FailureResult)
        assert result.decision == FailureDecision.ALLOW
        assert result.suggestion is None
        assert result.retryable is False  # write-tool no_match is NOT retryable
        assert result.blocked is False
        assert self.budget.get_tool_failure_count("precision_edit") == 1

    def test_second_same_pattern_is_allowed(self) -> None:
        """Test that second same pattern still returns ALLOW (need pattern_count > max_same_pattern = 2)."""
        # Use unique error message to avoid cache interference
        pattern = self.classifier.classify("precision_edit", f"no matches found #{id(self)}")

        # First failure - ALLOW
        result1 = self.budget.record_failure(pattern)
        assert result1.decision == FailureDecision.ALLOW
        assert self.budget.get_pattern_failure_count(pattern.error_signature) == 1

        # Second failure - still ALLOW (pattern_count = 2, not > 2)
        result2 = self.budget.record_failure(pattern)
        assert result2.decision == FailureDecision.ALLOW
        assert self.budget.get_pattern_failure_count(pattern.error_signature) == 2

    def test_third_same_pattern_escalates(self) -> None:
        """Test that third same pattern returns ESCALATE (pattern_count = 3 > max_same_pattern = 2)."""
        pattern = self.classifier.classify("precision_edit", f"no matches found #{id(self)}")

        # Three failures
        self.budget.record_failure(pattern)
        self.budget.record_failure(pattern)
        result = self.budget.record_failure(pattern)

        assert result.decision == FailureDecision.ESCALATE
        assert result.suggestion is not None
        assert "WARNING" in result.suggestion
        assert result.retryable is False  # write-tool no_match is NOT retryable

    def test_fourth_same_pattern_is_block(self) -> None:
        """Test that fourth same pattern returns BLOCK (tool count > max_failures_per_tool)."""
        # Use unique error message
        pattern = self.classifier.classify("precision_edit", f"no matches found #{id(self)}")

        # Four failures
        self.budget.record_failure(pattern)
        self.budget.record_failure(pattern)
        self.budget.record_failure(pattern)
        result = self.budget.record_failure(pattern)

        assert result.decision == FailureDecision.BLOCK
        assert result.suggestion is not None
        assert "BLOCKED" in result.suggestion
        assert result.retryable is False  # BLOCK means not retryable
        assert result.blocked is True

    def test_different_tools_independent_counters(self) -> None:
        """Test that different tools have independent failure counters."""
        p_edit = self.classifier.classify("precision_edit", f"no matches found edit #{id(self)}")
        p_read = self.classifier.classify("read_file", f"not found #{id(self)}")

        # 3 failures for precision_edit
        for _ in range(3):
            self.budget.record_failure(p_edit)

        # First failure for read_file - ALLOW
        self.budget.record_failure(p_read)
        # Second failure for read_file - ALLOW (pattern_count=2, not > 2)
        result = self.budget.record_failure(p_read)
        assert result.decision == FailureDecision.ALLOW

        assert self.budget.get_tool_failure_count("precision_edit") == 3
        assert self.budget.get_tool_failure_count("read_file") == 2

    def test_get_stats(self) -> None:
        """Test stats retrieval."""
        pattern = self.classifier.classify("edit", f"no matches found #{id(self)}")
        self.budget.record_failure(pattern)
        self.budget.record_failure(pattern)

        stats = self.budget.get_stats()

        assert stats["total_failures"] == 2
        assert stats["tool_failures"]["edit"] == 2
        assert stats["pattern_failures"] == 1
        assert stats["blocked_tools"] == []

    def test_is_tool_blocked(self) -> None:
        """Test tool blocking check - requires tool_count > max_failures_per_tool."""
        pattern = self.classifier.classify("edit", f"no matches found #{id(self)}")

        assert not self.budget.is_tool_blocked("edit")

        # 4 failures needed to block (max_failures_per_tool = 3)
        for _ in range(4):
            self.budget.record_failure(pattern)

        assert self.budget.is_tool_blocked("edit")
        assert self.budget.get_tool_failure_count("edit") == 4

    def test_get_blocked_tools(self) -> None:
        """Test blocked tools list."""
        p1 = self.classifier.classify("edit", f"no matches found #{id(self)}")
        p2 = self.classifier.classify("read", f"timeout error #{id(self)}")

        # 4 failures for edit (blocks)
        for _ in range(4):
            self.budget.record_failure(p1)
        # 3 failures for read (does NOT block - equals max)
        for _ in range(3):
            self.budget.record_failure(p2)

        blocked = self.budget.get_blocked_tools()
        assert "edit" in blocked
        assert "read" not in blocked

    def test_reset(self) -> None:
        """Test budget reset."""
        pattern = self.classifier.classify("edit", f"no matches found #{id(self)}")
        self.budget.record_failure(pattern)
        self.budget.record_failure(pattern)

        assert self.budget.get_total_failure_count() == 2

        self.budget.reset()

        assert self.budget.get_total_failure_count() == 0
        assert self.budget.get_tool_failure_count("edit") == 0
        assert self.budget.is_tool_blocked("edit") is False

    def test_error_type_in_result(self) -> None:
        """Test that error_type is correctly propagated to result."""
        pattern = self.classifier.classify("edit", "no matches found")
        result = self.budget.record_failure(pattern)

        assert result.error_type == "no_match"
        assert result.tool_name == "edit"
        assert result.pattern_signature == pattern.error_signature

    def test_retryable_for_timeout(self) -> None:
        """Test that timeout errors are marked as retryable."""
        pattern = self.classifier.classify("execute", "operation timed out")
        result = self.budget.record_failure(pattern)

        assert result.error_type == "timeout"
        assert result.retryable is True

    def test_not_retryable_for_not_found(self) -> None:
        """Test that not_found errors are marked as not retryable."""
        pattern = self.classifier.classify("read", "file not found")
        result = self.budget.record_failure(pattern)

        assert result.error_type == "not_found"
        assert result.retryable is False

    def test_no_match_write_tool_not_retryable(self) -> None:
        """Test that no_match for write tools is NOT retryable."""
        pattern = self.classifier.classify("write_file", "no matches found")
        result = self.budget.record_failure(pattern)

        assert result.error_type == "no_match"
        assert result.retryable is False

    def test_no_match_read_tool_retryable(self) -> None:
        """Test that no_match for read-only tools remains retryable."""
        pattern = self.classifier.classify("read_file", "no matches found")
        result = self.budget.record_failure(pattern)

        assert result.error_type == "no_match"
        assert result.retryable is True


class TestFailureBudgetSessionIsolation:
    """Tests for FailureBudget session isolation."""

    def setup_method(self) -> None:
        """Set up fresh test fixtures for each test."""
        self.classifier = ToolErrorClassifier()
        self.classifier.clear_cache()

    def test_for_session_creates_separate_instances(self) -> None:
        """Test that for_session creates separate budget instances per session."""
        budget1 = FailureBudget.for_session("session1")
        budget2 = FailureBudget.for_session("session2")

        assert budget1 is not budget2
        assert budget1.session_id == "session1"
        assert budget2.session_id == "session2"

    def test_session_isolation_failure_counters(self) -> None:
        """Test that different sessions have isolated failure counters."""
        budget1 = FailureBudget.for_session("session1")
        budget2 = FailureBudget.for_session("session2")

        pattern1 = self.classifier.classify("edit", f"no matches session1 {id(self)}")
        pattern2 = self.classifier.classify("edit", f"no matches session2 {id(self)}")

        budget1.record_failure(pattern1)
        budget1.record_failure(pattern1)
        budget2.record_failure(pattern2)

        assert budget1.get_tool_failure_count("edit") == 2
        assert budget2.get_tool_failure_count("edit") == 1

    def test_session_reset_clears_only_owned_counters(self) -> None:
        """Test that reset only clears the session's own counters."""
        import uuid

        session1_id = f"session1_{uuid.uuid4().hex[:8]}"
        session2_id = f"session2_{uuid.uuid4().hex[:8]}"

        budget1 = FailureBudget.for_session(session1_id)
        budget2 = FailureBudget.for_session(session2_id)

        pattern1 = self.classifier.classify("edit", f"no matches {uuid.uuid4().hex[:8]}")
        pattern2 = self.classifier.classify("edit", f"no matches {uuid.uuid4().hex[:8]}")

        budget1.record_failure(pattern1)
        budget2.record_failure(pattern2)

        budget1.reset()

        assert budget1.get_total_failure_count() == 0
        assert budget2.get_total_failure_count() == 1

    def test_for_session_same_id_returns_same_instance(self) -> None:
        """Test that calling for_session with same ID returns the same instance."""
        budget1 = FailureBudget.for_session("session_x")
        budget2 = FailureBudget.for_session("session_x")

        assert budget1 is budget2
