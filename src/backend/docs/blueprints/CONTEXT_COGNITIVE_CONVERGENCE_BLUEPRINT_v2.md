# Context + 认知生命体 收敛蓝图 v2.1

> 版本: v2.1 (整合评审增强)
> 日期: 2026-04-13
> 状态: 草稿 → 待评审
> 评审来源: 顶级架构师评审反馈

---

## 1. 执行摘要

### 1.1 背景与目标

当前系统存在四大问题域：
1. **Context 入口碎片化** - 多处 context 组装旁路，主链不清晰
2. **认知生命体 shadowwired** - 可用但不可控，缺乏 fallback
3. **语义检索越界** - vector 直接越过 Cell 边界，Graph 约束未生效
4. **治理门禁失真** - 脚本路径漂移、子进程环境缺失

本蓝图旨在 8 周内完成收敛，建立：
- Context 单一主链（RoleContextGateway → StateFirstContextOS.project()）
- 认知生命体可控升格（authority with deterministic fallback）
- Graph-Constrained Semantic Search
- 可审计、可回滚的治理体系

### 1.2 关键指标 (v2.1 增强版)

| 指标 | 当前基线 | 目标 | 测量方法 |
|------|---------|------|---------|
| 门禁通过率 | <80% | 100% (连续5次) | CI |
| Context 投影一致性 | 未量化 | >=99.5% | Immutable Snapshot 回归集 |
| Fallback 成功率 | 未量化 | >=99% | 监控 |
| 越权工具调用 | 未量化 | 0 | 审计日志 |
| Query 路径副作用 | 未量化 | 0 | 审计日志 |
| **硬边界越界率** | 未量化 | **0%** (跨租户/跨最高权限) | Graph 验证 |
| **软边界越界率** | 未量化 | **≤0.8%** (95% CI, 语义相似) | 统计采样 |
| **Recall@10** | 未量化 | **>=92%** (防止 threshold 过紧) | 检索回归集 |
| p95 context 延迟 | 基准值 | **≤40% 基线** (LanceDB pushdown) | 性能回归 |
| **Agent E2E 成功率** | 未量化 | **>=98%** | 生产采样 |
| **认知进化漂移率** | 未量化 | **<=0.5%/周** | EvolutionEngine 版本对比 |

---

## 2. 系统架构图

### 2.1 Context 主链架构 (v2.1: 分层压缩 + Immutable Snapshot)

```
┌─────────────────────────────────────────────────────────────────────┐
│                           LLM / Agent                               │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     RoleContextGateway                              │
│  (polaris/cells/roles/kernel/internal/context_gateway.py)          │
│                                                                      │
│  职责:                                                               │
│  - 统一入口，角色无关的 context 投影                                 │
│  - TokenBudget 强制执行                                             │
│  - StateOwner 唯一性保证                                            │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    StateFirstContextOS                              │
│  (polaris/kernelone/context/context_os/runtime.py)                 │
│                                                                      │
│  职责:                                                               │
│  - EpisodeCard 管理                                                 │
│  - Priority/Time slicing                                           │
│  - Deterministic projection                                         │
│  - [NEW] Immutable Snapshot 生成 (JSON + hash)                      │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 ContextAssembler (v2.1: 分层压缩)                    │
│  (polaris/cells/roles/kernel/internal/services/context_assembler.py)│
│                                                                      │
│  职责:                                                               │
│  - 组装最终 context block                                            │
│  - 旁路清理（禁止直接拼接）                                          │
│  - [NEW] Hierarchical Eviction Layer:                               │
│                                                                      │
│    layers = {                                                        │
│      "system": self._keep_all(system_blocks),         # 绝对保留     │
│      "episodic": self._fifo_compress(episodic, 0.6), # 60% FIFO    │
│      "working": self._semantic_prune(working, threshold=0.75),      │
│      "retrieved": self._graph_constrained_rank(retrieved)           │
│    }                                                                 │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ContextCatalog                                    │
│  (polaris/cells/context/catalog/)                                   │
│                                                                      │
│  职责:                                                               │
│  - Graph-filtered 候选                                              │
│  - Semantic rank                                                    │
│  - State owner 唯一                                                 │
│  - [NEW] Immutable Snapshot 存储 (context_snapshots/)              │
└─────────────────────────────────┴───────────────────────────────────┘
```

