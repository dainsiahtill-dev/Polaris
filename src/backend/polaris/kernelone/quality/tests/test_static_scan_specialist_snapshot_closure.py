"""Specialist reads must retain original blockers and reject unknown absence."""

from __future__ import annotations

import builtins
import io
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.artifact_quality import _scan
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot


def _sources(root: Path, sources: dict[str, str]) -> None:
    for name, text in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


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


CASES = {
    "term": (
        {"src/main.py": "REQUIRED_TERM_PAIRS=[]\nREQUIRED_TERMS=[]\nfor term in REQUIRED_TERMS:\n    pass\n"},
        "src/main.py",
        "_scan_python_acceptance_term_pair_evidence",
        "python_unused_term_pairs",
    ),
    "package": (
        {
            "src/package.json": "{}",
            "package.json": '{"scripts":{"test":"npm run test:js","test:js":"node --test"}}',
            "main.py": "assertIn('node', scripts.test)\n",
        },
        "package.json",
        "_scan_python_acceptance_npm_node_alias_evidence",
        "python_npm_node_alias_assert",
    ),
    "importer": (
        {"src/main.js": "import {missing} from './leaf.js';\n", "src/leaf.js": "export const present='中文';\n"},
        "src/main.js",
        "_scan_javascript_named_export_evidence",
        "javascript_missing_named_export",
    ),
    "exporter": (
        {"main.js": "import {missing} from './src/leaf.js';\n", "src/leaf.js": "export const present='中文';\n"},
        "src/leaf.js",
        "_scan_javascript_named_export_evidence",
        "javascript_missing_named_export",
    ),
    "namespace": (
        {
            "main.js": "import * as catalog from './src/leaf.js';\ncatalog.DEFAULT_MISSING;\n",
            "src/leaf.js": "export const present='中文';\n",
        },
        "src/leaf.js",
        "_scan_javascript_named_export_evidence",
        "javascript_missing_namespace_export",
    ),
    "catalog": (
        {"test_main.js": "console.log(DEFAULT_PRESENT);\n", "src/leaf.js": "export const DEFAULT_PRESENT='中文';\n"},
        "src/leaf.js",
        "_scan_javascript_named_export_evidence",
        "",
    ),
}


@pytest.mark.parametrize("kind", list(CASES))
@pytest.mark.parametrize("change", ["replace", "delete", "ancestor", "rewrite"])
def test_specialist_uses_original_source_after_interleaving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, change: str
) -> None:
    """Uncontrolled reopening or live module resolution would lose blockers."""
    sources, target, hook, code = CASES[kind]
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    _sources(root, sources)
    peer.mkdir()
    source = root / target
    replacement = (
        "{}" if source.suffix == ".json" else "x=1\n" if source.suffix == ".py" else "export const missing=1;\n"
    )
    outside = peer / source.name
    outside.write_text(replacement, encoding="utf-8")
    original = getattr(_scan, hook)

    def interleave(*args: Any, **kwargs: Any) -> Any:
        if change == "rewrite":
            source.write_text(replacement, encoding="utf-8")
        else:
            source.unlink()
            if change == "replace":
                source.symlink_to(outside)
            elif change == "ancestor":
                # Package root cannot be replaced; use its package path parent
                # by moving only our private fixture tree, then linking peer.
                if source.parent == root:
                    root.rename(tmp_path / "original-workspace")
                    root.symlink_to(peer, target_is_directory=True)
                else:
                    for sibling in source.parent.iterdir():
                        sibling.unlink()
                    source.parent.rmdir()
                    source.parent.symlink_to(peer, target_is_directory=True)
        return original(*args, **kwargs)

    attempts = _guard_peer(monkeypatch, peer)
    monkeypatch.setattr(_scan, hook, interleave)
    evidence = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static")
    assert not attempts
    assert not evidence.scan_complete
    assert any("drift" in reason for reason in evidence.incomplete_reasons)
    if code:
        assert any(issue.code == code for issue in evidence.issues)
    else:
        assert not any(issue.code == "javascript_missing_catalog_fixture" for issue in evidence.issues)


