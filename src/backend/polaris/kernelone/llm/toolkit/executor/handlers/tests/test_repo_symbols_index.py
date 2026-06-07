"""Tests for repo_symbols_index tree-sitter symbol extraction.

Regression coverage for the tree-sitter binding mismatch that caused
``repo_symbols_index`` to raise ``TypeError`` (the installed Rust-backed
binding wants ``str`` and exposes node accessors as methods, while the code
was written for the pure-Python ``py-tree-sitter`` property API).

These tests assert that the handler:
  * never raises ``TypeError`` and surfaces a real symbol (``foo``),
  * slices identifier names from UTF-8 *bytes* (non-ASCII correctness),
  * fails *soft* (returns ``ok: True`` with no symbols) when the grammar
    is unavailable, rather than propagating an exception.
"""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.llm.toolkit.executor.handlers.repo as repo_mod
from polaris.kernelone.llm.toolkit.executor.handlers.repo import (
    _find_all_symbols_ts,
    _handle_repo_symbols_index,
)


def _make_executor(workspace: Path):
    from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

    return AgentAccelToolExecutor(str(workspace))


class TestRepoSymbolsIndexNoTypeError:
    """The original bug raised TypeError from parser.parse(bytes)."""

    def test_python_function_is_indexed_without_typeerror(self, tmp_path: Path) -> None:
        (tmp_path / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        executor = _make_executor(tmp_path)

        # Must not raise (previously: TypeError: 'bytes' object is not a str).
        result = _handle_repo_symbols_index(executor, paths=["."])

        assert result["ok"] is True
        assert result["files_processed"] == 1
        names = {sym["name"] for sym in result["symbols"]}
        assert "foo" in names
        foo = next(sym for sym in result["symbols"] if sym["name"] == "foo")
        assert foo["kind"] == "function_definition"
        assert foo["line"] == 1
        assert foo["file"] == "sample.py"

    def test_class_and_function_both_indexed(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text(
            "def foo():\n    return 1\n\n\nclass Bar:\n    def baz(self):\n        return 2\n",
            encoding="utf-8",
        )
        executor = _make_executor(tmp_path)

        result = _handle_repo_symbols_index(executor, paths=["."])

        names = {sym["name"] for sym in result["symbols"]}
        assert {"foo", "Bar"}.issubset(names)

    def test_non_ascii_identifier_sliced_from_bytes(self, tmp_path: Path) -> None:
        # Byte offsets must index into UTF-8 bytes, not the str, otherwise a
        # multibyte identifier preceding the name would shift the slice.
        (tmp_path / "u.py").write_text("名前 = 1\n\n\ndef 関数():\n    return 1\n", encoding="utf-8")
        executor = _make_executor(tmp_path)

        result = _handle_repo_symbols_index(executor, paths=["."])

        names = {sym["name"] for sym in result["symbols"]}
        assert "関数" in names


class TestFindAllSymbolsHelper:
    def test_returns_foo_symbol(self) -> None:
        symbols = _find_all_symbols_ts("def foo():\n    return 1\n", "python")
        assert any(s["name"] == "foo" and s["kind"] == "function_definition" for s in symbols)

    def test_soft_fail_when_grammar_unavailable(self, monkeypatch) -> None:
        # Simulate a missing/broken grammar: get_parser raises LookupError.
        import tree_sitter_language_pack

        def _raise(_lang: str):
            raise LookupError("grammar not installed")

        monkeypatch.setattr(tree_sitter_language_pack, "get_parser", _raise)

        # Must not raise; returns empty list (soft fail).
        symbols = _find_all_symbols_ts("def foo():\n    return 1\n", "python")
        assert symbols == []

    def test_handler_soft_fail_returns_ok_when_grammar_unavailable(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "sample.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        executor = _make_executor(tmp_path)

        import tree_sitter_language_pack

        def _raise(_lang: str):
            raise LookupError("grammar not installed")

        monkeypatch.setattr(tree_sitter_language_pack, "get_parser", _raise)

        result = _handle_repo_symbols_index(executor, paths=["."])

        # No TypeError, no exception: clean result with zero symbols.
        assert result["ok"] is True
        assert result["symbols"] == []


class TestBindingAdapters:
    """The cross-binding accessor helpers normalise both tree-sitter APIs."""

    def test_call_or_value_handles_property(self) -> None:
        class Obj:
            value = 7

        assert repo_mod._ts_call_or_value(Obj(), "value") == 7

    def test_call_or_value_handles_method(self) -> None:
        class Obj:
            def value(self) -> int:
                return 9

        assert repo_mod._ts_call_or_value(Obj(), "value") == 9

    def test_node_kind_prefers_type_then_kind(self) -> None:
        class PyNode:
            type = "function_definition"

        class RustNode:
            def kind(self) -> str:
                return "function_item"

        assert repo_mod._ts_node_kind(PyNode()) == "function_definition"
        assert repo_mod._ts_node_kind(RustNode()) == "function_item"

    def test_node_children_supports_property_and_count_api(self) -> None:
        class Child:
            pass

        kids = [Child(), Child()]

        class PyNode:
            children = kids

        class RustNode:
            def child_count(self) -> int:
                return len(kids)

            def child(self, i: int) -> Child:
                return kids[i]

        assert repo_mod._ts_node_children(PyNode()) == kids
        assert repo_mod._ts_node_children(RustNode()) == kids

    def test_node_start_rowcol_supports_tuple_and_point(self) -> None:
        class PyNode:
            start_point = (3, 5)

        class Point:
            row = 3
            column = 5

        class RustNode:
            def start_position(self) -> Point:
                return Point()

        assert repo_mod._ts_node_start_rowcol(PyNode()) == (3, 5)
        assert repo_mod._ts_node_start_rowcol(RustNode()) == (3, 5)
