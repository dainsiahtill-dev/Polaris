"""Behavioural tests for the PM contract store's KernelOne-backed JSON writes.

Scope of this module
--------------------
``pm_contract_store.write_json_atomic`` is the single write path for PM
contracts, PM state and Director results.  It must route through the
KernelOne atomic JSON capability so that the active adapter owns the physical
write, instead of performing a direct non-atomic text write.

The tests pin four properties:

1. The write performs exactly one physical atomic replacement (one rename of a
   temporary file over the target) and leaves no temporary residue.
2. The on-disk payload is UTF-8 and readable from a cold reader (a separate
   process that shares no in-process state with the writer).
3. A rejected write (an adapter that cannot accept the protocol atomic call
   shape) and a write that fails *after* the effect happened are both
   surfaced to the caller and are never replayed through a second writer.
4. A custom, protocol-compliant adapter is honoured: the store asks the
   adapter for an atomic write instead of hard-coding a writer.

Known pre-existing failures
---------------------------
``test_pm_contract_store_rejects_non_kfs_path`` and
``test_engine_dispatch_contracts_backfill_missing_runtime_plan`` fail before
this change and are unrelated to the write route: both depend on the
workspace inference of ``_build_kernel_fs_for_path``, which resolves the
workspace from the requested path.  They are reported separately and are not
modified here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.runtime.state_owner.internal.pm_contract_store import (
    ensure_engine_dispatch_contracts,
    read_json_safe,
    write_json_atomic,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs.registry import get_default_adapter, set_default_adapter
from polaris.kernelone.storage import resolve_logical_path
from polaris.kernelone.storage.io_paths import resolve_artifact_path


def test_pm_contract_store_roundtrip_under_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    payload = {"tasks": [{"id": "t-1", "title": "migrate-kfs"}], "version": 1}
    logical_path = "runtime/contracts/pm_tasks.contract.json"

    write_json_atomic(logical_path, payload)

    absolute_path = Path(resolve_logical_path(str(tmp_path), logical_path))
    assert absolute_path.is_file()
    assert read_json_safe(logical_path) == payload


def test_pm_contract_store_rejects_non_kfs_path(tmp_path: Path) -> None:
    outside_path = tmp_path / "pm_state.json"

    with pytest.raises(ValueError, match="KernelFileSystem managed roots"):
        write_json_atomic(str(outside_path), {"status": "invalid-path"})

    assert read_json_safe(str(outside_path)) is None


def test_engine_dispatch_contracts_backfill_missing_runtime_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = workspace / ".polaris" / "runtime"
    workspace.mkdir()
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    normalized = {
        "overall_goal": "Stabilize PM to Director dispatch",
        "tasks": [
            {
                "id": "PM-DISPATCH-1",
                "title": "Run Director after PM planning",
                "goal": "Create dispatch prerequisites from PM output.",
                "target_files": ["src/backend/polaris/cells/runtime/state_owner/internal/pm_contract_store.py"],
                "execution_checklist": ["Persist runtime contracts", "Validate runtime plan"],
                "acceptance": ["Director dispatch can start"],
            }
        ],
    }
    runtime_pm_tasks = resolve_artifact_path(str(workspace), "", "runtime/contracts/pm_tasks.contract.json")
    run_pm_tasks = resolve_artifact_path(str(workspace), "", "runtime/runs/pm-00001/contracts/pm_tasks.contract.json")
    runtime_plan = resolve_artifact_path(str(workspace), "", "runtime/contracts/plan.md")

    ensure_engine_dispatch_contracts(
        normalized=normalized,
        run_pm_tasks=run_pm_tasks,
        runtime_pm_tasks_full=runtime_pm_tasks,
        runtime_plan_full=runtime_plan,
    )

    plan_text = Path(runtime_plan).read_text(encoding="utf-8")
    assert "Generated from the PM task contract" in plan_text
    assert "Run Director after PM planning" in plan_text
    assert read_json_safe(runtime_pm_tasks) == normalized
    assert read_json_safe(run_pm_tasks) == normalized


# ---------------------------------------------------------------------------
# Helpers for the atomic-write behaviour tests
# ---------------------------------------------------------------------------


class _RecordingLocalAdapter(LocalFileSystemAdapter):
    """Transparent subclass of the real local adapter that records its calls.

    The physical behaviour is the production one (temp file plus rename); only
    the calls are observed so that the test can assert how the store asked for
    the write.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = False,
    ) -> int:
        self.calls.append({"path": path, "atomic": atomic, "encoding": encoding})
        return super().write_text(path, content, encoding=encoding, atomic=atomic)


