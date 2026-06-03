"""Tests for transactional file writes in filesystem handlers."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import (
    _should_use_whole_file_placeholder_replacement,
    _write_temp_verify_rename,
)


class TestWriteTempVerifyRename:
    """Test the _write_temp_verify_rename helper."""

    def test_atomic_write_success(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        content = "hello world"
        result = _write_temp_verify_rename(str(target), content, encoding="utf-8")

        assert result["ok"] is True
        assert target.exists()
        assert target.read_text(encoding="utf-8") == content

    def test_atomic_write_does_not_touch_original_on_verify_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "existing.txt"
        original_content = "original"
        target.write_text(original_content, encoding="utf-8")

        # Patch verify_written_code to always fail
        import polaris.kernelone.llm.toolkit.executor.handlers.filesystem as fs_module

        def fake_verify(_filepath: str, _expected: str):
            class FakeResult:
                success = False
                error = "injected verify failure"

            return FakeResult()

        monkeypatch.setattr(fs_module, "verify_written_code", fake_verify)

        result = _write_temp_verify_rename(str(target), "new content", encoding="utf-8")

        assert result["ok"] is False
        assert "injected verify failure" in result["error"]
        # Original file must remain untouched
        assert target.read_text(encoding="utf-8") == original_content
        # Temp file must be cleaned up
        temp_files = list(tmp_path.glob("*.tmp"))
        assert temp_files == []

    def test_atomic_write_overwrites_existing_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_text("old", encoding="utf-8")

        new_content = "brand new"
        result = _write_temp_verify_rename(str(target), new_content, encoding="utf-8")

        assert result["ok"] is True
        assert target.read_text(encoding="utf-8") == new_content

    def test_atomic_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "dir" / "file.txt"
        result = _write_temp_verify_rename(str(target), "data", encoding="utf-8")

        assert result["ok"] is True
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "data"

    def test_atomic_write_returns_bytes_written(self, tmp_path: Path) -> None:
        target = tmp_path / "count.txt"
        content = "abc"  # 3 bytes in utf-8
        result = _write_temp_verify_rename(str(target), content, encoding="utf-8")

        assert result["ok"] is True
        assert result["bytes_written"] == 3


class TestEditBlocksPlaceholderFallback:
    """Test controlled whole-file fallback for edit_blocks shorthand."""

    def test_allows_placeholder_search_with_complete_typescript_replacement(self) -> None:
        replacement = "\n".join(
            [
                "import http from 'http';",
                "",
                "export interface HealthResponse {",
                "  status: 'ok';",
                "}",
                "",
                "export function createServer(): http.Server {",
                "  return http.createServer((_req, res) => {",
                "    res.statusCode = 200;",
                "    res.end(JSON.stringify({ status: 'ok' }));",
                "  });",
                "}",
            ]
        )

        assert (
            _should_use_whole_file_placeholder_replacement(
                search_text="// TODO: implement app",
                replace_text=replacement,
                rel="src/server/app.ts",
                block_count=1,
            )
            is True
        )

    def test_rejects_non_placeholder_search_text(self) -> None:
        replacement = "\n".join(["export const value = 1;"] * 12)

        assert (
            _should_use_whole_file_placeholder_replacement(
                search_text="export const oldValue = 0;",
                replace_text=replacement,
                rel="src/server/app.ts",
                block_count=1,
            )
            is False
        )

    def test_rejects_multiblock_placeholder_fallback(self) -> None:
        replacement = "\n".join(["export const value = 1;"] * 12)

        assert (
            _should_use_whole_file_placeholder_replacement(
                search_text="// TODO: implement",
                replace_text=replacement,
                rel="src/server/app.ts",
                block_count=2,
            )
            is False
        )
