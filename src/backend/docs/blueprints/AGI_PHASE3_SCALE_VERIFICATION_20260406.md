# AGI骨架进化 Phase 3: AGI规模验证

**版本**: v1.0.0
**日期**: 2026-04-06
**状态**: 待执行
**工期**: 12周
**人力**: 6人
**目标评分**: 80/100 (从75/100提升)

---

## 一、任务总览

| 任务 | 优先级 | 工作量 | 前置条件 |
|------|--------|--------|----------|
| 100+并发Agent压力测试 | P1 | 80h | Phase 2完成 |
| 知识积累自动化 | P2 | 64h | Phase 2完成 |
| 混合检索引擎 | P2 | 56h | Phase 1完成 |
| 全局记忆网络 | P2 | 72h | Phase 2完成 |
| AGI基准测试 | P1 | 48h | Phase 1完成 |

---

## 二、任务详情

### 2.1 任务T3-1: 100+并发Agent压力测试

**目标**: 验证系统在100+并发Agent下稳定运行1小时。

**测试场景**:

```python
# tests/agi_stress/test_100_agent_concurrent.py

import asyncio
import pytest
from datetime import datetime, timedelta
from polaris.kernelone.multi_agent.neural_syndicate import OrchestratorAgent
from polaris.kernelone.ipc.shared_memory_bus import SharedMemoryBus
from polaris.kernelone.resource.quota import ResourceQuota, ResourceQuotaManager

class Test100AgentConcurrent:
    """100+并发Agent压力测试"""

    @pytest.fixture
    async def orchestrator(self):
        bus = SharedMemoryBus(SharedMemoryConfig())
        await bus.initialize("stress_test")
        manager = ResourceQuotaManager()
        orch = OrchestratorAgent(bus=bus, quota_manager=manager)
        await orch.start()
        yield orch
        await orch.stop()

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_100_agents_1_hour_stability(self, orchestrator):
        """100 Agent并发1小时稳定性测试"""
        NUM_AGENTS = 100
        DURATION_HOURS = 1

        # 创建100个Agent
        agents = []
        for i in range(NUM_AGENTS):
            agent_id = f"stress_agent_{i}"
            quota = ResourceQuota(
                cpu_quota_ns=60_000_000_000 * DURATION_HOURS,
                memory_bytes=256 * 1024 * 1024,
                max_concurrent_tools=5,
                max_turns=100,
                max_wall_time_seconds=3600 * DURATION_HOURS,
            )
            orchestrator.allocate_agent(agent_id, quota)
            agents.append(agent_id)

        # 跟踪指标
        metrics = {
            "completed_turns": 0,
            "failed_turns": 0,
            "total_messages": 0,
            "peak_memory_bytes": 0,
            "peak_cpu_ns": 0,
        }

        # 启动并发任务
        async def agent_task(agent_id: str):
            start = datetime.now()
            turn = 0
            while (datetime.now() - start).total_seconds() < 3600 * DURATION_HOURS:
                try:
                    # 模拟Agent任务
                    result = await orchestrator.execute_turn(
                        agent_id,
                        f"task_{turn}",
                    )
                    metrics["completed_turns"] += 1

                    # 模拟消息交换
                    if turn % 5 == 0:
                        await orchestrator.broadcast(
                            agent_id,
                            {"type": "heartbeat", "turn": turn}
                        )
                        metrics["total_messages"] += 1

                except Exception as e:
                    metrics["failed_turns"] += 1

                turn += 1

                # 更新峰值资源
                resources = orchestrator.get_agent_resources(agent_id)
                if resources:
                    metrics["peak_memory_bytes"] = max(
                        metrics["peak_memory_bytes"],
                        resources.usage.memory_used_bytes,
                    )
                    metrics["peak_cpu_ns"] = max(
                        metrics["peak_cpu_ns"],
                        resources.usage.cpu_used_ns,
                    )

        # 并发执行
        tasks = [agent_task(aid) for aid in agents]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 验证
        assert metrics["completed_turns"] > 0, "No turns completed"
        assert metrics["failed_turns"] / (metrics["completed_turns"] + metrics["failed_turns"]) < 0.05, \
            f"Failure rate too high: {metrics['failed_turns']}"

        print(f"Metrics: {metrics}")

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_100_agents_ipc_throughput(self):
        """100 Agent IPC吞吐量测试"""
        bus = SharedMemoryBus(SharedMemoryConfig())
        await bus.initialize("throughput_test")

        NUM_MESSAGES = 10000
        NUM_AGENTS = 100

        # 订阅
        received = asyncio.Semaphore(0)
        received_count = 0

        def handler(msg):
            nonlocal received_count
            received_count += 1
            if received_count >= NUM_MESSAGES:
                received.release()

        bus.subscribe("test_topic", handler)

        # 发送
        start = datetime.now()
        for i in range(NUM_MESSAGES):
            await bus.publish("test_topic", {"index": i})
        elapsed = (datetime.now() - start).total_seconds()

        # 等待接收
        await asyncio.wait_for(received.acquire(), timeout=30.0)

        throughput = NUM_MESSAGES / elapsed
        latency_ms = (elapsed * 1000) / NUM_MESSAGES

        assert throughput > 1000, f"Throughput too low: {throughput}"
        assert latency_ms < 10, f"Latency too high: {latency_ms}ms"

        print(f"Throughput: {throughput:.0f} msg/s, Latency: {latency_ms:.2f}ms")
```

