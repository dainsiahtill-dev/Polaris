"""文本处理工具。

提供字符串处理、正则表达式和安全函数。

Example:
    >>> safe_truncate("Hello World", 5)
    'Hello'
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Final, TypeVar

T = TypeVar("T")

# 编译的正则表达式
RATE_LIMIT_SECONDS_RE: Final[re.Pattern[str]] = re.compile(r'resets_in_seconds"\s*:\s*(\d+)', re.IGNORECASE)

RATE_LIMIT_EPOCH_RE: Final[re.Pattern[str]] = re.compile(r'resets_at\\":(\d+)', re.IGNORECASE)

FILE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(r'<file path="([^"]+)">\n(.*?)\n</file>', re.DOTALL)

FILE_BLOCK_REGEX: Final[re.Pattern[str]] = FILE_BLOCK_RE  # 向后兼容别名

PATCH_FILE_SEARCH_REPLACE_RE: Final[re.Pattern[str]] = re.compile(
    r"PATCH_FILE\s+([^<\n]+)\s*\n<SEARCH>.*?</SEARCH>\s*\n<REPLACE>(.*?)</REPLACE>",
    re.DOTALL,
)

PATCH_FILE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"PATCH_FILE:\s*([^\n]+)\nREPLACE\n<EMPTY OR MISSING>\nWITH\n(.*?)\nEND FILE",
    re.DOTALL,
)

SIMPLE_FILE_BLOCK_RE: Final[re.Pattern[str]] = re.compile(r"FILE:\s*([^\n]+)\n(.*?)\nEND FILE", re.DOTALL)

TARGET_PATH_RE: Final[re.Pattern[str]] = re.compile(r"([A-Za-z0-9_./-]+\.(?:md|ts|tsx|js|jsx|json|yml|yaml|toml))")

ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-9;]*m")

TRUNCATE_SUFFIX: Final[str] = "..."

IGNORABLE_ERROR_PATTERNS: Final[list[str]] = [
    r"rmcp::transport::worker",
    r"AuthRequired\(AuthRequiredError",
    r"invalid_token",
    r"OAuth token exchange failed",
    r"mcp\.notion\.com/mcp",
    r"mcp\.linear\.app/mcp",
    r"unexpected EOF during handshake",
]


def safe_truncate(text: str, limit: int = 200) -> str:
    """安全截断字符串。

    Args:
        text: 要截断的文本
        limit: 最大长度

    Returns:
        截断后的字符串
    """
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def strip_ansi(text: str) -> str:
    """移除文本中的 ANSI 转义序列。

    Args:
        text: 包含 ANSI 转义序列的文本

    Returns:
        移除 ANSI 转义后的文本
    """
    if not text:
        return text
    return ANSI_ESCAPE_RE.sub("", text)


def safe_int(value: Any, default: int = -1) -> int:
    """安全转换为整数。

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        转换后的整数，失败返回 default
    """
    if isinstance(value, int):
        return value
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        转换后的浮点数，失败返回 default
    """
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


def extract_rate_limit_seconds(text: str) -> int:
    """从文本中提取速率限制秒数。

    支持两种格式:
    - resets_in_seconds: 直接的秒数
    - resets_at: Unix 时间戳格式

    Args:
        text: 要搜索的文本

    Returns:
        剩余秒数，最小为 0
    """
    if not text:
        return 0

    match = RATE_LIMIT_SECONDS_RE.search(text)
    if match:
        try:
            return max(0, int(match.group(1)))
        except ValueError:
            return 0

    match = RATE_LIMIT_EPOCH_RE.search(text)
    if match:
        try:
            reset_at = int(match.group(1))
            now = int(time.time())
            return max(0, reset_at - now)
        except ValueError:
            return 0

    return 0


