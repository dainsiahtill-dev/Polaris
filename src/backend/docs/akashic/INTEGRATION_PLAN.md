# 阿卡夏之枢：8周整合计划

## 执行摘要

本计划为 Akashic Nexus（多模态分层记忆引擎）的 8 周整合路线图。
目标：在零破坏性变更的前提下，将记忆引擎作为增强层整合到现有 KernelOne 系统。

**关键约束**：
- ✅ 不替换现有 MemoryStore、ContextOS 或缓存系统
- ✅ 仅在 `polaris/kernelone/akashic/` 目录新增代码
- ✅ 所有存储后端通过 Protocol/ABC 注入（DIP）
- ✅ 渐进式部署，无单点故障

---

## 当前状态评估

### 已验证可复用资产

| 组件 | 位置 | 复用方式 |
|------|------|----------|
| `MemoryPort` 协议 | `kernelone/memory/contracts.py` | 直接实现 |
| `TieredAssetCacheManager` | `kernelone/context/cache_manager.py` | 扩展而非替换 |
| `RoleContextCompressor` | `kernelone/context/compaction.py` | 整合进 Daemon |
| `KernelEmbeddingPort` | `kernelone/llm/embedding.py` | 依赖注入 |
| `BM25 + LanceDB` | `kernelone/memory/memory_store.py` | 扩展语义搜索 |
| `StateFirstContextOS` | `kernelone/context/context_os/runtime.py` | 情节记忆集成 |

### 待解决问题

| 问题 | 严重性 | 解决方案 |
|------|--------|----------|
| Lost in the Middle (线性追加) | P0 | `WorkingMemoryWindow` 层次化分块 |
| 语义缓存真空 | P0 | `SemanticCacheInterceptor` |
| 压缩时序错乱（被动） | P1 | `CompressionDaemon` 预emptive |
| 记忆层级割裂 | P1 | `MemoryManager` 统一调度 |
| Token 估算偏差 | P2 | 可注入 `TiktokenEstimator` |

---

## 8 周分阶段计划

### Week 1-2: Phase 1 - Bootstrap（无破坏性变更）

**目标**：建立 Akashic 模块骨架，实现核心协议和基础工作记忆

**交付物**：

```
polaris/kernelone/akashic/
├── __init__.py              ✅ 模块初始化
├── protocols.py            ✅ Protocol 定义 (MemoryManagerPort, WorkingMemoryPort, etc.)
├── working_memory.py        ✅ WorkingMemoryWindow (层次化滑动窗口)
├── docs/
│   ├── MEMORY_ENGINE_STATEFLOW.md  ✅ 已有
│   └── INTEGRATION_PLAN.md         ✅ 本文档
```

**实现任务**：

| 任务 | 描述 | 验证标准 |
|------|------|----------|
| T1.1 | 创建 `protocols.py` 定义所有 Port | 所有 Protocol 包含完整方法签名 |
| T1.2 | 实现 `WorkingMemoryWindow` | push/get_messages 基本功能通过单元测试 |
| T1.3 | 实现 `HierarchicalChunkPrioritizer` | 重要性评分覆盖 90% 关键词 |
| T1.4 | 创建 `integration.py` DI 工厂 | `create_memory_manager()` 返回可用的 Manager |
| T1.5 | 编写 Phase 1 单元测试 | `pytest akashic/tests/test_working_memory.py` 100% 通过 |

**代码变更清单**：
- 新增: `polaris/kernelone/akashic/__init__.py`
- 新增: `polaris/kernelone/akashic/protocols.py`
- 新增: `polaris/kernelone/akashic/working_memory.py`
- 新增: `polaris/kernelone/akashic/integration.py`

**测试策略**：
```bash
# 单元测试（无外部依赖）
pytest polaris/kernelone/akashic/tests/test_working_memory.py -v

# 集成测试（需要 mock）
pytest polaris/kernelone/akashic/tests/test_integration.py -v
```

**回滚计划**：
- 如发现问题，删除整个 `akashic/` 目录即可回滚
- 现有代码不受影响

---

### Week 3-4: Phase 2 - Memory Enhancement（增强现有系统）

**目标**：实现语义缓存拦截器，增强记忆子系统

**交付物**：

```
polaris/kernelone/akashic/
├── semantic_cache.py       ✅ 语义缓存拦截器
├── memory_manager.py        ✅ 统一记忆管理器
```

**实现任务**：

