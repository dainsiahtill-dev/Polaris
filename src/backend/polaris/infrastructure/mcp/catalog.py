"""MCP 工具目录：把外部 MCP server 的工具汇总成可选择的目录。

设计动机：MCP server 动辄几十上百个工具，全塞进请求既贵又稀释注意力。
宿主只维护**目录**（名字/描述/类别/成本），每轮由 AGI 选出需要的子集，
只有选中的工具进入当轮请求。

server 工具惰性拉取并缓存；server 不可用即报错（fail-closed）。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polaris.infrastructure.mcp.client import McpClient, McpServerError

#: 每个 server 的默认类别（用于按任务选工具）。
DEFAULT_CATEGORY = "general"

#: 任务关键词 → 类别。命中即认为该类别与任务相关。
CATEGORY_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "playwright": ("browser", "page", "click", "screenshot", "渲染", "页面", "浏览器"),
    "rea": ("reverse", "binary", "disassemble", "decompil", "汇编", "逆向", "so", "elf"),
    "codegraph": ("symbol", "call path", "architecture", "依赖", "调用链", "符号"),
}


@dataclass(frozen=True)
class CatalogEntry:
    server: str
    name: str
    description: str
    category: str
    schema: Mapping[str, Any]


@dataclass
class McpToolCatalog:
    servers: Sequence[Mapping[str, Any]]
    entries: dict[str, CatalogEntry] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    _clients: dict[str, McpClient] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_configs(cls, configs: Sequence[Mapping[str, Any]]) -> McpToolCatalog:
        return cls(servers=list(configs))

    def _client(self, config: Mapping[str, Any]) -> McpClient:
        name = str(config["name"])
        with self._lock:
            client = self._clients.get(name)
            if client is None:
                client = McpClient(
                    name,
                    command=config.get("command"),
                    url=config.get("url"),
                    timeout=float(config.get("timeout", 60.0)),
                    cwd=config.get("cwd"),
                    env=config.get("env"),
                )
                self._clients[name] = client
            return client

    def refresh(self) -> McpToolCatalog:
        """拉取所有 server 的工具，重建目录。不可用的 server 记进 unavailable。"""

        entries: dict[str, CatalogEntry] = {}
        unavailable: dict[str, str] = {}
        for config in self.servers:
            name = str(config["name"])
            try:
                client = self._client(config)
                client.initialize()
                category = str(config.get("category") or DEFAULT_CATEGORY)
                for tool in client.list_tools():
                    tool_name = str(tool.get("name"))
                    entries[f"mcp__{name}__{tool_name}"] = CatalogEntry(
                        server=name,
                        name=tool_name,
                        description=str(tool.get("description") or tool_name),
                        category=category,
                        schema={
                            "name": f"mcp__{name}__{tool_name}",
                            "description": str(tool.get("description") or tool_name),
                            "parameters": (tool.get("inputSchema") or {}).get("parameters")
                            or (tool.get("inputSchema") or {})
                            or {"type": "object", "properties": {}},
                        },
                    )
            except McpServerError as exc:
                unavailable[name] = str(exc)
        self.entries = entries
        self.unavailable = unavailable
        return self

    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries.values():
            counts[entry.category] = counts.get(entry.category, 0) + 1
        return counts

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - 清理不抛
                    pass
            self._clients.clear()
