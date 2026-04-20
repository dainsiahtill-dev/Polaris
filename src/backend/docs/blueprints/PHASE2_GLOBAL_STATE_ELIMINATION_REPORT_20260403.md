# Phase 2: 全局状态消除 - 执行报告

**日期**: 2026-04-03
**阶段**: Phase 2 全局状态消除
**状态**: 已完成

---

## 1. 全局状态完整清单

### P0 高优先级 (测试污染最严重)

| # | 全局变量 | 文件 | 用途 | reset 函数 | 状态 |
|---|---------|------|------|-----------|------|
| 1 | `_global_llm_semaphore` | kernelone/llm/engine/executor.py:60 | asyncio.Semaphore | `reset_llm_semaphore()` | 已存在 |
| 2 | `_executor_manager` | kernelone/llm/engine/executor.py:598 | WorkspaceExecutorManager | `reset_executor_manager()` | 已存在 |
| 3 | `_default_adapter` (fs) | kernelone/fs/registry.py:13 | KernelFileSystemAdapter | `reset_default_adapter()` | **新增** |
| 4 | `_default_embedding_port` | kernelone/llm/embedding.py:20 | KernelEmbeddingPort | `reset_default_embedding_port()` | **新增** |
| 5 | `_default_registry` | kernelone/events/typed/registry.py:542 | EventRegistry | `reset_default_registry()` | **新增** |

### P1 中优先级

| # | 全局变量 | 文件 | 用途 | reset 函数 | 状态 |
|---|---------|------|------|-----------|------|
| 6 | `_global_bus` | kernelone/events/registry.py:8 | MessageBus | `reset_global_bus()` | **新增** |
| 7 | `_default_adapter` (typed) | kernelone/events/typed/bus_adapter.py:496 | TypedEventBusAdapter | `reset_default_adapter()` | **新增** |
| 8 | `_default_config` | cells/roles/kernel/public/config.py:124 | KernelConfig | - | 待处理 |
| 9 | `_default_cache` | cells/runtime/projection/internal/runtime_projection_service.py:683 | ProjectionCache | - | 待处理 |
| 10 | `_default_reports_port` | cells/llm/evaluation/internal/index.py:86 | KernelFsReportsPort | - | 待处理 |

### P2 低优先级 (外围系统)

还包括以下模块的全局状态 (约 40+ 个):
- NATS client (`infrastructure/messaging/nats/client.py`)
- Ollama adapter (`kernelone/process/ollama_utils.py`)
- Console (`delivery/cli/visualization/rich_console.py`)
- Tracer (`cells/events/fact_stream/internal/debug_trace.py`)
- 各种 Registry (WorkflowRegistry, ActivityRegistry, CircuitBreakerRegistry 等)

---

## 2. DIContainerScope 设计方案

### 设计原则

1. **线程/异步安全**: 通过 `contextvars` 实现 scope 管理
2. **自动注册跟踪**: 跟踪所有 scoped singletons
3. **统一清理接口**: 通过 `cleanup_scope()` 清理所有注册
4. **向后兼容**: 支持现有全局状态模式

### 核心接口

```python
class DIContainerScope:
    """DI Container Scope with test isolation support."""

    def register_singleton(
        self,
        interface: type[T],
        factory: Callable[[], T],
    ) -> None: ...

    def register_async_singleton(
        self,
        interface: type[T],
        factory: Callable[[], T],
    ) -> None: ...

    def register_instance(
        self,
        interface: type[T],
        instance: T,
    ) -> None: ...

    def resolve(self, interface: type[T]) -> T: ...

    async def resolve_async(self, interface: type[T]) -> T: ...

    def cleanup_scope(self) -> None: ...
```

### 配套功能

- `ScopeContext`: 异步上下文管理器
- `cleanup_all_scopes()`: 全局清理所有 scope
- `register_resetter()`: 注册全局状态重置函数
- `reset_all_global_state()`: 重置所有注册的全局状态

---

## 3. 已实施的消除/重置修复清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `polaris/infrastructure/di/scope.py` | DIContainerScope 实现 |
| `polaris/infrastructure/di/tests/test_scope.py` | 20 个单元测试 |
| `polaris/infrastructure/di/tests/__init__.py` | 测试包初始化 |

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `polaris/kernelone/fs/registry.py` | 新增 `reset_default_adapter()` |
| `polaris/kernelone/llm/embedding.py` | 新增 `reset_default_embedding_port()` |
| `polaris/kernelone/events/typed/registry.py` | 新增 `reset_default_registry()` |
| `polaris/kernelone/events/registry.py` | 新增 `reset_global_bus()` |
| `polaris/kernelone/events/typed/bus_adapter.py` | 新增 `reset_default_adapter()` |

---

## 4. 质量验证

### Ruff 检查
```
All checks passed!
```

### MyPy 检查
```
Success: no issues found in 1 source file
```

### Pytest 测试
```
======================== 20 passed, 1 warning in 0.69s ========================
```

---

## 5. 后续建议

### 待完成的 P1 优先级任务

1. **cells/roles/kernel/public/config.py**: 为 `_default_config` 添加 reset 函数
2. **cells/runtime/projection/internal/runtime_projection_service.py**: 为 `_default_cache` 添加 reset 函数
3. **cells/llm/evaluation/internal/index.py**: 为 `_default_reports_port` 添加 reset 函数

### 长期优化方向

1. **统一全局状态管理**: 将所有 `_default_*` 和 `_global_*` 统一迁移到 DIContainerScope
2. **ContextVar 集成**: 将现有 `contextvars` 集成到 kernelone 的核心模块
3. **测试 fixture 标准化**: 创建标准的 `autouse` fixture 来自动清理全局状态

### 架构改进建议

```python
# 建议的测试 fixture 模板
@pytest.fixture(autouse=True)
def clean_kernelone_globals():
    """Clean all kernelone global state before each test."""
    from polaris.infrastructure.di.scope import cleanup_all_scopes
    from polaris.kernelone.llm.engine import reset_executor_manager, reset_llm_semaphore
    from polaris.kernelone.fs import reset_default_adapter
    from polaris.kernelone.llm import reset_default_embedding_port
    from polaris.kernelone.events.typed import reset_default_registry
    from polaris.kernelone.events import reset_global_bus

    # Reset all
    cleanup_all_scopes()
    reset_executor_manager()
    reset_llm_semaphore()
    reset_default_adapter()
    reset_default_embedding_port()
    reset_default_registry()
    reset_global_bus()

    yield

    # Cleanup after
    cleanup_all_scopes()
    reset_executor_manager()
```

---

## 6. 文件位置索引

### 核心实现
- `polaris/infrastructure/di/scope.py` - DIContainerScope 实现

### 测试文件
- `polaris/infrastructure/di/tests/test_scope.py` - 单元测试
- `polaris/infrastructure/di/tests/__init__.py` - 包初始化

### 新增 reset 函数的文件
- `polaris/kernelone/fs/registry.py`
- `polaris/kernelone/llm/embedding.py`
- `polaris/kernelone/events/typed/registry.py`
- `polaris/kernelone/events/registry.py`
- `polaris/kernelone/events/typed/bus_adapter.py`

### 已有 reset 函数的关键文件 (无需修改)
- `polaris/kernelone/llm/engine/executor.py` - `reset_executor_manager()`, `reset_llm_semaphore()`
- `polaris/infrastructure/di/container.py` - `reset_container()`
- `polaris/kernelone/context/strategy_registry.py` - `reset_instance()`
