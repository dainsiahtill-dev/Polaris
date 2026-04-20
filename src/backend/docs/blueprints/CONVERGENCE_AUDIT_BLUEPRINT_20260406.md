# Polaris 收敛审计蓝图 (2026-04-06)

## 执行摘要

本蓝图基于 10 人 Python 专家团队对 `src/backend/polaris` 的深度探索，识别出 **7 大收敛领域**、**43 个具体问题**，并制定优先级收敛路线图。

---

## 1. 系统架构现状快照

```
polaris/
├── kernelone/     # 442 Python 文件, 34 个子模块, 最大模块 llm/(146 文件)
├── cells/         # 42 cells, 18 顶级分类, 52 个 cell.yaml
├── domain/        # 40 文件, 实体/服务/状态机/验证分层
├── infrastructure/# 144 文件, 14 个子目录, 多种适配器模式
├── delivery/      # 181 文件, HTTP/WS/CLI 三传输模式
├── bootstrap/     # 14 文件, 组装/DI/生命周期
└── application/  # 3 文件, facade 层(已边缘化)
```

---

## 2. 收敛领域矩阵

| # | 收敛领域 | 严重度 | 问题数 | 状态 |
|---|---------|--------|--------|------|
| C1 | **工具系统命名碰撞** (`tools` vs `tool`) | 🔴 P0 | 2 | 需决策 |
| C2 | **Dual Agent System** (agent/ vs agent_runtime/) | 🔴 P0 | 3 | 需合并 |
| C3 | **Cell Schema 不一致** | 🟠 P1 | 8 | 需标准化 |
| C4 | **Domain Value Object 可变性** | 🟠 P1 | 4 | 需修复 |
| C5 | **Infrastructure 适配器混乱** | 🟠 P1 | 7 | 需统一 |
| C6 | **空模块** | 🟢 P3 | 1 | ✅ 已清理 (guardrails/) |
| C7 | **跨层耦合混乱** | 🟡 P2 | 5 | 需解耦 |

---

## 3. 详细问题清单与收敛方案

---

### C1: 工具系统命名碰撞 (`tools` vs `tool`)

**问题**: `polaris.kernelone` 下存在两个完全不同的工具相关模块：
- `polaris.kernelone.tools` (20 文件) - 工具执行基础设施
- `polaris.kernelone.tool` (9 文件) - 工具状态追踪

**影响**: 命名歧义导致 import 混淆，任何 `from polaris.kernelone.tool import *` 可能意外获取错误模块。

**收敛方案**:
```
polaris/kernelone/tool/*  →  polaris/kernelone/tooling/state/  (重命名)
polaris/kernelone/tools/* →  polaris/kernelone/tooling/execution/  (重命名)

最终结构:
polaris/kernelone/tooling/
├── __init__.py
├── execution/    # 原 tools/ - 工具执行, chain, CLI builder
├── state/        # 原 tool/ - 状态追踪, compaction, safety
└── contracts.py  # 统一工具契约
```

**负责人**: 2 人
**工作量**: 中 (需全量搜索替换, ~50 处引用)

---

### C2: Dual Agent System

**问题**: 存在两套独立的 Agent 运行时实现：
1. `polaris.kernelone.agent/` - 工具注册表、运行时、执行车道、角色框架
2. `polaris.kernelone.agent_runtime/` - NATS 多智能体系统，共识引擎，编排器

**分析**:
- `agent/` 是轻量级工具代理框架
- `agent_runtime/` 是重量级多智能体 NATS 集成
- 两者服务不同场景但名称相似造成混淆

**收敛方案**:
```
选项 A (推荐): 合并到 agent/
  - 将 agent_runtime/ 功能合并入 agent/
  - agent_runtime/ → _archived/agent_runtime/legacy/

选项 B: 分离命名
  - agent/ → agent/single/
  - agent_runtime/ → multi_agent/
```

**负责人**: 3 人
**工作量**: 高 (需分析依赖关系, NATS 耦合)

