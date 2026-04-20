# Application Cell 化盘点报告（2026-03-21）

## 1. 扫描结论

- 扫描范围：`polaris/application/**/*.py`
- 总文件数：`279`
- 已被 `docs/graph/catalog/cells.yaml` 的 `owned_paths` 覆盖：`168`
- 未覆盖：`111`
- 去除 `__init__.py` 后未覆盖真实实现文件：`91`

未覆盖集中区：

- `polaris/application/services`: `23`
- `polaris/application/orchestration`: `17`
- `polaris/application/resident`: `15`
- `polaris/application/role_agent`: `14`

## 2. 图谱治理 gap（需先修）

当前 `cells.yaml` 有 6 条失效 `owned_paths`：

1. `compatibility.legacy_bridge -> app/routers/**`
2. `compatibility.legacy_bridge -> app/llm/**`
3. `compatibility.legacy_bridge -> core/startup/backend_bootstrap.py`
4. `chief_engineer.blueprint -> polaris/infrastructure/legacy_core/code_intelligence.py`
5. `storage.layout -> polaris/kernelone/storage/storage_layout.py`
6. `storage.layout -> polaris/infrastructure/legacy_core/storage/layout.py`

## 3. 第一批可直接 IDE 移动清单（高确定性）

目标：先把高频调用且边界清晰的实现收敛到已存在 Cell，避免继续在 `application/services` 形成“新旧混合核心”。

### 3.1 收敛到 `director.execution`

- `polaris/application/services/task_service.py`
  -> `polaris/cells/director/execution/internal/task_lifecycle_service.py`
- `polaris/application/services/worker_service.py`
  -> `polaris/cells/director/execution/internal/worker_pool_service.py`
- `polaris/application/services/worker_executor.py`
  -> `polaris/cells/director/execution/internal/worker_executor.py`
- `polaris/application/services/code_generation_engine.py`
  -> `polaris/cells/director/execution/internal/code_generation_engine.py`
- `polaris/application/services/file_apply_service.py`
  -> `polaris/cells/director/execution/internal/file_apply_service.py`
- `polaris/application/services/unified_apply.py`
  -> `polaris/cells/director/execution/internal/patch_apply_engine.py`
- `polaris/application/services/bootstrap_template_catalog.py`
  -> `polaris/cells/director/execution/internal/bootstrap_template_catalog.py`
- `polaris/application/services/repair_service.py`
  -> `polaris/cells/director/execution/internal/repair_service.py`

### 3.2 收敛到 `orchestration.workflow_runtime`

- `polaris/application/orchestration/services/orchestration_service_impl.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/unified_orchestration_service.py`
- `polaris/application/orchestration/adapters/workflow_client.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/workflow_client.py`
- `polaris/application/orchestration/adapters/workflow_adapters.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/decorator_adapters.py`
- `polaris/application/orchestration/adapters/embedded_api.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/embedded_api.py`
- `polaris/application/orchestration/models.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/models.py`
- `polaris/application/orchestration/contracts/orchestration_contracts.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/runtime_contracts.py`
- `polaris/application/orchestration/runtime_adapter.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/runtime_backend_adapter.py`
- `polaris/application/orchestration/config.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/config.py`
- `polaris/application/orchestration/ports/orchestration_service.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/ports.py`
- `polaris/application/orchestration/usecases/runtime_queries.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/runtime_queries.py`
- `polaris/application/orchestration/core/observability.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/observability.py`
- `polaris/application/orchestration/support/ui_state_contract.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/ui_state_contract.py`
- `polaris/application/orchestration/telemetry/task_trace.py`
  -> `polaris/cells/orchestration/workflow_runtime/internal/task_trace.py`

### 3.3 收敛到 `docs.court_workflow`

- `polaris/application/orchestration/services/docs_stage_service.py`
  -> `polaris/cells/docs/court_workflow/internal/docs_stage_service.py`
- `polaris/application/orchestration/templates/plan_template.py`
  -> `polaris/cells/docs/court_workflow/internal/plan_template.py`

### 3.4 收敛到 `roles.runtime`

- `polaris/application/role_agent/base.py`
  -> `polaris/cells/roles/runtime/internal/agent_runtime_base.py`
- `polaris/application/role_agent/protocol.py`
  -> `polaris/cells/roles/runtime/internal/protocol_fsm.py`
- `polaris/application/role_agent/service.py`
  -> `polaris/cells/roles/runtime/internal/role_agent_service.py`
- `polaris/application/role_agent/worker.py`
  -> `polaris/cells/roles/runtime/internal/worker_pool.py`
- `polaris/application/role_agent/standalone_agent.py`
  -> `polaris/cells/roles/runtime/internal/standalone_runner.py`

## 4. 第二批（建议新增 Cell 后再移动）

这些能力已经成团，但当前 graph 尚未显式声明对应 public cell。

### 4.1 建议新增 `resident.autonomy`

建议候选 `owned_paths`：

- `polaris/application/resident/**`

### 4.2 建议新增 `architect.design`

建议候选 `owned_paths`：

- `polaris/application/services/architect_service.py`
- `polaris/application/role_agent/architect_agent.py`

## 5. 暂缓项（先不急着 Cell 化）

这些更适合下沉到 `kernelone` 或作为跨 Cell 技术基建，不建议直接放业务 Cell：

- `polaris/application/contracts/process_launch.py`
- `polaris/application/ports/process_runner.py`
- `polaris/application/orchestration/core/process_launcher.py`
- `polaris/application/main.py`
- `polaris/application/bootstrap.py`
- `polaris/application/utils.py`（需先拆分再归属）

## 6. 执行顺序建议

1. 先做本报告第 3 节的 IDE 物理移动（只移动，不改逻辑）。
2. 再统一修 import（IDE 自动重构）。
3. 然后更新每个目标 Cell 的 `cell.yaml -> owned_paths/current_modules`。
4. 最后补每个 Cell 的最小 smoke 测试与 `generated/descriptor.pack.json` 刷新。

