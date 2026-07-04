"""Regression tests for shared artifact quality scanning."""

from __future__ import annotations

import os
from pathlib import Path

from polaris.kernelone.quality import (
    ArtifactQualityIssue,
    artifact_quality_issue_key,
    artifact_quality_issue_raw,
    artifact_quality_issue_structural_key,
    artifact_quality_issues_for_errors,
    artifact_quality_issues_from_errors,
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.quality.interface_ledger import record_declared_interfaces


def test_scan_package_manifest_rejects_invalid_script_shell_syntax(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "bad-script-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"console.log('missing close quote')"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("script 'test' has invalid shell syntax" in error for error in errors)


def test_artifact_quality_evidence_projects_typed_issues(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "bad-script-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"console.log('missing close quote')"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors
    assert evidence.issues
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "npm_manifest_invalid"
    assert evidence.issues[0].path == "package.json"
    assert evidence.issues[0].source == "package_manifest_scanner"
    assert evidence.issues[0].metadata["manifest_path"] == "package.json"
    assert evidence.to_dict()["issues"][0]["code"] == "npm_manifest_invalid"


def test_artifact_quality_issue_identity_helpers_preserve_raw_and_structured_keys() -> None:
    issue = ArtifactQualityIssue(
        code="typescript_import_unresolved_symbol",
        message="Missing export",
        path="src\\engine\\forecast.ts",
        line=7,
        column=3,
        metadata={"raw": "Artifact quality scan failed: unresolved symbol Missing"},
    )

    assert artifact_quality_issue_raw(issue) == "Artifact quality scan failed: unresolved symbol Missing"
    assert artifact_quality_issue_key(issue) == (
        "structured",
        "typescript_import_unresolved_symbol",
        "src/engine/forecast.ts",
        "7",
        "3",
        "Missing export",
    )
    assert artifact_quality_issue_structural_key(issue) == (
        "typescript_import_unresolved_symbol",
        "src/engine/forecast.ts",
        "7",
        "3",
    )
    assert artifact_quality_issue_raw({"message": "legacy text"}) == "legacy text"
    assert artifact_quality_issue_structural_key({"message": "legacy text"}) == ()


def test_artifact_quality_evidence_uses_direct_typed_issue_for_missing_workspace(
    tmp_path: Path,
) -> None:
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path / "missing-workspace"),
    )

    assert evidence.errors == ("Artifact quality scan failed: workspace path does not exist",)
    assert [issue.code for issue in evidence.issues] == ["workspace_path_missing"]
    assert evidence.issues[0].source == "artifact_quality_scanner"


def test_artifact_quality_evidence_uses_direct_source_syntax_issue(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"broken": true,,}\n', encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "syntax_error"
    assert evidence.issues[0].path == "package.json"
    assert evidence.issues[0].source == "source_syntax_checker"
    assert evidence.issues[0].metadata["raw"] == evidence.errors[0]


def test_artifact_quality_evidence_uses_direct_declared_interface_issue(
    tmp_path: Path,
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "weather.ts").write_text(
        "export interface WeatherSnapshot { condition: string }\n",
        encoding="utf-8",
    )
    record_declared_interfaces(
        str(tmp_path),
        str(tmp_path),
        [{"step_id": "S1", "target_file": "src/weather.ts", "interface_names": ["WeatherReport"]}],
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path))

    assert evidence.errors == (
        "Artifact quality scan failed: declared interface 'WeatherReport' missing from src/weather.ts",
    )
    assert [issue.code for issue in evidence.issues] == ["declared_interface_missing"]
    assert evidence.issues[0].source == "declared_interface_ledger"
    assert evidence.issues[0].metadata == {
        "raw": "Artifact quality scan failed: declared interface 'WeatherReport' missing from src/weather.ts",
        "target_file": "src/weather.ts",
        "identifier": "WeatherReport",
    }


def test_artifact_quality_issue_projection_classifies_javascript_module_runtime_error() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues == (
        {
            "code": "javascript_module_error",
            "message": "The requested module ./engine/AlchemyEngine.js does not provide an export named default",
            "path": None,
            "severity": "error",
            "source": "runtime_smoke",
            "metadata": {"raw": error},
        },
    )


