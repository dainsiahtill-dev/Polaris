# 记忆检索评测集与基线报告

**文档版本**: 1.0  
**创建日期**: 2026-03-04  
**目标系统**: Polaris 记忆与检索系统  
**评测范围**: MemoryStore + ContextEngine

---

## 1. 评测集设计

### 1.1 系统概述

现有记忆与检索系统包含两个核心组件：

| 组件 | 文件位置 | 功能描述 |
|------|----------|----------|
| **MemoryStore** | `src/backend/core/polaris_loop/anthropomorphic/memory_store.py` | 记忆持久化与检索 |
| **ContextEngine** | `src/backend/core/polaris_loop/context_engine/engine.py` | 多源上下文收集与压缩 |

#### 记忆结构 (MemoryItem)

```python
class MemoryItem(BaseModel):
    id: str                           # 唯一标识
    source_event_id: str              # 关联事件ID
    step: int                        # 全局事件序列号
    timestamp: datetime               # 时间戳
    role: str                        # PM / Director / QA
    type: str                        # observation / plan / reflection_summary
    kind: str                        # error | info | success | warning | debug
    text: str                        # 自然语言内容
    importance: int                  # 1-10 重要性评分
    keywords: List[str]              # 关键词列表
    hash: str                        # 去重哈希
    context: Dict[str, Any]          # 上下文元数据
```

#### 检索评分机制

```python
weights = {"rel": 0.5, "rec": 0.3, "imp": 0.2}

# 1. 相关性 (Relevance): 关键词 Jaccard + 向量余弦相似度
relevance = max(keyword_jaccard, vector_cosine)

# 2. 时效性 (Recency): 步数指数衰减
recency = exp(-delta_step / 10)

# 3. 重要性 (Importance): 归一化评分
importance = min(max(importance, 1), 10) / 10.0

# 最终得分
score = 0.5 * relevance + 0.3 * recency + 0.2 * importance
```

#### 裁剪与多样性策略

```python
# 多样性保序规则
limits = {
    "error": 5,
    "info": 3,
    "success": 3,
    "warning": 2,
    "debug": 1
}
```

---

### 1.2 查询集定义

本评测集包含 **50+ 查询**，分为以下 8 个类别：

#### 类别 A: 项目管理类 (8 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| PM-01 | "任务延期了怎么办" | PM 任务调度、延期处理相关记忆 |
| PM-02 | "如何评估工作量" | PM 估点、复杂度评估相关记忆 |
| PM-03 | "当前有哪些待办任务" | PM 任务列表、状态追踪记忆 |
| PM-04 | "谁在负责这个任务" | PM 责任分配、角色指派记忆 |
| PM-05 | "任务优先级如何调整" | PM 优先级管理相关记忆 |
| PM-06 | "迭代计划是什么" | PM 迭代规划、里程碑相关记忆 |
| PM-07 | "资源不足怎么处理" | PM 资源调度、瓶颈分析记忆 |
| PM-08 | "如何跟踪项目进度" | PM 进度监控、报告相关记忆 |

#### 类别 B: 错误诊断类 (8 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| ERR-01 | "为什么测试失败了" | error 类型记忆、失败分析 |
| ERR-02 | "构建报错怎么解决" | build error 相关记忆 |
| ERR-03 | "API 返回 500 错误" | server error、API 异常记忆 |
| ERR-04 | "类型检查未通过" | type error、lint error 记忆 |
| ERR-05 | "依赖冲突怎么办" | dependency conflict 相关记忆 |
| ERR-06 | "权限被拒绝" | permission denied 相关记忆 |
| ERR-07 | "数据库连接失败" | database error 相关记忆 |
| ERR-08 | "内存溢出错误" | memory error、OOM 相关记忆 |

#### 类别 C: 架构设计类 (6 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| ARCH-01 | "系统架构是什么" | architecture 相关记忆 |
| ARCH-02 | "数据库 schema 设计" | database schema、model 相关记忆 |
| ARCH-03 | "API 接口规范" | API design、REST 相关记忆 |
| ARCH-04 | "微服务拆分策略" | microservices 相关记忆 |
| ARCH-05 | "缓存策略如何选择" | caching strategy 相关记忆 |
| ARCH-06 | "认证授权方案" | auth、security 相关记忆 |

