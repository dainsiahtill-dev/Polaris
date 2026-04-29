# ruff: noqa: E402
"""Tests for polaris.kernelone.prompts.utils module."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.kernelone.prompts.utils import (
    FileBlockParseResult,
    apply_file_blocks,
    build_file_context,
    extract_between,
    parse_file_blocks,
    parse_file_blocks_with_state,
    parse_files_to_edit,
    strip_full_content_markers,
)


class TestExtractBetween:
    def test_happy_path(self) -> None:
        text = "<TAG>content</TAG>"
        result = extract_between(text, "<TAG>", "</TAG>")
        assert result == "content"

    def test_empty_text(self) -> None:
        assert extract_between("", "<a>", "</a>") == ""

    def test_missing_start_tag(self) -> None:
        text = "content</TAG>"
        assert extract_between(text, "<TAG>", "</TAG>") == ""

    def test_missing_end_tag(self) -> None:
        text = "<TAG>content"
        assert extract_between(text, "<TAG>", "</TAG>") == ""

    def test_reversed_tags(self) -> None:
        text = "</TAG>content<TAG>"
        assert extract_between(text, "<TAG>", "</TAG>") == ""

    def test_same_tag(self) -> None:
        text = "<TAG><TAG>"
        assert extract_between(text, "<TAG>", "<TAG>") == ""

    def test_multiline_content(self) -> None:
        text = "<START>line1\nline2\nline3</END>"
        result = extract_between(text, "<START>", "</END>")
        assert result == "line1\nline2\nline3"

    def test_whitespace_stripping(self) -> None:
        text = "<TAG>  content  </TAG>"
        result = extract_between(text, "<TAG>", "</TAG>")
        assert result == "content"


class TestParseFilesToEdit:
    def test_happy_path(self) -> None:
        text = "Files to edit\n- src/foo.py\n- src/bar.py"
        result = parse_files_to_edit(text)
        assert result == ["src/foo.py", "src/bar.py"]

    def test_empty_text(self) -> None:
        assert parse_files_to_edit("") == []

    def test_no_files_section(self) -> None:
        text = "Some random text\nwithout files section"
        assert parse_files_to_edit(text) == []

    def test_numbered_header(self) -> None:
        text = "1) Files to edit\n- file1.py\n2) Next section\n- other"
        result = parse_files_to_edit(text)
        assert result == ["file1.py"]

    def test_bracket_header(self) -> None:
        text = "1] files to edit\n- a.py\n- b.py"
        result = parse_files_to_edit(text)
        assert result == ["a.py", "b.py"]

    def test_backtick_removal(self) -> None:
        text = "files to edit\n- ``src/foo.py```"
        result = parse_files_to_edit(text)
        assert result == ["src/foo.py"]

    def test_deduplication(self) -> None:
        text = "files to edit\n- src/foo.py\n- src/foo.py\n- src/bar.py"
        result = parse_files_to_edit(text)
        assert result == ["src/foo.py", "src/bar.py"]

    def test_stops_at_double_hash(self) -> None:
        text = "files to edit\n- a.py\n## Next section\n- b.py"
        result = parse_files_to_edit(text)
        assert result == ["a.py"]

    def test_stops_at_ollama_begin(self) -> None:
        text = "files to edit\n- a.py\n[OLLAMA_BEGIN]\n- b.py"
        result = parse_files_to_edit(text)
        assert result == ["a.py"]

    def test_asterisk_bullets(self) -> None:
        text = "files to edit\n* file1.py\n* file2.py"
        result = parse_files_to_edit(text)
        assert result == ["file1.py", "file2.py"]

    def test_empty_bullet(self) -> None:
        text = "files to edit\n- \n- file.py"
        result = parse_files_to_edit(text)
        assert result == ["file.py"]

    def test_case_insensitive_header(self) -> None:
        text = "FILES TO EDIT\n- file.py"
        result = parse_files_to_edit(text)
        assert result == ["file.py"]


class TestBuildFileContext:
    @patch("polaris.kernelone.prompts.utils.read_file_safe")
    def test_happy_path(self, mock_read) -> None:
        mock_read.return_value = "line1\nline2"
        result = build_file_context(["src/foo.py"], "/workspace")
        expected = "FILE: src/foo.py\nline1\nline2\nEND FILE"
        assert result == expected
        mock_read.assert_called_once_with(os.path.join("/workspace", "src/foo.py"))

    @patch("polaris.kernelone.prompts.utils.read_file_safe")
    def test_empty_file(self, mock_read) -> None:
        mock_read.return_value = ""
        result = build_file_context(["src/foo.py"], "/workspace")
        expected = "FILE: src/foo.py\n<EMPTY OR MISSING>\nEND FILE"
        assert result == expected

    @patch("polaris.kernelone.prompts.utils.read_file_safe")
    def test_multiple_files(self, mock_read) -> None:
        mock_read.side_effect = ["content1", "content2"]
        result = build_file_context(["a.py", "b.py"], "/workspace")
        lines = result.split("\n")
        assert "FILE: a.py" in lines
        assert "content1" in lines
        assert "FILE: b.py" in lines
        assert "content2" in lines

    @patch("polaris.kernelone.prompts.utils.read_file_safe")
    def test_empty_files_list(self, mock_read) -> None:
        result = build_file_context([], "/workspace")
        assert result == ""
        mock_read.assert_not_called()


class TestParseFileBlocks:
    def test_empty_text(self) -> None:
        assert parse_file_blocks("") == []

    def test_no_changes(self) -> None:
        assert parse_file_blocks("NO_CHANGES") == []
        assert parse_file_blocks("  NO_CHANGES  ") == []

    def test_file_format(self) -> None:
        text = "FILE: src/foo.py\nline1\nline2\nEND FILE"
        result = parse_file_blocks(text)
        assert len(result) == 1
        assert result[0]["path"] == "src/foo.py"
        assert result[0]["content"] == "line1\nline2\n"

    def test_patch_file_format(self) -> None:
        text = "PATCH_FILE src/foo.py\n<SEARCH>\nold\n</SEARCH>\n<REPLACE>\nnew\n</REPLACE>"
        result = parse_file_blocks(text)
        assert len(result) == 1
        assert result[0]["path"] == "src/foo.py"
        assert "new" in result[0]["content"]

    def test_patch_file_colon_format(self) -> None:
        text = "PATCH_FILE: src/foo.py\n<REPLACE>\nnew content\n</REPLACE>"
        result = parse_file_blocks(text)
        assert len(result) == 1
        assert result[0]["path"] == "src/foo.py"

    def test_git_diff_format(self) -> None:
        text = "FILE: src/foo.py\n<<<<<<< SEARCH\nold line\n=======\nnew line\n>>>>>>> REPLACE\nEND FILE"
        result = parse_file_blocks(text)
        assert len(result) == 1
        assert "new line" in result[0]["content"]
        assert "<<<<<<< SEARCH" not in result[0]["content"]

    def test_replace_marker(self) -> None:
        text = "FILE: foo.py\nold\nREPLACE\nnew\nEND FILE"
        result = parse_file_blocks(text)
        assert len(result) == 1
        assert "new" in result[0]["content"]

    def test_with_marker(self) -> None:
        text = "FILE: foo.py\nold\nWITH\nnew\nEND FILE"
        result = parse_file_blocks(text)
        assert len(result) == 1
        assert "new" in result[0]["content"]

    def test_skips_code_fences(self) -> None:
        text = "FILE: foo.py\n```python\ncode\n```\nEND FILE"
        result = parse_file_blocks(text)
        assert "```python" not in result[0]["content"]

    def test_skips_empty_or_missing(self) -> None:
        text = "FILE: foo.py\n<EMPTY OR MISSING>\nEND FILE"
        result = parse_file_blocks(text)
        assert result[0]["content"] == "\n"

    def test_multiple_blocks(self) -> None:
        text = "FILE: a.py\ncontent a\nEND FILE\nFILE: b.py\ncontent b\nEND FILE"
        result = parse_file_blocks(text)
        assert len(result) == 2
        assert result[0]["path"] == "a.py"
        assert result[1]["path"] == "b.py"

    def test_no_end_tag_unclosed(self) -> None:
        text = "FILE: foo.py\ncontent line"
        result = parse_file_blocks(text)
        assert result == []


class TestParseFileBlocksWithState:
    def test_empty_text(self) -> None:
        result = parse_file_blocks_with_state("")
        assert isinstance(result, FileBlockParseResult)
        assert result.blocks == []
        assert result.has_unclosed_block is False
        assert result.open_block_path is None
        assert result.is_no_changes is True

    def test_no_changes(self) -> None:
        result = parse_file_blocks_with_state("NO_CHANGES")
        assert result.blocks == []
        assert result.is_no_changes is True

    def test_happy_path(self) -> None:
        text = "FILE: src/foo.py\nline1\nEND FILE"
        result = parse_file_blocks_with_state(text)
        assert len(result.blocks) == 1
        assert result.has_unclosed_block is False
        assert result.is_no_changes is False

    def test_unclosed_block(self) -> None:
        text = "FILE: src/foo.py\nline1\nline2"
        result = parse_file_blocks_with_state(text)
        assert result.has_unclosed_block is True
        assert result.open_block_path == "src/foo.py"
        assert result.is_no_changes is False

    def test_unclosed_patch_file(self) -> None:
        text = "PATCH_FILE: src/foo.py\nline1"
        result = parse_file_blocks_with_state(text)
        assert result.has_unclosed_block is False
        assert result.open_block_path is None

    def test_multiple_blocks_with_state(self) -> None:
        text = "FILE: a.py\ncontent a\nEND FILE\nFILE: b.py\ncontent b\nEND FILE"
        result = parse_file_blocks_with_state(text)
        assert len(result.blocks) == 2
        assert result.has_unclosed_block is False
        assert result.is_no_changes is False


class TestStripFullContentMarkers:
    def test_happy_path(self) -> None:
        content = "<FULL FILE CONTENT>\ncode\n</FULL CONTENT>"
        result = strip_full_content_markers(content)
        assert result == "code"

    def test_empty_content(self) -> None:
        assert strip_full_content_markers("") == ""

    def test_no_markers(self) -> None:
        content = "just some code\n"
        assert strip_full_content_markers(content) == "just some code\n"

    def test_case_insensitive(self) -> None:
        content = "<full file content>\ncode\n</full file content>"
        result = strip_full_content_markers(content)
        assert result == "code"

    def test_trailing_newline_preserved(self) -> None:
        content = "<FULL FILE CONTENT>\ncode\n</FULL CONTENT>\n"
        result = strip_full_content_markers(content)
        assert result.endswith("\n")

    def test_only_open_marker(self) -> None:
        content = "<FULL FILE CONTENT>\ncode"
        result = strip_full_content_markers(content)
        assert result == "code"

    def test_only_close_marker(self) -> None:
        content = "code\n</FULL CONTENT>"
        result = strip_full_content_markers(content)
        assert result == "code"

    def test_whitespace_lines_around_markers(self) -> None:
        content = "\n\n<FULL FILE CONTENT>\n\ncode\n\n</FULL CONTENT>\n\n"
        result = strip_full_content_markers(content)
        assert result.strip() == "code"


class TestApplyFileBlocks:
    @patch("polaris.kernelone.prompts.utils.ensure_parent_dir")
    @patch("polaris.kernelone.prompts.utils.open", create=True)
    def test_happy_path(self, mock_open, mock_ensure) -> None:
        mock_handle = mock_open.return_value.__enter__.return_value
        blocks = [{"path": "src/foo.py", "content": "print('hello')\n"}]
        result = apply_file_blocks(blocks, "/workspace")
        assert result == ["src/foo.py"]
        mock_ensure.assert_called_once_with(os.path.join("/workspace", "src/foo.py"))
        mock_handle.write.assert_called_once_with("print('hello')\n")

    @patch("polaris.kernelone.prompts.utils.ensure_parent_dir")
    @patch("polaris.kernelone.prompts.utils.open", create=True)
    def test_strips_markers(self, mock_open, mock_ensure) -> None:
        mock_handle = mock_open.return_value.__enter__.return_value
        blocks = [{"path": "foo.py", "content": "<FULL FILE CONTENT>\ncode\n</FULL CONTENT>"}]
        apply_file_blocks(blocks, "/workspace")
        written = mock_handle.write.call_args[0][0]
        assert "<FULL FILE CONTENT>" not in written

    @patch("polaris.kernelone.prompts.utils.ensure_parent_dir")
    @patch("polaris.kernelone.prompts.utils.open", create=True)
    def test_empty_blocks(self, mock_open, mock_ensure) -> None:
        result = apply_file_blocks([], "/workspace")
        assert result == []
        mock_open.assert_not_called()

    @patch("polaris.kernelone.prompts.utils.ensure_parent_dir")
    @patch("polaris.kernelone.prompts.utils.open", create=True)
    def test_missing_path(self, mock_open, mock_ensure) -> None:
        blocks = [{"content": "code"}]
        result = apply_file_blocks(blocks, "/workspace")
        assert result == []
        mock_open.assert_not_called()

    @patch("polaris.kernelone.prompts.utils.ensure_parent_dir")
    @patch("polaris.kernelone.prompts.utils.open", create=True)
    def test_none_content(self, mock_open, mock_ensure) -> None:
        blocks = [{"path": "foo.py", "content": None}]
        result = apply_file_blocks(blocks, "/workspace")
        assert result == []
        mock_open.assert_not_called()

    @patch("polaris.kernelone.prompts.utils.ensure_parent_dir")
    @patch("polaris.kernelone.prompts.utils.open", create=True)
    def test_deduplication(self, mock_open, mock_ensure) -> None:
        blocks = [
            {"path": "foo.py", "content": "a\n"},
            {"path": "foo.py", "content": "b\n"},
        ]
        result = apply_file_blocks(blocks, "/workspace")
        assert result == ["foo.py"]