def test_artifact_quality_issue_projection_preserves_typed_issue_payload() -> None:
    issue = {
        "code": "typescript_ts2307",
        "message": "Cannot find module './missing.js'",
        "path": "src/main.ts",
        "severity": "error",
        "source": "typescript_compiler",
        "line": 2,
        "column": 8,
        "metadata": {"raw": "typed diagnostic", "diagnostic_code": "TS2307"},
    }

    issues = artifact_quality_issues_from_errors((issue,))

    assert issues == (issue,)


def test_artifact_quality_issue_projection_extracts_python_command_npm_script_metadata() -> None:
    error = "Artifact quality scan failed: npm package manifest contains Python command in script 'test:py' in package.json"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"]["manifest_path"] == "package.json"
    assert issues[0]["metadata"]["script_name"] == "test:py"
    assert issues[0]["metadata"]["script_issue"] == "python_command"


def test_artifact_quality_issues_for_errors_matches_typed_and_residual_rows() -> None:
    typed_raw = "src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'."
    residual_raw = "src/other.ts(2,3): error TS2304: Cannot find name 'Weather'."
    stale_raw = "src/stale.ts(3,4): error TS2304: Cannot find name 'Stale'."
    typed_issue = artifact_quality_issues_from_errors((typed_raw,))[0]
    stale_issue = artifact_quality_issues_from_errors((stale_raw,))[0]

    issues = artifact_quality_issues_for_errors(
        [typed_raw, residual_raw],
        (typed_issue, stale_issue),
    )

    assert [issue["path"] for issue in issues] == ["src/main.ts", "src/other.ts"]
    assert [issue["code"] for issue in issues] == ["typescript_ts2322", "typescript_ts2304"]
    assert all(issue["path"] != "src/stale.ts" for issue in issues)


def test_artifact_quality_issues_for_errors_matches_structural_keys_without_raw_equality() -> None:
    raw_error = "src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'."
    typed_issue = {
        **artifact_quality_issues_from_errors((raw_error,))[0],
        "metadata": {"raw": "compiler output was trimmed but code/path stayed stable"},
    }

    issues = artifact_quality_issues_for_errors([raw_error], (typed_issue,))

    assert issues == (typed_issue,)


def test_artifact_quality_issue_projection_preserves_extra_typed_fields() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "code": "unresolved_import_symbol",
                "message": "WeatherKind is imported but not exported",
                "path": "src/engine/forecast.py",
                "symbol": "WeatherKind",
                "importer_path": "src/engine/forecast.py",
                "owner_path": "src/models/weather.py",
                "details": {"available_exports": ["WeatherReport"]},
            },
        )
    )

    assert issues[0]["metadata"]["symbol"] == "WeatherKind"
    assert issues[0]["metadata"]["importer_path"] == "src/engine/forecast.py"
    assert issues[0]["metadata"]["owner_path"] == "src/models/weather.py"
    assert issues[0]["metadata"]["details"] == {"available_exports": ["WeatherReport"]}


def test_artifact_quality_issue_projection_extracts_compiler_path() -> None:
    error = "src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'."

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "typescript_ts2322"
    assert issues[0]["path"] == "src/main.ts"
    assert issues[0]["line"] == 1
    assert issues[0]["column"] == 1
    assert issues[0]["metadata"] == {"raw": error, "diagnostic_code": "TS2322"}


def test_artifact_quality_issue_projection_extracts_colon_line_column() -> None:
    error = "src/main.py:7:13: SyntaxError: invalid syntax"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["path"] == "src/main.py"
    assert issues[0]["line"] == 7
    assert issues[0]["column"] == 13
    assert issues[0]["metadata"] == {"raw": error}


