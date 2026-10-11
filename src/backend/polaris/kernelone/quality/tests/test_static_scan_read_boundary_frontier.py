"""Public pure-static coverage must follow actual confined read witnesses."""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.quality import interface_ledger as ledger
from polaris.kernelone.quality.tests.test_static_scan_coverage_frontier import _pure, _write


@pytest.mark.parametrize("operator", ["&&", ";", "||", "|", "\n"])
def test_adjacent_shell_operator_keeps_node_parser_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": "true" + operator + "node -e ''"}})})
    evidence = _pure(tmp_path, monkeypatch)
    assert any(c.check_id == "node_eval_parser" for c in evidence.deferred_checks)
    assert not evidence.checks_complete


@pytest.mark.parametrize("script", ["(node -e '')", "MODE=x node -pe ''", "exec node -e ''", "env MODE=x node -e ''"])
def test_bounded_command_positions_keep_required_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert any(c.check_id == "node_eval_parser" for c in e.deferred_checks)


@pytest.mark.parametrize("script", ["printf '%s' node -e ''", "echo 'node -e x'", "printf '%s' 'x;node' '-e'"])
def test_argument_words_do_not_invent_node_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert e.scan_complete and not e.deferred_checks


@pytest.mark.parametrize(
    "script", ["$NODE -e ''", '"${NODE}" -e x', "if true;then node -e '';fi", "node -e $CODE", "sh -c 'node -e x'"]
)
def test_dynamic_or_unsupported_shell_cannot_claim_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert not e.scan_complete and not e.checks_complete


def _ledger_control(root: Path) -> None:
    _write(
        root,
        {
            "main.py": "x=1\n",
            ".polaris/runtime/contracts/interface_ledger.json": json.dumps(
                {"files": {"main.py": {"public_symbols": ["expected_public_symbol"]}}}
            ),
        },
    )


@pytest.mark.parametrize(
    "level",
    [".polaris", ".polaris/runtime", ".polaris/runtime/contracts", ".polaris/runtime/contracts/interface_ledger.json"],
)
@pytest.mark.parametrize("kind", ["external", "broken", "cycle"])
def test_ledger_ancestry_links_are_unknown_before_content_or_external_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str, kind: str
) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    _write(root, {"main.py": "x=1\n"})
    _write(peer, {"contracts/interface_ledger.json": '{"files":{}}'})
    link = root / level
    link.parent.mkdir(parents=True, exist_ok=True)
    target = peer if kind == "external" else root / "missing" if kind == "broken" else link
    link.symlink_to(target, target_is_directory=not level.endswith(".json"))
    original_open, original_stat = builtins.open, Path.stat
    attempts: list[str] = []

    def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, (str, os.PathLike)) and str(file).startswith(str(link)):
            attempts.append("content:" + str(file))
            raise AssertionError("linked ledger content attempted")
        return original_open(file, *args, **kwargs)

    def stated(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if str(path).startswith(str(link)) and kwargs.get("follow_symlinks", True):
            attempts.append("stat:" + str(path))
            raise AssertionError("linked target stat attempted")
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as guards:
        guards.setattr(builtins, "open", opened)
        guards.setattr(Path, "stat", stated)
        from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

        e = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", relative_paths=["main.py"])
    assert not attempts
    assert not e.scan_complete and not e.checks_complete
    assert any("symlink" in reason for reason in e.incomplete_reasons)


def test_pure_scan_does_not_consume_external_cache_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "external-cache"
    _write(root, {"main.py": "x=1\n"})
    _write(peer, {"contracts/interface_ledger.json": '{"files":{}}'})
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(peer))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(peer))
    original = builtins.open

    def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
        assert not str(file).startswith(str(peer)), "external cache content read"
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    e = _pure(root, monkeypatch, relative_paths=["main.py"])
    assert e.scan_complete and e.checks_complete


def test_one_ledger_payload_drives_contract_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ledger_control(tmp_path)
    original = builtins.open
    reads: list[str] = []

    def once(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file).endswith("interface_ledger.json"):
            reads.append(str(file))
            if reads.count(str(file)) > 1:
                raise FileNotFoundError("second ledger open must not occur")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", once)
    e = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert any("expected_public_symbol" in error for error in e.errors)
    assert e.scan_complete
    assert max(reads.count(p) for p in reads) == 1


