"""Tool argument normalization tests.

Tests that verify tool arguments are correctly normalized including:
- Pattern/query preservation (regex metacharacters like trailing spaces)
- Alias mappings (query->pattern, file->path, etc.)
- Type coercion (int, bool)
- Default values

Run with: pytest polaris/kernelone/llm/toolkit/tests/test_tools_normalization.py -v
"""

import pytest
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments


class TestRepoRgNormalization:
    """Test repo_rg argument normalization."""

    def test_pattern_preserves_trailing_space(self) -> None:
        """Pattern '^def ' should NOT lose trailing space."""
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "^def "})
        assert normalized["pattern"] == "^def ", f"Expected '^def ' but got '{normalized.get('pattern')}'"

    def test_pattern_preserves_leading_space(self) -> None:
        """Pattern ' def' should NOT lose leading space."""
        normalized = normalize_tool_arguments("repo_rg", {"pattern": " def"})
        assert normalized["pattern"] == " def"

    def test_pattern_preserves_multiple_spaces(self) -> None:
        """Pattern 'def  ' (multiple trailing spaces) should be preserved."""
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "def  "})
        assert normalized["pattern"] == "def  "

    def test_regex_metacharacters_preserved(self) -> None:
        """Regex metacharacters should not be modified."""
        patterns = [
            "^def ",  # anchor with space
            "$end",  # end anchor
            "class|def",  # alternation
            "[a-z]+",  # character class
            "foo.*bar",  # any char
            "a+b?",  # quantifiers
            "(?:foo)",  # non-capturing group
        ]
        for p in patterns:
            normalized = normalize_tool_arguments("repo_rg", {"pattern": p})
            assert normalized["pattern"] == p, f"Pattern '{p}' was modified to '{normalized.get('pattern')}'"

    def test_query_alias_maps_to_pattern(self) -> None:
        """query alias should map to pattern."""
        normalized = normalize_tool_arguments("repo_rg", {"query": "foo bar"})
        assert "pattern" in normalized
        assert normalized["pattern"] == "foo bar"

    def test_text_alias_maps_to_pattern(self) -> None:
        """text alias should map to pattern."""
        normalized = normalize_tool_arguments("repo_rg", {"text": "test pattern"})
        assert "pattern" in normalized
        assert normalized["pattern"] == "test pattern"

    def test_file_alias_maps_to_path(self) -> None:
        """file alias should map to path."""
        normalized = normalize_tool_arguments("repo_rg", {"file": "src/main.py"})
        assert "path" in normalized
        assert "query" not in normalized  # alias should be removed

    def test_max_alias_maps_to_max_results(self) -> None:
        """max alias should map to max_results."""
        normalized = normalize_tool_arguments("repo_rg", {"max": 10})
        assert "max_results" in normalized
        assert normalized["max_results"] == 10

    def test_limit_alias_maps_to_max_results(self) -> None:
        """limit alias should map to max_results."""
        normalized = normalize_tool_arguments("repo_rg", {"limit": 25})
        assert "max_results" in normalized
        assert normalized["max_results"] == 25

    def test_context_alias_maps_to_context_lines(self) -> None:
        """context alias should map to context_lines."""
        normalized = normalize_tool_arguments("repo_rg", {"context": 3})
        assert "context_lines" in normalized
        assert normalized["context_lines"] == 3

    def test_g_alias_maps_to_glob(self) -> None:
        """g alias should map to glob."""
        normalized = normalize_tool_arguments("repo_rg", {"g": "*.py"})
        assert "glob" in normalized
        assert normalized["glob"] == "*.py"

    def test_max_results_clamped_to_valid_range(self) -> None:
        """max_results should be clamped to [1, 10000]."""
        # Too high
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "test", "max_results": 99999})
        assert normalized["max_results"] == 10000

        # Too low
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "test", "max_results": 0})
        assert normalized["max_results"] == 1

        # Negative
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "test", "max_results": -5})
        assert normalized["max_results"] == 1

    def test_context_lines_clamped_to_valid_range(self) -> None:
        """context_lines should be clamped to [0, 100]."""
        # Too high
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "test", "context_lines": 999})
        assert normalized["context_lines"] == 100

        # Negative
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "test", "context_lines": -1})
        assert normalized["context_lines"] == 0

    def test_defaults_set(self) -> None:
        """Default values should be set."""
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "test"})
        assert normalized.get("max_results") == 50
        assert normalized.get("context_lines") == 0