def is_ignorable_error_line(text: str) -> bool:
    """检查错误行是否应该被忽略。

    Args:
        text: 错误文本

    Returns:
        如果应该忽略返回 True
    """
    if not text:
        return False
    for pattern in IGNORABLE_ERROR_PATTERNS:
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def unique_preserve(items: list[str]) -> list[str]:
    """去重但保持原有顺序。

    Args:
        items: 输入列表

    Returns:
        去重后的列表
    """
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def extract_text_from_content(content: Any) -> str:
    """从各种格式的内容中提取文本。

    支持的格式:
    - 字符串
    - 包含 'text' 或 'content' 键的字典
    - 包含 type 为 'text'/'output_text'/'input_text' 项的列表

    Args:
        content: 内容对象

    Returns:
        提取的文本
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if "text" in content and isinstance(content["text"], str):
            return content["text"].strip()
        if "content" in content and isinstance(content["content"], str):
            return content["content"].strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in (
                "text",
                "output_text",
                "input_text",
            ):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text.strip())
        return " ".join(part for part in parts if part)
    return ""


def truncate_text(text: str, limit: int = 800) -> str:
    """截断文本到指定长度。

    Args:
        text: 要截断的文本
        limit: 最大字符数

    Returns:
        截断后的文本（可能更短以避免 Unicode 截断）
    """
    if not text:
        return text
    if len(text) <= limit:
        return text

    # 安全截断：确保不在多字节字符中间截断
    truncated = text[:limit]
    # 检查是否产生了不完整的 Unicode 序列
    try:
        truncated.encode("utf-8")
    except UnicodeEncodeError:
        # 尝试回溯到有效位置
        while truncated:
            try:
                truncated.encode("utf-8")
                break
            except UnicodeEncodeError:
                truncated = truncated[:-1]

    return truncated + "..."


def normalize_str_list(value: Any) -> list[str]:
    """将值规范化为字符串列表。

    Args:
        value: 要规范化的值

    Returns:
        字符串列表
    """
    items: list[str] = []
    if value is None:
        return items
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
            elif isinstance(item, dict) and "path" in item:
                path = str(item.get("path") or "").strip()
                if path:
                    items.append(path)
    elif isinstance(value, str) and value.strip():
        items.append(value.strip())
    return items


def normalize_bool(value: Any, default: bool = False) -> bool:
    """将值规范化为布尔值。

    Args:
        value: 要规范化的值
        default: 转换失败时的默认值

    Returns:
        布尔值
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in ("true", "1", "yes", "on", "enabled"):
        return True
    if token in ("false", "0", "no", "off", "disabled", ""):
        return False
    return default


def normalize_int(value: Any, default: int = 0) -> int:
    """将值规范化为整数。

    Args:
        value: 要规范化的值
        default: 转换失败时的默认值

    Returns:
        整数
    """
    if isinstance(value, int):
        return value
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def normalize_positive_int(value: Any, default: int = 1) -> int:
    """将值规范化为正整数。

    Args:
        value: 要规范化的值
        default: 转换失败时的默认值

    Returns:
        正整数，最小为 1
    """
    result = normalize_int(value, default)
    return max(1, result)


def normalize_timeout_seconds(value: Any, default: int = 0) -> int:
    """规范化超时秒数。

    全局语义: <=0 表示禁用。

    Args:
        value: 要规范化的值
        default: 转换失败时的默认值

    Returns:
        超时秒数
    """
    parsed = normalize_int(value, default)
    return parsed if parsed > 0 else 0


def timeout_seconds_or_none(value: Any, default: int = 0) -> int | None:
    """返回子进程/请求的超时秒数。

    Args:
        value: 要规范化的值
        default: 转换失败时的默认值

    Returns:
        超时秒数或 None（禁用时）
    """
    seconds = normalize_timeout_seconds(value, default=default)
    return seconds if seconds > 0 else None


def append_log(log_path: str, text: str) -> None:
    """追加文本到日志文件。

    Args:
        log_path: 日志文件路径
        text: 要写入的文本
    """
    if not log_path:
        return
    parent = os.path.dirname(log_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(text)


def compact_str(value: Any, max_chars: int) -> str:
    """将值转换为字符串并截断。

    Args:
        value: 要转换的值
        max_chars: 最大字符数

    Returns:
        截断后的字符串
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return truncate_text(value.strip(), max_chars)


def normalize_policy_decision(value: Any) -> str:
    """规范化策略决策值。

    Args:
        value: 要规范化的值

    Returns:
        规范化后的决策: allow, block, escalate 或空字符串
    """
    token = str(value or "").strip().lower()
    if token in ("allow", "pass", "approved"):
        return "allow"
    if token in ("block", "blocked", "deny", "denied"):
        return "block"
    if token in ("escalate", "escalated"):
        return "escalate"
    return ""
