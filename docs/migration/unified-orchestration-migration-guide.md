# 统一编排内核迁移指南

## 概述

本文档指导如何将代码从旧版编排系统迁移到统一编排内核。

## 迁移检查清单

### 如果你是 API/CLI 开发者

- [ ] 使用 `UnifiedOrchestrationService` 替代 `RuntimeOrchestrator`
- [ ] 使用统一契约类型 (`OrchestrationRunRequest`, `OrchestrationSnapshot`)
- [ ] 验证状态映射 (`CompatibilityMapper`)

### 如果你是角色开发者 (PM/Director/QA)

- [ ] 实现 `RoleOrchestrationAdapter` 接口
- [ ] 使用 `RoleExecutionKernel` 统一执行
- [ ] 移除自定义 workflow 逻辑

### 如果你是 UI 开发者

- [ ] 迁移到统一状态字段 (`RunStatus`, `TaskPhase`)
- [ ] 使用 `FileChangeStats` 显示文件变更
- [ ] 关注 `overall_progress` 而非自定义进度

## 代码迁移示例

### 1. 提交编排运行

**旧代码**:
```python
from core.runtime_orchestrator import RuntimeOrchestrator
from application.dto.process_launch import RunMode

orchestrator = RuntimeOrchestrator()
result = await orchestrator.spawn_pm(
    workspace=Path("."),
    mode=RunMode.SINGLE,
)
```

**新代码**:
```python
from core.orchestration import get_orchestration_service
from application.dto.orchestration_contracts import (
    OrchestrationRunRequest,
    OrchestrationMode,
    RoleEntrySpec,
)

service = await get_orchestration_service()

request = OrchestrationRunRequest(
    run_id="run-001",
    workspace=Path("."),
    mode=OrchestrationMode.WORKFLOW,
    role_entries=[
        RoleEntrySpec(role_id="pm", input="...")
    ],
)

snapshot = await service.submit_run(request)
```

### 2. 查询运行状态

**旧代码**:
```python
# PM 状态
pm_status = pm_service.get_status()

# Director 状态
director_status = director_service.get_status()

# QA 状态
qa_status = qa_service.get_status()
```

**新代码**:
```python
from core.orchestration import get_orchestration_service

service = await get_orchestration_service()
snapshot = await service.query_run(run_id)

# 统一访问
for task_id, task in snapshot.tasks.items():
    print(f"{task_id}: {task.status.value}")
```

### 3. 发送控制信号

**旧代码**:
```python
# PM 停止
pm_service.stop()

# Director 取消
director_service.cancel()
```

**新代码**:
```python
from core.orchestration import get_orchestration_service
from application.dto.orchestration_contracts import (
    SignalRequest,
    OrchestrationSignal,
)

service = await get_orchestration_service()

# 统一信号接口
await service.signal_run(
    run_id,
    SignalRequest(signal=OrchestrationSignal.CANCEL)
)
```

## 状态映射表

### PM 状态映射

| 旧状态 | 新状态 |
|-------|-------|
| idle | RunStatus.PENDING |
| running | RunStatus.RUNNING |
| completed | RunStatus.COMPLETED |
| error | RunStatus.FAILED |

### Director 状态映射

| 旧状态 | 新状态 |
|-------|-------|
| pending | RunStatus.PENDING |
| in_progress | RunStatus.RUNNING |
| success | RunStatus.COMPLETED |
| failure | RunStatus.FAILED |
| cancelled | RunStatus.CANCELLED |

## 故障排除

### 问题: 导入错误

**症状**: `ModuleNotFoundError: No module named 'application'`

**解决**: 确保后端根目录在 Python 路径中
```python
import sys
from pathlib import Path
backend_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_root))
```

### 问题: 状态不一致

**症状**: UI 显示的状态与实际不符

**解决**: 使用 `CompatibilityMapper` 确保映射正确
```python
from application.dto.orchestration_contracts import CompatibilityMapper

unified_status = CompatibilityMapper.legacy_status_to_unified(legacy_status)
```

### 问题: 依赖循环

**症状**: `ImportError: cannot import name 'X' from partially initialized module`

**解决**: 检查导入顺序，优先导入契约类型
```python
# 先导入契约类型（无副作用）
from application.dto.orchestration_contracts import ...

# 再导入服务实现
from core.orchestration import ...
```

## 支持

如有问题，请参考：
- [架构决策记录](../architecture/ADR-001-unified-orchestration-kernel.md)
- [架构守护测试](../../tests/refactor/test_architecture_guard.py)
