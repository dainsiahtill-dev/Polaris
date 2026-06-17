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

    def test_paths_string_coerced_to_array(self) -> None:
        """A scalar string paths is coerced to a one-element array (ADR-0090).

        repo_rg declares paths as array; weak models routinely send a scalar.
        Before the coercion this passed through and died at execution time with
        "Expected array (list), got str"."""
        normalized = normalize_tool_arguments("search_code", {"paths": "*.py"})
        assert normalized["paths"] == ["*.py"]

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


class TestJsonWrappedArguments:
    """Stage 0 JSON-wrapped argument recovery.

    Weak/provider-compatible models sometimes place the whole argument object in a
    JSON string. This is recoverable only when the parsed keys belong to the
    selected tool's parameter namespace.
    """

    def test_raw_json_object_string_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            '{"file": "app.js", "text": "console.log(1);\\n"}',
        )

        assert normalized["file"] == "app.js"
        assert normalized["content"] == "console.log(1);\n"

    def test_raw_python_literal_object_string_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            "{'file': 'app.js', 'text': 'console.log(1);\\n'}",
        )

        assert normalized["file"] == "app.js"
        assert normalized["content"] == "console.log(1);\n"

    def test_single_object_array_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            [{"path": "/workspace/src/app.py", "start_line": "2"}],
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2

    def test_json_string_single_object_array_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            '[{"path": "/workspace/src/app.py", "start_line": "2"}]',
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2

    def test_python_literal_single_object_array_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            "[{'path': '/workspace/src/app.py', 'start_line': '2'}]",
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2

    def test_multi_object_array_is_not_unwrapped(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            [{"path": "a.py"}, {"path": "b.py"}],
        )

        assert "file" not in normalized

    def test_nested_arguments_json_string_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            {"arguments": '{"path": "/workspace/src/app.py", "start_line": "2", "end_line": "4"}'},
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2
        assert normalized["end_line"] == 4

    def test_nested_arguments_python_literal_string_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            {"arguments": "{'path': '/workspace/src/app.py', 'start_line': '2', 'end_line': '4'}"},
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2
        assert normalized["end_line"] == 4

    def test_nested_arguments_object_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            {"arguments": {"path": "/workspace/src/app.py", "start_line": "2", "end_line": "4"}},
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2
        assert normalized["end_line"] == 4

    def test_tool_argument_wrapper_aliases_unwrap_to_tool_arguments(self) -> None:
        for wrapper_key in ("tool_arguments", "function_arguments", "tool_args", "function_args"):
            normalized = normalize_tool_arguments(
                "read_file",
                {wrapper_key: {"path": "/workspace/src/app.py", "start_line": "2"}},
            )

            assert normalized["file"] == "src/app.py"
            assert normalized["start_line"] == 2

    def test_tool_argument_wrapper_alias_string_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            {"function_arguments": '{"file_path": "app.js", "body": "console.log(1);\\n"}'},
        )

        assert normalized["file"] == "app.js"
        assert normalized["content"] == "console.log(1);\n"

    def test_same_tool_envelope_object_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            {
                "name": "create_file",
                "arguments": {"path": "app.js", "text": "console.log(1);\n"},
            },
        )

        assert normalized == {"file": "app.js", "content": "console.log(1);\n"}

    def test_same_tool_envelope_json_string_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            '{"tool_name": "save_file", "tool_input": {"target_file": "app.js", "body": "console.log(1);\\n"}}',
        )

        assert normalized == {"file": "app.js", "content": "console.log(1);\n"}

    def test_cross_tool_envelope_object_is_not_unwrapped(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            {
                "name": "execute_command",
                "arguments": {"command": "echo should-not-cross-tool"},
            },
        )

        assert normalized == {
            "name": "execute_command",
            "arguments": {"command": "echo should-not-cross-tool"},
        }

    def test_foreign_wrapper_key_with_tool_arguments_is_not_unwrapped(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            {"metadata": {"path": "/workspace/src/app.py", "start_line": "2"}},
        )

        assert "file" not in normalized

    def test_raw_json_string_nested_arguments_object_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            '{"arguments": {"path": "/workspace/src/app.py", "start_line": "2", "end_line": "4"}}',
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2
        assert normalized["end_line"] == 4

    def test_double_nested_arguments_kwargs_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            {"arguments": {"kwargs": {"path": "/workspace/src/app.py", "start_line": "2"}}},
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2

    def test_raw_json_string_double_nested_arguments_kwargs_unwraps_to_tool_arguments(self) -> None:
        normalized = normalize_tool_arguments(
            "read_file",
            '{"arguments": {"kwargs": {"path": "/workspace/src/app.py", "start_line": "2"}}}',
        )

        assert normalized["file"] == "src/app.py"
        assert normalized["start_line"] == 2

    def test_nested_object_wrapper_with_foreign_keys_is_not_unwrapped(self) -> None:
        normalized = normalize_tool_arguments(
            "write_file",
            {"arguments": {"unexpected": "value"}},
        )

        assert normalized == {"arguments": {"unexpected": "value"}}

    def test_write_content_json_string_is_not_unwrapped(self) -> None:
        content = '{"file": "wrong.txt", "content": "wrong"}'

        normalized = normalize_tool_arguments(
            "write_file",
            {"file": "data.json", "content": content},
        )

        assert normalized["file"] == "data.json"
        assert normalized["content"] == content

    def test_write_content_python_literal_string_is_not_unwrapped(self) -> None:
        content = "{'file': 'wrong.txt', 'content': 'wrong'}"

        normalized = normalize_tool_arguments(
            "write_file",
            {"file": "data.json", "content": content},
        )

        assert normalized["file"] == "data.json"
        assert normalized["content"] == content