class _NonCompliantAdapter(LocalFileSystemAdapter):
    """Adapter whose ``write_text`` predates the protocol ``atomic`` keyword."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:  # type: ignore[override]
        self.calls.append(path)
        raise AssertionError("a rejected atomic write must never reach the adapter")


class _PostWriteErrorAdapter(LocalFileSystemAdapter):
    """Adapter that commits the atomic replacement and then raises."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = False,
    ) -> int:
        self.calls.append(path)
        size = super().write_text(path, content, encoding=encoding, atomic=atomic)
        raise OSError("synthetic adapter failure after the replacement was committed")


def _install_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create an isolated workspace and make it the current working directory.

    The workspace is the process' current directory so that the store resolves
    relative logical paths against it.  The runtime root env is pinned to the
    canonical project-local root of that workspace, because the storage layout
    refuses to write anywhere else.
    """
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    return workspace


def _restore_default_adapter() -> None:
    """Reinstate the adapter that was active before the test installed one."""
    set_default_adapter(get_default_adapter())


def _record_renames(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record every physical rename performed while the patch is active."""
    renames: list[tuple[str, str]] = []
    original_replace = os.replace

    def counting_replace(src: Any, dst: Any) -> None:
        renames.append((str(src), str(dst)))
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", counting_replace)
    return renames


def _physical_target(workspace: Path, logical_path: str) -> Path:
    return Path(resolve_logical_path(str(workspace), logical_path))


def test_pm_contract_store_write_performs_single_atomic_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The contract write performs exactly one physical atomic replacement."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    adapter = _RecordingLocalAdapter()
    set_default_adapter(adapter)
    try:
        renames = _record_renames(monkeypatch)
        write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 1, "tasks": []})
    finally:
        _restore_default_adapter()

    assert len(adapter.calls) == 1, "the contract write must reach the adapter exactly once"
    assert adapter.calls[0]["atomic"] is True, "the contract write must request an atomic write"
    assert len(renames) == 1, "the contract write must perform exactly one physical replacement"

    target = _physical_target(workspace, "runtime/contracts/pm_tasks.contract.json")
    assert renames[0][1] == str(target)
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1, "tasks": []}

    # The temporary file used for the replacement must not survive.
    assert sorted(item.name for item in target.parent.iterdir()) == [target.name]


def test_pm_contract_store_atomic_write_is_utf8_and_cold_readable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The committed payload is UTF-8 and complete for a cold reader."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    payload = {"goal": "迁移到原子写入", "owner": "polaris", "count": 3}
    logical_path = "runtime/contracts/pm_tasks.contract.json"

    write_json_atomic(logical_path, payload)

    target = _physical_target(workspace, logical_path)
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    assert "迁移到原子写入" in text
    assert "\\u" not in text, "ensure_ascii=False must keep non-ASCII characters unescaped"
    assert text.endswith("\n")
    assert json.loads(text) == payload

    # A cold reader is a separate process that shares no in-process state with
    # the writer, so it observes only what reached the filesystem.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys;raw=open(sys.argv[1],'rb').read();"
            "sys.stdout.write(json.dumps(json.loads(raw.decode('utf-8')),ensure_ascii=False))",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout) == payload


def test_pm_contract_store_rejects_non_compliant_adapter_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An adapter that cannot accept the atomic call shape is rejected."""
    _install_workspace(monkeypatch, tmp_path)
    adapter = _NonCompliantAdapter()
    set_default_adapter(adapter)
    try:
        with pytest.raises(NotImplementedError):
            write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 1})
    finally:
        _restore_default_adapter()

    assert adapter.calls == [], "a rejected atomic write must not reach the adapter"
    target = _physical_target(Path.cwd(), "runtime/contracts/pm_tasks.contract.json")
    assert not target.exists()


def test_pm_contract_store_propagates_post_write_error_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failure after the replacement stays visible and is never replayed."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    adapter = _PostWriteErrorAdapter()
    set_default_adapter(adapter)
    try:
        with pytest.raises(OSError, match="synthetic adapter failure after the replacement"):
            write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 2})
    finally:
        _restore_default_adapter()

    assert len(adapter.calls) == 1, "a failed write must not be replayed through a second writer"

    # The effect did happen before the error, and the store must not hide it
    # behind a reported success: the payload is on disk with the committed value.
    target = _physical_target(workspace, "runtime/contracts/pm_tasks.contract.json")
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}


def test_pm_contract_store_honours_custom_compliant_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A custom protocol-compliant adapter receives the atomic write request."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    adapter = _RecordingLocalAdapter()
    set_default_adapter(adapter)
    try:
        write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 3, "ok": True})
    finally:
        _restore_default_adapter()

    assert len(adapter.calls) == 1
    assert adapter.calls[0]["atomic"] is True
    assert adapter.calls[0]["encoding"] == "utf-8"

    target = _physical_target(workspace, "runtime/contracts/pm_tasks.contract.json")
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 3, "ok": True}
