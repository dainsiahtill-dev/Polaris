# Turn Engine Transactional Tool Flow Implementation Plan

状态: In Progress
日期: 2026-03-26
范围: `polaris/cells/roles/kernel/internal/turn_engine.py`、`polaris/cells/roles/kernel/internal/llm_caller.py`、`polaris/cells/roles/kernel/internal/output_parser.py`、`polaris/cells/roles/kernel/internal/tool_loop_controller.py`、`polaris/cells/roles/runtime/**`、`polaris/cells/orchestration/workflow_runtime/**`

> 本文是实施计划，不是 graph truth。
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 为准。
> 本文只定义如何把 `TurnEngine` 从隐式 agent loop 收敛为显式事务型 turn。

---

## 0. 实施进度快照（2026-03-26）

### 已完成

1. `TurnDecisionDecoder` 已落地，`thinking` 在 decoder 路径中不参与工具决策。
2. `TurnTransactionController` / `TurnStateMachine` / `TurnPhaseEvent` 已实现并有对应测试。
3. `kernel.py` 运行时 `KERNEL_DEBUG print` 已移除，避免调试噪音污染。
4. `kernel.py` 的 `_parse_content_and_thinking_tool_calls` 已收口为“只解析可见正文 + native tool calls”，`thinking` 不再进入可执行工具解析链路。
5. 新增回归测试覆盖“thinking wrapper 不可执行”和“native calls 保留、thinking wrapper 忽略”。
6. 新增回归测试覆盖事务型 handoff 边界：`async_operation` 直接移交 workflow，`pending_async_receipts` 不再触发隐式 continuation。
7. 新增回归测试覆盖 `workflow_handoff` 阶段渲染，避免 CLI/审计误把 handoff 当作普通完成态。
8. 新增 workflow-runtime 合同测试，确认 handoff 任务载荷与 pending async receipt 元数据在 workflow 边界可保留。

### 未完成（继续按 Phase 推进）

1. `TurnEngine` 主路径彻底切换到事务控制器（当前仍存在兼容路径）。
2. 工具批执行与 `ToolRuntime.execute_batch()` 的统一接入。
3. workflow handoff 与异步 pending receipt 的生产级恢复链路仍未完整上移到 workflow runtime。
4. CLI host 侧尚未原生发出 `workflow_handoff` turn-phase 事件；当前仅有 renderer/contract 级覆盖。

---

## 1. 目标结果（DoD）

1. 单个 turn 的执行授权来源唯一，只能来自一次显式 `TurnDecision`。
2. `thinking`、流式中间正文、回合末尾重解析都不再各自拥有独立执行权。
3. 工具执行完成后不再默认触发下一次 LLM 请求。
4. 只有 `finalize_mode=llm_once` 时允许一次总结请求，且该请求禁止再调工具。
5. 多步探索、异步等待、长任务推进全部上移到 `workflow_runtime`。
6. CLI/日志能明确打印 `decision -> tool_batch -> optional finalization`，不再把隐藏 continuation 伪装成一次普通工具调用。

---

## 2. 当前基线（2026-03-26）

### 2.1 已确认事实

1. `turn_engine.py` 的 `run()` 与 `run_stream()` 仍保留 turn 内 continuation loop。
2. `output_parser.py` 当前同时接收 native tool calls 与文本 wrapper fallback。
3. `tool_loop_controller.py` 会把 assistant 可见文本和 tool receipt 重新写回 transcript，作为下一轮 LLM 输入的一部分。
4. `llm_caller.py` 当前同时支持 native tools 与 text protocol fallback。
5. graph 中已经存在 `roles.kernel`、`roles.runtime`、`orchestration.workflow_runtime` 三层边界，可以承接这次职责重分配。

### 2.2 当前症状

1. 同一轮里，用户会感觉“thinking 里像调了一次工具，最终输出又调了一次”。
2. 工具结果返回后，会感觉系统又“偷偷问了一次模型”。
3. CLI 层看到的是噪音和重复阶段，而不是显式的事务状态。

