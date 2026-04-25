# Polaris Backend Agent Rules

状态: Active  
适用范围: `src/backend`  
规范基线: `docs/AGENT_ARCHITECTURE_STANDARD.md` + `docs/FINAL_SPEC.md` + `docs/真正可执行的 ACGA 2.0 落地版.md`

本文件是后端目录下 Agent 的最高优先级执行规则。  
`CLAUDE.md` 与 `GEMINI.md` 只是镜像摘要，不得引入额外或冲突规则；若冲突，以本文件为准。

---

## 1. 权威关系与裁决顺序

迁移期固定按以下顺序裁决：

1. `AGENTS.md`
2. `docs/AGENT_ARCHITECTURE_STANDARD.md`
3. `docs/graph/catalog/cells.yaml` 与 `docs/graph/subgraphs/*.yaml`
4. `docs/FINAL_SPEC.md`
5. `docs/真正可执行的 ACGA 2.0 落地版.md` 与 `docs/ACGA_2.0_PRINCIPLES.md`
6. 2026-04-16 目标态治理资产：
   - `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
   - `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
   - `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`

规则：

1. 先按当前 graph 事实处理边界。
2. 再按 `FINAL_SPEC.md` 判断迁移方向。
3. 最后在前两者约束内应用 ACGA 2.0 的 Descriptor / Semantic / Governance 规则。
4. 禁止把目标态或规划态写成当前事实。

## 2. 核心目标

本仓是迁移中的 ACGA 2.0 图谱系统。默认目标是：

1. 边界可解释
2. 状态可追责
3. 副作用可审计
4. 迁移可回滚
5. Agent 以最小上下文拿到正确真相
6. 语义检索不越过 graph 声明的合法边界

## 3. 默认阅读顺序

处理中等及以上任务时，按以下顺序读取：

1. `docs/AGENT_ARCHITECTURE_STANDARD.md`
2. `docs/graph/catalog/cells.yaml`
3. 相关 `docs/graph/subgraphs/*.yaml`
4. `docs/FINAL_SPEC.md`
5. 若任务涉及 Context Plane / Descriptor / Semantic Search，再读 ACGA 2.0 文档
6. 目标 Cell 的 `cell.yaml`、`README.agent.md`、`generated/context.pack.json`
7. 若存在，再读 `generated/descriptor.pack.json`、`generated/impact.pack.json`、`generated/verify.pack.json`
8. 目标 Cell 的公开契约
9. 必要时才进入 `owned_paths`

不要先全仓扫描源码再猜边界。

## 4. 强制原则

### 4.1 Graph First

Graph 是唯一架构真相，优先于目录树。

### 4.2 Cell First

Cell 是最小自治边界。

### 4.2.1 Reuse First + KernelOne Foundation

1. 先复用已有 Cell 的公开能力，禁止重复造轮子。
2. 缺口优先补齐既有 Cell，再评估新增 Cell。
3. 所有新开发必须基于 `KernelOne` 契约与运行时能力，不允许绕过 `KernelOne` 直连底层实现。
4. 复用优先级固定为：`existing cell public contract` > `kernelone contract` > `new implementation`。

### 4.3 Public/Internal Fence

跨 Cell 依赖只能走公开边界，禁止直接依赖其他 Cell 的 `internal/`。

### 4.4 Contract First

跨 Cell 协作必须通过契约表达：`command/query/event/result/error/stream/effect`。

### 4.5 Graph-Constrained Semantic

先 Graph 约束，再 Descriptor 排序；向量检索不得创建边界、扩大授权或绕开 graph。

### 4.6 Descriptor / Context / Verify 分工

1. Descriptor 用于检索
2. Context Pack 用于工作
3. Verify Pack 用于验证

禁止把三者混成单一万能资产。

### 4.7 Single State Owner

一个 source-of-truth 状态只能有一个 Cell 拥有写权限。

### 4.8 Explicit Effects

文件、数据库、网络、WebSocket、子进程、外部工具、LLM、Descriptor、Embedding、Semantic Index 都是 effect，必须显式声明并可审计。

### 4.9 UTF-8 Mandatory

所有文本文件读写必须显式 UTF-8。

### 4.10 No Dual Graph Truth

禁止引入 `.acga/graph` 或任何第二套 graph 真相目录。

### 4.11 Truthful Migration

未落地的目录、Cell、契约或流程，不得写成“当前已完成事实”。

## 5. 根目录与归属裁决

规范根目录继续解释为：

