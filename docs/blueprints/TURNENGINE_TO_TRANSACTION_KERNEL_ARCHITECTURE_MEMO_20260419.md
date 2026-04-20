# TurnEngine 到 TransactionKernel 架构演进备忘录

**日期**: 2026-04-19
**作者**: AI Architecture Audit
**状态**: 已审计，工程落地中
**权威代码路径**:
- 旧架构: `polaris/cells/roles/kernel/internal/turn_engine/`
- 新架构内核: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
- 新架构编排器: `polaris/cells/roles/runtime/internal/session_orchestrator.py`
- 状态机: `polaris/cells/roles/kernel/internal/turn_state_machine.py`
- 契约定义: `polaris/cells/roles/kernel/public/turn_contracts.py`

---

## 1. 一句话概括

系统从一个**"依靠大模型自觉性的单体死循环脚本"**，升级成了一个**"拥有严格物理法则的智能体操作系统（Agent OS）"**。

---

## 2. 核心隐喻：全能选手 vs. 现代工厂

### 2.1 旧 TurnEngine（全能选手）

大模型就像一个被关在小黑屋里的工人。你给他一个任务（Prompt），他自己决定用什么工具，用了之后自己看结果，看了结果再决定下一步。所有的流程控制、死磕到底、甚至纠错，全靠大模型"脑子清醒"。一旦他犯迷糊钻了牛角尖，整个系统只能陪着他死循环。

**代码表现**:
```python
# TurnEngine（旧）核心循环 —— 模型隐式控制一切
while True:
    response = await self._llm_caller.call(context)
    decision = self.policy.evaluate(response)
    if decision.should_stop:
        break
    tool_result = await self.kernel._execute_single_tool(decision.tool)
    context.append(tool_result)  # 上下文无限膨胀
```

### 2.2 新架构（现代工厂）

大模型被剥夺了流程控制权，退化为纯粹的**"单步决断大脑（Kernel）"**。外面包裹了**"车间主任（Orchestrator）"**和**"专业流水线（Runtime）"**。大脑只负责看一眼当前状态，给出下一步动作的意图（比如"我要修改这个文件"），然后就休息。车间主任负责把动作执行到位，提炼出结果，再把大脑唤醒进行下一次决断。

**代码表现**:
```python
# TransactionKernel（新）—— 单次原子事务，无循环
class TurnTransactionController:
    async def execute(self, turn_id, context, tool_definitions):
        # Phase 1: 一次决策请求
        llm_response = await self._call_llm_for_decision(context, tool_definitions)
        # Phase 2: 解码为唯一 TurnDecision
        decision = self.decoder.decode(llm_response, TurnId(turn_id))
        # Phase 3: 执行（直接回答 / 工具批次 / 移交）
        if decision.kind == TurnDecisionKind.FINAL_ANSWER:
            return await self._handle_final_answer(decision, ...)
        elif decision.kind == TurnDecisionKind.TOOL_BATCH:
            return await self._execute_tool_batch(decision, ...)
        # ... 无循环，一次决策即结束

# RoleSessionOrchestrator —— 显式循环，系统控制
class RoleSessionOrchestrator:
    async def execute_stream(self, prompt):
        while True:
            # 1. 执行干净单 Turn
            async for event in self.kernel.execute_stream(...):
                yield event
            # 2. 提取 SESSION_PATCH（降维记忆）
            envelope = self._build_envelope_from_completion(event)
            # 3. 熔断策略仲裁
            can_continue, reason = self.policy.can_continue(self.state, envelope)
            if not can_continue:
                break
            # 4. 构建下一轮 Prompt（工作记忆注入）
            next_prompt = self._build_continuation_prompt()
```

---

## 3. 架构维度对比表

