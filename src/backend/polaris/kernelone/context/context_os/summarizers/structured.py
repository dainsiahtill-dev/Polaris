"""Structured Summarization - 结构化摘要实现

基于 Tree-sitter 的代码感知摘要。
ADR-0067: ContextOS 2.0 摘要策略选型 - Tier 2 安全摘要层

特点:
- 代码结构感知: 保留类/函数签名，折叠实现体
- 多语言支持: Python, JavaScript, Go
- 零幻觉: 直接操作 AST，不生成新代码
- 策略化: 支持多种压缩策略
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummaryStrategy,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Tree-sitter 语言映射
LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
}

# 关键节点类型 (用于保留签名)
SIGNATURE_NODE_TYPES = {
    "python": ["function_definition", "class_definition"],
    "javascript": ["function_declaration", "class_declaration", "method_definition"],
    "typescript": ["function_declaration", "class_declaration", "method_definition"],
    "go": ["function_declaration", "method_declaration", "type_declaration"],
}

# 错误处理节点类型 (用于 error_paths 策略)
ERROR_NODE_TYPES = {
    "python": ["raise_statement", "try_statement", "except_clause"],
    "javascript": ["throw_statement", "try_statement", "catch_clause"],
    "go": ["defer_statement", "recover"],
}


@dataclass(frozen=True)
class CodeCompressionConfig:
    """代码压缩配置"""

    strategy: str = "signatures_only"  # signatures_only | docstring_focus | error_paths
    keep_docstrings: bool = True
    keep_type_annotations: bool = True
    max_body_lines: int = 3  # 保留实现体的最大行数


class TreeSitterSummarizer:
    """基于 Tree-sitter 的代码感知摘要器

    使用 AST (抽象语法树) 进行智能代码压缩，保留关键结构。

    Example:
        ```python
        summarizer = TreeSitterSummarizer()
        compressed = summarizer.summarize(
            content=long_code,
            max_tokens=300,
            content_type="code",
        )
        ```
    """

    strategy = SummaryStrategy.STRUCTURED

    def __init__(
        self,
        default_config: CodeCompressionConfig | None = None,
    ) -> None:
        """初始化 TreeSitterSummarizer

        Args:
            default_config: 默认压缩配置
        """
        self.default_config = default_config or CodeCompressionConfig()
        self._parsers: dict[str, Any] = {}
        self._languages: dict[str, Any] = {}

    def _ensure_dependencies(self) -> None:
        """延迟加载 tree-sitter 依赖"""

        if importlib.util.find_spec("tree_sitter") is None:
            raise SummarizationError(
                "tree-sitter not installed. Run: pip install tree-sitter",
                strategy=self.strategy,
            )

    def _get_language(self, lang_name: str) -> Any | None:
        """获取或加载语言模块

        Args:
            lang_name: 语言名称 (python, javascript, go)

        Returns:
            Language 对象
        """
        if lang_name in self._languages:
            return self._languages[lang_name]

        self._ensure_dependencies()
        from tree_sitter import Language

        try:
            if lang_name == "python":

                lang = Language(tspython.language())
            elif lang_name == "javascript":

                lang = Language(tsjs.language())
            elif lang_name == "go":

                lang = Language(tsgo.language())
            else:
                logger.warning(f"Language {lang_name} not supported yet")
                return None

            self._languages[lang_name] = lang
            return lang
        except ImportError:
            logger.debug(f"Language module for {lang_name} not installed")
            return None

    def _get_parser(self, lang_name: str) -> Any | None:
        """获取或创建解析器

        Args:
            lang_name: 语言名称

        Returns:
            Parser 对象
        """
        if lang_name in self._parsers:
            return self._parsers[lang_name]

        lang = self._get_language(lang_name)
        if lang is None:
            return None

        from tree_sitter import Parser

        parser = Parser(lang)
        self._parsers[lang_name] = parser
        return parser

    def _detect_language(self, content: str, content_type: str = "code") -> str | None:
        """检测代码语言

        Args:
            content: 代码内容
            content_type: 内容类型提示

        Returns:
            语言名称或 None
        """
        # 基于内容的启发式检测
        content_start = content[:500].strip()

        if content_start.startswith("def ") or content_start.startswith("class "):
            return "python"
        if content_start.startswith("function ") or content_start.startswith("class "):
            return "javascript"
        if content_start.startswith("func ") or content_start.startswith("package "):
            return "go"
        if "import " in content_start and "from " in content_start:
            return "python"
        if "package main" in content_start:
            return "go"

        # 默认尝试 python
        return "python"

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "code",
    ) -> str:
        """生成代码感知摘要

        Args:
            content: 原始代码
            max_tokens: 目标 token 数
            content_type: 内容类型 (code, json)

        Returns:
            压缩后的代码
        """
        if not content or len(content.strip()) < 100:
            return content

        # JSON 特殊处理
        if content_type == "json" or content.strip().startswith(("{", "[")):
            return self._compress_json(content, max_tokens)

        # 检测语言
        lang_name = self._detect_language(content, content_type)
        if lang_name is None:
            # Fallback: 简单的行数截断
            return self._simple_truncate(content, max_tokens)

        parser = self._get_parser(lang_name)
        if parser is None:
            return self._simple_truncate(content, max_tokens)

        try:
            tree = parser.parse(content.encode())
            root = tree.root_node

            compressed = self._compress_ast(
                content,
                root,
                lang_name,
                self.default_config,
            )

            # 如果还是太长，再截断
            if len(compressed) > max_tokens * 4:
                compressed = self._simple_truncate(compressed, max_tokens)

            return compressed

        except (RuntimeError, ValueError) as e:
            logger.warning(f"Tree-sitter parsing failed: {e}")
            return self._simple_truncate(content, max_tokens)

    def _compress_ast(
        self,
        content: str,
        root_node: Any,
        lang_name: str,
        config: CodeCompressionConfig,
    ) -> str:
        """压缩 AST

        Args:
            content: 原始内容
            root_node: AST 根节点
            lang_name: 语言名称
            config: 压缩配置

        Returns:
            压缩后的代码
        """
        result_lines = []

        signature_types = SIGNATURE_NODE_TYPES.get(lang_name, [])
        error_types = ERROR_NODE_TYPES.get(lang_name, [])

        def process_node(node: Any, depth: int = 0) -> bool:
            """递归处理节点"""
            if node.type in signature_types:
                # 这是一个签名节点，保留签名，折叠实现
                signature = self._extract_signature(content, node, config)
                if signature:
                    result_lines.append(signature)
                    result_lines.append("    ...")
                return True  # 已处理，不递归子节点

            if config.strategy == "error_paths" and node.type in error_types:
                # 错误处理路径，完整保留
                error_block = content[node.start_byte : node.end_byte]
                result_lines.append(error_block)
                return True

            # 递归处理子节点
            for child in node.children:
                process_node(child, depth + 1)

            return False

        # 处理根节点的子节点
        for child in root_node.children:
            process_node(child)

        return "\n".join(result_lines) if result_lines else content[:1000]

    def _extract_signature(
        self,
        content: str,
        node: Any,
        config: CodeCompressionConfig,
    ) -> str | None:
        """提取函数/类签名

        Args:
            content: 原始内容
            node: AST 节点
            config: 压缩配置

        Returns:
            签名字符串
        """
        # 获取节点文本
        node_text = content[node.start_byte : node.end_byte]
        lines = node_text.split("\n")

        # 提取第一行 (通常是签名)
        signature_lines = [lines[0]]

        # 如果配置保留文档字符串，尝试提取
        if config.keep_docstrings:
            for i, line in enumerate(lines[1:], 1):
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    # 收集文档字符串
                    docstring_lines = [line]
                    for j in range(i + 1, min(i + 10, len(lines))):
                        docstring_lines.append(lines[j])
                        if '"""' in lines[j] or "'''" in lines[j]:
                            break
                    signature_lines.extend(docstring_lines)
                    break
                elif stripped and not stripped.startswith("#"):
                    # 不是空行或注释，说明文档字符串结束
                    break

        return "\n".join(signature_lines)

    def _compress_json(self, content: str, max_tokens: int) -> str:
        """压缩 JSON 内容

        Args:
            content: JSON 字符串
            max_tokens: 目标 token 数

        Returns:
            压缩后的 JSON
        """
        import json

        try:
            data = json.loads(content)

            def truncate_obj(obj: Any, depth: int = 0) -> Any:
                """递归截断对象"""
                if depth > 3:
                    return "..."

                if isinstance(obj, dict):
                    truncated = {}
                    for i, (k, v) in enumerate(obj.items()):
                        if i >= 10:  # 最多保留 10 个键
                            truncated["..."] = "..."
                            break
                        truncated[k] = truncate_obj(v, depth + 1)
                    return truncated

                if isinstance(obj, list):
                    if len(obj) > 10:
                        return [truncate_obj(item, depth + 1) for item in obj[:10]] + ["..."]
                    return [truncate_obj(item, depth + 1) for item in obj]

                if isinstance(obj, str) and len(obj) > 200:
                    return obj[:200] + "..."

                return obj

            truncated = truncate_obj(data)
            return json.dumps(truncated, indent=2, ensure_ascii=False)

        except json.JSONDecodeError:
            return content[: max_tokens * 4]

    def _simple_truncate(self, content: str, max_tokens: int) -> str:
        """简单的行数截断

        Args:
            content: 原始内容
            max_tokens: 目标 token 数

        Returns:
            截断后的内容
        """
        lines = content.split("\n")
        max_lines = max(20, max_tokens // 4)  # 估算: 1行 ≈ 4 tokens

        if len(lines) <= max_lines:
            return content

        # 保留开头和结尾
        head_lines = int(max_lines * 0.7)
        tail_lines = max_lines - head_lines

        head = lines[:head_lines]
        tail = lines[-tail_lines:] if tail_lines > 0 else []

        return "\n".join([*head, "    // ... (truncated) ...", *tail])

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """估算输出 token 数

        代码结构化压缩通常保留 20-40% 的内容。

        Args:
            input_tokens: 输入 token 数

        Returns:
            预估输出 token 数
        """
        return int(input_tokens * 0.25)  # 更激进的压缩

    def is_available(self) -> bool:
        """检查 tree-sitter 及语言模块是否已安装"""
        try:
            from tree_sitter import Parser  # noqa: F401

            # Check if at least one language module is available
            try:

                return True
            except ImportError:
                pass

            try:

                return True
            except ImportError:
                pass

            try:

                return True
            except ImportError:
                pass

            return False
        except ImportError:
            return False
