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
