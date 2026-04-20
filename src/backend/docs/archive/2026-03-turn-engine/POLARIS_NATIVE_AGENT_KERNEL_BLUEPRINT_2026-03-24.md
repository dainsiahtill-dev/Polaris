# Polaris Native Agent Kernel Blueprint

状态: Draft  
日期: 2026-03-24  
范围: `polaris/delivery/cli/`、`polaris/cells/roles/runtime/`、`polaris/cells/roles/kernel/`、`polaris/kernelone/llm/`、`polaris/infrastructure/tools/`

> 这是目标蓝图，不是当前 graph truth。  
> 本文不得替代 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`。  
> 当前正式边界仍以 graph/catalog/subgraph/cell manifest 为准。  
> 本文的作用是给后续重构提供统一的迁移蓝图、阶段计划和落点裁决。

---

## 1. 结论

Polaris 当前不应继续在多个入口、多个 loop、多个工具协议上做局部修补。

最终目标应明确收敛为：

`Typed transcript as kernel + Provider-native adapters + ToolRegistry/MCP tool bus + Dual execution runtime + WorkflowGraph orchestration + Trace/Evals/Guardrails control plane`

换成仓库内的表述就是：

1. `roles.kernel` 负责唯一共享的 `TurnEngine`
2. `kernelone.llm` 负责 provider-native transcript 归一
3. `ToolRegistry` / `ToolRuntime` / `MCPClientAdapter` 负责南向工具面
4. `roles.runtime` 只保留 facade / session / lifecycle
5. `WorkflowRoleAdapter` / `RoleAgent` / 后续 graph node 不再拥有独立 loop
6. policy / approval / budget / sandbox / trace / eval 全部放到模型外部的确定性层

---

## 2. 当前事实

截至 2026-03-24，当前仓库已经完成的正确收口只有第一步：

1. CLI 主机的流式链路已经统一经过 `RoleRuntimeService.stream_chat_turn()`
2. `roles.kernel` 的流式执行已从“伪 follow-up prompt 续轮”切到 transcript-driven continuation
3. 文本工具调用协议已经从“任意正文标签可执行”收紧到 canonical wrapper

当前相关落点包括：

- `polaris/cells/roles/kernel/internal/kernel.py`
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py`
- `polaris/cells/roles/kernel/internal/tool_call_protocol.py`
- `polaris/cells/roles/kernel/internal/output_parser.py`

但这些还只是 phase 0，不是最终架构。

当前仍存在的结构性问题：

1. `run()` 与 `run_stream()` 仍未完全共核
2. `ToolLoopController` 仍然更像 stream loop helper，而不是完整 `TurnEngine`
3. provider transcript 仍未统一成正式的 typed IR
4. `allowed_tool_names` 仍偏向 parser 白名单，而不是 registry selection policy
5. tool execution 仍未统一成标准 `ToolExecutionResult`
6. `WorkflowRoleAdapter`、历史 runner、旧角色链路仍然保留独立 loop
7. MCP 仍主要是 transport/client 层能力，尚未挂入正式 ToolRegistry
8. trace/eval 仍主要围绕结果观察，而不是“整条执行轨迹”

---

## 3. 这次蓝图明确拒绝什么

以下方向明确否决：

1. 在 CLI/Host 层继续自建 tool loop
2. 继续用字符串拼接方式表达 tool result transcript
3. 继续把正文示例、Markdown 代码块、说明文本当成可执行 tool call
4. 把 MCP 当成 northbound transcript 协议
5. 用 graph/FSM 直接替代 agent turn loop
6. 把审批、预算、越权治理继续塞进 prompt patching
7. 在 `WorkflowRoleAdapter`、`RoleAgent`、`delivery host` 中继续保留各自 loop

---

## 4. 目标架构蓝图

### 4.1 总体分层

