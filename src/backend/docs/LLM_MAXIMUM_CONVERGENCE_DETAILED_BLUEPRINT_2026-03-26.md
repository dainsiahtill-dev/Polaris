# LLM 模块最大化收敛最细化蓝图（Detailed Blueprint）

- Status: Design Frozen / Ready to Implement
- Date: 2026-03-26
- Parent Plan: `docs/LLM_MAXIMUM_CONVERGENCE_IMPLEMENTATION_MASTER_PLAN_2026-03-26.md`

> 本蓝图是“文件级 + 变更单元级”实施图，不是 graph truth。  
> 若与 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 冲突，以后者为准。

---

## 1. 蓝图目标

在不引入第二套真相和不扩大兼容债务的前提下，完成 LLM 模块最大化收敛：

1. 收敛为单一 Provider 运行时管理链路。
2. 清空 KernelOne 中 Polaris 角色业务语义。
3. 将 LLM Cell 的跨边界调用全部合同化。
4. 彻底移除 domain 层重复 LLM 契约定义。
5. 完成代码事实到治理事实的全量对齐。

---

## 2. 当前态与目标态依赖图

## 2.1 当前态（问题态）

```text
delivery/http/routers
  -> infrastructure.llm.providers.provider_manager        (direct)
  -> kernelone.llm.config_store/runtime_config            (direct)

cells.llm.*
  -> kernelone.llm.*                                      (many direct imports)
  -> infrastructure.llm.providers.provider_manager        (some direct paths)

kernelone.llm.toolkit.integrations
  -> Polaris role business semantics                  (violation)

domain/services/llm_provider.py
  -> duplicate LLM contracts                              (drift source)
```

## 2.2 目标态（收敛态）

```text
delivery/http/routers
  -> cells.llm.control_plane.public.service
  -> cells.llm.provider_config.public.service
  -> cells.llm.provider_runtime.public.service

cells.llm.*
  -> cells.*.public.service / public.contracts (cross-cell)
  -> kernelone.llm (only minimal whitelisted kernel capabilities)

kernelone.llm
  -> platform-agnostic runtime only
  -> no Polaris role business logic

infrastructure.llm
  -> provider adapters + bootstrap wiring only
  -> no primary provider manager authority

domain
  -> no duplicated LLM provider contracts
```

---

## 3. 设计原则（Blueprint Constraints）

1. Cell First：跨 Cell 访问只走 `public/contracts.py` 和 `public/service.py`。
2. KernelOne Pure：KernelOne 不承载 Polaris 业务角色语义。
3. Reuse Order：`existing cell public` > `kernelone contract` > `new implementation`。
4. One Authority：同一核心能力只保留一个主实现。
5. Compatibility Discipline：允许临时 shim，但必须带退役窗口与删除条件。

---

## 4. 变更工作流与变更单元（Change Unit）

## WS-A：Provider Runtime 单一化（最高优先级）

### CU-A01 删除纯转发文件

- Files:
  - Delete `polaris/infrastructure/llm/providers/base_provider.py`
  - Delete `polaris/infrastructure/llm/providers/stream_thinking_parser.py`
- Preconditions:
  - 所有 import 迁移完成
  - 目标测试通过
- Validation:
  - `rg -n "infrastructure\\.llm\\.providers\\.base_provider|infrastructure\\.llm\\.providers\\.stream_thinking_parser" polaris tests` -> 0

### CU-A02 收敛 ProviderManager 主实现

- Source of Truth:
  - Keep `polaris/kernelone/llm/providers/registry.py`
- Files to Refactor:
  - `polaris/infrastructure/llm/providers/provider_registry.py`
  - `polaris/infrastructure/llm/providers/__init__.py`
  - `polaris/infrastructure/llm/provider_bootstrap.py`
  - `polaris/infrastructure/llm/provider_runtime_adapter.py`
- Target:
  - infrastructure 层只负责 provider class 注册与 runtime 注入，不再承担主 manager 能力。

### CU-A03 统一对外 Provider 能力出口

- New/Adjusted public surface:
  - `polaris/cells/llm/provider_runtime/public/service.py`
