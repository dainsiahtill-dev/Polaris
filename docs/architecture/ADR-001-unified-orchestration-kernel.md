# ADR-001: 统一编排内核重构

## 状态

- 状态: 实现存在但 adoption 未完成
- 日期: 2026-03-06
- 作者: Polaris 架构团队
- 基线记录: 2026-03-06 W0 基线冻结

### Adoption 说明

本 ADR 定义的统一编排内核 **代码实现已完成**，但 **adoption（采用）尚未完成**：

1. **已完成**:
   - 统一类型定义 (`orchestration_contracts.py`)
   - 服务接口与实现 (`orchestration_service_impl.py`)
   - 角色适配器 (`app/roles/adapters/`)
   - V2 API 路由 (`api/v2/pm.py`, `api/v2/director.py`)

2. **未完成 Adoption**:
   - 旧版 API 路由 (`app/routers/pm.py`, `app/routers/director.py`) 仍在使用
   - 部分代码仍直接调用旧版 RuntimeOrchestrator
   - CLI 入口未完全迁移到 V2

### 后续工作

- 完成旧版路由的迁移或清理
- 确保所有新代码使用 V2 API
- 清理废弃的 RuntimeOrchestrator 实现

## 上下文

### 问题

Polaris 存在多套编排系统并行演进：

1. **两套 RuntimeOrchestrator**:
   - `core/runtime_orchestrator.py` (旧版，直接使用 subprocess)
   - `core/orchestration/runtime_orchestrator.py` (新版，基于 ServiceDefinition)

2. **cli_thin 断链**: 引用了缺失的模块:
   - `scripts.pm.pm_service` (缺失)
   - `scripts.director.director_service` (缺失)
   - `scripts.director.api_server` (缺失)

3. **状态字段漂移**: PM/Director/QA 各自维护状态枚举，导致 UI 需要多套解析逻辑

### 目标

收敛到单一编排内核 + 角色插件化执行，任意角色都能以两种模式运行：
- **chat**: 类似 Claude/Codex 交互
- **workflow**: 合同驱动执行

## 决策

### 1. 统一编排契约 (Phase 1)

**新增文件**:
- `application/dto/orchestration_contracts.py` - 统一类型定义
- `application/ports/orchestration_service.py` - 服务接口定义
- `core/orchestration/orchestration_service_impl.py` - 服务实现

**关键类型**:
```python
# 统一状态枚举 - 消除字段漂移
class RunStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

# 统一快照 - 单一事实来源
class OrchestrationSnapshot:
    schema_version: str
    run_id: str
    status: RunStatus
    tasks: Dict[str, TaskSnapshot]
    current_phase: TaskPhase
    overall_progress: float
```

### 2. 兼容层策略 (Phase 2-3)

**旧版 Orchestrator 改造**:
- `core/runtime_orchestrator.py` → 标记为 DEPRECATED
- 添加 `warnings.warn()` 废弃告警
- 方法转发到 `UnifiedOrchestrationService` (shim 模式)

**RunMode 兼容映射**:
```python
class CompatibilityMapper:
    @staticmethod
    def pm_mode_to_orchestration(mode: str) -> OrchestrationMode:
        # run_once/loop → WORKFLOW
        # chat → CHAT

    @staticmethod
    def legacy_status_to_unified(status: str) -> RunStatus:
        # PM/Director 各种状态 → RunStatus
```

### 3. 补齐缺失模块 (Phase 2)

**新增**:
- `scripts/pm/pm_service.py` - PM 核心服务
- `scripts/director/director_service.py` - Director 核心服务

**职责划分**:
- `cli_thin.py`: 只负责参数解析，调用服务
- `*_service.py`: 实际业务逻辑，调用 RoleExecutionKernel

### 4. 角色适配器 (Phase 4)

**新增**: `app/roles/adapters/`
- `pm_adapter.py` - PM 角色适配器
- `director_adapter.py` - Director 角色适配器
- `qa_adapter.py` - QA 角色适配器
- `chief_engineer_adapter.py` - Chief Engineer 角色适配器

**职责**:
- 实现 `RoleOrchestrationAdapter` 接口
- 将各角色接入统一编排系统
- 统一调用 `RoleExecutionKernel`

### 5. Workflow 通用化 (Phase 5)

