# AGI骨架进化 Phase 1: 核心能力补齐

**版本**: v1.0.0
**日期**: 2026-04-06
**状态**: 待执行
**工期**: 4周
**人力**: 4人
**目标评分**: 68/100 (从60/100提升)

---

## 一、任务总览

| 任务 | 优先级 | 工作量 | 前置条件 |
|------|--------|--------|----------|
| LanceDB向量存储启用 | P1 | 24h | Phase 0完成 |
| 工具调用图实现 | P1 | 32h | Phase 0完成 |
| 资源配额系统 | P1 | 40h | - |
| Cell契约100%覆盖 | P1 | 24h | Phase 0完成 |
| 形式化规划验证 | P2 | 32h | 规划引擎审计 |

---

## 二、任务详情

### 2.1 任务T1-1: LanceDB向量存储启用

**问题**: 长期记忆系统(Layer 3)仅有JSONL存储，语义搜索能力缺失。

**目标架构**:
```
┌─────────────────────────────────────────────────────────────┐
│              SemanticMemory 三层存储架构                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Tier 3a: LanceDB (主要向量存储)                            │
│   ├── 384维 embedding (nomic-embed-text)                    │
│   ├── 余弦相似度检索                                          │
│   └── content-hash 幂等性                                    │
│                                                              │
│   Tier 3b: JSONL (备份/归档)                                 │
│   ├── 完整原始数据                                           │
│   └── 故障恢复                                               │
│                                                              │
│   Tier 3c: GraphDB (未来扩展)                                │
│   └── 实体关系建模                                           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**实现方案**:

```python
# polaris/kernelone/akashic/knowledge_pipeline/lancedb_adapter.py

from dataclasses import dataclass, field
from typing import Any, Iterator
import lancedb
import numpy as np
from .schema import LanceDBSchema

@dataclass(frozen=True)
class VectorRecord:
    """向量记录"""
    id: str
    content: str
    embedding: np.ndarray
    content_hash: str
    importance: float
    semantic_tags: tuple[str, ...]
    source_file: str | None = None
    line_start: int | None = None
    line_end: int | None = None

class KnowledgeLanceDB:
    """LanceDB向量存储适配器"""

    SCHEMA: LanceDBSchema = LanceDBSchema()
    EMBEDDING_DIM: int = 384
    BATCH_SIZE: int = 100

    def __init__(
        self,
        db_path: str,
        table_name: str = "knowledge_vectors",
    ) -> None:
        self._db = lancedb.connect(db_path)
        self._table_name = table_name
        self._table = self._get_or_create_table()

    def _get_or_create_table(self) -> lancedb.Table:
        """获取或创建向量表"""
        if self._table_name in self._db.table_names():
            return self._db.open_table(self._table_name)

        return self._db.create_table(
            self._table_name,
            schema=self.SCHEMA.to_lance_schema(),
            exist_ok=True,
        )

    async def upsert(
        self,
        records: Iterator[VectorRecord],
    ) -> UpsertResult:
        """批量upsert向量记录"""
        batch: list[dict[str, Any]] = []
        ids_to_update: list[str] = []

        for record in records:
            batch.append({
                "id": record.id,
                "content": record.content,
                "embedding": record.embedding.tolist(),
                "content_hash": record.content_hash,
                "importance": record.importance,
                "semantic_tags": list(record.semantic_tags),
                "source_file": record.source_file,
                "line_start": record.line_start,
                "line_end": record.line_end,
            })
            ids_to_update.append(record.id)

            if len(batch) >= self.BATCH_SIZE:
                await self._flush_batch(batch)
                batch.clear()

        if batch:
            await self._flush_batch(batch)

        return UpsertResult(upserted=len(ids_to_update))

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """批量写入"""
        self._table.merge-upsert(batch)

    async def similarity_search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        min_score: float = 0.7,
        filter_tags: tuple[str, ...] | None = None,
    ) -> list[ScoredVectorRecord]:
        """语义相似度搜索"""
        query_vector = query_embedding.tolist()

        # 构建查询
        query = self._table.vector_query(query_vector)
        query = query.limit(top_k)

        # 可选: 按标签过滤
        if filter_tags:
            tag_filter = " OR ".join(
                f"semantic_tags LIKE '%{tag}%'" for tag in filter_tags
            )
            query = query.where(tag_filter)

        results = await query.execute()

        scored: list[ScoredVectorRecord] = []
        for row in results:
            score = self._cosine_similarity(query_embedding, row["embedding"])
            if score >= min_score:
                scored.append(ScoredVectorRecord(
                    record=VectorRecord(**{k: row[k] for k in VectorRecord.__dataclass_fields__}),
                    score=score,
                ))

        return sorted(scored, key=lambda x: x.score, reverse=True)

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: list[float]) -> float:
        """计算余弦相似度"""
        a_norm = np.linalg.norm(a)
        b_norm = np.linalg.norm(b)
        if a_norm == 0 or b_norm == 0:
            return 0.0
        return float(np.dot(a, b) / (a_norm * b_norm))