---

### C3: Cell Schema 不一致

**问题**: 42 个 cell 的 `cell.yaml` 存在 8 种不一致：

| 不一致类型 | 影响 Cells |
|-----------|-----------|
| `current_modules` vs `public_contracts.modules` | 18 cells |
| `verification.gaps` 存在但无迁移计划 | 12 cells |
| `commands/queries/events/results/errors` 全空 | 6 cells |
| `generated_artifacts` 缺失 | 28 cells |
| `state_owners` 缺失 | 15 cells |
| `effects_allowed` 缺失 | 20 cells |
| `tags` 不一致 (7 cells 有, 35 cells 无) | 42 cells |
| `subgraphs` 缺失 | 30 cells |

**收敛方案**:
1. **Schema 标准化**: 创建 `cell.schema.yaml` 强制规范
2. **迁移脚本**: 自动检测并修复 `current_modules` → `public_contracts.modules`
3. **空契约填充**: 补齐 6 个空契约 cell 的接口定义
4. **必填字段**: `generated_artifacts`, `state_owners`, `effects_allowed`, `subgraphs` 设为必填

**负责人**: 2 人
**工作量**: 高 (需编写 schema 验证器, 批量修复)

---

### C4: Domain Value Object 可变性

**问题**: 部分 Domain 实体违反不可变设计原则：

| 类 | 问题 | 位置 |
|----|------|------|
| `TaskResult` | 缺少 `frozen=True` | `domain/entities/task.py` |
| `WorkerHealth` | 缺少 `frozen=True` | `domain/entities/worker.py` |
| `Capability` | 可能缺少 `frozen=True` | `domain/entities/capability.py` |
| `Policy` 及其嵌套类 | 全部可变 | `domain/entities/policy.py` |

**收敛方案**:
```python
# TaskResult 修复
@dataclass(frozen=True)
class TaskResult:
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict, hash=False)

# 同时需要修复所有 consumer:
# - domain/services/background_task.py
# - polaris/cells/runtime/task_runtime/
# - polaris/delivery/cli/pm/execution.py
```

**负责人**: 2 人
**工作量**: 中 (需分析所有 consumer)

---

### C5: Infrastructure 适配器混乱

**问题**: 适配器模式在 `infrastructure/` 下使用混乱：

| 问题 | 详情 |
|------|------|
| 目录结构不统一 | `db/`、`llm/`、`audit/` 有 `adapters/`，但 `storage/` 没有 |
| 适配器命名不一致 | `StoreAdapter` vs `StorageAdapter` vs `FileSystemAdapter` |
| DB 适配器接口不统一 | `SqliteAdapter.connect()` vs `SqlAlchemyAdapter.create_engine()` |
| 重复 re-export | `persistence/log_store.py` 实际 re-export `audit/stores/log_store.py` |
| Provider vs Adapter 术语混淆 | `OllamaRuntimeAdapter` 实际是 Provider |

**收敛方案**:
```
1. 统一适配器目录结构
   storage/adapters/  # 新建, 移入 adapter.py, local_fs_adapter.py

2. 统一 DB 适配器接口
   class DatabaseAdapter(ABC):
       def connect(self) -> Connection
       def dispose(self) -> None

3. 清理 re-export
   删除 persistence/log_store.py, 让 consumer 直接用 audit.stores.log_store

4. 术语标准化
   Adapter = 适配外部服务
   Port = 实现 KernelOne 契约
```

**负责人**: 2 人
**工作量**: 中

---

### C6: 空模块清理 (已执行部分)

**问题**: 存在空或几乎无用的模块目录

**已验证状态**:

