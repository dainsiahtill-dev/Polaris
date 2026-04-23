from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.fs.contracts import KernelFileSystemAdapter
from polaris.kernelone.fs.registry import set_default_adapter
from polaris.kernelone.tool_execution.runtime_executor import BackendToolRuntime


class _TestFileSystemAdapter(KernelFileSystemAdapter):
    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return len(content.encode(encoding))

    def write_bytes(self, path: Path, content: bytes) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return len(content)

    def append_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding=encoding) as handle:
            handle.write(content)
        return len(content.encode(encoding))

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def remove(self, path: Path, *, missing_ok: bool = True) -> bool:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            if missing_ok:
                return False
            raise


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "nested").mkdir(parents=True, exist_ok=True)
    (workspace_root / "nested" / "hello.txt").write_text("hello\n", encoding="utf-8")

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    set_default_adapter(_TestFileSystemAdapter())
    from polaris.kernelone.tool_execution.tool_spec_registry import migrate_from_contracts_specs

    migrate_from_contracts_specs()
    return workspace_root


def test_backend_tool_runtime_exposes_kernelone_standard_tools(workspace: Path) -> None:
    runtime = BackendToolRuntime(str(workspace))

    tools = runtime.list_tools()

    assert "list_directory" in tools
    assert "read_file" in tools
    assert "write_file" in tools


def test_backend_tool_runtime_invokes_direct_executor_with_cwd(workspace: Path) -> None:
    runtime = BackendToolRuntime(str(workspace))

    result = runtime.invoke("list_directory", {"path": ".", "cwd": "nested"})

    assert result["ok"] is True
    payload = result["result"]
    assert payload["path"] == "."
    assert any(entry["name"] == "hello.txt" for entry in payload["entries"])
