# Cells → KernelOne 整合蓝图

**版本**: v1.0
**日期**: 2026-04-03
**状态**: APPROVED
**执行阶段**: Phase 1-5

---

## 1. 背景与目标

### 1.1 问题描述

Cells层存在大量基础设施重复实现，导致：
- **维护成本高**: 同一功能在多处独立修改
- **状态不一致**: ProviderManager等单例被多次实例化
- **技术债务积累**: 4套Event系统、3套Budget实现

### 1.2 整合目标

| 目标 | 指标 | 现状 |
|------|------|------|
| 消除CRITICAL重复 | 0处 | 4处 |
| 统一Event系统 | 1套 | 4套 |
| 统一Budget系统 | 1套 | 3套 |
| 统一Provider管理 | 1个单例 | 2个实例 |

### 1.3 整合原则

1. **Cell固有逻辑不动**: 角色授权、引擎策略、业务编排保持Cell私有
2. **kernelone为Canonical**: 工具执行、事件发射、Budget追踪以kernelone为准
3. **单向依赖**: Cells依赖kernelone，禁止反向
4. **渐进式迁移**: 不破坏现有功能，逐步替换

---

## 2. 架构变更

### 2.1 删除的组件

```
polaris/cells/director/execution/internal/tools/     # DELETE - 与kernelone重复
├── chain.py
├── cli_builder.py
├── constants.py
└── models.py

polaris/cells/llm/provider_runtime/internal/providers.py  # DELETE - ProviderManager独立实例
```

### 2.2 新增的组件

```
polaris/kernelone/security/                    # NEW
└── dangerous_patterns.py                       # 统一危险命令检测

polaris/kernelone/storage/                      # NEW
└── paths.py                                   # 统一存储路径解析

polaris/kernelone/tool/                        # NEW
├── compaction.py                              # 统一结果压缩
├── safety.py                                  # 统一安全策略
└── transcript.py                              # 统一Transcript管理

polaris/kernelone/events/                      # EXTEND
├── fact_events.py                             # emit_fact_event()
├── session_events.py                           # emit_session_event()
└── task_trace_events.py                       # emit_task_trace_event()
```

### 2.3 扩展的组件

```
polaris/kernelone/context/budget_gate.py        # EXTEND
└── 添加 per-section 分配支持

polaris/kernelone/llm/providers/                # EXTEND
└── 确保单例模式
```

### 2.4 架构图

```
                    ┌─────────────────────────────────────┐
                    │           kernelone (Canonical)       │
                    ├─────────────────────────────────────┤
                    │  events/    │  budget/  │  tools/   │
                    │  storage/   │  security/│  llm/     │
                    └─────────────┬─────────────────────────┘
                                  │
                    ┌─────────────┴─────────────────────────┐
                    │              Cells (Delegates)          │
                    ├─────────────────────────────────────────┤
                    │  roles.kernel → delegates to kernelone   │
                    │  roles.engine → delegates to kernelone   │
                    │  director → delegates to kernelone        │
                    │  llm.provider_runtime → delegates        │
                    └─────────────────────────────────────────┘

    KEEP SEPARATE (Cell固有):
    - roles.kernel: RoleToolGateway, RoleContextGateway, BudgetPolicy
    - roles.engine: ReAct, PlanSolve, ToT engines
    - orchestration: PM Planning, Workflow engines
    - audit: ReviewGate, IndependentAuditService
```

---

## 3. Phase 1: 消除CRITICAL重复

**工期**: 1-2天
**优先级**: P0

### 3.1 CR-1: 删除director工具链重复

**操作**:
```bash
# 删除4个文件
rm polaris/cells/director/execution/internal/tools/chain.py
rm polaris/cells/director/execution/internal/tools/cli_builder.py
rm polaris/cells/director/execution/internal/tools/constants.py
rm polaris/cells/director/execution/internal/tools/models.py

# 修改导入
# Before:
from polaris.cells.director.execution.internal.tools import ChainExecutor

# After:
from polaris.kernelone.tools import ChainExecutor
```

**验证**:
```bash
grep -r "from polaris.cells.director.execution.internal.tools" polaris/ --include="*.py"
# 应无输出
```

### 3.2 CR-2: 修复provider_runtime单例

**操作**:
```python
# polaris/cells/llm/provider_runtime/internal/providers.py
# 删除本地ProviderManager类定义
# 修改get_provider_manager():

from polaris.infrastructure.llm.providers.provider_manager import get_provider_manager

def get_provider_manager() -> ProviderManager:
    """返回infrastructure单例，不再返回新实例"""
    return get_provider_manager()
```