@pytest.mark.parametrize("fault", [FileNotFoundError, PermissionError, OSError, UnicodeDecodeError])
def test_real_strict_reader_fault_keeps_unknown_and_control_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: type[Exception]
) -> None:
    _ledger_control(tmp_path)
    assert any("expected_public_symbol" in x for x in _pure(tmp_path, monkeypatch, relative_paths=["main.py"]).errors)
    original = builtins.open

    def failed(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file).endswith(".polaris/runtime/contracts/interface_ledger.json"):
            if fault is UnicodeDecodeError:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "fault")
            raise fault("POL47 actual reader failure")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failed)
    e = _pure(tmp_path, monkeypatch, relative_paths=["main.py"])
    assert not e.scan_complete and not e.checks_complete


@pytest.mark.parametrize("target", ["linked.ts", "nested/linked.ts", "nested/a/../../linked.ts", "链接.ts"])
@pytest.mark.parametrize("kind", ["external", "broken", "cycle"])
def test_transitive_import_links_never_authorized_by_explicit_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, kind: str
) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    _write(
        root,
        {
            "main.ts": "import {secret} from './bridge';\n",
            "bridge.ts": "export {secret} from './" + target.removesuffix(".ts") + "';\n",
        },
    )
    _write(peer, {"outside.ts": "export const secret=1;\n"})
    link = root / target
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(peer / "outside.ts" if kind == "external" else root / "missing.ts" if kind == "broken" else link)
    e = _pure(root, monkeypatch, relative_paths=["main.ts"])
    assert not e.scan_complete and not e.checks_complete


@pytest.mark.parametrize(
    "content",
    ["export * from './missing';\n", "import {x} from './missing'; export const secret=x;\n", "module.exports = {};\n"],
)
def test_unknown_or_missing_transitive_surface_stays_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    _write(tmp_path, {"main.ts": "import {secret} from './bridge';\n", "bridge.ts": content})
    e = _pure(tmp_path, monkeypatch, relative_paths=["main.ts"])
    assert not e.scan_complete and not e.checks_complete


def test_normal_relative_chain_keeps_coverage_and_missing_symbol_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        {
            "main.ts": "import {secret} from './层/bridge';\n",
            "层/bridge.ts": "export {secret} from '../leaf';\n",
            "leaf.ts": "export const secret=1;\n",
        },
    )
    e = _pure(tmp_path, monkeypatch, relative_paths=["main.ts"])
    assert e.scan_complete and not e.errors
    (tmp_path / "main.ts").write_text("import {missing} from './leaf';\n", encoding="utf-8")
    e = _pure(tmp_path, monkeypatch, relative_paths=["main.ts"])
    assert e.scan_complete and any("unresolved import symbol" in x for x in e.errors)


def test_snapshot_returns_validated_payload_and_read_witness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ledger_control(tmp_path)
    reader = getattr(ledger, "read_declared_interface_ledger_snapshot", None)
    assert reader is not None, "strict public snapshot query missing"
    result = reader(str(tmp_path), str(tmp_path / ".polaris/runtime"))
    assert result.status == "readable" and result.detail == ""
    assert result.payload["files"]["main.py"]["public_symbols"] == ["expected_public_symbol"]
    assert len(result.content_sha256) == 64 and result.path.endswith("interface_ledger.json")


