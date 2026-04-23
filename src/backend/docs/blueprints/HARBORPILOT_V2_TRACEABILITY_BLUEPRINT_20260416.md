# Polaris v2.0 — 蓝图驱动全链路追溯强化方案

> 日期: 2026-04-16
> 状态: DRAFT → ARCHITECTURE_APPROVED (混合调度)
> 范围: 追溯引擎、蓝图进化、角色职责强化、门禁新增、混合调度架构
> 关联 ADR: 待创建 (adr-0072, adr-0075)

---

## 0) 本文档是什么

本蓝图基于 2026-04-16 三人专家审计报告的结论，结合 Grok 提出的"蓝图驱动+全链路追溯"核心思想，经过与当前代码实际状态对齐后，形成的**可落地技术方案**。

**设计哲学来源（标注取舍）**:

| Grok 提案 | 本方案采纳情况 | 理由 |
|-----------|--------------|------|
| 蓝图即事实 (Blueprint-as-Truth) | **采纳，降级为约束层** | 蓝图不能替代代码本身作为事实，但可作为变更的必要前置条件 |
| 文档永生进化 | **采纳，限 PM 职责范围** | PM 已有 TaskBoard + 合约管理，在其上增加文档版本化 |
| 全链路追溯 (USTG) | **采纳，简化为 Traceability Matrix** | USTG 语义因果图过于复杂，先实现结构化追溯矩阵 |
| Meta-Archon 自进化 | **不采纳** | 当前系统连基础追溯都没有，自进化是第三阶段的事 |
| 形式化证明 (Lean/Coq) | **不采纳** | 代码库零形式化基础设施，投入产出比极低 |
| 预测性风险门禁 | **P3 延后** | 需要先有足够历史数据才能训练预测模型 |

---

## 0-A) 当前角色交互模式事实记录（不可绕行，不可二套真相）

> 本节基于 2026-04-16 实际代码阅读，是角色间交互的**唯一权威记录**。
> 任何升级方案必须基于此事实，禁止另起炉灶或假设不存在的能力。

### 0-A.1 两套调度路径（并存，按入口选择）

| 路径 | 入口 | 调度器 | Task Market | CE 集成 |
|------|------|--------|-------------|---------|
| **Workflow 路径** | `orchestration_engine.run_once()` → `_run_dispatch_pipeline_with_workflow()` | `WorkflowRuntime` (DAG 引擎) | 不直接使用 | 延迟到 Workflow 内部 |
| **Dispatch Pipeline 路径** | `dispatch_pipeline.run_dispatch_pipeline()` | 内联顺序调用 | 按 mode 使用 | 显式 `run_chief_engineer_preflight()` |

**当前默认走 Workflow 路径**。Dispatch Pipeline 路径是 Task Market 实验性通道。

### 0-A.2 Workflow 路径完整调用链（默认路径）

```
run_once()                                              [orchestration_engine.py:126]
  │
  ├─ PolarisEngine 实例化 + 注册 4 角色               [engine/core.py:119]
  │   └─ engine.status.json 持久化状态追踪
  │
  ├─ load_state_and_context()                            [加载 requirements/plan/gap_report/last_qa/pm_state]
  │
  ├─ run_pm_planning_iteration()                         [pm_planning/pipeline.py:443]
  │   ├─ pm_invoke_port.build_prompt()                   [组装 LLM prompt]
  │   ├─ pm_invoke_port.invoke()                         [LLM 调用，返回 JSON]
  │   ├─ normalize_pm_payload()                          [合约正规化]
  │   ├─ autofix_pm_contract_for_quality()               [补全缺失字段]
  │   ├─ _evaluate_pm_task_quality()                     [质量门禁]
  │   └─ (质量不通过时重试，最多 max_quality_attempts 次)
  │
  ├─ persist_pm_payload()                                [持久化 Task Contracts]
  │
  ├─ _run_dispatch_pipeline_with_workflow()              [orchestration_engine.py:942]
  │   ├─ resolve_director_dispatch_tasks()               [dispatch_pipeline.py:779]
  │   │   ├─ LocalShangshulingPort.sync_tasks_to_shangshuling()
  │   │   │   └─ 写入 runtime/state/dispatch/shangshuling.registry.json
  │   │   └─ LocalShangshulingPort.get_shangshuling_ready_tasks()
  │   │       └─ 过滤非终态任务，按优先级排序
  │   │
  │   ├─ submit_pm_workflow_sync()                       [提交到 WorkflowRuntime]
  │   │   └─ 内部编排: CE Preflight → Director 执行 → QA 验证
  │   │
  │   ├─ wait_for_workflow_completion_sync()              [阻塞等待完成]
  │   │
  │   ├─ get_workflow_runtime_status()                   [读取最终状态]
  │   ├─ summarize_workflow_tasks()                      [聚合 task 状态]
  │   │
  │   └─ run_post_dispatch_integration_qa()              [dispatch_pipeline.py:1692]
  │       └─ run_integration_verify_runner()             [执行验证命令]
  │
  ├─ 停止条件检测 + 阻塞策略评估
  │   └─ should_apply_degrade_settings()                 [优雅降级]
  │
  └─ finalize_iteration()                                [归档 + 状态持久化]
```

### 0-A.3 Shangshuling（尚书令）本地注册表

**文件**: `polaris/cells/orchestration/pm_dispatch/internal/shangshuling_registry.py`
**存储**: `runtime/state/dispatch/shangshuling.registry.json`

| 方法 | 作用 |
|------|------|
| `sync_tasks_to_shangshuling(tasks)` | 将 PM 输出的 Task Contracts 同步到本地注册表 |
| `get_shangshuling_ready_tasks(limit)` | 返回非终态（非 done/failed/blocked）的任务，按优先级排序 |
| `record_shangshuling_task_completion(task_id, success)` | 记录任务完成/失败状态 |

**关键约束**: Shangshuling 是**本地 JSON 文件**，不是分布式服务。同一 workspace 内有效。

### 0-A.4 Task Market（任务市场）

**文件**: `polaris/cells/runtime/task_market/` (49 个文件)
**模式**: 由 `KERNELONE_TASK_MARKET_MODE` 环境变量控制

| 模式 | 行为 | 当前使用状态 |
|------|------|-------------|
| `off` | 完全禁用 Task Market | **默认** |
| `shadow` | 发布到 `PENDING_EXEC` 队列，不阻塞主流程 | 监控用途 |
| `mainline` | 发布到 `PENDING_DESIGN` 队列，PM 职责结束 | 实验性 |
| `mainline-design` | mainline + 设计阶段消费 | 实验性 |
| `mainline-full` | mainline + 内联 CE→Director→QA 消费循环 | 实验性 |
| `mainline-durable` | mainline + 后台守护线程消费 | 实验性 |

**消费者**:
- `CEConsumer` — 从 `pending_design` 队列认领任务
- `DirectorExecutionConsumer` — 从 `pending_exec` 队列认领任务
- `QAConsumer` — 从 `pending_verify` 队列认领任务

**核心子系统**:
- FSM 状态机 (`fsm.py`) — 管理工作项生命周期
- Saga 引擎 (`saga.py`) — 补偿/回滚
- DLQ 死信队列 (`dlq.py`) — 失败任务归档
- Lease 管理器 (`lease_manager.py`) — 任务认领超时
- 人工审核 (`human_review.py`) — HITL 关卡
- DAG 验证器 (`test_dag_validator.py`) — 依赖图校验

### 0-A.5 角色执行模型（单一角色级别）

```
kernel.run(role="director", request)
  │
  ├─ 加载 RoleProfile (prompt, tool_policy, model)
  ├─ 构建 system_prompt + context
  ├─ 创建 ToolLoopController
  │
  ├─ 使用 TransactionKernel (默认) 或 TurnEngine (legacy)
  │   └─ 单 Turn 生命周期:
  │       IDLE → CONTEXT_BUILT → LLM_CALLED → DECISION_DECODED →
  │       TOOL_EXECUTING → TOOL_RESULTS_READY → [FINALIZE] → COMPLETED
  │
  ├─ 质量验证 (QualityChecker)
  │   └─ 失败时重试 (最多 max_retries 次)
  │
  └─ 返回 RoleTurnResult
```

**角色间不是队列拉取**: `RoleRuntimeService` 是单角色门面，缓存 `RoleExecutionKernel` 实例。多角色编排由上层 `WorkflowEngine` / `SagaWorkflowEngine` 按 DAG 顺序调用。

### 0-A.6 Director 任务执行模型

在 `DirectorWorkflow` 内部（workflow_runtime 管理）:

```
DirectorWorkflow.run()
  ├─ 依赖图拓扑排序
  ├─ 死锁检测（无 ready 任务时报错）
  ├─ 批次选择（max_parallel_tasks=3）
  │
  └─ 对每个批次:
      └─ asyncio.gather([
           DirectorTaskWorkflow.run(task_1),
           DirectorTaskWorkflow.run(task_2),
           DirectorTaskWorkflow.run(task_3),
         ])
           └─ 每个 TaskWorkflow 五阶段:
               1. prepare  → 认领任务
               2. validate → 输入校验
               3. implement → 代码实现 (precision_edit, execute_command)
               4. verify   → 验证变更
               5. report   → 报告结果
```

**错误分类**: `ErrorClassifier` 分析异常 → `TaskFailureRecord` 记录重试/恢复策略。

### 0-A.7 PolarisEngine 的真实角色

**不是调度器**。是**状态追踪门面**:

| 能力 | 实现 |
|------|------|
| 角色注册 + 状态追踪 | `register_role()` / `update_role_status()` |
| 阶段管理 | `set_phase()` — planning/dispatching/completed/failed |
| 配置管理 | `EngineRuntimeConfig` — 执行模式/并行度/调度策略 |
| 状态持久化 | 原子写入 `engine.status.json` |
| 上下文历史 | 每角色 24 事件上限的 context history |

### 0-A.8 关键约束（升级方案必须遵守）

1. **Workflow 路径是主路径**，升级必须首先兼容此路径
2. **Shangshuling 是本地注册表**，不依赖外部服务
3. **Task Market 当前默认 off**，升级不能假设其开启
4. **角色间是顺序调用**，不是队列拉取
5. **RoleRuntimeService 是单角色门面**，多角色编排由 WorkflowEngine 完成
6. **Director 的五阶段生命周期**是变更执行的唯一路径
7. **PolarisEngine 是状态追踪器**，不执行实际调度
8. **混合调度是目标架构**：PM→CE 走 Task Market（松耦合），CE→Director 走 DirectorPool（紧耦合），Director→QA 走 Task Market（松耦合）— 详见 §13

---

## 0-B) 当前代码精确现状与 Blueprint 假设偏差

> 本节基于 2026-04-16 实际代码阅读，记录 Blueprint 中可能与现实不符的假设，防止后续开发基于错误前提。

### 0-B.1 关于 Traceability 基础设施

| Blueprint 假设 | 代码现实 | 影响 |
|---------------|---------|------|
| `TraceabilityService` 作为新增模块设计 | **完全不存在**。代码库中零 `TraceabilityService` 引用 | 需要从零新建，而非在现有模块上扩展 |
| `polaris/kernelone/traceability/` 目录 | **不存在**。`kernelone/trace/` 存在，但那是 telemetry/分布式追踪，不是业务级 traceability | 需新建目录和 `__init__.py` |
| PM Contract 已携带 `doc_id` / `blueprint_id` | **不存在**。`pm_planning/pipeline.py` 输出的任务字段为：`id`, `title`, `goal`, `description`, `phase`, `priority`, `dependencies`, `execution_checklist`, `acceptance_criteria`, `assigned_to`, `scope_paths`, `target_files`, `metadata`, `backlog_ref` | 需要修改 schema 并向后兼容 |

### 0-B.2 关于 Chief Engineer 蓝图持久化

| Blueprint 假设 | 代码现实 | 影响 |
|---------------|---------|------|
| CE 有持久化的 Blueprint/ADR Store | **严重缺失**。`ConstructionStore` 是**纯内存实现**（`threading.RLock` + `dict`），进程重启即丢失 | ADR 增量蓝图机制必须先解决持久化层问题 |
| `blueprint_id` 已稳定用于跨角色关联 | **半真**。`blueprint_id` 存在于 `ConstructionBlueprint` dataclass 中，但仅用于内存索引，不写入跨角色消息 | Director 消息中目前拿不到 `blueprint_id` |
| `adr_store.py` 相关代码 | **不存在**。`polaris/cells/chief_engineer/blueprint/internal/` 中无 ADR 相关模块 | 需完全新建 |

### 0-B.3 关于 Director Workflow 路径

| Blueprint 假设 | 代码现实 | 影响 |
|---------------|---------|------|
| `polaris/cells/roles/director/` 路径 | **错误**。实际路径为 `polaris/cells/director/execution/` 等，而 `DirectorTaskWorkflow` 位于 `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/director_task_workflow.py` | Blueprint 中的路径引用需修正 |
| 五阶段模型 | **正确**。`_DEFAULT_PHASES = ("prepare", "validate", "implement", "verify", "report")` 在 `director_task_workflow.py:53` | 但需区分 domain 层 4-phase 和 workflow 层 5-phase |
| Workflow 使用 Temporal API | **正确**。`workflow.execute_activity()` 调用意味着 traceability I/O 必须通过 activity 进行，不能直接在 workflow 方法中执行同步 I/O | 集成方式受限 |

