# Polaris W0 基线文档

**生成日期**: 2026-03-06
**目的**: 记录真实现状、兼容层、门禁失真点，形成所有 Agent 的共同基线

---

## 1. 核心状态 API 生产者/消费者/真源映射

### 1.1 /state/snapshot

| 属性 | 值 |
|------|-----|
| **路由** | `GET /state/snapshot` |
| **文件位置** | `src/backend/app/routers/system.py:196-210` |
| **生产者** | `app/services/runtime_projection.py::RuntimeProjectionService` → `build_snapshot_payload_from_projection()` |
| **真源属性** | 统一运行态投影，聚合 PM/Director/Workflow/Engine 状态 |
| **消费者** | 前端 UI 状态面板、PM/Director 工作区 |

**数据来源** (统一单一入口):
- `RuntimeProjectionService.build()` - 唯一状态聚合入口
- PM 本地状态 (`get_pm_local_status()`)
- Director 本地状态 (`get_director_local_status()`)
- Workflow 归档 (`get_workflow_runtime_status()`)
- Engine 回退状态 (`build_engine_status()`)

**注意**: 禁止各自拼状态，所有状态消费必须通过 `RuntimeProjection`

### 1.2 /v2/pm/status

| 属性 | 值 |
|------|-----|
| **路由** | `GET /v2/pm/status` |
| **文件位置** | `src/backend/api/v2/pm.py:141-144` |
| **生产者** | `PMService.get_status()` |
| **真源属性** | PM 进程运行状态、loop 模式、resume 状态 |
| **消费者** | PM 工作区前端组件 |

### 1.3 /v2/director/status

| 属性 | 值 |
|------|-----|
| **路由** | `GET /v2/director/status` |
| **文件位置** | `src/backend/api/v2/director.py:163-172` |
| **生产者** | `RuntimeProjectionService` |
| **真源属性** | Director 本地状态，无二重 merge |

**注意**: Director status 现在通过 `RuntimeProjection` 获取，不再在端点层做 merge。
Merge 逻辑已统一收敛到 `runtime_projection.py::merge_director_status()` (单一实现)。

### 1.4 /v2/ws/runtime (WebSocket)

| 属性 | 值 |
|------|-----|
| **路由** | `GET /v2/ws/runtime` |
| **文件位置** | `src/backend/api/v2/runtime_ws.py` |
| **生产者** | Workflow Runtime Store + Event Emitter |
| **真源属性** | 实时任务状态推送、LLM 事件流 |

---

## 2. 单一 Merge 实现 (已收敛)

### 2.1 Director Status Merge

**唯一实现**: `src/backend/app/services/runtime_projection.py`
**函数**: `merge_director_status()`

**Merge 规则**:
1. 若 local_status 处于 RUNNING 状态且有活跃任务 → 以 local 为准
2. 否则 → 以 workflow_status 为准
3. token_budget 和 workers 取并集

**已删除的重复实现**:
- ~~`src/backend/api/v2/director.py` 中的本地 merge~~ (已移除，使用统一实现)

### 2.2 运行态读模型统一入口

**唯一入口**: `RuntimeProjectionService.build()`

```python
projection = RuntimeProjectionService.build(
    workspace=workspace,
    cache_root=cache_root
)
# projection.pm_local - PM 本地状态
# projection.director_local - Director 本地状态
# projection.workflow_archive - Workflow 归档状态
# projection.engine_fallback - Engine 回退状态
```

---

## 3. 当前架构状态

### 3.1 统一编排内核

| 组件 | 状态 | 位置 |
|------|------|------|
| `application/dto/orchestration_contracts.py` | ✅ 已实现 | 统一类型定义 |
| `core/orchestration/orchestration_service_impl.py` | ✅ 已实现 | 服务实现 |
| `app/roles/adapters/` | ✅ 已实现 | 角色适配器 |
| `OrchestrationCommandService` | ✅ 已实现 | 单一执行写路径 |