**验收标准**:
- [ ] 100 Agent并发1小时通过
- [ ] 失败率 < 5%
- [ ] IPC吞吐量 > 1000 msg/s
- [ ] 延迟 < 10ms

---

### 2.2 任务T3-2: 知识积累自动化

**目标**: LLM驱动的知识提炼，自动从交互中提取知识。

**实现方案**:

```python
# polaris/kernelone/knowledge/auto_accumulation.py

from dataclasses import dataclass, field
from typing import Any, Callable
import asyncio

@dataclass
class KnowledgeExtraction:
    """知识提取结果"""
    entities: tuple[Entity, ...]
    relationships: tuple[Relationship, ...]
    summaries: tuple[str, ...]
    confidence: float

class KnowledgeAccumulator:
    """知识积累器"""

    def __init__(
        self,
        llm: LLMProvider,
        knowledge_graph: Neo4jKnowledgeGraph,
        extraction_prompt: str,
        min_confidence: float = 0.7,
    ) -> None:
        self._llm = llm
        self._graph = knowledge_graph
        self._extraction_prompt = extraction_prompt
        self._min_confidence = min_confidence
        self._buffer: list[dict[str, Any]] = []
        self._buffer_size = 10

    async def add_interaction(self, interaction: AgentInteraction) -> None:
        """添加交互到缓冲区"""
        self._buffer.append(interaction.to_dict())

        if len(self._buffer) >= self._buffer_size:
            await self._process_buffer()

    async def _process_buffer(self) -> None:
        """处理缓冲区，提取知识"""
        if not self._buffer:
            return

        # 构建提取提示
        prompt = self._extraction_prompt.format(
            interactions=json.dumps(self._buffer, indent=2)
        )

        # 调用LLM提取
        response = await self._llm.complete(prompt)
        extraction = self._parse_extraction(response)

        if extraction.confidence >= self._min_confidence:
            # 添加到知识图谱
            for entity in extraction.entities:
                await self._graph.add_entity(entity)
            for rel in extraction.relationships:
                await self._graph.add_relationship(rel)

        # 清空缓冲区
        self._buffer.clear()

    def _parse_extraction(self, response: str) -> KnowledgeExtraction:
        """解析LLM响应"""
        data = json.loads(response)
        return KnowledgeExtraction(
            entities=tuple(Entity(**e) for e in data.get("entities", [])),
            relationships=tuple(Relationship(**r) for r in data.get("relationships", [])),
            summaries=tuple(data.get("summaries", [])),
            confidence=data.get("confidence", 0.0),
        )

    async def force_process(self) -> None:
        """强制处理缓冲区"""
        await self._process_buffer()
```

---

### 2.3 任务T3-3: 混合检索引擎

