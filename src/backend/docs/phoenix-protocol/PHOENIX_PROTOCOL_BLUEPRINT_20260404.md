# 凤凰协议（Phoenix Protocol）自愈架构蓝图

**状态**: 草稿
**日期**: 2026-04-04
**负责人**: Python 架构十人委员会

---

## 1. 脆弱性溯源报告

### 1.1 `resilience.py` 审计

**位置**: `polaris/kernelone/llm/engine/resilience.py`

**超时与重试边界审计结果**:
- ✅ `total_timeout` 在每次重试循环开始时均被检查（第633-642行）
- ✅ `retry_after` 与 `total_timeout` 协调逻辑正确：`await asyncio.sleep(min(retry_after, self.timeout_config.total_timeout / 2))`（第691行）
- ✅ 重试延迟公式：`delay = base_delay * exponential_base^(attempt-1)`（第738-740行）
- ✅ 抖动因子：0.7~1.0 随机乘数（第745行）
- ⚠️ `is_retryable()` 函数（第54-85行）基于 HTTP status code 判断，未与 `ErrorCategory` 完全对齐：
  - `INVALID_RESPONSE`、`JSON_PARSE`、`CONFIG_ERROR` 走 HTTP fallback path（status=None → 默认 retryable）
  - 应优先使用 `classify_error()` 映射到 `ErrorCategory` 后再判断

**结论**: 基础可用，需强化错误分类集成。

### 1.2 `workflow/engine.py` 审计

**DAG 调度 `asyncio.wait()` 竞态隐患**:
- ✅ `fail_fast_triggered` 在任务启动前检查（第475-479行）
- ⚠️ `_cancel_running`（第748-753行）：`task.cancel()` 后 `await asyncio.gather(*running.values())` 在 cancel 后立即 gather，任务可能未真正取消
- ⚠️ `asyncio.wait()` + `FIRST_COMPLETED`（第495行）：无超时时不精确，无法感知具体哪个任务完成

**断点恢复缺口**:
- ❌ `start()`（第224-232行）仅初始化 schema 和 timer_wheel，无从 snapshot 恢复逻辑
- ❌ `create_snapshot()` 接口已定义（`WorkflowRuntimeStore`），但无调用路径
- ✅ `_workflow_state` 和 `_workflow_tasks` 内存状态在引擎停止时清空（第246-248行）

**死信缺口**:
- ❌ `_execute_spec()`（第532-588行）重试耗尽后返回 `TaskExecutionOutcome(status="failed")`
- ❌ `_apply_outcome()`（第712-746行）将失败任务标记为 `fail_fast_triggered`，但无 DLQ 写入路径
- ✅ `append_event()` 接口已存在，但从未写入 `task_dead_lettered` 事件

### 1.3 重试策略公式不一致

| 模块 | 公式 | 问题 |
|------|------|------|
| `retry_policy.py` (cells/roles) | `base_delay * 2 ** attempt` | attempt 从 0 开始，第0次重试 delay=base_delay |
| `resilience.py` (kernelone) | `base_delay * exponential_base ** (attempt - 1)` | attempt 从 1 开始，第1次重试 delay=base_delay |

**不一致影响**: 工具级重试与 LLM API 重试延迟节奏不同，可能导致工具重试先于/后于 LLM 重试

---

## 2. 凤凰自愈蓝图

### 2.1 断路器部署架构

```
┌─────────────────────────────────────────────────────────┐
│                    MultiProviderFallbackManager          │
│  (封装 ResilienceManager 链式调用，按优先级尝试 Provider) │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌───────────┐  ┌───────────┐  ┌───────────┐
│ Provider A│  │ Provider B│  │ Provider C│
│CircuitBreaker│ │CircuitBreaker│ │CircuitBreaker│
└─────┬─────┘  └─────┬─────┘  └─────┬─────┘
      │              │              │
      └──────────────┴──────────────┘
              CircuitBreakerRegistry
```

每个 Provider 一个独立 `CircuitBreaker` 实例，通过 `CircuitBreakerRegistry.get_or_create(provider_name)` 管理。

### 2.2 任务死信与状态恢复架构

```
WorkflowEngine
├── DLQ (DeadLetterQueue 接口)
│   ├── enqueue(task_id, workflow_id, payload, error)   → 持久化
│   ├── dequeue()                                       → 重入
│   └── requeue(task_id, workflow_id)                   → 重试
├── resume_workflow(workflow_id, from_snapshot=True)    → 断点续执
└── _execute_spec() 重试耗尽后 → DLQ.enqueue() → append_event("task_dead_lettered")
```

