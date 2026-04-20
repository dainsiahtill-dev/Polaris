"""ToolRegistry - 工具注册与管理

# -*- coding: utf-8 -*-

Blueprint: §8 ToolRegistry
把 "allowed_tool_names parser 白名单" 升级为 registry selection policy。
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from typing import Any

from polaris.kernelone.llm.embedding import KernelEmbeddingPort, get_default_embedding_port

from .contracts import AgentToolSpec

logger = logging.getLogger(__name__)

_EMBEDDING_CACHE_SIZE = 256


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 if vectors contain NaN/Inf or have zero norm.
    """
    if len(vec1) != len(vec2):
        return 0.0

    # Guard against NaN/Inf in input vectors (A16 fix)
    if any(math.isnan(x) or math.isinf(x) for x in vec1):
        return 0.0
    if any(math.isnan(x) or math.isinf(x) for x in vec2):
        return 0.0

    dot = sum(a * b for a, b in zip(vec1, vec2, strict=True))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    result = dot / (norm1 * norm2)
    # Guard against NaN result from edge cases
    if math.isnan(result) or math.isinf(result):
        return 0.0

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Builtin tool definitions bootstrap
# ──────────────────────────────────────────────────────────────────────────────

_STANDARD_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "tool_id": "builtin:read_file",
        "name": "read_file",
        "description": "读取工作区中的文本文件内容（UTF-8）。",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标文件相对路径"},
                "path": {"type": "string", "description": "目标文件相对路径（别名）"},
                "max_bytes": {
                    "type": "integer",
                    "description": "最大读取字节数（防止超大文件）",
                    "default": 200000,
                },
            },
            "required": [],
        },
        "source": "builtin",
        "tags": ("filesystem", "read", "io"),
    },
    {
        "tool_id": "builtin:write_file",
        "name": "write_file",
        "description": "在工作区中写入文件内容（UTF-8）。",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标文件相对路径"},
                "path": {"type": "string", "description": "目标文件相对路径（别名）"},
                "content": {"type": "string", "description": "要写入的文本内容"},
                "encoding": {
                    "type": "string",
                    "description": "文本编码（仅支持 utf-8）",
                    "default": "utf-8",
                },
            },
            "required": ["content"],
        },
        "source": "builtin",
        "tags": ("filesystem", "write", "io"),
    },
    {
        "tool_id": "builtin:execute_command",
        "name": "execute_command",
        "description": "在工作区执行受限命令。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {
                    "type": "integer",
                    "description": "命令超时时间（秒）",
                    "default": 30,
                },
                "shell": {
                    "type": "boolean",
                    "description": "是否使用 shell 执行",
                    "default": False,
                },
            },
            "required": ["command"],
        },
        "source": "builtin",
        "tags": ("execution", "shell", "process"),
    },
    {
        "tool_id": "builtin:search_code",
        "name": "search_code",
        "description": "代码搜索工具，兼容常见搜索参数别名。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键字"},
                "file_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "文件模式过滤",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数",
                    "default": 50,
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "是否区分大小写",
                    "default": False,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "结果上下文行数",
                    "default": 0,
                },
                "path": {"type": "string", "description": "搜索路径（相对工作区）"},
            },
            "required": ["query"],
        },
        "source": "builtin",
        "tags": ("search", "code", "grep"),
    },
    {
        "tool_id": "builtin:glob",
        "name": "glob",
        "description": "使用 glob 模式匹配文件路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 匹配模式，如 '*.py' 或 'src/**/*.ts'"},
                "path": {"type": "string", "description": "搜索起始路径（相对工作区）", "default": "."},
                "recursive": {"type": "boolean", "description": "是否递归搜索", "default": False},
                "include_hidden": {"type": "boolean", "description": "是否包含隐藏文件", "default": False},
                "max_results": {"type": "integer", "description": "最大返回结果数", "default": 200},
            },
            "required": ["pattern"],
        },
        "source": "builtin",
        "tags": ("filesystem", "glob", "io"),
    },
    {
        "tool_id": "builtin:list_directory",
        "name": "list_directory",
        "description": "列出目录中的文件和子目录。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径（相对工作区）", "default": "."},
                "recursive": {"type": "boolean", "description": "是否递归列出", "default": False},
                "include_hidden": {"type": "boolean", "description": "是否包含隐藏文件", "default": False},
                "max_entries": {"type": "integer", "description": "最大返回条目数", "default": 200},
            },
            "required": [],
        },
        "source": "builtin",
        "tags": ("filesystem", "directory", "io"),
    },
    {
        "tool_id": "builtin:file_exists",
        "name": "file_exists",
        "description": "检查文件或目录是否存在。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件或目录路径（相对工作区）"},
            },
            "required": ["path"],
        },
        "source": "builtin",
        "tags": ("filesystem", "check", "io"),
    },
    {
        "tool_id": "builtin:search_replace",
        "name": "search_replace",
        "description": "在单个文件中搜索并替换文本内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标文件路径（相对工作区）"},
                "search": {"type": "string", "description": "要搜索的文本"},
                "replace": {"type": "string", "description": "替换后的文本"},
                "regex": {"type": "boolean", "description": "是否使用正则表达式匹配", "default": False},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配项", "default": False},
            },
            "required": ["file", "search", "replace"],
        },
        "source": "builtin",
        "tags": ("filesystem", "edit", "io"),
    },
    {
        "tool_id": "builtin:edit_file",
        "name": "edit_file",
        "description": "编辑文件内容，支持行区间替换或文本搜索替换。",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标文件路径（相对工作区）"},
                "start_line": {"type": "integer", "description": "起始行号（行区间模式）"},
                "end_line": {"type": "integer", "description": "结束行号（行区间模式）"},
                "content": {"type": "string", "description": "新内容（行区间模式）或替换内容"},
                "search": {"type": "string", "description": "要搜索的文本（替换模式）"},
                "replace": {"type": "string", "description": "替换后的文本（替换模式）"},
                "regex": {"type": "boolean", "description": "是否使用正则表达式", "default": False},
            },
            "required": ["file"],
        },
        "source": "builtin",
        "tags": ("filesystem", "edit", "io"),
    },
    {
        "tool_id": "builtin:append_to_file",
        "name": "append_to_file",
        "description": "在文件末尾追加内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "目标文件路径（相对工作区）"},
                "content": {"type": "string", "description": "要追加的内容"},
                "ensure_newline": {
                    "type": "boolean",
                    "description": "追加前确保文件以换行符结尾",
                    "default": True,
                },
                "create_if_missing": {
                    "type": "boolean",
                    "description": "如果文件不存在则创建",
                    "default": True,
                },
            },
            "required": ["file", "content"],
        },
        "source": "builtin",
        "tags": ("filesystem", "write", "io"),
    },
]


