# LLM 模块最大化收敛实施总计划（Implementation Master Plan）

- Status: Proposed (Ready for Execution)
- Date: 2026-03-26
- Scope:
  - `polaris/kernelone/llm/**`
  - `polaris/infrastructure/llm/**`
  - `polaris/cells/llm/**`
  - `polaris/delivery/http/routers/{llm.py,providers.py,_shared.py}`
  - `polaris/domain/services/llm_provider.py`
  - `docs/graph/**`, `docs/governance/**`, `polaris/cells/llm/**/generated/*`

> 本文是执行计划，不是 graph truth。  
> 当前真实边界以 `AGENTS.md`、`docs/AGENT_ARCHITECTURE_STANDARD.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 为准。

---

## 1. 执行结论

本次采用“最大化收敛”策略，而不是温和重构：

1. 单一权威实现：LLM Provider 运行时管理只保留一套主实现。
2. 平台纯度恢复：`kernelone.llm` 仅保留平台无关能力，移除 Polaris 业务角色语义。
3. Cell 边界闭合：`cells/llm/*` 跨边界仅走 `public/contracts.py` + `public/service.py`。
4. 重复定义清零：清退 `domain/services/llm_provider.py` 的重复契约定义。
5. 治理同步闭环：代码、graph、descriptor、测试门禁一次对齐。

---

## 2. 现状基线（2026-03-26 核验）

### 2.1 已确认问题

1. `kernelone.llm.toolkit.integrations` 承载了 PM/Architect/ChiefEngineer/Director/QA/Scout 角色业务语义，违反 KernelOne 平台无关约束。
2. `ProviderManager` 存在双实现：  
   - `polaris/kernelone/llm/providers/registry.py`  
   - `polaris/infrastructure/llm/providers/provider_registry.py`
3. 纯转发文件存在：  
   - `polaris/infrastructure/llm/providers/base_provider.py`  
   - `polaris/infrastructure/llm/providers/stream_thinking_parser.py`
4. `cells/llm/*` 仍有多处直连 `polaris.kernelone.llm.*`。
5. `polaris/domain/services/llm_provider.py` 存在重复 LLM 契约定义，与收敛方向不一致。

### 2.2 已确认事实（避免误判）

1. 6 个 LLM 子 Cell 的 `cell.yaml`、`public/contracts.py`、`public/service.py`、`context.pack.json`、`generated/descriptor.pack.json` 均已存在。
2. 现阶段重点不是“补空壳”，而是“调用方与实现路径收敛”。

---

## 3. 目标态定义（Maximum Convergence Target State）

### 3.1 架构目标

1. `kernelone/llm/providers/registry.py` 成为唯一 ProviderManager 主实现。
2. `infrastructure/llm/providers/` 仅保留 provider adapter 与 bootstrap 注册逻辑。
3. `kernelone/llm/toolkit/` 仅保留通用工具运行时契约与执行能力：
   - parser
   - executor
   - definitions
   - protocol kernel
   - native function calling
4. 角色工具集成（角色提示、角色 loop、业务语义）全部归入 Cell（`llm.tool_runtime` / `roles.runtime`）。
5. `delivery` 不再直接依赖 infrastructure provider manager 与 kernelone config store，转由 LLM Cell 公共服务提供能力。
6. `domain` 不再维护 LLM provider 重复模型定义。

### 3.2 收敛不变量（Must Hold）

1. 单写原则：source-of-truth 状态仅一个 owner。
2. 单实现原则：核心运行时能力仅一套主实现。
3. 单入口原则：跨 Cell 只走 public contracts/service。
4. 反向依赖禁止：KernelOne 不能反向依赖 cells/domain/delivery/infrastructure 业务实现。
5. UTF-8 强制：全部文本读写显式 UTF-8。

---

## 4. 执行策略与节奏

- 总周期：10~12 周
- 执行方式：Phase Gate + 每阶段独立回滚点 + 每阶段强门禁
- PR 策略：每个 Phase 独立 PR，禁止跨 Phase 混改

---

## 5. Phase 计划（完整落地）

## Phase 0：治理冻结与基线固化（第 1 周）

### 目标

1. 固化改造边界、基线证据、验收门禁。
2. 建立 structural 级修复治理资产（Verification Card + ADR）。

### 任务

1. 生成 Verification Card（建议路径）  
   `docs/governance/templates/verification-cards/vc-20260326-llm-maximum-convergence.yaml`
2. 生成 ADR（建议路径）  
   `docs/governance/decisions/adr-0055-llm-maximum-convergence.md`
3. 产出导入矩阵与重复实现矩阵（附在 ADR）。
4. 冻结测试基线（当前失败/通过清单）。

### Gate

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`
2. `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
3. `python -m pytest -q tests/architecture/test_kernelone_llm_contract_reexports.py`

### 退出标准

1. 基线证据可复现。
2. structural 变更治理资产齐全。

---

## Phase 1：Provider Runtime 单一化收敛（第 2~3 周）

### 目标

1. 消除 provider manager 双实现。
2. 清理 pure forwarder。
3. 收敛 delivery/cell 到统一 provider runtime 公共能力。

### 文件级任务

1. 删除纯转发文件：
   - `polaris/infrastructure/llm/providers/base_provider.py`
   - `polaris/infrastructure/llm/providers/stream_thinking_parser.py`
2. 收敛 `polaris/infrastructure/llm/providers/provider_registry.py`：
   - 去除“主运行时管理器”角色
   - 保留“provider 注册 + bootstrap 协调”最小职责（或完全退役）
3. 将调用方迁移到权威能力：
   - `polaris/delivery/http/routers/providers.py`
   - `polaris/delivery/http/routers/llm.py`
   - `polaris/infrastructure/llm/provider_runtime_adapter.py`
   - `polaris/cells/llm/provider_runtime/internal/provider_actions.py`
4. 将 provider 管理能力通过 `llm.provider_runtime.public.service` 统一暴露。

### 代码约束

1. 禁止新增对 `polaris.infrastructure.llm.providers.provider_manager` 的直接依赖。
2. `delivery` 层只依赖 Cell 公共服务。

### Gate

1. `python -m pytest -q tests/test_provider_registry.py tests/test_provider_bootstrap.py tests/test_llm_provider_actions.py`
2. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`
3. `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

### 自动验收查询

1. `rg -n "class ProviderManager" polaris/kernelone/llm polaris/infrastructure/llm polaris/domain`
   - 期望：仅保留 1 套主实现
2. `rg -n "infrastructure\\.llm\\.providers\\.base_provider|infrastructure\\.llm\\.providers\\.stream_thinking_parser" polaris tests`
   - 期望：0

### 回滚点

1. 删除转发文件前单独打 tag。
2. manager 收敛后单独打 tag。

---

## Phase 2：KernelOne 业务语义剥离（第 4~6 周）

### 目标

1. 将角色业务工具集成从 KernelOne 完整迁出。
2. `kernelone.llm.toolkit` 只保留平台无关能力。

### 文件级任务

1. 迁移来源：
   - `polaris/kernelone/llm/toolkit/integrations.py`
   - `polaris/kernelone/llm/toolkit/__init__.py`
2. 迁移目标（推荐）：
   - `polaris/cells/llm/tool_runtime/internal/role_integrations.py`
   - `polaris/cells/llm/tool_runtime/public/service.py`
   - `polaris/cells/roles/runtime/internal/*`（若角色语义更贴近 roles runtime）
3. 调整调用方：
   - `polaris/delivery/cli/director/director_llm_tools.py`
   - `polaris/delivery/cli/pm/chief_engineer_llm_tools.py`
   - 其他引用 `kernelone.llm.toolkit.integrations` 的路径
4. `kernelone.llm.toolkit.__all__` 清理角色业务导出，仅保留通用 toolkit 导出。

### 代码约束

1. KernelOne 不承载 Polaris 角色 Prompt 语义。
2. 角色集成必须归属 Cell，并以 contract/service 方式提供。

### Gate

1. `python -m pytest -q tests/test_llm_toolkit_native_function_calling.py tests/test_llm_toolkit_executor_safety.py tests/test_llm_toolkit_executor_file_events.py`
2. `python -m pytest -q polaris/kernelone/llm/toolkit/tests/test_integration.py`
3. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`

### 自动验收查询

1. `rg -n "PMToolIntegration|ArchitectToolIntegration|ChiefEngineerToolIntegration|DirectorToolIntegration|QAToolIntegration|ScoutToolIntegration" polaris/kernelone/llm/toolkit`
   - 期望：0（或仅兼容 shim，且有退役日期）

### 回滚点

1. 迁移 integrations 前后分别打 tag。
2. 对外 API 变更必须有 compat 开关，且默认 fail-closed。

---

## Phase 3：LLM Cell 边界硬收敛（第 7~9 周）

### 目标

1. 清理 `cells/llm/*` 直连 `kernelone.llm.*` 的旁路调用。
2. LLM 子 Cell 之间通过 public contracts/service 协作。

### 文件级任务（重点）

1. `polaris/cells/llm/control_plane/internal/tui_llm_client.py`
2. `polaris/cells/llm/control_plane/public/service.py`
3. `polaris/cells/llm/dialogue/internal/{docs_dialogue.py,docs_suggest.py,role_dialogue.py}`
4. `polaris/cells/llm/evaluation/internal/{runner.py,readiness_tests.py,interview.py}`
5. `polaris/cells/llm/tool_runtime/internal/orchestrator.py`
6. `polaris/cells/llm/provider_runtime/internal/{providers.py,runtime_invoke.py,provider_actions.py}`

### 迁移规则

1. 允许直连 KernelOne 的最小白名单：
   - `kernelone.llm.embedding`（已在 `llm.evaluation` depends_on 声明）
   - 其他能力必须通过 Cell service 包装后消费
2. `delivery` 层对 LLM 的 config/runtime 调用统一经 `llm.control_plane` / `llm.provider_config` / `llm.provider_runtime`。

### Gate

1. `python -m pytest -q tests/test_llm_cell_public_services.py tests/test_llm_phase0_regression.py tests/test_llm_provider_request_context.py tests/test_llm_evaluation_runner_provider_cfg.py`
2. `python -m pytest -q tests/architecture/test_scope_cell_dependency_alignment.py tests/architecture/test_architecture_invariants.py`

### 自动验收查询

1. `rg -n "from polaris\\.kernelone\\.llm" polaris/cells/llm`
   - 期望：仅白名单路径存在

### 回滚点

1. 每个子 Cell 独立迁移、独立回滚，不跨 Cell 混改。

---

## Phase 4：Domain 重复定义清零（第 10 周）

### 目标

1. 清退 `polaris/domain/services/llm_provider.py` 重复定义。
2. 清理导出污染，防止旧类型继续扩散。

### 文件级任务

1. `polaris/domain/services/llm_provider.py`（删除或降级为明确弃用 shim）
2. `polaris/domain/services/__init__.py`
3. `polaris/domain/__init__.py`
4. `polaris/application/__init__.py`

### Gate

1. `python -m pytest -q tests/test_kernelone_architecture_cleanup.py tests/test_kernelone_contract_convergence.py`
2. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`

### 自动验收查询

1. `rg -n "class LLMResponse|class ProviderConfig|class LLMProvider" polaris/domain/services/llm_provider.py`
   - 期望：0（或文件已删除）

---

## Phase 5：治理资产同步与收尾（第 11~12 周）

### 目标

1. 代码事实与治理事实一致。
2. descriptor/context/verify 资产刷新。

### 必须同步

1. `docs/graph/catalog/cells.yaml`
2. `docs/graph/subgraphs/*.yaml`（涉及边界变化的子图）
3. `polaris/cells/llm/*/cell.yaml`
4. `docs/governance/ci/fitness-rules.yaml`
5. `docs/governance/ci/pipeline.template.yaml`
6. `polaris/cells/llm/*/generated/{descriptor,impact,verify}.pack.json`（按需要）

### Gate

1. `python -m pytest -q tests/architecture/test_catalog_governance_gate.py tests/architecture/test_graph_reality.py`
2. `python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`（触及 descriptor 时）
3. `python docs/governance/ci/scripts/run_catalog_governance_gate.py`

---

## 6. 风险矩阵与缓解

1. 导入断裂（高）  
   - 缓解：删除转发文件前完成全量 import 改写与静态扫描。
2. Provider 行为回归（高）  
   - 缓解：provider bootstrap + health/model/action 回归测试分层跑。
3. KernelOne 纯度破坏（高）  
   - 缓解：release gate + import fence + ADR 审查。
4. Cell 边界漂移（中）  
   - 缓解：`test_scope_cell_dependency_alignment.py` 与 catalog gate 强制执行。
5. 迁移周期过长（中）  
   - 缓解：Phase 独立 PR，控制单次变更半径。

---

## 7. 完成定义（Definition of Done）

1. ProviderManager 主实现唯一，纯转发文件清零。
2. `kernelone.llm.toolkit` 无 Polaris 角色业务语义。
3. `cells/llm/*` 直连 KernelOne 旁路降到白名单最小集。
4. domain LLM 重复定义清零。
5. graph + governance + descriptor 资产与代码事实一致。
6. 所有强制门禁通过，或失败原因有证据与闭环计划。

---

## 8. 最终审计输出（执行结束时）

执行完成后必须输出 JSON 审计包，最少字段：

```json
{
  "status": "PASS|FAIL",
  "workspace": "src/backend",
  "rounds": 0,
  "pm_quality_history": [],
  "leakage_findings": [],
  "director_tool_audit": {
    "total_calls": 0,
    "unauthorized_blocked": 0,
    "dangerous_commands": 0,
    "findings": []
  },
  "issues_fixed": [],
  "acceptance_results": {
    "phase0_governance": "PASS|FAIL",
    "phase1_provider": "PASS|FAIL",
    "phase2_kernelone_purity": "PASS|FAIL",
    "phase3_cell_boundary": "PASS|FAIL",
    "phase4_domain_cleanup": "PASS|FAIL",
    "phase5_governance_sync": "PASS|FAIL"
  },
  "evidence_paths": {
    "logs": [],
    "snapshots": [],
    "reports": []
  },
  "next_risks": []
}
```

---

## 9. 执行顺序（必须遵守）

1. 先 Phase 0（治理冻结）再写代码。
2. 先 Phase 1（provider 单一化）再动 Cell 边界。
3. Phase 2（KernelOne 业务剥离）与 Phase 3（Cell 边界）可交错，但每次只做一个工作流。
4. Phase 4/5 只能在前序门禁稳定后进行。
5. 任一阶段门禁失败，停止推进并回滚到上一阶段稳定点。


