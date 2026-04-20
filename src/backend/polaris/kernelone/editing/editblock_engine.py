"""EditBlock Engine - SEARCH/REPLACE 块解析器

Aider 风格的纯文本代码编辑格式解析引擎。

格式规范:
    <<<< SEARCH[:filepath]
    <原始代码>
    ====
    <替换代码>
    >>>> REPLACE

支持变体:
    - <<<< SEARCH / <<<<< SEARCH / <<<<<<< SEARCH (Git 风格)
    - 文件名可嵌在 SEARCH 行或前置行
    - 支持多文件连续编辑
    - 支持 ... 省略号锚点匹配
"""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

DEFAULT_FENCE = ("```", "```")
TRIPLE_BACKTICKS = "```"

# 增强的头部匹配模式 - 支持更多变体
HEAD_PATTERNS = [
    r"^<{4,9}\s*SEARCH[:\s]*([^\n]*)$",  # <<<< SEARCH[:filename]
    r"^<{4,9}\s*ORIGINAL[:\s]*([^\n]*)$",  # <<<< ORIGINAL[:filename]
    r"^<{4,9}\s*SOURCE[:\s]*([^\n]*)$",  # <<<< SOURCE[:filename]
]

DIVIDER_PATTERNS = [
    r"^={4,9}\s*$",  # ====
    r"^-{4,9}\s*$",  # ----
]

UPDATED_PATTERNS = [
    r"^>{4,9}\s*REPLACE\s*$",  # >>>> REPLACE
    r"^>{4,9}\s*UPDATED\s*$",  # >>>> UPDATED
    r"^>{4,9}\s*RESULT\s*$",  # >>>> RESULT
]

# 省略号锚点模式
ELLIPSIS_PATTERN = re.compile(r"^(\s*)\.\.\.(\s*)$")


class EditBlock:
    """单个编辑块数据结构"""

    def __init__(
        self,
        filepath: str,
        search_text: str,
        replace_text: str,
        start_line: int = 0,
        end_line: int = 0,
    ):
        self.filepath = filepath
        self.search_text = search_text
        self.replace_text = replace_text
        self.start_line = start_line
        self.end_line = end_line
        self.has_ellipsis = "..." in search_text or "..." in replace_text

    def __repr__(self) -> str:
        return f"EditBlock({self.filepath!r}, lines {self.start_line}-{self.end_line})"

    def get_search_lines(self) -> list[str]:
        """获取搜索文本的行列表"""
        return self.search_text.splitlines()

    def get_replace_lines(self) -> list[str]:
        """获取替换文本的行列表"""
        return self.replace_text.splitlines()


def strip_filename(filename: str, fence: tuple[str, str] = DEFAULT_FENCE) -> str | None:
    """清理文件名，去除 fence 和标记前缀。

    Args:
        filename: 原始文件名字符串
        fence: Markdown fence 元组

    Returns:
        清理后的文件名或 None
    """
    candidate = filename.strip()
    if not candidate or candidate == "...":
        return None

    upper = candidate.upper()
    marker_prefixes = (
        "<<<<<",
        ">>>>>",
        "=====",
        "SEARCH",
        "REPLACE",
        "ORIGINAL",
        "UPDATED",
        "SOURCE",
        "RESULT",
        "PATCH_FILE",
        "FILE:",
    )
    if any(upper.startswith(prefix) for prefix in marker_prefixes):
        return None

    start_fence = fence[0]
    if candidate.startswith(start_fence):
        inner = candidate[len(start_fence) :]
        if inner and ("." in inner or "/" in inner):
            return inner
        return None

    if candidate.startswith(TRIPLE_BACKTICKS):
        inner = candidate[len(TRIPLE_BACKTICKS) :]
        if inner and ("." in inner or "/" in inner):
            return inner
        return None

    candidate = candidate.rstrip(":").lstrip("#").strip()
    candidate = candidate.strip("`").strip("*").strip()
    if not candidate:
        return None
    if " " in candidate and "/" not in candidate and "\\" not in candidate and "." not in candidate:
        return None
    return candidate or None


