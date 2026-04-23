# 统一并发管理架构设计 (Unified Concurrency Management)

**版本**: 1.0
**日期**: 2026-04-24
**状态**: Draft

---

## 1. 背景与问题陈述

### 1.1 现状问题

项目中存在**多处独立创建 ThreadPoolExecutor** 的碎片化现象：

| 模块 | 创建位置 | max_workers | 用途 |
|------|---------|-------------|------|
| LLM HTTP 请求 | `provider_helpers.py` | 32 | 阻塞 HTTP I/O |
| Sleep 操作 | `provider_helpers.py` | 4 | 睡眠卸载 |
| KernelOne Blocking IO | `execution_runtime.py` | 8 (可配) | 通用阻塞 I/O |
| ContextOS CPU | `context_os/runtime.py` | 4 | CPU 密集计算 |
| 代码索引 | `code_intelligence_async.py` | 2 | 索引构建 |
| 散点临时池 | 多个文件 | 不限/1 | 各模块私有 |

**核心问题**:
1. 无统一入口，无法管控全局线程资源
2. `ExecutionRuntime` 未被其他模块复用
3. `WorkerPool` 是 Sync/Async 平行实现，非统一抽象
4. 部分 ThreadPoolExecutor 无 max_workers 限制

### 1.2 风险评估

| 风险 | 等级 | 描述 |
|------|------|------|
| 线程数失控 | **高** | 无限制的 ThreadPoolExecutor 可能创建过多线程 |
| 资源泄漏 | **中** | 局部 `with ThreadPoolExecutor()` 未显式 shutdown |
| 跨模块竞争 | **中** | 多模块独立创建池，共享 CPU 资源竞争 |
| 死锁风险 | **高** | threading/asyncio 混用点存在 |

---

## 2. 架构设计

### 2.1 核心组件

```
polaris/kernelone/concurrency/
├── __init__.py
├── manager.py              # UnifiedConcurrencyManager (核心)
├── pools.py                # 线程池/进程池定义
├── protocol.py             # 协议抽象
└── integration.py          # 与现有模块的集成适配器
```

### 2.2 UnifiedConcurrencyManager

**职责**:
- 作为 per-event-loop 单例，统一管理所有线程池/进程池
- 提供 `get_io_pool()`, `get_cpu_pool()`, `get_process_pool()` 统一接口
- 自动复用已创建的池，避免重复创建
- 提供健康检查和 metrics

**设计要点**:
```python
class UnifiedConcurrencyManager:
    """Per-event-loop 单例，按类型复用线程池"""

    _instance: WeakKeyDictionary[asyncio.AbstractEventLoop, UnifiedConcurrencyManager] = WeakKeyDictionary()

    def get_io_pool(self, max_workers: int = 32) -> ThreadPoolExecutor:
        """获取 I/O 密集型线程池（阻塞 HTTP、文件 I/O 等）"""

    def get_cpu_pool(self, max_workers: int | None = None) -> ThreadPoolExecutor:
        """获取 CPU 密集型线程池（自动按 CPU 核心数）"""

    def get_process_pool(self, max_workers: int | None = None) -> ProcessPoolExecutor:
        """获取多进程池（计算密集型任务）"""
```

### 2.3 池类型定义

| 池类型 | 用途 | 典型 max_workers | 生命周期 |
|--------|------|-----------------|----------|
| `IOThreadPool` | HTTP 请求、文件读写、网络 I/O | 32 | 应用级 |
| `CPUThreadPool` | CPU 密集计算 | CPU核心数 | 应用级 |
| `ProcessPool` | 多进程并行计算 | CPU核心数 | 应用级 |

### 2.4 集成策略

**Phase 1 - 统一入口**:
1. 创建 `UnifiedConcurrencyManager`
2. 为 `provider_helpers.py` 的 `_BLOCKING_HTTP_POOL` 和 `_SLEEP_POOL` 提供统一接口
3. 为 `execution_runtime.py` 的 `_blocking_executor` 提供统一接口

**Phase 2 - 迁移**:
1. 将散点 `with ThreadPoolExecutor()` 调用迁移到统一管理器
2. 为无限制的 ThreadPoolExecutor 添加 `max_workers` 限制

