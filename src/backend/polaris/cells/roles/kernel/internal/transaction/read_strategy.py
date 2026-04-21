"""Read Strategy 自动切换机制。

职责：
- 智能判断何时使用分段读取（repo_read_slice）替代完整读取（read_file）
- 检测 read_file 返回结果是否被截断
- 自动构建分段读取策略

设计原则：
- 保持向后兼容：现有调用方式不变，内部自动决策
- 类型注解完整，遵循 PEP 8 规范
- 支持并发读取的性能优化
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 默认阈值配置
DEFAULT_SLICE_MODE_THRESHOLD_BYTES = 100 * 1024  # 100KB
DEFAULT_SLICE_MODE_THRESHOLD_LINES = 1000  # 1000行
DEFAULT_SLICE_SIZE_LINES = 200  # 每次分段读取200行

# 截断检测标记
TRUNCATION_MARKERS = ("...", "[truncated]", "[截断]", "[TRUNCATED]")


@dataclass(frozen=True)
class ReadStrategy:
    """读取策略配置。

    Attributes:
        use_slice_mode: 是否使用分段读取模式
        slice_size_lines: 分段读取时每段行数
        reason: 选择该策略的原因
    """

    use_slice_mode: bool
    slice_size_lines: int = DEFAULT_SLICE_SIZE_LINES
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。"""
        return {
            "use_slice_mode": self.use_slice_mode,
            "slice_size_lines": self.slice_size_lines,
            "reason": self.reason,
        }


def _should_use_slice_mode(
    file_path: str,
    content_length: int | None = None,
    line_count: int | None = None,
    threshold_bytes: int = DEFAULT_SLICE_MODE_THRESHOLD_BYTES,
    threshold_lines: int = DEFAULT_SLICE_MODE_THRESHOLD_LINES,
) -> tuple[bool, str]:
    """判断是否应该使用分段读取模式。

    基于以下启发式规则：
    1. 文件大小超过阈值（默认100KB）
    2. 文件行数超过阈值（默认1000行）
    3. 文件扩展名暗示为大文件（如 .log, .jsonl）

    Args:
        file_path: 文件路径
        content_length: 已知的内容长度（字节），可选
        line_count: 已知的行数，可选
        threshold_bytes: 大小阈值（字节），默认100KB
        threshold_lines: 行数阈值，默认1000行

    Returns:
        Tuple of (should_use_slice: bool, reason: str)
    """
    if not file_path:
        return False, "empty file path"

    # 检查文件扩展名暗示的大文件类型
    large_file_extensions = {".log", ".jsonl", ".ndjson", ".csv", ".tsv", ".data"}
    ext = os.path.splitext(file_path.lower())[1]
    if ext in large_file_extensions:
        return True, f"large file extension detected: {ext}"

    # 基于已知内容长度判断
    if content_length is not None and content_length > threshold_bytes:
        return True, f"content length {content_length} bytes exceeds threshold {threshold_bytes}"

    # 基于已知行数判断
    if line_count is not None and line_count > threshold_lines:
        return True, f"line count {line_count} exceeds threshold {threshold_lines}"

    # 尝试从文件系统获取信息
    try:
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > threshold_bytes:
                return True, f"file size {file_size} bytes exceeds threshold {threshold_bytes}"
    except (OSError, ValueError):
        pass

    return False, "file size within normal range"


def is_content_truncated(content: str | None, result_metadata: dict[str, Any] | None = None) -> tuple[bool, str]:
    """检测内容是否被截断。

    检测方法（按优先级）：
    1. 检查 result_metadata 中的 truncated 标记
    2. 检查内容是否以截断标记结尾（如 "...", "[truncated]"）
    3. 检查内容长度是否异常短（启发式）

    Args:
        content: 读取到的内容字符串
        result_metadata: 工具返回的元数据字典，可选

    Returns:
        Tuple of (is_truncated: bool, reason: str)
    """
    # 优先检查元数据中的截断标记
    if result_metadata is not None:
        if result_metadata.get("truncated") is True:
            return True, "metadata indicates truncated=true"

        # 检查 line_count 和 content 长度是否匹配
        line_count = result_metadata.get("line_count")
        if line_count is not None and content is not None:
            actual_lines = content.count("\n") + 1
            if actual_lines < line_count * 0.9:  # 实际行数明显少于声明行数
                return True, f"line count mismatch: declared {line_count}, actual ~{actual_lines}"

    # 检查内容是否为空
    if not content:
        return False, "empty content"

    # 检查截断标记
    content_stripped = content.rstrip()
    for marker in TRUNCATION_MARKERS:
        if content_stripped.endswith(marker):
            return True, f"content ends with truncation marker: {marker!r}"

    # 检查是否有截断提示文本
    lower_content = content.lower()
    if "truncated" in lower_content or "截断" in lower_content:
        # 检查是否在结尾附近
        last_200_chars = content_stripped[-200:] if len(content_stripped) > 200 else content_stripped
        if "truncated" in last_200_chars.lower() or "截断" in last_200_chars:
            return True, "truncation warning found near end of content"

    return False, "no truncation indicators found"