**目标**: BM25 + 向量 + 图遍历混合检索，precision@10 > 85%。

**实现方案**:

```python
# polaris/kernelone/knowledge/hybrid_retriever.py

from dataclasses import dataclass
from typing import Any

@dataclass
class HybridRetrievalResult:
    """混合检索结果"""
    entity: Entity
    scores: dict[str, float]  # "bm25", "vector", "graph", "final"
    final_score: float

class HybridRetriever:
    """混合检索引擎"""

    def __init__(
        self,
        bm25_index: BM25Index,
        vector_store: KnowledgeLanceDB,
        knowledge_graph: Neo4jKnowledgeGraph,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._bm25 = bm25_index
        self._vector = vector_store
        self._graph = knowledge_graph
        self._weights = weights or {
            "bm25": 0.2,
            "vector": 0.4,
            "graph": 0.4,
        }

    async def retrieve(
        self,
        query: str,
        embedding: list[float],
        top_k: int = 10,
    ) -> list[HybridRetrievalResult]:
        """混合检索"""
        # 1. BM25检索
        bm25_results = await self._bm25.search(query, top_k * 2)

        # 2. 向量检索
        vector_results = await self._vector.similarity_search(
            embedding, top_k * 2
        )

        # 3. 图检索
        graph_results = await self._graph.query_by_embedding(embedding, top_k * 2)

        # 4. 合并评分
        all_ids = set()
        all_ids.update(r.entity.id for r in bm25_results)
        all_ids.update(r.record.id for r in vector_results)
        all_ids.update(e.id for e, _ in graph_results)

        scored_results: list[HybridRetrievalResult] = []
        for entity_id in all_ids:
            scores = {"bm25": 0.0, "vector": 0.0, "graph": 0.0}

            # BM25分数
            for r in bm25_results:
                if r.entity.id == entity_id:
                    scores["bm25"] = r.score
                    break

            # 向量分数
            for r in vector_results:
                if r.record.id == entity_id:
                    scores["vector"] = r.score
                    break

            # 图分数
            for e, score in graph_results:
                if e.id == entity_id:
                    scores["graph"] = score
                    break

            # 计算最终分数
            final = sum(
                scores[key] * weight
                for key, weight in self._weights.items()
            )

            # 获取实体(需要从存储获取)
            entity = await self._get_entity(entity_id)
            if entity:
                scored_results.append(HybridRetrievalResult(
                    entity=entity,
                    scores=scores,
                    final_score=final,
                ))

        # 排序返回top_k
        return sorted(scored_results, key=lambda x: x.final_score, reverse=True)[:top_k]

    async def _get_entity(self, entity_id: str) -> Entity | None:
        """获取实体"""
        # 实现...
        pass
```

---

### 2.4 任务T3-4: 全局记忆网络

**目标**: 跨workspace知识共享，全局向量索引。

**实现方案**:

```python
# polaris/kernelone/knowledge/global_memory_network.py

from dataclasses import dataclass

class GlobalMemoryNetwork:
    """全局记忆网络"""

    def __init__(
        self,
        local_stores: dict[str, KnowledgeLanceDB],
        global_index: KnowledgeLanceDB,
    ) -> None:
        self._local_stores = local_stores
        self._global_index = global_index
        self._sync_enabled = True

    async def share_to_global(
        self,
        workspace_id: str,
        entity: Entity,
    ) -> None:
        """将本地实体共享到全局"""
        if not self._sync_enabled:
            return

        # 添加全局标签
        shared_entity = Entity(
            id=f"{workspace_id}:{entity.id}",
            type=entity.type,
            properties={**entity.properties, "workspace": workspace_id},
            embeddings=entity.embeddings,
        )

        await self._global_index.upsert(shared_entity)

    async def search_global(
        self,
        query_embedding: list[float],
        exclude_workspace: str | None = None,
    ) -> list[HybridRetrievalResult]:
        """搜索全局知识"""
        results = await self._global_index.similarity_search(query_embedding)

        # 过滤
        if exclude_workspace:
            results = [
                r for r in results
                if not r.record.id.startswith(f"{exclude_workspace}:")
            ]

        return results

    async def sync_workspace(self, workspace_id: str) -> None:
        """同步workspace到全局"""
        local = self._local_stores.get(workspace_id)
        if not local:
            return

        async for entity in local.iter_all():
            await self.share_to_global(workspace_id, entity)
```

