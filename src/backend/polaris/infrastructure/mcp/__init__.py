"""MCP（Model Context Protocol）传输层：服务端 + 通用客户端。

实测 2026-11-27：Polaris 此前**没有** MCP 支持 —— 旧 `infrastructure/tools/mcp_client.py`
已删，无 JSON-RPC 传输层，Director/QA 工具集不含任何 MCP 工具。只留下两个响应契约
（`mcp_context_response` / `mcp_verify_events`）和 accel 的数据源。

本模块补齐传输层：
- `server`：把 accel 能力暴露成 MCP 工具（JSON-RPC 2.0 over stdio）。
- `client`：通用 stdio 客户端（配置驱动），把任意 MCP server 的工具接进工程循环。

设计不变量：**通用**（任意 MCP server，不针对特定项目）、**fail-closed**
（server 不可用时工具不可用，不静默降级）、**授权不外扩**（仍受 PermissionScope 约束）。
"""

from __future__ import annotations

from polaris.infrastructure.mcp.client import McpClient, McpServerError, load_server_configs
from polaris.infrastructure.mcp.server import MCP_SERVER_NAME, McpToolError, serve_stdio

__all__ = [
    "MCP_SERVER_NAME",
    "McpClient",
    "McpServerError",
    "McpToolError",
    "load_server_configs",
    "serve_stdio",
]
