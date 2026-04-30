"""Tests for polaris.delivery.cli.visualization.diff_parser."""

from __future__ import annotations

from polaris.delivery.cli.visualization.diff_parser import (
    DiffFile,
    DiffHunk,
    DiffLine,
    DiffResult,
    DiffStats,
    DiffView,
    compute_diff,
)


class TestDiffLine:
    def test_add_prefix(self) -> None:
        line = DiffLine(line_type="add", content="hello")
        assert line.prefix == "+"
        assert str(line) == "+hello"

    def test_delete_prefix(self) -> None:
        line = DiffLine(line_type="delete", content="hello")
        assert line.prefix == "-"
        assert str(line) == "-hello"

    def test_context_prefix(self) -> None:
        line = DiffLine(line_type="context", content="hello")
        assert line.prefix == " "
        assert str(line) == " hello"


class TestDiffHunk:
    def test_empty_lines(self) -> None:
        hunk = DiffHunk(old_start=1, old_count=0, new_start=1, new_count=0)
        assert hunk.header == "@@ -1,0 +1,0 @@"

    def test_header(self) -> None:
        hunk = DiffHunk(old_start=10, old_count=5, new_start=10, new_count=3)
        assert hunk.header == "@@ -10,5 +10,3 @@"

    def test_line_numbers_add(self) -> None:
        hunk = DiffHunk(
            old_start=1,
            old_count=0,
            new_start=1,
            new_count=1,
            lines=[
                DiffLine(line_type="add", content="new line"),
            ],
        )
        assert hunk.lines[0].new_line_no == 1
        assert hunk.lines[0].old_line_no is None

    def test_line_numbers_delete(self) -> None:
        hunk = DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=0,
            lines=[
                DiffLine(line_type="delete", content="old line"),
            ],
        )
        assert hunk.lines[0].old_line_no == 1
        assert hunk.lines[0].new_line_no is None

    def test_line_numbers_context(self) -> None:
        hunk = DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            lines=[
                DiffLine(line_type="context", content="same line"),
            ],
        )
        assert hunk.lines[0].old_line_no == 1
        assert hunk.lines[0].new_line_no == 1

    def test_render(self) -> None:
        hunk = DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            lines=[
                DiffLine(line_type="context", content="line"),
            ],
        )
        rendered = hunk.render()
        assert "@@ -1,1 +1,1 @@" in rendered
        assert " line" in rendered


class TestDiffFile:
    def test_empty(self) -> None:
        f = DiffFile(path="test.txt")
        assert f.insertions == 0
        assert f.deletions == 0

    def test_insertions(self) -> None:
        f = DiffFile(
            path="test.txt",
            hunks=[
                DiffHunk(
                    lines=[
                        DiffLine(line_type="add", content="new"),
                        DiffLine(line_type="add", content="new2"),
                    ]
                ),
            ],
        )
        assert f.insertions == 2

    def test_deletions(self) -> None:
        f = DiffFile(
            path="test.txt",
            hunks=[
                DiffHunk(
                    lines=[
                        DiffLine(line_type="delete", content="old"),
                    ]
                ),
            ],
        )
        assert f.deletions == 1


class TestDiffStats:
    def test_empty(self) -> None:
        stats = DiffStats()
        assert stats.total_insertions == 0
        assert stats.total_deletions == 0
        assert str(stats) == "No changes"

    def test_with_changes(self) -> None:
        stats = DiffStats(
            files=[
                DiffFile(
                    path="a.txt",
                    hunks=[
                        DiffHunk(
                            lines=[
                                DiffLine(line_type="add", content="new"),
                            ]
                        )
                    ],
                ),
                DiffFile(
                    path="b.txt",
                    hunks=[
                        DiffHunk(
                            lines=[
                                DiffLine(line_type="delete", content="old"),
                            ]
                        )
                    ],
                ),
            ]
        )
        assert stats.total_insertions == 1
        assert stats.total_deletions == 1
        assert "+1" in str(stats)
        assert "-1" in str(stats)

    def test_only_insertions(self) -> None:
        stats = DiffStats(
            files=[
                DiffFile(
                    path="a.txt",
                    hunks=[
                        DiffHunk(
                            lines=[
                                DiffLine(line_type="add", content="new"),
                            ]
                        )
                    ],
                ),
            ]
        )
        assert str(stats) == "+1"


class TestDiffResult:
    def test_post_init(self) -> None:
        result = DiffResult(files=[DiffFile(path="test.txt")])
        assert result.stats.files == result.files


class TestDiffView:
    def test_compute_no_changes(self) -> None:
        diff = DiffView.compute("hello\n", "hello\n")
        assert len(diff.files) == 0

    def test_compute_with_changes(self) -> None:
        diff = DiffView.compute("line1\nline2\n", "line1\nline2_modified\n")
        assert len(diff.files) == 1
        assert diff.files[0].path == ""

    def test_compute_with_path(self) -> None:
        diff = DiffView.compute("a\n", "b\n", path="file.txt")
        assert diff.files[0].path == "file.txt"

    def test_render_unified(self) -> None:
        diff = DiffView.compute("line1\n", "line1\nline2\n", path="test.txt")
        rendered = diff.render_unified()
        assert "--- a/test.txt" in rendered
        assert "+++ b/test.txt" in rendered
        assert "+line2" in rendered

    def test_render_stat(self) -> None:
        diff = DiffView.compute("line1\n", "line1\nline2\n", path="test.txt")
        stat = diff.render_stat()
        assert "test.txt" in stat
        assert "+1" in stat

    def test_render_stat_no_changes(self) -> None:
        diff = DiffView.compute("hello\n", "hello\n")
        assert diff.render_stat() == "No changes"

    def test_render_side_by_side(self) -> None:
        diff = DiffView.compute("a\n", "b\n", path="test.txt")
        rendered = diff.render_side_by_side()
        assert "=== test.txt ===" in rendered

    def test_render_side_by_side_empty(self) -> None:
        diff = DiffView.compute("a\n", "a\n")
        assert diff.render_side_by_side() == ""

    def test_stats_property(self) -> None:
        diff = DiffView.compute("a\n", "b\n")
        assert isinstance(diff.stats, DiffStats)

    def test_str(self) -> None:
        diff = DiffView.compute("a\n", "b\n", path="test.txt")
        assert "--- a/test.txt" in str(diff)

    def test_repr(self) -> None:
        diff = DiffView.compute("a\n", "b\n", path="test.txt")
        assert "DiffView" in repr(diff)

    def test_binary_file(self) -> None:
        diff = DiffView(files=[DiffFile(path="bin", is_binary=True)])
        rendered = diff.render_unified()
        assert "Binary files" in rendered


class TestComputeDiff:
    def test_basic(self) -> None:
        diff = compute_diff("a\n", "b\n", "test.txt")
        assert len(diff.files) == 1
        assert diff.files[0].path == "test.txt"

    def test_context_lines(self) -> None:
        diff = compute_diff("a\n", "b\n", "test.txt", context_lines=5)
        assert len(diff.files) == 1