class TestRepoApplyDiffNormalization:
    """repo_apply_diff accepts only explicit unified-diff payload aliases."""

    SIMPLE_UNIFIED_DIFF = """--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-old
+new
"""

    @pytest.mark.parametrize("alias", ["patch", "patch_text", "unified_diff", "diff_text"])
    def test_explicit_diff_payload_alias_maps_to_diff(self, alias: str) -> None:
        normalized = normalize_tool_arguments(
            "repo_apply_diff",
            {alias: self.SIMPLE_UNIFIED_DIFF, "dry_run": "true"},
        )

        assert normalized["diff"] == self.SIMPLE_UNIFIED_DIFF
        assert alias not in normalized
        assert normalized["dry_run"] is True

    def test_file_content_pair_is_not_inferred_as_diff(self) -> None:
        normalized = normalize_tool_arguments(
            "repo_apply_diff",
            {"file": "src/app.py", "content": "old\nnew\n", "dry_run": "true"},
        )

        assert "diff" not in normalized
        assert normalized["file"] == "src/app.py"
        assert normalized["dry_run"] is True


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

    def test_llm_tool_name_variants_fold_to_registered_canonical_names(self) -> None:
        """Common weak-model tool-name casing/separator variants should resolve."""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        assert normalize_tool_name("Write-File") == "write_file"
        assert normalize_tool_name("create") == "write_file"
        assert normalize_tool_name("readFile") == "read_file"
        assert normalize_tool_name("fs.read_file") == "read_file"
        assert normalize_tool_name("tools.repo-rg") == "repo_rg"

    def test_unknown_namespaces_do_not_fold_to_phantom_tools(self) -> None:
        """Namespaced unknown tools should stay unknown instead of becoming valid-looking."""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        assert normalize_tool_name("fs.delete_everything") == "fs.delete_everything"


class TestRepoReadHeadNormalization:
    """Test repo_read_head weak-model file argument aliases."""

    @pytest.mark.parametrize("alias", ["path", "filename", "target_file", "target_path"])
    def test_file_aliases_normalize_to_file(self, alias: str) -> None:
        normalized = normalize_tool_arguments("repo_read_head", {alias: "src/app.py", "limit": 20})

        assert normalized["file"] == "src/app.py"
        assert normalized["n"] == 20
        assert alias not in normalized


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