### 2.2 认知生命体架构 (v2.1: HITL + 超时隔离)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CognitiveOrchestrator                            │
│  (polaris/kernelone/cognitive/orchestrator.py)                     │
│                                                                      │
│  输入校验 → 超时 → 错误分类 → Fallback 触发                          │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│  Perception   │         │   Reasoning   │         │   Execution   │
│ (开关: PERCEIVE)│        │ (开关: REASON) │         │ (开关: EXEC)  │
└───────┬───────┘         └───────┬───────┘         └───────┬───────┘
        │                         │                         │
        └─────────────────────────┼─────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│               [NEW] HumanInterventionQueue                          │
│                                                                      │
│  - AlignmentVerifier 之后、Execution 之前强制插入                  │
│  - 支持 Webhook / Slack / 企业微信                                   │
│  - Execution 超时 15s 自动转入 Shadow 模式 + 告警                  │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   AlignmentVerifier                                │
│                                                                      │
│  职责:                                                               │
│  - alignment 结果进入执行前 gate                                     │
│  - 禁止"已判不安全仍执行"                                           │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  EvolutionEngine                                     │
│  (开关: EVOLUTION_ENABLED)                                           │
│                                                                      │
│  职责:                                                               │
│  - 学习与适应                                                        │
│  - 版本化证据                                                        │
│  - [NEW] 漂移率监控 (<=0.5%/周)                                     │
└─────────────────────────────────┴───────────────────────────────────┘
```

### 2.3 语义检索架构 (v2.1: LanceDB Predicate Pushdown)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SemanticSearchPipeline                           │
│                                                                      │
│  检索顺序固定:                                                       │
│  1. Graph 候选过滤（边界约束）                                       │
│  2. [NEW] LanceDB Predicate Pushdown (底层完成过滤)                 │
│  3. Descriptor Embedding Rank                                       │
│  4. Re-rank                                                         │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│              LanceDB Adapter (v2.1: Predicate Pushdown)             │
│  (polaris/kernelone/akashic/knowledge_pipeline/lancedb_adapter.py)  │
│                                                                      │
│  SQL 等价实现:                                                       │
│  SELECT * FROM descriptor_table                                      │
│  WHERE graph_entity_id IN (                                          │
│      SELECT id FROM graph_candidates WHERE owner = ?                │
│  )                                                                   │
│    AND tenant_id = ?                                                  │
│    AND version_hash = ?                                              │
│  ORDER BY vector_distance(?) LIMIT 50                                │
│                                                                      │
│  效果: p95 延迟 <= 40% 基线                                          │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DescriptorGenerator                              │
│  (polaris/cells/context/catalog/internal/descriptor_pack_generator)│
│                                                                      │
│  输出:                                                               │
│  - schema 校验                                                       │
│  - 版本号 + 来源哈希                                                 │
│  - Index receipt                                                    │
│  - [NEW] Recall@10 >= 92% 验证                                      │
└─────────────────────────────────┴───────────────────────────────────┘
```

---

## 3. 分阶段详细计划

### M0: 基线冻结 (第0周, 2天)

**目标**: 建立可信基线

| 任务 | 负责人 | 产物 |
|------|--------|------|
| M0.1 Context 主链现状测绘 | Chief Engineer | `docs/blueprints/m0_context_chain_map.md` |
| M0.2 认知开关现状采集 | Chief Engineer | `docs/blueprints/m0_cognitive_flags.csv` |
| M0.3 治理脚本现状测试 | QA | 基线报告 |
| M0.4 依赖图绘制 | Architect | 现状依赖图 |