```text
App / WorkflowGraph
  -> RouterNode / AgentNode / ApprovalNode / ReducerNode / HumanNode / EndNode
      -> TurnEngine
          -> ConversationState
          -> ProviderAdapter
          -> ToolRegistry
          -> ToolRuntime
          -> PolicyLayer
          -> Compactor
          -> TraceSink / EvalHooks
```

### 4.2 分层职责

#### A. Northbound Protocol Layer

职责：

1. 把 OpenAI / Anthropic / 其他 provider 的原生 tool-calling 线格式统一成 Polaris 内部 transcript IR
2. 保持 provider 差异只存在于 adapter 内部
3. 任何 loop 判定都基于 typed transcript item，而不是 prompt 文本

该层不负责：

1. 工具发现
2. 工具执行
3. MCP 连接
4. graph orchestration

#### B. Southbound Tool Layer

职责：

1. 统一管理工具来源、加载、schema、执行入口
2. 把内置工具、本地工具、MCP 工具、Agent 工具统一到一个 registry
3. 支持按需 materialize 工具，而不是一次性把所有工具塞进上下文

该层不负责：

1. transcript 归一
2. provider wire format
3. graph 调度

#### C. Execution Runtime Layer

职责：

1. 提供 `DirectExecutor`
2. 提供 `ProgrammaticExecutor`
3. 对同一批 tool calls 选择最合适的执行通道
4. 始终产出统一 `ToolExecutionResult`

#### D. Orchestration Layer

职责：

1. 在 `TurnEngine` 上层做 agent routing / handoff / approval / reducer / HITL
2. graph 只编排 turn，不直接实现 tool loop

#### E. Production Control Plane

职责：

1. trace
2. evals
3. guardrails
4. approval / budget / sandbox / redaction
5. recovery / checkpoint / resume

---

## 5. Canonical Transcript IR

### 5.1 最小 item 集合

`TurnEngine` 的唯一真相应是 transcript items，而不是字符串历史。

建议最小集合：

1. `SystemInstruction`
2. `UserMessage`
3. `AssistantMessage`
4. `ToolCall`
5. `ToolResult`
6. `ReasoningSummary`
7. `ControlEvent`

### 5.2 建议字段

#### `ToolCall`

```text
call_id
tool_name
args
provider
provider_meta
raw_reference
created_at
```

#### `ToolResult`

```text
call_id
tool_name
status
content
artifact_refs
metrics
retryable
error_code
error_message
created_at
```

#### `ControlEvent`

```text
event_type
reason
approval_required
budget_hit
compacted
handoff_target
metadata
```

### 5.3 关键约束

1. 所有 tool result 必须通过 `call_id` 对应到 tool call
2. 所有失败必须以 `ToolResult(status=error|blocked|timeout)` 回流 transcript
3. `TurnEngine` 的 stop condition 必须基于 transcript state，而不是 ad-hoc prompt
4. 任何 provider-specific 字段都只能存在 `provider_meta`

---

## 6. ConversationState

`TurnEngine` 不应直接拿散乱的 `message/history/tool_results` 参数运行。
应统一为 `ConversationState`。

### 6.1 最小状态模型

```text
session_id
run_id
role_id
transcript
loaded_tools
budgets
approvals
artifacts
working_state
checkpoint_cursor
compaction_state
```

### 6.2 单一职责

1. `transcript` 是对话真相
2. `loaded_tools` 是当前 turn 已 materialize 的工具集合
3. `budgets` 是确定性约束，不进入模型推理
4. `approvals` 是审批状态，不进入模型推理
5. `artifacts` 存大对象引用，不把大 payload 直接塞回 transcript

### 6.3 不应放入该对象的内容

1. provider client 实例
2. MCP transport 实例
3. graph node 实例
4. delivery CLI renderer 状态

---

## 7. ProviderAdapter 设计

### 7.1 目标

把 provider 差异完全收敛在 adapter 内：

1. OpenAI Responses / future Agents wire format
2. Anthropic Messages / tool_use / tool_result
3. 其他 provider 的 native tool call 表达

