# Kernel Loop 收敛审计报告

日期: 2026-03-24
状态: Phase 6A - 审计完成，收敛路径规划中
审计人: runtime-facade-lead
范围: `polaris/cells/roles/kernel/internal/`, `polaris/cells/roles/adapters/internal/`, `polaris/cells/roles/runtime/internal/`, `polaris/delivery/cli/`

---

## 1. 独立 Loop 清单

### 1.1 `WorkflowRoleAdapter.execute_role_with_tools()`

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/cells/roles/adapters/internal/workflow_adapter.py` |
| 行号 | 121-177 |
| 循环类型 | `for round_num in range(max_tool_rounds):` |
| 循环上限 | `max_tool_rounds` (默认 5) |
| 退出条件 | `not result.tool_calls or result.is_complete` 或达到最大轮数 |

**当前行为:**
- 每次循环调用 `kernel.run(role, request)`
- `tool_result` 通过字符串注入 `current_history`
- 当前 message 置空，依赖 history 驱动下一轮
- 工具结果格式: `f"[{tool_name}] success={...} error={...} result={...}"` (最长 240 字符截断)

**与 Blueprint §10 TurnEngine 统一伪代码差距:**

| 差距维度 | WorkflowRoleAdapter | Blueprint §10 目标 |
|----------|---------------------|-------------------|
| 状态管理 | 散乱 `current_message` + `current_history` 列表 | 统一 `ConversationState` |
| Transcript IR | 字符串元组 `(role, content)` | Typed `TranscriptItem` IR |
| 工具结果注入 | `f"[{tool}]..."` 字符串拼接 | 结构化 `ToolResult` + `call_id` 关联 |
| Provider 适配 | 透传 request 依赖 kernel | 统一 `ProviderAdapter` |
| 策略层 | 无 | `PolicyLayer.filter()` |
| 停止策略 | `is_complete` flag 或轮数硬上限 | `stop_policy.should_stop(state)` |
| 执行通道 | 单通道 (kernel.run) | `ExecutionLaneSelector` 选择 |

**收敛路径:**
- Phase 3: `WorkflowRoleAdapter` 改用 `TurnEngine.run()`
- Phase 6: 移除 `for` 循环，`TurnEngine` 内部处理续轮

---

### 1.2 `RoleExecutionKernel.run()` 工具循环

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/cells/roles/kernel/internal/kernel.py` |
| 行号 | 521-593 (`while True:`) |
| 循环类型 | `while True:` (工具续轮) |
| 退出条件 | `not last_parsed_tool_calls and not last_native_tool_calls` |
| 外层循环 | `for attempt in range(max_retries + 1):` (重试/修复) |

**当前行为:**
- 内部 `ToolLoopController` 管理 transcript 累积
- `controller.append_tool_cycle()` 注入工具结果到 context
- 工具执行在 `run()` 内直接处理 (通过 `_execute_tools_in_loop`)
- 质量验证在工具循环完成后执行，失败时重试 LLM 调用
- 停止条件: 无工具调用时 break，进入质量检查

**与 Blueprint §10 TurnEngine 统一伪代码差距:**

| 差距维度 | kernel.run() | Blueprint §10 目标 |
|----------|---------------|-------------------|
| 循环共核 | `run()` 与 `run_stream()` 逻辑部分重复但实现分离 | Stream/Non-stream 共核，差异仅在 event emitting |
| ConversationState | 散乱 local 变量 | 统一 `ConversationState` 对象 |
| Provider 适配 | `LLMCaller` 直接调用，无 adapter 层 | 统一 `ProviderAdapter.build_request()` / `decode_response()` |
| 策略层 | `ToolLoopController.register_cycle()` 做部分安全检查 | `PolicyLayer.filter()` 完整策略过滤 |
| 停止策略 | `break when no tool_calls` | `stop_policy.should_stop(state)` |
| Transcript IR | 散乱 dict/list | Typed `TranscriptItem` |
| ToolRuntime | 嵌入 kernel | 独立 `ToolRuntime.execute()` |

**收敛路径:**
- Phase 2: `TurnEngine` 骨架建立，吸收 `ToolLoopController`
- Phase 3: `run()` facade 降级为 `TurnEngine.run()` wrapper
- Phase 5: `LLMCaller` 替换为 `ProviderAdapter` 体系

---

