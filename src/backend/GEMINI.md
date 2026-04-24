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

## 6. 当前架构现实快照（2026-04-24）

> 本节是 `AGENTS.md §15` 的镜像摘要。如有冲突，以 `AGENTS.md` 为准。

### 6.1 Graph 图谱现状

- `docs/graph/catalog/cells.yaml` — `migration_status: phase1_public_phase2_composite_phase3_business_cells_declared`
- cells.yaml 声明的 Cell：**59 个**（统计命令：`grep "^  - id:" docs/graph/catalog/cells.yaml | wc -l`，2026-04-24）
- `polaris/cells/*/generated/descriptor.pack.json` 当前覆盖：**0 / 52**
- `docs/graph/subgraphs/` 当前仅有：
  - `execution_governance_pipeline.yaml`
  - `storage_archive_pipeline.yaml`

### 6.2 polaris/ 结构现状（`*.py` 快照，2026-04-24）

统计命令：`find polaris -name "*.py" | awk -F/ '{print $2}' | sort | uniq -c`

- `polaris/bootstrap/`: 16
- `polaris/delivery/`: 242
- `polaris/application/`: 4
- `polaris/domain/`: 44
- `polaris/kernelone/`: 1068
- `polaris/infrastructure/`: 155
- `polaris/cells/`: 1167
- `polaris/tests/`: 29
- `polaris/config/`: 5
- **总计**：**2732** 个 Python 文件

### 6.3 测试与收集现状

- `pytest --collect-only -q`（2026-04-24）结果：**13511 collected / 62 errors**
- 真实覆盖率（2026-04-24）：**23.3%**（69360/297487 lines，`pytest --cov=polaris`）
- 0% 覆盖率模块：390 个（delivery: 155, cells: 103, kernelone: 103, infrastructure: 20, bootstrap: 7, application: 1, domain: 1）

### 6.4 当前主要 gap

1. Descriptor 覆盖已提升至 **54 / 54**
2. 部分历史 Cell 仍未完成 `depends_on` 对齐（catalog gate 中 25 个 high 级别遗留）
3. `fitness-rules.yaml` blocker 尚未全量自动化执行
4. `KERNELONE_` 与 `KERNELONE_` 仍混用

### 6.5 未登记 Cell（需补充）

- `roles.host`
- `director.delivery`
- `director.runtime`
- `director.planning`
- `director.tasking`

### 6.6 环境变量前缀现状（2026-03-28）

- `KERNELONE_`: **769 处 / 165 文件**
- `KERNELONE_`: **225 处 / 43 文件**

### 6.7 CLI 入口点（已更新）

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

