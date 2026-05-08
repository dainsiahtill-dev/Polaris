"""Tests for file_apply_service module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    pass


class TestFileApplyService:
    """Tests for FileApplyService class."""

    def test_service_initialization(self) -> None:
        """Test FileApplyService initialization."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(
            workspace="/tmp/workspace",
            worker_id="worker-1",
        )
        assert service.workspace == "/tmp/workspace"
        assert service._worker_id == "worker-1"
        assert service._bus is None

    def test_service_with_message_bus(self) -> None:
        """Test FileApplyService with message bus."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        mock_bus = MagicMock()
        service = FileApplyService(
            workspace="/tmp/workspace",
            message_bus=mock_bus,
        )
        assert service._bus is mock_bus

    def test_write_files_empty_list(self) -> None:
        """Test write_files with empty list."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.write_files([])
        assert result == []

    def test_write_files_skips_empty_entries(self) -> None:
        """Test write_files skips entries without path or content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.write_files(
            [
                {"path": "", "content": "test"},  # Empty path
                {"path": "test.py", "content": ""},  # Empty content
            ]
        )
        assert result == []

    def test_collect_workspace_files_empty_list(self) -> None:
        """Test collect_workspace_files with empty list."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.collect_workspace_files([])
        assert result == []

    def test_collect_workspace_files_nonexistent(self) -> None:
        """Test collect_workspace_files with non-existent files."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        result = service.collect_workspace_files(["nonexistent.py"])
        assert len(result) == 1
        assert result[0]["path"] == "nonexistent.py"
        assert result[0]["content"] == ""
        assert result[0].get("deleted") is True

    def test_collect_workspace_files_existing(self, tmp_path: Path) -> None:
        """Test collect_workspace_files with existing files."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        # Create test file
        test_file = tmp_path / "existing.py"
        test_file.write_text("# existing file\n", encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        result = service.collect_workspace_files(["existing.py"])

        assert len(result) == 1
        assert result[0]["path"] == "existing.py"
        assert result[0]["content"] == "# existing file\n"
        assert result[0].get("deleted") is not True

    def test_collect_workspace_files_deduplication(self, tmp_path: Path) -> None:
        """Test collect_workspace_files deduplicates paths."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        test_file = tmp_path / "file.py"
        test_file.write_text("content", encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        result = service.collect_workspace_files(["file.py", "file.py", "file.py"])

        # Should only return one result
        assert len(result) == 1

    def test_collect_workspace_files_unicode_content(self, tmp_path: Path) -> None:
        """Test collect_workspace_files handles unicode content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        test_file = tmp_path / "unicode.py"
        test_file.write_text("# Unicode: \u4e2d\u6587\n# Emoji: \U0001f600\n", encoding="utf-8")

        service = FileApplyService(workspace=str(tmp_path))
        result = service.collect_workspace_files(["unicode.py"])

        assert result[0]["content"].startswith("# Unicode")

    def test_calculate_diff_stats(self) -> None:
        """Test calculate_diff_stats method."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        stats = service.calculate_diff_stats(
            old_content="line1\nline2\n",
            new_content="line1\nnew line\nline2\n",
        )

        assert "old_size" in stats
        assert "new_size" in stats
        assert "patch_size" in stats
        assert "patch" in stats
        assert stats["old_size"] == len("line1\nline2\n")

    def test_calculate_diff_stats_identical(self) -> None:
        """Test calculate_diff_stats with identical content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        content = "same content\n"
        service = FileApplyService(workspace="/tmp")
        stats = service.calculate_diff_stats(
            old_content=content,
            new_content=content,
        )

        assert stats["old_size"] == stats["new_size"]

    def test_calculate_diff_stats_empty_to_content(self) -> None:
        """Test calculate_diff_stats from empty to content."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        stats = service.calculate_diff_stats(
            old_content="",
            new_content="new content\n",
        )

        assert stats["old_size"] == 0
        assert stats["new_size"] == len("new content\n")

    def test_apply_response_operations_no_response(self) -> None:
        """Test apply_response_operations with empty response."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace="/tmp")
        applied, errors = service.apply_response_operations("")
        assert applied == []
        assert "no_changes" in errors

    def test_apply_response_operations_accepts_fenced_file_block(self, tmp_path: Path) -> None:
        """Regression: Director proposal bridge emits ```file: path fences."""
        from polaris.cells.director.tasking.internal.file_apply_service import FileApplyService

        service = FileApplyService(workspace=str(tmp_path))
        applied, errors = service.apply_response_operations(
            "```file: src/health.ts\n"
            "export function health(): string {\n"
            "  return 'ok';\n"
            "}\n"
            "```"
        )

        assert errors == []
        assert applied == [
            {
                "path": "src/health.ts",
                "content": "export function health(): string {\n  return 'ok';\n}\n",
            }
        ]
        assert (tmp_path / "src" / "health.ts").read_text(encoding="utf-8").strip() == (
            "export function health(): string {\n  return 'ok';\n}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
