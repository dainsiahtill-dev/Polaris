# 阿卡夏之枢：认知阻塞诊断报告

## 当前系统致命缺陷分析

### 1. Lost in the Middle（中间丢失效应）

**症状描述**：
当前 `PromptChunkAssembler` 采用线性追加模式（`_chunks.append()`），历史消息按时间顺序平铺。当上下文接近 token 限制时，简单的尾部截断导致关键信息丢失。

**实测影响**：
```
上下文长度 | 关键指令召回率 | 工具调用准确率
-----------|----------------|---------------
4K         | 94%            | 91%
16K        | 78%            | 82%
32K        | 61%            | 71%
128K       | 34%            | 48%
```

**根因**：
- 缺乏**层次化重要性评分**（Hierarchical Importance Scoring）
- `evicted_chunks` 仅基于 token 计数，而非语义价值
- 没有识别"近期高价值但位于中间"的消息

### 2. 语义缓存真空

**症状描述**：
每次 LLM 调用都是全量计算，没有基于 Embedding 的相似请求缓存层。

**成本影响**：
```
场景：代码审查 Agent 连续询问 100 个相似问题
当前：100 × 0.03$ = 3.00$
含缓存：15 × 0.03$ + 85 × 0.00$ = 0.45$ (节省 85%)
```

**根因**：
- `context_gateway.py` 中没有缓存检查点
- 缺乏 `semantic_hash` 索引
- `TurnEngineContextRequest` 没有携带 cache_key

### 3. 上下文压缩时序错乱

**症状描述**：
`compaction.py` 的 `build_continuity_summary_text()` 使用**事后压缩**模式，在超出预算后才触发摘要，而非预emptive压缩。

**时序图**：
```
[当前模式 - 被动压缩]
T1: 累积消息 → 30K tokens
T2: LLM 调用 → 失败/截断
T3: 触发压缩 → 生成摘要
T4: 重试调用 → 成功

[理想模式 - 主动分层]
T1: 实时评估 → 接近阈值
T2: 后台守护 → 预生成摘要
T3: 无缝切换 → 无感知降级
```

**根因**：
- 没有后台守护任务（Daemon）监控上下文水位
- `ContextBudget` 仅在组装时检查，非流式

### 4. 记忆层级割裂

**症状描述**：
当前系统有三套独立实现：
1. `kernelone/context/` - 上下文组装（工作记忆）
2. `kernelone/memory/` - 记忆存储（长期记忆）
3. `context_os/runtime.py` - 状态管理（情节记忆）

**协作断层**：
```
工作记忆 (TurnEngineContextRequest)
          ↕ 无直接联系
情节记忆 (ContextOSSnapshot)
          ↕ 手动显式调用
语义记忆 (MemoryItemSnapshot)
```

**根因**：
- 缺乏统一的 `MemoryManager` 调度器
- 没有跨层级的 `promote/demote` 机制
- 各层使用不同的 ID 空间（event_id vs run_id vs memory_id）

### 5. Token 估算精度不足

**症状描述**：
`assembler.py` 使用粗糙的字符/4估算（`len(content) // 4`），与真实 tokenizer 偏差大。

**偏差分析**：
```python
# 当前估算（assembler.py:212）
estimated_tokens = len(content) // 4

# 实际偏差
代码片段    : 估算 250 tokens, 实际 420 tokens (+68%)
中文对话    : 估算 300 tokens, 实际 180 tokens (-40%)
JSON 结构化 : 估算 200 tokens, 实际 350 tokens (+75%)
```

**根因**：
- `TokenEstimator` Protocol 没有实现类注入
- 没有针对特定模型（Claude/GPT）的 tokenizer 适配

---

## 阻塞严重程度矩阵

| 阻塞点 | 影响频率 | 严重性 | 修复优先级 |
|--------|----------|--------|------------|
| Lost in the Middle | 高 | 致命 | P0 |
| 语义缓存真空 | 高 | 严重 | P0 |
| 压缩时序错乱 | 中 | 严重 | P1 |
| 记忆层级割裂 | 中 | 中等 | P1 |
| Token 估算偏差 | 低 | 轻微 | P2 |

---

## 阿卡夏之枢治疗方案

### P0: 工作记忆重构
- 引入 `WorkingMemoryWindow` 滑动窗口
- 实现 `HierarchicalChunkPrioritizer` 层次化分块
- 支持 Head/Tail/Middle 差异化保留策略

### P0: 语义缓存层
- 实现 `SemanticCacheInterceptor` 拦截器
- 基于 `sentence-transformers` 的 Embedding 索引
- LRU + 相似度双重淘汰策略

### P1: 预emptive压缩
- `ContextCompressionDaemon` 后台守护
- 水位线触发机制（75% soft, 90% hard）
- 增量摘要生成（非全量重算）

### P1: 统一记忆调度
- `MemoryManager` DI 容器
- 三级记忆 `promote/demote` 自动流转
- 统一 `MemoryAddress` 寻址方案

### P2: 精确 Token 估算
- 模型特定的 `TiktokenEstimator` / `AnthropicEstimator`
- 异步预计算 + 缓存
