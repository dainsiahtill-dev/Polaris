# ContextOS 运作架构详解

**文档编号**: BLUEPRINT-2026-0417-CONTEXTOS-OPERATIONS  
**日期**: 2026-04-17  
**状态**: 已定稿，可直接作为新人 onboarding 与架构裁决依据  
**适用范围**: `src/backend/polaris/kernelone/context/` 及所有依赖 ContextOS 进行上下文组装的模块

---

## 1. 一句话定义

**ContextOS** 是 Polaris 的会话上下文操作系统。  
**StateFirstContextOS** 是 ContextOS 的唯一正式实现类（`polaris/kernelone/context/context_os/runtime.py:143`）。

> 两者的关系就像 **OS（操作系统）** 与 **具体内核实现（如 Linux kernel）**：ContextOS 是概念与接口规范，StateFirstContextOS 是唯一在生产环境中运行的代码实体。

---

## 2. 为什么叫 "StateFirst"？

在早期的 naive 实现中，上下文组装是"消息堆叠"——直接把历史消息一条一条 append 到 prompt 里。这会导致：
- 消息无限增长，很快超过 context window
- 控制面字段（telemetry、metrics、budget）混入 LLM 可见文本
- 原始工具输出（raw tool output）未经处理直接回灌 prompt

**StateFirst** 的含义是：
> 先提取和更新**结构化状态（WorkingState）**，再基于状态生成**投影（Projection）**，最后把投影转成 LLM-ready messages。

不是"消息驱动"，而是"状态驱动"。

---

## 3. 四层正交架构（ContextOS Data Plane）

ContextOS 的数据平面被严格拆分为四个正交层。这是 ADR-0071 的核心决策。

| 层级 | 代码实体 | 性质 | 职责 | 关键约束 |
|------|---------|------|------|---------|
| **TruthLog** | `TruthLogService` | Append-Only | 记录会话的"唯一真相"：所有发生的事件、决策、工具调用结果 | 写入后不可变；Replay 返回深拷贝 |
| **WorkingState** | `WorkingStateManager` | Mutable-but-controlled | 结构化可变工作区：用户画像、任务状态、待办事项、约束条件 | 通过 `replace()` 进行受控替换；支持 diff 追踪 |
| **ReceiptStore** | `ReceiptStore` | Offloading | 大内容（搜索结果、diff、文件切片）的引用存储，避免 prompt 膨胀 | Content-addressable（去重）；超出阈值自动创建 receipt |
| **ProjectionEngine** | `ProjectionEngine` | Read-Only | 将前三层状态**投影**为 LLM-ready prompt messages | 禁止反向修改 truth；自动剥离 control-plane 噪音 |

**代码出处**：`polaris/kernelone/context/context_os/runtime.py:202-206`

```python
class StateFirstContextOS:
    def __init__(...):
        # Four-layer ContextOS split components
        self._truth_log = TruthLogService()
        self._working_state_manager = WorkingStateManager(workspace=self._workspace)
        self._receipt_store = ReceiptStore(workspace=self._workspace)
        self._projection_engine = ProjectionEngine()
```

---

## 4. 各层详细运作机制

### 4.1 TruthLogService —— 唯一真相源

**文件**: `polaris/kernelone/context/truth_log_service.py`

```python
class TruthLogService:
    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
```

- **Append-Only**: 事件一旦写入，不可修改、不可删除。
- **Immutable Replay**: `replay()` 返回 `deepcopy`，防止外部代码意外篡改历史。
- **作用**: 为审计、调试、session 恢复提供可信赖的原始记录。

### 4.2 WorkingStateManager —— 结构化可变工作区

**文件**: `polaris/kernelone/context/working_state_manager.py`

```python
class WorkingStateManager:
    def __init__(self, workspace: str = ".") -> None:
        self._state: dict[str, Any] = {}
        self._working_state: WorkingState = WorkingState()
```

- `WorkingState` 是 Pydantic 模型，包含 `user_profile`、`task_state`、`plan_state` 等结构化字段。
- 所有修改必须通过 `replace()` 进行整体替换，而不是逐字段 patch——这保证了状态变更的原子性与可追踪性。

### 4.3 ReceiptStore —— 大内容卸载层

**文件**: `polaris/kernelone/context/receipt_store.py`

```python
class ReceiptStore:
    def __init__(self, workspace: str = ".") -> None:
        self._content_store = ContentStore(workspace=workspace)
        self._index: dict[str, ContentRef] = {}
```

- 工具返回的大段文本（如 `grep` 结果、文件 diff）不再直接 inline 到 prompt。
- `put()` 将内容存入 ContentStore，返回 content hash；prompt 中只保留一个**引用 stub**。
- **Content-addressable**: 相同内容自动去重，节省磁盘与内存。

