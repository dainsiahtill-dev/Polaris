"""Tests for file_apply_service module.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.execution.internal.file_apply_service import FileApplyService


class TestFileApplyService:
    """Tests for FileApplyService class."""

    def test_write_files_creates_files(self, tmp_path: Path):
        """Test that write_files creates files in workspace."""
        service = FileApplyService(workspace=str(tmp_path))

        files = [
            {"path": "test.py", "content": "print('hello')\n"},
            {"path": "readme.txt", "content": "Test README\n"},
        ]

        result = service.write_files(files)

        assert len(result) == 2
        assert (tmp_path / "test.py").exists()
        assert (tmp_path / "readme.txt").exists()
        assert (tmp_path / "test.py").read_text(encoding="utf-8") == "print('hello')\n"

    def test_write_files_skips_empty_content(self, tmp_path: Path):
        """Test that write_files skips files with empty content."""
        service = FileApplyService(workspace=str(tmp_path))

        files = [
            {"path": "empty.py", "content": ""},
            {"path": "valid.txt", "content": "valid"},
        ]

        result = service.write_files(files)

        assert len(result) == 1
        assert result[0]["path"] == "valid.txt"

    def test_write_files_skips_empty_path(self, tmp_path: Path):
        """Test that write_files skips files with empty path."""
        service = FileApplyService(workspace=str(tmp_path))

        files = [
            {"path": "", "content": "content"},
            {"path": "valid.txt", "content": "content"},
        ]

        result = service.write_files(files)

        assert len(result) == 1

    def test_collect_workspace_files(self, tmp_path: Path):
        """Test that collect_workspace_files reads files from workspace."""
        service = FileApplyService(workspace=str(tmp_path))

        # Create test files
        (tmp_path / "file1.txt").write_text("content1", encoding="utf-8")
        (tmp_path / "file2.txt").write_text("content2", encoding="utf-8")

        result = service.collect_workspace_files(["file1.txt", "file2.txt"])

        assert len(result) == 2
        paths = [r["path"] for r in result]
        assert "file1.txt" in paths
        assert "file2.txt" in paths

    def test_collect_workspace_files_handles_missing_files(self, tmp_path: Path):
        """Test that collect_workspace_files handles missing files."""
        service = FileApplyService(workspace=str(tmp_path))

        result = service.collect_workspace_files(["missing.txt"])

        assert len(result) == 1
        assert result[0]["path"] == "missing.txt"
        assert result[0]["content"] == ""
        assert result[0].get("deleted") is True

    def test_collect_workspace_files_deduplicates(self, tmp_path: Path):
        """Test that collect_workspace_files deduplicates paths."""
        service = FileApplyService(workspace=str(tmp_path))

        (tmp_path / "test.txt").write_text("content", encoding="utf-8")

        result = service.collect_workspace_files(["test.txt", "test.txt"])

        assert len(result) == 1


class TestFileApplyServiceUTF8:
    """Tests for UTF-8 encoding in FileApplyService."""

    def test_write_files_with_chinese_content(self, tmp_path: Path):
        """Test writing files with Chinese characters."""
        service = FileApplyService(workspace=str(tmp_path))

        files = [{"path": "chinese.txt", "content": "你好世界\n"}]

        result = service.write_files(files)

        assert len(result) == 1
        content = (tmp_path / "chinese.txt").read_text(encoding="utf-8")
        assert content == "你好世界\n"

    def test_write_files_with_emoji(self, tmp_path: Path):
        """Test writing files with emoji characters."""
        service = FileApplyService(workspace=str(tmp_path))

        files = [{"path": "emoji.txt", "content": "Hello 🌍\n"}]

        result = service.write_files(files)

        assert len(result) == 1
        content = (tmp_path / "emoji.txt").read_text(encoding="utf-8")
        assert content == "Hello 🌍\n"

    def test_collect_workspace_files_preserves_utf8(self, tmp_path: Path):
        """Test that collect_workspace_files preserves UTF-8 encoding."""
        service = FileApplyService(workspace=str(tmp_path))

        # Write a file with UTF-8 content
        (tmp_path / "utf8.txt").write_text("UTF-8 测试 🎉", encoding="utf-8")

        result = service.collect_workspace_files(["utf8.txt"])

        assert len(result) == 1
        assert result[0]["content"] == "UTF-8 测试 🎉"


class TestFileApplyServiceDiffStats:
    """Tests for diff statistics calculation."""

    def test_calculate_diff_stats(self, tmp_path: Path):
        """Test diff statistics calculation."""
        service = FileApplyService(workspace=str(tmp_path))

        old_content = "line1\nline2\nline3\n"
        new_content = "line1\nline2 modified\nline3\nline4\n"

        stats = service.calculate_diff_stats(old_content, new_content)

        assert stats["old_size"] == len(old_content)
        assert stats["new_size"] == len(new_content)
        assert "patch" in stats
        assert stats["patch_size"] > 0

    def test_calculate_diff_stats_empty_old(self, tmp_path: Path):
        """Test diff with empty old content."""
        service = FileApplyService(workspace=str(tmp_path))

        stats = service.calculate_diff_stats("", "new content\n")

        assert stats["old_size"] == 0
        assert stats["new_size"] == 12  # len("new content\n")
        assert stats["patch_size"] > 0
