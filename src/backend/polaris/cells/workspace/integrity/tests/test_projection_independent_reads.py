"""Workspace host reads must not require runtime projection or bypass KFS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.workspace.integrity.internal import workspace_service
from polaris.cells.workspace.integrity.internal.fs_utils import workspace_status_path
from polaris.cells.workspace.integrity.public import service as workspace_public
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.errors import PathSecurityError
from polaris.kernelone.fs import registry as fs_registry


class RecordingAdapter(LocalFileSystemAdapter):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.reads: list[tuple[str, str]] = []

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        self.reads.append((path, encoding))
        if self.error is not None:
            raise self.error
        return super().read_text(path, encoding=encoding)

    def write_text(self, *args: Any, **kwargs: Any) -> int:
        raise AssertionError("workspace read attempted a write")


def test_workspace_readers_load_when_projection_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "tui_runtime.md").write_text("# 冷读标题\n", encoding="utf-8")
    script = """
import importlib.abc
import sys
class NoProjection(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('polaris.cells.runtime.projection'):
            raise ImportError('runtime.projection deliberately unavailable')
sys.meta_path.insert(0, NoProjection())
from polaris.cells.workspace.integrity.public.service import read_workspace_status, build_docs_templates
assert read_workspace_status(sys.argv[1]) is None
assert '冷读标题' in build_docs_templates(sys.argv[1], 'import_readme', {}, [])['docs/product/requirements.md']
assert not any(name.startswith('polaris.cells.runtime.projection') for name in sys.modules)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[5]), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_workspace_production_sources_have_no_projection_import() -> None:
    cell = Path(__file__).resolve().parents[1]
    references: list[str] = []
    for path in cell.rglob("*.py"):
        if "tests" in path.relative_to(cell).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("polaris.cells.runtime.projection"):
                references.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.Import):
                references.extend(
                    f"{path.name}:{node.lineno}"
                    for name in node.names
                    if name.name.startswith("polaris.cells.runtime.projection")
                )
    assert not references, references


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'{"status":"READY","reason":"ok"}', {"status": "READY", "reason": "ok"}),
        ('{"status":"失败","reason":"保留"}'.encode(), {"status": "失败", "reason": "保留"}),
        (b"[]", None),
        (b"null", None),
        (b'"text"', None),
        (b"{", None),
        (b"\xff", None),
    ],
)
def test_status_read_goldens_use_exact_resolved_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes, expected: dict[str, str] | None
) -> None:
    path = Path(workspace_status_path(str(tmp_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    adapter = RecordingAdapter()
    monkeypatch.setattr(fs_registry, "_default_adapter", adapter)
    assert workspace_service.read_workspace_status(str(tmp_path)) == expected
    assert adapter.reads == [(str(path), "utf-8")]


def test_missing_status_does_not_read_or_initialize_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = RecordingAdapter()
    monkeypatch.setattr(fs_registry, "_default_adapter", adapter)
    assert workspace_service.read_workspace_status("") is None
    assert workspace_service.read_workspace_status(str(tmp_path)) is None
    assert adapter.reads == []


@pytest.mark.parametrize("error", [PermissionError("denied"), ValueError("invalid"), RuntimeError("unavailable")])
def test_status_read_retains_recoverable_failure_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    path = Path(workspace_status_path(str(tmp_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"status":"READY"}', encoding="utf-8")
    adapter = RecordingAdapter(error)
    monkeypatch.setattr(fs_registry, "_default_adapter", adapter)
    assert workspace_service.read_workspace_status(str(tmp_path)) is None
    assert adapter.reads == [(str(path), "utf-8")]


def test_status_read_does_not_swallow_unexpected_adapter_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(workspace_status_path(str(tmp_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fs_registry, "_default_adapter", RecordingAdapter(TypeError("bad adapter")))
    with pytest.raises(TypeError, match="bad adapter"):
        workspace_service.read_workspace_status(str(tmp_path))


def test_status_symlink_scope_failure_remains_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"status":"FORGED"}', encoding="utf-8")
    path = Path(workspace_status_path(str(workspace)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    adapter = RecordingAdapter()
    monkeypatch.setattr(fs_registry, "_default_adapter", adapter)
    with pytest.raises(PathSecurityError):
        workspace_service.read_workspace_status(str(workspace))
    assert adapter.reads == []


@pytest.mark.parametrize("error", [PermissionError("denied"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")])
def test_title_read_retains_io_failure_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    (tmp_path / "tui_runtime.md").write_text("# title", encoding="utf-8")
    adapter = RecordingAdapter(error)
    monkeypatch.setattr(fs_registry, "_default_adapter", adapter)
    assert workspace_public.read_readme_title(str(tmp_path)) == ""
    assert adapter.reads == [(str(tmp_path / "tui_runtime.md"), "utf-8")]


@pytest.mark.parametrize("error", [ValueError("scope invalid"), RuntimeError("unavailable"), TypeError("bad adapter")])
def test_title_read_does_not_swallow_non_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    (tmp_path / "tui_runtime.md").write_text("# title", encoding="utf-8")
    monkeypatch.setattr(fs_registry, "_default_adapter", RecordingAdapter(error))
    with pytest.raises(type(error), match=str(error)):
        workspace_public.read_readme_title(str(tmp_path))


def test_title_symlink_scope_failure_remains_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# forbidden", encoding="utf-8")
    (workspace / "tui_runtime.md").symlink_to(outside)
    adapter = RecordingAdapter()
    monkeypatch.setattr(fs_registry, "_default_adapter", adapter)
    with pytest.raises(PathSecurityError):
        workspace_public.read_readme_title(str(workspace))
    assert adapter.reads == []