### 1.3 `RoleExecutionKernel.run_stream()` 工具循环

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/cells/roles/kernel/internal/kernel.py` |
| 行号 | 798-930 (`while True:`) |
| 循环类型 | `while True:` (流式工具续轮) |
| 退出条件 | `not exec_tool_calls and not deferred_tool_calls` |

**当前行为:**
- 流式 yield chunk events (thinking_chunk, content_chunk, tool_call, tool_result)
- 每次循环: LLM stream → 解析 tool_calls → 同步执行工具 → yield tool_result
- 工具结果通过 controller 累积 transcript
- 循环内部 yield 流式事件，调用方消费 events

**与 Blueprint §10 TurnEngine 统一伪代码差距:**

| 差距维度 | kernel.run_stream() | Blueprint §10 目标 |
|----------|--------------------|-------------------|
| 循环共核 | 与 `run()` 部分逻辑重复但实现分离 | Stream/Non-stream 共核 |
| Transcript IR | 通过 controller 累积，未用 Typed IR | 统一 `TranscriptItem` |
| 工具执行 | 同步循环内执行 | `ToolRuntime` + `ExecutionLaneSelector` |
| 流式事件 | 散乱 chunk/type 字典 | 统一 `TurnEvents` 事件类型 |
| 策略层 | 无独立 PolicyLayer | `PolicyLayer` 外部过滤 |

**收敛路径:**
- Phase 2: `run_stream()` facade 降级为 `TurnEngine.run_stream()` wrapper
- Phase 3: Stream/Non-stream 共核实现

---

### 1.4 `StandaloneRunner.run_interactive()` 循环

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/cells/roles/runtime/internal/standalone_runner.py` |
| 行号 | 434-465 (`while self.status != AgentStatus.STOPPING:`) |
| 循环类型 | `while` 状态驱动 |
| 退出条件 | `status == STOPPING` 或 Ctrl+C/EOF |

**当前行为:**
- 交互式 CLI 循环: 读取用户输入 → 命令解析 → 执行 → 输出
- 支持命令: `/quit`, `/exit`, `/help`, `/tools`, `/plan`, `/status`
- 状态驱动: `AgentStatus.STOPPING` 时退出

**与 Blueprint §10 目标差距:**

| 差距维度 | StandaloneRunner | Blueprint §10 目标 |
|----------|------------------|-------------------|
| 执行路径 | 直接调用 agent，不走 `RoleRuntimeService` | 所有路径路由到 `RoleExecutionKernel` → `TurnEngine` |
| 状态管理 | 独立 `AgentStatus` 枚举 | 统一 `ConversationState` |
| Loop 归属 | 独立 loop | 仅为 facade，无独立 loop |

**架构标记:** 该文件已标记为 `DEPRECATED` (Phase 4)，生产路径已路由到 `RoleRuntimeService`。

**收敛路径:**
- Phase 4: `StandaloneRunner` 冻结，仅保留 CLI backward compatibility
- Phase 6: 确认生产路径无残留后，清理冻结注释

---

### 1.5 `StandaloneRunner._handle_autonomous_plan()` 循环

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/cells/roles/runtime/internal/standalone_runner.py` |
| 行号 | 529-555 (`for iteration in range(max_iterations):`) |
| 循环类型 | `for` 有界迭代 |
| 循环上限 | `max_iterations` (调用方控制) |
| 退出条件 | `result.status == "completed"` 或达到最大迭代次数 |

**当前行为:**
- 自主规划循环: Plan → Execute → 检查状态 → 必要时 Clarification
- 每次迭代创建任务计划，执行计划，检查完成状态

**与 Blueprint §10 目标差距:**

| 差距维度 | _handle_autonomous_plan | Blueprint §10 目标 |
|----------|-------------------------|-------------------|
| 执行路径 | 独立 loop | Graph Node 编排 `TurnEngine` |
| Loop 归属 | 独立 loop | AgentNode 下发 TurnEngine，Graph 负责路由/停止 |

**收敛路径:**
- Phase 4: 冻结模块内功能不动
- Phase 6: 当 Graph Runtime 成熟后，废弃此方法

---

### 1.6 delivery CLI `while True` 主循环

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/delivery/cli/director/cli_thin.py` |
| 行号 | 420-426 |
| 循环类型 | `while True:` |
| 退出条件 | `KeyboardInterrupt` |
| 用途 | API Server 保活循环 (sleep 1 循环) |

**审计结论:** 此 loop 仅用于 API server 保活，非 agent 执行 loop。不在收敛范围。

| 属性 | 值 |
|------|-----|
| 文件 | `polaris/delivery/cli/pm/cli_thin.py` |
| 行号 | 427-448 |
| 循环类型 | `while True:` |
| 退出条件 | `max_iterations` 或 `max_failures` 或异常 |
| 用途 | PM 迭代循环 (CLI loop mode) |

**审计结论:**
- 此 loop 是 CLI orchestration loop，非 agent turn loop
- 每次迭代调用 `_run_once()` → 内部调用 `RoleRuntimeService`
- 符合 Blueprint §13: "delivery host -> Runtime facade only"
- **不需要收敛到 TurnEngine**，因为它是 host/CLI 级别的迭代，不是 agent turn loop

---

## 2. 收敛原则

根据 Blueprint §13:

```text
run() facade           -> TurnEngine.run()
run_stream() facade    -> TurnEngine.run_stream()
WorkflowRoleAdapter    -> TurnEngine.run()
RoleAgent             -> facade only (无独立 loop)
delivery host         -> Runtime facade only (orchestration loop 除外)
```

### 2.1 允许保留的 loop

| Loop 类型 | 示例位置 | 理由 |
|-----------|----------|------|
| Orchestration loop | `cli_thin.py` PM 迭代 | host 级别迭代，非 agent turn |
| CLI event loop | `cli_thin.py` API server 保活 | 基础设施 loop |
| Status polling loop | delivery CLI | 非 agent 执行 loop |