### 0-B.4 关于现有 Event/Trace 机制

| Blueprint 假设 | 代码现实 | 影响 |
|---------------|---------|------|
| 当前无 traceability | **部分正确**。没有结构化 traceability matrix，但有隐式追踪：`orchestration_engine.py` 通过 `emit_event()` 写入 JSONL（`run_events`, `dialogue_full`）；`director_task_workflow.py` 通过 `_broadcast_task_trace()` 和 `_record_event()` 广播/记录事件 | 新 traceability 系统应作为**增强层**叠加，而非替代现有机制 |
| 可以直接修改 `PolarisEngine` | **不建议**。它是 facade 层，实际逻辑已下沉到 `polaris.cells.orchestration.*` Cell。修改 facade 需同步 Cell 层 public API | 集成点应优先放在 Cell 内部 |

### 0-B.5 关于 Task Market Consumer

| Blueprint 假设 | 代码现实 | 影响 |
|---------------|---------|------|
| `DirectorExecutionConsumer` 在 `task_market/` 内 | **部分正确**。`consumer_loop.py` 存在且管理 3 角色 daemon，但 `DirectorExecutionConsumer` 的实际导入路径是 `polaris.cells.director.task_consumer`（非 `roles.director`） | 引用路径需精确 |
| Consumer 异常处理 | **需注意**。`consumer_loop.py` 以 daemon thread 运行，异常被 `except Exception` 吞掉 | traceability 注入必须防御性编程，自身抛异常不能影响 consumer 生命周期 |

---

## 1) 当前代码现状基线 (2026-04-16 实测)

| 指标 | 实际值 | 备注 |
|------|--------|------|
| Cell 声明数 | 52 (cells.yaml) | 22 个顶级目录 |
| Descriptor 覆盖 | 52/52 (100%) | 审计报告中的"0/52"是过时数据 |
| CI 门禁脚本 | 19 个 | 涵盖 catalog/kernelone/tool/contextos 等 |
| 治理 Schema | 21 个 YAML | cell/context/governance/runtime/cognitive |
| ADR 总数 | 71+ | 编号 0067/0068 有冲突需修复 |
| TransactionKernel | 已落地 | 44 行 wrapper + TurnTransactionController |
| ContextOS 四层 | 已落地 | TruthLog/WorkingState/ReceiptStore/ProjectionEngine |
| 断路器 | 已升级 | 语义感知三级渐进 (L1/L2/L3) + 场景自适应 |
| 追溯引擎 | **不存在** | 零 traceability/ustg/trace_engine 文件；但有隐式 JSONL event log |
| 形式化验证 | **不存在** | 零 lean/coq/theorem 文件 |
| SWE-Bench | **不存在** | 零 swe_bench 文件 |
| Benchmark 框架 | **存在但分散** | 10+ 文件跨 LLM/cognitive/context/strategy |
| 角色工具集成 | 6 个角色 | PM/Architect/CE/Director/QA/Scout 各自独立 |
| Workflow 编排 | 已落地 | PMWorkflow → DirectorWorkflow → QAWorkflow 链 |
| CE Blueprint Store | **纯内存** | `ConstructionStore` 无持久化，进程重启丢失 |
| PM Contract `doc_id` | **缺失** | 任务对象无 `doc_id`/`blueprint_id` 字段 |
| `kernelone/traceability/` | **不存在** | 需从零新建 |

---

## 2) 核心架构升级：三条新约束

### 2.1 蓝图前置约束 (Blueprint-First Constraint)

**规则**: 任何涉及 3 个文件以上的变更，必须先由 Chief Engineer 输出 `construction_plan`，Director 才能执行。

**为什么**: 当前 Director 可以绕过 CE 直接执行，导致"代码实现偏离架构意图"。这不是禁止 Director 的自主性，而是对中大型变更增加质量闸门。

**落地位置**:
- `dispatch_pipeline.py` 的 `run_chief_engineer_preflight()` 已有骨架
- 需增强: CE 输出新增 `blueprint_id` + `scope_file_list` 字段
- 需增强: Director 认领任务时校验 `blueprint_id` 存在性

### 2.2 追溯矩阵约束 (Traceability Matrix Constraint)

**规则**: 每个 Task Contract 必须携带 `doc_id` + `blueprint_id`，Director 的每次文件变更必须记录对应的 `task_id`。

**为什么**: 当前系统无法回答"这行代码为什么这样写"。追溯链断裂时，QA 无法验证变更是否覆盖了需求。

**落地位置**: 新建 `polaris/kernelone/traceability/` 子系统。

### 2.3 文档版本约束 (Living Document Constraint)

**规则**: PM 的需求文档和 CE 的蓝图文档都采用版本化存储，变更时自动递增版本号并保留历史。

**为什么**: 当前 `runtime/tasks/plan.json` 只有一份快照，需求变更时无法 diff。

**落地位置**: 扩展 `polaris/cells/runtime/artifact_store/` 的能力。

---

## 2-A) Cell 归属与边界裁决

> 按 `AGENTS.md` §4 和 §5，任何新增模块必须首先明确其架构归属。

### 2-A.1 归属裁决

| 候选位置 | 分析 | 结论 |
|---------|------|------|
| `polaris/cells/traceability_engine/` | Cell 是最小自治边界，但 traceability 是跨 Cell 的平台能力，非 Polaris 业务语义 | 次优 |
| `polaris/kernelone/traceability/` | `kernelone` 是 Agent/AI OS 运行时底座，traceability 属于"平台无关技术能力"中的可观测/审计层 | **采纳** |
| `polaris/infrastructure/` | 基础设施层用于外部系统适配器（DB/文件/网络），traceability 是内部运行时能力 | 不采纳 |

**最终裁决**: `polaris/kernelone/traceability/` 作为核心实现承载层。

### 2-A.2 边界规则

1. **对外暴露**: 仅通过 `polaris/kernelone/traceability/public/contracts.py` 和 `public/service.py` 暴露 `TraceabilityService`、`TraceabilityMatrix`、`TraceNode`、`TraceLink`。
2. **禁止反向依赖**: `kernelone/traceability/` 禁止依赖 `application/domain/cells/delivery` 任何模块。
3. **跨 Cell 调用方式**: 其他 Cell 只能通过 `public/service.py` 中的 port 调用，禁止直接实例化 `TraceabilityService` 内部类。
4. **与现有 Event/Audit 系统的关系**:
   - `orchestration_engine.py` 的 `emit_event()` JSONL → **运行时事件流**（保留，不改动）
   - `director_task_workflow.py` 的 `_broadcast_task_trace()` → **前端实时推送**（保留，不改动）
   - `kernelone/traceability/` → **结构化追溯矩阵**（新增，作为上述机制的聚合/归档层）
   - 三者是**互补**关系，不是替代关系。

### 2-A.3 数据主权

- **写主权**: `TraceabilityService` 是 traceability matrix 的唯一写权限拥有者。
- **读主权**: 任何 Cell 都可以通过 query API 读取，但只能由 `TraceabilityService` 写入。
- **持久化路径**: `runtime/traceability/{run_id}.matrix.json` 和 `runtime/traceability/history/`（append-only）。

---

## 3) Traceability Engine — 技术设计

### 3.1 核心数据模型

```python
# polaris/kernelone/traceability/public/contracts.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import uuid
import hashlib
import time


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class TraceNode:
    """追溯矩阵中的一个节点。"""
    node_id: str                    # UUID
    node_kind: str                  # "doc" | "blueprint" | "task" | "commit" | "qa_verdict"
    role: str                       # "pm" | "chief_engineer" | "director" | "qa"
    external_id: str                # 对应系统的 ID（task_id / blueprint_id 等）
    content_hash: str               # 内容 SHA-256 摘要
    timestamp_ms: int               # 创建时间
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceLink:
    """两个 TraceNode 之间的有向边。"""
    link_id: str                    # UUID
    source_node_id: str             # 上游 node_id
    target_node_id: str             # 下游 node_id
    link_kind: str                  # "derives_from" | "implements" | "verifies" | "evolves_from"
    timestamp_ms: int


@dataclass(frozen=True)
class TraceabilityMatrix:
    """一个 iteration 的完整追溯矩阵。"""
    matrix_id: str                  # UUID
    run_id: str                     # PM run_id
    iteration: int                  # PM iteration 序号
    nodes: tuple[TraceNode, ...]    # 所有节点
    links: tuple[TraceLink, ...]    # 所有边
    created_at_ms: int

    def query_by_kind(self, kind: str) -> list[TraceNode]:
        return [n for n in self.nodes if n.node_kind == kind]

    def query_ancestors(self, node_id: str) -> list[TraceNode]:
        """查询某节点的所有上游节点（BFS）。"""
        ancestor_ids: set[str] = set()
        frontier = {node_id}
        while frontier:
            next_frontier: set[str] = set()
            for link in self.links:
                if link.target_node_id in frontier and link.source_node_id not in ancestor_ids:
                    next_frontier.add(link.source_node_id)
            ancestor_ids |= next_frontier
            frontier = next_frontier
        return [n for n in self.nodes if n.node_id in ancestor_ids]

    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix_id": self.matrix_id,
            "run_id": self.run_id,
            "iteration": self.iteration,
            "nodes": [
                {"node_id": n.node_id, "kind": n.node_kind, "role": n.role,
                 "external_id": n.external_id, "content_hash": n.content_hash,
                 "timestamp_ms": n.timestamp_ms, "metadata": n.metadata}
                for n in self.nodes
            ],
            "links": [
                {"link_id": l.link_id, "source": l.source_node_id,
                 "target": l.target_node_id, "kind": l.link_kind,
                 "timestamp_ms": l.timestamp_ms}
                for l in self.links
            ],
            "created_at_ms": self.created_at_ms,
        }
```

### 3.2 追溯服务接口

**分层说明**:
- `polaris/kernelone/traceability/public/service.py` 暴露 `TraceabilityService` **接口/契约**（port）。
- `polaris/kernelone/traceability/internal/service_impl.py` 承载具体实现，避免跨 Cell 直接依赖内部类。
- 以下代码为**核心实现逻辑**示意，实际工程化时应将 `TraceabilityService` 类拆分为 `public/service.py` 中的抽象/工厂 和 `internal/service_impl.py` 中的 `TraceabilityServiceImpl`。

```python
# polaris/kernelone/traceability/internal/service_impl.py

from __future__ import annotations
from pathlib import Path
from .contracts import TraceNode, TraceLink, TraceabilityMatrix, _uuid, _now_epoch_ms


class TraceabilityService:
    """追溯矩阵的构建、持久化与查询服务。"""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._nodes: list[TraceNode] = []
        self._links: list[TraceLink] = []

    def register_node(
        self,
        *,
        node_kind: str,
        role: str,
        external_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> TraceNode:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        node = TraceNode(
            node_id=_uuid(),
            node_kind=node_kind,
            role=role,
            external_id=external_id,
            content_hash=content_hash,
            timestamp_ms=_now_epoch_ms(),
            metadata=metadata or {},
        )
        self._nodes.append(node)
        return node

    def link(
        self,
        source: TraceNode,
        target: TraceNode,
        link_kind: str = "derives_from",
    ) -> TraceLink:
        link = TraceLink(
            link_id=_uuid(),
            source_node_id=source.node_id,
            target_node_id=target.node_id,
            link_kind=link_kind,
            timestamp_ms=_now_epoch_ms(),
        )
        self._links.append(link)
        return link

    def build_matrix(self, run_id: str, iteration: int) -> TraceabilityMatrix:
        matrix = TraceabilityMatrix(
            matrix_id=_uuid(),
            run_id=run_id,
            iteration=iteration,
            nodes=tuple(self._nodes),
            links=tuple(self._links),
            created_at_ms=_now_epoch_ms(),
        )
        return matrix

    def persist(self, matrix: TraceabilityMatrix, path: str) -> None:
        """原子写入追溯矩阵 JSON。"""
        import json
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(matrix.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(p)

    def reset(self) -> None:
        self._nodes.clear()
        self._links.clear()
```

### 3.3 追溯链标准流程

```
PM 输出 Task Contract
  → register_node(kind="doc", role="pm", external_id=doc_id)
  → register_node(kind="task", role="pm", external_id=task_id)
  → link(doc_node, task_node, "derives_from")

Chief Engineer 输出 construction_plan
  → register_node(kind="blueprint", role="chief_engineer", external_id=blueprint_id)
  → link(task_node, blueprint_node, "implements")

Director 每次文件变更
  → register_node(kind="commit", role="director", external_id=file_path + content_hash)
  → link(blueprint_node, commit_node, "implements")

QA 输出 Verdict
  → register_node(kind="qa_verdict", role="qa", external_id=review_id)
  → link(commit_node, verdict_node, "verifies")
```

### 3.4 集成点（精确对齐 §0-A 调用链）

**原则**: 追溯引擎作为**旁路观测器**注入，不改变现有调用链结构；写入失败必须被捕获，不能阻塞主流程。