**退出条件**: 基线指标可重复采集

---

### M0.5: Shadow Traffic + 1-Click Rollback (第0周末尾, 半天) [NEW]

**目标**: M0 结束前必须完成灰度基础设施

| 任务 | 负责人 | 产物 |
|------|--------|------|
| M0.5.1 流量镜像配置 | Director | Istio/Traefik 镜像策略 (1% 真实请求) |
| M0.5.2 回滚脚本 | Director | `rollback_contextos_v2.sh` (30s 内切换入口) |
| M0.5.3 回滚演练 | QA | 演练记录 + 证据 |

**回滚脚本规格**:
```bash
#!/bin/bash
# rollback_contextos_v2.sh
# 用法: ./rollback_contextos_v2.sh [context|semantic|cognitive|all]
# 执行时间: <= 30s

TARGET=${1:-"all"}
GATEWAY_PATH="polaris/cells/roles/kernel/internal/context_gateway.py"
BACKUP_DIR="/workspace/meta/backups/context_gateway"

case $TARGET in
  context)
    cp $BACKUP_DIR/context_gateway.py.current $GATEWAY_PATH
    ;;
  all)
    for component in context semantic cognitive; do
      cp $BACKUP_DIR/${component}_gateway.py.current polaris/.../${component}_gateway.py
    done
    ;;
esac

# 清理缓存
rm -rf /workspace/.polaris/runtime/context_cache/*
exit 0
```

**退出条件**: 回滚演练成功，证据链完整

---

### M1: 门禁纠偏 (第1周)

**目标**: 修复"门禁失真"

#### PR-01: 治理脚本路径与 AST 检测修复

**涉及文件**:
- `docs/governance/ci/scripts/run_contextos_governance_gate.py`
- `docs/governance/ci/scripts/run_cognitive_life_form_gate.py`

**修复内容** (v2.1 增强):
1. ~~sys.path.insert~~ → **使用 `uv sync --frozen` + `pip install -e .`**
2. **路径契约快照测试** (新增):
```python
# run_contextos_governance_gate.py
def test_path_contract():
    """路径契约快照测试 - 防止路径再次漂移"""
    assert Path(__file__).resolve().parent.parent == WORKSPACE / "polaris"
    # 或使用 pyproject.toml workspace 解析
```
3. CI/CD 强制执行 `uv sync --frozen` (2026 年 uv 为 Python 事实标准，比 pip 快 10x)
4. 消除硬编码路径漂移（`src/backend` → `polaris`）
5. AST 检测逻辑更新到当前类名/协议

#### PR-02: 治理脚本子进程环境继承修复

**涉及文件**:
- `docs/governance/ci/scripts/*.py` (all gate scripts)

**修复内容**:
1. ~~sys.path.insert(0, str(workspace))~~ → **使用 uv / pip install -e .**
2. 所有 gate 脚本增加路径探针自测
3. 确保 pytest 能找到模块
4. 消除 "No module named pytest" 假失败

**退出条件**: ContextOS gate 与 cognitive gate 稳定通过

---

### M2: Context 主链收敛 (第2-3周)

#### PR-03: test_context_gateway_integration async 改造

**涉及文件**:
- `polaris/cells/roles/kernel/internal/test_context_gateway_integration.py` (如存在)

**修复内容**:
- Async 测试框架对齐
- Gateway integration 语义对齐

#### PR-04: RoleContextGateway 契约收敛与类型收敛

**涉及文件**:
- `polaris/cells/roles/kernel/internal/context_gateway.py`

**修复内容**:
1. 移除遗留字段，类型注解 100%
2. 契约文档化
3. StateOwner 唯一性强制

#### PR-05: roles.runtime 入口移除旁路 context 组装

**涉及文件**:
- `polaris/cells/roles/kernel/internal/runtime.py` 或相关入口