class TestSearchCodeNormalization:
    """Test search_code/ripgrep/grep argument normalization."""

    def test_query_preserves_trailing_space(self) -> None:
        """Query '^def ' should NOT lose trailing space."""
        normalized = normalize_tool_arguments("search_code", {"query": "^def "})
        assert normalized["pattern"] == "^def ", f"Expected '^def ' but got '{normalized.get('pattern')}'"

    def test_query_preserves_leading_space(self) -> None:
        """Query ' def' should NOT lose leading space."""
        normalized = normalize_tool_arguments("search_code", {"query": " def"})
        assert normalized["pattern"] == " def"

    def test_q_alias_preserves_spaces(self) -> None:
        """q alias with spaces should be preserved."""
        normalized = normalize_tool_arguments("search_code", {"q": "foo bar"})
        assert normalized["pattern"] == "foo bar"

    def test_pattern_alias_maps_to_query(self) -> None:
        """pattern alias should map to pattern (search_code canonical)."""
        normalized = normalize_tool_arguments("search_code", {"pattern": "test"})
        assert "pattern" in normalized
        assert normalized["pattern"] == "test"

    def test_ripgrep_same_as_search_code(self) -> None:
        """ripgrep should use same normalization as search_code (canonical: pattern)."""
        normalized = normalize_tool_arguments("ripgrep", {"q": "^class "})
        assert normalized["pattern"] == "^class "

    def test_grep_same_as_repo_rg(self) -> None:
        """grep canonicalizes to repo_rg (canonical: pattern)."""
        normalized = normalize_tool_arguments("grep", {"q": "^def "})
        assert normalized["pattern"] == "^def "

    def test_file_path_alias_maps_to_scope(self) -> None:
        """file_path alias should be handled correctly."""
        normalized = normalize_tool_arguments("search_code", {"file_path": "src"})
        # Should not raise, and file_path should be removed
        assert "file_path" not in normalized

    def test_files_alias_not_in_repo_rg(self) -> None:
        """files is not a valid arg_alias for repo_rg; it passes through unchanged."""
        normalized = normalize_tool_arguments("search_code", {"files": ["*.py", "*.js"]})
        # search_code aliases to repo_rg, which has no files arg_alias, so files passes through
        assert "files" in normalized

    def test_paths_string_passed_through(self) -> None:
        """A string paths parameter is passed through as-is (repo_rg uses array)."""
        normalized = normalize_tool_arguments("search_code", {"paths": "*.py"})
        assert "paths" in normalized
        # paths is kept as string - the repo_rg schema expects array but normalization
        # doesn't coerce it; the handler layer validates/coerces at runtime
        assert normalized["paths"] == "*.py"

    def test_path_string_passes_through(self) -> None:
        """A string path parameter is kept as-is for repo_rg."""
        normalized = normalize_tool_arguments("search_code", {"path": "src/"})
        # path is the repo_rg arg for directory search, kept as string
        assert "path" in normalized
        assert normalized["path"] == "src/"


class TestGlobNormalization:
    """Test glob argument normalization."""

    def test_pattern_preserved(self) -> None:
        """Pattern should be preserved exactly."""
        normalized = normalize_tool_arguments("glob", {"pattern": "**/*.py"})
        assert normalized["pattern"] == "**/*.py"

    def test_glob_alias_normalizes_to_pattern(self) -> None:
        """glob alias should normalize to pattern."""
        normalized = normalize_tool_arguments("glob", {"glob": "src/**/*.ts"})
        assert normalized["pattern"] == "src/**/*.ts"


