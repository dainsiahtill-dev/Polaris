# Transaction Kernel Protocol Canonicalization Blueprint

**版本**: 1.0.0  
**日期**: 2026-04-19  
**架构师**: Principal Architect  
**状态**: 阶段一完成，待阶段二执行  
**关联审计**: `TRANSACTION_KERNEL_AUDIT_REMEDIATION_PLAN_20260419.md`

---

## 1. 业务背景与问题陈述

Transaction Kernel 是 Polaris 认知生命体的"心脏"——负责单次 turn 的决策→执行→收口完整闭环。当前内核面临的不是功能缺失，而是**协议语义分叉**：同一工具在不同模块中被分类为不同物种，导致 speculative/adopt、authoritative execution、contract guard、decision decoder 无法形成闭环。

本次蓝图聚焦**修复包 1：协议归一化**——为后续修复包 2/3/4 建立不可动摇的常量基石。

### 1.1 当前协议常量漂移全景

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WRITE_TOOLS 定义漂移图                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  constants.py:31          8 个工具 (基准真相源)                      │
│  ├─ precision_edit                                                  │
│  ├─ edit_blocks                                                     │
│  ├─ search_replace                                                  │
│  ├─ edit_file                                                       │
│  ├─ repo_apply_diff                                                 │
│  ├─ append_to_file                                                  │
│  ├─ write_file                                                      │
│  └─ create_file                                                     │
│                                                                     │
│  write_phases.py:19       6 个工具 (speculative 层)                  │
│  ├─ write_file         ✓                                            │
│  ├─ apply_patch        ✗ 不在 constants                             │
│  ├─ edit_file          ✓                                            │
│  ├─ create_file        ✓                                            │
│  ├─ delete_file        ✗ 不在 constants                             │
│  └─ rename_file        ✗ 不在 constants                             │
│  缺失: precision_edit, edit_blocks, search_replace, repo_apply_diff,│
│        append_to_file                                               │
│                                                                     │
│  turn_decision_decoder.py:87  7 个工具 (decoder 层)                  │
│  ├─ write_file         ✓                                            │
│  ├─ edit_file          ✓                                            │
│  ├─ delete_file        ✗ 不在 constants                             │
│  ├─ bash               ✗ 不在 constants                             │
│  ├─ mkdir              ✗ 不在 constants                             │
│  ├─ mv                 ✗ 不在 constants                             │
│  └─ cp                 ✗ 不在 constants                             │
│  缺失: precision_edit, edit_blocks, search_replace, repo_apply_diff,│
│        append_to_file, create_file                                  │
│                                                                     │
│  retry_orchestrator.py    硬编码集合 (retry 层)                      │
│  部分硬编码，部分引用常量，不完全一致                                │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 破坏的不变量

- **INV-1**: 同一 invocation 在所有阶段分类一致
- **INV-2**: 工具类别判定有唯一真相源
- **INV-3**: 新增/删除工具时不会遗漏任何引用点

当前三个不变量全部被打破。

---

## 2. 系统架构图

### 2.1 目标态：统一协议层

```
┌─────────────────────────────────────────────────────────────────┐
│                     统一协议层 (Protocol Layer)                  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  polaris/cells/roles/kernel/public/turn_contracts.py   │   │
│  │  ├─ _READONLY_TOOLS: set[str]      (13 个)             │   │
│  │  ├─ _ASYNC_TOOLS: set[str]         (4 个)              │   │
│  │  ├─ _infer_execution_mode()       (权威判定)           │   │
│  │  └─ _infer_effect_type()                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  transaction/constants.py   (唯一导出层)                │   │
│  │  ├─ WRITE_TOOLS: frozenset[str]   ← 从 turn_contracts  │   │
│  │  │                                   推导              │   │
│  │  ├─ READ_TOOLS: frozenset[str]    ← 从 _READONLY_TOOLS │   │
│  │  ├─ ASYNC_TOOLS: frozenset[str]   ← 从 _ASYNC_TOOLS   │   │
│  │  ├─ SAFE_READ_BOOTSTRAP_TOOLS    (业务级白名单)        │   │
│  │  └─ 意图/别名/拒绝标记等 (纯业务常量)                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│         ┌────────────────────┼────────────────────┐             │
│         ▼                    ▼                    ▼             │
│  ┌────────────┐      ┌────────────┐      ┌────────────┐        │
│  │ write_     │      │ turn_decision│      │ retry_      │        │
│  │ phases.py  │      │ _decoder.py │      │ orchestrator│        │
│  │ (import)   │      │ (import)    │      │ (import)    │        │
│  └────────────┘      └────────────┘      └────────────┘        │
│         │                    │                    │             │
│         └────────────────────┼────────────────────┘             │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  contract_guards.py / tool_batch_executor.py            │   │
│  │  (已有 import，保持不变)                                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责划分

| 模块 | 职责 | 约束 |
|------|------|------|
| `turn_contracts.py` | 定义工具分类的**工程真相源**（execution_mode / effect_type） | 不依赖任何 internal 模块 |
| `constants.py` | 定义工具分类的**业务真相源**（WRITE_TOOLS / READ_TOOLS 等） | 从 `turn_contracts` 导入工程分类，再导出业务集合 |
| `write_phases.py` | Speculative 执行阶段的写工具判定 | **只 import，不声明局部集合** |
| `turn_decision_decoder.py` | 决策解码阶段的工具分类 | **只 import，不声明局部集合** |
| `retry_orchestrator.py` | 重试编排阶段的工具选择 | **只 import，不声明局部集合** |
| `contract_guards.py` | 合约守卫阶段的工具判定 | 已有 import，验证一致性 |

---

## 3. 核心数据流

### 3.1 工具分类判定流（目标态）

```
新工具加入系统
    │
    ▼
