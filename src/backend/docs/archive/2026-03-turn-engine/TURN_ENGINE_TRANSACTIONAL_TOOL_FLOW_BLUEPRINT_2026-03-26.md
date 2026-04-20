# Turn Engine Transactional Tool Flow Blueprint

状态: Accepted Blueprint
日期: 2026-03-26
范围: `polaris/cells/roles/kernel/internal/turn_engine.py`、`polaris/cells/roles/kernel/internal/llm_caller.py`、`polaris/cells/roles/kernel/internal/tool_loop_controller.py`、`polaris/cells/roles/runtime/**`、`polaris/cells/orchestration/workflow_runtime/**`

> 这是目标蓝图，不是当前 graph truth。
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 为准。
> 本文只定义 TurnEngine 的重构目标、状态机、迁移阶段和收口裁决。
>
> 2026-04-16 起，本文作为**前序蓝图**保留；当前 canonical target-state 请统一参考
> `docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`。
> 2026-04-17 起，Phase 7 监控基线（TurnResult.metrics + stream complete.monitoring）也仅在上述 canonical 蓝图中维护。
> 本文中的 `handoff_workflow` / `HandoffPack` 语义也以现有 `ContextHandoffPack`
> 公开契约为准，禁止再派生 roles.kernel 私有 handoff model。

---

## 1. 结论

`turn_engine.py` 不应继续承担“流式显示 + thinking 解析 + 工具调用发现 + 工具执行 + 多轮 agent loop 收口”五件事。

最合理的目标架构是：

`Single transactional turn -> explicit decision -> batched tool execution -> optional one-shot finalization`

具体裁决：

1. `thinking` 永远不可执行。
2. 工具调用只能来自一次显式 `TurnDecision`，不能同时从流中途文本、native provider event、最终正文重解析三处并发产生。
3. 工具执行后不默认再次请求 LLM。
4. 若确实需要工具后总结，只允许一次 `finalize_mode=llm_once` 的收口请求，且明确 `tool_choice=none`。
5. 真正的多步探索、多轮读文件、多轮计划修正，不应继续放在 `TurnEngine` 内部 while loop 中实现，而应上移到 `workflow/runtime` 层。

---

## 2. 当前根因

当前 `TurnEngine.run()` / `run_stream()` 的结构性问题不是“偶发重复调用”，而是**执行授权点不唯一**。

今天同一个工具意图可能同时来自三条路径：

1. provider 的 native `tool_call`
2. assistant 正文中的 canonical wrapper 文本
3. 回合结束后对完整输出再次 parse

这会导致以下现象：

1. 同一轮里“看起来像思考中的工具调用”和“最终输出中的工具调用”都可能进入执行路径
2. 流式显示、执行协议、transcript 存储互相污染
3. 工具执行后下一轮是否继续请求 LLM 缺乏显式策略，只能靠 loop 自然收口
4. 同步工具、异步工具、长耗时工具都被塞进同一个 host-level turn loop

换句话说，问题根因不是 UI，而是：

`TurnEngine 缺少唯一提交点（single commit point for action execution）`

---

## 3. 架构原则

### 3.1 Single Commit Point

一个 turn 内，只有一个地方可以把“模型产物”升级为“可执行动作”：

- `TurnDecision`

任何 `thinking_chunk`、中间 token、wrapper 片段都只能是观测流，不能直接授权执行。

### 3.2 Thinking Is Telemetry

`thinking` / `reasoning_chunk` 的定位只能是：

1. UI 可选显示
2. trace / audit / debug
3. failure analysis

不能作为：

1. 工具执行来源
2. transcript 中的可回放指令
3. 下一轮 continuation 的执行依据

### 3.3 Tool Execution Is a Batch, Not a Loop Trigger

工具执行应是一个显式批次：

- `ToolBatchPlan`
- `BatchReceipt`

而不是“只要有 tool_call 就再次进入 LLM”。

### 3.4 Finalization Must Be Explicit

