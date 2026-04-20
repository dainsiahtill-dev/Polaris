# 真理熔炉（Truth Crucible）知识清洗管道重构蓝图
**日期**: 2026-04-04
**状态**: 草稿 → 待委员会审批
**架构师**: Python 架构十人委员会

---

## 1. 现状诊断（Truth Diagnosis）

### 1.1 语义割裂与脏数据问题

| 问题模块 | 缺陷描述 | 影响等级 | 根因 |
|---------|---------|---------|------|
| `lancedb_code_search.py:_chunk_file()` | 固定 80 行分块在函数中间切断，丢失类/模块级上下文；10 行重叠无法解决跨 chunk 语义依赖 | **HIGH** | 无 NLP 语义感知 |
| `AkashicSemanticMemory.delete()` | 仅从 `self._items` 内存删除，JSONL 文件不重写；`_load()` 重启后复活幽灵数据 | **CRITICAL** | 软删除机制缺失 |
| `AkashicSemanticMemory.add()` | 每次 append 写入 JSONL，无 content hash 查重；重复 add 产生重复记录 | **HIGH** | 幂等性缺失 |
| `LanceDbCodeSearch.search_code()` | 使用 pandas 字符串过滤而非向量搜索；尽管使用 LanceDB 但未发挥向量检索能力 | **MEDIUM** | 架构退化 |
| `TierCoordinator.sync_tiers()` | `promote_many()` 可能重复 promotion 同一条目（无幂等保护） | **MEDIUM** | 去重机制缺失 |

### 1.2 现有能力映射

```
kernelone/akashic/           ✅ 完整多层记忆架构（已验证存在）
├── protocols.py             ✅ SemanticMemoryPort + AVAILABLE_EMBEDDING_MODELS
├── memory_manager.py        ✅ TierCoordinator + asyncio.gather() 并行 promotion
├── semantic_memory.py       ⚠️  JSONL 持久化（幽灵数据问题）
├── semantic_cache.py        ✅ cosine similarity + LRU/TTL 双驱逐
├── working_memory.py        ✅ ChunkPriority 分级 + micro_compact
├── episodic_memory.py        ✅ turn/episode JSONL 存储
└── compression_daemon.py    ✅ 背景压缩调度

kernelone/context/compaction.py  ✅ _continuity_signal_score() 信号词评分（对话压缩，非文档）
kernelone/llm/embedding.py       ✅ KernelEmbeddingPort ABC + 全局单例
infrastructure/llm/adapters/local_embedding_adapter.py ✅ sentence-transformers 实现
infrastructure/db/repositories/lancedb_code_search.py  ⚠️  固定行分块 + 文本搜索
kernelone/workflow/engine.py    ✅ DAG + asyncio.wait() 并行调度
```

### 1.3 能力缺口矩阵

| 需求 | 现有实现 | 缺口 |
|-----|---------|-----|
| 多模态文档提取（PDF/Word/Markdown → text） | 无 | **全部缺失** |
| 语义分块（段落/意图级别） | 无 | **全部缺失** |
| Content hash 幂等去重 | 无 | **全部缺失** |
| 软删除机制（非内存删除） | 无 | **全部缺失** |
| 向量数据库同步 | LanceDB 已集成但未用向量搜索 | **部分缺失** |
| DAG 可视化管道编排 | workflow engine 可复用 | **需扩展** |

---

## 2. 架构蓝图（Architectural Blueprint）

### 2.1 整体数据流

