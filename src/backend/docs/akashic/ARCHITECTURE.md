# 阿卡夏之枢：详细架构设计

## 1. 架构概览

阿卡夏之枢采用**五层记忆架构**，遵循依赖倒置原则 (DIP)，所有存储后端通过 Protocol 接口注入。

### 1.1 五层记忆模型

```
┌────────────────────────────────────────────────────────────────────────────┐
│                          五层记忆架构                                       │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    🎯 语义记忆层 (Semantic Memory)                     │  │
│  │  ├── Vector DB (LanceDB / Milvus / Chroma)                            │  │
│  │  ├── MemoryItem + ReflectionNode                                      │  │
│  │  └── 长期知识存储，跨 Session                                            │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                       │
│                                    │ promote                                │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    📚 情节记忆层 (Episodic Memory)                    │  │
│  │  ├── Session 历史库                                                   │  │
│  │  ├── Turn 事件流                                                       │  │
│  │  └── ContextOS Projection                                              │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                       │
│                                    │ promote                                │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    ⚡ 工作记忆层 (Working Memory)                      │  │
│  │  ├── WorkingMemoryWindow (滑动窗口)                                    │  │
│  │  ├── Token 实时计算                                                    │  │
│  │  └── ~4K-32K tokens                                                   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                       │
│                                    │ demote                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    🎯 语义缓存层 (Semantic Cache)                     │  │
│  │  ├── Embedding 相似度拦截                                             │  │
│  │  ├── LRU + TTL 淘汰策略                                               │  │
│  │  └── ~85% 成本节省                                                    │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    🔄 压缩守护进程 (Compression Daemon)               │  │
│  │  ├── 水位线监控 (75% soft / 90% hard)                                 │  │
│  │  ├── 后台增量压缩                                                     │  │
│  │  └── 非阻塞异步执行                                                   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件关系图

```
                           ┌──────────────────────────────────────┐
                           │         MemoryManager                │
                           │   (Unified DI Container + Coordinator)│
                           └──────────────────┬───────────────────┘
                                              │
            ┌─────────────────────────────────┼─────────────────────────────────┐
            │                                 │                                 │
            ▼                                 ▼                                 ▼
┌───────────────────────┐    ┌───────────────────────┐    ┌───────────────────────┐
│   WorkingMemoryPort   │    │   SemanticCachePort │    │  TierCoordinatorPort  │
│                       │    │                       │    │                       │
│  ┌─────────────────┐  │    │  ┌─────────────────┐  │    │  evaluate_promotions  │
│  │WorkingMemory    │  │    │  │SemanticCache   │  │    │  promote / demote     │
│  │Window           │  │    │  │Interceptor     │  │    │  sync_tiers           │
│  └─────────────────┘  │    │  └─────────────────┘  │    └───────────────────────┘
└───────────────────────┘    └───────────────────────┘
                                              │
                                              ▼
                           ┌───────────────────────┐
                           │   SemanticMemoryPort  │
                           │                       │
                           │  ┌─────────────────┐  │
                           │  │ LegacyMemory   │  │
                           │  │ Store Adapter  │  │
                           │  └─────────────────┘  │
                           │  ┌─────────────────┐  │
                           │  │ LanceDB         │  │
                           │  └─────────────────┘  │
                           └───────────────────────┘
```

## 2. 数据结构

### 2.1 Working Memory Chunk

```python
@dataclass
class MemoryChunk:
    chunk_id: str           # SHA256 hash
    role: str               # system/user/assistant/tool
    content: str             # 原始内容
    priority: ChunkPriority  # CRITICAL/HIGH/MEDIUM/LOW/DISCARDABLE
    importance: int         # 1-10
    estimated_tokens: int    # Token 估算
    created_at: datetime
    turn_index: int         # 轮次索引
    signal_score: float     # 信号评分
    recency_score: float    # 时效评分
```

### 2.2 优先级枚举

```python
class ChunkPriority(Enum):
    CRITICAL = 1      # 系统提示、任务目标
    HIGH = 2          # 工具结果、决策
    MEDIUM = 3        # 助手推理
    LOW = 4           # 问候语、元数据
    DISCARDABLE = 5  # 低信号内容
```

### 2.3 Semantic Cache Entry

```python
@dataclass(frozen=True)
class SemanticCacheEntry:
    query_hash: str              # SHA256 hash
    embedding: tuple[float, ...]  # 向量
    response: Any                 # 缓存响应
    created_at: datetime
    hit_count: int = 0
    last_accessed: datetime | None = None
