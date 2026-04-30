"""Tests for operation_router module."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.editing.operation_router import route_edit_operations
from polaris.kernelone.editing.patch_engine import RoutedOperation


class TestRouteEditOperationsPatch:
    """Tests for apply_patch format routing."""

    def test_patch_format_priority(self) -> None:
        text = "*** Begin Patch\n*** Add File: test.py\n+print('hello')\n*** End Patch"
        with patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch:
            mock_patch.return_value = [RoutedOperation(kind="create", path="test.py", content="print('hello')")]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 1
            assert result[0].kind == "create"
            assert result[0].path == "test.py"

    def test_patch_format_empty_others_not_called(self) -> None:
        text = "*** Begin Patch\n*** Add File: test.py\n+print('hello')\n*** End Patch"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
            patch("polaris.kernelone.editing.operation_router.extract_wholefile_blocks") as mock_whole,
        ):
            mock_patch.return_value = [RoutedOperation(kind="create", path="test.py", content="")]
            route_edit_operations(text, inchat_files=[])
            mock_edit.assert_not_called()
            mock_udiff.assert_not_called()
            mock_whole.assert_not_called()

    def test_patch_format_no_match_falls_through(self) -> None:
        text = "some text without patch format"
        with patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch:
            mock_patch.return_value = []
            result = route_edit_operations(text, inchat_files=[])
            assert result == []


class TestRouteEditOperationsEditBlocks:
    """Tests for SEARCH/REPLACE edit block routing."""

    def test_edit_blocks_with_string_search_replace(self) -> None:
        text = "<<<< SEARCH:test.py\nold\n====\nnew\n>>>> REPLACE"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = [("test.py", "old", "new")]
            result = route_edit_operations(text, inchat_files=["test.py"])
            assert len(result) == 1
            assert result[0].kind == "search_replace"
            assert result[0].path == "test.py"
            assert result[0].search == "old"
            assert result[0].replace == "new"

    def test_edit_blocks_with_list_search_replace(self) -> None:
        text = "some edit block text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = [("test.py", ["o", "l", "d"], ["n", "e", "w"])]
            result = route_edit_operations(text, inchat_files=["test.py"])
            assert result[0].search == "old"
            assert result[0].replace == "new"

    def test_edit_blocks_empty_falls_through(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            result = route_edit_operations(text, inchat_files=[])
            assert result == []


class TestRouteEditOperationsUnifiedDiff:
    """Tests for unified diff routing."""

    def test_unified_diff_with_string_before_after(self) -> None:
        text = "```diff\n--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new\n```"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = [("test.py", "old", "new")]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 1
            assert result[0].kind == "search_replace"
            assert result[0].path == "test.py"
            assert result[0].search == "old"
            assert result[0].replace == "new"

    def test_unified_diff_with_list_before_after(self) -> None:
        text = "some diff text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = [("test.py", ["o", "l", "d"], ["n", "e", "w"])]
            result = route_edit_operations(text, inchat_files=[])
            assert result[0].search == "old"
            assert result[0].replace == "new"

    def test_unified_diff_skips_empty_path(self) -> None:
        text = "some diff text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = [("", "old", "new")]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 0

    def test_unified_diff_empty_falls_through(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = []
            result = route_edit_operations(text, inchat_files=[])
            assert result == []


class TestRouteEditOperationsWholeFile:
    """Tests for whole-file block routing."""

    def test_whole_file_format(self) -> None:
        text = "test.py\n```python\nprint('hello')\n```"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
            patch("polaris.kernelone.editing.operation_router.extract_wholefile_blocks") as mock_whole,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = []
            mock_whole.return_value = [("test.py", "print('hello')")]
            result = route_edit_operations(text, inchat_files=["test.py"])
            assert len(result) == 1
            assert result[0].kind == "full_file"
            assert result[0].path == "test.py"
            assert result[0].content == "print('hello')"

    def test_whole_file_empty_returns_empty(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
            patch("polaris.kernelone.editing.operation_router.extract_wholefile_blocks") as mock_whole,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = []
            mock_whole.return_value = []
            result = route_edit_operations(text, inchat_files=[])
            assert result == []


class TestRouteEditOperationsPriority:
    """Tests for routing priority."""

    def test_patch_takes_priority_over_edit_blocks(self) -> None:
        text = "mixed format text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = [RoutedOperation(kind="create", path="a.py", content="")]
            mock_edit.return_value = [("b.py", "old", "new")]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 1
            assert result[0].path == "a.py"

    def test_edit_blocks_take_priority_over_unified_diff(self) -> None:
        text = "mixed format text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = [("b.py", "old", "new")]
            mock_udiff.return_value = [("c.py", "before", "after")]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 1
            assert result[0].path == "b.py"

    def test_unified_diff_takes_priority_over_whole_file(self) -> None:
        text = "mixed format text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
            patch("polaris.kernelone.editing.operation_router.extract_wholefile_blocks") as mock_whole,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = [("c.py", "before", "after")]
            mock_whole.return_value = [("d.py", "content")]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 1
            assert result[0].path == "c.py"


class TestRouteEditOperationsInchatFiles:
    """Tests for inchat_files parameter passing."""

    def test_inchat_files_passed_to_edit_blocks(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            route_edit_operations(text, inchat_files=["a.py", "b.py"])
            mock_edit.assert_called_once()
            call_kwargs = mock_edit.call_args.kwargs
            assert call_kwargs["valid_filenames"] == ["a.py", "b.py"]

    def test_inchat_files_passed_to_wholefile(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
            patch("polaris.kernelone.editing.operation_router.extract_unified_diff_edits") as mock_udiff,
            patch("polaris.kernelone.editing.operation_router.extract_wholefile_blocks") as mock_whole,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            mock_udiff.return_value = []
            mock_whole.return_value = []
            route_edit_operations(text, inchat_files=["a.py"])
            mock_whole.assert_called_once()
            call_kwargs = mock_whole.call_args.kwargs
            assert call_kwargs["inchat_files"] == ["a.py"]

    def test_empty_inchat_files(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = []
            route_edit_operations(text, inchat_files=[])
            mock_edit.assert_called_once()
            call_kwargs = mock_edit.call_args.kwargs
            assert call_kwargs["valid_filenames"] == []


class TestRouteEditOperationsEdgeCases:
    """Edge case tests for route_edit_operations."""

    def test_empty_text(self) -> None:
        result = route_edit_operations("", inchat_files=[])
        assert result == []

    def test_whitespace_text(self) -> None:
        result = route_edit_operations("   \n\t  ", inchat_files=[])
        assert result == []

    def test_no_matching_format(self) -> None:
        text = "This is just plain text with no edit format"
        result = route_edit_operations(text, inchat_files=[])
        assert result == []

    def test_multiple_patch_operations(self) -> None:
        text = "*** Begin Patch\n..."
        with patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch:
            mock_patch.return_value = [
                RoutedOperation(kind="create", path="a.py", content="a"),
                RoutedOperation(kind="create", path="b.py", content="b"),
            ]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 2

    def test_multiple_edit_block_operations(self) -> None:
        text = "some text"
        with (
            patch("polaris.kernelone.editing.operation_router.extract_apply_patch_operations") as mock_patch,
            patch("polaris.kernelone.editing.operation_router.extract_edit_blocks") as mock_edit,
        ):
            mock_patch.return_value = []
            mock_edit.return_value = [
                ("a.py", "old1", "new1"),
                ("b.py", "old2", "new2"),
            ]
            result = route_edit_operations(text, inchat_files=[])
            assert len(result) == 2
            assert result[0].path == "a.py"
            assert result[1].path == "b.py"
