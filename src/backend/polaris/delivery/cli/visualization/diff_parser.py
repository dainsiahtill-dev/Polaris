"""Diff 解析与渲染模块

使用 Python 标准库 difflib 提供 unified diff 能力。

支持：
- unified diff 格式生成
- 行类型识别（add/delete/modify/context）
- 统计信息生成
- 统一和 side-by-side 两种渲染模式

Example:
    >>> from polaris.delivery.cli.visualization.diff_parser import DiffView
    >>> old_text = "line1\\nline2\\nline3\\n"
    >>> new_text = "line1\\nline2_modified\\nline3\\nline4\\n"
    >>> diff = DiffView.compute(old_text, new_text, "file.txt")
    >>> print(diff.render_stat())
    file.txt: +1/-1
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class DiffLine:
    """Diff 行

    Attributes:
        line_type: 行类型 (add/delete/context)
        content: 原始内容
        old_line_no: 原始文件行号（1-indexed）
        new_line_no: 新文件行号（1-indexed）
    """

    line_type: Literal["add", "delete", "context"]
    content: str
    old_line_no: int | None = None
    new_line_no: int | None = None

    @property
    def prefix(self) -> str:
        """获取行的前缀符号"""
        return {"add": "+", "delete": "-", "context": " "}[self.line_type]

    def __str__(self) -> str:
        return f"{self.prefix}{self.content}"


@dataclass
class DiffHunk:
    """Diff 块

    Attributes:
        old_start: 原始文件起始行（1-indexed）
        old_count: 原始文件涉及行数
        new_start: 新文件起始行（1-indexed）
        new_count: 新文件涉及行数
        lines: 块内行列表
    """

    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    lines: list[DiffLine] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.lines:
            return
        # 计算行号
        old_no = self.old_start
        new_no = self.new_start
        for line in self.lines:
            if line.line_type == "delete":
                line.old_line_no = old_no
                old_no += 1
            elif line.line_type == "add":
                line.new_line_no = new_no
                new_no += 1
            else:  # context
                line.old_line_no = old_no
                line.new_line_no = new_no
                old_no += 1
                new_no += 1

    @property
    def header(self) -> str:
        """生成 hunk 头"""
        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@"

    def render(self) -> str:
        """渲染为 unified diff 格式"""
        lines = [self.header]
        for line in self.lines:
            prefix = {"add": "+", "delete": "-", "context": " "}[line.line_type]
            lines.append(f"{prefix}{line.content}")
        return "\n".join(lines)


@dataclass
class DiffFile:
    """Diff 文件

    Attributes:
        path: 文件路径
        hunks: 块列表
        is_binary: 是否为二进制文件
    """

    path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_binary: bool = False

    @property
    def insertions(self) -> int:
        """新增行数"""
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.line_type == "add")

    @property
    def deletions(self) -> int:
        """删除行数"""
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.line_type == "delete")


@dataclass
class DiffStats:
    """Diff 统计信息"""

    files: list[DiffFile] = field(default_factory=list)

    @property
    def total_insertions(self) -> int:
        return sum(f.insertions for f in self.files)

    @property
    def total_deletions(self) -> int:
        return sum(f.deletions for f in self.files)

    def __str__(self) -> str:
        parts = []
        if self.total_insertions:
            parts.append(f"+{self.total_insertions}")
        if self.total_deletions:
            parts.append(f"-{self.total_deletions}")
        return " ".join(parts) if parts else "No changes"


@dataclass
class DiffResult:
    """Diff 解析结果"""

    files: list[DiffFile] = field(default_factory=list)
    stats: DiffStats = field(default_factory=DiffStats)

    def __post_init__(self) -> None:
        self.stats.files = self.files


class DiffView:
    """Diff View 渲染器

    使用 difflib 标准库提供 diff 能力。

    Example:
        >>> diff = DiffView.compute(
        ...     old_text="line1\\nline2\\n",
        ...     new_text="line1\\nline2_modified\\nline3\\n",
        ...     path="file.txt"
        ... )
        >>> print(diff.render_unified())
    """

    def __init__(self, files: list[DiffFile] | None = None) -> None:
        self.files: list[DiffFile] = files or []

    @classmethod
    def compute(
        cls,
        old_text: str,
        new_text: str,
        path: str = "",
        context_lines: int = 3,
    ) -> DiffView:
        """计算两个文本的 diff

        Args:
            old_text: 原始文本
            new_text: 新文本
            path: 文件路径（用于显示）
            context_lines: 上下文行数

        Returns:
            DiffView 实例

        Example:
            >>> diff = DiffView.compute("a\\nb\\n", "a\\nc\\n", "test.txt")
            >>> len(diff.files)
            1
        """
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        # 使用 difflib 生成 unified diff
        diff_lines: list[str] = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=path,
                tofile=path,
                n=context_lines,
            )
        )

        return cls._parse_diff_lines(diff_lines, path)

    @classmethod
    def _parse_diff_lines(cls, diff_lines: list[str], path: str) -> DiffView:
        """解析 difflib 生成的 unified diff 行"""
        files: list[DiffFile] = []
        current_file = DiffFile(path=path)
        current_hunk: DiffHunk | None = None
        old_start, old_count, new_start, new_count = 0, 0, 0, 0

        i = 0
        while i < len(diff_lines):
            line = diff_lines[i].rstrip("\n")

            if line.startswith("---") or line.startswith("+++"):
                i += 1
                continue

            if line.startswith("@@"):
                # 保存上一个 hunk
                if current_hunk is not None:
                    current_file.hunks.append(current_hunk)

                # 解析 hunk 头
                parts = line.split(" ", 3)
                if len(parts) >= 4:
                    old_part = parts[1]
                    new_part = parts[2]
                    old_match = old_part[1:].split(",")
                    new_match = new_part[1:].split(",")
                    old_start = int(old_match[0])
                    old_count = int(old_match[1]) if len(old_match) > 1 else 1
                    new_start = int(new_match[0])
                    new_count = int(new_match[1]) if len(new_match) > 1 else 1

                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                )
                i += 1
                continue

            if current_hunk is None:
                i += 1
                continue

            prefix = line[0] if line else ""
            content = line[1:] if len(line) > 1 else ""

            if prefix == "-":
                diff_line = DiffLine(line_type="delete", content=content)
                current_hunk.lines.append(diff_line)
            elif prefix == "+":
                diff_line = DiffLine(line_type="add", content=content)
                current_hunk.lines.append(diff_line)
            elif prefix == " " or content or line == "":
                diff_line = DiffLine(line_type="context", content=content)
                current_hunk.lines.append(diff_line)

            i += 1

        # 保存最后一个 hunk
        if current_hunk is not None:
            current_file.hunks.append(current_hunk)

        if current_file.hunks:
            files.append(current_file)

        return cls(files)

    def render_unified(self) -> str:
        """渲染为 unified diff 格式文本

        Returns:
            unified diff 格式字符串
        """
        output_parts: list[str] = []

        for file in self.files:
            if file.is_binary:
                output_parts.append(f"Binary files {file.path} differ")
                continue

            output_parts.append(f"--- a/{file.path}")
            output_parts.append(f"+++ b/{file.path}")

            for hunk in file.hunks:
                output_parts.append(hunk.header)
                for line in hunk.lines:
                    output_parts.append(str(line))

        return "\n".join(output_parts)

    def render_stat(self) -> str:
        """渲染统计信息

        Returns:
            统计信息字符串，格式: "file.txt: +1/-1"
        """
        parts: list[str] = []
        for file in self.files:
            insertions = file.insertions
            deletions = file.deletions
            if insertions or deletions:
                stat = f"{file.path}: "
                if insertions:
                    stat += f"+{insertions}"
                if deletions:
                    stat += f"-{deletions}"
                parts.append(stat)

        return "\n".join(parts) if parts else "No changes"

    def render_side_by_side(self, max_width: int = 120) -> str:
        """渲染为 side-by-side 格式

        Args:
            max_width: 最大宽度

        Returns:
            side-by-side 格式字符串
        """
        if not self.files:
            return ""

        output_lines: list[str] = []
        half_width = (max_width - 10) // 2

        for file in self.files:
            output_lines.append(f"=== {file.path} ===")
            output_lines.append("")

            for hunk in file.hunks:
                # 收集左右两侧内容
                left_lines: list[str] = []
                right_lines: list[str] = []

                for line in hunk.lines:
                    if line.line_type == "delete":
                        left_lines.append(f"- {line.content[:half_width]}")
                        right_lines.append("")
                    elif line.line_type == "add":
                        left_lines.append("")
                        right_lines.append(f"+ {line.content[:half_width]}")
                    else:
                        left_lines.append(f"  {line.content[:half_width]}")
                        right_lines.append(f"  {line.content[:half_width]}")

                # 合并输出
                for left, right in zip(left_lines, right_lines, strict=False):
                    output_lines.append(f"{left:<{half_width + 2}} │ {right}")

                output_lines.append("")

        return "\n".join(output_lines)

    @property
    def stats(self) -> DiffStats:
        """获取统计信息"""
        return DiffStats(files=self.files)

    def render_colored(self) -> str:
        """渲染为带颜色的终端输出（使用 Rich）

        绿色行表示新增，红色表示删除，上下文行为默认色。

        Returns:
            带 ANSI 颜色转义序列的 diff 文本
        """
        try:
            from rich.text import Text

            output_parts: list[str | Text] = []
            for file in self.files:
                if file.is_binary:
                    output_parts.append(f"Binary files {file.path} differ")
                    continue

                output_parts.append(f"--- a/{file.path}")
                output_parts.append(f"+++ b/{file.path}")

                for hunk in file.hunks:
                    output_parts.append(hunk.header)
                    for line in hunk.lines:
                        if line.line_type == "add":
                            color = "green"
                            prefix = "+"
                        elif line.line_type == "delete":
                            color = "red"
                            prefix = "-"
                        else:
                            color = "white"
                            prefix = " "
                        colored = Text(f"{prefix}{line.content}", style=color)
                        output_parts.append(colored)

            # 用 Console 渲染
            from io import StringIO

            from rich.console import Console

            buf = StringIO()
            console = Console(file=buf, force_terminal=True)
            for part in output_parts:
                if isinstance(part, Text):
                    console.print(part)
                else:
                    console.print(part)
            return buf.getvalue()
        except (RuntimeError, ValueError):
            # Rich 不可用或渲染失败，降级到普通文本
            return self.render_unified()

    def __str__(self) -> str:
        return self.render_unified()

    def __repr__(self) -> str:
        file_count = len(self.files)
        hunk_count = sum(len(f.hunks) for f in self.files)
        return f"DiffView(files={file_count}, hunks={hunk_count})"


def compute_diff(
    old_text: str,
    new_text: str,
    path: str = "",
    context_lines: int = 3,
) -> DiffView:
    """计算两个文本的 diff 的便捷函数

    Args:
        old_text: 原始文本
        new_text: 新文本
        path: 文件路径
        context_lines: 上下文行数

    Returns:
        DiffView 实例

    Example:
        >>> diff = compute_diff("a\\nb\\n", "a\\nc\\n", "test.txt")
        >>> diff.stats.total_insertions
        1
    """
    return DiffView.compute(old_text, new_text, path, context_lines)