def _find_filename_nearby(
    lines: Sequence[str],
    fence: tuple[str, str],
    valid_filenames: Sequence[str],
) -> str | None:
    """在编辑块附近查找文件名。

    搜索策略:
    1. 检查前两行是否包含文件名
    2. 模糊匹配已知文件名列表
    3. 返回最可能的候选
    """
    recent = list(lines)[-3:]
    recent.reverse()

    candidates: list[str] = []
    for line in recent:
        parsed = strip_filename(line, fence)
        if parsed:
            candidates.append(parsed)
        if not line.startswith(fence[0]) and not line.startswith(TRIPLE_BACKTICKS):
            break

    if not candidates:
        return None

    # 精确匹配
    for candidate in candidates:
        if candidate in valid_filenames:
            return candidate

    # 文件名匹配
    for candidate in candidates:
        for valid in valid_filenames:
            if candidate == Path(valid).name:
                return valid

    # 模糊匹配 (相似度 > 0.8)
    for candidate in candidates:
        close = difflib.get_close_matches(candidate, list(valid_filenames), n=1, cutoff=0.8)
        if len(close) == 1:
            return close[0]

    # 扩展名检查
    for candidate in candidates:
        if "." in candidate:
            return candidate

    return candidates[0]


def _extract_filename_from_header(line: str) -> str | None:
    """从 SEARCH 头行提取文件名。

    格式: <<<< SEARCH:filepath
    """
    for pattern in HEAD_PATTERNS:
        match = re.match(pattern, line.strip())
        if match:
            filename = match.group(1).strip()
            if filename:
                return filename
    return None


def _match_divider(line: str) -> bool:
    """匹配分隔行 ===="""
    for pattern in DIVIDER_PATTERNS:
        if re.match(pattern, line.strip()):
            return True
    return False


def _match_updated(line: str) -> bool:
    """匹配替换结束行 >>>> REPLACE"""
    for pattern in UPDATED_PATTERNS:
        if re.match(pattern, line.strip()):
            return True
    return False


def _match_head(line: str) -> tuple[bool, str | None]:
    """匹配搜索头行，返回 (是否匹配, 文件名)

    格式: <<<< SEARCH 或 <<<< SEARCH:filepath
    """
    # 先尝试提取内嵌文件名
    filename = _extract_filename_from_header(line)
    if filename is not None:
        return True, filename

    # 再尝试标准模式
    stripped = line.strip()
    if re.match(r"^<{4,9}\s*SEARCH\s*$", stripped):
        return True, None
    if re.match(r"^<{4,9}\s*ORIGINAL\s*$", stripped):
        return True, None

    return False, None


def extract_edit_blocks(
    content: str,
    *,
    fence: tuple[str, str] = DEFAULT_FENCE,
    valid_filenames: Sequence[str] | None = None,
    default_filepath: str | None = None,
) -> list[tuple[str, str, str]]:
    """提取 SEARCH/REPLACE 块 (向后兼容接口)。

    Args:
        content: 包含编辑块的文本内容
        fence: Markdown fence 标记
        valid_filenames: 有效文件名列表，用于模糊匹配
        default_filepath: 默认文件路径，当编辑块未指定路径时使用

    Returns:
        元组列表 (filepath, search_text, replace_text)
    """
    blocks = parse_edit_blocks(content, fence=fence, valid_filenames=valid_filenames, default_filepath=default_filepath)
    return [(b.filepath, b.search_text, b.replace_text) for b in blocks]


def parse_edit_blocks(
    content: str,
    *,
    fence: tuple[str, str] = DEFAULT_FENCE,
    valid_filenames: Sequence[str] | None = None,
    default_filepath: str | None = None,
) -> list[EditBlock]:
    """解析 SEARCH/REPLACE 编辑块。

    增强功能:
    - 支持从 SEARCH 头行提取文件名 (<<<< SEARCH:filepath)
    - 支持多文件连续编辑
    - 支持省略号锚点 (...)
    - 更好的错误恢复
    - 支持通过 default_filepath 参数提供默认文件名

    Args:
        content: 包含编辑块的文本内容
        fence: Markdown fence 标记
        valid_filenames: 有效文件名列表，用于模糊匹配
        default_filepath: 默认文件路径，当编辑块未指定路径时使用

    Returns:
        EditBlock 对象列表
    """
    if not content or not content.strip():
        return []

    valid = list(valid_filenames or [])
    lines = content.splitlines(keepends=True)

    blocks: list[EditBlock] = []
    current_filename: str | None = default_filepath  # 优先使用默认值
    i = 0

    while i < len(lines):
        line = lines[i]

        # 尝试匹配 SEARCH 头
        is_head, embedded_filename = _match_head(line)
        if not is_head:
            i += 1
            continue

        start_line = i

        # 确定文件名 (优先级: 内嵌 > 附近查找 > 继承/default)
        filename: str | None = None
        if embedded_filename:
            filename = embedded_filename
        else:
            filename = _find_filename_nearby(lines[max(0, i - 3) : i], fence, valid)

        if not filename and current_filename:
            filename = current_filename

        if not filename:
            # 无法确定文件名，跳过此块
            i += 1
            continue

        current_filename = filename

        # 收集搜索文本
        original_lines: list[str] = []
        i += 1
        while i < len(lines) and not _match_divider(lines[i].strip()):
            original_lines.append(lines[i])
            i += 1

        if i >= len(lines):
            break

        # 收集替换文本
        updated_lines: list[str] = []
        i += 1
        while i < len(lines) and not (_match_updated(lines[i].strip()) or _match_divider(lines[i].strip())):
            updated_lines.append(lines[i])
            i += 1

        # 创建编辑块
        block = EditBlock(
            filepath=filename,
            search_text="".join(original_lines),
            replace_text="".join(updated_lines),
            start_line=start_line,
            end_line=i,
        )
        blocks.append(block)
        i += 1

    return blocks