- 需覆盖的能力：
  1. `list_provider_info`
  2. `get_provider_info`
  3. `get_provider_default_config`
  4. `validate_provider_config`
  5. `migrate_legacy_config`
  6. `health_check_all`
  7. `get_provider_manager`（仅作为 cell-internal bridge 暴露，不鼓励 delivery 直接使用）

### CU-A04 delivery 路由收口

- Files:
  - `polaris/delivery/http/routers/providers.py`
  - `polaris/delivery/http/routers/llm.py`
- Target:
  - 不再 `from polaris.infrastructure.llm.providers import provider_manager`
  - 统一改为调用 `cells.llm.provider_runtime.public.service`。

---

## WS-B：KernelOne 角色业务剥离

### CU-B01 拆分 toolkit integrations

- Source:
  - `polaris/kernelone/llm/toolkit/integrations.py`
- Target:
  - `polaris/cells/llm/tool_runtime/internal/role_integrations.py`（建议）
  - 或 `polaris/cells/roles/runtime/internal/*`（当角色运行时语义更强时）
- Move:
  - `PMToolIntegration`
  - `ArchitectToolIntegration`
  - `ChiefEngineerToolIntegration`
  - `DirectorToolIntegration`
  - `QAToolIntegration`
  - `ScoutToolIntegration`
  - `ToolEnabledLLMClient`
  - 角色 prompt 增强函数

### CU-B02 清理 toolkit 导出面

- File:
  - `polaris/kernelone/llm/toolkit/__init__.py`
- Target:
  - 移除业务角色导出，保留平台通用导出：
    - definitions/parsers/executor
    - tool normalization/protocol kernel
    - native function calling
    - audit contracts

### CU-B03 调用方重定向

- Files:
  - `polaris/delivery/cli/director/director_llm_tools.py`
  - `polaris/delivery/cli/pm/chief_engineer_llm_tools.py`
  - 其他引用 `kernelone.llm.toolkit.integrations` 路径
- Target:
  - 改为调用 Cell 层角色工具集成服务。

---

## WS-C：LLM Cell 边界硬收敛

### CU-C01 `llm.control_plane` 去旁路

- Files:
  - `polaris/cells/llm/control_plane/internal/tui_llm_client.py`
  - `polaris/cells/llm/control_plane/public/service.py`
- Current issue:
  - 直连 `kernelone.llm.runtime_config` 与 `kernelone.llm.config_store`
- Target:
  - 通过 `llm.provider_config` 公共服务获取角色绑定与 provider context
  - `load_llm_config_port` 通过 cell facade 间接访问，避免 direct kernelone import 泄漏到 public 层。

### CU-C02 `llm.dialogue` 去旁路

- Files:
  - `polaris/cells/llm/dialogue/internal/docs_dialogue.py`
  - `polaris/cells/llm/dialogue/internal/docs_suggest.py`
  - `polaris/cells/llm/dialogue/internal/role_dialogue.py`
- Current issue:
  - 多处直连 `kernelone.llm.engine/toolkit/runtime_config`
- Target:
  - 统一通过：
    - `llm.provider_runtime.public.service`
    - `llm.tool_runtime.public.service`
    - `llm.provider_config.public.service`
  - 将 role-model 解析从 runtime_config 直接调用迁移到 provider_config 合同查询。

### CU-C03 `llm.evaluation` 去旁路

- Files:
  - `polaris/cells/llm/evaluation/internal/runner.py`
  - `polaris/cells/llm/evaluation/internal/readiness_tests.py`
  - `polaris/cells/llm/evaluation/internal/interview.py`
- Rule:
  - `kernelone.llm.embedding` 可作为白名单保留（已在 cell 依赖声明中）
  - 其他 engine/config 路径迁移到 cell 公共服务。

### CU-C04 `llm.tool_runtime` 合同化增强

- File:
  - `polaris/cells/llm/tool_runtime/internal/orchestrator.py`
- Current issue:
  - 结构上仍暴露 kernel type 到上层调用面
- Target:
  - 对外返回 cell-defined DTO/result
  - 将 kernel types 限制在 orchestrator 内部适配层。

### CU-C05 `llm.provider_runtime` 桥接清理

- Files:
  - `polaris/cells/llm/provider_runtime/internal/provider_actions.py`
  - `polaris/cells/llm/provider_runtime/internal/providers.py`
  - `polaris/cells/llm/provider_runtime/internal/runtime_invoke.py`