def _def_to_spec(def_dict: dict[str, Any]) -> AgentToolSpec:
    """将 builtin tool 定义字典转换为 AgentToolSpec。"""
    params = def_dict.get("parameters", {})
    return AgentToolSpec(
        tool_id=def_dict["tool_id"],
        name=def_dict["name"],
        source=def_dict.get("source", "builtin"),
        description=def_dict.get("description", ""),
        parameters=params,
        enabled=True,
        tags=tuple(def_dict.get("tags", ())),
    )


class ToolRegistry:
    """统一工具注册表

    策略：
    1. 开局只暴露少量核心工具
    2. 更多工具通过 search/load 按需进入当前 turn
    3. allowed_tool_names 升级为 registry selection policy

    与 RoleToolGateway 的区别：
    - RoleToolGateway: 角色级别的工具授权（黑名单/白名单/类别）
    - ToolRegistry:   工具级别的元信息注册与按需加载
    """

    def __init__(self) -> None:
        self._tools: dict[str, AgentToolSpec] = {}
        self._embedding_cache: dict[str, list[float]] = {}
        self._embedding_lock = threading.Lock()
        self._load_core_tools()

    def _load_core_tools(self) -> None:
        """加载核心内置工具（Phase 3 实现）。

        从本地标准工具定义注册所有 builtin 工具。
        工具按 source="builtin" 标记，供 selection_policy 过滤。
        """
        for def_dict in _STANDARD_TOOL_DEFINITIONS:
            spec = _def_to_spec(def_dict)
            self._tools[spec.tool_id] = spec
            logger.debug("Registered builtin tool: %s", spec.name)

    def list_core_tools(
        self,
        context: Any | None = None,  # TODO: ConversationState
    ) -> list[AgentToolSpec]:
        """列出当前可用的核心工具

        Args:
            context: 可选的对话上下文（用于动态过滤）

        Returns:
            当前可用的 ToolSpec 列表
        """
        return [t for t in self._tools.values() if t.enabled]

    def search_tools(
        self,
        query: str,
        limit: int = 10,
    ) -> list[AgentToolSpec]:
        """搜索工具（Phase 3 实现）

        通过工具名称/描述/tag 搜索可用工具。
        优先使用 embedding-based 语义搜索，失败时回退到关键词匹配。

        Args:
            query: 搜索关键词
            limit: 最大返回结果数（默认 10）

        Returns:
            匹配的工具规格列表（按相关性排序）
        """
        if not query or not query.strip():
            return []

        query_lower = query.strip().lower()
        tools = [t for t in self._tools.values() if t.enabled]

        # 尝试语义搜索
        try:
            query_emb = self._get_embedding(query_lower)
            if query_emb:
                scored: list[tuple[AgentToolSpec, float]] = []
                for tool in tools:
                    tool_text = self._tool_searchable_text(tool)
                    tool_emb = self._get_embedding(tool_text)
                    if tool_emb:
                        sim = _cosine_similarity(query_emb, tool_emb)
                        scored.append((tool, sim))

                # 按相关性排序
                scored.sort(key=lambda x: x[1], reverse=True)
                return [t for t, _ in scored[:limit]]
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Semantic search failed, falling back to keyword: %s", exc)

        # 回退到关键词匹配
        return self._keyword_search(tools, query_lower, limit)

    def _tool_searchable_text(self, tool: AgentToolSpec) -> str:
        """构建工具的可搜索文本（用于 embedding）"""
        parts = [tool.name, tool.description]
        if tool.tags:
            parts.append(" ".join(tool.tags))
        return " | ".join(parts)

    def _get_embedding(self, text: str) -> list[float] | None:
        """获取文本 embedding（带缓存）"""
        cache_key = hashlib.sha1(text.encode("utf-8")).hexdigest()

        with self._embedding_lock:
            cached = self._embedding_cache.get(cache_key)
            if cached is not None:
                return list(cached)

            try:
                port: KernelEmbeddingPort = get_default_embedding_port()
                emb = port.get_embedding(text)
                if emb:
                    self._embedding_cache[cache_key] = list(emb)
                    if len(self._embedding_cache) > _EMBEDDING_CACHE_SIZE:
                        # 简单策略：清除最早的 25%
                        keys_to_remove = list(self._embedding_cache.keys())[: _EMBEDDING_CACHE_SIZE // 4]
                        for k in keys_to_remove:
                            del self._embedding_cache[k]
                    return list(emb)
            except (RuntimeError, ValueError, TypeError) as exc:
                # Embedding port 未设置 or other errors
                logger.debug("Embedding lookup failed: %s", exc)

        return None

    def _keyword_search(
        self,
        tools: list[AgentToolSpec],
        query: str,
        limit: int,
    ) -> list[AgentToolSpec]:
        """基于关键词的简单搜索（回退方案）"""
        query_terms = set(query.split())

        scored: list[tuple[AgentToolSpec, int]] = []

        for tool in tools:
            score = 0
            name_lower = tool.name.lower()
            desc_lower = tool.description.lower()
            tags_str = " ".join(tool.tags).lower() if tool.tags else ""

            # 精确名称匹配优先
            if query in name_lower:
                score += 10
                if query == name_lower:
                    score += 20  # 完全匹配

            # 查询词在各处出现
            for term in query_terms:
                if term in name_lower:
                    score += 5
                if term in desc_lower:
                    score += 2
                if term in tags_str:
                    score += 3

            if score > 0:
                scored.append((tool, score))

        # 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)
        return [t for t, _ in scored[:limit]]

    def load_tools(
        self,
        tool_refs: list[str],
    ) -> list[AgentToolSpec]:
        """按引用加载工具（Phase 3 实现）。

        支持的引用格式：
        - builtin 工具名（直接返回已注册的 ToolSpec）
        - MCP URI 格式：mcp://<server>/<tool>（Phase 4 实现）

        Args:
            tool_refs: 工具引用列表（名称或 MCP URI）

        Returns:
            成功加载的工具规格列表（不含禁用的工具）
        """
        loaded: list[AgentToolSpec] = []

        for ref in tool_refs:
            if not isinstance(ref, str) or not ref.strip():
                continue

            ref = ref.strip()

            # MCP URI 格式: mcp://<server>/<tool>
            if ref.startswith("mcp://"):
                # Phase 4: 从 MCP server 动态获取
                # 暂时跳过，记录日志
                logger.debug("MCP tool loading deferred to Phase 4: %s", ref)
                continue

            # builtin 工具名：大小写不敏感匹配
            ref_lower = ref.lower()
            matched: AgentToolSpec | None = None

            # 先按 tool_id 精确匹配
            for spec in self._tools.values():
                if spec.tool_id.lower() == ref_lower:
                    matched = spec
                    break

            # 再按 name 精确匹配
            if matched is None:
                for spec in self._tools.values():
                    if spec.name.lower() == ref_lower:
                        matched = spec
                        break

            if matched is not None:
                if matched.enabled:
                    loaded.append(matched)
                else:
                    logger.debug("Tool skipped (disabled): %s", ref)
            else:
                logger.debug("Tool not found in registry: %s", ref)

        return loaded

    def get_tool_schema(
        self,
        tool_id: str,
    ) -> dict[str, Any] | None:
        """获取工具 schema

        Args:
            tool_id: 工具 ID

        Returns:
            工具 schema dict 或 None（未找到）
        """
        spec = self._tools.get(tool_id)
        return spec.schema_dict if spec else None

    def register_tool(self, spec: AgentToolSpec) -> None:
        """注册工具（供 infrastructure adapter 调用）

        Args:
            spec: 工具规格
        """
        self._tools[spec.tool_id] = spec
        logger.debug("Tool registered: %s (%s)", spec.name, spec.source)

    def selection_policy(
        self,
        allowed_tool_names: list[str] | None = None,
    ) -> list[str]:
        """从 allowed_tool_names 构建选择策略

        这是从 RoleToolGateway 的 whitelist/allowed_tool_names
        到 registry-based selection 的桥接方法。

        Args:
            allowed_tool_names: 原始允许工具名列表

        Returns:
            经 registry 过滤后的工具名列表
        """
        if not allowed_tool_names:
            # 空白名单 = 禁止所有工具（与 RoleToolGateway 策略一致）
            return []

        allowed_lower = {str(n).strip().lower() for n in allowed_tool_names}
        return [spec.name for spec in self._tools.values() if spec.enabled and spec.name.lower() in allowed_lower]

    def tool_names(self) -> list[str]:
        """返回所有注册的工具名称（不含禁用项）"""
        return [spec.name for spec in self.list_core_tools()]