**修复内容**:
1. 扫描所有 `roles/runtime` 入口
2. 移除直接拼接 context block 的代码
3. 强制通过 RoleContextGateway

#### PR-06: director.execution 入口移除旁路 context 组装

**涉及文件**:
- `polaris/cells/director/execution/` 下相关入口

**修复内容**:
1. 同 PR-05
2. 确保 director 路径走 gateway

#### PR-0X: Context Projection Immutable Snapshot [NEW]

**涉及文件**:
- `polaris/kernelone/context/context_os/runtime.py`
- `workspace/meta/context_snapshots/` (新目录)

**修复内容**:
```python
# StateFirstContextOS.project() 后强制生成不可变快照
class StateFirstContextOS:
    def project(self, state: StateOwner, ...) -> ContextProjection:
        projection = self._project_impl(state, ...)

        # [NEW] 生成 Immutable Snapshot
        snapshot = {
            "version": "2.1",
            "timestamp": datetime.utcnow().isoformat(),
            "input_hash": self._fingerprint(state),
            "output_hash": self._fingerprint(projection),
            "projection": projection,
        }

        snapshot_path = Path(f"workspace/meta/context_snapshots/{snapshot['timestamp']}.json")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

        return projection
```

**验证价值**: Context 投影一致性 >=99.5% 的测量 100% 可重复

**退出条件**: 全部入口走 gateway + contextOS

---

### M3: 认知主链升格 (第3-4周)

#### PR-07: 认知开关 profile 化与默认值治理 + Dynamic Feature Flag [ENHANCED]

**涉及文件**:
- `polaris/kernelone/cognitive/config.py`

**修复内容** (v2.1 增强):
```python
# [NEW] Dynamic Feature Flag 集成
from polaris.infrastructure.flag_service import FlagService  # 自建轻量 Flag Service

flag_client = FlagService()

def get_cognitive_profile(tenant_id: str | None = None, user_id: str | None = None):
    """
    获取认知配置，支持按租户/用户灰度。
    回滚成本接近 0。
    """
    base = COGNITIVE_PROFILES[ENV]

    if not tenant_id:
        return base

    # 动态覆写 (按需可切换到 LaunchDarkly/Unleash)
    overrides = flag_client.get_flags(
        "cognitive",
        {"tenant_id": tenant_id, "user_id": user_id}
    )
    return {**base, **overrides}


COGNITIVE_PROFILES = {
    "dev": {
        "enabled": True,
        "perception": True,
        "reasoning": True,
        "execution": False,  # shadow mode
        "evolution": False,
    },
    "staging": {
        "enabled": True,
        "perception": True,
        "reasoning": True,
        "execution": True,
        "evolution": "limited",  # 按租户可灰度
    },
    "prod": {
        "enabled": True,
        "perception": True,
        "reasoning": True,
        "execution": True,
        "evolution": True,  # 按租户可灰度
    },
}
```

#### PR-08: cognitive authority + fallback 实装 + HITL [ENHANCED]

**涉及文件**:
- `polaris/cells/factory/cognitive_runtime/public/service.py`
- `polaris/kernelone/cognitive/orchestrator.py`

**修复内容** (v2.1 增强):
```python
# [NEW] HumanInterventionQueue (HITL)
class HumanInterventionQueue:
    """
    Execution 前的强制人工审批点。
    支持 Webhook / Slack / 企业微信。
    """

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds

    async def request_approval(self, execution_plan: ExecutionPlan) -> Decision:
        task = asyncio.create_task(self._notify_and_wait(execution_plan))
        try:
            decision = await asyncio.wait_for(task, timeout=self.timeout_seconds)
            return decision
        except asyncio.TimeoutError:
            # [NEW] 超时自动隔离: 转入 Shadow 模式 + 告警
            await self._isolate_to_shadow_mode(execution_plan)
            return Decision.ShadowMode


# [NEW] Execution 超时自动隔离
class ExecutionStage:
    async def execute(self, plan: ExecutionPlan) -> Result:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._execute_impl(plan),
                timeout=15  # 15s 超时
            )
            return result
        except asyncio.TimeoutError:
            await self._isolate_to_shadow_mode(plan)
            raise CognitiveExecutionTimeout(plan.id)
```

