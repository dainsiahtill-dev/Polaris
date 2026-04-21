"""
ToolSpecRegistry - 单一权威源头 for LLM Tool定义

本模块作为Polaris LLM工具调用的单一事实来源（Single Source of Truth）。
统一了之前分散在以下位置的Tool定义:
- definitions.py (ToolDefinition类 - LLM-facing schemas)
- contracts.py (_TOOL_SPECS dict - 执行契约和别名)

用法:
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    # 获取工具规格
    spec = ToolSpecRegistry.get("repo_read_head")

    # 生成LLM schemas
    schemas = ToolSpecRegistry.generate_llm_schemas()
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# ToolSpec - 单一权威Tool定义
# =============================================================================


@dataclass(frozen=True)
class ToolSpec:
    """
    单一权威Tool定义

    Attributes:
        canonical_name: 唯一正式名称
        aliases: 所有别名
        description: 工具描述
        parameters: JSON Schema格式的参数定义
        categories: 工具分类 (read/write/exec)
        dangerous_patterns: 危险命令检测正则 (可选)
        handler_module: handler模块路径
        handler_function: handler函数名
        response_format_hint: 响应格式提示
    """

    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    parameters: dict[str, Any]
    categories: tuple[str, ...]
    dangerous_patterns: tuple[str, ...] = field(default_factory=lambda: ())
    handler_module: str = ""
    handler_function: str = ""
    response_format_hint: str = ""

    def to_openai_function(self) -> dict[str, Any]:
        """转换为OpenAI function calling格式"""
        return {
            "type": "function",
            "function": {
                "name": self.canonical_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_tool(self) -> dict[str, Any]:
        """转换为Anthropic native tool格式"""
        schema = self.parameters.copy()
        schema.pop("required", [])
        return {
            "name": self.canonical_name,
            "description": self.description,
            "input_schema": schema,
        }

    def is_read_tool(self) -> bool:
        """是否读取类工具"""
        return "read" in self.categories

    def is_write_tool(self) -> bool:
        """是否写入类工具"""
        return "write" in self.categories

    def is_exec_tool(self) -> bool:
        """是否执行类工具"""
        return "exec" in self.categories


# =============================================================================
# ToolSpecRegistry - 单一Source of Truth
# =============================================================================


class ToolSpecRegistry:
    """
    单一Source of Truth for所有Tool定义

    这个类是一个模块级别的注册表,维护所有ToolSpec实例。
    别名自动注册 - 通过任何别名或canonical name都可以查找。

    使用ContextVar实现线程安全和异步上下文隔离。
    """

    _registry_var: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar("tool_spec_registry", default=None)
    _specs_var: ContextVar[dict[str, ToolSpec] | None] = ContextVar("tool_spec_specs", default=None)
    _canonical_names_var: ContextVar[set[str] | None] = ContextVar("tool_spec_canonical_names", default=None)

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Lazily populate built-in tool specs for the current execution context."""
        registry = cls._registry_var.get()
        specs = cls._specs_var.get()
        canonical_names = cls._canonical_names_var.get()
        if registry is not None and specs is not None and canonical_names is not None:
            return

        initialized_registry: dict[str, dict[str, Any]] = {}
        initialized_specs: dict[str, ToolSpec] = {}
        initialized_canonical_names: set[str] = set()

        for tool_name, spec_dict in _BUILTIN_REGISTRY.items():
            initialized_registry[tool_name] = dict(spec_dict)
            spec = _build_tool_spec_from_dict(tool_name, spec_dict)
            initialized_specs[tool_name] = spec
            initialized_canonical_names.add(tool_name)

        for tool_name in _BUILTIN_REGISTRY:
            spec = initialized_specs[tool_name]
            for alias in spec.aliases:
                alias_lower = alias.lower()
                if alias_lower in _BUILTIN_REGISTRY:
                    continue
                initialized_specs[alias_lower] = spec

        cls._registry_var.set(initialized_registry)
        cls._specs_var.set(initialized_specs)
        cls._canonical_names_var.set(initialized_canonical_names)

    @classmethod
    def _get_registry(cls) -> dict[str, dict[str, Any]]:
        cls._ensure_initialized()
        val = cls._registry_var.get()
        if val is None:
            raise RuntimeError("ToolSpecRegistry registry failed to initialize")
        return val

    @classmethod
    def _get_specs(cls) -> dict[str, ToolSpec]:
        cls._ensure_initialized()
        val = cls._specs_var.get()
        if val is None:
            raise RuntimeError("ToolSpecRegistry specs failed to initialize")
        return val

    @classmethod
    def _get_canonical_names(cls) -> set[str]:
        cls._ensure_initialized()
        val = cls._canonical_names_var.get()
        if val is None:
            raise RuntimeError("ToolSpecRegistry canonical names failed to initialize")
        return val

    @classmethod
    def register(cls, arg1: str | ToolSpec, arg2: dict[str, Any] | None = None, *, strict: bool = False) -> None:
        """注册工具规格

        支持两种调用方式:
        - register(name: str, spec: dict[str, Any])  # 新SSOT API
        - register(spec: ToolSpec, *, strict=False)   # 旧API（兼容）

        Args:
            arg1: 工具名或ToolSpec实例
            arg2: 工具规格字典（仅新API使用）
            strict: 如果为True,已存在的工具或别名会抛异常

        Raises:
            ValueError: 如果strict=True且工具或别名已存在
            TypeError: 如果参数不匹配任何支持的调用方式
        """
        if isinstance(arg1, str) and arg2 is not None:
            name = arg1
            spec_dict = arg2
            cls._get_registry()[name] = dict(spec_dict)
            tool_spec = _build_tool_spec_from_dict(name, spec_dict)
            cls._register_tool_spec(tool_spec, strict=strict)
            return

        if isinstance(arg1, ToolSpec):
            spec = arg1
            cls._register_tool_spec(spec, strict=strict)
            cls._get_registry()[spec.canonical_name] = _tool_spec_to_dict(spec)
            return

        raise TypeError("register() expects either (name: str, spec: dict) or (spec: ToolSpec, *, strict=False)")

    @classmethod
    def _register_tool_spec(cls, spec: ToolSpec, *, strict: bool = False) -> None:
        """内部方法: 注册ToolSpec到_specs和_canonical_names。"""
        canonical_names = cls._get_canonical_names()
        specs = cls._get_specs()
        if spec.canonical_name in canonical_names:
            if strict:
                raise ValueError(f"Duplicate tool: {spec.canonical_name}")
            return
        specs[spec.canonical_name] = spec
        canonical_names.add(spec.canonical_name)
        for alias in spec.aliases:
            if alias in specs:
                if strict:
                    raise ValueError(f"Duplicate alias '{alias}' for tool '{spec.canonical_name}'")
                specs[alias] = spec
            else:
                specs[alias] = spec

    @classmethod
    def register_alias_only(cls, canonical_name: str, alias: str) -> None:
        """仅注册别名（不注册规范名）。

        用于两遍注册模式：第一遍注册所有别名，第二遍注册规范名。
        别名注册使用 last-registered-wins 策略。
        """
        specs = cls._get_specs()
        if alias in specs:
            existing = specs[alias]
            specs[alias] = existing
        else:
            spec = specs.get(canonical_name)
            if spec:
                specs[alias] = spec

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        """获取工具规格(支持别名查找)"""
        return cls._get_specs().get(name)

    @classmethod
    def get_canonical(cls, name: str) -> str:
        """获取canonical name"""
        spec = cls._get_specs().get(name)
        return spec.canonical_name if spec else name

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """检查工具是否已注册"""
        return name in cls._get_specs()

    @classmethod
    def is_canonical(cls, name: str) -> bool:
        """检查是否为canonical name"""
        return name in cls._get_canonical_names()

    @classmethod
    def get_all_canonical_names(cls) -> list[str]:
        """获取所有canonical names列表"""
        return sorted(cls._get_canonical_names())

    @classmethod
    def list_names(cls) -> list[str]:
        """获取所有已注册的工具名称列表（基于_registry）。"""
        return sorted(cls._get_registry().keys())

    @classmethod
    def get_all_specs(cls) -> dict[str, dict[str, Any]]:
        """获取所有已注册工具的原始规格字典（基于_registry）。"""
        return dict(cls._get_registry())

    @classmethod
    def get_all_tools(cls) -> list[ToolSpec]:
        """获取所有唯一的ToolSpec列表(去重别名)"""
        seen: set[str] = set()
        result: list[ToolSpec] = []
        for spec in cls._get_specs().values():
            if spec.canonical_name not in seen:
                seen.add(spec.canonical_name)
                result.append(spec)
        return sorted(result, key=lambda s: s.canonical_name)

    @classmethod
    def generate_llm_schemas(
        cls,
        format: str = "openai",
        categories: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """生成LLM-facing的tool schemas"""
        specs = cls.get_all_tools()
        if categories:
            specs = [s for s in specs if any(c in s.categories for c in categories)]

        if format == "openai":
            return [spec.to_openai_function() for spec in specs]
        elif format == "anthropic":
            return [spec.to_anthropic_tool() for spec in specs]
        else:
            raise ValueError(f"Unknown format: {format}")

    @classmethod
    def generate_handler_registry(cls) -> dict[str, tuple[str, str]]:
        """生成handler映射表"""
        registry: dict[str, tuple[str, str]] = {}
        for spec in cls.get_all_tools():
            if spec.handler_module and spec.handler_function:
                registry[spec.canonical_name] = (spec.handler_module, spec.handler_function)
        return registry

    @classmethod
    def get_by_category(cls, category: str) -> list[ToolSpec]:
        """按分类获取工具"""
        return [s for s in cls.get_all_tools() if category in s.categories]

    @classmethod
    def get_read_tools(cls) -> list[ToolSpec]:
        """获取所有读取类工具"""
        return cls.get_by_category("read")

    @classmethod
    def get_write_tools(cls) -> list[ToolSpec]:
        """获取所有写入类工具"""
        return cls.get_by_category("write")

    @classmethod
    def get_exec_tools(cls) -> list[ToolSpec]:
        """获取所有执行类工具"""
        return cls.get_by_category("exec")

    @classmethod
    def clear(cls) -> None:
        """清空注册表(主要用于测试)"""
        cls._get_registry().clear()
        cls._get_specs().clear()
        cls._get_canonical_names().clear()

    @classmethod
    def reset_for_testing(cls) -> None:
        """重置注册表用于测试隔离(别名: clear)"""
        cls.clear()

    @classmethod
    def count(cls) -> int:
        """返回canonical tools数量"""
        return len(cls._get_canonical_names())


# =============================================================================
# Built-in tool specs (migrated from contracts.py)
# =============================================================================


_BUILTIN_REGISTRY: dict[str, dict[str, Any]] = {
    "repo_tree": {
        "category": "read",
        "description": "List directory tree within the workspace. Shows files and folders with optional depth limiting.",
        "aliases": ["repo_ls", "repo_list_dir", "list_dir", "ls_tree", "list_directory"],
        "arg_aliases": {
            "root": "path",
            "dir": "path",
            "directory": "path",
            "max": "max_entries",
            "limit": "max_entries",
        },
        "arguments": [
            {"name": "path", "type": "string", "required": False, "default": "."},
            {"name": "depth", "type": "integer", "required": False},
            {"name": "max_entries", "type": "integer", "required": False},
        ],
        "response_format_hint": "Tree-structured text showing files and directories",
        "required_any": [],
        "required_doc": "args.path optional (default '.')",
    },
    "repo_rg": {
        "category": "read",
        "description": "PRIMARY code search tool for Polaris. Search for pattern matches across files using ripgrep. Returns file:line:snippet format showing WHERE matches occur.\n\nCRITICAL USAGE NOTES:\n- This tool shows LOCATIONS (file:line) and limited snippets, NOT complete code definitions\n- For complete code, use repo_read_head or read_file AFTER finding the location\n- context_lines provides surrounding lines for context only (max 5), not full definitions\n- Do NOT use repo_rg to read entire functions, classes, or files — use read_file instead",
        "aliases": [
            "repo_search",
            "repo_grep",
            "find",
            "rg",
            "repo_rg_direct",
            "ripgrep",
            "search",
            "code_search",
            "search_code",
            "grep",
        ],
        "arg_aliases": {
            "query": "pattern",
            "text": "pattern",
            "search": "pattern",
            "keyword": "pattern",
            "q": "pattern",
            "file": "path",
            "file_path": "path",
            "filepath": "path",
            "dir": "path",
            "directory": "path",
            "max": "max_results",
            "limit": "max_results",
            "max_results": "max_results",
            "n": "max_results",
            "g": "glob",
            "case_sensitive": "case_sensitive",
            "case": "case_sensitive",
            "sensitive": "case_sensitive",
            "context": "context_lines",
            "c": "context_lines",
            "C": "context_lines",
        },
        "arguments": [
            {
                "name": "pattern",
                "type": "string",
                "required": True,
                "min_length": 1,
                "max_length": 1000,
                "pattern": r"^[^\x00]+$",
            },
            {"name": "paths", "type": "array", "required": False, "min_items": 1, "max_items": 100},
            {"name": "path", "type": "string", "required": False},
            {
                "name": "max_results",
                "type": "integer",
                "required": False,
                "default": 50,
                "minimum": 1,
                "maximum": 10000,
            },
            {"name": "glob", "type": "string", "required": False},
            {"name": "context_lines", "type": "integer", "required": False, "default": 0, "minimum": 0, "maximum": 100},
            {"name": "case_sensitive", "type": "boolean", "required": False, "default": False},
        ],
        "response_format_hint": "Lines matching pattern with file:line:content format",
        "required_any": [("pattern",)],
        "required_doc": "args.pattern required",
    },
    "repo_read_around": {
        "category": "read",
        "description": "Read a slice of a file centered around a target line with configurable radius. Ideal for examining specific code locations with surrounding context.",
        "aliases": ["read_around", "repo_context"],
        "arg_aliases": {
            "file_path": "file",
            "path": "file",
            "around": "line",
            "around_line": "line",
            "center_line": "line",
            "line_number": "line",
            "window": "radius",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "line", "type": "integer", "required": True, "minimum": 1},
            {"name": "radius", "type": "integer", "required": False, "default": 5, "minimum": 1, "maximum": 100},
            {"name": "start", "type": "integer", "required": False, "minimum": 1},
            {"name": "end", "type": "integer", "required": False, "minimum": 1},
        ],
        "response_format_hint": "Lines with line numbers around the target line, formatted as 'N: content'",
        "required_any": [("file",), ("line", "start")],
        "required_doc": "args.file + (args.line or args.start/end)",
    },
    "update_session_state": {
        "category": "write",
        "description": "Update the working memory state at the end of a turn to track progress, findings, and plan next steps.",
        "aliases": ["patch_session", "update_working_memory"],
        "arg_aliases": {},
        "arguments": [
            {
                "name": "task_progress",
                "type": "string",
                "required": True,
                "enum": ["exploring", "investigating", "implementing", "verifying", "done"],
            },
            {"name": "confidence", "type": "string", "required": True, "enum": ["hypothesis", "likely", "confirmed"]},
            {"name": "action_taken", "type": "string", "required": True},
            {"name": "error_summary", "type": "string", "required": False},
            {"name": "suspected_files", "type": "array", "items": {"type": "string"}, "required": False},
            {"name": "patched_files", "type": "array", "items": {"type": "string"}, "required": False},
            {"name": "verified_results", "type": "array", "items": {"type": "string"}, "required": False},
            {"name": "pending_files", "type": "array", "items": {"type": "string"}, "required": False},
            {"name": "superseded", "type": "boolean", "required": False},
        ],
        "response_format_hint": "Session state updated successfully",
        "required_any": [("task_progress",), ("confidence",), ("action_taken",)],
        "required_doc": "args.task_progress + args.confidence + args.action_taken required",
    },
    "repo_read_slice": {
        "category": "read",
        "description": "Read a precise line range [start, end] from a file. Canonical tool for targeted code inspection - use this instead of full file reads.",
        "aliases": ["read_slice", "repo_slice"],
        "arg_aliases": {
            "file_path": "file",
            "path": "file",
            "start_line": "start",
            "end_line": "end",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "start", "type": "integer", "required": True, "minimum": 1},
            {"name": "end", "type": "integer", "required": True, "minimum": 1},
        ],
        "response_format_hint": "Lines with line numbers in range [start, end], formatted as 'N: content'",
        "required_any": [("file",), ("start",), ("end",)],
        "required_doc": "args.file + args.start + args.end",
    },
    "repo_read_head": {
        "category": "read",
        "description": "Read the first N lines from a file. Fast for getting file headers, imports, or class/function signatures.",
        "aliases": ["read_head", "repo_head"],
        "arg_aliases": {
            "file_path": "file",
            "filepath": "file",
            "count": "n",
            "lines": "n",
            "max_lines": "n",
            "limit": "n",
            "max_bytes": "n",
            "first_n": "n",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "n", "type": "integer", "required": False, "default": 50, "minimum": 1, "maximum": 10000},
        ],
        "response_format_hint": "First N lines with line numbers, formatted as 'N: content'",
        "required_any": [("file",)],
        "required_doc": "args.file required",
    },
    "repo_read_tail": {
        "category": "read",
        "description": "Read the last N lines from a file. Useful for log files, test results, or end-of-file examination.",
        "aliases": ["read_tail", "repo_tail"],
        "arg_aliases": {
            "file_path": "file",
            "count": "n",
            "lines": "n",
            "max_lines": "n",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "n", "type": "integer", "required": False, "default": 50, "minimum": 1, "maximum": 10000},
        ],
        "response_format_hint": "Last N lines with line numbers, formatted as 'N: content'",
        "required_any": [("file",)],
        "required_doc": "args.file required",
    },
    "repo_diff": {
        "category": "read",
        "description": "Show uncommitted changes in the repository using git diff.",
        "aliases": ["git_diff", "diff"],
        "arg_aliases": {"mode": "mode", "stat": "stat"},
        "arguments": [
            {"name": "stat", "type": "boolean", "required": False},
            {"name": "mode", "type": "string", "required": False},
        ],
        "response_format_hint": "Git diff output with +/- line markers",
        "required_any": [],
        "required_doc": "no required args",
    },
    "repo_map": {
        "category": "read",
        "description": "Build a code map of the repository showing file skeletons with top-level class and function definitions.",
        "aliases": ["code_map", "repo_index_map"],
        "arg_aliases": {
            "path": "root",
            "dir": "root",
            "max": "max_files",
            "lang": "languages",
            "per_file": "per_file_lines",
        },
        "arguments": [
            {"name": "root", "type": "string", "required": False, "default": "."},
            {"name": "max_files", "type": "integer", "required": False, "default": 200},
            {"name": "languages", "type": "array", "required": False},
            {"name": "per_file_lines", "type": "integer", "required": False, "default": 12},
        ],
        "response_format_hint": "Indented file paths and symbol entries with line ranges",
        "required_any": [],
        "required_doc": "args.root optional (default '.')",
    },
    "repo_symbols_index": {
        "category": "read",
        "description": "Index and list all top-level symbols (classes, functions) across matched files using tree-sitter.",
        "aliases": ["symbols_index", "repo_symbol_index"],
        "arg_aliases": {},
        "arguments": [
            {"name": "paths", "type": "array", "required": False, "default": ["."]},
            {"name": "max_results", "type": "integer", "required": False, "default": 500},
            {"name": "glob", "type": "string", "required": False},
        ],
        "response_format_hint": "Symbol entries with kind, name, and file:line location",
        "required_any": [],
        "required_doc": "no required args",
    },
    "treesitter_find_symbol": {
        "category": "read",
        "description": "Find the exact location of a symbol (function, class, method) in a file using tree-sitter AST analysis.",
        "aliases": ["ts_find", "find_symbol", "repo_ts_find"],
        "arg_aliases": {
            "file_path": "file",
            "lang": "language",
            "name": "symbol",
        },
        "arguments": [
            {"name": "language", "type": "string", "required": True},
            {"name": "file", "type": "string", "required": True},
            {"name": "symbol", "type": "string", "required": True},
            {"name": "kind", "type": "string", "required": False},
            {"name": "max_results", "type": "integer", "required": False, "default": 10},
        ],
        "response_format_hint": "Symbol definition locations with file, line range, and AST node type",
        "required_any": [("language",), ("file",), ("symbol",)],
        "required_doc": "args.language + args.file + args.symbol",
    },
    "skill_manifest": {
        "category": "read",
        "description": "List all available skills/agents in the workspace with their descriptions.",
        "aliases": ["skills_manifest", "list_skills"],
        "arg_aliases": {"role": "role"},
        "arguments": [
            {"name": "role", "type": "string", "required": False},
        ],
        "response_format_hint": "List of skill/agent names with descriptions",
        "required_any": [],
        "required_doc": "args.role optional",
    },
    "load_skill": {
        "category": "read",
        "description": "Load and return the content of a named skill definition.",
        "aliases": ["skill_load", "get_skill"],
        "arg_aliases": {"skill": "name", "skill_name": "name"},
        "arguments": [
            {"name": "name", "type": "string", "required": True},
        ],
        "response_format_hint": "Skill content including name, description, and tool definitions",
        "required_any": [("name",)],
        "required_doc": "args.name required",
    },
    "background_run": {
        "category": "exec",
        "description": "Start a long-running command in the background and return a task ID.",
        "aliases": ["bg_run", "background_start"],
        "arg_aliases": {
            "cmd": "command",
            "working_dir": "cwd",
            "workdir": "cwd",
            "max_seconds": "timeout",
            "timeout_seconds": "timeout",
        },
        "arguments": [
            {"name": "command", "type": "string", "required": True},
            {"name": "cwd", "type": "string", "required": False, "default": "."},
            {"name": "timeout", "type": "integer", "required": False, "default": 300},
        ],
        "response_format_hint": "Task ID and status of the started background process",
        "required_any": [("command",)],
        "required_doc": "args.command required; args.timeout <= 3600",
    },
    "background_check": {
        "category": "read",
        "description": "Check the status of a previously started background task by its task ID.",
        "aliases": ["bg_check", "background_status"],
        "arg_aliases": {"id": "task_id", "task": "task_id"},
        "arguments": [
            {"name": "task_id", "type": "string", "required": True},
        ],
        "response_format_hint": "Task status, output excerpt, and exit code if completed",
        "required_any": [("task_id",)],
        "required_doc": "args.task_id required",
    },
    "background_list": {
        "category": "read",
        "description": "List all currently running background tasks.",
        "aliases": ["bg_list", "background_tasks"],
        "arg_aliases": {"status": "status"},
        "arguments": [
            {"name": "status", "type": "string", "required": False},
        ],
        "response_format_hint": "List of task IDs with their status and metadata",
        "required_any": [],
        "required_doc": "args.status optional",
    },
    "todo_read": {
        "category": "read",
        "description": "Read the current todo list with all task items and their statuses.",
        "aliases": ["todo_list", "todo_get"],
        "arg_aliases": {},
        "arguments": [],
        "response_format_hint": "List of todo items with status, priority, and blocking information",
        "required_any": [],
        "required_doc": "no args required",
    },
    "todo_write": {
        "category": "exec",
        "description": "Write or update the entire todo list. Replaces all existing items.",
        "aliases": ["todo_update", "todo_set"],
        "arg_aliases": {"tasks": "items"},
        "arguments": [
            {"name": "items", "type": "array", "required": True},
        ],
        "response_format_hint": "Confirmation of written items with status",
        "required_any": [("items",)],
        "required_doc": "args.items required; max 20 items; max 1 in_progress",
    },
    "background_cancel": {
        "category": "exec",
        "description": "Cancel a running background task by its task ID.",
        "aliases": ["bg_cancel", "cancel_task"],
        "arg_aliases": {"id": "task_id", "task": "task_id"},
        "arguments": [
            {"name": "task_id", "type": "string", "required": True},
        ],
        "response_format_hint": "Cancellation confirmation with task ID",
        "required_any": [("task_id",)],
        "required_doc": "args.task_id required",
    },
    "background_wait": {
        "category": "exec",
        "description": "Wait for one or more background tasks to complete, with an optional timeout.",
        "aliases": ["bg_wait", "wait_for_tasks"],
        "arg_aliases": {
            "ids": "task_ids",
            "tasks": "task_ids",
            "max_seconds": "timeout",
            "on_timeout": "on_timeout",
        },
        "arguments": [
            {"name": "task_ids", "type": "array", "required": True},
            {"name": "timeout", "type": "integer", "required": False, "default": 300},
            {"name": "on_timeout", "type": "string", "required": False, "default": "continue"},
        ],
        "response_format_hint": "Completed task results with exit codes",
        "required_any": [("task_ids",)],
        "required_doc": "args.task_ids required; args.timeout <= 3600; args.on_timeout in [continue, needs_continue, fail]",
    },
    "compact_context": {
        "category": "exec",
        "description": "Compact the current context window by compressing or truncating older messages.",
        "aliases": ["context_compact", "compress_context"],
        "arg_aliases": {"focus": "focus", "method": "method"},
        "arguments": [
            {"name": "focus", "type": "string", "required": False},
            {"name": "method", "type": "string", "required": False, "default": "auto"},
        ],
        "response_format_hint": "Compaction statistics and new context summary",
        "required_any": [],
        "required_doc": "args.focus optional; args.method in [auto, truncate, llm]",
    },
    "task_create": {
        "category": "exec",
        "description": "Create a new task in the task tracking system.",
        "aliases": ["create_task", "add_task"],
        "arg_aliases": {
            "title": "subject",
            "name": "subject",
            "depends_on": "blocked_by",
            "priority": "priority",
        },
        "arguments": [
            {"name": "subject", "type": "string", "required": True},
            {"name": "blocked_by", "type": "array", "required": False},
            {"name": "priority", "type": "string", "required": False},
        ],
        "response_format_hint": "Created task ID and initial status",
        "required_any": [("subject",)],
        "required_doc": "args.subject required; args.blocked_by optional list of task_ids",
    },
    "task_update": {
        "category": "exec",
        "description": "Update the status or result of an existing task.",
        "aliases": ["update_task", "set_task_status"],
        "arg_aliases": {"id": "task_id", "status": "status", "result": "result_summary"},
        "arguments": [
            {"name": "task_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": True},
            {"name": "result_summary", "type": "string", "required": False},
        ],
        "response_format_hint": "Updated task status confirmation",
        "required_any": [("task_id",), ("status",)],
        "required_doc": "args.task_id + args.status required",
    },
    "task_ready": {
        "category": "read",
        "description": "List all tasks that are ready to be worked on (not blocked).",
        "aliases": ["ready_tasks", "get_ready_tasks"],
        "arg_aliases": {},
        "arguments": [],
        "response_format_hint": "List of ready task IDs with subjects and priorities",
        "required_any": [],
        "required_doc": "no args required",
    },
    "precision_edit": {
        "category": "write",
        "deprecated": True,
        "deprecation_reason": "Use 'edit_blocks' for better reliability with Aider-style SEARCH/REPLACE format.",
        "description": "DEPRECATED: Use 'edit_blocks' instead. Apply a precise search-and-replace edit to a single file. Note: This tool uses JSON format which is prone to character-level hallucinations (e.g., 'return0' instead of 'return 0').",
        "aliases": ["apply_search_replace", "search_replace", "replace_text"],
        "arg_aliases": {
            "path": "file",
            "filepath": "file",
            "file_path": "file",
            "target": "file",
            "query": "search",
            "find": "search",
            "text": "search",
            "pattern": "search",
            "replacement": "replace",
            "with": "replace",
            "to": "replace",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "search", "type": "string", "required": True},
            {"name": "replace", "type": "string", "required": True},
        ],
        "response_format_hint": "Edit confirmation with number of replacements made",
        "required_any": [("file",), ("search",), ("replace",)],
        "required_doc": "DEPRECATED: Use edit_blocks instead",
    },
    "edit_blocks": {
        "category": "write",
        "description": "Apply one or more SEARCH/REPLACE blocks to edit files. RECOMMENDED: Use this instead of precision_edit for better reliability.\n\nFormat:\n<<<< SEARCH[:filepath]\n<original code>\n====\n<new code>\n>>>> REPLACE\n\nBenefits over precision_edit:\n- Zero JSON escaping issues\n- Native code formatting preserved\n- Character-level hallucination tolerant (handles 'return0' -> 'return 0')\n- Multi-file edits in single call",
        "aliases": ["edit_file_blocks", "search_replace_blocks", "apply_edit_blocks", "aider_edit"],
        "arg_aliases": {
            "path": "file",
            "filepath": "file",
            "file_path": "file",
            "content": "blocks",
            "edits": "blocks",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": False},
            {"name": "blocks", "type": "string", "required": True},
        ],
        "response_format_hint": "Edit confirmation with files modified and blocks applied",
        "required_any": [("blocks",)],
        "required_doc": "args.blocks required (SEARCH/REPLACE format); args.file optional if specified in blocks",
    },
    "repo_apply_diff": {
        "category": "write",
        "description": "Apply a unified diff patch to files. Parses file paths from diff headers.",
        "aliases": ["apply_diff", "patch_apply"],
        "arg_aliases": {"path": "file", "patch": "diff"},
        "arguments": [
            {"name": "diff", "type": "string", "required": True},
            {"name": "patch", "type": "string", "required": False},
            {"name": "dry_run", "type": "boolean", "required": False},
            {"name": "strict", "type": "boolean", "required": False},
        ],
        "response_format_hint": "Patch application result with files processed and hunks applied",
        "required_any": [["diff", "patch"]],
        "required_doc": "args.file + (args.diff or args.patch)",
    },
    "treesitter_replace_node": {
        "category": "write",
        "description": "Replace an AST node identified by symbol name using tree-sitter.",
        "aliases": ["ts_replace_node", "replace_ast_node"],
        "arg_aliases": {"lang": "language", "path": "file"},
        "arguments": [
            {"name": "language", "type": "string", "required": True},
            {"name": "file", "type": "string", "required": True},
            {"name": "symbol", "type": "string", "required": True},
            {"name": "kind", "type": "string", "required": False},
            {"name": "text", "type": "string", "required": False},
        ],
        "response_format_hint": "Node replacement confirmation with AST location",
        "required_any": [("language",), ("file",), ("symbol",)],
        "required_doc": "args.language + args.file + args.symbol",
    },
    "treesitter_insert_method": {
        "category": "write",
        "description": "Insert a new method into a class using tree-sitter AST manipulation.",
        "aliases": ["ts_insert_method", "insert_method"],
        "arg_aliases": {
            "lang": "language",
            "path": "file",
            "class": "class_name",
            "text": "method_text",
        },
        "arguments": [
            {"name": "language", "type": "string", "required": True},
            {"name": "file", "type": "string", "required": True},
            {"name": "class_name", "type": "string", "required": True},
            {"name": "method_text", "type": "string", "required": True},
        ],
        "response_format_hint": "Insertion confirmation with method name and line location",
        "required_any": [("language",), ("file",), ("class_name",), ("method_text",)],
        "required_doc": "args.language + args.file + args.class_name + args.method_text",
    },
    "treesitter_rename_symbol": {
        "category": "write",
        "description": "Rename a symbol across all references in a file using tree-sitter.",
        "aliases": ["ts_rename_symbol", "rename_symbol"],
        "arg_aliases": {"lang": "language", "path": "file"},
        "arguments": [
            {"name": "language", "type": "string", "required": True},
            {"name": "file", "type": "string", "required": True},
            {"name": "symbol", "type": "string", "required": True},
            {"name": "new_name", "type": "string", "required": True},
            {"name": "kind", "type": "string", "required": False},
        ],
        "response_format_hint": "Rename confirmation with count of references updated",
        "required_any": [("language",), ("file",), ("symbol",), ("new_name",)],
        "required_doc": "args.language + args.file + args.symbol + args.new_name",
    },
    "read_file": {
        "category": "read",
        "cost_level": "high",
        "description": "Read the full content of a text file (UTF-8). HIGH COST: prefer repo_read_slice/around for targeted reading.",
        "aliases": ["rf", "cat", "file_read"],
        "arg_aliases": {
            "file_path": "file",
            "path": "file",
            "filepath": "file",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "max_bytes", "type": "integer", "required": False, "default": 200001},
            {"name": "range_required", "type": "boolean", "required": False, "default": False},
        ],
        "response_format_hint": "Full file content with truncation flag",
        "budget_hint": "For files >500 lines, prefer repo_read_slice. For files >2000 lines, explicit budget upgrade required.",
        "required_any": [("file",)],
        "required_doc": "args.file required. Prefer repo_read_slice/around for files >100 lines.",
    },
    # --- 9 active tools with handlers but missing from contracts.py ---
    "execute_command": {
        "category": "exec",
        "description": "Execute a shell command in the workspace and return its output.",
        "aliases": ["run_command", "shell", "cmd"],
        "arg_aliases": {"cmd": "command", "timeout": "timeout"},
        "arguments": [
            {"name": "command", "type": "string", "required": True},
            {"name": "timeout", "type": "integer", "required": False, "default": 30},
        ],
        "response_format_hint": "Command output with exit code",
        "required_any": [("command",)],
        "required_doc": "args.command required; args.timeout in [1-120] seconds",
    },
    "write_file": {
        "category": "write",
        "description": "Write content to a file, replacing the entire file. "
        "For partial modifications (changing specific lines), use edit_file or precision_edit instead.",
        "aliases": ["create_file", "new_file"],
        "arg_aliases": {"path": "file", "filepath": "file", "file_path": "file", "content": "content"},
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "content", "type": "string", "required": True},
            {"name": "encoding", "type": "string", "required": False, "default": "utf-8"},
        ],
        "response_format_hint": "Write confirmation with file path",
        "required_any": [("file",), ("content",)],
        "required_doc": "args.file + args.content required; only utf-8 supported",
    },
    "append_to_file": {
        "category": "write",
        "description": "Append content to the end of an existing file.",
        "aliases": ["add_content", "file_append"],
        "arg_aliases": {"filepath": "file", "file_path": "file", "path": "file"},
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "content", "type": "string", "required": True},
            {"name": "ensure_newline", "type": "boolean", "required": False, "default": True},
            {"name": "create_if_missing", "type": "boolean", "required": False, "default": True},
        ],
        "response_format_hint": "Append confirmation with byte count",
        "required_any": [("file",), ("content",)],
        "required_doc": "args.file + args.content required",
    },
    "edit_file": {
        "category": "write",
        "description": "Edit a file using one of three modes: 1) Line range mode (start_line+end_line+content), 2) Search-and-replace mode (search+replace), 3) SEARCH/REPLACE block mode (blocks). Block mode is recommended for complex edits.",
        "aliases": ["file_edit", "replace_in_file"],
        "arg_aliases": {"filepath": "file", "file_path": "file", "path": "file"},
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "content", "type": "string", "required": False},
            {"name": "start_line", "type": "integer", "required": False},
            {"name": "end_line", "type": "integer", "required": False},
            {"name": "search", "type": "string", "required": False},
            {"name": "replace", "type": "string", "required": False},
            {
                "name": "blocks",
                "type": "string",
                "required": False,
                "description": "SEARCH/REPLACE block format for complex edits",
            },
            {"name": "regex", "type": "boolean", "required": False, "default": False},
        ],
        "response_format_hint": "Edit confirmation with lines changed or blocks applied",
        "required_any": [("file",)],
        "required_doc": "args.file required; use (start_line+end_line) or (search+replace) or blocks",
    },
    "search_replace": {
        "category": "write",
        "description": "Replace occurrences of a search string in a file.",
        "aliases": ["replace_in_file", "str_replace"],
        "arg_aliases": {"filepath": "file", "file_path": "file", "path": "file"},
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "search", "type": "string", "required": True},
            {"name": "replace", "type": "string", "required": True, "default": ""},
            {"name": "regex", "type": "boolean", "required": False, "default": False},
            {"name": "replace_all", "type": "boolean", "required": False, "default": False},
        ],
        "response_format_hint": "Replacement count and confirmation",
        "required_any": [("file",), ("search",)],
        "required_doc": "args.file + args.search required",
    },
    "file_exists": {
        "category": "read",
        "description": "Check whether a file or directory exists at the given path.",
        "aliases": ["exists", "path_exists", "file_exist"],
        "arg_aliases": {"file": "path", "filepath": "path", "file_path": "path"},
        "arguments": [
            {"name": "path", "type": "string", "required": True},
        ],
        "response_format_hint": "Boolean existence result",
        "required_any": [("path",)],
        "required_doc": "args.path required",
    },
    "glob": {
        "category": "read",
        "description": "Find files matching a glob pattern within the workspace.",
        "aliases": ["find_files", "glob_files", "match_files"],
        "arg_aliases": {
            "glob": "pattern",
            "pattern": "pattern",
            "query": "pattern",
            "q": "pattern",
            "path": "path",
            "file_pattern": "pattern",
            "max": "max_results",
            "limit": "max_results",
            "n": "max_results",
            "recursive": "recursive",
            "recurse": "recursive",
            "r": "recursive",
        },
        "arguments": [
            {"name": "pattern", "type": "string", "required": True},
            {"name": "path", "type": "string", "required": False, "default": "."},
            {"name": "recursive", "type": "boolean", "required": False, "default": False},
            {"name": "include_hidden", "type": "boolean", "required": False, "default": False},
            {"name": "max_results", "type": "integer", "required": False, "default": 200},
        ],
        "response_format_hint": "List of matching file paths",
        "required_any": [("pattern",)],
        "required_doc": "args.pattern required",
    },
    # NOTE: grep is now an alias for repo_rg (defined above)
    # NOTE: list_directory is now a pure alias for repo_tree (defined above)
    # --- Context OS session memory tools ---
    "search_memory": {
        "category": "read",
        "description": "Search in the current role session's State-First Context OS for states, artifacts, or episodes.",
        "aliases": ["context_search", "memory_search"],
        "arg_aliases": {"q": "query", "n": "limit"},
        "arguments": [
            {"name": "query", "type": "string", "required": True},
            {"name": "kind", "type": "string", "required": False, "enum": ["state", "artifact", "episode"]},
            {"name": "entity", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False, "default": 6},
        ],
        "response_format_hint": "Search results with kind, id, score, text, and metadata",
        "required_any": [("query",)],
        "required_doc": "args.query required",
    },
    "read_artifact": {
        "category": "read",
        "description": "Read one Context OS artifact from the current role session, optionally by line range.",
        "aliases": ["artifact_read", "get_artifact"],
        "arg_aliases": {"id": "artifact_id"},
        "arguments": [
            {"name": "artifact_id", "type": "string", "required": True},
            {"name": "start_line", "type": "integer", "required": False},
            {"name": "end_line", "type": "integer", "required": False},
        ],
        "response_format_hint": "Artifact details with content, peek, mime_type, token_count",
        "required_any": [("artifact_id",)],
        "required_doc": "args.artifact_id required",
    },
    "read_episode": {
        "category": "read",
        "description": "Read one Context OS episode card from the current role session.",
        "aliases": ["episode_read", "get_episode"],
        "arg_aliases": {"id": "episode_id"},
        "arguments": [
            {"name": "episode_id", "type": "string", "required": True},
        ],
        "response_format_hint": "Episode details with intent, outcome, digests, artifact_refs",
        "required_any": [("episode_id",)],
        "required_doc": "args.episode_id required",
    },
    "get_state": {
        "category": "read",
        "description": "Read a state path from the current role session's State-First Context OS.",
        "aliases": ["state_get", "read_state"],
        "arg_aliases": {"key": "path", "name": "path"},
        "arguments": [
            {"name": "path", "type": "string", "required": True},
        ],
        "response_format_hint": "Structured value at the state path",
        "required_any": [("path",)],
        "required_doc": "args.path required",
    },
}