@pytest.mark.parametrize("failure", [PermissionError, OSError, UnicodeDecodeError, FileNotFoundError])
@pytest.mark.parametrize("kind", ["importer", "exporter", "namespace", "catalog", "package", "term"])
def test_unknown_specialist_read_does_not_invent_absence_or_erase_known_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception], kind: str
) -> None:
    """Actual primitive failures must propagate coverage, not empty success."""
    sources, target, _, _ = CASES[kind]
    _sources(tmp_path, sources)
    _sources(
        tmp_path, {"known.py": "REQUIRED_TERM_PAIRS=[]\nREQUIRED_TERMS=[]\nfor term in REQUIRED_TERMS:\n    pass\n"}
    )
    original = Path.read_text

    def fail_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == tmp_path / target:
            if failure is UnicodeDecodeError:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "actual primitive fault")
            raise failure("actual primitive fault")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    assert not evidence.scan_complete
    assert any(target in reason for reason in evidence.incomplete_reasons)
    assert any(issue.code == "python_unused_term_pairs" for issue in evidence.issues)
    assert not any(
        issue.code
        in {
            "javascript_missing_named_export",
            "javascript_missing_namespace_export",
            "javascript_missing_catalog_fixture",
        }
        for issue in evidence.issues
    )


def test_unknown_catalog_sibling_preserves_readable_named_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sources(
        tmp_path,
        {
            "test_main.js": "import {missing} from './good.js';\nconsole.log(DEFAULT_PRESENT);\n",
            "good.js": "export const known=1;\n",
            "bad.js": "export const DEFAULT_PRESENT=1;\n",
        },
    )
    original = Path.read_text

    def denied(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name == "bad.js":
            raise PermissionError("catalog sibling read denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), scan_mode="pure_static", relative_paths=["test_main.js"]
    )
    assert not evidence.scan_complete
    assert evidence.scanned_relative_paths == ("test_main.js",)
    assert any(issue.code == "javascript_missing_named_export" for issue in evidence.issues)
    assert not any(issue.code == "javascript_missing_catalog_fixture" for issue in evidence.issues)


@pytest.mark.parametrize("specifier", ["./src/leaf.js", "./src/../src/leaf.js", "./src/leaf"])
def test_valid_internal_imports_and_namespace_use_one_unicode_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, specifier: str
) -> None:
    _sources(
        tmp_path,
        {
            "test_main.js": f"import {{present}} from '{specifier}';\nimport * as ns from '{specifier}';\nns.DEFAULT_PRESENT;\n",
            "src/leaf.js": "export const present='中文';\nexport const DEFAULT_PRESENT=[];\n",
        },
    )
    reads: list[str] = []
    original = Path.read_text

    def counted(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), scan_mode="pure_static", relative_paths=["test_main.js"]
    )
    assert evidence.scan_complete
    assert not evidence.errors
    assert evidence.scanned_relative_paths == ("test_main.js",)
    assert reads.count(str(tmp_path / "test_main.js")) == 1
    assert reads.count(str(tmp_path / "src/leaf.js")) == 1


def test_initial_unknown_module_link_is_not_reported_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    _sources(
        root,
        {
            "main.js": "import {present} from './linked.js';\nimport {missing} from './good.js';\n",
            "good.js": "export const known=1;\n",
        },
    )
    _sources(peer, {"outside.js": "export const present=1;\n"})
    (root / "linked.js").symlink_to(peer / "outside.js")
    attempts = _guard_peer(monkeypatch, peer)
    evidence = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", relative_paths=["main.js"])
    assert not attempts
    assert not evidence.scan_complete
    assert any(issue.code == "javascript_missing_named_export" for issue in evidence.issues)
    assert not any(issue.code == "javascript_missing_relative_module" for issue in evidence.issues)


def test_real_missing_and_catalog_diagnostics_remain(tmp_path: Path) -> None:
    _sources(tmp_path, {"test_main.js": "import {x} from './absent.js';\nconsole.log(DEFAULT_ABSENT);\n"})
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="pure_static")
    # The existing symbol aggregator also records unresolved import coverage.
    # This specialist contract preserves its two real absence diagnostics.
    assert {"javascript_missing_relative_module", "javascript_missing_catalog_fixture"} <= {
        issue.code for issue in evidence.issues
    }


@pytest.mark.parametrize("kind", ["javascript", "python"])
def test_specialist_rejects_reader_workspace_mismatch(tmp_path: Path, kind: str) -> None:
    from polaris.kernelone.quality.artifact_quality._scan_javascript import _scan_javascript_named_export_evidence
    from polaris.kernelone.quality.artifact_quality._scan_python import _scan_python_acceptance_term_pair_evidence

    other = tmp_path / "other"
    other.mkdir()
    scanner = (
        _scan_javascript_named_export_evidence if kind == "javascript" else _scan_python_acceptance_term_pair_evidence
    )
    with pytest.raises(ValueError, match="workspace binding mismatch"):
        scanner(tmp_path, [], reader=WorkspaceReadSnapshot(other))