- `bootstrap/` -> `polaris/bootstrap/`
- `delivery/` -> `polaris/delivery/`
- `application/` -> `polaris/application/`
- `domain/` -> `polaris/domain/`
- `kernelone/` -> `polaris/kernelone/`
- `infrastructure/` -> `polaris/infrastructure/`
- `cells/` -> `polaris/cells/`
- `tests/` -> `polaris/tests/`

共享真相资产继续保留在仓库顶层：

- `docs/graph/`
- `docs/governance/`
- `docs/templates/`

归属裁决顺序：

1. HTTP / WebSocket / CLI / transport -> `delivery/`
2. 用例编排 / workflow / 事务边界 -> `application/`
3. 业务规则 / 实体 / 策略 -> `domain/`
4. Agent/AI 通用 OS 能力 -> `kernelone/`
5. SDK / 存储 / 消息 / 插件 / 遥测适配 -> `infrastructure/`
6. 启动与装配 -> `bootstrap/`

旧根迁移状态（2026-04-24，Squad V 完成）：

- `app/`、`core/`、`api/`：已不存在于本仓库。
- `director_interface.py`：已迁移至 `polaris/delivery/cli/pm/director_interface_core.py`，旧根保留 shim 兼容层。
- `server.py`：已迁移至 `polaris/delivery/server.py`，旧根保留 shim 兼容层。
- `scripts/`：仍保留（56 个文件），仅作为历史工具/诊断脚本；新功能必须写入 `polaris/delivery/cli/` 或对应 Cell 目录。

## 6. 开工前必做

中等及以上任务开工前必须确认：

1. 目标 Cell 或治理资产
2. 相关 subgraph
3. `owned_paths`
4. `depends_on`
5. `state_owners`
6. `effects_allowed`
7. `verification.gaps`
8. 若涉及 Context Plane / Descriptor / Semantic Index，确认 pack 与 `workspace/meta/context_catalog/*` 边界

## 7. 修改规则

1. 默认只修改目标 Cell 的 `owned_paths`
2. 修改公共边界、Descriptor、Semantic Search 或治理门禁时，至少同步评估：
   - `docs/graph/catalog/cells.yaml`
   - `docs/graph/subgraphs/*.yaml`
   - `docs/governance/schemas/*.yaml`
   - `docs/governance/ci/fitness-rules.yaml`
   - `docs/governance/ci/pipeline.template.yaml`
3. 禁止新增或扩大 `common/ helpers/ misc/ 无边界 utils/ base_utils.py`
4. 兼容层只能做薄垫片，禁止双边长期打补丁

## 8. 验证与结构性修复协议

### 8.1 基本规则

修改后必须明确说明：

1. 改了哪个 Cell 或治理资产
2. 是否跨 Cell
3. 是否触及公开契约、状态拥有、副作用或 Descriptor / Index 规则
4. 跑了什么验证
5. 哪些风险还没验证

### 8.2 验证门禁

对代码改动，必须实际运行并通过：

1. `ruff check <paths> --fix`
2. `ruff format <paths>`
3. `mypy <paths>`
4. `pytest <tests> -q`

### 8.3 自修复循环

若任一门禁失败：

1. 分析错误
2. 本地修复
3. 重新运行对应门禁
4. 连续同类失败 5 次再向人类求助

### 8.4 Verification Card / ADR 适用范围

对 `pattern` 或 `structural` 问题，必须执行 `§8.6`。

### 8.5 输出要求

禁止只说“应该可以”“大概没问题”。结论必须对应证据和验证。

### 8.6 Pre-Fix Thinking Protocol（修前思考协议）

#### 8.6.1 适用范围

所有 `pattern` 和 `structural` 级问题，修复前必须填写 Verification Card。

#### 8.6.2 分类

1. `one_off`: 局部错误，可直接修 + 测试
2. `pattern`: 同类错误重复出现，必须出 ADR 或设计文档
3. `structural`: 多模块共享同一错误假设，必须出 ADR

#### 8.6.3 强制步骤

1. 写出 Assumption Register
2. 逐条找代码证据验证假设
3. 做 pre-mortem，写明修错的最可能位置
4. 写 Verification Plan，具体到测试文件 / 命令 / 预期
5. 填写 Verification Card：
   - `docs/governance/templates/verification-cards/vc-<yyyymmdd>-<slug>.yaml`
6. 若分类为 `structural`，补 ADR：
   - `docs/governance/decisions/adr-<number>-<slug>.md`

## 9. 状态与副作用

1. 查询路径禁止偷写
2. `workspace/history/*` 不是运行时 source-of-truth
3. Descriptor / Embedding / Index 写入本身是 effect
4. 归档、压缩、解压与文本落盘都必须显式 UTF-8

## 10. 测试与质量门禁