def calculate_slice_ranges(
    total_lines: int,
    slice_size: int = DEFAULT_SLICE_SIZE_LINES,
    target_line: int | None = None,
    context_radius: int = 5,
) -> list[tuple[int, int]]:
    """计算分段读取的范围列表。

    Args:
        total_lines: 文件总行数
        slice_size: 每段读取的行数
        target_line: 目标行号（用于优先读取特定区域），可选
        context_radius: 目标行周围的上下文半径

    Returns:
        列表 of (start_line, end_line) 元组，行号从1开始
    """
    if total_lines <= 0:
        return []

    if total_lines <= slice_size:
        return [(1, total_lines)]

    ranges: list[tuple[int, int]] = []

    # 如果有目标行，优先包含目标行及其上下文
    if target_line is not None and 1 <= target_line <= total_lines:
        context_start = max(1, target_line - context_radius)
        context_end = min(total_lines, target_line + context_radius)
        ranges.append((context_start, context_end))

        # 添加其他分段
        remaining_ranges = _split_remaining_ranges(total_lines, slice_size, ranges)
        ranges.extend(remaining_ranges)
    else:
        # 均匀分割
        ranges = _split_uniformly(total_lines, slice_size)

    return ranges


def _split_uniformly(total_lines: int, slice_size: int) -> list[tuple[int, int]]:
    """均匀分割文件为多个范围。"""
    ranges: list[tuple[int, int]] = []
    start = 1

    while start <= total_lines:
        end = min(start + slice_size - 1, total_lines)
        ranges.append((start, end))
        start = end + 1

    return ranges


def _split_remaining_ranges(
    total_lines: int,
    slice_size: int,
    excluded_ranges: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """分割未被排除的范围。"""
    if not excluded_ranges:
        return _split_uniformly(total_lines, slice_size)

    # 合并已排除的范围
    sorted_excluded = sorted(excluded_ranges, key=lambda x: x[0])
    merged_excluded: list[tuple[int, int]] = []
    for start, end in sorted_excluded:
        if merged_excluded and start <= merged_excluded[-1][1] + 1:
            merged_excluded[-1] = (merged_excluded[-1][0], max(merged_excluded[-1][1], end))
        else:
            merged_excluded.append((start, end))

    # 计算剩余范围
    ranges: list[tuple[int, int]] = []
    current_start = 1

    for excl_start, excl_end in merged_excluded:
        if current_start < excl_start:
            # 添加当前范围之前的分段
            remaining_start = current_start
            while remaining_start < excl_start:
                remaining_end = min(remaining_start + slice_size - 1, excl_start - 1)
                ranges.append((remaining_start, remaining_end))
                remaining_start = remaining_end + 1
        current_start = max(current_start, excl_end + 1)

    # 添加最后一个排除范围之后的部分
    while current_start <= total_lines:
        end = min(current_start + slice_size - 1, total_lines)
        ranges.append((current_start, end))
        current_start = end + 1

    return ranges


def build_slice_read_plan(
    file_path: str,
    total_lines: int,
    slice_size: int = DEFAULT_SLICE_SIZE_LINES,
    target_line: int | None = None,
) -> dict[str, Any]:
    """构建分段读取计划。

    Args:
        file_path: 文件路径
        total_lines: 文件总行数
        slice_size: 每段读取的行数
        target_line: 目标行号，可选

    Returns:
        读取计划字典，包含 ranges 和 estimated_calls
    """
    ranges = calculate_slice_ranges(total_lines, slice_size, target_line)

    return {
        "file_path": file_path,
        "total_lines": total_lines,
        "slice_size": slice_size,
        "ranges": [{"start": start, "end": end} for start, end in ranges],
        "estimated_calls": len(ranges),
        "strategy": "slice_mode",
    }


def determine_optimal_strategy(
    file_path: str,
    content: str | None = None,
    result_metadata: dict[str, Any] | None = None,
    file_size_bytes: int | None = None,
    total_lines: int | None = None,
) -> ReadStrategy:
    """确定最优读取策略。

    这是主要的决策入口，综合所有信息决定使用何种读取策略。

    Args:
        file_path: 文件路径
        content: 已读取的内容（如果有），可选
        result_metadata: 工具返回的元数据，可选
        file_size_bytes: 文件大小（字节），可选
        total_lines: 文件总行数，可选

    Returns:
        ReadStrategy 对象
    """
    # 首先检查是否已经被截断
    if content is not None or result_metadata is not None:
        is_truncated, truncate_reason = is_content_truncated(content, result_metadata)
        if is_truncated:
            return ReadStrategy(
                use_slice_mode=True,
                reason=f"content truncated: {truncate_reason}",
            )

    # 检查文件大小/行数是否超过阈值
    # 如果没有提供 file_size_bytes，从 content 计算
    effective_file_size = file_size_bytes
    if effective_file_size is None and content is not None:
        effective_file_size = len(content.encode("utf-8"))

    should_slice, size_reason = _should_use_slice_mode(
        file_path,
        content_length=effective_file_size,
        line_count=total_lines,
    )

    if should_slice:
        return ReadStrategy(
            use_slice_mode=True,
            reason=size_reason,
        )

    # 默认使用普通读取模式
    return ReadStrategy(
        use_slice_mode=False,
        reason="file size within normal range, no truncation detected",
    )


# 便捷函数，用于向后兼容
def should_switch_to_slice_mode(
    file_path: str,
    content: str | None = None,
    result_metadata: dict[str, Any] | None = None,
) -> bool:
    """判断是否应切换到分段读取模式（便捷函数）。

    Args:
        file_path: 文件路径
        content: 已读取的内容，可选
        result_metadata: 工具返回的元数据，可选

    Returns:
        True 如果应该切换到分段读取模式
    """
    strategy = determine_optimal_strategy(file_path, content, result_metadata)
    return strategy.use_slice_mode
