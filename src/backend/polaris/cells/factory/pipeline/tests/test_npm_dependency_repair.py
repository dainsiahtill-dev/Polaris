"""Tests for hallucinated npm dependency repair in WorkspaceQualityRunner.

Regression tests for the fix that removes non-existent npm packages from
package.json when npm install fails with ETARGET/notarget errors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import (
    WorkspaceQualityRunner,
)


class TestNpmDependencyRepair:
    """Validate repair_hallucinated_npm_dependencies removes bad deps."""

    def _write_package_json(self, tmp_path: Path, payload: dict) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def test_remove_nonexistent_dependency_etarget(self, tmp_path: Path) -> None:
        """npm ETARGET error -> remove the hallucinated package."""
        self._write_package_json(
            tmp_path,
            {
                "name": "test-project",
                "version": "1.0.0",
                "dependencies": {"alchemist": "0.0.0", "express": "^4.18.0"},
            },
        )
        runner = WorkspaceQualityRunner(tmp_path)
        stderr = "npm error code ETARGET\nnpm error notarget No matching version found for alchemist@0.0.0."
        removed = runner.repair_hallucinated_npm_dependencies(stderr)
        assert removed == ["alchemist"]
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert "alchemist" not in pkg["dependencies"]
        assert "express" in pkg["dependencies"]

    def test_remove_nonexistent_dependency_404(self, tmp_path: Path) -> None:
        """npm E404 error -> remove the hallucinated package."""
        self._write_package_json(
            tmp_path,
            {
                "name": "test-project",
                "dependencies": {"dream-forge": "1.0.0", "lodash": "^4.0.0"},
            },
        )
        runner = WorkspaceQualityRunner(tmp_path)
        stderr = "npm error code E404\nnpm error 404 Not Found: dream-forge@1.0.0"
        removed = runner.repair_hallucinated_npm_dependencies(stderr)
        assert removed == ["dream-forge"]
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert "dream-forge" not in pkg["dependencies"]
        assert "lodash" in pkg["dependencies"]

    def test_remove_from_devdependencies(self, tmp_path: Path) -> None:
        """Hallucinated dep in devDependencies -> also removed."""
        self._write_package_json(
            tmp_path,
            {
                "name": "test-project",
                "dependencies": {},
                "devDependencies": {"fake-tool": "0.0.1"},
            },
        )
        runner = WorkspaceQualityRunner(tmp_path)
        stderr = "npm error notarget No matching version found for fake-tool@0.0.1."
        removed = runner.repair_hallucinated_npm_dependencies(stderr)
        assert removed == ["fake-tool"]

    def test_no_removal_when_no_match(self, tmp_path: Path) -> None:
        """Unrelated npm error -> no changes."""
        self._write_package_json(
            tmp_path,
            {"name": "test-project", "dependencies": {"express": "^4.18.0"}},
        )
        runner = WorkspaceQualityRunner(tmp_path)
        stderr = "npm error code ECONNREFUSED\nnpm error network request failed"
        removed = runner.repair_hallucinated_npm_dependencies(stderr)
        assert removed == []
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert "express" in pkg["dependencies"]

    def test_no_package_json(self, tmp_path: Path) -> None:
        """Missing package.json -> no crash, empty result."""
        runner = WorkspaceQualityRunner(tmp_path)
        removed = runner.repair_hallucinated_npm_dependencies("notarget foo@1.0.0")
        assert removed == []

    def test_multiple_bad_deps(self, tmp_path: Path) -> None:
        """Multiple hallucinated deps -> all removed."""
        self._write_package_json(
            tmp_path,
            {
                "name": "test-project",
                "dependencies": {
                    "alchemist": "0.0.0",
                    "dream-engine": "1.0.0",
                    "express": "^4.18.0",
                },
            },
        )
        runner = WorkspaceQualityRunner(tmp_path)
        stderr = (
            "npm error notarget No matching version found for alchemist@0.0.0.\n"
            "npm error notarget No matching version found for dream-engine@1.0.0."
        )
        removed = runner.repair_hallucinated_npm_dependencies(stderr)
        assert sorted(removed) == ["alchemist", "dream-engine"]
        pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert "alchemist" not in pkg["dependencies"]
        assert "dream-engine" not in pkg["dependencies"]
        assert "express" in pkg["dependencies"]

    def test_empty_stderr(self, tmp_path: Path) -> None:
        """Empty stderr -> no changes."""
        self._write_package_json(tmp_path, {"name": "test", "dependencies": {"a": "1.0"}})
        runner = WorkspaceQualityRunner(tmp_path)
        assert runner.repair_hallucinated_npm_dependencies("") == []
