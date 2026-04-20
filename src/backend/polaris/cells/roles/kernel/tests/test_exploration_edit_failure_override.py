"""Test for edit tool failure + read_file cooldown override fix.

BUGFIX: When precision_edit fails (no match), the agent needs read_file to
diagnose the issue. But read_file may be in cooldown, creating a deadlock:
edit fails -> need read_file to diagnose -> read_file blocked by cooldown -> loop

This test verifies that ExplorationToolPolicy allows diagnostic read_file
calls after edit tool failures, bypassing cooldown.
"""

import pytest
from polaris.cells.roles.kernel.internal.policy.layer.core import (
    CanonicalToolCall,
)
from polaris.cells.roles.kernel.internal.policy.layer.exploration import (
    EDIT_TOOLS,
    ExplorationToolPolicy,
)


class TestEditFailureDiagnosticOverride:
    """Test diagnostic read_file override after edit tool failures."""

    def test_edit_failure_allows_diagnostic_read_file(self):
        """After precision_edit fails, read_file should bypass cooldown."""
        policy = ExplorationToolPolicy(
            cooldown_after_calls=2,  # Low threshold for testing
            max_calls_per_tool=10,
        )

        # Simulate 2 read_file calls (reaches cooldown threshold)
        read_file_call = CanonicalToolCall(tool="read_file", args={"path": "test.py"})
        policy.evaluate([read_file_call], task_metadata=None)
        policy.evaluate([read_file_call], task_metadata=None)

        # Verify read_file is now in cooldown
        assert policy.is_in_cooldown("read_file")

        # Simulate precision_edit failure
        last_tool_failed = {"tool": "precision_edit", "failed": True, "error": "No matches found"}

        # read_file should now be allowed (diagnostic override)
        approved, blocked, violations = policy.evaluate(
            [read_file_call],
            task_metadata={"last_tool_failed": last_tool_failed},
        )

        assert len(approved) == 1
        assert len(blocked) == 0
        assert len(violations) == 1
        assert "diagnostic_read_override" in violations[0].reason

    def test_non_edit_failure_does_not_override(self):
        """Non-edit tool failures should not trigger read_file override."""
        policy = ExplorationToolPolicy(
            cooldown_after_calls=2,
            max_calls_per_tool=10,
        )

        # Put read_file in cooldown
        read_file_call = CanonicalToolCall(tool="read_file", args={"path": "test.py"})
        policy.evaluate([read_file_call], task_metadata=None)
        policy.evaluate([read_file_call], task_metadata=None)
        assert policy.is_in_cooldown("read_file")

        # Simulate glob failure (not an edit tool)
        last_tool_failed = {"tool": "glob", "failed": True, "error": "No files found"}

        # read_file should still be blocked
        approved, blocked, violations = policy.evaluate(
            [read_file_call],
            task_metadata={"last_tool_failed": last_tool_failed},
        )

        assert len(approved) == 0
        assert len(blocked) == 1

    def test_edit_tools_list_contains_expected_tools(self):
        """Verify EDIT_TOOLS contains expected edit tools."""
        expected = {
            "precision_edit",
            "apply_patch",
            "edit_file",
            "replace",
            "replace_in_file",
            "write_file",
            "create_file",
        }
        assert expected == EDIT_TOOLS

    def test_diagnostic_override_only_for_file_read_tools(self):
        """Diagnostic override should only apply to file_read category tools."""
        policy = ExplorationToolPolicy(
            cooldown_after_calls=2,
            max_calls_per_tool=10,
        )

        # Put ripgrep (content_search) in cooldown
        grep_call = CanonicalToolCall(tool="ripgrep", args={"pattern": "test"})
        policy.evaluate([grep_call], task_metadata=None)
        policy.evaluate([grep_call], task_metadata=None)
        assert policy.is_in_cooldown("ripgrep")

        # Simulate precision_edit failure
        last_tool_failed = {"tool": "precision_edit", "failed": True, "error": "No matches"}

        # ripgrep should NOT be allowed (not a file_read tool)
        approved, blocked, violations = policy.evaluate(
            [grep_call],
            task_metadata={"last_tool_failed": last_tool_failed},
        )

        assert len(approved) == 0
        assert len(blocked) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
