"""通用 MCP 客户端：配置驱动的 stdio 客户端，把任意 MCP server 的工具接进工程循环。

只做传输与缓存，不做语义假设 —— 任意 MCP server（第三方或自研）都能接。
失败**不静默**：server 不可用就是不可用（fail-closed），不降级成"无工具"。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

DEFAULT_TIMEOUT = 60.0
_ENV_CONFIG = "KERNELONE_MCP_SERVERS"


class McpServerError(RuntimeError):
    """MCP server 不可用或协议违规。必须让调用方看见。"""


class McpClient:
    """一个 server 一条子进程，请求串行化（stdio 协议本身是有状态的）。"""

    def __init__(
        self,
        name: str,
        *,
        command: Sequence[str] | None = None,
        url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not command and not url:
            raise McpServerError(f"MCP server {name!r} 需要 command 或 url")
        self.name = name
        self._command = [str(part) for part in command] if command else None
        self._url = url
        self._timeout = timeout
        self._cwd = cwd
        self._env = dict(env or {})
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._tools: list[dict[str, Any]] | None = None

    # -- 生命周期 ---------------------------------------------------------
    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._url:
            raise McpServerError(f"MCP server {self.name!r} 是 http 类型，需要 http 传输（未配置）")
        if self._process is not None and self._process.poll() is None:
            return self._process
        if not self._command:
            raise McpServerError(f"MCP server {self.name!r} 没有可执行命令")
        environment = dict(os.environ)
        environment.update(self._env)
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self._cwd,
                env=environment,
            )
        except OSError as exc:
            raise McpServerError(f"MCP server {self.name!r} 启动失败: {exc}") from exc
        return self._process

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def __enter__(self) -> McpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- 协议 --------------------------------------------------------------
    def _request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        process = self._ensure_process()
        assert process.stdin is not None and process.stdout is not None
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            request["params"] = params
        with self._lock:
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpServerError(f"MCP server {self.name!r} 管道已断: {exc}") from exc
            # stdio 协议按行响应：每请求一个读取线程 + 超时等待。
            # 比持久读取线程更简单、可证明正确（不依赖缓冲/队列时序）。
            box: list[str] = []
            done = threading.Event()

            def _read_line() -> None:
                assert process.stdout is not None
                box.append(process.stdout.readline())
                done.set()

            threading.Thread(target=_read_line, daemon=True).start()
            if not done.wait(self._timeout):
                raise McpServerError(f"MCP server {self.name!r} 响应超时（{self._timeout}s）")
            line = box[0]
            if not line:
                stderr = ""
                if process.stderr is not None:
                    try:
                        stderr = process.stderr.read() or ""
                    except (OSError, ValueError):
                        stderr = ""
                raise McpServerError(f"MCP server {self.name!r} 无响应。stderr: {stderr[-400:]}")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpServerError(f"MCP server {self.name!r} 返回非 JSON: {line[:200]!r}") from exc
        if response.get("id") != self._next_id:
            raise McpServerError(f"MCP server {self.name!r} 响应 id 不匹配")
        if "error" in response:
            error = response["error"] or {}
            raise McpServerError(f"MCP {self.name}.{method} 失败: {error.get('message')}")
        return response.get("result")

    def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        """发通知：只写不读。通知没有 id，等响应必然超时。"""

        process = self._ensure_process()
        assert process.stdin is not None
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        with self._lock:
            try:
                process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpServerError(f"MCP server {self.name!r} 管道已断: {exc}") from exc

    def initialize(self) -> dict[str, Any]:
        result = self._request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        self._notify("notifications/initialized")
        return result if isinstance(result, dict) else {}

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        if self._tools is not None and not refresh:
            return list(self._tools)
        result = self._request("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpServerError(f"MCP server {self.name!r} 的 tools/list 不是工具数组")
        self._tools = [dict(item) for item in tools]
        return list(self._tools)

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        if isinstance(result, dict) and result.get("isError"):
            text = "".join(
                str(item.get("text") or "")
                for item in (result.get("content") or [])
                if isinstance(item, dict)
            )
            raise McpServerError(f"MCP 工具 {self.name}.{name} 返回错误: {text[:400]}")
        if isinstance(result, dict):
            return result.get("content")
        return result


def load_server_configs(raw: str | None = None) -> list[dict[str, Any]]:
    """读取 MCP server 配置：`KERNELONE_MCP_SERVERS`（JSON 数组）。

    每项：`{"name": ..., "command": ["node","server.js"], "enabled_tools": [...]}`。
    未提供就返回空 —— **不静默**：调用方必须明确知道没有 MCP 可用。
    """

    payload = raw if raw is not None else os.environ.get(_ENV_CONFIG, "")
    payload = (payload or "").strip()
    if not payload:
        return []
    try:
        configs = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise McpServerError(f"{_ENV_CONFIG} 不是合法 JSON: {exc}") from exc
    if not isinstance(configs, list):
        raise McpServerError(f"{_ENV_CONFIG} 必须是 server 数组")
    result: list[dict[str, Any]] = []
    for item in configs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        command = item.get("command")
        url = item.get("url")
        if not command and not url:
            raise McpServerError(f"MCP server {name!r} 缺少 command/url")
        result.append(
            {
                "name": name,
                "command": [str(part) for part in command] if command else None,
                "url": str(url) if url else None,
                "enabled_tools": [str(value) for value in (item.get("enabled_tools") or [])],
                "cwd": str(item["cwd"]) if item.get("cwd") else None,
                "env": dict(item["env"]) if isinstance(item.get("env"), dict) else {},
            }
        )
    return result


@contextlib.contextmanager
def mcp_toolset(
    configs: Sequence[Mapping[str, Any]] | None = None,
    *,
    enabled_tools: Mapping[str, Sequence[str]] | None = None,
) -> Iterator[dict[str, Mapping[str, Any]]]:
    """按配置拉起 MCP server，产出可注入 `EngineeringTools` 的工具 schema。

    退出即关闭所有子进程。任一 server 不可用即抛 `McpServerError` ——
    不静默降级成"没有 MCP 工具"。
    """

    clients: list[McpClient] = []
    schemas: dict[str, Mapping[str, Any]] = {}
    try:
        for config in configs if configs is not None else load_server_configs():
            allowed = (enabled_tools or {}).get(str(config["name"]))
            client = McpClient(
                str(config["name"]),
                command=config.get("command"),
                url=config.get("url"),
                timeout=float(config.get("timeout", DEFAULT_TIMEOUT)),
                cwd=config.get("cwd"),
                env=config.get("env"),
            )
            client.initialize()
            clients.append(client)
            for tool in client.list_tools():
                name = str(tool.get("name"))
                if allowed is not None and name not in allowed:
                    continue
                schemas[f"mcp__{config['name']}__{name}"] = {
                    "name": f"mcp__{config['name']}__{name}",
                    "description": str(tool.get("description") or name),
                    "parameters": (tool.get("inputSchema") or {}).get("parameters")
                    or (tool.get("inputSchema") or {})
                    or {"type": "object", "properties": {}},
                }
        yield schemas
    finally:
        for client in clients:
            client.close()
