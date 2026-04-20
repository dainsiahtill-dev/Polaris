"""从 tools/contracts.py 自动生成 normalizer 函数的代码生成器。

CANONICAL STRATEGY (2026-03-30):
- polaris.kernelone.tool_execution.tool_spec_registry.ToolSpecRegistry 是单一规范来源
- generator.py 从 arg_aliases 自动生成 normalizer 函数代码
- 生成的代码需人工 review 后再注册到 TOOL_NORMALIZERS
"""

from __future__ import annotations

import textwrap
from typing import Any


def _to_python_type(t: str) -> str:
    return {"string": "str", "integer": "int", "boolean": "bool"}.get(t, "Any")


def _is_path_key(key: str) -> bool:
    path_keys = {"path", "file", "filepath", "file_path", "root", "dir", "directory", "cwd"}
    return key.lower() in path_keys


def _collect_arg_aliases(spec: dict[str, Any]) -> dict[str, list[str]]:
    """收集 arg_aliases，分组为 path 类和普通类.

    Returns:
        {canonical: (aliases_list)}
    """
    result: dict[str, list[str]] = {}
    for alias, canonical in spec.get("arg_aliases", {}).items():
        if alias == canonical:
            continue
        if canonical not in result:
            result[canonical] = []
        result[canonical].append(alias)
    return result


def generate_normalizer_code(tool_name: str, spec: dict[str, Any]) -> str:
    """为单个工具生成完整 normalizer 函数代码。"""
    arg_aliases = _collect_arg_aliases(spec)

    lines = [
        f'"""Normalizer for {tool_name} tool."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "",
        f"def normalize_{tool_name}_args(tool_args: dict[str, Any]) -> dict[str, Any]:",
        f'    """Normalize {tool_name} arguments."""',
        "    normalized = dict(tool_args)",
        "",
    ]

    # 路径归一化
    path_aliases = {}
    other_aliases = {}
    for canonical, aliases in arg_aliases.items():
        if _is_path_key(canonical):
            path_aliases[canonical] = aliases
        else:
            other_aliases[canonical] = aliases

    # 路径别名处理（需要 _normalize_workspace_alias_path）
    if path_aliases:
        for canonical, aliases in path_aliases.items():
            lines.append(f"    # Normalize {canonical} from path aliases")
            lines.append(f"    if not normalized.get('{canonical}'):")
            lines.append(f"        for alias in {aliases}:")
            lines.append("            candidate = normalized.get(alias)")
            lines.append("            if isinstance(candidate, str) and candidate.strip():")
            lines.append("                from ._shared import _normalize_workspace_alias_path")
            lines.append(
                f"                normalized['{canonical}'] = _normalize_workspace_alias_path(candidate.strip())"
            )
            lines.append("                break")
            lines.append("")

    # 普通别名归一化
    for canonical, aliases in other_aliases.items():
        lines.append(f"    # Normalize {canonical} from aliases: {aliases}")
        lines.append(f"    if not normalized.get('{canonical}'):")
        lines.append(f"        for alias in {aliases}:")
        lines.append("            if alias in normalized:")
        lines.append(f"                normalized['{canonical}'] = normalized.pop(alias)")
        lines.append("                break")
        lines.append("")

    # 清理所有原始别名键
    all_alias_keys = [alias for aliases in arg_aliases.values() for alias in aliases]
    if all_alias_keys:
        lines.append("    # Remove consumed alias keys")
        lines.append(f"    for alias in {all_alias_keys}:")
        lines.append("        normalized.pop(alias, None)")
        lines.append("")

    lines.append("    return normalized")
    lines.append("")

    return "\n".join(lines)


def generate_all_normalizers() -> dict[str, str]:
    """为所有 ToolSpecRegistry 中的工具生成 normalizer 代码。"""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    return {
        name: generate_normalizer_code(name, spec)
        for name, spec in ToolSpecRegistry.get_all_specs().items()
        if spec.get("category") != "internal"
    }


def generate_imports() -> str:
    return textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any
    """)


if __name__ == "__main__":
    # CLI: 生成所有 normalizer 代码并打印
    generated = generate_all_normalizers()
    for tool_name, code in sorted(generated.items()):
        print(f"# === {tool_name} ===")
        print(code)
        print()