工具执行后是否需要再调用 LLM，必须由策略显式决定，而不是默认发生。

### 3.5 Multi-Step Reasoning Belongs Above TurnEngine

如果任务本质上需要：

1. 读文件
2. 再判断是否继续读
3. 再读更多文件
4. 再组织最终结论

那它是 `workflow/runtime` 级能力，不是单 turn 能力。

---

## 4. 目标分层

```text
delivery / host
  -> RoleRuntimeService
      -> TurnOrchestrator
          -> TurnEngine (single transactional turn)
              -> DecisionDecoder
              -> ToolRuntime.execute_batch()
              -> FinalizationPolicy
      -> WorkflowRuntime (multi-step orchestration only)
```

### 4.1 TurnEngine

只负责一次事务型 turn：

1. 构建上下文
2. 请求一次决策模型
3. 解码为 `TurnDecision`
4. 若有工具批，执行一次工具批
5. 按策略直接完成，或允许一次收口

`TurnEngine` 不再负责无限 continuation loop。

### 4.2 ToolRuntime

只负责执行工具批：

1. 同步工具
2. 异步工具
3. 并行只读工具
4. 串行写工具
5. receipt / artifact / error normalization

### 4.3 WorkflowRuntime

只负责真正多步 agent 任务：

1. exploration workflow
2. planning workflow
3. read-analyze-read workflow
4. approval / HITL / retry / handoff

---

## 5. Canonical Contracts

### 5.1 TurnDecision

```python
class TurnDecision(TypedDict):
    kind: Literal["final_answer", "tool_batch", "ask_user", "handoff_workflow"]
    visible_message: str
    reasoning_summary: str | None
    tool_batch: list[ToolInvocation]
    finalize_mode: Literal["none", "local", "llm_once"]
    metadata: dict[str, Any]
```

约束：

1. `kind=final_answer` 时 `tool_batch=[]`
2. `kind=tool_batch` 时，执行来源只能是这里
3. `visible_message` 仅面向用户显示，不参与执行
4. `reasoning_summary` 仅面向观测，不参与执行

### 5.2 ToolInvocation

```python
class ToolInvocation(TypedDict):
    call_id: str
    tool_name: str
    args: dict[str, Any]
    execution_mode: Literal["readonly_parallel", "readonly_serial", "write_serial", "async_receipt"]
```

### 5.3 BatchReceipt

```python
class BatchReceipt(TypedDict):
    batch_id: str
    results: list[ToolExecutionReceipt]
    success_count: int
    failure_count: int
    has_pending_async: bool
    artifacts: list[ArtifactRef]
```

### 5.4 TurnFinalization

```python
class TurnFinalization(TypedDict):
    mode: Literal["none", "local", "llm_once"]
    final_visible_message: str
    needs_followup_workflow: bool
    workflow_reason: str | None
```

---

## 6. 单次 Turn 状态机

```text
IDLE
  -> CONTEXT_READY
  -> DECISION_REQUESTED
  -> DECISION_DECODED
      -> FINAL_ANSWER
      -> TOOL_BATCH_EXECUTING
      -> ASK_USER
      -> HANDOFF_WORKFLOW
  -> TOOL_BATCH_EXECUTED
      -> FINALIZED_LOCAL
      -> FINALIZED_BY_LLM_ONCE
      -> HANDOFF_WORKFLOW
  -> COMPLETED
```

关键约束：

1. `DECISION_REQUESTED` 在一个 turn 中只出现一次
2. `FINALIZED_BY_LLM_ONCE` 最多一次
3. `TOOL_BATCH_EXECUTING` 结束后禁止再回到 `DECISION_REQUESTED`
4. 任何“还想继续调工具”的需求都必须转成 `handoff_workflow`

---

## 7. Thinking 是否要实现工具调用

结论：**不要**。

理由：

1. thinking 是 provider / model 的内部推理通道，不稳定，也不具备协议约束
2. 一旦允许 thinking 执行工具，host 就必须把中间流 token 当执行授权，架构上必然失控
3. thinking 和正文同时可执行时，重复调用与双重语义无法彻底消除
4. 审计上无法证明“到底是模型已提交动作，还是仍在草拟”

