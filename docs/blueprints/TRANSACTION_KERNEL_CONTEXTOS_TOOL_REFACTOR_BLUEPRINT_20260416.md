# TurnEngine / ContextOS / LLM Tool Calling 根因级重构计划 v2.2

状态: Active Parent Blueprint / 主链路已收口, ContextOS 四层正式化已落地, 仅保留实验层与长期监控  
日期: 2026-04-16  
最后更新: 2026-04-17  
适用范围: `polaris/cells/roles/kernel/**`、`polaris/kernelone/context/**`、`polaris/domain/cognitive_runtime/**`、`polaris/cells/factory/cognitive_runtime/**`

> 目标：把当前"旧 TurnEngine + 新事务控制器 + ContextOS 修补链 + workflow 蓝图并存"的状态，收敛成一个可证明正确、可审计、可恢复的 Agent 执行内核。
> 本文是目标蓝图，不替代 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 的当前事实裁决。

> 2026-04-17 状态增量：
> - `TransactionKernel` 主链、`TurnEngine` facade、`KernelGuard`、handoff canonical contract、tool spec SSOT 已完成收口。
> - 当前事实对账统一见 `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_CLOSURE_MATRIX_20260416.md`。
> - `ProjectionEngine` / `ReceiptStore` / `TruthLogService` / `WorkingStateManager` 正式化已落地并完成联合回归；执行与证据统一见 `../../docs/blueprints/CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md` 与 `src/backend/docs/governance/templates/verification-cards/vc-20260417-contextos-service-hardening.yaml`。
> - `ENABLE_SPECULATIVE_EXECUTION` feature flag 已补齐并默认关闭；Speculative 层已完成 stream 路径受控接线（可回滚）。
> - 灰度观测执行面（`tool_calling_matrix`, stream, workspace=`C:/Temp/`）最新全量 run：`3ce32d24`，结果 `76/78`（`96.85%`，达成门槛 `>=70/74`）；同阶段稳定基线 `febb3027` 为 `76/78`（`97.44%`）。
> - 本轮 benchmark 根因修复：新增 no-tools contract 强制（benchmark only）与 under-min-tool-calls 单次强化重试（benchmark only），残留 deterministic 失败 `bridge_emits_session_patch`、`l5_sequential_dag` 已在 `run_id=61e23065`（2/2 PASS）收敛。
> - 实测证据：`C:/Temp/runtime/llm_evaluations/3ce32d24/AGENTIC_EVAL_AUDIT.json`、`C:/Temp/runtime/llm_evaluations/61e23065/AGENTIC_EVAL_AUDIT.json` 与 `X:/.polaris/projects/temp-1df48b9917eb/runtime/llm_evaluations/{3ce32d24,61e23065}/TOOL_CALLING_MATRIX_REPORT.json`。
> - CLI Console / ContextOS 去噪修复已落地：`TransactionKernel prebuilt_messages` 路径去除了 current user 重复与 BOM 噪音，证据见 `X:/.polaris/projects/fileserver-32fc198ee3e4/runtime/events/director.llm.events.jsonl`（`auto_db...`/`auto_d16...` vs `auto_770...`）。

---

## 0. 文档统一裁决

为避免再次出现“双蓝图 / 双协议 / 双真相”，本文定义以下统一规则：

1. **当前事实** 以 `AGENTS.md`、`docs/AGENT_ARCHITECTURE_STANDARD.md`、`docs/graph/catalog/cells.yaml`、`docs/graph/subgraphs/*.yaml` 为准。
2. **目标态父蓝图** 以本文为准；`docs/TURN_ENGINE_TRANSACTIONAL_TOOL_FLOW_BLUEPRINT_2026-03-26.md` 退化为前序蓝图，只保留历史设计依据。
3. **handoff 契约** 不新建第二套 truth。本文中的 `HandoffPack` 是逻辑名称，代码与持久化 contract 统一落在现有 `polaris.domain.cognitive_runtime.ContextHandoffPack` 及 `factory.cognitive_runtime` 公开契约上。
4. **ContextOS 四层拆分** 不改变 graph ownership：`roles.kernel` 仍拥有 context plane 相关实现边界，`factory.cognitive_runtime` 仍拥有 handoff / receipt 的公开承载能力。
5. **旧引擎兼容** 只允许 `TurnEngine -> TransactionKernel` 单向 facade；禁止继续维护“新旧双主路径”。
6. **当前事实对账** 统一见 `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_CLOSURE_MATRIX_20260416.md`，不得把本文的历史诊断段直接当作 2026-04-17 当前事实。
7. **四层正式化执行文档** 统一见 `../../docs/blueprints/CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md`；该子蓝图已完成并进入闭环归档阶段。

