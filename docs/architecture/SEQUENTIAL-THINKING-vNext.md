# Sequential-Thinking vNext 内核化方案

## 1. 背景与问题陈述（现有循环分散、冲突点、黑盒问题）

当前 Polaris 在角色执行链路中存在多处“回合推进”实现，形成了以下系统性问题：

1. 循环分散：
   - `RoleExecutionKernel`、`role_dialogue`、`workflow_adapter.execute_role_with_tools` 都可能推进回合，导致重复重试、预算失真与行为不可预测。
2. 与主状态机耦合风险：
   - workflow/taskboard 维护主状态字段（如 `phase/status/retry_count`），若 sequential 直接写这些字段，会造成主状态污染与回退冲突。
3. 观测黑盒：
   - 当前实时投影重点在 `llm` 事件，缺少 sequential 结构化轨迹（步骤、预算、终止原因），导致“为什么停”“为什么重试”不可解释。
4. 压测不可审计：
   - 无统一 `sequential_stats` 契约，run 结束后难以对“无进展退出”“预算耗尽退出”做自动归因统计。

因此，需要将 sequential-thinking 下沉为内核能力，并建立严格状态边界与观测契约，确保“可收敛、可解释、无状态冲突”。

## 2. 设计目标与非目标（提升收敛、可观测；不改 workflow 主状态机）

设计目标：

1. 单一回合驱动器：`RoleExecutionKernel` 成为唯一回合推进入口。
2. 子状态隔离：sequential 仅写 `metadata.seq.*`，绝不写 workflow/taskboard 主状态字段。
3. 统一预算与终止：采用固定预算模型和 fail-fast 规则，避免隐式无限循环。
4. 端到端可观测：新增 `seq.*` 事件并进入 WS/SSE 投影，observer 可直接查看 sequential trace。
5. 兼容现有编排：不改变 phase/status 主状态语义，不改变 taskboard 主迁移规则。

非目标：

1. 不引入内容代写兜底，不替 LLM 生成业务内容。
2. 不记录原始 CoT 文本，仅记录结构化摘要。
3. 不重写 workflow 主状态机逻辑，不改 TaskBoard 的核心迁移判定。
4. 不改变现有 UTF-8 文本读写规范与既有工具权限门禁。

### 2.1 接入范围约束（Director-first）

1. vNext 首阶段仅接入 Director 任务执行链路（prepare/validate/implement/verify）。
2. PM/QA 不运行 SequentialEngine，本阶段仅消费 `sequential_stats`、`failure_class`、`retry_hint` 与 `metadata.seq.*`。
3. 非 Director 角色默认 `sequential_mode=disabled`，仅允许显式白名单开启。
4. 任何角色扩展接入前，必须先通过“主状态零污染 + 幂等恢复 + 终止映射稳定性”三项门禁。

## 3. 架构总览图（文字版数据流：Kernel->SeqEngine->ToolGateway->Events->Projection）

文字版数据流：

1. `RoleExecutionKernel` 接收 `RoleTurnRequest`，根据 `sequential_mode` 决定是否启用 sequential。
2. Kernel 调用统一 `SequentialEngine`，驱动 step 决策与工具调用编排。
3. `SequentialEngine` 通过 `ToolGateway` 执行工具并返回结构化结果。
4. 每个 step 产生日志化 `seq.*` 事件（start/step/progress/no_progress/end/error）。
5. 事件进入 runtime event bus，经 WS/SSE 进入投影层。
6. Observer 在新增 `sequential_trace` 面板展示 step 序列与终止原因。
7. `status.merge_workflow_tasks` 仅附加只读 `seq` 展示字段，不参与主状态计算。

## 4. 状态边界与所有权矩阵（Workflow/TaskBoard/Sequential 字段所有权表）

| 字段/域 | 所有者 | 写权限 | 说明 |
|---|---|---|---|
| `phase` | Workflow | Workflow only | 主阶段流转，sequential 禁写 |
| `status` | TaskBoard/Workflow | TaskBoard/Workflow only | 主任务状态语义，sequential 禁写 |
| `retry_count` | Workflow | Workflow only | 主重试计数，sequential 禁写 |
| `max_retries` | Workflow | Workflow only | 主重试上限，sequential 禁写 |
| `completed_phases` | Workflow | Workflow only | 主完成阶段集合，sequential 禁写 |
| `workflow_state` | Workflow | Workflow only | 工作流运行态，sequential 禁写 |
| `metadata.seq.*` | SequentialEngine | Sequential only | sequential 子状态与统计信息 |
| `sequential_stats`（结果对象） | RoleExecutionKernel | Kernel 写入 | 每回合摘要输出，供外层消费 |