### 7.2 标准接口

```text
build_request(state, stream_mode)
decode_response(raw_response)
decode_stream_event(raw_event)
build_tool_result_payload(tool_result)
extract_usage(raw_response)
```

### 7.3 仓库落点建议

建议新增：

- `polaris/kernelone/llm/provider_adapters/base.py`
- `polaris/kernelone/llm/provider_adapters/openai_responses_adapter.py`
- `polaris/kernelone/llm/provider_adapters/anthropic_messages_adapter.py`
- `polaris/kernelone/llm/provider_adapters/factory.py`

当前 `LLMCaller` 后续应退化为 facade，不再自己承担 transcript 组装和 loop 控制。

---

## 8. ToolRegistry 设计

### 8.1 目标

把“当前给模型看哪些工具”从 parser 白名单升级成 registry selection policy。

### 8.2 标准接口

```text
list_core_tools(context)
search_tools(query, budget)
load_tools(tool_refs)
get_tool_schema(tool_id)
execute(tool_call)
```

### 8.3 工具来源

统一抽象为四类：

1. `BuiltInTool`
2. `LocalTool`
3. `MCPTool`
4. `AgentTool`

### 8.4 关键策略

1. 开局只暴露少量核心工具
2. 更多工具通过 search/load 按需进入当前 turn
3. `allowed_tool_names` 升级成 `registry selection policy`
4. role capability、governance policy、workspace guard 共同决定可 materialize 的工具集合

### 8.5 仓库落点建议

建议新增：

- `polaris/kernelone/agent/tools/registry.py`
- `polaris/kernelone/agent/tools/contracts.py`
- `polaris/kernelone/agent/tools/search.py`
- `polaris/kernelone/agent/tools/materializer.py`

现有 `polaris/infrastructure/tools/mcp_client.py` 保留为 transport adapter，不上提为 registry 真相。

---

## 9. ToolRuntime 设计

### 9.1 双执行通道

#### A. DirectExecutor

适用于：

1. 工具少
2. 结果小
3. 不需要聚合
4. 无复杂分支

标准流程：

`model -> tool_call -> runtime execute -> tool_result -> model`

#### B. ProgrammaticExecutor

适用于：

1. 高 fan-out
2. 大量中间结果
3. 需要筛选、聚合、循环、条件分支
4. 需要对工具结果做预处理后再回模型

### 9.2 运行时选择器

建议引入：

`ExecutionLaneSelector`

判断信号至少包括：

1. tool 数量
2. 预估结果体积
3. 是否需要批处理
4. 是否需要循环
5. role 权限是否允许 programmatic execution

### 9.3 统一结果模型

建议引入：

```text
ToolExecutionResult
  call_id
  tool_name
  status: success|error|blocked|timeout
  output
  artifact_refs
  retryable
  error_code
  error_message
  metrics
```

### 9.4 硬规则

1. 工具执行失败不得直接把 turn 打崩
2. 所有错误都要转成 `ToolResult`
3. 大结果优先落 artifact，再给 transcript 写摘要和引用

### 9.5 仓库落点建议

建议新增：

- `polaris/kernelone/agent/runtime/tool_runtime.py`
- `polaris/kernelone/agent/runtime/direct_executor.py`
- `polaris/kernelone/agent/runtime/programmatic_executor.py`
- `polaris/kernelone/agent/runtime/execution_lane_selector.py`

---

## 10. TurnEngine 设计

### 10.1 目标

`TurnEngine` 是唯一共享 loop。

以下位置都不得再拥有自己的 loop：

1. `run()`
2. `run_stream()`
3. `WorkflowRoleAdapter`
4. `RoleAgent`
5. delivery host

它们只能是 facade / adapter / node shell。

### 10.2 统一伪代码

