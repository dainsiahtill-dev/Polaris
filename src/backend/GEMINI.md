# Gemini Backend Playbook

适用范围: `src/backend`  
权威规则: `AGENTS.md`

本文件是 `AGENTS.md` 的镜像摘要，不是独立权威。若冲突，以 `AGENTS.md` 为准。

---

## 1. 裁决顺序

固定按以下顺序裁决：

1. `AGENTS.md`
2. `docs/AGENT_ARCHITECTURE_STANDARD.md`
3. `docs/graph/catalog/cells.yaml` 与 `docs/graph/subgraphs/*.yaml`
4. `docs/FINAL_SPEC.md`
5. ACGA 2.0 文档
6. 2026-04-16 的 Blueprint / VC / ADR

规则：先服从当前 graph，再按 `FINAL_SPEC.md` 判断迁移方向，最后应用 ACGA 2.0 增强规则。

## 2. 最小必要规则

### 2.1 Graph / Cell / KernelOne

1. Graph 是唯一架构真相
2. Cell 是最小自治边界
3. 先复用公开 Cell 能力，再复用 `KernelOne`，最后才新增实现

### 2.2 Public Contract / Effects / UTF-8

1. 跨 Cell 只能走公开契约，禁止直连 `internal/`
2. 文件、数据库、网络、子进程、LLM、Descriptor、Embedding、Index 都是 effect，必须可审计
3. 所有文本读写必须显式 UTF-8

### 2.3 归属与旧根冻结

规范根目录统一落在 `polaris/` 下。  
`app/ core/ scripts/ api/` 视为冻结旧根，不得承载新主实现。

## 3. 默认工作入口

开始中大型修改前，按顺序读取：

1. `docs/AGENT_ARCHITECTURE_STANDARD.md`
2. `docs/graph/catalog/cells.yaml`
3. 相关 subgraph
4. `docs/FINAL_SPEC.md`
5. 任务需要时再读 ACGA 2.0 文档
6. 目标 Cell 的 `cell.yaml`、`README.agent.md`、packs 与公开契约

不要先全仓扫描源码。

## 4. 动手前与验证

开始修改前至少确认：

1. 目标 Cell 或治理资产
2. `owned_paths / depends_on / state_owners / effects_allowed / verification.gaps`
3. 是否触及 Descriptor / Context Plane / Semantic Index

代码改动必须实际运行并通过最小门禁：

1. `ruff check <paths> --fix`
2. `ruff format <paths>`
3. `mypy <paths>`
4. `pytest <tests> -q`

结构性问题遵循 `AGENTS.md §8.6`：Verification Card + 必要 ADR。

### 4.1 两阶段执行模型

1. 先做 `Blueprint & Architecture`
   - 方案先落到 `docs/blueprints/*.md`
   - 至少包含：文本架构图、模块职责、核心数据流、技术理由
2. 再做 `Execution & Implementation`

除极小型纯文字修正外，默认不能跳过 blueprint 直接实现。

### 4.2 工程标准

1. 遵循 Ruff/Black 约束下的现代 PEP 8
2. 清晰命名、单一职责、低耦合、高内聚
3. 类型注解、防御性边界处理、合理异常处理；禁止裸 `except:`
4. 关键类和复杂函数需要清晰 docstring
5. 严禁过度设计、炫技、隐藏副作用、重复代码

### 4.3 任务协议与输出

1. 新需求：交付可生产使用的完整实现
2. 重构：默认无损重构，保持外部接口和行为一致
3. Bug 修复：写清现象、根因、防御性修复
4. 测试：默认 `pytest`，覆盖正常/边界/异常/回归
5. 输出结构默认按：
   - `Result`
   - `Analysis`
   - `Risks & Boundaries`
   - `Testing`
   - `Self-Check`
   - `Future Optimization`

## 5. 交付要求

交付时至少说明：

1. 改了哪个 Cell 或治理资产
2. 是否跨 Cell
3. 是否触及契约 / 状态拥有 / effect / Descriptor / Index
4. 跑了什么验证
5. 还剩哪些风险

