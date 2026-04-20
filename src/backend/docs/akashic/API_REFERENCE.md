# 阿卡夏之枢：API 参考

## 模块导入

```python
from polaris.kernelone.akashic import (
    # Core
    MemoryManager,
    MemoryManagerConfig,
    MemoryManagerPort,
    # Working Memory
    WorkingMemoryWindow,
    WorkingMemoryPort,
    WorkingMemoryConfig,
    WorkingMemorySnapshot,
    ChunkPriority,
    MemoryChunk,
    # Semantic Cache
    SemanticCacheInterceptor,
    SemanticCachePort,
    SemanticCacheConfig,
    SemanticCacheEntry,
    # Compression Daemon
    CompressionDaemon,
    DaemonConfig,
    DaemonState,
    CompressionStats,
    # Ports
    TierCoordinatorPort,
    EpisodicMemoryPort,
    SemanticMemoryPort,
    # Types
    PromotionCandidate,
    DemotionCandidate,
)
```

## MemoryManager

### 类定义

```python
class MemoryManager:
    """Unified Memory Manager with DI support.

    Coordinates WorkingMemory, SemanticCache, EpisodicMemory, and SemanticMemory
    through a single interface.
    """
```

### 构造函数

```python
def __init__(
    self,
    config: MemoryManagerConfig | None = None,
    *,
    working_memory: WorkingMemoryPort | None = None,
    semantic_cache: SemanticCachePort | None = None,
    episodic_memory: EpisodicMemoryPort | None = None,
    semantic_memory: SemanticMemoryPort | None = None,
    tier_coordinator: TierCoordinatorPort | None = None,
    legacy_memory_store: MemoryPort | None = None,
) -> None:
    """Create a MemoryManager with optional DI.

    All port implementations are optional. If not provided,
    default implementations are lazily created.
    """
```

### 属性

```python
@property
def working_memory(self) -> WorkingMemoryPort:
    """Get the working memory port (short-term context window)."""

@property
def semantic_cache(self) -> SemanticCachePort:
    """Get the semantic cache port (LLM call caching)."""

@property
def episodic_memory(self) -> EpisodicMemoryPort:
    """Get the episodic memory port (session-level history)."""

@property
def semantic_memory(self) -> SemanticMemoryPort:
    """Get the semantic memory port (long-term vector storage)."""

@property
def tier_coordinator(self) -> TierCoordinatorPort:
    """Get the tier coordinator port (cross-tier orchestration)."""
```

### 方法

```python
async def initialize(self) -> None:
    """Initialize the memory manager and all sub-systems."""

async def shutdown(self) -> None:
    """Gracefully shutdown the memory manager."""

def get_status(self) -> dict[str, Any]:
    """Get comprehensive status of all memory tiers.

    Returns:
        dict with keys:
        - initialized: bool
        - shutdown: bool
        - config: dict
        - tiers: dict with per-tier status
        - healthy: bool
    """
```

### MemoryManagerConfig

```python
@dataclass
class MemoryManagerConfig:
    enable_semantic_cache: bool = True
    enable_episodic_promotion: bool = True
    enable_tier_sync: bool = True
    promotion_importance_threshold: int = 7  # Min importance to promote
    sync_interval_seconds: float = 60.0
```

---

## WorkingMemoryWindow

### 类定义

```python
class WorkingMemoryWindow:
    """Hierarchical working memory window with differentiated preservation.

    Solves "Lost in the Middle" by using Head/Tail/Middle preservation.
    """
```

### 构造函数

```python
def __init__(
    self,
    config: WorkingMemoryConfig | None = None,
    *,
    token_estimator: Any = None,
) -> None:
    """Create a WorkingMemoryWindow.

    Args:
        config: Optional configuration. Defaults to WorkingMemoryConfig().
        token_estimator: Optional token estimator (TokenEstimatorProtocol).
    """
```

### 配置

```python
@dataclass
class WorkingMemoryConfig:
    max_tokens: int = 32_000
    soft_watermark_pct: float = 0.75
    hard_watermark_pct: float = 0.90
    head_preserve_tokens: int = 8_000
    tail_preserve_count: int = 3
    middle_compress_enabled: bool = True
```

### 方法