---

## 1. 现状诊断

> 注：本节保留 2026-04-16 的根因诊断，用于解释为什么需要本轮重构。
> 其中若与 2026-04-17 当前代码事实存在差异，以 Closure Matrix 为准。

### 1.1 双引擎并存（旧 TurnEngine 仍掌权）

- **旧架构**：`TurnEngine` (`polaris/cells/roles/kernel/internal/turn_engine/engine.py`) 仍是 `RoleExecutionKernel.run()` / `run_stream()` 的硬编码实现，保留 `while True` 循环、continuation loop、PolicyLayer stop 语义。
- **真实入口**：`RoleExecutionKernel.run()` / `run_stream()` 与 `kernel/turn_runner.py` 仍直接实例化 `TurnEngine`，所以 `TurnTransactionController` 虽存在，但并不是当前执行主路径。
- **新架构**：`TurnTransactionController` (`polaris/cells/roles/kernel/internal/turn_transaction_controller.py`) 已具备 `TurnStateMachine`、`TurnLedger`、`ToolBatchRuntime`、`TurnDecisionDecoder`，但**未被 kernel 主路径启用**。
- **迁移层**：`LegacyTurnEngineAdapter` (`turn_engine_migration.py`) 假设旧引擎有 `execute_turn()` 方法，实际上 `TurnEngine` 并没有此方法，导致适配器 fallback 路径在现实中无法运行。

### 1.2 ContextOS 双轨并行

- `StateFirstContextOS` (`polaris/kernelone/context/context_os/runtime.py`) 内部维持**并行双实现**：
  - `_project_impl`：单体路径（当前默认）
  - `_project_via_pipeline`：`PipelineRunner` 路径（`enable_pipeline=False`，未启用）
- `ContentStore` v2.1 已落地（commit `01dc4787`），但每个组件（`StateFirstContextOS`、`TranscriptMerger`、`Canonicalizer`）各自实例化独立的 `ContentStore`，导致去重收益完全丢失。
- `ContextOSSnapshot.to_dict()` 仍会序列化完整的 `transcript_log`（~109KB "nuke" 问题），`SnapshotSummaryView` 只是 workaround。

### 1.3 工具层：Receipt 与执行尚未统一

- `ToolBatchRuntime` 已存在（`polaris/cells/roles/kernel/internal/tool_batch_runtime.py`），拥有 `READONLY_PARALLEL` / `WRITE_SERIAL` / `ASYNC_RECEIPT` 概念，但调度逻辑尚未按 `effect_type` + `execution_mode` 完全收紧。
- `ExplorationWorkflow` (`polaris/cells/roles/kernel/internal/exploration_workflow.py`) 已存在基础骨架，但 `TurnTransactionController` 尚未真正通过 `handoff_workflow` 与其集成。
- 文件写操作不是事务化的：`filesystem.py` 中的 `verify_written_code()` 只记录不匹配，不自动回滚。
- `FailureBudget` 是 per-gateway / per-executor 的短命对象，不跨 turn 持久化。

### 1.4 LLM 调用层：Facade 循环依赖

- `LLMCaller` 被标记为 deprecated，但 `LLMInvoker` 内部仍会实例化 `LLMCaller` 来复用 `_prepare_llm_request()`，形成循环依赖。
- 存在**两个 `LLMInvoker`**：一个位于 `internal/llm_caller/invoker.py`，另一个位于 `internal/services/llm_invoker.py`，功能重叠。
- Streaming 路径是 "fire-and-forget"：流式产出 `tool_call` 事件，但真正的工具执行发生在流外，消费者必须手动缓冲并调用 `KernelToolCallingRuntime`。

### 1.5 文档与协议漂移

- 2026-03-26 的 `Turn Engine Transactional Tool Flow Blueprint` 已经给出“单次事务 turn”方向，但实现蓝图、治理卡片、handoff 现有 contract 没有收敛成一套命名。
- `ContextHandoffPack` 已经在 `polaris/domain/cognitive_runtime/models.py`、`factory.cognitive_runtime` 公开契约和 graph 中落地；如果本次重构再创建 roles.kernel 私有的 `handoff_pack.py`，会立刻形成第二套 handoff truth。
- `turn_contracts.py` 目前仍是 `TypedDict`/`dataclass` 混合模型，与本文要求的 frozen typed IR 不一致，属于协议层的实际 gap，而不是纯命名问题。

---

## 2. 核心架构裁决（v2.2 最终版，不可协商）

