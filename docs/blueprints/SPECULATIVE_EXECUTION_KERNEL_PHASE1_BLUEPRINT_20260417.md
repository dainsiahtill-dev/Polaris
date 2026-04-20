# Speculative Execution Kernel Phase 1 执行蓝图

**日期**: 2026-04-17  
**版本**: v1.0  
**范围**: ADR-0077 的第一批落地（correctness 地基）  
**目标**: 在现有骨架代码上建立 `ShadowTaskRegistry` + `SpeculationResolver` + `spec_key` + 统一事件日志，实现 `ADOPT/JOIN/CANCEL/REPLAY` 事务语义。

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│  turn_transaction_controller.execute_stream()                        │
│   ├─ consume_delta() 兼容接口                                        │
│   ├─ speculate_tool_call() → 走 Resolver (ADOPT/JOIN/CANCEL/REPLAY)  │
│   └─ resolve_or_execute()  ← 正式 authoritative 调用                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  StreamShadowEngine (facade)                                         │
│   ├─ 保留 consume_delta() / speculate_tool_call() / reset()         │
│   ├─ 新增 resolve_or_execute()                                       │
│   └─ 注入 registry + resolver                                        │
└─────────────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┴─────────────────┐
            ▼                                   ▼
┌─────────────────────────┐         ┌─────────────────────────┐
│  SpeculationResolver    │         │  ShadowTaskRegistry     │
│  - resolve_or_execute() │◄──────►│  - start_shadow_task()  │
│  - ADOPT / JOIN         │         │  - lookup()             │
│  - CANCEL / REPLAY      │         │  - adopt() / join()     │
│                         │         │  - cancel() / drain_turn│
└─────────────────────────┘         │  - asyncio.Lock         │
                                    └─────────────────────────┘
                                                  │
                                                  ▼
                                    ┌─────────────────────────┐
                                    │  SpeculativeExecutor    │
                                    │  - execute_speculative()│
                                    │  (透传 cancel_token)    │
                                    └─────────────────────────┘
                                                  │
                                                  ▼
                                    ┌─────────────────────────┐
                                    │  ToolBatchRuntime       │
                                    │  - execute_batch()      │
                                    │  - ToolExecutionContext │
                                    │    扩展 cancel_token    │
                                    └─────────────────────────┘
```

---

## 2. 模块职责与接口契约

### 2.1 `polaris/cells/roles/kernel/internal/speculation/models.py`
**职责**: 定义所有 speculation 相关的数据模型。

**必须包含**:
- `ParseState(Enum)`: `INCOMPLETE`, `SYNTACTIC_COMPLETE`, `SCHEMA_VALID`, `SEMANTICALLY_STABLE`
- `ShadowTaskState(Enum)`: `CREATED`, `ELIGIBLE`, `STARTING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCEL_REQUESTED`, `CANCELLED`, `ABANDONED`, `ADOPTED`, `EXPIRED`
- `SalvageDecision(Enum)`: `CANCEL_NOW`, `LET_FINISH_AND_CACHE`, `JOIN_AUTHORITATIVE`
- `ToolSpecPolicy(dataclass)`: 四维策略 + `speculate_mode` + `min_stability_score` + `timeout_ms` + `max_parallel` + `cache_ttl_ms`
- `CandidateToolCall(dataclass)`: 候选工具调用
- `ShadowTaskRecord(dataclass)`: 影子任务记录，含 `task_id`, `origin_turn_id`, `spec_key`, `state`, `future`, `result`, `error`, `cost_estimate`, `cancel_reason`, `adopted_by_call_id`, `expiry_at`
- `BudgetSnapshot(dataclass)`: 预算快照
- `CancelToken`: 简单的取消标记类

**约束**:
- 所有字段必须带完整类型注解。
- `ToolSpecPolicy` 使用 `frozen=True`（不可变）。

### 2.2 `polaris/cells/roles/kernel/internal/speculation/fingerprints.py`
**职责**: 参数归一化与 spec_key 生成。

**接口**:
```python
def normalize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """对参数做 canonical 归一化：排序键、去首尾空白字符串、统一换行。"""

def build_spec_key(
    tool_name: str,
    normalized_args: dict[str, Any],
    *,
    corpus_version: str = "",
    auth_scope: str = "",
    env_fingerprint: str = "",
) -> str:
    """基于 SHA-256 生成唯一 spec_key。"""

def build_env_fingerprint(workspace: str = ".") -> str:
    """基于当前环境生成指纹（简化版：workspace path + git HEAD 或 mtime hash）。"""
