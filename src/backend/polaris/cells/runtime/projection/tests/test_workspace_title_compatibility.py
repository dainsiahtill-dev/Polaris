"""Projection's legacy title query keeps workspace owner semantics."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.runtime.projection.internal import file_io
from polaris.cells.runtime.projection.public.file_io_facade import read_readme_title as facade_title
from polaris.cells.workspace.integrity.public import service as workspace_public
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import registry as fs_registry


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, ""),
        (b"", ""),
        (b"\n \n", ""),
        ("\n \n### 标题  \nnext".encode(), "标题"),
        (b"first paragraph\n# Heading", "first paragraph"),
        (b"####\nnext", ""),
        (("# " + "长" * 140 + "\n").encode(), "长" * 120),
        ("\ufeff# BOM preserved\n".encode(), "\ufeff# BOM preserved"),
        (b"\r\n # CRLF \r\nnext", "CRLF"),
        (b"\xff", ""),
    ],
)
def test_projection_title_preexisting_goldens(tmp_path: Path, content: bytes | None, expected: str) -> None:
    if content is not None:
        (tmp_path / "tui_runtime.md").write_bytes(content)
    assert file_io.read_readme_title(str(tmp_path)) == expected
    assert facade_title(str(tmp_path)) == expected


def test_projection_title_is_same_workspace_query() -> None:
    assert file_io.read_readme_title is workspace_public.read_readme_title
    assert facade_title is workspace_public.read_readme_title


def test_empty_workspace_never_uses_cwd_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "tui_runtime.md").write_text("# cwd must not leak", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert file_io.read_readme_title("") == ""


@pytest.mark.parametrize(
    ("error", "empty"),
    [
        (PermissionError("denied"), True),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), True),
        (ValueError("scope invalid"), False),
        (RuntimeError("unavailable"), False),
        (TypeError("bad adapter"), False),
    ],
)
def test_projection_title_adapter_error_goldens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, empty: bool
) -> None:
    class DeniedAdapter(LocalFileSystemAdapter):
        def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
            raise error

    (tmp_path / "tui_runtime.md").write_text("# title", encoding="utf-8")
    monkeypatch.setattr(fs_registry, "_default_adapter", DeniedAdapter())
    if empty:
        assert file_io.read_readme_title(str(tmp_path)) == ""
    else:
        with pytest.raises(type(error), match=str(error)):
            file_io.read_readme_title(str(tmp_path))