class TestListDirectoryNormalization:
    """Test list_directory argument normalization."""

    def test_path_preserved(self) -> None:
        """Path should be preserved."""
        normalized = normalize_tool_arguments("list_directory", {"path": "src"})
        assert normalized["path"] == "src"

    def test_directory_alias_maps_to_path(self) -> None:
        """directory alias should map to path."""
        normalized = normalize_tool_arguments("list_directory", {"directory": "src"})
        assert "path" in normalized

    def test_recursive_alias_handled(self) -> None:
        """recursive alias should be handled."""
        normalized = normalize_tool_arguments("list_directory", {"path": "src", "recursive": True})
        assert normalized.get("recursive") is True


class TestReadFileNormalization:
    """Test read_file argument normalization."""

    def test_file_preserved(self) -> None:
        """File path should be preserved."""
        normalized = normalize_tool_arguments("read_file", {"file": "src/main.py"})
        assert normalized["file"] == "src/main.py"

    def test_n_alias_passed_through(self) -> None:
        """n alias is passed through to read_file (limit is for repo_read_head)."""
        normalized = normalize_tool_arguments("read_file", {"file": "test.py", "n": 10})
        # n is not remapped for read_file - it's passed as-is
        assert normalized["n"] == 10

    def test_home_user_project_prefix_maps_to_workspace_relative(self) -> None:
        """Common Linux pseudo-workspace prefixes should normalize to relative paths."""
        normalized = normalize_tool_arguments("read_file", {"file": "/home/user/project/src/main.py"})
        assert normalized["file"] == "src/main.py"

    def test_project_prefix_maps_to_workspace_relative(self) -> None:
        """`/project/...` should normalize to workspace-relative path."""
        normalized = normalize_tool_arguments("read_file", {"file": "/project/README.md"})
        assert normalized["file"] == "README.md"

    def test_home_user_repo_prefix_maps_to_workspace_relative(self) -> None:
        """`/home/user/repo/...` should normalize to workspace-relative path."""
        normalized = normalize_tool_arguments("read_file", {"file": "/home/user/repo/game.js"})
        assert normalized["file"] == "game.js"

    def test_repo_prefix_maps_to_workspace_relative(self) -> None:
        """`/repo/...` should normalize to workspace-relative path."""
        normalized = normalize_tool_arguments("read_file", {"file": "/repo/src/main.py"})
        assert normalized["file"] == "src/main.py"

    def test_app_prefix_maps_to_workspace_relative(self) -> None:
        """`/app/...` should normalize to workspace-relative path."""
        normalized = normalize_tool_arguments("read_file", {"file": "/app/index.html"})
        assert normalized["file"] == "index.html"


class TestSearchReplaceNormalization:
    """Test search_replace argument normalization."""

    def test_search_and_replace_preserved(self) -> None:
        """Search and replace should be preserved."""
        normalized = normalize_tool_arguments("search_replace", {"search": "old text", "replace": "new text"})
        assert normalized["search"] == "old text"
        assert normalized["replace"] == "new text"


class TestExecuteCommandNormalization:
    """Test execute_command argument normalization."""

    def test_command_preserved(self) -> None:
        """Command should be preserved."""
        normalized = normalize_tool_arguments("execute_command", {"command": "ls -la"})
        assert normalized["command"] == "ls -la"

    def test_run_command_is_tool_name_alias(self) -> None:
        """run_command is a tool name alias (not argument alias) for execute_command."""
        # run_command normalizes TOOL NAME to execute_command, but run_command as argument is not an alias for command
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        assert normalize_tool_name("run_command") == "execute_command"


