# 阿卡夏之枢：记忆引擎状态流图

## 整体架构（上帝模式视图）

```mermaid
flowchart TB
    subgraph Input["📝 输入层"]
        UserQuery["用户查询"]
        SystemPrompt["系统提示"]
        ToolResult["工具执行结果"]
    end

    subgraph SemanticCache["🎯 语义缓存层"]
        CacheInterceptor["SemanticCacheInterceptor<br/>相似度拦截器"]
        EmbeddingIndex["EmbeddingIndex<br/>向量索引"]
        CacheHit["Cache Hit<br/>直接返回"]:::success
        CacheMiss["Cache Miss<br/>继续处理"]:::normal
    end

    subgraph WorkingMemory["⚡ 工作记忆层<br/>(WorkingMemoryWindow)"]
        SlidingWindow["滑动窗口<br/>Token 实时计算"]
        HeadAnchor["Head Anchor<br/>系统提示+任务目标"]
        TailAnchor["Tail Anchor<br/>最近 2-3 轮对话"]
        MiddleCompress["Middle Compress<br/>压缩摘要区"]
    end

    subgraph EpisodicMemory["📚 情节记忆层<br/>(EpisodicMemoryStore)"]
        SessionHistory["Session 历史库"]
        TurnEvents["Turn 事件流"]
        ContextOS["ContextOS 投影"]
    end

    subgraph SemanticMemory["🧠 语义记忆层<br/>(SemanticMemoryStore)"]
        VectorDB["Vector DB<br/>Milvus/Chroma"]
        MemoryItem["MemoryItem<br/>结构化记忆"]
        Reflection["Reflection<br/>推导洞察"]
    end

    subgraph CompressionDaemon["🔄 压缩守护进程"]
        WaterlineMonitor["水位线监控<br/>75% soft / 90% hard"]
        BackgroundSummarize["后台摘要生成"]
        IncrementalCompact["增量压缩"]
    end

    subgraph Output["🚀 输出层"]
        LLMCall["LLM 调用"]
        Response["响应生成"]
    end

    UserQuery --> CacheInterceptor
    SystemPrompt --> CacheInterceptor
    ToolResult --> CacheInterceptor

    CacheInterceptor --> EmbeddingIndex
    EmbeddingIndex -->|相似度 > 0.92| CacheHit
    EmbeddingIndex -->|相似度 < 0.92| CacheMiss

    CacheMiss --> SlidingWindow
    SlidingWindow --> HeadAnchor
    SlidingWindow --> MiddleCompress
    SlidingWindow --> TailAnchor

    MiddleCompress -.->|fallback| SessionHistory
    SessionHistory -.-> ContextOS

    SlidingWindow -.->|promote| VectorDB
    VectorDB -.->|retrieve| MemoryItem
    VectorDB -.->|reflect| Reflection

    WaterlineMonitor -.->|trigger| BackgroundSummarize
    BackgroundSummarize -.->|update| MiddleCompress
    IncrementalCompact -.->|archive| SessionHistory

    HeadAnchor --> LLMCall
    MiddleCompress --> LLMCall
    TailAnchor --> LLMCall

    LLMCall --> Response
    Response -.->|record| TurnEvents
    TurnEvents -.->|sync| SessionHistory

    classDef success fill:#4caf50,stroke:#2e7d32,color:white
    classDef normal fill:#2196f3,stroke:#1565c0,color:white
    classDef warning fill:#ff9800,stroke:#ef6c00,color:white
    classDef danger fill:#f44336,stroke:#c62828,color:white
```

---

## 详细数据流向

### 1. 输入处理流（Input Flow）

```mermaid
sequenceDiagram
    actor User
    participant InputSanitizer
    participant SemanticHasher
    participant CacheInterceptor
    participant MemoryManager

    User->>InputSanitizer: 原始查询
    InputSanitizer->>InputSanitizer: 注入检测 / Unicode 规范化
    InputSanitizer->>SemanticHasher: 清洗后文本
    SemanticHasher->>SemanticHasher: SHA-256 + Embedding 计算
    SemanticHasher->>CacheInterceptor: (query_hash, embedding_vector)

    CacheInterceptor->>CacheInterceptor: 本地 LRU 检查
    alt 本地命中
        CacheInterceptor-->>User: 返回缓存响应
    else 本地未命中
        CacheInterceptor->>MemoryManager: 查询语义记忆
        MemoryManager->>VectorDB: 相似度搜索 (top_k=3)
        VectorDB-->>MemoryManager: 相似记忆项
        MemoryManager-->>CacheInterceptor: 匹配结果

        alt 相似度 > 0.92
            CacheInterceptor-->>User: 改写后返回
        else 相似度 < 0.92
            CacheInterceptor->>WorkingMemory: 进入工作记忆处理
        end
    end
```

### 2. 工作记忆管理流（Working Memory Flow）

