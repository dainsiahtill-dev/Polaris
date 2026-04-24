"""Tests for SUPER mode Director execution loop."""

from __future__ import annotations

from polaris.delivery.cli.terminal_console import _director_output_suggests_more_work


class TestDirectorOutputSuggestsMoreWork:
    """Tests for the heuristic that decides whether Director needs more turns."""

    def test_empty_output_suggests_more_work(self) -> None:
        assert _director_output_suggests_more_work("") is True

    def test_short_output_suggests_more_work(self) -> None:
        assert _director_output_suggests_more_work("ok") is True
        assert _director_output_suggests_more_work("done") is True

    def test_long_output_without_markers_no_more_work(self) -> None:
        text = "This is a detailed summary of all work completed. " * 20
        assert _director_output_suggests_more_work(text) is False

    def test_done_marker_indicates_complete(self) -> None:
        assert _director_output_suggests_more_work("全部完成") is False
        assert _director_output_suggests_more_work("all tasks complete") is False
        assert _director_output_suggests_more_work("all done") is False

    def test_pending_marker_indicates_more_work(self) -> None:
        assert _director_output_suggests_more_work("待执行任务") is True
        assert _director_output_suggests_more_work("2 tasks remaining") is True
        assert _director_output_suggests_more_work("pending work") is True

    def test_done_marker_overrides_pending_in_same_text(self) -> None:
        # If both markers present, done takes precedence
        text = "all tasks complete but 2 tasks remaining"
        assert _director_output_suggests_more_work(text) is False

    def test_chinese_markers(self) -> None:
        assert _director_output_suggests_more_work("下一步执行") is True
        assert _director_output_suggests_more_work("继续执行剩余任务") is True
        assert _director_output_suggests_more_work("执行完毕") is False
        assert _director_output_suggests_more_work("全部完成") is False
