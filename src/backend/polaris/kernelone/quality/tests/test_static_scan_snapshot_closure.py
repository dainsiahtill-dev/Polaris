"""Regression gates for original source witnesses, not later path contents."""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.artifact_quality import _scan
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot


def _guard_peer(monkeypatch: pytest.MonkeyPatch, peer: Path) -> list[str]:
    attempts: list[str] = []

    def guard(operation: Any) -> Any:
        def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(file, (str, os.PathLike)) and Path(file).resolve().is_relative_to(peer):
                attempts.append(str(file))
                raise AssertionError("external content intercepted before open")
            return operation(file, *args, **kwargs)

        return opened

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    return attempts


@pytest.mark.parametrize(
    ("suffix", "content"),
    [("py", "def broken(:\n"), ("json", '{"broken":'), ("html", "<html><script>")],
)
@pytest.mark.parametrize("change", ["replace", "rewrite", "remove", "ancestor"])
def test_syntax_keeps_original_diagnostic_and_reports_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str, content: str, change: str
) -> None:
    """Reopening or accepting path drift loses the original syntax blocker."""
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    folder = root / "src"
    folder.mkdir(parents=True)
    peer.mkdir()
    source = folder / f"main.{suffix}"
    source.write_text(content, encoding="utf-8")
    outside = peer / source.name
    outside.write_text("x=1\n" if suffix == "py" else "{}" if suffix == "json" else "<html></html>", encoding="utf-8")
    original = _scan._check_source_file_syntax_static

    def interleave(*args: Any, **kwargs: Any) -> Any:
        if change == "rewrite":
            source.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            source.unlink()
            if change == "replace":
                source.symlink_to(outside)
            elif change == "ancestor":
                folder.rmdir()
                folder.symlink_to(peer, target_is_directory=True)
        return original(*args, **kwargs)

    # Rewrite content is captured before installing the external access guard.
    safe_text = outside.read_text(encoding="utf-8")
    if change == "rewrite":

        def interleave(*args: Any, **kwargs: Any) -> Any:
            source.write_text(safe_text, encoding="utf-8")
            return original(*args, **kwargs)

    attempts = _guard_peer(monkeypatch, peer)
    monkeypatch.setattr(_scan, "_check_source_file_syntax_static", interleave)
    evidence = scan_workspace_artifact_quality_evidence(
        str(root), scan_mode="pure_static", relative_paths=[f"src/{source.name}"]
    )
    assert not attempts
    assert not evidence.scan_complete
    assert any(issue.code == "syntax_error" for issue in evidence.issues)
    assert any("drift" in reason for reason in evidence.incomplete_reasons)