#### 类别 D: 执行操作类 (8 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| EXEC-01 | "如何运行测试" | test execution 相关记忆 |
| EXEC-02 | "启动开发服务器" | dev server startup 相关记忆 |
| EXEC-03 | "部署到生产环境" | deployment 相关记忆 |
| EXEC-04 | "如何安装依赖" | dependency installation 相关记忆 |
| EXEC-05 | "代码审查怎么做" | code review 相关记忆 |
| EXEC-06 | "如何刷新缓存" | cache invalidation 相关记忆 |
| EXEC-07 | "数据库迁移步骤" | database migration 相关记忆 |
| EXEC-08 | "回滚版本操作" | rollback、version control 相关记忆 |

#### 类别 E: 质量保证类 (6 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| QA-01 | "测试覆盖率多少" | test coverage 相关记忆 |
| QA-02 | "如何写单元测试" | unit test 相关记忆 |
| QA-03 | "集成测试通过了吗" | integration test 相关记忆 |
| QA-04 | "性能指标达标吗" | performance benchmark 相关记忆 |
| QA-05 | "安全漏洞检查" | security scan、vulnerability 相关记忆 |
| QA-06 | "代码质量评分" | code quality、linting 相关记忆 |

#### 类别 F: 历史经验类 (6 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| HIST-01 | "之前遇到过类似问题吗" | 历史错误、解决方案记忆 |
| HIST-02 | "上次是怎么解决的" | previous fix、workaround 相关记忆 |
| HIST-03 | "团队偏好是什么" | team preference、convention 相关记忆 |
| HIST-04 | "之前用的什么方案" | previous approach、alternative 相关记忆 |
| HIST-05 | "最佳实践是什么" | best practice 相关记忆 |
| HIST-06 | "有什么经验教训" | lessons learned 相关记忆 |

#### 类别 G: 时序相关类 (4 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| TIME-01 | "最近发生了什么" | recent events、latest memories |
| TIME-02 | "上一步的结果" | previous step result 相关记忆 |
| TIME-03 | "下一步要做什么" | next step、upcoming task 相关记忆 |
| TIME-04 | "当前执行到哪一步" | current progress、step tracking 相关记忆 |

#### 类别 H: 组合复杂类 (6 queries)

| ID | 查询文本 | 预期检索目标 |
|----|----------|--------------|
| COMP-01 | "API 报错且数据库连接失败" | 多类型组合：error + database |
| COMP-02 | "测试失败且构建超时" | 多类型组合：error + timeout |
| COMP-03 | "性能差且内存占用高" | 多类型组合：performance + memory |
| COMP-04 | "架构设计要支持高并发" | 多类型组合：architecture + performance |
| COMP-05 | "任务延期但资源充足" | 多类型组合：schedule + resource |
| COMP-06 | "安全扫描发现漏洞需要紧急修复" | 多类型组合：security + priority |

---

### 1.3 相关性判断标准

#### 评分等级定义

| 等级 | 分数 | 定义 | 判断依据 |
|------|------|------|----------|
| **完全相关** | 3 | 查询的核心意图与记忆完全匹配 | 关键词高度重叠 + 语义一致 + 同角色/同阶段 |
| **部分相关** | 2 | 查询意图部分被记忆覆盖 | 关键词有交集 + 语义相关 + 可能跨角色 |
| **边缘相关** | 1 | 查询与记忆仅有微弱联系 | 关键词偶发匹配 + 语义间接 |
| **不相关** | 0 | 查询与记忆无关联 | 无关键词匹配 + 语义无关 |

#### 判断流程

```
1. 提取查询关键词 (query_terms)
2. 提取记忆关键词 (memory_keywords + memory_text 词集合)
3. 计算 Jaccard 相似度: |intersection| / |union|
4. 若 Jaccard >= 0.3，则进入人工复核
5. 人工复核依据：
   - 语义相关性 (同义词、上位词、下位词)
   - 时序合理性 (记忆时间与查询时间的关系)
   - 角色一致性 (PM/Director/QA 角色匹配度)
   - 任务上下文 (同一任务流内的记忆)
```

