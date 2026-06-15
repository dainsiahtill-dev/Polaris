"""Tests for code_validator module."""

from __future__ import annotations

import pytest
from polaris.kernelone.tool_execution.code_validator import (
    MultiLanguageCodeValidator,
    PythonCodeValidator,
    SyntaxValidationResult,
    format_validation_error,
    validate_code_syntax,
)


class TestPythonCodeValidator:
    """Test Python code validation."""

    def test_valid_python_code(self):
        """Test valid Python code passes validation."""
        code = """def median(values: list[int]) -> int:
    if not values:
        raise ValueError("median() arg is an empty sequence")
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle]
"""
        validator = PythonCodeValidator()
        result = validator.validate(code, "test.py")
        assert result.is_valid is True

    def test_return0_auto_fixed(self):
        """Test that return0 (no space) is auto-fixed."""
        code = """def median():
    return0
"""
        validator = PythonCodeValidator()
        result = validator.validate(code, "test.py")
        assert result.is_valid is True
        assert result.fixed_code is not None
        assert "return 0" in result.fixed_code

    def test_if_without_space_auto_fixed(self):
        """Test that if( without space is auto-fixed."""
        code = """def median():
    if(values):
        return 0
"""
        validator = PythonCodeValidator()
        result = validator.validate(code, "test.py")
        assert result.is_valid is True
        assert result.fixed_code is not None
        assert "if (values)" in result.fixed_code

    def test_print_without_parentheses_auto_fixed(self):
        """Test that print without parentheses is auto-fixed."""
        code = """def median():
    print "hello"
"""
        validator = PythonCodeValidator()
        result = validator.validate(code, "test.py")
        assert result.is_valid is True
        assert result.fixed_code is not None
        assert 'print("hello")' in result.fixed_code

    def test_quick_check_return0(self):
        """Test quick_check detects return0."""
        validator = PythonCodeValidator()
        is_clean, errors = validator.quick_check("    return0\n")
        assert is_clean is False
        assert any("return0" in e for e in errors)

    def test_fix_method_return0(self):
        """Test fix() method auto-fixes return0."""
        validator = PythonCodeValidator()
        code = "    return0\n"
        fixed, fixes = validator.fix(code)
        assert fixed == "    return 0\n"
        assert len(fixes) == 1
        assert fixes[0].original == "return0"
        assert fixes[0].fixed == "return 0"
        assert fixes[0].confidence == 0.95

    def test_fix_method_if_without_space(self):
        """Test fix() method auto-fixes if(."""
        validator = PythonCodeValidator()
        code = "    if(x):\n"
        fixed, fixes = validator.fix(code)
        assert fixed == "    if (x):\n"
        assert len(fixes) == 1

    def test_fix_method_multiple_hallucinations(self):
        """Test fix() method handles multiple hallucinations."""
        validator = PythonCodeValidator()
        code = "    return0\n    if(x):\n    return1\n"
        fixed, fixes = validator.fix(code)
        assert "return 0" in fixed
        assert "if (x)" in fixed
        assert "return 1" in fixed
        assert len(fixes) == 3


class TestMultiLanguageCodeValidator:
    """Test multi-language validation."""

    def test_python_validation_auto_fix(self):
        """Test Python files are auto-fixed for hallucinations."""
        code = "def test():\n    return0\n"
        validator = MultiLanguageCodeValidator()
        result = validator.validate(code, "test.py")
        assert result.is_valid is True
        assert result.fixed_code is not None
        assert "return 0" in result.fixed_code

    def test_js_basic_validation(self):
        """Test JS files get basic bracket validation."""
        code = "function test() { return 0; }"
        validator = MultiLanguageCodeValidator()
        result = validator.validate(code, "test.js")
        # Basic bracket check should pass
        assert result.is_valid is True

    def test_unknown_extension_passes(self):
        """Test unknown file extensions pass through."""
        code = "some content"
        validator = MultiLanguageCodeValidator()
        result = validator.validate(code, "test.unknown")
        assert result.is_valid is True


