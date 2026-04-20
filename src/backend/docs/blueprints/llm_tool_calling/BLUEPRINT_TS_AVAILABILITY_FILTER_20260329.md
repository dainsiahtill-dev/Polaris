# Blueprint: Tree-sitter 可用性检测与工具过滤机制

**日期**: 2026-03-29
**状态**: 草案
**负责人**: Python 架构与代码治理实验室
**目标版本**: v2.2.0

---

## 1. 背景与问题

### 1.1 当前问题

当 `tree_sitter_language_pack` 无法下载 parser 二进制文件时（如网络隔离环境），以下工具会挂起或失败：

| 工具 | 当前行为 | 后果 |
|------|---------|------|
| `repo_symbols_index` | 导入挂起 | LLM 调用超时 |
| `treesitter_find_symbol` | 导入挂起 | LLM 调用超时 |
| `treesitter_replace_node` | 仅规格，无实现 | N/A |
| `treesitter_insert_method` | 仅规格，无实现 | N/A |
| `treesitter_rename_symbol` | 仅规格，无实现 | N/A |

**关键**: `search_code` / `repo_rg` 使用 `subprocess.run(["rg", ...])`，**不受影响**。

### 1.2 影响范围

- LLM 工具配置中包含 tree-sitter 依赖工具，但实际不可用
- PM 角色 builtin profile 将这些工具列入白名单
- 用户体验：工具调用失败率高

---

## 2. 目标

1. **启动时检测** tree-sitter 可用性（带超时）
2. **LLM 工具过滤**：根据可用性动态过滤工具列表
3. **优雅降级**：tree-sitter 不可用时提供明确的错误信息
4. **零破坏**：不影响 `search_code` / `repo_rg` 等 ripgrep 工具

---

## 3. 技术方案

### 3.1 核心组件

```
polaris/kernelone/llm/toolkit/
├── ts_availability.py          # NEW: tree-sitter 可用性检测
├── tool_normalization.py       # MOD: 添加 get_available_tools()
└── executor/
    └── core.py                 # MOD: 集成可用性检测

polaris/kernelone/tools/
└── contracts.py                # MOD: 标记 TS_DEPENDENT_TOOLS

polaris/cells/roles/kernel/internal/
└── tool_gateway.py            # MOD: filter_tools() 集成可用性过滤
```

### 3.2 API 设计

```python
# ts_availability.py

@dataclass(frozen=True)
class TreeSitterAvailability:
    """Tree-sitter 可用性状态"""
    available: bool
    reason: str | None = None  # 不可用时的原因
    checked_at: float | None = None

def is_tree_sitter_available(timeout: float = 5.0) -> TreeSitterAvailability:
    """
    检测 tree-sitter 是否可用。

    Args:
        timeout: 检测超时（秒）

    Returns:
        TreeSitterAvailability 状态对象
    """

# tool_normalization.py

def get_available_tools(
    requested_tools: list[str],
    ts_availability: TreeSitterAvailability | None = None,
) -> list[str]:
    """
    根据 tree-sitter 可用性过滤工具列表。

    Args:
        requested_tools: 请求的工具列表
        ts_availability: tree-sitter 可用性状态（None 时自动检测）

    Returns:
        过滤后的可用工具列表
    """
```

### 3.3 TS_DEPENDENT_TOOLS 常量

```python
# contracts.py

TS_DEPENDENT_TOOLS: frozenset[str] = frozenset({
    "repo_symbols_index",
    "treesitter_find_symbol",
    "treesitter_replace_node",  # 仅规格
    "treesitter_insert_method",  # 仅规格
    "treesitter_rename_symbol",  # 仅规格
})
```

---

## 4. 执行计划

| Phase | 任务 | 文件 | 负责人 |
|-------|------|------|--------|
| 1 | 核心检测机制 | `ts_availability.py` | Agent |
| 2 | 工具规格标记 | `contracts.py` | Agent |
| 3 | 工具过滤入口 | `tool_normalization.py` | Agent |
| 4 | 角色配置更新 | `tool_gateway.py` | Agent |
| 5 | 验证与测试 | 测试套件 | Agent |

---

## 5. 验收标准

1. tree-sitter 不可用时，`repo_symbols_index` 等工具不出现在 LLM 工具列表
2. `search_code` / `repo_rg` 完全不受影响
3. 启动时检测耗时 < 5 秒（超时保护）
4. 所有新增代码通过 mypy 严格类型检查
5. 新增单元测试覆盖可用性检测逻辑

---

## 6. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 检测超时导致启动慢 | 添加 5 秒超时，快速失败 |
| 缓存失效 | 使用 `@lru_cache` 缓存结果 |
| 多实例状态不一致 | 单例模式 `TreeSitterAvailabilityChecker` |
