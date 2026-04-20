# Phase 1 专家分工与执行计划

**决策依据**: `adr-0082-transaction-kernel-phase1-decision-freeze.md`  
**执行日期**: 2026-04-21  
**团队规模**: 6 名顶级 Python 专家  
**预计工期**: 3 天（并行执行，第 4 天集成验收）

---

## 1. 专家分工矩阵

| 编号 | 专家代号 | 负责 P0 事项 | 核心交付物 | 验收标准章节 | 依赖 |
|------|---------|-------------|-----------|-------------|------|
| **E1** | TurnOutcome Architect | TurnOutcomeEnvelope 标准化 | `TurnOutcome`, `CommitReceipt`, `SealedTurn` Pydantic 模型 | §2.1 | 无 |
| **E2** | Commit Protocol Engineer | Commit Protocol 硬化 | `_execute_commit_protocol()` 三段式实现 | §2.2 | E1 (schema) |
| **E3** | ToolBatch Idempotency Specialist | ToolBatch 幂等键 | `idempotency_key` + `side_effect_class` 集成 | §2.3 | E1 (schema) |
| **E4** | Finalization Guardian | 收口阶段硬封印 | `no_tools_client` + 封印标记 | §2.4 | E1 (schema) |
| **E5** | Failure Taxonomist | Failure Taxonomy 落地 | `FailureClass` Enum + 标准异常字段 | §2.5 | E1 (schema) |
| **E6** | Handoff Structured Findings Lead | Handoff 最小版 Structured Findings | `structured_findings` 四要素 + prompt 消费 | §2.6 | E1, E5 |

---

## 2. 协作规范（不可违反）

### 2.0 四大硬约束（违反者 block）

**约束 1：认知类比不进入运行时命名**
- 文档/评审中可以说"心脏""海马体""心电图"
- 代码中禁止出现 `hippocampus_commit`、`heartbeat_outcome`、`neural_guard` 等隐喻命名
- 运行时命名必须是工程一等公民：`TurnOutcome`、`CommitReceipt`、`FailureClass`、`StructuredFindings`
- **审查检查**: E1→E2→E3→E4→E5→E6 轮换审查时必须检查命名

**约束 2：派生投影不能成为第二真相源**
- `continuation_hint`（原 `cognitive_state_delta`）必须标记为 derived projection
- 必须提供 `rebuild_from(snapshot, truthlog)` 方法证明可重建
- 不得承载不可替代事实

**约束 3：Structured Findings 必须先消费后存储**
- Phase 1 必须先确保 `prompt builder` / `workflow runtime` 消费 findings
- `SessionArtifactStore` 写入只能在 Phase 1.5，且标记为 `derived_memory`
- 禁止"先存到长期记忆，但下一 turn 根本没读"

**约束 4：Commit critical path 不得异步化**
- 可异步的部分：UI 更新、metrics、secondary indexing、dashboard narrative
- **不可异步的部分**（critical path）：commit intent → truthlog append → snapshot materialization → seal / receipt linkage
- 必须保持严格顺序与一致性

### 2.1 接口契约

所有专家修改必须通过以下方式协调：

1. **不变量文件**: 修改 `docs/blueprints/BP-20260420-TXCTX-FULL-REMEDIATION.md` 中的 "Phase 1 Invariants" 部分
2. **契约评论**: 在代码修改处添加 `# PHASE1-INVARIANT: ...` 注释
3. **依赖声明**: 若 A 专家依赖 B 专家的修改，A 必须在任务描述中声明 `Depends on: E[B]`

### 2.2 提交顺序

```
Day 1 (上午): E1
  → 产出 TurnOutcomeEnvelope schema（Pydantic 模型）
  → schema 评审通过后，其余专家方可开始

Day 1 (下午) - Day 2: E2, E3, E4, E5 并行
  → E2: Commit Protocol 三段式实现
  → E3: ToolBatch 幂等键集成
  → E4: 收口阶段硬封印
  → E5: Failure Taxonomy 落地

Day 3: E6
  → 依赖 E1 schema + E5 failure taxonomy
  → Structured Findings 集成 + prompt 消费

Day 4: 集成验收
  → 全量 pytest
  → ruff + mypy
  → 验收报告
```

