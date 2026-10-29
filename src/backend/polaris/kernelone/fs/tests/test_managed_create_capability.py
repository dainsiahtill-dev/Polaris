"""Generic create-only workspace file capability tests.

``KernelFileSystem.workspace_create_text`` is the generic producer for a
file that must be created only when it does not exist yet.  It exists because
a consumer that calls a guarded primitive *directly* bypasses the active
adapter's policy: an ordinary KFS write can be denied while the direct
primitive still creates the file.

These tests pin:

* a real denying adapter refuses the create before any effect;
* a legitimate fresh create succeeds and is reported truthfully;
* existing content is preserved and never overwritten;
* two creators race safely: exactly one creates, the other preserves;
* an uncertain effect after a possible creation is reported honestly and is
  never replayed.
"""

from __future__ import annotations

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
from polaris.kernelone.fs.types import NamespaceBindingV1, NamespaceCreateReceiptV1
from polaris.kernelone.storage import clear_storage_roots_cache


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated workspace with its own canonical runtime root."""
    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    clear_storage_roots_cache()
    return workspace


def _tree(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(item.relative_to(root)) for item in root.rglob("*"))


class _DenyingCreateAdapter(LocalFileSystemAdapter):
    """Adapter whose policy denies every managed write."""

    def __init__(self) -> None:
        self.legacy_write_calls: list[str] = []

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        self.legacy_write_calls.append(path)
        raise PermissionError("synthetic adapter write policy denial")

    def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> int:
        self.legacy_write_calls.append(path)
        raise PermissionError("synthetic adapter write policy denial")


class _NoCreateCapabilityAdapter:
    """Adapter that does not implement the managed create capability."""

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


# ---------------------------------------------------------------------------
# Refusal precedes every compound creation effect
# ---------------------------------------------------------------------------


def test_a_denying_adapter_refuses_the_create_before_any_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A policy denial must not create the file, the directory or a stage."""
    workspace = _workspace(tmp_path, monkeypatch)
    adapter = _DenyingCreateAdapter()
    fs = KernelFileSystem(str(workspace), adapter)  # type: ignore[arg-type]

    with pytest.raises(NamespaceOperationUnavailableError):
        fs.workspace_create_text("skills/demo.md", "payload")

    assert adapter.legacy_write_calls == [], "the legacy writer must not run as an authorization probe"
    assert _tree(workspace) == [".polaris", ".polaris/runtime"], "the denial must leave no physical trace"


def test_an_adapter_without_the_capability_is_refused_before_any_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing capability is refused before the namespace is provisioned."""
    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), _NoCreateCapabilityAdapter())  # type: ignore[arg-type]

    with pytest.raises(NamespaceOperationUnavailableError):
        fs.workspace_create_text("skills/demo.md", "payload")

    assert not (workspace / "skills").exists()


def test_a_denying_adapter_must_opt_into_the_managed_create_explicitly() -> None:
    """A subclass that overrides the legacy writer keeps its own refusal."""

    class _OptedOutSubclass(LocalFileSystemAdapter):
        def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
            raise PermissionError("subclass policy denial")

    with pytest.raises(NamespaceOperationUnavailableError):
        _OptedOutSubclass().create_managed_namespace_bytes(
            NamespaceBindingV1(namespace_root="/tmp", relative_path="a.txt", logical_prefix="workspace"),
            b"payload",
            max_bytes=1024,
        )


# ---------------------------------------------------------------------------
# Legitimate creation
# ---------------------------------------------------------------------------


def test_a_fresh_create_succeeds_and_is_reported_truthfully(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A legitimate fresh create reports that it created the file."""
    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    receipt = fs.workspace_create_text("skills/demo.md", "内容\n")

    assert isinstance(receipt, NamespaceCreateReceiptV1)
    assert receipt.created is True
    assert receipt.bytes_written == len("内容\n".encode())
    assert (workspace / "skills" / "demo.md").read_text(encoding="utf-8") == "内容\n"


def test_an_existing_file_is_preserved_and_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An existing file keeps its content and the receipt says so."""
    workspace = _workspace(tmp_path, monkeypatch)
    target = workspace / "skills" / "demo.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("USER-AUTHORED\n", encoding="utf-8")
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    receipt = fs.workspace_create_text("skills/demo.md", "installer-content\n")

    assert receipt.created is False
    assert receipt.bytes_written == 0
    assert target.read_text(encoding="utf-8") == "USER-AUTHORED\n"


def test_a_repeated_create_creates_once_and_preserves_afterwards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only the first call creates; later calls preserve the content."""
    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    first = fs.workspace_create_text("skills/demo.md", "first\n")
    second = fs.workspace_create_text("skills/demo.md", "second\n")

    assert first.created is True
    assert second.created is False
    assert (workspace / "skills" / "demo.md").read_text(encoding="utf-8") == "first\n"