**Fallback 监控指标**:
- Fallback 成功率 >= 99%
- HITL 超时率 <= 0.1%
- 认知进化漂移率 <= 0.5%/周

**退出条件**: 主链启用且 fallback/HITL 稳定

---

### M4: 语义检索收敛 (第4-6周)

#### PR-09: descriptor pack 生成与 schema 校验 + Recall@10 验证 [ENHANCED]

**涉及文件**:
- `polaris/cells/context/catalog/internal/descriptor_pack_generator.py`

**修复内容** (v2.1 增强):
1. Schema 校验加强
2. 版本号机制
3. 来源哈希 (content fingerprint)
4. Index write receipt
5. **新增 Recall@10 >= 92% 验证**:
```python
def verify_recall_at_10(candidate_set: list[str], ground_truth: list[str]) -> float:
    """
    验证检索召回率。
    防止 threshold 过紧导致"搜不出东西"。
    """
    hits = len(set(candidate_set[:10]) & set(ground_truth))
    return hits / len(ground_truth) if ground_truth else 0.0
```

#### PR-10: graph-constrained semantic rank + LanceDB Predicate Pushdown [ENHANCED]

**涉及文件**:
- `polaris/kernelone/akashic/knowledge_pipeline/lancedb_adapter.py`
- `polaris/cells/context/catalog/`

**修复内容** (v2.1 增强 - LanceDB Predicate Pushdown):
```python
# lancedb_adapter.py
async def semantic_search(
    self,
    query_vector: list[float],
    graph_owner: str,
    tenant_id: str,
    version_hash: str,
    limit: int = 50,
) -> list[DescriptorResult]:
    """
    LanceDB Predicate Pushdown 实现。

    Graph 过滤在向量库底层完成，p95 延迟 <= 40% 基线。
    """
    import lancedb

    db = lancedb.connect(self.db_path)
    table = db.open_table("descriptor_table")

    # [NEW] Predicate Pushdown - Graph 过滤在底层完成
    results = await table.search(query_vector, vector_column_name="embedding") \
        .where(f"graph_entity_id IN (SELECT id FROM graph_candidates WHERE owner = '{graph_owner}')") \
        .where(f"tenant_id = '{tenant_id}'") \
        .where(f"version_hash = '{version_hash}'") \
        .limit(limit) \
        .to_list()

    return [DescriptorResult(**r) for r in results]
```

**边界率定义** (v2.1 新增):
| 边界类型 | 定义 | 目标 |
|----------|------|------|
| 硬边界越界 | 跨租户、跨最高权限的精确匹配失败 | 0% |
| 软边界越界 | 语义相似但非精确匹配的越界 | <= 0.8% (95% CI) |

**退出条件**: 检索不越界、准确率提升、Recall@10 >= 92%

---

### M5: 观测与治理闭环 (第6-7周)

#### PR-11: trace/event/receipt 统一埋点 + 业务指标 [ENHANCED]

**涉及文件**:
- `polaris/kernelone/telemetry/`
- `polaris/kernelone/events/`

**修复内容** (v2.1 增强):
1. 统一 trace ID 传播
2. 关键节点事件写入
3. 失败证据链完整
4. **新增业务指标埋点**:
```python
# 关键指标采集点
metrics = {
    "agent_e2e_success_rate": Gauge("agent_e2e_success_rate"),
    "cognitive_drift_rate": Gauge("cognitive_drift_rate_per_week"),
    "context_projection_latency_p95": Histogram("context_projection_latency_p95"),
    "fallback_success_rate": Gauge("fallback_success_rate"),
    "hitl_timeout_rate": Gauge("hitl_timeout_rate"),
}
```