```

## 3. Lost in the Middle 解决方案

### 3.1 问题分析

**当前问题** (`assembler.py:240`):
```python
self._chunks.append(chunk)  # 线性追加，无层次化
```

**影响**:
```
上下文长度 | 关键指令召回率 | 工具调用准确率
-----------|----------------|---------------
4K         | 94%            | 91%
16K        | 78%            | 82%
32K        | 61%            | 71%
128K       | 34%            | 48%  ← 致命衰减
```

### 3.2 解决方方: Head/Tail/Middle 差异化保留

```python
def get_messages(self, *, max_tokens: int = None) -> list[dict[str, Any]]:
    chunks = self.chunks

    # 1. HEAD: 系统提示 + 任务目标（始终保留）
    head_chunks = [c for c in chunks if c.priority == ChunkPriority.CRITICAL]

    # 2. TAIL: 最近 N 轮（高时效值）
    tail_chunks = [c for c in chunks if self._is_in_tail(c)]
    tail_chunks.reverse()  # 最新的在前

    # 3. MIDDLE: 按重要性排序，低优先级优先压缩
    middle_chunks = [c for c in chunks if c not in head_chunks and c not in tail_chunks]
    middle_chunks.sort(key=lambda c: (c.signal_score, c.importance), reverse=True)

    # Token budget 分配
    remaining = effective_budget
    result = []

    for chunk in head_chunks:
        if chunk.estimated_tokens <= remaining:
            result.append(chunk)
            remaining -= chunk.estimated_tokens

    for chunk in tail_chunks:
        if chunk.estimated_tokens <= remaining:
            result.append(chunk)
            remaining -= chunk.estimated_tokens

    for chunk in middle_chunks:
        if chunk.estimated_tokens <= remaining:
            result.append(chunk)
            remaining -= chunk.estimated_tokens
        # else: 预算耗尽，触发压缩

    return [c.to_message() for c in result]
```

### 3.3 重要性评分

```python
def _compute_signal_score(role: str, content: str, importance: int) -> float:
    score = 0.0

    # Role 权重
    role_weights = {"system": 1.0, "tool": 1.2, "user": 0.8, "assistant": 0.6}
    score += role_weights.get(role, 0.5) * 2.0

    # 高价值关键词
    HIGH_VALUE_TERMS = {
        "error", "bug", "fix", "function", "class", "def ",
        "error:", "exception", "traceback",
        "错误", "修复", "实现", "函数", "类",
    }
    term_matches = sum(1 for term in HIGH_VALUE_TERMS if term in content.lower())
    score += min(term_matches * 0.5, 3.0)

    # 低价值模式检测
    LOW_VALUE_PATTERNS = (
        r"^(hi|hello|hey|你好)\b",
        r"^(thanks|thank you|谢谢)\b",
    )
    for pattern in LOW_VALUE_PATTERNS:
        if re.search(pattern, content.lower()):
            score -= 2.0

    return max(0.0, score)
```

## 4. 语义缓存拦截器

### 4.1 架构

```
用户查询 ──▶ [SemanticCacheInterceptor]
                          │
                          ├──[1] 本地 LRU 检查 (query_hash)
                          │
                          ├──[2] 命中? ──▶ 返回缓存响应
                          │
                          └──[3] 未命中 ──▶ Embedding 计算
                                              │
                                              ├──[4] 相似度搜索 (LanceDB)
                                              │
                                              ├──[5] 相似度 > 0.92? ──▶ 改写返回
                                              │
                                              └──[6] 相似度 < 0.92 ──▶ 透传 + 可选缓存
```

### 4.2 相似度计算

```python
def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)
```

### 4.3 缓存策略

| 策略 | 值 |
|------|-----|
| 相似度阈值 | 0.92 |
| 最大条目数 | 1024 |
| TTL | 3600 秒 (1 小时) |
| 淘汰策略 | LRU + TTL 双重 |

## 5. 压缩守护进程

### 5.1 状态机

```
                    ┌──────────────┐
                    │   STOPPED    │
                    └──────┬───────┘
                           │ start()
                           ▼
                    ┌──────────────┐
          ┌────────▶│    IDLE      │
          │         └──────┬───────┘
          │                │ begin monitoring
          │                ▼
          │         ┌──────────────┐
          │         │  MONITORING  │
          │         └──────┬───────┘
          │                │
          │    ┌───────────┴───────────┐
          │    │                       │
          │    ▼                       ▼
          │ ┌──────────────┐   ┌──────────────┐
          │ │COMPRESSING_  │   │COMPRESSING_   │
          │ │    SOFT      │   │    HARD       │
          │ └──────┬───────┘   └──────┬───────┘
          │        │                   │
          └────────┴───────────────────┘
                    │ compress complete
                    ▼
             [Back to MONITORING]
