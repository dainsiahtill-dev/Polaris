"""Regression tests for shared artifact quality scanning."""

from __future__ import annotations

import json
import os
from pathlib import Path

import polaris.kernelone.quality.artifact_quality as artifact_quality_module
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


def _assert_file_marker_issue(
    tmp_path: Path,
    *,
    relative_path: str,
    content: str,
    code: str,
) -> ArtifactQualityIssue:
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=[relative_path])
    issue = next((candidate for candidate in evidence.issues if candidate.code == code), None)

    assert issue is not None
    assert issue.path == relative_path
    assert issue.source == "file_artifact_scanner"
    assert issue.metadata["artifact_path"] == relative_path
    assert issue.metadata["raw"] in evidence.errors
    return issue


def test_artifact_quality_evidence_uses_direct_file_marker_scaffold_issue(tmp_path: Path) -> None:
    issue = _assert_file_marker_issue(
        tmp_path,
        relative_path="src/scaffold.ts",
        content='export const label = "Polaris TypeScript scaffold";\n',
        code="deterministic_scaffold_marker",
    )

    assert "Polaris TypeScript scaffold" in issue.metadata["raw"]


def test_artifact_quality_evidence_uses_direct_file_marker_numeric_helper_filler_issue(
    tmp_path: Path,
) -> None:
    content = "\n".join(
        f"export function sampleHelper{index}(value: number): number {{ return value + {index}; }}"
        for index in range(5)
    )

    issue = _assert_file_marker_issue(
        tmp_path,
        relative_path="src/helpers.ts",
        content=content,
        code="repeated_numeric_helper_filler",
    )

    assert "count=5" in issue.metadata["raw"]


def test_artifact_quality_evidence_uses_direct_file_marker_generic_payload_index_store_issue(
    tmp_path: Path,
) -> None:
    helpers = "\n".join(
        f"export function storeHelper{index}(value: number): number {{ return value + {index}; }}" for index in range(3)
    )
    content = f"""
export interface ItemRecord {{
  payload: string;
  index: number;
}}

export class ItemStore {{
  private readonly items = new Map<string, ItemRecord>();
}}

{helpers}
""".strip()

    _assert_file_marker_issue(
        tmp_path,
        relative_path="src/store.ts",
        content=content,
        code="generic_payload_index_store_scaffold",
    )


def test_artifact_quality_evidence_uses_direct_file_marker_patch_residue_issue(tmp_path: Path) -> None:
    _assert_file_marker_issue(
        tmp_path,
        relative_path="src/merge.ts",
        content="<<<<<<< SEARCH\nconst value = 1;\n>>>>>>> REPLACE\n",
        code="patch_residue_marker",
    )


def test_artifact_quality_evidence_uses_direct_file_marker_trivial_arithmetic_tests_issue(
    tmp_path: Path,
) -> None:
    issue = _assert_file_marker_issue(
        tmp_path,
        relative_path="tests/arithmetic.test.ts",
        content="\n".join(
            [
                "expect(1 + 1).toBe(2);",
                "expect(2 + 2).toBe(4);",
                "expect(3 + 3).toBe(6);",
            ]
        ),
        code="repeated_trivial_arithmetic_tests",
    )

    assert "count=3" in issue.metadata["raw"]


def test_artifact_quality_evidence_emits_no_file_marker_issue_for_clean_artifact(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "engine.ts").write_text(
        "export function double(value: number): number { return value * 2; }\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/engine.ts"])

    assert evidence.errors == ()
    assert evidence.issues == ()


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


def test_artifact_quality_issue_mapping_prefers_typed_metadata_code() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "message": "typed package script issue",
                "source": "package_manifest_scanner",
                "metadata": {
                    "script_issue": "shell_command_substitution",
                    "script_issue_source": "package_manifest_scanner",
                },
            },
            {
                "message": "typed runtime module issue",
                "source": "runtime_smoke",
                "metadata": {"script_issue": "missing_compiled_entrypoint"},
            },
            {
                "message": "typed go issue",
                "metadata": {
                    "diagnostic_kind": "undefined_identifier",
                    "language": "go",
                    "identifier": "errString",
                },
            },
        )
    )

    assert [issue["code"] for issue in issues] == [
        "npm_manifest_invalid",
        "javascript_module_error",
        "go_compile_error",
    ]


def test_artifact_quality_evidence_uses_direct_typed_issue_for_missing_workspace(
    tmp_path: Path,
) -> None:
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path / "missing-workspace"),
    )

    assert evidence.errors == ("Artifact quality scan failed: workspace path does not exist",)
    assert [issue.code for issue in evidence.issues] == ["workspace_path_missing"]
    assert evidence.issues[0].source == "artifact_quality_scanner"
    metadata = evidence.issues[0].metadata or {}
    assert metadata.get("diagnostic_kind") == "workspace_path_missing"
    assert metadata.get("raw") == evidence.errors[0]