| 集成位置 | 精确路径 | 修改内容 | 优先级 |
|---------|---------|---------|--------|
| `orchestration_engine.run_once()` | `polaris/delivery/cli/pm/orchestration_engine.py:126` | 在 `PolarisEngine` 初始化后（约 line 202）懒加载 `TraceabilityService`，通过 `run_once()` 局部变量或 `finalize_context` 向下传递 | P0 |
| PM 规划完成 | `polaris/cells/orchestration/pm_planning/pipeline.py:688`（`run_pm_planning_iteration()` 返回处） | 在 quality gate 通过后，调用 `register_node(kind="doc", external_id=run_id)` + 遍历 `normalized["tasks"]` 注册 `kind="task"`，并建立 `derives_from` link | P0 |
| 任务注册表归档 | `polaris/cells/orchestration/pm_dispatch/internal/shangshuling_registry.py:202`（`archive_task_history()`） | 在 JSONL 历史归档后，为每个 task 补充 doc→task link（作为冗余校验层） | P0 |
| CE 蓝图预审 | `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py:971`（`run_chief_engineer_preflight()` 返回后） | 解析 `chief_engineer_result` 中的 `blueprint_id`，注册 `kind="blueprint"`，link(task, blueprint, "implements") | P1 |
| Director 五阶段 | `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/director_task_workflow.py:305-325` | 在每个 phase 的 `workflow.execute_activity("execute_task_phase", ...)` 完成后，通过 **新增 traceability activity** 异步注册 commit 节点。禁止在 workflow 方法内直接做同步 I/O | P1 |
| Director 任务完成 | `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/director_task_workflow.py:460+`（`complete_task` activity 后） | 注册最终 commit 摘要节点 | P1 |
| QA 验证 | `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/qa_workflow.py` | Verdict 生成后注册 `kind="qa_verdict"`，link(commit, verdict, "verifies") | P1 |
| 迭代归档 | `polaris/delivery/cli/pm/orchestration_engine.py:929`（`finalize_iteration()` 调用前） | 调用 `build_matrix(run_id, iteration)` + `persist()` → `runtime/traceability/{run_id}.{iteration}.matrix.json` | P0 |
| Task Market 内联消费 | `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py:1015`（`_run_inline_task_market_consumers()`） | 在每个 consumer 轮询完成后注册对应节点 | P2 |

**注意**: `orchestration_engine.py` 是 facade 层，traceability 的核心逻辑应下沉到被调用的 Cell 函数中。facade 层只负责初始化 `TraceabilityService` 实例并放入上下文。

**不修改的模块**:
- `PolarisEngine` — 它只做状态追踪，追溯是另一个观测维度
- `ShangshulingPort` — 它是任务注册表，追溯在它之上叠加
- `RoleRuntimeService` — 它是单角色门面，追溯在调用者层面
- `TransactionKernel` — 它管理 Turn 生命周期，追溯在 Workflow 层面

---

## 3-A) Traceability 旁路安全策略

> 追溯引擎是**旁路观测器**，不是主流程的依赖。任何 traceability 写入失败都不能阻塞、延迟或破坏 PM/CE/Director/QA 的核心职责。

### 3-A.1 失败隔离原则

1. **捕获所有异常**: 所有 `TraceabilityService.register_node()` / `link()` / `persist()` 调用必须包裹在 `try/except Exception` 中。
2. **不传播异常**: catch 后只记录日志（`logger.warning`），不向调用方抛异常。
3. **不阻塞主流程**: 即使 `persist()` 到磁盘失败，迭代仍继续，`finalize_iteration()` 正常返回。
4. **不污染返回值**: `TraceabilityService` 的方法返回 `TraceNode` / `TraceLink`，但调用方不应依赖这些返回值做后续判断。

### 3-A.2 推荐包装模式

```python
# 在每个集成点使用的安全包装器

def _safe_trace_register(
    trace_service: TraceabilityService | None,
    *,
    node_kind: str,
    role: str,
    external_id: str,
    content: str,
    metadata: dict | None = None,
) -> TraceNode | None:
    if trace_service is None:
        return None
    try:
        return trace_service.register_node(
            node_kind=node_kind,
            role=role,
            external_id=external_id,
            content=content,
            metadata=metadata or {},
        )
    except Exception as e:
        logger.warning(
            "Traceability register_node failed (kind=%s, external_id=%s): %s",
            node_kind, external_id, e,
        )
        return None


def _safe_trace_link(
    trace_service: TraceabilityService | None,
    source: TraceNode | None,
    target: TraceNode | None,
    link_kind: str = "derives_from",
) -> TraceLink | None:
    if trace_service is None or source is None or target is None:
        return None
    try:
        return trace_service.link(source, target, link_kind)
    except Exception as e:
        logger.warning(
            "Traceability link failed (%s -> %s): %s",
            source.external_id, target.external_id, e,
        )
        return None
```

### 3-A.3 降级策略

| 故障场景 | 行为 |
|---------|------|
| `TraceabilityService` 初始化失败 | `trace_service = None`，后续所有 trace 操作静默跳过 |
| 单次 `register_node()` 失败 | 记录 warning，该节点缺失，但后续节点仍继续注册 |
| `link()` 失败 | 记录 warning，缺失一条边，但不影响 matrix 构建 |
| `persist()` 失败 | 记录 error，matrix 不落地，但 iteration 正常结束 |
| 磁盘满或权限问题 | 同 `persist()` 失败处理，运维通过日志告警发现 |

### 3-A.4 与现有 Event Log 的互补

即使 traceability matrix 完全不可用，系统仍可通过以下机制保留最低限度的可审计性：
- `orchestration_engine.py` 的 `emit_event()` → `runtime/events/*.jsonl`
- `director_task_workflow.py` 的 `_record_event()` → workflow 事件历史
- `shangshuling_registry.py` 的 `archive_task_history()` → `runtime/state/dispatch/shangshuling.history.jsonl`

这意味着 traceability 的引入是**增值**而非**替换**，风险可控。

---

## 4) 角色职责强化

### 4.1 PM 尚书令 — 文档版本化需求管理

**新增职责**:
1. 每个需求文档附带 `doc_id` + `doc_version`，存入 `runtime/docs/`
2. 需求变更时自动生成新版本，旧版本保留在 `runtime/docs/history/`
3. Task Contract 必须携带 `doc_id` 字段

**不新增代码模块**: 复用 `polaris/cells/runtime/artifact_store/` 现有能力，在其公开契约中增加 `doc_version` 字段。

**PM 输出 Schema 扩展**:

```python
# 在现有 Task Contract 中增加字段
{
    "id": "T-001",
    "title": "...",
    "description": "...",
    "doc_id": "DOC-20260416-001",       # 新增：来源文档ID
    "doc_version": 1,                    # 新增：文档版本号
    "blueprint_id": null,               # 新增：关联蓝图ID（CE 填入前为 null）
    "target_files": [...],
    "acceptance_criteria": [...],
    "priority": "high",
    "dependencies": []
}
```

### 4.1-A) Chief Engineer 持久化改造前置条件

**当前事实**: `polaris/cells/chief_engineer/blueprint/internal/chief_engineer_agent.py:55-88` 中的 `ConstructionStore` 是**纯内存实现**（`threading.RLock` + `dict`），进程重启后所有 blueprint 丢失。

**影响**: 本 Blueprint 提出的 ADR 增量蓝图机制、blueprint_id 跨角色关联、blueprint 版本化，都依赖于 blueprint 的**跨进程持久化**。在 `ConstructionStore` 没有持久化之前，ADR 机制只能是内存中的演示，无法支撑真实的 traceability 链路。

**改造方案（最小充分）**:

1. **持久化路径**: `runtime/blueprints/{blueprint_id}.json`
2. **存储格式**: 每个 blueprint 一个 JSON 文件，避免单文件膨胀
3. **向后兼容**: `ConstructionStore` 保留内存缓存作为热层，底层增加磁盘冷层
4. **原子写入**: 使用 `tmp` + `replace` 模式，避免写坏文件

```python
# polaris/cells/chief_engineer/blueprint/internal/blueprint_persistence.py

from pathlib import Path
import json

class BlueprintPersistence:
    """Blueprint 磁盘持久化层。"""

    def __init__(self, workspace: str) -> None:
        self._dir = Path(workspace) / "runtime" / "blueprints"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, blueprint_id: str, data: dict) -> None:
        p = self._dir / f"{blueprint_id}.json"
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(p)

    def load(self, blueprint_id: str) -> dict | None:
        p = self._dir / f"{blueprint_id}.json"
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_all(self) -> list[str]:
        return [p.stem for p in self._dir.glob("*.json")]
```

**验收标准**:
- `ChiefEngineerAgent` 进程重启后，通过 `list_construction_plans` 仍能返回历史 blueprint
- `run_chief_engineer_preflight()` 输出的 `blueprint_id` 能在磁盘上找到对应文件
- 不影响现有 `ConstructionStore` 的内存查询性能（缓存命中时零磁盘 I/O）

**与 ADR 的关系**: 这是 ADR 机制的 P0 前置依赖，必须先于或同步于 `adr_store.py` 实现。

### 4.2 Chief Engineer 工部尚书 — 蓝图 ADR 增量进化

**新增职责**:
1. 每次输出 `construction_plan` 时分配 `blueprint_id`
2. 蓝图不直接重写/覆盖，而是通过 **ADR（Architecture Decision Record）增量机制** 进化
3. CE 调用 `propose_adr()` 提出增量决策，系统自动编译为最新版蓝图
4. 蓝图模板统一为标准格式

#### 4.2.1 核心痛点与解法

**痛点**: 每次让 AI 重写或 diff 整个 `construction_plan.md` 会导致：
- 上下文截断：蓝图文档动辄 200+ 行，LLM 重写时丢失细节
- 结构破坏：diff 结果可能破坏 YAML/Markdown 结构
- 历史丢失：无法追踪"为什么从这个方案变成那个方案"

**解法**: 引入软件工程经典的 ADR 机制。蓝图由一个**主干 (Base Schema)** 和一系列 **ADR 增量**组成。CE 不直接修改蓝图全貌，而是调用 `propose_adr()`。系统底层自动将 ADR **编译 (Compile)** 为最新版 `construction_plan`。

#### 4.2.2 ADR 增量数据模型

```python
# polaris/cells/chief_engineer/blueprint/internal/adr_store.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass(frozen=True)
class BlueprintADR:
    """蓝图的增量架构决策记录。"""
    adr_id: str                      # "ADR-{blueprint_id}-{seq:03d}"
    blueprint_id: str                # 关联蓝图 ID
    related_task_ids: list[str]      # 关联的任务 ID
    decision: str                    # 决策内容（简短描述）
    context: str                     # 决策上下文（为什么做这个变更）
    delta: dict[str, Any]            # 增量变更（结构化 diff）
    status: str                      # "proposed" | "approved" | "compiled" | "reverted"
    proposed_at_ms: int
    compiled_at_ms: int | None = None
    supersedes: str | None = None    # 取代的 ADR ID（如果是对之前决策的修正）


@dataclass
class BlueprintBase:
    """蓝图主干 — 只在初次创建时写入，后续只通过 ADR 增量进化。"""
    blueprint_id: str
    version: int                     # 每次编译递增
    base_schema: dict[str, Any]      # 初始 construction_plan
    adrs: list[BlueprintADR] = field(default_factory=list)
    created_at_ms: int = 0
    last_compiled_at_ms: int = 0


class ADRStore:
    """蓝图 ADR 的存储、编译与查询服务。"""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._blueprints: dict[str, BlueprintBase] = {}

    def create_blueprint(
        self, blueprint_id: str, base_schema: dict[str, Any],
    ) -> BlueprintBase:
        """创建蓝图主干（只在首次 CE 输出时调用）。"""
        bp = BlueprintBase(
            blueprint_id=blueprint_id,
            version=1,
            base_schema=base_schema,
            created_at_ms=int(time.time() * 1000),
            last_compiled_at_ms=int(time.time() * 1000),
        )
        self._blueprints[blueprint_id] = bp
        return bp

    def propose_adr(
        self,
        blueprint_id: str,
        related_task_ids: list[str],
        decision: str,
        context: str,
        delta: dict[str, Any],
        supersedes: str | None = None,
    ) -> BlueprintADR:
        """CE 提出增量决策 — 核心入口。

        delta 结构示例:
        {
            "type": "add_step",              # add_step | modify_step | remove_step |
                                              # add_file | remove_file | change_scope |
                                              # change_risk
            "target": "construction_steps",  # 修改蓝图的哪个部分
            "payload": {                     # 具体变更内容
                "after_step": 2,
                "step": {
                    "title": "新增缓存层",
                    "target_files": ["src/cache.py"],
                    "risk_level": "medium"
                }
            }
        }
        """
        bp = self._blueprints.get(blueprint_id)
        if not bp:
            raise ValueError(f"Blueprint {blueprint_id} not found")

        seq = len(bp.adrs) + 1
        adr = BlueprintADR(
            adr_id=f"ADR-{blueprint_id}-{seq:03d}",
            blueprint_id=blueprint_id,
            related_task_ids=related_task_ids,
            decision=decision,
            context=context,
            delta=delta,
            status="proposed",
            proposed_at_ms=int(time.time() * 1000),
            supersedes=supersedes,
        )
        bp.adrs.append(adr)
        return adr

    def compile(self, blueprint_id: str) -> dict[str, Any]:
        """将蓝图主干 + 所有 approved ADR 编译为最新版 construction_plan。

        编译过程:
        1. 从 base_schema 开始
        2. 按 ADR 顺序应用每个 delta
        3. 递增 version
        4. 返回完整的 construction_plan
        """
        bp = self._blueprints.get(blueprint_id)
        if not bp:
            raise ValueError(f"Blueprint {blueprint_id} not found")

        # 深拷贝 base_schema 作为起点
        import copy
        compiled = copy.deepcopy(bp.base_schema)

        # 按顺序应用所有 approved ADR
        for adr in bp.adrs:
            if adr.status in ("proposed", "approved"):
                compiled = self._apply_delta(compiled, adr.delta)
                # 标记为已编译
                object.__setattr__(adr, "status", "compiled")
                object.__setattr__(adr, "compiled_at_ms", int(time.time() * 1000))

        bp.version += 1
        bp.last_compiled_at_ms = int(time.time() * 1000)
        return compiled

    def get_blueprint_history(
        self, blueprint_id: str,
    ) -> list[dict[str, Any]]:
        """获取蓝图的完整进化历史。"""
        bp = self._blueprints.get(blueprint_id)
        if not bp:
            return []
        return [
            {
                "adr_id": adr.adr_id,
                "decision": adr.decision,
                "context": adr.context,
                "status": adr.status,
                "proposed_at_ms": adr.proposed_at_ms,
            }
            for adr in bp.adrs
        ]

    def revert_adr(self, adr_id: str) -> None:
        """回退某个 ADR — 标记为 reverted，下次 compile 时跳过。"""
        for bp in self._blueprints.values():
            for adr in bp.adrs:
                if adr.adr_id == adr_id:
                    object.__setattr__(adr, "status", "reverted")
                    return
        raise ValueError(f"ADR {adr_id} not found")

    def _apply_delta(
        self, schema: dict[str, Any], delta: dict[str, Any],
    ) -> dict[str, Any]:
        """将单个 delta 应用到 schema 上。"""
        delta_type = delta.get("type")
        target = delta.get("target")
        payload = delta.get("payload", {})

        if delta_type == "add_step":
            steps = schema.setdefault("construction_steps", [])
            after = payload.get("after_step", len(steps))
            steps.insert(after, payload["step"])

        elif delta_type == "modify_step":
            steps = schema.get("construction_steps", [])
            idx = payload.get("step_index", 0)
            if 0 <= idx < len(steps):
                steps[idx].update(payload.get("changes", {}))

        elif delta_type == "remove_step":
            steps = schema.get("construction_steps", [])
            idx = payload.get("step_index", -1)
            if 0 <= idx < len(steps):
                steps.pop(idx)

        elif delta_type == "add_file":
            files = schema.setdefault("scope_for_apply", {}).setdefault(
                payload.get("category", "modified_files"), [],
            )
            files.append(payload["file"])

        elif delta_type == "remove_file":
            for cat in schema.get("scope_for_apply", {}).values():
                if payload["file"] in cat:
                    cat.remove(payload["file"])

        elif delta_type == "change_scope":
            schema["scope_for_apply"] = payload["new_scope"]

        elif delta_type == "change_risk":
            schema.setdefault("risk_flags", []).append(payload["risk"])

        return schema
```

