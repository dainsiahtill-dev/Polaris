from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.contracts import FileWriteReceipt, KernelFileSystemAdapter
from polaris.kernelone.fs.registry import get_default_adapter, set_default_adapter


class _TestFileSystemAdapter(KernelFileSystemAdapter):
    """Test adapter that implements KernelFileSystemAdapter protocol.

    All methods accept str paths per the contract and convert to Path internally.
    """

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

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

    def append_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:
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

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLARIS_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("POLARIS_RAMDISK_ROOT", raising=False)
    monkeypatch.delenv("POLARIS_RUNTIME_CACHE_ROOT", raising=False)
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


def test_workspace_scoped_path_rejects_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fs, _ = _build_kernel_fs(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        fs.workspace_read_text("../outside.txt")