| 任务 | 描述 | 验证标准 |
|------|------|----------|
| T2.1 | 实现 `SemanticCacheInterceptor` | 相似度阈值 0.92，缓存命中率 > 60% |
| T2.2 | 实现 `SemanticCachePort` | 支持 `get_or_compute/invalidate/clear/get_stats` |
| T2.3 | 实现 `MemoryManager` 核心 | 统一调度 Working/Episodic/Semantic 记忆 |
| T2.4 | 实现 `TierCoordinatorPort` | promote/demote 跨层流转 |
| T2.5 | 集成 LanceDB 向量检索 | 复用 `memory/memory_store.py` 的 LanceDB |
| T2.6 | 编写 Phase 2 单元测试 | 缓存命中率测试通过 |

**代码变更清单**：
- 新增: `polaris/kernelone/akashic/semantic_cache.py`
- 新增: `polaris/kernelone/akashic/memory_manager.py`
- 修改: `polaris/kernelone/akashic/integration.py` (扩展 DI 工厂)

**测试策略**：
```bash
# 语义缓存测试
pytest polaris/kernelone/akashic/tests/test_semantic_cache.py -v

# 内存管理器测试
pytest polaris/kernelone/akashic/tests/test_memory_manager.py -v

# 向量检索集成测试
pytest polaris/kernelone/akashic/tests/test_vector_integration.py -v
```

**回滚计划**：
- 禁用 `enable_semantic_cache` 配置即可禁用新功能
- MemoryManager 有 no-op 回退实现

---

### Week 5-6: Phase 3 - Cache Intelligence（缓存智能层）

**目标**：实现预emptive 压缩守护进程，增强 ContextOS 集成

**交付物**：

```
polaris/kernelone/akashic/
├── compression_daemon.py    ✅ 后台压缩守护进程
```

**实现任务**：

| 任务 | 描述 | 验证标准 |
|------|------|----------|
| T3.1 | 实现 `CompressionDaemon` | 75%/90% 水位线触发正常 |
| T3.2 | 实现 `DaemonConfig` | 支持 `check_interval_ms`/`soft_watermark_pct` 配置 |
| T3.3 | 集成 `RoleContextCompressor` | 调用 `compaction.py` 的压缩逻辑 |
| T3.4 | 实现增量压缩 | 非阻塞式后台压缩 |
| T3.5 | 实现状态机 | `IDLE → MONITORING → COMPRESSING_SOFT/HARD → IDLE` |
| T3.6 | 集成 ContextOS | 作为情节记忆层增强 |
| T3.7 | 编写 Phase 3 集成测试 | Daemon 状态转换测试通过 |

**代码变更清单**：
- 新增: `polaris/kernelone/akashic/compression_daemon.py`
- 修改: `polaris/kernelone/akashic/working_memory.py` (添加压缩触发)
- 修改: `polaris/kernelone/akashic/integration.py` (添加 Daemon 生命周期)

**测试策略**：
```bash
# 压缩 Daemon 测试
pytest polaris/kernelone/akashic/tests/test_compression_daemon.py -v

# ContextOS 集成测试
pytest polaris/kernelone/akashic/tests/test_contextos_integration.py -v
```

---

### Week 7-8: Phase 4 - Session Continuity Enhancement（会话连续性增强）

**目标**：实现完整的 promote/demote 机制，增强会话恢复能力

**交付物**：

```
polaris/kernelone/akashic/
├── promotion.py             ✅ 跨层晋升/降级机制
├── episodic_store.py        ✅ 情节记忆存储（可选实现）
```

**实现任务**：

| 任务 | 描述 | 验证标准 |
|------|------|----------|
| T4.1 | 实现 `PromotionCandidate` 评估 | 重要性阈值过滤正常 |
| T4.2 | 实现 `promote_to_episodic()` | Working → Episodic 流转正常 |
| T4.3 | 实现 `promote_to_semantic()` | Episodic → Semantic (LanceDB) 流转正常 |
| T4.4 | 实现会话恢复 | 恢复后上下文完整性 > 90% |
| T4.5 | 实现 `sync_tiers()` | 跨层 GC 和一致性校验 |
| T4.6 | 端到端集成测试 | 全链路测试通过 |
| T4.7 | 性能基准测试 | Token 使用率改善 > 30% |

**代码变更清单**：
- 新增: `polaris/kernelone/akashic/promotion.py`
- 新增: `polaris/kernelone/akashic/episodic_store.py` (可选实现)
- 修改: `polaris/kernelone/akashic/memory_manager.py` (增强 TierCoordinator)
- 修改: `polaris/kernelone/akashic/integration.py` (完整初始化)

