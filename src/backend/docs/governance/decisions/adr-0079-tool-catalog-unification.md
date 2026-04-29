# ADR-0079: 工具目录统一与治理架构

## 状态

Proposed (2026-03-28)

## 问题陈述

当前系统存在三重真相源分裂问题：

1. **`contracts.py`** - 定义工具规范名称和别名（技术真相）
2. **`builtin_profiles.py`** - 定义角色可用的工具列表（权限真相）
3. **`benchmark fixtures/*.json`** - 定义测试期望的工具名称（验收真相）

三者之间没有自动化同步机制，导致：
- 别名冲突（`rg` 同时是 `repo_rg` 和 `ripgrep` 的别名）
- benchmark fixture 使用非规范名称
- 角色权限同时包含规范名和别名
- LLM 工具选择困惑

## 决策

### 1. 统一工具定义

将 `ripgrep` 合并到 `repo_rg` 作为单一 canonical 工具：

```python
"repo_rg": {
    "aliases": ["repo_search", "repo_grep", "rg", "find", "ripgrep", "repo_rg_direct"],
    "description": "PRIMARY code search tool. Use this for most searches.",
}
```

### 2. 建立单一真相源

创建 `docs/governance/schemas/tool-catalog.schema.yaml` 作为治理真相源，所有工具定义从此文件派生。

### 3. 自动化检测机制

创建 `docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py` 检查：
- 别名冲突
- profiles 使用规范名
- fixtures 使用规范名

### 4. 修复 PolicyLayer 调用时机

将 `PolicyLayer.evaluate()` 移到工具执行前，确保冷却机制生效。

### 5. 统一规范化机制

`tool_gateway.py` 使用 `canonicalize_tool_name()` 替代 `normalize_tool_name()`。

## 后果

### 正面

- 消除别名冲突
- 单一真相源，易于维护
- 自动化检测防止回归
- LLM 工具选择更清晰

### 负面

- 需要更新现有 benchmark fixtures
- 需要更新角色 whitelist
- 可能影响依赖 `ripgrep` 的外部代码

### 风险缓解

- 保留 `ripgrep` 作为 `repo_rg` 的别名，确保向后兼容
- CI 门禁检测不一致

## 实施计划

| 阶段 | 任务 | 负责专家 | 文件 |
|------|------|----------|------|
| Phase 1 | 合并 ripgrep 到 repo_rg | 专家1 | contracts.py |
| Phase 1 | 修复 benchmark fixture | 专家3 | l1_grep_search.json |
| Phase 2 | 修复 PolicyLayer 时机 | 专家5 | turn_engine.py |
| Phase 2 | 统一规范化机制 | 专家2 | tool_gateway.py |
| Phase 3 | 创建治理门禁 | 专家6 | run_tool_catalog_consistency_gate.py |
| Phase 3 | 更新角色 whitelist | 专家2 | builtin_profiles.py |

## 验证标准

1. `canonicalize_tool_name("ripgrep") == "repo_rg"`
2. `canonicalize_tool_name("rg") == "repo_rg"`
3. benchmark `l1_grep_search` 通过
4. CI 门禁检测通过

## 参考

- 专家审计报告 (2026-03-28)
- `AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD.md`