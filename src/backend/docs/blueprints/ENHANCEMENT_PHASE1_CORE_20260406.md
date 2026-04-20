# Polaris 强化 - 第一阶段：核心强化

**版本**: v1.0.0
**日期**: 2026-04-06
**状态**: 待执行
**工期**: 4周
**人力**: 4人
**目标评分**: 72/100 (从68/100)

---

## 一、任务总览

| 任务 | 目标 | 工作量 | 优先级 |
|------|------|--------|--------|
| S1-1: 增强规划引擎 | ReAct/ToT增强 + 自我反思 | 40h | P0 |
| S1-2: 工具组合器 | 复杂任务自动工具链生成 | 32h | P0 |
| S1-3: 记忆增强 | LanceDB + 全文检索 + 知识图谱基础 | 40h | P1 |
| S1-4: 上下文压缩 | 智能摘要 + 重要性排序 | 24h | P1 |
| S1-5: 错误自愈 | 失败自动重试 + 备选策略 | 24h | P0 |

---

## 二、任务详情

### S1-1: 增强规划引擎

**目标**: 让规划引擎具备自我反思能力

**实现方案**:

```python
# polaris/kernelone/planning/self_reflective_engine.py

from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio

@dataclass
class Reflection:
    """反思结果"""
    is_reasonable: bool
    missing_info: tuple[str, ...]
    suggested_action: str | None
    needs_rethink: bool

@dataclass
class EnhancedPlanStep:
    """增强计划步骤"""
    id: str
    description: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    estimated_duration: int | None = None
    confidence: float = 1.0
    alternatives: tuple[str, ...] = field(default_factory=tuple)

class SelfReflectivePlanner:
    """带自我反思的规划器"""

    def __init__(
        self,
        llm: LLMProvider,
        base_planner: PlanSolveEngine,
    ) -> None:
        self._llm = llm
        self._base = base_planner

    async def plan(
        self,
        goal: str,
        constraints: Constraints,
    ) -> Plan:
        """规划 - 包含自我反思"""
        # 1. 基础规划
        initial_plan = await self._base.plan(goal, constraints)

        # 2. 自我反思
        reflection = await self._reflect(initial_plan, goal)

        # 3. 如果需要，重新规划
        if reflection.needs_rethink:
            refined_goal = self._incorporate_feedback(goal, reflection)
            initial_plan = await self._base.plan(refined_goal, constraints)

        # 4. 添加备选方案
        return await self._add_alternatives(initial_plan, reflection)

    async def _reflect(
        self,
        plan: Plan,
        goal: str,
    ) -> Reflection:
        """反思计划是否合理"""
        reflection_prompt = f"""
        目标: {goal}
        计划:
        {self._format_plan(plan)}

        请评估:
        1. 这个计划是否完整覆盖了目标?
        2. 是否遗漏了重要步骤?
        3. 步骤顺序是否合理?
        4. 是否有更优的替代方案?

        返回JSON格式:
        {{
            "is_reasonable": true/false,
            "missing_info": ["缺失1", "缺失2"],
            "suggested_action": "改进建议或null",
            "needs_rethink": true/false
        }}
        """
        response = await self._llm.complete(reflection_prompt)
        return self._parse_reflection(response)

    async def _add_alternatives(
        self,
        plan: Plan,
        reflection: Reflection,
    ) -> Plan:
        """为每个步骤添加备选方案"""
        enhanced_steps = []

        for step in plan.steps:
            alternatives = await self._generate_alternatives(step)
            enhanced_step = EnhancedPlanStep(
                id=step.id,
                description=step.description,
                depends_on=step.depends_on,
                estimated_duration=step.estimated_duration,
                confidence=0.9 if reflection.needs_rethink else 1.0,
                alternatives=alternatives,
            )
            enhanced_steps.append(enhanced_step)

        return Plan(steps=tuple(enhanced_steps), metadata=plan.metadata)
```

**验收标准**:
- [ ] SelfReflectivePlanner实现完整
- [ ] 反思机制在>50%的复杂任务中被触发
- [ ] 复杂任务成功率从65%提升到85%
- [ ] 单元测试覆盖

---

