# Phase 1 P0 事项与认知生命体架构对齐评估

**文档编号**: EVAL-2026-0421-COGNITIVE-ALIGNMENT  
**评估范围**: `adr-0082-transaction-kernel-phase1-decision-freeze.md` 的 6 项 P0 事项  
**对齐基线**: `AGENTS.md §18` + `COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md`  
**评估结论**: **全部兼容，且有显著增强**。3 项需要微调以最大化认知连续性。

**执行前提**: 以下 4 条工程约束必须同时满足，否则认知类比将从"设计对齐工具"退化为"运行时命名反模式"。

> **约束 1：认知类比不进入运行时命名**
> 认知生命体类比（"心脏""海马体""心电图"）仅用于架构解释，不作为 schema、类、字段、API 的命名依据。运行时命名必须保持工程一等公民风格。
>
> **约束 2：cognitive_state_delta 仅为派生投影**
> `cognitive_state_delta` 是可选、可派生、可丢弃的 summary projection，不能成为独立的第二真相源。它可以从 snapshot / truthlog / findings 重建。
>
> **约束 3：Structured Findings 写入 SessionArtifactStore 仅为派生记忆层**
> `SessionArtifactStore` 是 retrievable derived memory / convenience cache，`TruthLog` 才是 append-only event truth。handoff findings 必须先被 prompt builder / workflow runtime 消费，再派生写入长期记忆。
>
> **约束 4：Commit 关键一致性路径不得因异步化而变弱**
> commit 的长尾后处理（UI 更新、metrics、secondary indexing）可以异步，但 `durable commit critical section`（commit intent → truthlog append → snapshot materialization → seal / receipt linkage）必须保持严格顺序与一致性。

---

## 1. 评估框架

### 1.1 认知生命体四层架构回顾

```
角色层（Role）
    ↓ 赋予身份
会话编排层（RoleSessionOrchestrator + OrchestratorSessionState）— 主控意识
    ↓ 裁决"此刻该做什么"
专有运行时层（DevelopmentWorkflowRuntime）— 肌肉记忆/潜意识
    ↓ 自动执行 read→write→test
事务内核层（TurnTransactionController + KernelGuard）— 心脏/物理法则
    ↓ 单次神经放电，不可逆
ContextOS（TruthLog + Snapshot + WorkingState）— 海马体/记忆固化
```

### 1.2 Phase 1 的架构定位

Phase 1 的 6 项 P0 **全部落在"事务内核层"和"ContextOS 层"**，不触及"会话编排层"和"角色层"。

这意味着：
- **架构冲突风险低**：不会破坏主控意识的裁决逻辑和角色层职责分配
- **接口联调成本中等**：Orchestrator 对 TurnOutcome 的消费、ContinuationPolicy 对 FailureClass 的映射、Handoff builder 对 StructuredFindings 的使用都需要真实改动
- **收益明确**：事务内核层和 ContextOS 层的硬化让心脏跳动更规律、记忆固化更可靠、物理法则更严格
- **需要关注**：必须确保硬化后的事务内核仍能被主控意识正确消费（TurnOutcome → Orchestrator 的衔接）

---

## 2. 逐项对齐评估

### P0-1: TurnOutcomeEnvelope 标准化

**工程实体**: `TurnOutcome`, `CommitReceipt`, `SealedTurn`  
**认知映射**: **心脏跳动后的"心电图标准化输出"**

| 评估维度 | 对齐状态 | 说明 |
|---------|---------|------|
| **与心脏层关系** | ✅ 直接增强 | TurnOutcome 是单次神经放电（turn）的 canonical result，相当于心脏跳动的"标准心电图报告" |
| **与主控意识关系** | ⚠️ 需微调 | Orchestrator 当前消费 `TurnResult` dict，需确保 `TurnOutcome` 的投影兼容旧接口 |
| **与海马体关系** | ✅ 直接增强 | `commit_ref.snapshot_id` + `truthlog_seq_range` 直接对应"记忆固化的坐标" |
| **与物理法则关系** | ✅ 兼容 | `outcome_status` 的枚举值（COMPLETED/FAILED/PANIC/HANDED_OFF）让物理法则的裁决结果可结构化消费 |