### 3.2 V2 API 路由

| 路由 | 状态 | 位置 |
|------|------|------|
| `/v2/pm/*` | ✅ 已实现 | `api/v2/pm.py` |
| `/v2/director/*` | ✅ 已实现 | `api/v2/director.py` |
| `/v2/orchestration/*` | ✅ 已实现 | `api/v2/orchestration.py` |
| `/v2/roles/*` | ✅ 已实现 | `app/routers/role_session.py` |
| `/health`, `/ready`, `/live` | ✅ 已实现 | `api/routers/primary.py` |

### 3.3 路由兼容性状态

| 路由 | 状态 | 备注 |
|------|------|------|
| `/pm/*` | ❌ 已废弃 | 返回 410 Gone，迁移到 `/v2/pm/*` |
| `/director/*` | ❌ 已废弃 | 返回 410 Gone，迁移到 `/v2/director/*` |
| `/role/*/chat` | ❌ 已废弃 | 返回 410 Gone，迁移到 `/v2/roles/*/chat` |

**Legacy Tombstone**: `api/routers/legacy_tombstone.py` 提供标准化的 410 响应和迁移指引。

### 3.4 Adoption 状态表 (真实采用情况)

| 功能 | 状态 | 描述 |
|------|------|------|
| BackendLaunchRequest | adopted | DTO 统一完成，application/dto 为真源 |
| RuntimeProjectionService | adopted | 后端已收敛，前端采用中 |
| FactoryRunService | implemented | 后端实现完成，测试通过，主链集成中 |
| export-to-workflow | adopted | 后端实现完成，前端已集成 |
| legacy_tombstone | adopted | 已替换 legacy_bridge |
| useRuntimeSocket | adopted | 前端 canonical WebSocket 钩子 |
| RuntimeProjection (frontend) | partially_adopted | selectors 已创建，useRuntime 改造中 |

**更新时间**: 2026-03-06

---

## 4. 基线检查脚本输出

### 4.1 check_architecture_drift.py

```json
{
  "success": true,
  "errors": [],
  "warnings": [],
  "sys_path_check": {
    "status": "PASSED",
    "violations": [],
    "whitelisted": [
      "scripts/",
      "tests/",
      "core/startup/"
    ]
  },
  "notes": [
    "app/adapters/scripts_pm.py:106 使用 try/finally 模式临时修改 sys.path (允许)"
  ]
}
```

**Phase 3 完成**: 生产代码中的 `sys.path.insert` 已清除，仅剩允许的临时模式。

### 4.2 check_architecture_convergence.py

```json
{
  "timestamp": "2026-03-06T08:00:52.226382+00:00",
  "checks": {
    "state_bridge": {
      "name": "State Bridge",
      "score": 1.0,
      "status": "passed"
    },
    "error_classifier": {
      "name": "Error Classifier",
      "score": 1.0,
      "status": "passed"
    },
    "task_board": {
      "name": "Task Board",
      "score": 1.0,
      "status": "passed"
    },
    "workflow_runtime": {
      "name": "Workflow Runtime",
      "score": 0.833,
      "status": "passed"
    }
  },
  "overall": {
    "passed": true,
    "score": 0.958
  }
}
```

---

## 5. 关键约束

### 5.1 禁止复制的模式 (硬性红线)

1. **禁止生产代码 sys.path.insert** - 仅允许以下模式:
   - `scripts/` 入口点 (启动时)
   - `tests/` 测试文件
   - `core/startup/` 启动模块 (使用 try/finally 清理)
   - **禁止**: `app/`, `api/`, `domain/`, `application/` 中的任何 sys.path 操作

2. **禁止重复状态 merge** - 唯一 merge 逻辑:
   - `app/services/runtime_projection.py::merge_director_status()`

3. **禁止新建角色对话独立文件** - 必须使用:
   - `app/llm/usecases/role_dialogue.py::generate_role_response()`

