# Polaris v2.0 Traceability Blueprint — 执行计划

> 文档 ID: PLAN-20260416-TRACEABILITY-EXEC
> 日期: 2026-04-16
> 状态: COMPLETED — 2026-04-16
> 负责人: Principal Architect (Chief Engineer)
> 关联蓝图: `docs/blueprints/POLARIS_V2_TRACEABILITY_BLUEPRINT_20260416.md`

---

## 1. 执行总览

本计划将宏大的 Traceability + Hybrid Dispatch 蓝图拆分为 **6 个独立的 Engineering Packages**，按依赖关系分三波次落地：

| 波次 | Package | 核心目标 | 工期 | 前置依赖 |
|------|---------|---------|------|---------|
| Wave 1 | **Pkg-A: Traceability Kernel** | 建立 `kernelone/traceability/` 核心契约与服务 | Week 1 | 无 |
| Wave 1 | **Pkg-B: Safety & Bypass** | 旁路安全包装器、持久化策略、降级规则 | Week 1 | Pkg-A |
| Wave 2 | **Pkg-C: Pipeline Integration** | 将 traceability 注入 PM/CE/Director/QA 调用链 | Week 2 | Pkg-A, Pkg-B |
| Wave 2 | **Pkg-D: CE Persistence & ADR** | `BlueprintPersistence` + `ADRStore` + CE 集成 | Week 2-3 | 无（内部前置：先 D1 再 D2） |
| Wave 3 | **Pkg-E: Governance Gates** | 3 道新增门禁、Schema、验证卡片、ADR 文档 | Week 3-4 | Pkg-C, Pkg-D |
| Wave 3 | **Pkg-F: DirectorPool & Hybrid** | DirectorPool、EventBus、混合调度激活 | Week 4-6 | Pkg-C, Pkg-D |

---

## 2. 团队编排（10x 工程师分配）

| 工程师 | 代号 | 主责 Package | 技术专精 |
|--------|------|-------------|---------|
| Alice | `platform_eng` | Pkg-A | KernelOne 模块设计、dataclass 契约、原子持久化 |
| Bob | `safety_eng` | Pkg-B | 防御性编程、旁路隔离、异常安全 |
| Charlie | `integration_eng` | Pkg-C | 跨模块集成、Workflow Activity、异步边界 |
| Diana | `storage_eng` | Pkg-D1 | 文件存储、JSON Schema、向后兼容 |
| Evan | `ce_eng` | Pkg-D2 | ADR 编译器、delta apply、CE Agent 改造 |
| Fiona | `governance_eng` | Pkg-E | CI 脚本、YAML Schema、验证卡片 |
| George | `orchestration_eng` | Pkg-F1 | DirectorPool 核心、调度算法、竞态处理 |
| Hannah | `event_infra_eng` | Pkg-F2 | EventBus、Redis Stream 预备、可观测性埋点 |
| Ian | `test_eng` | Pkg-TEST | pytest 策略、集成测试、Chaos 测试框架 |
| Julia | `qa_lead` | Pkg-REVIEW | 代码审查、红线检查、mypy/ruff 门禁 |

---

## 3. 系统架构图（文本描述）

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Polaris Engine                        │
│  (orchestration_engine.py — Facade, 只负责初始化 trace_service)     │
└────────────────────┬────────────────────────────────────────────────┘
                     │ inject
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Pkg-A: kernelone/traceability/                                     │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────────┐   │
│  │  contracts  │──▶│   service   │──▶│  internal/service_impl  │   │
│  │  (port)     │   │  (port)     │   │      (impl)             │   │
│  └─────────────┘   └─────────────┘   └─────────────────────────┘   │
│                                               │                      │
│                                               ▼                      │
│                                    runtime/traceability/             │
│                                    {run_id}.{iteration}.matrix.json  │
└─────────────────────────────────────────────────────────────────────┘
                     ▲
                     │ register_node / link (via Pkg-B safety wrapper)