```mermaid
sequenceDiagram
    participant Input as 新输入
    participant WMW as WorkingMemoryWindow
    participant TokenCounter as TokenCounter<br/>(精确估算)
    participant Prioritizer as HierarchicalPrioritizer
    participant Compressor as StreamingCompressor
    participant Output as 组装后上下文

    Input->>WMW: push_message(role, content)
    WMW->>TokenCounter: estimate_tokens(content)
    TokenCounter-->>WMW: precise_token_count

    WMW->>WMW: 当前总 token > threshold?

    alt token_count < 75% budget
        WMW->>Output: 直接追加
    else 75% < token_count < 90%
        WMW->>Prioritizer: 触发 soft 压缩
        Prioritizer->>Prioritizer: 计算各块重要性
        Prioritizer->>Compressor: 压缩低优先级块
        Compressor-->>WMW: 更新摘要
        WMW->>Output: 重组后输出
    else token_count > 90%
        WMW->>Prioritizer: 触发 hard 压缩
        Prioritizer->>Prioritizer: 标记驱逐候选
        Prioritizer->>EpisodicMemory: promote 到情节记忆
        Prioritizer->>Compressor: 强制压缩中间区域
        Compressor-->>WMW: 极简摘要
        WMW->>Output: 紧急组装
    end
```

### 3. 记忆晋升/降级流（Memory Promotion Flow）

```mermaid
flowchart LR
    subgraph Working["⚡ Working Memory<br/>~4K-32K tokens"]
        WM[WorkingMemoryWindow]
    end

    subgraph Episodic["📚 Episodic Memory<br/>Session 级别"]
        ES[EpisodicStore]
        TL[TranscriptLog]
        CS[CompactSnapshot]
    end

    subgraph Semantic["🧠 Semantic Memory<br/>长期知识"]
        VS[VectorStore]
        MI[MemoryItem]
        RF[Reflection]
    end

    WM -->|promote<br/>session end| ES
    WM -->|real-time sync| TL
    ES -->|compact<br/>threshold trigger| CS
    CS -->|vectorize<br/>importance > 7| VS
    VS -->|derive| MI
    MI -->|aggregate| RF
    RF -->|feedback| WM

    style Working fill:#ffebee,stroke:#c62828
    style Episodic fill:#e3f2fd,stroke:#1565c0
    style Semantic fill:#e8f5e9,stroke:#2e7d32
```

### 4. 压缩守护进程流（Compression Daemon Flow）

```mermaid
stateDiagram-v2
    [*] --> Idle: 启动

    Idle --> Monitoring: 开始监控

    Monitoring --> Monitoring: 每 500ms 检查

    Monitoring --> SoftCompression: 水位线 75%
    SoftCompression --> BackgroundSummarize: 启动后台任务
    BackgroundSummarize --> Idle: 摘要完成

    Monitoring --> HardCompression: 水位线 90%
    HardCompression --> EmergencyCompact: 暂停新输入
    EmergencyCompact --> Idle: 压缩完成

    SoftCompression --> HardCompression: 水位继续上涨

    Monitoring --> Archival: 会话结束
    Archival --> [*]: 归档到长期记忆
```

---

## 状态转换表

### WorkingMemoryWindow 状态机

| 当前状态 | 事件 |  guard 条件 | 下一状态 | 动作 |
|----------|------|-------------|----------|------|
| Healthy | push_message | tokens < 75% | Healthy | 直接追加 |
| Healthy | push_message | 75% <= tokens < 90% | SoftCompressing | 触发后台压缩 |
| Healthy | push_message | tokens >= 90% | HardCompressing | 强制压缩+拒绝新输入 |
| SoftCompressing | compact_complete | tokens < 75% | Healthy | 更新摘要 |
| SoftCompressing | push_message | tokens >= 90% | HardCompressing | 升级压缩级别 |
| HardCompressing | emergency_complete | tokens < 80% | SoftCompressing | 降级恢复 |

### SemanticCache 状态机

| 当前状态 | 事件 | guard 条件 | 下一状态 | 动作 |
|----------|------|------------|----------|------|
| Idle | query_received | - | Hashing | 计算语义哈希 |
| Hashing | local_hit | key in LRU | CacheHit | 返回缓存 |
| Hashing | local_miss | key not in LRU | Embedding | 计算向量 |
| Embedding | similarity_search | - | Searching | 查询向量库 |
| Searching | high_similarity | score > 0.92 | CacheHit | 改写返回 |
| Searching | low_similarity | score < 0.92 | CacheMiss | 透传处理 |
| CacheHit | response_sent | - | Idle | 更新 LRU |
| CacheMiss | response_received | - | Idle | 可选缓存 |

---

## 关键数据结构演进

```mermaid
flowchart TB
    subgraph Raw["原始输入"]
        R[字符串 / Message]
    end

    subgraph Structured["结构化"]
        S[MemoryItem<br/>id, timestamp, embedding, importance]
    end

    subgraph Compressed["压缩形态"]
        C[CompactSnapshot<br/>summary, key_points, anchors]
    end

    subgraph Vectorized["向量化"]
        V[VectorRecord<br/>embedding, metadata, payload_hash]
    end

    R -->|sanitize + hash| S
    S -->|summarize| C
    C -->|vectorize| V
    V -->|retrieve| S
    S -->|rehydrate| R

    style Raw fill:#f5f5f5
    style Structured fill:#e3f2fd
    style Compressed fill:#fff3e0
    style Vectorized fill:#e8f5e9
```