@pytest.mark.parametrize("change", ["replace", "rewrite", "remove", "ancestor"])
def test_aggregate_and_ledger_share_original_transitive_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A second index read must not erase a known missing-symbol blocker."""
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    folder = root / "src"
    folder.mkdir(parents=True)
    peer.mkdir()
    (root / "main.ts").write_text("import {known, missing} from './barrel';\n", encoding="utf-8")
    (root / "barrel.ts").write_text("export * from './src/leaf';\n", encoding="utf-8")
    source = folder / "leaf.ts"
    source.write_text("export const known = '中文';\n", encoding="utf-8")
    outside = peer / "leaf.ts"
    replacement = "export const known=1; export const missing=2;\n"
    outside.write_text(replacement, encoding="utf-8")
    ledger = root / ".polaris/runtime/contracts/interface_ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        '{"schema_version":"interface-ledger/1","files":{"src/leaf.ts":{"public_symbols":["expected_public_symbol"]}}}',
        encoding="utf-8",
    )
    original = _scan.scan_cross_artifact_consistency

    def interleave(*args: Any, **kwargs: Any) -> Any:
        if change == "rewrite":
            source.write_text(replacement, encoding="utf-8")
        else:
            source.unlink()
            if change == "replace":
                source.symlink_to(outside)
            elif change == "ancestor":
                folder.rmdir()
                folder.symlink_to(peer, target_is_directory=True)
        return original(*args, **kwargs)

    attempts = _guard_peer(monkeypatch, peer)
    monkeypatch.setattr(_scan, "scan_cross_artifact_consistency", interleave)
    evidence = scan_workspace_artifact_quality_evidence(
        str(root), scan_mode="pure_static", relative_paths=["main.ts", "src/leaf.ts"]
    )
    assert not attempts
    assert not evidence.scan_complete
    assert any(issue.symbol == "missing" for issue in evidence.cross_artifact_issues)
    assert any("expected_public_symbol" in issue.message for issue in evidence.issues)
    assert any("drift" in reason for reason in evidence.incomplete_reasons)


@pytest.mark.parametrize("failure", [PermissionError, OSError, UnicodeDecodeError])
def test_real_reader_failure_is_unknown_without_losing_readable_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    (tmp_path / "main.ts").write_text("import {missing} from './good'; import {x} from './bad';\n", encoding="utf-8")
    (tmp_path / "good.ts").write_text("export const present=1;\n", encoding="utf-8")
    (tmp_path / "bad.ts").write_text("export const x=1;\n", encoding="utf-8")
    original = Path.read_text

    def fail_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name == "bad.ts":
            if failure is UnicodeDecodeError:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "injected actual read failure")
            raise failure("injected actual read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        scan_mode="pure_static",
        relative_paths=["main.ts"],
        interface_contract={"interfaces": [{"owner_path": "bad.ts", "name": "x"}]},
    )
    assert not evidence.scan_complete
    assert any("bad.ts" in reason for reason in evidence.incomplete_reasons)
    assert any(issue.symbol == "missing" for issue in evidence.cross_artifact_issues)
    assert not any(issue.symbol == "x" for issue in evidence.cross_artifact_issues)


@pytest.mark.parametrize(
    ("suffix", "content", "has_error"),
    [
        ("py", "变量 = '中文'\n", False),
        ("py", "def broken(:\n", True),
        ("json", '{"中文":1}', False),
        ("json", '{"中文":', True),
        ("html", "<html></html>", False),
        ("html", "<html><script>", True),
    ],
)
def test_static_syntax_normal_diagnostics(tmp_path: Path, suffix: str, content: str, has_error: bool) -> None:
    source = tmp_path / f"main.{suffix}"
    source.write_text(content, encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert any(issue.code == "syntax_error" for issue in evidence.issues) is has_error
    assert evidence.scan_complete


def test_controlled_index_rejects_wrong_workspace(tmp_path: Path) -> None:
    from polaris.kernelone.quality.cross_artifact_interfaces import build_symbol_index_snapshot

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="workspace binding mismatch"):
        build_symbol_index_snapshot(tmp_path, reader=WorkspaceReadSnapshot(other))


@pytest.mark.parametrize("suffix", ["ts", "py"])
def test_index_resolves_owner_from_original_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """Live filesystem resolution must not erase already witnessed imports."""
    from polaris.kernelone.quality import cross_artifact_interfaces as interfaces

    main = tmp_path / f"main.{suffix}"
    leaf = tmp_path / f"leaf.{suffix}"
    main.write_text(
        "import {missing} from './leaf';\n" if suffix == "ts" else "from leaf import missing\n", encoding="utf-8"
    )
    leaf.write_text("export const known=1;\n" if suffix == "ts" else "known=1\n", encoding="utf-8")
    reader = WorkspaceReadSnapshot(tmp_path)
    reader.read(main)
    reader.read(leaf)
    original = interfaces._scan_file_interfaces

    def interleave(*args: Any, **kwargs: Any) -> Any:
        if args[1] == main.name:
            leaf.unlink()
        return original(*args, **kwargs)

    monkeypatch.setattr(interfaces, "_scan_file_interfaces", interleave)
    snapshot = interfaces.build_symbol_index_snapshot(tmp_path, reader=reader)
    issues = interfaces.scan_cross_artifact_consistency(tmp_path, source_snapshot=snapshot)
    assert any(issue.symbol == "missing" and issue.owner_path == leaf.name for issue in issues)


def test_scoped_multiple_specifiers_and_ledger_read_each_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross and ledger validation must not build independent content views."""
    sources = {
        "main.ts": "import {missingA} from './a'; import {missingB} from './b';\n",
        "a.ts": "export const presentA='中文';\n",
        "b.ts": "export const presentB=2;\n",
        "ignored.ts": "import {unrelated} from './a';\n",
    }
    for name, text in sources.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    ledger = tmp_path / ".polaris/runtime/contracts/interface_ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"files":{"a.ts":{"public_symbols":["presentA"]}}}', encoding="utf-8")
    reads: list[str] = []
    original = Path.read_text

    def counted(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.suffix == ".ts":
            reads.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), scan_mode="pure_static", relative_paths=iter(["main.ts", "a.ts"])
    )
    assert evidence.scan_complete
    assert {issue.symbol for issue in evidence.cross_artifact_issues} == {"missingA", "missingB"}
    assert all(reads.count(name) == 1 for name in sources)
    assert not any(issue.code == "declared_interface_missing" for issue in evidence.issues)


@pytest.mark.parametrize(
    ("suffix", "source", "symbol"),
    [
        ("ts", "export const present='中文';\n", "present"),
        ("js", "export const present='中文';\n", "present"),
        ("py", "present='中文'\n", "present"),
        ("go", "package main\nfunc Present() {}\n", "Present"),
    ],
)
@pytest.mark.parametrize("require_missing", [False, True])
def test_normal_multilanguage_contract_diagnostics(
    tmp_path: Path, suffix: str, source: str, symbol: str, require_missing: bool
) -> None:
    path = f"main.{suffix}"
    (tmp_path / path).write_text(source, encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        scan_mode="pure_static",
        interface_contract={"interfaces": [{"owner_path": path, "name": "missing" if require_missing else symbol}]},
    )
    assert evidence.scan_complete
    assert any(issue.code == "contract_export_missing" for issue in evidence.cross_artifact_issues) is require_missing


def test_valid_python_package_transitive_imports(tmp_path: Path) -> None:
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / "leaf.py").write_text("def present():\n    return '中文'\n", encoding="utf-8")
    (folder / "__init__.py").write_text("from .leaf import present\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from pkg import present\n", encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), scan_mode="pure_static", relative_paths=["main.py"]
    )
    assert evidence.scan_complete
    assert not evidence.cross_artifact_issues