4. **禁止绕过 OrchestrationCommandService** - 所有执行必须通过:
   - `app/services/orchestration_command_service.py`

### 5.2 兼容层清单

| 模块 | 类型 | 状态 | 说明 |
|------|------|------|------|
| `api/routers/legacy_tombstone.py` | 废弃网关 | 活跃 | 返回 410 Gone 指导迁移 |
| `core/runtime_orchestrator.py` | 废弃 | 已标记 | 使用 `OrchestrationCommandService` |
| `app/routers/role_chat.py` | 废弃 | 已标记 | 使用 `/v2/roles/{role}/chat` |
| `scripts/pm/cli_thin.py` | 兼容 | 活跃 | Thin CLI 入口 |
| `scripts/director/cli_thin.py` | 兼容 | 活跃 | Thin CLI 入口 |

**Sunset 日期**: 2026-06-01 (废弃端点完全移除)

---

## 6. 门禁基线 (Post Phase 0-9)

| 门禁 | 当前状态 | 基线分数 |
|------|----------|----------|
| 架构漂移检查 | ✅ PASS | 0 errors, 0 violations |
| 架构收敛检查 | ✅ PASS | score: 0.958 |
| State Bridge | ✅ PASS | 1.0 |
| Error Classifier | ✅ PASS | 1.0 |
| Task Board | ✅ PASS | 1.0 |
| Workflow Runtime | ✅ PASS | 0.833 |
| 新功能测试 | ✅ PASS | 161+ tests |
| 总测试 | ✅ PASS | 801 passed |

**硬性红线** (fail-closed):
- 生产代码 `sys.path.insert`: 0 violations
- 重复 merge 实现: 0
- 架构守卫测试: 通过

---

## 7. 后端启动配置 (Phase 0 修复)

### 7.1 BackendLaunchRequest 配置处理

**文件**:
- `src/backend/application/dto/backend_launch.py` - 规范 DTO (dataclass)
- `src/backend/app/schemas/backend.py` - API Schema (re-export)

修复内容:
- `app/schemas/backend.py` 从 `application/dto/backend_launch.py` re-export，消除重复定义
- 移除 `__post_init__` 的 workspace existence hard fail
- log_level 验证改为 soft fallback (无效值默认使用 "info")
- 统一配置校验入口移至 `validate()` 方法

```python
# application/dto/backend_launch.py - 规范 DTO
@dataclass(frozen=True)
class BackendLaunchRequest:
    host: str = "127.0.0.1"
    port: int = 0
    workspace: Path = field(default_factory=lambda: Path.cwd())
    # ... 其他字段

    def __post_init__(self) -> None:
        # 只保留规范化逻辑，不抛异常
        valid_levels = {"debug", "info", "warning", "error", "critical"}
        normalized_level = self.log_level.lower()
        if normalized_level not in valid_levels:
            normalized_level = "info"  # Soft fallback
        object.__setattr__(self, "log_level", normalized_level)
        # ...

    def validate(self) -> ConfigValidationResult:
        """Comprehensive validation of launch request"""
        # 校验逻辑统一在这里
```

```python
# app/schemas/backend.py - re-export 规范 DTO
from application.dto.backend_launch import (
    BackendLaunchRequest as _BackendLaunchRequest,
    BackendLaunchResult as _BackendLaunchResult,
)

BackendLaunchRequest = _BackendLaunchRequest
BackendLaunchResult = _BackendLaunchResult
```

### 7.2 统一配置校验入口

**文件**: `application/dto/backend_launch.py`

```python
def validate(self) -> ConfigValidationResult:
    """Comprehensive validation of launch request."""
    result = ConfigValidationResult()

    # Check port range
    if self.port < 0 or self.port > 65535:
        result = result.add_error(f"Invalid port: {self.port}")

    # Check workspace permissions
    if not self.workspace.is_dir():
        result = result.add_error(f"Not a directory: {self.workspace}")
    else:
        # Check writable (best effort on Windows)
        try:
            test_file = self.workspace / ".write_test"
            test_file.write_text("")
            test_file.unlink()
        except (OSError, PermissionError):
            result = result.add_warning(f"Workspace may not be writable")

    return result
```

