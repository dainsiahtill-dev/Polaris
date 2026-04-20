# 阿卡夏之枢 (Akashic Nexus)

> 多模态分层记忆引擎 - 为 AI/Agent 运行时提供层级化语义感知记忆能力

## 概述

阿卡夏之枢是 KernelOne 的核心记忆管理子系统，实现了：

- **工作记忆 (Working Memory)**: 短期上下文滑动窗口，防 Token 溢出
- **情节记忆 (Episodic Memory)**: Session 级别的对话历史库
- **语义记忆 (Semantic Memory)**: 基于 Vector DB 的长期知识库
- **语义缓存 (Semantic Cache)**: 基于 Embedding 相似度的 LLM 调用拦截层
- **压缩守护 (Compression Daemon)**: 预emptive 后台上下文压缩

## 核心问题解决

| 问题 | 根因 | 解决方案 | 状态 |
|------|------|----------|------|
| Lost in the Middle | 线性追加模式 | `WorkingMemoryWindow` 层次化分块 | ✅ 已实现 |
| 语义缓存真空 | 无缓存层 | `SemanticCacheInterceptor` | ✅ 已实现 |
| 压缩时序错乱 | 被动压缩 | `CompressionDaemon` 预emptive | ✅ 已实现 |
| 记忆层级割裂 | 三套独立系统 | `MemoryManager` 统一调度 | ✅ 已实现 |
| Token 估算偏差 | 粗糙字符估算 | 可注入 `TiktokenEstimator` | 📋 待增强 |

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Akashic Nexus                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Working    │  │  Semantic   │  │  Episodic   │             │
│  │  Memory     │  │  Cache      │  │  Memory     │             │
│  │  Window     │  │ Interceptor │  │  Store      │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │               │                    │
│         └────────────────┼───────────────┘                    │
│                          ▼                                     │
│              ┌─────────────────────┐                          │
│              │   Memory Manager     │  ← 统一 DI 容器          │
│              │   (TierCoordinator)  │                          │
│              └──────────┬──────────┘                          │
│                         │                                      │
│                         ▼                                      │
│              ┌─────────────────────┐                          │
│              │ Compression Daemon  │  ← 预emptive 压缩        │
│              └─────────────────────┘                          │
│                                                              │
│  集成点                                                      │
│  ├── kernelone/memory/*        (Legacy MemoryStore)          │
│  ├── kernelone/context/*       (ContextOS, Compaction)       │
│  └── kernelone/llm/embedding  (KernelEmbeddingPort)          │
└─────────────────────────────────────────────────────────────────┘
```

## 快速开始

```python
from polaris.kernelone.akashic import MemoryManager, WorkingMemoryWindow

# 创建 Manager
manager = MemoryManager(
    working_memory=WorkingMemoryWindow(),
    semantic_cache=SemanticCacheInterceptor(),
)

# 初始化
await manager.initialize()

# 推送消息（自动层次化）
manager.working_memory.push("user", "Fix the login bug")

# 获取状态
snapshot = manager.working_memory.get_snapshot()
print(f"Token使用率: {snapshot.usage_ratio:.1%}")

# 语义缓存调用
result = await manager.semantic_cache.get_or_compute(
    query="How to fix the login?",
    compute_fn=lambda: llm.call("How to fix the login?"),
)

# 关闭
await manager.shutdown()
```

## 模块导航

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 详细架构设计与数据流 |
| [IMPLEMENTATION.md](./IMPLEMENTATION.md) | 8 周分阶段实施计划 |
| [API_REFERENCE.md](./API_REFERENCE.md) | 完整 API 参考 |
| [COGNITIVE_BOTTLENECK_DIAGNOSIS.md](./COGNITIVE_BOTTLENECK_DIAGNOSIS.md) | 问题诊断报告 |

## 代码结构

```
polaris/kernelone/akashic/
├── __init__.py              # 模块入口，导出所有公共 API
├── protocols.py             # Protocol 定义（端口抽象）
├── working_memory.py        # WorkingMemoryWindow 实现
├── semantic_cache.py        # SemanticCacheInterceptor 实现
├── memory_manager.py        # MemoryManager + TierCoordinator
├── compression_daemon.py    # CompressionDaemon 实现
├── integration.py            # DI 工厂函数
└── docs/                    # 本文档目录
    └── INTEGRATION_PLAN.md  # 详细整合计划
```

## 设计原则

1. **DIP (依赖倒置)**: 所有存储后端通过 Protocol/ABC 注入
2. **UTF-8 显式**: 所有文本 I/O 使用 `encoding="utf-8"`
3. **类型安全**: 使用泛型 (TypeVar) 确保类型安全
4. **懒初始化**: 避免循环依赖，按需创建
5. **优雅降级**: 某层不可用时自动回退到 No-Op 实现

## 与现有系统集成

阿卡夏之枢作为增强层，**不替换**现有系统：

| 现有系统 | 集成方式 |
|----------|----------|
| `MemoryStore` | 通过 `_LegacyMemoryStoreAdapter` 适配 |
| `ContextOS` | 作为 Episodic Memory 层集成 |
| `RoleContextCompressor` | CompressionDaemon 调用其实施压缩 |
| `KernelEmbeddingPort` | SemanticCache 依赖注入 |

## 验证命令

```bash
# 导入检查
python -c "from polaris.kernelone.akashic import MemoryManager; print('OK')"

# Ruff 规范
ruff check polaris/kernelone/akashic/

# Mypy 类型
mypy polaris/kernelone/akashic/ --ignore-missing-imports

# 完整验证
pytest polaris/kernelone/akashic/tests/ -v
```

## 状态

- **版本**: 0.1.0
- **实现状态**: Phase 1 Bootstrap 完成
- **下一步**: Phase 2 Memory Enhancement (语义缓存增强)

---

*最后更新: 2026-04-04*