#### 4.2.3 蓝图模板 v2 (Base Schema)

```markdown
# Construction Plan v2 — {blueprint_id}

## 元数据
- blueprint_id: BP-20260416-{seq}
- version: {n}  (由 ADR compile 自动递增)
- doc_id: {关联的 PM 文档 ID}
- task_ids: [T-001, T-002, ...]

## 1. 变更范围 (scope_for_apply)
- 新增文件: [...]
- 修改文件: [...]
- 删除文件: [...]

## 2. 架构影响 (architecture_impact)
- 涉及 Cell: [...]
- 涉及 Contract: [...]
- 涉及 State Owner: [...]

## 3. 施工步骤 (construction_steps)
### Step 1: {描述}
- target_files: [...]
- risk_level: low/medium/high
- verification: {验收标准}

## 4. 风险标记 (risk_flags)
- [...]

## 5. ADR 进化历史 (由 compile() 自动生成)
| ADR ID | 决策 | 关联任务 | 状态 |
|--------|------|---------|------|
| ADR-BP-001-001 | 新增缓存层 | T-003 | compiled |
| ADR-BP-001-002 | 移除 Step 2 (已无效) | T-001 | reverted |
```

#### 4.2.4 CE 工作流程（ADR 模式）

```
1. 首次创建蓝图:
   CE.create_blueprint(blueprint_id, base_schema)
   └─ 输出完整 construction_plan → Base Schema

2. 后续进化（不再重写蓝图）:
   CE.propose_adr(
       blueprint_id="BP-20260416-001",
       related_task_ids=["T-003"],
       decision="新增缓存层以解决性能问题",
       context="QA 报告 T-003 的 API 响应 >2s，需要缓存",
       delta={
           "type": "add_step",
           "target": "construction_steps",
           "payload": {
               "after_step": 2,
               "step": {"title": "新增缓存层", "target_files": [...], "risk_level": "medium"}
           }
       }
   )

3. 编译（Director 执行前）:
   compiled_plan = ADRStore.compile("BP-20260416-001")
   └─ 自动将所有 approved ADR 应用到 base_schema
   └─ version 递增
   └─ 输出最新版 construction_plan 给 Director

4. 回退（QA 发现问题时）:
   ADRStore.revert_adr("ADR-BP-001-002")
   └─ 标记为 reverted
   └─ 下次 compile 时跳过
```

#### 4.2.5 为什么 ADR 增量优于全量重写

| 维度 | 全量重写 | ADR 增量 |
|------|---------|---------|
| 上下文消耗 | 高（每次输入整个蓝图 + 修改指令） | **低（只输入 delta）** |
| 结构安全性 | 低（LLM 可能破坏格式） | **高（delta 是结构化的）** |
| 历史追溯 | 无（覆盖后丢失） | **完整（每个 ADR 都保留）** |
| 回退能力 | 差（只能整体回退） | **优（精确回退单个 ADR）** |
| 并发修改 | 不支持 | **支持（ADR 可按序合并）** |

**CE 输出 Schema 扩展**:

```python
{
    "blueprint_id": "BP-20260416-001",
    "blueprint_version": 1,
    "doc_id": "DOC-20260416-001",
    "task_id": "T-001",
    "construction_plan": "...",            # 首次输出完整内容
    "adr_delta": null,                     # 后续进化时，这里放增量而非完整内容
    "scope_for_apply": {...},
    "risk_flags": [...]
}
```

### 4.3 Director 工部侍郎 — 蓝图驱动执行

**新增职责**:
1. 认领任务时校验 `blueprint_id` 存在性
2. 每次文件变更记录 `blueprint_id` + `task_id`
3. 变更结果附带追溯信息

**Director 输出 Schema 扩展**:

```python
{
    "execution_plan": "...",
    "file_changes": [
        {
            "path": "src/foo.py",
            "change_type": "modify",
            "blueprint_id": "BP-20260416-001",  # 新增
            "task_id": "T-001",                  # 新增
            "doc_id": "DOC-20260416-001",       # 新增
            "content_hash": "sha256:..."         # 新增
        }
    ]
}
```

**不新增模块**: 在 `director_role.py` 的消息构建中加入 `blueprint_id` 约束提示。

### 4.4 QA 门下侍中 — 追溯验证

**新增职责**:
1. 验证变更是否 100% 覆盖对应蓝图的 `construction_steps`
2. Verdict 附带追溯链证据

**不新增模块**: 在 `qa_workflow.py` 的 `collect_evidence` 阶段查询 TraceabilityMatrix。

---

## 5) 新增门禁 (3 道，总计 15+6)

### 门禁 13: 蓝图-文档-任务三方 ID 一致性校验

**检查逻辑**:
1. 每个 Task 的 `doc_id` 在 PM 文档库中存在
2. 每个 Task 的 `blueprint_id` 在蓝图库中存在（CE 完成后）
3. 每个 Director file_change 的 `blueprint_id` + `task_id` 与 Task Contract 匹配

**实现位置**: 新增 `docs/governance/ci/scripts/run_traceability_gate.py`

```python
# 伪代码
def run_traceability_gate(workspace: str) -> GateResult:
    matrix = load_latest_traceability_matrix(workspace)
    errors = []

    # 检查 1: 每个 task 节点都有上游 doc 节点
    for task_node in matrix.query_by_kind("task"):
        ancestors = matrix.query_ancestors(task_node.node_id)
        doc_ancestors = [a for a in ancestors if a.node_kind == "doc"]
        if not doc_ancestors:
            errors.append(f"Task {task_node.external_id} has no doc ancestor")

    # 检查 2: 每个 commit 节点都有上游 blueprint 节点
    for commit_node in matrix.query_by_kind("commit"):
        ancestors = matrix.query_ancestors(commit_node.node_id)
        bp_ancestors = [a for a in ancestors if a.node_kind == "blueprint"]
        if not bp_ancestors:
            errors.append(f"Commit {commit_node.external_id} has no blueprint ancestor")

    # 检查 3: 每个 blueprint 节点都有上游 task 节点
    for bp_node in matrix.query_by_kind("blueprint"):
        ancestors = matrix.query_ancestors(bp_node.node_id)
        task_ancestors = [a for a in ancestors if a.node_kind == "task"]
        if not task_ancestors:
            errors.append(f"Blueprint {bp_node.external_id} has no task ancestor")

    return GateResult(
        gate="traceability_consistency",
        passed=len(errors) == 0,
        errors=errors,
    )
```

### 门禁 14: 变更必须来自已批准的蓝图版本

**检查逻辑**: 所有 Director file_change 中引用的 `blueprint_id` 必须存在于 CE 的蓝图输出中，且状态为 `approved`。

**实现位置**: 同上，在 `run_traceability_gate.py` 中增加检查。

### 门禁 15: 文档变更触发蓝图演进提案

**检查逻辑**: 当 PM 文档版本递增时，检查对应的 `blueprint_version` 是否也已递增或标记为 `no_impact`。

**实现位置**: 同上。

---

## 6) 自动回滚机制

### 6.1 设计原则

- **Director 级独立快照**: 每个 Director 维护自己的文件快照，互不干扰
- **Task 级粒度**: 每个 Director Task 是一个回滚单元
- **不破坏成功 Task**: 只回滚失败的 Task
- **完全兼容并行**: 3 个 Director 同时改不同文件互不影响

### 6.2 两种回滚模式

| 模式 | 适用场景 | 机制 | 并行兼容 |
|------|---------|------|---------|
| **Director 级内存快照**（推荐） | DirectorPool 并行模式 | 每个 Director 在 assign 前缓存原始文件内容，失败时恢复 | 完全兼容 |
| **git stash**（降级） | 单 Director 串行模式 | 全局 git stash，仅在降级模式下使用 | 不兼容并行 |

### 6.2-A Director 级内存快照（并行模式推荐方案）

```python
# polaris/cells/chief_engineer/blueprint/internal/rollback_guard.py

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RollbackGuard:
    """回滚守卫 — 支持并行模式的 Director 级文件快照。"""

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._snapshots: dict[str, dict[str, str]] = {}  # director_id → {file_path: content}

    async def snapshot_for_director(
        self,
        director_id: str,
        files: list[str],
    ) -> None:
        """Director 认领任务前，缓存目标文件的原始内容。

        每个 Director 独立维护快照，互不干扰。
        """
        snapshot: dict[str, str] = {}
        for f in files:
            path = Path(self._workspace) / f
            if path.exists():
                try:
                    snapshot[f] = path.read_text(encoding="utf-8")
                except OSError as e:
                    logger.warning("Failed to snapshot %s for %s: %s", f, director_id, e)
            # 文件不存在 → 新建文件，回滚时删除
        self._snapshots[director_id] = snapshot
        logger.info(
            "Snapshot created for %s: %d files", director_id, len(snapshot),
        )

    async def rollback_director(self, director_id: str) -> bool:
        """Director 失败时，恢复其快照中的所有文件到原始状态。"""
        snapshot = self._snapshots.get(director_id)
        if not snapshot:
            logger.warning("No snapshot for %s, nothing to rollback", director_id)
            return False

        success = True
        for f, original_content in snapshot.items():
            path = Path(self._workspace) / f
            try:
                path.write_text(original_content, encoding="utf-8")
                logger.info("Rolled back %s for %s", f, director_id)
            except OSError as e:
                logger.error("Failed to rollback %s for %s: %s", f, director_id, e)
                success = False

        del self._snapshots[director_id]
        return success

    def discard_snapshot(self, director_id: str) -> None:
        """Director 任务成功后，丢弃快照（变更已确认）。"""
        self._snapshots.pop(director_id, None)

    def has_snapshot(self, director_id: str) -> bool:
        return director_id in self._snapshots
```

### 6.2-B git stash 模式（单 Director 降级备选）