def test_the_create_refuses_a_non_utf8_encoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The create keeps the platform's UTF-8-only text contract."""
    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    with pytest.raises(ValueError):
        fs.workspace_create_text("skills/demo.md", "x", encoding="latin-1")

    assert not (workspace / "skills").exists()


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_a_linked_leaf_is_preserved_and_never_followed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A linked leaf cannot be used to write outside the workspace.

    The create-only contract preserves an existing leaf, and a symbolic link
    *is* an existing leaf: it is neither followed nor replaced.  The link
    entry stays in place and its target is untouched.
    """
    workspace = _workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    canary = outside / "canary.txt"
    canary.write_text("SYNTHETIC_ORIGINAL", encoding="utf-8")
    (workspace / "skills").mkdir(parents=True, exist_ok=True)
    link = workspace / "skills" / "demo.md"
    os.symlink(canary, link)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    receipt = fs.workspace_create_text("skills/demo.md", "payload")

    assert receipt.created is False
    assert link.is_symlink(), "the link entry is preserved and not replaced"
    assert canary.read_text(encoding="utf-8") == "SYNTHETIC_ORIGINAL"


@pytest.mark.skipif(os.name == "nt", reason="symbolic links require POSIX semantics")
def test_a_linked_parent_is_refused_instead_of_reaching_outside(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A linked parent directory cannot be used to write outside the workspace."""
    workspace = _workspace(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    canary = outside / "canary.txt"
    canary.write_text("SYNTHETIC_ORIGINAL", encoding="utf-8")
    os.symlink(outside, workspace / "skills")
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    with pytest.raises(NamespaceBoundaryError):
        fs.workspace_create_text("skills/demo.md", "payload")

    assert canary.read_text(encoding="utf-8") == "SYNTHETIC_ORIGINAL"


# ---------------------------------------------------------------------------
# Two creators
# ---------------------------------------------------------------------------


def test_two_creators_produce_exactly_one_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent creators never both claim the creation."""
    from concurrent.futures import ThreadPoolExecutor

    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    def create(index: int) -> bool:
        return fs.workspace_create_text("skills/demo.md", f"creator-{index}\n").created

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, (0, 1)))

    assert sorted(outcomes) == [False, True], "exactly one creator may create the file"
    content = (workspace / "skills" / "demo.md").read_text(encoding="utf-8")
    assert content in {"creator-0\n", "creator-1\n"}


def test_two_creators_on_distinct_leaves_both_create(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent creators of distinct files do not contend."""
    from concurrent.futures import ThreadPoolExecutor

    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())

    def create(index: int) -> bool:
        return fs.workspace_create_text(f"skills/demo-{index}.md", f"payload-{index}\n").created

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, (0, 1)))

    assert outcomes == [True, True]
    for index in (0, 1):
        assert (workspace / "skills" / f"demo-{index}.md").read_text(encoding="utf-8") == f"payload-{index}\n"


# ---------------------------------------------------------------------------
# Uncertain effect
# ---------------------------------------------------------------------------


def test_an_uncertain_effect_is_reported_honestly_and_never_replayed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A post-creation failure stays unknown and is not retried."""

    class _CommitThenFailAdapter(LocalFileSystemAdapter):
        def __init__(self) -> None:
            self.create_calls = 0

        def create_managed_namespace_bytes(
            self,
            binding: NamespaceBindingV1,
            content: bytes,
            *,
            max_bytes: int,
        ) -> NamespaceCreateReceiptV1:
            self.create_calls += 1
            LocalFileSystemAdapter.create_managed_namespace_bytes(self, binding, content, max_bytes=max_bytes)
            raise OSError("synthetic failure after the creation landed")

    workspace = _workspace(tmp_path, monkeypatch)
    adapter = _CommitThenFailAdapter()
    fs = KernelFileSystem(str(workspace), adapter)  # type: ignore[arg-type]

    with pytest.raises(OSError, match="synthetic failure after the creation landed"):
        fs.workspace_create_text("skills/demo.md", "payload")

    assert adapter.create_calls == 1, "an uncertain create must never be replayed"


def test_the_unknown_effect_type_carries_the_binding() -> None:
    """The unknown-effect error names the binding it could not confirm."""
    binding = NamespaceBindingV1(namespace_root="/tmp", relative_path="a.txt", logical_prefix="workspace")
    error = NamespaceOperationEffectUnknownError(binding, "create_commit")

    assert error.original_effect == "unknown"
    assert error.phase == "create_commit"
    assert error.binding is binding


def test_a_cold_reader_sees_the_created_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A restarted process observes exactly what the create committed."""
    import subprocess
    import sys

    workspace = _workspace(tmp_path, monkeypatch)
    fs = KernelFileSystem(str(workspace), LocalFileSystemAdapter())
    fs.workspace_create_text("skills/demo.md", "冷读内容\n")
    target = workspace / "skills" / "demo.md"

    completed = subprocess.run(
        [sys.executable, "-c", "import sys;sys.stdout.write(open(sys.argv[1],encoding='utf-8').read())", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert completed.stdout == "冷读内容\n"
