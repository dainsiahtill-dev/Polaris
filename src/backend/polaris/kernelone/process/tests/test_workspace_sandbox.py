"""Real Linux namespace effects; no provider or delivery acceptance claims."""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import socket
import tempfile
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import pytest
from polaris.kernelone.process.process_tree import (
    ProcessTreeCancelledError,
    ProcessTreeRunControl,
    run_process_tree_safe,
)


@pytest.fixture
def sandbox_workspace() -> Iterator[Path]:
    private = Path.home() / ".polaris" / "tmp"
    private.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workspace-sandbox-", dir=private) as name:
        workspace = Path(name) / "project"
        workspace.mkdir()
        control = workspace / ".polaris" / "runtime" / "engineering"
        control.mkdir(parents=True)
        (control / "sessions.sqlite3").write_text("host database", encoding="utf-8")
        (control / "workspace.lock").write_text("host lock", encoding="utf-8")
        snapshots = workspace / ".polaris" / "runtime" / "contexts"
        snapshots.mkdir()
        (snapshots / "snapshot.json").write_text("host snapshot", encoding="utf-8")
        yield workspace


def _sandbox(argv: list[str], workspace: Path, **scope: object) -> list[str]:
    name = "polaris.kernelone.process.sandbox"
    assert importlib.util.find_spec(name) is not None, "workspace sandbox primitive is not implemented"
    module = importlib.import_module(name)
    options = {
        "workspace": workspace,
        "cwd": workspace,
        "read_roots": (workspace,),
        "write_roots": (workspace,),
        "network_allowed": False,
        **scope,
    }
    return module.build_workspace_sandbox_argv(argv, **options)


def _run(workspace: Path, script: str, **scope: object):
    argv = _sandbox(["/bin/sh", "-c", script], workspace, **scope)
    return run_process_tree_safe(argv, timeout=10, cancel_control=ProcessTreeRunControl())


def test_workspace_write_and_private_cache_work_without_host_environment(sandbox_workspace: Path, monkeypatch):
    monkeypatch.setenv("POLARIS_SANDBOX_SECRET", "host-secret")
    result = _run(
        sandbox_workspace,
        'printf source > result.txt; printf cache > "$XDG_CACHE_HOME/entry"; '
        'printf output > "$POLARIS_SANDBOX_OUTPUT/result"; printf temp > "$TMPDIR/temp"; '
        'test -z "${POLARIS_SANDBOX_SECRET-}" && test "$HOME" != /home/dains',
    )
    assert result.returncode == 0, result.stderr
    assert (sandbox_workspace / "result.txt").read_text(encoding="utf-8") == "source"
    assert not (sandbox_workspace / "entry").exists()


@pytest.mark.parametrize("via_symlink", [False, True])
def test_shell_cannot_write_outside_workspace(sandbox_workspace: Path, via_symlink: bool):
    sentinel = sandbox_workspace.parent / "outside-sentinel"
    sentinel.write_text("untouched", encoding="utf-8")
    destination = sentinel
    if via_symlink:
        destination = sandbox_workspace / "escape"
        destination.symlink_to(sentinel)
    result = _run(sandbox_workspace, f"printf changed > '{destination}'")
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "untouched"


@pytest.mark.parametrize(
    "target",
    [
        ".polaris/runtime/engineering/sessions.sqlite3",
        ".polaris/runtime/engineering/workspace.lock",
        ".polaris/runtime/contexts/snapshot.json",
    ],
)
@pytest.mark.parametrize("operation", ["write", "delete", "replace", "symlink"])
def test_control_records_cannot_be_modified_or_replaced(sandbox_workspace: Path, target: str, operation: str):
    path = sandbox_workspace / target
    before = path.read_text(encoding="utf-8")
    if operation == "write":
        command = f"printf forged > '{target}'"
    elif operation == "delete":
        command = f"rm '{target}'"
    elif operation == "replace":
        command = f"printf forged > replacement; mv replacement '{target}'"
    else:
        (sandbox_workspace / "control-alias").symlink_to(path)
        command = "printf forged > control-alias"
    result = _run(sandbox_workspace, command)
    assert result.returncode != 0
    assert path.read_text(encoding="utf-8") == before


def test_control_directory_itself_cannot_be_replaced(sandbox_workspace: Path):
    result = _run(sandbox_workspace, "mv .polaris stolen && mkdir .polaris")
    assert result.returncode != 0
    assert (sandbox_workspace / ".polaris/runtime/engineering/sessions.sqlite3").read_text(
        encoding="utf-8"
    ) == "host database"
    assert not (sandbox_workspace / "stolen").exists()


def test_only_explicit_source_write_root_is_writable(sandbox_workspace: Path):
    source = sandbox_workspace / "src"
    source.mkdir()
    (sandbox_workspace / "readonly.txt").write_text("unchanged", encoding="utf-8")
    result = _run(sandbox_workspace, "printf ok > src/result.txt", write_roots=(source,))
    assert result.returncode == 0, result.stderr
    result = _run(sandbox_workspace, "printf bad > readonly.txt", write_roots=(source,))
    assert result.returncode != 0
    assert (sandbox_workspace / "readonly.txt").read_text(encoding="utf-8") == "unchanged"
    assert (source / "result.txt").read_text(encoding="utf-8") == "ok"


