"""Transaction Kernel 协议归一化一致性测试。

验证核心原则：
1. WRITE_TOOLS 是全局唯一手动维护的写工具集合（constants.py）
2. READ_TOOLS / ASYNC_TOOLS 自动从 turn_contracts 派生
3. 不存在孤立的局部写工具集合定义
4. _infer_execution_mode 分类行为一致
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.transaction.constants import (
    ASYNC_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    _ASYNC_TOOLS,
    _READONLY_TOOLS,
    ToolExecutionMode,
    _infer_execution_mode,
)

# ---------------------------------------------------------------------------
# 测试 1: 写工具集合单一真相源
# ---------------------------------------------------------------------------


EXCLUDED_PATH_FRAGMENTS: frozenset[str] = frozenset(
    {
        "transaction/constants.py",
        "transaction/__init__.py",
        "test_",
        "/tests/",
        "__pycache__",
    }
)


def _should_skip_file(file_path: Path) -> bool:
    """判断文件是否应被排除在扫描之外。"""
    path_str = file_path.as_posix()
    return any(fragment in path_str for fragment in EXCLUDED_PATH_FRAGMENTS)


def _find_local_write_tool_assignments(root_dir: Path) -> list[tuple[Path, int, str]]:
    """扫描目录下所有 .py 文件，查找局部 WRITE_TOOLS / _WRITE_TOOLS 定义。

    Returns:
        (文件路径, 行号, target名称) 列表
    """
    violations: list[tuple[Path, int, str]] = []
    for py_file in root_dir.rglob("*.py"):
        if _should_skip_file(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("WRITE_TOOLS", "_WRITE_TOOLS"):
                    violations.append((py_file, target.lineno, target.id))
    return violations


def test_write_tools_single_source_of_truth() -> None:
    """验证不存在局部 WRITE_TOOLS / _WRITE_TOOLS 定义。

    唯一允许的真相源：
    - polaris/cells/roles/kernel/internal/transaction/constants.py
    - polaris/cells/roles/kernel/internal/transaction/__init__.py (re-export)
    """
    backend_root = Path(__file__).resolve().parents[8]  # -> src/backend
    polaris_dir = backend_root / "polaris"
    kernelone_dir = backend_root / "kernelone"

    all_violations: list[tuple[Path, int, str]] = []
    for scan_dir in (polaris_dir, kernelone_dir):
        if scan_dir.exists():
            all_violations.extend(_find_local_write_tool_assignments(scan_dir))

    if all_violations:
        formatted = "\n".join(
            f"  {path.relative_to(backend_root)}:{lineno}  {name}" for path, lineno, name in sorted(all_violations)
        )
        pytest.fail(
            f"发现 {len(all_violations)} 处局部 WRITE_TOOLS/_WRITE_TOOLS 定义，"
            f"违反单一真相源原则（应仅在 transaction/constants.py 维护）：\n{formatted}"
        )


# ---------------------------------------------------------------------------
# 测试 2: READ_TOOLS 派生一致性
# ---------------------------------------------------------------------------


def test_read_tools_derived_from_truth_source() -> None:
    """验证 READ_TOOLS 身份等于 _READONLY_TOOLS（或集合相等）。"""
    assert READ_TOOLS is _READONLY_TOOLS or READ_TOOLS == _READONLY_TOOLS, (
        f"READ_TOOLS 与 _READONLY_TOOLS 不一致：\n"
        f"  READ_TOOLS      = {sorted(READ_TOOLS)}\n"
        f"  _READONLY_TOOLS = {sorted(_READONLY_TOOLS)}"
    )


# ---------------------------------------------------------------------------
# 测试 3: ASYNC_TOOLS 派生一致性
# ---------------------------------------------------------------------------


def test_async_tools_derived_from_truth_source() -> None:
    """验证 ASYNC_TOOLS 身份等于 _ASYNC_TOOLS（或集合相等）。"""
    assert ASYNC_TOOLS is _ASYNC_TOOLS or ASYNC_TOOLS == _ASYNC_TOOLS, (
        f"ASYNC_TOOLS 与 _ASYNC_TOOLS 不一致：\n"
        f"  ASYNC_TOOLS    = {sorted(ASYNC_TOOLS)}\n"
        f"  _ASYNC_TOOLS   = {sorted(_ASYNC_TOOLS)}"
    )


# ---------------------------------------------------------------------------
# 测试 4: WriteToolPhases 使用规范集合
# ---------------------------------------------------------------------------


def test_write_tool_phases_uses_canonical_set() -> None:
    """验证 WriteToolPhases.is_write_tool 对已知写/读工具的分类正确。"""
    assert WriteToolPhases.is_write_tool("precision_edit") is True, "precision_edit 应被识别为写工具（此前遗漏）"
    assert WriteToolPhases.is_write_tool("edit_blocks") is True, "edit_blocks 应被识别为写工具"
    assert WriteToolPhases.is_write_tool("read_file") is False, "read_file 不应被识别为写工具"


# ---------------------------------------------------------------------------
# 测试 5: _infer_execution_mode 一致性
# ---------------------------------------------------------------------------


def test_infer_execution_mode_consistency() -> None:
    """验证 _infer_execution_mode 对典型工具的分类正确。"""
    assert _infer_execution_mode("precision_edit") == ToolExecutionMode.WRITE_SERIAL
    assert _infer_execution_mode("read_file") == ToolExecutionMode.READONLY_PARALLEL
    assert _infer_execution_mode("create_pull_request") == ToolExecutionMode.ASYNC_RECEIPT
    # 未知工具默认安全：WRITE_SERIAL
    assert _infer_execution_mode("unknown_tool_xyz") == ToolExecutionMode.WRITE_SERIAL


# ---------------------------------------------------------------------------
# 测试 6: kernel/helpers.py _WRITE_TOOL_NAMES 无孤儿工具
# ---------------------------------------------------------------------------


def test_no_orphaned_write_tool_names() -> None:
    """验证 kernel/helpers.py 的 _WRITE_TOOL_NAMES 不超出 WRITE_TOOLS。

    如果 helpers.py 仍保留局部 _WRITE_TOOL_NAMES，则检查其是否为 WRITE_TOOLS 子集。
    若存在遗漏（如 patch_apply 不在 WRITE_TOOLS 中），测试失败并给出明确错误。
    """
    helpers_path = Path(__file__).resolve().parents[3] / "internal" / "kernel" / "helpers.py"
    if not helpers_path.exists():
        pytest.skip(f"helpers.py 不存在于预期路径: {helpers_path}")

    source = helpers_path.read_text(encoding="utf-8")
    tree = ast.parse(source, str(helpers_path))

    local_write_tool_names: set[str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_WRITE_TOOL_NAMES":
                    # 尝试静态提取集合字面量
                    local_write_tool_names = _extract_set_literals(node.value)
                    break

    if local_write_tool_names is None:
        # 已经没有局部定义，测试通过
        return

    orphaned = local_write_tool_names - set(WRITE_TOOLS)
    if orphaned:
        pytest.fail(
            f"kernel/helpers.py 的 _WRITE_TOOL_NAMES 包含不在 WRITE_TOOLS 中的工具：\n"
            f"  孤儿工具: {sorted(orphaned)}\n"
            f"  应修复：删除局部 _WRITE_TOOL_NAMES，改为从 transaction.constants 导入 WRITE_TOOLS"
        )


def _extract_set_literals(node: ast.AST | None) -> set[str]:
    """从 AST 节点中提取字符串集合字面量（支持 set {...} 和 {...}）。"""
    result: set[str] = set()
    if node is None:
        return result
    if isinstance(node, ast.Set):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                result.add(elt.value)
    elif isinstance(node, ast.Call):
        # frozenset({...}) or set({...})
        if node.args:
            result.update(_extract_set_literals(node.args[0]))
        for keyword in node.keywords:
            if keyword.arg is None:
                result.update(_extract_set_literals(keyword.value))
    return result