### 4.1 状态真相源层级（Source of Truth Hierarchy）

为避免双写导致的不一致，明确定义三级真相源：

| 层级 | 位置 | 性质 | 生命周期 |
|------|------|------|----------|
| **持久化真相源** | `metadata.seq.*` | 唯一可持久化状态 | 跨进程、可恢复 |
| **运行时投影** | `context.seq` | 内存缓存/投影 | 单次请求，可重建 |
| **历史审计** | `seq.*` 事件流 | 不可变事件日志 | 用于回放与归因 |

**关键约束**：
1. `context.seq` **只读或可重建**，不得与 `metadata.seq` 双写
2. 恢复流程必须从 `metadata.seq.*` 重建，不得信任 `context.seq` 残留
3. 展示层（Observer/WS）消费事件流，不直接读取内存状态

这防止了"展示一个值、恢复读另一个值"的问题。

### 4.2 保留字约束与统一写屏障（State Proxy）

1. **统一写屏障**：所有对 `metadata.seq.*` 的写入必须经过 `SequentialStateProxy` / Reducer，禁止直接修改。
2. **保留字检查**：
   - Dev/Test 环境：命中保留字段（phase/status/retry_count 等）时 **fail fast**，抛出 `ReservedKeyViolationError`
   - Prod 环境：拒绝写入 + 发出 `seq.reserved_key_violation` 事件 + 告警上报
3. **写入路径**：
   ```
   caller -> SequentialStateProxy.write(key, value)
          -> 保留字检查
          -> 通过: 写入 metadata.seq.*
          -> 拒绝: 抛出异常或记录 violation 事件
   ```
4. **禁止行为**：任何代码不得绕过 State Proxy 直接修改 `metadata.seq.*`，CI 静态检查拦截直接赋值模式。
5. **违规审计**：所有 violations 进入 `seq.reserved_key_violation` 事件流，包含调用栈与意图字段，用于事后追踪。

### 4.3 三层编排职责与禁止跨层写入

| 层级 | 主要职责 | 可写域 | 禁写域 |
|---|---|---|---|
| Workflow（外层流程编排） | 角色阶段流转、全局退出条件、回合控制 | workflow 主状态、phase、全局重试计数 | `metadata.seq.*` |
| TaskBoard（任务编排） | 任务依赖、ready/blocked 调度、任务主状态迁移 | taskboard 主任务字段 | `metadata.seq.*` |
| Sequential（单任务执行编排） | 单任务步骤推进、预算控制、进展检测、终止归因 | `metadata.seq.*`、`sequential_stats` | workflow/taskboard 主状态字段 |

强制约束：
1. `needs_continue` 定义为“编排语义”，不是底层 runtime 必须新增的主状态枚举。
2. Sequential 禁止直接写 `phase/status/retry_count`，只能通过 intent 提议，由外层 reducer 决策 commit。
3. 展示层可以渲染 `needs_continue` 视图态，但不得将其反向写入主状态机作为真相源。

## 5. 统一预算与终止条件（含默认值与 fail-fast 规则）

固定预算参数（`sequential_budget`）：

1. `max_steps`：默认 12
2. `max_tool_calls_total`：默认 24
3. `max_no_progress_steps`：默认 3
4. `max_wall_time_seconds`：默认 120

终止条件：

1. 达到 `max_steps`。
2. 达到 `max_tool_calls_total`。
3. 连续无进展达到 `max_no_progress_steps`。
4. 运行墙钟时间超过 `max_wall_time_seconds`。
5. 发生不可恢复错误（解析失败、权限拒绝、关键依赖缺失）。

fail-fast 规则：

1. 非法状态写入立即终止并打点。
2. 工具返回不可恢复错误时立即终止，不做内容代写兜底。
3. 预算耗尽必须返回可解释 termination reason，不得伪造”成功完成”。