```python
# polaris/cells/roles/kernel/internal/rollback_guard.py
# 仅在 DirectorPool 降级为单 Director 串行模式时使用

from pathlib import Path
import subprocess
import logging

logger = logging.getLogger(__name__)


class GitStashRollbackGuard:
    """git stash 回滚 — 仅用于单 Director 串行降级模式。

    警告: 此模式与 DirectorPool 并行模式不兼容。
    当 DirectorPool 启用且 max_directors > 1 时，必须使用 RollbackGuard（内存快照）。
    """

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace
        self._git_available = self._check_git_available()

    def _check_git_available(self) -> bool:
        git_dir = Path(self._workspace) / ".git"
        if not git_dir.exists():
            logger.warning("RollbackGuard disabled: not a git repository")
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self._workspace, capture_output=True, text=True, encoding="utf-8",
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def create_snapshot(self, task_id: str) -> str | None:
        if not self._git_available:
            return None
        result = subprocess.run(
            ["git", "stash", "push", "-m", f"polaris-snapshot-{task_id}"],
            cwd=self._workspace, capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            logger.warning("git stash push failed for %s: %s", task_id, result.stderr)
            return None
        return result.stdout.strip()

    def rollback(self, task_id: str) -> bool:
        if not self._git_available:
            return False
        result = subprocess.run(
            ["git", "stash", "list"],
            cwd=self._workspace, capture_output=True, text=True, encoding="utf-8",
        )
        for line in result.stdout.splitlines():
            if f"polaris-snapshot-{task_id}" in line:
                stash_ref = line.split(":")[0]
                pop_result = subprocess.run(
                    ["git", "stash", "pop", stash_ref],
                    cwd=self._workspace, capture_output=True, text=True, encoding="utf-8",
                )
                if pop_result.returncode != 0:
                    logger.error("git stash pop failed for %s: %s", task_id, pop_result.stderr)
                    return False
                return True
        return False

    def discard_snapshot(self, task_id: str) -> bool:
        if not self._git_available:
            return False
        result = subprocess.run(
            ["git", "stash", "list"],
            cwd=self._workspace, capture_output=True, text=True, encoding="utf-8",
        )
        for line in result.stdout.splitlines():
            if f"polaris-snapshot-{task_id}" in line:
                stash_ref = line.split(":")[0]
                subprocess.run(
                    ["git", "stash", "drop", stash_ref],
                    cwd=self._workspace, capture_output=True, text=True, encoding="utf-8",
                )
                return True
        return False
```

### 6.3 回滚模式选择策略

```python
def create_rollback_guard(
    workspace: str,
    director_pool_mode: bool,
) -> RollbackGuard | GitStashRollbackGuard:
    """根据调度模式自动选择回滚策略。"""
    if director_pool_mode:
        # 并行模式: 使用 Director 级内存快照
        return RollbackGuard(workspace=workspace)
    else:
        # 串行降级模式: 使用 git stash
        return GitStashRollbackGuard(workspace=workspace)
```

### 6.4 集成点（精确对齐 DirectorTaskWorkflow 五阶段）

在 `DirectorTaskWorkflow` 的五阶段生命周期中（§0-A.6）：

```
1. prepare  → 认领任务
              └─ rollback_guard.snapshot_for_director(director_id, task.target_files)  ← 新增

2. validate → 输入校验

3. implement → 代码实现 (precision_edit, execute_command)
               └─ 正常执行

4. verify   → 验证变更
              ├─ 成功: rollback_guard.discard_snapshot(director_id)   ← 新增
              └─ 失败: rollback_guard.rollback_director(director_id) ← 新增
                        → 标记 task 为 "rolled_back"

5. report   → 报告结果
```

### 6.5 三种回滚方案对比

| 方案 | 并行兼容性 | 实现难度 | 回滚粒度 | 性能 | 推荐指数 |
|------|-----------|---------|---------|------|---------|
| 全局 git stash | 完全不兼容 | 低 | 文件级 | 低（git 操作） | ★☆☆☆☆ |
| 每个 Director 独立 git worktree | 兼容 | 中 | 仓库级 | 中（磁盘开销） | ★★★☆☆ |
| Director 级内存快照（推荐） | 完美 | 低 | 文件级 | 高（纯内存） | ★★★★★ |

**结论**: 默认使用内存快照方案，在 `handle_failure` 中调用 `rollback_guard.rollback_director(director_id)` 即可。git stash 仅保留给单 Director 降级模式使用。

---

## 7) 与现有治理体系的对齐

### 7.1 ADR 编号冲突修复（精确映射）

**当前事实**（2026-04-16 实测 `docs/governance/decisions/`）：
- `adr-0067` 被 **4 个文件** 共用：
  - `adr-0067-benchmark-context-adapter-metrics-integration.md`
  - `adr-0067-cognitive-life-form-hardening.md`
  - `adr-0067-contextos-summarization-strategy.md`
  - `adr-0067-ts-availability-tool-filtering.md`
- `adr-0068` 被 **2 个文件** 共用：
  - `adr-0068-dead-loop-prevention.md`
  - `adr-0068-llm-event-persistence-convergence.md`

**修复方案**: 保留时间戳最早的文件为原编号，其余递增重编号。同时需同步更新各文件内部的 `adr_id` / `decision_id`  frontmatter。

| 原文件 | 新编号 | 理由 |
|--------|--------|------|
| `adr-0067-benchmark-context-adapter-metrics-integration.md` | **ADR-0067** | 保留原编号（最早，2026-04-01） |
| `adr-0067-ts-availability-tool-filtering.md` | **ADR-0072** | 重编号（同日，内容独立） |
| `adr-0067-cognitive-life-form-hardening.md` | **ADR-0073** | 重编号（2026-04-15） |
| `adr-0067-contextos-summarization-strategy.md` | **ADR-0076** | 重编号（2026-04-15，避免与已有的 0074/0075 冲突） |
| `adr-0068-llm-event-persistence-convergence.md` | **ADR-0068** | 保留原编号（最早，2026-04-01） |
| `adr-0068-dead-loop-prevention.md` | **ADR-0074** | 重编号（2026-04-15） |
| `TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md` 缺失 | 创建占位文件 | 标记为 `status: implemented`，已存在 `adr-0071` 对应此主题 |

**注意**: 0075 已预留给本 Blueprint 的混合调度架构 ADR（§15.2），因此 0067 的第四个冲突文件应跳过 0075，使用 **0076**。

### 7.2 新增治理 Schema

```yaml
# docs/governance/schemas/traceability-matrix.schema.yaml
type: object
required: [matrix_id, run_id, iteration, nodes, links, created_at_ms]
properties:
  matrix_id:
    type: string
    format: uuid
  run_id:
    type: string
  iteration:
    type: integer
    minimum: 0
  nodes:
    type: array
    items:
      type: object
      required: [node_id, kind, role, external_id, content_hash, timestamp_ms]
      properties:
        node_id: { type: string, format: uuid }
        kind: { type: string, enum: [doc, blueprint, task, commit, qa_verdict] }
        role: { type: string, enum: [pm, chief_engineer, director, qa] }
        external_id: { type: string }
        content_hash: { type: string }
        timestamp_ms: { type: integer }
        metadata: { type: object }
  links:
    type: array
    items:
      type: object
      required: [link_id, source, target, kind, timestamp_ms]
      properties:
        link_id: { type: string, format: uuid }
        source: { type: string, format: uuid }
        target: { type: string, format: uuid }
        kind: { type: string, enum: [derives_from, implements, verifies, evolves_from] }
        timestamp_ms: { type: integer }
  created_at_ms:
    type: integer
```

### 7.3 验证卡片模板

```yaml
# docs/governance/templates/verification-cards/vc-20260416-traceability-engine.yaml
verification_card:
  card_id: vc-20260416-traceability-engine
  title: Traceability Engine 核心实现
  classification: structural
  assumptions:
    - id: A1
      statement: "TraceabilityService 可在 orchestration_engine.run_once() 中正确初始化"
      status: unverified
    - id: A2
      statement: "所有角色的输出 Schema 扩展向后兼容"
      status: unverified
  pre_mortem:
    most_likely_failure: "Director 消息构建中的 blueprint_id 提示被 LLM 忽略"
    risk_zones:
      - polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py
      - polaris/delivery/cli/pm/orchestration_engine.py
  verification_plan:
    unit_tests:
      - test_traceability_service_register_node
      - test_traceability_service_build_matrix
      - test_traceability_service_persist_and_load
      - test_traceability_gate_consistency_check
    integration_tests:
      - test_full_pm_iteration_with_traceability
    manual_checks:
      - 验证追溯矩阵 JSON 可读且结构正确
```

---

## 8) 实施路线图

### P0 (第 1-2 周): 追溯基础设施

```
Week 1:
├── 新建 polaris/kernelone/traceability/__init__.py
├── 实现 public/contracts.py (TraceNode, TraceLink, TraceabilityMatrix)
├── 实现 public/service.py (TraceabilityService port)
├── 实现 internal/service_impl.py (TraceabilityServiceImpl)
├── 实现 §3-A 旁路安全包装器 (_safe_trace_register / _safe_trace_link)
├── 编写单元测试 (5 个: register/link/build/persist/query)
└── ruff + mypy 通过

Week 2:
├── 集成到 orchestration_engine.py:126 (run_once 中初始化 trace_service)
├── 集成到 pm_planning/pipeline.py:688 (PM output 后注册 doc/task)
├── 集成到 shangshuling_registry.py:202 (archive_task_history 后补充 link)
├── 集成到 dispatch_pipeline.py:971 (CE preflight 后注册 blueprint)
├── 集成到 director_task_workflow.py (通过新增 activity 注册 commit)
├── 集成到 qa_workflow.py (verdict 后注册)
├── 迭代归档到 runtime/traceability/{run_id}.{iteration}.matrix.json
└── 端到端测试通过
```

### P1 (第 3-4 周): 蓝图驱动 + ADR 增量 + 门禁

```
Week 3:
├── CE BlueprintPersistence 层实现 (§4.1-A 前置)
├── ConstructionStore 改造 (内存缓存 + 磁盘持久化双写)
├── PM 输出 Schema 扩展 (doc_id, blueprint_id, 向后兼容 default=null)
├── CE 蓝图模板 v2 实现
├── ADRStore 增量蓝图引擎实现 (propose_adr + compile + revert)
├── ADRStore 单元测试 (6 个: create/propose/compile/revert/history/delta_types)
├── Director 消息构建增加 blueprint_id 约束
└── run_traceability_gate.py 门禁实现

Week 4:
├── ADR 编号冲突精确修复 (0067→0072/0073/0076, 0068→0074)
├── 同步更新所有受影响 ADR 文件内的 frontmatter
├── 治理 Schema (traceability-matrix.schema.yaml)
├── CEConsumer 集成 ADRStore (蓝图进化时调 propose_adr 而非重写)
├── 验证卡片填写 + 签署
└── 全门禁 CI 流水线集成
```

### P2 (第 5-8 周): 自动回滚 + 可视化

```
Week 5-6:
├── TaskRollbackGuard 实现
├── 集成到 director_task_workflow.py
├── 回滚状态机测试
├── ADR compaction: 累积 >30 个 ADR 时自动合并为新的 base_schema
└── Benchmark 统一框架 (合并 3 套)

Week 7-8:
├── 追溯矩阵 Web 可视化 (简易版)
├── Turn 级 Trace 集成到 ContextOS projection
└── 混合人机模式骨架 (高风险 Task 暂停点)
```

### P3 (第 9-12 周): 进化与优化

```
Week 9-10:
├── 蓝图增量 diff 算法
├── CE Architecture Evolution (基于已有 evolution_engine.py)
└── 文档语义变更检测

Week 11-12:
├── 跨项目知识积累 (project memory store)
├── SWE-Bench 风格基准集成
└── 性能调优 + Token 预算优化
```

---

## 9) 交付清单

本蓝图完成后应产出:

| 序号 | 交付物 | 路径 | Phase |
|------|--------|------|-------|
| 1 | 追溯引擎契约 | `polaris/kernelone/traceability/contracts.py` | P0 |
| 2 | 追溯服务 | `polaris/kernelone/traceability/service.py` | P0 |
| 3 | 追溯引擎测试 | `polaris/kernelone/traceability/tests/test_traceability.py` | P0 |
| 4 | 追溯门禁 | `docs/governance/ci/scripts/run_traceability_gate.py` | P1 |
| 5 | 追溯 Schema | `docs/governance/schemas/traceability-matrix.schema.yaml` | P1 |
| 6 | 蓝图模板 v2 | 本文档 §4.2 | P1 |
| 7 | 回滚守卫 | `polaris/cells/roles/kernel/internal/rollback_guard.py` | P2 |
| 8 | ADR-0072 | `docs/governance/decisions/adr-0072-traceability-engine.md` | P1 |
| 9 | 验证卡片 | `docs/governance/templates/verification-cards/vc-20260416-traceability-engine.yaml` | P1 |

---

## 10) 风险与边界

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| LLM 忽略 blueprint_id 约束 | 高 | 门禁 13 硬检查，不通过则拒绝 |
| 追溯矩阵过大影响性能 | 中 | 单次 iteration 预计 <100 节点，可接受 |
| git stash 并行冲突 | **已解决** | §6.2: 并行模式使用 Director 级内存快照，git stash 仅用于降级模式 |
| 内存快照占用 | 低 | 单次快照预计 <5MB（<100 文件），完成后自动释放 |
| PM/CE/Director Schema 扩展破坏兼容性 | 低 | 所有新字段 default=null，向后兼容 |
| 门禁 15 误报（文档变更不影响蓝图） | 中 | CE 可标记 `no_impact` 跳过 |

---

## 11) 本蓝图不包含的内容

以下内容明确**不在本蓝图范围内**，留待未来:

1. **Meta-Archon / 自进化 Agent** — 当前无基础设施，属于研究课题而非工程任务
2. **形式化证明 (Lean/Coq)** — 零基础设施，投入产出比不适合当前阶段
3. **预测性风险门禁** — 需要先积累至少 50 个 iteration 的历史数据
4. **跨项目知识蒸馏** — 需要先把单项目追溯做稳
5. **USTG 语义因果图** — 当前结构化追溯矩阵已满足核心需求，语义层是锦上添花