**验证**:
```bash
python -c "
from polaris.infrastructure.llm.providers.provider_manager import get_provider_manager
m1 = get_provider_manager()
m2 = get_provider_manager()
assert m1 is m2, 'Must be singleton!'
print('Singleton verified')
"
```

---

## 4. Phase 2: 统一Budget基础设施

**工期**: 3-5天
**优先级**: P1

### 4.1 扩展ContextBudgetGate

**文件**: `polaris/kernelone/context/budget_gate.py`

**添加**:
```python
@dataclass
class SectionAllocation:
    """Budget section allocation result"""
    section: str
    allocated: int
    actual: int
    compressed: bool

class ContextBudgetGate:
    # ... 现有代码 ...

    def allocate_section(
        self,
        section: str,
        tokens: int
    ) -> SectionAllocation:
        """为指定section分配budget"""
        ...

    def get_section_breakdown(self) -> dict[str, int]:
        """返回各section的token使用"""
        ...
```

### 4.2 委托TokenBudget

**文件**: `polaris/cells/roles/kernel/internal/token_budget.py`

**修改**:
```python
class TokenBudget:
    """委托给kernelone ContextBudgetGate"""
    _gate: ContextBudgetGate | None = None

    def __init__(self, ...):
        self._gate = ContextBudgetGate(...)
        # 保留原有接口，委托给gate
```

**验证**:
```bash
pytest polaris/kernelone/context/tests/ polaris/cells/roles/kernel/tests/ -v
```

---

## 5. Phase 3: 统一安全模式

**工期**: 2-3天
**优先级**: P1

### 5.1 创建dangerous_patterns模块

**文件**: `polaris/kernelone/security/dangerous_patterns.py`

```python
"""统一危险命令模式检测"""

_DANGEROUS_PATTERNS: list[str] = [
    r"rm\s+-rf\s+[/~]",
    r"rm\s+-rf\s+\$HOME",
    r"rm\s+-rf\s+\*",
    r"del\s+/[fqs]\s+",
    r"dd\s+if=/dev/",
    r"mkfs\.",
    r"format\s+[a-z]:",
    # ... 15+ patterns
]

_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS), re.IGNORECASE)

def is_dangerous_command(text: str) -> bool:
    """检测命令是否危险"""
    return bool(_DANGEROUS_RE.search(text))
```

### 5.2 删除重复实现

**删除**:
- `polaris/cells/roles/kernel/internal/policy/layer/budget.py:24-45` (_DANGEROUS_PATTERNS)
- `polaris/cells/roles/kernel/internal/policy/sandbox_policy.py:51-54` (_DANGEROUS_COMMAND_RE)

**验证**:
```bash
ruff check polaris/cells/roles/kernel/internal/policy/ --select=F811
# 应无输出
```

---

## 6. Phase 4: 统一存储路径

**工期**: 2-3天
**优先级**: P1

### 6.1 创建统一路径模块

**文件**: `polaris/kernelone/storage/paths.py`

```python
"""统一存储路径解析"""

def resolve_signal_path(
    workspace: str,
    role: str,
    stage: str,
) -> Path:
    """解析signal文件路径"""
    return Path(workspace) / "runtime" / "signals" / f"{stage}.{role}.signals.json"

def resolve_artifact_path(
    workspace: str,
    artifact_id: str,
) -> Path:
    ...

def resolve_session_path(
    workspace: str,
    session_id: str,
) -> Path:
    ...

def resolve_taskboard_path(
    workspace: str,
) -> Path:
    ...
```

### 6.2 修改各Cell导入

**修改文件**:
- `polaris/cells/roles/adapters/internal/base.py:288-289`
- `polaris/cells/roles/adapters/internal/pm_adapter.py:1490`
- `polaris/cells/roles/session/internal/storage_paths.py`

**验证**:
```bash
grep -r "resolve_preferred_logical_prefix\|resolve_runtime_path" polaris/cells/ --include="*.py" | wc -l
# 应从22降到<5
```

---

## 7. Phase 5: 清理事件基础设施

**工期**: 2-3天
**优先级**: P1

### 7.1 扩展kernelone.events

**添加**:
```python
# polaris/kernelone/events/fact_events.py
async def emit_fact_event(
    workspace: str,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    """发射fact event到审计链"""
    ...

# polaris/kernelone/events/session_events.py
async def emit_session_event(
    workspace: str,
    event_name: str,
    session_id: str,
    ...
) -> None:
    ...

# polaris/kernelone/events/task_trace_events.py
async def emit_task_trace_event(
    workspace: str,
    task_id: str,
    trace: dict[str, Any],
) -> None:
    ...
```

