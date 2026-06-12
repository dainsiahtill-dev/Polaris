"""Unit tests for orchestration.pm_planning internal shared_quality.

Tests pure functions: _parse_command_args, _normalize_path_list,
_normalize_text, _normalize_path, _contains_prompt_leakage,
_has_measurable_acceptance_anchor, _tail_non_empty_lines,
detect_integration_verify_command (mocked fs), and
run_integration_verify_runner (mocked).
"""

from __future__ import annotations

import json
import shlex
import sys

import pytest
from polaris.cells.orchestration.pm_planning.internal.shared_quality import (
    _contains_prompt_leakage,
    _has_measurable_acceptance_anchor,
    _normalize_path,
    _normalize_path_list,
    _normalize_text,
    _parse_command_args,
    _resolve_repo_tsc,
    _run_typescript_typecheck,
    _strip_wrapping_quotes,
    _tail_non_empty_lines,
    _ts_error_is_declared_dep_noise,
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

    def test_chinese_system_prompt_marker(self) -> None:
        assert _contains_prompt_leakage("系统提示词泄露") is True

    def test_domain_prompt_work_item_is_allowed(self) -> None:
        assert _contains_prompt_leakage("提示词编译链路生成 prompt-package.json") is False

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
        assert shlex.split(result)[0] == sys.executable
        assert "pytest" in result

    def test_python_without_tests(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert shlex.split(result)[0] == sys.executable
        assert "compileall" in result

    def test_nodejs_with_test_script(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest run"}}),
            encoding="utf-8",
        )
        result = detect_integration_verify_command(str(tmp_path))
        assert result == "npm run test -- --watch=false"

    def test_nodejs_prefers_verify_final_when_test_script_missing(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"verify:final": "node scripts/verify_final.mjs"}}),
            encoding="utf-8",
        )
        result = detect_integration_verify_command(str(tmp_path))
        assert result == "npm run verify:final"

    def test_nodejs_prefers_smoke_boot_when_only_smoke_exists(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"smoke:boot": "node scripts/smoke_boot.mjs"}}),
            encoding="utf-8",
        )
        result = detect_integration_verify_command(str(tmp_path))
        assert result == "npm run smoke:boot"

    def test_go_module(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "go.mod").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "go test" in result

    def test_rust_cargo(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert "cargo test" in result

    def test_fallback_workspace_check_without_python(self, monkeypatch, tmp_path) -> None:
        """Empty/non-Python workspaces get the deterministic workspace check —
        compileall over zero .py files passes vacuously (L2-11 r4)."""
        result = detect_integration_verify_command(str(tmp_path))
        assert shlex.split(result)[0] == sys.executable
        assert "polaris.kernelone.quality.workspace_check" in result

    def test_fallback_compileall_with_python_sources(self, monkeypatch, tmp_path) -> None:
        (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
        result = detect_integration_verify_command(str(tmp_path))
        assert shlex.split(result)[0] == sys.executable
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

    def test_node_dependency_static_fallback_passes_with_tests(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("KERNELONE_INTEGRATION_QA_COMMAND", raising=False)
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK", "1")
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_AUTO_INSTALL_NODE_DEPS", "0")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"test": "vitest run"},
                    "devDependencies": {"vitest": "^2.1.0", "typescript": "^5.7.0"},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("export const ok = true;\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "app.test.ts").write_text("import { ok } from '../src/app';\n", encoding="utf-8")

        ok, summary, errors = run_integration_verify_runner(str(tmp_path))

        assert ok is True
        assert "static verification passed" in summary.lower()
        assert errors == []

    def test_node_dependency_static_fallback_requires_tests_for_test_script(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("KERNELONE_INTEGRATION_QA_COMMAND", raising=False)
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK", "1")
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_AUTO_INSTALL_NODE_DEPS", "0")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"test": "vitest run"},
                    "devDependencies": {"vitest": "^2.1.0"},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("export const ok = true;\n", encoding="utf-8")

        ok, summary, errors = run_integration_verify_runner(str(tmp_path))

        assert ok is False
        assert "static verification failed" in summary.lower()
        assert any("no test/spec files" in item for item in errors)


# ---------------------------------------------------------------------------
# TypeScript typecheck gate (real `tsc --noEmit`) — false-green prevention
# ---------------------------------------------------------------------------


class TestTypeScriptTypecheckGate:
    def _write_ts_project(self, root, *, sources: dict[str, str], package: dict | None = None) -> None:
        (root / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2022",
                        "module": "NodeNext",
                        "moduleResolution": "NodeNext",
                        "strict": True,
                        "noEmit": True,
                    },
                    "include": ["src/**/*.ts"],
                }
            ),
            encoding="utf-8",
        )
        if package is not None:
            (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
        src = root / "src"
        src.mkdir(exist_ok=True)
        for name, content in sources.items():
            (src / name).write_text(content, encoding="utf-8")

    def test_non_typescript_project_returns_none(self, tmp_path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
        assert _run_typescript_typecheck(str(tmp_path)) is None

    def test_disabled_via_env_returns_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("KERNELONE_INTEGRATION_QA_TS_TYPECHECK", "0")
        self._write_ts_project(tmp_path, sources={"app.ts": "export const ok = true;\n"})
        assert _run_typescript_typecheck(str(tmp_path)) is None

    def test_syntax_error_fails_gate(self, monkeypatch, tmp_path) -> None:
        if not _resolve_repo_tsc():
            pytest.skip("tsc toolchain not available")
        monkeypatch.delenv("KERNELONE_INTEGRATION_QA_TS_TYPECHECK", raising=False)
        # A stray top-level '}' reproduces the exact TS1128 failure mode seen in the
        # gemma-generated card-game (false-green) project.
        self._write_ts_project(tmp_path, sources={"bad.ts": "export const ok = true;\n}\n"})
        result = _run_typescript_typecheck(str(tmp_path))
        assert result is not None
        passed, summary, errors = result
        assert passed is False
        assert "typecheck" in summary.lower() and "fail" in summary.lower()
        assert any("error TS" in item for item in errors)

    def test_clean_project_passes_gate(self, monkeypatch, tmp_path) -> None:
        if not _resolve_repo_tsc():
            pytest.skip("tsc toolchain not available")
        monkeypatch.delenv("KERNELONE_INTEGRATION_QA_TS_TYPECHECK", raising=False)
        self._write_ts_project(tmp_path, sources={"app.ts": "export const ok: boolean = true;\n"})
        result = _run_typescript_typecheck(str(tmp_path))
        assert result is not None
        passed, _summary, errors = result
        assert passed is True
        assert errors == []

    def test_declared_dependency_missing_is_noise(self) -> None:
        deps = {"three", "@scope/pkg"}
        assert _ts_error_is_declared_dep_noise("a.ts(1,1): error TS2307: Cannot find module 'three'.", deps) is True
        assert (
            _ts_error_is_declared_dep_noise("a.ts(1,1): error TS2307: Cannot find module '@scope/pkg'.", deps) is True
        )

    def test_relative_import_missing_is_real_error(self) -> None:
        assert _ts_error_is_declared_dep_noise("a.ts(1,1): error TS2307: Cannot find module './x'.", {"three"}) is False

    def test_undeclared_module_is_real_error(self) -> None:
        assert _ts_error_is_declared_dep_noise("a.ts(1,1): error TS2307: Cannot find module 'lodash'.", set()) is False

    def test_syntax_error_is_not_dependency_noise(self) -> None:
        assert (
            _ts_error_is_declared_dep_noise("a.ts(1,1): error TS1128: Declaration or statement expected.", {"three"})
            is False
        )
