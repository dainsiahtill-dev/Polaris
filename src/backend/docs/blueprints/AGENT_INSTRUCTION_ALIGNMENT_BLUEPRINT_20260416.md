# AGENT Instruction Alignment Blueprint (2026-04-16)

## 1. Goal

统一 `AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 三份 Agent 指令文件，使其同时满足：

1. 与当前后端真实架构快照保持一致
2. 与 2026-04-16 `TransactionKernel / ContextOS / ContextHandoffPack` 目标态裁决保持一致
3. 与当前 `CI/CA` 治理门禁矩阵保持一致
4. 继续满足 `tests/architecture/test_kernelone_release_gates.py::test_agent_instruction_snapshot_is_consistent`

本蓝图只处理**文档治理与指令快照对齐**，不改变运行时行为。

---

## 2. Problem Statement

当前三份指令文件存在两类偏移：

1. **镜像摘要滞后**
   - `CLAUDE.md`
   - `GEMINI.md`

   仍停留在 2026-03-28 的镜像摘要，只覆盖旧快照与部分治理工具入口，缺少：

   - `TransactionKernel` 单提交点裁决
   - `ContextHandoffPack` canonical contract 裁决
   - 完整 `CI/CA` gate 矩阵
   - “目标态不是当前事实”的显式边界

2. **治理入口未完全显式化**
   - `AGENTS.md` 已经包含当前现实快照和部分治理工具，但尚未把 2026-04-16 的目标态裁决与 pipeline gate 矩阵明确写成统一规则。

如果不统一这三份文档，后续会持续出现：

1. Agent 依据不同指令文件采取不同架构假设
2. `ContextHandoffPack` 与 roles.kernel 私有 handoff schema 再次分裂
3. Agent 只知道 `run_kernelone_release_gate.py --mode all`，却不知道当前实际 pipeline 还有 `tool_calling_canonical_gate`、`structural_bug_governance_gate` 等强门禁

---

## 3. Architecture Truth Chain

### 3.1 Authority Order

文档治理真相链固定为：

1. `AGENTS.md`
2. `docs/AGENT_ARCHITECTURE_STANDARD.md`
3. `docs/graph/catalog/cells.yaml` 与相关 subgraph
4. `docs/FINAL_SPEC.md`
5. 2026-04-16 目标态治理资产：
   - `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
   - `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
   - `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
6. `CLAUDE.md` / `GEMINI.md` 仅作为镜像摘要

### 3.2 Textual Architecture Diagram

```text
TransactionKernel / ContextOS / Workflow Runtime target-state governance
    │
    ├─ Blueprint (target-state design)
    │    ../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md
    │
    ├─ ADR (architecture decision)
    │    docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md
    │
    ├─ Verification Card (pre-fix / post-fix proof)
    │    docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml
    │
    ├─ AGENTS.md (authoritative execution rule)
    │
    ├─ CLAUDE.md / GEMINI.md (mirror summary, no extra truth)
    │
    └─ CI/CA gates
         docs/governance/ci/fitness-rules.yaml
         docs/governance/ci/pipeline.template.yaml
         tests/architecture/test_kernelone_release_gates.py
```

---

## 4. Module Responsibilities

### 4.1 `AGENTS.md`

唯一权威执行规则。必须同时表达：

1. 当前真实架构快照
2. 当前可执行治理工具
3. 最新目标态治理裁决
4. 镜像同步规则
5. CI/CA gate matrix

### 4.2 `CLAUDE.md` / `GEMINI.md`

镜像摘要，职责仅限：

1. 重述 `AGENTS.md` 的关键门禁
2. 保持当前现实快照摘要一致
3. 补充目标态治理裁决摘要
4. 列出当前最关键的执行 gate

禁止：

1. 新增独立权威
2. 引入与 `AGENTS.md` 冲突的说法
3. 将目标态写成当前事实

### 4.3 `fitness-rules.yaml` / `pipeline.template.yaml`

承担可执行治理门禁：

1. `agent_instruction_snapshot_consistent`
2. pipeline stages for governance CI/CA

文档必须反映这些门禁，不得只写局部入口。

---

