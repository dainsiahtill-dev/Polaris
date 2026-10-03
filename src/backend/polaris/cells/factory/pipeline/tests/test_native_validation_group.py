"""Self-owned compiler fixtures; never execute a generated Bench target."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.process import run_process_tree_safe


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _typescript_failure_fixture(root: Path) -> None:
    _write(root, "src/main.ts", "export const answer: number = 42;\n")
    _write(root, "tests/verify.ts", "import { answer } from '../src/main';\nexport const actual = answer;\n")
    _write(
        root,
        "tsconfig.json",
        json.dumps(
            {
                "compilerOptions": {"rootDir": "src", "outDir": "dist", "declaration": True, "sourceMap": True},
                "include": ["src/**/*.ts", "tests/**/*.ts"],
            }
        ),
    )


def _group(root: Path, tmp_path: Path, **kwargs: Any) -> Any:
    from polaris.cells.factory.pipeline.internal.native_validation_group import verification_group

    parent = tmp_path / "staging"
    parent.mkdir(exist_ok=True)
    tools = kwargs.pop("toolchain_roots", ())
    hashes = kwargs.pop("input_hashes") if "input_hashes" in kwargs else _inventory(root)
    return verification_group(
        workspace=root,
        candidate_id="candidate-1",
        input_hashes=hashes,
        staging_parent=parent,
        toolchain_roots=tools,
        **kwargs,
    )


def _run(group: Any, argv: list[str]) -> subprocess.CompletedProcess[str]:
    if argv[0] == "node":
        argv = ["/usr/bin/node", *argv[1:]]
    prepared = group.prepare_command(argv)
    completed = run_process_tree_safe(prepared.argv, cwd=prepared.cwd, timeout=10)
    group.record_result(prepared, completed, drained=True)
    return completed


def test_failed_real_typescript_compiler_does_not_emit_into_live_fixture(tmp_path: Path) -> None:
    if not shutil.which("tsc"):
        pytest.skip("real TypeScript compiler unavailable")
    workspace = tmp_path / "source"
    _typescript_failure_fixture(workspace)
    before = _inventory(workspace)
    with _group(workspace, tmp_path) as group:
        result = _run(group, ["tsc", "-p", "tsconfig.json"])
        assert result.returncode == 2
        assert "TS6059" in result.stdout + result.stderr
        assert (group.staging_workspace / "tests/verify.js").is_file()
        assert group.effects_summary()["input_hashes"] == before
    assert _inventory(workspace) == before


def test_group_build_test_start_share_outputs_without_inventing_drain_proof(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "build.js", "require('fs').writeFileSync('output.json', JSON.stringify({answer:42}));\n")
    _write(workspace, "test.js", "if(require('./output.json').answer !== 42) process.exit(1);\n")
    _write(workspace, "start.js", "console.log(require('./output.json').answer);\n")
    before = _inventory(workspace)
    with _group(workspace, tmp_path) as group:
        stage = group.staging_workspace
        assert _run(group, ["node", "build.js"]).returncode == 0
        assert _run(group, ["node", "test.js"]).returncode == 0
        assert _run(group, ["node", "start.js"]).stdout.strip() == "42"
        evidence = group.effects_summary()
        assert len(evidence["commands"]) == 3
        assert evidence["output_hashes"]["output.json"]
        assert evidence["authoritative"] is False
    assert stage.is_dir()  # Caller `drained=True` is not a physical-owner proof.
    assert group.effects_summary()["drain_status"] == "pending_process_owner"
    assert _inventory(workspace) == before


def test_closed_manifest_includes_authored_js_but_not_unknown_emitted_js(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "authored.js", "console.log('authored');\n")
    _write(workspace, "unknown.js", "throw new Error('stale compiler output');\n")
    declared = {"authored.js": _inventory(workspace)["authored.js"]}
    with _group(workspace, tmp_path, input_hashes=declared) as group:
        assert (group.staging_workspace / "authored.js").is_file()
        assert not (group.staging_workspace / "unknown.js").exists()
        assert _run(group, ["node", "authored.js"]).stdout.strip() == "authored"


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape", ".polaris/runtime/token", "./", ""])
def test_input_paths_fail_closed(tmp_path: Path, path: str) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    with pytest.raises((ValueError, RuntimeError)), _group(workspace, tmp_path, input_hashes={path: "0" * 64}):
        pass


def test_root_and_symlink_inputs_are_not_copied(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    (workspace / "link.txt").symlink_to(workspace / "source.txt")
    with (
        pytest.raises((ValueError, RuntimeError)),
        _group(workspace, tmp_path, input_hashes={"link.txt": _inventory(workspace)["source.txt"]}),
    ):
        pass
    with pytest.raises((ValueError, RuntimeError)), _group(Path("/"), tmp_path, input_hashes={"etc/passwd": "0" * 64}):
        pass


def test_stale_expected_hash_and_later_source_drift_cannot_be_success(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    with pytest.raises((ValueError, RuntimeError)), _group(workspace, tmp_path, input_hashes={"source.txt": "0" * 64}):
        pass
    with pytest.raises((ValueError, RuntimeError)), _group(workspace, tmp_path) as group:
        _write(workspace, "source.txt", "concurrent owner edit\n")
        group.prepare_command(["node", "--version"])


def test_dependencies_are_readonly_and_escape_links_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "check.js", "require('fs').writeFileSync('node_modules/dep.txt','pollution');\n")
    dependency = tmp_path / "dependencies"
    _write(dependency, "dep.txt", "dependency\n")
    before = _inventory(dependency)
    with _group(workspace, tmp_path, dependency_roots={"node_modules": dependency}) as group:
        result = _run(group, ["node", "check.js"])
        assert result.returncode != 0
        assert "EROFS" in result.stderr
    assert _inventory(dependency) == before
    (dependency / "escape").symlink_to(workspace, target_is_directory=True)
    with (
        pytest.raises((ValueError, RuntimeError)),
        _group(workspace, tmp_path, dependency_roots={"node_modules": dependency}),
    ):
        pass


def test_original_source_is_unmounted_and_network_is_disabled(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    program = (
        "const fs=require('fs'),os=require('os');\n"
        f"if(fs.existsSync({json.dumps(str(workspace))})) process.exit(10);\n"
        "if(Object.values(os.networkInterfaces()).flat().some(x=>!x.internal)) process.exit(11);\n"
        "console.log('isolated');\n"
    )
    _write(workspace, "check.js", program)
    with _group(workspace, tmp_path) as group:
        result = _run(group, ["node", "check.js"])
        assert result.returncode == 0
        assert result.stdout.strip() == "isolated"


def test_missing_isolation_does_not_execute_live_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    original = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "bwrap" else original(name))
    before = _inventory(workspace)
    with pytest.raises(RuntimeError, match="unavailable"), _group(workspace, tmp_path):
        pass
    assert _inventory(workspace) == before


def test_staging_reads_and_writes_route_through_kfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import KernelFileSystem

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    reads: list[tuple[str, str]] = []
    writes: list[tuple[str, str]] = []
    read = KernelFileSystem.workspace_read_bytes
    write = KernelFileSystem.workspace_write_bytes

    def read_spy(fs: Any, path: str) -> bytes:
        reads.append((fs.workspace, path))
        data = read(fs, path)
        assert isinstance(data, bytes)
        return data

    def write_spy(fs: Any, path: str, data: bytes) -> Any:
        writes.append((fs.workspace, path))
        return write(fs, path, data)

    monkeypatch.setattr(KernelFileSystem, "workspace_read_bytes", read_spy)
    monkeypatch.setattr(KernelFileSystem, "workspace_write_bytes", write_spy)
    with _group(workspace, tmp_path):
        pass
    assert (str(workspace), "source.txt") in reads
    assert any(path == "source.txt" and root != str(workspace) for root, path in writes)
    assert all(root != str(workspace) for root, _path in writes)


def test_denied_kfs_copy_never_uses_direct_write_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.kernelone.fs import KernelFileSystem

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")

    def denied(_fs: Any, _path: str, _data: bytes) -> Any:
        raise PermissionError("denied fixture staging")

    monkeypatch.setattr(KernelFileSystem, "workspace_write_bytes", denied)
    with pytest.raises((ValueError, RuntimeError), match="denied"), _group(workspace, tmp_path):
        pass


def test_platform_authority_root_is_not_a_readonly_dependency(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    authority = tmp_path / ".polaris"
    _write(authority, "authority.txt", "synthetic fixture only\n")
    with pytest.raises(RuntimeError), _group(workspace, tmp_path, dependency_roots={"deps": authority}):
        pass


def test_workspace_inside_system_mount_is_rejected_before_capture(tmp_path: Path) -> None:
    workspace = Path("/usr/share/zoneinfo")
    source = workspace / "Etc/UTC"
    if not source.is_file():
        pytest.skip("read-only system fixture unavailable")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    with (
        pytest.raises(RuntimeError, match="system_mount"),
        _group(workspace, tmp_path, input_hashes={"Etc/UTC": expected}),
    ):
        pass


def test_executable_replacement_cannot_retain_old_hash_after_real_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    tools = tmp_path / "tools"
    _write(tools, "probe", "#!/bin/sh\nprintf 'OLD\\n'\n")
    executable = tools / "probe"
    executable.chmod(0o755)
    with (
        pytest.raises(RuntimeError, match="executable_drift"),
        _group(workspace, tmp_path, toolchain_roots=(tools,)) as group,
    ):
        prepared = group.prepare_command([str(executable)])
        old_hash = prepared.executable_hash
        _write(tools, "replacement", "#!/bin/sh\nprintf 'NEW\\n'\n")
        (tools / "replacement").chmod(0o755)
        (tools / "replacement").replace(executable)
        result = run_process_tree_safe(prepared.argv, cwd=prepared.cwd, timeout=10)
        assert result.stdout.strip() == "NEW"
        assert hashlib.sha256(executable.read_bytes()).hexdigest() != old_hash
        group.record_result(prepared, result, drained=True)
    assert group.effects_summary()["source_binding_current"] is False
    assert group.staging_workspace.is_dir()


def test_actual_readonly_backing_drift_is_not_hidden_by_unchanged_base_seed(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "ORIGINAL\n")
    with pytest.raises(RuntimeError, match="backing_drift"), _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["cat", "source.txt"])
        group._root_fs.workspace_write_bytes("inputs/source.txt", b"TAMPERED\n")
        result = run_process_tree_safe(prepared.argv, cwd=prepared.cwd, timeout=10)
        assert result.stdout.strip() == "TAMPERED"
        assert (group.staging_workspace / "source.txt").read_text(encoding="utf-8") == "ORIGINAL\n"
        group.record_result(prepared, result, drained=True)
    assert group.effects_summary()["source_binding_current"] is False
    assert group.staging_workspace.is_dir()


def test_tampering_copy_adapter_is_rejected_before_command_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.fs import KernelFileSystem

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "ORIGINAL\n")
    write = KernelFileSystem.workspace_write_bytes

    def tamper(fs: Any, path: str, data: bytes) -> Any:
        return write(fs, path, b"TAMPERED\n" if path.startswith("inputs/") else data)

    monkeypatch.setattr(KernelFileSystem, "workspace_write_bytes", tamper)
    with pytest.raises(RuntimeError, match="backing_drift"), _group(workspace, tmp_path):
        pass


@pytest.mark.parametrize("mount_kind", ["dependency", "toolchain"])
@pytest.mark.parametrize("blocked", [".polaris", ".git"])
def test_nested_platform_authority_is_not_exposed_by_readonly_mounts(
    tmp_path: Path, mount_kind: str, blocked: str
) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    root = tmp_path / "external-root"
    _write(root, "nested/" + blocked + "/authority.txt", "synthetic fixture only\n")
    mounts = {"dependency_roots": {"deps": root}} if mount_kind == "dependency" else {"toolchain_roots": (root,)}
    with pytest.raises(RuntimeError, match="platform_root_forbidden"), _group(workspace, tmp_path, **mounts):
        pass


def test_only_internal_actual_execute_path_permits_terminal_disposal(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    with _group(workspace, tmp_path) as group:
        stage = group.staging_workspace
        prepared = group.prepare_command(["/usr/bin/node", "-e", "console.log('actual')"])
        result = group.execute_command(prepared, timeout=10)
        assert result.stdout.strip() == "actual"
    assert not stage.exists()
    assert group.effects_summary()["drain_status"] == "owner_terminal"


def test_fake_completed_process_and_forged_prepared_never_permit_disposal(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    with _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/true"])
        fake = subprocess.CompletedProcess(prepared.argv, 0, "", "")
        group.record_result(prepared, fake, drained=True)
        with pytest.raises(RuntimeError):
            group.execute_command(replace(prepared, argv=("/usr/bin/true",)), timeout=10)
    assert group.staging_workspace.exists()
    assert group.effects_summary()["drain_status"] == "pending_process_owner"


def test_actual_timeout_is_failed_terminal_evidence_not_pending_bool(tmp_path: Path) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "wait.js", "console.log('started');setTimeout(()=>{},5000);\n")
    with _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/node", "wait.js"])
        with pytest.raises(subprocess.TimeoutExpired):
            group.execute_command(prepared, timeout=0.3)
        assert group.effects_summary()["commands"][0]["timed_out"] is True
    assert not group.staging_workspace.exists()


def test_real_cancel_control_before_spawn_is_terminal(tmp_path: Path) -> None:
    from polaris.kernelone.process import ProcessTreeCancelledError, ProcessTreeRunControl

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    control = ProcessTreeRunControl()
    control.cancel()
    with _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/true"])
        with pytest.raises(ProcessTreeCancelledError):
            group.execute_command(prepared, timeout=10, cancel_control=control)
        assert group.effects_summary()["commands"][0]["cancelled"] is True
    assert not group.staging_workspace.exists()


def test_real_current_node_tool_root_smoke(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("current Node tool unavailable")
    executable = Path(node).resolve()
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    started = time.monotonic()
    with _group(workspace, tmp_path, toolchain_roots=(executable.parent.parent,)) as group:
        prepared = group.prepare_command([str(executable), "--version"])
        result = group.execute_command(prepared, timeout=300)
        assert result.returncode == 0
        assert result.stdout.startswith("v")
    print(json.dumps({"real_node_tool_root_smoke_seconds": round(time.monotonic() - started, 3)}))


def test_mount_cache_reuses_only_identical_leaf_witness_and_rejects_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.fs import KernelFileSystem

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    tools = tmp_path / "tools"
    _write(tools, "probe", "#!/bin/sh\nprintf 'stable\\n'\n")
    (tools / "probe").chmod(0o755)
    read = KernelFileSystem.workspace_read_bytes
    seen: list[str] = []

    def read_spy(fs: Any, path: str) -> bytes:
        if fs.workspace == str(tools):
            seen.append(path)
        data = read(fs, path)
        assert isinstance(data, bytes)
        return data

    monkeypatch.setattr(KernelFileSystem, "workspace_read_bytes", read_spy)
    with (
        pytest.raises(RuntimeError, match="toolchain_drift"),
        _group(workspace, tmp_path, toolchain_roots=(tools,)) as group,
    ):
        assert seen.count("probe") == 1
        group.assert_inputs_current()
        assert seen.count("probe") == 1
        _write(tools, "replacement", "#!/bin/sh\nprintf 'stable\\n'\n")
        (tools / "replacement").chmod(0o755)
        (tools / "replacement").replace(tools / "probe")
        group.assert_inputs_current()


def test_owner_cancel_contains_setsid_child_and_disposes_only_after_terminal(tmp_path: Path) -> None:
    from polaris.kernelone.process import ProcessTreeCancelledError, ProcessTreeRunControl

    workspace = tmp_path / "source"
    _write(
        workspace,
        "wait.js",
        "const fs=require('fs'),cp=require('child_process');\n"
        "cp.spawn('/bin/sh',['-c','sleep 0.8; echo late > late.txt'],{detached:true,stdio:'ignore'});\n"
        "fs.writeFileSync('ready.txt','ready');setTimeout(()=>{},5000);\n",
    )
    control = ProcessTreeRunControl()
    with _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/node", "wait.js"])
        stage = group.staging_workspace
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(group.execute_command, prepared, timeout=10, cancel_control=control)
            deadline = time.monotonic() + 5
            while not (stage / "ready.txt").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (stage / "ready.txt").exists()
            control.cancel()
            with pytest.raises(ProcessTreeCancelledError):
                future.result(timeout=5)
        assert not (stage / "late.txt").exists()
    assert not stage.exists()
    assert not (workspace / "late.txt").exists()


@pytest.mark.parametrize("drain_error", [False, True])
def test_unknown_or_drain_failure_never_disposes_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drain_error: bool
) -> None:
    from polaris.cells.factory.pipeline.internal import native_validation_group as module
    from polaris.kernelone.process import ProcessTreeDrainError

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")

    def failed_owner(*_args: Any, **_kwargs: Any) -> Any:
        if drain_error:
            raise ProcessTreeDrainError("fixture drain cannot be proved")
        raise OSError("fixture unknown process failure")

    monkeypatch.setattr(module, "_owned_process_runner", failed_owner)
    with _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/true"])
        with pytest.raises((RuntimeError, OSError)):
            group.execute_command(prepared, timeout=10)
    assert group.staging_workspace.exists()
    assert group.effects_summary()["dispose_allowed"] is False


def test_real_fake_bwrap_cannot_expose_live_source_and_authorize_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned synthetic source\n")
    wrapper = tmp_path / "fake-bwrap"
    wrapper.write_text(
        f"#!/bin/sh\ntest -f {str(workspace / 'source.txt')!r} && printf 'LIVE_VISIBLE\\n'\nexit 0\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: str(wrapper) if name == "bwrap" else which(name))
    with pytest.raises(RuntimeError, match="isolation_binary_untrusted"), _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/true"])
        result = group.execute_command(prepared, timeout=10)
        assert result.stdout.strip() == "LIVE_VISIBLE"


def test_system_bwrap_binding_is_canonical_and_written_to_actual_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    alias = tmp_path / "system-bwrap-alias"
    alias.symlink_to("/usr/bin/bwrap")
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: str(alias) if name == "bwrap" else which(name))
    with _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/true"])
        assert prepared.argv[0] == "/usr/bin/bwrap"
        group.execute_command(prepared, timeout=10)
        binding = group.effects_summary()["commands"][0]["isolation_binary"]
        assert binding["path"] == "/usr/bin/bwrap"
        assert binding["sha256"] == hashlib.sha256(Path("/usr/bin/bwrap").read_bytes()).hexdigest()
        assert binding["witness"]
        assert binding["uid"] == 0
    assert not group.staging_workspace.exists()


def test_isolation_binary_read_drift_is_rejected_and_stage_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.fs import KernelFileSystem

    workspace = tmp_path / "source"
    _write(workspace, "source.txt", "owned\n")
    read = KernelFileSystem.workspace_read_bytes
    corrupt = False

    def inconsistent_read(fs: Any, path: str) -> bytes:
        data = read(fs, path)
        assert isinstance(data, bytes)
        return data + b"synthetic-drift" if corrupt and fs.workspace == "/usr/bin" and path == "bwrap" else data

    monkeypatch.setattr(KernelFileSystem, "workspace_read_bytes", inconsistent_read)
    with pytest.raises(RuntimeError, match="isolation_binary_drift"), _group(workspace, tmp_path) as group:
        prepared = group.prepare_command(["/usr/bin/true"])
        result = run_process_tree_safe(prepared.argv, cwd=prepared.cwd, timeout=10)
        assert result.returncode == 0
        corrupt = True
        group.record_result(prepared, result, drained=True)
    assert group.effects_summary()["source_binding_current"] is False
    assert group.staging_workspace.exists()
