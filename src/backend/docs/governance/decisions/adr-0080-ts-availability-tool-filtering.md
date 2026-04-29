# ADR-0080: Tree-sitter 可用性检测与工具过滤机制

**日期**: 2026-03-29
**状态**: 草案
**决策者**: Python 架构与代码治理实验室

---

## 背景

Polaris 提供多个依赖 `tree_sitter_language_pack` 的工具。当该库因网络/环境问题无法下载 parser 二进制文件时，相关工具会挂起或失败，导致 LLM 工具调用体验差。

## 问题陈述

1. **无可用性检测**: 代码只在调用点用 try/except 处理，没有启动时检测
2. **工具暴露过度**: LLM 工具配置包含可能不可用的 tree-sitter 工具
3. **角色配置错误**: PM 角色的 builtin profile 将无实现的 tree-sitter 工具列入白名单

## 决策

### 3.1 检测机制

引入 `TreeSitterAvailability` 数据类和 `is_tree_sitter_available()` 函数：

```python
@dataclass(frozen=True)
class TreeSitterAvailability:
    available: bool
    reason: str | None
    checked_at: float | None
```

- 带 5 秒超时检测
- 使用 `@lru_cache` 缓存结果
- 不可用时提供明确原因

### 3.2 工具过滤

引入 `get_available_tools()` 函数，根据可用性过滤工具列表：

```python
def get_available_tools(
    requested_tools: list[str],
    ts_availability: TreeSitterAvailability | None = None,
) -> list[str]:
    ...
```

### 3.3 工具规格标记

定义 `TS_DEPENDENT_TOOLS` 常量标记依赖工具：

```python
TS_DEPENDENT_TOOLS: frozenset[str] = frozenset({
    "repo_symbols_index",
    "treesitter_find_symbol",
    "treesitter_replace_node",
    "treesitter_insert_method",
    "treesitter_rename_symbol",
})
```

## 后果

### 正面

- LLM 工具列表始终有效
- 失败明确，可诊断
- 不影响 ripgrep 工具

### 负面

- 增加启动时检测开销（约 5 秒超时）
- 需要更新多个文件

## 替代方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 不检测，在调用时处理 | 简单 | LLM 工具列表仍包含无效工具 |
| B. 仅在配置时过滤 | 灵活 | 需要用户配置 |
| **C. 启动时检测+过滤（选中）** | 自动化、用户体验好 | 增加启动复杂度 |
| D. 全部移除 tree-sitter 工具 | 消除问题 | 功能丧失 |

## 实施计划

| Phase | 任务 | 目标文件 |
|-------|------|---------|
| 1 | 实现 `is_tree_sitter_available()` | `ts_availability.py` (new) |
| 2 | 定义 `TS_DEPENDENT_TOOLS` | `contracts.py` |
| 3 | 实现 `get_available_tools()` | `tool_normalization.py` |
| 4 | 集成到 `filter_tools()` | `tool_gateway.py` |
| 5 | 添加测试用例 | `test_ts_availability.py` (new) |