优先跑与改动最相关的最小门禁集合；完成修复后再补回归。  
若改动涉及 runtime / contracts / governance，高风险门禁优先于大而全测试。

### 10.1 两阶段执行模型（Blueprint First）

接到具体任务后，默认按两个阶段执行：

1. **阶段一：Blueprint & Architecture**
   - 先输出架构/重构方案
   - 方案必须落到 `docs/blueprints/*.md`
   - 至少包含：文本架构图、模块职责、核心数据流、技术理由
2. **阶段二：Execution & Implementation**
   - 再按任务类型落地实现、重构、修复、测试或文档更新
   - 实施前后都要受本文件门禁约束

除极小型纯文字修正外，禁止跳过 blueprint 直接进入实现。

### 10.2 工程标准（Engineering Standards）

所有实现与重构默认遵守：

1. 严格基于 Ruff/Black 约束的现代 PEP 8
2. 清晰命名、单一职责、低耦合、高内聚、隐藏内部状态
3. 防御性编程：类型注解、边界处理、合理异常处理；禁止裸 `except:`
4. 关键类和复杂函数应有清晰 docstring
5. 严禁过度设计、炫技、隐藏副作用和重复代码
6. 类型安全优先：以 `mypy --strict` / 等价严格类型门禁为目标
7. 默认按工程化模块组织，而不是临时脚本堆砌

### 10.3 任务协议（Task Protocols）

1. **新需求/写代码**：交付可生产使用的完整实现，不交付伪代码
2. **重构**：默认无损重构，保持外部接口和行为一致，并说明改进维度
3. **代码审查**：按 `Blocker / Suggestion / Nitpick` 输出，包含定位、根因、建议和严重度
4. **Bug 修复**：必须写清现象、根因和防御性修复方案，禁止头痛医头
5. **测试编写**：默认使用 `pytest`，覆盖 Happy Path、Edge Cases、Exceptions、Regression

### 10.4 输出结构（Output Format）

交付说明默认按以下顺序组织：

1. `结果 (Result)`
2. `分析 (Analysis)`
3. `风险与边界 (Risks & Boundaries)`
4. `测试 (Testing)`
5. `自检 (Self-Check)`
6. `后续优化 (Future Optimization)`

## 11. 交付要求

交付时至少说明：

1. 改动范围
2. 根因或设计理由
3. 已完成验证
4. 剩余风险

## 12. 禁止事项

1. 禁止绕过 graph 和 Cell 边界
2. 禁止把规划态写成现状
3. 禁止引入第二套 graph truth 或 handoff truth
4. 禁止未经声明的副作用
5. 禁止为了过测试回退历史旧实现“续命”

## 13. 镜像同步规则

1. `CLAUDE.md` 与 `GEMINI.md` 只是镜像摘要
2. 修改 `§15 / §16 / §17 / §18` 时，必须同步三个指令文件
3. 若存在冲突，以本文件为准并立即修复镜像漂移

## 14. 执行自检

动手前自问：

1. 我修改的是哪个 Cell 或治理资产？
2. 我是否先看了 graph、`FINAL_SPEC.md` 和所需 ACGA 2.0 文档？
3. 我是否只改了受控边界？
4. 我是否引入了未声明 effect？
5. 我是否给出了真实验证结论？

若任何一项回答不清楚，先不要写代码。

---

## 15. 当前架构现实快照（2026-04-24）

本节记录当前事实，不得与目标态混写。修改须同步 `CLAUDE.md` 与 `GEMINI.md`。

### 15.1 Graph 图谱现状

- `docs/graph/catalog/cells.yaml` — `migration_status: phase1_public_phase2_composite_phase3_business_cells_declared`
- cells.yaml 声明的 Cell：**59 个**（统计命令：`grep "^  - id:" docs/graph/catalog/cells.yaml | wc -l`，2026-04-24）
- `polaris/cells/*/generated/descriptor.pack.json` 当前覆盖：**0 / 52**
- `docs/graph/subgraphs/` 当前仅有：
  - `execution_governance_pipeline.yaml`
  - `storage_archive_pipeline.yaml`

### 15.2 polaris/ 结构现状（`*.py` 快照，2026-04-24）

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

### 15.3 测试与收集现状

- `pytest --collect-only -q`（2026-04-24）结果：**13511 collected / 62 errors**
- 真实覆盖率（2026-04-24）：**23.3%**（69360/297487 lines，`pytest --cov=polaris`）
- 0% 覆盖率模块：390 个（delivery: 155, cells: 103, kernelone: 103, infrastructure: 20, bootstrap: 7, application: 1, domain: 1）