所以设计上必须把 thinking 与 executable action 完全断开。

---

## 8. 工具后是否还要再请求一次 LLM

答案不是绝对 yes/no，而是分三类：

### 8.1 `finalize_mode=none`

适用场景：

1. 工具本身返回的就是最终用户结果
2. 工具是 deterministic render / query / list / read
3. host 可以直接模板化输出

示例：

- `read_file`
- `list_directory`
- `search_code`
- `grep`
- `schema inspect`

### 8.2 `finalize_mode=local`

适用场景：

1. 工具结果结构化稳定
2. 最终回答可以本地模板渲染
3. 不需要模型再做开放式推理

示例：

- “已读取 3 个文件，核心模块如下”
- “搜索到 12 个命中，按文件分组如下”

### 8.3 `finalize_mode=llm_once`

只在以下场景允许：

1. 需要开放式语言综合
2. 需要跨多个工具结果做归纳
3. 需要写自然语言结论，而本地模板不足

但必须满足：

1. 只能一次
2. 强制 `tool_choice=none`
3. 若模型再次输出工具请求，直接协议错误，不继续 loop

---

## 9. 同步/异步工具如何处理

不要让 `TurnEngine` 直接分支处理同步/异步。

统一交给 `ToolRuntime.execute_batch(plan)`：

### 9.1 只读工具

- 可并行
- 可聚合结果
- 若全成功，可走 `none/local/llm_once`

### 9.2 写工具

- 串行
- 必须带 effect receipt
- 失败时立即中止或转人工确认

### 9.3 长耗时异步工具

- 返回 `pending receipt`
- 当前 turn 不等待最终业务结果
- 直接转 `handoff_workflow`

也就是说，“等待异步工具完成后再回头请求一次 LLM”不应该在单 turn 里做，而应该交给 workflow。

---

## 10. Transcript 改造方向

`ToolLoopController` 当前以字符串 history 为核心，这对“事务型单 turn”是不够的。

建议拆成两个层次：

### 10.1 Turn Ledger

单次事务的 typed record：

1. `DecisionRequested`
2. `DecisionDecoded`
3. `ToolBatchStarted`
4. `ToolReceiptAppended`
5. `TurnFinalized`

### 10.2 User Transcript

仅保留用户可见内容：

1. user message
2. assistant visible message
3. compact tool receipt summary

不要再把“可执行协议片段”和“用户显示文本”混存。

---

## 11. 对当前实现的直接裁决

### 11.1 要删除的行为

1. 流中途根据正文片段实时 parse 并授权执行工具
2. provider native tool_call 与最终文本 parse 并行作为执行来源
3. 工具执行完成后默认再次进入下一轮 LLM continuation
4. `turn_engine` 内部无限 while loop 驱动多步探索

### 11.2 要保留的行为

1. stream visible output contract
2. transcript sanitization
3. tool result compacting
4. safety policy / budget policy / approval policy

### 11.3 要上移的行为

1. 多轮探索
2. 反复读文件再决定下一步
3. 长任务 async wait
4. 任务级 retry / handoff / resume

这些都应上移到 `workflow/runtime`。

---

## 12. 推荐迁移路径

### Phase 1: 先立新契约，不改 UI

1. 新增 `TurnDecision`
2. 新增 `ToolBatchPlan`
3. 新增 `BatchReceipt`
4. 新增 `FinalizationPolicy`
5. 保持现有 host 输出协议不变

### Phase 2: 执行来源收口

1. 在 `LLMCaller` 明确 `decision_mode`
2. 只保留一个 action commit point
3. 文本工具协议从“执行协议”降为“兼容 fallback 决策载荷”

### Phase 3: TurnEngine 事务化

1. `run()` 和 `run_stream()` 共核到同一状态机
2. 删除内部 continuation while loop
3. 工具后走 `none/local/llm_once`