| 维度 | 旧架构 (TurnEngine) | 新架构 (Orchestrator + TransactionKernel) |
|------|---------------------|-------------------------------------------|
| **控制流 (Loop)** | 内置 `while True` 循环，大模型隐式控制。上下文无限追加，直到模型自己说停。 | 内核**绝对无循环**（单决断）。循环由服务端的编排层状态机接管，且受熔断策略约束。 |
| **回合定义 (Turn)** | 一个回合 = 一整个复杂任务（可能包含几十次工具调用 + 多轮 LLM 对话）。 | 一个回合 = 仅仅**一次决策 + 一批工具的执行**（原子操作）。`len(TurnDecisions) == 1`。 |
| **上下文 (Context)** | **全局污染（Append-Only）**: 所有执行日志、几千行的代码搜索结果全塞进主 History，迅速打爆 Token。 | **降维提炼（ContextOS）**: 执行完即丢弃废料，只提取 `session_patch`（核心结论）带入下一轮，永远保持清爽。 |
| **断点续传 (Resume)** | **极其脆弱**: 中间如果断网或报错，之前的尝试全部丢失，只能从头重来。 | **坚如磐石**: 每个 Turn 之间自动 checkpoint (`_checkpoint_session`)。断电重启后，带着上一步的诊断结论完美继续。 |
| **死循环防御** | 靠暴力截断（Token 上限）或写死在代码里的 `max_steps`。 | 靠 `ContinuationPolicy`（熔断策略），连续几次无进展自动挂起，请求人工介入 (`WAITING_HUMAN`)。 |
| **可观测性** | 黑盒。前端只有一个转圈的 Loading 动画，跑了 5 分钟后，要么给最终答案，要么崩溃。 | 白盒。每次 Turn 之间 Emit 结构化事件（`TurnPhaseEvent`, `ToolBatchEvent`, `CompletionEvent`），客户端可实时渲染进度。 |
| **状态管理** | `ConversationState` 全局可变，任何阶段都能偷偷修改。 | `TurnStateMachine` 显式状态转换，**禁止** `TOOL_BATCH_EXECUTED -> DECISION_REQUESTED` 等危险回退。 |
| **容错/重试** | 重试逻辑散落在各处，Retry 次数和条件不透明。 | 异常决策通过 `TurnContinuationMode` 显式移交（`HANDOFF_WORKFLOW`, `WAITING_HUMAN`），不偷偷循环。 |

---

## 4. 深度解析三大核心区别

### 4.1 区别一：从"走一步看一步"到"显式意图驱动"

**旧架构的问题**:
模型如果想做"测试驱动开发（TDD）"，它只能在当前上下文中调用 `run_tests`，看到失败，再调用 `edit_file`。模型自己都不知道自己到底要循环多少次。流程控制完全依赖模型的"自觉性"——一旦模型陷入"我再读一下这个文件"的无限探索，没有任何机制能阻止它。

**新架构的解决方案**:
引入了强契约 (`TurnContinuationMode`)。模型不再瞎转悠，它必须明确向系统输出信号：

- `AUTO_CONTINUE`: "我查到了新线索，申请进入下一回合。"
- `HANDOFF_DEVELOPMENT`: "我要开始修 Bug 了，请把控制权交给代码运行时。"
- `WAITING_HUMAN`: "我遇到不确定的情况，需要人工确认。"
- `END_SESSION`: "任务已完成。"

系统拿到这个意图后，由编排器去调度资源。**控制权回到了系统代码手里，而不是大模型的幻觉里**。

**关键代码**:
```python
# polaris/cells/roles/kernel/public/turn_contracts.py
class TurnContinuationMode(str, Enum):
    END_SESSION = "end_session"
    AUTO_CONTINUE = "auto_continue"
    WAITING_HUMAN = "waiting_human"
    HANDOFF_EXPLORATION = "handoff_exploration"
    HANDOFF_DEVELOPMENT = "handoff_development"
```

### 4.2 区别二：消除"上下文爆炸 (Context Pollution)"

**旧架构最致命的痛点**:
假设模型要查一个深层 Bug，调用了 5 次 `search_code`，翻了 10 个无关文件。在旧 TurnEngine 里，这 10 个无关文件的内容会一直堆积在 Prompt 里，导致模型后期的注意力严重失焦，俗称"失忆症"。Token 消耗呈指数增长，最终必然触顶。

**新架构的解决方案**:
内核在回合结束时，强制提炼出一个 `session_patch` (工作记忆)。这是一个结构化的降维结论：

```python
# polaris/cells/roles/runtime/internal/continuation_policy.py
class SessionPatch(dict):
    # 任务宏观进度
    "task_progress": "exploring | investigating | implementing | verifying | done"
    # 已确认事实
    "suspected_files": ["src/auth.py"],
    "patched_files": ["src/db.py"],
    "error_summary": "Database timeout in auth flow",
    # 待验证假设
    "pending_files": ["src/config.py"],
```

到了下一回合，那 10 个无关文件的原文被彻底抛弃，模型看到的只有：

```xml
<Goal>修复数据库超时问题</Goal>
<Progress>当前阶段: investigating | 回合: 3 / 10</Progress>
<WorkingMemory>
  已确认:
    - 错误摘要: Database timeout in auth flow
    - 疑似问题文件: src/auth.py, src/db.py
  待验证:
    - src/config.py
</WorkingMemory>
<Instruction>继续深入调查。已识别疑似文件，关注错误栈和调用链。</Instruction>
```

这叫做**上下文降维**，它是长线复杂任务能跑通的唯一解。

### 4.3 区别三：状态机的物理法则约束