**关键对齐点**:
- `TurnOutcome` 相当于认知生命体一次"心跳"的完整医学报告：决策类型、执行结果、收口策略、最终状态、记忆坐标
- **必须确保**: `RoleSessionOrchestrator.execute_stream()` 能无缝消费 `TurnOutcome`，不破坏 `can_continue()` 的决策逻辑
- **建议增强**: 在 `TurnOutcome` 中增加 `cognitive_state_delta` 字段（可选），记录本次 turn 对会话状态的变更摘要，帮助主控意识快速判断 continuation 策略

**认知价值**: ⭐⭐⭐⭐⭐（5/5）
- 让"心跳"从模糊输出变成结构化医学报告
- 主控意识（Orchestrator）可以更精确地判断"继续/停止/求助"

---

### P0-2: Commit Protocol 硬化

**工程实体**: `_execute_commit_protocol()` 三段式  
**认知映射**: **海马体写入纪律 — 从"随手记"升级为"标准化记忆固化流程"**

| 评估维度 | 对齐状态 | 说明 |
|---------|---------|------|
| **与海马体关系** | ✅ 直接增强 | Pre-commit validation → Atomic durable append → Post-commit seal 对应"记忆固化三步曲" |
| **与物理法则关系** | ✅ 直接增强 | 验证清单（7 项）是物理法则在提交阶段的显式执行 |
| **与主控意识关系** | ✅ 间接增强 | Seal 后的 `SealedTurn` 让 Orchestrator 可以精确恢复状态，防止"记忆碎片化" |
| **与 TruthLog 关系** | ✅ 核心增强 | Commit protocol 是 TruthLog append-only 语义的唯一合法入口 |

**关键对齐点**:
- `pre_commit_validate` = "记忆写入前的质量检查"：这次心跳是否合法？是否有幻觉？是否超出预算？
- `execute_commit_protocol` = "海马体写入"：TruthLog append + Snapshot materialization
- `post_commit_seal` = "记忆封印"：生成不可篡改的记忆坐标（hash + parent + seq range）
- **必须避免**: 不要把 commit protocol 做成"阻塞主控意识"的重量级操作。Orchestrator 的 `while True` 循环不应该被 commit 延迟卡住

**认知风险**: ⚠️ 低
- 如果 commit 太慢，会导致主控意识"心跳过缓"
- **缓解**: commit 必须是异步非阻塞的，或者 Orchestrator 在 commit 期间可以处理其他事件（如 UI 更新）

**认知价值**: ⭐⭐⭐⭐⭐（5/5）
- 记忆固化从"可能丢失"变成"可验证、可恢复"
- 防止认知生命体"失忆"或"记忆混乱"

---

### P0-3: ToolBatch 幂等键

**工程实体**: `batch_idempotency_key`, `side_effect_class`  
**认知映射**: **肌肉记忆的"动作去重" — 防止潜意识重复执行同一动作**

| 评估维度 | 对齐状态 | 说明 |
|---------|---------|------|
| **与肌肉记忆关系** | ✅ 直接增强 | `DevelopmentWorkflowRuntime` 执行 `read→write→test` 时，可能因重试触发相同工具。幂等键防止重复副作用 |
| **与心脏层关系** | ✅ 兼容 | ToolBatch 是心脏跳动的一部分，幂等性让同一心跳的重复执行不产生额外副作用 |
| **与物理法则关系** | ⚠️ 需明确 | `side_effect_class` 的分类（readonly/local_write/external_write）必须与 `ContinuationPolicy` 的副作用预算对齐 |

**关键对齐点**:
- `side_effect_class` 的分类直接影响"物理法则"的副作用预算计算
  - `readonly`: 不消耗 write budget
  - `local_write`: 消耗 workspace write budget
  - `external_write`: 消耗 external effect budget，且可能不可逆