@dataclass(frozen=True)
class LanceDBConfig:
    """LanceDB配置"""
    db_path: str
    table_name: str = "knowledge_vectors"
    embedding_dim: int = 384
    batch_size: int = 100
    index_type: str = "ivf"  # 或 "hnsw"
    num_partitions: int = 256
```

**执行步骤**:
1. 创建`polaris/kernelone/akashic/knowledge_pipeline/lancedb_adapter.py`
2. 实现`VectorRecord`和`KnowledgeLanceDB`类
3. 实现相似度搜索方法
4. 更新`polaris/kernelone/akashic/semantic_memory.py`使用新适配器
5. 添加迁移脚本(从JSONL迁移到LanceDB)
6. 编写集成测试

**验收标准**:
- [ ] LanceDB向量存储可用
- [ ] 相似度搜索 precision@10 > 75%
- [ ] 从JSONL迁移数据完整
- [ ] 集成测试覆盖核心场景

---

### 2.2 任务T1-2: 工具调用图实现

**问题**: 当前工具调用仅支持顺序执行，无法表达依赖关系和条件分支。

**目标架构**:
```
┌─────────────────────────────────────────────────────────────┐
│                    工具调用图架构                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  @dataclass(frozen=True)                                     │
│  class ToolCallGraph:                                        │
│      nodes: tuple[ToolCallNode, ...]    # 节点列表           │
│      edges: tuple[ToolCallEdge, ...]    # 依赖边             │
│      entry_points: tuple[str, ...]      # 入口节点ID         │
│                                                              │
│  @dataclass(frozen=True)                                     │
│  class ToolCallNode:                                         │
│      id: str                         # 唯一标识             │
│      tool_call: ToolCall              # 工具调用            │
│      condition: str | None             # 执行条件            │
│      retry_policy: RetryPolicy        # 重试策略            │
│      timeout_seconds: int             # 超时时间            │
│                                                              │
│  @dataclass(frozen=True)                                     │
│  class ToolCallEdge:                                         │
│      from_id: str                     # 源节点ID           │
│      to_id: str                       # 目标节点ID          │
│      condition: str | None             # 边条件(可选)       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**实现方案**:

```python
# polaris/kernelone/tool_execution/graph.py

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Iterator
import asyncio

@dataclass(frozen=True)
class ToolCallGraph:
    """工具调用图"""
    nodes: tuple[ToolCallNode, ...]
    edges: tuple[ToolCallEdge, ...]
    entry_points: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # 验证入口点存在
        node_ids = {n.id for n in self.nodes}
        for ep in self.entry_points:
            if ep not in node_ids:
                raise ValueError(f"Entry point '{ep}' not in nodes")

        # 验证边引用有效节点
        for edge in self.edges:
            if edge.from_id not in node_ids:
                raise ValueError(f"Edge from_id '{edge.from_id}' not in nodes")
            if edge.to_id not in node_ids:
                raise ValueError(f"Edge to_id '{edge.to_id}' not in nodes")

        # 验证无循环引用(除非显式允许)
        if not self._has_valid_topology():
            raise ValueError("Graph contains invalid cycles")

    def _has_valid_topology(self) -> bool:
        """验证图拓扑(无循环或允许循环)"""
        # 简化: 允许有向无环图(DAG)
        visited: set[str] = set()
        path: set[str] = set()

        def dfs(node_id: str) -> bool:
            if node_id in path:
                return False  # 循环
            if node_id in visited:
                return True
            path.add(node_id)
            visited.add(node_id)
            for edge in self.edges:
                if edge.from_id == node_id:
                    if not dfs(edge.to_id):
                        return False
            path.remove(node_id)
            return True

        for entry in self.entry_points:
            if not dfs(entry):
                return False
        return True

    def get_execution_order(self) -> list[list[str]]:
        """获取分层执行顺序(可并行层)"""
        # Kahn算法获取拓扑序
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adj_list: dict[str, list[str]] = {n.id: [] for n in self.nodes}

        for edge in self.edges:
            in_degree[edge.to_id] += 1
            adj_list[edge.from_id].append(edge.to_id)

        layers: list[list[str]] = []
        remaining = set(in_degree.keys())

        while remaining:
            # 收集入度为0的节点(可并行执行)
            layer = [nid for nid in remaining if in_degree[nid] == 0]
            if not layer:
                raise GraphCycleError("Cannot resolve execution order: cycle detected")

            layers.append(layer)

            # 移除当前层，更新入度
            for nid in layer:
                remaining.remove(nid)
                for next_nid in adj_list[nid]:
                    in_degree[next_nid] -= 1

        return layers

@dataclass(frozen=True)
class ToolCallNode:
    """工具调用节点"""
    id: str
    tool_call: ToolCall
    condition: str | None = None
    retry_policy: RetryPolicy | None = None
    timeout_seconds: int = 30

    def should_execute(self, context: ExecutionContext) -> bool:
        """判断节点是否应执行"""
        if self.condition is None:
            return True

        # 简单条件评估(可扩展为表达式引擎)
        # 格式: "result.count > 0" 或 "vars.get('flag') == True"
        try:
            return self._evaluate_condition(self.condition, context)
        except Exception:
            return False

    def _evaluate_condition(self, condition: str, ctx: ExecutionContext) -> bool:
        """评估条件表达式"""
        # 安全评估器 - 只支持受限的操作
        allowed_names = {
            "result": ctx.last_result,
            "vars": ctx.variables,
            "context": ctx,
        }
        return bool(eval(condition, {"__builtins__": {}}, allowed_names))

@dataclass(frozen=True)
class ToolCallEdge:
    """工具调用边"""
    from_id: str
    to_id: str
    condition: str | None = None

@dataclass
class ExecutionContext:
    """执行上下文"""
    last_result: Any = None
    variables: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

class GraphExecutor:
    """工具调用图执行器"""

    def __init__(
        self,
        executor: ToolExecutorPort,
        evaluator: ConditionEvaluator | None = None,
    ) -> None:
        self._executor = executor
        self._evaluator = evaluator or DefaultConditionEvaluator()

    async def execute(
        self,
        graph: ToolCallGraph,
        initial_context: ExecutionContext | None = None,
    ) -> GraphExecutionResult:
        """执行工具调用图"""
        context = initial_context or ExecutionContext()
        results: dict[str, ToolResult] = {}
        layers = graph.get_execution_order()

        for layer_ids in layers:
            # 过滤条件不满足的节点
            executable_nodes = [
                node for node in graph.nodes
                if node.id in layer_ids and node.should_execute(context)
            ]

            # 并行执行同层节点
            tasks = [
                self._execute_node(node, context)
                for node in executable_nodes
            ]
            layer_results = await asyncio.gather(*tasks, return_exceptions=True)

            # 更新上下文
            for node, result in zip(executable_nodes, layer_results):
                results[node.id] = result
                if isinstance(result, ToolSuccess):
                    context.last_result = result.payload
                    if node.id in graph.edges:
                        # 更新边相关变量
                        pass

        return GraphExecutionResult(
            success=all(isinstance(r, ToolSuccess) for r in results.values()),
            node_results=results,
            context=context,
        )

    async def _execute_node(
        self,
        node: ToolCallNode,
        context: ExecutionContext,
    ) -> ToolResult:
        """执行单个节点"""
        retry_policy = node.retry_policy or RetryPolicy()

        for attempt in range(retry_policy.max_attempts):
            try:
                result = await asyncio.wait_for(
                    self._executor.execute(node.tool_call),
                    timeout=node.timeout_seconds,
                )

                if isinstance(result, ToolSuccess):
                    return result

                # 可重试错误
                if attempt < retry_policy.max_attempts - 1:
                    await asyncio.sleep(retry_policy.backoff_seconds(attempt))

            except asyncio.TimeoutError:
                if attempt == retry_policy.max_attempts - 1:
                    return ToolFailure(f"Timeout after {node.timeout_seconds}s")
            except Exception as e:
                if attempt == retry_policy.max_attempts - 1:
                    return ToolFailure(str(e))

        return ToolFailure("Max retries exceeded")
```