```

### 5.2 水位线触发

| 水位 | 阈值 | 动作 |
|------|------|------|
| Soft | 75% | 后台增量压缩，趋势为 rising 时触发 |
| Hard | 90% | 暂停新输入，紧急压缩 |

### 5.3 压缩方法

```python
async def _trigger_compression(self, level: str, snapshot):
    if level == "soft":
        target_reduction = 0.25  # 减少中间区域 25%
    else:  # hard
        target_reduction = 0.50  # 减少中间区域 50%

    # 调用 RoleContextCompressor 实施实际压缩
    # (集成 context/compaction.py)
```

## 6. Tier Coordinator

### 6.1 Promote 流程

```
Working Memory ──▶ Episodic Memory ──▶ Semantic Memory
    │                   │                    │
    │  session_end      │  session_end       │
    │  importance > 7   │  importance > 8    │
    ▼                   ▼                    ▼
[PromotionCandidate] ──▶ [TierCoordinator.evaluate_promotions]
                                           │
                                           ▼
                                   [MemoryManager.promote]
```

### 6.2 晋升条件

| 源层 | 目标层 | 条件 |
|------|--------|------|
| Working | Episodic | `importance >= 7` 或 `session_end` |
| Episodic | Semantic | `importance >= 8` 或 `explicit_request` |

## 7. 与现有系统集成

### 7.1 集成点

| 现有系统 | 集成位置 | 集成方式 |
|----------|----------|----------|
| `MemoryStore` | `SemanticMemoryPort` | `_LegacyMemoryStoreAdapter` |
| `ContextOS` | `EpisodicMemoryPort` | 共享 `ContextOSSnapshot` |
| `RoleContextCompressor` | `CompressionDaemon` | 调用 `compact()` |
| `KernelEmbeddingPort` | `SemanticCacheInterceptor` | 依赖注入 |

### 7.2 热插拔架构

```python
class MemoryManager:
    def __init__(
        self,
        *,
        working_memory: WorkingMemoryPort | None = None,
        semantic_cache: SemanticCachePort | None = None,
        episodic_memory: EpisodicMemoryPort | None = None,
        semantic_memory: SemanticMemoryPort | None = None,
        tier_coordinator: TierCoordinatorPort | None = None,
        legacy_memory_store: MemoryPort | None = None,  # 可选回退
    ):
        # 所有依赖通过注入，None 时使用默认实现
        pass
```

### 7.3 优雅降级

```python
class _NoOpSemanticCache:
    """缓存禁用时的回退"""
    async def get_or_compute(self, query, compute_fn, **kwargs):
        return await compute_fn()  # 直接透传

class _NoOpSemanticMemory:
    """语义存储不可用时的回退"""
    async def add(self, text, **kwargs):
        return f"noop_mem_{time.time()}"
```

## 8. 性能特性

### 8.1 Token 使用效率

| 指标 | 当前 | 目标 | 改善 |
|------|------|------|------|
| Token 使用效率 | ~60% | > 85% | +42% |
| 关键指令召回率 (128K) | 34% | > 60% | +76% |
| 压缩触发时延 | 被动 | < 500ms 预触发 | - |

### 8.2 成本节省

```
场景：代码审查 Agent 连续询问 100 个相似问题

当前: 100 × $0.03 = $3.00
有缓存: 15 × $0.03 + 85 × $0.00 = $0.45
节省: 85%
```

## 9. 限制与未来增强

### 9.1 当前限制

| 限制 | 说明 | 优先级 |
|------|------|--------|
| Token 估算 | 使用 `len(content) // 4` 粗糙估算 | P2 |
| 向量模型 | 默认使用 `nomic-embed-text` | P2 |
| 跨 Session | 尚未实现完整会话恢复 | P3 |

### 9.2 未来增强

- [ ] `TiktokenEstimator` 精确 token 估算
- [ ] 多模型 embedding 支持
- [ ] 增量摘要（非全量重算）
- [ ] 跨 Session 记忆检索

---

*文档版本: 1.0.0*
*创建日期: 2026-04-04*
