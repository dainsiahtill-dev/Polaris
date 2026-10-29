"""Manifest faults stay with their writer instead of failing every source task."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_repair_message,
    _collect_materialization_quality_errors,
)
from polaris.kernelone.quality import scan_workspace_artifact_quality


def _write_project(workspace: Path) -> list[str]:
    (workspace / "src").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "src/app.js").write_text(
        "function double(value) {\n"
        "  if (!Number.isFinite(value)) throw new TypeError('number required');\n"
        "  return value * 2;\n"
        "}\nmodule.exports = { double };\n",
        encoding="utf-8",
    )
    (workspace / "tests/app.test.js").write_text(
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "const { double } = require('../src/app.js');\n"
        "test('doubles numbers', () => assert.equal(double(3), 6));\n",
        encoding="utf-8",
    )
    (workspace / "package.json").write_text(
        json.dumps(
            {
                "name": "manifest-ownership-test",
                "version": "1.0.0",
                "scripts": {
                    "build": "node --check src/app.js",
                    "test": "node --test tests/app.test.js",
                    "start": "node src/app.js",
                    "serve": "node scripts/serve.js",
                },
            }
        ),
        encoding="utf-8",
    )
    return ["package.json", "src/app.js", "tests/app.test.js"]


def test_source_task_routes_unowned_script_fault_to_declared_manifest_owner(tmp_path: Path) -> None:
    project_targets = _write_project(tmp_path)
    context = {"project_declared_target_files": project_targets}

    errors = _collect_materialization_quality_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/app.js"]},
        all_affected_files=["src/app.js"],
        workspace_name=tmp_path.name,
        context=context,
        task_boundary=True,
    )

    assert errors == []
    deferred = context["director_task_boundary_deferred_quality_errors"]
    assert len(deferred) == 1
    assert deferred[0]["target_files"] == ["package.json"]
    assert "scripts/serve.js" in deferred[0]["artifact_quality_errors"][0]
    assert deferred[0]["artifact_quality_issues"][0]["path"] == "package.json"
    # The same untouched workspace must still fail full-project acceptance.
    assert any("scripts/serve.js" in error for error in scan_workspace_artifact_quality(tmp_path))
    assert not (tmp_path / "scripts/serve.js").exists()


def test_manifest_owner_still_fails_for_its_unowned_script(tmp_path: Path) -> None:
    project_targets = _write_project(tmp_path)
    context = {"project_declared_target_files": project_targets}

    errors = _collect_materialization_quality_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["package.json"]},
        all_affected_files=["package.json"],
        workspace_name=tmp_path.name,
        context=context,
        task_boundary=True,
    )

    assert any("scripts/serve.js" in error for error in errors)
    assert not context.get("director_task_boundary_deferred_quality_errors")


def test_source_task_keeps_manifest_fault_when_no_manifest_owner_is_declared(tmp_path: Path) -> None:
    _write_project(tmp_path)
    context = {"project_declared_target_files": ["src/app.js"]}

    errors = _collect_materialization_quality_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/app.js"]},
        all_affected_files=["src/app.js"],
        workspace_name=tmp_path.name,
        context=context,
        task_boundary=True,
    )

    assert any("scripts/serve.js" in error for error in errors)
    assert not context.get("director_task_boundary_deferred_quality_errors")


def test_serve_repair_request_does_not_direct_changes_to_unrelated_test_script(tmp_path: Path) -> None:
    _write_project(tmp_path)
    message = _build_materialization_quality_repair_message(
        original_message="Prepare project configuration.",
        artifact_quality_errors=[
            "Artifact quality scan failed: npm package manifest script "
            "'serve' references missing local entrypoint 'scripts/serve.js' in package.json"
        ],
        changed_files=["package.json"],
        repair_target_files=["package.json"],
        workspace_full=str(tmp_path),
    )

    manifest_guidance = message.split("NPM PACKAGE MANIFEST REPAIR:", 1)[1].split("EDIT CONSISTENCY PREFLIGHT", 1)[0]
    assert "Make `npm test`" not in manifest_guidance
    assert "test script" not in manifest_guidance
    assert "scripts/serve.js" in message.split("Quality errors:", 1)[1]
    assert "tests/app.test.js" in message  # Existing scripts remain visible as read-only context.