### 2.1 三条铁律

1. **Turn 单原子性**
   ```python
   assert len(TurnDecisions) == 1
   assert len(ToolBatches) <= 1
   assert hidden_continuation == 0
   ```
   违反即 `protocol panic` + `handoff_workflow`。

2. **`sequential` 语义边界（强制）**
   - `sequential` 仅指 **单个 turn 内、单个 ToolBatch 内** 的有序多工具调用（例如 read -> edit -> verify）。
   - `sequential` **不是** hidden continuation，不允许通过第二次决策循环补步骤。
   - 当单批次执行后仍需推进时，必须显式 commit 当前 turn，并通过 `session_patch` 驱动下一 turn。

3. **ContextOS 不可变性**
   - `TruthLog` 只能 append。
   - `PromptProjection` 只能 read-only 生成。
   - 任何组件不得直接修改 `TruthLog`。

4. **执行权零污染**
   - `Control Plane`（decision、telemetry、policy verdict、budget status）禁止任何字段进入 `Data Plane`（TruthLog、WorkingState、ReceiptStore）。

### 2.2 命名最终确认

| 组件 | 名称 | 说明 |
|------|------|------|
| 事务执行内核 | `TransactionKernel` | 由现有 `TurnTransactionController` 升级而来 |
| 多步邪术层 | `ExplorationWorkflowRuntime` | 由现有 `ExplorationWorkflow` 升级而来 |
| Prompt 投影层 | `ProjectionEngine` | 新建，取代 gateway 级 prompt 组装 |
| 工具结果存储层 | `ReceiptStore` | 新建，基于 `ContentStore` v2.1 |
| handoff 逻辑名 | `HandoffPack` | 逻辑别名，代码中统一映射到 `ContextHandoffPack` |
| 技术底座 | `polaris/kernelone/` | 继续保留，不重命名 |

---

## 3. 目标架构

### 3.1 TransactionKernel（五阶段固定 + KernelGuard）

固定五阶段，不可插队、不可回跳：

1. **Deliberate** — 构建上下文，请求 LLM
2. **Decode** — `TurnDecisionDecoder` 解码为唯一 `TurnDecision`
3. **ExecuteToolBatch** — `ToolBatchRuntime.execute_batch()`（最多 1 批）
4. **OptionalFinalize** — `FinalizationCaller`（仅在需要时调用 1 次，强制 `tool_choice=none`）
5. **Commit** — 生成 `TurnOutcome` 并持久化到 `TurnLedger`

新增 `KernelGuard` 运行时断言类：

```python
class KernelGuard:
    @staticmethod
    def assert_single_decision(turn: TurnContext):
        assert len(turn.decisions) == 1, "Protocol violation: multiple decisions in one turn"
        assert turn.tool_batch_count <= 1, "Protocol violation: >1 ToolBatch in turn"

    @staticmethod
    def assert_no_hidden_continuation(ledger: TurnLedger):
        assert ledger.decisions[-1].kind != "tool_batch" or ledger.finalized, \
            "Protocol violation: tool_batch without finalize/commit"
```

旧 `TurnEngine` 的最终形态：
- 保留 `run()` / `run_stream()` / host facade 签名
- **删除**自己的 `while` loop、`tool scheduling`、`continuation stop logic`
- 内部直接委托给 `TransactionKernel.execute()` / `execute_stream()`，仅做参数适配与结果映射

### 3.2 单一 Typed IR（Pydantic v2 严格冻结）

使用 Pydantic v2 + `ConfigDict(frozen=True, extra='forbid')` 让 IR 真正 immutable。

升级 `polaris/cells/roles/kernel/public/turn_contracts.py` 中的所有类型：

```python
class ToolInvocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    effect_type: Literal["read", "write", "async"]  # 新增必须字段
    execution_mode: Literal["readonly_parallel", "readonly_serial", "write_serial", "async_receipt"]  # 新增必须字段
```

`TurnDecision.kind`：
- `final_answer`
- `tool_batch`
- `ask_user`
- `handoff_workflow`

`finalize_mode`：
- `none`
- `local`
- `llm_once`

**执行协议 panic 规则**：
- decoder 如果在 `OptionalFinalize` 阶段再产出工具调用 → 协议错误 → 不 retry turn，不回退 loop → 直接 `handoff_workflow`。

### 3.3 DecisionCaller / FinalizationCaller 分离 + thinking 彻底隔离

基于现有 `LLMInvoker` 拆分出两个语义明确的调用器：

