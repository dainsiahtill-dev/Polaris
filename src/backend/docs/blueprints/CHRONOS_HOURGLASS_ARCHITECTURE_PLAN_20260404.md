# Chronos Hourglass - 长程任务状态机架构蓝图

**版本**: v1.0  
**日期**: 2026-04-04  
**架构师**: Python 架构十人委员会  
**目标**: 构建状态持久化、可中断、支持人工介入的超长程任务状态机引擎
**状态**: ✅ 全部5个Phase已实现（2026-04-04）

---

## 1. 现状审计与问题剖析

### 1.1 僵死进程与状态丢失根因

| 问题 | 位置 | 影响 |
|------|------|------|
| `_workflow_tasks` 内存字典 | `engine.py:211` | 服务重启后运行中 workflow 永久丢失 |
| `pause_event: asyncio.Event` 无法序列化 | `engine.py:159` | pause 语义在重启后丢失 |
| `pending_signals` 内存列表 | `engine.py:157` | 重启前未处理的信号丢失 |
| 无 Saga 补偿机制 | `contracts.py:72` | 任务失败后无法回滚 |
| 无 Human-in-the-loop | `contracts.py:142` | 高风险操作无法暂停等待人工审批 |
| `InMemoryDeadLetterQueue` 重启清空 | `dlq.py:142` | 死信队列状态不持久 |
| 无 `WAITING_HUMAN` 状态 | `task.py:41` | 任务无法进入人工等待状态 |

### 1.2 当前状态流转（有限）

```
QUEUED → PENDING → READY → CLAIMED → IN_PROGRESS → COMPLETED/FAILED
                                    ↓
                                CANCELLED/TIMEOUT
```

### 1.3 目标状态流转

```
PLANNING → EXECUTING → WAITING_HUMAN → COMPENSATING → COMPLETED/FAILED
    ↑           ↓              ↓               ↓
    └───────────┴──────────────┴───────────────┘
                    (可重启恢复)
```

---

## 2. 核心扩展设计

### 2.1 Saga 补偿机制

#### 2.1.1 TaskSpec 扩展

```python
@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_type: str
    handler_name: str
    depends_on: tuple[str, ...] = ()
    input_payload: dict[str, Any] = field(default_factory=dict)
    input_from: dict[str, str] = field(default_factory=dict)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float = 300.0
    continue_on_error: bool = False
    # --- Saga 补偿字段（新增）---
    compensation_handler: str | None = None  # 补偿动作 handler name
    compensation_input: dict[str, Any] = field(default_factory=dict)  # 补偿输入
    is_high_risk: bool = False  # 高风险标记
    metadata: dict[str, Any] = field(default_factory=dict)
```

#### 2.1.2 WorkflowContract 扩展

```python
@dataclass(frozen=True)
class WorkflowContract:
    mode: str
    task_specs: tuple[TaskSpec, ...]
    max_concurrency: int
    continue_on_error: bool
    workflow_timeout_seconds: float = 3600.0
    # --- Human-in-the-loop 字段（新增）---
    high_risk_actions: frozenset[str] = frozenset()  # 高风险 action 集合
    human_review_webhook: str | None = None  # 人工审批 webhook
```

#### 2.1.3 补偿执行流程

```
任务失败 → 查询补偿链 → 逆序执行补偿动作 → append_event("compensation_started")
                                                            ↓
                                                    append_event("compensation_completed")
                                                            ↓
                                               append_event("workflow_failed", {compensation_done: true})
```

### 2.2 Human-in-the-Loop 断点机制

#### 2.2.1 WAITING_HUMAN 状态扩展

在 `domain/entities/task.py` 的 `TaskStatus` 枚举新增：

```python
class TaskStatus(str, Enum):
    # ... 现有状态 ...
    WAITING_HUMAN = "waiting_human"  # 新增：等待人工审批
```

#### 2.2.2 挂起事件流转

```
执行高风险 action → append_event("task_suspended_human_review", {task_id, reason})
                       ↓
                  写入 WAITING_HUMAN 状态 → 释放 CPU/内存
                       ↓
                  等待 webhook 唤醒 → append_event("human_approved", {task_id})
                       ↓
                  恢复执行 → append_event("task_resumed", {task_id})
```

#### 2.2.3 断点恢复流程

```
服务重启 → resume_workflow() 扫描 WAITING_HUMAN 任务
              ↓
         恢复挂起任务到 pending → 等待 webhook 唤醒
              ↓
         webhook 到达 → signal_workflow("resume_task", {task_id})
```

### 2.3 持久化 FSM 设计

#### 2.3.1 无状态引擎原则

**重构目标**: `SagaWorkflowEngine` 必须是**无状态**的，只读写外部存储。