┌────────────────────┴────────────────────────────────────────────────┐
│  Pkg-C: Pipeline Integration Points                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ pm_planning  │  │  dispatch    │  │ director_task_workflow   │  │
│  │  pipeline    │  │  pipeline    │  │ (activity-based trace)   │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Pkg-D: CE Persistence & ADR Engine                                 │
│  ┌──────────────────┐    ┌──────────────────┐                       │
│  │ BlueprintPersistence│──▶│   ADRStore       │                       │
│  │   (disk layer)    │    │ (delta compiler) │                       │
│  └──────────────────┘    └──────────────────┘                       │
│           ▲                        │                                │
│           └────────── ConstructionStore (dual-layer: mem + disk)    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Pkg-F: DirectorPool & Hybrid Dispatch (Phase A/B)                  │
│  ┌─────────────┐   ┌─────────────────┐   ┌─────────────────────┐   │
│  │ DirectorPool│──▶│ ScopeConflict   │──▶│   EventBus (sync)   │   │
│  │             │   │   Detector      │   │                     │   │
│  └─────────────┘   └─────────────────┘   └─────────────────────┘   │
│         │                                                            │
│         ▼                                                            │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  DirectorTaskWorkflow (保留现有 5-phase Temporal workflow)  │     │
│  └────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. 模块职责划分

### Pkg-A: Traceability Kernel (`kernelone/traceability/`)
- **Alice**
- 职责: 实现 `TraceNode`, `TraceLink`, `TraceabilityMatrix` 不可变契约；实现 `TraceabilityServiceImpl`（构建、持久化、查询）；提供 `public/service.py` 中的工厂函数。
- 禁止: 依赖任何 `cells/` 或 `delivery/` 模块；禁止在写入时抛异常到调用方。

### Pkg-B: Safety & Bypass
- **Bob**
- 职责: 设计 `_safe_trace_register` / `_safe_trace_link` / `_safe_trace_persist` 包装器；定义旁路失败隔离策略；编写安全集成规范文档片段。
- 禁止: 任何可能阻塞主流程的同步 I/O；禁止裸 `except:`。

### Pkg-C: Pipeline Integration
- **Charlie**
- 职责: 在 `orchestration_engine.py:126-929` 间注入 traceability 初始化与归档；在 `pm_planning/pipeline.py:688` 后注册 doc/task 节点；在 `dispatch_pipeline.py:971` 注册 blueprint；在 `director_task_workflow.py` 新增 `traceability_activity` 用于 commit 注册；在 `qa_workflow.py` 注册 verdict。
- 禁止: 在 Temporal workflow 方法内直接执行 traceability I/O。

### Pkg-D1: Blueprint Persistence
- **Diana**
- 职责: 实现 `blueprint_persistence.py`（原子 JSON 写入、目录管理、版本检测）；改造 `ConstructionStore` 为内存缓存 + 磁盘双写；确保向后兼容（无持久化目录时 graceful degrade）。

### Pkg-D2: ADR Engine
- **Evan**
- 职责: 实现 `adr_store.py`（`BlueprintBase`, `BlueprintADR`, `ADRStore`）；实现 delta apply 逻辑（`add_step`, `modify_step`, `remove_step`, `add_file`, `remove_file`, `change_scope`, `change_risk`）；集成到 `ce_consumer.py` 和 `chief_engineer_agent.py`。

### Pkg-E: Governance Gates
- **Fiona**
- 职责: 实现 `run_traceability_gate.py`（3 道门禁）；编写 `traceability-matrix.schema.yaml`；撰写 `adr-0072-traceability-engine.md` 和 `vc-20260416-traceability-engine.yaml`；修复 ADR 编号冲突（0067/0068 重编号）。

### Pkg-F1: DirectorPool Core
- **George**
- 职责: 实现 `director_pool.py`（含 `DirectorStatus`, `DirectorPoolStatus`, `ScopeConflictDetector`, `RecoveryDecision`, 任务分配算法）；集成到 `ce_consumer.py`；废弃 `DirectorExecutionConsumer`。