---

## 6. 当前架构现实快照（2026-03-28）

> 本节是 `AGENTS.md §15` 的镜像摘要。如有冲突，以 `AGENTS.md` 为准。

### 6.1 Graph 图谱现状

- `migration_status: phase1_public_phase2_composite_phase3_business_cells_declared`
- cells.yaml 声明 Cell：**51 个**
- `polaris/cells/*/generated/descriptor.pack.json` 当前覆盖：**0 / 52**
- `docs/graph/subgraphs/` 当前仅有：
  - `execution_governance_pipeline.yaml`
  - `storage_archive_pipeline.yaml`

### 6.2 polaris/ 快照

- `polaris/bootstrap/`: 14
- `polaris/delivery/`: 181
- `polaris/application/`: 3
- `polaris/domain/`: 40
- `polaris/kernelone/`: 442
- `polaris/infrastructure/`: 144
- `polaris/cells/`: 809
- `polaris/tests/`: 11
- **总计**：约 **1642** 个 Python 文件

### 6.3 测试与主要 gap

- `pytest --collect-only -q`（2026-04-17）：**11860 collected / 0 errors**
- Descriptor 覆盖已提升至 **54 / 54**
- 部分历史 Cell 仍未完成 `depends_on` 对齐（catalog gate 遗留）
- `KERNELONE_` 与 `KERNELONE_` 仍混用

### 6.4 当前工具入口

- Descriptor 批量刷新：`python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`
- KernelOne 发布门禁：`python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
- Catalog 治理门禁：`python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

### 6.5 未登记 Cell（需补充）

- `roles.host`
- `director.delivery`
- `director.runtime`
- `director.planning`
- `director.tasking`

### 6.6 工具治理铁律（保留锚点）

1. 工具别名只能基于**功能等价性**
2. `read_file` 不能作为 `repo_read_head` 的别名
3. 白名单检查必须在别名归一化之前执行
4. 禁止通过别名或参数映射绕过角色工具白名单

---

## 7. CLI 入口点（已更新）

- 后端服务：`python src/backend/server.py --host 127.0.0.1 --port 49977`
- PM CLI：`python -m polaris.delivery.cli.pm.cli`
- Director CLI：`python -m polaris.delivery.cli.director.cli_thin`
- Architect CLI：`python -m polaris.cells.architect.design.internal.architect_cli`
- Chief Engineer CLI：`python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli`
- Console：`python -m polaris.delivery.cli console --backend plain`

---

## 8. 自动化治理工具

### 8.1 Descriptor Pack 批量生成器

`python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`

### 8.2 KernelOne 发布门禁执行器

`python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

### 8.3 Catalog 治理门禁

`python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

### 8.4 当前 CI/CA 门禁矩阵（2026-04-16）

关键 gate：

- `catalog_governance_audit`
- `catalog_governance_fail_on_new`
- `catalog_governance_hard_fail`
- `kernelone_release_gate`
- `delivery_cli_hygiene_gate`
- `opencode_convergence_gate`
- `manifest_catalog_reconciliation_gate`
- `structural_bug_governance_gate`
- `tool_calling_canonical_gate`

补充规则：

1. `agent_instruction_snapshot_consistent` 要求三份指令文件的快照事实一致
2. 修改 `AGENTS.md §15 / §16 / §17` 时必须同步镜像文件

---

## 9. 最新目标态治理裁决（2026-04-16，非当前事实）

> 本节是 `AGENTS.md §17` 的镜像摘要，不是当前现实快照。

### 9.1 权威来源

