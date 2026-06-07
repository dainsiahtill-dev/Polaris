"""Regression: tree-sitter tag extraction must fire under both binding ABIs.

The traversal in ``repo_intelligence/tags.py`` was written against the py-tree-sitter C ABI
(``node.type`` / ``node.children`` / ``tree.root_node`` properties). The Rust-style binding
shipped by recent ``tree_sitter_language_pack`` exposes those as zero-arg methods, so every
file silently degraded to the regex fallback (the repo map ran blind to methods/structure).
These tests pin the ABI-agnostic shim so extraction never silently degrades again.
"""

from __future__ import annotations

import logging
from pathlib import Path

from polaris.kernelone.context.repo_intelligence.tags import get_tags_for_file


def test_tree_sitter_extraction_fires_without_degrading(tmp_path: Path, caplog: object) -> None:
    src = tmp_path / "widget.py"
    src.write_text(
        "class WidgetFactory:\n"
        "    def build_widget(self):\n"
        "        return Widget()\n\n"
        "def top_level():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
        tags = get_tags_for_file(str(tmp_path), str(src), languages=["python"])

    names = {t.name for t in tags}
    # class, nested method, and top-level function must all be surfaced.
    assert "WidgetFactory" in names
    assert "build_widget" in names
    assert "top_level" in names
    # the tree-sitter path must SUCCEED — no degradation warning may be emitted.
    messages = [r.getMessage() for r in caplog.records]  # type: ignore[attr-defined]
    assert not any("tree-sitter extraction failed" in m for m in messages), messages
    assert not any("tree-sitter parse failed" in m for m in messages), messages


def test_method_line_numbers_are_resolved(tmp_path: Path) -> None:
    src = tmp_path / "mod.py"
    src.write_text(
        "class A:\n    def first(self):\n        return 1\n",
        encoding="utf-8",
    )
    tags = get_tags_for_file(str(tmp_path), str(src), languages=["python"])
    by_name = {t.name: t for t in tags}
    assert by_name["A"].line == 0
    assert by_name["first"].line == 1  # 0-based start row via the ABI shim