---

### 2.5 任务T3-5: AGI基准测试

**目标**: 标准AGI能力评估，覆盖率 > 90%。

**基准测试框架**:

```python
# tests/agi_benchmark/test_framework.py

class AGIBenchmarkSuite:
    """AGI基准测试套件"""

    CATEGORIES = {
        "reasoning": [
            "logic_puzzles",
            "math_problems",
            "commonsense_reasoning",
            "causal_inference",
        ],
        "planning": [
            "task_decomposition",
            "resource_allocation",
            "plan_optimization",
            "contingency_planning",
        ],
        "communication": [
            "question_answering",
            "summarization",
            "explanation",
            "negotiation",
        ],
        "learning": [
            "few_shot_learning",
            "knowledge_integration",
            "concept_grounding",
            "analogy_mapping",
        ],
        "tool_use": [
            "single_tool",
            "multi_tool",
            "tool_composition",
            "tool_creation",
        ],
    }

    def __init__(
        self,
        agent: Agent,
        judge_llm: LLMProvider,
    ) -> None:
        self._agent = agent
        self._judge = judge_llm

    async def run_category(
        self,
        category: str,
        num_samples: int = 20,
    ) -> CategoryResult:
        """运行单个类别测试"""
        test_cases = self.CATEGORIES[category]
        results = []

        for test_case in test_cases[:num_samples]:
            dataset = await self._load_test_case(category, test_case)
            for sample in dataset:
                result = await self._run_sample(sample)
                results.append(result)

        passed = sum(1 for r in results if r.passed)
        return CategoryResult(
            category=category,
            total=len(results),
            passed=passed,
            accuracy=passed / len(results) if results else 0.0,
        )

    async def run_full_suite(self) -> AGIBenchmarkReport:
        """运行完整基准测试"""
        category_results = []
        for category in self.CATEGORIES:
            result = await self.run_category(category)
            category_results.append(result)

        total_tests = sum(r.total for r in category_results)
        total_passed = sum(r.passed for r in category_results)

        return AGIBenchmarkReport(
            category_results=tuple(category_results),
            overall_accuracy=total_passed / total_tests if total_tests else 0.0,
            coverage=len(category_results) / len(self.CATEGORIES),
        )

@dataclass
class CategoryResult:
    category: str
    total: int
    passed: int
    accuracy: float

@dataclass
class AGIBenchmarkReport:
    category_results: tuple[CategoryResult, ...]
    overall_accuracy: float
    coverage: float
```

---

## 三、AGI基准测试评分标准

| 能力维度 | 权重 | 目标 | 测试案例数 |
|----------|------|------|-----------|
| 推理 (Reasoning) | 25% | 85% | 80 |
| 规划 (Planning) | 20% | 80% | 80 |
| 通信 (Communication) | 15% | 90% | 60 |
| 学习 (Learning) | 15% | 75% | 60 |
| 工具使用 (Tool Use) | 25% | 85% | 100 |
| **综合** | 100% | **83%** | **380** |

---

## 四、验收清单

```markdown
## Phase 3 验收检查单

### 100+并发Agent
- [ ] 1小时稳定性测试通过
- [ ] 失败率 < 5%
- [ ] IPC吞吐量 > 1000 msg/s

### 知识积累自动化
- [ ] LLM知识提取
- [ ] 自动图谱更新
- [ ] 置信度过滤

### 混合检索
- [ ] BM25 + 向量 + 图混合
- [ ] Precision@10 > 85%

### 全局记忆网络
- [ ] 跨workspace共享
- [ ] 同步机制

### AGI基准测试
- [ ] 380个测试案例
- [ ] 综合准确率 > 83%
- [ ] 覆盖率 > 90%

### 整体
- [ ] AGI评分达到80/100
```
