"""Physical output handoff under readonly source authority, not delivery proof."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from polaris.kernelone import process
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.process.process_tree import (
    ProcessTreeCancelledError,
    ProcessTreeDrainError,
    ProcessTreeRunControl,
    run_process_tree_safe,
)
from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv
from polaris.kernelone.storage import clear_storage_roots_cache, resolve_runtime_path

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="30" fill="red"/></svg>'
)


@pytest.fixture
def capture_workspace(tmp_path: Path) -> Iterator[Path]:
    fs = KernelFileSystem(str(tmp_path), get_default_adapter(), read_only_paths=True)
    fs.workspace_mkdir("project/.polaris", parents=True)
    fs.workspace_write_text("project/source.txt", "unchanged", encoding="utf-8")
    yield tmp_path / "project"


def _open(workspace: Path):
    opener = getattr(process, "open_command_output_capture", None)
    assert opener is not None, "KernelOne command output capture capability is missing"
    return opener(workspace)


def _sandbox(workspace: Path, script: str, **options: object) -> list[str]:
    return build_workspace_sandbox_argv(
        ["/bin/sh", "-c", script],
        workspace=workspace,
        cwd=workspace,
        read_roots=[workspace],
        write_roots=[],
        network_allowed=False,
        **options,
    )


def _write(workspace: Path, target: Path, data: bytes = b"actual") -> None:
    fs = KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True)
    fs.write_bytes(str(target), data)


def test_readonly_source_command_exports_exact_bytes_until_own_capture_cleanup(capture_workspace: Path):
    workspace = capture_workspace
    with _open(workspace) as capture:
        root = capture.root
        assert root.parent == workspace / ".polaris/runtime/command-output"
        assert len(root.name) == 32
        sibling = root.parent / ("f" * 32)
        fs = KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True)
        fs.workspace_mkdir(str(sibling))
        _write(workspace, sibling / "keep", b"unrelated")
        script = f"printf '%s' '{SVG.decode('utf-8')}' > /sandbox-output/report.svg; printf forbidden > source.txt"
        result = run_process_tree_safe(
            _sandbox(workspace, script, capture_output_root=root), timeout=5, cancel_control=ProcessTreeRunControl()
        )
        assert result.returncode != 0, "source write must remain denied"
        assert "Read-only file system" in result.stderr
        assert (workspace / "source.txt").read_text(encoding="utf-8") == "unchanged"
        assert root.is_dir()
        outputs = capture.read(["report.svg"])
        assert [(item.name, item.data) for item in outputs] == [("report.svg", SVG)]
        assert (root / "report.svg").is_file()
    assert not root.exists()
    assert (sibling / "keep").read_bytes() == b"unrelated"


def test_no_capture_keeps_existing_private_tmpfs_argv(capture_workspace: Path):
    normal = _sandbox(capture_workspace, "true")
    explicit = _sandbox(capture_workspace, "true", capture_output_root=None)
    assert normal == explicit
    assert normal[normal.index("/sandbox-output") - 1] == "--tmpfs"


def test_explicit_canonical_runtime_uses_existing_single_storage_root(capture_workspace: Path, monkeypatch):
    workspace = capture_workspace
    runtime = workspace / ".polaris/runtime"
    KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True).workspace_mkdir(".polaris/runtime")
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime))
    clear_storage_roots_cache()
    try:
        with _open(workspace) as capture:
            assert capture.root.parent == Path(
                resolve_runtime_path(str(workspace), "runtime/command-output", read_only=True)
            )
            result = run_process_tree_safe(
                _sandbox(workspace, "printf exact > /sandbox-output/result", capture_output_root=capture.root),
                timeout=5,
                cancel_control=ProcessTreeRunControl(),
            )
            assert result.returncode == 0, result.stderr
            assert capture.read(["result"])[0].data == b"exact"
        assert not (workspace / "runtime").exists()
    finally:
        clear_storage_roots_cache()


def test_unconfirmed_drain_retains_scratch_and_never_reports_readable_capture(capture_workspace: Path):
    with pytest.raises(ProcessTreeDrainError), _open(capture_workspace) as capture:
        root = capture.root
        _write(capture_workspace, root / "possibly-live", b"unknown")
        raise ProcessTreeDrainError("physical drain is unconfirmed")
    assert (root / "possibly-live").read_bytes() == b"unknown"
    with pytest.raises(ValueError):
        capture.read(["possibly-live"])
    # The synthetic test starts no writer; explicitly close the retained resource.
    fs = KernelFileSystem(str(root.parent), get_default_adapter(), read_only_paths=True)
    fs.workspace_remove_tree(root.name, expected=fs.workspace_lstat(root.name))


def test_posteffect_commit_uncertainty_retains_real_output_without_extending_read_lifetime(capture_workspace: Path):
    with _open(capture_workspace) as capture:
        root = capture.root
        result = run_process_tree_safe(
            _sandbox(capture_workspace, "printf uncommitted > /sandbox-output/result", capture_output_root=root),
            timeout=5,
            cancel_control=ProcessTreeRunControl(),
        )
        assert result.returncode == 0, result.stderr
        assert capture.read(["result"])[0].data == b"uncommitted"
        retain = getattr(capture, "retain_for_reconciliation", None)
        assert retain is not None, "capture has no post-effect uncertainty retention capability"
        retain()
    assert (root / "result").read_bytes() == b"uncommitted"
    with pytest.raises(ValueError):
        capture.read(["result"])
    with pytest.raises(ValueError):
        capture.retain_for_reconciliation()
    # No writer exists in this actual completed process; emulate host reconciliation.
    fs = KernelFileSystem(str(root.parent), get_default_adapter(), read_only_paths=True)
    fs.workspace_remove_tree(root.name, expected=fs.workspace_lstat(root.name))


def test_capture_requires_existing_control_root_without_provisioning_workspace(tmp_path: Path):
    workspace = tmp_path / "unprovisioned"
    KernelFileSystem(str(tmp_path), get_default_adapter(), read_only_paths=True).workspace_mkdir("unprovisioned")
    with pytest.raises(ValueError), _open(workspace):
        pytest.fail("capture opened an unprovisioned control root")
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize("invalid", ["foreign", "namespace", "workspace", "symlink", "dirty", "non_uuid", "traversal"])
def test_mount_rejects_noncanonical_or_nonempty_capture(capture_workspace: Path, invalid: str):
    workspace = capture_workspace
    with _open(workspace) as capture:
        target = capture.root
        fs = KernelFileSystem(str(workspace.parent), get_default_adapter(), read_only_paths=True)
        if invalid == "foreign":
            fs.workspace_mkdir("foreign/.polaris", parents=True)
            with _open(workspace.parent / "foreign") as foreign, pytest.raises(ValueError):
                _sandbox(workspace, "true", capture_output_root=foreign.root)
            return
        if invalid == "namespace":
            target = target.parent
        elif invalid == "workspace":
            target = workspace
        elif invalid == "symlink":
            target = capture.root.parent / ("e" * 32)
            target.symlink_to(capture.root, target_is_directory=True)
        elif invalid == "dirty":
            _write(workspace, target / "unexpected")
        elif invalid == "non_uuid":
            target = capture.root.parent / "untrusted"
            fs.workspace_mkdir(str(target))
        elif invalid == "traversal":
            target = capture.root / ".." / capture.root.name
        with pytest.raises(ValueError):
            _sandbox(workspace, "true", capture_output_root=target)


@pytest.mark.parametrize(
    "name", ["../source.txt", "/etc/passwd", "nested/../report.svg", "./report.svg", "", "a\\b", "a//b"]
)
def test_capture_rejects_escaping_or_noncanonical_names(capture_workspace: Path, name: str):
    with _open(capture_workspace) as capture, pytest.raises(ValueError):
        capture.read([name])


@pytest.mark.parametrize("kind", ["missing", "symlink", "hardlink", "fifo", "directory", "parent_symlink"])
def test_capture_rejects_missing_linked_or_special_output(capture_workspace: Path, kind: str):
    workspace = capture_workspace
    with _open(workspace) as capture:
        target = capture.root / "result"
        name = "result"
        if kind == "symlink":
            target.symlink_to(workspace / "source.txt")
        elif kind == "hardlink":
            os.link(workspace / "source.txt", target)
        elif kind == "fifo":
            os.mkfifo(target)
        elif kind == "directory":
            KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True).workspace_mkdir(str(target))
        elif kind == "parent_symlink":
            target.symlink_to(workspace, target_is_directory=True)
            name = "result/source.txt"
        with pytest.raises((ValueError, RuntimeError, OSError)):
            capture.read([name])
    assert (workspace / "source.txt").read_text(encoding="utf-8") == "unchanged"


def test_capture_enforces_aggregate_bytes_count_duplicates_and_closed_state(capture_workspace: Path):
    with _open(capture_workspace) as capture:
        root = capture.root
        _write(capture_workspace, root / "first", b"abc")
        _write(capture_workspace, root / "second", b"def")
        with pytest.raises((ValueError, RuntimeError)):
            capture.read(["first", "second"], max_bytes=5)
        with pytest.raises(ValueError):
            capture.read(["first", "first"])
        with pytest.raises(ValueError):
            capture.read(["first", "second"], max_files=1)
        with pytest.raises(ValueError):
            capture.read(["first"] * 17, max_files=17)
        with pytest.raises(ValueError):
            capture.read(["first"], max_bytes=1_000_001)
        assert [item.data for item in capture.read(["first", "second"], max_bytes=6)] == [b"abc", b"def"]
    with pytest.raises((ValueError, RuntimeError)):
        capture.read(["first"])


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_physical_drain_precedes_capture_cleanup(capture_workspace: Path, stop: str):
    control = ProcessTreeRunControl()
    with _open(capture_workspace) as capture:
        root = capture.root
        command = _sandbox(
            capture_workspace,
            "printf started > /sandbox-output/start; (sleep 1; printf orphan > /sandbox-output/late) & wait",
            capture_output_root=root,
        )
        if stop == "timeout":
            with pytest.raises(subprocess.TimeoutExpired):
                run_process_tree_safe(command, timeout=0.25, cancel_control=control)
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_process_tree_safe, command, timeout=5, cancel_control=control)
                deadline = time.monotonic() + 3
                while not (root / "start").exists() and time.monotonic() < deadline:
                    threading.Event().wait(0.01)
                assert (root / "start").is_file()
                control.cancel()
                with pytest.raises(ProcessTreeCancelledError):
                    future.result(timeout=3)
        assert capture.read(["start"])[0].data == b"started"
        assert not (root / "late").exists()
    assert not root.exists()
    threading.Event().wait(1.1)
    assert not root.exists()
