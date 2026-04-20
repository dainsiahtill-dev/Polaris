# 性能优化实施总结报告

**日期**: 2026-04-13
**任务**: #74 性能优化实施总结
**状态**: 已完成

---

## 执行摘要

本报告总结 2026-04 期间完成的 3 个关键性能优化实施，分别涉及工具执行异步化、上下文缓存系统收敛、以及 TurnEngine 增量执行优化。这些优化显著提升了系统吞吐量、降低了响应延迟、并增强了资源利用率。

| 优化项 | 实施文件 | 主要收益 |
|--------|----------|----------|
| Tool Executor 异步化 | `polaris/cells/roles/kernel/internal/services/tool_executor.py` | 并发执行 + 超时控制 |
| 5层缓存系统 | `polaris/kernelone/context/cache_manager.py` | 缓存命中率提升 |
| TurnEngine 异步工具执行 | `polaris/cells/roles/kernel/internal/turn_engine/engine.py` | Stream/Non-Stream 一致化 |

---

## 1. Tool Executor 异步化

### 1.1 优化背景

**问题**: 原生工具执行采用同步阻塞模式，存在以下问题：
- 批量工具调用串行执行，吞吐量受限
- 无超时控制，单个工具hang死导致整个会话阻塞
- 缺乏错误分类和重试策略

**根因**: `AgentAccelToolExecutor.execute()` 是同步方法，无法利用 asyncio 并发能力。

### 1.2 实现方案

**架构设计**:
```
ToolExecutor Service Layer
├── asyncio.Semaphore(MAX_CONCURRENT_TOOL_EXECUTIONS=5)  # 并发控制
├── execute_single()     # 单工具执行（带信号量）
├── execute_batch()      # 批量并发执行
└── execute_with_fallback()  # 重试机制
```

**核心实现** (`polaris/cells/roles/kernel/internal/services/tool_executor.py`):

```python
class ToolExecutor:
    def __init__(self, ...):
        # 并发控制信号量
        self._concurrency_limit = asyncio.Semaphore(MAX_CONCURRENT_TOOL_EXECUTIONS)

    async def execute_single(self, call: ToolCall, profile: RoleProfile) -> ToolResult:
        async with self._concurrency_limit:
            return await self._execute_single_impl(call, profile)

    async def execute_batch(self, calls: list[ToolCall], profile: RoleProfile) -> list[ToolResult]:
        async def _execute_with_semaphore(call: ToolCall) -> ToolResult:
            async with self._concurrency_limit:
                return await self._execute_single_impl(call, profile)
        tasks = [_execute_with_semaphore(call) for call in calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
```

**超时控制**:
- Director 角色: `DIRECTOR_TIMEOUT_SECONDS` (较长)
- 其他角色: `TIMEOUT_DEFAULT_SECONDS` (60s)
- 使用 `asyncio.wait_for()` 实现超时中断

**错误分类** (`ErrorCategory`):
- `TIMEOUT` - 可重试
- `RATE_LIMIT` - 可重试
- `NETWORK_ERROR` - 可重试
- `AUTHORIZATION` - 不可重试
- `PERMISSION_DENIED` - 不可重试
- `VALIDATION` - 不可重试

### 1.3 性能收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 批量工具执行 | 串行 5 工具 = 5x 单工具耗时 | 并发执行 ≈ max(工具耗时) |
| 超时控制 | 无 | 60s / 300s 分级控制 |
| 并发安全 | 无限制 | Semaphore(5) 上限 |

### 1.4 验证结果

- `execute_single()` 单工具执行路径已验证
- `execute_batch()` 批量并发执行已验证
- 错误分类逻辑已验证
- 资源清理（close/close_sync）已验证

---

## 2. 5层缓存系统 (TieredAssetCacheManager)

### 2.1 优化背景

**问题**: 存在两套独立缓存实现：
- `KernelOneCacheManager` (旧): 3层，路径 `.polaris/cache/`
- `TieredAssetCacheManager` (新): 5层，路径 `.polaris/kernelone_cache/`

**根因**: 历史演进导致双轨缓存，TTL 语义不同（continuity=24h vs projection=2min）。

### 2.2 实现方案

**架构设计** (5层缓存):

```
TieredAssetCacheManager
├── SESSION_CONTINUITY    # 内存 LRU, TTL=1h (continuity pack)
├── REPO_MAP              # 持久化 JSON, TTL=10min
├── SYMBOL_INDEX          # 持久化 JSON, TTL=30min
├── HOT_SLICE             # 内存 LRU, TTL=5min, max=50
└── PROJECTION            # 持久化 JSON, TTL=2min
```

**关键实现** (`polaris/kernelone/context/cache_manager.py`):

```python
class TieredAssetCacheManager:
    async def get(self, key: str, tier: CacheTier) -> Any | None:
        # 分层路由
        if t == CacheTier.HOT_SLICE:
            return await self._get_hot_slice(key)
        elif t == CacheTier.SESSION_CONTINUITY:
            return await self._get_session_continuity(key)
        ...

    async def _get_hot_slice(self, key: str) -> Any | None:
        entry = self._hot_slices.get(key)
        # TTL + mtime 双检失效
        if entry.is_expired():
            return None
        if entry.file_mtime and os.path.getmtime(path) > entry.file_mtime:
            return None  # 文件已修改，失效
```