```
DocumentInput
     │
     ▼
┌─────────────────┐
│   EXTRACTOR     │ ← 多模态文档解析（PDF/Word/Markdown → PlainText）
│   (Node 1)      │
└────────┬────────┘
         │ DocumentChunk[]
         ▼
┌─────────────────┐
│ SEMANTIC_CHUNKER│ ← 信号词评分 + 段落边界检测 + 意图切片
│   (Node 2)      │
└────────┬────────┘
         │ SemanticChunk[]
         ▼
┌─────────────────┐
│METADATA_ENRICHER│ ← importance score + content hash + source tracking
│   (Node 3)      │
└────────┬────────┘
         │ EnrichedChunk[]
         ▼
┌─────────────────┐
│EMBEDDING_COMPUTER│ ← KernelEmbeddingPort + batch compute + cache
│   (Node 4)      │
└────────┬────────┘
         │ VectorizedChunk[]
         ▼
┌─────────────────┐
│  VECTOR_STORE   │ ← IdempotentVectorStore（软删除 + hash 查重）
│   (Node 5)      │   ├─→ AkashicSemanticMemory（JSONL 持久化）
│                 │   └─→ LanceDB（向量检索）
└─────────────────┘
```

### 2.2 节点状态机

```
                    ┌──────────┐
     ┌──────────────│ PENDING  │──────────────┐
     │              └────┬─────┘              │
     │                   │ extract()         │
     │                   ▼                   │
     │              ┌──────────┐             │
     │         ┌────│ RUNNING  │────┐        │
     │         │    └────┬─────┘    │        │
     │         │         │ success   │        │
     │         │         ▼           │ error  │
     │         │   ┌──────────┐      │        │
     │         │   │COMPLETED │      │        │
     │         │   └──────────┘      │        │
     │         │         │           │        │
     │         │         │ retry      │        │
     │         │         ▼           │        │
     │         │   ┌──────────┐      │        │
     └──│FAILING│◄──│ RETRYING │─────┘        │
         └──────┘   └──────────┘
```

### 2.3 DAG 拓扑约束

```
EXTRACTOR ──────────────────────────────┐
     │                                    │
     ▼                                    ▼
SEMANTIC_CHUNKER ──→ METADATA_ENRICHER ──→ EMBEDDING_COMPUTER ──→ VECTOR_STORE
     │                    │
     └────────────────────┘
        (可并行同层节点)
```

**约束**：
- `EXTRACTOR` → `SEMANTIC_CHUNKER`：**1:1 顺序**
- `SEMANTIC_CHUNKER` → `METADATA_ENRICHER`：**N:N 并行**（每个 chunk 独立 Enricher 实例）
- `METADATA_ENRICHER` → `EMBEDDING_COMPUTER`：**N:1 批量**
- `EMBEDDING_COMPUTER` → `VECTOR_STORE`：**N:1 批量落库**

---

## 3. 核心组件设计（Component Design）

### 3.1 DocumentPipeline（编排器）

```python
class DocumentPipeline:
    """
    DAG 管道编排器，参考 memory_manager.py TierCoordinator 模式。

    使用 asyncio.TaskGroup 并行执行独立 Stage，
    复用 kernelone/workflow/engine.py 的 DAG 调度思想。
    """
    def __init__(
        self,
        extractor: ExtractorPort,
        chunker: SemanticChunkerPort,
        enricher: MetadataEnricherPort,
        embedding_computer: EmbeddingComputerPort,
        vector_store: IdempotentVectorStorePort,
        max_concurrency: int = 4,
    ):
        ...

    async def run(self, documents: list[DocumentInput]) -> PipelineResult:
        """
        执行管道：

        1. EXTRACTOR: 串行解析（PDF/Word/Markdown 解析器是 CPU-bound）
        2. SEMANTIC_CHUNKER: 并行分块（asyncio.gather）
        3. METADATA_ENRICHER: 并行富化（asyncio.gather）
        4. EMBEDDING_COMPUTER: 批量向量化（batch size=32）
        5. VECTOR_STORE: 批量落库（transaction）
        """
```

### 3.2 SemanticChunker（语义分块器）

**复用现有**：
- `kernelone/context/compaction.py` 的 `_continuity_signal_score()` 信号词评分机制
- `kernelone/akashic/working_memory.py` 的 `ChunkPriority` 分级