### 5.1 终止原因到外层动作的映射表

定义 `termination_reason` 到 `failure_class` + `retry_hint` 的映射，供外层 Reducer 稳定消费：

| termination_reason | failure_class | retry_hint | 外层 Reducer 动作 |
|-------------------|---------------|------------|------------------|
| `seq_completed` | `success` | `handoff` | 正常移交，进入下一阶段 |
| `seq_no_progress` | `retryable` | `stagnation` | 重试一次，若仍无进展则升级 |
| `seq_budget_exhausted` | `retryable` | `escalate` | 一次性重试或升级到人工 |
| `seq_tool_fail_recoverable_exhausted` | `retryable` | `cooldown_retry` | 冷却后重试 |
| `seq_output_invalid_exhausted` | `validation_fail` | `manual_review` | 标记验证失败，需人工介入 |
| `seq_reserved_key_violation` | `internal_bug` | `alert` | 立即告警，回滚到 disabled 模式 |
| `seq_crash_orphan` | `unknown` | `audit_recover` | 审计恢复流程 |

**关键设计**：
- `failure_class` 决定问题性质，用于路由
- `retry_hint` 指导外层决策，避免与 reason_code 文本耦合
- 外层 Reducer 根据这两个字段做出稳定决策，不解析 termination_reason 字符串

### 5.2 副作用重放防护与幂等恢复（Crash/Retry 安全）

**问题**：若某一步已执行 `write/edit/patch_apply`，但进程在 `seq.step_finished` 持久化前崩溃，恢复后按 step_index 重放会导致重复写文件、重复 patch。

**解决方案**：

1. **幂等标识体系**：
   ```python
   {
     "seq_session_id": "uuid",        # 本次 sequential 运行唯一ID
     "outer_attempt_id": "task/123",  # 外层重试 attempt 标识
     "tool_call_id": "call_abc",      # 单次工具调用唯一ID
     "operation_digest": "sha256(...)" # 操作内容指纹
   }
   ```

2. **执行状态机**：
   ```
   step_started -> tool_invoked -> tool_completed -> step_finishing -> step_finished
                                        ^
                                        | 崩溃点风险区
   ```

3. **"已执行未确认"判定**：
   - 每次工具调用前，检查 `metadata.seq.tool_outcomes[{tool_call_id}]` 是否存在
   - 若存在且 `digest` 匹配，直接返回缓存结果（**不重新执行**）
   - 若 `digest` 不匹配，视为新调用（参数变更）
   - 若工具调用已完成但 `step_finished` 未记录，进入**恢复模式**

4. **恢复模式（Recovery Mode）**：
   ```python
   def recover_step(seq_session_id, step_index):
       # 1. 读取 metadata.seq.steps[step_index]
       partial = metadata.seq.steps[step_index]

       if partial.status == "tool_invoked":
           # 工具已调用但结果未知——查询 tool_outcomes 缓存
           outcome = lookup_tool_outcome(partial.tool_call_id)
           if outcome:
               # 幂等命中，跳过执行，直接推进
               return StepResult.from_cached(outcome)
           else:
               # 真正需要重放——但使用相同 tool_call_id 确保幂等
               return reinvoke_with_same_id(partial.tool_call_id)

       elif partial.status == "tool_completed":
           # 工具完成但 step 未收尾——继续后续处理
           return continue_step_completion(partial)
   ```

5. **工具层面的幂等支持**：
   - `write_file`: 使用 `operation_digest` 作为文件元数据标记，重复写入时检测 digest 是否已存在
   - `edit_file`: 基于文件当前内容与目标内容的 diff 指纹，相同 edit 不重复应用
   - `patch_apply`: patch hash 去重

6. **会话隔离**：
   - `seq_session_id` 每次进入 sequential 时生成，关联到 `RoleTurnRequest`
   - 崩溃恢复时，新进程使用相同 `seq_session_id` 重建状态
   - 防止"跨会话污染"

**验收标准**：
- 模拟崩溃在任意 step 阶段，恢复后不重放已成功的工具调用
- 重复提交相同 `tool_call_id` 返回相同结果，不触发实际副作用
- 工具幂等性验证列为单元测试必须项

## 6. 顺序步骤合同（`step_decision`/`sequential_stats` 字段定义）

