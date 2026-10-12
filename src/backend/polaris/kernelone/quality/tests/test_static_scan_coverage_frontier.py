"""Public static coverage must preserve empty invocations and unknown reads."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.quality import ArtifactQualityEvidence, scan_workspace_artifact_quality_evidence
from polaris.kernelone.quality.artifact_quality import _scan
from polaris.kernelone.quality.interface_ledger import read_declared_interface_ledger_status
from polaris.kernelone.quality.tests.test_artifact_quality_static_scan import _effect_trap


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(current) / name
            if path.is_symlink():
                result[path.relative_to(root).as_posix()] = "link:" + os.readlink(path)
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _pure(root: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> ArtifactQualityEvidence:
    before = _snapshot(root)
    with _effect_trap(monkeypatch) as effects:
        evidence = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", **kwargs)
        payload = evidence.to_dict()
    after = _snapshot(root)
    assert effects == []
    assert after == before
    assert not list(root.rglob("*.pyc"))
    print(json.dumps({"fixture": str(root), "before": before, "after": after, "effects": effects, "evidence": payload}))
    return evidence


@pytest.mark.parametrize(
    "script",
    ["node -e ''", "node -e '   '", "node --eval=''", "node -e=", "node --no-warnings --require helper --eval ''"],
)
def test_empty_eval_remains_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    evidence = _pure(tmp_path, monkeypatch)
    checks = [check for check in evidence.deferred_checks if check.check_id == "node_eval_parser"]
    assert len(checks) == 1
    assert checks[0].script_name == "probe"
    assert checks[0].required and checks[0].status == "deferred"
    assert not evidence.checks_complete


@pytest.mark.parametrize("content", ["{bad", "[]", '{"files":[]}', '{"files":{"main.py":null}}'])
def test_malformed_ledger_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    _write(tmp_path, {"main.py": "x = 1\n", ".polaris/runtime/contracts/interface_ledger.json": content})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert not evidence.scan_complete and not evidence.checks_complete
    assert any("ledger" in reason for reason in evidence.incomplete_reasons)
    assert evidence.scanned_relative_paths == ("main.py",)


def test_optional_absent_ledger_is_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.py": "x = 1\n"})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert evidence.scan_complete and evidence.checks_complete and not evidence.errors


def test_low_level_ledger_denial_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "main.py": "x = 1\n",
            ".polaris/runtime/contracts/interface_ledger.json": json.dumps(
                {
                    "schema_version": "interface-ledger/1",
                    "files": {"main.py": {"public_symbols": ["expected_public_symbol"]}},
                }
            ),
        },
    )
    readable = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert any("expected_public_symbol" in error for error in readable.errors)
    assert readable.scan_complete
    original = builtins.open

    def denied(file: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(file).name == "interface_ledger.json":
            raise PermissionError("frontier ledger open denied")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied)
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert not evidence.scan_complete and not evidence.checks_complete
    assert any("ledger" in reason for reason in evidence.incomplete_reasons)


@pytest.mark.parametrize(
    "helper",
    ["read_all_declared_interfaces", "read_declared_interfaces", "validate_declared_interface_issues_against_snapshot"],
)
def test_ledger_helper_exception_keeps_unknown_and_intents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    _write(tmp_path, {"main.js": "const x = 1;\n"})

    def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("frontier helper denied")

    monkeypatch.setattr(_scan, helper, denied)
    kwargs = {"relative_paths": ["main.js"]} if helper == "read_declared_interfaces" else {}
    evidence = _pure(tmp_path, monkeypatch, **kwargs)
    assert not evidence.scan_complete
    assert any("ledger" in reason for reason in evidence.incomplete_reasons)
    assert any(check.check_id == "javascript_parser" for check in evidence.deferred_checks)


@pytest.mark.parametrize("scope", [None, ["linked"], ["linked/main.ts"]])
@pytest.mark.parametrize("kind", ["external", "internal", "broken", "cycle"])
def test_unvisited_symlink_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: list[str] | None, kind: str
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    peer = tmp_path / "peer" if kind == "external" else root / "ordinary"
    _write(peer, {"main.ts": "export const x = 1;\n"})
    target = peer if kind in {"external", "internal"} else root / ("missing" if kind == "broken" else "linked")
    (root / "linked").symlink_to(target, target_is_directory=True)
    original = Path.read_text

    def guarded(path: Path, *args: Any, **kwargs: Any) -> str:
        assert not path.resolve().is_relative_to(tmp_path / "peer"), "external linked directory read"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    evidence = _pure(root, monkeypatch, relative_paths=scope)
    assert not evidence.scan_complete and not evidence.checks_complete
    assert any("symlink" in reason for reason in evidence.incomplete_reasons)
    assert all(not path.startswith("linked") for path in evidence.scanned_relative_paths)


def test_ordinary_directory_keeps_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"src/good.py": "x = 1\n", "src/bad.json": "{bad"})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["src"])
    assert evidence.scan_complete and evidence.checks_complete
    assert set(evidence.scanned_relative_paths) == {"src/good.py", "src/bad.json"}
    assert any(issue.path == "src/bad.json" and issue.code == "syntax_error" for issue in evidence.issues)


@pytest.mark.parametrize(
    "script",
    [
        "node -p ''",
        "node --print=''",
        "node -pe ''",
        "node --input-type module -e ''",
        "node --input-type=module --import helper -e ''",
        "node -C development -r helper -e ''",
    ],
)
def test_supported_node_flags_keep_parser_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    evidence = _pure(tmp_path, monkeypatch)
    assert any(
        check.check_id == "node_eval_parser" and check.script_name == "probe" for check in evidence.deferred_checks
    )
    assert evidence.scan_complete and not evidence.checks_complete


@pytest.mark.parametrize("script", ["node --unknown-option value -e ''", "node -e", "node '", "node -e $(printf code)"])
def test_ambiguous_script_cannot_claim_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    evidence = _pure(tmp_path, monkeypatch)
    assert not evidence.scan_complete and not evidence.checks_complete
    assert any("script_invocation_unknown" in reason for reason in evidence.incomplete_reasons)


@pytest.mark.parametrize("script", ["node script.js -e ''", "node -- -e ''"])
def test_positional_script_does_not_invent_eval_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"script.js": "const x = 1;\n", "package.json": json.dumps({"scripts": {"probe": script}})})
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["package.json"])
    assert not any(check.check_id == "node_eval_parser" for check in evidence.deferred_checks)


@pytest.mark.parametrize("pattern", ["tests/*.test.js", "tests/??.test.js", "tests/[ab]*.test.js"])
@pytest.mark.parametrize("compiler", ["tsc", "tsc --noEmit", "tsc -p tsconfig.json"])
def test_node_test_file_glob_does_not_make_static_command_coverage_unknown(pattern: str, compiler: str) -> None:
    from polaris.kernelone.quality.artifact_quality._scan_package import _static_shell_node_sources

    sources, unknown = _static_shell_node_sources(f"{compiler} && node --test {pattern}")

    assert sources == ()
    assert unknown == []


@pytest.mark.parametrize("script", ["echo *.js", "node *.js --test", "node --test --import loaders/*.js tests/*.test.js"])
def test_glob_outside_recognized_node_test_sources_remains_unknown(script: str) -> None:
    from polaris.kernelone.quality.artifact_quality._scan_package import _static_shell_node_sources

    _sources, unknown = _static_shell_node_sources(script)

    assert "shell_glob_expansion" in unknown


def test_nested_ledger_scope_and_task_remain_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "one/main.py": "x = 1\n",
            "two/main.py": "y = 1\n",
            ".polaris/runtime/contracts/interface_ledger.json": json.dumps(
                {
                    "files": {
                        "one/main.py": {"public_symbols": ["expected_public_symbol"]},
                        "two/main.py": {"public_symbols": ["unrelated_symbol"]},
                    }
                }
            ),
        },
    )
    evidence = _pure(tmp_path, monkeypatch, relative_paths=(path for path in ["one/main.py"]), task_id="frontier-task")
    assert evidence.scan_complete
    assert evidence.scanned_relative_paths == ("one/main.py",)
    assert any("expected_public_symbol" in error for error in evidence.errors)
    assert not any("unrelated_symbol" in error for error in evidence.errors)
    assert any(issue.details.get("task_id") == "frontier-task" for issue in evidence.cross_artifact_issues)


def test_ledger_status_is_read_only_and_requires_explicit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _effect_trap(monkeypatch) as effects:
        assert read_declared_interface_ledger_status(str(tmp_path), str(tmp_path / ".polaris/runtime")) == (
            "missing",
            "",
        )
        with pytest.raises(ValueError, match="explicit root"):
            read_declared_interface_ledger_status(str(tmp_path), "")
    assert effects == []
    assert not (tmp_path / ".polaris").exists()


@pytest.mark.parametrize("error", [OSError("frontier IO failure"), PermissionError("frontier permission failure")])
def test_low_level_ledger_io_failure_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    _write(tmp_path, {"main.py": "x = 1\n"})
    original = builtins.open

    def failed(file: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(file).name == "interface_ledger.json":
            raise error
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failed)
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert not evidence.scan_complete and not evidence.checks_complete
    assert any(type(error).__name__ in reason for reason in evidence.incomplete_reasons)


def test_invalid_utf8_ledger_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"main.py": "x = 1\n", ".polaris/runtime/contracts/interface_ledger.json": ""})
    (tmp_path / ".polaris/runtime/contracts/interface_ledger.json").write_bytes(b"\xff")
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert not evidence.scan_complete
    assert any("UnicodeDecodeError" in reason for reason in evidence.incomplete_reasons)


def test_scope_cannot_normalize_away_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "workspace"
    _write(root, {"ordinary/main.py": "x = 1\n"})
    peer = tmp_path / "peer"
    peer.mkdir()
    (root / "linked").symlink_to(peer, target_is_directory=True)
    evidence = _pure(root, monkeypatch, relative_paths=["linked/../ordinary/main.py"])
    assert not evidence.scan_complete
    assert any("symlink" in reason for reason in evidence.incomplete_reasons)
    assert not evidence.scanned_relative_paths


def test_unknown_ledger_schema_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "main.py": "x = 1\n",
            ".polaris/runtime/contracts/interface_ledger.json": '{"schema_version":"future-version","files":{}}',
        },
    )
    evidence = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert not evidence.scan_complete and not evidence.checks_complete
    assert any("ledger" in reason for reason in evidence.incomplete_reasons)