```python
def push(
    self,
    role: str,
    content: str,
    *,
    importance: int = 5,
    turn_index: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Push a message into the working memory window.

    Args:
        role: Message role (system/user/assistant/tool)
        content: Message content
        importance: Importance score 1-10
        turn_index: Optional turn index
        metadata: Optional metadata dict

    Returns:
        The chunk_id of the inserted chunk.
    """

def get_snapshot(self) -> WorkingMemorySnapshot:
    """Get current working memory state snapshot."""

def get_messages(
    self,
    *,
    max_tokens: int | None = None,
    include_role: str | None = None,
) -> list[dict[str, Any]]:
    """Get messages from working memory with hierarchical preservation.

    Returns messages ordered by: HEAD (critical) → TAIL (recent) → MIDDLE (importance)
    """

def promote_to_episodic(self, item_id: str, reason: str) -> bool:
    """Promote an item from working to episodic memory.

    Args:
        item_id: Chunk ID to promote
        reason: Reason for promotion

    Returns:
        True if item was found and queued.
    """

def clear(self) -> None:
    """Clear the entire working memory window."""

def reset_turn(self) -> None:
    """Increment turn counter (call at end of each turn)."""
```

### WorkingMemorySnapshot

```python
@dataclass
class WorkingMemorySnapshot:
    total_tokens: int
    chunk_count: int
    head_tokens: int
    middle_tokens: int
    tail_tokens: int
    usage_ratio: float  # total_tokens / max_tokens
    compression_triggered: str | None  # None | "soft" | "hard"
```

### ChunkPriority

```python
class ChunkPriority(Enum):
    CRITICAL = 1  # System prompt, task goal
    HIGH = 2     # Tool results, decisions
    MEDIUM = 3   # Assistant reasoning
    LOW = 4      # Greetings, meta chatter
    DISCARDABLE = 5
```

### MemoryChunk

```python
@dataclass
class MemoryChunk:
    chunk_id: str
    role: str
    content: str
    priority: ChunkPriority
    importance: int  # 1-10
    estimated_tokens: int
    created_at: datetime
    turn_index: int
    metadata: dict[str, Any]
    signal_score: float
    recency_score: float

    def to_message(self) -> dict[str, Any]:
        """Convert to chat message format."""
```

---

## SemanticCacheInterceptor

### 类定义

```python
class SemanticCacheInterceptor:
    """Embedding-based semantic cache for LLM calls.

    Features:
    - Exact match via content hash
    - Near-duplicate detection via embedding similarity
    - LRU + TTL dual eviction
    - Thread-safe operations
    """
```

### 构造函数

```python
def __init__(
    self,
    config: SemanticCacheConfig | None = None,
    embedding_port: Any = None,
) -> None:
    """Create a SemanticCacheInterceptor.

    Args:
        config: Optional configuration. Defaults to SemanticCacheConfig().
        embedding_port: Optional embedding port (KernelEmbeddingPort).
    """
```

### 配置

```python
@dataclass
class SemanticCacheConfig:
    similarity_threshold: float = 0.92
    max_entries: int = 1024
    ttl_seconds: float = 3600.0
    embedding_model: str | None = None
```

### 方法

```python
async def get_or_compute(
    self,
    query: str,
    compute_fn: Callable[[], T],
    *,
    ttl_seconds: float | None = None,
) -> T:
    """Get cached response or compute and cache a new one.

    Args:
        query: The query string to cache
        compute_fn: Async function to call if cache miss
        ttl_seconds: Optional TTL override

    Returns:
        The cached or newly computed response.
    """

async def invalidate(self, query_hash: str) -> bool:
    """Invalidate a cache entry by hash.

    Returns:
        True if entry existed and was removed.
    """

async def clear(self) -> int:
    """Clear all cache entries.

    Returns:
        The number of entries cleared.
    """

def get_stats(self) -> dict[str, Any]:
    """Get comprehensive cache statistics.

    Returns:
        dict with keys:
        - size: int
        - max_size: int
        - hits: int
        - misses: int
        - exact_hits: int
        - similarity_hits: int
        - evictions: int
        - total_requests: int
        - hit_rate: float
        - similarity_threshold: float
    """
```

---

## CompressionDaemon

### 类定义

```python
class CompressionDaemon:
    """Background daemon for preemptive context compression.

    Monitors memory usage and triggers background compression
    before token budget is exhausted.
    """
```

### 构造函数

```python
def __init__(
    self,
    memory_manager: MemoryManager,
    config: DaemonConfig | None = None,
) -> None:
    """Create a CompressionDaemon.

    Args:
        memory_manager: The MemoryManager to monitor.
        config: Optional configuration. Defaults to DaemonConfig().
    """
```

### 配置

```python
@dataclass
class DaemonConfig:
    check_interval_ms: int = 500
    soft_watermark_pct: float = 0.75
    hard_watermark_pct: float = 0.90
    max_concurrent_compressions: int = 2
    compression_timeout_seconds: float = 30.0
    enable_incremental: bool = True
    min_tokens_to_compress: int = 1000
```

### 方法