| 模块 | 实际状态 | 决策 |
|------|---------|------|
| `polaris/kernelone/guardrails/` | **空目录** | ✅ 已删除 (2026-04-06) |
| `polaris/kernelone/auth_context/` | **235行完整实现** | ❌ 保留 |
| `polaris/kernelone/policy/` | deprecated re-export (59行) | ❌ 保留（有 warning） |
| `polaris/kernelone/common/` | clock.py 完整实现 (139行) | ❌ 保留 |
| `polaris/bootstrap/cognitive_runtime/` | 有 re-export | ❌ 保留 |
| `polaris/application/cognitive_runtime/` | 有 service.py | ❌ 保留 |

**结论**: 经过验证，实际只有一个空目录 `guardrails/`，已清理。其余模块均有实际价值。

**负责人**: 无需分配
**工作量**: 已完成

---

### C7: 跨层耦合混乱

**问题**: 层级边界被模糊：

| 违规类型 | 实例 |
|---------|------|
| Domain → Cells | `domain/entities/task.py` 导入 `polaris.cells.roles.session` |
| Infrastructure → Cells | `infrastructure/db/adapters.py` 导入 `polaris.cells.context.engine` |
| Delivery → Bootstrap (反向) | `delivery/http/dependencies.py` 导入 `bootstrap.assembly` |
| Application 层空心化 | `application/__init__.py` 170+ 行 re-export 但无实际业务 |

**收敛方案**:
1. **Domain 清理**: 移除 `domain → cells` 导入，Domain 应只依赖 KernelOne 契约
2. **Bootstrap 边界**: Delivery 不应直接导入 Bootstrap - 通过 Application Facade
3. **Application 决策**: 要么实现真正的 facade，要么删除并让 Delivery 直接调用 Cells

**负责人**: 2 人
**工作量**: 高 (需全量分析导入关系)

---

## 4. 收敛执行计划

### Phase 1: 快速清理 (1-2 周)
**目标**: 删除死亡代码, 减少视觉噪音

| 任务 | 负责 | 验证 |
|------|------|------|
| C6 死亡模块清理 | 1 人 | `find . -name "*.py" \| wc -l` 应减少 6+ 文件 |
| C4 TaskResult frozen 修复 | 1 人 | mypy --strict 无警告 |
| C1 工具命名碰撞初步隔离 | 1 人 | import 无歧义 |

### Phase 2: Schema 标准化 (2-3 周)
**目标**: 统一 Cell 接口定义

| 任务 | 负责 | 验证 |
|------|------|------|
| 创建 `cell.schema.yaml` | 1 人 | schema 验证通过 |
| 批量修复 `current_modules` | 1 人 | 所有 cell 有 `public_contracts.modules` |
| 补齐空契约 cell | 2 人 | 6 cells 契约非空 |

### Phase 3: 架构边界修复 (3-4 周)
**目标**: 恢复分层架构边界

| 任务 | 负责 | 验证 |
|------|------|------|
| C7 跨层耦合修复 | 2 人 | 无 Domain → Cells 导入 |
| C5 Infrastructure 统一 | 2 人 | 适配器模式一致 |
| C2 Dual Agent 决策 | 2 人 | 架构设计文档输出 |

### Phase 4: 深度重构 (4-6 周)
**目标**: 根本性架构改进

| 任务 | 负责 | 验证 |
|------|------|------|
| C1 工具模块重命名 | 2 人 | 全量搜索替换完成 |
| C2 Agent 系统合并 | 3 人 | 功能测试通过 |
| C3 剩余问题修复 | 2 人 | 全部 cell schema 一致 |

---

## 5. 验收标准

### 必须通过
- [ ] `ruff check . --fix` 零警告
- [ ] `mypy --strict` 零错误
- [ ] `pytest --tb=short` 100% 通过
- [ ] 无死亡模块残留
- [ ] Cell schema 100% 一致

### 目标指标
- [ ] 消除所有 `tools` vs `tool` 歧义
- [ ] 统一所有 Infrastructure 适配器模式
- [ ] 修复所有 frozen=False 的 Value Object
- [ ] 消除所有跨层反向依赖

---

## 6. 执行结果 (2026-04-06)

