"""Tests for hallucinated npm dependency repair in WorkspaceQualityRunner.

Regression tests for the fix that removes non-existent npm packages from
package.json when npm install fails with ETARGET/notarget errors.
"""

from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import (
    WorkspaceQualityRunner,
)


class TestNpmDependencyRepair:
    """Validate repair_hallucinated_npm_dependencies removes bad deps."""

    def _write_package_json(self, tmp_path: Path, payload: dict) -> None:
        (tmp_path / "package.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

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
        receipts = runner.consume_repair_receipts()
        assert len(receipts) == 1
        assert receipts[0]["source_tool"] == "factory_workspace_quality.hallucinated_npm_dependency"
        assert receipts[0]["path"] == "package.json"
        assert receipts[0]["metadata"]["removed_dependencies"] == ["alchemist"]
        assert receipts[0]["runtime_migration_required"] is True
        assert runner.consume_repair_receipts() == []

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

    def test_workspace_quality_skips_long_lived_http_server_start(self, tmp_path: Path) -> None:
        """Static web server start scripts should not be required to exit for workspace validation."""
        self._write_package_json(
            tmp_path,
            {
                "name": "web-project",
                "scripts": {
                    "build": "tsc -p tsconfig.json",
                    "test": "npm run build",
                    "start": "npx --yes http-server . -p ${PORT:-0} -c-1",
                },
            },
        )

        commands = WorkspaceQualityRunner(tmp_path).workspace_quality_commands({})

        assert ["npm", "run", "build"] in commands
        assert ["npm", "test"] in commands
        assert ["npm", "run", "start"] not in commands

    def test_workspace_quality_keeps_exiting_node_start_smoke(self, tmp_path: Path) -> None:
        """CLI-style npm start scripts still run as entrypoint smoke checks."""
        self._write_package_json(
            tmp_path,
            {
                "name": "cli-project",
                "scripts": {
                    "build": "tsc -p tsconfig.json",
                    "start": "node dist/main.js",
                },
            },
        )

        commands = WorkspaceQualityRunner(tmp_path).workspace_quality_commands({})

        assert ["npm", "run", "build"] in commands
        assert ["npm", "run", "start"] in commands

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

    def test_cjs_export_import_mismatch_uses_controlled_patch_receipt(self, tmp_path: Path) -> None:
        """CJS direct export mismatch -> controlled patch with receipt evidence."""
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "Dream.js").write_text(
            "class Dream {}\nmodule.exports = Dream;\n",
            encoding="utf-8",
        )
        consumer = tmp_path / "src" / "main.js"
        consumer.write_text(
            'const { Dream } = require("./models/Dream");\nmodule.exports = new Dream();\n',
            encoding="utf-8",
        )
        runner = WorkspaceQualityRunner(tmp_path)

        repairs = runner.repair_cjs_export_import_mismatch()

        assert repairs == [{"file": "src/main.js", "fix": "destructure_to_direct:Dream"}]
        assert consumer.read_text(encoding="utf-8").startswith('const Dream = require("./models/Dream");')
        receipts = runner.consume_repair_receipts()
        assert len(receipts) == 1
        assert receipts[0]["source_tool"] == "factory_workspace_quality.cjs_export_import_mismatch"
        assert receipts[0]["path"] == "src/main.js"
        assert receipts[0]["before_hash"] != receipts[0]["after_hash"]

    def test_trim_mismatch_uses_controlled_patch_receipt(self, tmp_path: Path) -> None:
        """Whitespace assertion mismatch -> controlled test patch with receipt evidence."""
        (tmp_path / "tests").mkdir()
        test_file = tmp_path / "tests" / "behavior.test.js"
        test_file.write_text(
            'const assert = require("assert");\nassert.strictEqual(result.name, "Tide");\n',
            encoding="utf-8",
        )
        runner = WorkspaceQualityRunner(tmp_path)

        patched = runner.repair_test_trim_mismatch("AssertionError: ' Tide ' !== 'Tide'")

        assert patched == ["tests/behavior.test.js"]
        assert "result.name.trim()" in test_file.read_text(encoding="utf-8")
        receipts = runner.consume_repair_receipts()
        assert len(receipts) == 1
        assert receipts[0]["source_tool"] == "factory_workspace_quality.test_trim_mismatch"
        assert receipts[0]["path"] == "tests/behavior.test.js"
        assert receipts[0]["metadata"]["stderr_pattern"] == "whitespace_only_assertion_diff"
