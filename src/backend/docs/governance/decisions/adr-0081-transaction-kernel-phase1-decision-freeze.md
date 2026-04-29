# Phase 1 决策冻结清单（Decision Freeze）

**冻结日期**: 2026-04-21  
**冻结范围**: Transaction Kernel 硬化（P0）  
**下次评审**: Phase 1 验收完成后  
**文档状态**: FROZEN — 未经 ADR 修订程序不得变更

---

## 1. 最终 P0 清单（6 项，已冻结）

| # | 事项 | 负责域 | 核心交付物 | 验收标准 |
|---|------|--------|-----------|---------|
| 1 | **TurnOutcomeEnvelope 标准化** | `turn_contracts.py` + `ledger.py` | `TurnOutcome` Pydantic 模型 | [§2.1](#21-turnoutcomeenvelope-验收标准) |
| 2 | **Commit Protocol 硬化** | `core.py` | `_execute_commit_protocol()` | [§2.2](#22-commit-protocol-验收标准) |
| 3 | **ToolBatch 幂等键** | `tool_batch_executor.py` + `ToolExecutionContext` | `idempotency_key` + `side_effect_class` | [§2.3](#23-toolbatch-幂等键验收标准) |
| 4 | **收口阶段硬封印** | `finalization.py` | `tool_choice=none` 强制 + 封印标记 | [§2.4](#24-收口阶段硬封印验收标准) |
| 5 | **Failure Taxonomy 落地** | `kernel_guard.py` + 新建 `failure_taxonomy.py` | `FailureClass` Enum + 标准异常字段 | [§2.5](#25-failure-taxonomy-验收标准) |
| 6 | **Handoff 最小版 Structured Findings** | `ContextHandoffPack` (ADR-0071) | `structured_findings` 字段（4 要素） | [§2.6](#26-handoff-structured-findings-验收标准) |

**冻结后新增项规则**: 任何新增 P0 项必须触发重新冻结评审，禁止滑入。

---

## 2. 验收标准（Acceptance Criteria）

### 2.1 TurnOutcomeEnvelope 验收标准

1. **唯一性**: 每个 turn 完成后必须产生且仅产生一个 `TurnOutcome` 实例
2. **下游消费统一**: 所有下游（Workflow、Audit、Replay）读取 turn 结果必须通过 `TurnOutcome` 或其稳定投影（`to_dict()` / `to_json()`）
3. **Ledger 边界**: `TurnLedger` 继续作为审计源存在，但新代码禁止直接拼 `ledger.decisions + ledger.receipts + ledger.state_history` 作为业务结果
4. **不可为空**: `TurnOutcome.outcome_status` 必须是 `Literal["COMPLETED", "FAILED", "PANIC", "HANDED_OFF"]` 之一，禁止自由文本
5. **Commit 引用**: `commit_ref` 字段必须包含 `snapshot_id` 和 `truthlog_seq_range`

### 2.2 Commit Protocol 验收标准

1. **阶段显式化**: `_commit_turn_to_snapshot()` 必须拆分为三个显式阶段：
   - `_pre_commit_validate(ledger)` → `ValidationReport`
   - `_execute_commit_protocol(ledger, snapshot)` → `CommitReceipt`
   - `_post_commit_seal(commit_receipt)` → `SealedTurn`
2. **非真原子声明**: 代码注释和文档必须明确说明这是"durable commit protocol"而非"global atomic transaction"，崩溃恢复语义是"可恢复到唯一一致状态"
3. **防双重提交**: 同一 `turn_id` 不允许产生两个 `SealedTurn`；若检测到重复 commit，第二次必须返回已存在的 `SealedTurn` 并写入 `TruthLog` 为 no-op
4. **验证清单**: `_pre_commit_validate` 必须检查：
   - `single_decision`: `len(ledger.decisions) == 1`
   - `single_tool_batch`: `len(ledger.tool_batches) <= 1`
   - `no_hidden_continuation`: 状态轨迹中 `DECISION_REQUESTED` 出现次数 <= 1
   - `receipts_integrity`: 所有 tool_batch 中的 tool_call 都有对应 receipt
   - `budget_balance`: `ledger.tokens_consumed <= budget_plan.input_budget + budget_plan.output_budget`

### 2.3 ToolBatch 幂等键验收标准

1. **Batch 级幂等**: `ToolExecutionContext` 必须包含 `batch_idempotency_key: str`，格式为 `f"{turn_id}:{batch_seq}"`
2. **Call 级预留**: schema 必须预留 `call_idempotency_key` 字段（Phase 1 可为空，但 schema 必须存在）
3. **Receipt 检查**: `ToolBatchExecutor.execute_tool_batch()` 必须先查询 `ReceiptStore`：
   - 若 `batch_idempotency_key` 已存在且 receipt 完整 → 返回现有结果
   - 若不存在 → 执行工具并写入 receipt
4. **Side-effect class 来源**: `side_effect_class` 必须从 `ToolSpecPolicy` 或工具注册元数据读取，禁止运行时自由填写
5. **键稳定性**: `idempotency_key` 必须绑定 `turn_id + batch_seq + 规范化 tool payload hash`，确保参数变化时键变化

### 2.4 收口阶段硬封印验收标准

1. **Tool choice 强制**: `FinalizationHandler.execute_llm_once()` 发给 LLM 的 request payload 必须包含 `"tool_choice": "none"`，且类型为字符串 `"none"` 而非 `None`
2. **调用面封印**: 收口阶段必须从调用面切换到 `no_tools_client` 或 `tooling_disabled_request_mode`，不能只依赖下游过滤
3. **封印标记**: request payload 必须包含 `_control.phase == "closing"` 和 `_control.continuation_forbidden == True`（该字段会被 ContextOS `to_prompt_dict()` 过滤，不进入 data plane）
4. **违规检测**: 若收口阶段 LLM 仍返回 tool_calls，`KernelGuard.assert_no_finalization_tool_calls()` 必须触发 `FailureClass.CONTRACT_VIOLATION`

### 2.5 Failure Taxonomy 验收标准

1. **五类定义**: 新建 `FailureClass` Enum，包含：
   - `CONTRACT_VIOLATION`
   - `RUNTIME_FAILURE`
   - `DURABILITY_FAILURE`
   - `INSUFFICIENT_EVIDENCE`（原 SEMANTIC_INSUFFICIENCY）
   - `POLICY_FAILURE`
2. **异常字段扩展**: `KernelGuardError` 必须包含：
   - `failure_class: FailureClass`
   - `phase: str`（发生阶段）
   - `invariant_or_policy: str`（违反的不变量或策略）
   - `retryable: bool`
   - `handoff_recommended: bool`
3. **下游消费**: `TurnOutcome` 必须包含 `failure_class` 和 `resolution_code`（即使非失败 turn，也显式为 `"completed"`）
4. **日志格式**: 所有异常日志必须包含 `failure_id`（UUID）、`failure_class`、`phase`、`invariant_or_policy`

### 2.6 Handoff Structured Findings 验收标准

1. **四要素最小版**: `ContextHandoffPack` 必须新增 `structured_findings` 字段，包含：
   - `confirmed_facts: list[str]`
   - `rejected_hypotheses: list[str]`
   - `open_questions: list[str]`
   - `relevant_refs: list[str]`（文件/artifact 引用）
2. **Prompt 消费**: 下一 turn 的 `prompt builder` 必须能消费 `structured_findings`，至少将 `confirmed_facts` 注入 system message 或 context header
3. **非空约束**: `structured_findings` 可为 `None`，但若存在，四个字段都必须是 `list`（允许空列表，禁止 `None`）
4. **来源验证**: handoff 产生 `structured_findings` 时，必须记录 `source_turn_id` 和 `extracted_at` 时间戳

---

## 3. 会改动的 Schema / 文件列表

### 3.1 修改文件（8 个）

| 文件路径 | 变更性质 | 影响面 |
|---------|---------|--------|
| `polaris/cells/roles/kernel/public/turn_contracts.py` | **Schema 扩展** | 新增 `TurnOutcome`, `CommitReceipt`, `SealedTurn` |
| `polaris/cells/roles/kernel/internal/kernel/core.py` | **逻辑重构** | `_commit_turn_to_snapshot()` 拆三段 |
| `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py` | **逻辑增强** | 幂等查询 + `idempotency_key` 集成 |
| `polaris/cells/roles/kernel/internal/transaction/finalization.py` | **逻辑增强** | 收口封印 + `no_tools_client` |
| `polaris/cells/roles/kernel/internal/kernel_guard.py` | **Schema 扩展** | `KernelGuardError` 新增 failure 字段 |
| `polaris/cells/roles/kernel/internal/transaction/ledger.py` | **Schema 扩展** | `TurnLedger` 集成 `TurnOutcome` 生成 |
| `polaris/cells/roles/kernel/internal/tool_batch_runtime.py` | **Schema 扩展** | `ToolExecutionContext` 新增字段 |
| `polaris/cells/roles/kernel/internal/transaction/failure_taxonomy.py` | **新增文件** | `FailureClass` Enum + 标准异常基类 |

### 3.2 下游消费点（需同步评估，非本 Phase 修改）

| 消费点 | 当前行为 | 需要变更 |
|--------|---------|---------|
| `ExplorationWorkflowRuntime` | 直接读 `ledger.decisions` | 改为消费 `TurnOutcome` |
| `StreamShadowEngine` | 直接读 `TurnResult` dict | 改为消费 `TurnOutcome` |
| `pm/workspace` 状态展示 | 拼 `turn_history` + `decisions` | 改为读 `TurnOutcome.user_visible_result_ref` |
| `audit/diagnosis` | 扫描 raw events | 改为扫描 `TruthLog` + `TurnOutcome` |

**注意**: 下游消费点不在 Phase 1 修改范围内，但必须在 Phase 1 设计时预留接口兼容性。

---

## 4. 风险与缓解（已冻结评估）

| 风险 | 概率 | 影响 | 缓解措施 | 责任人 |
|------|------|------|---------|--------|
| **Ledger / Envelope 双轨并存** | 高 | 高 | 冻结后新增代码必须消费 `TurnOutcome`，禁止直接拼 ledger；存量代码标记 `@deprecated` | E9 |
| **幂等键定义错误** | 中 | 高 | `idempotency_key` 必须包含 `payload_hash`；新增 `test_idempotency_stability.py` 验证键稳定性 | E8 |
| **Commit Protocol 被误解为真原子** | 中 | 中 | 文档和注释统一使用 "durable commit protocol"，明确说明是 crash-recoverable 而非 ACID atomic | E1 |
| **收口阶段工具封印绕过** | 低 | 高 | 从调用面切换 `no_tools_client`，不依赖下游过滤；增加 regression test | E2 |
| **Structured findings 消费不足** | 中 | 中 | 验收标准强制要求 prompt builder 消费 `confirmed_facts`；不消费则测试失败 | E5 |

---

## 5. 被冻结排除的项（明确不进入 Phase 1）

| 事项 | 排除理由 | 目标阶段 |
|------|---------|---------|
| ContextOS 三层真相模型命名 | 概念冻结与文档治理，非运行时阻塞 | **P1 治理文档** |
| Replay / Recovery 完整测试矩阵 | 需要 Commit Protocol 落地后才能验证 | **P1 测试包** |
| Break-glass 模式 | 无当前运维需求，提前实现增加滥用风险 | **P2 ADR 预留** |
| 外部副作用补偿事务 | 涉及跨服务 Saga/TCC，当前外部工具主要是本地操作 | **P2 设计** |
| FAIL_CLOSED / NEED_HUMAN 决策枚举 | 现有 `HANDOFF_WORKFLOW` + `ASK_USER` + `resolution_code` 已覆盖 | **不新增** |
| Property-based tests（Hypothesis） | Phase 1 先用 deterministic invariant tests 打底 | **P1 增强** |

---

## 6. 自检结论（按 AGENTS.md §14）

| 问题 | 回答 |
|------|------|
| 修改的是哪个 Cell 或治理资产？ | `roles.kernel`（核心运行时契约） |
| 是否先看了 graph、FINAL_SPEC.md 和 ACGA 2.0 文档？ | 是。符合 ADR-0071、§18 认知生命体映射 |
| 是否只改了受控边界？ | 是。仅修改 `roles.kernel` 的 `owned_paths`，不越界 |
| 是否引入了未声明 effect？ | 否。无新网络/磁盘/外部调用 effect，仅为 schema 和逻辑硬化 |
| 是否给出了真实验证结论？ | 是。每项都有明确的 acceptance criteria 和测试要求 |

---

## 7. 下一步动作

1. **本清单冻结后**，立即开始 `TurnOutcomeEnvelope` schema 设计（预计 0.5 天）
2. **Schema 评审通过后**，并行启动 Commit Protocol 和 ToolBatch 幂等键实现（预计 2 天）
3. **P0 核心完成后**，运行新增回归测试 + 全量 pytest（预计 1 天）
4. **验收通过后**，输出 Phase 1 验收报告，解冻进入 P1

**冻结声明**: 本清单自 2026-04-21 起冻结。任何变更必须通过 ADR 修订程序，由 Principal Architect 审批。