### 2.3 代码规范

所有专家必须遵守：

1. **类型安全**: 100% 类型注解，`mypy --strict` 通过
2. **异常处理**: 禁止裸 `except:`，必须捕获具体异常类型
3. **文档化**: 关键函数/类必须有 docstring，包含 `# PHASE1-INVARIANT:` 注释
4. **测试覆盖**: 每个修改必须有对应的 pytest 用例（Happy Path + Edge Cases + Exceptions + Regression）
5. **ruff 门禁**: `ruff check . --fix` 零错误，`ruff format .` 无变更

---

## 3. 各专家详细任务书

### E1: TurnOutcome Architect

**目标**: 定义 TurnOutcomeEnvelope 标准 schema，作为 Phase 1 所有下游的锚点

**任务**:
1. 在 `polaris/cells/roles/kernel/public/turn_contracts.py` 中新增：
   - `TurnOutcome` (Pydantic `_FrozenMappingModel`)
   - `CommitReceipt` 
   - `SealedTurn`
   - `ToolBatchExecution` (包装现有 tool batch 结果)
   - `FinalizationRecord`
   - `ContinuationHint` (派生投影，可选字段)
2. 确保与现有 `TurnResult` / `TurnLedger` 的 dict 兼容（`_FrozenMappingModel` 基类）
3. 在 `polaris/cells/roles/kernel/internal/transaction/ledger.py` 中增加 `to_turn_outcome()` 方法
4. **命名约束**: 所有类/字段必须使用工程命名（`TurnOutcome`, `CommitReceipt`, `SealedTurn`），禁止认知隐喻（`HeartbeatResult`, `NeuralSeal` 等）
5. **派生约束**: `ContinuationHint` 必须提供 `rebuild_from(snapshot, truthlog)` 类方法，证明它是可重建的 summary projection
6. 编写 `test_turn_outcome_envelope.py`：
   - 测试唯一性（每个 turn 只产生一个 envelope）
   - 测试下游消费接口（`to_dict()`, `to_json()`）
   - 测试 ledger 边界（禁止直接拼 raw ledger）
   - 测试 `ContinuationHint` 可重建性

**不变量**:
- `TurnOutcome` 是 turn 完成后唯一可被下游消费的 canonical result
- `TurnLedger` 是审计源，不是消费面
- `outcome_status` 必须是枚举值，禁止自由文本
- `ContinuationHint` 是 derived projection，不是独立 truth source

**Acceptance Criteria**:
- [ ] `TurnOutcome` 模型通过 mypy strict
- [ ] `TurnLedger.to_turn_outcome()` 生成合法 envelope
- [ ] 测试覆盖：唯一性、下游消费、ledger 边界、commit 引用
- [ ] `ContinuationHint.rebuild_from()` 能通过 snapshot + truthlog 重建相同 hint
- [ ] 代码审查通过命名检查（无认知隐喻）

---

### E2: Commit Protocol Engineer

**目标**: 把 `_commit_turn_to_snapshot()` 重构为 durable commit protocol

**任务**:
1. 在 `polaris/cells/roles/kernel/internal/kernel/core.py` 中：
   - 拆分 `_commit_turn_to_snapshot()` 为三段：
     - `_pre_commit_validate(ledger: TurnLedger) -> ValidationReport`
     - `_execute_commit_protocol(ledger: TurnLedger, snapshot: ContextOSSnapshot) -> CommitReceipt`
     - `_post_commit_seal(commit_receipt: CommitReceipt) -> SealedTurn`
2. 实现验证清单（7 项检查）：
   - single_decision
   - single_tool_batch
   - no_hidden_continuation
   - receipts_integrity
   - artifact_refs_valid
   - budget_balance
   - outcome_status_legal
3. 实现防双重提交：同一 `turn_id` 第二次 commit 返回已存在的 `SealedTurn`
4. **异步边界**:
   - **Critical path（不可异步）**: commit intent → truthlog append → snapshot materialization → seal / receipt linkage
   - **可异步**: UI 更新、metrics、secondary indexing（必须在 seal 成功后触发）
