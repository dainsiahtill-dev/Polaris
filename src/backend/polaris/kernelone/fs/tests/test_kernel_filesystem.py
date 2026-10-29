from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.contracts import DurabilityMode, FileWriteReceipt, KernelFileSystemAdapter
from polaris.kernelone.fs.registry import get_default_adapter, set_default_adapter
from polaris.kernelone.fs.types import (
    AtomicWriteBindingV1,
    NamespaceBindingV1,
    NamespaceCreateReceiptV1,
)


class _TestFileSystemAdapter(KernelFileSystemAdapter):
    """Test adapter that implements KernelFileSystemAdapter protocol.

    All methods accept str paths per the contract and convert to Path internally.

    The managed namespace capabilities are implemented through the *real*
    descriptor-bound primitives, so the fixture exercises the same capability
    contract that the production adapter honours.
    """

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

    def write_managed_text_atomic(self, binding: AtomicWriteBindingV1, content: str, *, encoding: str = "utf-8") -> int:
        # This fixture explicitly opts into the actual native managed contract.
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().write_managed_text_atomic(binding, content, encoding=encoding)

    def read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().read_managed_namespace_bytes(binding)

    def write_managed_namespace_bytes(self, binding: NamespaceBindingV1, content: bytes) -> int:
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().write_managed_namespace_bytes(binding, content)

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().write_managed_namespace_bytes_atomic(binding, content)

    def append_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        durability: DurabilityMode = "buffered",
    ) -> int:
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().append_managed_namespace_bytes(binding, content, durability=durability)

    def remove_managed_namespace(self, binding: NamespaceBindingV1, *, missing_ok: bool = True) -> bool:
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().remove_managed_namespace(binding, missing_ok=missing_ok)

    def create_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        max_bytes: int,
    ) -> NamespaceCreateReceiptV1:
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().create_managed_namespace_bytes(binding, content, max_bytes=max_bytes)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = str(content)
        if atomic:
            import tempfile

            with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, delete=False, dir=p.parent) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            try:
                tmp_path.replace(p)
            except (RuntimeError, ValueError):
                tmp_path.unlink(missing_ok=True)
                raise
        else:
            p.write_text(content, encoding=encoding)
        return len(data.encode(encoding))

    def write_bytes(self, path: str, content: bytes) -> int:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return len(content)

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        del durability
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding=encoding) as handle:
            handle.write(content)
        return len(content.encode(encoding))

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        """Serialize data to JSON and write atomically."""
        p = Path(path)
        payload = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
        size = self.write_text(path, payload, encoding="utf-8", atomic=True)
        return FileWriteReceipt(logical_path=p.name, absolute_path=str(p), bytes_written=size)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        return Path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        try:
            Path(path).unlink()
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise


def _build_kernel_fs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[KernelFileSystem, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    runtime_root = workspace / ".polaris" / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    set_default_adapter(_TestFileSystemAdapter())

    return KernelFileSystem(str(workspace), get_default_adapter()), workspace


def test_write_and_read_text_roundtrip_utf8(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    receipt = fs.write_text("workspace/meta/kfs/hello.txt", "hello\nnihao\n")
    content = fs.read_text("workspace/meta/kfs/hello.txt")

    assert "hello" in content
    assert "nihao" in content
    assert receipt.bytes_written > 0
    assert Path(receipt.absolute_path).is_file()


def test_rejects_legacy_or_invalid_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    # "invalid_prefix" is not in _ALLOWED_PREFIXES ("runtime", "workspace", "config")
    with pytest.raises(ValueError):
        fs.write_text("invalid_prefix/some/path.txt", "data")


def test_append_evidence_record_writes_jsonl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    receipt = fs.append_evidence_record("audit_events", {"event": "descriptor_generated"})
    raw = Path(receipt.absolute_path).read_text(encoding="utf-8").strip()
    parsed = json.loads(raw)

    assert parsed["event"] == "descriptor_generated"
    assert "timestamp" in parsed


def test_append_log_line_writes_runtime_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    receipt = fs.append_log_line("worker", "line one")
    content = Path(receipt.absolute_path).read_text(encoding="utf-8")

    assert "line one" in content
    assert receipt.logical_path == "runtime/logs/worker.log"


def test_write_text_requires_utf8_encoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        fs.write_text("workspace/meta/kfs/latin1.txt", "x", encoding="latin-1")


def test_write_text_atomic_roundtrip_utf8(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    receipt = fs.write_text_atomic("workspace/meta/kfs/status.md", "状态\n")
    content = fs.read_text("workspace/meta/kfs/status.md")

    assert content == "状态\n"
    assert receipt.logical_path == "workspace/meta/kfs/status.md"
    assert Path(receipt.absolute_path).read_text(encoding="utf-8") == "状态\n"


def test_write_text_atomic_requires_utf8_encoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        fs.write_text_atomic("workspace/meta/kfs/status.md", "x", encoding="latin-1")


def test_workspace_scoped_text_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)

    receipt = fs.workspace_write_text("src/app.py", "print('ok')\n")
    loaded = fs.workspace_read_text("src/app.py")

    assert loaded == "print('ok')\n"
    assert receipt.logical_path == "src/app.py"
    assert Path(receipt.absolute_path) == (workspace / "src" / "app.py")


def test_workspace_scoped_text_atomic_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)

    receipt = fs.workspace_write_text_atomic("registry.json", "状态\n")
    loaded = fs.workspace_read_text("registry.json")

    assert loaded == "状态\n"
    assert receipt.logical_path == "registry.json"
    assert Path(receipt.absolute_path) == (workspace / "registry.json")


def test_workspace_scoped_text_atomic_requires_utf8(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        fs.workspace_write_text_atomic("registry.json", "x", encoding="latin-1")


def test_workspace_scoped_path_rejects_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        fs.workspace_read_text("../outside.txt")

    with pytest.raises(ValueError):
        fs.workspace_write_text_atomic("../outside.txt", "x")


@pytest.mark.skipif(sys.platform == "win32", reason="Symlink tests require Unix-like OS or admin privileges on Windows")
def test_resolve_workspace_path_rejects_symlink_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test that symlink traversal attacks are blocked.

    Attack scenario:
    1. Attacker creates symlink inside workspace: ln -s /etc/passwd evil
    2. Path 'evil' resolves to '/etc/passwd' (outside workspace)
    3. Without symlink check, _is_within_root(workspace, /etc/passwd) passes
    4. Result: Arbitrary file read vulnerability

    With the fix: Symlink is detected BEFORE resolve(), blocking the attack.
    """
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)

    # Create a target file OUTSIDE the workspace
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("SECRET DATA", encoding="utf-8")

    # Create a symlink INSIDE the workspace pointing to the outside file
    evil_symlink = workspace / "evil"
    try:
        evil_symlink.symlink_to(outside_file)
    except OSError:
        pytest.skip("Cannot create symlinks on this system")

    # Verify the symlink was created
    assert evil_symlink.is_symlink()
    assert evil_symlink.resolve() == outside_file.resolve()

    # Attempting to access via the symlink should raise PathSecurityError
    with pytest.raises(PathSecurityError, match="Symlink detected"):
        fs.resolve_workspace_path("evil")

    with pytest.raises(PathSecurityError, match="Symlink detected"):
        fs.workspace_read_text("evil")

    with pytest.raises(PathSecurityError, match="Symlink detected"):
        fs.workspace_exists("evil")

    with pytest.raises(PathSecurityError, match="Symlink detected"):
        fs.workspace_is_file("evil")


@pytest.mark.skipif(sys.platform == "win32", reason="Symlink tests require Unix-like OS or admin privileges on Windows")
def test_resolve_workspace_path_rejects_nested_symlink_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test that nested symlink traversal (symlink in subdirectory) is blocked."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)

    # Create a target file OUTSIDE the workspace
    outside_file = tmp_path / "secret_config.txt"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_text("API_KEY=secret123", encoding="utf-8")

    # Create subdirectory structure and symlink inside workspace
    subdir = workspace / "data"
    subdir.mkdir()
    evil_symlink = subdir / "leak"
    try:
        evil_symlink.symlink_to(outside_file)
    except OSError:
        pytest.skip("Cannot create symlinks on this system")

    # Attempting to access via nested symlink should raise PathSecurityError
    with pytest.raises(PathSecurityError, match="Symlink detected"):
        fs.resolve_workspace_path("data/leak")


@pytest.mark.skipif(sys.platform == "win32", reason="Symlink tests require Unix-like OS or admin privileges on Windows")
def test_resolve_absolute_workspace_path_rejects_symlink_in_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test that symlinks in absolute paths are also blocked."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)

    # Create a target file outside workspace
    outside_file = tmp_path / "outside" / "file.txt"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("SENSITIVE", encoding="utf-8")

    # Create a symlink inside workspace
    link_target = workspace / "link"
    try:
        link_target.symlink_to(outside_file.parent)
    except OSError:
        pytest.skip("Cannot create symlinks on this system")

    # The symlink is in workspace, but points outside
    # Access via absolute path should detect the symlink
    abs_link_path = str(link_target)
    with pytest.raises(PathSecurityError, match="Symlink detected"):
        fs.resolve_workspace_path(abs_link_path)


# ---------------------------------------------------------------------------
# The logical atomic write must not guess adapter behavior from caught errors.
#
# The former implementation caught any ``TypeError`` raised by the adapter and
# then wrote the file itself through a private temporary file.  That bypassed
# the adapter's own permission and error contract, and it replayed a write that
# the adapter had already committed, so a post-effect failure was reported as
# a success.  These tests pin the replacement contract.
#
# The atomic write is now performed by the adapter's *managed namespace*
# capability.  The refusal contract is unchanged in substance: an adapter
# that cannot honour the capability is refused before any effect, a denial
# stays visible and is never replayed, and the validated callable is the one
# that writes.
# ---------------------------------------------------------------------------


class _NoManagedNamespaceAdapter:
    """Adapter that does not implement the managed namespace capability."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:
        self.calls.append(path)
        raise AssertionError("adapter must not be called for a rejected atomic write")

    def write_bytes(self, path: str, content: bytes) -> int:
        raise AssertionError("adapter must not be called for a rejected atomic write")

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        raise AssertionError("adapter must not be called for a rejected atomic write")

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        raise AssertionError("adapter must not be called for a rejected atomic write")

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        return Path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        raise AssertionError("adapter must not be called for a rejected atomic write")


class _DenyingAtomicAdapter:
    """Adapter that denies the atomic write through its own policy."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        self.calls.append({"path": binding.physical_path(), "atomic": True})
        raise PermissionError("synthetic adapter write policy denial")

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        raise AssertionError("unexpected legacy write")

    def write_bytes(self, path: str, content: bytes) -> int:
        raise AssertionError("unexpected bytes write")

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        raise AssertionError("unexpected append")

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        raise AssertionError("unexpected json write")

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        return Path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        raise AssertionError("unexpected remove")


class _TypeErrorBeforeEffectAdapter(_DenyingAtomicAdapter):
    """Adapter that fails with ``TypeError`` before any physical effect."""

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        self.calls.append({"path": binding.physical_path(), "atomic": True})
        raise TypeError("synthetic adapter type error before effect")


class _TypeErrorAfterEffectAdapter(_DenyingAtomicAdapter):
    """Adapter that commits the atomic replacement and then raises ``TypeError``."""

    def __init__(self) -> None:
        super().__init__()
        self.sentinel = TypeError("synthetic adapter type error after effect")

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        self.calls.append({"path": binding.physical_path(), "atomic": True})
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        LocalFileSystemAdapter().write_managed_namespace_bytes_atomic(binding, content)
        raise self.sentinel


class _KwargsAtomicWrapperAdapter(_DenyingAtomicAdapter):
    """Compliant ``**kwargs`` wrapper that forwards the managed request."""

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes, **kwargs: Any) -> int:
        self.calls.append({"path": binding.physical_path(), "atomic": True})
        from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

        return LocalFileSystemAdapter().write_managed_namespace_bytes_atomic(binding, content)


def _workspace_tree(root: Path) -> list[str]:
    """Return the physical tree under ``root`` so side effects stay visible."""
    if not root.exists():
        return []
    return sorted(str(item.relative_to(root)) for item in root.rglob("*"))


def test_atomic_write_rejects_adapter_without_managed_capability_before_any_effect(
    tmp_path: Path,
) -> None:
    """A non-compliant adapter must not be replaced by an unmanaged write."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    adapter = _NoManagedNamespaceAdapter()
    fs = KernelFileSystem(str(workspace), adapter)  # type: ignore[arg-type]

    with pytest.raises(NotImplementedError):
        fs.write_text_atomic("runtime/probe/rejected.txt", "payload")

    assert adapter.calls == []
    assert _workspace_tree(workspace) == []


def test_atomic_json_write_rejects_adapter_without_managed_capability_before_any_effect(
    tmp_path: Path,
) -> None:
    """The JSON route must reject before it resolves a path or calls the adapter."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    adapter = _NoManagedNamespaceAdapter()
    fs = KernelFileSystem(str(workspace), adapter)  # type: ignore[arg-type]

    with pytest.raises(NotImplementedError):
        fs.write_json_atomic("runtime/probe/rejected.json", {"payload": True})

    assert adapter.calls == []
    assert _workspace_tree(workspace) == []


def test_atomic_write_propagates_adapter_policy_denial_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An adapter denial stays visible and is never retried through another writer."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)
    adapter = _DenyingAtomicAdapter()
    fs = KernelFileSystem(workspace, adapter)  # type: ignore[arg-type]

    with pytest.raises(PermissionError, match="synthetic adapter write policy denial"):
        fs.write_text_atomic("workspace/meta/kfs/status.md", "payload")

    assert len(adapter.calls) == 1
    assert adapter.calls[0]["atomic"] is True
    assert not Path(str(adapter.calls[0]["path"])).exists()


def test_atomic_write_propagates_type_error_before_effect_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A pre-effect ``TypeError`` is the adapter's failure, not a fallback signal."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)
    adapter = _TypeErrorBeforeEffectAdapter()
    fs = KernelFileSystem(workspace, adapter)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="synthetic adapter type error before effect"):
        fs.write_text_atomic("workspace/meta/kfs/status.md", "payload")

    assert len(adapter.calls) == 1
    assert not Path(str(adapter.calls[0]["path"])).exists()


def test_atomic_write_after_effect_type_error_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A post-effect failure keeps the original error and calls the adapter once."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)
    adapter = _TypeErrorAfterEffectAdapter()
    fs = KernelFileSystem(workspace, adapter)  # type: ignore[arg-type]

    with pytest.raises(TypeError) as captured:
        fs.write_text_atomic("workspace/meta/kfs/status.md", "payload")

    assert captured.value is adapter.sentinel
    assert len(adapter.calls) == 1
    committed = Path(str(adapter.calls[0]["path"]))
    assert committed.read_text(encoding="utf-8") == "payload"


def test_atomic_write_accepts_compliant_kwargs_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A ``**kwargs`` wrapper that honors ``atomic`` stays a legal adapter."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)
    adapter = _KwargsAtomicWrapperAdapter()
    fs = KernelFileSystem(workspace, adapter)  # type: ignore[arg-type]

    receipt = fs.write_text_atomic("workspace/meta/kfs/status.md", "状态\n")

    assert receipt.bytes_written > 0
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["atomic"] is True
    assert Path(receipt.absolute_path).read_text(encoding="utf-8") == "状态\n"


# ---------------------------------------------------------------------------
# The validated writer must be the writer that is actually used.
#
# The capability check resolves the adapter attribute and validates that
# callable, but the write used to resolve the attribute a second time.  An
# adapter whose capability attribute is dynamic could therefore return a
# different, incompatible callable for the write, so a write that passed the
# refusal check could still fail with the adapter's own ``TypeError`` after
# the runtime directories had already been created.  These tests pin that the
# validated callable is bound once and used exactly once.
# ---------------------------------------------------------------------------


class _SwitchingWriterAdapter:
    """Adapter whose managed capability attribute yields a different callable per lookup.

    The first lookup yields a compliant writer that performs a real atomic
    replacement.  Every later lookup yields a writer that cannot accept the
    managed call shape, so it raises ``TypeError`` when called.
    """

    def __init__(self) -> None:
        self.lookups = 0
        self.writes = 0
        self.writer_ids: list[int] = []

    @property
    def write_managed_namespace_bytes_atomic(self) -> Any:
        self.lookups += 1
        if self.lookups == 1:

            def compatible(binding: NamespaceBindingV1, content: bytes) -> int:
                self.writes += 1
                from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter

                return LocalFileSystemAdapter().write_managed_namespace_bytes_atomic(binding, content)

            self.writer_ids.append(id(compatible))
            return compatible

        def incompatible(binding: NamespaceBindingV1, content: bytes) -> int:
            self.writes += 1
            raise TypeError("incompatible() got an unexpected keyword argument 'atomic'")

        self.writer_ids.append(id(incompatible))
        return incompatible

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write_bytes(self, path: str, content: bytes) -> int:
        raise AssertionError("unexpected bytes write")

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        raise AssertionError("unexpected append")

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        raise AssertionError("unexpected json write")

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        return Path(path).is_file()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        raise AssertionError("unexpected remove")


def test_atomic_text_write_uses_the_validated_writer_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The validated callable is the callable that writes; it is never re-resolved."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)
    adapter = _SwitchingWriterAdapter()
    fs = KernelFileSystem(workspace, adapter)  # type: ignore[arg-type]

    receipt = fs.write_text_atomic("workspace/meta/kfs/status.md", "状态\n")

    assert adapter.lookups == 1
    assert adapter.writes == 1
    assert len(set(adapter.writer_ids)) == 1
    assert Path(receipt.absolute_path).read_text(encoding="utf-8") == "状态\n"


def test_atomic_json_write_uses_the_validated_writer_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The JSON route binds the same single validated callable as the text route."""
    fs, workspace = _build_kernel_fs(monkeypatch, tmp_path)
    adapter = _SwitchingWriterAdapter()
    fs = KernelFileSystem(workspace, adapter)  # type: ignore[arg-type]

    receipt = fs.write_json_atomic("workspace/meta/kfs/status.json", {"payload": True})

    assert adapter.lookups == 1
    assert adapter.writes == 1
    assert len(set(adapter.writer_ids)) == 1
    assert json.loads(Path(receipt.absolute_path).read_text(encoding="utf-8")) == {"payload": True}