- `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
- `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
- `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
- `docs/blueprints/AGENT_INSTRUCTION_ALIGNMENT_BLUEPRINT_20260416.md`
- `docs/blueprints/AGENT_INSTRUCTION_COMPACTION_BLUEPRINT_20260416.md`
- `docs/blueprints/AGENT_ENGINEERING_DISCIPLINE_ALIGNMENT_BLUEPRINT_20260416.md`

### 9.2 核心裁决

1. `TransactionKernel` 是唯一 turn 事务执行内核与唯一 commit point
2. 一个 turn 内必须满足：`len(TurnDecisions) == 1`、`len(ToolBatches) <= 1`、`hidden_continuation == 0`
3. ContextOS 目标态固定为 `TruthLog / WorkingState / ReceiptStore / ProjectionEngine`
4. control-plane 字段不得进入 data plane
5. `ContextHandoffPack` 是 canonical handoff contract，`roles.kernel` 禁止再造第二套 handoff schema

### 9.3 镜像规则

1. 本文件不是独立权威
2. 若 `AGENTS.md §15 / §16 / §17 / §18` 更新，必须同步更新本文件

---

## 10. 认知生命体与工程架构对齐（2026-04-17）

> 本节是 `AGENTS.md §18` 的镜像摘要。如有冲突，以 `AGENTS.md` 为准。

### 10.1 核心命题

**"认知生命体（Cognitive Lifeform）"与"认知运行时（Cognitive Runtime）"是 Polaris 工程架构的灵魂与哲学顶层；**
**当前工程架构（`RoleSessionOrchestrator` + `TurnTransactionController` + `DevelopmentWorkflowRuntime` + `StreamShadowEngine`）是灵魂唯一可运行、可观测、可进化的实体化落地形态。**

两者是**上下层映射关系**。

### 10.2 概念 ↔ 工程实体映射

| 抽象概念 | 工程实体 | 作用 |
|---------|---------|------|
| 认知生命体 | `OrchestratorSessionState` + `SessionArtifactStore` | 躯体 + 海马体 + 自我意识 |
| 主控意识 | `RoleSessionOrchestrator` | 前额叶皮层：裁决"此刻该做什么" |
| 心脏 / 单次神经放电 | `TurnTransactionController` + `KernelGuard` | 不可逆的单次思考-行动循环 |
| 肌肉记忆 / 潜意识 | `DevelopmentWorkflowRuntime` | 小脑：自动执行 `read→write→test` 闭环 |
| 潜意识加速器 / 直觉预感 | `StreamShadowEngine` | 神经预激：让"思考"与"行动"时间重叠 |
| 物理法则 / 生存约束 | `ContinuationPolicy` + `KernelGuard` | 防止死循环、资源泄漏、幻觉 |
| 脑电图 / 对外表达 | `TurnEvent` 流 | 实时向人类/UI 暴露内心活动 |

### 10.3 四层正交架构

1. **角色层（Role）** —— 赋予身份
2. **会话编排层（`RoleSessionOrchestrator` + `OrchestratorSessionState`）** —— 赋予主控意识与记忆中枢
3. **专有运行时层（`DevelopmentWorkflowRuntime`）** —— 赋予肌肉记忆与潜意识闭环
4. **事务内核层（`TurnTransactionController` + `StreamShadowEngine` + `KernelGuard`）** —— 赋予心脏跳动、神经预激与物理法则

### 10.4 关键代码路径

- `polaris/cells/roles/runtime/internal/session_orchestrator.py`
- `polaris/cells/roles/runtime/internal/continuation_policy.py`
- `polaris/cells/roles/runtime/internal/session_artifact_store.py`
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- `polaris/cells/roles/kernel/internal/development_workflow_runtime.py`
- `polaris/cells/roles/kernel/internal/stream_shadow_engine.py`
- `polaris/cells/roles/kernel/public/turn_contracts.py` / `turn_events.py`

### 10.5 对齐结论

- **没有工程约束**：认知生命体将变成精神分裂的模型，在无限 Prompt 循环中产生幻觉，最终 Token 爆仓而脑死亡。
- **没有哲学愿景**：工程代码就只是一堆冷冰冰的 if-else，失去了统一的叙事与演进目标。
- **当前架构把哲学真正变成了可运行、可测试、可进化的实体。**
