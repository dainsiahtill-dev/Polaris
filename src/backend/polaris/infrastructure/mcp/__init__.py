"""MCP（Model Context Protocol）：通用客户端 + 工具目录 + AGI 选择。

实测 2026-11-27：Polaris 此前没有 MCP 支持（旧 infrastructure/tools/mcp_client.py 已删，
无 JSON-RPC 传输层）。本模块提供：

- `client`：通用 stdio 客户端（配置驱动），任意 MCP server 都能接。
- `catalog`：把外部 server 的工具汇总成**目录**（类别/描述），惰性拉取并缓存。
- `selection`：按任务文本与类别**选择**工具子集，只有选中的进当轮请求。

设计不变量：**通用**（任意 MCP server，不针对特定项目）、**fail-closed**
（server 不可用即报错，不静默降级）、**授权不外扩**（仍受 PermissionScope 约束）、
**不全量注入**（工具多不等于能力强，未选中的不进请求）。
"""

from __future__ import annotations

from polaris.infrastructure.mcp.catalog import CatalogEntry, McpToolCatalog
from polaris.infrastructure.mcp.client import McpClient, McpServerError, load_server_configs
from polaris.infrastructure.mcp.selection import select_categories, select_tools

__all__ = [
    "CatalogEntry",
    "McpClient",
    "McpServerError",
    "McpToolCatalog",
    "load_server_configs",
    "select_categories",
    "select_tools",
]