**DLQ 实现路径**:
- 复用现有 `TaskQueueManager` 的 `TaskQueue`，建立名为 `"__dlq__"` 的死信队列
- 或通过 `WorkflowRuntimeStore.append_event()` 标记 `task_dead_lettered`，由外部消费者扫描

### 2.3 多模型 Fallback 落点

**新增文件**: `polaris/kernelone/llm/engine/client.py`

```
ResilientLLMClient
├── _providers: list[LLMProvider]          # 按优先级排序
├── _circuit_breakers: CircuitBreakerRegistry
├── _resilience_config: RetryConfig
├── invoke(messages, model, ...)            # 主入口
│   ├── for each provider in priority order:
│   │   ├── breaker = registry.get_or_create(provider.name)
│   │   ├── response = await breaker.call(provider.invoke)
│   │   ├── if success: return response
│   │   ├── if retryable + has_next: continue
│   │   └── if non-retryable: break + fallback
│   └── return create_fallback_response()
└── invoke_stream(messages, model, ...)     # 流式版本
```

---

## 3. 韧性基石代码规格

### 3.1 `ResilientLLMClient` 接口规格

```python
# polaris/kernelone/llm/engine/client.py

from dataclasses import dataclass
from typing import Protocol

class LLMProvider(Protocol):
    """LLM Provider 接口，所有 Provider 必须实现此契约"""
    name: str
    async def invoke(self, request: AIRequest) -> AIResponse: ...
    async def invoke_stream(self, request: AIRequest) -> AsyncGenerator[AIStreamEvent]: ...

@dataclass(frozen=True)
class ProviderConfig:
    provider_name: str
    priority: int = 0  # 越小优先级越高
    timeout: float = 60.0
    retry_config: RetryConfig | None = None
    circuit_breaker_config: CircuitBreakerConfig | None = None

class ResilientLLMClient:
    """多模型 Fallback + 弹性策略的 LLM 客户端"""
    
    def __init__(
        self,
        providers: list[LLMProvider],
        default_timeout_config: TimeoutConfig | None = None,
        default_retry_config: RetryConfig | None = None,
    ) -> None: ...
    
    async def invoke(
        self,
        request: AIRequest,
        provider_configs: dict[str, ProviderConfig] | None = None,
    ) -> AIResponse: ...
    
    async def invoke_stream(
        self,
        request: AIRequest,
        provider_configs: dict[str, ProviderConfig] | None = None,
    ) -> AsyncGenerator[AIStreamEvent]: ...
```

### 3.2 错误分类强化规格

**问题**: `is_retryable(status_code)` 散落在 HTTP 层面，未与 `ErrorCategory` 对齐

**修复**: `resilience.py` 新增 `is_retryable_by_category()` 方法：

```python
def is_retryable_by_category(self, category: ErrorCategory) -> bool:
    """基于 ErrorCategory 判断是否可重试（优先于 HTTP status code）"""
    # 语义层错误：fast-fail
    if category in (
        ErrorCategory.INVALID_RESPONSE,
        ErrorCategory.JSON_PARSE,
        ErrorCategory.CONFIG_ERROR,
    ):
        return False
    # 传输层错误：通过 RetryConfig.retryable_errors 判断
    return category in self.retry_config.retryable_errors
```

### 3.3 死信队列接口规格

```python
# polaris/kernelone/workflow/dlq.py

from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass(frozen=True)
class DeadLetterItem:
    task_id: str
    workflow_id: str
    handler_name: str
    input_payload: dict[str, Any]
    error: str
    failed_at: str  # ISO format
    attempt: int
    max_attempts: int
    dlq_reason: str  # "retry_exhausted" | "circuit_breaker_open" | ...

class DeadLetterQueue(Protocol):
    """死信队列接口"""
    async def enqueue(self, item: DeadLetterItem) -> None: ...
    async def dequeue(self, timeout: float = 0.1) -> DeadLetterItem | None: ...
    async def requeue(self, item: DeadLetterItem, delay_seconds: float = 0) -> None: ...
    async def size(self) -> int: ...
    async def peek(self, limit: int = 10) -> list[DeadLetterItem]: ...
```

---

## 4. 实施计划