**执行步骤**:
1. 创建`polaris/kernelone/tool_execution/graph.py`
2. 实现`ToolCallGraph`、`ToolCallNode`、`ToolCallEdge`类
3. 实现`GraphExecutor`支持并行执行
4. 实现条件评估器
5. 更新`AgentAccelToolExecutor`集成图执行
6. 编写集成测试

**验收标准**:
- [ ] 工具调用图支持DAG表达
- [ ] 支持并行执行同层节点
- [ ] 支持条件执行
- [ ] 支持重试策略
- [ ] 集成测试覆盖

---

### 2.3 任务T1-3: 资源配额系统

**问题**: 无Agent/Cell级别的资源配额，无公平调度机制。

**目标架构**:
```
┌─────────────────────────────────────────────────────────────┐
│                    资源配额系统架构                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  @dataclass(frozen=True)                                     │
│  class ResourceQuota:                                        │
│      cpu_quota_ns: int        # CPU时间配额(纳秒)           │
│      memory_bytes: int        # 内存配额(字节)              │
│      max_concurrent_tools: int # 最大并发工具数             │
│      max_turns: int           # 最大对话轮次                 │
│      max_wall_time_seconds: int # 最大墙上时间               │
│                                                              │
│  @dataclass(frozen=True)                                     │
│  class AgentResources:                                       │
│      agent_id: str                                           │
│      quota: ResourceQuota                                    │
│      usage: ResourceUsage           # 当前使用量             │
│      acquired_at: datetime                                  │
│                                                              │
│  ResourceQuotaManager                                         │
│  ├── allocate(agent_id, quota)                              │
│  ├── release(agent_id)                                      │
│  ├── check_quota(agent_id) → QuotaStatus                    │
│  └── enforce_quota(agent_id) → bool                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**实现方案**:

```python
# polaris/kernelone/resource/quota.py

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import ClassVar
import threading
import resource

@dataclass(frozen=True)
class ResourceQuota:
    """资源配额"""
    cpu_quota_ns: int = 60_000_000_000  # 60秒
    memory_bytes: int = 2 * 1024 * 1024 * 1024  # 2GB
    max_concurrent_tools: int = 10
    max_turns: int = 50
    max_wall_time_seconds: int = 300  # 5分钟

    # 系统级配额
    SYSTEM_CPU_QUOTA_NS: ClassVar[int] = 600_000_000_000  # 10分钟总CPU
    SYSTEM_MEMORY_BYTES: ClassVar[int] = 16 * 1024 * 1024 * 1024  # 16GB总内存

@dataclass
class ResourceUsage:
    """资源使用量"""
    cpu_used_ns: int = 0
    memory_used_bytes: int = 0
    concurrent_tools: int = 0
    turns: int = 0
    wall_time_seconds: int = 0

    def is_within_quota(self, quota: ResourceQuota) -> bool:
        """检查是否在配额内"""
        return (
            self.cpu_used_ns <= quota.cpu_quota_ns
            and self.memory_used_bytes <= quota.memory_bytes
            and self.concurrent_tools <= quota.max_concurrent_tools
            and self.turns <= quota.max_turns
            and self.wall_time_seconds <= quota.max_wall_time_seconds
        )

    def check_quota(self, quota: ResourceQuota) -> QuotaStatus:
        """检查配额状态"""
        violations: list[str] = []

        if self.cpu_used_ns > quota.cpu_quota_ns:
            violations.append(f"CPU {self.cpu_used_ns} > {quota.cpu_quota_ns}")
        if self.memory_used_bytes > quota.memory_bytes:
            violations.append(f"Memory {self.memory_used_bytes} > {quota.memory_bytes}")
        if self.concurrent_tools > quota.max_concurrent_tools:
            violations.append(f"Concurrent {self.concurrent_tools} > {quota.max_concurrent_tools}")
        if self.turns > quota.max_turns:
            violations.append(f"Turns {self.turns} > {quota.max_turns}")
        if self.wall_time_seconds > quota.max_wall_time_seconds:
            violations.append(f"Wall time {self.wall_time_seconds} > {quota.max_wall_time_seconds}")

        if not violations:
            return QuotaStatus.ALLOWED
        return QuotaStatus.DENIED_REASON if len(violations) == 1 else QuotaStatus.DENIED_MULTIPLE