# =============================================================================
# Helper functions
# =============================================================================


def _build_tool_spec_from_dict(tool_name: str, spec_dict: dict[str, Any]) -> ToolSpec:
    """从原始spec字典构建ToolSpec。"""
    aliases_list = spec_dict.get("aliases", [])
    aliases = tuple(aliases_list) if isinstance(aliases_list, list) else aliases_list
    description = spec_dict.get("description", "")
    response_format_hint = spec_dict.get("response_format_hint", "")

    category_val = spec_dict.get("category", "read")
    if isinstance(category_val, str):
        categories = (category_val,)
    elif isinstance(category_val, (list, tuple)):
        categories = tuple(category_val)
    else:
        categories = ("read",)

    arguments = spec_dict.get("arguments", [])
    properties: dict[str, Any] = {}
    required: list[str] = []

    for arg in arguments:
        if isinstance(arg, dict):
            arg_name = arg.get("name", "")
            if not arg_name:
                continue
            arg_type = arg.get("type", "string").lower()
            param_schema: dict[str, Any] = {
                "type": arg_type,
                "description": arg.get("description", ""),
            }
            if "enum" in arg:
                param_schema["enum"] = arg["enum"]
            if arg_type == "array" and "items" in arg:
                param_schema["items"] = arg["items"]
            if "default" in arg:
                param_schema["default"] = arg["default"]
            properties[arg_name] = param_schema
            if arg.get("required", False):
                required.append(arg_name)

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    return ToolSpec(
        canonical_name=tool_name,
        aliases=aliases,
        description=description,
        parameters=parameters,
        categories=categories,
        response_format_hint=response_format_hint,
    )