**退出条件**: 故障可 1 次定位根因

---

### M6: 灰度发布与回滚演练 (第8周)

#### PR-12: 文档、cells/subgraph、governance 资产同步

**涉及文件**:
- `docs/graph/catalog/cells.yaml`
- `docs/graph/subgraphs/*.yaml`
- `workspace/meta/context_catalog/*`

**修复内容**:
1. cells.yaml 补充未登记 Cell
2. Subgraph 同步更新
3. Context catalog 真实落盘

**灰度策略**:
| 阶段 | 流量比例 | 验证重点 |
|------|---------|---------|
| Phase 1 | 10% | 核心门禁通过 |
| Phase 2 | 30% | 业务指标无显著下降 |
| Phase 3 | 100% | 全量回归集通过 |

**退出条件**: 10%→30%→100% 连续 PASS

---

## 4. PR 依赖图 (v2.1 新增 PR-0X)

```
M0 ─→ M0.5 (Shadow Traffic + Rollback)
                │
                ▼
PR-01 ─┬─→ PR-02 ─┬─→ PR-04 ─┬─→ PR-05 ─┬─→ PR-08
       │          │          │          └─→ PR-07
       │          │          └─→ PR-06
       │          └─→ PR-03
       └───────────────┬─→ PR-09 ─┬─→ PR-10
                       └──────────┴─→ PR-11
                                              └─→ PR-12
```

---

## 5. 门禁指标量化阈值 (v2.1 增强版)

| 门禁 | 阈值 | 测量方法 |
|------|------|---------|
| ruff check --fix | 0 warnings/errors | CI |
| ruff format | 0 diff | CI |
| mypy --strict | Success | CI |
| pytest | 100% pass | CI (连续5次) |
| Context 投影一致性 | >=99.5% | Immutable Snapshot 回归集 |
| Fallback 成功率 | >=99% | 监控 |
| HITL 超时率 | <=0.1% | 监控 |
| 越权工具调用 | 0 | 审计日志 |
| Query 副作用 | 0 | 审计日志 |
| 硬边界越界率 | **0%** | Graph 验证 |
| 软边界越界率 | **<=0.8%** (95% CI) | 统计采样 |
| Recall@10 | **>=92%** | 检索回归集 |
| p95 context 延迟 | **<=40% 基线** | 性能回归 |
| **Agent E2E 成功率** | **>=98%** | 生产采样 |
| **认知进化漂移率** | **<=0.5%/周** | EvolutionEngine 版本对比 |

---

## 6. 风险与对策 (v2.1 增强版)

| 风险 | 对策 |
|------|------|
| 主链收敛导致隐性依赖断裂 | 先加"旁路探测告警"，后删旁路 |
| 开关切换引发行为漂移 | **Profile 固化 + 灰度 + 回放测试 + M0.5 回滚脚本** |
| 语义检索引入不稳定排序 | **Graph 先过滤 + LanceDB Predicate Pushdown + rank 可复现 + 固定 seed 回归集** |
| 治理脚本再次漂移 | **uv sync --frozen + 路径契约快照测试** |
| 历史资产与新资产并存造成双真相 | 明确 canonical path，旧资产只读并标记弃用 |
| **Threshold 过紧导致"搜不出东西"** | **Recall@10 >= 92% 验证** |
| **认知进化漂移未被监测** | **漂移率 <= 0.5%/周 监控** |

---

## 7. 验证卡片模板 (v2.1 增强版)

每个结构性 bug 必须填写:

```yaml
verification_card:
  id: VC-{date}-{issue}
  title: {问题简述}
  root_cause: {根因分析}
  fix: {修复方案}
  regression_tests:
    - test_case_1
    - test_case_2
  evidence_path: {证据路径}
  # [NEW] 业务影响验证
  business_impact:
    agent_e2e_success_rate_delta: {before} -> {after}
    p95_latency_delta: {before} -> {after}
  # [NEW] 边界情况覆盖
  boundary_cases:
    - hard_boundary: {case}
    - soft_boundary: {case}
    - recall_at_10: {case}
```

