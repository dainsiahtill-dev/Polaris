"""Compiler source maps are covered metadata, never execution evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.quality import ArtifactQualityEvidence, scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.artifact_quality import _scan as scanner
from polaris.kernelone.quality.tests.test_artifact_quality_static_scan import _effect_trap


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def _scan(root: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> ArtifactQualityEvidence:
    before = _hashes(root)
    with _effect_trap(monkeypatch) as effects:
        evidence = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", **kwargs)
    assert not effects
    assert _hashes(root) == before
    return evidence


def _assert_metadata(evidence: ArtifactQualityEvidence, path: str) -> None:
    assert evidence.scan_complete, evidence.to_dict()
    assert path in evidence.scanned_relative_paths
    issues = [issue for issue in evidence.issues if issue.path == path and issue.code == "non_executable_source_map"]
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert issues[0].metadata is not None
    assert issues[0].metadata["artifact_kind"] == "source_map"
    assert issues[0].metadata["execution_qualified"] is False
    assert issues[0].metadata["references_followed"] is False
    assert not any(check.relative_paths == (path,) for check in evidence.deferred_checks)


@pytest.mark.parametrize("failed_build", [False, True])
def test_real_tsc_success_and_failed_build_residue_are_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_build: bool
) -> None:
    compiler = shutil.which("tsc")
    assert compiler, "real TypeScript compiler required for source-map regression"
    _write(tmp_path, "src/value.ts", "export const value: number = 7;\n")
    _write(tmp_path, "test/value.test.ts", "import { value } from '../src/value';\nexport const check = value === 7;\n")
    config: dict[str, Any] = {
        "compilerOptions": {
            "rootDir": "src" if failed_build else ".",
            "outDir": "lib",
            "sourceMap": True,
            "target": "ES2020",
            "module": "CommonJS",
            "types": [],
        },
        "include": ["src/**/*.ts", "test/**/*.ts"],
    }
    _write(tmp_path, "tsconfig.json", json.dumps(config))
    result = subprocess.run(
        [compiler, "-p", "tsconfig.json"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert result.returncode == (2 if failed_build else 0), result.stdout + result.stderr
    if failed_build:
        assert "TS6059" in result.stdout
        path = "test/value.test.js.map"
        assert (tmp_path / path).is_file(), result.stdout
        config["compilerOptions"]["rootDir"] = "."
        _write(tmp_path, "tsconfig.json", json.dumps(config))
    else:
        path = "lib/test/value.test.js.map"
    evidence = _scan(tmp_path, monkeypatch)
    _assert_metadata(evidence, path)
    assert not evidence.checks_complete
    assert any(check.check_id == "typescript_project_typecheck" for check in evidence.deferred_checks)
    if failed_build:
        assert result.returncode == 2 and "TS6059" in result.stdout


@pytest.mark.parametrize(
    "payload",
    [
        {
            "version": 3,
            "sources": ["webpack://virtual.ts", "missing.ts"],
            "sourcesContent": ["export {}", None],
            "names": [],
            "mappings": "AAAA",
            "file": "absent.js",
        },
        {"version": 3, "sources": [], "names": [], "mappings": "", "sourceRoot": "https://example.invalid/"},
        {
            "version": 3,
            "sections": [
                {
                    "offset": {"line": 0, "column": 0},
                    "map": {"version": 3, "sources": [None], "names": [], "mappings": "AAAA"},
                }
            ],
        },
    ],
)
def test_metadata_references_are_not_followed_or_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict
) -> None:
    _write(tmp_path, "bundle.js.map", json.dumps(payload))
    _assert_metadata(_scan(tmp_path, monkeypatch), "bundle.js.map")


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "{}",
        '{"version":2,"sources":[],"mappings":""}',
        '{"version":3,"sources":[],"mappings":7}',
        '{"version":3,"sources":[],"mappings":"!"}',
        '{"version":3,"sources":[],"mappings":"g"}',
        '{"version":3,"sources":[],"mappings":"AA"}',
        '{"version":3,"sections":[{"offset":{"line":-1,"column":0},"map":{}}]}',
    ],
)
def test_malformed_source_map_remains_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    _write(tmp_path, "bundle.js.map", content)
    evidence = _scan(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert any(reason.endswith(":bundle.js.map") for reason in evidence.incomplete_reasons)
    assert not any(issue.code == "non_executable_source_map" for issue in evidence.issues)


def test_unknown_map_and_unknown_executable_still_block_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps({"version": 3, "sources": [], "mappings": ""})
    _write(tmp_path, "unknown.map", payload)
    _write(tmp_path, "worker.custom", "execute code")
    evidence = _scan(tmp_path, monkeypatch)
    assert "unsupported_source:unknown.map" in evidence.incomplete_reasons
    assert "unsupported_source:worker.custom" in evidence.incomplete_reasons


def test_source_map_symlink_is_unvisited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _write(tmp_path, "original.json", '{"version":3,"sources":[],"mappings":""}')
    (tmp_path / "bundle.js.map").symlink_to(target)
    evidence = _scan(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert "scope_symlink_unvisited:bundle.js.map" in evidence.incomplete_reasons
    assert "bundle.js.map" not in evidence.scanned_relative_paths


def test_declared_scope_keeps_unknown_source_and_map_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "src/bundle.js.map", '{"version":3,"sources":[],"mappings":""}')
    _write(tmp_path, "src/unknown.custom", "code")
    evidence = _scan(tmp_path, monkeypatch, relative_paths=("src",))
    assert not evidence.scan_complete
    assert "unsupported_source:src/unknown.custom" in evidence.incomplete_reasons
    assert "unsupported_source:src/bundle.js.map" not in evidence.incomplete_reasons
    assert any(
        issue.code == "non_executable_source_map" and issue.path == "src/bundle.js.map" for issue in evidence.issues
    )


def test_source_map_snapshot_drift_keeps_coverage_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, "bundle.js.map", '{"version":3,"sources":[],"mappings":""}')
    validate = scanner._source_map_structure_error

    def concurrent_change(value: Any, *, depth: int = 0) -> str:
        result = validate(value, depth=depth)
        path.write_text('{"version":3,"sources":[],"mappings":"A"}', encoding="utf-8")
        return result

    monkeypatch.setattr(scanner, "_source_map_structure_error", concurrent_change)
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert not evidence.scan_complete
    assert any("drift" in reason and "bundle.js.map" in reason for reason in evidence.incomplete_reasons)
