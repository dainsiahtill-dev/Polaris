"""MCP 服务端：JSON-RPC 2.0 over stdio，把 accel 能力暴露成工具。

协议只实现工程真正需要的三个方法：`initialize`、`tools/list`、`tools/call`。
工具注册表是**通用**的（任意 callable 都能注册），accel 只是内置的两个。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

MCP_SERVER_NAME = "polaris-accel"
MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 错误码（MCP 规范子集）
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpToolError(RuntimeError):
    """工具执行失败。消息原样回给调用方，不包装成成功。"""


#: mcp_context_response 契约要求的字段。内部 pack 用的是另一套键，
#: 直接返回会产出"不符合自己声明契约"的响应 —— 这里显式投影，缺的字段
#: 用 pack 的 meta/budget 推导，推不出的如实留空而不是编。
_CONTEXT_REQUIRED = (
    "schema_version",
    "status",
    "out",
    "top_files",
    "snippets",
    "verify_plan",
    "estimated_tokens",
    "estimated_source_tokens",
    "compression_ratio",
    "token_reduction_ratio",
    "warnings",
    "constraint_mode",
)


def project_context_response(pack: Mapping[str, Any] | None) -> dict[str, Any]:
    """把 accel 内部 pack 投影到 mcp_context_response 契约。"""

    if pack is None:
        return dict.fromkeys(_CONTEXT_REQUIRED)
    assert pack is not None  # 收窄 Optional，下面安全
    raw_meta = pack.get("meta")
    meta: Mapping[str, Any] = raw_meta if isinstance(raw_meta, Mapping) else {}
    raw_budget = pack.get("budget")
    budget: Mapping[str, Any] = raw_budget if isinstance(raw_budget, Mapping) else {}
    source_chars = meta.get("source_chars_est") or meta.get("source_bytes") or 0
    snippet_chars = meta.get("snippet_chars") or 0
    ratio = (snippet_chars / source_chars) if source_chars else 0.0
    projected: dict[str, Any] = {
        "schema_version": pack.get("schema_version") or "accel.context.v1",
        "status": "ok" if pack else "empty",
        "out": pack.get("out"),
        "top_files": pack.get("top_files") or [],
        "snippets": pack.get("snippets") or [],
        "verify_plan": pack.get("verify_plan") or [],
        "estimated_tokens": meta.get("estimated_tokens"),
        "estimated_source_tokens": source_chars,
        "compression_ratio": round(ratio, 6),
        "token_reduction_ratio": round(1.0 - ratio, 6) if source_chars else None,
        "warnings": meta.get("warnings") or [],
        "constraint_mode": "budget" if budget else None,
    }
    return {key: projected[key] for key in _CONTEXT_REQUIRED}


def _accel_context(project: str, task: str, changed_files: Sequence[str] | None) -> dict[str, Any]:
    """调用 accel 编译上下文包（与 `accel context` 同一数据源）。"""

    from polaris.infrastructure.accel.config import resolve_effective_config
    from polaris.infrastructure.accel.query.context_compiler import compile_context_pack

    project_dir = resolve_project(project)
    config = resolve_effective_config(project_dir)
    pack = compile_context_pack(
        project_dir=project_dir,
        config=config,
        task=task,
        changed_files=list(changed_files or []),
        hints=None,
        previous_attempt_feedback=None,
    )
    return pack


def _accel_verify_events(project: str, changed_files: Sequence[str] | None) -> dict[str, Any]:
    """运行 accel 验证并返回事件（与 `accel verify` 同一数据源）。"""

    from polaris.infrastructure.accel.config import resolve_effective_config
    from polaris.infrastructure.accel.verify.verify.core import run_verify

    project_dir = resolve_project(project)
    config = resolve_effective_config(project_dir)
    return run_verify(project_dir=project_dir, config=config, changed_files=list(changed_files or []))


def resolve_project(project: str) -> Any:
    from pathlib import Path

    return Path(project).expanduser().resolve()


def build_default_tools() -> dict[str, Callable[[Mapping[str, Any]], Any]]:
    """内置工具：accel 的 context / verify。注册表本身对任意 callable 开放。"""

    def accel_context(args: Mapping[str, Any]) -> Any:
        project = str(args.get("project") or "")
        if not project:
            raise McpToolError("accel_context requires 'project'")
        from typing import cast

        return project_context_response(
            cast(
                Mapping[str, Any],
                _accel_context(project, str(args.get("task") or ""), args.get("changed_files") or []),
            )
        )

    def accel_verify_events(args: Mapping[str, Any]) -> Any:
        project = str(args.get("project") or "")
        if not project:
            raise McpToolError("accel_verify_events requires 'project'")
        return _accel_verify_events(project, args.get("changed_files") or [])

    return {"accel_context": accel_context, "accel_verify_events": accel_verify_events}


def tool_schema(name: str, description: str, required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "目标项目根目录"},
                "task": {"type": "string", "description": "任务描述（accel_context 用）"},
                "changed_files": {"type": "array", "items": {"type": "string"}},
            },
            "required": list(required) or ["project"],
        },
    }


def handle_request(
    request: Mapping[str, Any],
    tools: Mapping[str, Callable[[Mapping[str, Any]], Any]],
) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 请求；`notification` 返回 None。"""

    method = request.get("method")
    request_id = request.get("id")
    if not isinstance(method, str):
        return _error(request_id, INVALID_REQUEST, "invalid request")

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": MCP_SERVER_NAME, "version": "1"},
                "capabilities": {"tools": {}},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _result(
            request_id,
            {
                "tools": [
                    tool_schema(
                        "accel_context",
                        "编译该项目的 accel 上下文包（top_files/snippets/预算）",
                        required=["project"],
                    ),
                    tool_schema(
                        "accel_verify_events",
                        "运行 accel 验证并返回事件与结论",
                        required=["project"],
                    ),
                ]
            },
        )
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str) or name not in tools:
            return _error(request_id, INVALID_PARAMS, f"unknown tool: {name!r}")
        try:
            outcome = tools[name](params.get("arguments") or {})
        except McpToolError as exc:
            return _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # noqa: BLE001 - 失败必须可见，不能静默成成功
            return _result(
                request_id,
                {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True},
            )
        return _result(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(outcome, ensure_ascii=False, default=str)}]},
        )
    return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio(tools: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None) -> int:
    """逐行读 stdin 的 JSON-RPC 请求，写 stdout。

    只处理协议消息；其它行忽略（stdout 必须保持协议纯净）。
    """

    registry = dict(tools or build_default_tools())
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, PARSE_ERROR, "invalid JSON")
        else:
            try:
                response = handle_request(request, registry)
            except Exception as exc:  # noqa: BLE001 - 协议层不能崩
                response = _error(request.get("id") if isinstance(request, dict) else None, INTERNAL_ERROR, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0