- **必须与 `ContinuationPolicy` 集成**: `side_effect_class` 必须能被 `can_continue()` 读取，用于判断"是否还能继续执行"
- **潜意识（WorkflowRuntime）的受益**: WorkflowRuntime 在自动修复循环中经常重试，幂等键防止同一 patch 被重复应用

**认知价值**: ⭐⭐⭐⭐（4/5）
- 防止肌肉记忆在"自动修复"时重复执行同一动作
- 但核心价值在工程可靠性，对"认知"的直接影响次于 TurnOutcome 和 Commit Protocol

---

### P0-4: 收口阶段硬封印

**工程实体**: `tool_choice=none` 强制 + `no_tools_client`  
**认知映射**: **神经放电的"强制终止" — 防止心脏在收口期再次触发动作**

| 评估维度 | 对齐状态 | 说明 |
|---------|---------|------|
| **与心脏层关系** | ✅ 核心增强 | 收口阶段 = 神经放电的"不应期"，此时心脏必须停止，不能再次触发工具调用 |
| **与物理法则关系** | ✅ 核心增强 | 这是 KernelGuard "无隐藏连续法则" 的最关键 enforcement point |
| **与主控意识关系** | ✅ 间接增强 | 防止 LLM 在收口时"诈尸"，产生额外的、主控意识未预期的动作 |

**关键对齐点**:
- 心脏（TurnTransactionController）在收口阶段进入"绝对不应期"：
  - 从调用面就切断工具能力（`no_tools_client`）
  - 即使 LLM 产生 tool_calls 意图，也被 decoder 过滤
  - 即使 decoder 失败，KernelGuard 触发 panic
- **生物学类比**: 就像心肌细胞在收缩后必须有绝对不应期，防止心脏纤维性颤动（无限循环）
- **与 `ContinuationPolicy` 的协作**: 如果收口阶段检测到 tool_calls，不应只是 panic，还应该向 Orchestrator 发送特定的 `TurnEvent`，让主控意识知道"心脏出现了异常放电"

**认知价值**: ⭐⭐⭐⭐⭐（5/5）
- 这是防止认知生命体"精神分裂"和"Token 爆仓脑死亡"的最后一道防线
- 没有硬封印，所有其他约束都可能被 LLM 的"创造力"绕过

---

### P0-5: Failure Taxonomy 落地

**工程实体**: `FailureClass` Enum, `TransactionKernelError`  
**认知映射**: **异常感知的"神经分类学" — 让主控意识知道"哪里痛、为什么痛、能不能继续"**

| 评估维度 | 对齐状态 | 说明 |
|---------|---------|------|
| **与主控意识关系** | ✅ 直接增强 | `FailureClass` + `retryable` + `handoff_recommended` 直接支撑 `ContinuationPolicy.can_continue()` 的决策 |
| **与物理法则关系** | ✅ 直接增强 | `CONTRACT_VIOLATION` 是物理法则被打破时的标准信号 |
| **与脑电图关系** | ✅ 直接增强 | `FailureEvent` 必须进入 `TurnEvent` 流，让 UI/人类观测到"哪里出了问题" |

**关键对齐点**:
- `FailureClass` 的五类分类与认知生命体的"痛感类型"映射：
  - `CONTRACT_VIOLATION` = "心脏节律异常"（物理法则被打破）
  - `RUNTIME_FAILURE` = "肌肉拉伤"（工具执行失败）
  - `DURABILITY_FAILURE` = "记忆写入失败"（海马体故障）
  - `INSUFFICIENT_EVIDENCE` = "感知不足"（信息不够做决策）
  - `POLICY_FAILURE` = "行为被阻止"（超出预算或权限）