def test_long_relative_path_keeps_real_typescript_diagnostic(tmp_path: Path) -> None:
    """A long relative path must not displace the actual compiler error either."""
    relative = "/".join(["nested-diagnostic-folder" * 2] * 4) + "/broken.ts"
    _sources(tmp_path, {relative: "export const invalid = ;\n"})
    evidence = _scan._scan_file_evidence(tmp_path, tmp_path / relative, relative)
    syntax = [issue for issue in evidence.issues if issue.code == "syntax_error"]
    assert syntax
    assert "TS1109: Expression expected." in syntax[0].metadata["syntax_error"]
    assert len(syntax[0].metadata["syntax_error"]) <= 200


@pytest.mark.parametrize(
    ("package", "legacy_scripts"),
    [
        ('{"scripts":', {}),
        ("[]", {}),
        ('{"scripts":[]}', {}),
        ('{"scripts":{"test":7,"test:js":"node --test"}}', {"test:js": "node --test"}),
    ],
)
def test_package_schema_unknown_keeps_readable_blocker(
    tmp_path: Path, package: str, legacy_scripts: dict[str, str]
) -> None:
    """An invalid manifest cannot be silently projected as an empty script map."""
    from polaris.kernelone.quality.artifact_quality._scan_python import _load_package_scripts

    _sources(tmp_path, {"main.py": CASES["term"][0]["src/main.py"], "package.json": package})
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=["main.py"], scan_mode="pure_static"
    )
    assert not evidence.scan_complete
    assert evidence.scanned_relative_paths == ("main.py",)
    assert any("package.json" in reason for reason in evidence.incomplete_reasons)
    assert any(issue.code == "python_unused_term_pairs" for issue in evidence.issues)
    # No-reader best-effort consumers keep their established compatibility.
    assert _load_package_scripts(tmp_path) == legacy_scripts


@pytest.mark.parametrize("package", [None, "{}", '{"scripts":{}}', '{"name":"中文"}'])
def test_package_schema_optional_absence_and_valid_empty_are_complete(tmp_path: Path, package: str | None) -> None:
    _sources(tmp_path, {"main.py": "message='中文'\n"})
    if package is not None:
        _sources(tmp_path, {"package.json": package})
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=["main.py"], scan_mode="pure_static"
    )
    assert evidence.scan_complete
    assert not evidence.errors
    assert evidence.scanned_relative_paths == ("main.py",)


def _replace_directory(directory: Path, peer: Path, change: str) -> None:
    directory.rename(directory.with_name(directory.name + "-retained"))
    if change == "link":
        directory.symlink_to(peer, target_is_directory=True)
    elif change == "replace":
        peer.rename(directory)


@pytest.mark.parametrize("level", ["parent", "root", "nested"])
@pytest.mark.parametrize("change", ["link", "replace", "delete"])
def test_original_workspace_identity_rejects_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str, change: str
) -> None:
    """Checking only the last root component allows parent namespace redirection."""
    root, peer = tmp_path / "parent/workspace", tmp_path / "peer"
    _sources(root, {"nested/known.py": CASES["term"][0]["src/main.py"], "nested/unread.py": "value='中文'\n"})
    _sources(
        peer,
        {
            "unread.py": "value='外部'\n",
            "nested/unread.py": "value='外部'\n",
            "workspace/nested/unread.py": "value='外部'\n",
        },
    )
    reader = WorkspaceReadSnapshot(root)
    known = reader.read(root / "nested/known.py")
    assert known.status == "readable"
    directory = root.parent if level == "parent" else root if level == "root" else root / "nested"
    _replace_directory(directory, peer, change)
    reads: list[str] = []
    original = Path.read_text

    def observed(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", observed)
    result = reader.read(root / "nested/unread.py")
    assert result.status == "unknown"
    assert not result.text
    assert not reads
    assert reader.read(root / "nested/known.py") is known
    assert reader.drift_reasons()


@pytest.mark.parametrize("level", ["parent", "root", "nested"])
@pytest.mark.parametrize("change", ["link", "replace", "delete"])
def test_actual_open_rejects_directory_drift_before_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str, change: str
) -> None:
    """Real opener must bind identity even when drift follows metadata preflight."""
    root, peer = tmp_path / "parent/workspace", tmp_path / "peer"
    _sources(root, {"nested/main.py": "value='中文'\n"})
    _sources(
        peer,
        {"main.py": "value='外部'\n", "nested/main.py": "value='外部'\n", "workspace/nested/main.py": "value='外部'\n"},
    )
    # Identical file identity must not authorize a different directory tree.
    # Hard links keep the leaf witness constant and isolate directory binding.
    for relative in ["main.py", "nested/main.py", "workspace/nested/main.py"]:
        (peer / relative).unlink()
        os.link(root / "nested/main.py", peer / relative)
    reader = WorkspaceReadSnapshot(root)
    original_read = Path.read_text
    original_open = builtins.open
    streams: list[str] = []

    def interleave(path: Path, *args: Any, **kwargs: Any) -> str:
        directory = root.parent if level == "parent" else root if level == "root" else root / "nested"
        _replace_directory(directory, peer, change)
        return original_read(path, *args, **kwargs)

    def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
        stream = original_open(file, *args, **kwargs)
        streams.append(str(file))
        return stream

    monkeypatch.setattr(Path, "read_text", interleave)
    monkeypatch.setattr(builtins, "open", opened)
    result = reader.read(root / "nested/main.py")
    assert result.status == "unknown"
    assert not result.text
    assert not streams  # No stream exists from which peer content can be read.