def _is_safe_relative_path(path: str) -> bool:
    """检查路径是否安全（相对路径且不含..）。"""
    token = str(path or "").strip().replace("\\", "/")
    if not token:
        return False
    if token.startswith("/") or token.startswith("\\"):
        return False
    if re.match(r"^[a-zA-Z]:[/\\]", token):
        return False
    if "\x00" in token:
        return False
    parts = [part for part in token.split("/") if part]
    return not any(part in {".", ".."} for part in parts)


def _normalize_line_endings(text: str) -> str:
    """统一换行符为 \n，便于匹配。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def apply_edit_blocks(
    file_contents: dict[str, str],
    blocks: list[EditBlock],
    *,
    fuzzy: bool = True,
) -> dict[str, str]:
    """应用编辑块到文件内容。

    Args:
        file_contents: 文件名 -> 内容 的映射
        blocks: 编辑块列表
        fuzzy: 是否启用模糊匹配

    Returns:
        修改后的文件内容映射
    """
    from polaris.kernelone.tool_execution.suggestions.precise_matcher import fuzzy_replace

    result = dict(file_contents)

    for block in blocks:
        filepath = block.filepath

        # Skip no-op blocks: search == replace (LLM hallucination pattern)
        if block.search_text == block.replace_text:
            logger.info("Skipping no-op edit block for %s (search == replace)", filepath)
            continue

        # 安全路径检查
        if not _is_safe_relative_path(filepath):
            logger.warning("Unsafe filepath in edit block: %s", filepath)
            continue

        if filepath not in result:
            # 文件不存在，尝试创建
            result[filepath] = block.replace_text
            continue

        content = result[filepath]
        search = block.search_text
        replace = block.replace_text

        # 关键修复: 空搜索文本检查
        # BUG: '' in 'any string' 永远返回 True，会导致整个文件被替换为空
        if not search:
            logger.warning("Empty search text in edit block for %s", filepath)
            continue

        # 尝试精确匹配（先规范化换行符）
        normalized_content = _normalize_line_endings(content)
        normalized_search = _normalize_line_endings(search)

        if normalized_search in normalized_content:
            # 找到匹配位置，在原始内容中替换
            idx = normalized_content.index(normalized_search)
            # 计算原始内容中的位置
            result[filepath] = content[:idx] + replace + content[idx + len(search) :]
            continue

        # 模糊匹配回退
        if fuzzy:
            new_content, metadata = fuzzy_replace(content, search, replace)
            if metadata.get("success"):
                result[filepath] = new_content
                continue

        # 未找到匹配，保留原内容
        logger.warning("No match found in %s for search: %s...", filepath, search[:50].replace("\n", "\\n"))

    return result


def count_edit_blocks(content: str) -> int:
    """快速统计编辑块数量"""
    return len(parse_edit_blocks(content))


def validate_edit_blocks(blocks: list[EditBlock]) -> list[str]:
    """验证编辑块的有效性，返回错误列表。

    Note: search == replace (no-op blocks) are silently filtered out
    by apply_edit_blocks rather than treated as errors. This handles
    the LLM hallucination pattern where the model copies search text
    into replace without making changes.
    """
    errors: list[str] = []

    for i, block in enumerate(blocks):
        prefix = f"Block {i + 1}"

        if not block.filepath:
            errors.append(f"{prefix}: Missing filepath")

        if not block.search_text.strip():
            errors.append(f"{prefix} ({block.filepath}): Empty search text")

    return errors


__all__ = [
    "EditBlock",
    "_is_safe_relative_path",  # 导出用于测试
    "_normalize_line_endings",  # 导出用于测试
    "apply_edit_blocks",
    "count_edit_blocks",
    "extract_edit_blocks",
    "parse_edit_blocks",
    "strip_filename",
    "validate_edit_blocks",
]