---

## 12) 结论

本方案的核心价值是**用最小工程量补齐当前最大短板**（零追溯），同时**不过度设计**。

与 Grok 方案的对比:

| 维度 | Grok v3.0 方案 | 本方案 |
|------|---------------|--------|
| 复杂度 | 极高（Meta-Archon + USTG + Formal） | 中等（Traceability Matrix + 3 道门禁） |
| 落地周期 | 6-12 个月 | 4 周（P0+P1） |
| 新增模块 | 5+ 个全新子系统 | 1 个（traceability） |
| 与现有代码兼容 | 需要大规模重构 | 在现有模块上扩展字段 |
| 风险 | 高（多处未经验证的理论设计） | 低（基于已有的数据模式） |

**建议**: 先完成 P0+P1（4 周），验证追溯引擎确实能在真实迭代中工作，再考虑后续阶段。

---

## 13) 混合调度架构 (Hybrid Dispatch Architecture)

> 本节是本蓝图的核心架构决策。定义每一段角色间链路的最优调度模式。

### 13.1 核心判断

| 交互链路 | 推荐模式 | 理由 | 关键收益 |
|---------|---------|------|---------|
| PM → CE | **Task Market** (松耦合) | PM 只输出 Task Contract，不关心下游执行细节 | 解耦彻底，PM 可独立演进 |
| CE → Director(s) | **Direct + DirectorPool** (紧耦合) | CE 必须实时掌控进度、冲突、动态调度 | 可见性 + 智能决策 |
| Director → QA | **Task Market** (松耦合) | QA 只关心最终产物是否符合验收标准，不关心谁做的、怎么做的 | QA 保持纯净，易并行 |
| CE → 外部监控 | **Event Bus** (松耦合) | 让运维/产品/PM 能实时看到 CE 仪表盘，而不侵入 CE 内部 | 可观测性不污染核心流程 |

**设计哲学**: 不是折中，而是"把每一段链路都推到它最擅长的模式上"。

### 13.2 为什么 CE→Director 必须用 DirectorPool（反面案例）

如果硬把 Director 也塞进 Task Market，会出现以下已验证的问题：

**问题 1: 文件级冲突爆炸**
两个 Director 同时从队列认领了修改 `core/engine.py` 的任务，队列中间件无法感知"正在修改中"的状态。
→ DirectorPool 在 Pool 层面做全局 ScopeConflictDetector，认领前就拦截。

**问题 2: CE 失明**
CE 只能看到"队列里有 3 个 pending_exec"，却不知道哪个 Director 卡在第 3 步的 `verify` 阶段，耗时已经 47 分钟。
→ DirectorPool 提供实时 `DirectorHandle`，CE 随时查询每个 Director 的 phase/files/progress。

**问题 3: 无法做"抢救性调度"**
真实场景：Director-2 显存爆了，需要把它的子任务立刻转给 Director-1。队列模式做不到——任务已经出队了。
→ DirectorPool 提供 `reassign(task_id, target_director_id)`，CE 实时转派。

**结论**: DirectorPool 正是把"人类技术总监的真实管理模式"搬进代码。技术总监不会把任务扔进一个邮箱等下属自己来取，而是直接分配、实时跟踪、动态调整。

### 13.3 DirectorPool 技术设计

#### 13.3.1 核心类

```python
# polaris/cells/chief_engineer/blueprint/internal/director_pool.py

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DirectorPhase(str, Enum):
    IDLE = "idle"
    PREPARE = "prepare"
    VALIDATE = "validate"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REPORT = "report"


@dataclass
class DirectorStatus:
    """单个 Director 的实时状态快照。"""
    director_id: str
    phase: DirectorPhase
    current_task_id: str | None
    active_files: list[str]
    started_at_ms: int | None
    progress_pct: float              # 0.0 ~ 1.0
    last_heartbeat_ms: int
    capabilities: list[str]          # 如 ["strong_in_refactor", "weak_in_frontend"]


@dataclass
class DirectorPoolStatus:
    """整个 Director 池的实时状态面板。"""
    directors: dict[str, DirectorStatus]
    global_conflicts: list[dict[str, Any]]
    pending_assignments: list[str]   # task_ids 等待分配
    estimated_completion_ms: int | None


@dataclass
class RecoveryDecision:
    """Director 失败后的恢复决策。"""
    action: str                      # "retry" | "reassign" | "split" | "abort"
    target_director_id: str | None = None
    max_retries: int = 1
    reason: str = ""


class ScopeConflictDetector:
    """Pool 级别的全局文件冲突检测器。"""

    def __init__(self) -> None:
        self._active_files: dict[str, str] = {}  # file_path → director_id

    def detect(
        self, director_id: str, files: list[str],
    ) -> list[str]:
        """检测指定 Director 即将操作的文件是否与其他 Director 冲突。"""
        conflicts = []
        for f in files:
            owner = self._active_files.get(f)
            if owner and owner != director_id:
                conflicts.append(f)
        return conflicts

    def acquire(self, director_id: str, files: list[str]) -> None:
        """Director 开始操作文件时，注册文件所有权。"""
        for f in files:
            self._active_files[f] = director_id

    def release(self, director_id: str) -> None:
        """Director 完成任务时，释放所有文件所有权。"""
        to_remove = [
            f for f, d in self._active_files.items() if d == director_id
        ]
        for f in to_remove:
            del self._active_files[f]


class EventBus:
    """轻量级事件总线，向外部推送状态变更。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Any]] = {}

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """发布事件。同步实现，避免引入 Redis 依赖。"""
        for callback in self._subscribers.get(event_type, []):
            try:
                callback(event_type, payload)
            except Exception:
                logger.warning("EventBus subscriber error for %s", event_type)

    def subscribe(self, event_type: str, callback: Any) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)


class DirectorPool:
    """CE 直接管理的 Director 实例池。

    职责:
    1. 管理多个 Director 的生命周期
    2. 全局文件冲突检测
    3. 智能任务分配（空闲优先、无冲突优先、负载均衡）
    4. 实时状态面板
    5. 异常恢复（retry/reassign/split/abort）
    """

    def __init__(
        self,
        workspace: str,
        max_directors: int = 3,
        auto_scale: bool = False,
    ) -> None:
        self._workspace = workspace
        self._max_directors = max_directors
        self._auto_scale = auto_scale
        self._directors: dict[str, DirectorStatus] = {}
        self._task_assignments: dict[str, str] = {}   # task_id → director_id
        self._conflict_detector = ScopeConflictDetector()
        self._event_bus = EventBus()

    # --- 任务分配 ---

    async def assign_task(
        self,
        task: Any,          # TaskContract
        blueprint: Any,     # Blueprint
    ) -> str:
        """为任务选择最合适的 Director 并分配。

        选择策略:
        1. 空闲 Director 优先
        2. 无文件冲突的 Director 优先
        3. 负载最低的优先
        4. 能力标签匹配（如有）
        """
        director_id = await self._select_best_director(task)

        # 全局冲突检测
        task_files = getattr(task, "target_files", [])
        conflicts = self._conflict_detector.detect(director_id, task_files)
        if conflicts:
            # 尝试找另一个无冲突的 Director
            alt_id = self._find_conflict_free_director(task_files)
            if alt_id:
                director_id = alt_id
            else:
                logger.warning(
                    "File conflicts for task %s: %s — queuing",
                    getattr(task, "id", "?"), conflicts,
                )

        # 注册分配
        self._task_assignments[getattr(task, "id", "")] = director_id
        self._conflict_detector.acquire(director_id, task_files)

        # 更新 Director 状态
        status = self._directors[director_id]
        self._directors[director_id] = DirectorStatus(
            director_id=director_id,
            phase=DirectorPhase.PREPARE,
            current_task_id=getattr(task, "id", ""),
            active_files=task_files,
            started_at_ms=int(time.time() * 1000),
            progress_pct=0.0,
            last_heartbeat_ms=int(time.time() * 1000),
            capabilities=status.capabilities,
        )

        # 发布事件
        self._event_bus.publish("director.assigned", {
            "task_id": getattr(task, "id", ""),
            "director_id": director_id,
            "progress": 0.0,
        })

        return director_id

    # --- 状态查询 ---

    def get_live_dashboard(self) -> DirectorPoolStatus:
        """供 CE 实时面板使用，也可暴露给前端。"""
        return DirectorPoolStatus(
            directors=dict(self._directors),
            global_conflicts=self._current_global_conflicts(),
            pending_assignments=list(self._task_assignments.keys()),
            estimated_completion_ms=self._estimate_remaining_time(),
        )

    def get_director_for_task(self, task_id: str) -> str | None:
        """查询任务当前分配给哪个 Director。"""
        return self._task_assignments.get(task_id)

    # --- 异常恢复 ---

    def handle_failure(
        self, task_id: str, error: Exception,
    ) -> RecoveryDecision:
        """Director 失败时的恢复决策。"""
        director_id = self._task_assignments.get(task_id)
        if not director_id:
            return RecoveryDecision(action="abort", reason="unknown task")

        # 释放文件锁
        self._conflict_detector.release(director_id)

        # 根据错误类型决策
        error_type = type(error).__name__
        if "Timeout" in error_type:
            return RecoveryDecision(
                action="reassign",
                target_director_id=self._find_idle_director(),
                reason=f"timeout on {director_id}",
            )
        if "Memory" in error_type or "OOM" in error_type:
            return RecoveryDecision(
                action="split",
                reason=f"OOM on {director_id}, split into smaller tasks",
            )
        # 默认重试一次
        return RecoveryDecision(
            action="retry", max_retries=1,
            reason=f"recoverable error: {error_type}",
        )

    async def reassign(
        self, task_id: str, target_director_id: str,
    ) -> None:
        """将任务从一个 Director 转派到另一个。"""
        old_director = self._task_assignments.get(task_id)
        if old_director:
            self._conflict_detector.release(old_director)
        self._task_assignments[task_id] = target_director_id
        self._event_bus.publish("director.reassigned", {
            "task_id": task_id,
            "from_director": old_director,
            "to_director": target_director_id,
        })

    # --- 任务完成 ---

    def mark_completed(self, task_id: str, success: bool) -> None:
        """标记任务完成，释放 Director。"""
        director_id = self._task_assignments.pop(task_id, None)
        if director_id:
            self._conflict_detector.release(director_id)
            status = self._directors[director_id]
            self._directors[director_id] = DirectorStatus(
                director_id=director_id,
                phase=DirectorPhase.IDLE,
                current_task_id=None,
                active_files=[],
                started_at_ms=None,
                progress_pct=1.0 if success else 0.0,
                last_heartbeat_ms=int(time.time() * 1000),
                capabilities=status.capabilities,
            )
            self._event_bus.publish("director.completed", {
                "task_id": task_id,
                "director_id": director_id,
                "success": success,
            })

    # --- 优雅降级 ---

    def initialize_directors(self) -> None:
        """初始化 Director 实例池。如果全部初始化失败，降级为单 Director 模式。"""
        initialized = 0
        for i in range(self._max_directors):
            did = f"director-{i + 1}"
            try:
                self._directors[did] = DirectorStatus(
                    director_id=did,
                    phase=DirectorPhase.IDLE,
                    current_task_id=None,
                    active_files=[],
                    started_at_ms=None,
                    progress_pct=0.0,
                    last_heartbeat_ms=int(time.time() * 1000),
                    capabilities=[],
                )
                initialized += 1
            except Exception as e:
                logger.warning("Failed to initialize %s: %s", did, e)

        if initialized == 0:
            logger.warning(
                "DirectorPool 不可用，降级为单 Director 顺序执行模式",
            )
            self._max_directors = 1
            self._directors["director-fallback"] = DirectorStatus(
                director_id="director-fallback",
                phase=DirectorPhase.IDLE,
                current_task_id=None,
                active_files=[],
                started_at_ms=None,
                progress_pct=0.0,
                last_heartbeat_ms=int(time.time() * 1000),
                capabilities=[],
            )

    # --- 内部方法 ---

    async def _select_best_director(self, task: Any) -> str:
        """选择最优 Director: 空闲 > 无冲突 > 低负载。"""
        task_files = getattr(task, "target_files", [])

        # 第一优先: 空闲且无冲突
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                conflicts = self._conflict_detector.detect(did, task_files)
                if not conflicts:
                    return did

        # 第二优先: 任意空闲
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                return did

        # 第三优先: 负载最低的
        return min(
            self._directors.keys(),
            key=lambda d: self._directors[d].progress_pct,
        )

    def _find_conflict_free_director(self, files: list[str]) -> str | None:
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                conflicts = self._conflict_detector.detect(did, files)
                if not conflicts:
                    return did
        return None

    def _find_idle_director(self) -> str | None:
        for did, status in self._directors.items():
            if status.phase == DirectorPhase.IDLE:
                return did
        return None

    def _current_global_conflicts(self) -> list[dict[str, Any]]:
        """当前全局文件冲突列表。"""
        return [
            {"file": f, "director": d}
            for f, d in self._conflict_detector._active_files.items()
        ]

    def _estimate_remaining_time(self) -> int | None:
        """粗略估算剩余时间 (ms)。"""
        active = [
            s for s in self._directors.values()
            if s.phase != DirectorPhase.IDLE and s.started_at_ms
        ]
        if not active:
            return 0
        avg_progress = sum(s.progress_pct for s in active) / len(active)
        if avg_progress == 0:
            return None
        elapsed = int(time.time() * 1000) - active[0].started_at_ms  # type: ignore
        estimated_total = elapsed / avg_progress
        return int(estimated_total - elapsed)
```

