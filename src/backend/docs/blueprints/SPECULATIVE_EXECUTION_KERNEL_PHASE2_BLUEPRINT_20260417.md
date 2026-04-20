# Blueprint: Speculative Execution Kernel Phase 2 — Structured Concurrency, Cooperative Cancellation & Salvage

**Date:** 2026-04-17  
**Scope:** `polaris/cells/roles/kernel/internal/speculation/` and upstream integration points  
**Status:** Draft → Implementation Ready  
**Author:** Principal Architect  
**Related:** ADR-0077, VC-20260417-speculative-execution-kernel-v2

---

## 1. 业务背景与问题陈述

Phase 1 已落地 `ShadowTaskRegistry`、`SpeculationResolver`、`spec_key` 与 `ADOPT/JOIN/REPLAY` 四动作，使推测执行从骨架升级到了可事务追踪的内核。但当前取消语义仍然是粗暴的：

- `_drain_speculative_tasks()` 使用固定 0.2s timeout，超时后直接 `task.cancel()` 强杀；
- 没有细粒度的 **Cancel-or-Salvage** 策略（立刻取消 / 允许完成并缓存 / 正式流程接管 JOIN）；
- `turn` 取消或 `refusal abort` 后，已完成但尚未被 ADOPT 的 shadow task 仍可能被错误复用；
- `CancelToken` 仅在 `_runner()` 入口检查一次，工具执行链内部缺少 cooperative cancellation 埋点。

Phase 2 的目标是：
> **让推测执行具备结构化并发约束、合作式取消语义、以及 turn 级/拒绝级安全清理能力，彻底消除 ghost tasks。**

---

## 2. 高层架构（文本图）

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TurnTransactionController                            │
│  ┌────────────────────┐      refusal / turn_cancel                         │
│  │ StreamShadowEngine │ ───────────────────────────────────────────────┐   │
│  │   (facade)         │                                                  │   │
│  └─────────┬──────────┘                                                  │   │
│            │ speculate_tool_call()                                       │   │
│            ▼                                                             │   │
│  ┌────────────────────┐      adopt / join / replay                       │   │
│  │ SpeculationResolver│◄─────────────────────────────────────────────┐   │   │
│  └────────────────────┘                                              │   │   │
│            ▲                                                         │   │   │
│            │ lookup(spec_key)                                         │   │   │
│            ▼                                                         │   │   │
│  ┌────────────────────┐   cancel(task_id)   ┌──────────────────┐    │   │   │
│  │ ShadowTaskRegistry │◄────────────────────│ SalvageGovernor  │    │   │   │
│  │                    │   evaluate_salvage()│ (NEW Phase 2)    │    │   │   │
│  │  - _tasks_by_id    │◄────────────────────│                  │    │   │   │
│  │  - _active_spec_   │                     │  CANCEL_NOW      │    │   │   │
│  │    index           │                     │  LET_FINISH_AND_ │    │   │   │
│  │  - _turn_index     │                     │    CACHE         │    │   │   │
│  │  - _lock           │                     │  JOIN_AUTH       │    │   │   │
│  └─────────┬──────────┘                     └──────────────────┘    │   │   │
│            │                                                         │   │   │
│            │ start_shadow_task()                                     │   │   │
│            ▼                                                         │   │   │
│  ┌──────────────────────────────────────────────────────────────┐   │   │   │
│  │                    Structured Concurrency                     │   │   │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐  │   │   │   │
│  │  │ Shadow Task 1   │  │ Shadow Task 2   │  │ Shadow Task N│  │   │   │   │
│  │  │ (asyncio.Task)  │  │ (asyncio.Task)  │  │              │  │   │   │   │
│  │  │  - CancelToken  │  │  - CancelToken  │  │              │  │   │   │   │
│  │  │  - check_cancel │  │  - check_cancel │  │              │  │   │   │   │
│  │  │    checkpoints  │  │    checkpoints  │  │              │  │   │   │   │
│  │  └─────────────────┘  └─────────────────┘  └──────────────┘  │   │   │   │
│  │                                                               │   │   │   │
│  │  TurnScopedTaskGroup (NEW): 隔离 turn 生命周期与取消传播       │   │   │   │
│  └──────────────────────────────────────────────────────────────┘   │   │   │
│                                                                     │   │   │
└─────────────────────────────────────────────────────────────────────┘   │   │
                                                                            │   │
    authoritative execution path ───────────────────────────────────────────┘   │
                                                                                │
    _execute_tool_batch() ──────────────────────────────────────────────────────┘