`step_decision`（单步决策）字段建议：

1. `step_index`: int
2. `intent`: str（本步目标）
3. `planned_actions`: list[str]
4. `tool_plan`: list[object]（工具名 + 参数摘要）
5. `expected_progress_signal`: list[str]
6. `risk_flags`: list[str]

### 6.1 Intent 层与状态边界闭合（Sequential 只提议，外层 Commit）

**核心原则**：Sequential 只负责"提议"，主状态机（TaskBoard/Workflow）负责"落账"。

**Sequential 可产出的意图（Intents）**：

| intent_type | 内容 | 消费方 |
|-------------|------|--------|
| `proposed_task_update` | 建议更新 task 字段（priority/assignee 等） | TaskBoard Reducer |
| `proposed_phase_handoff` | 建议推进到下一 phase | Workflow Reducer |
| `evidence_append` | 追加证据/上下文到 task.metadata | TaskBoard |
| `retry_hint` | 建议重试策略 | RoleExecutionKernel |
| `blocker_record` | 记录阻塞原因 | TaskBoard |

**边界闭合机制**：

1. Sequential 输出 `StepIntents[]`，不直接修改 Task/Workflow 状态
2. `RoleExecutionKernel` 收集 intents，提交给外层 Reducer
3. 外层 Reducer 根据当前主状态决定是否接受/拒绝/转换意图
4. 拒绝的意图进入 `seq.intent_rejected` 事件流，用于调试

**示例流程**：
```python
# Sequential 内部
if progress_detected:
    return StepResult(
        intents=[
            ProposedTaskUpdate(priority="high"),
            EvidenceAppend(key="validation_passed", value=True)
        ]
    )

# Kernel 层
intents = sequential_result.intents
for intent in intents:
    if isinstance(intent, ProposedPhaseHandoff):
        # 提交给 Workflow Reducer
        workflow_reducer.submit_intent(intent)
    elif isinstance(intent, ProposedTaskUpdate):
        # 提交给 TaskBoard
        taskboard_reducer.submit_intent(intent)
```

这确保了：
- Sequential 不越界修改主状态
- 主状态机保持最终决策权
- 意图被拒绝时可审计、可调试

`RoleTurnRequest` 新增字段：

1. `sequential_mode`: `disabled|enabled|required`
2. `sequential_budget`: object（预算配置）
3. `sequential_trace_level`: `off|summary|detailed`

`RoleTurnResult` 新增字段：

1. `sequential_stats.steps`: int
2. `sequential_stats.tool_calls`: int
3. `sequential_stats.no_progress`: int
4. `sequential_stats.termination_reason`: str
5. `sequential_stats.budget_exhausted`: bool

进展判定标准（任一成立即 progress）：

**Type-A: Artifact 推进**（适合 implement 型角色）
1. 有写入变更（文件新增/修改/删除之一）。
2. changed_files 计数增加。

**Type-B: Validation 改善**（适合 qa/chief_engineer 角色）
3. 错误类型收敛（从广泛错误收敛到具体可修复错误）。
4. 阶段性验收前进（测试通过数提升、关键门禁由 fail->pass）。

**Type-C: Blocker 明确化**（适合 director/pm 角色）
5. 阻塞原因被首次明确记录（blocker 从模糊到具体）。
6. 任务拆解产生新的有效子任务（非重复）。

**Type-D: 信息增量**（适合 director 预分析阶段）
7. 关键证据/依赖关系被首次发现（非重复读取）。
8. 验证状态从 unknown -> identified。

**v1 保守策略**：
- 默认启用 Type-A + Type-B + Type-C
- Type-D 作为可选开关（`progress_info_incremental=true`）
- `max_no_progress_steps=3`（若启用 Type-D 可保持，否则建议提升到 3）

**注意**：Sequential 只能"检测"进展，不能"宣称"进展——进展判定需通过 `SeqProgressDetector` 产出 `proposed_progress_event`，由外层 Reducer 确认后写入。

## 7. 事件契约与投影契约（`seq.*` 事件字段表，WS/SSE 消费说明）

事件类型：

1. `seq.start`
2. `seq.step`
3. `seq.progress`
4. `seq.no_progress`
5. `seq.termination`
6. `seq.reserved_key_violation`
7. `seq.error`