```

**约束**:
- `normalize_args` 必须保证字段顺序变化不影响输出结构（`sort_keys=True`）。
- 等价空白/换行处理后字符串值一致。

### 2.3 `polaris/cells/roles/kernel/internal/speculation/events.py`
**职责**: 统一事件模型与日志输出。

**接口**:
```python
@dataclass
class SpeculationEvent:
    event_type: str
    turn_id: str
    stream_id: str | None = None
    call_id: str | None = None
    candidate_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    spec_key: str | None = None
    policy_mode: str | None = None
    action: str | None = None
    reason: str | None = None
    latency_ms: int | None = None
    saved_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

def emit(event: SpeculationEvent) -> None:
    """输出结构化日志。"""
```

### 2.4 `polaris/cells/roles/kernel/internal/speculation/metrics.py`
**职责**: 指标记录器，供 Registry 和 Resolver 调用。

**接口**:
```python
class SpeculationMetrics:
    def record_started(self, candidate: CandidateToolCall, spec_key: str) -> None: ...
    def record_adopt(self, turn_id: str, call_id: str, tool_name: str, spec_key: str) -> None: ...
    def record_join(self, turn_id: str, call_id: str, tool_name: str, spec_key: str) -> None: ...
    def record_replay(self, turn_id: str, call_id: str, tool_name: str, reason: str) -> None: ...
    def record_cancel(self, task_id: str, reason: str) -> None: ...
    def record_skip(self, candidate: CandidateToolCall, reason: str) -> None: ...
    def record_completed(self, task_id: str, duration_ms: int) -> None: ...
    def record_failed(self, task_id: str, error: str) -> None: ...
```

### 2.5 `polaris/cells/roles/kernel/internal/speculation/registry.py`
**职责**: 影子任务注册表，系统核心协调者。

**接口**:
```python
class ShadowTaskRegistry:
    def __init__(
        self,
        *,
        speculative_executor: SpeculativeExecutor,
        metrics: SpeculationMetrics,
        cache: "EphemeralSpecCache | None" = None,
    ) -> None: ...

    async def start_shadow_task(
        self,
        *,
        turn_id: str,
        candidate_id: str,
        tool_name: str,
        normalized_args: dict[str, Any],
        spec_key: str,
        env_fingerprint: str,
        policy: ToolSpecPolicy,
        cost_estimate: float = 0.0,
    ) -> ShadowTaskRecord: ...

    def lookup(self, spec_key: str) -> ShadowTaskRecord | None: ...
    def exists_active(self, spec_key: str) -> bool: ...
    async def adopt(self, task_id: str, call_id: str) -> Any: ...
    async def join(self, task_id: str, call_id: str) -> Any: ...
    async def cancel(self, task_id: str, reason: str) -> None: ...
    async def drain_turn(self, turn_id: str, *, timeout_s: float = 0.2) -> None: ...