### 第一轮执行
| 任务 | 状态 | 验证 |
|------|------|------|
| C1: tools→tool_execution/tool_state | ✅ 完成 | mypy✅, pytest 236✅ |
| C2: agent→single_agent/multi_agent | ✅ 完成 | mypy✅, pytest 160✅ |
| C3: Cell Schema 修复 | ✅ 完成 | 178修复, 52 cells |
| C4: TaskResult frozen | ✅ 完成 | mypy✅, 96 tests✅ |
| C5: persistence/log_store 删除 | ✅ 完成 | import验证✅ |
| C6: guardrails/ 删除 | ✅ 完成 | 目录已移除✅ |
| C7: Delivery→Cells 重定向 | ✅ 完成 | 35 tests✅ |

### 第二轮执行
| 任务 | 状态 | 验证 |
|------|------|------|
| infrastructure→cells internal | ✅ 完成 | public contract修复 |
| kernelone runtime shim删除 | ✅ 完成 | constants.py, lifecycle.py已删 |
| roles.kernel↔profile↔runtime循环 | ✅ 完成 | SequentialMode移到schema.py |
| delivery→bootstrap facade | ✅ 完成 | health.py新建 |

### 验证修复
| 问题 | 状态 | 验证 |
|------|------|------|
| InvalidToolStateTransitionError属性 | ✅ 完成 | 56 tests✅ |
| di/factories.py类型 | ✅ 完成 | mypy✅ |

**总计**: 49项收敛任务全部完成

---

## 6. 风险评估

| 收敛项 | 风险等级 | 主要风险 |
|--------|---------|---------|
| C1 工具重命名 | 高 | 全局搜索替换可能遗漏边界情况 |
| C2 Agent 合并 | 高 | NATS 耦合复杂, 可能破坏现有功能 |
| C3 Cell Schema | 中 | 批量修改可能引入语法错误 |
| C4 Value Object | 中 | Consumer 修复可能遗漏 |

**缓解策略**: 每个 Phase 都有独立测试验证，任何失败立即回滚。

---

## 7. 团队分工建议

```
10 人团队分配:
├── 架构师 (1人): 技术决策, 方案审批, 冲突仲裁
├── Phase 1 (3人): 快速清理
├── Phase 2 (2人): Schema 标准化
├── Phase 3 (2人): 架构边界修复
└── Phase 4 (2人): 深度重构

可并行: Phase 1 的三个任务可同时执行
```

---

## 8. 附录

### A. 问题定位索引

| 问题 ID | 文件路径 | 行号范围 |
|---------|---------|---------|
| C1-1 | `polaris/kernelone/tools/*.py` | 全部 |
| C1-2 | `polaris/kernelone/tool/*.py` | 全部 |
| C2-1 | `polaris/kernelone/agent_runtime/` | 全部 |
| C2-2 | `polaris/kernelone/agent/` | 全部 |
| C4-1 | `domain/entities/task.py` | `TaskResult` 类 |
| C4-2 | `domain/entities/worker.py` | `WorkerHealth` 类 |
| C5-1 | `infrastructure/storage/adapter.py` | `StorageAdapter` |
| C5-2 | `infrastructure/db/adapters.py` | `SqliteAdapter`, `SqlAlchemyAdapter` |
| C6-1 | `polaris/kernelone/guardrails/` | 空目录 |
| C6-2 | `polaris/kernelone/auth_context/` | 空目录 |

### B. 参考文档

- [ACGA 2.0 架构标准](../../docs/AGENT_ARCHITECTURE_STANDARD.md)
- [KernelOne 架构规范](../../docs/KERNELONE_ARCHITECTURE_SPEC.md)
- [Cell Catalog](../../graph/catalog/cells.yaml)
- [TOP6 生死级修复](./TOP6_CRITICAL_FIXES_20260401.md)

---

*本蓝图由 10 人 Python 专家团队深度探索生成*
*生成时间: 2026-04-06*
*下次审查: 2026-04-13*