## 5. Core Data Flow

### 5.1 Governance Update Flow

```text
Blueprint / ADR / VC update
    -> AGENTS.md update
    -> CLAUDE.md / GEMINI.md mirror update
    -> snapshot consistency test
    -> CI/CA pipeline stages remain discoverable from docs
```

### 5.2 Runtime Refactor Truth Flow

```text
TransactionKernel target-state decision
    -> AGENTS target-state section
    -> mirror summary sections
    -> developers/agents apply same architecture assumptions
    -> CI gates validate no doc drift
```

---

## 6. Technical Decisions

### 6.1 Keep Current Snapshot and Target-State Separate

`AGENTS.md §15` 与镜像中的 “当前架构现实快照（2026-03-28）” 保持不变，继续描述**当前事实**。  
新增单独章节描述 **2026-04-16 目标态治理裁决**，并显式标注“非当前事实”。

原因：

1. 不破坏现有一致性测试的提取逻辑
2. 不把蓝图状态伪装成当前现实
3. 允许迁移中同时保留 current-state 和 target-state

### 6.2 Promote CI/CA Matrix to First-Class Rule

除已有工具入口外，明确列出当前 pipeline stages：

1. `catalog_governance_audit`
2. `catalog_governance_fail_on_new`
3. `catalog_governance_hard_fail`
4. `kernelone_release_gate`
5. `delivery_cli_hygiene_gate`
6. `opencode_convergence_gate`
7. `manifest_catalog_reconciliation_gate`
8. `structural_bug_governance_gate`
9. `tool_calling_canonical_gate`

原因：

1. Agent 需要知道当前“真正会挡发布”的门禁，而不是只知道两个入口脚本
2. `tool_calling_canonical_gate` 与当前 roles.kernel / director 工具调用治理直接相关
3. `structural_bug_governance_gate` 与结构性修复协议、ADR/VC 闭环直接相关

### 6.3 Make Handoff Contract Canonical

文档统一声明：

1. `ContextHandoffPack` 是 canonical handoff contract
2. 公开契约落点是 `polaris.domain.cognitive_runtime` 与 `factory.cognitive_runtime`
3. roles.kernel 禁止再造第二套 handoff schema

原因：

1. 避免 `TransactionKernel` 重构期间制造第二套真相
2. 与 ADR-0071 和 verification card 完全对齐

---

## 7. Implementation Plan

### Phase A: Blueprint Landing

新增本文件，作为文档治理对齐的专用蓝图。

### Phase B: Instruction Synchronization

修改：

1. `AGENTS.md`
2. `CLAUDE.md`
3. `GEMINI.md`

新增两类内容：

1. `CI/CA` gate matrix
2. `2026-04-16` 目标态治理裁决摘要

### Phase C: Verification

执行：

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py::test_agent_instruction_snapshot_is_consistent`

---

## 8. Risks And Boundaries

### Risks

1. 镜像摘要补充过多，可能再次形成第二套长文档真相
2. 若修改“当前现实快照”中的数字或日期，可能触发一致性测试失败

### Boundaries

1. 本蓝图不修改运行时代码
2. 本蓝图不更新 graph 资产
3. 本蓝图不把 2026-04-16 目标态写成当前现实

---

## 9. Verification Plan

### Happy Path

1. `AGENTS.md` 增加 `CI/CA` 矩阵与目标态治理裁决
2. `CLAUDE.md` / `GEMINI.md` 增加对应镜像摘要
3. snapshot consistency 测试继续通过

### Regression

1. `当前架构现实快照（2026-03-28）` 仍可被既有正则提取
2. descriptor generator command 仍存在于三个文件
3. `CLAUDE.md == AGENTS snapshot facts`
4. `GEMINI.md == AGENTS snapshot facts`

---

## 10. Expected Outcome

完成后，三份 Agent 指令文档将满足：

1. 当前现实与目标态治理裁决分层清晰
2. `TransactionKernel / ContextOS / ContextHandoffPack` 的目标态说法统一
3. `CI/CA` 门禁矩阵在文档中可直接发现
4. `CLAUDE.md / GEMINI.md` 不再滞后于 `AGENTS.md`