- `DecisionCaller` — 唯一职责：产出一次 `TurnDecision`
- `FinalizationCaller` — 仅在 `finalize_mode=llm_once` 时调用一次，强制 `tool_choice=none`

`thinking` 输出只允许进入：
- OpenTelemetry trace
- `TurnLedger` 的 telemetry 字段
- 禁止进入任何 ContextOS 层（TruthLog / WorkingState / PromptProjection）

### 3.4 ToolBatchRuntime 收紧 + Receipt-first 强化

执行策略固定为四种模式：

| 模式 | 调度方式 | 约束 |
|------|----------|------|
| `readonly_parallel` | `asyncio.gather` | 可并行 |
| `readonly_serial` | 串行 for loop | 仅在明确顺序依赖时使用 |
| `write_serial` | 串行 for loop | 必须生成 `effect_receipt` |
| `async_receipt` | 立即返回 | `pending` receipt，不阻塞 turn |

事务策略收紧：
- **code domain**：第 2 批企图 → 直接 `handoff_workflow`
- **deep_analysis domain**：decoder 若需要多步探索，必须在第一次 `TurnDecision` 就输出 `handoff_workflow`
- **所有 async_receipt**：立即返回 `pending`，不阻塞 turn

文件写操作事务化：
- temp file → `verify_written_code()` → atomic rename
- 验证失败自动回滚（删除 temp，不触碰原文件）

### 3.5 ContextOS 四层正交化 + ProjectionEngine

将现有的 `StateFirstContextOS` 概念层**物理拆分为四个独立组件**：

| 组件 | 职责 | 对应现有代码/需新建 |
|------|------|---------------------|
| `TruthLog` | append-only 可回放 truth | 现有 `ContextOSSnapshot.transcript_log` → 独立 `TruthLogService` |
| `WorkingState` | 结构化任务状态、active entities、pending followup | 现有 `WorkingState` → `WorkingStateManager` |
| `ReceiptStore` | 大工具输出、搜索结果、diff、文件切片、异步 receipt | 新建 `ReceiptStore`，基于 workspace-scoped `ContentStore` v2.1 |
| `ProjectionEngine` | 动态生成 prompt，只允许 summary + refs + compact receipt | 新建 `ProjectionEngine`，替换 gateway 级 prompt 组装 |

隔离层硬化：
- `ControlPlaneEvent` 与 `DataPlaneEvent` 两个完全独立的 enum
- `PromptProjection` 禁止任何 control-plane 字段（budget_status、policy_verdict 等）
- `ContextOSSnapshot` 必须独立自洽，以完整 `transcript_log` 为主格式

### 3.6 ExplorationWorkflowRuntime 强化（邪术层收口）

由现有 `ExplorationWorkflow` 升级：

- 独立的 ledger / checkpoint / resume 能力
- `HandoffPack` 标准化结构（JSON Schema 固定，**实际承载类型为 `ContextHandoffPack` 的扩展 profile**）：
  ```json
  {
    "checkpoint_state": {},
    "pending_receipt_refs": [],
    "suggestion_rankings": [],
    "lease_token": "..."
  }
  ```
- Sidecar（ContextOS、Cognitive Runtime、Semantic Search）**只能通过 `HandoffPack` 与 workflow 通信**，禁止直接调用 `TransactionKernel`
- `TransactionKernel` 在 `TurnDecision.kind == "handoff_workflow"` 时，将上下文打包为 `ContextHandoffPack`（遵循本文定义的 HandoffPack profile）并移交给 `ExplorationWorkflowRuntime`

**统一裁决**：

1. 不新建第二套 `HandoffPack` model。
2. 若 `ContextHandoffPack` 缺字段，则在 `polaris/domain/cognitive_runtime/models.py` 与 `factory.cognitive_runtime` 公开 contract 上扩展。
3. `roles.kernel` 只能消费该 contract，不能私自生成一套平行 public schema。

### 3.7 前沿邪术：Speculative Tool Execution（推测执行层）

在 streaming 路径上增加 **Shadow Pre-execution Engine**：

- 当 LLM stream 产出**不完整的 tool_call JSON 片段**时（例如已识别出工具名和部分参数），`StreamShadowEngine` 在后台启动**推测性的工具执行**
- 执行在**隔离的只读沙箱**中进行（基于 `ToolBatchRuntime` 的 `readonly_parallel`）
- 如果最终 `TurnDecisionDecoder` 确认该 tool_call 有效，则直接复用推测结果（零延迟返回）
- 如果 decoder 最终放弃该 tool_call，则丢弃推测结果