def test_artifact_quality_issue_projection_extracts_rust_compiler_code_and_location() -> None:
    error = (
        "error[E0583]: file not found for module `weather`\n"
        "  --> src/main.rs:2:1\n"
        "   |\n"
        "2  | mod weather;\n"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "rust_e0583"
    assert issues[0]["path"] == "src/main.rs"
    assert issues[0]["line"] == 2
    assert issues[0]["column"] == 1
    assert issues[0]["metadata"] == {"raw": error.strip(), "diagnostic_code": "E0583"}


def test_artifact_quality_issue_projection_extracts_language_compile_errors() -> None:
    cases = (
        ("engine/main.go:10:5: undefined: Weather", "go_compile_error", "engine/main.go", 10, 5, "go"),
        ("src/Main.java:3: error: cannot find symbol", "java_compile_error", "src/Main.java", 3, None, "java"),
        (
            "src/main.cpp:4:5: error: 'cout' was not declared in this scope",
            "cpp_compile_error",
            "src/main.cpp",
            4,
            5,
            "cpp",
        ),
    )

    for error, code, path, line, column, language in cases:
        issues = artifact_quality_issues_from_errors((error,))

        assert issues[0]["code"] == code
        assert issues[0]["path"] == path
        assert issues[0]["line"] == line
        if column is None:
            assert "column" not in issues[0]
        else:
            assert issues[0]["column"] == column
        assert issues[0]["metadata"] == {"raw": error, "language": language}


def test_artifact_quality_issue_projection_extracts_declared_target_metadata() -> None:
    error = "Artifact quality scan failed: declared target file missing 'src/main.py' is missing"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "declared_target_missing"
    assert issues[0]["path"] == "src/main.py"
    assert issues[0]["metadata"] == {"raw": error, "target_file": "src/main.py"}


def test_artifact_quality_issue_projection_extracts_npm_script_metadata() -> None:
    error = (
        "Artifact quality scan failed: npm package manifest script 'test' "
        "is a placeholder command: echo \"Error: no test specified\" && exit 1"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"] == {
        "raw": error,
        "manifest_path": "package.json",
        "script_name": "test",
        "script_issue": "placeholder_command",
    }


def test_artifact_quality_issue_projection_extracts_npm_missing_entrypoint_metadata() -> None:
    error = (
        "Artifact quality scan failed: npm package manifest script 'start' "
        "references missing local entrypoint 'src/index.js'"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"] == {
        "raw": error,
        "manifest_path": "package.json",
        "script_name": "start",
        "script_issue": "missing_local_entrypoint",
        "entrypoint": "src/index.js",
    }


def test_artifact_quality_issue_projection_extracts_unresolved_import_symbol_metadata() -> None:
    error = (
        "Artifact quality scan failed: unresolved import symbol 'WeatherKind' "
        "from 'src.models.weather' in src/engine/forecast.py"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "unresolved_import_symbol"
    assert issues[0]["path"] == "src/engine/forecast.py"
    assert issues[0]["metadata"] == {
        "raw": error,
        "symbol": "WeatherKind",
        "module": "src.models.weather",
        "importer_path": "src/engine/forecast.py",
    }


def test_artifact_quality_issue_projection_extracts_unresolved_relative_import_metadata() -> None:
    error = "Artifact quality scan failed: unresolved relative import './engine/runner' in src/index.ts"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "unresolved_relative_import"
    assert issues[0]["path"] == "src/index.ts"
    assert issues[0]["metadata"] == {
        "raw": error,
        "specifier": "./engine/runner",
        "importer_path": "src/index.ts",
    }


def test_artifact_quality_evidence_uses_direct_typescript_import_issue(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text('import { run } from "./engine/runner";\nrun();\n', encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/index.ts"])

    assert evidence.errors == (
        "Artifact quality scan failed: unresolved relative import './engine/runner' in src/index.ts",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "unresolved_relative_import"
    assert evidence.issues[0].source == "typescript_import_scanner"
    assert evidence.issues[0].path == "src/index.ts"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "importer_path": "src/index.ts",
        "specifier": "./engine/runner",
    }


def test_artifact_quality_evidence_uses_direct_typescript_red_flag_issue(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "factory.ts").write_text(
        """
export function build() {
  return {
    name: "demo";
  };
}
""".lstrip(),
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/factory.ts"])

    assert evidence.errors == (
        "Artifact quality scan failed: TypeScript return object contains "
        "semicolon-terminated property in src/factory.ts",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "typescript_return_object_semicolon_property"
    assert evidence.issues[0].source == "typescript_syntax_red_flag_scanner"
    assert evidence.issues[0].path == "src/factory.ts"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "path": "src/factory.ts",
    }


def test_artifact_quality_evidence_uses_direct_html_module_script_issue(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><script type="module" src="./src/main.ts"></script></body></html>\n',
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["index.html"])

    assert evidence.errors == (
        "Artifact quality scan failed: HTML module script references TypeScript source "
        "'./src/main.ts' in index.html; static entrypoints must load JavaScript",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "html_module_script_typescript_source"
    assert evidence.issues[0].source == "html_module_script_scanner"
    assert evidence.issues[0].path == "index.html"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "html_path": "index.html",
        "script_src": "./src/main.ts",
    }


def test_artifact_quality_evidence_uses_direct_package_module_type_issue(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.js").write_text("module.exports = { start: true };\n", encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: JavaScript source src/index.js uses CommonJS runtime syntax; "
        "npm package manifest declares type=module but workspace JavaScript uses CommonJS runtime syntax "
        "in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "package_module_type_commonjs_mismatch"
    assert evidence.issues[0].source == "package_module_type_scanner"
    assert evidence.issues[0].path == "package.json"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "manifest_path": "package.json",
        "source_path": "src/index.js",
        "declared_type": "module",
        "runtime_syntax": "commonjs",
    }


def test_artifact_quality_evidence_uses_direct_typescript_project_typecheck_issue(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    tsc = bin_dir / ("tsc.cmd" if os.name == "nt" else "tsc")
    if os.name == "nt":
        tsc.write_text("@echo off\necho src/index.ts(1,7): error TS2322: bad type\nexit /b 2\n", encoding="utf-8")
    else:
        tsc.write_text(
            "#!/usr/bin/env sh\n"
            "echo 'src/index.ts(1,7): error TS2322: bad type'\n"
            "exit 2\n",
            encoding="utf-8",
        )
        tsc.chmod(0o755)

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/index.ts"])

    assert evidence.errors == (
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/index.ts(1,7): error TS2322: bad type",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "typescript_project_typecheck_failed"
    assert evidence.issues[0].source == "typescript_project_typecheck"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "command": "tsc --noEmit --pretty false",
        "exit_code": 2,
        "detail": "src/index.ts(1,7): error TS2322: bad type",
    }


def test_artifact_quality_evidence_uses_direct_typescript_symbol_coherence_issue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KERNELONE_TS_SYMBOL_COHERENCE", "1")
    (tmp_path / "sibling.ts").write_text("export const Other = 1;\n", encoding="utf-8")
    (tmp_path / "index.ts").write_text("import { Missing } from './sibling';\n", encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["index.ts"])

    issue = next(item for item in evidence.issues if item.code == "typescript_import_unresolved_symbol")
    assert issue.source == "typescript_symbol_coherence_scanner"
    assert issue.path == "index.ts"
    assert issue.metadata == {
        "raw": (
            "Artifact quality scan failed: unresolved import symbol 'Missing' "
            "from './sibling' in index.ts (sibling module does not define it)"
        ),
        "importer_path": "index.ts",
        "exporter_path": "sibling.ts",
        "specifier": "./sibling",
        "imported_symbol": "Missing",
    }
    assert issue.metadata["raw"] in evidence.errors


def test_artifact_quality_evidence_uses_direct_npm_script_missing_config_issue(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "jest --config jest.config.js --forceExit"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest script "
        "'test' references missing config file 'jest.config.js' in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "npm_script_missing_local_config"
    assert evidence.issues[0].source == "npm_script_config_scanner"
    assert evidence.issues[0].path == "package.json"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "manifest_path": "package.json",
        "script_name": "test",
        "config_path": "jest.config.js",
    }


def test_artifact_quality_evidence_uses_direct_npm_script_missing_entrypoint_issue(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "missing-entrypoint-project",
  "version": "1.0.0",
  "scripts": {
    "start": "node src/index.js"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest script "
        "'start' references missing local entrypoint 'src/index.js' in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "npm_script_missing_local_entrypoint"
    assert evidence.issues[0].source == "npm_script_entrypoint_scanner"
    assert evidence.issues[0].path == "package.json"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "manifest_path": "package.json",
        "script_name": "start",
        "entrypoint": "src/index.js",
    }


def test_artifact_quality_evidence_uses_direct_npm_script_test_directory_issue(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "test-directory-project",
  "version": "1.0.0",
  "scripts": {
    "test": "node --test tests"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "product.test.js").write_text("import test from 'node:test';\n", encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest script "
        "'test' references test directory 'tests' instead of concrete test files in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].code == "npm_script_node_test_directory_target"
    assert evidence.issues[0].source == "npm_script_test_target_scanner"
    assert evidence.issues[0].path == "package.json"
    assert evidence.issues[0].metadata == {
        "raw": evidence.errors[0],
        "manifest_path": "package.json",
        "script_name": "test",
        "target_directory": "tests",
    }


def test_typescript_import_scanner_ignores_fixture_string_imports(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "verify.test.ts").write_text(
        """
const VALID_WEB = `import { render } from "./engine/renderer";
export function boot(): void { render(); }`;
""".lstrip(),
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path))

    assert not any("unresolved import symbol 'render'" in error for error in errors)
    assert not any("unresolved relative import './engine/renderer'" in error for error in errors)