```text
while True:
    provider_request = provider_adapter.build_request(state, mode)
    provider_response = model.generate(provider_request, stream=mode.streaming)

    delta = provider_adapter.decode(provider_response)
    state.append(delta.transcript_items)

    if not delta.tool_calls:
        break

    approved_calls = policy_layer.filter(delta.tool_calls, state)
    execution_lane = lane_selector.choose(approved_calls, state)
    tool_results = tool_runtime.execute(approved_calls, execution_lane, state)

    state.append(tool_results.to_transcript_items())

    if stop_policy.should_stop(state):
        break

    state = compactor.maybe_compact(state)
```

### 10.3 Stream / Non-stream 共核

唯一允许的差异：

1. event emitting
2. chunk projection
3. final serialization

不允许的差异：

1. 不同 continuation logic
2. 不同 tool parsing logic
3. 不同 stop condition
4. 不同 policy enforcement

### 10.4 仓库落点建议

建议新增或演进：

- `polaris/cells/roles/kernel/internal/turn_engine.py`
- `polaris/cells/roles/kernel/internal/conversation_state.py`
- `polaris/cells/roles/kernel/internal/transcript_ir.py`
- `polaris/cells/roles/kernel/internal/turn_events.py`
- `polaris/cells/roles/kernel/internal/stop_policy.py`
- `polaris/cells/roles/kernel/internal/compactor.py`

当前 `ToolLoopController` 的演进方向：

1. 保留
2. 改名或升级为 `TurnEngine` 的内部状态控制器
3. 不再只绑定 stream 场景

---

## 11. Policy Layer 设计

policy 必须放在模型外部的确定性层。

建议拆成：

1. `ToolPolicy`
2. `ApprovalPolicy`
3. `BudgetPolicy`
4. `SandboxPolicy`
5. `RedactionPolicy`

### 11.1 职责边界

#### `ToolPolicy`

- 当前 role 是否允许某工具
- 当前 workspace 是否允许某路径/命令
- 当前 turn 可 materialize 哪些工具

#### `ApprovalPolicy`

- 是否要求人工确认
- 哪些工具必须审批
- 哪些外部副作用必须二次确认

#### `BudgetPolicy`

- 最大总 tool calls
- 最大 wall time
- token / result size / artifact 数量预算

#### `SandboxPolicy`

- 进程执行
- 文件系统范围
- 网络访问范围

#### `RedactionPolicy`

- 日志脱敏
- trace 脱敏
- prompt / tool result 中的敏感字段遮罩

---

## 12. Graph Runtime 的位置

graph 必须在 `TurnEngine` 之上。

### 12.1 Graph 负责什么

1. route
2. handoff
3. reducer
4. approval gate
5. HITL
6. end condition

### 12.2 Graph 不负责什么

1. provider-native transcript 解析
2. tool call 解析
3. tool execution loop
4. tool result transcript append

### 12.3 未来节点形态

```text
WorkflowGraph
  RouterNode
  AgentNode
  ApprovalNode
  ReducerNode
  HumanNode
  EndNode
```

其中：

`AgentNode -> TurnEngine`

而不是：

`Graph node 自己实现一套 tool loop`

---

## 13. 对当前仓库的明确裁决

### 13.1 保留并继续演进的对象

1. `RoleRuntimeService.stream_chat_turn()`  
   保留为 delivery host 的 canonical 入口
2. `ToolLoopController`  
   保留，但升级为共享 `TurnEngine` 的状态控制器
3. `CanonicalToolCallParser`  
   保留，但定位为 northbound provider normalization 的一部分
4. `strict textual tool protocol`  
   保留，作为无 native tool call 时的 fallback

### 13.2 必须降级为壳层的对象

1. `run()` facade
2. `run_stream()` facade
3. `WorkflowRoleAdapter`
4. `RoleAgent`
5. delivery CLI/TUI host

### 13.3 必须新增的中间层

1. `ConversationState`
2. `TranscriptItem`
3. `ProviderAdapter`
4. `ToolRegistry`
5. `ToolExecutionResult`
6. `ExecutionLaneSelector`
7. `ProgrammaticExecutor`
8. `TraceSink / EvalHooks`

