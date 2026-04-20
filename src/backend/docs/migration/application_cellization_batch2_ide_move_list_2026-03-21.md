# Application Cell 化第二批 IDE 移动清单（2026-03-21）

## 1. 当前基线（第一批后）

- `polaris/application/**/*.py` 总数：`252`
- 未被 `cells.yaml` 覆盖文件：`82`
- 去掉 `__init__.py` 后未覆盖实现文件：`62`

本清单目标：把剩余高价值业务实现继续收敛到 Cell，优先减少 `application/services`、`application/role_agent`、`application/resident` 的核心逻辑承载。

---

## 2. Batch-2A（低风险，优先执行）

这批都可直接并入**已存在 Cell**，建议先做。

### 2.1 `director.execution`

- `polaris/application/services/director_logic.py`
  -> `polaris/cells/director/execution/internal/director_logic_rules.py`

### 2.2 `audit.evidence`

- `polaris/application/services/audit_llm_runtime.py`
  -> `polaris/cells/audit/evidence/internal/task_audit_llm_binding.py`

### 2.3 `runtime.projection`

- `polaris/application/services/workspace_context.py`
  -> `polaris/cells/runtime/projection/internal/workspace_runtime_context.py`

### 2.4 `roles.runtime`

- `polaris/application/role_agent/skill.py`
  -> `polaris/cells/roles/runtime/internal/skill_loader.py`
- `polaris/application/role_agent/tui/agent_tui.py`
  -> `polaris/cells/roles/runtime/internal/tui_console.py`
- `polaris/application/role_agent/__main__.py`
  -> `polaris/cells/roles/runtime/internal/standalone_entry.py`

### 2.5 `orchestration.pm_planning`

- `polaris/application/role_agent/pm_agent.py`
  -> `polaris/cells/orchestration/pm_planning/internal/pm_agent.py`

### 2.6 `llm.control_plane`

- `polaris/application/role_agent/hr_agent.py`
  -> `polaris/cells/llm/control_plane/internal/llm_config_agent.py`
- `polaris/application/role_agent/tui/llm_client.py`
  -> `polaris/cells/llm/control_plane/internal/tui_llm_client.py`

### 2.7 `finops.budget_guard`

- `polaris/application/role_agent/cfo_agent.py`
  -> `polaris/cells/finops/budget_guard/internal/budget_agent.py`

---

## 3. Batch-2B（新增 Cell 后执行）

### 3.1 新增 Cell：`resident.autonomy`

建议新增目录：

- `polaris/cells/resident/autonomy/cell.yaml`
- `polaris/cells/resident/autonomy/README.agent.md`
- `polaris/cells/resident/autonomy/public/contracts.py`
- `polaris/cells/resident/autonomy/internal/`

建议移动：

- `polaris/application/resident/service.py`
  -> `polaris/cells/resident/autonomy/internal/resident_runtime_service.py`
- `polaris/application/resident/storage.py`
  -> `polaris/cells/resident/autonomy/internal/resident_storage.py`
- `polaris/application/resident/models.py`
  -> `polaris/cells/resident/autonomy/internal/resident_models.py`
- `polaris/application/resident/capability_graph.py`
  -> `polaris/cells/resident/autonomy/internal/capability_graph.py`
- `polaris/application/resident/counterfactual_lab.py`
  -> `polaris/cells/resident/autonomy/internal/counterfactual_lab.py`
- `polaris/application/resident/decision_trace.py`
  -> `polaris/cells/resident/autonomy/internal/decision_trace.py`
- `polaris/application/resident/execution_projection.py`
  -> `polaris/cells/resident/autonomy/internal/execution_projection.py`
- `polaris/application/resident/goal_governor.py`
  -> `polaris/cells/resident/autonomy/internal/goal_governor.py`
- `polaris/application/resident/meta_cognition.py`
  -> `polaris/cells/resident/autonomy/internal/meta_cognition.py`
- `polaris/application/resident/pm_bridge.py`
  -> `polaris/cells/resident/autonomy/internal/pm_bridge.py`
- `polaris/application/resident/self_improvement_lab.py`
  -> `polaris/cells/resident/autonomy/internal/self_improvement_lab.py`
- `polaris/application/resident/skill_foundry.py`
  -> `polaris/cells/resident/autonomy/internal/skill_foundry.py`
- `polaris/application/resident/evidence_bundle_service.py`
  -> `polaris/cells/resident/autonomy/internal/evidence_bundle_service.py`
- `polaris/application/resident/evidence_service.py`
  -> `polaris/cells/resident/autonomy/internal/evidence_service.py`
- `polaris/application/resident/evidence_models.py`
  -> `polaris/cells/resident/autonomy/internal/evidence_models.py`

### 3.2 新增 Cell：`architect.design`

建议新增目录：

- `polaris/cells/architect/design/cell.yaml`
- `polaris/cells/architect/design/README.agent.md`
- `polaris/cells/architect/design/public/contracts.py`
- `polaris/cells/architect/design/internal/`

建议移动：

- `polaris/application/services/architect_service.py`
  -> `polaris/cells/architect/design/internal/architect_service.py`
- `polaris/application/role_agent/architect_agent.py`
  -> `polaris/cells/architect/design/internal/architect_agent.py`
- `polaris/application/role_agent/architect_cli.py`
  -> `polaris/cells/architect/design/internal/architect_cli.py`

---

## 4. Batch-2C（先不做 Cell 化，保持在分层内）

这些更偏“跨 Cell 技术支撑”或“应用边界 DTO/Port”，短期不建议硬塞进业务 Cell：

- `polaris/application/contracts/backend_launch.py`
- `polaris/application/contracts/process_launch.py`
- `polaris/application/ports/backend_bootstrap.py`
- `polaris/application/ports/process_runner.py`
- `polaris/application/schemas/backend.py`
- `polaris/application/types/factory.py`
- `polaris/application/types/runtime_v2.py`
- `polaris/application/artifact_paths.py`
- `polaris/application/file_io.py`
- `polaris/application/settings_utils.py`
- `polaris/application/workspace_utils.py`
- `polaris/application/backend_utils.py`
- `polaris/application/runtime_state_registry.py`
- `polaris/application/utils.py`
- `polaris/application/constants.py`
- `polaris/application/debug_trace.py`

备注：

- `polaris/application/orchestration/support/shared_quality.py` 暂留；它当前被 `pm_planning` 与 `pm_dispatch` 共同使用，建议下一轮先抽成单点质量策略模块后再归属。
- `polaris/application/orchestration/core/process_launcher.py` 建议后续下沉到 `polaris/kernelone/process/`，不建议直接 Cell 化。

---

## 5. 执行顺序（建议）

1. 先执行 Batch-2A（10 个文件，低风险）。
2. 然后更新 `cell.yaml` + `docs/graph/catalog/cells.yaml`（只更新受影响 Cell）。
3. 再执行 Batch-2B（先建 `resident.autonomy`，后建 `architect.design`）。
4. 最后做针对性测试（不要先跑全量）：
   - `tests/test_director_service_convergence.py`
   - `tests/test_pm_orchestration_api.py`
   - `tests/test_roles_kernel.py`
   - `tests/test_runtime_role_binding.py`
   - `tests/test_plan_sync.py`