**新增能力**：
- 段落边界检测（`\n\n` 换行符序列）
- 代码结构感知（class/def/interface 关键字行）
- Markdown 标题层级感知（`#` / `##` / `###`）
- 意图切片（sentence-level NLP）

```python
class SemanticChunker:
    """
    语义感知分块器，替代 lancedb_code_search.py 的固定行分块。

    评分机制：
    - 段落边界 +5
    - 函数/类定义行 +3
    - Markdown 标题行 +4
    - 高信号词（error/bug/fix） +3
    - 代码路径引用 +2
    - 低信号词（greeting/log） -4
    """
    CHUNK_TARGET_TOKENS: int = 512      # 目标 chunk 大小
    CHUNK_MIN_TOKENS: int = 128         # 最小 chunk
    SIGNAL_THRESHOLD: int = 3          # 边界触发阈值

    def chunk(self, text: str, source_hint: str = "auto") -> list[SemanticChunk]:
        """
        返回语义分块列表，每个 chunk 包含：
        - text: str
        - start_line: int
        - end_line: int
        - boundary_score: float
        - semantic_tags: list[str]
        """
```

### 3.3 IdempotentVectorStore（幂等向量存储）

**修复现有缺陷**：
- `AkashicSemanticMemory.delete()` 幽灵数据问题 → **软删除标记**
- `AkashicSemanticMemory.add()` 重复添加问题 → **content hash 查重**

```python
class IdempotentVectorStore:
    """
    封装 AkashicSemanticMemory，解决幽灵数据和幂等性问题。

    1. add() 前查重：compute sha256(content) 作为幂等键
    2. delete() 改用软删除：写入 {"deleted": true, "deleted_at": ...} JSONL 条目
    3. _load() 跳过 deleted=True 条目
    4. 重启后自动清理过期软删除（可选 vacuum）
    """
    def __init__(self, semantic_memory: AkashicSemanticMemory):
        ...

    async def add(
        self,
        text: str,
        metadata: dict | None = None,
        importance: int = 5,
    ) -> str:
        # 1. Compute content_hash = sha256(text.encode("utf-8")).hexdigest()[:16]
        # 2. Check if content_hash exists in _hash_index
        # 3. If exists: return existing memory_id (idempotent)
        # 4. If not: call super().add() then add to _hash_index

    async def delete(self, memory_id: str) -> bool:
        # 1. Write tombstone entry to JSONL: {"memory_id": ..., "deleted": true, "deleted_at": "..."}
        # 2. Remove from _items (内存删除)
        # 3. Remove from _hash_index
        # NOTE: 不再直接修改 JSONL 文件，而是追加 tombstone

    async def vacuum(self, max_age_days: int = 30) -> int:
        # 可选：重写 JSONL 文件，移除所有 deleted=True 条目
```

### 3.4 EmbeddingComputer（向量化计算器）

**复用现有**：
- `kernelone/llm/embedding.py` 的 `KernelEmbeddingPort`
- `kernelone/akashic/semantic_cache.py` 的 batch embedding cache

```python
class EmbeddingComputer:
    """
    批量向量化计算器，支持：
    - Batch processing（max_batch_size=32）
    - 异步并发（asyncio.Semaphore 控制并发）
    - Cache hit 跳过（复用 semantic_cache 的缓存逻辑）
    """
    def __init__(
        self,
        embedding_port: KernelEmbeddingPort,
        model: str = "nomic-embed-text",
        max_batch_size: int = 32,
        max_concurrency: int = 8,
    ):
        ...

    async def compute_batch(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """
        并行计算多个文本的 embedding。
        1. 检查 cache（exact match by text hash）
        2. 批量请求 embedding_port.get_embedding()
        3. 更新 cache
        """
```

### 3.5 ExtractorPort（文档提取器接口）