---

## 14. 代码落点蓝图

### 14.1 `polaris/cells/roles/kernel/`

建议承载：

- `turn_engine.py`
- `conversation_state.py`
- `transcript_ir.py`
- `stop_policy.py`
- `compactor.py`
- `policy_bridge.py`

不应再承载：

- provider SDK 细节
- MCP transport
- graph orchestration

### 14.2 `polaris/kernelone/llm/`

建议承载：

- provider adapters
- usage extraction
- provider-native event normalization

不应承载：

- Polaris role policy
- business approval logic

### 14.3 `polaris/kernelone/agent/`

建议新增，承载：

- tool registry
- tool runtime
- execution lanes
- trace / resume / checkpoint 的通用 agent runtime 能力

### 14.4 `polaris/infrastructure/tools/`

保留为：

- MCP transport
- 外部工具协议适配器
- 具体 backend/client

### 14.5 `polaris/cells/roles/runtime/`

保留为：

- public facade
- session binding
- lifecycle
- host-facing command/result contract

不应继续承载：

- 第二套 loop
- 第二套 tool runtime

---

## 15. 分阶段迁移计划

## Phase 0

状态：已开始  
目标：止住最明显的不稳定行为

已完成方向：

1. CLI 流式链路统一走 `RoleRuntimeService.stream_chat_turn()`
2. stream continuation 改成 transcript-driven
3. 文本工具协议改成 canonical wrapper

Phase 0 DoD：

1. 工具执行后能继续真实续轮
2. 工具示例不再误执行
3. CLI/host 不再直连 LLM loop

## Phase 1

目标：抽出 typed transcript 与 `ConversationState`

工作项：

1. 新建 `TranscriptItem` 契约
2. 新建 `ConversationState`
3. `kernel.py` 不再直接用裸 `history/message/tool_results`
4. stream 与 non-stream 都基于 `ConversationState`

DoD：

1. 内核内部只操作 `ConversationState`
2. transcript append 有统一入口
3. `ToolResult` 已有 `call_id`

## Phase 2

目标：把 `ToolLoopController` 升级为唯一共享 `TurnEngine`

工作项：

1. 新建 `turn_engine.py`
2. `run()` 和 `run_stream()` 退化成 facade
3. stop condition、policy filter、continuation logic 全部共核

DoD：

1. `run()` / `run_stream()` 不再各自维护 loop
2. stream / non-stream 只有 I/O surface 差异
3. 旧循环 helper 被清理或降为内部组件

## Phase 3

目标：引入 `ToolRegistry`

工作项：

1. 把 `allowed_tool_names` 升级为 registry policy
2. 加入 `list_core_tools`
3. 加入 `search_tools`
4. 加入 `load_tools`
5. 统一 tool schema 获取方式

DoD：

1. 当前 turn 的工具集合来自 registry materialization
2. 工具白名单不再只存在 parser 层
3. role capability 与 governance policy 能共同约束 registry

## Phase 4

目标：统一 `ToolExecutionResult` 和 dual execution lane

工作项：

1. 实现 `DirectExecutor`
2. 设计 `ProgrammaticExecutor` 契约
3. 实现 `ExecutionLaneSelector`
4. 所有异常改为 `ToolResult(status=error|blocked|timeout)`

DoD：

1. tool failure 不再直接打崩 turn
2. 大结果支持 artifact 化
3. direct/programmatic 有正式选择逻辑

## Phase 5

目标：provider-native adapter 收口

工作项：

1. OpenAI adapter
2. Anthropic adapter
3. 统一 usage / tool-call / tool-result payload builder
4. 逐步减少“正文解析工具调用”的重要性

DoD：

1. native tool call 成为首选通道
2. textual wrapper 成为 fallback
3. provider 差异不再散落在 kernel 主流程

## Phase 6

目标：旧 loop 退役

工作项：

