"""Regression tests for shared artifact quality scanning."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import (
    artifact_quality_issues_from_errors,
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)


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


def test_artifact_quality_issue_projection_extracts_compiler_path() -> None:
    error = "src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'."

    issues = artifact_quality_issues_from_errors((error,))

    assert issues[0]["path"] == "src/main.ts"
    assert issues[0]["metadata"] == {"raw": error}


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