### Pkg-F2: EventBus Infrastructure
- **Hannah**
- 职责: 实现 `event_bus.py`（sync 本地模式，预留 Redis Stream 接口）；定义事件 schema；在 DirectorPool 关键生命周期埋点；配置 `polaris/config/chief_engineer.yaml`。

### Pkg-TEST: Test Strategy
- **Ian**
- 职责: 为每个 Package 编写单元测试和集成测试；设计 Chaos 测试（随机 kill Director、并发分配竞态）；确保 mypy/ruff/pytest 全绿。

### Pkg-REVIEW: Quality Gate
- **Julia**
- 职责: 在每个 Package 合并前执行代码审查；检查是否违反 KernelOne 反向依赖规则、是否使用裸 `except:`、类型注解是否完整。

---

## 5. 核心数据流

### 5.1 单次 PM Iteration 的 Traceability 数据流

```
1. orchestration_engine.run_once()
   └─▶ trace_service = create_traceability_service(workspace)

2. pm_planning.pipeline (quality gate passed)
   └─▶ doc_node = register_node(kind="doc", external_id=run_id)
       for each task in normalized["tasks"]:
           task_node = register_node(kind="task", external_id=task.id)
           link(doc_node, task_node, "derives_from")

3. shangshuling_registry.archive_task_history()
   └─▶ 冗余校验: 为每个 task 确认 doc→task link 存在

4. dispatch_pipeline.run_chief_engineer_preflight()
   └─▶ bp_node = register_node(kind="blueprint", external_id=blueprint_id)
       link(task_node, bp_node, "implements")

5. director_task_workflow (via activity)
   └─▶ commit_node = register_node(kind="commit", external_id=file_path_hash)
       link(bp_node, commit_node, "implements")

6. qa_workflow
   └─▶ verdict_node = register_node(kind="qa_verdict", external_id=review_id)
       link(commit_node, verdict_node, "verifies")

7. orchestration_engine.finalize_iteration()
   └─▶ matrix = build_matrix(run_id, iteration)
       persist(matrix, f"runtime/traceability/{run_id}.{iteration}.matrix.json")
       trace_service.reset()
```

### 5.2 CE→DirectorPool 数据流（混合调度 Phase A/B）

```
CEConsumer 认领 PENDING_DESIGN 任务
   └─▶ chief_engineer_agent 生成 blueprint
       └─▶ ADRStore.create_blueprint() / propose_adr() / compile()
           └─▶ director_pool.assign_task(task, compiled_blueprint)
               ├─▶ ScopeConflictDetector.acquire(director_id, task_files)
               ├─▶ DirectorTaskWorkflow.run(task) [5 phases]
               └─▶ EventBus.publish("director.assigned", ...)
           
DirectorPool 监控循环
   └─▶ get_live_dashboard() → 供 CE 实时决策

CE 汇总所有 Director 结果
   └─▶ 跨任务一致性检查
       └─▶ 生成 QA Contract → Task Market (pending_qa)
```

---

## 6. 接口契约定义（Package 间边界）

### 6.1 `kernelone/traceability/public/service.py`

```python
from polaris.kernelone.traceability.public.contracts import (
    TraceNode, TraceLink, TraceabilityMatrix
)

class TraceabilityService:
    """Traceability 公开端口。实现类位于 internal/service_impl.py。"""

    def register_node(...) -> TraceNode: ...
    def link(...) -> TraceLink: ...
    def build_matrix(run_id: str, iteration: int) -> TraceabilityMatrix: ...
    def persist(matrix: TraceabilityMatrix, path: str) -> None: ...
    def reset(self) -> None: ...

def create_traceability_service(workspace: str) -> TraceabilityService:
    """工厂函数，返回具体实现实例。"""
```

### 6.2 `chief_engineer/blueprint/public/service.py` 扩展

新增 `get_adr_store(workspace: str) -> ADRStore` 工厂函数，供 `ce_consumer.py` 调用。

### 6.3 `director_pool.py` 公开接口