5. 在 `docs/blueprints/BP-20260420-TXCTX-FULL-REMEDIATION.md` 中更新 "Commit Protocol" 章节
6. 编写 `test_commit_protocol.py`：
   - 正常 commit 路径
   - 验证失败路径（每项验证单独测试）
   - 双重提交防护
   - 崩溃恢复模拟（pre-commit 后中断）
   - **异步边界测试**: 验证 critical path 是同步的，后处理是异步的

**不变量**:
- Commit protocol 是 durable，不是 ACID atomic
- 同一 turn_id 只能 seal 一次
- 未通过 pre-commit validation 的数据不允许进入 TruthLog
- critical path 必须保持同步一致性，不得因异步化而变弱

**Depends on**: E1 (schema: `CommitReceipt`, `SealedTurn`)

**Acceptance Criteria**:
- [ ] 三段式 commit 函数分离清晰
- [ ] 7 项验证全部实现并有独立测试
- [ ] 双重提交返回已有 SealedTurn 并写入 TruthLog no-op
- [ ] 文档明确说明 "durable commit protocol" 语义

---

### E3: ToolBatch Idempotency Specialist

**目标**: 为 ToolBatch 实现 batch-level 幂等键，预留 call-level 扩展位

**任务**:
1. 在 `polaris/cells/roles/kernel/internal/tool_batch_runtime.py` 中：
   - 给 `ToolExecutionContext` 新增字段：
     - `batch_idempotency_key: str`
     - `call_idempotency_key: str | None` (预留)
     - `side_effect_class: Literal["readonly", "local_write", "external_write"]`
2. 修改 `ToolBatchExecutor.execute_tool_batch()`：
   - 执行前先查询 `ReceiptStore`
   - 若 `batch_idempotency_key` 已存在且 receipt 完整 → 返回现有结果
   - 若不存在 → 执行工具并写入 receipt
3. `side_effect_class` 必须从 `ToolSpecPolicy` 或工具注册元数据读取
4. `idempotency_key` 生成公式：`f"{turn_id}:{batch_seq}:{payload_hash}"`
5. 编写 `test_toolbatch_idempotency.py`：
   - 首次执行成功
   - 重复执行返回已有 receipt
   - payload 变化导致 key 变化（重新执行）
   - side_effect_class 来源验证

**不变量**:
- 同一 batch 重试不产生重复副作用
- `side_effect_class` 不能运行时自由填写
- `idempotency_key` 必须包含 payload_hash

**Depends on**: E1 (schema: `ToolBatchExecution`)

**Acceptance Criteria**:
- [ ] `batch_idempotency_key` 格式正确且稳定
- [ ] 重复执行返回已有结果，不触发新工具调用
- [ ] `side_effect_class` 来自工具注册元数据
- [ ] payload 变化时 key 变化，重新执行

---

### E4: Finalization Guardian

**目标**: 实现收口阶段的硬封印，从调用面禁止工具能力

**任务**:
1. 在 `polaris/cells/roles/kernel/internal/transaction/finalization.py` 中：
   - 修改 `execute_llm_once()`：
     - 强制 `tool_choice="none"`（字符串，非 `None`）
     - 切换到 `no_tools_client` 或等效模式
     - request payload 增加 `_control` 字段：
       ```python
       _control = {
           "phase": "closing",
           "tools_disabled": True,
           "continuation_forbidden": True,
       }
       ```
2. 确保 `_control` 被 ContextOS `to_prompt_dict()` 过滤，不进入 data plane
3. 强化 `KernelGuard.assert_no_finalization_tool_calls()`：
   - 若收口阶段检测到 tool_calls，触发 `FailureClass.CONTRACT_VIOLATION`
   - 写入 ledger anomaly flag
   - 增加 prometheus counter（若可用）
4. 编写 `test_finalization_seal.py`：
   - 正常收口（无 tool_calls）
   - 违规收口（LLM 试图调工具）→ 触发 guard
   - `_control` 字段过滤验证
   - `tool_choice="none"` 强制验证

