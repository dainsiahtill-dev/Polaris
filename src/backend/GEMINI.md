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

## 6. 当前架构现实快照（2026-05-06）

> 本节是 `AGENTS.md §15` 的镜像摘要。如有冲突，以 `AGENTS.md` 为准。

### 6.1 Graph 图谱现状

- `docs/graph/catalog/cells.yaml` — `migration_status: phase1_public_phase2_composite_phase3_business_cells_declared`
- cells.yaml 声明的 Cell：**63 个**（统计命令：`grep "^  - id:" docs/graph/catalog/cells.yaml | wc -l`，2026-05-06）
- `polaris/cells/*/generated/descriptor.pack.json` 当前覆盖：**64 / 63**
- `docs/graph/subgraphs/` 当前仅有：
  - `execution_governance_pipeline.yaml`
  - `storage_archive_pipeline.yaml`

### 6.2 polaris/ 结构现状（`*.py` 快照，2026-05-06）

统计命令：`find polaris -name "*.py" | awk -F/ '{print $2}' | sort | uniq -c`

- `polaris/bootstrap/`: 16
- `polaris/delivery/`: 279
- `polaris/application/`: 16
- `polaris/domain/`: 44
- `polaris/kernelone/`: 1143
- `polaris/infrastructure/`: 155
- `polaris/cells/`: 1238
- `polaris/tests/`: 897
- `polaris/config/`: 5
- **总计**：**3796** 个 Python 文件

### 6.3 测试与收集现状

- `pytest --collect-only -q`（2026-05-06）结果：**28677 collected / 0 errors**
- 真实覆盖率（2026-04-24）：**23.3%**（69360/297487 lines，`pytest --cov=polaris`）
- 0% 覆盖率模块：390 个（delivery: 155, cells: 103, kernelone: 103, infrastructure: 20, bootstrap: 7, application: 1, domain: 1）

### 6.4 当前主要 gap

1. Descriptor 覆盖已提升至 **64 / 63**
2. 部分历史 Cell 仍未完成 `depends_on` 对齐（catalog gate 中 26 个 high 级别遗留、9 个 blocker）
3. `fitness-rules.yaml` blocker 尚未全量自动化执行
4. `KERNELONE_` 与 `KERNELONE_` 仍混用

### 6.5 未登记 Cell（已清零）

以下 Cell 已于 2026-04-25 全部补登至 `cells.yaml`，无剩余未登记 Cell：

- ~~`roles.host`~~
- ~~`director.delivery`~~
- ~~`director.runtime`~~
- ~~`director.planning`~~
- ~~`director.tasking`~~

### 6.6 环境变量前缀现状（2026-05-06）

- `KERNELONE_`: **1825 处 / 375 文件**
- `KERNELONE_`: **225 处 / 43 文件**

### 6.7 CLI 入口点（已更新）

- 后端服务：`python -m polaris.delivery.server --host 127.0.0.1 --port 49977`（兼容：`python src/backend/server.py`）
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
2. 若 `AGENTS.md §15 / §16 / §17` 更新，必须同步更新本文件

---

See docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md