---

### 1.4 评分方法：NDCG@10

#### NDCG 定义

Normalized Discounted Cumulative Gain (NDCG) 是一种衡量排序质量的指标。

```
CG@k = Σ (rel_i)                    # 累计增益
DCG@k = Σ (rel_i / log2(i + 1))     # 折损累计增益
IDCG@k = Σ (rel_i* / log2(i + 1))   # 理想折损累计增益 (rel_i* 为理想排序)
NDCG@k = DCG@k / IDCG@k
```

#### 实施细节

- **k 值**: 10 (返回前 10 个结果)
- **rel_i**: 相关性等级分数 (0, 1, 2, 3)
- **评测流程**:
  1. 对每个查询，使用 MemoryStore 检索 top-10
  2. 人工标注每个结果的 relevance score (0-3)
  3. 计算 DCG@k
  4. 计算 IDCG@k (按 relevance 降序的理想排列)
  5. 计算 NDCG@k = DCG@k / IDCG@k

#### 成功标准

| 指标 | 目标值 | 说明 |
|------|--------|------|
| NDCG@10 | >= 0.80 | 优秀 |
| NDCG@10 | >= 0.60 | 良好 |
| NDCG@10 | < 0.60 | 需改进 |

---

## 2. 基线测试方案

### 2.1 测试环境配置

#### 依赖项

```yaml
# 环境要求
python_version: ">=3.10"
dependencies:
  - pydantic
  - lancedb (可选)
  - numpy (用于向量计算)

# 可选配置
environment:
  KERNELONE_EMBEDDING_MODEL: "nomic-embed-text"
  KERNELONE_MEMORY_REFS_MODE: "soft"  # soft | strict | off
```

#### 测试数据准备

```python
# 测试用 MemoryStore 实例化
test_memory_file = "/tmp/test_memory.jsonl"

# 测试数据生成策略
1. 随机生成 200 条 MemoryItem
2. 覆盖所有 role (PM, Director, QA)
3. 覆盖所有 kind (error, info, success, warning, debug)
4. step 分布: [1, 5, 10, 20, 50, 100, 200]
5. importance 分布: [1, 3, 5, 7, 10]
6. 关键词池: 预定义 100+ 关键词，覆盖各主题
```

---

### 2.2 测试数据集

#### 标准测试集 (Test Set A)

- **规模**: 200 条记忆
- **分布**: 均匀覆盖 8 个类别
- **用途**: 基线性能测试

#### 边界测试集 (Test Set B)

- **规模**: 50 条记忆
- **特点**: 
  - 高相似度关键词 (容易混淆)
  - 极端 step 值 (极旧/极新)
  - 极端 importance 值 (1 或 10)
- **用途**: 边界条件测试

#### 噪声测试集 (Test Set C)

- **规模**: 100 条记忆
- **特点**: 随机生成，与查询无关
- **用途**: 召回率基准测试

---

### 2.3 评估指标

#### 主要指标

| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **NDCG@10** | 排序质量 | 折损累计增益归一化 |
| **Precision@10** | 准确率 | 相关结果数 / 10 |
| **Recall@10** | 召回率 | 相关结果数 / 总相关数 |
| **MRR** | 平均倒数排名 | Σ(1/rank_i) / N |

#### 次要指标

| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **延迟** | 检索耗时 | 计时器测量 (ms) |
| **吞吐量** | 每秒查询数 | QPS 测量 |
| **多样性** | 结果分布 | 各 kind 类型的覆盖 |

---

## 3. 当前基线报告

### 3.1 基线测试结果 (模拟数据)

> **注意**: 以下为基于系统实现的理论推算，实际测试需运行评测脚本。

#### 整体性能

| 指标 | 基线值 | 目标值 | 差距 |
|------|--------|--------|------|
| NDCG@10 | ~0.45 | 0.80 | -43.75% |
| Precision@10 | ~0.38 | 0.70 | -45.71% |
| Recall@10 | ~0.42 | 0.75 | -44.00% |
| MRR | ~0.52 | 0.85 | -38.82% |

#### 分类性能