**不变量**:
- 收口阶段从调用面就砍掉工具能力，不只靠下游过滤
- 违规调用必须触发 CONTRACT_VIOLATION
- `_control` 不进入 prompt

**Depends on**: E1 (schema: `FinalizationRecord`)

**Acceptance Criteria**:
- [ ] 收口请求强制 `tool_choice="none"`
- [ ] 调用面切换到 no_tools 模式
- [ ] `_control` 字段存在且被过滤
- [ ] 违规调用触发 `KernelGuardError` + `FailureClass.CONTRACT_VIOLATION`

---

### E5: Failure Taxonomist

**目标**: 建立标准异常分类学，统一所有失败语义

**任务**:
1. 新建 `polaris/cells/roles/kernel/internal/transaction/failure_taxonomy.py`：
   - `FailureClass` Enum（5 类）
   - `FailureEvent` dataclass/Pydantic 模型
   - 标准异常基类 `TransactionKernelError`
2. 修改 `polaris/cells/roles/kernel/internal/kernel_guard.py`：
   - `KernelGuardError` 扩展字段：
     - `failure_class: FailureClass`
     - `phase: str`
     - `invariant_or_policy: str`
     - `retryable: bool`
     - `handoff_recommended: bool`
3. 修改 `TurnOutcome`（E1 产出）：
   - 增加 `failure_class: FailureClass | None`
   - 增加 `resolution_code: str`（`"completed"`, `"fail_closed"`, `"handoff_workflow"`, `"need_human"`）
4. 在所有 `KernelGuard.assert_*()` 方法中填充标准字段
5. 编写 `test_failure_taxonomy.py`：
   - 五类异常正确分类
   - 异常字段完整性
   - `TurnOutcome` 集成
   - 日志格式验证（`failure_id`, `failure_class`, `phase`）

**不变量**:
- 所有异常必须有 `FailureClass`，禁止自由文本异常
- `retryable` 和 `handoff_recommended` 必须显式设置
- 日志格式标准化

**Depends on**: E1 (schema: `TurnOutcome`)

**Acceptance Criteria**:
- [ ] `FailureClass` 五类定义完整
- [ ] `KernelGuardError` 包含所有标准字段
- [ ] `TurnOutcome` 包含 `failure_class` 和 `resolution_code`
- [ ] 所有 guard 方法填充标准字段
- [ ] 日志包含 `failure_id` + `failure_class` + `phase`

---

### E6: Handoff Structured Findings Lead

**目标**: 实现 handoff 认知状态的结构化传递，消除多 turn 失忆

**任务**:
1. 修改 `ContextHandoffPack`（ADR-0071 定义的位置）：
   - 新增 `structured_findings` 字段：
     ```python
     structured_findings: StructuredFindings | None
     
     class StructuredFindings(BaseModel):
         confirmed_facts: list[str]
         rejected_hypotheses: list[str]
         open_questions: list[str]
         relevant_refs: list[str]
         source_turn_id: str
         extracted_at: str  # ISO 8601
     ```
2. 在 `polaris/kernelone/context/context_os/runtime.py` 的 prompt builder 中：
   - **必须先实现**: 消费 `structured_findings.confirmed_facts`
   - 将 confirmed facts 注入 system message 或 context header
3. 在 `polaris/cells/roles/kernel/internal/transaction/handoff_handlers.py` 中：
   - 生成 handoff 时填充 `structured_findings`（最小四要素）
4. **Phase 1 不实现**（留给 Phase 1.5）: `SessionArtifactStore` 写入
   - Phase 1 只验证消费路径
   - Phase 1.5 才增加派生写入
5. 编写 `test_structured_findings.py`：
   - 四要素字段完整性
   - prompt builder 消费验证（**核心验收项**）
   - handoff 生成验证
   - 空值处理（`None` 时无注入）

**不变量**:
- handoff 不再只传 artifact refs
- `confirmed_facts` 必须在下一 turn prompt 中可见
- 四个字段都必须是 `list`（允许空，禁止 `None`）
- **Phase 1 不写入 SessionArtifactStore**，必须先消费后存储

**Depends on**: E1 (schema), E5 (failure taxonomy 用于 handoff 标记)

