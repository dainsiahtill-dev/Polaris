"""Regression tests for shared artifact quality scanning."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import (
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
    assert evidence.issues[0].code == "npm_manifest_invalid"
    assert evidence.issues[0].path == "package.json"
    assert evidence.to_dict()["issues"][0]["code"] == "npm_manifest_invalid"


def test_artifact_quality_evidence_uses_direct_typed_issue_for_missing_workspace(
    tmp_path: Path,
) -> None:
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path / "missing-workspace"),
    )

    assert evidence.errors == ("Artifact quality scan failed: workspace path does not exist",)
    assert [issue.code for issue in evidence.issues] == ["workspace_path_missing"]
    assert evidence.issues[0].source == "artifact_quality_scanner"


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
