"""Public pure-static coverage must survive aggregation without effects."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from polaris.kernelone.quality import (
    ArtifactQualityEvidence,
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.quality.artifact_quality import _scan


@pytest.mark.parametrize("source_suffix", [".ts", ".tsx"])
def test_static_html_scan_reports_javascript_appended_to_typescript_source_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_suffix: str
) -> None:
    _write(
        tmp_path,
        {
            "src/web" + source_suffix: "export const start = () => {};\n",
            "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}',
            "index.html": (
                '<html><body><script type="module" src="./src/web' + source_suffix + '.js"></script></body></html>'
            ),
        },
    )

    evidence = _pure(tmp_path, monkeypatch, relative_paths=["index.html"])

    assert evidence.scan_complete is True
    assert len(evidence.issues) == 1
    issue = evidence.issues[0]
    assert issue.code == "html_module_script_compiled_javascript_missing"
    assert issue.path == "index.html"
    assert issue.metadata["typescript_source"] == "src/web" + source_suffix
    assert issue.metadata["emitted_script_src"] == "./dist/web.js"
    assert len(evidence.errors) == 1


def test_static_html_scan_resolves_compiled_script_relative_to_nested_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        {
            "src/pages/web.ts": "export const start = () => {};\n",
            "tsconfig.json": '{"compilerOptions":{"outDir":"./dist","rootDir":"./src"}}',
            "src/pages/index.html": ('<html><body><script type="module" src="./web.ts.js"></script></body></html>'),
        },
    )

    evidence = _pure(tmp_path, monkeypatch, relative_paths=["src/pages/index.html"])

    assert len(evidence.issues) == 1
    metadata = evidence.issues[0].metadata
    assert metadata["compiled_script_src"] == "./web.ts.js"
    assert metadata["typescript_source"] == "src/pages/web.ts"
    assert metadata["emitted_script_src"] == "../../dist/pages/web.js"


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@contextmanager
def _effect_trap(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    active = [True]
    effects: list[str] = []

    def reject(*args, **kwargs):
        effects.append("process_or_bytecode")
        raise AssertionError("pure static scan attempted process or bytecode")

    def audit(event: str, args: tuple) -> None:
        if not active[0]:
            return
        mutation = event in {
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
            "os.fork",
            "os.exec",
            "os.mkdir",
            "os.remove",
            "os.rename",
            "os.rmdir",
            "os.chmod",
            "os.truncate",
            "os.link",
            "os.symlink",
            "os.utime",
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
        }
        if event == "open":
            mode = args[1]
            flags = args[2]
            mutation = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            )
        if mutation:
            effects.append(event)
            raise AssertionError(f"pure static scan attempted {event}")

    sys.addaudithook(audit)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", reject)
        patch.setattr(py_compile, "compile", reject)
        try:
            yield effects
        finally:
            active[0] = False


def _pure(root: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> ArtifactQualityEvidence:
    before = _hashes(root)
    with _effect_trap(monkeypatch) as effects:
        evidence = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", **kwargs)
        payload = evidence.to_dict()
    assert effects == []
    after = _hashes(root)
    assert after == before
    assert payload["scan_mode"] == "pure_static"
    print(
        json.dumps(
            {
                "fixture": str(root),
                "hashes_before": before,
                "hashes_after": after,
                "effects": effects,
                "evidence": payload,
            }
        )
    )
    return evidence


def test_legacy_public_scan_creates_python_bytecode(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": "value = 1\n"})
    scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["main.py"])
    # Respect the interpreter's configured cache prefix; the legacy scan still
    # has to produce bytecode, wherever Python is configured to store it.
    assert Path(importlib.util.cache_from_source(str(tmp_path / "main.py"))).is_file()


def test_legacy_public_scan_reaches_node_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.js": "const value = 1;\n"})

    # Legacy storage resolution itself mutates cold roots; the original RED
    # observed this before reaching Node. Isolate the process boundary here.
    def reject(*args, **kwargs):
        raise AssertionError("legacy process reached")

    monkeypatch.setattr(subprocess, "Popen", reject)
    with pytest.raises(AssertionError, match="legacy process"):
        scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["main.js"])


@pytest.mark.parametrize(
    "name,text,check",
    [
        ("main.py", "value = 1\n", None),
        ("main.js", "const value = 1;\n", "javascript_parser"),
        ("main.mjs", "export const value = 1;\n", "javascript_parser"),
        ("main.cjs", "module.exports = 1;\n", "javascript_parser"),
        ("main.jsx", "export const value = <div/>;\n", "javascript_parser"),
        ("src/main.ts", "export const value: number = 1;\n", "typescript_parser"),
        ("src/main.tsx", "export const value = <div/>;\n", "typescript_parser"),
        ("main.go", "package main\nfunc main() {}\n", "go_project_compile"),
    ],
)
def test_pure_source_checks_defer_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name, text, check) -> None:
    _write(tmp_path, {name: text})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=[name])
    assert evidence.scan_complete
    if check:
        assert check in {item.check_id for item in evidence.deferred_checks}
        assert not evidence.checks_complete
        assert all(item.required and item.status == "deferred" for item in evidence.deferred_checks)
    else:
        assert evidence.checks_complete


def test_pure_nested_project_and_package_coverage_serializes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "web/package.json": json.dumps({"scripts": {"test": "node -e 'const x = ;'", "bad": "node '"}}),
            "web/tsconfig.json": "{}",
            "web/src/main.ts": "export const value = 1;\n",
            "go/go.mod": "module example.test/unit\n",
            "go/main.go": "package unit\n",
            "go/main_test.go": "package unit\n",
        },
    )
    evidence = _pure(tmp_path, monkeypatch, relative_paths=list(_hashes(tmp_path)))
    items = evidence.to_dict()["deferred_checks"]
    assert {i["check_id"] for i in items} >= {
        "node_eval_parser",
        "typescript_parser",
        "typescript_project_typecheck",
        "go_project_compile",
        "go_project_test",
    }
    assert any(i["script_name"] == "test" and i["relative_paths"] == ["web/package.json"] for i in items)
    assert any(
        i["check_id"] == "typescript_project_typecheck" and "web/src/main.ts" in i["relative_paths"] for i in items
    )
    assert any("invalid shell syntax" in error for error in evidence.errors)


@pytest.mark.parametrize(
    "name,text,fragment",
    [
        ("main.py", "def broken(:\n", "syntax error"),
        ("data.json", "{broken", "invalid JSON"),
        ("index.html", "<html><script>", "incomplete HTML"),
        ("main.ts", "// audit-seed\nexport const x = 1;\n", "scaffold marker"),
        ("main.ts", "import {x} from './missing';\n", "unresolved"),
    ],
)
def test_static_diagnostics_still_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name, text, fragment) -> None:
    _write(tmp_path, {name: text})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=[name])
    assert any(fragment.lower() in error.lower() for error in evidence.errors)


def test_pure_error_limit_preserves_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {f"file{i}.js": "// audit-seed\n" for i in range(55)}
    _write(tmp_path, files)
    evidence = _pure(tmp_path, monkeypatch, relative_paths=list(files))
    assert len(evidence.errors) == 50
    assert not evidence.scan_complete
    assert "error_limit" in evidence.incomplete_reasons
    assert evidence.deferred_checks


def test_pure_exception_preserves_prior_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"first.js": "// audit-seed\n", "second.py": "x = 1\n"})
    original = _scan._scan_file_evidence

    def fail_second(root, path, relative, **kwargs):
        if relative == "second.py":
            raise OSError("private injected read failure")
        return original(root, path, relative, **kwargs)

    monkeypatch.setattr(_scan, "_scan_file_evidence", fail_second)
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["first.js", "second.py"])
    assert not evidence.scan_complete
    assert evidence.deferred_checks
    assert any("scaffold" in e for e in evidence.errors)
    assert any("private injected" in e for e in evidence.errors)


def test_pure_unknown_and_missing_are_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.unknown": "value\n"})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.unknown"])
    assert not evidence.scan_complete
    assert evidence.incomplete_reasons
    missing = _pure(tmp_path / "absent", monkeypatch)
    assert not missing.scan_complete


def test_string_api_rejects_static_mode(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        scan_workspace_artifact_quality(str(tmp_path), scan_mode="pure_static")


def test_invalid_mode_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="scan_mode"):
        scan_workspace_artifact_quality_evidence(str(tmp_path), scan_mode="typo")


def test_root_full_scan_keeps_static_errors_and_process_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        {
            "main.py": "x = 1\n",
            "main.js": "const x = ;\n",
            "data.json": "{bad",
            "tsconfig.json": "{}",
            "src/a.ts": "export const value = 1;\n",
            "src/b.ts": "import {missing} from './a';\n",
            "go.mod": "module example.test/unit\n",
            "unit_test.go": "package unit\n",
            "package.json": json.dumps({"scripts": {"test": "node --eval='const x = ;'"}}),
        },
    )
    evidence = _pure(tmp_path, monkeypatch)
    assert evidence.scan_complete
    assert set(evidence.scanned_relative_paths) == set(_hashes(tmp_path))
    assert {c.check_id for c in evidence.deferred_checks} >= {
        "javascript_parser",
        "typescript_parser",
        "typescript_project_typecheck",
        "go_project_compile",
        "go_project_test",
        "node_eval_parser",
    }
    assert any("invalid JSON" in e for e in evidence.errors)
    assert any(i.code == "unresolved_import_symbol" for i in evidence.issues)
    assert not any(i.code in {"go_compile_error", "typescript_project_typecheck_failed"} for i in evidence.issues)


def test_package_deferred_survives_earlier_static_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "package.json": json.dumps(
                {
                    "scripts": {"first": "python broken.py", "later": "node --no-warnings -e 'const x = ;'"},
                    "dependencies": {"numpy": "1"},
                }
            )
        },
    )
    evidence = _pure(tmp_path, monkeypatch)
    assert evidence.errors
    assert any(c.check_id == "node_eval_parser" and c.script_name == "later" for c in evidence.deferred_checks)


def test_pure_read_failure_retains_required_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.js": "const x = 1;\n"})
    original = Path.read_text

    def fail_source(path: Path, *args, **kwargs):
        if path == tmp_path / "main.js":
            raise OSError("private source unreadable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_source)
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.js"])
    assert not evidence.scan_complete
    assert evidence.incomplete_reasons == ("file_unreadable:main.js",)
    assert any(c.check_id == "javascript_parser" for c in evidence.deferred_checks)
    assert evidence.errors


def test_pure_truncated_input_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.js": "//" + "x" * 1_000_001})
    evidence = _pure(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert "file_truncated:main.js" in evidence.incomplete_reasons
    assert evidence.deferred_checks


def test_pure_scope_missing_and_escape_are_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["missing.ts", "../escape.ts"])
    assert not evidence.scan_complete
    assert "scope_missing:missing.ts" in evidence.incomplete_reasons
    assert "scope_outside_workspace:../escape.ts" in evidence.incomplete_reasons


def test_pure_generator_scope_is_not_consumed_by_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.py": "x = 1\n"})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=(p for p in ["main.py"]))
    assert evidence.scanned_relative_paths == ("main.py",)


def test_explicit_legacy_preserves_default_errors(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": "def invalid(:\n"})
    default = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["main.py"])
    explicit = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["main.py"], scan_mode="legacy")
    assert (
        default.errors
        == explicit.errors
        == tuple(scan_workspace_artifact_quality(str(tmp_path), relative_paths=["main.py"]))
    )
    assert default.issues == explicit.issues
    assert default.deferred_checks == ()


def test_deferred_project_scope_distinguishes_nested_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "one/tsconfig.json": "{}",
            "one/main.ts": "export const x = 1;\n",
            "two/tsconfig.json": "{}",
            "two/main.ts": "export const x = 1;\n",
        },
    )
    evidence = _pure(tmp_path, monkeypatch)
    checks = [c.to_dict() for c in evidence.deferred_checks if c.check_id == "typescript_project_typecheck"]
    assert {c["project_relative_path"] for c in checks} == {"one", "two"}


def test_legacy_does_not_claim_certified_check_coverage(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": "x = 1\n"})
    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path))
    assert not evidence.checks_complete


def test_pure_current_file_exception_keeps_parser_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.js": "const x = 1;\n"})

    def fail_file(*args, **kwargs):
        raise OSError("private per-file scanner failure")

    monkeypatch.setattr(_scan, "_scan_file_evidence", fail_file)
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.js"])
    assert not evidence.scan_complete
    assert any(c.check_id == "javascript_parser" for c in evidence.deferred_checks)


def test_pure_unreadable_directory_does_not_claim_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable_walk(top, topdown=True, onerror=None, followlinks=False):
        if onerror is not None:
            onerror(OSError("private directory unreadable"))
        return iter(())

    monkeypatch.setattr(os, "walk", unreadable_walk)
    evidence = _pure(tmp_path, monkeypatch)
    assert not evidence.scan_complete
    assert any("directory unreadable" in e for e in evidence.errors)