```

---

## 3. 模块职责划分

### 3.1 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| `SalvageGovernor` | `speculation/salvage.py` | 在取消或 drain 时评估每个 shadow task 的命运：立刻取消、允许完成并缓存、或 JOIN 接管。 |
| `TurnScopedTaskGroup` | `speculation/task_group.py` | 为单个 turn 提供结构化并发容器。turn 结束时统一取消未完成的任务，保证无悬挂 task。 |
| `CancellationCoordinator` | `speculation/cancel.py` | 协调 `CancelToken` 的广播、`task.cancel()` 的调用、以及 `refusal abort` 后的批量清理。 |

### 3.2 改造模块

| 模块 | 改造点 |
|------|--------|
| `ShadowTaskRegistry` | 引入 `SalvageGovernor` 评估；`drain_turn()` 从“一刀切取消”升级为“按策略分别处理”；`mark_abandoned()` 扩展为 turn 级批量接口。 |
| `TurnTransactionController` | `execute_stream()` 中引入 `TurnScopedTaskGroup`；在 `refusal abort` 和 turn cancel 时调用 Registry 批量清理；`_drain_speculative_tasks()` 与 TaskGroup 生命周期对齐。 |
| `ToolBatchRuntime` | 在 `_execute_single()` 的关键路径（如网络请求前后、子进程启动前后、文件 IO 前后）增加 `check_cancel()` 埋点。 |
| `StreamShadowEngine` | `reset()` 扩展为同时清理当前 turn 的 speculative state；在 `turn` 边界显式关闭旧 TaskGroup。 |

---

## 4. 核心数据流

### 4.1 Shadow Task 启动流

1. `StreamShadowEngine.speculate_tool_call()` 触发。
2. `ShadowTaskRegistry.start_shadow_task()` 在 `TurnScopedTaskGroup` 内创建 `asyncio.Task`。
3. Task 的 `_runner()` 持有 `CancelToken`，在 `RUNNING` 后周期性 `check_cancel()`。
4. 结果写入 `ShadowTaskRecord`，状态流转为 `COMPLETED`。

### 4.2 Authoritative 解析流（Phase 1 已存在，Phase 2 增强）

1. `_execute_tool_batch()` 对每个 invocation 调用 `resolver.resolve_or_execute()`。
2. **ADOPT**: 直接复用 `COMPLETED` 结果，无需再执行。
3. **JOIN**: 等待 `RUNNING` 的 shadow future 完成，不重复执行。
4. **REPLAY**: 无匹配或状态异常，走 authoritative `tool_runtime`。

### 4.3 Turn Cancel / Refusal Abort 流（Phase 2 新增）

```
TurnTransactionController
    │
    ├── refusal detected ──► CancellationCoordinator.refuse_turn(turn_id)
    │                         └── Registry.mark_abandoned(task_id, reason="refusal")
    │
    ├── turn cancelled ────► TurnScopedTaskGroup.cancel_all()
    │                         └── for each running task:
    │                                 SalvageGovernor.evaluate(task)
    │                                 ├── CANCEL_NOW ───────► task.cancel()
    │                                 ├── LET_FINISH_AND_CACHE ► detach from group, short TTL
    │                                 └── JOIN_AUTHORITATIVE ► keep running, handoff to batch
    │
    └── execute_stream finally ──► _drain_speculative_tasks()
                                   └── Registry.drain_turn(turn_id)
                                       └── SalvageGovernor batch evaluate + apply