- **必须与 `ContinuationPolicy` 深度集成**:
  ```python
  # ContinuationPolicy.can_continue() 应该消费 FailureClass
  if last_turn_outcome.failure_class == FailureClass.CONTRACT_VIOLATION:
      return False, "contract_violation_stop"  # 心脏节律异常，必须停止
  if last_turn_outcome.failure_class == FailureClass.RUNTIME_FAILURE and not last_turn_outcome.retryable:
      return False, "unrecoverable_runtime_error"
  if last_turn_outcome.failure_class == FailureClass.INSUFFICIENT_EVIDENCE:
      return True, "need_more_evidence"  # 感知不足，可以继续探索
  ```
- **与 "重复失败熔断法则" 的协作**: `POLICY_FAILURE` 和 `RUNTIME_FAILURE` 的连续出现应该触发 `ContinuationPolicy` 的熔断逻辑

**认知价值**: ⭐⭐⭐⭐⭐（5/5）
- 让主控意识从"模糊地感觉不对劲"升级为"精确知道哪里痛、能不能继续"
- 这是认知生命体"自我保护本能"的工程化

---

### P0-6: Handoff Structured Findings

**工程实体**: `StructuredFindings` (confirmed_facts, rejected_hypotheses, open_questions, relevant_refs)  
**认知映射**: **跨海马体区域的"认知状态传递" — 防止多 turn 间的"集体失忆"**

| 评估维度 | 对齐状态 | 说明 |
|---------|---------|------|
| **与海马体关系** | ✅ 核心增强 | Structured findings 是"工作记忆"的结构化摘要，在 turn 间传递时防止信息丢失 |
| **与主控意识关系** | ✅ 核心增强 | Orchestrator 在 handoff 后可以基于 `confirmed_facts` 快速重建上下文，不需要重读所有历史 |
| **与潜意识关系** | ✅ 核心增强 | `DevelopmentWorkflowRuntime` 接收 handoff 时，可以直接消费 `relevant_refs` 和 `open_questions`，不需要重新探索 |

**关键对齐点**:
- 这是解决"collective amnesia"问题的核心工程方案
- **与 `SessionArtifactStore` 的协作**: `confirmed_facts` 应该同时写入 `SessionArtifactStore`，作为长期记忆的一部分
- **与 `StreamShadowEngine` 的协作**: ShadowEngine 的推测预热可以基于 `open_questions` 和 `relevant_refs` 进行更有针对性的预执行
- **生物学类比**: 就像人类大脑的工作记忆（prefrontal cortex）在任务切换时，会把关键假设和待办事项"写在小纸条上"，防止切换后失忆

**认知价值**: ⭐⭐⭐⭐⭐（5/5）
- 这是从"单 turn 有纪律"到"多 turn 有连续性"的关键桥梁
- 没有 structured findings，再硬的单 turn 内核也会在多 turn 任务中"失忆"

---

## 3. 综合评估结论

### 3.1 对齐状态总结

| P0 事项 | 与认知生命体关系 | 对齐状态 | 认知价值 | 风险等级 |
|---------|----------------|---------|---------|---------|
| TurnOutcomeEnvelope | 心电图标准化 | ✅ 完全对齐 | ⭐⭐⭐⭐⭐ | 低 |
| Commit Protocol | 海马体写入纪律 | ✅ 完全对齐 | ⭐⭐⭐⭐⭐ | 低 |
| ToolBatch 幂等键 | 肌肉记忆去重 | ✅ 完全对齐 | ⭐⭐⭐⭐ | 极低 |
| 收口阶段硬封印 | 神经不应期 | ✅ 完全对齐 | ⭐⭐⭐⭐⭐ | 极低 |
| Failure Taxonomy | 痛感分类学 | ✅ 完全对齐 | ⭐⭐⭐⭐⭐ | 低 |
| Structured Findings | 认知状态传递 | ✅ 完全对齐 | ⭐⭐⭐⭐⭐ | 低 |

### 3.2 关键增强建议（3 项微调）

基于认知生命体架构，建议在 Phase 1 中增加 3 个微调，以最大化认知连续性：

#### 微调 A: TurnOutcome 增加 `continuation_hint`（可选派生投影）