def _tool_spec_to_dict(spec: ToolSpec) -> dict[str, Any]:
    """将ToolSpec转换为原始spec字典（最佳 effort，用于同步）。"""
    return {
        "canonical_name": spec.canonical_name,
        "aliases": list(spec.aliases),
        "description": spec.description,
        "category": next(iter(spec.categories)) if spec.categories else "read",
        "arguments": [],
        "response_format_hint": spec.response_format_hint,
    }


# =============================================================================
# 迁移函数
# =============================================================================


def migrate_from_contracts_specs() -> None:
    """
    从内置_TOOL_SPECS迁移到ToolSpecRegistry。

    现在数据已内置于本模块，此函数用于测试隔离时重新填充。
    """
    ToolSpecRegistry.clear()

    registry = ToolSpecRegistry._get_registry()
    specs = ToolSpecRegistry._get_specs()
    canonical_names = ToolSpecRegistry._get_canonical_names()

    for tool_name, spec_dict in _BUILTIN_REGISTRY.items():
        registry[tool_name] = dict(spec_dict)
        spec = _build_tool_spec_from_dict(tool_name, spec_dict)
        specs[tool_name] = spec
        canonical_names.add(tool_name)

    for tool_name, _spec_dict in _BUILTIN_REGISTRY.items():
        spec = specs[tool_name]
        for alias in spec.aliases:
            alias_lower = alias.lower()
            if alias_lower in _BUILTIN_REGISTRY:
                continue
            specs[alias_lower] = spec


# =============================================================================
# 导入时自动填充（SSOT模式）
# =============================================================================

migrate_from_contracts_specs()


class _RegistryProxy:
    """轻量代理对象: 对 registry.xxx 的访问委托给 ToolSpecRegistry"""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(ToolSpecRegistry, name)


# 便捷单例代理
registry = _RegistryProxy()

__all__ = ["ToolSpec", "ToolSpecRegistry", "migrate_from_contracts_specs", "registry"]