- Target:
  - 去掉 infrastructure provider_manager 的直接依赖
  - 统一通过 kernelone provider manager + adapter bridge。

---

## WS-D：Domain 重复定义清零

### CU-D01 清退重复契约

- File:
  - `polaris/domain/services/llm_provider.py`
- Strategy:
  1. 第一步：替换为 Deprecated shim（明确 warning + 删除日期）
  2. 第二步：全仓调用迁移后删除 shim

### CU-D02 清理聚合导出

- Files:
  - `polaris/domain/services/__init__.py`
  - `polaris/domain/__init__.py`
  - `polaris/application/__init__.py`
- Target:
  - 移除 `LLMProvider/LLMResponse/ProviderConfig` 的 domain 侧重复导出。

---

## WS-E：治理资产同步

### CU-E01 graph 同步

- Files:
  - `docs/graph/catalog/cells.yaml`
  - `docs/graph/subgraphs/execution_governance_pipeline.yaml`
  - 必要时 `docs/graph/subgraphs/context_plane.yaml`
- Focus:
  - LLM 子 Cell 依赖与 gap 描述按真实实现更新。

### CU-E02 cell manifest 与 pack 同步

- Files:
  - `polaris/cells/llm/*/cell.yaml`
  - `polaris/cells/llm/*/generated/descriptor.pack.json`
  - 必要时 `impact.pack.json`、`verify.pack.json`

### CU-E03 governance CI 同步

- Files:
  - `docs/governance/ci/fitness-rules.yaml`
  - `docs/governance/ci/pipeline.template.yaml`
  - 必要时 architecture allowlist

---

## 5. 文件级改造矩阵（Current -> Target）

| Current Path | Problem | Target Ownership | Action |
| --- | --- | --- | --- |
| `polaris/infrastructure/llm/providers/base_provider.py` | pure forwarder | remove | delete |
| `polaris/infrastructure/llm/providers/stream_thinking_parser.py` | pure forwarder | remove | delete |
| `polaris/infrastructure/llm/providers/provider_registry.py` | duplicate manager | kernelone providers registry | reduce/bootstrap-only or retire |
| `polaris/kernelone/llm/toolkit/integrations.py` | business logic in kernelone | `cells.llm.tool_runtime` / `cells.roles.runtime` | move out |
| `polaris/kernelone/llm/toolkit/__init__.py` | business exports | kernelone generic only | trim exports |
| `polaris/cells/llm/control_plane/public/service.py` | direct kernelone config import in public layer | provider_config facade | refactor |
| `polaris/cells/llm/dialogue/internal/*.py` | direct kernelone engine/runtime imports | provider_runtime/tool_runtime/provider_config public services | refactor |
| `polaris/cells/llm/evaluation/internal/*.py` | direct kernelone engine/config imports | cell public services + whitelist embedding | refactor |
| `polaris/delivery/http/routers/providers.py` | direct infra manager | provider_runtime public service | refactor |
| `polaris/delivery/http/routers/llm.py` | direct infra manager + config store | control_plane/provider_config/provider_runtime | refactor |
| `polaris/domain/services/llm_provider.py` | duplicate contracts | kernelone shared contracts + cell contracts | remove |

---

## 6. 测试与门禁蓝图

## 6.1 Phase Gate（每阶段必跑）

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`
2. `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
3. `python -m pytest -q tests/architecture/test_kernelone_llm_contract_reexports.py`

## 6.2 WS-A Provider 专项

1. `python -m pytest -q tests/test_provider_registry.py tests/test_provider_bootstrap.py tests/test_llm_provider_actions.py tests/test_enhanced_providers.py`

## 6.3 WS-B Toolkit/Role 专项

1. `python -m pytest -q polaris/kernelone/llm/toolkit/tests/test_integration.py`
2. `python -m pytest -q tests/test_llm_toolkit_native_function_calling.py tests/test_llm_toolkit_executor_safety.py tests/test_llm_toolkit_executor_file_events.py`

## 6.4 WS-C Cell 边界专项

