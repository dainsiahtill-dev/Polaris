"""Format utilities for audit quick CLI.

This module contains formatting functions for time, output, and data display.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def format_relative_time(iso_timestamp: str) -> str:
    """将 ISO 时间格式化为人类可读的相对时间。

    Args:
        iso_timestamp: ISO 格式的时间戳字符串

    Returns:
        人类可读的相对时间字符串
    """
    if not iso_timestamp:
        return ""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt

        if diff < timedelta(seconds=60):
            return f"{int(diff.total_seconds())}秒前"
        elif diff < timedelta(minutes=60):
            return f"{int(diff.total_seconds() / 60)}分钟前"
        elif diff < timedelta(hours=24):
            return f"{int(diff.total_seconds() / 3600)}小时前"
        elif diff < timedelta(days=30):
            return f"{diff.days}天前"
        else:
            return dt.strftime("%Y-%m-%d")
    except (RuntimeError, ValueError):
        return iso_timestamp[:19] if iso_timestamp else ""


def parse_relative_time(time_str: str) -> datetime | None:
    """解析相对时间字符串为绝对时间。

    Args:
        time_str: 相对时间字符串 (如 "1h", "30m", "yesterday", "now")

    Returns:
        解析后的 datetime 对象，或 None 如果解析失败
    """
    if not time_str:
        return None

    now = datetime.now(timezone.utc)
    time_str = time_str.strip().lower()

    # 处理绝对时间格式
    try:
        if "T" in time_str:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except ValueError:
        pass

    # 处理相对时间关键字
    if time_str == "now":
        return now
    if time_str == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if time_str == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # 解析数字+单位格式
    units = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
    }

    for suffix, seconds in sorted(units.items(), key=lambda x: -len(x[0])):
        if time_str.endswith(suffix):
            try:
                num = int(time_str[: -len(suffix)])
                return now - timedelta(seconds=num * seconds)
            except ValueError:
                continue

    return None


def resolve_export_format(*, export_format_arg: str | None, output_path: Path) -> str:
    """根据参数和文件后缀推断导出格式。

    Args:
        export_format_arg: 显式指定的格式参数
        output_path: 输出文件路径

    Returns:
        导出格式 ("json" 或 "csv")

    Raises:
        ValueError: 不支持的格式
    """
    explicit = str(export_format_arg or "").strip().lower()
    if explicit:
        if explicit in {"json", "csv"}:
            return explicit
        raise ValueError(f"Unsupported format: {explicit}")

    suffix = output_path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    return "json"


def format_time_window(*, since: datetime | None, until: datetime | None) -> str:
    """格式化时间范围描述。

    Args:
        since: 起始时间
        until: 结束时间

    Returns:
        时间范围描述字符串
    """
    if not since and not until:
        return "all"
    since_text = since.isoformat() if since else "begin"
    until_text = until.isoformat() if until else "now"
    return f"{since_text} -> {until_text}"


def parse_window(window_str: str) -> float:
    """解析时间窗口字符串为小时数。

    Args:
        window_str: 时间窗口字符串 (如 "1h", "30m", "24h", "7d")

    Returns:
        时间窗口的小时数
    """
    window_str = window_str.strip().lower()
    if window_str.endswith("h"):
        return float(window_str[:-1])
    if window_str.endswith("m"):
        return float(window_str[:-1]) / 60.0
    if window_str.endswith("d"):
        return float(window_str[:-1]) * 24.0
    try:
        return float(window_str)
    except ValueError:
        return 1.0


def get_result_attr(result: Any, key: str, default: Any = None) -> Any:
    """安全地从 ErrorCorrelationResult (dict with __slots__) 获取属性。

    Args:
        result: ErrorCorrelationResult 对象
        key: 属性名
        default: 默认值

    Returns:
        属性值或默认值
    """
    # First try attribute access (for slots)
    try:
        return getattr(result, key, default)
    except TypeError:
        pass
    # Fall back to dict access
    return result.get(key, default)


def format_event_compact(
    event: dict[str, Any],
    *,
    use_relative_time: bool = True,
) -> str:
    """格式化事件为紧凑显示格式。

    Args:
        event: 事件字典
        use_relative_time: 是否使用相对时间

    Returns:
        紧凑格式的事件字符串
    """
    ts = event.get("timestamp", "")
    ts = format_relative_time(ts) if use_relative_time else (ts[11:19] if ts else "")

    event_type = event.get("event_type", "unknown")
    source = event.get("source", {})
    role = source.get("role", "unknown") if isinstance(source, dict) else "unknown"
    action = event.get("action", {})
    name = action.get("name", "") if isinstance(action, dict) else ""
    result_str = action.get("result", "") if isinstance(action, dict) else ""

    result_mark = ""
    if result_str == "success":
        result_mark = "✓"
    elif result_str == "failure":
        result_mark = "✗"

    return f"{ts:12} [{role:12}] {event_type:20} {name[:30]:30} {result_mark}"