```python
class ExtractorPort(Protocol):
    """多模态文档提取接口"""
    async def extract(self, doc: DocumentInput) -> list[ExtractedFragment]: ...

class DocumentInput:
    source: str                        # 文件路径 / URL / DB query
    mime_type: str                    # "application/pdf" / "text/markdown" / ...
    content: bytes | str               # 原始内容

class ExtractedFragment:
    text: str
    line_start: int
    line_end: int
    mime_type: str
    metadata: dict                    # 页码/段落号/标题等
```

---

## 4. 幂等性关键设计（Idempotency Critical Points）

### 4.1 Content Hash 去重

```
add(text):
  content_hash = sha256(text.encode("utf-8")).hexdigest()[:16]
  if content_hash in self._hash_index:
    return self._hash_index[content_hash]  # idempotent: return existing

  memory_id = await self._semantic_memory.add(text, ...)
  self._hash_index[content_hash] = memory_id
  return memory_id
```

### 4.2 软删除机制

```python
# 写入 tombstone 到 JSONL
{"memory_id": "sem_1234567890", "content_hash": "abc123...", "deleted": true, "deleted_at": "2026-04-04T12:00:00Z"}

# _load() 时跳过 deleted=True
def _load(self):
    for line in open(self._memory_file):
        item = json.loads(line)
        if item.get("deleted"):
            continue  # skip tombstone
        self._items[item["memory_id"]] = item
```

### 4.3 LanceDB 同步

```python
async def sync_to_lancedb(self, chunk: VectorizedChunk) -> None:
    """同步到 LanceDB，保持与 AkashicSemanticMemory 的幂等一致性"""
    # 1. Compute content_hash
    # 2. Check if exists in LanceDB（by content_hash）
    # 3. If exists: update metadata
    # 4. If not: insert new row
    # 5. Delete: soft-delete in LanceDB (mark deleted_at)
```

---

## 5. 内存安全设计（Memory Safety）

### 5.1 生成器与延迟求值

```python
async def run_lazy(
    self,
    documents: AsyncGenerator[DocumentInput, None],
) -> AsyncGenerator[PipelineResult, None]:
    """
    使用生成器模式处理 GB 级别语料库。
    内存占用 = O(batch_size)，不随文档总数增长。
    """
    batch: list[DocumentInput] = []
    async for doc in documents:
        batch.append(doc)
        if len(batch) >= self._max_batch_size:
            yield await self._process_batch(batch)
            batch.clear()
    if batch:
        yield await self._process_batch(batch)
```

### 5.2 并发控制

```python
# 使用 asyncio.Semaphore 控制最大并发
self._semaphore = asyncio.Semaphore(max_concurrency)

async def _process_chunk(self, chunk: SemanticChunk) -> VectorizedChunk:
    async with self._semaphore:
        # ... enrichment + embedding
```

---

## 6. 文件结构（File Structure）

```
polaris/kernelone/
├── akashic/
│   └── knowledge_pipeline/           # NEW: 真理熔炉管道
│       ├── __init__.py
│       ├── pipeline.py               # DocumentPipeline 编排器
│       ├── protocols.py              # ExtractorPort / SemanticChunkerPort / ...
│       ├── extractors/               # 多模态提取器
│       │   ├── __init__.py
│       │   ├── base.py               # ExtractorPort 实现
│       │   ├── markdown_extractor.py # Markdown 解析
│       │   ├── pdf_extractor.py       # PDF 解析（可选依赖）
│       │   └── docx_extractor.py      # Word 解析（可选依赖）
│       ├── semantic_chunker.py        # 语义分块器
│       ├── metadata_enricher.py       # 元数据富化器
│       ├── embedding_computer.py      # 批量向量化
│       └── idempotent_vector_store.py # 幂等向量存储（修复幽灵数据）
│
├── context/
│   └── compaction.py                 # EXISTING: 复用 _continuity_signal_score()
│
├── llm/
│   └── embedding.py                  # EXISTING: 复用 KernelEmbeddingPort
│
├── workflow/
│   └── engine.py                     # EXISTING: 复用 DAG + asyncio.wait()
│
infrastructure/
└── db/
    └── repositories/
        └── lancedb_code_search.py     # MODIFIED: 集成新 SemanticChunker
```

