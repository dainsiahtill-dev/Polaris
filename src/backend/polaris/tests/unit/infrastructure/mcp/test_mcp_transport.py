"""MCP 传输层：服务端协议 + 客户端通用性（任意 stdio server 都能接）。

实测 2026-11-27：Polaris 此前没有 MCP 传输层（旧 mcp_client.py 已删，无 JSON-RPC，
Director/QA 工具集不含 MCP 工具），只有两个响应契约和 accel 数据源。
"""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from polaris.infrastructure.mcp.client import McpClient, McpServerError, load_server_configs, mcp_toolset
from polaris.infrastructure.mcp.server import (
    METHOD_NOT_FOUND,
    build_default_tools,
    handle_request,
    serve_stdio,
)


def test_tools_list_and_call_round_trip() -> None:
    tools = {"echo": lambda args: {"echoed": args}}
    initialized = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, tools)
    assert initialized["result"]["serverInfo"]["name"] == "polaris-accel"

    listed = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tools)
    assert [item["name"] for item in listed["result"]["tools"]] == [
        "accel_context",
        "accel_verify_events",
    ]

    called = handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        tools,
    )
    assert called["error"]["code"] == -32602


def test_tool_failure_is_visible_not_silent() -> None:
    def boom(_args: dict[str, object]) -> object:
        raise RuntimeError("真实失败")

    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        },
        {"boom": boom},
    )
    assert called["result"]["isError"] is True
    assert "真实失败" in called["result"]["content"][0]["text"]


def test_unknown_method_reports_method_not_found() -> None:
    response = handle_request({"jsonrpc": "2.0", "id": 4, "method": "bogus"}, {})
    assert response["error"]["code"] == METHOD_NOT_FOUND


def test_client_drives_an_arbitrary_stdio_server() -> None:
    """客户端不假设服务端是谁：用一个独立的最小 stdio server 验证通用性。"""

    stub = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    method = req.get('method')\n"
        "    if method == 'initialize':\n"
        "        out = {'protocolVersion': '2024-11-05', 'serverInfo': {'name': 'stub'}}\n"
        "    elif method == 'tools/list':\n"
        "        out = {'tools': [{'name': 'stub_add', 'description': 'add', 'inputSchema': {'type': 'object'}}]}\n"
        "    elif method == 'tools/call':\n"
        "        a = (req.get('params') or {}).get('arguments') or {}\n"
        "        out = {'content': [{'type': 'text', 'text': str(a.get('x', 0) + a.get('y', 0))}]}\n"
        "    elif method and method.startswith('notifications/'):\n"
        "        continue\n"
        "    else:\n"
        "        resp = {'jsonrpc': '2.0', 'id': req.get('id'), 'error': {'code': -32601, 'message': 'nope'}}\n"
        "        sys.stdout.write(json.dumps(resp) + chr(10)); sys.stdout.flush(); continue\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req.get('id'), 'result': out}) + chr(10))\n"
        "    sys.stdout.flush()\n"
    )
    command = [sys.executable, "-c", textwrap.dedent(stub.lstrip("\n"))]
    with McpClient("stub", command=command, timeout=20) as client:
        client.initialize()
        assert [item["name"] for item in client.list_tools()] == ["stub_add"]
        content = client.call_tool("stub_add", {"x": 2, "y": 40})
    assert "42" in json.dumps(content, ensure_ascii=False)


def test_client_fails_loudly_when_server_is_missing() -> None:
    with pytest.raises(McpServerError):
        with McpClient("absent", command=[sys.executable, "-c", "import sys; sys.exit(3)"], timeout=10) as client:
            client.initialize()


def test_server_configs_require_command_or_url() -> None:
    with pytest.raises(McpServerError):
        load_server_configs('[{"name":"broken"}]')
    assert load_server_configs("") == []
    assert load_server_configs('[{"name":"ok","command":["node","s.js"]}]')[0]["name"] == "ok"


def test_default_registry_has_accel_tools() -> None:
    assert sorted(build_default_tools()) == ["accel_context", "accel_verify_events"]


def test_serve_stdio_is_importable_and_bound() -> None:
    assert callable(serve_stdio)


def test_mcp_toolset_materializes_configured_servers() -> None:
    """配置 → 拉起 server → 产出 mcp__<server>__<tool> schema，退出即回收。"""

    import sys as _sys
    import textwrap as _tw

    stub = _tw.dedent(
        """
        import json, sys
        for line in sys.stdin:
            req = json.loads(line)
            m = req.get("method")
            if m == "initialize":
                out = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "stub"}}
            elif m == "tools/list":
                out = {"tools": [{"name": "add", "description": "add", "inputSchema": {"type": "object"}}]}
            else:
                continue
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": out}) + chr(10))
            sys.stdout.flush()
        """
    )
    configs = [{"name": "calc", "command": [_sys.executable, "-c", stub]}]
    with mcp_toolset(configs) as schemas:
        assert "mcp__calc__add" in schemas
        assert schemas["mcp__calc__add"]["parameters"] == {"type": "object"}
    # 退出后配置再跑一次仍可用（说明没有泄漏/污染）。
    with mcp_toolset(configs) as again:
        assert "mcp__calc__add" in again
