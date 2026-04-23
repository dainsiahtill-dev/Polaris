# TRANSACTION_KERNEL_P0_RUNTIME_DISCIPLINE_BLUEPRINT_20260424

## 0. 文档元数据
- 日期: 2026-04-24
- 状态: Draft for Execution
- 范围: `roles.kernel` + `kernelone.process`（不新增业务域）
- 编码: UTF-8
- 目标: 在 Polaris 现有四层正交架构内落地 P0 运行时纪律能力包
  - Correlation ID
  - TruthLog（append-only）
  - Tool Failure Circuit Breaker（turn 内工具失败熔断）
  - Effect Enforcement Adapter（策略到执行的强制适配层）

> **工程注释**：本文档涉及"四层正交架构"等源自哲学层隐喻的概念。
> 所有隐喻均可在 [TERMINOLOGY.md](../TERMINOLOGY.md) 中找到对应的工程实体。
> 代码实现中使用的是工程实体名称，而非隐喻。

---

## 1. 背景与问题定义

Polaris 当前已具备 TransactionKernel、KernelGuard、Policy Facade、SessionArtifactStore 等关键机制，但 P0 运行时纪律存在四个结构性缺口：

1. 事件可追踪性缺口  
`TurnEvent` 以 `turn_id` 为主，但缺少统一 `turn_request_id` 关联主键，跨事件链路检索成本高。

2. 过程事实缺口  
已有 checkpoint/artifact 与部分 LLM JSONL 落盘，但缺少 turn 全生命周期统一 append-only 事实日志。

3. turn 内失败收敛缺口  
现有熔断重点在循环停滞/信息增益，缺少“同一 turn 内被拒/失败工具调用”的硬收敛机制。

4. 副作用强制执行缺口  
已有 SandboxPolicy 判定层，但执行层尚未统一“策略编译 -> 受限执行后端”适配链路。

---

## 2. 架构原则与边界

1. Graph First + Cell First：仅在既有 Cell 与 KernelOne 契约上扩展，不引入第二执行内核。  
2. Contract First：新增字段/事件必须进入公开契约，禁止隐式 side-channel。  
3. Single Commit Point：仍由 `TurnTransactionController` 维持唯一事务提交点。  
4. Fail-Closed：策略无法确认时默认拒绝执行。  
5. Reuse First：优先复用 `kernelone.process` 与既有 policy facade。

非目标（本蓝图不做）：
- 不重构为 Codex-RS 的单 Session SQ/EQ 大循环。
- 不引入 LLM-as-Judge Guardian 子会话。
- 不做三平台原生沙箱一次性全量落地。

---

## 3. 文本架构图（P0 目标态）

```text
[RoleSessionOrchestrator]
        |
        v
[TurnTransactionController] --(emit TurnEvent stream)----------------------+
        |                                                                  |
        | (A) Correlation Context                                           |
        +--> turn_request_id / span_id / parent_span_id                     |
        |                                                                  |
        | (B) Tool Path                                                     |
        +--> PolicyLayerFacade -> ToolFailureCircuitBreaker -> EffectEnforcementAdapter
                                      |                        |
                                      | allow                  | compile effect policy
                                      v                        v
                                [ToolBatchRuntime] -----> [KernelOne Process Runner]
                                      |
                                      v
                                Tool Results / Violations
                                      |
                                      v
                               [KernelGuard Invariants]
                                      |
                                      v
                              [TurnResult + CompletionEvent]
                                      |
                                      +--> [TruthLog Recorder (append-only JSONL)]
                                      +--> [Realtime/Delivery Projection]
```

---

## 4. 模块职责划分（按实施落点）

### 4.1 Correlation ID（roles.kernel + runtime bridge）
- 目标:
  - 为每个 turn 生成稳定 `turn_request_id`
  - 为每个事件附带 `turn_request_id` + `span_id` + `parent_span_id`