---

## 3. 非目标（这次明确不做）

1. 不通过隐藏显示来掩盖重复执行问题。
2. 不继续给 `turn_engine.py` 增加 ad-hoc 去重 if/else。
3. 不把复杂探索继续塞回一个 turn 内的 while loop。
4. 不让 delivery/CLI 自己发明第二套 tool loop。
5. 不把目标蓝图写成“当前已完成事实”。

---

## 4. 分阶段落地路线

### Phase 1: Typed Contracts 先行

目标：

1. 固化单 turn 的 typed IR，先把“该说什么”和“该执行什么”分开。

交付：

1. `TurnDecision`
2. `ToolBatchPlan`
3. `BatchReceipt`
4. `FinalizationPolicy`
5. `TurnPhaseEvent`

实施点：

1. `polaris/cells/roles/kernel/public/contracts.py` 或等价 public contract 位置
2. `polaris/cells/roles/kernel/internal/turn_engine.py`

退出门禁：

1. 新增 contract/schema 单元测试
2. `run()` 与 `run_stream()` 可以共用同一份状态机输入输出模型

### Phase 2: 执行授权点收口

目标：

1. 一个 turn 只保留一个 action commit point。

交付：

1. `TurnDecisionDecoder`
2. `thinking` 降级为 telemetry-only
3. 文本 wrapper 降级为 compatibility decode input，而不是独立执行通道

实施点：

1. `polaris/cells/roles/kernel/internal/output_parser.py`
2. `polaris/cells/roles/kernel/internal/llm_caller.py`
3. `polaris/cells/roles/kernel/internal/turn_engine.py`

退出门禁：

1. 同一轮 native/textual 不会形成两次独立执行
2. `thinking` 永不触发工具执行

### Phase 3: TurnEngine 事务化

目标：

1. `TurnEngine` 只做 `Deliberate -> Act -> Finalize`。

交付：

1. `TurnTransactionController`
2. `TurnStateMachine`
3. `TurnLedger`

实施点：