@pytest.mark.parametrize("operation", ["replace", "delete", "rewrite", "bad-schema"])
def test_actual_read_drift_does_not_become_empty_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    _ledger_control(tmp_path)
    path = tmp_path / ".polaris/runtime/contracts/interface_ledger.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"files":{}}', encoding="utf-8")
    original = builtins.open

    def drift(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(path):
            if operation == "replace":
                os.replace(replacement, path)
            elif operation == "delete":
                path.unlink()
            else:
                path.write_text(
                    '{"files":{}}' if operation == "rewrite" else '{"schema_version":"unknown","files":{}}',
                    encoding="utf-8",
                )
        return original(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", drift)
    reader = getattr(ledger, "read_declared_interface_ledger_snapshot", None)
    assert reader is not None
    result = reader(str(tmp_path), str(path.parent.parent))
    assert result.status == "unknown" and not result.payload["files"]
    assert result.detail


@pytest.mark.parametrize(
    "script", ["n*de -e ''", "command node -e ''", "npx node -e ''", "NODE=node; $NODE -e ''", "node - < input.js"]
)
def test_unbounded_invocation_forms_are_explicitly_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert not e.scan_complete and not e.checks_complete


def test_explicit_root_snapshot_preserves_legacy_logical_path(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"contracts/interface_ledger.json": '{"files":{"main.py":{"public_symbols":["expected_public_symbol"]}}}'},
    )
    reader = getattr(ledger, "read_declared_interface_ledger_snapshot", None)
    assert reader is not None
    result = reader(str(tmp_path), str(tmp_path))
    assert result.status == "readable" and result.path == str(tmp_path / "contracts/interface_ledger.json")


@pytest.mark.parametrize("kind", ["permission", "unicode", "io"])
def test_transitive_actual_read_failure_keeps_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _write(
        tmp_path,
        {
            "main.ts": "import {secret} from './bridge';\n",
            "bridge.ts": "export {secret} from './leaf';\n",
            "leaf.ts": "export const secret=1;\n",
        },
    )
    if kind == "unicode":
        (tmp_path / "leaf.ts").write_bytes(b"\xff")
        e = _pure(tmp_path, monkeypatch, relative_paths=["main.ts"])
    else:
        original = builtins.open

        def failed(file: Any, *args: Any, **kwargs: Any) -> Any:
            if str(file).endswith("leaf.ts"):
                raise (PermissionError if kind == "permission" else OSError)("transitive actual read failed")
            return original(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", failed)
        e = _pure(tmp_path, monkeypatch, relative_paths=["main.ts"])
    assert not e.scan_complete and not e.checks_complete


def test_import_ancestor_link_does_not_attempt_external_stat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    _write(root, {"main.ts": "import {secret} from './linked/leaf';\n"})
    _write(peer, {"leaf.ts": "export const secret=1;\n"})
    link = root / "linked"
    link.symlink_to(peer, target_is_directory=True)
    original = Path.stat

    def guarded(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        assert not (str(path).startswith(str(link)) and kwargs.get("follow_symlinks", True)), "external target stat"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded)
    from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

    e = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", relative_paths=["main.ts"])
    assert not e.scan_complete and any("symlink" in x for x in e.incomplete_reasons)


@pytest.mark.parametrize("script", ["node -e \"const x='$CODE';\"", "env -i node -e ''", "exec -a custom node -e ''"])
def test_quote_nesting_and_wrapper_options_cannot_hide_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert not e.scan_complete and not e.checks_complete


@pytest.mark.parametrize(
    "script", ["printf '%s' x # ignored;node -e ''", "# ignored;node -e ''", "printf '%s' 'node' ';' '-e' ''"]
)
def test_comments_and_quoted_operators_do_not_create_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert e.scan_complete and not e.deferred_checks


def test_linked_tsconfig_never_authorizes_content_or_target_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    _write(root, {"main.ts": "interface Thing {}\nexport { Thing };\n"})
    _write(peer, {"tsconfig.json": '{"compilerOptions":{"isolatedModules":true}}'})
    linked = root / "tsconfig.json"
    linked.symlink_to(peer / "tsconfig.json")
    original_open, original_stat = builtins.open, Path.stat

    def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
        assert str(file) != str(linked), "linked config content attempted"
        return original_open(file, *args, **kwargs)

    def stated(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        assert not (str(path) == str(linked) and kwargs.get("follow_symlinks", True)), (
            "linked config target stat attempted"
        )
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as guards:
        guards.setattr(builtins, "open", opened)
        guards.setattr(Path, "stat", stated)
        from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

        e = scan_workspace_artifact_quality_evidence(str(root), scan_mode="pure_static", relative_paths=["main.ts"])
    assert not e.scan_complete


@pytest.mark.parametrize("script", ["node --require", "node --import", "node --input-type"])
def test_missing_node_option_value_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": script}})})
    e = _pure(tmp_path, monkeypatch)
    assert not e.scan_complete and not e.checks_complete


def test_stdin_node_keeps_required_parser_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, {"package.json": json.dumps({"scripts": {"probe": "node --input-type=module -"}})})
    e = _pure(tmp_path, monkeypatch)
    assert any(c.check_id == "node_eval_parser" and c.required for c in e.deferred_checks)
    assert e.scan_complete and not e.checks_complete


def test_snapshot_reader_cannot_change_requested_workspace(tmp_path: Path) -> None:
    root, peer = tmp_path / "workspace", tmp_path / "peer"
    root.mkdir()
    _ledger_control(peer)
    reader = getattr(ledger, "WorkspaceReadSnapshot", None)
    query = getattr(ledger, "read_declared_interface_ledger_snapshot", None)
    assert reader is not None and query is not None
    with pytest.raises(ValueError, match="workspace"):
        query(str(root), str(peer / ".polaris/runtime"), reader=reader(peer))


def test_status_keeps_explicit_root_requirement_when_snapshot_supplied(tmp_path: Path) -> None:
    _ledger_control(tmp_path)
    query = getattr(ledger, "read_declared_interface_ledger_snapshot", None)
    assert query is not None
    snapshot = query(str(tmp_path), str(tmp_path / ".polaris/runtime"))
    with pytest.raises(ValueError, match="explicit root"):
        ledger.read_declared_interface_ledger_status(str(tmp_path), "", snapshot=snapshot)