```python
class DirectorPool:
    async def assign_task(self, task: Any, blueprint: Any) -> str: ...
    def get_live_dashboard(self) -> DirectorPoolStatus: ...
    def handle_failure(self, task_id: str, error: Exception) -> RecoveryDecision: ...
    async def reassign(self, task_id: str, target_director_id: str) -> None: ...
    def mark_completed(self, task_id: str, success: bool) -> None: ...
```

---

## 7. 技术选型理由

| 决策 | 选型 | 理由 |
|------|------|------|
| Traceability 持久化 | JSON 原子写入 (`tmp` + `replace`) | 单次 iteration 节点数 < 100，JSON 足够轻量；无需引入 SQLite/PostgreSQL 增加复杂度 |
| Traceability 位置 | `kernelone/traceability/` | 平台无关技术能力，符合 AGENTS.md 对 kernelone 的归属定义 |
| Blueprint 持久化 | 单文件单 JSON (`runtime/blueprints/{id}.json`) | 避免单文件膨胀；原子写保证不损坏；与现有 `runtime/` 目录约定一致 |
| ADR 编译 | 内存深拷贝 + 顺序 apply delta | 无需引入复杂 diff 库；delta 类型有限且结构化，Python dict 操作足够 |
| DirectorPool 通信 | 本地 sync EventBus | 当前无分布式需求，sync 实现零外部依赖；预留 Redis Stream 接口供 P3 升级 |
| Workflow trace 集成 | 新增 Temporal Activity | 遵守 Temporal Workflow 确定性规则，避免在 workflow 方法内直接 I/O |

---

## 8. Sprint 0 — 第一周冲刺计划（Wave 1）

### Day 1-2: Pkg-A (Alice)
- [ ] 新建 `polaris/kernelone/traceability/__init__.py`
- [ ] 实现 `public/contracts.py` (`TraceNode`, `TraceLink`, `TraceabilityMatrix`)
- [ ] 实现 `public/service.py` (port + factory)
- [ ] 实现 `internal/service_impl.py` (`TraceabilityServiceImpl`)
- [ ] 单元测试: `tests/test_traceability.py` (register, link, build, persist, query)
- [ ] mypy + ruff 通过

### Day 2-3: Pkg-B (Bob)
- [ ] 在 `pkg-c` 的集成规范中定义安全包装器模板
- [ ] 编写失败隔离测试（模拟 disk full / permission error）
- [ ] 输出 Pkg-B 安全规范文档（内嵌到本计划 §3-A 的执行细则）

### Day 3-5: Pkg-C (Charlie) — 等待 Pkg-A/B 完成后
- [ ] `orchestration_engine.py` 注入点（初始化 + finalize 归档）
- [ ] `pm_planning/pipeline.py` doc/task 注册
- [ ] `shangshuling_registry.py` 冗余校验
- [ ] `dispatch_pipeline.py` blueprint 注册
- [ ] `director_task_workflow.py` 新增 traceability activity
- [ ] `qa_workflow.py` verdict 注册
- [ ] 端到端测试: 跑一次完整 PM iteration，检查 `runtime/traceability/*.matrix.json` 生成

---

## 9. 执行命令

### Alice (Pkg-A) 启动命令
```bash
# 创建模块骨架
mkdir -p polaris/kernelone/traceability/public
mkdir -p polaris/kernelone/traceability/internal
mkdir -p polaris/kernelone/traceability/tests
touch polaris/kernelone/traceability/__init__.py
```

### Julia (Pkg-REVIEW) 的红线检查清单
```bash
ruff check polaris/kernelone/traceability/ --fix
ruff format polaris/kernelone/traceability/
mypy polaris/kernelone/traceability/ --strict
pytest polaris/kernelone/traceability/tests/ -v
```

---

## 10. 结论

本执行计划将蓝图转化为 **6 个可独立开发、测试、审查的 Engineering Packages**，遵循：
- **先基础设施（Pkg-A/B）**
- **再集成与持久化（Pkg-C/D）**
- **最后治理与调度（Pkg-E/F）**

立即开始 **Sprint 0，Day 1: Pkg-A**。
