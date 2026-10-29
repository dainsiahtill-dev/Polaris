"""Production KFS logical namespace boundary tests.

The logical ``runtime/*``, ``workspace/*`` and ``config/*`` namespaces must
bind their effects to the namespace root that ``storage.layout`` selected.
Before this boundary existed, the logical API resolved a lexical path and
handed that string to a path-based adapter, so a symbolic link in the
namespace could reach a file outside it while the receipt still pointed
inside.

These tests pin:

* the original counterexample (leaf and parent symbolic link) for every
  affected public logical operation;
* that the effects stay adapter-owned, so a custom deny policy is preserved;
* that the refusal is explicit and creates no physical trace;
* that legitimate operations, including the global config namespace, still
  advance;
* that the receipt stays truthful about what was written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.contracts import (
    DurabilityMode,
    FileWriteReceipt,
    NamespaceOperationUnavailableError,
)
from polaris.kernelone.fs.guarded_namespace_ops import (
    NamespaceBoundaryError,
    NamespaceOperationEffectUnknownError,
)
from polaris.kernelone.fs.types import NamespaceBindingV1
from polaris.kernelone.storage import clear_storage_roots_cache

_LOGICAL_OPERATIONS = (
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "append_text",
    "write_text_atomic",
    "write_json_atomic",
    "remove",
)


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, KernelFileSystem]:
    """Create an isolated workspace with its own canonical runtime root."""
    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    clear_storage_roots_cache()
    return workspace, KernelFileSystem(str(workspace), LocalFileSystemAdapter())


def _link_shape(tmp_path: Path, workspace: Path, shape: str) -> tuple[str, Path]:
    """Create one hostile link inside the runtime namespace.

    Returns the logical path and the synthetic canary outside the namespace.
    """
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    canary = outside / "target.txt"
    canary.write_text("SYNTHETIC_ORIGINAL", encoding="utf-8")

    runtime = workspace / ".polaris" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    if shape == "leaf":
        os.symlink(canary, runtime / "probe")
        return "runtime/probe", canary
    (runtime / "jump").symlink_to(outside)
    return "runtime/jump/target.txt", canary


def _invoke(fs: KernelFileSystem, operation: str, logical_path: str) -> Any:
    if operation == "read_text":
        return fs.read_text(logical_path)
    if operation == "read_bytes":
        return fs.read_bytes(logical_path)
    if operation == "write_text":
        return fs.write_text(logical_path, "CHANGED")
    if operation == "write_bytes":
        return fs.write_bytes(logical_path, b"CHANGED")
    if operation == "append_text":
        return fs.append_text(logical_path, "CHANGED")
    if operation == "write_text_atomic":
        return fs.write_text_atomic(logical_path, "CHANGED")
    if operation == "write_json_atomic":
        return fs.write_json_atomic(logical_path, {"changed": True})
    if operation == "remove":
        return fs.remove(logical_path)
    raise AssertionError(f"unknown operation {operation!r}")


def _tree(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(item.relative_to(root)) for item in root.rglob("*"))


# ---------------------------------------------------------------------------
# The original counterexample
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
@pytest.mark.parametrize("shape", ["leaf", "parent"])
@pytest.mark.parametrize("operation", _LOGICAL_OPERATIONS)
def test_logical_operations_never_reach_outside_the_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shape: str,
    operation: str,
) -> None:
    """No logical operation may mutate or read a file outside its namespace.

    This is the production counterexample: a symbolic link inside the
    logical namespace used to reach a synthetic canary outside it while the
    receipt still pointed inside the workspace.

    ``remove`` is the one operation whose effect on a *leaf* link is a
    documented ``unlink(2)`` property: it removes the link entry itself and
    never touches the link target.  That is an in-namespace effect, so the
    invariant for it is that the outside canary survives, not that the call
    is refused.
    """
    workspace, fs = _workspace(tmp_path, monkeypatch)
    logical_path, canary = _link_shape(tmp_path, workspace, shape)

    if operation == "remove" and shape == "leaf":
        assert fs.remove(logical_path) is True
    else:
        with pytest.raises(NamespaceBoundaryError):
            _invoke(fs, operation, logical_path)

    assert canary.read_text(encoding="utf-8") == "SYNTHETIC_ORIGINAL"


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_removing_a_leaf_link_removes_the_entry_not_the_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A leaf link removal is an in-namespace effect with documented semantics."""
    workspace, fs = _workspace(tmp_path, monkeypatch)
    logical_path, canary = _link_shape(tmp_path, workspace, "leaf")
    link = workspace / ".polaris" / "runtime" / "probe"

    assert fs.remove(logical_path) is True

    assert not link.exists(), "the link entry inside the namespace is removed"
    assert canary.read_text(encoding="utf-8") == "SYNTHETIC_ORIGINAL", "the link target is untouched"


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_read_leak_through_a_leaf_link_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A read through a linked leaf is refused, not silently answered."""
    workspace, fs = _workspace(tmp_path, monkeypatch)
    logical_path, _canary = _link_shape(tmp_path, workspace, "leaf")

    with pytest.raises(NamespaceBoundaryError) as captured:
        fs.read_text(logical_path)

    assert captured.value.code == "guarded_snapshot_symlink_rejected"
    assert "SYNTHETIC_ORIGINAL" not in str(captured.value)


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_a_refused_operation_leaves_no_physical_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A refusal must not create a directory, a file or a stage entry."""
    workspace, fs = _workspace(tmp_path, monkeypatch)
    logical_path, _canary = _link_shape(tmp_path, workspace, "parent")
    before = _tree(workspace / ".polaris" / "runtime")

    with pytest.raises(NamespaceBoundaryError):
        fs.write_text(logical_path, "CHANGED")

    assert _tree(workspace / ".polaris" / "runtime") == before


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_a_refused_write_does_not_create_the_missing_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A refusal must not provision the namespace on its way out."""
    workspace, fs = _workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    canary = outside / "target.txt"
    canary.write_text("SYNTHETIC_ORIGINAL", encoding="utf-8")
    runtime = workspace / ".polaris" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "jump").symlink_to(outside)

    with pytest.raises(NamespaceBoundaryError):
        fs.write_text("runtime/jump/new/deep.txt", "CHANGED")

    assert not (outside / "new").exists()
    assert canary.read_text(encoding="utf-8") == "SYNTHETIC_ORIGINAL"


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_a_hard_linked_leaf_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A multiply-linked leaf would mutate every alias, so it is refused."""
    workspace, fs = _workspace(tmp_path, monkeypatch)
    runtime = workspace / ".polaris" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "linked.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    os.link(target, alias)

    with pytest.raises(NamespaceBoundaryError) as captured:
        fs.write_text("runtime/linked.txt", "CHANGED")

    assert captured.value.code == "guarded_snapshot_hard_link_rejected"
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    assert alias.read_text(encoding="utf-8") == "ORIGINAL"