**理由**: 帮助主控意识（Orchestrator）快速判断 continuation 策略，不需要解析完整 snapshot。

**约束**: 必须是**可选、可派生、可丢弃的 summary projection**，不能成为独立 truth source。

```python
class TurnOutcome(_FrozenMappingModel):
    # ... 现有字段 ...
    continuation_hint: ContinuationHint | None = None

class ContinuationHint(BaseModel):
    """为 Orchestrator 和 UI 提供的轻量 continuation hint。
    
    注意：这是 derived summary，不是独立 truth source。
    可以从 snapshot / truthlog / findings 重建。
    """
    goal_progress_summary: str | None = None
    new_refs: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    continuation_hint: str | None = None  # "explore" | "repair" | "verify" | "stop"
    # confidence_delta: float  # Phase 1.5 后根据稳定 confidence contract 再评估
```

**影响**: 纯新增可选字段，不破坏现有契约。`ContinuationPolicy.can_continue()` 可以消费此字段做出更智能的决策。

#### 微调 B: Failure Taxonomy 与 ContinuationPolicy 映射表冻结

**理由**: 让物理法则的异常信号能直接驱动主控意识的"自我保护本能"。

**Phase 1 必须冻结的映射表**（即使 Phase 1.5 再接完整逻辑）：

| FailureClass | 默认 Continuation Action | 说明 |
|-------------|-------------------------|------|
| `CONTRACT_VIOLATION` | **STOP** | 心脏节律异常，必须停止 |
| `DURABILITY_FAILURE` | **STOP + SEEK HELP** | 记忆不可靠，停止并求助 |
| `RUNTIME_FAILURE(retryable=True)` | **CONTINUE with retry budget** | 肌肉拉伤，可重试 |
| `RUNTIME_FAILURE(retryable=False)` | **STOP** | 不可恢复的运行时错误 |
| `INSUFFICIENT_EVIDENCE` | **CONTINUE EXPLORE** | 感知不足，继续探索 |
| `POLICY_FAILURE` | **STOP or REROUTE** | 行为被阻止，停止或改道 |

**实施**: 在 `ContinuationPolicy.can_continue()` 中增加 `FailureClass` 的消费逻辑。Phase 1 先冻结映射表，Phase 1.5 实现完整消费逻辑。

```python
def can_continue(self, state: OrchestratorSessionState, envelope: TurnOutcome) -> tuple[bool, str]:
    if envelope.failure_class == FailureClass.CONTRACT_VIOLATION:
        return False, "contract_violation_stop"
    if envelope.failure_class == FailureClass.DURABILITY_FAILURE:
        return False, "durability_failure_stop"
    if envelope.failure_class == FailureClass.RUNTIME_FAILURE and not envelope.retryable:
        return False, "unrecoverable_runtime_error"
    # ... 现有逻辑 ...
```

**影响**: 修改 `ContinuationPolicy`，属于 Orchestrator 层。Phase 1 冻结映射表，Phase 1.5 实现消费逻辑。

#### 微调 C: Structured Findings 写入 SessionArtifactStore（派生记忆层）

**理由**: `confirmed_facts` 和 `rejected_hypotheses` 应该沉淀为长期记忆，但**顺序不能反**。

**约束**: SessionArtifactStore 是 **retrievable derived memory / convenience cache**，不是与 TruthLog 平级的真相源。

**正确执行顺序**:
1. **Phase 1**: `handoff pack` 带 `findings` → `prompt builder` / `workflow runtime` 真正消费 findings
2. **Phase 1.5**: 确认消费路径稳定后，再将 findings **派生投影**写入 `SessionArtifactStore`

**错误顺序**（禁止）:
- 先存到长期记忆 → 但下一 turn 根本没好好消费

**实施**: 在 `HandoffHandler` 生成 `ContextHandoffPack` 时，Phase 1 先确保 findings 被消费，Phase 1.5 再增加：