### 2.2 必须收敛的 loop

| Loop | 当前状态 | 目标 |
|------|----------|------|
| `WorkflowRoleAdapter.execute_role_with_tools()` | 独立 `for` 循环 | 移除，使用 `TurnEngine.run()` |
| `kernel.run()` 工具续轮 | `while True` 在 attempt 循环内 | 吸收到 `TurnEngine` 内部 |
| `kernel.run_stream()` 工具续轮 | `while True` 在流式生成器内 | 吸收到 `TurnEngine.run_stream()` |

---

## 3. 当前已有正确组件

| 组件 | 状态 | 说明 |
|------|------|------|
| `ToolLoopController` | 保留演进 | 已有 `build_context_request()` / `append_tool_cycle()` / `register_cycle()`，演进为 `TurnEngine` 状态控制器 |
| `CanonicalToolCallParser` | 保留 | provider normalization 的一部分 |
| `strict textual tool protocol` | 保留 | 无 native tool call 时的 fallback |
| `RoleRuntimeService.stream_chat_turn()` | 保留为 delivery host 入口 | Blueprint §13 明确保留 |

---

## 4. 未完成 Gap

| Gap 编号 | 描述 | 阻塞依赖 |
|----------|------|----------|
| G-01 | `ConversationState` 未定义 | Phase 1A (TranscriptItem IR 定义) |
| G-02 | `TranscriptItem` IR 未定义 | Phase 1A |
| G-03 | `ProviderAdapter` 未建立 | Phase 5A (已完成 ProviderAdapter 契约) |
| G-04 | `ToolRegistry` 契约未建立 | Phase 3A |
| G-05 | `ToolRuntime` 双通道未建立 | Phase 4A |
| G-06 | `TurnEngine` 骨架未建立 | Phase 2A |
| G-07 | `run()` / `run_stream()` 未共核 | Phase 2A → Phase 3 |
| G-08 | `PolicyLayer` 未建立 | Phase 6B |

---

## 5. 收敛路线图

```
Phase 1A ──────────────────────────────────────────────────────────►
  定义 TranscriptItem IR + ConversationState                          │
  (Task #1, #12)                                                    │
                                                                     ▼
Phase 2A ──────────────────────────────────────────────────────────►
  TurnEngine 骨架 + ToolLoopController 升级                          │
  (Task #3, #14)                                                    │
                                                                     ▼
Phase 3A ──────────────────────────────────────────────────────────►
  ToolRegistry 契约 + 工具来源统一抽象                                │
  (Task #5, #15)                                                    │
                                                                     ▼
Phase 4A ──────────────────────────────────────────────────────────►
  ToolRuntime 双通道 (DirectExecutor + ProgrammaticExecutor)          │
  (Task #6, #16)                                                    │
                                                                     ▼
Phase 5A ──────────────────────────────────────────────────────────►
  ProviderAdapter 实现 (OpenAI / Anthropic) + LLMCaller 重构          │
  (Task #4, #13)                                                    │
                                                                     ▼
Phase 6A (本任务) ─────────────────────────────────────────────────►
  Runtime Facade Loop 审计 (已完成本文档)                            │
  (Task #7, #18)                                                    │
                                                                     ▼
Phase 6B ──────────────────────────────────────────────────────────►
  PolicyLayer 设计 (ToolPolicy / ApprovalPolicy / BudgetPolicy)       │
  (Task #8, #17)                                                    │
                                                                     ▼
Phase 7: 实施收敛 ─────────────────────────────────────────────────►
  - WorkflowRoleAdapter 改用 TurnEngine.run()                        │
  - run() / run_stream() facade 降级                                │
  - 移除各独立 tool loop                                            │
  - StandaloneRunner 冻结确认                                       │
```

---

## 6. 立即可执行的前置任务

在 Task #3 (TurnEngine 骨架) 完成后，以下任务可立即启动:

1. **Task #7a**: 创建 `polaris/cells/roles/kernel/internal/turn_engine.py` 骨架
2. **Task #7b**: 定义 `ConversationState` dataclass (基于 Task #1 IR)
3. **Task #7c**: `WorkflowRoleAdapter` 添加 `execute_role_with_tools_v2()` 候选方法，调用 `TurnEngine.run()`

---

## 7. 附录: 关键源码行号映射

| 源码位置 | 行号区间 | Loop 类型 |
|----------|----------|-----------|
| `workflow_adapter.py` | 121-177 | `for round_num in range(max_tool_rounds)` |
| `kernel.py run()` | 521-593 | `while True` (工具续轮) |
| `kernel.py run()` | 285-340 | `for attempt in range(max_retries)` (LLM 重试) |
| `kernel.py run_stream()` | 798-930 | `while True` (流式工具续轮) |
| `standalone_runner.py` | 434-465 | `while status != STOPPING` (交互式) |
| `standalone_runner.py` | 529-555 | `for iteration in range(max_iterations)` (自主规划) |

---

*本报告基于 2026-03-24 代码审计，与 `docs/POLARIS_NATIVE_AGENT_KERNEL_BLUEPRINT_2026-03-24.md` 保持一致。*
