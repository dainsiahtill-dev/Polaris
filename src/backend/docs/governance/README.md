# Governance Draft

本目录承载 ACGA 的第一版治理资产。

目标不是立刻把所有规则都接进 CI，而是先把规则写成：

1. 可验证的数据结构
2. 可扩展的 schema
3. 可逐步落地的门禁清单

## 目录说明

- `docs/governance/schemas/`
  `cell.yaml`、Cell catalog、Subgraph、Context Pack、Semantic Descriptor、Migration Ledger、Cell Evolution Proposal、Cell Evolution Decision Log 的 schema 草案
- `docs/governance/decisions/`
  架构与治理 ADR。结构性 bug 的长期裁决必须落在这里
- `docs/governance/schemas/plugin.schema.yaml`
  External Cell Plugin 包级清单 `plugin.yaml` 的 schema（实验可执行）
- `docs/governance/schemas/debt-register.schema.yaml`
  治理债务台账 schema
- `docs/governance/schemas/verify-pack.schema.yaml`
  Cell `generated/verify.pack.json` schema
- `docs/governance/ci/fitness-rules.yaml`
  第一版 Fitness Rule 清单
- `docs/governance/ci/pipeline.template.yaml`
  第一版 CI/审计流水线模板
- `docs/governance/TOOL_CALLING_CANONICAL_GATE_STANDARD.md`
  Tool Calling canonical 身份门禁标准与执行方式
- `docs/governance/ci/scripts/run_tool_calling_canonical_gate.py`
  基于 tool_calling_matrix 审计包的 raw tool identity 门禁
- `docs/governance/CONTEXT_OS_COGNITIVE_RUNTIME_EVAL_SUITE.md`
  Context OS + Cognitive Runtime 的统一评测与 rollout gate 说明
- `docs/governance/ci/context-os-runtime-eval-gate.yaml`
  Context OS + Cognitive Runtime 的统一门禁阈值配置
- `docs/governance/schemas/context-os-runtime-eval-suite.schema.yaml`
  评测用例集 schema
- `docs/governance/schemas/context-os-runtime-eval-report.schema.yaml`
  评测结果报告 schema
- `docs/governance/ci/external_plugin_pipeline.template.yaml`
  External Cell Plugin 专用准入流水线模板（当前分支可执行）
- `docs/governance/debt.register.yaml`
  结构性治理债务台账
- `docs/governance/STRUCTURAL_BUG_PROTOCOL.md`
  结构性 bug 的强制交付协议
- `docs/governance/templates/debt.register.template.yaml`
  debt register 样板
- `docs/governance/templates/verification-cards/`
  结构性 bug 验证卡模板与实例
- `docs/governance/CELL_EVOLUTION_ROLLOUT_CHECKLIST.md`
  Cell Evolution 从设计说明推进到治理落地的执行清单
- `docs/migration/ledger.yaml`
  迁移执行状态的机器事实源
- `docs/MIGRATION_LEDGER.md`
  面向人类的迁移看板镜像

## 架构真相 vs 迁移真相

迁移期必须明确分离：

1. `docs/graph/catalog/cells.yaml`
   当前架构事实
2. `docs/FINAL_SPEC.md`
   目标架构裁决
3. `docs/ARCHITECTURE_REBUILD_PLAN.md`
   重建方案与波次
4. `docs/migration/ledger.yaml`
   迁移执行状态

不要把“是否迁完”直接混进 `cells.yaml`。  
`cells.yaml` 负责架构事实，`ledger.yaml` 负责迁移事实。

## AI / Agent 协作要求

后续由 AI / Agent 执行迁移时，至少要遵守：

1. 先读 `docs/AGENT_ARCHITECTURE_STANDARD.md`，再执行迁移任务
2. 先查 `cells.yaml` 和 subgraph，再查 `ledger.yaml`
3. 每次迁移必须同步更新 `ledger.yaml` 和 `docs/MIGRATION_LEDGER.md`
4. `target.catalog_status = missing` 的单元，不得直接推进到 `code_moved`
5. 达到 `imports_switched` 或 `shim_only` 的单元，legacy 文件必须补迁移头标记
6. 任何新的 public Cell 或新副作用，都要同步更新 graph 与 governance 资产
7. 所有 Cell 开发先复用已有 Cell 公开能力，所有新开发必须基于 KernelOne 底座能力

## 设计原则

1. 规则优先写成机器可读数据，而不是散落在自然语言文档里
2. 当前仓库未完全节点化，因此 schema 对迁移期字段保持兼容
3. 规则必须允许“当前存在 gap，但 gap 必须被显式记录”
4. 结构性 bug 必须留下可追踪的治理资产，而不是只留在聊天记录里

## 当前状态

本目录内容属于 `draft v1`：

- 可以作为正式规范入口
- 已落地一套最小可执行校验器（外部插件准入守卫）
- 还没有与仓库现有测试/脚本完全接线

### 已实现的最小可执行能力（2026-03-21）

1. `plugin.yaml` schema：
   - `docs/governance/schemas/plugin.schema.yaml`
2. 外部插件准入守卫 CLI：
   - `python -m polaris.bootstrap.governance.architecture_guard_cli check_external_plugin --plugin-root <path> --mode hard-fail`
3. 覆盖测试：
   - `tests/test_external_cell_architecture_guard_cli.py`

注意：当前 CLI 只实现 `check_external_plugin` 子命令，不代表历史模板里的所有命令都已可执行。

### 结构性 bug 治理闭环（2026-03-25）

以下资产已纳入仓库真相：

1. debt register：
   - `docs/governance/debt.register.yaml`
2. structural bug protocol：
   - `docs/governance/STRUCTURAL_BUG_PROTOCOL.md`
3. verify pack schema：
   - `docs/governance/schemas/verify-pack.schema.yaml`
4. debt register schema：
   - `docs/governance/schemas/debt-register.schema.yaml`
5. roles.kernel verify pack：
   - `polaris/cells/roles/kernel/generated/verify.pack.json`
6. 结构性 bug ADR：
   - `docs/governance/decisions/adr-0042-turn-engine-triple-responsibility.md`
   - `docs/governance/decisions/adr-0043-structural-bug-governance-loop.md`

## 下一步建议

1. 为 `docs/graph/catalog/cells.yaml` 编写真正的 schema 校验脚本
2. 为 `docs/migration/ledger.yaml` 编写真正的 schema 校验和冲突检测脚本
3. 将 `fitness-rules.yaml` 映射到现有 `pytest`、`rg`、静态扫描脚本
4. 为关键 Cell 生成真实 `context.pack.json`
5. 为 `context.catalog` 描述卡缓存接入 `semantic-descriptor.schema.yaml` 与 freshness 校验脚本（`docs/scripts/check_context_catalog_descriptor_cache.py`）
6. 将 Cell Evolution Proposal / Decision Log schema 接入审计流水线