- 计划落点:
  - `polaris/cells/roles/kernel/public/turn_events.py`
  - `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
  - `polaris/cells/roles/runtime/internal/session_orchestrator.py`（透传）
- 约束:
  - 向后兼容：旧消费者不依赖新字段时不破坏
  - 不改语义，只增强可观测性

### 4.2 TruthLog Recorder（append-only）
- 目标:
  - 记录 turn 生命周期全事件事实，不覆盖、不回写
  - 支持离线 replay / 审计 / 根因分析
- 计划落点:
  - 新增 `polaris/cells/roles/kernel/internal/transaction/turn_truth_log_recorder.py`
  - 新增 `polaris/cells/roles/kernel/internal/transaction/turn_truth_log_schema.py`
  - 在 `TurnTransactionController.execute_stream()` 注入 recorder
- 数据要求:
  - 一行一事件 JSONL
  - 必含: `timestamp`, `turn_id`, `turn_request_id`, `event_type`, `payload`, `source`
  - 大字段截断与摘要策略明确化（防日志爆炸）

### 4.3 Tool Failure Circuit Breaker（turn 内）
- 目标:
  - 对同一 turn 内重复失败/拒绝调用快速熔断
  - 熔断后触发统一 cancel path，避免僵尸任务
- 计划落点:
  - 新增 `polaris/cells/roles/kernel/internal/transaction/tool_failure_circuit_breaker.py`
  - `tool_batch_runtime.py` 与 `tool_batch_executor.py` 在调用后上报失败信号
  - `kernel_guard.py` 接收并发布违规事件
- 策略:
  - key 维度: `tool_name + failure_class + effect_scope`
  - 默认阈值（可配置）:
    - 连续失败 >= 3 -> break turn
    - 累计失败 >= 10 -> break turn
    - destructive 类失败 >= 1 -> 直接 break + 需审批

### 4.4 Effect Enforcement Adapter（策略到执行）
- 目标:
  - 把 `effects_allowed` 从“判定”升级为“执行强制”
  - 形成统一执行后端入口（本地/受限/容器）
- 计划落点:
  - 新增 `polaris/cells/roles/kernel/internal/policy/effect_enforcement_adapter.py`
  - 复用 `polaris/cells/roles/kernel/internal/policy/layer/sandbox.py`
  - 复用 `polaris/kernelone/process/async_contracts.py` 的 runner 抽象
- 适配接口:
  - `compile(tool_call, policy_context) -> EnforcementPlan`
  - `transform(command, EnforcementPlan) -> ProcessSpawnSpec`
  - `execute(ProcessSpawnSpec) -> ToolResult`

---

## 5. 核心数据流（端到端）

1. Orchestrator 发起 turn，`TurnTransactionController` 生成 `turn_id + turn_request_id`。  
2. 每个 `TurnEvent` 自动携带 correlation lineage。  
3. 工具调用先过 `PolicyLayerFacade`；再进入 `ToolFailureCircuitBreaker.observe_pre_call()`。  
4. 对通过策略的调用，`EffectEnforcementAdapter` 将逻辑策略编译为执行计划并转换为受限执行规格。  
5. `ToolBatchRuntime` 执行工具并返回结果；失败事件回馈熔断器计数。  
6. 达到熔断阈值时，当前 turn 进入 break path，触发 cancel token 传播并产出结构化 violation event。  
7. `KernelGuard` 完成事务不变量检查，产出 `TurnResult/CompletionEvent`。  
8. 全链路事件同步写入 TruthLog JSONL（append-only）。  
9. Delivery/Projection 仅读取事实事件，不反向改写事实层。

---

## 6. 分阶段实施计划（含 4+ 专家委派）

## Phase P0-A（第 1 周）Correlation ID + Event Contract
- Owner A（Principal Runtime Contract）
- Owner B（Kernel Event Stream）
- 交付:
  - `TurnEvent` 新增 correlation 字段（兼容默认值）
  - `TurnTransactionController` 注入 `turn_request_id`
  - Session Orchestrator / delivery 透传
- 验收:
  - 关键事件 100% 带 `turn_request_id`

## Phase P0-B（第 1-2 周）TruthLog Recorder
- Owner C（ContextOS / TruthLog）
- Owner D（Storage & Replay）
- 交付:
  - append-only recorder + schema
  - `execute_stream()` 全事件落盘
  - 截断与安全字段策略
- 验收:
  - turn 级 replay 可还原事件顺序
  - 不覆盖、不可变、可按 request_id 检索

## Phase P0-C（第 2 周）Tool Failure Circuit Breaker + Cancellation 收束
- Owner E（Tool Runtime）
- Owner F（Kernel Guard & Continuation）
- 交付:
  - turn 内失败熔断器
  - 熔断触发统一 cancel path
  - 熔断事件进入 TruthLog 与 metrics
- 验收:
  - 同类失败达到阈值后 turn 及时中断
  - 无“表面结束但底层仍跑”任务泄漏

## Phase P0-D（第 2-3 周）Effect Enforcement Adapter
- Owner G（Policy Compiler）
- Owner H（KernelOne Process Backend）
- 交付:
  - enforcement adapter 接口
  - workspace read-only/workspace-write/full-access 三档执行策略
  - fail-closed 默认行为
- 验收:
  - 策略禁止调用在执行层被强制拦截
  - 受限后端可输出结构化拒绝证据

说明：
- 每个阶段均要求“最小可回滚”交付，不做大爆炸合并。
- 全程由 QA/治理官（Owner I）执行 Verification Card 更新与证据归档。

---

## 7. 回滚策略（Rollback）

1. 双开关策略（按能力包）  
- `KERNELONE_ENABLE_TURN_CORRELATION_ID`
- `KERNELONE_ENABLE_TRUTHLOG_P0`
- `KERNELONE_ENABLE_TOOL_FAILURE_BREAKER_P0`
- `KERNELONE_ENABLE_EFFECT_ENFORCEMENT_ADAPTER_P0`

2. 回滚原则  
- 先关新能力开关，再回退实现分支；禁止直接删历史证据。  
- TruthLog 只追加，不回删；回滚仅停止新增写入。  
- 若 enforcement adapter 失败，默认 fail-closed（拒绝危险执行），不回退为 fail-open。  

3. 回滚触发条件  
- 关键路径延迟劣化 > 30% 且连续 3 次回归失败  
- 工具执行错误率较基线上升 > 20%  
- 出现越权执行或不可审计副作用

---

## 8. 验证计划（最小门禁集）

以下为实现阶段最小门禁集（按 P0 变更文件范围收敛执行）：

1. Ruff
```bash
ruff check polaris/cells/roles/kernel/public/turn_events.py \
  polaris/cells/roles/kernel/internal/turn_transaction_controller.py \
  polaris/cells/roles/kernel/internal/transaction \
  polaris/cells/roles/kernel/internal/policy \
  polaris/kernelone/process --fix