### Phase 1: 错误分类强化 ✅ 已完成（2026-04-04）
- [x] 在 `resilience.py` 新增 `is_retryable_by_category()` 方法
- [x] `execute_with_resilience()` 优先使用 `ErrorCategory` 判断
- [x] `retry_policy.py` 与 `resilience.py` 退避公式在各自作用域内一致（已确认无需改动）
- [x] 修复 `TimeoutError`/`Exception` 处理返回过期响应 Bug
- [x] `asyncio.CancelledError` 不再被 `except Exception` 吞掉

**变更**: `polaris/kernelone/llm/engine/resilience.py`

### Phase 2: 多模型 Fallback ✅ 已完成（2026-04-04）
- [x] 新建 `polaris/kernelone/llm/engine/client.py` - `ResilientLLMClient`
- [x] `MultiProviderFallbackManager` 实现（集成在 client.py）
- [x] 每个 Provider 通过独立 `CircuitBreaker` 保护
- [x] `invoke_stream()` 流式 fallback：ERROR 事件触发切换下一 Provider
- [x] 非流式 Provider 自动降级为单次 invoke + CHUNK + COMPLETE

**新增**: `polaris/kernelone/llm/engine/client.py`

### Phase 3: 死信队列 ✅ 已完成（2026-04-04）
- [x] 新建 `polaris/kernelone/workflow/dlq.py` - `InMemoryDeadLetterQueue` + `DeadLetterQueue` 协议
- [x] `WorkflowEngine` 集成 DLQ（`_dead_letter_queue` 属性）
- [x] `_execute_spec()` 重试耗尽后写入 `task_dead_lettered` 事件
- [x] `WORKFLOW_TIMEOUT` / `WORKFLOW_CANCELLED` 触发 pending 任务入 DLQ
- [x] `DLQReason` 枚举：`RETRY_EXHAUSTED` / `CIRCUIT_BREAKER_OPEN` / `WORKFLOW_TIMEOUT` / `WORKFLOW_CANCELLED`
- [x] `append_dlq_event()` 使用 `EventStore` Protocol（替代 `Any`）
- [x] `resume_workflow()` 的 task_id key 构造增加 None 保护

**新增**: `polaris/kernelone/workflow/dlq.py`
**变更**: `polaris/kernelone/workflow/engine.py`

### Phase 4: 断点恢复 ✅ 已完成（2026-04-04）
- [x] `resume_workflow()` 从 `list_task_states()` + `get_events()` 恢复 DAG 进度
- [x] 新增 `workflow_resumed` 事件

**变更**: `polaris/kernelone/workflow/engine.py`

### Phase 5: `_cancel_running` 竞态修复 ✅ 已完成（2026-04-04）
- [x] 使用 `asyncio.wait()` + `grace period` 替代直接 `gather()`
- [x] 两波取消机制：优先 grace period 内取消，第二波强制取消剩余任务

**变更**: `polaris/kernelone/workflow/engine.py`

### Phase 6: DLQ Requeue Worker ✅ 已完成（2026-04-04）
- [x] `DLQRequeueWorker` 后台 worker：定时轮询 DLQ + 重新提交任务
- [x] `RequeueStrategy` 枚举：`RETRY_NOW` / `REJECT`
- [x] `max_requeue_attempts` 限制单任务重试次数
- [x] 支持手动 `process_one()` / `process_all()` 调用

**新增**: `polaris/kernelone/workflow/dlq.py`

---

## 5. 验证计划

### 5.1 单元测试
- `test_resilience.py` - 重试边界、错误分类、降级响应
- `test_circuit_breaker.py` - 已有 698 行，保持通过
- `test_dlq.py` - 入队/出队/重入逻辑（需新增）
- `test_workflow_resume.py` - 快照恢复 DAG 执行（需新增）

### 5.2 集成测试
- Provider A 失败 → 自动切换 Provider B
- Provider 全部失败 → 返回 `fallback_response`
- 任务重试耗尽 → 进入 DLQ
- 引擎重启后恢复进行中的 workflow
- Workflow 超时/取消 → pending 任务入 DLQ

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 引入新的全局状态 | 测试隔离困难 | DLQ 通过 DI 注入，不使用全局变量 |
| `asyncio.wait` 升级 `TaskGroup` | 行为变化可能破坏现有逻辑 | 仅在 DAG 执行路径升级，保持顺序执行兼容 |
| 多 Provider 并发调用风暴 | 多个 Provider 同时请求 | CircuitBreaker HALF_OPEN 限制测试请求数 |
