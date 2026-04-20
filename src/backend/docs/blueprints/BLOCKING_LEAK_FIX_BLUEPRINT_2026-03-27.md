# Execution Broker 阻塞与内存泄漏根因修复蓝图

**文档版本**: 1.0.0
**创建日期**: 2026-03-27
**状态**: 待实施
**优先级**: P0-P2

---

## 摘要 (Executive Summary)

本次全面代码审计覆盖 `polaris/` 下的 10 个关键模块，发现 **12+ P0-P1 问题** 和 **20+ P2 问题**，涵盖阻塞、内存泄漏、资源泄漏三类风险。核心问题包括：

1. **P0**: minimax provider 的 `_blocking_sleep` 在异步上下文抛出 InvalidStateError
2. **P0**: `bus_port.py` 使用同步 `time.sleep()` 轮询，无法被取消
3. **P0**: `SSEJetStreamConsumer.__anext__` 每次调用创建新生成器，状态丢失
4. **已修复**: `executor.py` 不可达代码和缩进错误

---

## 问题清单 (Issue Catalog)

### P0 - 立即修复 (Critical)

| ID | 问题 | 文件 | 类型 | 根因 |
|----|------|------|------|------|
| P0-001 | `minimax _blocking_sleep` | `provider_helpers.py` | 阻塞 | 同步sleep在asyncio上下文 |
| P0-002 | `bus_port` 阻塞轮询 | `bus_port.py:143-152` | 阻塞 | `time.sleep()`无法被取消 |
| P0-003 | `SSEJetStreamConsumer.__anext__` | `sse_utils.py:254-260` | 逻辑错误 | 每次创建新生成器 |
| P0-004 | `executor.py` 不可达代码 | `executor.py:199-253` | 代码缺陷 | **已修复** |

### P1 - 高优先级 (High)

| ID | 问题 | 文件 | 风险 |
|----|------|------|------|
| P1-001 | 任务无追踪无法取消 | `archive_hook.py` | 僵尸任务 |
| P1-002 | 文件锁无超时机制 | `factory_store.py:46-54` | 死锁 |
| P1-003 | 异常吞噬无限循环 | `agent_runtime_base.py:780-795` | CPU 100% |
| P1-004 | drain-and-requeue非原子 | `agent_runtime_base.py:280-283` | 消息丢失 |
| P1-005 | `sse_jetstream_generator` 无finally | `sse_utils.py:286-314` | 资源泄漏 |
| P1-006 | 全局ThreadPoolExecutor未关闭 | 全局 | 资源泄漏 |

### P2 - 中优先级 (Medium)

| ID | 问题 | 文件 | 影响 |
|----|------|------|------|
| P2-001 | 无界缓存 (5处) | 多个文件 | 内存无限增长 |
| P2-002 | `_listeners`列表永不清理 | `sequential_engine.py` | 内存泄漏 |
| P2-003 | yaml.safe_load文件句柄泄漏 | 治理脚本 (4处) | 句柄耗尽 |
| P2-004 | 全局`_RUN_FILE_LOCKS_GUARD`瓶颈 | `factory_store.py` | 锁竞争 |
| P2-005 | `stream_from_async_gen`无finally | `ports.py:249-276` | 资源泄漏 |

---

## 详细问题分析 (Detailed Analysis)

### P0-001: minimax `_blocking_sleep` 问题

**现象**: 调用 minimax provider 时抛出 `InvalidStateError: Task is already running`

**根因**:
```python
# provider_helpers.py - 错误模式
def _blocking_sleep(seconds: float) -> None:
    time.sleep(seconds)  # 在asyncio上下文调用会失败

# 在async函数中调用
async def some_async_func():
    _blocking_sleep(0.1)  # InvalidStateError
```

**修复方案**:
```python
async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)

# 或使用run_sync包装
from polaris.kernelone.runtime import run_sync
run_sync(asyncio.sleep(0.1))
```

---

### P0-002: bus_port 阻塞轮询

**现象**: 当外部取消信号到达时，`poll()` 方法的轮询循环无法立即终止

**根因**:
```python
# bus_port.py:143-152 - 错误模式
while time.monotonic() < deadline:
    time.sleep(interval)  # 同步sleep，无法被asyncio取消
    envelope = self._pop_inbox(receiver)
    if envelope is not None:
        return envelope
```

**修复方案**:
```python
async def poll_async(self, receiver: str, timeout: float = 1.0) -> AgentEnvelope | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)  # 可被取消
        envelope = self._pop_inbox(receiver)
        if envelope is not None:
            return envelope
    return None
```

---

### P0-003: SSEJetStreamConsumer.__anext__ 生成器缺陷

**现象**: 异步迭代消费者时，事件丢失或重复

**根因**:
```python
# sse_utils.py:254-260 - 错误模式
async def __anext__(self) -> dict[str, Any]:
    async for event in self.stream():  # 每次创建新生成器
        if event.get("type") == "ping":
            continue
        return event
    raise StopAsyncIteration
```

**修复方案**:
```python
class SSEJetStreamConsumer:
    def __init__(self, ...):
        self._stream_iter: AsyncIterator | None = None

    async def __anext__(self) -> dict[str, Any]:
        if self._stream_iter is None:
            self._stream_iter = self.stream().__aiter__()
        try:
            while True:
                event = await self._stream_iter.__anext__()
                if event.get("type") != "ping":
                    return event
        except StopAsyncIteration:
            self._stream_iter = None
            raise
```

---

### P1-001: archive_hook 任务无追踪