### Phase 4: 多步探索上移

1. 建立 `ExplorationWorkflow`
2. `TurnEngine` 遇到复杂任务时返回 `handoff_workflow`
3. workflow 负责下一步读文件、再规划、再调用 turn

### Phase 5: 异步工具外移

1. `pending receipt` 进入 workflow runtime
2. turn 不再阻塞等待异步工具结果
3. workflow 完成后再产生新的用户可见 turn

---

## 13. 验证门禁

重构完成后，至少新增以下门禁：

1. 单 turn 内，工具执行来源唯一性测试
2. `thinking` 永不执行测试
3. 工具后 `finalize_mode=none/local/llm_once` 三态测试
4. `llm_once` 下再次 tool_call 直接协议失败测试
5. `handoff_workflow` 不再触发 turn 内 continuation 测试
6. stream/run parity 测试
7. async tool returns pending receipt -> workflow handoff 测试

---

## 14. 最终裁决

对 Polaris 当前阶段，最合理的答案不是：

1. “thinking 中工具调用要不要支持”
2. “工具后要不要默认再问一次 LLM”

而是：

1. `thinking` 不可执行
2. 单 turn 只允许一次显式动作提交
3. 工具后默认不再请求 LLM
4. 真需要总结时，只允许一次禁止再调工具的收口请求
5. 真正多步探索统一交给上层 workflow

这条线能同时解决：

1. thinking / output 双重执行
2. 工具后无上限 continuation
3. 同步/异步工具混杂
4. stream/run 分叉
5. transcript 污染与审计困难

---

## 15. 受影响模块

目标态预计影响：

1. `polaris/cells/roles/kernel/internal/turn_engine.py`
2. `polaris/cells/roles/kernel/internal/llm_caller.py`
3. `polaris/cells/roles/kernel/internal/tool_loop_controller.py`
4. `polaris/cells/roles/kernel/internal/output_parser.py`
5. `polaris/cells/roles/runtime/public/service.py`
6. `polaris/cells/orchestration/workflow_runtime/**`

当前文档阶段不代表这些变更已经落地。

---

## 16. 领域默认策略

### 16.1 Document Domain

默认角色：

1. 中书令
2. 尚书令 PM
3. 工部尚书 Chief Engineer

默认收口：

1. 优先直接 `final_answer`
2. 若需要工具，优先 `tool_batch + llm_once`
3. 若发现任务需要多轮探索，直接 `handoff_workflow`

### 16.2 Code Domain

默认角色：

1. Director

默认收口：

1. 优先 `tool_batch + none/local`
2. 代码/仓库检索结果优先本地模板总结
3. 需要反复 read-analyze-read 时，直接 `handoff_workflow`

---

## 17. 当前模块到目标职责映射

| 当前模块 | 当前问题 | 目标职责 |
|---|---|---|
| `turn_engine.py` | 同时做渲染、解析、执行、续轮 | `TurnTransactionController` + `TurnStateMachine` |
| `llm_caller.py` | 决策请求与工具模式协商耦合 | `DecisionCaller` / `FinalizationCaller` |
| `output_parser.py` | native/textual 双通道混合解析 | `TurnDecisionDecoder` |
| `tool_loop_controller.py` | transcript 投影与续轮控制耦合 | `TranscriptProjector` + `TurnLedger` |
| `workflow_runtime` | 还未完全承接深度探索 | 多步探索、异步等待、恢复、handoff |

---

## 18. CLI 与观测契约

为了避免“其实请求了两次，但用户只能猜”的体验，CLI 与日志层必须绑定真实事务阶段：

1. `decision_requested` / `decision_completed`
2. `tool_batch_started` / `tool_batch_completed`
3. `finalization_requested` / `finalization_completed`
4. `workflow_handoff`

规则：

1. spinner 只能绑定真实 LLM 请求生命周期
2. 若发生 `llm_once`，必须显式显示为 `finalization`
3. 不能再把隐藏 continuation 伪装成一次普通工具调用