### 7.2 删除重复EventEmitter

**删除/修改**:
- `polaris/cells/roles/kernel/internal/events.py` → 使用 `emit_fact_event()`
- `polaris/cells/roles/kernel/internal/kernel/error_handler.py` → 使用 `LLMEventEmitter` 直接
- `polaris/cells/roles/session/internal/session_persistence.py:572-642` → 使用 `emit_session_event()`

### 7.3 修复audit.diagnosis双重写入

**文件**: `polaris/cells/audit/diagnosis/internal/connection_audit_service.py`

```python
# 删除JSONL写入，只保留KernelAuditRuntime
def write_ws_connection_event(...):
    # Before: 写入JSONL + emit to KernelAuditRuntime
    # After: 只emit to KernelAuditRuntime
    runtime.emit_event(...)
```

---

## 8. CI门禁

### 8.1 新增检查规则

**文件**: `docs/governance/ci/fitness-rules.yaml`

```yaml
- id: CELL_KERNELONE_INTEGRATION_01
  title: "Director tools must use kernelone"
  description: "director.execution/internal/tools/ must not exist or must import from kernelone"
  severity: blocker
  status: draft
  check:
    type: file_not_exists_or_import
    files:
      - polaris/cells/director/execution/internal/tools/chain.py
    import_from: polaris.kernelone.tools

- id: CELL_KERNELONE_INTEGRATION_02
  title: "ProviderManager must be singleton"
  description: "llm.provider_runtime must not create new ProviderManager instances"
  severity: blocker
  status: draft
  check:
    type: no_direct_instantiation
    pattern: "ProviderManager\\(\\)"
    exclude:
      - polaris/infrastructure/llm/providers/

- id: CELL_KERNELONE_INTEGRATION_03
  title: "No duplicate dangerous patterns"
  description: "Dangerous pattern definitions must only exist in kernelone.security"
  severity: blocker
  status: draft
  check:
    type: single_source
    allowed:
      - polaris/kernelone/security/dangerous_patterns.py
    forbidden:
      - polaris/cells/roles/kernel/internal/policy/layer/budget.py
      - polaris/cells/roles/kernel/internal/policy/sandbox_policy.py
```

### 8.2 执行脚本

**文件**: `docs/governance/ci/scripts/run_cells_kernelone_gate.py`

```python
#!/usr/bin/env python3
"""Cells→KernelOne整合门禁检查"""

import sys
from pathlib import Path

def check_critical_integrations():
    errors = []

    # CR-1: director tools
    director_tools = Path("polaris/cells/director/execution/internal/tools")
    if director_tools.exists():
        errors.append("CR-1: director/tools/ must be deleted")

    # CR-2: provider_runtime singleton
    providers_file = Path("polaris/cells/llm/provider_runtime/internal/providers.py")
    if providers_file.exists():
        content = providers_file.read_text()
        if "return ProviderManager()" in content:
            errors.append("CR-2: provider_runtime returns new instance")

    # ... more checks

    return errors

if __name__ == "__main__":
    errors = check_critical_integrations()
    if errors:
        print("FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("PASSED: All integration checks")
```

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 删除工具链破坏director功能 | 高 | 先加feature flag，验证后删除 |
| ProviderManager单例改变导致重启 | 中 | 保持API兼容，只改实现 |
| Budget Section分配改变现有行为 | 中 | 添加配置开关，默认兼容模式 |
| Event统一改变日志格式 | 低 | 添加adapter保持旧格式 |

---

## 10. 验收标准

### 10.1 量化指标

| 指标 | 目标 | 当前 |
|------|------|------|
| CRITICAL重复 | 0 | 4 |
| Event系统数 | 1 | 4 |
| Budget实现数 | 1 | 3 |
| ProviderManager实例 | 1 | 2 |
| 存储路径模式 | 1 | 22+ |

### 10.2 功能验收

```bash
# 1. Director功能正常
python -m polaris.delivery.cli.director.cli_thin --workspace . --iterations 1

# 2. Provider管理正常
pytest polaris/infrastructure/llm/providers/tests/ -v

# 3. Budget计算一致
pytest polaris/kernelone/context/tests/ -v

# 4. Event发射正常
pytest polaris/kernelone/events/tests/ -v

# 5. 门禁通过
python docs/governance/ci/scripts/run_cells_kernelone_gate.py
```

---

**批准人**: _______________ **日期**: _______________