---

## 7. 实现计划（Implementation Plan）

### Phase 1: 基础设施（Week 1-2）
- [ ] 定义 `ExtractorPort` / `SemanticChunkerPort` / `MetadataEnricherPort` 协议
- [ ] 实现 `IdempotentVectorStore`（修复 `AkashicSemanticMemory` 幽灵数据问题）
- [ ] 实现 `EmbeddingComputer`（复用 `KernelEmbeddingPort`）
- [ ] 单元测试覆盖

### Phase 2: 语义分块（Week 3-4）
- [ ] 实现 `SemanticChunker`（复用 `compaction._continuity_signal_score()`）
- [ ] 实现 Markdown 段落边界检测
- [ ] 实现代码结构感知（class/def/interface）
- [ ] 单元测试覆盖

### Phase 3: 管道编排（Week 5-6）
- [ ] 实现 `DocumentPipeline` 编排器
- [ ] 实现 `asyncio.TaskGroup` 并行执行
- [ ] 实现 Lazy Generator 模式
- [ ] 集成 `lancedb_code_search.py`

### Phase 4: 集成与验证（Week 7-8）
- [ ] End-to-End 测试
- [ ] 性能基准测试（GB 级语料）
- [ ] 幂等性验证（同一文档处理 100 次 = 1 次）
- [ ] 文档编写

---

## 8. 验证计划（Verification Plan）

### 8.1 幂等性验证
```python
async def test_idempotency():
    store = IdempotentVectorStore(semantic_memory)
    doc = DocumentInput(source="test.md", mime_type="text/markdown", content="Hello world")

    # 同一文档处理 100 次
    ids = []
    for _ in range(100):
        result = await pipeline.run([doc])
        ids.append(result.memory_ids[0])

    # 所有 ID 应该相同
    assert len(set(ids)) == 1, "Idempotency failed: got different IDs"
```

### 8.2 幽灵数据验证
```python
async def test_ghost_data():
    store = IdempotentVectorStore(semantic_memory)
    memory_id = await store.add("Test content", importance=5)

    # 删除
    await store.delete(memory_id)

    # 重启（模拟）
    new_store = IdempotentVectorStore(semantic_memory)

    # 应该找不到
    result = await new_store.search("Test content")
    assert result is None, "Ghost data detected: deleted item resurrected"
```

### 8.3 语义分块验证
```python
async def test_semantic_chunking():
    chunker = SemanticChunker()
    code = """
    class MyClass:
        def method_one(self):
            pass

        def method_two(self):
            pass
    """
    chunks = chunker.chunk(code, source_hint="python")

    # 应该按函数边界分块，不应在函数中间切断
    for chunk in chunks:
        assert not chunk.text.startswith("    def "), "Chunk split in middle of class/function"
```

---

## 9. 风险与缓解（Risks & Mitigations）

| 风险 | 等级 | 缓解措施 |
|-----|-----|---------|
| PDF/Docx 解析依赖外部库 | MEDIUM | 实现 `ExtractorPort`，提供 mock 实现用于测试 |
| 大文件 OOM | HIGH | Lazy Generator 模式，batch size 控制 |
| 向量数据库一致性 | HIGH | Transaction + 版本号控制 |
| 向后兼容破坏 | MEDIUM | 新增接口不修改现有 `AkashicSemanticMemory` API |

---

## 10. 参考资料（References）

- `kernelone/akashic/memory_manager.py` - TierCoordinator 模式
- `kernelone/context/compaction.py` - 信号词评分机制
- `kernelone/workflow/engine.py` - DAG + asyncio.wait() 调度
- `infrastructure/db/repositories/lancedb_code_search.py` - LanceDB 集成
- `docs/governance/TOOL_ALIAS_DESIGN_GUIDE.md` - 别名设计规范