ruff format polaris/cells/roles/kernel/public/turn_events.py \
  polaris/cells/roles/kernel/internal/turn_transaction_controller.py \
  polaris/cells/roles/kernel/internal/transaction \
  polaris/cells/roles/kernel/internal/policy \
  polaris/kernelone/process
```

2. Mypy
```bash
mypy polaris/cells/roles/kernel/public/turn_events.py \
  polaris/cells/roles/kernel/internal/turn_transaction_controller.py \
  polaris/cells/roles/kernel/internal/transaction \
  polaris/cells/roles/kernel/internal/policy \
  polaris/kernelone/process
```

3. Pytest（最小回归）
```bash
pytest -q \
  polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py \
  polaris/cells/roles/kernel/tests/test_phase_timeout_loop_fix.py \
  polaris/cells/roles/kernel/tests/test_modification_contract.py \
  polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py
```

4. 新增测试（目标）
- `test_turn_event_correlation_lineage.py`
- `test_turn_truthlog_recorder.py`
- `test_tool_failure_circuit_breaker.py`
- `test_effect_enforcement_adapter.py`

---

## 9. 与现有治理资产对齐

1. 对齐 `TransactionKernel` 唯一 commit point（ADR-0071）  
2. 对齐 `ContextOS` 目标态中的 `TruthLog append-only`  
3. 不引入第二套 handoff/schema 真相  
4. 保持四层正交架构，不倒退为单循环大内核

---

## 10. 交付物清单

1. 本蓝图文档（当前文件）  
2. 对应 Verification Card：  
`docs/governance/templates/verification-cards/vc-20260424-transaction-kernel-p0-runtime-discipline.yaml`

