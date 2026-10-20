"""Repository metadata has file coverage without source execution credit."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.tests.test_source_map_artifact_coverage import _scan, _write


@pytest.mark.parametrize("name, content", [(".gitignore", "node_modules/\ndist/\n*.log\n"), ("nested/.gitignore", "")])
def test_exact_repository_metadata_has_non_executable_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, content: str
) -> None:
    _write(tmp_path, "src/value.ts", "export const value: number = 7;\n")
    _write(tmp_path, name, content)
    evidence = _scan(tmp_path, monkeypatch)
    assert evidence.scan_complete, evidence.to_dict()
    assert name in evidence.scanned_relative_paths
    issues = [issue for issue in evidence.issues if issue.path == name]
    metadata = [issue for issue in issues if issue.code == "non_executable_repository_metadata"]
    assert len(metadata) == 1
    assert metadata[0].metadata == {"artifact_kind": "repository_metadata", "execution_qualified": False}
    assert not any(check.relative_paths == (name,) for check in evidence.deferred_checks)
    assert any(check.relative_paths == ("src/value.ts",) for check in evidence.deferred_checks)
    assert not evidence.checks_complete


@pytest.mark.parametrize("name", ["unknown.custom", ".GITIGNORE"])
def test_unknown_artifacts_remain_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _write(tmp_path, name, "unclassified code\n")
    evidence = _scan(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert f"unsupported_source:{name}" in evidence.incomplete_reasons
    assert not any(issue.code == "non_executable_repository_metadata" for issue in evidence.issues)


def test_source_disguised_as_metadata_keeps_semantic_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, ".gitignore.ts", "export const value: number = 7;\n")
    evidence = _scan(tmp_path, monkeypatch)
    assert evidence.scan_complete
    assert not evidence.checks_complete
    assert any(check.relative_paths == (".gitignore.ts",) for check in evidence.deferred_checks)
    assert not any(issue.code == "non_executable_repository_metadata" for issue in evidence.issues)


def test_metadata_keeps_invalid_utf8_and_symlink_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".gitignore"
    path.write_bytes(b"\xff")
    evidence = _scan(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert any(".gitignore" in reason for reason in evidence.incomplete_reasons)
    path.unlink()
    target = _write(tmp_path, "patterns.md", "dist/\n")
    path.symlink_to(target)
    evidence = _scan(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert "scope_symlink_unvisited:.gitignore" in evidence.incomplete_reasons
    assert ".gitignore" not in evidence.scanned_relative_paths


def test_metadata_snapshot_change_remains_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.quality.artifact_quality import _scan as scanner

    path = _write(tmp_path, ".gitignore", "dist/\n")
    original = scanner._scan_file_evidence

    def changing_scan(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text("changed/\n", encoding="utf-8")
        return result

    monkeypatch.setattr(scanner, "_scan_file_evidence", changing_scan)
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert not evidence.scan_complete
    assert any("drift" in reason and ".gitignore" in reason for reason in evidence.incomplete_reasons)


def test_metadata_size_limit_remains_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, ".gitignore", "a" * 1_000_001)
    evidence = _scan(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert "file_truncated:.gitignore" in evidence.incomplete_reasons