```python
# handoff_handlers.py (Phase 1.5)
async def _build_handoff_pack(...) -> ContextHandoffPack:
    findings = StructuredFindings(...)
    # Phase 1.5: 消费稳定后，派生写入长期记忆
    await session_artifact_store.store_cognitive_findings(
        session_id=session_id,
        turn_id=turn_id,
        findings=findings,  # derived projection, not canonical truth
    )
    return ContextHandoffPack(structured_findings=findings, ...)
```

**影响**: 新增 `SessionArtifactStore` 方法，属于 Orchestrator 层。Phase 1 预留消费路径，Phase 1.5 实现派生存储。

### 3.3 架构层级影响评估

```
角色层（Role）
    ↓ 无影响
会话编排层（Orchestrator）
    ↓ 微调 A/B/C 增强 continuation 决策（Phase 1.5）
专有运行时层（WorkflowRuntime）
    ↓ 幂等键直接受益（防止重复执行）
事务内核层（TurnTransactionController）
    ↓ 全部 6 项 P0 直接硬化心脏层
ContextOS（TruthLog + Snapshot）
    ↓ Commit Protocol 增强记忆固化，Structured Findings 增强记忆传递
```

**结论**: Phase 1 的改动是"心脏层和海马体的硬化"，不触及"主控意识"的裁决逻辑，但为后续主控意识的智能化提供了更可靠的基础设施。

---

## 4. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 | 认知映射 |
|------|------|------|---------|---------|
| TurnOutcome 过于复杂，阻塞 Orchestrator | 中 | 高 | 提供 `to_summary_dict()` 轻量投影 | 防止"心电图报告太厚，主控意识读不过来" |
| Commit Protocol 慢导致心跳过缓 | 低 | 中 | 长尾后处理异步，但 critical section 保持同步一致性 | 防止"记忆固化时心脏停跳" |
| Failure Taxonomy 分类不够，导致主控意识"误判" | 中 | 中 | Phase 1 先冻结映射表，Phase 1.5 根据实践补充分类 | 防止"痛感分类不全，无法对症下药" |
| Structured Findings 消费不足，继续失忆 | 中 | 高 | 验收标准强制 prompt builder 消费 findings 后才允许写入 SessionArtifactStore | 防止"写了小纸条但切换任务时没看" |
| 认知类比污染运行时命名 | 低 | 高 | 代码审查强制检查：schema/类/字段/API 不得出现认知隐喻 | 防止"隐喻驱动替代契约驱动" |
| 新增派生投影变成第二真相源 | 中 | 高 | `continuation_hint` 和 findings 必须标记 `derived=True`，并提供 `rebuild_from()` 方法 | 防止"snapshot 和 delta 不一致" |

---

## 5. 最终判断

> **Phase 1 的 6 项 P0 与认知生命体架构完全兼容，且构成心脏层与海马体层的基础硬化。**
>
> 它们不改变主控意识的裁决权分配，而是为主控意识提供更可靠的放电结果、异常信号、记忆固化与跨 turn 状态传递。建议立即执行。
>
> 同时增加 3 项接口级微调，但必须遵守四条约束：
> 1. 认知类比不进入运行时命名
> 2. `continuation_hint` 仅为派生投影
> 3. StructuredFindings 写入 SessionArtifactStore 仅为派生记忆层
> 4. commit 的关键一致性路径不得因异步化而变弱

**推荐执行顺序**:
1. **Phase 1（立即）**: 6 项 P0 内核硬化
2. **Phase 1.5（1 周后）**: 3 项微调（接口增强）
   - `TurnOutcome.continuation_hint` 派生投影
   - FailureClass → ContinuationPolicy 映射消费
   - StructuredFindings → SessionArtifactStore 派生记忆写入
3. **Phase 2**: 基于 hardened kernel 优化 Orchestrator 的 continuation 策略

---

**评估人**: Agent (Architect Review)  
**评估日期**: 2026-04-21  
**状态**: APPROVED — 建议立即执行，并预留 3 项微调接口