# ---------------------------------------------------------------------------
# The effects stay adapter-owned
# ---------------------------------------------------------------------------


class _DenyingNamespaceAdapter:
    """Adapter that denies every managed namespace operation."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
        self.calls.append("read")
        raise PermissionError("synthetic adapter namespace read denial")

    def write_managed_namespace_bytes(self, binding: NamespaceBindingV1, content: bytes) -> int:
        self.calls.append("write")
        raise PermissionError("synthetic adapter namespace write denial")

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        self.calls.append("atomic")
        raise PermissionError("synthetic adapter namespace atomic denial")

    def append_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        durability: DurabilityMode = "buffered",
    ) -> int:
        self.calls.append("append")
        raise PermissionError("synthetic adapter namespace append denial")

    def remove_managed_namespace(self, binding: NamespaceBindingV1, *, missing_ok: bool = True) -> bool:
        self.calls.append("remove")
        raise PermissionError("synthetic adapter namespace remove denial")

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        raise AssertionError("the legacy writer must not be reached")

    def write_bytes(self, path: str, content: bytes) -> int:
        raise AssertionError("the legacy writer must not be reached")

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        raise AssertionError("the legacy writer must not be reached")

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        raise AssertionError("the legacy remover must not be reached")

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        raise AssertionError("the legacy reader must not be reached")

    def read_bytes(self, path: str) -> bytes:
        raise AssertionError("the legacy reader must not be reached")

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        raise AssertionError("the legacy writer must not be reached")

    def exists(self, path: str) -> bool:
        return False

    def is_file(self, path: str) -> bool:
        return False

    def is_dir(self, path: str) -> bool:
        return False


def test_a_custom_deny_policy_is_preserved_and_never_bypassed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A denying adapter stays in charge of every logical effect."""
    workspace, _ = _workspace(tmp_path, monkeypatch)
    adapter = _DenyingNamespaceAdapter()
    fs = KernelFileSystem(str(workspace), adapter)  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="synthetic adapter namespace write denial"):
        fs.write_text("runtime/probe/file.txt", "payload")
    with pytest.raises(PermissionError, match="synthetic adapter namespace read denial"):
        fs.read_text("runtime/probe/file.txt")
    with pytest.raises(PermissionError, match="synthetic adapter namespace append denial"):
        fs.append_text("runtime/probe/file.txt", "payload")
    with pytest.raises(PermissionError, match="synthetic adapter namespace atomic denial"):
        fs.write_text_atomic("runtime/probe/file.txt", "payload")
    with pytest.raises(PermissionError, match="synthetic adapter namespace remove denial"):
        fs.remove("runtime/probe/file.txt")

    assert adapter.calls == ["write", "read", "append", "atomic", "remove"]
    assert _tree(workspace / ".polaris" / "runtime") == []


