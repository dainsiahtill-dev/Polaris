# Polaris Transaction Kernel 架构白皮书

> **核心命题**：Polaris 不是把 LLM 包装成"会自己跑的 agent"，而是把 LLM 降级为事务内的受限决策组件，再由内核接管控制流、预算、审计和状态提交。

**版本**: v1.0  
**生效日期**: 2026-04-21  
**适用范围**: `polaris/cells/roles/kernel/` + `polaris/kernelone/context/context_os/`  
**关联文档**: `BP-20260420-TXCTX-FULL-REMEDIATION.md`, `ADR-0071`, `AGENTS.md §18`

---

## 1. 架构分水岭

### 1.1 传统 Agent 的计算模型：语言递归

在传统 ReAct/CoT 框架中，系统执行的本质是**模型主导的隐式递归**：

```
LLM 生成 Thought → 输出 Action → 系统执行 Observation → 结果返回 LLM
    ↓
LLM 再次生成 Thought → 可能再次输出 Action → 再次执行 Observation
    ↓
... 循环持续，直到 LLM "决定"不再输出 Action → 停止
```

在这个模型里：
- **基本计算单元**是"模型的一轮生成"
- **停止条件**是模型涌现出来的主观判断
- **状态边界**是模糊的，散落在 prompt、memory、tool output 各处
- **执行权**在 LLM 手中，系统是跟随者

这本质上是一个**披着工具外壳的、由语言模型主导的解释器循环**。

### 1.2 Polaris 的计算模型：事务执行

Polaris 将基本计算单元从"模型生成"提升为**"受约束、可提交、可审计的 Turn Transaction"**：

```
加载 Snapshot → 构建投影 → 请求 LLM 决策（仅一次）→ 解码决策
    ↓
如果是 TOOL_BATCH：执行工具（最多一个批次）→ 收口（LLM_ONCE/NONE/LOCAL）→ 提交
    ↓
如果是 FINAL_ANSWER：直接返回 → 提交
    ↓
如果是 HANDOFF：移交给 Workflow 层 → 提交当前状态
```

在这个模型里：
- **基本计算单元**是"一个 turn transaction"
- **停止条件是物理法则**，不是模型选择
- **状态边界**由状态机明确定义，所有状态迁移必须经过显式验证
- **执行权**在 Kernel 手中，LLM 只是决策生产者

---

## 2. 核心差异对比

| 维度 | 传统 Agent（语言递归） | Polaris Transaction Kernel（事务执行） |
|------|---------------------|----------------------------------------|
| **计算单元** | 模型的一轮生成（Thought-Action-Observation） | Turn Transaction：加载→决策→执行→收口→提交 |
| **停止权** | LLM 拥有（涌现式判断） | Kernel 拥有（物理法则强制） |
| **循环控制** | `while True` 隐式循环，无天然边界 | 单次事务，状态机禁止回到 DECISION_REQUESTED |
| **工具调用** | 模型可连续多次调用 | 每个 turn 最多一个 ToolBatch（`len <= 1`） |
| **状态语义** | 漂浮状态（prompt/memory/output 散落） | Durable Truth（只有 commit 进 ContextOS 才算数） |
| **资源约束** | 无法预算，可能无限消耗 | TransactionConfig 预设 token/工具/时间上限 |
| **审计能力** | 事后拼凑 conversation history | TurnLedger 记录完整状态轨迹和决策证据 |
| **失败模式** | 可能处于未知状态 | 状态机明确定义 FAILED，可补偿/可标记 |

---

## 3. Transaction Kernel 架构

### 3.1 Turn Transaction 定义

一个 **Turn Transaction** 是 Polaris 的最小执行原子，包含五个严格阶段：

```
┌─────────────────────────────────────────────────────────────┐
│  1. LOAD: 从 ContextOS 加载 snapshot，构建投影上下文          │
│  2. DECIDE: 请求 LLM 产生一次 TurnDecision（受约束）         │
│  3. EXECUTE: 如果决策包含工具，执行 ToolBatch（最多一个批次）  │
│  4. FINALIZE: 根据 DeliveryContract 选择收口策略              │
│  5. COMMIT: 将结果写入 ContextOS snapshot + TruthLog         │
└─────────────────────────────────────────────────────────────┘
```

**关键约束**：
- 阶段 2 的 LLM 请求**只能发生一次**
- 阶段 3 的工具批次**最多一个**
- 阶段 4 的收口阶段**禁止 LLM 再次触发工具**（`tool_choice=none`）

### 3.2 Turn State Machine

`TurnStateMachine` 管理事务生命周期，所有状态迁移必须经过显式验证：

