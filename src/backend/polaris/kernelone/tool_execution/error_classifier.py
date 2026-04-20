"""Error Pattern Classifier - 错误模式识别模块。

将具体错误归类为错误模式，用于识别同类错误反复出现。
不依赖 exact signature，而是通过 error_type + generalized_msg 聚类。

Usage:
    >>> from polaris.kernelone.tool_execution.error_classifier import ToolErrorClassifier, ToolErrorPattern
    >>> classifier = ToolErrorClassifier()
    >>> pattern = classifier.classify("precision_edit", "Search string '  def foo' not found at line 42")
    >>> print(pattern.error_type)
    no_match
    >>> print(pattern.error_signature[:50])
    precision_edit:no_match:search string '  def foo' not
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class ToolErrorPattern:
    """错误模式 - 用于识别同类错误。

    Attributes:
        tool_name: 工具名称
        error_type: 错误类型 (not_found, permission, timeout, syntax, invalid_arg, no_match, encoding, unknown)
        error_signature: 泛化的错误签名，用于同类错误聚类
        frequency: 此类错误的出现次数（由 FailureBudget 维护）
    """

    tool_name: str
    error_type: str
    error_signature: str
    frequency: int = 0


class ToolErrorClassifier:
    """将具体错误分类为错误模式。

    使用多层策略分类错误：
    1. 关键词匹配确定 error_type
    2. 正则泛化移除具体数值生成 error_signature

    Example:
        >>> classifier = ToolErrorClassifier()
        >>> # 同类错误应该有相同的 signature
        >>> p1 = classifier.classify("edit", "Search '  def foo' not found at line 42")
        >>> p2 = classifier.classify("edit", "Search '  def bar' not found at line 100")
        >>> p1.error_type == p2.error_type
        True
        >>> p1.error_signature == p2.error_signature  # 泛化后相同
        False  # 因为内容不同
        >>> # 但如果都是 "no matches found" 类型，它们会被归为同一 error_type
    """

    # 错误类型关键词映射：error_type -> keywords
    _ERROR_TYPE_KEYWORDS: ClassVar[dict[str, tuple[str, ...]]] = {
        "not_found": (
            "not found",
            "does not exist",
            "no such file",
            "not exist",
        ),
        "permission": (
            "permission",
            "denied",
            "unauthorized",
            "forbidden",
            "access denied",
        ),
        "timeout": (
            "timeout",
            "timed out",
            "deadline exceeded",
        ),
        "syntax": (
            "syntax",
            "parse error",
            "invalid syntax",
            "malformed",
            "unexpected token",
        ),
        "invalid_arg": (
            "invalid",
            "illegal",
            "illegal argument",
            "wrong type",
            "missing required",
            "missing argument",
        ),
        "no_match": (
            "no matches found",
            "no match found",
            "search not found",
            "no matches",
        ),
        "encoding": (
            "encoding",
            "decode error",
            "utf-8",
            "codec can't decode",
        ),
        "exists": (
            "already exists",
            "file exists",
            "directory exists",
        ),
    }

    # 泛化模式：移除具体数值，使同类错误有相似的 signature
    _GENERALIZE_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        (re.compile(r"line \d+", re.IGNORECASE), "line N"),
        (re.compile(r"col \d+", re.IGNORECASE), "col N"),
        (re.compile(r"0x[0-9a-f]+", re.IGNORECASE), "HEXADDR"),
        # 移除长数字序列（如行号、字符偏移）
        (re.compile(r"\b\d{4,}\b"), "N"),
        # 移除具体文件路径，但保留路径结构提示
        (re.compile(r"/[^\s/]+/"), "/PATH/"),
        (re.compile(r"[A-Z]:\\[^\\]+"), "WINDOWS_PATH"),
    ]

    # 缓存已分类的错误模式，避免重复计算 (LRU eviction at max size)
    _MAX_PATTERN_CACHE_SIZE: ClassVar[int] = 1024
    _pattern_cache: OrderedDict[str, ToolErrorPattern] = OrderedDict()
    _cache_lock: ClassVar[threading.Lock] = threading.Lock()

    def classify(
        self,
        tool_name: str,
        error: str | Exception,
        *,
        use_cache: bool = True,
    ) -> ToolErrorPattern:
        """将错误分类为错误模式。

        Args:
            tool_name: 工具名称
            error: 错误字符串或异常对象
            use_cache: 是否使用缓存（默认 True）

        Returns:
            ToolErrorPattern 实例
        """
        error_msg = str(error).lower() if error else ""

        # 构建缓存键
        cache_key = f"{tool_name}:{error_msg[:100]}"
        if use_cache:
            with self._cache_lock:
                if cache_key in self._pattern_cache:
                    self._pattern_cache.move_to_end(cache_key)
                    return self._pattern_cache[cache_key]

        # 泛化错误消息
        generalized = self._generalize_message(error_msg)

        # 确定错误类型
        error_type = self._determine_error_type(error_msg)

        # 生成错误签名: tool_name:error_type:泛化消息
        signature = f"{tool_name}:{error_type}:{generalized[:80]}"

        pattern = ToolErrorPattern(
            tool_name=tool_name,
            error_type=error_type,
            error_signature=signature,
            frequency=0,
        )

        if use_cache:
            with self._cache_lock:
                self._pattern_cache[cache_key] = pattern
                while len(self._pattern_cache) > self._MAX_PATTERN_CACHE_SIZE:
                    self._pattern_cache.popitem(last=False)

        return pattern

    def _generalize_message(self, message: str) -> str:
        """泛化错误消息，移除具体数值使同类错误有相似签名。"""
        result = message

        for pattern, replacement in self._GENERALIZE_PATTERNS:
            result = pattern.sub(replacement, result)

        # 清理多余空白
        result = re.sub(r"\s+", " ", result).strip()

        return result

    def _determine_error_type(self, message: str) -> str:
        """根据错误消息确定错误类型。

        按顺序检查关键词，返回第一个匹配的类型。
        如果没有匹配，返回 "unknown"。
        """
        for error_type, keywords in self._ERROR_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in message:
                    return error_type

        return "unknown"

    def clear_cache(self) -> None:
        """清除模式缓存。"""
        with self._cache_lock:
            self._pattern_cache.clear()

    def get_error_type_display_name(self, error_type: str) -> str:
        """获取错误类型的可读名称。"""
        display_names = {
            "not_found": "文件/资源未找到",
            "permission": "权限被拒绝",
            "timeout": "操作超时",
            "syntax": "语法错误",
            "invalid_arg": "无效参数",
            "no_match": "搜索未命中",
            "encoding": "编码错误",
            "exists": "资源已存在",
            "unknown": "未知错误",
        }
        return display_names.get(error_type, error_type)