**Facade 模式收敛** (`polaris/kernelone/context/cache.py`):

```python
class KernelOneCacheManager(TieredAssetCacheManager):
    """向后兼容 facade，保留旧 TTL 语义"""

    def _resolve_cache_root(self) -> Path:
        return Path(self._workspace) / ".polaris" / "cache"  # 旧路径

    def __init__(self, workspace, ...):
        super().__init__(
            workspace,
            hot_slice_max_entries=20,  # 旧限制
            projection_ttl=SESSION_CONTINUITY_TTL_SECONDS,  # 24h
        )
```

### 2.3 性能收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 热数据命中率 | 低（无分层） | 高（HOT_SLICE LRU） |
| 持久化 I/O | 每次读取 | 仅在 cache miss 时 |
| 缓存统计 | 无 | CacheStats 完整追踪 |
| mtime 失效 | 无 | 文件修改自动失效 |

### 2.4 验证结果

- 5层缓存读写路径已验证
- TTL 失效机制已验证
- mtime 变更检测已验证
- Facade 向后兼容已验证

---

## 3. TurnEngine 异步工具执行

### 3.1 优化背景

**问题**: Stream 和 Non-Stream 执行路径不一致
- Stream: 增量执行，工具执行后立即追加到 transcript
- Non-Stream: 批量执行，所有工具执行完才追加

**根因**: `ToolLoopController.append_tool_result()` 未在非流式路径调用。

### 3.2 实现方案

**核心改动** (`polaris/cells/roles/kernel/internal/turn_engine/engine.py`):

```python
async def run(self, request, role, ...):
    while True:
        # ... LLM call ...

        # 增量执行：每个工具执行后立即追加到 transcript
        for call in exec_tool_calls:
            result = await self._execute_single_tool(
                profile=profile,
                request=request,
                call=call,
            )
            # 立即追加结果到 transcript
            _controller.append_tool_result(result, tool_args=getattr(call, "args", None))

        # 循环结束后才检查停止条件
        policy_result = policy.evaluate(...)
        if policy_result.stop_reason:
            return _build_run_result(error=policy_result.stop_reason)
```

**关键优化点**:

1. **增量追加模式**: 工具执行完成后立即调用 `_controller.append_tool_result()`
2. **Round Index 递增**: 在所有路径（包括 content-only）正确递增 `round_index`
3. **max_turns 硬限制**: 双重保险检查

```python
# 硬限制检查（双重保险）
if round_index >= self.config.max_turns:
    logger.debug("[TurnEngine] max_turns exceeded")
    return _build_run_result(error="max_turns_exceeded")

# 每次循环末尾递增
round_index += 1
```

### 3.3 性能收益

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| Stream/Non-Stream 一致性 | 不一致 | 完全一致 |
| 工具结果可见性 | 延迟（批量后） | 即时（增量后） |
| 无限循环防护 | 单一 policy 检查 | policy + 硬限制双重 |
| 上下文修剪时机 | 无 | HALLUCINATION_LOOP 触发时 |

### 3.4 验证结果

- `run()` 非流式增量执行已验证
- `run_stream()` 流式增量执行已验证
- max_turns 硬限制已验证
- 断路器集成已验证

---

## 4. 集成验证

所有 3 项优化均通过以下验证：

```bash
# Ruff 静态检查
ruff check polaris/cells/roles/kernel/internal/services/tool_executor.py
ruff check polaris/kernelone/context/cache_manager.py
ruff check polaris/cells/roles/kernel/internal/turn_engine/engine.py

# Mypy 类型检查
mypy polaris/cells/roles/kernel/internal/services/tool_executor.py
mypy polaris/kernelone/context/cache_manager.py
mypy polaris/cells/roles/kernel/internal/turn_engine/engine.py

# 单元测试
pytest polaris/kernelone/context/tests/test_cache_manager.py -v
pytest polaris/kernelone/context/tests/test_tiered_cache.py -v
```

---

## 5. 后续建议

1. **Tool Executor**: 考虑添加熔断器模式，防止下游服务故障扩散
2. **缓存系统**: 监控 HOT_SLICE 命中率，动态调整 max_entries
3. **TurnEngine**: 评估 `round_index >= max_turns` 硬限制是否可泛化为通用 BudgetState

---

## 6. 参考文档

- `docs/blueprints/TOP6_CRITICAL_FIXES_20260401.md`
- `docs/blueprints/CACHE_SYSTEM_CONVERGENCE_PLAN_20260404.md`
- `docs/blueprints/STREAM_NONSTREAM_PARITY_FIX_20260329.md`
- `polaris/kernelone/context/cache_manager.py` (TieredAssetCacheManager)
- `polaris/kernelone/context/cache.py` (KernelOneCacheManager facade)
- `polaris/cells/roles/kernel/internal/services/tool_executor.py` (ToolExecutor)
- `polaris/cells/roles/kernel/internal/turn_engine/engine.py` (TurnEngine)