```
IDLE → CONTEXT_BUILT → DECISION_REQUESTED → DECISION_RECEIVED → DECISION_DECODED
                                                              ↓
                    ┌─────────────────────────────────────────┼─────────────┐
                    ↓                                         ↓             ↓
            FINAL_ANSWER_READY                         TOOL_BATCH_EXECUTING  HANDOFF_*
                    ↓                                         ↓             ↓
                COMPLETED                          TOOL_BATCH_EXECUTED   (移交 workflow)
                                                          ↓
                                    ┌─────────────────────┼─────────────┐
                                    ↓                     ↓             ↓
                            FINALIZATION_REQUESTED    COMPLETED      HANDOFF_*
                                    ↓
                            FINALIZATION_RECEIVED
                                    ↓
                                COMPLETED
```

**禁止的迁移**（硬编码在状态机中）：
- `TOOL_BATCH_EXECUTED → DECISION_REQUESTED`（防止 continuation loop）
- `FINALIZATION_REQUESTED → TOOL_BATCH_EXECUTING`（防止工具链幻觉）

### 3.3 Kernel Guard 三大铁律

`KernelGuard` 在运行时强制执行三条不可违背的物理法则：

#### 铁律一：单次决策法则（Single Decision）

```python
assert len(turn_decisions) == 1
```

每个 turn 只能产生**一个** TurnDecision。如果 LLM 返回多个决策意图，decoder 必须将其合并或拒绝。

#### 铁律二：单次工具批次法则（Single Tool Batch）

```python
assert len(tool_batches) <= 1
```

每个 turn 最多执行**一个**工具批次。工具执行后不允许再次请求 LLM 决策继续调用工具。

#### 铁律三：无隐藏连续法则（No Hidden Continuation）

```python
assert hidden_continuation == 0
```

状态轨迹中禁止出现非法循环。如果 `DECISION_REQUESTED` 出现多次，或最后一个决策是未收口的 `tool_batch`，立即 panic。

**违反任何一条 → 立即 panic + handoff_workflow**，而不是让 LLM 继续"自由发挥"。

### 3.4 收口策略（Finalization）

工具执行后，系统（而非 LLM）决定如何收口：

| 策略 | 行为 | 使用场景 |
|------|------|---------|
| **NONE** | 直接返回工具结果作为可见输出 | 简单查询，无需 LLM 总结 |
| **LOCAL** | 本地模板渲染结果 | 结构化输出，确定性渲染 |
| **LLM_ONCE** | 调用 LLM 生成自然语言摘要 | 复杂结果需要解释性总结 |

**LLM_ONCE 的关键约束**：
- 强制 `tool_choice=none`
- 即使 LLM 在收口阶段试图调用工具，也会被 decoder 过滤
- 如果过滤失败，`KernelGuard.assert_no_finalization_tool_calls()` 触发异常

---

## 4. ContextOS 协作关系

ContextOS（`polaris/kernelone/context/context_os/`）是 **turn 间状态的"唯一真相源"**（Single Source of Truth）。

### 4.1 三层真相结构

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: WorkingState (运行期投影)                           │
│  - 当前活跃的对话历史、决策日志、待办事项                        │
│  - 运行期缓存，不是最终真相                                    │
│  - 每个 turn 开始时从 Snapshot 重建                           │
└─────────────────────────────────────────────────────────────┘
                            ↑↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Snapshot (物化状态)                                 │
│  - 某个 turn 完成后的 materialized state                      │
│  - 包含 turn_history, decision_log, tool_receipts, budget_plan │
│  - 可序列化、可比较、可回放                                    │
└─────────────────────────────────────────────────────────────┘
                            ↑↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: TruthLog (追加事件真相)                             │
│  - append-only 的事件日志                                     │
│  - 记录所有 turn 的决策、执行、异常、提交事件                   │
│  - 不可变，可审计，可重放完整执行历史                           │
└─────────────────────────────────────────────────────────────┘
```

**真相优先级**：
- **TruthLog** 是 canonical event source
- **Snapshot** 是某一时刻的物化视图
- **WorkingState** 是运行期投影，**不是**最终真相源

### 4.2 预算约束

ContextOS 的 `BudgetPlan` 在每个 turn 开始时决定资源上限：

```python
BudgetPlan(
    input_budget=...,      # 可用输入 token 数
    output_budget=...,     # 可用输出 token 数
    tool_budget=...,       # 可用工具调用次数
    time_budget_ms=...,    # 可用执行时间
)
```

Transaction Kernel 必须遵守这些约束。如果超出预算，触发 `emergency_truncate` 或 `handoff_workflow`。

### 4.3 Control-Plane 隔离

ContextOS 严格执行**控制平面/数据平面隔离**：

- **Data Plane**：LLM 可见的 prompt 内容（用户消息、工具结果、系统指令）
- **Control Plane**：内核控制信息（guard 约束、策略裁决、预算状态、审计标记）

**规则**：
- `metadata.plane == "control"` 的消息不会进入 prompt
- guard 约束、系统警告、思考残留不得直接回灌 LLM
- 控制面信息只能通过结构化方式影响数据面（如通过状态机状态、预算调整）

---

## 5. Handoff 分层原则

### 5.1 不是失败，是职责分离

当 Transaction Kernel 遇到以下情况时，不会尝试"硬撑"，而是将控制权**移交**（handoff）给上层 Workflow：

- 需要多步探索（multi-turn search / diagnose）
- 需要分支尝试（ speculative execution / branch-and-converge）
- 需要用户确认（ASK_USER 决策）
- 违反 Kernel Guard 铁律（panic 场景）

**关键认知**：handoff 不是 kernel 能力不够，而是**边界设计正确**。

```
┌─────────────────────────────────────────────────────────────┐
│  Workflow Layer (ExplorationWorkflow / Orchestrator)         │
│  ├─ 多步任务编排                                             │
│  ├─ 分支探索与收敛                                            │
│  ├─ 长期目标跟踪                                              │
│  └─ 跨 turn 策略调整                                          │
└─────────────────────────────────────────────────────────────┘
                            ↓ handoff / resume