### S1-2: 工具组合器

**目标**: 给定高层目标，自动生成工具调用链

**实现方案**:

```python
# polaris/kernelone/tool_execution/composer.py

from dataclasses import dataclass
from typing import Any

@dataclass
class ToolCapability:
    """工具能力描述"""
    tool_name: str
    input_type: str
    output_type: str
    description: str
    semantic_tags: tuple[str, ...]

@dataclass
class CompositionResult:
    """组合结果"""
    graph: ToolCallGraph
    confidence: float
    reasoning: str

class ToolComposer:
    """工具组合器 - 从需求自动生成工具链"""

    def __init__(
        self,
        tool_registry: ToolSpecRegistry,
        llm: LLMProvider,
    ) -> None:
        self._registry = tool_registry
        self._llm = llm

    async def compose(
        self,
        goal: str,
        constraints: Constraints,
    ) -> CompositionResult:
        """将高层目标分解为工具调用图"""
        # 1. 分析目标
        goal_analysis = await self._analyze_goal(goal)

        # 2. 选择工具
        selected_tools = await self._select_tools(
            goal_analysis.required_capabilities,
        )

        # 3. 排序依赖
        execution_order = self._resolve_dependencies(selected_tools)

        # 4. 构建图
        graph = self._build_graph(execution_order, goal_analysis)

        # 5. 评估置信度
        confidence = await self._evaluate_confidence(graph, goal)

        return CompositionResult(
            graph=graph,
            confidence=confidence,
            reasoning=goal_analysis.reasoning,
        )

    async def _analyze_goal(self, goal: str) -> GoalAnalysis:
        """分析目标"""
        prompt = f"""
        目标: {goal}

        请分析:
        1. 这个目标需要什么能力?
        2. 工具调用的顺序应该是怎样的?
        3. 有哪些可能的分支?

        返回JSON格式:
        {{
            "required_capabilities": ["capability1", "capability2"],
            "execution_order_hint": "顺序提示",
            "reasoning": "分析理由"
        }}
        """
        response = await self._llm.complete(prompt)
        return self._parse_goal_analysis(response)

    async def _select_tools(
        self,
        capabilities: list[str],
    ) -> list[ToolSelection]:
        """选择工具"""
        all_tools = self._registry.get_all_tools()
        selections = []

        for cap in capabilities:
            # 向量相似度匹配
            best_match = self._match_tool(cap, all_tools)
            selections.append(best_match)

        return selections

    def _build_graph(
        self,
        tools: list[ToolSelection],
        analysis: GoalAnalysis,
    ) -> ToolCallGraph:
        """构建工具调用图"""
        nodes = []
        edges = []

        for i, selection in enumerate(tools):
            node_id = f"step_{i}"
            nodes.append(ToolCallNode(
                id=node_id,
                tool_call=selection.tool.to_tool_call(),
                condition=None,
            ))

            # 添加依赖边
            if i > 0:
                edges.append(ToolCallEdge(
                    from_id=f"step_{i-1}",
                    to_id=node_id,
                ))

        return ToolCallGraph(
            nodes=tuple(nodes),
            edges=tuple(edges),
            entry_points=("step_0",) if nodes else (),
        )
```

**验收标准**:
- [ ] ToolComposer实现完整
- [ ] 工具选择准确率 > 80%
- [ ] 新任务适配时间 < 5分钟
- [ ] 集成测试覆盖

---

### S1-3: 记忆增强

**目标**: 从向量存储升级到混合存储（向量+全文+图谱）

**实现方案**:

```python
# polaris/kernelone/akashic/hybrid_memory.py

from dataclasses import dataclass
from typing import Any

@dataclass
class HybridMemoryConfig:
    """混合记忆配置"""
    vector_store_path: str
    fulltext_index_path: str
    graph_db_uri: str | None = None
    embedding_model: str = "nomic-embed-text"
    hybrid_weight_vector: float = 0.4
    hybrid_weight_fulltext: float = 0.3
    hybrid_weight_graph: float = 0.3

class HybridMemory:
    """混合记忆系统 - 向量+全文+图谱"""

    def __init__(self, config: HybridMemoryConfig) -> None:
        self._config = config
        self._vector = LanceDBVectorStore(config.vector_store_path)
        self._fulltext = WhooshFullTextIndex(config.fulltext_index_path)
        self._graph = Neo4jGraph(config.graph_db_uri) if config.graph_db_uri else None

    async def store(self, memory: Memory) -> None:
        """存储记忆到三层"""
        # 1. 存储向量
        embedding = await self._compute_embedding(memory.content)
        await self._vector.add(
            id=memory.id,
            embedding=embedding,
            content=memory.content,
            metadata=memory.metadata,
        )

        # 2. 全文索引
        await self._fulltext.add_document(
            doc_id=memory.id,
            content=memory.content,
            fields=memory.metadata,
        )

        # 3. 知识图谱 - 提取实体关系
        if self._graph:
            entities = await self._extract_entities(memory.content)
            relations = await self._extract_relations(memory.content)
            for entity in entities:
                await self._graph.add_entity(entity)
            for relation in relations:
                await self._graph.add_relation(relation)

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[MemoryResult]:
        """混合检索 - 向量+全文+图谱"""
        # 1. 并行执行三种检索
        vector_task = self._vector.search(query, top_k * 2)
        fulltext_task = self._fulltext.search(query, top_k * 2)
        graph_task = self._graph.search(query, top_k * 2) if self._graph else []

        vector_results, fulltext_results, graph_results = await asyncio.gather(
            vector_task, fulltext_task, graph_task
        )

        # 2. 融合排序
        return self._hybrid_merge(
            vector_results,
            fulltext_results,
            graph_results,
            top_k,
        )

    def _hybrid_merge(
        self,
        vector: list[ScoredResult],
        fulltext: list[ScoredResult],
        graph: list[ScoredResult],
        top_k: int,
    ) -> list[MemoryResult]:
        """混合排序"""
        scores: dict[str, float] = {}

        for r in vector:
            scores[r.id] = scores.get(r.id, 0) + r.score * self._config.hybrid_weight_vector

        for r in fulltext:
            scores[r.id] = scores.get(r.id, 0) + r.score * self._config.hybrid_weight_fulltext

        for r in graph:
            scores[r.id] = scores.get(r.id, 0) + r.score * self._config.hybrid_weight_graph

        # 排序返回top_k
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_k]

        return [self._get_memory_by_id(id) for id in sorted_ids]
```

**验收标准**:
- [ ] HybridMemory三层存储实现完整
- [ ] 向量检索精度 > 75%
- [ ] 全文检索精度 > 80%
- [ ] 图谱检索(如果有) > 70%
- [ ] 整体召回准确率 > 80%

---

### S1-4: 上下文压缩

**目标**: 智能摘要 + 重要性排序

**实现方案**:

```python
# polaris/kernelone/context/intelligent_compressor.py

from dataclasses import dataclass
from typing import Any
import heapq

@dataclass
class CompressionResult:
    """压缩结果"""
    compressed_content: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    preserved_key_points: tuple[str, ...]

class ImportanceScorer:
    """重要性评分器"""

    def score(self, item: ContextItem) -> float:
        """计算重要性分数"""
        score = 0.0

        # 1. 时间衰减
        score += self._time_decay(item.timestamp)

        # 2. 引用频率
        score += item.reference_count * 0.1

        # 3. 语义重要性
        if item.contains_decision:
            score += 0.5
        if item.contains_error:
            score += 0.3
        if item.contains_tool_result:
            score += 0.2

        # 4. 用户显式标记
        if item.is_pinned:
            score += 1.0

        return score

    def _time_decay(self, timestamp: datetime) -> float:
        """时间衰减"""
        hours_old = (datetime.now() - timestamp).total_seconds() / 3600
        return max(0.0, 1.0 - (hours_old / 168))  # 一周衰减到0

class IntelligentCompressor:
    """智能上下文压缩器"""

    def __init__(
        self,
        llm: LLMProvider,
        max_tokens: int = 32000,
    ) -> None:
        self._llm = llm
        self._max_tokens = max_tokens
        self._scorer = ImportanceScorer()

    async def compress(
        self,
        context: ContextOSProjection,
        target_tokens: int | None = None,
    ) -> CompressionResult:
        """智能压缩上下文"""
        target = target_tokens or int(self._max_tokens * 0.7)

        # 1. 评分所有项
        scored_items = [
            (self._scorer.score(item), item)
            for item in context.items
        ]
        heapq.heapify(scored_items)

        # 2. 贪心选择重要项
        selected = []
        total_tokens = 0

        while scored_items and total_tokens < target:
            score, item = heapq.heappop(scored_items)
            item_tokens = self._estimate_tokens(item)

            if total_tokens + item_tokens <= target:
                selected.append(item)
                total_tokens += item_tokens
            else:
                # 尝试摘要后添加
                summary = await self._summarize(item)
                summary_tokens = self._estimate_tokens(summary)
                if total_tokens + summary_tokens <= target:
                    selected.append(summary)
                    total_tokens += summary_tokens

        # 3. 构建压缩后上下文
        compressed = self._build_compressed_context(selected)

        return CompressionResult(
            compressed_content=compressed,
            original_tokens=context.total_tokens,
            compressed_tokens=total_tokens,
            compression_ratio=total_tokens / context.total_tokens if context.total_tokens > 0 else 1.0,
            preserved_key_points=tuple(item.key_point for item in selected if hasattr(item, 'key_point')),
        )

    async def _summarize(self, item: ContextItem) -> ContextItem:
        """摘要单个项"""
        prompt = f"""
        请简洁摘要以下内容，保留关键信息:

        {item.content}

        只返回摘要，不要其他解释。
        """
        summary = await self._llm.complete(prompt)
        return ContextItem(
            id=f"{item.id}_summary",
            content=summary,
            timestamp=item.timestamp,
            importance=item.importance,
            is_summary=True,
        )
```

**验收标准**:
- [ ] 重要性评分器实现完整
- [ ] 智能压缩保留关键信息
- [ ] 上下文利用率 > 90%
- [ ] 压缩比可配置

---

### S1-5: 错误自愈

**目标**: 失败自动重试 + 备选策略

**实现方案**:

```python
# polaris/kernelone/resilience/self_healing.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import asyncio

class FailureType(Enum):
    """失败类型"""
    TRANSIENT = "transient"  # 临时性失败，可重试
    PERMANENT = "permanent"  # 永久性失败，需换策略
    UNKNOWN = "unknown"      # 未知原因

@dataclass
class RetryStrategy:
    """重试策略"""
    max_attempts: int = 3
    base_delay: float = 1.0
    exponential_base: float = 2.0
    max_delay: float = 30.0
    jitter: bool = True

@dataclass
class AlternativeStrategy:
    """备选策略"""
    name: str
    description: str
    execute: Callable[..., Any]

@dataclass
class HealingResult:
    """自愈结果"""
    success: bool
    final_result: Any | None
    attempts: int
    strategies_tried: tuple[str, ...]
    final_error: str | None

class SelfHealingExecutor:
    """自愈执行器"""

    def __init__(
        self,
        retry_strategy: RetryStrategy,
        alternatives: list[AlternativeStrategy] | None = None,
    ) -> None:
        self._retry = retry_strategy
        self._alternatives = alternatives or []

    async def execute(
        self,
        primary_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> HealingResult:
        """带自愈的执行"""
        strategies_tried = []

        # 1. 尝试主策略 + 重试
        for attempt in range(self._retry.max_attempts):
            try:
                result = await primary_func(*args, **kwargs)
                return HealingResult(
                    success=True,
                    final_result=result,
                    attempts=attempt + 1,
                    strategies_tried=tuple(strategies_tried),
                    final_error=None,
                )

            except Exception as e:
                failure_type = self._classify_failure(e)

                if failure_type == FailureType.PERMANENT:
                    # 永久失败，不重试
                    break

                if attempt < self._retry.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    strategies_tried.append(f"retry_{attempt+1}")
                    await asyncio.sleep(delay)

        # 2. 尝试备选策略
        for alt in self._alternatives:
            try:
                strategies_tried.append(alt.name)
                result = await alt.execute(*args, **kwargs)
                return HealingResult(
                    success=True,
                    final_result=result,
                    attempts=len(strategies_tried),
                    strategies_tried=tuple(strategies_tried),
                    final_error=None,
                )
            except Exception:
                continue

        # 3. 所有策略都失败
        return HealingResult(
            success=False,
            final_result=None,
            attempts=len(strategies_tried),
            strategies_tried=tuple(strategies_tried),
            final_error="All strategies exhausted",
        )

    def _classify_failure(self, error: Exception) -> FailureType:
        """分类失败类型"""
        error_msg = str(error).lower()

        # 永久性失败模式
        permanent_patterns = [
            "not found", "invalid", "permission denied",
            "authentication failed", "unauthorized",
        ]

        for pattern in permanent_patterns:
            if pattern in error_msg:
                return FailureType.PERMANENT

        return FailureType.TRANSIENT

    def _calculate_delay(self, attempt: int) -> float:
        """计算重试延迟"""
        delay = self._retry.base_delay * (self._retry.exponential_base ** attempt)
        delay = min(delay, self._retry.max_delay)

        if self._retry.jitter:
            import random
            delay *= (0.5 + random.random())

        return delay
```

