"""Tests for Tree-sitter-preferred back-mapping parsing."""

from __future__ import annotations

from typing import Any

from polaris.cells.factory.pipeline.internal.back_mapping import build_python_back_mapping_index
from polaris.cells.factory.pipeline.internal.models import ProjectionEntry


class _FakeNode:
    def __init__(
        self,
        *,
        node_type: str,
        start_byte: int = 0,
        end_byte: int = 0,
        start_point: tuple[int, int] = (0, 0),
        end_point: tuple[int, int] = (0, 0),
        children: list[Any] | None = None,
    ) -> None:
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.end_point = end_point
        self.children = list(children or [])


class _FakeTree:
    def __init__(self, root_node: _FakeNode) -> None:
        self.root_node = root_node


class _FakeParser:
    def parse(self, source_bytes: bytes) -> _FakeTree:
        class_name_bytes = b"ExampleService"
        method_name_bytes = b"execute"
        class_start = source_bytes.index(class_name_bytes)
        method_start = source_bytes.index(method_name_bytes)
        method_identifier = _FakeNode(
            node_type="identifier",
            start_byte=method_start,
            end_byte=method_start + len(method_name_bytes),
            start_point=(2, 8),
            end_point=(2, 15),
        )
        method_node = _FakeNode(
            node_type="function_definition",
            start_byte=method_start,
            end_byte=method_start + len(method_name_bytes),
            start_point=(2, 4),
            end_point=(3, 20),
            children=[method_identifier],
        )
        class_identifier = _FakeNode(
            node_type="identifier",
            start_byte=class_start,
            end_byte=class_start + len(class_name_bytes),
            start_point=(0, 6),
            end_point=(0, 20),
        )
        class_node = _FakeNode(
            node_type="class_definition",
            start_byte=class_start,
            end_byte=method_start + len(method_name_bytes),
            start_point=(0, 0),
            end_point=(3, 20),
            children=[class_identifier, method_node],
        )
        return _FakeTree(_FakeNode(node_type="module", children=[class_node]))


def test_back_mapping_prefers_tree_sitter_when_parser_is_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.internal.back_mapping._load_tree_sitter_parser",
        _FakeParser,
    )

    index = build_python_back_mapping_index(
        project_root="C:/Temp/tree-sitter-lab",
        rendered_files={
            "demo/application/service.py": (
                "class ExampleService:\n    def execute(self) -> None:\n        return None\n"
            )
        },
        projection_entries=(
            ProjectionEntry(
                path="demo/application/service.py",
                cell_ids=("target.demo",),
                description="demo service",
            ),
        ),
    )

    files = index.get("files", [])
    assert isinstance(files, list)
    service_file = files[0]
    assert service_file["symbol_count"] == 2
    assert all(symbol["syntax_source"] == "tree_sitter" for symbol in service_file["symbols"])
    lookup = index.get("lookup", {})
    assert isinstance(lookup, dict)
    by_qualified_name = lookup.get("by_qualified_name", {})
    assert isinstance(by_qualified_name, dict)
    assert "ExampleService.execute" in by_qualified_name