### 4.4 ProjectionEngine —— 只读投影生成器

**文件**: `polaris/kernelone/context/projection_engine.py`

```python
class ProjectionEngine:
    _CONTROL_PLANE_KEYS = frozenset({
        "budget_status",
        "metrics",
        "policy_verdict",
        "system_warnings",
        "telemetry",
        "telemetry_events",
    })
    _TURN_BLOCKED_KEYS = frozenset({
        "budget_status",
        "metrics",
        "policy_verdict",
        "raw_output",
        "system_warnings",
        "telemetry",
        "telemetry_events",
        "thinking",
        "thinking_content",
    })
```

- **Read-Only**: `project()` 和 `build_payload()` 只有读权限，绝不修改 `TruthLog` 或 `WorkingState`。
- **Control-Plane Stripping**: 自动移除 `budget_status`、`metrics`、`telemetry`、`thinking` 等控制面字段，防止它们污染 LLM 的 data plane。
- **Receipt-Aware**: 遇到超大内容时，自动用 `ReceiptStore` 中的引用替换内联文本。

---

## 5. 控制面（Control Plane）与数据面（Data Plane）隔离

这是 ContextOS 最重要的设计原则之一，直接来自 ADR-0071。

| 类别 | 可进入数据面 | 禁止进入数据面 |
|------|-------------|---------------|
| **允许** | 用户消息、工具结果摘要、计划状态、约束条件、运行卡片 | — |
| **禁止** | — | `budget_status`、`metrics`、`policy_verdict`、`system_warnings`、`telemetry`、`telemetry_events`、`thinking`、`thinking_content`、`raw_output` |

**意义**:
1. LLM 不会因为看到"你还剩多少 token"而产生投机性行为。
2. LLM 不会因为 telemetry 中的内部错误日志而陷入自我怀疑循环。
3. 原始工具输出必须经过 ReceiptStore / summary 处理后才能进入 prompt。

---

## 6. StateFirstContextOS 的完整执行流程

**文件**: `polaris/kernelone/context/context_os/runtime.py:308+`（`project()` 主入口）

```
外部调用 project(context_payload) 
         │
         ├──→ 1. 解析并归一化输入（context_gateway 组装）
         ├──→ 2. 更新 WorkingState（结构化状态变更）
         ├──→ 3. 追加 TruthLog（记录本次输入的 canonical truth）
         ├──→ 4. 触发 ReceiptStore 检查（大内容自动 offload）
         ├──→ 5. PipelineRunner 执行投影策略（切片、去重、截断）
         ├──→ 6. ProjectionEngine 生成 LLM-ready messages
         ├──→ 7. _strip_control_plane_noise() 最终清洗
         └──→ 8. 返回 messages + 可选 snapshot
```

关键点：
- `project()` 是 **async-safe** 的，通过 `asyncio.Lock` 保证并发调用不会破坏内部状态。
- 每次投影后会生成 `ImmutableSnapshot`，用于审计、replay 和一致性校验。

---

## 7. ContextOS 与上层架构的交互关系

ContextOS 不是孤立存在的。它是整个"认知生命体"的**记忆工作区**。

### 7.1 与 TransactionKernel（事务内核）的交互

**文件**: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

```
TransactionKernel._execute_turn()
         │
         ├──→ 1. 构建 Context（可能调用 ContextOS/project）
         ├──→ 2. 请求 LLM Decision
         ├──→ 3. KernelGuard 检查决策合法性
         ├──→ 4. 执行 ToolBatch
         └──→ 5. 将 ToolBatch 结果回写 ContextOS（TruthLog + WorkingState）
```

- **一个 Turn 一次写入**: ToolBatch 执行完成后，结果通过统一入口更新 ContextOS，而不是在工具执行过程中不断 patch prompt。
- **单提交点**: TransactionKernel 是唯一的 turn 提交点，ContextOS 不会在没有事务保护的情况下被中间状态污染。

### 7.2 与 RoleSessionOrchestrator（会话编排器）的交互

**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py`

```
OrchestratorSessionState
         │
         ├──→ goal（生命体目标）
         ├──→ turn_count（年龄/阅历）
         └──→ artifacts（积累的知识财富）