**现象**: 调用 `disable()` 后，已创建的归档任务仍在后台运行

**根因**:
```python
# archive_hook.py - 错误模式
def trigger_run_archive(self, run_id: str, ...) -> None:
    asyncio.create_task(self._archive_run_async(...))  # 任务丢失
```

**修复方案**:
```python
def __init__(self, ...):
    self._pending_tasks: dict[str, asyncio.Task] = {}

def trigger_run_archive(self, run_id: str, ...) -> None:
    task = asyncio.create_task(self._archive_run_async(...))
    self._pending_tasks[run_id] = task
    task.add_done_callback(lambda t: self._pending_tasks.pop(run_id, None))

def shutdown(self) -> None:
    for task in self._pending_tasks.values():
        task.cancel()
```

---

### P1-003: agent_runtime_base 异常吞噬

**现象**: Agent循环遇到错误后进入快速失败循环，CPU占用率飙升

**根因**:
```python
# agent_runtime_base.py:785-795 - 错误模式
except Exception as e:
    logger.error(...)
    time.sleep(1)  # 固定1秒间隔，但连续异常会快速循环
    continue  # 无退出条件
```

**修复方案**:
```python
async def _run_loop(self) -> None:
    consecutive_errors = 0
    max_backoff = 60.0

    while self._running:
        try:
            await self.run_cycle()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            backoff = min(2 ** consecutive_errors, max_backoff)
            logger.error("[%s] Cycle error (attempt %d): %s",
                       self.agent_name, consecutive_errors, e)
            await asyncio.sleep(backoff)

            if consecutive_errors >= 10:
                logger.critical("[%s] Too many errors, stopping", self.agent_name)
                break
```

---

### P2-001: 无界缓存

**现象**: 长期运行后内存持续增长

**涉及文件**:
- `execution_broker/service.py`: `_process_handles`, `_process_log_tasks`
- `sequential_engine.py`: `_event_history`
- `factory_store.py`: `_RUN_FILE_LOCKS`

**修复方案**:
```python
from collections import OrderedDict
from typing import TypeVar, Generic

K = TypeVar('K')
V = TypeVar('V')

class BoundedCache(Generic[K, V]):
    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[K, V] = OrderedDict()
        self._max_size = max_size

    def set(self, key: K, value: V) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = value

    def get(self, key: K) -> V | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None
```

---

## 测试策略 (Testing Strategy)

### 单元测试覆盖

```python
# tests/test_bus_port_async.py
import pytest
import asyncio

class TestBusPortAsync:
    @pytest.mark.asyncio
    async def test_poll_can_be_cancelled(self):
        """验证poll()可以被asyncio取消"""
        bus = InMemoryBus()
        task = asyncio.create_task(bus.poll_async("receiver", timeout=10.0))

        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_poll_returns_message(self):
        """验证poll()正确返回消息"""
        bus = InMemoryBus()
        bus.publish("receiver", create_message("test"))

        result = await bus.poll_async("receiver", timeout=1.0)

        assert result is not None
        assert result.message == "test"
```

### 边界测试

```python
# tests/test_timeout_edge_cases.py
class TestTimeoutEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_timeout(self):
        """零超时应该立即返回"""
        result = await poll_async(timeout=0.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_negative_timeout(self):
        """负超时应该视为零超时"""
        result = await poll_async(timeout=-1.0)
        assert result is None
```

---

## 实施计划 (Implementation Plan)

### 阶段1: P0修复 (1-2天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| P0-001 minimax修复 | Agent-01 | 无InvalidStateError |
| P0-002 bus_port修复 | Agent-02 | poll可被取消 |
| P0-003 sse_utils修复 | Agent-03 | 迭代器无状态丢失 |

### 阶段2: P1修复 (2-3天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| P1-001 archive_hook | Agent-04 | 任务可追踪取消 |
| P1-002 factory_store | Agent-05 | 锁有超时保护 |
| P1-003 agent_runtime | Agent-06 | 异常有退避 |
| P1-004 peek原子性 | Agent-07 | 消息不丢失 |

### 阶段3: P2修复 (3-5天)

| 任务 | 负责人 | 验收标准 |
|------|--------|----------|
| P2-001 缓存限制 | Agent-08 | 内存有上限 |
| P2-003 文件句柄 | Agent-09 | 无泄漏模式 |
| P2-005 stream清理 | Agent-10 | 有finally |

---

## 工程化标准 (Engineering Standards)

### 必须遵守

1. **PEP 8**: 遵循代码风格指南
2. **类型注解**: 所有公共API必须有类型提示
3. **docstring**: 类和公共方法必须有docstring
4. **异常处理**: 捕获具体异常，避免裸except
5. **测试覆盖**: 正常、边界、异常场景

### 审查清单

- [ ] 函数命名清晰（动宾结构）
- [ ] 单一职责（不超过50行）
- [ ] 无全局可变状态
- [ ] 资源使用后释放
- [ ] 超时有上限
- [ ] 日志级别正确

---

## 风险评估 (Risk Assessment)

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 重构引入回归 | 高 | 先写测试，CI门禁 |
| 性能回退 | 中 | 基准测试对比 |
| 向后兼容 | 中 | 保持API签名 |

---

## 后续优化建议 (Future Optimizations)

1. **统一异步运行时**: 所有 `asyncio.run()` 调用统一到 `ExecutionFacade`
2. **资源追踪**: 添加上下文管理器自动清理
3. **监控指标**: 暴露缓存命中率、队列深度等
4. **优雅关闭**: 实现完整的生命周期管理协议