class TestFormatValidationError:
    """Test error formatting."""

    def test_format_valid_result(self):
        """Test formatting valid result returns empty string."""
        result = SyntaxValidationResult.success()
        formatted = format_validation_error(result, "test.py")
        assert formatted == ""

    def test_format_errors_with_filepath(self):
        """Test formatting errors includes filepath."""
        from polaris.kernelone.tool_execution.code_validator import CodeSyntaxError

        result = SyntaxValidationResult.failure(
            errors=[CodeSyntaxError(line=1, column=0, message="test error", error_type="SyntaxError")]
        )
        formatted = format_validation_error(result, "test.py")
        assert "test.py" in formatted
        assert "test error" in formatted


class TestValidateCodeSyntax:
    """Test convenience function."""

    def test_validate_valid_code(self):
        """Test validate_code_syntax with valid code."""
        code = "x = 1"
        result = validate_code_syntax(code, "test.py")
        assert result.is_valid is True

    def test_validate_invalid_code(self):
        """Test validate_code_syntax with invalid code."""
        code = "if(x"  # Unclosed parenthesis
        result = validate_code_syntax(code, "test.py")
        assert result.is_valid is False


class TestFixCodeWithTool:
    """Test third-party tool auto-fix functions."""

    def test_fix_code_with_tool_python_formatting(self):
        """Test fix_code_with_tool uses ruff for Python formatting."""
        from polaris.kernelone.tool_execution.code_validator import fix_code_with_tool

        # Code with correct syntax but bad formatting
        code = "x=1\ny=2\n"
        fixed, fixes = fix_code_with_tool(code, "test.py")
        # ruff format should fix this (if ruff is available)
        # If ruff not found, fixes will be empty
        assert isinstance(fixed, str)
        assert isinstance(fixes, list)

    def test_fix_code_with_tool_unknown_extension(self):
        """Test fix_code_with_tool handles unknown extensions."""
        from polaris.kernelone.tool_execution.code_validator import fix_code_with_tool

        code = "some content"
        fixed, fixes = fix_code_with_tool(code, "test.unknown")
        assert fixed == code
        assert len(fixes) == 0

    def test_fix_code_with_tool_no_filepath(self):
        """Test fix_code_with_tool handles missing filepath."""
        from polaris.kernelone.tool_execution.code_validator import fix_code_with_tool

        code = "some content"
        fixed, fixes = fix_code_with_tool(code, None)
        assert fixed == code
        assert len(fixes) == 0


class TestIndentationFix:
    """Test indentation auto-fix."""

    def test_fix_indentation_tabs(self):
        """Test Tab → 4 spaces fix."""
        from polaris.kernelone.tool_execution.code_validator import PythonCodeValidator

        validator = PythonCodeValidator()
        code = "def f():\n\treturn 1\n"
        fixed, fixes = validator._fix_indentation(code)
        assert "\t" not in fixed
        assert "    " in fixed
        assert len(fixes) > 0

    def test_fix_indentation_non_multiple_of_4(self):
        """Test non-4-space indentation fix."""
        from polaris.kernelone.tool_execution.code_validator import PythonCodeValidator

        validator = PythonCodeValidator()
        code = "def f():\n  return 1\n"  # 2 spaces
        fixed, fixes = validator._fix_indentation(code)
        assert "    " in fixed  # Should be 4 spaces
        assert len(fixes) > 0