```

---

## 5. 关键设计决策

### 5.1 SalvageDecision 三选一策略

每个 shadow task 在面临取消压力时，由 `SalvageGovernor.evaluate(record: ShadowTaskRecord)` 决定命运：

| 决策 | 条件 | 行为 |
|------|------|------|
| `CANCEL_NOW` | `cancellability=cooperative` AND (`cost=expensive` OR `progress<20%`) | 立刻 `task.cancel()`，状态变为 `CANCELLED`。 |
| `LET_FINISH_AND_CACHE` | `progress>80%` OR `elapsed_ms > timeout_ms * 0.9` | 允许 task 继续在后台完成，完成后从 `_turn_index` 移除但保留在 `_active_spec_index` 短 TTL。 |
| `JOIN_AUTHORITATIVE` | `state=RUNNING` AND authoritative batch 即将执行该 tool | 将 task 保留，由 `_execute_tool_batch()` 的 `resolver.join()` 接管。 |

> **注意**：`progress` 在 Phase 2 是一个估计值（基于 elapsed / timeout_ms）。Phase 4 可由具体 runner 上报真实进度。

### 5.2 TurnScopedTaskGroup 设计

由于 Python 3.11+ `asyncio.TaskGroup` 一旦创建 task 就不能取消后重新加入，且 `TaskGroup` 会在 `__aexit__` 时等待所有任务完成，不适合“允许后台完成并缓存”的语义。因此：

- `TurnScopedTaskGroup` **不直接继承 `asyncio.TaskGroup`**，而是使用一个自定义的 `set[asyncio.Task]` 管理。
- 提供 `create_task(coro)` 注册任务。
- 提供 `async def cancel_all(salvage: bool = True)`：
  - 如果 `salvage=True`，先对每个任务调用 `SalvageGovernor.evaluate()`，分别处理；
  - 如果 `salvage=False`（如 hard turn cancel），全部 `cancel()`。
- 提供 `async def join_all(timeout: float | None = None)`：等待所有任务完成（用于 drain）。

### 5.3 Cooperative Cancellation 埋点

`CancelToken` 已在 Phase 1 落地。Phase 2 需要在 `ToolBatchRuntime._execute_single()` 的执行链中增加 `check_cancel()` 检查点：

1. **执行前**：确认 token 未被取消。
2. **网络请求前**：如果是外部 API 调用（如 `web_search`），在 HTTP client 发送前检查。
3. **子进程启动前**：如果是 `subprocess` 类工具，在 `create_subprocess_exec` 前检查。
4. **长时间循环内部**：如果是 `repo_rg` 类扫描工具，在每次文件读取或每 100ms 检查一次。
5. **执行后**：确认结果封装前未被取消，防止“取消后仍返回 stale 结果”。

### 5.4 Refusal Abort 语义

当模型输出 `refusal`（拒绝执行）时，整个 turn 的工具推测意图都失效了。此时：

- 所有 **已完成但未 ADOPT** 的 shadow task 必须标记为 `ABANDONED`，禁止后续 authoritative 路径错误 ADOPT。
- 所有 **运行中** 的任务执行 `CANCEL_NOW`（refusal 意味着用户指令已改变，继续执行无意义）。
- Registry 的 `_active_spec_index` 中移除该 turn 的所有 spec_key。

---

## 6. 接口变更

### 6.1 新增接口

```python
# speculation/salvage.py
class SalvageGovernor:
    def evaluate(self, record: ShadowTaskRecord) -> SalvageDecision: ...

# speculation/task_group.py
class TurnScopedTaskGroup:
    def create_task(self, coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]: ...
    async def cancel_all(self, *, salvage: bool = True) -> None: ...
    async def join_all(self, *, timeout: float | None = None) -> None: ...

# speculation/cancel.py
class CancellationCoordinator:
    async def refuse_turn(self, turn_id: str, registry: ShadowTaskRegistry) -> None: ...
    async def cancel_turn(self, turn_id: str, registry: ShadowTaskRegistry, task_group: TurnScopedTaskGroup) -> None: ...