def test_an_adapter_without_the_capability_is_refused_before_any_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An adapter that cannot honour the capability never causes a write."""

    class _NoCapabilityAdapter:
        def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
            raise AssertionError("the legacy writer must not be reached")

        def write_bytes(self, path: str, content: bytes) -> int:
            raise AssertionError("the legacy writer must not be reached")

        def append_text(
            self,
            path: str,
            content: str,
            *,
            encoding: str = "utf-8",
            durability: DurabilityMode = "buffered",
        ) -> int:
            raise AssertionError("the legacy writer must not be reached")

        def remove(self, path: str, *, missing_ok: bool = True) -> bool:
            raise AssertionError("the legacy remover must not be reached")

        def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
            raise AssertionError("the legacy reader must not be reached")

        def read_bytes(self, path: str) -> bytes:
            raise AssertionError("the legacy reader must not be reached")

        def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
            raise AssertionError("the legacy writer must not be reached")

        def exists(self, path: str) -> bool:
            return False

        def is_file(self, path: str) -> bool:
            return False

        def is_dir(self, path: str) -> bool:
            return False

    workspace, _ = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), _NoCapabilityAdapter())  # type: ignore[arg-type]

    with pytest.raises(NamespaceOperationUnavailableError):
        fs.write_text("runtime/probe/file.txt", "payload")

    assert _tree(workspace / ".polaris" / "runtime") == []


def test_a_policy_subclass_must_opt_into_the_managed_capability() -> None:
    """A subclass that overrides the legacy writer keeps its own refusal.

    The inherited managed capability would otherwise defeat the subclass's
    own ``PermissionError`` policy, which is the regression the managed
    atomic write guard already prevents for that capability.
    """

    class _DenyingSubclass(LocalFileSystemAdapter):
        def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
            raise PermissionError("subclass policy denial")

    adapter = _DenyingSubclass()
    with pytest.raises(NamespaceOperationUnavailableError):
        adapter.write_managed_namespace_bytes(
            NamespaceBindingV1(namespace_root="/tmp", relative_path="a.txt", logical_prefix="runtime"),
            b"payload",
        )


def test_an_explicitly_opted_in_subclass_keeps_its_legacy_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An opted-in subclass may bind its managed capability to its own policy."""
    workspace, _ = _workspace(tmp_path, monkeypatch)

    class _OptedInSubclass(LocalFileSystemAdapter):
        def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
            raise PermissionError("subclass policy denial")

        def write_managed_namespace_bytes(self, binding: NamespaceBindingV1, content: bytes) -> int:
            raise PermissionError("subclass policy denial")

    fs = KernelFileSystem(str(workspace), _OptedInSubclass())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="subclass policy denial"):
        fs.write_text("runtime/probe/file.txt", "payload")

    assert _tree(workspace / ".polaris" / "runtime") == []