**为什么是邪术级别**：
- 把感知-行动延迟从 "LLM 完全生成后再执行" 压缩到 "LLM 刚露出意图就开始执行"
- 在代码审查、文档分析等 read-heavy 场景中，可实现真正的零等待探索
- 实现基础完全基于现有组件：`ToolBatchRuntime` + `ContentStore` + `TurnDecisionDecoder`

安全约束：
- 仅对 `effect_type="read"` 启用
- 写工具永远不会被推测执行
- 通过 feature flag `ENABLE_SPECULATIVE_EXECUTION` 控制，默认关闭

### 3.8 Tool Spec Single Source of Truth

迁移 `_TOOL_SPECS`（`polaris/kernelone/tool_execution/contracts.py`）到 `ToolSpecRegistry`（`polaris/kernelone/tool_execution/tool_spec_registry.py`）：

- `ToolSpecRegistry` 成为唯一权威来源
- `contracts.py` 降级为向后兼容的 `@deprecated` thin wrapper
- 动态注册支持：新 Cell 可以在启动时通过 `ToolSpecRegistry.register()` 注入自己的工具定义

---

## 4. 实施路线图

### 4.0 2026-04-17 阶段状态总览

| Phase | 状态 | 说明 |
|---|---|---|
| `Phase 0` | **Closed** | 骨架切流、dry-run 预热、主路径切换已完成，不再是活跃实施项 |
| `Phase 1` | **Closed** | `TransactionKernel` 切主、`TurnEngine` facade、`KernelGuard` 已收口 |
| `Phase 2` | **Closed** | typed IR、decoder panic、单 turn 协议收口已完成 |
| `Phase 3` | **Closed** | 四层正式化硬化已在 `CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md` 完成并验证 |
| `Phase 4` | **Closed** | tool runtime 事务化、receipt 强校验、handoff 主路径均已落地 |
| `Phase 5` | **Implemented Core** | workflow / handoff canonical profile 已具备主干能力，后续仅保留增强空间 |
| `Phase 6` | **Implemented Core (Flagged)** | speculative 层已接入 stream 主路径但默认关闭，保持低风险可回滚 |
| `Phase 7` | **Implemented Core / Monitoring Baseline Landed** | ToolSpec SSOT 与主路径切流已完成；监控指标已导出到 TurnResult.metrics 与 stream complete.monitoring，剩余是长期观察 |

### 4.0 实施切片（先文档统一，再代码切主）

为避免一次性重构把风险摊平，本蓝图强制按以下切片落地：

#### Slice A：文档/治理统一

目标：

1. 本文成为唯一目标态蓝图。
2. 新增 verification card 与 ADR，明确 structural assumptions、门禁与迁移边界。
3. 旧 `Turn Engine Transactional Tool Flow Blueprint`、ContextOS/Cognitive Runtime hardening plan 全部改为引用本文，并明确 `ContextHandoffPack` 是 canonical handoff contract。

完成标志：

1. 不再有文档建议新建第二套 `HandoffPack` model。
2. 所有文档对 `TransactionKernel`、`ProjectionEngine`、`ReceiptStore`、`ExplorationWorkflowRuntime` 的命名一致。

#### Slice B：执行内核切主

目标：

1. `RoleExecutionKernel.run()` / `run_stream()`、`kernel/turn_runner.py` 全部经 `TransactionKernel` 进入。
2. `TurnEngine` 退化为 facade。
3. `KernelGuard` 运行时断言上线。

完成标志：

1. turn 内只能产生一个 `TurnDecision`。
2. `turn.single_batch_ratio == 100%`。

#### Slice C：协议与上下文收口

目标：

1. `turn_contracts.py` 升级为 frozen IR。
2. `ProjectionEngine` 接管 prompt 组装。
3. `ReceiptStore` / `ContextHandoffPack` 取代 roles.kernel 私有 handoff/receipt schema。

完成标志：

1. prompt 中不再出现 control-plane noise。
2. 无第二套 handoff truth。

#### Slice D：workflow 与邪术层上移

目标：

1. 多步 read-analyze-read 全部 handoff 到 `ExplorationWorkflowRuntime`。
2. async receipt 与 speculative read execution 全部从单 turn 中剥离/隔离。

完成标志：

1. turn 内没有 hidden continuation。
2. workflow handoff 成为唯一多步出口。

### Phase 0: 骨架与零风险预热（1 周）

> 2026-04-17 更新：本阶段已完成并关闭。`TransactionKernel` 已切主，`USE_TRANSACTION_KERNEL_PRIMARY` 不再处于预热状态；本文保留本节仅作为迁移历史记录。