### 7.3 修复导入

**文件**: `src/backend/core/polaris_loop/runtime_lifecycle.py`

修复内容:
- 将 `from io_utils import` 改为相对导入 `from .io_utils import`
- 添加 try/except 以支持脚本模式回退

---

## 8. 文件变更记录

### Phase 0 新增/修改文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/backend/application/dto/backend_launch.py` | 修改 | 移除 __post_init__ hard fail，soft fallback for log_level |
| `src/backend/app/schemas/backend.py` | 修改 | Re-export from application.dto.backend_launch，消除重复定义 |
| `src/backend/core/polaris_loop/runtime_lifecycle.py` | 修改 | 修复导入为相对导入 |
| `docs/architecture/current-baseline.md` | 修改 | 更新基线文档 |

---

## 9. Phase 0-9 完成汇总

### 9.1 架构目标达成

| 目标 | 实现 | 关键文件 |
|------|------|----------|
| 单一执行写路径 | ✅ | `OrchestrationCommandService` |
| 单一运行态读模型 | ✅ | `RuntimeProjectionService` |
| 单一配置真源 | ✅ | `ConfigLoader.DEFAULTS` |
| 前端 Canonical Contract | ✅ | `RuntimeProjectionPayload` |
| 生产代码无 sys.path | ✅ | Drift Checker 硬性校验 |
| API 装配边界清晰 | ✅ | Primary/Legacy/V2 路由分离 |

### 9.2 新增核心模块

| 模块 | 功能 | 测试 |
|------|------|------|
| `runtime_projection.py` | 统一运行态投影 | 14 tests |
| `orchestration_command_service.py` | 单一执行入口 | 集成测试 |
| `factory_run_service.py` | Factory 服务化 | 21 tests |
| `permission/` | 权限条件/角色图 | 54 tests |
| `role_session_*_service.py` | 会话工件/审计 | 集成测试 |
| `projection.ts` | 前端规范类型 | 35 tests |

### 9.3 废弃模块清单

| 模块 | 替代方案 | Sunset |
|------|----------|--------|
| `core/runtime_orchestrator.py` | `OrchestrationCommandService` | 2025-06-01 |
| `/pm/*`, `/director/*` (旧) | `/v2/pm/*`, `/v2/director/*` | 2025-06-01 |
| `/role/*/chat` | `/v2/roles/{role}/chat` | 2025-06-01 |

---

## 10. 开发者指南

### 10.1 新增运行时状态

```python
# 正确做法 - 使用 RuntimeProjectionService
from app.services.runtime_projection import RuntimeProjectionService

projection = RuntimeProjectionService.build(workspace=".")
print(projection.pm_local)        # PM 本地状态
print(projection.director_local)  # Director 本地状态
print(projection.workflow_archive) # Workflow 归档
```

### 10.2 新增执行命令

```python
# 正确做法 - 使用 OrchestrationCommandService
from app.services.orchestration_command_service import OrchestrationCommandService

service = OrchestrationCommandService(settings)
result = await service.execute_pm_run(workspace=".", run_type="full")
```

### 10.3 前端消费运行态

```typescript
// 正确做法 - 使用 RuntimeProjectionPayload
import { toCanonicalProjection, type RuntimeProjectionPayload } from "@/runtime/projection";

const projection: RuntimeProjectionPayload = toCanonicalProjection(response);
const tasks = selectTaskRows(projection);
const status = selectPrimaryStatus(projection);
```

---

*本文档为 Phase 0-9 完成后的基线，记录于 2026-03-06*
*commit: cd55447 - feat(architecture): 实现架构完善与强收敛计划 v3*