**新增**: `app/orchestration/workflows/generic_pipeline_workflow.py`

统一工作流实现:
- 支持任意角色的 PipelineSpec
- 依赖图解析
- 并行/串行执行
- 错误处理与重试

**兼容包装器**:
- `PMWorkflow` - 兼容旧 PM Workflow
- `DirectorWorkflow` - 兼容旧 Director Workflow
- `QAWorkflow` - 兼容旧 QA Workflow

### 6. API/CLI 入口切换 (Phase 6)

**新增**: `api/v2/orchestration.py`

统一编排 API:
- `POST /v2/orchestration/runs` - 创建运行
- `GET /v2/orchestration/runs/{run_id}` - 查询状态
- `POST /v2/orchestration/runs/{run_id}/signal` - 发送信号

**增强**:
- `api/v2/pm.py` - 新增 `/v2/pm/run` 编排端点
- `api/v2/director.py` - 新增 `/v2/director/run` 编排端点

### 7. 状态可观测 (Phase 7)

**新增**:
- `app/orchestration/ui_state_contract.py` - UI 状态合同
- `app/orchestration/file_change_tracker.py` - 文件变更追踪

**UI 状态统一**:
- 统一任务状态: `UIPhase`, `UITaskStatus`
- 文件变更指标: `C/M/D 文件数`, `+/-/~ 行数`
- 状态延迟追踪: `latency_ms`

### 8. 清理收尾 (Phase 8)

**脚本**: `scripts/phase8_cleanup.py`

功能:
- 预览待清理文件
- 检查旧模块引用
- 备份并删除旧文件
- 生成清理报告

**架构守护**: `tests/refactor/test_architecture_guard.py`

验证:
- 只有一个生产 RuntimeOrchestrator
- cli_thin 不包含业务逻辑
- 统一契约类型存在且完整
- 角色适配器完整性
- 通用工作流存在
- UI 状态合同存在
- 文件变更追踪存在
- 兼容映射正确

## 实施计划

| 阶段 | 内容 | 状态 |
|-----|------|------|
| Phase 0 | 架构守护测试 | ✅ 完成 |
| Phase 1 | 统一契约类型 | ✅ 完成 |
| Phase 2 | 编排服务实现 | ✅ 完成 |
| Phase 3 | 运行时收敛 | ✅ 完成 |
| Phase 4 | 角色适配器 | ✅ 完成 |
| Phase 5 | Workflow 通用化 | ✅ 完成 |
| Phase 6 | API/CLI 兼容 | ✅ 完成 |
| Phase 7 | 状态可观测 | ✅ 完成 |
| Phase 8 | 清理收尾 | ✅ 就绪（待执行） |

**注意**: Phase 8 的清理脚本已就绪，请在完整回归测试通过后执行。

## 影响

### 对现有代码的影响

1. **旧 Orchestrator**: 添加废弃告警，功能通过 shim 转发
2. **PMService**: 不受影响，后续可选迁移到 UnifiedOrchestrationService
3. **CLI**: 无变化，兼容层保证行为一致

### 迁移路径

```
旧代码:
  from core.runtime_orchestrator import RuntimeOrchestrator
  orch = RuntimeOrchestrator()
  orch.spawn_pm(workspace, mode)

新代码 (推荐):
  from core.orchestration import get_orchestration_service
  service = await get_orchestration_service()
  snapshot = await service.submit_run(request)
```

## 风险与缓解

| 风险 | 缓解措施 |
|-----|---------|
| 兼容层性能损耗 | shim 层只做转发，开销可忽略 |
| 状态字段漂移导致 UI 断更 | 统一 RunStatus 枚举，强制所有角色使用 |
| 双写/双调度 | 旧 orchestrator 只保留读取 API，写入走统一服务 |
| 回滚需求 | 保留 KERNELONE_UNIFIED_ORCH 特性开关 |

## 验证

```bash
# 架构守护测试
pytest tests/refactor/test_architecture_guard.py -v

# 前端类型检查
npm run typecheck

# 后端单元测试
pytest src/backend/tests -x -q
```

## 参考文献

- [统一编排重构计划](../migration/unified-orchestration-plan.md)
- [RoleExecutionKernel 重构](../roles/kernel-refactor.md)
