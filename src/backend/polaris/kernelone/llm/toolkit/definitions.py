"""Standard tool definitions for LLM integration.

【DEPRECATED MODULE - 废弃模块】
This module is maintained for backward compatibility only.
Single source of truth: polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry

    所有工具数据来自 ToolSpecRegistry，此模块仅提供：
    1. ToolDefinition/ToolParameter dataclass 抽象层
    2. create_default_registry() - 从 ToolSpecRegistry 生成 ToolRegistry

NOTE: STANDARD_TOOLS has been deprecated and removed.
All tool definitions now come from polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


# 废弃警告 (Deprecation Warning)
warnings.warn(
    "polaris.kernelone.llm.toolkit.definitions is deprecated. "
    "Use create_default_registry() to get ToolDefinition objects. "
    "Tool data comes from polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry.",
    DeprecationWarning,
    stacklevel=2,
)


@dataclass
class ToolParameter:
    """工具参数定义."""

    name: str
    type: str  # string, integer, array, object, boolean
    description: str
    required: bool = True
    enum: list[str] | None = None
    default: Any = None
    items: dict[str, Any] | None = None  # 用于 array 类型
    properties: dict[str, Any] | None = None  # 用于 object 类型

    def to_json_schema(self) -> dict[str, Any]:
        """转换为标准的 JSON Schema 格式 (兼容 OpenAI & Anthropic)."""
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.items is not None:
            schema["items"] = self.items
        if self.properties is not None:
            schema["properties"] = self.properties
        # 透传默认值，帮助 LLM 做出更精准的决策
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class ToolDefinition:
    """工具定义."""

    name: str
    description: str
    parameters: list[ToolParameter]
    handler: Callable[..., Any] | None = None
    returns: str = ""
    examples: list[str] = field(default_factory=list)

    def _get_schema_components(self) -> tuple[dict[str, Any], list[str]]:
        """内部方法：提取 properties 和 required 列表."""
        properties = {}
        required = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)
        return properties, required

    def to_openai_function(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 格式."""
        properties, required = self._get_schema_components()

        schema_params = {
            "type": "object",
            "properties": properties,
        }
        # 优化：仅在有必填项时才包含 required 字段，避免某些严格的解析器报错
        if required:
            schema_params["required"] = required

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema_params,
            },
        }

    def to_anthropic_tool(self) -> dict[str, Any]:
        """转换为 Anthropic 官方 native tool 格式."""
        properties, required = self._get_schema_components()

        input_schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required

        # 注意：Anthropic 的结构是扁平的，使用 input_schema 而不是 parameters
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }

    def to_prompt_template(self) -> str:
        """生成 Prompt-based 模板说明 (用于不支持原生 Tool Calling 的模型回退)."""
        params_desc = "\n".join(
            f"  - {p.name} ({p.type}){' [必需]' if p.required else f' [可选, 默认: {p.default}]'}: {p.description}"
            for p in self.parameters
        )

        examples = "\n".join(self.examples) if self.examples else ""

        return f"""
### {self.name}
{self.description}

参数:
{params_desc}

使用格式:
[{self.name.upper()}]
{chr(10).join(f"{p.name}: <{p.type}>" for p in self.parameters)}
[/{self.name.upper()}]

{examples}
""".strip()

    def validate_arguments(self, arguments: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate tool arguments against this definition.

        Args:
            arguments: The arguments to validate.

        Returns:
            Tuple of (is_valid, list_of_error_messages).
        """
        errors: list[str] = []
        if not isinstance(arguments, dict):
            return False, ["arguments must be a dictionary"]

        # Check required parameters
        required_names = {p.name for p in self.parameters if p.required}
        provided_names = set(arguments.keys())
        missing = required_names - provided_names
        if missing:
            errors.append(f"Missing required parameters: {', '.join(sorted(missing))}")

        # Validate each provided parameter
        param_map = {p.name: p for p in self.parameters}
        for name, value in arguments.items():
            if name not in param_map:
                errors.append(f"Unknown parameter: {name}")
                continue

            param = param_map[name]
            # Type validation
            expected_type = param.type.lower()
            type_errors = self._validate_param_type(name, value, expected_type)
            errors.extend(type_errors)

            # Enum validation
            if param.enum is not None and value not in param.enum:
                errors.append(f"Parameter '{name}' value {value!r} not in allowed values: {param.enum}")

        return len(errors) == 0, errors

    @staticmethod
    def _validate_param_type(
        name: str,
        value: Any,
        expected_type: str,
    ) -> list[str]:
        """Validate a parameter value against expected type."""
        errors: list[str] = []

        if expected_type == "string":
            if not isinstance(value, str):
                errors.append(f"Parameter '{name}' must be a string, got {type(value).__name__}")
        elif expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"Parameter '{name}' must be an integer, got {type(value).__name__}")
        elif expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"Parameter '{name}' must be a number, got {type(value).__name__}")
        elif expected_type == "boolean":
            if not isinstance(value, bool):
                errors.append(f"Parameter '{name}' must be a boolean, got {type(value).__name__}")
        elif expected_type == "array":
            if not isinstance(value, list):
                errors.append(f"Parameter '{name}' must be an array, got {type(value).__name__}")
        elif expected_type == "object" and not isinstance(value, dict):
            errors.append(f"Parameter '{name}' must be an object, got {type(value).__name__}")

        return errors


class ToolRegistry:
    """工具注册表."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable[..., Any] | None] = {}

    def register(self, tool: ToolDefinition, handler: Callable[..., Any] | None = None) -> None:
        """注册工具."""
        self._tools[tool.name] = tool
        if handler or tool.handler:
            self._handlers[tool.name] = handler or tool.handler

    def get(self, name: str) -> ToolDefinition | None:
        """获取工具定义."""
        return self._tools.get(name)

    def get_handler(self, name: str) -> Callable[..., Any] | None:
        """获取工具处理器."""
        return self._handlers.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """列出所有工具."""
        return list(self._tools.values())

    def to_openai_functions(self) -> list[dict[str, Any]]:
        """转换为 OpenAI functions 列表."""
        return [tool.to_openai_function() for tool in self._tools.values()]

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        """转换为 Anthropic tools 列表."""
        return [tool.to_anthropic_tool() for tool in self._tools.values()]

    def to_prompt_documentation(self) -> str:
        """生成 Prompt 文档."""
        tools_doc = "\n\n".join(tool.to_prompt_template() for tool in self._tools.values())
        return f"""# 可用工具

你可以使用以下工具来辅助分析和决策:

{tools_doc}

---
当你需要使用工具时，请按照上述格式输出。系统会自动解析并执行。
"""


def create_default_registry() -> ToolRegistry:
    """Create default tool registry from ToolSpecRegistry (single source of truth).

    DEPRECATED: 直接使用此函数获取工具定义。
    推荐迁移: 使用 polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry。

    NOTE: STANDARD_TOOLS has been deprecated.
    This function reads from polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry.
    """
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    registry = ToolRegistry()

    for tool_name, spec in ToolSpecRegistry.get_all_specs().items():
        # Skip if this is an alias entry (aliases point to the same spec)
        # We only want canonical tool names
        if "aliases" not in spec:
            continue

        # Build canonical parameter index for alias expansion
        arg_index: dict[str, dict[str, Any]] = {}
        for arg in spec.get("arguments", []):
            if isinstance(arg, dict):
                name = str(arg.get("name") or "").strip()
                if name:
                    arg_index[name] = arg

        # Create ToolDefinition parameters including alias expansions
        parameters: list[ToolParameter] = []
        seen_param_names: set[str] = set()

        # First add canonical parameters
        for arg in spec.get("arguments", []):
            if not isinstance(arg, dict):
                continue
            param = _make_tool_parameter(arg)
            if param and param.name not in seen_param_names:
                parameters.append(param)
                seen_param_names.add(param.name)

        # Then add alias parameters (arg_aliases map aliases to canonical names)
        arg_aliases = spec.get("arg_aliases") or {}
        for alias_name, canonical_name in arg_aliases.items():
            alias_name = str(alias_name or "").strip()
            canonical_name = str(canonical_name or "").strip()
            if not alias_name or alias_name in seen_param_names:
                continue
            canonical_arg = arg_index.get(canonical_name)
            if not canonical_arg:
                continue
            # Create alias parameter with same type as canonical
            alias_param = ToolParameter(
                name=alias_name,
                type=str(canonical_arg.get("type", "string") or "string").strip().lower() or "string",
                description=f"(alias for {canonical_name})",
                required=False,
                default=canonical_arg.get("default"),
                enum=canonical_arg.get("enum"),
                items=canonical_arg.get("items"),
                properties=canonical_arg.get("properties"),
            )
            parameters.append(alias_param)
            seen_param_names.add(alias_name)

        tool_def = ToolDefinition(
            name=tool_name,
            description=spec.get("description", ""),
            parameters=parameters,
        )
        registry.register(tool_def)

    return registry


def _make_tool_parameter(arg: dict[str, Any]) -> ToolParameter | None:
    """Create a ToolParameter from an argument spec dict."""
    name = str(arg.get("name") or "").strip()
    if not name:
        return None
    return ToolParameter(
        name=name,
        type=str(arg.get("type", "string") or "string").strip().lower() or "string",
        description=str(arg.get("description", "") or ""),
        required=bool(arg.get("required", False)),
        default=arg.get("default"),
        enum=arg.get("enum"),
        items=arg.get("items"),
        properties=arg.get("properties"),
    )


# =============================================================================
# DEPRECATION NOTICE (2026-04-05)
# =============================================================================
# This module (definitions.py) is deprecated for direct imports.
#
# Migration path:
#   1. Tool data (specs) -> polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry
#   2. Tool validation -> polaris.kernelone.tool_execution.contracts.validate_tool_step()
#   3. Tool normalization -> polaris.kernelone.tool_execution.contracts.normalize_tool_args()
#   4. Tool registry -> use create_default_registry() (bridge to ToolSpecRegistry)
#
# This module provides:
#   - ToolParameter/ToolDefinition dataclasses (abstraction layer)
#   - ToolRegistry class (collection of ToolDefinition objects)
#   - create_default_registry() function (builds ToolRegistry from ToolSpecRegistry)
# =============================================================================