**Phase 3 - 治理**:
1. 移除重复的本地 ThreadPoolExecutor 创建
2. 统一通过管理器获取池

---

## 3. 核心数据流

```
                    ┌──────────────────────────────────┐
                    │  UnifiedConcurrencyManager       │
                    │  (per-loop singleton)             │
                    └──────────────────────────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          ▼                         ▼                         ▼
   ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
   │  _io_pools    │         │  _cpu_pools  │         │ _proc_pools  │
   │  Dict[int,   │         │  Dict[int,   │         │  Dict[int,   │
   │   ThreadPool] │         │   ThreadPool]│         │   ProcessPool│
   └──────────────┘         └──────────────┘         └──────────────┘
          │                         │                         │
          └─────────────────────────┼─────────────────────────┘
                                    │
                                    ▼
                         get_pool(max_workers)
                                    │
                    ┌───────────────┴───────────────┐
                    │ 如果池存在且 max_workers 匹配  │
                    │   → 直接返回                  │
                    │ 否则创建新池并存入字典          │
                    └───────────────────────────────┘
```

---

## 4. 技术选型理由

### 4.1 为什么使用 WeakKeyDictionary 存储单例？

- key 是 `asyncio.AbstractEventLoop`
- 当 event loop 被垃圾回收时，对应的 manager 也被回收
- 避免在多 event loop 场景下不同 loop 使用同一个 manager（可能导致问题）

### 4.2 为什么池按 max_workers 分类存储？

- 不同调用方可能需要不同大小的池
- 按 max_workers 作为 key 避免重复创建相同配置的池
- 后续可扩展为命名池，支持更细粒度管控

### 4.3 为什么不直接复用 ExecutionRuntime？

- ExecutionRuntime 设计为执行引擎，职责是"运行任务"而非"管理池"
- 它内部创建了 `_blocking_executor`，但没有暴露统一获取接口
- 统一管理器可以作为更高层的抽象，整合多个底层池

---

## 5. 模块职责划分

| 文件 | 职责 | 公共接口 |
|------|------|----------|
| `manager.py` | 统一管理器单例 | `get_io_pool()`, `get_cpu_pool()`, `get_process_pool()`, `shutdown_all()` |
| `pools.py` | 池配置和工厂函数 | `create_io_pool()`, `create_cpu_pool()`, `create_process_pool()` |
| `protocol.py` | 并发抽象协议 | `ConcurrencyPool` protocol |
| `integration.py` | 兼容层适配器 | `install()` 替换现有调用 |

---

## 6. 修复优先级

| 优先级 | 问题 | 修复方案 |
|--------|------|----------|
| **P0** | 无限制 ThreadPoolExecutor | 在 manager 中强制设置 max_workers |
| **P0** | threading/asyncio 混用 | 通过统一管理器消除跨边界调用 |
| **P1** | 散点 executor 创建 | 迁移到 `UnifiedConcurrencyManager` |
| **P2** | WorkerPool 平行实现 | 设计统一抽象基类 |

---

## 7. 后续优化方向

1. **监控集成**: 添加 metrics 暴露全局池使用情况
2. **动态调参**: 根据负载动态调整 max_workers
3. **健康检查**: 提供池的运行状态检查接口
4. **优雅关闭**: 统一管理所有池的 shutdown 时机

---

## 8. 影响范围

### 需要修改的文件
1. `polaris/infrastructure/llm/providers/provider_helpers.py` - 使用统一管理器
2. `polaris/kernelone/runtime/execution_runtime.py` - 使用统一管理器
3. `polaris/kernelone/context/context_os/runtime.py` - 使用统一管理器
4. `polaris/infrastructure/code_intelligence/code_intelligence_async.py` - 使用统一管理器

### 需要新增的文件
1. `polaris/kernelone/concurrency/manager.py`
2. `polaris/kernelone/concurrency/pools.py`
3. `polaris/kernelone/concurrency/protocol.py`
4. `polaris/kernelone/concurrency/integration.py`

### 需要修复的高风险文件
1. `polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py:314`
2. `polaris/cells/runtime/artifact_store/internal/artifacts.py:685`
3. `polaris/infrastructure/realtime/process_local/signal_hub.py`