def test_artifact_quality_evidence_uses_direct_typed_issue_for_unresolved_workspace() -> None:
    evidence = scan_workspace_artifact_quality_evidence("\0")

    assert evidence.errors == ("Artifact quality scan failed: workspace path cannot be resolved",)
    assert [issue.code for issue in evidence.issues] == ["workspace_path_unresolved"]
    assert evidence.issues[0].source == "artifact_quality_scanner"
    metadata = evidence.issues[0].metadata or {}
    assert metadata.get("diagnostic_kind") == "workspace_path_unresolved"
    assert metadata.get("raw") == evidence.errors[0]


def test_artifact_quality_evidence_uses_direct_source_syntax_issue(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"broken": true,,}\n', encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "syntax_error"
    assert issue.path == "package.json"
    assert issue.source == "source_syntax_checker"
    assert issue.metadata["raw"] == evidence.errors[0]
    assert issue.metadata["diagnostic_kind"] == "syntax_error"


def test_artifact_quality_issue_projection_maps_source_syntax_diagnostic_kind() -> None:
    """Stable scanner metadata must classify syntax_error without display-string parsing."""

    issues = artifact_quality_issues_from_errors(
        (
            {
                "path": "src/main.ts",
                "source": "source_syntax_checker",
                "message": "scanner reported a source syntax problem",
                "metadata": {
                    "diagnostic_kind": "syntax_error",
                    "language": "typescript",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "syntax_error"
    assert issues[0]["path"] == "src/main.ts"
    assert issues[0]["source"] == "source_syntax_checker"
    assert issues[0]["metadata"]["diagnostic_kind"] == "syntax_error"
    assert issues[0]["metadata"]["language"] == "typescript"


def test_artifact_quality_issue_projection_maps_workspace_path_missing_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text."""

    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "artifact_quality_scanner",
                "message": "scanner reported a missing workspace path",
                "metadata": {
                    "diagnostic_kind": "workspace_path_missing",
                    "raw": "Artifact quality scan failed: workspace path does not exist",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "workspace_path_missing"
    assert issues[0]["source"] == "artifact_quality_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "workspace_path_missing"
    assert issues[0]["metadata"]["raw"] == "Artifact quality scan failed: workspace path does not exist"


def test_artifact_quality_issue_projection_maps_workspace_path_unresolved_diagnostic_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "artifact_quality_scanner",
                "message": "scanner reported an unresolved workspace path",
                "metadata": {
                    "diagnostic_kind": "workspace_path_unresolved",
                    "raw": "Artifact quality scan failed: workspace path cannot be resolved",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "workspace_path_unresolved"
    assert issues[0]["source"] == "artifact_quality_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "workspace_path_unresolved"


def test_artifact_quality_issue_projection_rejects_wrong_source_workspace_unresolved_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "file_artifact_scanner",
                "message": "scanner reported an unresolved workspace path",
                "metadata": {
                    "diagnostic_kind": "workspace_path_unresolved",
                    "raw": "Artifact quality scan failed: workspace path cannot be resolved",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] != "workspace_path_unresolved"


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
            "metadata": {
                "raw": error,
                "diagnostic_kind": "javascript_module_error",
            },
        },
    )


def test_artifact_quality_issue_projection_maps_javascript_module_diagnostic_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "runtime_smoke",
                "message": "opaque runtime smoke issue without module error text",
                "metadata": {
                    "diagnostic_kind": "javascript_module_error",
                    "raw": "runtime smoke captured a JavaScript module error",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "javascript_module_error"
    assert issues[0]["source"] == "runtime_smoke"


def test_artifact_quality_issue_projection_rejects_wrong_source_javascript_module_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "file_artifact_scanner",
                "message": "opaque runtime smoke issue without module error text",
                "metadata": {
                    "diagnostic_kind": "javascript_module_error",
                    "raw": "runtime smoke captured a JavaScript module error",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] != "javascript_module_error"


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
    error = (
        "Artifact quality scan failed: npm package manifest contains Python command in script 'test:py' in package.json"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"]["manifest_path"] == "package.json"
    assert issues[0]["metadata"]["script_name"] == "test:py"
    assert issues[0]["metadata"]["script_issue"] == "python_command"
    assert issues[0]["metadata"]["script_issue_source"] == "legacy_error_text"


def test_artifact_quality_issue_projection_marks_legacy_npm_script_issue_source() -> None:
    error = (
        "Artifact quality scan failed: npm package manifest script 'test' "
        "uses shell command substitution in package.json"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"]["manifest_path"] == "package.json"
    assert issues[0]["metadata"]["script_name"] == "test"
    assert issues[0]["metadata"]["script_issue"] == "shell_command_substitution"
    assert issues[0]["metadata"]["script_issue_source"] == "legacy_error_text"


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


def test_artifact_quality_issue_projection_extracts_missing_compiled_entrypoint() -> None:
    issues = artifact_quality_issues_from_errors(
        ("npm run start failed: Error: Cannot find module '/tmp/factory/project/dist/main.js'",)
    )

    assert issues[0]["code"] == "javascript_module_error"
    assert issues[0]["metadata"]["script_name"] == "start"
    assert issues[0]["metadata"]["script_issue"] == "missing_compiled_entrypoint"
    assert issues[0]["metadata"]["script_issue_source"] == "node_module_not_found"
    assert issues[0]["metadata"]["entrypoint"] == "dist/main.js"


def test_artifact_quality_issue_projection_extracts_go_undefined_identifier() -> None:
    issues = artifact_quality_issues_from_errors(("models/gallery.go:6:27: undefined: errString",))

    assert issues[0]["code"] == "go_compile_error"
    assert issues[0]["metadata"]["language"] == "go"
    assert issues[0]["metadata"]["diagnostic_kind"] == "undefined_identifier"
    assert issues[0]["metadata"]["identifier"] == "errString"


def test_artifact_quality_issue_projection_uses_module_type_diagnostic_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "message": "type=module package contains CommonJS runtime syntax",
                "path": "package.json",
                "source": "package_module_type_scanner",
                "metadata": {
                    "diagnostic_kind": "package_module_type_commonjs_mismatch",
                    "source_path": "src/index.js",
                    "declared_type": "module",
                    "runtime_syntax": "commonjs",
                },
            },
        )
    )

    assert issues[0]["code"] == "package_module_type_commonjs_mismatch"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["source"] == "package_module_type_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "package_module_type_commonjs_mismatch"
    assert issues[0]["metadata"]["source_path"] == "src/index.js"


def test_artifact_quality_issue_projection_extracts_undeclared_runtime_import() -> None:
    error = "Artifact quality scan failed: undeclared runtime import 'mongoose' in src/models/auditlog.ts"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "undeclared_runtime_import"
    assert issues[0]["path"] == "src/models/auditlog.ts"
    assert issues[0]["metadata"] == {
        "raw": error,
        "specifier": "mongoose",
        "package_root": "mongoose",
        "path": "src/models/auditlog.ts",
        "diagnostic_kind": "undeclared_runtime_import",
        "archetype": "missing_dependency",
    }


def test_artifact_quality_issue_projection_extracts_colon_line_column() -> None:
    error = "src/main.py:7:13: SyntaxError: invalid syntax"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["path"] == "src/main.py"
    assert issues[0]["line"] == 7
    assert issues[0]["column"] == 13
    assert issues[0]["metadata"] == {"raw": error}


def test_artifact_quality_issue_projection_extracts_rust_compiler_code_and_location() -> None:
    error = "error[E0583]: file not found for module `weather`\n  --> src/main.rs:2:1\n   |\n2  | mod weather;\n"

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
        expected_metadata = {"raw": error, "language": language}
        if language == "go":
            expected_metadata.update(
                {
                    "diagnostic_kind": "undefined_identifier",
                    "identifier": "Weather",
                }
            )
        assert issues[0]["metadata"] == expected_metadata


def test_artifact_quality_issue_projection_extracts_declared_target_metadata() -> None:
    error = "Artifact quality scan failed: declared target file missing 'src/main.py' is missing"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "declared_target_missing"
    assert issues[0]["path"] == "src/main.py"
    assert issues[0]["metadata"] == {"raw": error, "target_file": "src/main.py"}


def test_artifact_quality_issue_projection_extracts_npm_script_metadata() -> None:
    error = (
        "Artifact quality scan failed: npm package manifest script 'test' "
        'is a placeholder command: echo "Error: no test specified" && exit 1'
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"] == {
        "raw": error,
        "manifest_path": "package.json",
        "script_name": "test",
        "script_issue": "placeholder_command",
        "script_issue_source": "legacy_error_text",
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
        "script_issue_source": "legacy_error_text",
        "entrypoint": "src/index.js",
    }


def test_artifact_quality_issue_projection_extracts_node_test_runner_contract_metadata() -> None:
    error = "Artifact quality scan failed: test script must use node --test"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"] == {
        "raw": error,
        "manifest_path": "package.json",
        "script_name": "test",
        "script_issue": "node_test_runner_contract",
        "script_issue_source": "legacy_error_text",
    }


def test_artifact_quality_issue_projection_extracts_manifest_only_test_script_metadata() -> None:
    error = "Artifact quality scan failed: npm manifest-only test script in package.json"

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"] == {
        "raw": error,
        "manifest_path": "package.json",
        "script_name": "test",
        "script_issue": "manifest_only_test_script",
        "script_issue_source": "legacy_error_text",
    }


def test_artifact_quality_issue_projection_extracts_fixed_port_conflict_metadata() -> None:
    error = (
        "Artifact quality scan failed: step verify failed (exit 1): npm run serve :: "
        "Error: listen EADDRINUSE: address already in use 0.0.0.0:8080"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "npm_manifest_invalid"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["metadata"] == {
        "raw": error,
        "manifest_path": "package.json",
        "script_name": "serve",
        "script_issue": "fixed_port_conflict",
        "script_issue_source": "legacy_error_text",
    }


def test_artifact_quality_issue_projection_extracts_typescript_start_loader_metadata() -> None:
    error = (
        "Artifact quality scan failed: npm start :: node --loader ts-node/esm src/index.ts\n"
        "Error [ERR_REQUIRE_CYCLE_MODULE]: Cannot require() ES Module /workspace/src/index.ts"
    )

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["code"] == "javascript_module_error"
    assert issues[0]["metadata"] == {
        "raw": error,
        "diagnostic_kind": "javascript_module_error",
        "script_name": "start",
        "script_issue": "typescript_source_loader_require_cycle",
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


def test_artifact_quality_evidence_maps_cross_artifact_diagnostic_kind(tmp_path: Path) -> None:
    package_dir = tmp_path / "src" / "models"
    engine_dir = tmp_path / "src" / "engine"
    package_dir.mkdir(parents=True)
    engine_dir.mkdir(parents=True)
    (package_dir / "weather.py").write_text("class WeatherReport:\n    pass\n", encoding="utf-8")
    (engine_dir / "forecast.py").write_text(
        "from src.models.weather import WeatherKind\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path))
    issues = [issue.to_dict() for issue in evidence.issues]

    issue = next(item for item in issues if item["code"] == "unresolved_import_symbol")
    assert issue["source"] == "cross_artifact_consistency"
    assert issue["path"] == "src/engine/forecast.py"
    assert issue["metadata"]["diagnostic_kind"] == "unresolved_import_symbol"
    assert issue["metadata"]["importer_path"] == "src/engine/forecast.py"
    assert issue["metadata"]["owner_path"] == "src/models/weather.py"
    assert issue["metadata"]["symbol"] == "WeatherKind"
    assert issue["metadata"]["details"] == {"available_exports": ["WeatherReport"]}


def test_artifact_quality_issue_projection_maps_cross_artifact_metadata_only_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "cross_artifact_consistency",
                "message": "contract requires an owner export",
                "metadata": {
                    "diagnostic_kind": "contract_export_missing",
                    "owner_path": "src/models/weather.py",
                    "symbol": "WeatherReport",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "contract_export_missing"
    assert issues[0]["source"] == "cross_artifact_consistency"
    assert issues[0]["metadata"]["diagnostic_kind"] == "contract_export_missing"


def test_artifact_quality_issue_projection_rejects_wrong_cross_artifact_kind_source() -> None:
    wrong_source_issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "file_artifact_scanner",
                "message": "symbol import did not resolve",
                "metadata": {
                    "diagnostic_kind": "unresolved_import_symbol",
                    "symbol": "WeatherKind",
                },
            },
        )
    )
    unknown_kind_issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "cross_artifact_consistency",
                "message": "cross artifact diagnostic without legacy hints",
                "metadata": {"diagnostic_kind": "future_cross_artifact_code"},
            },
        )
    )

    assert wrong_source_issues[0]["code"] != "unresolved_import_symbol"
    assert unknown_kind_issues[0]["code"] != "future_cross_artifact_code"


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


def test_artifact_quality_issue_projection_maps_unresolved_relative_import_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text.

    The scanner now emits ``diagnostic_kind="unresolved_relative_import"`` plus
    ``source="typescript_import_scanner"``. The projection layer must map that
    contract directly to the canonical issue code, regardless of what the
    ``message`` field carries. Callers should not have to repackage scanner
    output into legacy strings to recover the typed code.
    """

    issues = artifact_quality_issues_from_errors(
        (
            {
                "path": "src/index.ts",
                "source": "typescript_import_scanner",
                # Message intentionally omits the legacy hint phrase so the
                # classifier cannot fall back to message-text matching.
                "message": "scanner reported a relative import that did not resolve",
                "metadata": {
                    "diagnostic_kind": "unresolved_relative_import",
                    "specifier": "./engine/runner",
                    "importer_path": "src/index.ts",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "unresolved_relative_import"
    assert issues[0]["path"] == "src/index.ts"
    assert issues[0]["source"] == "typescript_import_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "unresolved_relative_import"
    assert issues[0]["metadata"]["specifier"] == "./engine/runner"
    assert issues[0]["metadata"]["importer_path"] == "src/index.ts"


def test_artifact_quality_evidence_uses_direct_typescript_import_issue(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text('import { run } from "./engine/runner";\nrun();\n', encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/index.ts"])

    assert evidence.errors == (
        "Artifact quality scan failed: unresolved relative import './engine/runner' in src/index.ts",
    )
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "unresolved_relative_import"
    assert issue.source == "typescript_import_scanner"
    assert issue.path == "src/index.ts"
    # Assert metadata fields individually so each typed contract is documented in the test surface.
    metadata = dict(issue.metadata)
    assert metadata["raw"] == evidence.errors[0]
    assert metadata["importer_path"] == "src/index.ts"
    assert metadata["specifier"] == "./engine/runner"
    # The scanner-emitted diagnostic_kind must match the issue code so downstream
    # gates can key off the typed metadata contract rather than reparsing message prose.
    assert metadata["diagnostic_kind"] == "unresolved_relative_import"


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
    issue = evidence.issues[0]
    assert issue.code == "typescript_return_object_semicolon_property"
    assert issue.source == "typescript_syntax_red_flag_scanner"
    assert issue.path == "src/factory.ts"
    # Assert metadata fields individually so each typed contract is documented in
    # the test surface. ``diagnostic_kind`` is the scanner-emitted classifier that
    # lets downstream gates key off the typed metadata contract instead of
    # reparsing the legacy ``message`` string.
    metadata = dict(issue.metadata)
    assert metadata["raw"] == evidence.errors[0]
    assert metadata["path"] == "src/factory.ts"
    assert metadata["diagnostic_kind"] == "typescript_return_object_semicolon_property"


def test_artifact_quality_issue_projection_maps_typescript_return_object_semicolon_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text.

    The scanner now emits ``diagnostic_kind="typescript_return_object_semicolon_property"``
    plus ``source="typescript_syntax_red_flag_scanner"``. The projection layer must
    map that contract directly to the canonical issue code, regardless of what
    the ``message`` field carries. Callers should not have to repackage scanner
    output into legacy strings to recover the typed code.
    """

    issues = artifact_quality_issues_from_errors(
        (
            {
                "path": "src/factory.ts",
                "source": "typescript_syntax_red_flag_scanner",
                # Message intentionally omits the legacy hint phrase so the
                # classifier cannot fall back to message-text matching.
                "message": "scanner reported a semicolon-terminated property",
                "metadata": {
                    "diagnostic_kind": "typescript_return_object_semicolon_property",
                    "path": "src/factory.ts",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "typescript_return_object_semicolon_property"
    assert issues[0]["path"] == "src/factory.ts"
    assert issues[0]["source"] == "typescript_syntax_red_flag_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "typescript_return_object_semicolon_property"
    assert issues[0]["metadata"]["path"] == "src/factory.ts"


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
    issue = evidence.issues[0]
    assert issue.code == "html_module_script_typescript_source"
    assert issue.source == "html_module_script_scanner"
    assert issue.path == "index.html"
    # Assert metadata fields individually so each typed contract is documented in the test surface.
    metadata = dict(issue.metadata)
    assert metadata["raw"] == evidence.errors[0]
    assert metadata["html_path"] == "index.html"
    assert metadata["script_src"] == "./src/main.ts"
    assert metadata["diagnostic_kind"] == "html_module_script_typescript_source"


def test_artifact_quality_issue_code_from_typed_metadata_maps_html_module_script_diagnostic() -> None:
    """Project the HTML module-script diagnostic_kind to its canonical issue code."""

    issues = artifact_quality_issues_from_errors(
        (
            {
                "message": "HTML module script references TypeScript source",
                "path": "index.html",
                "source": "html_module_script_scanner",
                "metadata": {
                    "diagnostic_kind": "html_module_script_typescript_source",
                    "html_path": "index.html",
                    "script_src": "./src/main.ts",
                },
            },
        )
    )

    assert issues[0]["code"] == "html_module_script_typescript_source"
    assert issues[0]["path"] == "index.html"
    assert issues[0]["source"] == "html_module_script_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "html_module_script_typescript_source"
    assert issues[0]["metadata"]["script_src"] == "./src/main.ts"


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
    issue = evidence.issues[0]
    assert issue.code == "package_module_type_commonjs_mismatch"
    assert issue.source == "package_module_type_scanner"
    assert issue.path == "package.json"
    # Assert metadata fields individually so each typed contract is documented in the test surface.
    metadata = dict(issue.metadata)
    assert metadata["raw"] == evidence.errors[0]
    assert metadata["manifest_path"] == "package.json"
    assert metadata["source_path"] == "src/index.js"
    assert metadata["declared_type"] == "module"
    assert metadata["runtime_syntax"] == "commonjs"
    assert metadata["diagnostic_kind"] == "package_module_type_commonjs_mismatch"


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
            "#!/usr/bin/env sh\necho 'src/index.ts(1,7): error TS2322: bad type'\nexit 2\n",
            encoding="utf-8",
        )
        tsc.chmod(0o755)

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/index.ts"])

    assert evidence.errors == (
        "Artifact quality scan failed: TypeScript project typecheck failed: src/index.ts(1,7): error TS2322: bad type",
    )
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "typescript_project_typecheck_failed"
    assert issue.source == "typescript_project_typecheck"
    metadata = dict(issue.metadata)
    assert metadata["raw"] == evidence.errors[0]
    assert metadata["command"] == "tsc --noEmit --pretty false"
    assert metadata["exit_code"] == 2
    assert metadata["detail"] == "src/index.ts(1,7): error TS2322: bad type"
    assert metadata["diagnostic_kind"] == "typescript_project_typecheck_failed"


def test_artifact_quality_issue_projection_maps_typescript_project_typecheck_failed_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text."""

    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "typescript_project_typecheck",
                "message": "scanner reported a typecheck failure",
                "metadata": {
                    "diagnostic_kind": "typescript_project_typecheck_failed",
                    "command": "tsc --noEmit --pretty false",
                    "exit_code": 2,
                    "detail": "src/index.ts(1,7): error TS2322: bad type",
                },
            },
        )
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "typescript_project_typecheck_failed"
    assert issue["source"] == "typescript_project_typecheck"
    metadata = dict(issue["metadata"])
    assert metadata["diagnostic_kind"] == "typescript_project_typecheck_failed"
    assert metadata["command"] == "tsc --noEmit --pretty false"
    assert metadata["exit_code"] == 2
    assert metadata["detail"] == "src/index.ts(1,7): error TS2322: bad type"


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
    metadata = dict(issue.metadata)
    assert metadata["raw"] == (
        "Artifact quality scan failed: unresolved import symbol 'Missing' "
        "from './sibling' in index.ts (sibling module does not define it)"
    )
    assert metadata["importer_path"] == "index.ts"
    assert metadata["exporter_path"] == "sibling.ts"
    assert metadata["specifier"] == "./sibling"
    assert metadata["imported_symbol"] == "Missing"
    assert metadata["diagnostic_kind"] == "typescript_import_unresolved_symbol"
    assert metadata["raw"] in evidence.errors


def test_artifact_quality_issue_projection_maps_typescript_import_unresolved_symbol_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text."""

    issues = artifact_quality_issues_from_errors(
        (
            {
                "path": "index.ts",
                "source": "typescript_symbol_coherence_scanner",
                # Message intentionally omits the legacy hint phrase so the
                # classifier cannot fall back to message-text matching.
                "message": "scanner reported an unresolved symbol imported across modules",
                "metadata": {
                    "diagnostic_kind": "typescript_import_unresolved_symbol",
                    "importer_path": "index.ts",
                    "exporter_path": "sibling.ts",
                    "specifier": "./sibling",
                    "imported_symbol": "Missing",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "typescript_import_unresolved_symbol"
    assert issues[0]["path"] == "index.ts"
    assert issues[0]["source"] == "typescript_symbol_coherence_scanner"
    assert issues[0]["metadata"]["diagnostic_kind"] == "typescript_import_unresolved_symbol"
    assert issues[0]["metadata"]["importer_path"] == "index.ts"
    assert issues[0]["metadata"]["exporter_path"] == "sibling.ts"
    assert issues[0]["metadata"]["specifier"] == "./sibling"
    assert issues[0]["metadata"]["imported_symbol"] == "Missing"


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
        "script_issue": "missing_local_config",
        "script_issue_source": "npm_script_config_scanner",
        "script_name": "test",
        "config_path": "jest.config.js",
        "diagnostic_kind": "npm_script_missing_local_config",
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
        "script_issue": "missing_local_entrypoint",
        "script_issue_source": "npm_script_entrypoint_scanner",
        "script_name": "start",
        "entrypoint": "src/index.js",
        "diagnostic_kind": "npm_script_missing_local_entrypoint",
    }


def test_artifact_quality_evidence_projects_test_script_placeholder_metadata(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "placeholder-test-project",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 1"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm default failing test script in package.json",
        "Artifact quality scan failed: npm placeholder test script in package.json",
        "Artifact quality scan failed: npm package manifest script 'test' is a placeholder command: "
        'echo "Error: no test specified" && exit 1 in package.json',
    )
    assert [issue.metadata["script_issue"] for issue in evidence.issues] == [
        "default_failing_test_script",
        "placeholder_test_script",
        "placeholder_command",
    ]
    assert all(issue.code == "npm_manifest_invalid" for issue in evidence.issues)
    assert all(issue.source == "package_manifest_scanner" for issue in evidence.issues)
    assert all(issue.path == "package.json" for issue in evidence.issues)
    assert all(issue.metadata["script_name"] == "test" for issue in evidence.issues)
    assert [issue.metadata.get("script_issue_source") for issue in evidence.issues] == [
        "package_manifest_scanner",
        "package_manifest_scanner",
        "package_scripts",
    ]
    assert evidence.issues[2].metadata["package_script_issue_code"] == "npm_placeholder_script"
    assert evidence.issues[2].metadata["raw"] == evidence.errors[2]
    assert "placeholder command" in evidence.issues[2].message


def test_artifact_quality_evidence_projects_package_script_cycle_from_typed_gate(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "recursive-script-project",
  "version": "1.0.0",
  "scripts": {
    "build": "npm run verify",
    "verify": "npm run build"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest script 'build' "
        "recursively invokes itself via build -> verify -> build in package.json",
    )
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "npm_manifest_invalid"
    assert issue.source == "package_manifest_scanner"
    assert issue.metadata["script_issue"] == "recursive_invocation"
    assert issue.metadata["script_issue_source"] == "package_scripts"
    assert issue.metadata["package_script_issue_code"] == "npm_script_cycle"
    assert issue.metadata["cycle"] == ["build", "verify", "build"]
    assert issue.metadata["raw"] == evidence.errors[0]


def test_artifact_quality_evidence_projects_per_script_issue_metadata_directly(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        """
{
  "name": "shell-substitution-project",
  "version": "1.0.0",
  "scripts": {
    "test": "node --test $(find tests -name '*.js')"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest script 'test' uses shell command substitution in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].metadata["script_name"] == "test"
    assert evidence.issues[0].metadata["script_issue"] == "shell_command_substitution"
    assert evidence.issues[0].metadata["script_issue_source"] == "package_manifest_scanner"


def test_artifact_quality_evidence_projects_node_eval_syntax_per_script_issue_metadata(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "node-eval-syntax-project",
                "version": "1.0.0",
                "scripts": {
                    "test": 'node -e "console.log(\'missing close quote)"',
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert len(evidence.errors) == 1
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "npm_manifest_invalid"
    assert issue.path == "package.json"
    assert issue.source == "package_manifest_scanner"
    assert issue.metadata["script_name"] == "test"
    assert issue.metadata["script_issue"] == "invalid_node_eval_syntax"
    assert issue.metadata["script_issue_source"] == "package_manifest_scanner"
    assert issue.metadata["diagnostic_detail"]
    assert "SyntaxError" in issue.metadata["diagnostic_detail"]
    assert issue.metadata["raw"] == evidence.errors[0]


def test_artifact_quality_evidence_projects_node_eval_syntax_with_options_before_eval_flag(
    tmp_path: Path,
) -> None:
    """Regression: node options before -e must not hide invalid eval JavaScript."""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "node-eval-options-project",
                "version": "1.0.0",
                "scripts": {
                    "test": 'node --input-type module --no-warnings -e "console.log(\'missing close quote)"',
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert len(evidence.errors) == 1
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "npm_manifest_invalid"
    assert issue.path == "package.json"
    assert issue.source == "package_manifest_scanner"
    assert issue.metadata["script_name"] == "test"
    assert issue.metadata["script_issue"] == "invalid_node_eval_syntax"
    assert issue.metadata["script_issue_source"] == "package_manifest_scanner"
    assert issue.metadata["diagnostic_kind"] == "node_eval_syntax"
    assert issue.metadata["diagnostic_detail"]
    assert "SyntaxError" in issue.metadata["diagnostic_detail"]
    assert issue.metadata["raw"] == evidence.errors[0]


def test_artifact_quality_evidence_projects_typescript_dependency_metadata_directly(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"typescript-project","version":"1.0.0","scripts":{"build":"tsc"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.ts").write_text("export const value: number = 1;\n", encoding="utf-8")

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: TypeScript project requires 'typescript' devDependency in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].metadata["manifest_issue"] == "typescript_dependency_missing"
    assert evidence.issues[0].metadata["manifest_issue_source"] == "package_manifest_scanner"
    assert evidence.issues[0].metadata["package_name"] == "typescript"
    assert evidence.issues[0].metadata["dependency_section"] == "devDependencies"


def test_artifact_quality_evidence_projects_python_runtime_entrypoint_metadata_directly(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"python-entrypoint-project","version":"1.0.0","main":"main.py"}\n',
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest contains Python runtime entrypoint in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].metadata["manifest_issue"] == "python_runtime_entrypoint"
    assert evidence.issues[0].metadata["manifest_issue_source"] == "package_manifest_scanner"
    assert evidence.issues[0].metadata["entrypoint"] == "main.py"


def test_artifact_quality_evidence_projects_python_dependency_metadata_directly(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name":"python-dependency-project","version":"1.0.0","dependencies":{"pytest":"latest"}}\n',
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors == (
        "Artifact quality scan failed: npm package manifest declares Python package dependency 'pytest' "
        "in package.json",
    )
    assert len(evidence.issues) == 1
    assert evidence.issues[0].metadata["manifest_issue"] == "python_package_dependency"
    assert evidence.issues[0].metadata["manifest_issue_source"] == "package_manifest_scanner"
    assert evidence.issues[0].metadata["package_name"] == "pytest"
    assert evidence.issues[0].metadata["dependency_section"] == "dependencies"


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
    issue = evidence.issues[0]
    metadata = dict(issue.metadata)
    assert issue.code == "npm_script_node_test_directory_target"
    assert issue.source == "npm_script_test_target_scanner"
    assert issue.path == "package.json"
    assert metadata["script_name"] == "test"
    assert metadata["target_directory"] == "tests"
    assert metadata["script_issue"] == "node_test_directory_target"
    assert metadata["script_issue_source"] == "npm_script_test_target_scanner"
    assert metadata["diagnostic_kind"] == "npm_script_node_test_directory_target"
    # Preserve the legacy context fields required by callers that key on them.
    assert metadata["manifest_path"] == "package.json"
    assert metadata["raw"] == evidence.errors[0]


def test_artifact_quality_issue_projection_maps_npm_script_node_test_directory_target_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text."""

    issues = artifact_quality_issues_from_errors(
        (
            {
                "path": "package.json",
                "source": "npm_script_test_target_scanner",
                "message": "scanner reported a test script targeting a directory",
                "metadata": {
                    "diagnostic_kind": "npm_script_node_test_directory_target",
                    "script_name": "test",
                    "target_directory": "tests",
                    "script_issue": "node_test_directory_target",
                    "script_issue_source": "npm_script_test_target_scanner",
                    "manifest_path": "package.json",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "npm_script_node_test_directory_target"
    assert issues[0]["path"] == "package.json"
    assert issues[0]["source"] == "npm_script_test_target_scanner"
    metadata = dict(issues[0]["metadata"])
    assert metadata["diagnostic_kind"] == "npm_script_node_test_directory_target"
    assert metadata["script_name"] == "test"
    assert metadata["target_directory"] == "tests"
    assert metadata["script_issue"] == "node_test_directory_target"
    assert metadata["script_issue_source"] == "npm_script_test_target_scanner"
    assert metadata["manifest_path"] == "package.json"


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


def test_scan_workspace_artifact_quality_evidence_returns_typed_issue_on_scanner_infrastructure_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "engine.ts").write_text("export const value = 1;\n", encoding="utf-8")

    def _raise_os_error(_root: Path):
        raise OSError("scanner unavailable")

    monkeypatch.setattr(
        artifact_quality_module,
        "_iter_workspace_source_files",
        _raise_os_error,
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path))

    assert evidence.errors == ("Artifact quality scan failed: scanner unavailable",)
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "artifact_quality_scan_failed"
    assert issue.source == "artifact_quality_scanner"
    assert issue.message == "Artifact quality scan failed: scanner unavailable"
    assert issue.metadata["raw"] == "Artifact quality scan failed: scanner unavailable"
    assert issue.metadata["exception_type"] == "OSError"
    assert issue.metadata["diagnostic_kind"] == "artifact_quality_scan_failed"


def test_artifact_quality_issue_projection_maps_artifact_quality_scan_failed_diagnostic_kind() -> None:
    """Stable scanner metadata must classify without depending on message text.

    The scanner emits ``diagnostic_kind="artifact_quality_scan_failed"``
    plus ``source="artifact_quality_scanner"``. The projection layer must
    map that contract directly to the canonical issue code, regardless of
    what the ``message`` field carries. The payload is intentionally code-less
    so the classifier cannot fall back to a pre-assigned code field.
    """

    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "artifact_quality_scanner",
                # Message intentionally omits the legacy hint phrase so the
                # classifier cannot fall back to message-text matching.
                "message": "scanner reported an infrastructure failure during scan",
                "metadata": {
                    "diagnostic_kind": "artifact_quality_scan_failed",
                    "raw": "Artifact quality scan failed: scanner unavailable",
                    "exception_type": "OSError",
                },
            },
        )
    )

    assert len(issues) == 1
    assert issues[0]["code"] == "artifact_quality_scan_failed"
    assert issues[0]["source"] == "artifact_quality_scanner"
    metadata = dict(issues[0]["metadata"])
    assert metadata["diagnostic_kind"] == "artifact_quality_scan_failed"
    assert metadata["raw"] == "Artifact quality scan failed: scanner unavailable"
    assert metadata["exception_type"] == "OSError"


def test_artifact_quality_issue_projection_maps_npm_script_missing_local_diagnostic_kinds() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "npm_script_entrypoint_scanner",
                "message": "script references a missing entrypoint",
                "metadata": {
                    "diagnostic_kind": "npm_script_missing_local_entrypoint",
                    "manifest_path": "package.json",
                    "script_name": "start",
                    "entrypoint": "src/index.js",
                },
            },
            {
                "source": "npm_script_config_scanner",
                "message": "script references a missing config file",
                "metadata": {
                    "diagnostic_kind": "npm_script_missing_local_config",
                    "manifest_path": "package.json",
                    "script_name": "test",
                    "config_path": "jest.config.js",
                },
            },
        )
    )

    assert [issue["code"] for issue in issues] == [
        "npm_script_missing_local_entrypoint",
        "npm_script_missing_local_config",
    ]
    assert issues[0]["metadata"]["diagnostic_kind"] == "npm_script_missing_local_entrypoint"
    assert issues[1]["metadata"]["diagnostic_kind"] == "npm_script_missing_local_config"


def test_artifact_quality_issue_projection_rejects_wrong_source_npm_script_missing_local_kind() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            {
                "source": "file_artifact_scanner",
                "message": "entrypoint scanner issue without legacy hints",
                "metadata": {"diagnostic_kind": "npm_script_missing_local_entrypoint"},
            },
            {
                "source": "package_manifest_scanner",
                "message": "config scanner issue without legacy hints",
                "metadata": {"diagnostic_kind": "npm_script_missing_local_config"},
            },
        )
    )

    assert issues[0]["code"] != "npm_script_missing_local_entrypoint"
    assert issues[1]["code"] != "npm_script_missing_local_config"