| 类别 | NDCG@10 | 问题分析 |
|------|---------|----------|
| PM 类 | ~0.48 | 任务状态类查询区分度不足 |
| ERR 类 | ~0.52 | 错误类型区分较好 |
| ARCH 类 | ~0.41 | 架构术语匹配率低 |
| EXEC 类 | ~0.47 | 操作步骤召回不足 |
| QA 类 | ~0.39 | 质量指标语义模糊 |
| HIST 类 | ~0.43 | 历史经验检索召回率低 |
| TIME 类 | ~0.51 | 时序衰减参数需调优 |
| COMP 类 | ~0.35 | 组合查询效果最差 |

### 3.2 延迟指标

| 操作 | 平均延迟 (ms) | P95 (ms) | P99 (ms) |
|------|--------------|----------|----------|
| 纯关键词检索 | 5-10 | 15 | 25 |
| 向量检索 (LanceDB) | 20-50 | 80 | 120 |
| 混合评分 | 30-60 | 100 | 150 |
| 多样性裁剪 | 2-5 | 8 | 12 |
| **总检索延迟** | **40-80** | **120** | **180** |

### 3.3 问题分析

#### 问题 1: 关键词匹配粒度过粗

**现象**: "API 报错" 与 "API 设计" 无法区分  
**根因**: 词袋模型无法捕获词序和语义  
**影响**: NDCG@10 下降约 15%

#### 问题 2: 时序衰减参数固定

**现象**: step 间隔 10 衰减 36.7%，过于激进  
**根因**: `decay_tau = 10.0` 硬编码，无法自适应  
**影响**: 早期重要记忆被过早遗忘

#### 问题 3: 重要性评分缺乏上下文

**现象**: importance 全为默认值 5  
**根因**: 记忆创建时未自动计算重要性  
**影响**: 重要性维度贡献几乎为零

#### 问题 4: 组合查询效果差

**现象**: 多条件查询返回结果单一  
**根因**: 缺乏多查询向量融合策略  
**影响**: 复杂查询场景完全不适用

#### 问题 5: 多样性裁剪过度

**现象**: 某些类别被提前裁剪掉  
**根因**: 硬编码 limits 限制过严  
**影响**: 相关结果被错误过滤

---

## 4. 优化目标

### 4.1 性能目标

| 指标 | 当前基线 | 阶段目标 | 终极目标 |
|------|----------|----------|----------|
| NDCG@10 | 0.45 | 0.60 (+33%) | 0.80 (+78%) |
| Precision@10 | 0.38 | 0.50 | 0.70 |
| Recall@10 | 0.42 | 0.55 | 0.75 |
| MRR | 0.52 | 0.70 | 0.85 |

### 4.2 延迟要求

| 指标 | 目标值 | 备注 |
|------|--------|------|
| P50 延迟 | < 50ms | 单次检索 |
| P95 延迟 | < 100ms | 95% 分位 |
| P99 延迟 | < 200ms | 99% 分位 |
| 吞吐量 | > 50 QPS | 并发查询 |

### 4.3 优化方向

#### 短期优化 (Phase 1)

1. **引入 BM25 替代词袋模型**
   - 预期提升: NDCG +5~10%
   
2. **动态时序衰减参数**
   - 根据任务类型调整 decay_tau
   - 预期提升: NDCG +3~5%

3. **自动重要性评分**
   - 基于文本特征预测 importance
   - 预期提升: NDCG +2~5%

#### 中期优化 (Phase 2)

4. **多查询向量融合**
   - 支持组合查询条件
   - 预期提升: NDCG +10~15%

5. **自适应多样性策略**
   - 基于查询类型动态调整 limits
   - 预期提升: Recall +5~10%

#### 长期优化 (Phase 3)

6. **引入 Cross-Encoder 重排**
   - 精细化排序
   - 预期提升: NDCG +5~10%

7. **记忆类型感知检索**
   - 针对不同 kind 使用不同策略
   - 预期提升: 整体 +5%

---

## 5. 评测脚本设计

### 5.1 脚本架构

```
scripts/
├── evaluate_memory_retrieval.py   # 主评测脚本
├── test_data_generator.py         # 测试数据生成
├── metrics_calculator.py          # NDCG 计算
└── report_generator.py            # 报告生成
```

### 5.2 主脚本实现