**测试策略**：
```bash
# 跨层晋升测试
pytest polaris/kernelone/akashic/tests/test_promotion.py -v

# 端到端测试
pytest polaris/kernelone/akashic/tests/test_e2e.py -v

# 性能基准测试
python -m polaris.kernelone.akashic.benchmark.memory_benchmark
```

---

## 回滚计划

### 按阶段回滚

| 阶段 | 回滚方法 | 影响范围 |
|------|----------|----------|
| Phase 1 | 删除 `akashic/` 目录 | 无（纯新增） |
| Phase 2 | 设置 `enable_semantic_cache=False` | 缓存功能禁用 |
| Phase 3 | 设置 `enable_daemon=False` | 后台压缩禁用 |
| Phase 4 | 删除 `akashic/` + 重启 | 全功能禁用 |

### 紧急回滚

```bash
# 紧急回滚：删除整个 akashic 目录
rm -rf polaris/kernelone/akashic/

# 清理 Python 缓存
find . -type d -name "__pycache__" -path "*akashic*" -exec rm -rf {} +

# 重启服务
python -m polaris.delivery.cli server --restart
```

---

## 验证命令

### 基础验证

```bash
# 1. Python 导入检查
python -c "from polaris.kernelone.akashic import MemoryManager; print('OK')"

# 2. Ruff 代码规范
ruff check polaris/kernelone/akashic/ --fix

# 3. Mypy 类型检查
mypy polaris/kernelone/akashic/ --ignore-missing-imports

# 4. 单元测试
pytest polaris/kernelone/akashic/tests/ -v
```

### 集成验证

```bash
# 5. 与 ContextOS 集成
pytest polaris/kernelone/akashic/tests/test_contextos_integration.py -v

# 6. 与 MemoryStore 集成
pytest polaris/kernelone/akashic/tests/test_memory_store_integration.py -v

# 7. 向量检索功能
pytest polaris/kernelone/akashic/tests/test_vector_integration.py -v
```

### 性能验证

```bash
# 8. Token 使用率对比
python -m polaris.kernelone.akashic.benchmark.compare_token_usage

# 9. 缓存命中率基准
python -m polaris.kernelone.akashic.benchmark.cache_hit_rate

# 10. Lost in Middle 改善测量
python -m polaris.kernelone.akashic.benchmark.litm_measurement
```

---

## 风险评估

| 风险 | 可能性 | 影响 | 缓解策略 |
|------|--------|------|----------|
| 向量检索性能下降 | 中 | 中 | 使用 LanceDB 本地索引 |
| 缓存一致性问题 | 低 | 高 | TTL + 版本号双重淘汰 |
| 内存泄漏（Daemon） | 低 | 中 | 定期 GC + 监控 |
| 与现有 ContextOS 冲突 | 低 | 高 | 只扩展不修改现有代码 |
| 向后兼容性破坏 | 极低 | 极高 | Phase 1 无破坏性变更 |

---

## 依赖关系

```
Phase 1 (Week 1-2)
    └── Phase 2 (Week 3-4)
            └── Phase 3 (Week 5-6)
                    └── Phase 4 (Week 7-8)
```

**依赖说明**：
- Phase 2 依赖 Phase 1 的 Protocol 定义
- Phase 3 依赖 Phase 2 的 MemoryManager
- Phase 4 依赖 Phase 3 的 CompressionDaemon

**并行任务**：
- Week 1-2: 同步完成 `context/compaction.py` 整合设计
- Week 3-4: 同步完成 `llm/embedding.py` 注入改造

---

## 文档更新清单

| 文档 | 更新时机 | 负责人 |
|------|----------|--------|
| `polaris/kernelone/akashic/README.md` | Phase 1 完成后 | AI |
| `polaris/kernelone/akashic/ARCHITECTURE.md` | Phase 2 完成后 | AI |
| `docs/governance/ci/fitness-rules.yaml` | Phase 4 完成后 | 人工 |
| `docs/graph/catalog/cells.yaml` | Phase 4 完成后 | 人工 |
| `CLAUDE.md` | Phase 4 完成后 | 人工 |

---

## 成功标准

| 指标 | 当前值 | Phase 4 目标 |
|------|--------|--------------|
| Token 使用效率 | ~60% (Lost in Middle) | > 85% |
| LLM 调用缓存命中率 | 0% | > 60% |
| 关键指令召回率 (128K) | 34% | > 60% |
| 压缩触发时延 | 被动 (失败后) | < 500ms 预触发 |
| 单元测试覆盖率 | N/A | > 90% |

---

*文档版本: 1.0.0*
*创建日期: 2026-04-04*
*最后更新: 2026-04-04*
