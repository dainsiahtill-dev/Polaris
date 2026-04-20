# Cells → KernelOne 整合分析报告

**日期**: 2026-04-03
**审计团队**: 10个专家并行团队
**覆盖范围**: polaris/cells/* + polaris/kernelone/* 全链路分析

---

## 执行摘要

**核心发现**: Cells层存在大量基础设施重复实现，根源在于`polaris/kernelone`层能力暴露不足，导致各Cell独立复制功能。整合收益远大于迁移成本。

| 类别 | 数量 | 整合优先级 |
|------|------|-----------|
| **CRITICAL重复** (byte-for-byte相同) | 4处 | P0 |
| **HIGH重复** (功能等价独立实现) | 12处 | P1 |
| **MEDIUM重复** (可合并抽象) | 8处 | P2 |
| **KEEP_SEPARATE** (Cell固有逻辑) | 35+处 | 不动 |

---

## P0: 必须立即整合（CRITICAL重复）

### CR-1: `director.execution` ↔ `kernelone` 工具链完全重复

**证据**: Team-D 发现

```
polaris/cells/director/execution/internal/tools/chain.py
polaris/kernelone/tools/chain.py
---
BYTE-FOR-BYTE IDENTICAL (除了import路径)

同样的文件:
- chain.py
- cli_builder.py
- constants.py
- models.py
```

**影响文件**:
| 文件 | 大小 | 状态 |
|------|------|------|
| `director/execution/internal/tools/chain.py` | ~400行 | 应删除 |
| `director/execution/internal/tools/cli_builder.py` | ~200行 | 应删除 |
| `director/execution/internal/tools/constants.py` | ~50行 | 应删除 |
| `director/execution/internal/tools/models.py` | ~100行 | 应删除 |

**整合方案**:
```
删除 director/execution/internal/tools/
改为 from polaris.kernelone.tools import ChainExecutor, ...
Cell只需保留 authorization wrapper (tool_gateway)
```

**验证**: `diff <(cat director/execution/internal/tools/chain.py) <(cat kernelone/tools/chain.py)` 应无输出

---

### CR-2: `llm.provider_runtime` ProviderManager 独立实例

**证据**: Team-H 发现

**文件**: `polaris/cells/llm/provider_runtime/internal/providers.py:33-407`

```python
# 问题代码
def get_provider_manager() -> ProviderManager:
    return ProviderManager()  # 返回NEW实例，不是单例！
```

**正确模式** (已在 `runtime_invoke.py`):
```python
from polaris.kernelone.llm import KernelLLM  # 正确：使用kernelone
```

**影响**:
- `llm.provider_runtime` 的 `get_provider_manager()` 返回本地实例
- 另一个实例在 `polaris/infrastructure/llm/providers/provider_registry.py` (真正的单例)
- 两套实例 → 状态不一致

**整合方案**:
```python
# 删除 llm.provider_runtime/internal/providers.py 的本地ProviderManager
# 改为直接调用 infrastructure 单例
from polaris.infrastructure.llm.providers.provider_manager import get_provider_manager
```

---

## P1: 高优先级整合（功能等价独立实现）

### H-1: Budget/Tracking 基础设施三重实现

**证据**: Team-C + Team-J 一致发现

| 实现 | 位置 | 功能 |
|------|------|------|
| **Canonical** | `kernelone/context/budget_gate.py` | `ContextBudgetGate` - 模型窗口、安全边际、锁保护 |
| **重复A** | `cells/roles/kernel/internal/token_budget.py` | `TokenBudget` - 简化版，无锁 |
| **重复B** | `cells/roles/kernel/internal/policy/budget_policy.py` | `BudgetPolicy` - 工具调用限制 |
| **重复C** | `cells/finops/budget_guard/internal/budget_store.py` | `BudgetRecord` |

**关键问题**:
```
roles.kernel/token_budget.py:19-54
定义了自己的 TokenBudget dataclass:
- system_context: int = 4000
- task_context: int = 2000
- conversation: int = 4000

kernelone/budget_gate.py 已有相同功能的 ContextBudgetGate:
- 模型窗口解析
- 安全边际
- 线程安全RLock
- suggest_compaction()
```

**整合方案**:
```
Phase 1: 让 TokenBudget 委托给 ContextBudgetGate
Phase 2: 删除 TokenBudget，用 ContextBudgetGate 替代
Phase 3: 扩展 ContextBudgetGate 支持 per-section 分配
```

**需要修改的文件**:
- `polaris/cells/roles/kernel/internal/token_budget.py` → 委托模式
- `polaris/kernelone/context/budget_gate.py` → 添加 section 分配支持

---

### H-2: Dangerous Command Pattern 两份独立实现

**证据**: Team-C 发现

```python
# layer/budget.py:24-45
_DANGEROUS_PATTERNS: list[str] = [
    r"rm\s+-rf\s+[/~]",
    r"rm\s+-rf\s+\$HOME",
    ...
]

# sandbox_policy.py:51-54
_DANGEROUS_COMMAND_RE = re.compile(
    r"(rm\s+-rf|dd\s+if=/dev/|mkfs\.|format\s+[a-z]:|...)",
    re.IGNORECASE,
)
```

**整合方案**:
```
polaris/kernelone/security/dangerous_patterns.py  (NEW)
├── _DANGEROUS_PATTERNS (canonical list)
├── is_dangerous_command(text) -> bool
└── DangerousPatternMatcher

删除:
- cells/roles/kernel/internal/policy/layer/budget.py:24-45
- cells/roles/kernel/internal/policy/sandbox_policy.py:51-54
```

---

### H-3: Storage Path Resolution 22处分散实现

**证据**: Team-C 发现

**分散文件**:
- `cells/roles/session/internal/storage_paths.py` - custom `resolve_preferred_logical_prefix`
- `cells/runtime/task_runtime/internal/task_board.py` - `resolve_runtime_path`
- `cells/roles/adapters/internal/base.py:288-289` - 重新实现路径解析
- `cells/roles/adapters/internal/pm_adapter.py:1490` - 使用 `resolve_runtime_path`
- `cells/roles/adapters/internal/qa_adapter.py` - 同上

**问题代码** (base.py:288-289):
```python
relative_path = f"runtime/signals/{stage_token}.{role_token}.signals.json"
target = Path(resolve_runtime_path(self.workspace, relative_path))
```

**整合方案**:
```
polaris/kernelone/storage/paths.py  (NEW)
├── resolve_signal_path(role, stage, ...)
├── resolve_artifact_path(...)
├── resolve_session_path(...)
├── resolve_taskboard_path(...)
└── resolve_runtime_path(workspace, rel_path) -> Path

统一所有Cell使用此模块
```

---

### H-4: Tool Loop 基础设施重复

**证据**: Team-C + Team-J 一致发现

**重复内容**:
| 组件 | kernelone | roles.kernel |
|------|-----------|--------------|
| Tool执行器 | `AgentAccelToolExecutor` | `RoleToolGateway` (wrapper) ✅正确 |
| 结果压缩 | `_compact_tool_result_payload()` | `_compact_value()` ❌重复 |
| 安全策略 | `ContextBudgetGate` | `ToolLoopSafetyPolicy` ❌重复 |
| Transcript | `ContextEvent` | `ToolLoopTranscript` ❌重复 |

**整合方案**:
```
polaris/kernelone/tool/  (NEW module)
├── polaris/kernelone/tool/compaction.py
│   └── compact_result_payload()  # 统一压缩逻辑
├── polaris/kernelone/tool/safety.py
│   └── ToolLoopSafetyPolicy  # 从 context_event.py 移入
└── polaris/kernelone/tool/transcript.py
    └── ContextEvent transcript management

删除 roles.kernel/internal/context_event.py 的重复实现
```

---

### H-5: Event Publishing 四套并行实现

**证据**: Team-C 发现

| 实现 | 位置 | 用途 |
|------|------|------|
| Canonical | `kernelone/events.py` | `LLMEventEmitter` |
| 重复A | `roles/kernel/internal/kernel/error_handler.py` | `KernelEventEmitter` (wrapper) |
| 重复B | `roles/session/internal/session_persistence.py` | `SessionEventPublisher` |
| 重复C | `cells/events/fact_stream/public/service.py` | `append_fact_event` |

**问题代码** (session_persistence.py:428-541):
```python
def publish_session_created(self, session_id: str, role: str, ...):
    self._emit_event(name="session_created", kind="action", ...)

def _emit_event(self, name: str, kind: str, actor: str, ...):
    from polaris.kernelone.events import emit_event  # 又导入kernelone
    emit_event(event_path=event_path, kind=kind, ...)
```

**整合方案**:
```
polaris/kernelone/events/  (扩展现有)
├── emit_fact_event(...)
├── emit_session_event(...)
└── emit_task_trace_event(...)

删除:
- cells/roles/kernel/internal/kernel/error_handler.py 的 KernelEventEmitter
- cells/roles/session/internal/session_persistence.py:572-642 (SessionEventPublisher._emit_event)
```

---

### H-6: LLM调用 三处独立实现

**证据**: Team-C + Team-H 一致发现

| 实现 | 位置 | 状态 |
|------|------|------|
| **Canonical** | `kernelone/llm/` | 正确 |
| 重复A | `cells/roles/adapters/internal/base.py:_call_role_llm()` | 应删除 |
| 重复B | `cells/roles/adapters/internal/director/dialogue.py:role_llm_invoke()` | 应删除 |
| 重复C | `cells/llm/dialogue/internal/role_dialogue.py` | Cell固有逻辑，OK |

**整合方案**:
```
删除:
- cells/roles/adapters/internal/base.py:_call_role_llm()
- cells/roles/adapters/internal/director/dialogue.py:role_llm_invoke()

保留:
- cells/llm/dialogue/internal/role_dialogue.py (角色对话逻辑)
```

---

## P2: 中优先级整合（可合并抽象）

### M-1: `_compact_repeated_tools` 在 history_materialization.py 硬编码

**位置**: `polaris/kernelone/context/history_materialization.py:199-257`

```python
repeat_threshold: int = 3  # 硬编码，不可配置
```

**整合方案**: 提取为 `CompactionPolicy` 配置字段

---

### M-2: ContextAssembler._deduplicate_messages 死代码

**位置**: `polaris/cells/roles/kernel/internal/services/context_assembler.py:514-548`

方法定义并有测试但从未在生产路径调用

**整合方案**: 删除 + 删除对应测试

---

### M-3: JSON Storage 多处独立实现

**位置**:
- `cells/finops/budget_guard/` - JSON budget store
- `cells/roles/session/` - JSON session persistence
- `cells/runtime/task_runtime/` - JSON task board

**整合方案**: 统一使用 `KernelFileSystem` + `LocalFileSystemAdapter`

---

## KEEP_SEPARATE: Cell固有逻辑（禁止整合）

以下逻辑具有Cell-specific领域语义，禁止整合到kernelone：

### 角色授权层
| 文件 | 组件 | 原因 |
|------|------|------|
| `roles/kernel/internal/tool_gateway.py` | `RoleToolGateway` | 角色白名单/黑名单，Cell级授权 |
| `roles/kernel/internal/context_gateway.py` | `RoleContextGateway` | 角色上下文策略，注入检测 |
| `roles/kernel/internal/policy/budget_policy.py` | `BudgetPolicy` | 工具调用限制，Cell级策略 |

### 引擎策略层
| 文件 | 组件 | 原因 |
|------|------|------|
| `roles/engine/internal/base.py` | `BaseEngine`, `EngineStrategy` | ReAct/PlanSolve/ToT推理范式 |
| `roles/engine/internal/react.py` | `ReActEngine` | 推理循环实现 |
| `roles/engine/internal/plan_solve.py` | `PlanSolveEngine` | 计划-执行范式 |
| `roles/engine/internal/tot.py` | `ToTEngine` | 思维树范式 |
| `roles/engine/internal/registry.py` | `EngineRegistry` | 引擎注册与选择 |

### 业务编排层
| 文件 | 组件 | 原因 |
|------|------|------|
| `orchestration/pm_planning/` | 计划编排 | Cell固有 |
| `orchestration/pm_dispatch/` | 分发编排 | Cell固有 |
| `orchestration/workflow_*` | 工作流引擎 | Cell固有 |

### LLM配置层
| 文件 | 组件 | 原因 |
|------|------|------|
| `llm/control_plane/internal/llm_config_agent.py` | `LLMConfigStore`, `HRAgent` | Cell级配置存储 |
| `llm/dialogue/internal/role_dialogue.py` | Role templates | Cell固有对话逻辑 |

---

## 整合路线图

### Phase 1: 消除CRITICAL重复 (1-2天)
1. **CR-1**: 删除 `director/execution/internal/tools/` (4文件)
2. **CR-2**: 修复 `llm.provider_runtime` 使用单例

### Phase 2: 统一Budget基础设施 (3-5天)
1. 扩展 `ContextBudgetGate` 支持 per-section 分配
2. 让 `TokenBudget` 委托给 `ContextBudgetGate`
3. 删除 `roles/kernel/internal/token_budget.py`

### Phase 3: 统一安全模式 (2-3天)
1. 创建 `kernelone/security/dangerous_patterns.py`
2. 删除两处独立实现

### Phase 4: 统一存储路径 (2-3天)
1. 创建 `kernelone/storage/paths.py`
2. 统一所有Cell使用

### Phase 5: 清理事件基础设施 (2-3天)
1. 扩展 `kernelone/events` 支持所有事件类型
2. 删除 `KernelEventEmitter` wrapper

---

## 验证命令

```bash
# 验证CR-1修复：确认文件被删除或导入统一
grep -r "from polaris.cells.director.execution.internal.tools" polaris/ --include="*.py"
# 应无输出

# 验证CR-2修复：确认使用单例
grep -r "ProviderManager()" polaris/cells/llm/provider_runtime/ --include="*.py"
# 应无输出

# 验证Budget整合
python -c "from polaris.kernelone.context.budget_gate import ContextBudgetGate; g = ContextBudgetGate(128000); print(g.model_window)"
# 应输出: 128000

# 运行测试
pytest polaris/kernelone/context/tests/ polaris/cells/roles/kernel/tests/ -v
```

---

## 附录：团队分工

| 团队 | 负责领域 | 关键发现 |
|------|----------|----------|
| Team-A | Cells全量审计 | 2 INTEGRATE, 9 CANDIDATE |
| Team-B | KernelOne审计 | 8 cells正确使用, 1 CANDIDATE |
| Team-C | Cross-cell依赖 | TOP5 整合候选 |
| Team-D | Tool Runtime | director工具链重复 (CRITICAL) |
| Team-E | Context/Budget | roles.kernel重复实现 |
| Team-F | Event系统 | 4套并行事件实现 |
| Team-G | Audit/Governance | 审计evidence链正确 |
| Team-H | LLM/Provider | provider_runtime重复 (CRITICAL) |
| Team-I | Orchestration | workflow引擎正确隔离 |
| Team-J | Roles/Engine分离 | 3 INTEGRATE_CANDIDATE |

---

**报告生成**: 2026-04-03
**下次审查**: 2026-04-10 (Phase 1完成后)
