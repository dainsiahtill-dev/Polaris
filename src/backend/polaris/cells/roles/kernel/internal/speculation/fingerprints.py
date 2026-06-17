from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any

_FINGERPRINT_DEFAULT_ARGUMENTS: dict[str, dict[str, frozenset[Any]]] = {
    "read_file": {
        "max_bytes": frozenset({200000, 200001}),
        "range_required": frozenset({False}),
    }
}


def _normalize_value(value: Any) -> Any:
    """递归归一化单个值：仅统一换行符，列表/字典递归处理.

    抗碰撞优先（ADR-0077 wrong-adoption 是最严重缺陷）：spec_key 的归一化
    只能折叠**语义等价**的差异，绝不能把语义不同的值折叠成同一指纹，否则会
    错误领养陈旧 shadow 结果。因此这里只做跨平台等价的换行符规范化
    （`\r\n` / `\r` → `\n`），**不**对字符串值做 `strip()`：首尾空白对路径、
    内容、查询串等可能是语义相关的，折叠它们会制造 false-same 碰撞。多余的
    空白差异最坏只导致一次安全的 REPLAY（不命中），永远不会导致错误领养。
    """
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in sorted(value.items())}
    return value


def _drop_default_arguments(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    for key, default_values in _FINGERPRINT_DEFAULT_ARGUMENTS.get(tool_name, {}).items():
        value = normalized.get(key)
        try:
            should_drop = value in default_values
        except TypeError:
            should_drop = False
        if should_drop:
            normalized.pop(key, None)
    return normalized


def normalize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """对工具参数做 canonical 归一化.

    规则：
    1. 复用工具协议层的同工具别名/参数别名归一化（如 read_file path→file）。
    2. dict 按键排序（递归）——字段顺序语义无关，折叠安全。
    3. str 仅统一换行符为 `\n`——跨平台等价，折叠安全。
    刻意**不**对字符串值做首尾去空白：那是 false-same 碰撞方向，会导致
    wrong-adoption。详见 `_normalize_value` docstring。
    """
    if not isinstance(args, dict):
        return {}
    normalized_tool_name = str(tool_name or "").strip().lower()
    normalized_args = dict(args)
    try:
        from polaris.kernelone.llm.toolkit.tool_normalization import (
            normalize_tool_arguments,
            normalize_tool_name,
        )

        normalized_tool_name = normalize_tool_name(normalized_tool_name) or normalized_tool_name
        normalized_args = normalize_tool_arguments(normalized_tool_name, normalized_args)
    except (ImportError, RuntimeError, TypeError, ValueError):
        pass
    normalized = _normalize_value(normalized_args)
    if not isinstance(normalized, dict):
        return {}
    return _drop_default_arguments(normalized_tool_name, normalized)


def build_spec_key(
    tool_name: str,
    normalized_args: dict[str, Any],
    *,
    corpus_version: str = "",
    auth_scope: str = "",
    env_fingerprint: str = "",
) -> str:
    """基于 SHA-256 生成唯一 spec_key.

    Args:
        tool_name: 工具名称
        normalized_args: 经 normalize_args 处理后的参数
        corpus_version: 语料/代码库版本标识
        auth_scope: 权限范围
        env_fingerprint: 环境指纹

    Returns:
        32 字节十六进制字符串
    """
    payload = json.dumps(
        {
            "tool_name": tool_name,
            "args": normalized_args,
            "corpus_version": corpus_version,
            "auth_scope": auth_scope,
            "env_fingerprint": env_fingerprint,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_env_fingerprint(workspace: str = ".") -> str:
    """基于当前环境生成简化指纹.

    优先尝试 git HEAD；失败则回退到 workspace 目录的 mtime.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"git:{result.stdout.strip()}"
    except (subprocess.SubprocessError, OSError):
        pass

    try:
        stat = os.stat(workspace)
        return f"mtime:{int(stat.st_mtime)}"
    except OSError:
        return "env:unknown"