通用字段：

1. `run_id`
2. `role`
3. `task_id`
4. `timestamp`
5. `step_index`
6. `payload`（按事件类型扩展）

WS/SSE 投影契约：

1. 复用现有 runtime WS/SSE 链路，不新增独立传输协议。
2. `projection-focus` 扩展为 `llm|seq|all`，默认 `all`。
3. `observer` 新增 `sequential_trace` 面板：
   - 步骤流（step timeline）
   - 预算消耗（steps/tool_calls/time）
   - 终止原因（termination_reason）
   - 违规告警（reserved_key_violation）

## 8. 与现有编排兼容策略（无 phase 污染、无双循环、无状态回退）

兼容策略：

1. 关闭外层二次循环：
   - `role_dialogue` 与 `workflow_adapter.execute_role_with_tools` 改为透传内核结果，不再自行 while-loop。
2. 主状态零污染：
   - sequential 只写 `metadata.seq.*`，并通过保留字检查强制隔离。
3. 无状态回退：
   - taskboard/workflow 主状态迁移规则保持原样，sequential 不得逆向改写。
4. Runtime 聚合保持稳定：
   - `status.merge_workflow_tasks` 主映射逻辑不变，仅追加可选 `seq` 展示字段。

### 8.1 与 Workflow/TaskBoard 无冲突接入契约

1. Sequential 仅输出：
   - `metadata.seq.*`
   - `sequential_stats`
   - `termination_reason/failure_class/retry_hint`
   - 可选 `proposed_* intents`
2. 外层 reducer 负责将上述输出映射为主状态动作；Sequential 不直接落账主状态。
3. 若底层状态机暂不支持 `needs_continue`，采用兼容表达：
   - 主状态维持既有枚举（如 completed/in_progress/failed/blocked）
   - 通过 `continue_required=true`（位于 metadata/summary）表达“需继续”
   - PM 层解释为 `needs_continue`，并且不计入 `consecutive_blocked/failures`
4. 阻塞策略（skip/manual_wait/degrade_retry/smart）在 PM 策略层执行，不下沉到 Sequential 内核。
5. 任何“把 Sequential 结果直接写成 Workflow 终态”的实现均视为违例（会造成状态污染与误停机）。

## 9. 配置项与默认值（env + settings）

建议配置项：

1. `HP_SEQ_ENABLED`（default: `true`）
2. `HP_SEQ_DEFAULT_MODE`（default: `enabled`）
3. `HP_SEQ_DEFAULT_ROLES`（default: `director,adaptive`）
4. `HP_SEQ_MAX_STEPS`（default: `12`）
5. `HP_SEQ_MAX_TOOL_CALLS_TOTAL`（default: `24`）
6. `HP_SEQ_MAX_NO_PROGRESS_STEPS`（default: `3`）
7. `HP_SEQ_MAX_WALL_TIME_SECONDS`（default: `120`）
8. `HP_SEQ_TRACE_LEVEL`（default: `summary`）
9. `HP_PROJECTION_FOCUS`（default: `all`，支持 `llm|seq|all`）
10. `HP_SEQ_MAX_SAME_ERROR_FINGERPRINT`（default: `2`）- 同一错误指纹最多容忍次数
11. `HP_SEQ_PROGRESS_INFO_INCREMENTAL`（default: `false`）- 是否将信息增量视为进展
12. `HP_SEQ_IDEMPOTENCY_CHECK`（default: `true`）- 是否启用工具幂等检查

角色启用默认策略：

1. vNext 默认启用：`director + adaptive`
2. 其他角色默认：`disabled`
3. 可通过 settings/role profile 显式开启扩展

## 10. 实施步骤（代码改造顺序与回滚点）

M1（内核收敛）：

1. `RoleExecutionKernel` 接入统一 `SequentialEngine`。
2. 为 `RoleTurnRequest/Result` 增加 sequential 字段。
3. 回滚点：保留 `sequential_mode=disabled` 可全局关闭。

**回滚策略补强**：
- `disabled` 模式必须是**语义等价的 passthrough**，而非"仍走新内核但少几个判断"
- 回滚后外部观察到的行为应与旧路径一致
- 建议增加 `observe/shadow` 模式做一轮对照（可选，非 v1 必需）