1. `polaris/cells/roles/kernel/internal/turn_engine.py`
2. `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

退出门禁：

1. 删除 turn 内默认 continuation
2. 工具后只能走 `none/local/llm_once`

### Phase 4: Tool Batch Runtime 正式化

目标：

1. `TurnEngine` 不再关心同步/异步细节。

交付：

1. `ToolRuntime.execute_batch(plan)`
2. `readonly_parallel` / `write_serial` / `async_receipt` 统一执行协议
3. `BatchReceipt` 标准化结果

实施点：

1. `polaris/kernelone/agent/runtime/tool_runtime.py` 或既有 ToolRuntime 所在 canonical 路径
2. `polaris/cells/roles/kernel/internal/turn_engine.py`

退出门禁：

1. 只读工具可并行
2. 写工具串行且带 receipt
3. 异步工具返回 pending receipt 后直接上移 workflow

### Phase 5: Workflow Handoff 收口

目标：

1. 复杂任务不再由单个 turn 偷偷多轮推进。

交付：

1. `handoff_workflow` 决策路径
2. `ExplorationWorkflow` / 等价 workflow runtime 入口
3. pending async receipt 的恢复链路

实施点：

1. `polaris/cells/roles/runtime/**`
2. `polaris/cells/orchestration/workflow_runtime/**`

退出门禁：

1. 反复读文件再判断的任务会走 workflow
2. async waiting 不再阻塞 turn

### Phase 6: CLI 与观测契约对齐

目标：

1. 让用户看到真实状态，而不是猜测系统是不是“又偷偷请求了一次 LLM”。

交付：

1. `decision_requested`
2. `tool_batch_started` / `tool_batch_completed`
3. `finalization_requested` / `finalization_completed`
4. `workflow_handoff`

实施点：

1. `polaris/delivery/cli/**`
2. `polaris/cells/roles/kernel/internal/events.py`
3. `polaris/kernelone/llm/**` 中相关 trace/log 输出

退出门禁：

1. spinner 只绑定真实 LLM 请求生命周期
2. 若存在 `llm_once`，CLI 必须明确显示为“finalization”，而不是看起来像重复请求

---

## 5. 领域默认策略

### 5.1 Document Domain

默认角色：

1. Architect
2. PM PM
3. Chief Engineer Chief Engineer

默认策略：

1. 优先 `final_answer`
2. 若确需工具，优先 `tool_batch + llm_once`
3. 超过一次工具批的探索任务直接 `handoff_workflow`

### 5.2 Code Domain

默认角色：

1. Director

默认策略：

1. 优先 `tool_batch + none/local`
2. repo/code inspection 结果优先本地模板收口
3. 需要多轮 read-analyze-read 时直接 `handoff_workflow`

---

## 6. 工作包拆分（推荐顺序）

1. 先做 Phase 1 + Phase 2，先把执行授权点收口，不然所有后续优化都会反复回退。
2. 再做 Phase 3，把 `run()` / `run_stream()` 共核到同一事务状态机。
3. 然后做 Phase 4 + Phase 5，把同步/异步工具与 workflow handoff 的边界固化。
4. 最后做 Phase 6，让 CLI/日志呈现真实事务状态。

---

## 7. 质量门禁与验证

### 7.1 目标测试

1. `pytest polaris/cells/roles/kernel/tests/test_transaction_controller.py -q`
2. `pytest polaris/cells/roles/kernel/tests/test_turn_phase_renderer.py -q`
3. `pytest polaris/cells/orchestration/workflow_runtime/tests/test_handoff_contracts.py -q`
4. `pytest polaris/cells/roles/kernel/tests/test_tool_batch_runtime.py -q`
5. `pytest polaris/cells/roles/kernel/tests/test_state_machine.py -q`

### 7.2 验收信号

1. 单个 turn 中不会既从 thinking 又从正文执行工具。
2. 单个 turn 中不会既从 native tools 又从 textual fallback 独立执行两次。
3. `async_operation` handoff 路径不再触发工具执行或第二轮 LLM 请求。
4. pending async receipt 能保留在 workflow handoff 边界，不会在转换中丢失。
5. `workflow_handoff` 能被 renderer 正确标成 handoff，而不是普通 completed。

---

## 8. 风险与防御

1. 风险：只改 `turn_engine` 表层逻辑，`llm_caller` 与 `output_parser` 仍保留双通道授权。  
   防御：Phase 2 必须先做，先统一执行授权点。

2. 风险：迁移后 stream/run 再次分叉。  
   防御：两条路径必须共享同一个 `TurnDecision` 和状态机。

3. 风险：文档域与代码域行为差异不透明。  
   防御：域策略写成显式默认值，并在 debug/receipt 中输出。

4. 风险：用户误以为第二次 LLM 请求仍是 bug。  
   防御：只有 `llm_once` 才允许第二次请求，且 CLI 必须显式标记其语义。

---

## 9. 回滚与降级策略

1. 若事务型状态机未完全稳定，可先保留现有 host 输出协议，但不得恢复 thinking 可执行。
2. 若 native tools provider 不稳定，可暂时回退到文本兼容解码，但仍必须维持单一执行提交点。
3. 若 workflow handoff 未完全接好，可先返回明确错误或 ask_user，不得重新打开 turn 内无限 continuation。

---

## 10. 下一步执行顺序（建议）

1. 定义 typed contracts 与 phase events。
2. 把 `output_parser` 收敛为单一 `TurnDecisionDecoder`。
3. 重写 `turn_engine` 为事务状态机。
4. 把 `tool_loop_controller` 缩成 transcript/receipt projector。
5. 接通 workflow handoff 与 CLI phase rendering。