---

## 8. 角色分工 (v2.1 增强版)

| 角色 | 职责 | 新增任务 |
|------|------|---------|
| PM | 按周维护收敛看板与 gate 状态 | M0.5 Shadow Traffic 协调 |
| Architect | 每个里程碑前冻结边界决策 | Recall@10 / 越界率阈值设计 |
| Chief Engineer | PR 级别审查副作用与回退路径 | LanceDB Pushdown 评审 |
| Director | 实施与回归，确保 UTF-8 I/O 显式 | M0.5 回滚脚本实现 |
| QA | 门禁回归 + 审计包出具 | 业务指标埋点验证 |

---

## 9. 关键新增组件规格

### 9.1 Flag Service (轻量级自建)

```python
# polaris/infrastructure/flag_service.py
class FlagService:
    """轻量 Feature Flag Service，支持按租户/用户灰度。"""

    def __init__(self):
        self._flags: dict[str, dict] = {}
        self._load_from_config()

    def get_flags(self, namespace: str, context: dict[str, str]) -> dict:
        key = f"{namespace}:{context.get('tenant_id')}:{context.get('user_id')}"
        return self._flags.get(key, {})

    def set_flag(self, namespace: str, context: dict, overrides: dict) -> None:
        key = f"{namespace}:{context.get('tenant_id')}:{context.get('user_id')}"
        self._flags[key] = overrides

# 用法: 可随时切换到 LaunchDarkly / Unleash
```

### 9.2 Context Snapshot Store

```
workspace/meta/context_snapshots/
  2026-04-13T10:30:00Z.json  # timestamp 作为文件名
  2026-04-13T10:35:00Z.json
  ...
```

每个 snapshot 包含: `version`, `timestamp`, `input_hash`, `output_hash`, `projection`

### 9.3 Rollback Script Contract

```bash
# rollback_contextos_v2.sh 契约测试
$ ./rollback_contextos_v2.sh --dry-run
# 应该输出将要执行的操作，但不实际修改任何文件

$ time ./rollback_contextos_v2.sh all
# 应该 <= 30s 完成
```

---

## 10. 下一步行动 (v2.1 更新)

- [ ] 评审本蓝图 v2.1
- [ ] 确认 M0 + M0.5 基线冻结时间 (M0.5 需半天)
- [ ] 分配 PR 责任人 (特别关注 PR-0X: Immutable Snapshot)
- [ ] 建立周会节奏
- [ ] 确认 Flag Service 实现方案 (自建 vs LaunchDarkly/Unleash)
- [ ] 确认 LanceDB 版本是否支持 Predicate Pushdown

---

## 附录: v2 → v2.1 变更摘要

| 维度 | v2 原建议 | v2.1 升级内容 |
|------|----------|--------------|
| Context | Eviction Policy | **分层压缩 (Hierarchical Eviction Layer)** |
| Context | 投影一致性 >=99.5% | **+ Immutable Snapshot 生成** |
| 认知 | HITL (可选) | **强制 HITL + 15s 超时隔离** |
| 认知 | COGNITIVE_PROFILES | **+ Dynamic Feature Flag + Tenant Override** |
| 语义 | Graph Filter | **+ LanceDB Predicate Pushdown (p95 <= 40%)** |
| 边界 | "0 越界" | **硬边界 0% / 软边界 <=0.8% (95% CI)** |
| 检索 | 准确率提升 | **+ Recall@10 >= 92%** |
| 门禁 | CI 通过 | **+ Agent E2E >= 98% / 认知漂移 <= 0.5%/周** |
| M0 | 基线冻结 | **+ M0.5 Shadow Traffic + 1-Click Rollback** |
| 治理 | 路径探针 | **+ uv sync --frozen + 路径契约快照测试** |
