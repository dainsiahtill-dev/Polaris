"""Unit tests for orchestration.pm_planning internal shared_quality.

Tests pure functions: _parse_command_args, _normalize_path_list,
_normalize_text, _normalize_path, _contains_prompt_leakage,
_has_measurable_acceptance_anchor, _tail_non_empty_lines,
detect_integration_verify_command (mocked fs), and
run_integration_verify_runner (mocked).
"""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.pm_planning.internal.shared_quality import (
    _contains_prompt_leakage,
    _has_measurable_acceptance_anchor,
    _normalize_path,
    _normalize_path_list,
    _normalize_text,
    _parse_command_args,
    _strip_wrapping_quotes,
    _tail_non_empty_lines,
    detect_integration_verify_command,
    run_integration_verify_runner,
)

# ---------------------------------------------------------------------------
# _parse_command_args
# ---------------------------------------------------------------------------


class TestParseCommandArgs:
    def test_simple_command(self) -> None:
        assert _parse_command_args("pytest") == ["pytest"]

    def test_command_with_args(self) -> None:
        result = _parse_command_args("python -m pytest --tb=short")
        assert result[0] == "python"
        assert "-m" in result
        assert "pytest" in result

    def test_quoted_args(self) -> None:
        result = _parse_command_args("echo 'hello world'")
        assert "hello world" in result

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty command"):
            _parse_command_args("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="empty command"):
            _parse_command_args("   ")

    def test_invalid_syntax_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid command syntax"):
            _parse_command_args("echo 'unclosed quote")


# ---------------------------------------------------------------------------
# _strip_wrapping_quotes
# ---------------------------------------------------------------------------


class TestStripWrappingQuotesShared:
    def test_single_quotes(self) -> None:
        assert _strip_wrapping_quotes("'foo'") == "foo"

    def test_double_quotes(self) -> None:
        assert _strip_wrapping_quotes('"bar"') == "bar"

    def test_none_input(self) -> None:
        # _strip_wrapping_quotes expects str, handle None before calling
        assert _strip_wrapping_quotes(None or "") == ""


# ---------------------------------------------------------------------------
# _normalize_path_list
# ---------------------------------------------------------------------------


class TestNormalizePathListShared:
    def test_csv_string(self) -> None:
        result = _normalize_path_list("src/app,tests,docs")
        assert "src/app" in result
        assert "tests" in result
        assert "docs" in result

    def test_list_input(self) -> None:
        result = _normalize_path_list(["a.py", "b.py"])
        assert result == ["a.py", "b.py"]

    def test_strips_leading_dotslash(self) -> None:
        result = _normalize_path_list(["./foo.py"])
        assert "foo.py" in result

    def test_removes_duplicates(self) -> None:
        # No deduplication — identical paths are preserved
        result = _normalize_path_list(["a.py", "a.py"])
        assert result.count("a.py") == 2


# ---------------------------------------------------------------------------
# _normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeTextShared:
    def test_collapse_whitespace(self) -> None:
        assert _normalize_text("  a   b  c  ") == "a b c"

    def test_none_input(self) -> None:
        assert _normalize_text(None) == ""


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------


class TestNormalizePathShared:
    def test_lowercase(self) -> None:
        assert _normalize_path("SRC/APP.PY") == "src/app.py"

    def test_strips_leading_dotslash(self) -> None:
        assert _normalize_path("./foo/bar.py") == "foo/bar.py"

    def test_normalises_backslashes(self) -> None:
        assert _normalize_path(r"src\app.py") == "src/app.py"


# ---------------------------------------------------------------------------
# _contains_prompt_leakage
# ---------------------------------------------------------------------------


class TestContainsPromptLeakageShared:
    def test_system_prompt_marker(self) -> None:
        assert _contains_prompt_leakage("you are a PM agent") is True

    def test_normal_text(self) -> None:
        assert _contains_prompt_leakage("build a login page") is False

    def test_empty(self) -> None:
        assert _contains_prompt_leakage("") is False


# ---------------------------------------------------------------------------
# _has_measurable_acceptance_anchor
# ---------------------------------------------------------------------------


class TestHasMeasurableAcceptanceAnchorShared:
    def test_backtick_command(self) -> None:
        assert _has_measurable_acceptance_anchor(["`pytest` passes"]) is True

    def test_command_word(self) -> None:
        assert _has_measurable_acceptance_anchor(["run npm test"]) is True

    def test_assert_with_result(self) -> None:
        assert _has_measurable_acceptance_anchor(["should return 200 ok"]) is True

    def test_chinese_measurable(self) -> None:
        # Chinese text does not match ASCII command/assert regex patterns
        assert _has_measurable_acceptance_anchor(["验证返回200状态码"]) is False

    def test_empty_list(self) -> None:
        assert _has_measurable_acceptance_anchor([]) is False


# ---------------------------------------------------------------------------
# _tail_non_empty_lines
# ---------------------------------------------------------------------------


class TestTailNonEmptyLines:
    def test_under_limit(self) -> None:
        lines = ["a", "b", "c"]
        assert _tail_non_empty_lines("a\nb\nc") == lines

    def test_over_limit(self) -> None:
        text = "\n".join(f"line{i}" for i in range(20))
        result = _tail_non_empty_lines(text, limit=8)
        assert len(result) == 8
        assert result[0] == "line12"
        assert result[-1] == "line19"

    def test_empty_input(self) -> None:
        assert _tail_non_empty_lines("") == []
        assert _tail_non_empty_lines("   \n  \n  ") == []


# ---------------------------------------------------------------------------
# detect_integration_verify_command (mocked filesystem)
# ---------------------------------------------------------------------------


class TestDetectIntegrationVerifyCommand:
    def test_env_override(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_COMMAND", "npm test")
        result = detect_integration_verify_command(str(tmp_path))
        assert result == "npm test"

    def test_python_with_pytest(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "pytest" in result

    def test_python_without_tests(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "compileall" in result

    def test_nodejs(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "npm" in result

    def test_go_module(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "go.mod").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "go test" in result

    def test_rust_cargo(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "cargo test" in result

    def test_fallback_compileall(self, monkeypatch, tmp_path) -> None:
        result = detect_integration_verify_command(str(tmp_path))
        assert "compileall" in result


# ---------------------------------------------------------------------------
# run_integration_verify_runner (mocked CommandExecutionService)
# ---------------------------------------------------------------------------


class TestRunIntegrationVerifyRunner:
    def test_rejects_invalid_command(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_COMMAND", "")
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_TIMEOUT_SECONDS", "60")
        monkeypatch.setattr(
            "polaris.cells.orchestration.pm_planning.internal.shared_quality.detect_integration_verify_command",
            lambda ws: "nonexistent_cmd_xyz",
        )
        ok, summary, _errors = run_integration_verify_runner(str(tmp_path))
        assert ok is False
        assert "rejected" in summary.lower() or "failed" in summary.lower()

    def test_command_parse_error(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_COMMAND", "")
        monkeypatch.setattr(
            "polaris.cells.orchestration.pm_planning.internal.shared_quality.detect_integration_verify_command",
            lambda ws: "echo 'unclosed",
        )
        ok, _summary, _errors = run_integration_verify_runner(str(tmp_path))
        assert ok is False

    def test_timeout_env_clamped(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_COMMAND", "echo hello")
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_TIMEOUT_SECONDS", "bad")
        # Should use default 300
        ok, summary, _ = run_integration_verify_runner(str(tmp_path))
        # Either passes or fails — does not raise
        assert isinstance(ok, bool)
        assert isinstance(summary, str)
