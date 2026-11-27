"""MCP 工具目录与 AGI 选择。

设计动机：MCP server 动辄几十上百个工具，全塞进请求既贵又稀释注意力。
宿主只维护目录，每轮按任务文本选子集；未选中的不进请求。
"""

from __future__ import annotations

from polaris.infrastructure.mcp.catalog import CatalogEntry, McpToolCatalog
from polaris.infrastructure.mcp.selection import select_categories, select_tools


def _catalog() -> McpToolCatalog:
    catalog = McpToolCatalog(servers=[])
    catalog.entries = {
        "mcp__rea__disasm": CatalogEntry("rea", "disasm", "disassemble", "rea", {}),
        "mcp__rea__decomp": CatalogEntry("rea", "decomp", "decompile", "rea", {}),
        "mcp__playwright__click": CatalogEntry("playwright", "click", "click", "playwright", {}),
        "mcp__codegraph__explore": CatalogEntry("codegraph", "explore", "explore", "codegraph", {}),
    }
    return catalog


def test_select_categories_by_task_text() -> None:
    assert select_categories("逆向 so 二进制，反汇编看看") == {"rea"}
    assert select_categories("render the page and screenshot it 渲染页面") == {"playwright"}
    assert select_categories("定位折叠相关实现") == set()


def test_only_selected_tools_are_materialized() -> None:
    schemas, evidence = select_tools(_catalog(), task_text="reverse the binary")
    assert set(schemas) == {"mcp__rea__disasm", "mcp__rea__decomp"}
    assert evidence["selected"] == 2
    assert evidence["catalog_size"] == 4
    assert evidence["dropped_by_category"] == 2


def test_max_tools_caps_the_selection() -> None:
    schemas, evidence = select_tools(_catalog(), task_text="reverse and render", max_tools=1)
    assert len(schemas) == 1
    assert evidence["dropped_by_limit"] == 1


def test_explicit_extra_categories_bypass_keyword_match() -> None:
    schemas, _ = select_tools(_catalog(), task_text="无关任务", extra_categories=["playwright"])
    assert set(schemas) == {"mcp__playwright__click"}