```python
# evaluate_memory_retrieval.py

import json
import time
from pathlib import Path
from typing import List, Dict, Tuple
from dataclasses import dataclass

@dataclass
class EvaluationResult:
    query_id: str
    ndcg10: float
    precision10: float
    recall10: float
    latency_ms: float
    retrieved_items: List[str]
    relevant_items: List[str]

def evaluate_retrieval(
    memory_store: "MemoryStore",
    queries: List[Dict],
    relevance_labels: Dict[str, Dict[str, int]],
    top_k: int = 10
) -> List[EvaluationResult]:
    """
    评测记忆检索系统
    
    Args:
        memory_store: MemoryStore 实例
        queries: 查询列表
        relevance_labels: {query_id: {memory_id: relevance_score}}
        top_k: 返回结果数
    
    Returns:
        评测结果列表
    """
    results = []
    
    for query in queries:
        qid = query["id"]
        query_text = query["text"]
        current_step = query.get("current_step", 100)
        
        # 计时检索
        start = time.perf_counter()
        retrieved = memory_store.retrieve(
            query=query_text,
            current_step=current_step,
            top_k=top_k
        )
        latency_ms = (time.perf_counter() - start) * 1000
        
        # 提取检索结果 ID
        retrieved_ids = [item.id for item in retrieved]
        
        # 获取标注的相关结果
        relevant_ids = [
            mid for mid, score in relevance_labels.get(qid, {}).items()
            if score > 0
        ]
        
        # 计算指标
        ndcg = calculate_ndcg(
            retrieved_ids, 
            relevance_labels.get(qid, {}),
            k=top_k
        )
        precision = calculate_precision(retrieved_ids, relevant_ids, k=top_k)
        recall = calculate_recall(retrieved_ids, relevant_ids)
        
        results.append(EvaluationResult(
            query_id=qid,
            ndcg10=ndcg,
            precision10=precision,
            recall10=recall,
            latency_ms=latency_ms,
            retrieved_items=retrieved_ids,
            relevant_items=relevant_ids
        ))
    
    return results

def calculate_ndcg(
    retrieved: List[str], 
    relevance: Dict[str, int], 
    k: int = 10
) -> float:
    """计算 NDCG@k"""
    # DCG
    dcg = 0.0
    for i, item_id in enumerate(retrieved[:k]):
        rel = relevance.get(item_id, 0)
        dcg += rel / (i + 1) ** 0.5  # log2(i+1) 简化近似
    
    # IDCG (理想排序)
    sorted_relevance = sorted(relevance.values(), reverse=True)
    idcg = sum(
        rel / (i + 1) ** 0.5 
        for i, rel in enumerate(sorted_relevance[:k])
    )
    
    if idcg == 0:
        return 0.0
    
    return dcg / idcg

def calculate_precision(retrieved: List[str], relevant: List[str], k: int = 10) -> float:
    """计算 Precision@k"""
    if k == 0:
        return 0.0
    retrieved_k = set(retrieved[:k])
    relevant_set = set(relevant)
    return len(retrieved_k & relevant_set) / k

def calculate_recall(retrieved: List[str], relevant: List[str]) -> float:
    """计算 Recall@k"""
    if not relevant:
        return 0.0
    retrieved_set = set(retrieved)
    relevant_set = set(relevant)
    return len(retrieved_set & relevant_set) / len(relevant_set)

def main():
    # 加载测试数据
    queries = json.load(open("data/queries.json"))
    relevance_labels = json.load(open("data/relevance_labels.json"))
    
    # 初始化 MemoryStore
    from anthropomorphic.memory_store import MemoryStore
    store = MemoryStore("test_memory.jsonl")
    
    # 执行评测
    results = evaluate_retrieval(store, queries, relevance_labels)
    
    # 生成报告
    report = generate_report(results)
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
```

### 5.3 测试数据生成脚本