```

- Orchestrator 的 `artifacts` 通过 `_checkpoint_session()` 持久化。
- 在每次 Turn 开始时，Orchestrator 将必要的 artifact 和 session 上下文注入 ContextOS，作为本次 Turn 的初始 `context_payload`。
- ContextOS 处理完后，生成的 messages 被传递给 TransactionKernel 用于 LLM 调用。

### 7.3 与 DevelopmentWorkflowRuntime（开发运行时）的交互

**文件**: `polaris/cells/roles/kernel/internal/development_workflow_runtime.py`

- 开发运行时被 Handoff 后，会创建自己的局部 ContextOS 实例或复用父实例的投影结果。
- 它的 `read→write→test` 循环产生的中间结果（如测试输出、编译错误）会通过 ReceiptStore 卸载，避免污染主会话的 prompt。

### 7.4 与 StreamShadowEngine（推测引擎）的交互

**文件**: `polaris/cells/roles/kernel/internal/stream_shadow_engine.py`

- ShadowEngine 提前执行的工具调用结果，如果被命中消费，其输出同样需要通过 ContextOS 的 `TruthLogService.append()` 和 `ReceiptStore.put()` 流程进行规范化写入。
- 这保证了"预热结果"与"真实结果"在 ContextOS 层面不可区分，维护了 Truth 的一致性。

---

## 8. 两层 4-layer 的关系澄清

目前项目中有"两套 4 层"，新人容易产生困惑：

| 分层 | 名称 | 领域 | 代码实体 |
|------|------|------|---------|
| **控制面 4 层** | 认知生命体控制平面 | Agent 执行架构 | `RoleSessionOrchestrator` → `DevelopmentWorkflowRuntime` → `TurnTransactionController` + `StreamShadowEngine` |
| **数据面 4 层** | ContextOS 数据平面 | 上下文/记忆管理 | `TruthLogService` → `WorkingStateManager` → `ReceiptStore` → `ProjectionEngine` |

**关系**：
- **控制面 4 层** 决定"此刻该做什么"（思考-行动-编排）。
- **数据面 4 层** 决定"记忆如何存储、如何投影给 LLM"。
- 两者**正交**：控制面调用数据面，但数据面不感知控制面的决策逻辑。

没有冲突。两者共同构成了 Polaris 的完整认知运行时。

---

## 9. 关键代码速查表

| 组件 | 文件路径 | 作用 |
|-----|---------|------|
| StateFirstContextOS | `polaris/kernelone/context/context_os/runtime.py` | ContextOS 唯一实现 |
| TruthLogService | `polaris/kernelone/context/truth_log_service.py` | Append-only 真相日志 |
| WorkingStateManager | `polaris/kernelone/context/working_state_manager.py` | 可变工作区管理 |
| ReceiptStore | `polaris/kernelone/context/receipt_store.py` | 大内容卸载与引用 |
| ProjectionEngine | `polaris/kernelone/context/projection_engine.py` | 只读 prompt 投影生成 |
| ContextOS models | `polaris/kernelone/context/context_os/models.py` | `WorkingState`、`ContextOSProjection`、`ContextOSSnapshot` 定义 |
| TransactionKernel | `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` | 唯一 turn 事务内核 |
| RoleSessionOrchestrator | `polaris/cells/roles/runtime/internal/session_orchestrator.py` | 会话编排与记忆 checkpoint |

---

## 10. 常见误区与纠正

### 误区 1: "ContextOS 就是消息历史记录器"
**纠正**: ContextOS 不是简单的消息堆叠。它是"状态优先"的——先更新 `WorkingState`，再基于状态生成投影。消息只是投影的最终输出形式。

### 误区 2: "StateFirstContextOS 和 ContextOS 是两套东西"
**纠正**: `StateFirstContextOS` 是 `ContextOS` 概念在当前代码中的**唯一实现**。就像 `Linux` 是操作系统概念的一个实现。不要寻找"另一个 ContextOS"。

### 误区 3: "ProjectionEngine 可以随意修改状态"
**纠正**: `ProjectionEngine` 是**严格只读**的。它只能读取 `TruthLog`、`WorkingState` 和 `ReceiptStore`，然后生成 messages。任何状态修改都必须通过 `WorkingStateManager.replace()` 或 `TruthLogService.append()` 完成。

### 误区 4: "控制面字段可以放进 prompt 让 LLM 自己判断"
**纠正**: `budget_status`、`telemetry`、`thinking` 等控制面字段被 `_CONTROL_PLANE_KEYS` 和 `_TURN_BLOCKED_KEYS` 显式禁止进入 prompt。这是为了防止 LLM 产生投机性、自指性或 panic 行为。

---

## 11. 权威引用链

- `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
- `docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md`
- `docs/blueprints/SESSION_ORCHESTRATOR_AND_DEVELOPMENT_WORKFLOW_RUNTIME_BLUEPRINT_20260417.md`
- `src/backend/polaris/kernelone/context/context_os/runtime.py`
- `src/backend/polaris/kernelone/context/projection_engine.py`
- `src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py`

---

**结论**: ContextOS（通过 `StateFirstContextOS`）是 Polaris 的"记忆与上下文操作系统"。四层正交架构（TruthLog / WorkingState / ReceiptStore / ProjectionEngine）与事务内核（TransactionKernel）和会话编排器（RoleSessionOrchestrator）完美协作，构成了当前项目可运行、可审计、可进化的认知基础设施。