def test_snapshot_cache_retains_unicode_and_independent_scan_reads_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sources(tmp_path, {"main.py": "value='中文'\n"})
    original = Path.read_text
    reads: list[str] = []

    def counted(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    first = WorkspaceReadSnapshot(tmp_path)
    witness = first.read(tmp_path / "main.py")
    _sources(tmp_path, {"main.py": "value='改写后内容'\n"})
    assert first.read(tmp_path / "main.py") is witness
    assert witness.text == "value='中文'\n"
    assert first.drift_reasons()
    second = WorkspaceReadSnapshot(tmp_path).read(tmp_path / "main.py")
    assert second.text == "value='改写后内容'\n"
    assert reads == [str(tmp_path / "main.py")] * 2


@pytest.mark.parametrize("fault", ["none", "fstat", "dup", "decode", "open_race", "interrupt"])
def test_descriptors_close_on_success_and_actual_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """Owned directory/file/duplicate descriptors must close on every exit."""
    _sources(tmp_path, {"main.py": "value='中文'\n"})
    if fault == "decode":
        (tmp_path / "main.py").write_bytes(b"\xff")
    reader = WorkspaceReadSnapshot(tmp_path)
    before = set(Path("/proc/self/fd").iterdir())
    original_fstat = os.fstat
    original_read = Path.read_text

    def fstat(fd: int) -> os.stat_result:
        result = original_fstat(fd)
        if fault == "fstat" and stat.S_ISREG(result.st_mode):
            raise OSError("owned fstat failure")
        return result

    def dup(fd: int) -> int:
        if fault == "interrupt":
            raise KeyboardInterrupt("owned interrupt")
        raise OSError("owned dup failure")

    def race(path: Path, *args: Any, **kwargs: Any) -> str:
        path.unlink()
        path.symlink_to(tmp_path / "absent.py")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(os, "fstat", fstat)
    if fault in {"dup", "interrupt"}:
        monkeypatch.setattr(os, "dup", dup)
    if fault == "open_race":
        monkeypatch.setattr(Path, "read_text", race)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            reader.read(tmp_path / "main.py")
    else:
        result = reader.read(tmp_path / "main.py")
        assert result.status == ("readable" if fault == "none" else "unknown")
    assert set(Path("/proc/self/fd").iterdir()) == before


@pytest.mark.parametrize("relative", ["main.py", "main.js"])
def test_same_unreadable_witness_has_one_public_coverage_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    """Specialist projections must not duplicate the per-file refusal identity."""
    _sources(tmp_path, {relative: "value=1\n", "known.py": CASES["term"][0]["src/main.py"]})
    original = Path.read_text

    def denied(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == tmp_path / relative:
            raise PermissionError("actual source refused")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=[relative, "known.py"], scan_mode="pure_static"
    )
    assert not evidence.scan_complete
    assert evidence.incomplete_reasons == (f"file_unreadable:{relative}",)
    assert any("actual source refused" in error for error in evidence.errors)
    assert any(issue.code == "python_unused_term_pairs" for issue in evidence.issues)