```python
async def start(self) -> None:
    """Start the compression daemon."""

async def stop(self) -> None:
    """Stop the compression daemon gracefully."""

def get_status(self) -> dict[str, Any]:
    """Get daemon status for monitoring.

    Returns:
        dict with keys:
        - state: str (STOPPED/IDLE/MONITORING/COMPRESSING_SOFT/COMPRESSING_HARD)
        - usage_trend: str (stable/rising/falling)
        - last_usage_ratio: float
        - active_compressions: int
        - max_concurrent: int
        - stats: CompressionStats
    """
```

### DaemonState

```python
class DaemonState(Enum):
    STOPPED = "stopped"
    IDLE = "idle"
    MONITORING = "monitoring"
    COMPRESSING_SOFT = "compressing_soft"
    COMPRESSING_HARD = "compressing_hard"
    STOPPING = "stopping"
```

---

## 协议接口 (Protocols)

### WorkingMemoryPort

```python
@runtime_checkable
class WorkingMemoryPort(Protocol):
    def push(
        self,
        role: str,
        content: str,
        *,
        importance: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    def get_snapshot(self) -> WorkingMemorySnapshot: ...

    def get_messages(
        self,
        *,
        max_tokens: int | None = None,
        include_role: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def promote_to_episodic(self, item_id: str, reason: str) -> bool: ...

    def clear(self) -> None: ...
```

### SemanticCachePort

```python
@runtime_checkable
class SemanticCachePort(Protocol):
    async def get_or_compute(
        self,
        query: str,
        compute_fn: Callable[[], Any],
        *,
        ttl_seconds: float | None = None,
    ) -> Any: ...

    async def invalidate(self, query_hash: str) -> bool: ...

    async def clear(self) -> int: ...

    def get_stats(self) -> dict[str, Any]: ...
```

### TierCoordinatorPort

```python
@runtime_checkable
class TierCoordinatorPort(Protocol):
    async def evaluate_promotions(
        self,
        candidates: list[PromotionCandidate],
    ) -> list[PromotionCandidate]: ...

    async def promote(self, candidate: PromotionCandidate) -> bool: ...

    async def demote(self, candidate: DemotionCandidate) -> bool: ...

    async def sync_tiers(self) -> dict[str, int]: ...
```

---

## 类型定义

### PromotionCandidate

```python
@dataclass(frozen=True)
class PromotionCandidate:
    item_id: str
    source_tier: str
    target_tier: str
    importance: int
    text_preview: str  # First 100 chars
    reason: str  # session_end | importance_threshold | explicit_request
```

### DemotionCandidate

```python
@dataclass(frozen=True)
class DemotionCandidate:
    item_id: str
    source_tier: str
    target_tier: str
    reason: str  # token_budget | staleness | explicit_request
```

---

## 使用示例

### 基本使用

```python
from polaris.kernelone.akashic import MemoryManager, WorkingMemoryWindow

manager = MemoryManager(
    working_memory=WorkingMemoryWindow(),
)

await manager.initialize()

# Push messages
manager.working_memory.push("system", "You are a coding assistant.")
manager.working_memory.push("user", "Fix the login bug in auth.py")
manager.working_memory.push("assistant", "I'll fix the bug...")

# Get snapshot
snapshot = manager.working_memory.get_snapshot()
print(f"Tokens: {snapshot.total_tokens}/{snapshot.total_tokens}")
print(f"Usage: {snapshot.usage_ratio:.1%}")

# Get messages within token budget
messages = manager.working_memory.get_messages(max_tokens=16000)
for msg in messages:
    print(f"{msg['role']}: {msg['content'][:50]}...")

await manager.shutdown()
```

### 语义缓存

```python
from polaris.kernelone.akashic import SemanticCacheInterceptor

cache = SemanticCacheInterceptor()

async def expensive_computation():
    # This would be an LLM call
    return await llm.generate("How to fix the login bug?")

# First call - cache miss
result1 = await cache.get_or_compute(
    query="How to fix the login bug in auth.py?",
    compute_fn=expensive_computation,
)

# Second call with similar query - cache hit!
result2 = await cache.get_or_compute(
    query="Fix the login bug",
    compute_fn=expensive_computation,
)  # Returns cached result

# Check stats
stats = cache.get_stats()
print(f"Hit rate: {stats['hit_rate']:.1%}")
```

### 压缩守护进程

```python
from polaris.kernelone.akashic import CompressionDaemon, DaemonConfig

daemon = CompressionDaemon(
    memory_manager=manager,
    config=DaemonConfig(
        check_interval_ms=500,
        soft_watermark_pct=0.75,
        hard_watermark_pct=0.90,
    ),
)

await daemon.start()

# Daemon runs in background, monitoring and compressing
# ...

await daemon.stop()
```

---

*文档版本: 1.0.0*
*创建日期: 2026-04-04*
