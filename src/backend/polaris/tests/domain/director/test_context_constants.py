# ruff: noqa: E402
"""Tests for polaris.domain.director.context_constants module.

Covers:
- All exported constant values and types
- __all__ completeness
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.director import context_constants as cc


class TestContextConstantsValues:
    def test_max_file_chars(self) -> None:
        assert cc.MAX_FILE_CHARS == 32_000

    def test_max_tree_chars(self) -> None:
        assert cc.MAX_TREE_CHARS == 8_000

    def test_max_similar(self) -> None:
        assert cc.MAX_SIMILAR == 2

    def test_head_lines(self) -> None:
        assert cc.HEAD_LINES == 300


class TestContextConstantsTypes:
    def test_max_file_chars_is_int(self) -> None:
        assert isinstance(cc.MAX_FILE_CHARS, int)

    def test_max_tree_chars_is_int(self) -> None:
        assert isinstance(cc.MAX_TREE_CHARS, int)

    def test_max_similar_is_int(self) -> None:
        assert isinstance(cc.MAX_SIMILAR, int)

    def test_head_lines_is_int(self) -> None:
        assert isinstance(cc.HEAD_LINES, int)

    def test_constants_are_final_int_at_runtime(self) -> None:
        # At runtime Final[int] is just int after evaluation
        assert type(cc.MAX_FILE_CHARS) is int


class TestAllExports:
    def test_all_exports_are_defined(self) -> None:
        for name in cc.__all__:
            assert hasattr(cc, name), f"{name} not defined in module"

    def test_all_contains_expected_names(self) -> None:
        expected = {"HEAD_LINES", "MAX_FILE_CHARS", "MAX_SIMILAR", "MAX_TREE_CHARS"}
        assert expected.issubset(set(cc.__all__))

    def test_no_extra_public_names(self) -> None:
        public_names = {n for n in dir(cc) if not n.startswith("_")}
        # Allow standard module attributes
        allowed = {
            "__annotations__",
            "__builtins__",
            "__cached__",
            "__doc__",
            "__file__",
            "__loader__",
            "__name__",
            "__package__",
            "__spec__",
            "annotations",
            "Final",
        }
        extra = public_names - set(cc.__all__) - allowed
        assert extra == set(), f"Unexpected public names not in __all__: {extra}"

    def test_module_docstring_present(self) -> None:
        assert cc.__doc__ is not None
        assert "Context Gatherer" in cc.__doc__