def test_explicit_read_root_does_not_grant_write(sandbox_workspace: Path):
    reference = sandbox_workspace.parent / "reference"
    reference.mkdir()
    (reference / "data.txt").write_text("reference", encoding="utf-8")
    result = _run(sandbox_workspace, f"cat '{reference}/data.txt'", read_roots=(sandbox_workspace, reference))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "reference"
    result = _run(
        sandbox_workspace,
        f"printf corrupt > '{reference}/data.txt'",
        read_roots=(sandbox_workspace, reference),
    )
    assert result.returncode != 0
    assert (reference / "data.txt").read_text(encoding="utf-8") == "reference"


def test_network_disabled_cannot_connect_to_host_loopback(sandbox_workspace: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = [
            "/usr/bin/python3",
            "-c",
            f"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=1)",
        ]
        result = run_process_tree_safe(_sandbox(command, sandbox_workspace), timeout=5)
        assert result.returncode != 0
        allowed = run_process_tree_safe(_sandbox(command, sandbox_workspace, network_allowed=True), timeout=5)
        assert allowed.returncode == 0, allowed.stderr


def _matching_processes(marker: str) -> list[int]:
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except (OSError, ProcessLookupError):
            continue
        if marker.encode("utf-8") in command:
            result.append(int(entry.name))
    return result


def test_cancellation_reaps_setsid_child_before_return(sandbox_workspace: Path):
    marker = "sandbox-escaped-child-" + uuid.uuid4().hex
    ready = sandbox_workspace / "ready"
    child = (
        "import os,time; from pathlib import Path; "
        "Path('ready').write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(60)"
    )
    parent = (
        "import subprocess,time; "
        f"subprocess.Popen(['/usr/bin/python3','-c',{child!r},{marker!r}], start_new_session=True); "
        "time.sleep(60)"
    )
    command = _sandbox(["/usr/bin/python3", "-c", parent], sandbox_workspace)
    control = ProcessTreeRunControl()
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(run_process_tree_safe, command, timeout=10, cancel_control=control)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline and not worker.done():
                time.sleep(0.02)
            assert ready.exists(), worker.result() if worker.done() else "child did not start"
            pids = _matching_processes(marker)
            assert pids, "host observer must see real detached descendant before cancellation"
            control.cancel()
            with pytest.raises(ProcessTreeCancelledError, match="cancelled_and_drained"):
                worker.result(timeout=5)
            assert not _matching_processes(marker), "cancellation returned before namespace descendants were reaped"
            assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
        finally:
            control.cancel()
            if not worker.done():
                with suppress(ProcessTreeCancelledError):
                    worker.result(timeout=5)


@pytest.mark.parametrize("scope", ["outside_write", "control_write", "home_read", "symlink_control"])
def test_unsafe_mount_scope_fails_before_execution(sandbox_workspace: Path, scope: str):
    changes = {}
    if scope == "outside_write":
        changes["write_roots"] = (sandbox_workspace.parent,)
    elif scope == "control_write":
        changes["write_roots"] = (sandbox_workspace / ".polaris",)
    elif scope == "home_read":
        changes["read_roots"] = (Path.home(),)
    else:
        shutil.rmtree(sandbox_workspace / ".polaris")
        (sandbox_workspace / ".polaris").symlink_to(sandbox_workspace.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="sandbox"):
        _sandbox(["/usr/bin/true"], sandbox_workspace, **changes)


def test_preexisting_hardlink_cannot_modify_host_control(sandbox_workspace: Path):
    control = sandbox_workspace / ".polaris/runtime/engineering/sessions.sqlite3"
    os.link(control, sandbox_workspace / "innocent-source.txt")
    with pytest.raises(ValueError, match=r"sandbox.*hardlink"):
        _run(sandbox_workspace, "printf forged > innocent-source.txt")
    assert control.read_text(encoding="utf-8") == "host database"


def test_missing_bubblewrap_is_explicit_and_never_runs_bare(sandbox_workspace: Path, monkeypatch):
    module = importlib.import_module("polaris.kernelone.process.sandbox")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(module.WorkspaceSandboxUnavailableError, match=r"bubblewrap.*user namespaces"):
        _run(sandbox_workspace, "printf unsafe > escaped.txt")
    assert not (sandbox_workspace / "escaped.txt").exists()


def test_hardlinks_fully_inside_write_roots_remain_writable(sandbox_workspace: Path):
    first = sandbox_workspace / "first"
    second = sandbox_workspace / "second"
    first.mkdir()
    second.mkdir()
    (first / "source.txt").write_text("before", encoding="utf-8")
    os.link(first / "source.txt", second / "alias.txt")
    result = _run(
        sandbox_workspace,
        "printf after > second/alias.txt",
        write_roots=(first, second, first),
    )
    assert result.returncode == 0, result.stderr
    assert (first / "source.txt").read_text(encoding="utf-8") == "after"