1. `python -m pytest -q tests/test_llm_cell_public_services.py tests/test_llm_phase0_regression.py tests/test_llm_provider_request_context.py tests/test_llm_evaluation_runner_provider_cfg.py`
2. `python -m pytest -q tests/architecture/test_scope_cell_dependency_alignment.py tests/architecture/test_architecture_invariants.py`

## 6.5 WS-E 治理同步专项

1. `python -m pytest -q tests/architecture/test_catalog_governance_gate.py tests/architecture/test_graph_reality.py`
2. `python docs/governance/ci/scripts/run_catalog_governance_gate.py`
3. `python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`（触及 descriptor 时）

---

## 7. 查询式验收脚本（DoD 快速核查）

1. ProviderManager 唯一性：
   - `rg -n "class ProviderManager" polaris/kernelone/llm polaris/infrastructure/llm polaris/domain`
2. 纯转发残留：
   - `rg -n "infrastructure\\.llm\\.providers\\.base_provider|infrastructure\\.llm\\.providers\\.stream_thinking_parser" polaris tests`
3. KernelOne 业务角色污染：
   - `rg -n "PMToolIntegration|ArchitectToolIntegration|ChiefEngineerToolIntegration|DirectorToolIntegration|QAToolIntegration|ScoutToolIntegration" polaris/kernelone/llm/toolkit`
4. LLM Cell 直连 kernelone 检查：
   - `rg -n "from polaris\\.kernelone\\.llm" polaris/cells/llm`
5. Domain 重复契约检查：
   - `rg -n "class LLMResponse|class ProviderConfig|class LLMProvider" polaris/domain/services/llm_provider.py`

---

## 8. 提交蓝图（Recommended Commit Sequence）

1. `chore(llm): add verification card + adr for maximum convergence`
2. `refactor(llm-provider): unify provider manager authority in kernelone`
3. `refactor(llm-provider): migrate delivery routers to provider_runtime public service`
4. `refactor(llm-provider): remove forwarding provider files`
5. `refactor(kernelone-toolkit): extract role integrations to cell layer`
6. `refactor(kernelone-toolkit): trim business exports from toolkit __init__`
7. `refactor(llm-cell): migrate control_plane boundary to provider_config contracts`
8. `refactor(llm-cell): migrate dialogue boundary to provider_runtime/tool_runtime`
9. `refactor(llm-cell): migrate evaluation boundary and keep embedding whitelist`
10. `refactor(domain): remove duplicated llm provider contracts`
11. `docs(graph): sync cells/subgraphs with converged llm boundaries`
12. `chore(governance): refresh descriptor packs and architecture gates`

---

## 9. 回滚蓝图（Rollback by Layer）

1. Layer-1（Provider）回滚
   - 回滚到“删除转发文件前”的 tag
2. Layer-2（Toolkit/Role）回滚
   - 回滚 role integration 抽离提交
3. Layer-3（Cell Boundary）回滚
   - 按子 Cell 粒度回滚，不做整仓回滚
4. Layer-4（Governance）回滚
   - graph/governance 资产与代码提交必须同步回滚

禁止：

1. `git reset --hard` 式破坏性回滚
2. 回退旧实现复制到新路径“临时过门禁”
3. 在新旧路径双修长期共存

---

## 10. 证据包蓝图（Execution Evidence Package）

每个 WS 结束必须记录：

1. `before/after` 导入矩阵
2. `before/after` 依赖查询结果
3. 关键测试命令与结果摘要
4. graph/cell manifest 变更点
5. 回滚点标记

建议存放：

- `runtime/evidence/llm-convergence/<phase>/<timestamp>/...`

---

## 11. 最终完成判据

1. 架构判据：
   - KernelOne 无业务角色语义
   - LLM Provider 管理链路单一
   - LLM Cell 跨边界调用合同化
2. 代码判据：
   - 纯转发文件清零
   - domain 重复契约清零
3. 治理判据：
   - graph/cell/gov/descriptor 与代码一致
4. 质量判据：
   - 所有 Phase Gate 与专项回归通过

---

## 12. 附录：执行中的硬规则

1. 任何文本读写显式 UTF-8。
2. 每个结构性修复先写 Verification Card，再动代码。
3. 任何跨 Cell 边界变化必须同步 graph 与 cell manifest。
4. 发现“新增兼容入口/旧路径续命”立即拒绝，回到 canonical path。