┌──────────────────────┐
│ 1. 在 turn_contracts │
│    中注册 execution_ │
│    mode / effect_type│
└──────────────────────┘
    │
    ▼
┌──────────────────────┐
│ 2. 在 constants.py   │
│    中自动派生分类     │
│    (不手动维护集合)   │
└──────────────────────┘
    │
    ▼
┌──────────────────────┐
│ 3. 全仓各模块通过    │
│    import 使用，无   │
│    局部定义          │
└──────────────────────┘
    │
    ▼
┌──────────────────────┐
│ 4. 一致性测试扫描    │
│    全仓，发现硬编码  │
│    即失败            │
└──────────────────────┘
```

### 3.2 常量推导关系

```python
# turn_contracts.py (工程真相源)
_READONLY_TOOLS = {"read_file", "list_directory", "repo_rg", ...}  # 13个
_ASYNC_TOOLS = {"create_pull_request", "submit_job", ...}  # 4个

def _infer_execution_mode(tool_name: str) -> ToolExecutionMode:
    if tool_name in _READONLY_TOOLS: return READONLY_PARALLEL
    if tool_name in _ASYNC_TOOLS: return ASYNC_FIRE_AND_FORGET
    return WRITE_SERIAL  # 默认：其余全部为写工具

# constants.py (业务真相源，自动派生)
from turn_contracts import _READONLY_TOOLS, _ASYNC_TOOLS, _infer_execution_mode

# 推导式：所有非只读、非异步的工具 = 写工具
_WRITE_TOOL_NAMES: set[str] = {
    tool for tool in ALL_REGISTERED_TOOLS
    if _infer_execution_mode(tool) == ToolExecutionMode.WRITE_SERIAL
}
WRITE_TOOLS: frozenset[str] = frozenset(_WRITE_TOOL_NAMES)
READ_TOOLS: frozenset[str] = frozenset(_READONLY_TOOLS)
ASYNC_TOOLS: frozenset[str] = frozenset(_ASYNC_TOOLS)
```

---

## 4. 技术选型理由

### 4.1 为什么选择 "推导式" 而非 "手动维护列表"

| 方案 | 优点 | 缺点 | 决策 |
|------|------|------|------|
| 手动维护列表（当前） | 简单直观 | 易遗漏、易漂移 | ❌ 放弃 |
| 推导式（目标态） | 单一真相源、自动同步 | 需要 ALL_REGISTERED_TOOLS 完整 | ✅ 采用 |
| 运行时反射 | 最自动 | 启动开销、不确定性 | ❌ 过度设计 |

推导式方案的核心假设：`turn_contracts.py` 的 `_READONLY_TOOLS` + `_ASYNC_TOOLS` 是完整的，其余工具默认归类为 WRITE_SERIAL。这个假设是安全的，因为：
1. 新增工具时必须在 `turn_contracts.py` 注册 execution_mode
2. 如果忘记注册，默认 WRITE_SERIAL 是安全的（偏保守）
3. 一致性测试会捕获未注册的工具

### 4.2 为什么 `constants.py` 仍然是业务真相源，而非直接废弃

`turn_contracts.py` 是工程级分类（execution_mode / effect_type），而 `constants.py` 是业务级分类（SAFE_READ_BOOTSTRAP_TOOLS 等业务白名单）。两者属于不同抽象层，不应合并。但 `constants.py` 的 WRITE_TOOLS / READ_TOOLS 应从工程层自动派生，而非手动维护。

### 4.3 一致性测试方案

```python
def test_no_local_write_tool_definitions():
    """扫描全仓，确保没有模块再声明局部 WRITE_TOOLS 集合."""
    forbidden_patterns = [
        r"WRITE_TOOLS\s*[=:]\s*(frozenset|set|{)",  # 局部定义
        r"_WRITE_TOOLS\s*[=:]\s*(frozenset|set|{)",
    ]
    # 排除 constants.py 和 __init__.py 的 re-export
    allowed_files = {
        "transaction/constants.py",
        "transaction/__init__.py",
    }
    violations = scan_source_for_patterns(forbidden_patterns, exclude=allowed_files)
    assert not violations, f"发现硬编码写工具定义: {violations}"