class TestRealCodeNotFalseRejected:
    """Regression: valid real-world code must not be rejected by heuristics.

    Root cause (Phase B Task 2): quick_check() consulted an indentation heuristic
    AFTER a successful ast.parse(); it flagged PEP 8 continuation-line alignment
    (leading whitespace not a multiple of 4) as 'inconsistent', and validate() then
    returned is_valid=False with an EMPTY error list — a silent rejection that blocked
    legitimate edit_blocks edits to real files (e.g. requests/sessions.py).
    """

    def test_continuation_line_alignment_is_valid(self):
        """Operands aligned to an opening delimiter (non-4-multiple) are valid."""
        code = (
            "def call():\n"
            "    result = some_function(arg_one,\n"
            "                           arg_two,\n"
            "                           arg_three)\n"
            "    return result\n"
        )
        result = validate_code_syntax(code, "real.py")
        assert result.is_valid is True
        assert result.errors is None or result.errors == []

    def test_quick_check_ignores_valid_indentation(self):
        """quick_check must not flag indentation that already parses as valid."""
        validator = PythonCodeValidator()
        aligned = "x = foo(a,\n        b)\n"  # 8-space alignment to '(' -> not %4
        is_clean, errors = validator.quick_check(aligned)
        assert is_clean is True
        assert errors == []

    def test_failure_always_carries_a_diagnostic(self):
        """Fail-closed-with-evidence: a failure must never have an empty error list."""
        validator = PythonCodeValidator()
        # return0 parses (valid identifier reference) but is a hallucination pattern;
        # validate() must surface it rather than swallow it.
        result = validator.validate("def f():\n    x = return0\n", "x.py")
        if result.is_valid is False:
            assert result.errors, "failure returned with no diagnostic"

    def test_hallucination_still_detected(self):
        """The genuine hallucination patterns remain active after the fix."""
        validator = PythonCodeValidator()
        is_clean, errors = validator.quick_check("    return0\n")
        assert is_clean is False
        assert any("return0" in e for e in errors)


class TestPostWriteVerification:
    """Test post-write verification."""

    def test_verify_written_code_success(self):
        """Test successful verification."""
        import os
        import tempfile

        from polaris.kernelone.tool_execution.code_validator import verify_written_code

        content = "def test():\n    return 1\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(content)
            f.flush()
            temp_path = f.name

        try:
            result = verify_written_code(temp_path, content)
            assert result.success is True
            assert result.error is None
        finally:
            os.unlink(temp_path)

    def test_verify_written_code_mismatch(self):
        """Test content mismatch detection."""
        import os
        import tempfile

        from polaris.kernelone.tool_execution.code_validator import verify_written_code

        expected = "def test():\n    return 1\n"
        actual = "def test():\n    return 2\n"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(actual)
            f.flush()
            temp_path = f.name

        try:
            result = verify_written_code(temp_path, expected)
            assert result.success is False
            assert "Content mismatch" in result.error
        finally:
            os.unlink(temp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestJsNodeCheckGate:
    """Write-path fail-closed JS syntax gate (2026-06-15, batch b-norm root cause #1).

    Live evidence: L2-09 game.js:50 and L2-10 app.js:10 shipped broken JS (a `;` used as
    an object-literal member separator) because the old bracket-balance heuristic could not
    see statement-level errors. `node --check` (authority) must REJECT these before they land.
    """

    # exact L2-09 game.js shape: ';' instead of ',' inside an object literal
    L209_BROKEN = (
        "function step() {\n"
        "  const head = {\n"
        "    x: snake[0].x + direction.x,\n"
        "    y: snake[0].y + direction.y;\n"
        "  };\n"
        "  return head;\n"
        "}\n"
    )
    # exact L2-10 app.js shape: ';' before the closing brace of setOptions({...})
    L210_BROKEN = "marked.setOptions({\n  gfm: true,\n  breaks: true,\n  mangle: false;\n});\n"

    def test_object_literal_semicolon_is_rejected_l209(self) -> None:
        result = validate_code_syntax(self.L209_BROKEN, "game.js")
        assert not result.is_valid, "node --check must reject ';' as an object-literal separator"

    def test_object_literal_semicolon_is_rejected_l210(self) -> None:
        result = validate_code_syntax(self.L210_BROKEN, "app.js")
        assert not result.is_valid

    def test_valid_classic_js_passes(self) -> None:
        code = "function add(a, b) {\n  const o = { x: a, y: b };\n  return o.x + o.y;\n}\n"
        result = validate_code_syntax(code, "game.js")
        assert result.is_valid, format_validation_error(result, "game.js")

    def test_valid_esm_js_passes(self) -> None:
        """A valid ES-module .js (top-level import/export) must NOT be false-rejected."""
        code = "import { CONFIG } from './config.js';\nexport function init() {\n  return CONFIG;\n}\n"
        result = validate_code_syntax(code, "main.js")
        assert result.is_valid, format_validation_error(result, "main.js")

    def test_rejection_reports_a_line(self) -> None:
        result = validate_code_syntax(self.L210_BROKEN, "app.js")
        assert result.errors and any(e.line > 0 for e in result.errors)