```

**关键约束**:
- `start_shadow_task` 内部必须持 `asyncio.Lock()`。
- 同一 `spec_key` 同时最多一个 active task（状态为 `STARTING`/`RUNNING`/`COMPLETED` 且未过期）。
- `_runner()` 必须正确处理 `asyncio.CancelledError`（记录 `CANCELLED` 并 re-raise）和通用异常（记录 `FAILED`）。
- `COMPLETED` 任务设置 `expiry_at = finished_at + cache_ttl_ms/1000`。

### 2.6 `polaris/cells/roles/kernel/internal/speculation/resolver.py`
**职责**: 正式执行阶段的四动作裁决器。

**接口**:
```python
class SpeculationResolver:
    def __init__(
        self,
        *,
        registry: ShadowTaskRegistry,
        speculative_executor: SpeculativeExecutor,
        metrics: SpeculationMetrics,
    ) -> None: ...

    async def resolve_or_execute(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """
        返回统一结果字典：
        {
            "action": "adopt" | "join" | "replay",
            "result": Any | None,
            "error": str | None,
        }
        """
```

**判定逻辑**:
1. `normalized_args` → `spec_key`
2. `task = registry.lookup(spec_key)`
3. `task is None` → `REPLAY`
4. `task.state == COMPLETED` → `ADOPT`
5. `task.state in (STARTING, RUNNING)` → `JOIN`
6. `task.state in (FAILED, CANCELLED, EXPIRED)` → `REPLAY`
7. 其他 → `REPLAY`（安全降级）
8. `REPLAY` 时调用 `speculative_executor.execute_authoritative()`（Phase 1 先直接调用现有 runtime）

**注意**: 由于现有 `turn_transaction_controller` 的 authoritative 执行逻辑分散在流式处理中，Phase 1 的 `resolve_or_execute` 返回结果字典，由 controller 判断是否还需要调用 `tool_runtime`。这样改造面最小。

---

## 3. 现有文件改造清单

### 3.1 `speculative_executor.py`
**改造**:
- 保留 `speculate(tool_invocation)` 作为旧路径兼容。
- 新增 `execute_speculative(tool_name, args, *, timeout_ms, cancel_token)`。
- 内部封装 `ToolInvocation` + `ToolBatch`，调用 `self._batch_runtime.execute_batch(batch, context=ctx)`。

### 3.2 `tool_batch_runtime.py`
**改造**:
- `ToolExecutionContext` 新增字段：`turn_id`, `call_id`, `speculative`, `cancel_token`, `deadline_monotonic`, `spec_key`。
- `execute_batch()`  signature 保持 `execute_batch(self, batch, *, context=None)`。
- 在每个 tool invocation 执行前检查 `cancel_token`（如果存在且已取消，抛 `CancelledError`）。
- 检查 `deadline_monotonic`（如果超时，抛 `TimeoutError`）。

### 3.3 `stream_shadow_engine.py`
**改造**:
- `__init__` 新增可选参数 `registry=None, resolver=None`。
- `consume_delta(delta)` 保留兼容实现。
- `speculate_tool_call(tool_name, arguments, call_id)`：如果 `resolver` 存在，直接调用 `resolver.resolve_or_execute()`；否则 fallback 到旧路径。
- 新增 `resolve_or_execute(turn_id, call_id, tool_name, args)`，透传到 `resolver`。

### 3.4 `turn_transaction_controller.py`
**改造**:
- `_build_stream_shadow_engine()`: 注入 `ShadowTaskRegistry` 和 `SpeculationResolver`。
- `_drain_speculative_tasks()`: 
  - 保留现有对 `speculative_tasks` 列表的 drain 逻辑（兼容）。
  - 新增调用 `registry.drain_turn(turn_id)`。
- `execute_stream()` 中的正式工具执行点：
  - 当前在 `event_type == "tool_call"` 后直接 yield `ToolBatchEvent`，authoritative 执行可能在后续处理。
  - **Phase 1 最小改造策略**: 在 `_try_speculate_tool_call` 中如果 shadow_engine 有 resolver，直接走 `resolve_or_execute`；如果返回 `action == "replay"`，则继续现有逻辑调用 `tool_runtime`。
  - 这样不需要大幅重构 controller 的流式事件序列。

---

## 4. 测试矩阵

### 4.1 单元测试
- `test_speculation_registry.py`:
  - `test_same_spec_key_deduplication`
  - `test_adopt_completed_task`
  - `test_join_running_task`
  - `test_cancel_running_task`
  - `test_ttl_expiration`
  - `test_drain_turn_cancels_all`

- `test_speculation_resolver.py`:
  - `test_resolve_adopts_completed`
  - `test_resolve_joins_running`
  - `test_resolve_replays_when_no_task`
  - `test_resolve_replays_when_failed`
  - `test_resolve_replays_when_cancelled`

- `test_speculation_fingerprints.py`:
  - `test_normalize_args_sorts_keys`
  - `test_normalize_args_trims_strings`
  - `test_spec_key_changes_with_args`
  - `test_spec_key_changes_with_env`

### 4.2 集成测试
- `test_speculation_integration.py`:
  - `test_single_tool_adopt`: shadow 完成后 authoritative ADOPT
  - `test_single_tool_join`: shadow 运行中 authoritative JOIN
  - `test_param_drift_replay`: 参数不一致导致 REPLAY
  - `test_turn_cancel_no_ghost`: turn 结束后 registry 无悬挂任务
  - `test_refusal_abort`: refusal 后 completed shadow 不可 adopt

### 4.3 回归测试
- `test_speculative_execution.py` 现有 8 个测试必须全部通过。

---

## 5. 交付验收标准

1. `ruff check polaris/cells/roles/kernel/internal/speculation/ --fix` 静默通过。
2. `ruff format polaris/cells/roles/kernel/internal/speculation/` 完成格式化。
3. `mypy polaris/cells/roles/kernel/internal/speculation/` 输出 `Success: no issues found`。
4. `pytest polaris/cells/roles/kernel/tests/test_speculation_*.py -v` 全部通过。
5. `pytest polaris/cells/roles/kernel/tests/test_speculative_execution.py -v` 全部通过。
6. `pytest polaris/cells/roles/kernel/tests/ -q` 全目录回归无新增失败。

---

## 6. Rollout 时序

| 步骤 | 内容 | 预计文件数 |
|------|------|-----------|
| 1 | 创建 `speculation/` 子目录 + `models.py` + `fingerprints.py` + `events.py` + `metrics.py` | 5 |
| 2 | 实现 `registry.py` + `resolver.py` | 2 |
| 3 | 改造 `speculative_executor.py` + `tool_batch_runtime.py` | 2 |
| 4 | 改造 `stream_shadow_engine.py` | 1 |
| 5 | 改造 `turn_transaction_controller.py`（最小面） | 1 |
| 6 | 编写单元测试 + 集成测试 | 4 |
| 7 | 运行 ruff / mypy / pytest 并修复 | - |
