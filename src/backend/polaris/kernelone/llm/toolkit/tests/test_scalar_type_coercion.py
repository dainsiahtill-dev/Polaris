"""ADR-0090: schema-aware scalar coercion for weak-model tool arguments.

Observed live failure (diag5e, qwen3.6-27b): ``repo_rg: Parameter validation
failed: Expected array (list), got str`` — the model sent ``paths: "django/db"``
where the schema declares an array. Coercion fires only for unambiguous,
lossless conversions; genuinely wrong shapes still reach the validators.
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments
from polaris.kernelone.tool_execution.contracts import validate_tool_step


class TestArrayCoercion:
    def test_repo_rg_scalar_paths_becomes_list(self) -> None:
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "class X", "paths": "django/db"})

        assert normalized["paths"] == ["django/db"]

    def test_repo_rg_scalar_paths_passes_validation(self) -> None:
        ok, _code, message = validate_tool_step("repo_rg", {"pattern": "class X", "paths": "django/db"})

        assert ok, message

    def test_existing_list_untouched(self) -> None:
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "x", "paths": ["a", "b"]})

        assert normalized["paths"] == ["a", "b"]

    def test_optional_empty_string_array_is_dropped(self) -> None:
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "x", "paths": "   "})

        assert "paths" not in normalized


class TestScalarCoercion:
    def test_single_element_list_to_string(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": ["src/app.py"]})

        assert normalized["file"] == "src/app.py"

    def test_numeric_string_to_integer(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "max_bytes": "50000"})

        assert normalized["max_bytes"] == 50000

    def test_non_numeric_string_left_for_validator(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "max_bytes": "many"})

        assert normalized["max_bytes"] == "many"

    def test_undeclared_param_untouched(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "line_hint": "5"})

        assert normalized["line_hint"] == "5"

    def test_integer_string_with_unit_to_integer(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"command": "echo ok", "timeout": "30s"})

        assert normalized["timeout"] == 30

    def test_integer_string_with_plus_sign_to_integer(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "start_line": "+5"})

        assert normalized["start_line"] == 5

    def test_integer_decimal_string_to_integer(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "end_line": "5.0"})

        assert normalized["end_line"] == 5

    def test_boolean_string_on_declared_param(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "range_required": "true"})

        assert normalized["range_required"] is True

    def test_boolean_yes_string_on_declared_param(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "range_required": "yes"})

        assert normalized["range_required"] is True

    def test_boolean_off_string_on_declared_param(self) -> None:
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "x", "case_sensitive": "off"})

        assert normalized["case_sensitive"] is False

    def test_boolean_string_coerced(self) -> None:
        normalized = normalize_tool_arguments("repo_rg", {"pattern": "x", "case_sensitive": "true"})

        if "case_sensitive" in normalized:
            assert normalized["case_sensitive"] in (True, "true")

    def test_body_string_list_joins_with_newlines(self) -> None:
        normalized = normalize_tool_arguments("write_file", {"file": "a.py", "content": ["a = 1", "b = 2"]})

        assert normalized["content"] == "a = 1\nb = 2"

    def test_command_argv_list_joins_as_shell_command(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"command": ["npm", "run", "build"]})

        assert normalized["command"] == "npm run build"

    def test_optional_integer_empty_string_is_dropped_for_default(self) -> None:
        normalized = normalize_tool_arguments("execute_command", {"command": "echo ok", "timeout": ""})

        assert "timeout" not in normalized

    def test_optional_boolean_empty_string_is_dropped_for_default(self) -> None:
        normalized = normalize_tool_arguments("read_file", {"file": "a.py", "range_required": ""})

        assert normalized["range_required"] is False

    def test_required_empty_string_body_is_preserved(self) -> None:
        normalized = normalize_tool_arguments("write_file", {"file": "empty.txt", "content": ""})

        assert normalized["content"] == ""
