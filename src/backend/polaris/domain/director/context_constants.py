"""Context Gatherer 常量定义。

为 Director 的 context gatherer 模块提供统一的字符限制和数量限制常量。
从 planning 和 execution 的 context_gatherer.py 中提取，避免重复定义。

迁移历史:
     - 2026-04-05: 从 planning/execution/context_gatherer.py 合并
"""

from __future__ import annotations

from typing import Final

# Maximum characters of file content to include per file.
MAX_FILE_CHARS: Final[int] = 32_000
"""单个文件内容的最大字符数"""

# Maximum characters for repo_tree output.
MAX_TREE_CHARS: Final[int] = 8_000
"""repo_tree 输出的最大字符数"""

# Maximum similar-file candidates to search for.
MAX_SIMILAR: Final[int] = 2
"""最大相似文件候选数量"""

# Number of lines to read from the head of each target file in MODIFY mode.
HEAD_LINES: Final[int] = 300
"""MODIFY 模式下读取目标文件的头部行数"""


__all__ = [
    "HEAD_LINES",
    "MAX_FILE_CHARS",
    "MAX_SIMILAR",
    "MAX_TREE_CHARS",
]