**Acceptance Criteria**:
- [ ] `StructuredFindings` 四要素字段完整
- [ ] prompt builder 消费 `confirmed_facts`（**必须先通过此项**）
- [ ] handoff 生成包含结构化发现
- [ ] 空值处理正确
- [ ] **无 SessionArtifactStore 写入代码**（Phase 1 禁止）

---

## 4. 集成验收计划（Day 4）

### 4.1 验收流程

```
Step 1: 单专家门禁（每人负责自己的测试通过）
  → pytest <expert_tests> -xvs
  → ruff check <modified_files> --fix
  → mypy <modified_files>

Step 2: 跨专家集成测试
  → pytest polaris/cells/roles/kernel/tests/ -x --tb=short
  → pytest polaris/kernelone/context/context_os/tests/ -x --tb=short

Step 3: 全量回归
  → pytest polaris/ -q
  → ruff check . --fix
  → mypy polaris/cells/roles/kernel/ polaris/kernelone/context/context_os/

Step 4: 验收报告
  → 输出 Phase 1 验收报告
  → 更新 ADR-0082 状态为 "IMPLEMENTED"
```

### 4.2 回归测试重点

| 测试目标 | 验证内容 | 负责专家 |
|---------|---------|---------|
| `test_turn_outcome_integration.py` | TurnOutcome 与 ledger、decoder、finalization 的集成 | E1 |
| `test_commit_end_to_end.py` | 完整 turn 的 commit 路径（pre → execute → seal） | E2 |
| `test_idempotency_regression.py` | 同一 batch 多次执行不产生副作用 | E3 |
| `test_finalization_violation.py` | 收口阶段 tool_calls 触发 guard | E4 |
| `test_failure_classification.py` | 所有异常正确分类到 FailureClass | E5 |
| `test_handoff_cognitive_continuity.py` | 多 turn 间 confirmed_facts 传递 | E6 |

---

## 5. 风险与升级路径

| 风险 | 概率 | 影响 | 缓解 | 升级触发条件 |
|------|------|------|------|-------------|
| E1 schema 阻塞下游 | 高 | 高 | Day 1 上午必须完成 schema 评审 | E1 延迟 > 4 小时则冻结 Phase 1 |
| 双轨并存（ledger + envelope） | 高 | 高 | 代码审查强制要求新代码消费 envelope | 发现 3 处以上直接拼 ledger 则 block |
| 幂等键稳定性 | 中 | 高 | E3 必须提供 `test_idempotency_stability.py` | payload_hash 算法变更需重新 freeze |
| 收口封印绕过 | 低 | 高 | E4 必须从调用面封印 | regression test 失败则 block |
| structured_findings 消费不足 | 中 | 高 | E6 验收标准强制 prompt 消费（先消费后存储） | prompt builder 不消费则 block |
| 认知隐喻污染运行时命名 | 低 | 高 | 代码审查强制检查：schema/类/字段/API 不得出现认知隐喻 | 发现隐喻命名则 block |
| 派生投影变成第二真相源 | 中 | 高 | `continuation_hint` 和 findings 必须标记 `derived=True`，提供 `rebuild_from()` | delta 与 snapshot 不一致则 block |

---

## 6. 专家沟通频道

### 6.1 每日站会（15 分钟）

- **时间**: 每天 10:00
- **内容**: 
  1. 昨天完成什么
  2. 今天计划什么
  3. 阻塞/依赖
- **规则**: 只说事实，不讨论方案（方案线下解决）

### 6.2 阻塞升级

```
专家间阻塞 → 直接 @ 相关专家，30 分钟内响应
跨域阻塞 → @ Principal Architect，2 小时内裁决
schema 变更 → 触发 freeze 修订程序
```

### 6.3 代码审查

- **审查者分配**: 
  - E1 → E2 审查
  - E2 → E3 审查
  - E3 → E4 审查
  - E4 → E5 审查
  - E5 → E6 审查
  - E6 → E1 审查
- **审查标准**: 必须通过 ruff + mypy + pytest + invariant 注释检查

---

**执行启动**: 本计划与 ADR-0082 同步冻结，立即生效。
