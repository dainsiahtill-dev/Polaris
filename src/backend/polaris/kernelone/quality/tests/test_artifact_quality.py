"""Regression tests for shared artifact quality scanning."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import scan_workspace_artifact_quality


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