**旧架构的问题**:
`ConversationState` 是一个全局可变对象，任何组件在任何时刻都能修改它。没有显式的状态转换规则，"当前在哪一步"完全依赖代码中的隐式约定。这导致了一个经典 Bug：工具执行完后，代码不小心又回到了 "决策请求" 阶段，引发了 continuation loop。

**新架构的解决方案**:
`TurnStateMachine` 用显式的**允许/禁止转换矩阵**来约束所有状态迁移：

```python
# polaris/cells/roles/kernel/internal/turn_state_machine.py
_VALID_TRANSITIONS = {
    TurnState.DECISION_DECODED: {
        TurnState.FINAL_ANSWER_READY,
        TurnState.TOOL_BATCH_EXECUTING,
        TurnState.HANDOFF_WORKFLOW,
        TurnState.HANDOFF_DEVELOPMENT,
        TurnState.FAILED,
    },
    TurnState.TOOL_BATCH_EXECUTED: {
        TurnState.COMPLETED,               # finalize_mode=none/local
        TurnState.FINALIZATION_REQUESTED,  # finalize_mode=llm_once
        TurnState.HANDOFF_WORKFLOW,        # async pending
        TurnState.FAILED,
    },
}

# 关键：明确禁止的转换（旧架构的根源问题）
_FORBIDDEN_TRANSITIONS = {
    (TurnState.TOOL_BATCH_EXECUTED, TurnState.DECISION_REQUESTED),  # 禁止 continuation loop
    (TurnState.TOOL_BATCH_EXECUTED, TurnState.DECISION_DECODED),
    (TurnState.FINALIZATION_REQUESTED, TurnState.TOOL_BATCH_EXECUTING),  # 收口禁止触发新工具
}
```

任何试图违反这些规则的状态转换都会抛出 `InvalidStateTransitionError`，从机制上根绝了无限循环。

---

## 5. 四层正交架构映射

```
+---------------------------+
| 角色层 (Role)              |  pm / architect / chief_engineer / director / qa
| 赋予身份                   |
+---------------------------+
| 会话编排层                 |  RoleSessionOrchestrator
| (主控意识 + 记忆中枢)       |  + OrchestratorSessionState (structured_findings)
|                            |  + ContinuationPolicy (熔断策略)
+---------------------------+
| 专有运行时层               |  DevelopmentWorkflowRuntime
| (肌肉记忆 + 潜意识闭环)      |  + StreamShadowEngine (跨 Turn 推测预热)
+---------------------------+
| 事务内核层                 |  TurnTransactionController
| (心脏跳动 + 神经预激)       |  + TurnStateMachine (状态机)
|                            |  + TurnDecisionDecoder (决策解码)
|                            |  + KernelGuard (不变量断言)
+---------------------------+
```

**关键原则**: 上层可以调用下层，下层**绝对不可**回调上层。内核层对编排层的存在一无所知，它只接收 `context` 和 `tool_definitions`，返回 `TurnResult`。

---

## 6. 关键审计发现（2026-04-19）

在架构演进过程中，以下代码债务需要关注：

1. **Retry 循环的内核污染**: `_retry_tool_batch_after_contract_violation` 在 `TurnTransactionController` 内部实现了一个最多 8 次 LLM 调用的循环，并通过直接赋值 `state_machine.state = TurnState.DECISION_DECODED` 绕过了状态机禁令。这违背了"内核绝对无循环"的哲学。

2. **Orchestrator 信封路由的完整性**: `_build_envelope_from_completion` 需要从 `CompletionEvent.turn_kind` 正确推断 `TurnContinuationMode`，确保 `ask_user -> WAITING_HUMAN`、`handoff_workflow -> HANDOFF_EXPLORATION`、`final_answer -> END_SESSION`、`tool_batch -> AUTO_CONTINUE` 等路由全部激活。

3. **流式事件契约的完备性**: 所有流式路径（包括 `ASK_USER` 和 `HANDOFF_DEVELOPMENT`）都必须以 `CompletionEvent` 收尾，否则 Orchestrator 会误判为"内核崩溃"。

---

## 7. 总结

旧的 TurnEngine 是 AI 爆发初期的典型产物：用最暴力的 Prompt Engineering 解决所有问题。它适合做 Demo，但做不了企业级软件。

新的四层架构（**角色层 -> 编排层 -> 运行时 -> 事务内核**）则是成熟的软件工程理念在 AI 领域的重现。它把**"大模型的不确定性"死死地锁在了一个极其纯净的沙箱（Kernel）**里，用严密的状态机和持久化机制来兜底。

只有升级到这套新架构，"持续不间断运转、无人值守的软件开发工厂"才真正有了落地的物理地基。