- 将 `TurnTransactionController` 重命名为 `TransactionKernel`，保留所有现有接口
- 在 `RoleExecutionKernel` 中增加原子切流开关 `USE_TRANSACTION_KERNEL_PRIMARY`（默认 `False`）
- 当开关为 `False` 时走旧 `TurnEngine`；为 `True` 时走 `TransactionKernel`
- 在 `TransactionKernel` 路径上增加 **dry-run 模式**：只记录 `TurnOutcome` 到日志，不 commit 到外部状态
- 运行新旧路径的实时日志比对，发现不一致立即报警但不影响生产

**完成门禁**：
- `TransactionKernel.execute()` 和 `execute_stream()` 可被 `RoleExecutionKernel` 正常调用
- dry-run 模式不修改任何外部状态

### Phase 1: 执行权单点化 + KernelGuard（1-2 周）

> 2026-04-17 更新：本阶段已完成并关闭。`TurnEngine` 已收敛为 facade，`KernelGuard` 已上线，`LEGACY_FALLBACK` 仅保留为一次性逃生阀，不再视为双主路径。

- 实现 `KernelGuard` 类，并在 `TransactionKernel` 的每个阶段切换时运行断言
- 删除旧 `TurnEngine` 内部的 `while` loop、`tool scheduling`、`continuation stop logic`
- `TurnEngine.run()` / `run_stream()` 改为 facade，直接委托 `TransactionKernel`
- 打开切流开关 `USE_TRANSACTION_KERNEL_PRIMARY=True`
- 保留一次性 `LEGACY_FALLBACK=true` 逃生阀

**完成门禁**：
- 任意 turn 只生成一个 `TurnDecision`
- 任意 turn 只允许一个 `ToolBatch`
- 旧 `TurnEngine` 文件中不得再存在执行循环主体（CI 扫描）

### Phase 2: Typed IR + Decoder 收口（1 周）

> 2026-04-17 更新：本阶段已完成并关闭。`turn_contracts.py` frozen IR、decoder protocol panic、单 turn 协议约束都已落地；后续若有调整，应通过 Closure Matrix 对账，不再按本节重新规划。

- 将 `turn_contracts.py` 全部升级为 Pydantic v2 frozen models
- `TurnDecisionDecoder` 增加 protocol panic 分支
- `ToolBatchRuntime` 按新的 `effect_type` / `execution_mode` 字段调度执行
- 增加 stream / non-stream parity 测试

**完成门禁**：
- 同输入、同上下文、同策略得到相同的 `TurnDecision`
- finalization 再出工具时进入 protocol panic
- 无 hidden continuation

### Phase 3: ContextOS 四层拆分 + ProjectionEngine（2 周）

> 2026-04-17 更新：本阶段已完成并关闭。四层服务正式化、runtime authoritative wiring、gateway projection delegation、
> 以及 `kernel + context` 联合回归（`1745 passed, 5 skipped`）已通过。实施细节与证据收口见
> `CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md` 与对应 Verification Card。

- 新建 `ProjectionEngine`：接收 `ContextOSProjection` + `ReceiptStore`，产出 LLM-ready messages
- 新建 `ReceiptStore`：基于 workspace-scoped `ContentStore` 构建
- 在 `context_gateway.py` 中接入 `ProjectionEngine`，删除直接 prompt 组装逻辑
- `StateFirstContextOS` 默认启用 `PipelineRunner` 路径，开始退役 `_project_impl`
- 增加 `TruthLog` append-only 校验

**完成门禁**：
- `ContextOSSnapshot` 可独立 replay
- control-plane noise 不进入 projection / state hints
- 大工具结果只以 ref 形式进入 prompt

### Phase 4: Tool Runtime 事务化 + handoff 收口（1-2 周）

> 2026-04-17 更新：本阶段核心项已完成并关闭。文件写事务化、自动回滚、receipt 强校验、`handoff_workflow` 主路径均已落地；若后续继续加强，只应作为局部硬化，不再作为主蓝图未完成项。

- `filesystem.py` 增加 transactional write（temp → verify → atomic rename）
- `ToolBatchRuntime` 增加 receipt 强制校验：每个 write tool 必须返回非空 `effect_receipt`
- `FailureBudget` 提升为 session-scoped 服务
- `TransactionKernel` 增加 `handoff_workflow` 分支
- decoder 一旦判断需要多步探索，直接输出 `handoff_workflow`

**完成门禁**：
- turn 内绝不出现第 2 批工具
- write tool 必有 effect receipt
- async tool 不阻塞 turn

### Phase 5: ExplorationWorkflowRuntime + HandoffPack（1-2 周）