M2（状态边界）：

1. 落地 `metadata.seq.*` 写入路径。
2. 实施保留字约束与 `seq.reserved_key_violation` 事件。
3. 回滚点：仅关闭写入拦截日志上报，不动主执行路径。

M3（观测投影）：

1. 打通 `seq.*` 事件到 WS/SSE。
2. observer 增加 `sequential_trace` 面板与 focus 过滤。
3. 回滚点：focus 回退为 `llm|all`，保留后端事件兼容。

M4（测试回归）：

1. 单测：状态机流转、预算终止、无进展终止、保留字拦截。
2. 集成：无双循环、主状态不污染、投影展示正确。
3. 压测：`probe-only -> smoke(1轮) -> 标准轮次`。

M5（文档发布）：

1. 发布架构文档与 ADR。
2. 关联测试证据与压测证据路径。

## 11. 验收标准（功能、稳定性、观测、压测）

功能验收：

1. 回合驱动仅存在内核一处推进。
2. `RoleTurnRequest/Result` 新字段向后兼容。
3. sequential 预算/终止可解释且可复现。

稳定性验收：

1. 不出现 phase/status/retry_count 污染。
2. 无连续死循环，预算触发后稳定退出。
3. 失败可定位到具体 step 与工具调用。

观测验收：

1. `seq.*` 事件在 runtime WS/SSE 可见。
2. observer `sequential_trace` 面板可展示关键数据。
3. `projection-focus=seq` 可过滤出 sequential 事件流。

压测验收：

1. 主链可达 QA，且 `phase` 主状态无污染。
2. `seq_budget_exhausted` 出现时具备解释字段与证据。
3. 通过率不低于启用前基线，失败可审计归因。

可靠性验收（新增）：

1. **幂等恢复**：模拟在任意 step 阶段崩溃，恢复后不重复执行已成功的工具调用。
2. **意图边界**：Sequential 产出的 `proposed_*` intents 不直接修改 Task/Workflow 主状态，需经 Reducer 确认。
3. **单一真相源**：`metadata.seq.*` 与 `context.seq` 双写冲突测试通过，恢复流程正确。
4. **终止映射**：所有 termination_reason 都能正确映射到 failure_class + retry_hint，外层 Reducer 不解析字符串。
5. **分层一致性**：Workflow/TaskBoard/Sequential 写域检查通过，未发生跨层直接写入。
6. **语义一致性**：`seq_budget_exhausted + progress_delta>0` 被外层稳定解释为 `needs_continue`，且不触发 stop_on_failure 误停。
7. **无冲突投影**：UI 可见 `needs_continue`，但底层主状态机未被新增语义污染（兼容模式通过）。

## 12. 风险与缓解（token 增长、循环风险、事件噪声）

风险 1：token 增长

1. 原因：step 摘要与事件增加上下文负担。
2. 缓解：默认 `trace_level=summary`，仅保留结构化摘要。

风险 2：循环风险

1. 原因：多步推理可能进入“工具调用但无有效推进”。
2. 缓解：`max_no_progress_steps` + `max_wall_time_seconds` 双保险。

风险 3：事件噪声

1. 原因：`seq.step` 高频上报导致投影噪声。
2. 缓解：focus 过滤 + 面板聚合 + 采样折叠。

风险 4：状态边界被误写

1. 原因：历史代码可能沿用旧字段写入习惯。
2. 缓解：保留字拦截 + `seq.reserved_key_violation` 强审计 + CI 规则校验。

风险 5：与现有流程冲突误判

1. 原因：外层仍残留循环逻辑。
2. 缓解：双重循环回归测试列为必须项，未通过禁止发布。

风险 6：Crash 后副作用重放（幂等失效）

1. 原因：工具调用已执行但状态未持久化，恢复时重复执行。
2. 缓解：
   - `seq_session_id` + `tool_call_id` 幂等键体系
   - 工具层面实现 digest 去重
   - 恢复模式自动检测"已执行未确认"状态
   - 单元测试覆盖各崩溃点的恢复行为

风险 7：同一错误反复撞墙

1. 原因：LLM 陷入错误循环，反复尝试相同无效修复。
2. 缓解：`max_same_error_fingerprint=2`，相同错误指纹超过阈值立即终止并上报。