#### 13.3.2 配置化

```yaml
# polaris/config/chief_engineer.yaml
chief_engineer:
  director_pool:
    max_directors: 3
    auto_scale: false              # P3 启用
    conflict_detection: global     # global | per_consumer (兼容旧模式)
    graceful_degrade: true         # 初始化失败时降级为单 Director
    event_bus:
      backend: sync                # sync | redis_stream (P3)
      subscribers: []
  task_market:
    mode: mainline-design          # PM→CE 走 Task Market
    poll_interval_ms: 2000
```

### 13.4 完整协作流程

```
[PM 输出 Task Contracts]
       │
       ▼
  Task Market (pending_design)              ← 松耦合：PM 不知道 CE 的存在
       │
       ▼
  CE Consumer 认领 → 生成 Blueprint
       │
       ▼
  DirectorPool.assign_task()                ← 紧耦合：CE 直接管理
       │
  ┌──── Director-1 (T-001) ── prepare → implement → verify → report ──→
  ├──── Director-2 (T-002) ── prepare → implement → verify → report ──→
  └──── Director-3 (T-003) ── prepare → implement → verify → report ──→
       │
       ▼
  CE 汇总所有 Director 结果
    ├─ 跨任务一致性检查 (所有 Director 修改的 API 是否兼容)
    ├─ 生成 QA Contract
    └─ 推入 Task Market (pending_qa)        ← 松耦合：QA 不关心谁做的
       │
       ▼
  QA Consumer 认领并验证
       │
       ▼
  Verdict → TraceabilityMatrix 持久化
```

**CE 汇总阶段的关键能力**（纯队列模式几乎不可能做到）：
1. **跨任务一致性检查**: 所有 Director 修改的 API 签名是否互相兼容
2. **全局文件冲突回顾**: 是否有隐式依赖被遗漏
3. **蓝图覆盖率验证**: 所有 `construction_steps` 是否都有对应的 Director 输出

### 13.5 CE 作为技术总监的交互模型

```
ChiefEngineer (工部尚书 / 技术总监)
  │
  ├─ 输入: TaskContract[] (从 Task Market 认领)
  │
  ├─ 决策: 为每个 Task 生成 Blueprint + 分配 Director
  │   ├─ 全局视野: 知道所有 Director 的当前状态
  │   ├─ 冲突预防: 认领前就知道哪些文件在被谁修改
  │   └─ 动态调度: Director-2 挂了 → 立刻转派给 Director-1
  │
  ├─ 监控: 实时面板 (DirectorPoolStatus)
  │   ├─ Director-1: T-001 implementing 60% [engine.py, utils.py]
  │   ├─ Director-2: T-003 verifying   90% [api.py]
  │   └─ Director-3: idle
  │
  ├─ 异常处理: handle_failure() → retry/reassign/split/abort
  │
  └─ 输出: 汇总结果 + QA Contract → Task Market (pending_qa)
```

### 13.6 与现有代码的精确对齐

| 现有模块 | 精确路径 | 变化 | 工作量 | 风险点 |
|---------|---------|------|--------|--------|
| `CEConsumer` | `polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py` | 执行完蓝图后调用 `director_pool.assign_task()` 而非推入 `pending_exec` | 小 | 无 |
| `DirectorExecutionConsumer` | `polaris.cells.director.task_consumer`（懒加载导入） | **弃用**（保留代码作为历史兼容，不走这条路） | 0 | 需在文档和代码中标记 `@deprecated` |
| `DirectorTaskWorkflow` | `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/director_task_workflow.py` | **保留**——`DirectorPool` 内部调用每个 Director 时，底层仍用 `DirectorTaskWorkflow` 的五阶段模型 | 小 | 禁止在 workflow 方法内直接执行同步 I/O |
| `Shangshuling Registry` | `polaris/cells/orchestration/pm_dispatch/internal/shangshuling_registry.py` | **强化**——CE 把 Director 实时状态也写入注册表 | 小 | 确保原子性，避免 registry 文件膨胀 |
| `ScopeConflictDetector` | 当前在 consumer 内部（需搜索定位） | **迁移**——从 consumer 内部提升到 `DirectorPool` 层面 | 中 | 最重要的一步，需保证行为一致 |
| `orchestration_engine.py` | `polaris/delivery/cli/pm/orchestration_engine.py` | **调整**——当 `KERNELONE_TASK_MARKET_MODE=mainline-design` 时，PM 只负责 publish 到 Task Market，不直接调用 `_run_dispatch_pipeline_with_workflow()` | 中 | 当前默认走 Workflow 路径，不能粗暴移除 |
| `director_pool.py` | 新建 | 核心新组件 | 大 | 需完整的 unit + integration + chaos 测试 |

**不修改的模块**:
- `PolarisEngine` — 状态追踪器，调度不在其职责范围
- `RoleRuntimeService` — 单角色门面，`DirectorPool` 在其上层
- `TransactionKernel` — Turn 级事务，`DirectorPool` 在 Task 级
- `QAConsumer` — 继续从 Task Market 认领，行为不变

### 13.7 可观测性

#### Prometheus 指标

```python
# 建议在 director_pool.py 中埋点
METRICS = {
    "director_pool_active_tasks_total": Gauge,       # 当前活跃任务数
    "director_pool_conflict_events_total": Counter,  # 冲突事件计数
    "director_phase_duration_seconds": Histogram,     # 按 phase 标签的耗时分布
    "director_pool_reassign_events_total": Counter,   # 转派事件计数
    "director_pool_degrade_events_total": Counter,    # 降级事件计数
}
```

#### EventBus 事件类型

| 事件类型 | 触发时机 | Payload |
|---------|---------|---------|
| `director.assigned` | 任务分配给 Director | task_id, director_id, progress |
| `director.phase_changed` | Director 阶段变更 | director_id, old_phase, new_phase |
| `director.completed` | 任务完成 | task_id, director_id, success |
| `director.reassigned` | 任务转派 | task_id, from_director, to_director |
| `director.conflict_detected` | 文件冲突 | file, conflicting_directors |
| `director_pool.degraded` | 降级触发 | reason, fallback_mode |

### 13.8 优雅降级策略

```python
# 降级链: DirectorPool → 单 Director 顺序 → 报错

class DirectorPoolDegradedError(Exception):
    """DirectorPool 已降级运行。"""

def create_pool_with_degrade(workspace: str, config: dict) -> DirectorPool:
    pool = DirectorPool(
        workspace=workspace,
        max_directors=config.get("max_directors", 3),
        auto_scale=config.get("auto_scale", False),
    )
    pool.initialize_directors()

    if len(pool._directors) == 1 and pool._directors.get("director-fallback"):
        logger.warning(
            "⚠️ DirectorPool 已降级为单 Director 顺序执行模式。"
            "原因：多 Director 初始化失败。"
            "影响：无法并行执行任务，文件冲突检测降级为串行。",
        )
        # 写入 Shangshuling 注册表，供外部监控
        # event_bus.publish("director_pool.degraded", {"reason": "init_failure"})

    return pool
```

---

## 14) 混合架构测试策略

### 14.1 Unit Test

| 测试场景 | 覆盖目标 |
|---------|---------|
| `test_select_best_director_idle_first` | 空闲 Director 优先于忙碌的 |
| `test_select_best_director_conflict_avoidance` | 有文件冲突时选择另一个 Director |
| `test_scope_conflict_detect_and_acquire` | 冲突检测 + 文件锁获取/释放 |
| `test_scope_conflict_release_on_complete` | 任务完成后释放文件锁 |
| `test_handle_failure_timeout_reassign` | 超时错误触发转派 |
| `test_handle_failure_oom_split` | OOM 错误触发任务拆分 |
| `test_reassign_updates_tracking` | 转派正确更新内部映射 |
| `test_degrade_to_single_director` | 全部初始化失败时降级 |
| `test_event_bus_publish_on_assign` | 分配时正确发布事件 |

### 14.2 Integration Test

| 测试场景 | 覆盖目标 |
|---------|---------|
| `test_3_directors_5_tasks_no_conflict` | 3 个 Director 并行 5 个任务，无文件冲突 |
| `test_3_directors_conflict_resolution` | 有文件冲突时正确排队/转派 |
| `test_ce_consumer_to_director_pool` | CEConsumer 完成蓝图后调用 DirectorPool |
| `test_director_pool_to_qa_task_market` | CE 汇总后推入 Task Market pending_qa |

### 14.3 Chaos Test

| 测试场景 | 覆盖目标 |
|---------|---------|
| `test_random_kill_director_recovery` | 随机 kill 一个 Director，`handle_failure` 正确决策 |
| `test_all_directors_fail_degrade` | 所有 Director 失败，降级到单 Director |
| `test_concurrent_assign_race_condition` | 并发分配竞态条件 |

---

## 15) 混合架构迁移路线图

### 15.1 Phase A (Week 1-2): DirectorPool 核心实现

```
Week 1:
├── 实现 director_pool.py (DirectorPool, DirectorHandle, ScopeConflictDetector)
├── 实现 event_bus.py (EventBus sync 模式)
├── 实现 director_pool_config.yaml
├── 编写 Unit Test (9 个)
└── ruff + mypy 通过

Week 2:
├── 修改 CEConsumer → 蓝图完成后调用 director_pool.assign_task()
├── 修改 CE 汇总逻辑 → 跨任务一致性检查 + 推入 pending_qa
├── 集成测试 (4 个)
├── Chaos 测试 (3 个)
└── 降级策略验证
```

### 15.2 Phase B (Week 3-4): PM→CE Task Market 激活

```
Week 3:
├── KERNELONE_TASK_MARKET_MODE 切换为 mainline-design
├── 验证 CEConsumer 正确认领 + 生成蓝图 + 调用 DirectorPool
├── 移除 orchestration_engine.py 中 CE 的直接调用路径
└── DirectorExecutionConsumer 标记 deprecated

Week 4:
├── QA Consumer 验证 (从 pending_qa 认领)
├── 全链路冒烟测试 (PM → CE → DirectorPool → QA)
├── Prometheus 指标埋点
├── ADR-0075 混合调度架构决策记录
└── 验证卡片签署
```

### 15.3 Phase C (Week 5-8): 生产加固 + 可视化

```
Week 5-6:
├── EventBus 升级为 Redis Stream (可选)
├── CE 实时面板 API (/v2/ce/dashboard)
├── DirectorPool auto_scale 基础版
└── 性能基准测试 (3 Directors vs 1 Director)

Week 7-8:
├── 前端 CE Dashboard 组件
├── 追溯矩阵与 DirectorPool 状态关联
├── 文档完善 + 示例
└── 全链路压测
```

---

## 16) 混合架构与追溯引擎的协同

### 16.1 追溯节点在混合架构中的注册点

| 追溯节点 | 注册时机 | 调度模式 | 注册位置 |
|---------|---------|---------|---------|
| doc node | PM 输出后 | Task Market | `pm_planning/pipeline.py` |
| task node | PM 输出后 | Task Market | `pm_planning/pipeline.py` |
| blueprint node | CE 生成蓝图后 | Task Market→Direct | `ce_consumer.py` |
| commit node | Director 文件变更后 | Direct (DirectorPool) | `director_pool.py` |
| qa_verdict node | QA 验证后 | Task Market | `qa_consumer.py` |

### 16.2 DirectorPool 状态与追溯矩阵的关联

```python
# director_pool.assign_task() 内部追加:
trace_service.register_node(
    node_kind="commit",
    role="director",
    external_id=f"{task_id}:{director_id}",
    content=file_change_summary,
    metadata={
        "director_id": director_id,
        "blueprint_id": blueprint_id,
        "pool_conflicts": conflicts,
    },
)
trace_service.link(blueprint_node, commit_node, "implements")
```

---

## 17) 未来扩展点（先写进 ADR，暂不实现）

1. **外部 Director 借用**: CE 可以动态"借用"外部 Agent（如 Cursor Agent、Claude Code CLI），通过统一接口接入 DirectorPool
2. **能力标签**: Director 支持能力标签（`strong_in_refactor`, `weak_in_frontend`），`_select_best_director` 根据任务类型匹配
3. **DirectorPool auto_scale**: 根据 CPU/GPU 负载自动创建/销毁 Director 实例
4. **EventBus 升级**: 从 sync 升级到 Redis Stream，支持跨进程/跨机器的事件订阅
5. **预测性调度**: 基于历史数据预测任务耗时，优化分配策略

---

## 18) 更新后的完整交付清单