@dataclass
class AgentResources:
    """Agent资源"""
    agent_id: str
    quota: ResourceQuota
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    acquired_at: datetime = field(default_factory=datetime.now)

    def remaining_quota(self) -> ResourceQuota:
        """计算剩余配额"""
        return ResourceQuota(
            cpu_quota_ns=self.quota.cpu_quota_ns - self.usage.cpu_used_ns,
            memory_bytes=self.quota.memory_bytes - self.usage.memory_used_bytes,
            max_concurrent_tools=self.quota.max_concurrent_tools - self.usage.concurrent_tools,
            max_turns=self.quota.max_turns - self.usage.turns,
            max_wall_time_seconds=self.quota.max_wall_time_seconds - self.usage.wall_time_seconds,
        )

class QuotaStatus(Enum):
    ALLOWED = "allowed"
    DENIED_REASON = "denied_single"
    DENIED_MULTIPLE = "denied_multiple"
    SYSTEM_OVERLOADED = "system_overloaded"

class ResourceQuotaManager:
    """资源配额管理器"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agent_resources: dict[str, AgentResources] = {}
        self._system_usage = ResourceUsage()
        self._default_quota = ResourceQuota()

    def allocate(self, agent_id: str, quota: ResourceQuota | None = None) -> AgentResources:
        """分配资源配额"""
        with self._lock:
            if agent_id in self._agent_resources:
                raise ResourceAlreadyAllocatedError(agent_id)

            resources = AgentResources(
                agent_id=agent_id,
                quota=quota or self._default_quota,
            )
            self._agent_resources[agent_id] = resources
            return resources

    def release(self, agent_id: str) -> None:
        """释放资源配额"""
        with self._lock:
            if agent_id in self._agent_resources:
                del self._agent_resources[agent_id]

    def get_resources(self, agent_id: str) -> AgentResources | None:
        """获取Agent资源"""
        return self._agent_resources.get(agent_id)

    def check_quota(self, agent_id: str) -> QuotaStatus:
        """检查配额状态"""
        with self._lock:
            # 系统级检查
            if self._system_usage.cpu_used_ns >= ResourceQuota.SYSTEM_CPU_QUOTA_NS:
                return QuotaStatus.SYSTEM_OVERLOADED

            agent = self._agent_resources.get(agent_id)
            if not agent:
                return QuotaStatus.ALLOWED

            return agent.usage.check_quota(agent.quota)

    def acquire(self, agent_id: str, resource_type: ResourceType) -> bool:
        """获取资源(原子操作)"""
        with self._lock:
            status = self.check_quota(agent_id)
            if status != QuotaStatus.ALLOWED:
                return False

            agent = self._agent_resources.get(agent_id)
            if not agent:
                return False

            # 更新使用量
            if resource_type == ResourceType.CPU:
                # CPU通过时间模拟更新
                pass
            elif resource_type == ResourceType.MEMORY:
                agent.usage.memory_used_bytes += 1  # 示例
            elif resource_type == ResourceType.TOOL_SLOT:
                agent.usage.concurrent_tools += 1

            self._system_usage.concurrent_tools += 1
            return True

    def release_resource(self, agent_id: str, resource_type: ResourceType) -> None:
        """释放资源"""
        with self._lock:
            agent = self._agent_resources.get(agent_id)
            if not agent:
                return

            if resource_type == ResourceType.TOOL_SLOT:
                agent.usage.concurrent_tools = max(0, agent.usage.concurrent_tools - 1)
            elif resource_type == ResourceType.MEMORY:
                agent.usage.memory_used_bytes = max(0, agent.usage.memory_used_bytes - 1)

            self._system_usage.concurrent_tools = max(0, self._system_usage.concurrent_tools - 1)

    def update_cpu_usage(self, agent_id: str, cpu_ns: int) -> None:
        """更新CPU使用量"""
        with self._lock:
            agent = self._agent_resources.get(agent_id)
            if agent:
                agent.usage.cpu_used_ns = cpu_ns

class ResourceType(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    TOOL_SLOT = "tool_slot"
    TURN = "turn"

class ResourceAlreadyAllocatedError(Exception):
    """资源已分配错误"""
    pass
```

**执行步骤**:
1. 创建`polaris/kernelone/resource/quota.py`
2. 实现`ResourceQuota`、`AgentResources`、`ResourceQuotaManager`类
3. 在`TurnEngine`集成配额检查
4. 在`ToolExecutor`集成并发控制
5. 添加系统级配额保护
6. 编写集成测试

**验收标准**:
- [ ] 每个Agent可设置独立配额
- [ ] CPU/内存/并发工具数配额生效
- [ ] 系统级配额超载保护
- [ ] 配额超限正确拒绝执行
- [ ] 集成测试覆盖

---

### 2.4 任务T1-4: Cell契约100%覆盖

**目标**: Phase 0未完成的Cell契约，加上新发现的缺口。

**待完成Cell契约**:

| Cell | 优先级 | 状态 |
|------|--------|------|
| roles.host | P1 | Phase 0未完成 |
| director.* (4个) | P1 | Phase 0未完成 |
| orchestration.workflow_engine | P1 | Phase 0未完成 |
| orchestration.workflow_activity | P1 | Phase 0未完成 |
| context.engine | P2 | 已有部分，需完善 |

**执行步骤**:
1. 审查Phase 0完成状态
2. 补充剩余Cell契约
3. 验证所有Cell的cell.yaml与contracts.py一致
4. 添加单元测试

**验收标准**:
- [ ] 100% Cell有完整契约
- [ ] cell.yaml public_contracts与contracts.py一致
- [ ] 所有契约有__post_init__验证

---

### 2.5 任务T1-5: 形式化规划验证

**问题**: PlanSolveEngine计划阶段无形式化验证，仅依赖LLM自身。

**实现方案**:

```python
# polaris/kernelone/planning/validator.py

from dataclasses import dataclass
from typing import Protocol

class PlanValidator(Protocol):
    """计划验证器协议"""

    def validate(self, plan: Plan) -> ValidationResult:
        """验证计划"""
        ...

@dataclass(frozen=True)
class ValidationResult:
    """验证结果"""
    is_valid: bool
    violations: tuple[Violation, ...]
    suggestions: tuple[str, ...]

@dataclass(frozen=True)
class Violation:
    """违反项"""
    severity: ViolationSeverity
    rule_id: str
    message: str
    location: str | None = None

class ViolationSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

class StructuralPlanValidator:
    """结构化计划验证器"""

    def validate(self, plan: Plan) -> ValidationResult:
        """验证计划结构"""
        violations: list[Violation] = []

        # 检查计划完整性
        if not plan.steps:
            violations.append(Violation(
                severity=ViolationSeverity.ERROR,
                rule_id="EMPTY_PLAN",
                message="Plan has no steps",
            ))

        # 检查步骤依赖
        step_ids = {s.id for s in plan.steps}
        for step in plan.steps:
            if step.depends_on:
                for dep_id in step.depends_on:
                    if dep_id not in step_ids:
                        violations.append(Violation(
                            severity=ViolationSeverity.ERROR,
                            rule_id="INVALID_DEPENDENCY",
                            message=f"Step {step.id} depends on non-existent step {dep_id}",
                            location=step.id,
                        ))

        # 检查循环依赖
        if self._has_cycle(plan):
            violations.append(Violation(
                severity=ViolationSeverity.ERROR,
                rule_id="CYCLE_DETECTED",
                message="Plan contains circular dependencies",
            ))

        # 检查资源约束
        for step in plan.steps:
            if step.estimated_duration > plan.max_duration:
                violations.append(Violation(
                    severity=ViolationSeverity.WARNING,
                    rule_id="EXCEEDS_DURATION",
                    message=f"Step {step.id} estimated duration exceeds plan max",
                    location=step.id,
                ))

        return ValidationResult(
            is_valid=not any(v.severity == ViolationSeverity.ERROR for v in violations),
            violations=tuple(violations),
            suggestions=self._generate_suggestions(plan, violations),
        )

    def _has_cycle(self, plan: Plan) -> bool:
        """检测循环依赖"""
        visited: set[str] = set()
        path: set[str] = set()

        def dfs(step_id: str) -> bool:
            if step_id in path:
                return True
            if step_id in visited:
                return False
            path.add(step_id)
            visited.add(step_id)

            step = next((s for s in plan.steps if s.id == step_id), None)
            if step:
                for dep_id in (step.depends_on or []):
                    if dfs(dep_id):
                        return True
            path.remove(step_id)
            return False

        for step in plan.steps:
            if step.id not in visited:
                if dfs(step.id):
                    return True
        return False

    def _generate_suggestions(self, plan: Plan, violations: tuple[Violation, ...]) -> tuple[str, ...]:
        """生成建议"""
        suggestions: list[str] = []

        if any(v.rule_id == "EMPTY_PLAN" for v in violations):
            suggestions.append("Add at least one step to the plan")

        if any(v.rule_id == "CYCLE_DETECTED" for v in violations):
            suggestions.append("Reorder dependencies to break cycles")

        return tuple(suggestions)
```

**执行步骤**:
1. 创建`polaris/kernelone/planning/validator.py`
2. 实现`PlanValidator`协议和`StructuralPlanValidator`
3. 集成到`PlanSolveEngine`
4. 添加验证测试

**验收标准**:
- [ ] 计划结构验证可用
- [ ] 循环依赖检测
- [ ] 依赖完整性验证
- [ ] 集成测试覆盖

---

## 三、执行计划

### Week 3-4: 存储与记忆增强

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | T1-1: LanceDB架构设计 | Storage-1 |
| Tue | T1-1: LanceDB实现 | Storage-1 |
| Wed | T1-1: 集成测试 | Storage-1 |
| Thu | T1-4: roles.host契约 | Integration-1 |
| Fri | T1-4: context.engine契约 | Integration-2 |

### Week 5-6: 工具与规划增强

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | T1-2: 工具调用图设计 | Tool-1 |
| Tue | T1-2: GraphExecutor | Tool-1 |
| Wed | T1-2: 条件评估器 | Tool-1 |
| Thu | T1-5: PlanValidator | Tool-2 |
| Fri | T1-2+T1-5: 集成测试 | Both |

### Week 7-8: 资源与安全增强

| Day | 任务 | 负责人 |
|-----|------|--------|
| Mon | T1-3: 配额系统设计 | Kernel-1 |
| Tue | T1-3: ResourceQuotaManager | Kernel-1 |
| Wed | T1-3: TurnEngine集成 | Kernel-2 |
| Thu | T1-3: 集成测试 | Kernel-2 |
| Fri | Phase 1验收 | All |

---

## 四、验收清单

```markdown
## Phase 1 验收检查单

### LanceDB向量存储
- [ ] KnowledgeLanceDB类实现完整
- [ ] similarity_search精度 > 75%
- [ ] JSONL迁移脚本可用
- [ ] 集成测试覆盖

### 工具调用图
- [ ] ToolCallGraph支持DAG
- [ ] GraphExecutor并行执行
- [ ] 条件评估器可用
- [ ] 集成测试覆盖

### 资源配额
- [ ] ResourceQuotaManager实现
- [ ] Agent级配额生效
- [ ] 系统级配额保护
- [ ] 集成测试覆盖

### Cell契约
- [ ] 100% Cell契约覆盖
- [ ] cell.yaml一致
- [ ] __post_init__验证

### 规划验证
- [ ] StructuralPlanValidator实现
- [ ] 循环依赖检测
- [ ] 集成测试覆盖

### 整体
- [ ] AGI评分达到68/100
- [ ] pytest 100%通过
- [ ] mypy --strict零警告
```

---

## 五、关键文件索引

```
polaris/kernelone/
├── akashic/
│   └── knowledge_pipeline/
│       └── lancedb_adapter.py          # 待创建
├── tool_execution/
│   └── graph.py                         # 待创建
├── resource/
│   └── quota.py                         # 待创建
└── planning/
    └── validator.py                     # 待创建
```