核心转变：
- ❌ 旧: `WorkflowRuntimeState` 持有 `pause_event: asyncio.Event`（内存对象）
- ✅ 新: `WorkflowRuntimeState` 不持有任何内存事件，pause 语义通过 `append_event("workflow_paused")` 持久化

#### 2.3.2 状态重建流程

```
start() / 重启 → 从 store 加载 last_snapshot
                     ↓
              扫描 event_log 重建内存状态
                     ↓
              对 WAITING_HUMAN 任务注册 timer wheel 回调
```

#### 2.3.3 事件溯源日志

利用现有 `WorkflowRuntimeStore.append_event()` 作为不可变事件日志：

| 事件类型 | 用途 |
|---------|------|
| `workflow_started` | 启动记录 |
| `task_started` | 任务开始 |
| `task_completed` | 任务完成 |
| `task_failed` | 任务失败 |
| `task_suspended_human_review` | 高风险挂起 |
| `human_approved` | 人工批准 |
| `human_rejected` | 人工拒绝 |
| `compensation_started` | 补偿开始 |
| `compensation_task_started` | 补偿任务开始 |
| `compensation_task_completed` | 补偿任务完成 |
| `compensation_completed` | 补偿完成 |
| `workflow_paused` | 工作流暂停 |
| `workflow_resumed` | 工作流恢复 |
| `workflow_suspended` | 工作流挂起（高风险） |
| `workflow_checkpoint` | 定期检查点 |
| `workflow_completed` | 工作流完成 |
| `workflow_failed` | 工作流失败 |

---

## 3. 文件变更清单

### 3.1 新增文件

| 文件路径 | 用途 | 状态 |
|---------|------|------|
| `polaris/kernelone/workflow/saga_engine.py` | SagaWorkflowEngine 核心实现 | ✅ |
| `polaris/kernelone/workflow/stateful_task.py` | StatefulTask 基类 | ✅ |
| `polaris/kernelone/workflow/human_in_loop.py` | Human-in-the-loop 断点管理 | ✅ (集成到 saga_engine.py) |
| `polaris/kernelone/workflow/compensation链.py` | 补偿链执行器 | ✅ (集成到 saga_engine.py) |
| `polaris/kernelone/workflow/event_store.py` | 事件溯源存储适配器 | ❌ (无需新建，复用 WorkflowRuntimeStore) |
| `polaris/kernelone/workflow/persistent_timer_wheel.py` | 可持久化 TimerWheel | ✅ |
| `polaris/kernelone/workflow/checkpoint_manager.py` | 检查点管理器 | ✅ |
| `polaris/kernelone/workflow/tests/test_saga_engine.py` | Saga 集成测试 (18 tests) | ✅ |
| `polaris/kernelone/workflow/tests/test_saga_recovery.py` | 重启恢复集成测试 (21 tests) | ✅ |

### 3.2 修改文件

| 文件路径 | 变更 | 状态 |
|---------|------|------|
| `polaris/kernelone/workflow/contracts.py` | 新增 `compensation_handler`, `compensation_input`, `is_high_risk`, `high_risk_actions`, `human_review_webhook` 字段 | ✅ |
| `polaris/domain/entities/task.py` | 新增 `WAITING_HUMAN` 状态 | ✅ |
| `polaris/kernelone/workflow/engine.py` | 保留原实现，Saga能力在 saga_engine.py 并行实现 | ⚠️ |
| `polaris/kernelone/workflow/dlq.py` | 扩展 `DeadLetterItem` 支持 `compensation_event` | ✅ |

---

## 4. SagaWorkflowEngine 核心设计

### 4.1 类结构

```python
class SagaWorkflowEngine:
    """
    无状态 Saga 工作流引擎。
    所有状态通过 WorkflowRuntimeStore 持久化。
    """

    def __init__(
        self,
        store: WorkflowRuntimeStore,
        activity_runner: ActivityRunner,
        timer_wheel: PersistentTimerWheel,
        dlq: DeadLetterQueue,
        checkpoint_interval_seconds: float = 300.0,
    ) -> None:
        self._store = store
        self._activity_runner = activity_runner
        self._timer_wheel = timer_wheel
        self._dlq = dlq
        self._checkpoint_interval = checkpoint_interval_seconds

    async def start_workflow(
        self,
        workflow_id: str,
        contract: WorkflowContract,
        payload: dict[str, Any],
    ) -> RuntimeSubmissionResult:
        """启动新工作流（无状态，只写 store）"""

    async def resume_workflow(self, workflow_id: str) -> RuntimeSubmissionResult:
        """从检查点恢复工作流"""

    async def signal_workflow(
        self,
        workflow_id: str,
        signal_name: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """向工作流发送信号（pause/resume/cancel/approve_task）"""
```

### 4.2 补偿执行伪代码

