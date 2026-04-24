"""Tests for polaris.domain.director.context_constants."""

from __future__ import annotations

from polaris.domain.director.context_constants import (
    HEAD_LINES,
    MAX_FILE_CHARS,
    MAX_SIMILAR,
    MAX_TREE_CHARS,
)


class TestContextConstants:
    def test_max_file_chars(self) -> None:
        assert MAX_FILE_CHARS == 32_000

    def test_max_tree_chars(self) -> None:
        assert MAX_TREE_CHARS == 8_000

    def test_max_similar(self) -> None:
        assert MAX_SIMILAR == 2

    def test_head_lines(self) -> None:
        assert HEAD_LINES == 300