```

### 6.2 改造接口

```python
# ShadowTaskRegistry.drain_turn()
# 改造前：统一 cancel + 0.2s timeout
# 改造后：先 salvage evaluate，再分别处理
async def drain_turn(
    self,
    turn_id: str,
    *,
    timeout_s: float = 0.2,
    salvage_governor: SalvageGovernor | None = None,
    task_group: TurnScopedTaskGroup | None = None,
) -> None: ...

# ShadowTaskRegistry.mark_abandoned()
# 新增批量接口
async def abandon_turn(self, turn_id: str, reason: str) -> None: ...
```

---

## 7. 测试策略

### 7.1 新增测试文件

| 文件 | 测试点 |
|------|--------|
| `test_speculation_cancellation.py` | `check_cancel` 抛出 `CancelledError`；runner `finally` 执行；token 广播生效。 |
| `test_speculation_salvage.py` | `SalvageGovernor` 三决策边界； expensive/cheap 工具分别决策；progress 阈值边界。 |
| `test_speculation_task_group.py` | `TurnScopedTaskGroup` 创建/取消/等待；salvage 模式下部分任务被允许完成。 |

### 7.2 集成测试扩展

在 `test_speculation_integration.py` 中新增：

1. `test_param_drift_replay`：参数变化后旧 shadow 被 cancel，新 authoritative 执行成功。
2. `test_turn_cancel_no_ghost`：turn cancel 后 Registry 中该 turn 所有任务状态为 `CANCELLED` 或 `ABANDONED`，且无运行中 task。
3. `test_refusal_abort`：refusal 后已完成的 shadow task 被标记为 `ABANDONED`，resolver 走 `replay`。

### 7.3 回归测试

- 全目录 `polaris/cells/roles/kernel/tests/` 必须保持 **968 passed, 1 skipped**。

---

## 8. 风险与边界

1. **TaskGroup 兼容性**：`TurnScopedTaskGroup` 与 Python 3.11 原生 `TaskGroup` 语义不同，团队成员必须清楚这一点，避免混用。
2. **Salvage 误判**：`progress` 目前是时间估计，对于“前 10% 时间完成 90% 工作”的工具（如缓存命中），可能错误 `CANCEL_NOW`。Phase 4 引入真实 runner progress 上报后可改善。
3. **CancelToken 检查开销**：在 tight loop 中高频 `check_cancel()` 可能影响性能。建议用时间阈值（如每 5ms 或每 N 次迭代）控制检查频率。
4. **Refusal 检测点**：refusal 的检测依赖于 LLM 输出解析，如果解析器漏检，abort 不会触发。需要确保 refusal 检测与 speculative 清理逻辑在同一异常处理路径中。

---

## 9. 实施顺序

1. **Step 1**: 实现 `SalvageGovernor` + `TurnScopedTaskGroup` + `CancellationCoordinator`。
2. **Step 2**: 改造 `ShadowTaskRegistry.drain_turn()` 和 `abandon_turn()`。
3. **Step 3**: 在 `ToolBatchRuntime`  runner 路径增加 `check_cancel()` 埋点。
4. **Step 4**: 改造 `TurnTransactionController` 的 refusal 处理与 `_drain_speculative_tasks()`。
5. **Step 5**: 编写 `test_speculation_cancellation.py`、`test_speculation_salvage.py`、`test_speculation_task_group.py` 及集成测试扩展。
6. **Step 6**: 全量回归测试 (`pytest polaris/cells/roles/kernel/tests/ -q`)。

---

## 10. 自检清单（Self-Check）

- [x] 无过度设计：`SalvageGovernor` 是简单规则引擎，无 ML、无复杂配置 DSL。
- [x] 向后兼容：`StreamShadowEngine` 和 `SpeculationResolver` 的 public 签名不变。
- [x] 类型安全：所有新增模块使用 `from __future__ import annotations` 与现代类型注解。
- [x] 防御性编程：`drain_turn` 的 timeout 有默认值；`evaluate()` 对缺失字段有 fallback。
- [x] 禁止炫技：不使用元编程、动态导入或复杂的 descriptor 模式。