**验收标准**:
- [ ] SelfHealingExecutor实现完整
- [ ] 失败分类准确率 > 80%
- [ ] 自动恢复率 > 80%
- [ ] 备选策略触发正常

---

## 三、执行计划

### Week 1

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | S1-1: SelfReflectivePlanner设计 | AI Engineer-1 |
| Tue | S1-1: 实现 + 单元测试 | AI Engineer-1 |
| Wed | S1-2: ToolComposer设计 | Tool Engineer-1 |
| Thu | S1-2: 实现 + 单元测试 | Tool Engineer-1 |
| Fri | S1-3: HybridMemory设计 | Memory Engineer-1 |

### Week 2

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | S1-3: LanceDB + Whoosh集成 | Memory Engineer-1 |
| Tue | S1-3: 混合排序算法 | Memory Engineer-2 |
| Wed | S1-4: IntelligentCompressor设计 | AI Engineer-1 |
| Thu | S1-4: 实现 + 测试 | AI Engineer-2 |
| Fri | S1-5: SelfHealingExecutor设计 | Performance Engineer |

### Week 3

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | S1-5: 实现 + 测试 | Performance Engineer |
| Tue | 集成测试: S1-1 + S1-2 | All |
| Wed | 集成测试: S1-3 + S1-4 | All |
| Thu | 集成测试: S1-5 + 整体 | All |
| Fri | Bug修复 | All |

### Week 4

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | 性能测试 | Test Engineer |
| Tue | 文档完善 | All |
| Wed | 最终验收 | Principal Architect |
| Thu | Phase 1 总结 | All |
| Fri | Phase 2 准备 | - |

---

## 四、关键文件

```
polaris/kernelone/
├── planning/
│   └── self_reflective_engine.py      # S1-1
├── tool_execution/
│   └── composer.py                     # S1-2
├── akashic/
│   └── hybrid_memory.py               # S1-3
├── context/
│   └── intelligent_compressor.py      # S1-4
└── resilience/
    └── self_healing.py               # S1-5
```

---

## 五、验收清单

```markdown
## Phase 1 验收检查单

### S1-1: 增强规划引擎
- [ ] SelfReflectivePlanner实现
- [ ] 反思机制触发率 > 50%
- [ ] 复杂任务成功率 > 85%

### S1-2: 工具组合器
- [ ] ToolComposer实现
- [ ] 工具选择准确率 > 80%
- [ ] 适配时间 < 5分钟

### S1-3: 记忆增强
- [ ] HybridMemory三层存储
- [ ] 召回准确率 > 80%

### S1-4: 上下文压缩
- [ ] IntelligentCompressor实现
- [ ] 上下文利用率 > 90%

### S1-5: 错误自愈
- [ ] SelfHealingExecutor实现
- [ ] 自动恢复率 > 80%

### 整体
- [ ] AGI评分达到72/100
- [ ] pytest > 95% 通过
- [ ] ruff format零警告
- [ ] mypy --strict零错误
```