1. `WorkflowRoleAdapter` 去 loop
2. `RoleAgent` 去 loop
3. 旧 standalone runner 去 loop
4. delivery host 全部只调 runtime facade

DoD：

1. 仓内不再存在第二套角色 tool loop
2. graph/node 只编排，不执行 loop

## Phase 7

目标：上层 graph / trace / eval / checkpoint

工作项：

1. `WorkflowGraph` 定型
2. `TraceSink` 一等化
3. checkpoint / resume
4. evals 从“最终答案”升级为“整条轨迹”

DoD：

1. 可恢复执行
2. 可审计停机原因
3. 可测工具选择、参数、停止条件、越权拦截

---

## 16. 验证蓝图

### 16.1 单元测试

必须覆盖：

1. provider transcript normalization
2. transcript append / compact
3. tool call -> tool result call_id 对齐
4. tool failure 回流 transcript
5. execution lane selection
6. budget / approval / sandbox 拦截

### 16.2 集成测试

必须覆盖：

1. CLI stream turn
2. non-stream turn
3. native tool call provider
4. textual fallback tool call
5. MCP-backed tool registry
6. approval-required turn
7. resume / replay / trace export

### 16.3 轨迹评估

必须新增的 eval 维度：

1. 是否选对工具
2. 参数是否正确
3. 是否过度调用
4. 停止原因是否正确
5. 是否出现重复循环
6. 是否越权

### 16.4 文档与治理门禁

当本蓝图进入实现阶段后，必须同步评估：

1. `docs/graph/catalog/cells.yaml`
2. 相关 `docs/graph/subgraphs/*.yaml`
3. `docs/governance/ci/fitness-rules.yaml`
4. KernelOne release gate
5. Context / Descriptor / Verify 资产是否受影响

---

## 17. 风险与注意事项

### 17.1 最大风险

最大风险不是“写不出 loop”，而是迁移过程中产生第二套真相：

1. 一套老 loop 继续跑
2. 一套新 `TurnEngine` 同时跑
3. host、workflow、agent runtime 分别偷偷保留自己的 continuation logic

这会直接导致：

1. 工具行为不一致
2. 停止条件不一致
3. trace 不一致
4. 权限治理不一致

### 17.2 控制策略

1. 一旦 `TurnEngine` 成形，旧 loop 必须尽快降级为 facade
2. 每个阶段都要有“禁止回退到第二套 loop”的测试
3. 不允许把 graph 提前做成 loop 替代品
4. 不允许把 MCP client 直接抬成 northbound transcript parser

---

## 18. 非目标

本蓝图当前明确不包含以下承诺：

1. 不承诺本轮就完成 graph runtime
2. 不承诺本轮就引入完整多 agent orchestration
3. 不承诺本轮就把所有 provider 全部迁完
4. 不承诺本轮就上真正的 code execution sandbox
5. 不把 blueprint 文档本身当作 graph truth

---

## 19. 下一步执行建议

如果按工程价值排序，下一步应严格按下面顺序实施：

1. 先抽 `TranscriptItem` + `ConversationState`
2. 再把 `run()` / `run_stream()` 共核成 `TurnEngine`
3. 再引入 `ToolRegistry`
4. 再统一 `ToolExecutionResult` 与 dual execution lane
5. 最后才做 graph/runtime/node 收口

不建议的顺序：

1. 先做 graph
2. 先把 MCP 到处接进来
3. 先把 host UI 做复杂
4. 先补更多 prompt patching

---

## 20. 最终裁决

这条线的最终目标不是：

`所有入口都接到 ToolLoopController 就结束`

而是：

`所有入口收敛到同一个 TurnEngine，再由 ToolRegistry + MCP + Programmatic Runtime + WorkflowGraph + Trace/Evals 包起来`

因此后续所有相关改动，都必须用下面这句作为总裁决：

**Typed transcript is the kernel. MCP is the tool bus. Direct and programmatic execution are the runtime. Graph is the orchestrator. Trace and eval are the production control plane.**
