"""把配置里的 MCP server 接到 `EngineeringTools`。

每个 server 一条子进程，进程内缓存；任一 server 不可用即抛错（fail-closed），
不静默降级成"没有 MCP 工具"。
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Mapping
from typing import Any

from polaris.infrastructure.mcp.client import McpClient, McpServerError, load_server_configs

_CLIENTS: dict[str, McpClient] = {}
_LOCK = threading.Lock()
_CLOSED = False


def engineering_mcp_kwargs() -> dict[str, Any]:
    """无配置返回 {}；有配置则返回 mcp_tools / mcp_call。"""

    configs = load_server_configs()
    if not configs:
        return {}
    schemas: dict[str, Mapping[str, Any]] = {}
    for config in configs:
        client = _client_for(config)
        for tool in client.list_tools():
            name = str(tool.get("name"))
            schemas[f"mcp__{config['name']}__{name}"] = {
                "name": f"mcp__{config['name']}__{name}",
                "description": str(tool.get("description") or name),
                "parameters": (tool.get("inputSchema") or {}).get("parameters")
                or (tool.get("inputSchema") or {})
                or {"type": "object", "properties": {}},
            }

    async def _call(server: str, tool: str, arguments: Mapping[str, Any]) -> Any:
        config = next((item for item in load_server_configs() if str(item["name"]) == server), None)
        if config is None:
            raise McpServerError(f"MCP server {server!r} 未配置")
        return await _client_for(config).call_tool(tool, arguments)

    return {"mcp_tools": schemas, "mcp_call": _call}


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
        try:
            client.close()
        except Exception:  # noqa: BLE001 - 退出清理不抛
            pass
