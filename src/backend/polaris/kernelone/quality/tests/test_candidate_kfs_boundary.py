"""Candidate gates must honor registered filesystem policy and scratch isolation."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import registry
from polaris.kernelone.quality import candidate_compile_gate, syntax_gate


class _DeniedCandidateAdapter(LocalFileSystemAdapter):
    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        raise PermissionError("candidate write denied by registered adapter")

    def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> int:
        raise PermissionError("candidate write denied by registered adapter")


def test_syntax_candidate_obeys_registered_write_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    """A policy denial must not become a checked, successful source candidate."""

    monkeypatch.setattr(registry, "_default_adapter", _DeniedCandidateAdapter())

    result = syntax_gate.check_content_syntax("src/main.py", "message = '中文'\r\n")

    assert result.path == "src/main.py"
    assert result.checked is False
    assert result.ok is True
    assert result.reason == "candidate write denied by registered adapter"


def test_compile_candidate_obeys_registered_write_denial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Denied scratch overlays retain the successful baseline, not a fabricated check."""

    (tmp_path / "go.mod").write_text("module example.com/guard\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package guard\n", encoding="utf-8")
    monkeypatch.setattr(registry, "_default_adapter", _DeniedCandidateAdapter())
    monkeypatch.setattr(candidate_compile_gate.shutil, "which", lambda _tool: "/compiler")

    def compile_ok(command: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(candidate_compile_gate, "_run_compile", compile_ok)
    directories_before = set(tmp_path.parent.iterdir())

    result = candidate_compile_gate.check_candidate_workspace_compile(tmp_path, "main.go", "package guard\n// 中文\r\n")

    assert result.checked is False
    assert result.before_ok is True
    assert result.after_ok is False
    assert result.regression is False
    assert result.reason == "candidate write denied by registered adapter"
    assert (tmp_path / "main.go").read_text(encoding="utf-8") == "package guard\n"
    assert set(tmp_path.parent.iterdir()) == directories_before


def test_compile_baseline_and_candidate_keep_compiler_artifacts_in_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the baseline compiler in the live source would create Cargo.lock/target."""

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "guard"\n', encoding="utf-8")
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir()
    source.write_text("pub fn original() {}\n", encoding="utf-8")
    candidate = "// 中文\r\npub fn candidate() {}\r\n"
    monkeypatch.setattr(registry, "_default_adapter", LocalFileSystemAdapter())
    monkeypatch.setattr(candidate_compile_gate.shutil, "which", lambda _tool: "/compiler")
    checked_roots: list[Path] = []

    def compile_with_artifacts(
        command: tuple[str, ...], *, cwd: Path, timeout_seconds: int
    ) -> subprocess.CompletedProcess[str]:
        checked_roots.append(cwd)
        (cwd / "Cargo.lock").write_text("compiler artifact\n", encoding="utf-8")
        (cwd / "target").mkdir(exist_ok=True)
        payload = (cwd / "src" / "lib.rs").read_bytes()
        assert payload in {
            b"pub fn original() {}\n",
            "// 中文\r\npub fn candidate() {}\r\n".encode(),
        }
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(candidate_compile_gate, "_run_compile", compile_with_artifacts)

    result = candidate_compile_gate.check_candidate_workspace_compile(tmp_path, "src/lib.rs", candidate)

    assert result.checked is True
    assert result.before_ok is True
    assert result.after_ok is True
    assert result.regression is False
    assert len(checked_roots) == 2
    assert all(root != tmp_path and not root.exists() for root in checked_roots)
    assert not (tmp_path / "Cargo.lock").exists()
    assert not (tmp_path / "target").exists()
    assert source.read_text(encoding="utf-8") == "pub fn original() {}\n"


def test_syntax_candidate_retains_utf8_and_crlf_bytes_in_cleaned_scratch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KFS writes must retain the previous newline='' candidate byte contract."""

    monkeypatch.setattr(registry, "_default_adapter", LocalFileSystemAdapter())
    checked_paths: list[Path] = []

    def inspect_candidate(path: str, *, timeout_seconds: int) -> syntax_gate.SyntaxCheckResult:
        candidate = Path(path)
        checked_paths.append(candidate)
        assert candidate.read_bytes() == "message = '中文'\r\n".encode()
        return syntax_gate.SyntaxCheckResult(path, True, True, "", "")

    monkeypatch.setattr(syntax_gate, "check_file_syntax", inspect_candidate)

    result = syntax_gate.check_content_syntax("src/main.py", "message = '中文'\r\n")

    assert result.checked is True
    assert result.ok is True
    assert result.path == "src/main.py"
    assert len(checked_paths) == 1
    assert not checked_paths[0].parent.exists()


@pytest.mark.skipif(shutil.which("go") is None, reason="go unavailable")
def test_baseline_testmain_source_mutation_cannot_mask_candidate_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A baseline TestMain must not change the source compiled for the candidate."""

    sources = {
        "go.mod": "module example.com/demo\n\ngo 1.21\n",
        "a.go": "package demo\ntype Value = int\n",
        "main.go": "package demo\nvar x Value = 1\n",
        "main_test.go": (
            "package demo\n"
            'import ("os"; "testing")\n'
            "func TestMain(_ *testing.M) {\n"
            '    if err := os.WriteFile("a.go", []byte("package demo\\ntype Value = string\\n"), 0600); err != nil {\n'
            "        panic(err)\n"
            "    }\n"
            "    os.Exit(0)\n"
            "}\n"
        ),
    }
    for filename, source in sources.items():
        (tmp_path / filename).write_text(source, encoding="utf-8")
    before_hashes = {filename: hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest() for filename in sources}
    directories_before = set(tmp_path.parent.iterdir())
    monkeypatch.setattr(registry, "_default_adapter", LocalFileSystemAdapter())

    result = candidate_compile_gate.check_candidate_workspace_compile(
        tmp_path, "main.go", 'package demo\nvar x Value = "bad"\n'
    )

    after_hashes = {filename: hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest() for filename in sources}
    assert after_hashes == before_hashes
    assert {path.name for path in tmp_path.iterdir()} == set(sources)
    assert set(tmp_path.parent.iterdir()) == directories_before
    assert result.checked is True
    assert result.before_ok is True
    assert result.after_ok is False
    assert result.regression is True
    assert 'cannot use "bad"' in result.error
