"""Static coverage is discovery, never Node execution authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.tests.test_artifact_quality_static_scan import _effect_trap


def _fixture(root: Path, script: str) -> None:
    (root / "package.json").write_text(json.dumps({"scripts": {"test": script}}), encoding="utf-8")
    (root / "verify.test.js").write_text(
        "import {test} from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "test('parse input', () => assert.equal(JSON.parse('{\"x\":2}').x, 2));\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "script", ["node --test", "node --test verify.test.js", "node --test --import tsx verify.test.js"]
)
def test_node_test_is_covered_but_test_execution_remains_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _fixture(tmp_path, script)
    with _effect_trap(monkeypatch) as effects:
        evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert effects == []
    assert evidence.scan_complete, evidence.incomplete_reasons
    assert not evidence.checks_complete
    assert any(check.check_id == "javascript_parser" for check in evidence.deferred_checks)


@pytest.mark.parametrize("name", ["README.md", "requirements.md", "docs/design.markdown"])
def test_documentation_has_explicit_non_executable_classification(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Project\nThis document describes expected behavior.\n", encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert evidence.scan_complete, evidence.incomplete_reasons
    assert not evidence.errors
    assert any(
        issue.code == "non_executable_document" and issue.path == name and issue.severity == "info"
        for issue in evidence.issues
    )


@pytest.mark.parametrize(
    "script",
    [
        "node --test --unknown verify.test.js",
        "node --test verify.test.js --unknown",
        "node --test --import",
        "node --test --import --unknown verify.test.js",
        "node --test README.md",
        "node README.md",
        "node --test verify.test.js && mystery-command --flag",
        "node --test verify.test.js && node --unknown",
        "node --test verify.test.js && node README.md",
        "node --test $(printf verify.test.js)",
        "node --test verify.test.js || true",
        "node --test -e 'process.exit(0)'",
        "node --test --import data:text/javascript,process.exit(0)",
        "node --test --require arbitrary-loader verify.test.js",
        "node --test verify.test.js && npm exec arbitrary-command",
        "node --test verify.test.js && tsc --arbitrary-option",
        "python3 README.md",
        "node --test verify.test.js || node --test verify.test.js",
        "node --test verify.test.js ; node --test verify.test.js",
        "node --test verify.test.js | node --test verify.test.js",
        "NODE_OPTIONS='--unknown' node --test verify.test.js",
        "env NODE_OPTIONS='--unknown' node --test verify.test.js",
        "node --test verify.test.js &&",
        "&& node --test verify.test.js",
        "node --test verify.test.js && && node --test verify.test.js",
    ],
)
def test_node_test_unknown_or_document_execution_cannot_claim_complete(tmp_path: Path, script: str) -> None:
    _fixture(tmp_path, script)
    (tmp_path / "README.md").write_text("console.log('not executable documentation');\n", encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert not evidence.scan_complete or evidence.errors


def test_declared_npm_build_before_node_test_is_bounded(tmp_path: Path) -> None:
    _fixture(tmp_path, "npm run build && node --test --import tsx verify.test.js")
    package = tmp_path / "package.json"
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["scripts"]["build"] = "tsc -p tsconfig.json"
    package.write_text(json.dumps(payload), encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert evidence.scan_complete, evidence.incomplete_reasons


@pytest.mark.parametrize(
    "script",
    [
        "node -- README.md",
        "node --import ./README.md main.js",
        "node --import=./README.md main.js",
        "node --require ./README.md main.js",
        "node --require=./README.md main.js",
        "node -r ./README.md main.js",
    ],
)
def test_declared_node_document_execution_is_rejected(tmp_path: Path, script: str) -> None:
    _fixture(tmp_path, script)
    (tmp_path / "README.md").write_text("# Ordinary documentation\n", encoding="utf-8")
    (tmp_path / "main.js").write_text("export const value = 2;\n", encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert not evidence.scan_complete
    assert any("node_executable_document:" in reason for reason in evidence.incomplete_reasons)


def test_document_symlink_and_receipt_contamination_still_rejected(tmp_path: Path) -> None:
    (tmp_path / "README.md").symlink_to(tmp_path / "absent")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert not evidence.scan_complete
    (tmp_path / "README.md").unlink()
    (tmp_path / "README.md").write_text("**write_file**: error director_write_policy_denied", encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert evidence.errors