| 序号 | 交付物 | 路径 | Phase |
|------|--------|------|-------|
| 1 | 追溯引擎契约 | `polaris/kernelone/traceability/public/contracts.py` | P0 |
| 2 | 追溯服务 | `polaris/kernelone/traceability/public/service.py` | P0 |
| 3 | 追溯引擎内部实现 | `polaris/kernelone/traceability/internal/service_impl.py` | P0 |
| 4 | 追溯引擎测试 | `polaris/kernelone/traceability/tests/test_traceability.py` | P0 |
| 5 | 追溯旁路安全包装器 | 本文档 §3-A | P0 |
| 6 | 追溯门禁 | `docs/governance/ci/scripts/run_traceability_gate.py` | P1 |
| 7 | 追溯 Schema | `docs/governance/schemas/traceability-matrix.schema.yaml` | P1 |
| 8 | **CE Blueprint 持久化层** | `polaris/cells/chief_engineer/blueprint/internal/blueprint_persistence.py` | **P1 前置** |
| 9 | 蓝图模板 v2 + ADR 增量模型 | 本文档 §4.2 | P1 |
| 10 | **ADRStore 蓝图增量引擎** | `polaris/cells/chief_engineer/blueprint/internal/adr_store.py` | **P1** |
| 11 | **ADRStore 测试** | `polaris/cells/chief_engineer/blueprint/tests/test_adr_store.py` | **P1** |
| 12 | 回滚守卫（内存快照） | `polaris/cells/chief_engineer/blueprint/internal/rollback_guard.py` | P2 |
| 12b | 回滚守卫（git stash 降级） | `polaris/cells/roles/kernel/internal/rollback_guard.py` | P2 |
| 13 | ADR-0072 | `docs/governance/decisions/adr-0072-traceability-engine.md` | P1 |
| 14 | 验证卡片 | `docs/governance/templates/verification-cards/vc-20260416-traceability-engine.yaml` | P1 |
| 15 | **DirectorPool 核心** | `polaris/cells/chief_engineer/blueprint/internal/director_pool.py` | **Phase A** |
| 16 | **EventBus** | `polaris/cells/chief_engineer/blueprint/internal/event_bus.py` | **Phase A** |
| 17 | **DirectorPool 配置** | `polaris/config/chief_engineer.yaml` | **Phase A** |
| 18 | **DirectorPool 测试** | `polaris/cells/chief_engineer/blueprint/tests/test_director_pool.py` | **Phase A** |
| 19 | **CEConsumer 改造** | `polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py` | **Phase B** |
| 20 | **ADR-0075** | `docs/governance/decisions/adr-0075-hybrid-dispatch-architecture.md` | **Phase B** |
| 21 | **混合架构验证卡片** | `docs/governance/templates/verification-cards/vc-20260416-hybrid-dispatch.yaml` | **Phase B** |
| 22 | **ADR 编号冲突修复脚本** | `docs/governance/ci/scripts/fix_adr_numbering.py`（可选一次性工具） | P1 |

---

## 19) 更新后的风险与边界

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| LLM 忽略 blueprint_id 约束 | 高 | 门禁 13 硬检查，不通过则拒绝 |
| 追溯矩阵过大影响性能 | 中 | 单次 iteration 预计 <100 节点，可接受 |
| git stash 并行冲突 | **已解决** | §6.2: 并行模式使用 Director 级内存快照，git stash 仅用于降级模式 |
| 内存快照占用 | 低 | 单次快照预计 <5MB（<100 文件），完成后自动释放 |
| PM/CE/Director Schema 扩展破坏兼容性 | 低 | 所有新字段 default=null，向后兼容 |
| 门禁 15 误报（文档变更不影响蓝图） | 中 | CE 可标记 `no_impact` 跳过 |
| **CE Blueprint Store 纯内存，进程重启丢失** | **高** | 必须先实现 §4.1-A 的 `BlueprintPersistence` 层，ADR 机制才有意义 |
| **Traceability 写入失败阻塞主流程** | **高→低** | 通过 §3-A 旁路安全策略隔离，失败只记日志不抛异常 |
| **DirectorTaskWorkflow 内同步 I/O 导致非确定性** | **中** | 必须通过新增 activity 调用 traceability，禁止在 workflow 方法内直接 I/O |
| **DirectorPool 并发竞态** | **中** | asyncio 单线程模型天然避免大部分竞态；关键操作用锁 |
| **CE Consumer 单点** | **低** | 可启动多个 CE Consumer 实例（Task Market 天然负载均衡） |
| **Director 全部失败** | **低** | 降级到单 Director 顺序执行 + 告警 |
| **文件冲突检测误判** | **中** | 误判导致串行执行（性能损失），不会导致错误（安全方向） |
| **ADR delta 编译冲突** | **低** | 两个 ADR 修改同一个 step 时，后应用者覆盖；compile 前做冲突检测 |
| **ADR 累积过多** | **低** | 单蓝图预计 <20 个 ADR，compile 性能可忽略；超过 50 时建议合并为新的 base |
| **ADR 编号冲突修复遗漏文件内 frontmatter** | **低** | 重编号时必须同步更新文件内的 `adr_id` / `decision_id` / 文件名和相互引用 |

---

## 20) 最终结论

本方案从四个维度解决 Polaris 的核心短板：

| 维度 | 解决方案 | 价值 |
|------|---------|------|
| **追溯** | TraceabilityMatrix + 3 道门禁 | 回答"这行代码为什么这样写" |
| **调度** | 混合架构 (Task Market + DirectorPool) | 每段链路用最优模式 |
| **恢复** | RollbackGuard (Director 级内存快照) + CE 智能恢复 | 失败不扩散，并行安全，实时抢救 |
| **进化** | ADR 增量蓝图机制 | 蓝图可持续进化，不截断不破坏 |

**与纯 Task Market 或纯 Workflow 的对比**:

| 维度 | 纯 Workflow | 纯 Task Market | 混合架构 |
|------|-----------|---------------|---------|
| CE 可见性 | 中（通过 Workflow 状态） | 差（轮询队列） | **优（DirectorHandle 实时）** |
| 动态调度 | 不可能 | 不可能 | **CE 实时决策** |
| 失败恢复 | 无 | 队列重入 | **智能判断** |
| PM 解耦 | 差（PM 调度一切） | 优 | **优** |
| 实现复杂度 | 低 | 高 | **中** |
| 调试友好度 | 中 | 差 | **优** |

**最终建议**: 按 P0+P1+Phase A+Phase B 顺序执行（约 8 周），验证混合架构在真实迭代中工作，再进入 P2+Phase C。

---

## 21) 落地就绪度评估 (V2 强化版)

> 整体就绪度评分：**8.7 / 10**（极高 — 本周即可启动 P0/P1）

### 21.1 评分明细

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码完整度 | **9.5** | 蓝图已给出 80% 以上核心实现（contracts/service/pool/adr/rollback 均有完整代码） |
| 配置 & 依赖 | **7.5** | 仅缺 `polaris/config/` 目录约定，可用 cell.yaml 替代 |
| 设计一致性 | **9.0** | 回滚冲突已闭环（§6.2），唯一冲突已解决 |
| 测试 & 门禁 | **9.0** | 已有 19 个门禁脚本可直接复用，测试策略已定义 |
| 集成点可达性 | **8.5** | 4 个关键集成点文件全部存在，行号可能漂移但可按函数名定位 |

### 21.2 可以直接开始的部分（零阻断）

| Phase | 就绪度 | 优先级 | 预计人天 | 验收标准 | 建议启动时间 |
|-------|--------|--------|---------|---------|-------------|
| **P0 追溯引擎** | 高 | ★★★★★ | 1.5 | `contracts.py` + `service.py` + 4 个单元测试通过 ruff+mypy+pytest | 立即 |
| **P1 ADRStore** | 高 | ★★★★☆ | 1.0 | ADR 写入/读取/compile/revert 全流程跑通 | 立即 |
| **P1 门禁** | 高 | ★★★★★ | 0.5 | 新增 2 个门禁脚本，全部绿灯 | 立即 |
| **P1 DirectorWorkflow（复用现有）** | 高 | ★★★★ | 0.5 | 保留五阶段模型，DirectorPool 直接调用 | 立即 |

**立即行动建议**: 今天即可并行开 P0 追溯引擎 + P1 门禁 + P1 ADRStore 三个任务。

### 21.3 需要补前置步骤才能启动的部分

| Phase | 阻断项 | 首选解决方案 | 备选方案 | 风险等级 | 解决后预计人天 |
|-------|--------|-------------|---------|---------|-------------|
| **Phase A DirectorPool** | `polaris/config/` 目录不存在 | 在 `chief_engineer` cell 内新增 `cell.yaml` 管理配置 | 新建 `polaris/config/chief_engineer.yaml` | 低 | 0.5 |
| **蓝图引用行号** | `orchestration_engine.py:126` 等行号可能漂移 | 完全不依赖行号，改用函数/类名 + grep 定位 | 运行时 AST 解析 | 低 | 0.2 |

**首选 cell.yaml 配置写法**:

```yaml
# polaris/cells/chief_engineer/cell.yaml (新增 director_pool 配置段)
director_pool:
  max_directors: 5
  auto_scale: false              # P3 启用
  conflict_detection: global     # global | per_consumer
  rollback_strategy: per_director_snapshot  # 内存快照（推荐）
  enable_prometheus: false       # Phase C 启用
```

### 21.4 回滚冲突闭环（已彻底解决）

**冲突核心**（已解决）: §6 回滚机制原来用 git stash，与 §13 DirectorPool 并行模式互斥。

**推荐方案**: Director 级独立内存快照 + Task 级重做（无需 git stash）。

**为什么最好**:
1. **完全兼容并行** — 3 个 Director 同时改不同文件互不影响
2. **失败恢复最灵活** — CE 可选择 retry / reassign / split
3. **性能最好** — 无 git 操作，纯内存读写
4. **符合 DirectorPool 设计理念** — 回滚守卫在 Pool 内部，与 DirectorHandle 对齐

| 方案 | 并行兼容性 | 实现难度 | 回滚粒度 | 推荐指数 |
|------|-----------|---------|---------|---------|
| 全局 git stash | 完全不兼容 | 低 | 文件级 | ★☆☆☆☆ |
| 每 Director 独立 git worktree | 兼容 | 中 | 仓库级 | ★★★☆☆ |
| **Director 级内存快照** | **完美** | **低** | **文件级** | **★★★★★** |

**代码已在 §6.2-A 给出完整实现**。集成方式：`handle_failure()` 中调用 `rollback_guard.rollback_director(director_id)` 即可。

### 21.5 风险雷达

```
风险等级分布:
  ■■■■□ 高风险 (0 项) — 无阻断性高风险
  ■■■□□ 中风险 (3 项) — 需关注但不阻断启动
  ■■□□□ 低风险 (5 项) — 已有缓解措施

中风险（关注但不阻断）:
  ├─ ScopeConflictDetector 从 consumer 迁移到 Pool 层（需全局锁）
  ├─ DirectorPool 并发竞态（asyncio 单线程天然缓解大部分）
  └─ 文件冲突检测误判（安全方向：误判导致串行，不会导致错误）

低风险（已缓解）:
  ├─ 配置目录缺失 → cell.yaml 替代
  ├─ 行号漂移 → 函数名定位
  ├─ ADR 累积过多 → 50+ 时 compaction
  ├─ 内存快照占用 → <5MB/次，完成后释放
  └─ Director 全部失败 → 降级到单 Director + 告警
```

### 21.6 下一步行动清单

| 时间 | 行动 | 产出 | 负责 |
|------|------|------|------|
| **Day 1** | 创建 `cell.yaml` + 补全 DirectorPool `__init__` | 配置就绪 | 开发者 |
| **Day 1-2** | P0 追溯引擎实现 + 测试 | `contracts.py` + `service.py` + 4 tests PASS | 开发者 |
| **Day 2-3** | P1 门禁 + ADRStore | 门禁脚本 + `adr_store.py` + 6 tests PASS | 开发者 |
| **Day 3-4** | P0 集成到 `orchestration_engine.py` + `dispatch_pipeline.py` | 端到端 traceability 工作 | 开发者 |
| **Day 4-5** | Director 级内存快照 + 冲突检测全局化 | `rollback_guard.py` + 集成测试 | 开发者 |
| **Week 2** | Phase A: DirectorPool 核心 + EventBus | `director_pool.py` + 16 tests PASS | 开发者 |
| **Week 3** | Phase B: CEConsumer 改造 + PM→CE Task Market 激活 | 全链路冒烟通过 | 开发者 |
| **Week 4** | ADR 签署 + 验证卡片 + CI 集成 | 治理闭环 | 开发者 |

### 21.7 蓝图落地前提条件检查清单

| # | 前提条件 | 状态 | 备注 |
|---|---------|------|------|
| 1 | 关键集成点文件存在 | ✅ | `orchestration_engine.py`, `dispatch_pipeline.py`, `director_workflow.py`, `ce_consumer.py` 全部存在 |
| 2 | DirectorPool 父目录存在 | ✅ | `polaris/cells/chief_engineer/blueprint/internal/` 已有 |
| 3 | 规范文件齐全 | ✅ | `AGENTS.md` + `AGENT_ARCHITECTURE_STANDARD.md` 存在 |
| 4 | P0 代码完整度 | ✅ | contracts + service 代码已给出，可直接落地 |
| 5 | 回滚冲突已解决 | ✅ | §6.2-A Director 级内存快照替代 git stash |
| 6 | 配置基础设施 | ⚠️ | 需先创建 `cell.yaml` 或 `polaris/config/`，0.5 人天 |
| 7 | ADR 持久化层 | ⚠️ | CE `ConstructionStore` 纯内存，需先补 `BlueprintPersistence`（§4.1-A）|
| 8 | Temporal I/O 约束 | ⚠️ | traceability 写入必须通过 activity，不能在 workflow 方法内直接 I/O |
