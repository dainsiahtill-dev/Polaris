"""路径处理工具。

提供路径规范化和安全检查功能。

Example:
    >>> normalize_path("/path/to/../file")
    '/path/to/file'
    >>> normalize_path("/absolute/path")
    '/absolute/path'
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from typing import Any, Final

from polaris.kernelone.shared.text_utils import unique_preserve

# 路径分隔符
WINDOWS_SEP = "\\"
POSIX_SEP = "/"

# 不安全的路径模式
UNSAFE_PATH_CHARS: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")

# 相对路径模式
RELATIVE_PATH_RE: Final[re.Pattern[str]] = re.compile(r"^\.\.?(/|$)")


def normalize_path(text: str) -> str:
    """规范化路径字符串。

    执行以下操作:
    - 去除首尾空白和引号
    - 去除尾部标点
    - 统一反斜杠为正斜杠
    - 去除开头的 ./
    - 合并连续斜杠
    - 使用 posixpath.normpath 规范化
    - 处理 .. 和 . 路径
    - 拒绝危险路径（.. 向上穿透）
    - 保留 Unix 绝对路径的前导 /

    Args:
        text: 要规范化的路径字符串

    Returns:
        规范化后的路径字符串（Unix 绝对路径保留 / 前缀）
    """
    if not text:
        return ""
    path = text.strip().strip("'\"")
    path = path.rstrip(".,;")
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = re.sub(r"/+", "/", path)
    path = posixpath.normpath(path)
    if path == ".":
        return ""
    if path == ".." or path.startswith("../"):
        return ""
    # 注意：保留绝对路径的 / 前缀，不要移除
    return path


def normalize_path_safe(path: str | Path) -> Path | None:
    """安全规范化路径，失败返回 None。

    Args:
        path: 要规范化的路径

    Returns:
        规范化后的 Path 对象，失败返回 None
    """
    try:
        normalized = normalize_path(str(path))
        if not normalized:
            return None
        return Path(normalized)
    except (ValueError, OSError):
        return None


def normalize_path_list(value: Any) -> list[str]:
    """将值规范化为规范化路径列表。

    使用 / 作为分隔符。

    Args:
        value: 要规范化的值

    Returns:
        规范化路径列表
    """
    items: list[str] = []
    if value is None:
        return items
    values = value if isinstance(value, list) else [value]

    for item in values:
        candidate = ""
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            candidate = str(item.get("path") or item.get("file") or "").strip()
        normalized = normalize_path(candidate)
        if normalized:
            items.append(normalized)
    return unique_preserve(items)


def is_docs_path(path: str) -> bool:
    """检查路径是否指向 docs 目录。

    Args:
        path: 要检查的路径

    Returns:
        如果路径指向 docs 目录或其内部文件返回 True
    """
    if not path:
        return False
    normalized = normalize_path(path).lstrip("/")
    lowered = normalized.lower()
    return lowered == "docs" or lowered.startswith("docs/")


def is_safe_path(path: str | Path) -> bool:
    """检查路径是否安全。

    Args:
        path: 要检查的路径

    Returns:
        路径安全返回 True
    """
    path_str = str(path)
    if ".." in path_str:
        return False
    if UNSAFE_PATH_CHARS.search(path_str):
        return False
    return not (WINDOWS_SEP in path_str and os.name != "nt")