### 15.4 当前主要 gap

1. Descriptor 覆盖已提升至 **54 / 54**
2. 部分历史 Cell 仍未完成 `depends_on` 对齐（catalog gate 中 25 个 high 级别遗留）
3. `fitness-rules.yaml` blocker 尚未全量自动化执行
4. `KERNELONE_` 与 `KERNELONE_` 仍混用

### 15.5 未登记 Cell（需补充）

- `roles.host`
- `director.delivery`
- `director.runtime`
- `director.planning`
- `director.tasking`

### 15.6 环境变量前缀现状（2026-03-28）

- `KERNELONE_`: **769 处 / 165 文件**
- `KERNELONE_`: **225 处 / 43 文件**

### 15.7 CLI 入口点（已更新）

- 后端服务：`python -m polaris.delivery.server --host 127.0.0.1 --port 49977`（兼容：`python src/backend/server.py`）
- PM CLI：`python -m polaris.delivery.cli.pm.cli`
- Director CLI：`python -m polaris.delivery.cli.director.cli_thin`
- Architect CLI：`python -m polaris.cells.architect.design.internal.architect_cli`
- Chief Engineer CLI：`python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli`
- Console：`python -m polaris.delivery.cli console --backend plain`

---

## 16. 自动化治理工具

### 16.1 Descriptor Pack 批量生成器

**命令**: `python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`

用途：批量生成 `polaris/cells/*/generated/descriptor.pack.json`。  
任何涉及 `owned_paths` 内 Python 源码或公共 docstring 的改动，提交前应评估是否需要执行。

### 16.2 KernelOne 发布门禁执行器

**命令**: `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

### 16.3 Catalog 治理门禁

**命令**: `python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

迁移期默认阻断模式是 `fail-on-new`；`hard-fail` 只适用于已清债域。

### 16.4 当前 CI/CA 门禁矩阵（2026-04-16）

当前后端治理以 `docs/governance/ci/pipeline.template.yaml` 为准。关键 gate：

1. `catalog_governance_audit`
2. `catalog_governance_fail_on_new`
3. `catalog_governance_hard_fail`
4. `kernelone_release_gate`
5. `delivery_cli_hygiene_gate`
6. `opencode_convergence_gate`
7. `manifest_catalog_reconciliation_gate`
8. `structural_bug_governance_gate`
9. `tool_calling_canonical_gate`

补充规则：

1. `docs/governance/ci/fitness-rules.yaml` 中的 `agent_instruction_snapshot_consistent` 要求 `AGENTS.md / CLAUDE.md / GEMINI.md` 的快照事实保持一致。
2. 修改 `§15 / §16 / §17` 时必须同步三个指令文件。

---

## 17. 最新目标态治理裁决（2026-04-16，非当前事实）

本节是目标态治理裁决，不是当前现实快照。

### 17.1 权威来源