# ---------------------------------------------------------------------------
# Legitimate operations still advance
# ---------------------------------------------------------------------------


def test_ordinary_operations_still_advance_with_truthful_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A legal write, read, append, atomic write and remove still work."""
    workspace, fs = _workspace(tmp_path, monkeypatch)

    receipt = fs.write_text("runtime/probe/file.txt", "第一段\n")
    assert receipt.logical_path == "runtime/probe/file.txt"
    assert receipt.bytes_written > 0
    assert receipt.atomic is False
    assert Path(receipt.absolute_path).is_file()
    assert Path(receipt.absolute_path) == workspace / ".polaris" / "runtime" / "probe" / "file.txt"
    assert fs.read_text("runtime/probe/file.txt") == "第一段\n"
    assert fs.read_bytes("runtime/probe/file.txt") == "第一段\n".encode()

    appended = fs.append_text("runtime/probe/file.txt", "第二段\n")
    assert appended.bytes_written > 0
    assert fs.read_text("runtime/probe/file.txt") == "第一段\n第二段\n"

    atomic = fs.write_text_atomic("runtime/probe/file.txt", "第三段\n")
    assert atomic.atomic is True
    assert fs.read_text("runtime/probe/file.txt") == "第三段\n"

    json_receipt = fs.write_json_atomic("runtime/probe/data.json", {"键": "值"})
    assert json_receipt.atomic is True
    assert fs.read_json("runtime/probe/data.json") == {"键": "值"}

    assert fs.remove("runtime/probe/data.json") is True
    assert fs.remove("runtime/probe/data.json") is True


def test_the_workspace_namespace_is_bound_to_its_own_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The ``workspace/*`` namespace writes below the project metadata root."""
    workspace, fs = _workspace(tmp_path, monkeypatch)

    receipt = fs.write_text("workspace/meta/kfs/data.txt", "payload\n")

    assert Path(receipt.absolute_path) == workspace / ".polaris" / "meta" / "kfs" / "data.txt"
    assert Path(receipt.absolute_path).read_text(encoding="utf-8") == "payload\n"
    assert fs.read_text("workspace/meta/kfs/data.txt") == "payload\n"


def test_the_global_config_namespace_is_a_legitimate_positive_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The global config namespace is a real namespace and must keep working."""
    config_home = tmp_path / "kernel-home"
    (config_home / "config").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_HOME", str(config_home))
    clear_storage_roots_cache()
    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    clear_storage_roots_cache()

    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())
    receipt = fs.write_text("config/settings.json", '{"模式": "合法"}\n')

    assert Path(receipt.absolute_path) == config_home / "config" / "settings.json"
    assert fs.read_text("config/settings.json") == '{"模式": "合法"}\n'
    assert json.loads((config_home / "config" / "settings.json").read_text(encoding="utf-8")) == {"模式": "合法"}


def test_a_fresh_path_is_created_inside_the_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A write to a new nested path creates it inside the namespace."""
    workspace, fs = _workspace(tmp_path, monkeypatch)

    fs.write_text("runtime/fresh/deep/nested.txt", "payload\n")

    assert (workspace / ".polaris" / "runtime" / "fresh" / "deep" / "nested.txt").read_text(
        encoding="utf-8"
    ) == "payload\n"


# ---------------------------------------------------------------------------
# Unknown effect and no-replay
# ---------------------------------------------------------------------------