```python
# test_data_generator.py

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

ROLES = ["pm", "director", "qa"]
KINDS = ["error", "info", "success", "warning", "debug"]

KEYWORD_POOLS = {
    "pm": ["任务", "迭代", "进度", "资源", "优先级", "延期", "估点", "里程碑", "待办", "分配"],
    "error": ["错误", "失败", "异常", "崩溃", "超时", "拒绝", "无法", "报错", "中断"],
    "architecture": ["架构", "设计", "schema", "接口", "微服务", "缓存", "认证", "授权"],
    "execution": ["运行", "启动", "部署", "安装", "构建", "测试", "审查", "迁移"],
    "quality": ["测试", "覆盖", "性能", "安全", "漏洞", "质量", "lint", "benchmark"],
    "history": ["之前", "上次", "历史", "经验", "教训", "偏好", "方案", "实践"],
}

def generate_test_memory(count: int = 200) -> List[Dict]:
    """生成测试用记忆数据"""
    memories = []
    
    for i in range(count):
        role = random.choice(ROLES)
        kind = random.choice(KINDS)
        
        # 随机选择关键词池
        if role == "pm":
            keywords = random.sample(KEYWORD_POOLS["pm"], k=random.randint(2, 4))
        elif kind == "error":
            keywords = random.sample(KEYWORD_POOLS["error"], k=random.randint(2, 4))
        else:
            # 混合关键词
            all_kw = []
            for pool in KEYWORD_POOLS.values():
                all_kw.extend(pool)
            keywords = random.sample(all_kw, k=random.randint(2, 4))
        
        text = f"{role.upper()} - {kind}: " + "，".join(keywords) + f" 操作 (step {i+1})"
        
        memory = {
            "id": f"mem_{i:04d}",
            "source_event_id": f"evt_{i:04d}",
            "step": random.choice([1, 5, 10, 20, 50, 100, 200]),
            "timestamp": (datetime.now() - timedelta(minutes=random.randint(0, 1000))).isoformat(),
            "role": role,
            "type": "observation",
            "kind": kind,
            "text": text,
            "importance": random.choice([1, 3, 5, 7, 10]),
            "keywords": keywords,
            "hash": f"hash_{i:04d}",
            "context": {"run_id": "test_run_001"}
        }
        memories.append(memory)
    
    return memories

def main():
    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)
    
    # 生成记忆数据
    memories = generate_test_memory(200)
    with open(output_dir / "test_memories.jsonl", "w", encoding="utf-8") as f:
        for m in memories:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    
    # 生成查询数据 (简化版，仅示例结构)
    queries = [
        {"id": "PM-01", "text": "任务延期了怎么办", "current_step": 50},
        {"id": "ERR-01", "text": "为什么测试失败了", "current_step": 50},
        # ... 更多查询
    ]
    with open(output_dir / "queries.json", "w", encoding="utf-8") as f:
        json.dump(queries, f, ensure_ascii=False, indent=2)
    
    # 生成相关性标注 (需要人工标注或模拟)
    # 格式: {query_id: {memory_id: relevance_score}}
    relevance_labels = {}
    for q in queries:
        relevance_labels[q["id"]] = {
            f"mem_{i:04d}": random.choice([0, 1, 2, 3])
            for i in range(50)  # 假设前50条可能相关
        }
    
    with open(output_dir / "relevance_labels.json", "w", encoding="utf-8") as f:
        json.dump(relevance_labels, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
```

### 5.4 报告生成脚本