```python
async def _execute_compensation_chain(
    self,
    workflow_id: str,
    completed_tasks: list[TaskRuntimeState],
) -> None:
    """逆序执行补偿链"""
    await self._store.append_event(
        workflow_id,
        "compensation_started",
        {"task_count": len(completed_tasks)}
    )

    # 逆序遍历已完成任务
    for task in reversed(completed_tasks):
        if task.task_spec.compensation_handler:
            await self._store.append_event(
                workflow_id,
                "compensation_task_started",
                {"task_id": task.task_id, "handler": task.task_spec.compensation_handler}
            )
            try:
                result = await self._activity_runner.execute(
                    task.task_spec.compensation_handler,
                    task.task_spec.compensation_input,
                )
                await self._store.append_event(
                    workflow_id,
                    "compensation_task_completed",
                    {"task_id": task.task_id, "result": result}
                )
            except Exception as e:
                await self._store.append_event(
                    workflow_id,
                    "compensation_task_failed",
                    {"task_id": task.task_id, "error": str(e)}
                )
                # 补偿失败可选择重试或escalate到DLQ

    await self._store.append_event(workflow_id, "compensation_completed", {})
```

### 4.3 Human-in-the-Loop 伪代码

```python
async def _execute_spec_with_human_gate(
    self,
    task_spec: TaskSpec,
    state: WorkflowRuntimeState,
) -> TaskRuntimeState:
    """执行任务前检查高风险标记"""
    if task_spec.is_high_risk or task_spec.task_id in state.contract.high_risk_actions:
        # 挂起并等待人工审批
        await self._store.append_event(
            state.workflow_id,
            "task_suspended_human_review",
            {"task_id": task_spec.task_id, "reason": "high_risk_action"}
        )
        # 注册 timer wheel 回调，超时后 escalate 到 DLQ
        self._timer_wheel.schedule(
            workflow_id=state.workflow_id,
            task_id=task_spec.task_id,
            due_monotonic=time.monotonic() + task_spec.timeout_seconds,
            callback=lambda: self._escalate_to_dlq(state.workflow_id, task_spec.task_id),
        )
        # 更新任务状态为 WAITING_HUMAN（不阻塞引擎）
        return TaskRuntimeState(
            task_id=task_spec.task_id,
            status="waiting_human",
            # ...
        )
    else:
        return await self._execute_spec(task_spec, state)

async def _handle_human_approval(
    self,
    workflow_id: str,
    task_id: str,
    approved: bool,
) -> None:
    """处理人工审批结果"""
    if approved:
        await self._store.append_event(
            workflow_id, "human_approved", {"task_id": task_id}
        )
        # 恢复任务执行
    else:
        await self._store.append_event(
            workflow_id, "human_rejected", {"task_id": task_id}
        )
        # 触发补偿链
```

---

## 5. 状态转换图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CHRONOS HOURGLASS FSM                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────┐    start     ┌────────────┐   checkpoint   ┌───────────────┐ │
│   │  PLANNING │────────────▶│  EXECUTING │───────────────▶│ CHECKPOINTING │ │
│   └──────────┘              └────────────┘                └───────────────┘ │
│         │                         │                              │          │
│         │                         │                              │          │
│         │                         ▼                              ▼          │
│         │                  ┌────────────┐                ┌───────────────┐  │
│         │                  │  WAITING   │◀──human_review──│ EXECUTING     │  │
│         │                  │  _HUMAN    │                │ (resumed)     │  │
│         │                  └────────────┘                └───────────────┘  │
│         │                         │                              │          │
│         │                         │ approved                     │          │
│         │                         ▼                              │          │
│         │                  ┌────────────┐                        │          │
│         │                  │ EXECUTING │                        │          │
│         │                  │ (continued)│                       │          │
│         │                  └────────────┘                        │          │
│         │                         │                              │          │
│         │                         │ task failed                  │          │
│         │                         ▼                              │          │
│         │                  ┌────────────┐                        │          │
│         │                  │COMPENSATING│◀──compensate──────────┘          │
│         │                  └────────────┘                                  │
│         │                         │                                        │
│         │                         │ completed                              │
│         │                         ▼                                        │
│         │                  ┌────────────┐   ┌────────────┐                   │
│         └─────────────────▶│  COMPLETED │   │  FAILED    │                   │
│           cancel/request   └────────────┘   └────────────┘                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**状态说明**:
- `PLANNING`: 工作流初始化，解析 contract
- `EXECUTING`: DAG 任务执行中
- `CHECKPOINTING`: 定期保存检查点（后台，不阻塞执行）
- `WAITING_HUMAN`: 任务挂起等待人工审批
- `COMPENSATING`: 执行 Saga 补偿链
- `COMPLETED`: 正常结束
- `FAILED`: 失败结束（含补偿后仍失败）

---

## 6. 实现阶段计划