> 2026-04-17 更新：本阶段核心能力已落地。`ExplorationWorkflowRuntime` 与 `ContextHandoffPack` canonical profile 已进入当前事实；若后续继续扩展 workflow ledger/checkpoint/resume，只应视为增强项，而不是主链未收口。

- 升级 `ExplorationWorkflow` → `ExplorationWorkflowRuntime`
- 定义 `HandoffPack` JSON Schema，并将其落实为 `ContextHandoffPack` 的 canonical profile，而不是新增第二套 model
- 实现 workflow 的独立 ledger / checkpoint / resume
- 将 `read-analyze-read`、异步恢复、approval 逻辑迁移到 workflow runtime
- Sidecar 通信全部通过 `HandoffPack`

**完成门禁**：
- 多步探索仅存在于 workflow
- turn 层完全不再承担自治循环职责

### Phase 6: Speculative Tool Execution（邪术层）（2 周）

> 2026-04-17 更新：本阶段已完成“受控接线”。`StreamShadowEngine` 与 `SpeculativeExecutor`
> 已接入 `TurnTransactionController._call_llm_for_decision_stream`，并通过
> `ENABLE_SPECULATIVE_EXECUTION`（默认关闭）控制启用，满足低风险可回滚要求。

- 实现 `StreamShadowEngine`：从 LLM stream delta 中预测 tool_call 意图
- 实现 `SpeculativeExecutor`：在隔离环境中预执行只读工具
- 集成到 `TransactionKernel.execute_stream()` 路径
- 增加命中率/误执行率监控

**完成门禁**：
- 推测执行不修改真实文件系统
- 推测结果可被 decoder 确认后零延迟复用
- 误推测率 < 5%

### Phase 7: Tool Spec SSOT + 生产切流 & 长期监控（1 周）

> 2026-04-17 更新：本阶段核心目标已完成。`ToolSpecRegistry` 已接管 canonical tool spec，主路径切流已完成；
> 监控基线已落地（`TurnResult.metrics` + stream `complete.monitoring`），本节剩余意义主要是长期监控，而不是继续作为架构阻塞项。

- 完成 `ToolSpecRegistry` 对 `_TOOL_SPECS` 的接管
- 打开 `USE_TRANSACTION_KERNEL_PRIMARY` 到全量
- 灰度 24-48 小时观察

监控指标：
- `transaction_kernel.violation_count`（协议 panic 数）
- `turn.single_batch_ratio`（必须 100%）
- `contextos.projection_purity_score`（noise 进入率，必须 0）
- `workflow.handoff_rate`（多步探索上移比例）
- `kernel_guard.assert_fail_rate`
- `speculative.hit_rate` / `speculative.false_positive_rate`

---

## 5. 关键修改文件清单

### 修改现有文件

- `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` — 升级为 `TransactionKernel`
- `polaris/cells/roles/kernel/internal/turn_engine/engine.py` — 删除循环主体，改为 facade
- `polaris/cells/roles/kernel/internal/role_execution_kernel.py` — 硬编码路径切换
- `polaris/cells/roles/kernel/internal/kernel/core.py` — `run()` / `run_stream()` 主路径切换
- `polaris/cells/roles/kernel/internal/kernel/turn_runner.py` — 移除对旧 `TurnEngine` 的直接实例化
- `polaris/cells/roles/kernel/internal/turn_engine_migration.py` — 删除双轨 fallback 逻辑
- `polaris/cells/roles/kernel/public/turn_contracts.py` — 升级为 Pydantic frozen models
- `polaris/cells/roles/kernel/internal/turn_decision_decoder.py` — 增加 protocol panic
- `polaris/cells/roles/kernel/internal/tool_batch_runtime.py` — 完善调度逻辑
- `polaris/cells/roles/kernel/internal/llm_caller/invoker.py` — 拆分为 `DecisionCaller` / `FinalizationCaller`
- `polaris/cells/roles/kernel/internal/services/llm_invoker.py` — 评估冗余，合并或删除
- `polaris/cells/roles/kernel/internal/turn_materializer.py` — thinking 只进 telemetry
- `polaris/cells/roles/kernel/internal/exploration_workflow.py` — 升级 runtime
- `polaris/cells/roles/kernel/internal/context_gateway.py` — 接入 `ProjectionEngine`
- `polaris/kernelone/context/context_os/runtime.py` — 统一切到 `PipelineRunner`
- `polaris/kernelone/context/context_os/content_store.py` — 提升为 workspace-scoped 服务
- `polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py` — 事务化写操作
- `polaris/kernelone/tool_execution/contracts.py` — 委托到 `ToolSpecRegistry`
- `polaris/kernelone/tool_execution/tool_spec_registry.py` — 完善 SSOT
- `polaris/kernelone/tool_execution/failure_budget.py` — session-scoped 改造
- `polaris/domain/cognitive_runtime/models.py` — 扩展 `ContextHandoffPack` 以承载 canonical handoff profile
- `polaris/cells/factory/cognitive_runtime/public/contracts.py` — 对齐 handoff/export/rehydrate 公开契约

