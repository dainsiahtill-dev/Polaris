"""把配置里的 MCP server 接成**可选工具集**（目录 + 选择）。

每个 server 一条子进程，进程内缓存；不可用即抛错（fail-closed）。
工具不是一次性全注入：`select_tools` 按任务文本与类别选子集，只有选中的进请求，
未选中的不进 —— 这是"低上下文 + 增强"的关键（全塞进去只会稀释注意力）。
"""

from __future__ import annotations

import atexit
import contextlib
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.infrastructure.mcp.catalog import McpToolCatalog
from polaris.infrastructure.mcp.client import McpClient, McpServerError, load_server_configs
from polaris.infrastructure.mcp.selection import select_tools

_CLIENTS: dict[str, McpClient] = {}
_LOCK = threading.Lock()
_CLOSED = False


def engineering_mcp_kwargs(
    *,
    task_text: str = "",
    max_tools: int | None = None,
    extra_categories: Sequence[str] = (),
) -> dict[str, Any]:
    """无配置返回 {}；有配置则返回 mcp_tools / mcp_call（已按任务选择子集）。"""

    configs = load_server_configs()
    if not configs:
        return {}
    catalog = McpToolCatalog.from_configs(configs).refresh()
    schemas, _evidence = select_tools(
        catalog, task_text=task_text, max_tools=max_tools or 12, extra_categories=extra_categories
    )

    async def _call(server: str, tool: str, arguments: Mapping[str, Any]) -> Any:
        config = next((item for item in configs if str(item["name"]) == server), None)
        if config is None:
            raise McpServerError(f"MCP server {server!r} 未配置")
        return await _client_for(config).call_tool(tool, arguments)

    return {"mcp_tools": schemas, "mcp_call": _call}


def catalog() -> McpToolCatalog:
    return McpToolCatalog.from_configs(load_server_configs()).refresh()


def _client_for(config: Mapping[str, Any]) -> McpClient:
    name = str(config["name"])
    with _LOCK:
        client = _CLIENTS.get(name)
        if client is None:
            client = McpClient(
                name,
                command=config.get("command"),
                url=config.get("url"),
                timeout=float(config.get("timeout", 60.0)),
                cwd=config.get("cwd"),
                env=config.get("env"),
            )
            client.initialize()
            _CLIENTS[name] = client
    return client


@atexit.register
def _close_all() -> None:
    global _CLOSED
    with _LOCK:
        if _CLOSED:
            return
        _CLOSED = True
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        with contextlib.suppress(Exception):  # 退出清理不抛
            client.close()