class TestWriteContentSynonyms:
    """Weak/diverse-LLM adaptation (2026-06-15): the file body arrives under a non-`content`
    key. Normalization must land it under canonical `content` (resolved before the spec's
    _drop_unknown_arguments pass), instead of silently dropping it -> no_materialized_changes.
    """

    @pytest.mark.parametrize(
        "synonym",
        ["text", "body", "code", "source", "file_content", "file_contents", "contents", "new_content"],
    )
    def test_write_file_content_synonym_maps_to_content(self, synonym: str) -> None:
        normalized = normalize_tool_arguments("write_file", {"file": "app.js", synonym: "const X = 1;\n"})
        assert normalized.get("content") == "const X = 1;\n", f"{synonym!r} did not map to content: {normalized}"
        assert synonym not in normalized or synonym == "content"

    def test_write_file_canonical_content_unchanged(self) -> None:
        """Additive: a correct `content` call is byte-identical after normalization."""
        normalized = normalize_tool_arguments("write_file", {"file": "a.py", "content": "x = 1\n"})
        assert normalized.get("content") == "x = 1\n"

    @pytest.mark.parametrize("synonym", ["filePath", "targetFile", "targetPath"])
    def test_write_file_camel_case_path_synonym_maps_to_file(self, synonym: str) -> None:
        normalized = normalize_tool_arguments("write_file", {synonym: "src/app.py", "content": "x = 1\n"})
        assert normalized.get("file") == "src/app.py", f"{synonym!r} did not map file: {normalized}"

    @pytest.mark.parametrize(
        "synonym", ["sourceCode", "fileContent", "fileContents", "newContent", "newText", "newCode"]
    )
    def test_write_file_camel_case_content_synonym_maps_to_content(self, synonym: str) -> None:
        normalized = normalize_tool_arguments("write_file", {"file": "src/app.py", synonym: "x = 1\n"})
        assert normalized.get("content") == "x = 1\n", f"{synonym!r} did not map content: {normalized}"

    @pytest.mark.parametrize("synonym", ["text", "body", "append", "data", "new_content", "contents"])
    def test_append_to_file_content_synonym_maps_to_content(self, synonym: str) -> None:
        normalized = normalize_tool_arguments("append_to_file", {"file": "log.txt", synonym: "line\n"})
        assert normalized.get("content") == "line\n", f"{synonym!r} did not map to content: {normalized}"

    def test_path_synonyms_still_map_to_file(self) -> None:
        """Pre-existing path aliases must keep working alongside the new content synonyms."""
        normalized = normalize_tool_arguments("write_file", {"path": "x.py", "text": "y = 2\n"})
        assert normalized.get("file") == "x.py"
        assert normalized.get("content") == "y = 2\n"


class TestExecuteCommandSynonyms:
    """Weak-LLM adaptation (2026-06-15): the command body arrives under an invented key.
    Unambiguous command-string synonyms must map to canonical `command`."""

    @pytest.mark.parametrize("synonym", ["cmd", "command_line", "cmdline", "shell_command", "script", "commands"])
    def test_command_synonym_maps_to_command(self, synonym: str) -> None:
        normalized = normalize_tool_arguments("execute_command", {synonym: "npm install"})
        assert normalized.get("command") == "npm install", f"{synonym!r} did not map to command: {normalized}"

    def test_argv_list_maps_to_shell_command(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"argv": ["npm", "run", "build"]})

        assert normalized.get("command") == "npm run build"

    def test_args_list_maps_to_shell_command(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"args": ["npm", "run", "build"]})

        assert normalized.get("command") == "npm run build"

    def test_executable_args_shape_maps_to_shell_command(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"executable": "npm", "args": ["run", "build"]})

        assert normalized.get("command") == "npm run build"

    def test_canonical_command_unchanged(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"command": "node index.js"})
        assert normalized.get("command") == "node index.js"


class TestEditFileSynonyms:
    """Weak/diverse-LLM adaptation for edit_file body names.

    Line-range edits must keep the model's explicit replacement body. Without this,
    _drop_unknown_arguments removes `new_text`/`replacement` and the handler receives an
    empty `content`, which can turn a clear line replacement into an accidental deletion.
    """

    @pytest.mark.parametrize("synonym", ["new_text", "replacement", "code", "source", "body"])
    def test_line_range_body_synonym_maps_to_content(self, synonym: str) -> None:
        normalized = normalize_tool_arguments(
            "edit_file",
            {
                "file": "app.js",
                "start_line": 2,
                "end_line": 2,
                synonym: "const ready = true;\n",
            },
        )

        assert normalized.get("content") == "const ready = true;\n", (
            f"{synonym!r} did not map to line-range content: {normalized}"
        )

    def test_search_replacement_synonym_maps_to_replace_not_content(self) -> None:
        normalized = normalize_tool_arguments(
            "edit_file",
            {"file": "app.js", "search": "oldName", "replacement": "newName"},
        )

        assert normalized.get("replace") == "newName"
        assert "content" not in normalized


@pytest.mark.parametrize(
    "tool_name",
    ["put_file", "write", "write_to_file", "file_write", "write_text_file"],
)
def test_write_file_tool_name_synonyms_are_canonical(tool_name: str) -> None:
    normalized = normalize_tool_arguments(
        tool_name,
        {"target": "src/app.py", "source_code": "print('ok')\n"},
    )
    assert normalized == {"file": "src/app.py", "content": "print('ok')\n"}


@pytest.mark.parametrize("synonym", ["data", "value", "new_text", "new_code", "source_code", "payload"])
def test_write_file_additional_content_synonyms_map_to_content(synonym: str) -> None:
    normalized = normalize_tool_arguments("write_file", {"file": "a.py", synonym: "x = 1\n"})
    assert normalized.get("content") == "x = 1\n", f"{synonym!r} did not map content: {normalized}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
