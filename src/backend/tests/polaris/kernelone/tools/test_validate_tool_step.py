"""Integration tests for validate_tool_step."""

from __future__ import annotations

import pytest
from polaris.kernelone.tool_execution.contracts import validate_tool_step


@pytest.fixture(autouse=True)
def _populate_tool_registry():
    """Repopulate ToolSpecRegistry after reset_singletons clears it."""
    from polaris.kernelone.llm.toolkit.tool_normalization import schema_driven_normalizer
    from polaris.kernelone.tool_execution.tool_spec_registry import migrate_from_contracts_specs

    migrate_from_contracts_specs()
    schema_driven_normalizer._normalizer_instance = None


class TestValidateToolStep:
    """Tool step validation integration tests."""

    # -------------------------------------------------------------------------
    # Valid tool calls - repo_rg
    # -------------------------------------------------------------------------

    def test_repo_rg_with_valid_pattern(self) -> None:
        """Test repo_rg with valid pattern passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"pattern": "test"}
        )
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_repo_rg_with_pattern_and_path(self) -> None:
        """Test repo_rg with pattern and path passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"pattern": "class", "path": "src/"}
        )
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_repo_rg_with_max_results(self) -> None:
        """Test repo_rg with max_results passes (no range validation in validate_tool_step)."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"pattern": "test", "max_results": 10000}
        )
        # Note: validate_tool_step does not enforce max_results range
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_repo_rg_alias_resolved(self) -> None:
        """Test repo_rg accepts alias 'grep'."""
        is_valid, error_code, error_msg = validate_tool_step(
            "grep",
            {"pattern": "test"}
        )
        assert is_valid is True
        assert error_code is None

    # -------------------------------------------------------------------------
    # Valid tool calls - repo_read_head
    # -------------------------------------------------------------------------

    def test_repo_read_head_with_valid_file(self) -> None:
        """Test repo_read_head with valid file passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_read_head",
            {"file": "test.py"}
        )
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_repo_read_head_with_valid_n(self) -> None:
        """Test repo_read_head with valid n passes (no range validation in validate_tool_step)."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_read_head",
            {"file": "test.py", "n": 50000}
        )
        # Note: validate_tool_step does not enforce n range
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_repo_read_head_alias_resolved(self) -> None:
        """Test repo_read_head accepts alias 'read_file'."""
        is_valid, error_code, error_msg = validate_tool_step(
            "read_file",
            {"file": "test.py"}
        )
        assert is_valid is True
        assert error_code is None

    # -------------------------------------------------------------------------
    # Valid tool calls - other tools
    # -------------------------------------------------------------------------

    def test_repo_tree_valid(self) -> None:
        """Test repo_tree with optional path passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_tree",
            {"path": "src/"}
        )
        assert is_valid is True
        assert error_code is None

    def test_repo_diff_valid(self) -> None:
        """Test repo_diff with no required args passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_diff",
            {}
        )
        assert is_valid is True
        assert error_code is None

    def test_todo_read_valid(self) -> None:
        """Test todo_read with no args passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "todo_read",
            {}
        )
        assert is_valid is True
        assert error_code is None

    # -------------------------------------------------------------------------
    # Invalid - missing required args
    # -------------------------------------------------------------------------

    def test_repo_rg_missing_pattern(self) -> None:
        """Test repo_rg without pattern fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"path": "."}
        )
        assert is_valid is False
        assert error_code == "REQUIRED_MISSING"
        assert "pattern" in error_msg

    def test_repo_rg_empty_pattern(self) -> None:
        """Test repo_rg with empty pattern fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"pattern": ""}
        )
        assert is_valid is False
        assert error_code == "MIN_LENGTH_VIOLATION"

    def test_repo_read_head_missing_file(self) -> None:
        """Test repo_read_head without file fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_read_head",
            {"n": 10}
        )
        assert is_valid is False
        assert error_code == "REQUIRED_MISSING"
        assert "file" in error_msg

    def test_repo_read_head_empty_file(self) -> None:
        """Test repo_read_head with whitespace-only file passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_read_head",
            {"file": "  "}
        )
        assert is_valid is True
        assert error_code is None

    def test_precision_edit_missing_required(self) -> None:
        """Test precision_edit without required args fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "precision_edit",
            {"file": "test.py"}
        )
        assert is_valid is False
        assert error_code == "REQUIRED_MISSING"

    # -------------------------------------------------------------------------
    # Invalid - unknown tool
    # -------------------------------------------------------------------------

    def test_unknown_tool(self) -> None:
        """Test unknown tool returns UNKNOWN_TOOL error."""
        is_valid, error_code, error_msg = validate_tool_step(
            "unknown_tool",
            {}
        )
        assert is_valid is False
        assert error_code == "UNKNOWN_TOOL"
        assert "unknown_tool" in error_msg
        assert "Allowed:" in error_msg

    def test_empty_tool_name(self) -> None:
        """Test empty tool name returns UNKNOWN_TOOL error."""
        is_valid, error_code, error_msg = validate_tool_step(
            "",
            {}
        )
        assert is_valid is False
        assert error_code == "UNKNOWN_TOOL"

    # -------------------------------------------------------------------------
    # Invalid - background_run timeout validation
    # -------------------------------------------------------------------------

    def test_background_run_negative_timeout(self) -> None:
        """Test background_run with negative timeout fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "background_run",
            {"command": "echo hi", "timeout": -1}
        )
        assert is_valid is False
        assert error_code == "INVALID_TOOL_ARGS"
        assert "greater than 0" in error_msg

    def test_background_run_zero_timeout(self) -> None:
        """Test background_run with zero timeout fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "background_run",
            {"command": "echo hi", "timeout": 0}
        )
        assert is_valid is False
        assert error_code == "INVALID_TOOL_ARGS"
        assert "greater than 0" in error_msg

    def test_background_run_exceeds_max_timeout(self) -> None:
        """Test background_run with timeout > 3600 fails validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "background_run",
            {"command": "echo hi", "timeout": 3601}
        )
        assert is_valid is False
        assert error_code == "INVALID_TOOL_ARGS"
        assert "less than or equal to 3600" in error_msg

    def test_background_run_valid_min_timeout(self) -> None:
        """Test background_run with minimum valid timeout passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "background_run",
            {"command": "echo hi", "timeout": 1}
        )
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_background_run_valid_max_timeout(self) -> None:
        """Test background_run with maximum valid timeout passes validation."""
        is_valid, error_code, error_msg = validate_tool_step(
            "background_run",
            {"command": "echo hi", "timeout": 3600}
        )
        assert is_valid is True
        assert error_code is None
        assert error_msg == ""

    def test_background_run_default_timeout(self) -> None:
        """Test background_run without timeout uses default 300 and passes."""
        is_valid, error_code, error_msg = validate_tool_step(
            "background_run",
            {"command": "echo hi"}
        )
        assert is_valid is True
        assert error_code is None

    # -------------------------------------------------------------------------
    # Edge cases - None/non-dict args handling
    # -------------------------------------------------------------------------

    def test_none_args(self) -> None:
        """Test None args is treated as empty dict."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            None
        )
        # repo_rg requires pattern, so should fail
        assert is_valid is False
        assert error_code == "REQUIRED_MISSING"

    def test_non_dict_args(self) -> None:
        """Test non-dict args is treated as empty dict."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            "not a dict"
        )
        # repo_rg requires pattern, so should fail
        assert is_valid is False
        assert error_code == "REQUIRED_MISSING"

    # -------------------------------------------------------------------------
    # Arg alias normalization
    # -------------------------------------------------------------------------

    def test_repo_rg_query_alias_normalized(self) -> None:
        """Test repo_rg 'query' alias is normalized to 'pattern'."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"query": "test"}  # query should be normalized to pattern
        )
        assert is_valid is True
        assert error_code is None

    def test_repo_rg_q_alias_normalized(self) -> None:
        """Test repo_rg 'q' alias is normalized to 'pattern'."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_rg",
            {"q": "test"}  # q should be normalized to pattern
        )
        assert is_valid is True
        assert error_code is None

    def test_repo_read_head_lines_alias_normalized(self) -> None:
        """Test repo_read_head 'lines' alias is normalized to 'n'."""
        is_valid, error_code, error_msg = validate_tool_step(
            "repo_read_head",
            {"file": "test.py", "lines": 10}  # lines should be normalized to n
        )
        assert is_valid is True
        assert error_code is None