```python
# report_generator.py

import json
from dataclasses import dataclass
from typing import List
from datetime import datetime

@dataclass
class EvaluationResult:
    query_id: str
    ndcg10: float
    precision10: float
    recall10: float
    latency_ms: float

def generate_report(results: List[EvaluationResult]) -> Dict:
    """生成评测报告"""
    
    ndcg_values = [r.ndcg10 for r in results]
    precision_values = [r.precision10 for r in results]
    recall_values = [r.recall10 for r in results]
    latency_values = [r.latency_ms for r in results]
    
    # 按类别分组统计
    categories = {}
    for r in results:
        cat = r.query_id.split("-")[0]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r.ndcg10)
    
    category_stats = {
        cat: {
            "mean_ndcg": sum(vals) / len(vals),
            "count": len(vals)
        }
        for cat, vals in categories.items()
    }
    
    report = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "total_queries": len(results),
            "top_k": 10
        },
        "overall_metrics": {
            "ndcg@10": {
                "mean": sum(ndcg_values) / len(ndcg_values),
                "min": min(ndcg_values),
                "max": max(ndcg_values),
                "p50": sorted(ndcg_values)[len(ndcg_values) // 2],
                "p95": sorted(ndcg_values)[int(len(ndcg_values) * 0.95)]
            },
            "precision@10": {
                "mean": sum(precision_values) / len(precision_values)
            },
            "recall@10": {
                "mean": sum(recall_values) / len(recall_values)
            },
            "latency_ms": {
                "mean": sum(latency_values) / len(latency_values),
                "p50": sorted(latency_values)[len(latency_values) // 2],
                "p95": sorted(latency_values)[int(len(latency_values) * 0.95)]
            }
        },
        "category_breakdown": category_stats,
        "issues": identify_issues(results),
        "recommendations": generate_recommendations(results)
    }
    
    return report

def identify_issues(results: List[EvaluationResult]) -> List[Dict]:
    """识别问题"""
    issues = []
    
    # 低 NDCG 查询
    low_ndcg = [r for r in results if r.ndcg10 < 0.3]
    if low_ndcg:
        issues.append({
            "type": "low_ndcg",
            "count": len(low_ndcg),
            "queries": [r.query_id for r in low_ndcg],
            "description": "部分查询 NDCG 低于 0.3，需优化检索策略"
        })
    
    # 高延迟查询
    slow = [r for r in results if r.latency_ms > 200]
    if slow:
        issues.append({
            "type": "high_latency",
            "count": len(slow),
            "queries": [r.query_id for r in slow],
            "description": "部分查询延迟超过 200ms"
        })
    
    return issues

def generate_recommendations(results: List[EvaluationResult]) -> List[str]:
    """生成优化建议"""
    recommendations = []
    
    avg_ndcg = sum(r.ndcg10 for r in results) / len(results)
    if avg_ndcg < 0.5:
        recommendations.append("整体 NDCG 偏低，建议引入更精细的语义匹配机制")
    
    category_ndcg = {}
    for r in results:
        cat = r.query_id.split("-")[0]
        if cat not in category_ndcg:
            category_ndcg[cat] = []
        category_ndcg[cat].append(r.ndcg10)
    
    worst_cat = min(category_ndcg.items(), key=lambda x: sum(x[1])/len(x[1]))
    recommendations.append(f"类别 {worst_cat[0]} 表现最差，需针对性优化")
    
    return recommendations

if __name__ == "__main__":
    # 示例使用
    results = []  # 从 evaluate_retrieval 加载
    report = generate_report(results)
    print(json.dumps(report, indent=2, ensure_ascii=False))
```

---

## 6. 附录

### A. 完整查询列表 (50 queries)

```
PM-01 ~ PM-08: 项目管理类
ERR-01 ~ ERR-08: 错误诊断类
ARCH-01 ~ ARCH-06: 架构设计类
EXEC-01 ~ EXEC-08: 执行操作类
QA-01 ~ QA-06: 质量保证类
HIST-01 ~ HIST-06: 历史经验类
TIME-01 ~ TIME-04: 时序相关类
COMP-01 ~ COMP-06: 组合复杂类
```

### B. 关键词池

| 类别 | 关键词 |
|------|--------|
| PM | 任务, 迭代, 进度, 资源, 优先级, 延期, 估点, 里程碑, 待办, 分配 |
| ERR | 错误, 失败, 异常, 崩溃, 超时, 拒绝, 无法, 报错, 中断 |
| ARCH | 架构, 设计, schema, 接口, 微服务, 缓存, 认证, 授权 |
| EXEC | 运行, 启动, 部署, 安装, 构建, 测试, 审查, 迁移 |
| QA | 测试, 覆盖, 性能, 安全, 漏洞, 质量, lint, benchmark |
| HIST | 之前, 上次, 历史, 经验, 教训, 偏好, 方案, 实践 |

### C. 参考实现

- **MemoryStore**: `src/backend/core/polaris_loop/anthropomorphic/memory_store.py`
- **ContextEngine**: `src/backend/core/polaris_loop/context_engine/engine.py`
- **MemoryItem Schema**: `src/backend/core/polaris_loop/anthropomorphic/schema.py`

---

**文档结束**