1. `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
2. `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
3. `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
4. `docs/blueprints/AGENT_INSTRUCTION_ALIGNMENT_BLUEPRINT_20260416.md`
5. `docs/blueprints/AGENT_INSTRUCTION_COMPACTION_BLUEPRINT_20260416.md`
6. `docs/blueprints/AGENT_ENGINEERING_DISCIPLINE_ALIGNMENT_BLUEPRINT_20260416.md`

### 17.2 TransactionKernel 裁决

1. `TransactionKernel` 是唯一 turn 事务执行内核和唯一 commit point
2. 旧 `TurnEngine` 只保留 facade / shim
3. 一个 turn 内必须满足：
   - `len(TurnDecisions) == 1`
   - `len(ToolBatches) <= 1`
   - `hidden_continuation == 0`
4. 协议违规统一 `panic + handoff_workflow`

### 17.3 ContextOS / Plane Isolation 裁决

1. ContextOS 固定拆成 `TruthLog`、`WorkingState`、`ReceiptStore`、`ProjectionEngine`
2. `TruthLog` append-only
3. `PromptProjection` 只读生成
4. control-plane 字段不得进入 data plane
5. raw tool output / system warning / thinking residue 不得直接回灌 prompt

### 17.4 Handoff Contract 裁决

1. `ContextHandoffPack` 是 canonical handoff contract
2. 公开真相位于：
   - `polaris.domain.cognitive_runtime.models.ContextHandoffPack`
   - `polaris.cells.factory.cognitive_runtime.public.contracts`
3. `roles.kernel`、`TransactionKernel`、`ExplorationWorkflowRuntime` 禁止再造第二套 `HandoffPack` schema

---

## 18. 认知生命体与工程架构对齐（2026-04-17）

> **工程注释**：本节使用生物学隐喻作为记忆辅助。
> 所有隐喻均可在 [docs/TERMINOLOGY.md](../../docs/TERMINOLOGY.md) 中找到对应的工程实体。
> 代码实现中使用的是工程实体名称，而非隐喻。
>
> 本节是 `docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md` 的权威摘要。修改须同步 `CLAUDE.md` 与 `GEMINI.md`。

### 18.1 核心命题

**"认知生命体（Cognitive Lifeform）"与"认知运行时（Cognitive Runtime）"是 Polaris 工程架构的灵魂与哲学顶层；**
**当前工程架构（`RoleSessionOrchestrator` + `TurnTransactionController` + `DevelopmentWorkflowRuntime` + `StreamShadowEngine`）是灵魂唯一可运行、可观测、可进化的实体化落地形态。**

两者是**上下层映射关系**，不是平行关系，更不是冲突关系。

### 18.2 概念 ↔ 工程实体映射

| 抽象概念 | 工程实体（代码基线） | 工程职责 | 生物学隐喻（记忆辅助） |
|---------|-------------------|---------|---------------------|
| 认知生命体 | `OrchestratorSessionState` + `SessionArtifactStore` | 持久身份、会话状态、记忆固化 | 躯体 + 海马体 + 自我意识 |
| 主控意识 | `RoleSessionOrchestrator.execute_stream()` | 裁决"此刻该做什么"，编排 turn 级执行流 | 前额叶皮层 |
| 心脏 / 单次神经放电 | `TurnTransactionController` + `KernelGuard` | 不可逆的单次思考-行动循环，强制单决策/单工具批次 | 心脏起搏 |
| 肌肉记忆 / 潜意识 | `DevelopmentWorkflowRuntime` | 自动执行 `read→write→test` 闭环 | 小脑 |
| 潜意识加速器 / 直觉预感 | `StreamShadowEngine`（跨 Turn 推测） | 跨 turn 推测执行，让思考与行动时间重叠 | 神经预激 |
| 物理法则 / 生存约束 | `ContinuationPolicy` + `KernelGuard` | 防止死循环、资源泄漏、幻觉 | 免疫系统/痛觉 |
| 脑电图 / 对外表达 | `TurnEvent` 流 | 实时向人类/UI 暴露内心活动 | 脑电图 |

### 18.3 四层正交架构

1. **角色层（Role）** —— 赋予身份
2. **会话编排层（`RoleSessionOrchestrator` + `OrchestratorSessionState`）** —— 赋予主控意识与记忆中枢
3. **专有运行时层（`DevelopmentWorkflowRuntime`）** —— 赋予肌肉记忆与潜意识闭环
4. **事务内核层（`TurnTransactionController` + `StreamShadowEngine` + `KernelGuard`）** —— 赋予心脏跳动、神经预激与物理法则

### 18.4 关键代码路径

- `polaris/cells/roles/runtime/internal/session_orchestrator.py` — 会话编排器（主控意识）
- `polaris/cells/roles/runtime/internal/continuation_policy.py` — 理智中枢
- `polaris/cells/roles/runtime/internal/session_artifact_store.py` — 海马体（Artifact 记忆固化）
- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` — 事务内核（心脏）
- `polaris/cells/roles/kernel/internal/development_workflow_runtime.py` — 开发运行时（肌肉记忆）
- `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` — 推测引擎（直觉预感）
- `polaris/cells/roles/kernel/public/turn_contracts.py` / `turn_events.py` — 公开契约与事件流

### 18.5 物理法则（不可违背的约束）

1. **单次决策法则**：每个 Turn 只能产生 `1` 个决策（`len(TurnDecisions) == 1`）
2. **单次工具批次法则**：每个 Turn 最多 `1` 个工具批次（`len(ToolBatches) <= 1`）
3. **无隐藏连续法则**：禁止状态轨迹中出现非法循环（`hidden_continuation == 0`）
4. **最大自动回合法则**：超过 `max_auto_turns` 必须停止
5. **Stagnation 检测法则**：最近 2 个 Turn 的 artifact hash 未变化且无 speculative hints 时，强制终止
6. **重复失败熔断法则**：最近 3 个 Turn 连续发生相同错误时，强制终止

### 18.6 对齐结论

- **没有工程约束**：认知生命体将变成精神分裂的模型，在无限 Prompt 循环中产生幻觉，最终 Token 爆仓而脑死亡。
- **没有哲学愿景**：工程代码就只是一堆冷冰冰的 if-else，失去了统一的叙事与演进目标。
- **当前架构把哲学真正变成了可运行、可测试、可进化的实体。**