```

---

## 5. 外部接口变更

### 5.1 向后兼容性

| 变更 | 影响 | 兼容策略 |
|------|------|---------|
| `write_phases.py._WRITE_TOOLS` 删除 | 内部使用，无外部暴露 | 直接删除 |
| `turn_decision_decoder.py.WRITE_TOOLS` 删除 | 内部使用，无外部暴露 | 直接删除 |
| `constants.py` 增加 `ASYNC_TOOLS` | 新增导出 | 向后兼容 |
| `constants.py` 的 `WRITE_TOOLS` 内容变化 | 新增 precision_edit, edit_blocks 等；移除无影响 | 行为修复（正确包含） |

### 5.2 关键风险

1. **`write_phases.py` 的 `delete_file` / `rename_file` / `apply_patch`**：这些工具不在 `constants.WRITE_TOOLS` 中。需要确认它们是否是系统中的真实工具。如果是，需要将它们加入 `turn_contracts.py` 的默认 WRITE_SERIAL 分类（或专门注册）。
2. **`turn_decision_decoder.py` 的 `bash` / `mkdir` / `mv` / `cp`**：这些工具是否是系统中的真实工具？如果是，同样需要处理。

---

## 6. 验收标准

### 6.1 代码验收

- [ ] `ruff check . --fix` 无错误无警告
- [ ] `ruff format .` 无变更
- [ ] `mypy <修改文件>` Success: no issues found
- [ ] `pytest <测试文件> -v` 全部通过

### 6.2 功能验收

- [ ] 全仓 `grep -r "WRITE_TOOLS\s*="` 只命中 `constants.py` 和 `__init__.py`
- [ ] 全仓 `grep -r "_WRITE_TOOLS\s*="` 只命中 `constants.py`
- [ ] `WriteToolPhases.is_write_tool("precision_edit")` 返回 `True`
- [ ] `WriteToolPhases.is_write_tool("edit_blocks")` 返回 `True`
- [ ] `_infer_execution_mode("precision_edit")` 返回 `WRITE_SERIAL`
- [ ] `READ_TOOLS` 包含 `list_directory`, `repo_rg`, `search_code` 等 13 个工具

### 6.3 架构验收

- [ ] 新增工具时只需修改 `turn_contracts.py` 一处
- [ ] 一致性测试能自动捕获未来的硬编码漂移

---

## 7. 修复包依赖

```
本蓝图 (修复包 1: 协议归一化)
    │
    ├──► 修复包 2: 收据/账本归一化
    │       (依赖 WRITE_TOOLS / READ_TOOLS 统一)
    │
    ├──► 修复包 3: Finalization 安全模型
    │       (依赖统一常量进行 tool classification)
    │
    └──► 修复包 4: 意图入口收口
            (依赖统一常量进行 intent → tool mapping)
```

---

## 8. 任务分配（阶段二）

| 工程师 | 职责 | 交付物 |
|--------|------|--------|
| **工程师 A** (Protocol Specialist) | 重构 `turn_contracts.py` + `constants.py`，建立推导关系 | 统一常量层 |
| **工程师 B** (Integration Engineer) | 清理 `write_phases.py` + `turn_decision_decoder.py` + `retry_orchestrator.py` 的局部定义 | 全仓引用修复 |
| **工程师 C** (Quality Engineer) | 编写一致性测试 + 边界测试 + 运行全量验证 | 测试套件 + 验证报告 |

---

*本蓝图待审批后进入阶段二执行。*