class TestToolNameNormalization:
    """Test tool name normalization."""

    def test_run_command_normalizes_to_execute_command(self) -> None:
        """run_command should normalize to execute_command."""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        assert normalize_tool_name("run_command") == "execute_command"

    def test_run_shell_normalizes_to_execute_command(self) -> None:
        """run_shell should normalize to execute_command."""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        assert normalize_tool_name("run_shell") == "execute_command"

    def test_canonical_names_unchanged(self) -> None:
        """Canonical tool names should remain unchanged."""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        # search_code and list_directory are now aliases, not canonical
        canonical = ["repo_rg", "glob", "repo_tree", "read_file"]
        for name in canonical:
            assert normalize_tool_name(name) == name

    def test_aliases_normalize_to_canonical(self) -> None:
        """Aliases should normalize to their canonical tool names."""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        assert normalize_tool_name("search_code") == "repo_rg"
        assert normalize_tool_name("grep") == "repo_rg"
        assert normalize_tool_name("ripgrep") == "repo_rg"
        assert normalize_tool_name("list_directory") == "repo_tree"
        assert normalize_tool_name("list_dir") == "repo_tree"


class TestTypeCoercion:
    """Test type coercion for various parameter types."""

    def test_bool_string_coerced_to_true(self) -> None:
        """String 'true' should be coerced to bool True."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_bool

        assert _coerce_bool("true") is True

    def test_bool_string_coerced_to_false(self) -> None:
        """String 'false' should be coerced to bool False."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_bool

        assert _coerce_bool("false") is False

    def test_bool_numeric_strings_coerced(self) -> None:
        """Numeric strings should be coerced to bool."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_bool

        assert _coerce_bool("1") is True
        assert _coerce_bool("0") is False
        assert _coerce_bool("yes") is True
        assert _coerce_bool("no") is False

    def test_int_from_string(self) -> None:
        """String numbers should be coerced to int."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_int

        assert _coerce_int("42") == 42
        assert _coerce_int("-10") == -10

    def test_int_from_float(self) -> None:
        """Float that is whole number should be coerced to int."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_int

        assert _coerce_int(42.0) == 42
        assert _coerce_int(-10.0) == -10

    def test_int_from_string_with_surrounding_text(self) -> None:
        """String with number embedded should extract the number."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_int

        assert _coerce_int("limit 42") == 42
        assert _coerce_int("max_results: 100") == 100

    def test_bool_preserved(self) -> None:
        """Bool values should be preserved as-is."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_bool

        assert _coerce_bool(True) is True
        assert _coerce_bool(False) is False

    def test_int_preserved(self) -> None:
        """Int values should be preserved as-is."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_int

        assert _coerce_int(42) == 42
        assert _coerce_int(-10) == -10

    def test_bool_rejects_bool_input_for_int(self) -> None:
        """Bool should return None for int coercion (prevents True->1 confusion)."""
        from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import _coerce_int

        assert _coerce_int(True) is None
        assert _coerce_int(False) is None

    def test_mixed_types_in_search_code(self) -> None:
        """Search code with mixed types should normalize correctly."""
        # int string for max_results
        normalized = normalize_tool_arguments("search_code", {"q": "test", "max": "50"})
        assert normalized.get("max_results") == 50
        # Bool string for type
        normalized = normalize_tool_arguments("search_code", {"q": "test", "case_sensitive": "true"})
        # case_sensitive should be set (via _coerce_bool)
        assert "case_sensitive" in normalized

    def test_mixed_types_in_glob(self) -> None:
        """Glob with mixed types should normalize correctly."""
        # int string for max_results - coerced via _normalize_int_option
        normalized = normalize_tool_arguments("glob", {"pattern": "*.py", "max": "200"})
        assert normalized.get("max_results") == 200
        # Bool string via alias gets coerced
        normalized = normalize_tool_arguments("glob", {"pattern": "*.py", "recurse": "true"})
        assert normalized.get("recursive") is True

    def test_mixed_types_in_list_directory(self) -> None:
        """List directory with mixed types should normalize correctly."""
        # int string for max_entries
        normalized = normalize_tool_arguments("list_directory", {"path": "src", "max": "100"})
        assert normalized.get("max_entries") == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
