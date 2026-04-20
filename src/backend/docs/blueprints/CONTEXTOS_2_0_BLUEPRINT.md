# ContextOS 2.0 企业级架构蓝图

**版本**: 2.0.0  
**日期**: 2026-04-11  
**状态**: Phase 2 进行中  
**原则**: 借力生态，避免重复造轮子

---

## 一、执行摘要

### 1.1 核心问题诊断

| 问题 | 严重度 | 根因 | 影响 |
|------|--------|------|------|
| 工具结果被无条件归档 | **P0** | ArtifactSelector 策略缺陷 | LLM 无法看到关键信息，死循环 |
| 并发安全漏洞 | **P0** | StateFirstContextOS 无锁保护 | 数据竞争，状态损坏 |
| 循环引用检测缺失 | **P1** | TranscriptMerger 依赖追踪不足 | 无限递归，堆栈溢出 |
| 参数膨胀与类型错误 | **P1** | 使用原始 dataclass，无强制校验 | ArtifactSelector 返回类型错误 |
| 压缩策略粗暴 | **P2** | 简单截断/替换，无内容感知 | 关键语义丢失 |
| 排序策略僵化 | **P2** | 固定权重，无动态调整 | 重要信息被挤出 |
| 重复压缩 | **P3** | 无压缩状态追踪 | CPU 浪费，延迟增加 |

### 1.2 架构评分 (3专家审计)

| 维度 | 评分 | 主要问题 |
|------|------|----------|
| 架构设计 | B+ | 分层清晰，但 Stage 间耦合度高 |
| 算法实现 | B | 基础算法正确，但缺乏高级优化 |
| 健壮性 | B | 单线程安全，并发和多进程有漏洞 |

---

## 二、ContextOS 2.0 架构设计

### 2.1 技术栈选型 (成熟开源库优先)

| 层级 | 职责 | 选型方案 | 理由 |
|------|------|----------|------|
| **Layer 5** | 数据模型与校验 | **Pydantic V2** | Rust 核心，严格校验，性能极高 |
| **Layer 4** | 状态机与管道 | **LangGraph** (或借鉴其 StateGraph 思想) | 专为 LLM 代理设计，支持循环检测、分支路由 |
| **Layer 3** | 压缩与摘要 | **LLMLingua** + **Tree-sitter** | 语义感知压缩，代码结构保留 |
| **Layer 2** | 路由决策 | 启发式规则 + **Google OR-Tools** (Phase 4) | 先跑通基线，再引入高级优化 |
| **Layer 1** | 并发与监控 | **asyncio.Lock** + **Prometheus-Client** + **Structlog** | 异步安全，可观测性 |

---

## 三、实施路线图

### Phase 1: 紧急修复 (已完成) ✅

| 任务 | 状态 | 改动点 |
|------|------|--------|
| T1.1 | ✅ | 启发式路由规则（小文件/错误内容保留） |
| T1.2 | ✅ | StateFirstContextOS 并发锁保护 (threading.RLock) |
| T1.3 | ✅ | TranscriptMerger 异常检测（重复 event_id） |
| T1.4 | ✅ | ArtifactSelector 返回类型修复 |

### Phase 2: 安全摘要层 (进行中) 🚧

**已完成**:
- ✅ `SumySummarizer` - 抽取式摘要 (TextRank)
- ✅ `TruncationSummarizer` - 紧急截断（智能日志截断）
- ✅ `TieredSummarizer` - 分层降级架构
- ✅ `SummarizerInterface` - 防腐层协议
- ✅ 集成到 `Canonicalizer` 的 SUMMARIZE 路由
- ✅ `TreeSitterSummarizer` - 代码感知摘要 (AST-based)
- ✅ `NetworkXCycleDetector` - 循环检测增强 (DFS + Tarjan)
- ✅ Pydantic V2 数据模型 - `models_v2.py` + `models_v2_compat.py` 兼容层

**依赖**:
```
sumy>=0.11.0
jieba>=0.42.1  # 中文分词
tree-sitter>=0.21.0
tree-sitter-python>=0.21.0
networkx>=3.2
pydantic>=2.6.0
```

### Phase 3: 智能压缩层 (进行中) 🚧

**已完成**:
- ✅ `LLMLinguaSummarizer` - 语义压缩 (基于 perplexity)
- ✅ `CompressionStateTracker` - 压缩状态追踪 (LRU + 去重)
- ✅ `TieredSummarizer` 集成压缩追踪

**待实现**:
- 压缩质量评估指标
- 迭代压缩优化

### Phase 4: 高级路由优化 (进行中) 🚧

**已完成**:
- ✅ `ORToolsBudgetOptimizer` - OR-Tools 背包求解 (带贪心回退)
- ✅ `HeuristicBudgetOptimizer` - 启发式预算优化
- 动态权重学习 (待实现)

**待实现**:
- 性能基准测试
- WindowCollector 集成优化

**依赖**:
```
ortools>=9.9.0  # 可选，启发式作为 fallback
```

---

## 四、关键实现

### 4.1 分层摘要架构

```python
from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

summarizer = TieredSummarizer()

# 自动选择最佳策略
summary = summarizer.summarize(
    content=long_log,
    max_tokens=300,
    content_type="log",  # log | code | dialogue | json
)
```

**策略链**:
1. **log/error**: sumy → truncation
2. **code**: tree-sitter (待实现) → sumy → truncation
3. **dialogue**: transformers (待实现) → sumy → truncation
4. **default**: sumy → truncation

### 4.2 关键错误关键字保留

```python
CRITICAL_KEYWORDS = frozenset({
    "error", "exception", "failed", "failure", "crash", "abort",
    "timeout", "deadlock", "corruption", "traceback",
})
```

摘要器会自动检查并确保这些关键字不会被丢失。

---

## 五、依赖管理

```bash
# 安装基础依赖（必需）
pip install sumy jieba

# 安装完整依赖（可选）
pip install -r requirements-contextos.txt
```

---

## 六、验证状态

| 测试 | 状态 |
|------|------|
| T1.1 启发式路由规则 | ✅ PASS |
| T1.2 并发锁保护 | ✅ PASS |
| T1.3 异常检测 | ✅ PASS |
| T1.4 返回类型修复 | ✅ PASS |
| SumySummarizer 可用性 | ✅ PASS |
| TieredSummarizer 降级 | ✅ PASS |
| TreeSitterSummarizer 可用性 | ✅ PASS |
| LLMLinguaSummarizer 可用性 | ✅ PASS (optional) |
| 代码摘要功能 | ✅ PASS |
| 日志摘要关键字保留 | ✅ PASS |
| NetworkXCycleDetector | ✅ PASS |
| CompressionStateTracker | ✅ PASS |
| ORToolsBudgetOptimizer | ✅ PASS |
| Pydantic V2 模型验证 | ✅ PASS |
| Pydantic V2 兼容性转换 | ✅ PASS |
| ruff 检查 | ✅ PASS |
| mypy 检查 | ✅ PASS |

---

## 七、相关文档

- **ADR-0067**: ContextOS 2.0 摘要策略选型
- **ADR-0066**: Benchmark Framework Convergence

---

**蓝图作者**: ContextOS 2.0 架构团队  
**更新日期**: 2026-04-11  
**状态**: Phase 2 实施中