def test_an_unknown_effect_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A post-effect failure stays unknown and is never replayed."""

    class _CommitThenFailAdapter(LocalFileSystemAdapter):
        def __init__(self) -> None:
            self.write_calls = 0

        def write_managed_namespace_bytes(self, binding: NamespaceBindingV1, content: bytes) -> int:
            self.write_calls += 1
            LocalFileSystemAdapter.write_managed_namespace_bytes(self, binding, content)
            raise OSError("synthetic failure after the effect landed")

    workspace, _ = _workspace(tmp_path, monkeypatch)
    adapter = _CommitThenFailAdapter()
    fs = KernelFileSystem(str(workspace), adapter)  # type: ignore[arg-type]

    with pytest.raises(OSError, match="synthetic failure after the effect landed"):
        fs.write_text("runtime/probe/file.txt", "payload")

    assert adapter.write_calls == 1, "a failed write must never be replayed"


def test_the_unknown_effect_type_carries_the_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The unknown-effect error names the binding it could not confirm."""
    binding = NamespaceBindingV1(namespace_root="/tmp", relative_path="a.txt", logical_prefix="runtime")
    error = NamespaceOperationEffectUnknownError(binding, "post_commit_probe")

    assert error.original_effect == "unknown"
    assert error.phase == "post_commit_probe"
    assert error.binding is binding


# ---------------------------------------------------------------------------
# Concurrency and restart
# ---------------------------------------------------------------------------


def test_concurrent_writers_on_distinct_leaves_both_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two concurrent atomic writes on distinct leaves both land."""
    from concurrent.futures import ThreadPoolExecutor

    workspace, fs = _workspace(tmp_path, monkeypatch)

    def write(index: int) -> int:
        return fs.write_text_atomic(f"runtime/concurrent/{index}.txt", f"payload-{index}\n").bytes_written

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, (0, 1)))

    assert all(size > 0 for size in results)
    for index in (0, 1):
        assert (workspace / ".polaris" / "runtime" / "concurrent" / f"{index}.txt").read_text(
            encoding="utf-8"
        ) == f"payload-{index}\n"


def test_concurrent_writers_on_one_leaf_do_not_corrupt_the_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Competing writes may invalidate observations; they must preserve that fact.

    write_text is non-atomic. Another writer may truncate the leaf between
    this writer's syscall and its post-check, so requiring all four calls to
    confirm the current state was a stronger, unsupported test contract.
    Keep UNKNOWN visible and verify both final bytes and legal progression.
    """
    from concurrent.futures import ThreadPoolExecutor

    workspace, fs = _workspace(tmp_path, monkeypatch)

    def write(index: int) -> str:
        try:
            receipt = fs.write_text("runtime/concurrent/shared.txt", f"payload-{index}\n" * 200)
        except NamespaceOperationEffectUnknownError:
            return "unknown"
        assert receipt.bytes_written == 2000
        return "completed"

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(write, range(4)))
    assert "completed" in outcomes
    assert all(outcome in {"completed", "unknown"} for outcome in outcomes)

    content = (workspace / ".polaris" / "runtime" / "concurrent" / "shared.txt").read_text(encoding="utf-8")
    assert content.startswith("payload-")
    assert content.endswith("\n")
    assert len(content) % len("payload-0\n") == 0
    assert content in {f"payload-{index}\n" * 200 for index in range(4)}


def test_a_cold_reader_sees_the_committed_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A restarted process observes exactly what the write committed."""
    import subprocess
    import sys

    workspace, fs = _workspace(tmp_path, monkeypatch)
    fs.write_text_atomic("runtime/probe/cold.txt", "冷读内容\n")
    target = workspace / ".polaris" / "runtime" / "probe" / "cold.txt"

    completed = subprocess.run(
        [sys.executable, "-c", "import sys;sys.stdout.write(open(sys.argv[1],encoding='utf-8').read())", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert completed.stdout == "冷读内容\n"