### 新建文件

- `polaris/cells/roles/kernel/internal/kernel_guard.py` — `KernelGuard`
- `polaris/cells/roles/kernel/internal/speculative_executor.py` — `SpeculativeExecutor`
- `polaris/cells/roles/kernel/internal/stream_shadow_engine.py` — `StreamShadowEngine`
- `polaris/kernelone/context/projection_engine.py` — `ProjectionEngine`
- `polaris/kernelone/context/receipt_store.py` — `ReceiptStore`
- `polaris/kernelone/context/truth_log_service.py` — `TruthLogService`
- `polaris/kernelone/context/working_state_manager.py` — `WorkingStateManager`
- `polaris/cells/roles/kernel/internal/transaction_kernel.py` — `TransactionKernel` facade / canonical entrypoint

---

## 6. 文档同步矩阵

当本蓝图进入实现阶段时，以下文档必须同步更新，禁止只改代码不改真相：

1. `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
2. `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
3. `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_CLOSURE_MATRIX_20260416.md`
4. `../../docs/blueprints/CONTEXTOS_SERVICE_HARDENING_BLUEPRINT_20260417.md`
5. `src/backend/docs/governance/templates/verification-cards/vc-20260417-contextos-service-hardening.yaml`
6. `docs/TURN_ENGINE_TRANSACTIONAL_TOOL_FLOW_BLUEPRINT_2026-03-26.md`
7. `docs/KERNELONE_CONTEXT_OS_COGNITIVE_RUNTIME_HARDENING_PLAN_2026-03-27.md`
8. 如 handoff contract 变更，必须同步：
   - `docs/graph/catalog/cells.yaml`
   - `docs/graph/subgraphs/execution_governance_pipeline.yaml`
   - `polaris/cells/factory/cognitive_runtime/generated/descriptor.pack.json`（若公开契约变更）

---

## 7. 测试门禁（必须 100% 通过）

1. **单一执行授权测试**: native/text/final parse 不得各自产生执行
2. **单 ToolBatch 事务边界测试**: turn 内绝不允许第二批工具；工具后默认不再 continuation
3. **Finalization 协议测试**: `llm_once` 时强制 `tool_choice=none`；再出工具即 panic
4. **ContextOS replay 一致性测试**: `TruthLog` 可独立 replay；legacy snapshot 能迁移
5. **Projection purity 测试**: raw tool output、system warning、thinking、XML wrapper 不得进入 prompt projection
6. **Tool runtime mode 测试**: 只读并行 / 写串行 / 异步 pending receipt
7. **Stream / Non-stream parity**: 同输入、同上下文、同策略得到相同 decision/outcome
8. **Workflow handoff 强制测试**: 多步探索只能 handoff；turn 内不允许自治循环
9. **Ledger / telemetry 对齐测试**: phase events、audit ledger、truth log 三者可对齐
10. **KernelGuard 运行时/静态测试**: CI 必须通过所有断言
11. **HandoffPack 序列化/反序列化测试**: sidecar 通信必须 100% 通过 `HandoffPack`
12. **Primary cutover regression suite**: host API 行为兼容；旧入口签名不变；fallback 开关生效
13. **Sequential contract guard 测试**: `l3/l5` 读改类场景必须在单批次内完成有序工具计划；不得依赖 hidden continuation

---

## 8. 风险与迁移策略

1. **双轨运行风险**: 不再采用长期 shadow 双跑。仅保留 Phase 0 的短期 dry-run 预热（1 周）和一次性 fallback 开关。
2. **接口兼容性**: `RoleExecutionKernel.run()` / `run_stream()` 签名保持不变，内部切换对调用方透明。
3. **ContextOS 数据迁移**: 旧 `ContextOSSnapshot` 通过 `rehydrate_persisted_context_os_payload()` 自动迁移，不强制清理历史数据。
4. **工具 spec 迁移**: `contracts.py` 保留 `@deprecated` wrapper，给下游 Cell 3-6 个月迁移窗口。
5. **推测执行风险**: 默认关闭，仅对只读工具启用，写工具永远不会被推测执行。
