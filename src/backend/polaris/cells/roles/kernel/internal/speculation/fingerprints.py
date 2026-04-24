from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any


def _normalize_value(value: Any) -> Any:
    """递归归一化单个值：字符串去空白、统一换行、列表递归处理."""
    if isinstance(value, str):
        return value.strip().replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in sorted(value.items())}
    return value


def normalize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """对工具参数做 canonical 归一化.

    规则：
    1. dict 按键排序（递归）
    2. str 去首尾空白，统一换行符为 \n
    这使得字段顺序变化、等价空白/换行差异不会改变 spec_key.
    """
    if not isinstance(args, dict):
        return {}
    return {k: _normalize_value(v) for k, v in sorted(args.items())}


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
