"""Schema-Driven Normalization Engine.

设计原则：
1. polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry 是单一事实来源
2. arg_aliases 直接驱动归一化，无需 per-tool normalizer 函数
3. 复杂转换通过 escape_hatch 钩子处理
4. Stage 1: 处理所有 arg_aliases 映射
5. Stage 2: 复杂转换由 TOOL_NORMALIZERS 处理
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["SchemaDrivenNormalizer", "normalize_with_schema"]


# 路径类参数名（需要 _normalize_workspace_alias_path）
_PATH_CANONICAL_KEYS = frozenset(
    {"file", "path", "filepath", "file_path", "root", "dir", "directory", "cwd", "target", "source"}
)


def _is_path_key(key: str) -> bool:
    """检查规范参数名是否为路径类型。"""
    return key.lower() in _PATH_CANONICAL_KEYS


class SchemaDrivenNormalizer:
    """基于 contracts.py schema 的运行时归一化器。

    职责：
    - 处理 contracts.py 中声明的所有 arg_aliases 映射
    - 路径类参数自动调用 _normalize_workspace_alias_path
    - 复杂转换委托给 escape_hatch 或后续的 TOOL_NORMALIZERS
    """

    def __init__(self, contracts_specs: dict[str, dict[str, Any]]) -> None:
        self.specs = contracts_specs
        self._escape_hatches: dict[str, Callable[..., Any]] = {}

    def register_escape_hatch(self, tool_name: str, func: Callable[..., Any]) -> None:
        """为特定工具注册复杂转换钩子。"""
        self._escape_hatches[tool_name] = func

    def _resolve_tool_alias(self, tool_name: str) -> str:
        """Resolve tool name to its canonical tool name using aliases in specs.

        tools/contracts.py defines tool aliases in the 'aliases' field of each spec.
        For example, 'search_code' is in the aliases list of 'repo_rg'.
        This method finds the canonical tool name when given an alias.
        """
        for canonical, spec in self.specs.items():
            aliases = spec.get("aliases", [])
            if tool_name in aliases:
                return canonical
        return tool_name

    def normalize(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        """根据 schema 归一化工具参数。

        处理 contracts.py 中声明的所有 arg_aliases 映射。
        路径类参数自动规范化，非路径类参数直接映射。
        """
        if not isinstance(tool_args, dict):
            return {}

        normalized = dict(tool_args)
        # Resolve tool name aliases: search_code -> repo_rg, ripgrep -> repo_rg, etc.
        canonical_tool = self._resolve_tool_alias(tool_name)
        spec = self.specs.get(canonical_tool, {})
        arg_aliases = spec.get("arg_aliases", {})

        if not arg_aliases:
            return normalized  # 无别名，直接返回

        # 分类别名：路径类 vs 普通类
        path_mappings: dict[str, list[str]] = {}
        other_mappings: dict[str, list[str]] = {}

        for alias, canonical in arg_aliases.items():
            if alias == canonical:
                continue
            if _is_path_key(canonical):
                path_mappings.setdefault(canonical, []).append(alias)
            else:
                other_mappings.setdefault(canonical, []).append(alias)

        # 处理路径别名：多个别名 -> 同一规范名
        for canonical, aliases in path_mappings.items():
            if canonical in normalized:
                # 规范名已存在，规范化其值
                value = normalized.get(canonical)
                if isinstance(value, str) and value.strip():
                    normalized[canonical] = self._normalize_path(value.strip())
            else:
                # 尝试从别名中找到第一个非空值
                for alias in aliases:
                    candidate = normalized.get(alias)
                    if isinstance(candidate, str) and candidate.strip():
                        normalized[canonical] = self._normalize_path(candidate.strip())
                        break

        # 处理普通别名：多个别名 -> 同一规范名
        for canonical, aliases in other_mappings.items():
            if canonical in normalized:
                continue
            for alias in aliases:
                if alias in normalized:
                    normalized[canonical] = normalized.pop(alias)
                    break

        # 调用 escape hatch（如有）- 用于无法用别名映射表达的复杂转换
        if tool_name in self._escape_hatches:
            normalized = self._escape_hatches[tool_name](normalized)

        # 清理所有别名键（确保不残留）
        all_aliases: set[str] = set()
        for aliases in list(path_mappings.values()) + list(other_mappings.values()):
            all_aliases.update(aliases)
        for alias in all_aliases:
            normalized.pop(alias, None)

        return normalized

    def _normalize_path(self, path: str) -> str:
        """归一化路径（复用 shared helpers）。"""
        from .normalizers._shared import _normalize_workspace_alias_path

        return _normalize_workspace_alias_path(path)


# 全局实例（惰性初始化）
_normalizer_instance: SchemaDrivenNormalizer | None = None


def get_schema_normalizer() -> SchemaDrivenNormalizer:
    """获取全局 SchemaDrivenNormalizer 实例。"""
    global _normalizer_instance
    if _normalizer_instance is None:
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        _normalizer_instance = SchemaDrivenNormalizer(ToolSpecRegistry.get_all_specs())
    return _normalizer_instance


def normalize_with_schema(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    """使用 schema-driven 引擎归一化参数。"""
    return get_schema_normalizer().normalize(tool_name, tool_args)