### Phase 1: 基础设施扩展（1-2周）✅ DONE
- [x] 扩展 `TaskSpec` 新增 `compensation_handler`, `compensation_input`, `is_high_risk`
- [x] 扩展 `WorkflowContract` 新增 `high_risk_actions`, `human_review_webhook`
- [x] 扩展 `TaskStatus` 新增 `WAITING_HUMAN`
- [x] 事件溯源通过现有 `WorkflowRuntimeStore.append_event()` 接口实现（无需新建 event_store.py）

### Phase 2: Saga 引擎核心（2-3周）✅ DONE
- [x] 创建 `polaris/kernelone/workflow/saga_engine.py` 无状态引擎
- [x] 实现 `_execute_compensation_chain()` 补偿执行器
- [x] 实现 `resume_workflow()` 从检查点恢复
- [x] 集成 `DeadLetterQueue` 处理补偿失败

### Phase 3: Human-in-the-Loop（2周）✅ DONE (集成到 saga_engine.py)
- [x] 实现高风险任务自动挂起机制 (`_suspend_for_human_review()`)
- [x] 实现 webhook 唤醒流程 (`_handle_human_approval()`)
- [x] 实现超时 escalation 到 DLQ (`_schedule_human_review_timer()`)

### Phase 4: 检查点与恢复（1-2周）✅ DONE
- [x] 创建 `polaris/kernelone/workflow/checkpoint_manager.py`
- [x] 实现定期检查点保存
- [x] 实现服务重启后状态重建
- [x] 验证 pause/resume 跨重启可恢复

### Phase 5: 集成测试与调优（1周）✅ DONE
- [x] 编写 saga 补偿集成测试
- [x] 编写 human-in-loop 集成测试
- [x] 编写断点恢复集成测试
- [x] 性能调优

---

## 8. 深度审计修复记录 (2026-04-04)

### 审计发现的 CRITICAL BUGS（已修复）

| Bug ID | 描述 | 严重性 | 修复方案 |
|--------|------|--------|---------|
| BUG-1 | 高风险任务审批后不会重新加入执行队列 | CRITICAL | 新增 `suspended` 集合跟踪挂起任务，审批后将状态设为 `pending` 并重新加入 `pending` 集合 |
| BUG-2 | 暂停信号处理中挂起任务会丢失 | CRITICAL | 修复 `_consume_pending_signals()` 使用内存队列，暂停时正确等待恢复 |
| BUG-3 | 审批时定时器未取消导致竞态条件 | CRITICAL | `_handle_human_approval()` 中先调用 `cancel_timer()` 再处理审批 |
| BUG-4 | Resume 后 waiting_human 任务未恢复执行 | CRITICAL | `resume_workflow()` 从 store 加载 waiting_human 任务并重新加入 `pending` 集合 |
| BUG-5 | `_get_pending_signals()` 永远返回空列表 | CRITICAL | 实现真正的内存信号队列 `_pending_signals[workflow_id]` 并在 `signal_workflow()` 时填充 |
| BUG-6 | 多个并发 `resume_workflow` 调用无检查 | HIGH | 增加 `already_running` 状态检查，防止同一 workflow 重复启动 |
| BUG-7 | `start_workflow` 中存在竞态条件 | HIGH | 在 `_lock` 内增加 `already_running` 检查 |

### 修复后的架构改进

1. **信号处理改进**: 使用内存信号队列 + 持久化事件双写，保证信号不丢失且可审计
2. **挂起任务跟踪**: `suspended` 集合与 `pending` 集合分离，审批后正确重新入队
3. **定时器生命周期**: 审批时显式取消定时器，避免超时回调覆盖审批结果
4. **并发控制**: 增加 `already_running` 检查，防止同一 workflow 重复执行

---

## 9. 技术约束

1. **Python 3.12+ match-case**: 复杂状态转移使用 `match` 表达式
2. **无状态引擎**: 不在内存中持有可序列化状态
3. **UTF-8**: 所有文本文件读写显式 UTF-8
4. **复用优先**: 复用现有 `TimerWheel`, `DeadLetterQueue`, `WorkflowRuntimeStore`
5. **fail-closed**: 验证失败不得标记任务完成

---

## 10. 风险与缓解

| 风险 | 缓解策略 |
|------|---------|
| 补偿链执行中途服务崩溃 | 补偿任务也写入检查点，可断点恢复 |
| 人工审批超时 | TimerWheel 超时后自动 escalation 到 DLQ |
| 循环依赖检测 | 复用 `contracts.py` 现有 DAG 循环检测 |
| 事件日志膨胀 | 定期压缩旧事件，保留关键节点快照 |

---

*本蓝图为 Chronos Hourglass 架构的完整设计文档。实施前请先评审，评审通过后按 Phase 顺序执行。*