┌─────────────────────────────────────────────────────────────┐
│  Transaction Kernel (TurnTransactionController)               │
│  ├─ 单次 turn 执行                                           │
│  ├─ 状态机守卫                                               │
│  ├─ 预算约束                                                 │
│  └─ 显式 commit                                              │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Deterministic Outer Loop

Polaris 不是不要多步任务，而是把**不确定性关进单个 turn**，把多步编排上移到**显式 workflow 层**。

- **Turn 内**：严格受限、短事务、单决策、可预测边界
- **Turn 间**：由 Workflow 显式推进，每一步都有明确的输入/输出契约

这叫：**把隐式 `while True`，拆成显式的 workflow graph**。

---

## 6. 常见误解澄清

### 误解一："Polaris 只能做一步，复杂任务做不了？"

**澄清**：Polaris **不是拒绝多步任务**，而是拒绝**无边界的隐式多步**。

复杂任务被分解为多个显式、可审计、可恢复的 turn transaction，由 workflow 层负责串联。每个 turn 都是原子单元，失败时可以精确定位到某一步，而不是整个会话处于未知状态。

### 误解二："LLM 被限制这么多，能力是不是被削弱了？"

**澄清**：LLM 的能力没有被削弱，只是**控制权被收回了**。

在传统 Agent 中，LLM 既是决策者又是调度者，这导致它经常"越权"（无限循环、幻觉调用工具）。在 Polaris 中：
- LLM 仍然是**强大的模式识别和推理引擎**
- 但系统不再把**执行控制权**交给它
- LLM 只需要在事务边界内做好**一次决策**

这实际上让 LLM 更专注于它擅长的（推理），而不是它不擅长的（资源管理、循环控制、状态一致性）。

### 误解三："Turn Transaction 不就是限制工具调用次数吗？"

**澄清**：限制工具调用次数只是表象，本质是**计算模型的升级**。

从"语言递归"到"事务执行"的区别在于：
- 不是"让模型少调几次工具"
- 而是"系统拥有停止权，模型只在边界内工作"
- 不是"加了点限制的传统 Agent"
- 而是"以 LLM 为组件的事务执行内核"

### 误解四："Snapshot 提交是不是性能开销很大？"

**澄清**：Snapshot 提交是**显式、可审计的 state persistence**，不是性能瓶颈。

- Snapshot 是追加写（append-only），不是原地修改
- 只有 turn 结束时才提交，不是每步都提交
- 开销远小于无限制循环中的重复 LLM 调用和工具执行
- 换来的可审计性和可恢复性在工程系统中是无价之宝

---

## 7. 总结

### 核心定位

**Polaris 不是"LLM agent with tools"，而是"一个以 LLM 为受限决策组件的事务执行内核"。**

这不是措辞差异，是架构层级的差异。

### 关键设计决策

| 设计点 | 传统方式 | Polaris 方式 |
|--------|---------|-----------------|
| 基本单元 | 模型生成 | Turn Transaction |
| 停止权 | LLM | Kernel Guard |
| 状态管理 | 散落/隐式 | ContextOS 三层真相 |
| 工具控制 | LLM 自主 | 单次批次 + 强制收口 |
| 多步任务 | 隐式循环 | 显式 Workflow handoff |
| 失败处理 | 未知状态 | 状态机 FAILED + 可补偿 |

### 工程价值

这套架构不是为了"更聪明"，而是为了**可运行、可观测、可进化**：

- **可审计**：TruthLog 记录完整执行轨迹
- **可恢复**：Snapshot 支持精确回滚和重放
- **可预算**：TransactionConfig 预设资源上限
- **可追责**：每个 turn 的状态迁移都有明确的责任方
- **可长期运行**：Kernel Guard 防止死循环和资源泄漏
- **可分层演进**：Workflow 层和 Kernel 层可独立迭代

---

> **最后一句**：Polaris 把 Agent 执行从"语言驱动的隐式循环"提升成了"内核驱动的显式事务系统"。这不是对 LLM 的不信任，而是对工程系统的尊重。