1. `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
2. `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
3. `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
4. `docs/blueprints/AGENT_INSTRUCTION_ALIGNMENT_BLUEPRINT_20260416.md`
5. `docs/blueprints/AGENT_INSTRUCTION_COMPACTION_BLUEPRINT_20260416.md`
6. `docs/blueprints/AGENT_ENGINEERING_DISCIPLINE_ALIGNMENT_BLUEPRINT_20260416.md`

### 9.2 TransactionKernel 裁决

1. `TransactionKernel` 是唯一 turn 事务执行内核和唯一 commit point
2. 旧 `TurnEngine` 只保留 facade / shim
3. 一个 turn 内必须满足：
   - `len(TurnDecisions) == 1`
   - `len(ToolBatches) <= 1`
   - `hidden_continuation == 0`
4. 协议违规统一 `panic + handoff_workflow`

### 9.3 ContextOS / Plane Isolation 裁决

1. ContextOS 固定拆成 `TruthLog`、`WorkingState`、`ReceiptStore`、`ProjectionEngine`
2. `TruthLog` append-only
3. `PromptProjection` 只读生成
4. control-plane 字段不得进入 data plane
5. raw tool output / system warning / thinking residue 不得直接回灌 prompt

### 9.4 Handoff Contract 裁决

1. `ContextHandoffPack` 是 canonical handoff contract
2. 公开真相位于：
   - `polaris.domain.cognitive_runtime.models.ContextHandoffPack`
   - `polaris.cells.factory.cognitive_runtime.public.contracts`
3. `roles.kernel`、`TransactionKernel`、`ExplorationWorkflowRuntime` 禁止再造第二套 `HandoffPack` schema

### 9.5 镜像规则

1. 本文件不是独立权威
2. 若 `AGENTS.md §15 / §16 / §17 / §18` 更新，必须同步更新本文件

---

## 10. 认知生命体与工程架构对齐（2026-04-17）

> **工程注释**：本节使用生物学隐喻作为记忆辅助。
> 所有隐喻均可在 [TERMINOLOGY.md](../TERMINOLOGY.md) 中找到对应的工程实体。
> 代码实现中使用的是工程实体名称，而非隐喻。
>
> 本节是 `docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md` 的权威摘要。修改须同步 `CLAUDE.md` 与 `GEMINI.md`。

### 10.1 核心命题

**"认知生命体（Cognitive Lifeform）"与"认知运行时（Cognitive Runtime）"是 Polaris 工程架构的灵魂与哲学顶层；**
**当前工程架构（`RoleSessionOrchestrator` + `TurnTransactionController` + `DevelopmentWorkflowRuntime` + `StreamShadowEngine`）是灵魂唯一可运行、可观测、可进化的实体化落地形态。**

两者是**上下层映射关系**，不是平行关系，更不是冲突关系。

### 10.2 概念 ↔ 工程实体映射

| 抽象概念 | 工程实体（代码基线） | 工程职责 | 生物学隐喻（记忆辅助） |
|---------|-------------------|---------|---------------------|
| 认知生命体 | `OrchestratorSessionState` + `SessionArtifactStore` | 持久身份、会话状态、记忆固化 | 躯体 + 海马体 + 自我意识 |
| 主控意识 | `RoleSessionOrchestrator.execute_stream()` | 裁决"此刻该做什么"，编排 turn 级执行流 | 前额叶皮层 |
| 心脏 / 单次神经放电 | `TurnTransactionController` + `KernelGuard` | 不可逆的单次思考-行动循环，强制单决策/单工具批次 | 心脏起搏 |
| 肌肉记忆 / 潜意识 | `DevelopmentWorkflowRuntime` | 自动执行 `read→write→test` 闭环 | 小脑 |
| 潜意识加速器 / 直觉预感 | `StreamShadowEngine`（跨 Turn 推测） | 跨 turn 推测执行，让思考与行动时间重叠 | 神经预激 |
| 物理法则 / 生存约束 | `ContinuationPolicy` + `KernelGuard` | 防止死循环、资源泄漏、幻觉 | 免疫系统/痛觉 |
| 脑电图 / 对外表达 | `TurnEvent` 流 | 实时向人类/UI 暴露内心活动 | 脑电图 |

### 10.3 四层正交架构

1. **角色层（Role）** —— 赋予身份
2. **会话编排层（`RoleSessionOrchestrator` + `OrchestratorSessionState`）** —— 赋予主控意识与记忆中枢
3. **专有运行时层（`DevelopmentWorkflowRuntime`）** —— 赋予肌肉记忆与潜意识闭环
4. **事务内核层（`TurnTransactionController` + `StreamShadowEngine` + `KernelGuard`）** —— 赋予心脏跳动、神经预激与物理法则

### 10.4 关键代码路径

- `polaris/cells/roles/runtime/internal/session_orchestrator.py` — 会话编排器（主控意识）
- `polaris/cells/roles/runtime/internal/continuation_policy.py` — 理智中枢
- `polaris/cells/roles/runtime/internal/session_artifact_store.py` — 海马体（Artifact 记忆固化）
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` — 事务内核（心脏）
- `polaris/cells/roles/kernel/internal/development_workflow_runtime.py` — 开发运行时（肌肉记忆）
- `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` — 推测引擎（直觉预感）
- `polaris/cells/roles/kernel/public/turn_contracts.py` / `turn_events.py` — 公开契约与事件流

### 10.5 物理法则（不可违背的约束）

1. **单次决策法则**：每个 Turn 只能产生 `1` 个决策（`len(TurnDecisions) == 1`）
2. **单次工具批次法则**：每个 Turn 最多 `1` 个工具批次（`len(ToolBatches) <= 1`）
3. **无隐藏连续法则**：禁止状态轨迹中出现非法循环（`hidden_continuation == 0`）
4. **最大自动回合法则**：超过 `max_auto_turns` 必须停止
5. **Stagnation 检测法则**：最近 2 个 Turn 的 artifact hash 未变化且无 speculative hints 时，强制终止
6. **重复失败熔断法则**：最近 3 个 Turn 连续发生相同错误时，强制终止

### 10.6 对齐结论

- **没有工程约束**：认知生命体将变成精神分裂的模型，在无限 Prompt 循环中产生幻觉，最终 Token 爆仓而脑死亡。
- **没有哲学愿景**：工程代码就只是一堆冷冰冰的 if-else，失去了统一的叙事与演进目标。
- **当前架构把哲学真正变成了可运行、可测试、可进化的实体。**
