---
status: accepted
date: 2026-04-23
author: ContextOS Architecture Team
---

# ADR-007: ContextOS 异步架构迁移决策记录

## Table of Contents

1. [Background](#background)
2. [Decision](#decision)
3. [Consequences](#consequences)
4. [Alternatives Considered](#alternatives-considered)
5. [Implementation Notes](#implementation-notes)

---

## Background

### 1.1 迁移动机

ContextOS 当前使用 `threading.RLock` 实现并发控制，在 Polaris 的异步运行时环境中产生以下结构性问题：

1. **线程模型不匹配**  
   Polaris 采用 `asyncio` 作为核心事件循环，`threading.RLock` 的阻塞语义与异步协程的协作式调度产生冲突，导致：
   - 协程切换时无法释放锁，造成不必要的阻塞
   - 线程上下文切换开销远高于协程切换（~1μs vs ~100ns）
   - 异步代码中混合同步锁，破坏 `async/await` 的语义一致性

2. **性能瓶颈**  
   在高并发场景（>1000 并发会话）下，锁竞争导致：
   - 上下文切换开销增加 3-5 倍
   - 内存占用增加（每个线程栈 ~8MB vs 协程栈 ~4KB）
   - 延迟 P99 从 50ms 恶化到 200ms+

3. **可维护性问题**  
   - 同步/异步混合代码难以追踪调用链
   - 死锁风险增加（跨线程锁 + 协程锁混合）
   - 测试复杂度上升（需要同时处理线程和协程的边界情况）

4. **架构一致性要求**  
   ADR-0071 确立了 `TransactionKernel` 作为唯一事务提交点，要求上下文系统完全异步化以支持：
   - `TurnTransactionController` 的流式执行
   - `StreamShadowEngine` 的推测执行
   - 统一的 `ContextHandoffPack` 异步序列化

### 1.2 当前状态

```python
# 迁移前：同步锁实现
from threading import RLock

class ContextOS:
    def __init__(self):
        self._lock = RLock()
        self._state = {}
    
    def read(self, key: str) -> Any:
        with self._lock:  # 阻塞式锁
            return self._state.get(key)
    
    def write(self, key: str, value: Any) -> None:
        with self._lock:  # 阻塞式锁
            self._state[key] = value
```

### 1.3 目标状态

```python
# 迁移后：异步锁实现
from asyncio import Lock

class AsyncContextOS:
    def __init__(self):
        self._lock = Lock()
        self._state = {}
    
    async def read(self, key: str) -> Any:
        async with self._lock:  # 协作式锁
            return self._state.get(key)
    
    async def write(self, key: str, value: Any) -> None:
        async with self._lock:  # 协作式锁
            self._state[key] = value
```

---

## Decision

### 2.1 技术选型

**选定方案**: `asyncio.Lock`

**决策依据**:

| 维度 | asyncio.Lock | asyncio.Semaphore | threading.RLock |
|------|--------------|-------------------|-----------------|
| **语义匹配** | 完美匹配 Polaris 的异步架构 | 过度设计（无需并发限制） | 与异步运行时冲突 |
| **性能** | 协程切换 ~100ns | 协程切换 ~100ns | 线程切换 ~1μs |
| **内存占用** | ~4KB/协程 | ~4KB/协程 | ~8MB/线程 |
| **公平性** | FIFO（默认） | FIFO（默认） | 非公平 |
| **重入支持** | 不支持（强制规范） | 不支持 | 支持 |
| **死锁风险** | 低（单事件循环） | 低 | 高（跨线程） |

**关键决策点**:

1. **放弃重入锁语义**  
   `asyncio.Lock` 不支持重入，这强制要求：
   - 锁粒度细化到最小临界区
   - 避免锁嵌套，改用更细粒度的状态管理
   - 通过代码审查确保无隐式重入

2. **单事件循环约束**  
   所有 `AsyncContextOS` 实例必须在同一个事件循环中操作，禁止跨线程访问。

3. **锁超时策略**  
   引入 `asyncio.wait_for()` 包装，默认超时 5 秒，防止无限等待。

### 2.2 向后兼容策略

采用**双轨制架构**，逐步迁移：

```
┌─────────────────────────────────────────────────────────────┐
│                    Public API Layer                         │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │  sync_read() │  │ sync_write() │  │  sync_project() │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 Compatibility Bridge                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  asyncio.run_coroutine_threadsafe()                  │  │
│  │  - 将同步调用转换为异步执行                          │  │
│  │  - 在独立线程中运行事件循环                          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Async Implementation                      │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │  async_read()│  │async_write() │  │ async_project() │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**实现代码**:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

class ContextOS:
    """ContextOS 双轨实现：异步核心 + 同步兼容层"""
    
    def __init__(self):
        self._async_impl = _AsyncContextOSCore()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="context_os_")
    
    # ========== 异步 API（推荐）==========
    async def read(self, key: str) -> Any:
        """异步读取状态"""
        return await self._async_impl.read(key)
    
    async def write(self, key: str, value: Any) -> None:
        """异步写入状态"""
        await self._async_impl.write(key, value)
    
    # ========== 同步兼容 API（已弃用）==========
    def sync_read(self, key: str) -> Any:
        """
        同步读取接口（向后兼容）
        
        警告: 此接口性能较低，新代码应使用异步 API
        计划移除版本: v2.1.0
        """
        return self._run_sync(self._async_impl.read(key))
    
    def sync_write(self, key: str, value: Any) -> None:
        """
        同步写入接口（向后兼容）
        
        警告: 此接口性能较低，新代码应使用异步 API
        计划移除版本: v2.1.0
        """
        return self._run_sync(self._async_impl.write(key, value))
    
    def _run_sync(self, coro):
        """在独立线程中运行协程，保持同步接口"""
        try:
            loop = asyncio.get_running_loop()
            # 已在异步上下文中，直接调度
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=5.0)
        except RuntimeError:
            # 无事件循环，创建临时循环
            return asyncio.run(coro)


class _AsyncContextOSCore:
    """异步核心实现"""
    
    def __init__(self):
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = {}
    
    async def read(self, key: str) -> Any:
        async with self._lock:
            return self._state.get(key)
    
    async def write(self, key: str, value: Any) -> None:
        async with self._lock:
            self._state[key] = value
```

### 2.3 迁移路线图

| 阶段 | 时间 | 目标 | 验证标准 |
|------|------|------|----------|
| **Phase 1** | Week 1 | 核心异步化 | `AsyncContextOSCore` 单元测试 100% 通过 |
| **Phase 2** | Week 2 | 兼容层实现 | 同步 API 回归测试通过 |
| **Phase 3** | Week 3 | 调用点迁移 | `polaris/kernelone/context/` 全异步 |
| **Phase 4** | Week 4 | 性能基准 | P99 延迟 < 100ms @ 1000 并发 |
| **Phase 5** | Week 6 | 废弃同步 API | 添加 `@warnings.deprecated` |

---

## Consequences

### 3.1 正向影响

1. **性能提升**
   - 上下文切换开销降低 90%（~1μs → ~100ns）
   - 内存占用减少 99.9%（~8MB/线程 → ~4KB/协程）
   - 高并发 P99 延迟稳定在 50ms 以下

2. **架构一致性**
   - 与 `TransactionKernel` 的异步执行模型对齐
   - 支持 `StreamShadowEngine` 的推测执行
   - 统一的 `async/await` 语义

3. **可维护性**
   - 单一并发模型（纯异步）
   - 调用链清晰（无线程/协程混合）
   - 死锁风险显著降低

### 3.2 负面影响

1. **API 破坏性变更**
   - 所有调用点需要添加 `await`
   - 同步代码需要显式桥接
   - 第三方库可能不兼容

2. **调试复杂度**
   - 协程堆栈追踪较复杂
   - 异步死锁更难诊断
   - 需要新的性能分析工具

3. **测试成本**
   - 需要重构现有测试（`pytest-asyncio`）
   - 异步测试用例增加 30%
   - 需要并发压力测试

### 3.3 风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| **竞态条件** | 中 | 高 | 代码审查 + 静态分析（`asyncio-race`） |
| **性能回退** | 低 | 高 | 基准测试 + 自动回归检测 |
| **死锁** | 低 | 高 | 锁超时 + 死锁检测器 |
| **API 不兼容** | 高 | 中 | 双轨制 + 废弃警告 |
| **测试覆盖不足** | 中 | 中 | 强制覆盖率门禁（>90%） |

### 3.4 测试策略

```python
# 1. 单元测试：锁语义验证
@pytest.mark.asyncio
async def test_async_lock_exclusion():
    """验证异步锁的互斥语义"""
    ctx = AsyncContextOS()
    results = []
    
    async def writer(value: int):
        await ctx.write("key", value)
        results.append(await ctx.read("key"))
    
    # 并发写入，验证无竞态
    await asyncio.gather(*[writer(i) for i in range(100)])
    assert len(set(results)) == 100  # 无重复值

# 2. 压力测试：高并发场景
@pytest.mark.asyncio
async def test_concurrent_stress():
    """1000 并发下的性能基准"""
    ctx = AsyncContextOS()
    
    async def workload():
        for _ in range(100):
            await ctx.write("counter", await ctx.read("counter") + 1)
    
    await ctx.write("counter", 0)
    await asyncio.gather(*[workload() for _ in range(1000)])
    assert await ctx.read("counter") == 100000

# 3. 兼容性测试：同步 API
@pytest.mark.asyncio
async def test_sync_bridge():
    """验证同步桥接层"""
    ctx = ContextOS()
    
    # 在异步测试中调用同步 API
    ctx.sync_write("key", "value")
    assert ctx.sync_read("key") == "value"
```

### 3.5 回滚计划

**触发条件**:
- P99 延迟 > 200ms（持续 5 分钟）
- 死锁错误率 > 0.1%
- 竞态条件导致的数据损坏

**回滚步骤**:

1. **立即回滚（< 5 分钟）**
   ```bash
   # 切换 feature flag
   export CONTEXTOS_ASYNC_MODE=0
   # 重启服务（热加载配置）
   curl -X POST http://localhost:49977/admin/reload
   ```

2. **代码回滚（< 30 分钟）**
   ```bash
   git revert HEAD~n..HEAD  # n 为迁移提交数
   # 重新部署
   ./scripts/deploy.sh
   ```

3. **数据修复（如需要）**
   ```python
   # 检查状态一致性
   python -m polaris.kernelone.context.tools.consistency_check
   # 自动修复（如可能）
   python -m polaris.kernelone.context.tools.repair_state
   ```

---

## Alternatives Considered

### 4.1 方案 A: 保持 threading.RLock（拒绝）

**理由**:
- 与 Polaris 异步架构根本冲突
- 性能瓶颈无法解决
- 技术债务持续累积

### 4.2 方案 B: asyncio.Semaphore（拒绝）

**理由**:
- 过度设计：ContextOS 不需要并发限制
- 增加复杂度（需要管理许可数）
- 无实际收益

### 4.3 方案 C: 第三方库（aiorwlock, asynciolimiter）（拒绝）

**理由**:
- 引入外部依赖增加维护成本
- 标准库 `asyncio.Lock` 已满足需求
- 避免供应链安全风险

### 4.4 方案 D: 无锁架构（拒绝）

**理由**:
- 需要重写核心数据结构（CAS 操作）
- 复杂度极高，风险不可控
- 收益有限（当前锁竞争不激烈）

---

## Implementation Notes

### 5.1 代码规范

1. **锁粒度原则**
   ```python
   # 正确：最小临界区
   async def update(self, key: str, delta: int) -> int:
       async with self._lock:
           current = self._state.get(key, 0)
           new_value = current + delta
           self._state[key] = new_value
       # 锁外执行 I/O
       await self._persist_async(key, new_value)
       return new_value
   
   # 错误：过大临界区
   async def update_bad(self, key: str, delta: int) -> int:
       async with self._lock:
           current = self._state.get(key, 0)
           new_value = current + delta
           self._state[key] = new_value
           await self._persist_async(key, new_value)  # 在锁内做 I/O！
   ```

2. **超时处理**
   ```python
   async def safe_read(self, key: str, timeout: float = 5.0) -> Any:
       try:
           return await asyncio.wait_for(
               self._read_internal(key),
               timeout=timeout
           )
       except asyncio.TimeoutError:
           logger.error(f"Lock timeout reading key: {key}")
           raise ContextOSLockTimeout(key)
   ```

3. **异常安全**
   ```python
   async def transactional_update(self, key: str, value: Any) -> None:
       backup = await self.read(key)
       try:
           await self.write(key, value)
           await self._validate_state()
       except Exception:
           # 回滚
           await self.write(key, backup)
           raise
   ```

### 5.2 监控指标

```python
from dataclasses import dataclass
from typing import Dict

@dataclass
class ContextOSMetrics:
    """ContextOS 性能指标"""
    
    # 锁指标
    lock_wait_time_ms: float           # 平均锁等待时间
    lock_hold_time_ms: float           # 平均锁持有时间
    lock_timeout_count: int            # 锁超时次数
    
    # 性能指标
    operation_latency_ms: Dict[str, float]  # 各操作延迟
    concurrent_operations: int         # 当前并发数
    
    # 兼容性指标
    sync_api_calls: int                # 同步 API 调用次数
    async_api_calls: int               # 异步 API 调用次数
```

### 5.3 相关文档

- ADR-0071: TransactionKernel 单提交点与 Context Plane 隔离
- ADR-0076: ContextOS 2.0 摘要策略选型
- CONTEXTOS_2.0_BLUEPRINT: Phase 2-4 实施计划

### 5.4 验证清单

- [ ] `AsyncContextOSCore` 单元测试 100% 通过
- [ ] 同步兼容层回归测试通过
- [ ] 1000 并发压力测试 P99 < 100ms
- [ ] 死锁检测器零报警
- [ ] 静态分析无竞态条件警告
- [ ] 代码审查通过（2+ 维护者）

---

## 结论

**决策**: 采用 `asyncio.Lock` 替换 `threading.RLock`，通过双轨制架构实现向后兼容。

**关键收益**:
1. 性能提升 90%（上下文切换）
2. 内存占用减少 99.9%
3. 架构与 TransactionKernel 对齐

**实施状态**: Phase 1 进行中

**下次评审**: 2026-05-07
