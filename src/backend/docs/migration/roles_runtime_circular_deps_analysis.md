# roles.runtime 循环依赖分析报告

> 分析时间: 2026-03-23
> 分析范围: `polaris/cells/roles/` 目录（含所有子 Cell）与其余 Cell 的导入关系
> 分析工具: `grep -rn` 全量导入扫描

---

## 1. 循环依赖拓扑总览

### 拓扑概览

```
                          [Ring 1] roles.runtime (HUB) ↔ 7 Business Cells
                                    ┌──────────────────────────────────────┐
                                    │  roles.runtime (内部: role_agent_    │
                                    │  service.py) 导入 7 个业务 Cell:     │
  architect.design  ───────────────►│  • pm_planning                       │
  chief_engineer    ───────────────►│  • director.execution                 │
  context.catalog   ───────────────►│  • architect.design                  │
  director.execution ───────────────►│  • chief_engineer.blueprint          │
  finops.budget_guard ─────────────►│  • finops.budget_guard               │
  llm.control_plane  ─────────────►│  • llm.control_plane                 │
  qa.audit_verdict   ─────────────►│  • qa.audit_verdict                  │
                                    └──────────────────────────────────────┘
                                    ▲           ▲           ▲           ▲
                                    │           │           │           │
                          各自通过 roles.runtime.public.service 获取:
                          StandaloneRoleAgent / registry / create_protocol_fsm
                          / create_worker_pool 等

                          [Ring 2] roles.adapters ↔ orchestration.workflow_runtime
                                    BaseRoleAdapter(RoleOrchestrationAdapter)
                                    configure_orchestration_role_adapter_factory()
```

### Ring 1: roles.runtime (God Cell Hub) ↔ 7 Business Cells

**本质**: `roles.runtime` 是事实上的"角色代理注册中心"，每个业务 Cell 都依赖它，同时它也导入每个业务 Cell——典型的 God Cell 反模式。

#### 正向导入（roles.runtime → 业务 Cell）

文件: `polaris/cells/roles/runtime/internal/role_agent_service.py` 行 27-33，**顶层 import**（硬耦合）:

```python
# roles.runtime 硬编码依赖 7 个业务 Cell
from polaris.cells.orchestration.pm_planning.public.service import PMAgent           # 环 A
from polaris.cells.director.execution.public.service import DirectorAgent           # 环 B
from polaris.cells.architect.design.public.service import ArchitectAgent           # 环 C
from polaris.cells.chief_engineer.blueprint.public.service import ChiefEngineerAgent  # 环 D
from polaris.cells.finops.budget_guard.public.service import CFOAgent             # 环 E
from polaris.cells.llm.control_plane.public.service import HRAgent                # 环 F
from polaris.cells.qa.audit_verdict.public.service import QAAgent                 # 环 G
```

文件: `polaris/cells/roles/runtime/internal/process_service.py` 行 11:
```python
from polaris.cells.runtime.state_owner.public.service import ProcessHandle  # 非循环，仅单向
```

#### 逆向导入（业务 Cell → roles.runtime）

| 业务 Cell | 文件 | 行 | 导入内容 | 性质 |
|-----------|------|-----|----------|------|
| director.execution | director_agent.py | 22 | `AgentMessage, MessageType, RoleAgent, WorkerPool, WorkerTask` | 顶层 import |
| director.execution | director_cli.py | 41, 76 | `DirectorStandaloneAgent, run_tui` | 顶层 + lazy |
| pm_planning | pm_agent.py | 17, 223 | `StandaloneRoleAgent, create_protocol_fsm` | 顶层 + lazy |
| architect.design | architect_agent.py | 16 | `StandaloneRoleAgent, ...` | 顶层 import |
| architect.design | architect_cli.py | 21, 38 | `ArchitectStandaloneAgent, run_tui` | 顶层 + lazy |
| chief_engineer | chief_engineer_agent.py | 10 | `StandaloneRoleAgent, ...` | 顶层 import |
| chief_engineer | chief_engineer_cli.py | 21, 38 | `ChiefEngineerStandaloneAgent, run_tui` | 顶层 + lazy |
| finops | budget_agent.py | 22 | `StandaloneRoleAgent, ...` | 顶层 import |
| llm.control_plane | llm_config_agent.py | 11 | `StandaloneRoleAgent, ...` | 顶层 import |
| llm.dialogue | role_dialogue.py | 902-915, 1124-1125 | `RoleExecutionKernel, RoleTurnRequest, RoleExecutionMode, registry, load_core_roles` | lazy import |
| qa | qa_agent.py | 19 | `StandaloneRoleAgent, ...` | 顶层 import |
| context.catalog | evolution_engine.py | 44 | `StandaloneRoleAgent` | lazy import |

#### 7 个独立环的共享类型

| 环 | 业务 Cell | roles.runtime 共享类型 | 导入行 |
|----|-----------|----------------------|--------|
| A | pm_planning | `PMAgent` ↔ `StandaloneRoleAgent` | role_agent_service.py:27 ↔ pm_agent.py:17 |
| B | director.execution | `DirectorAgent` ↔ `AgentMessage, RoleAgent, WorkerPool` | role_agent_service.py:28 ↔ director_agent.py:22 |
| C | architect.design | `ArchitectAgent` ↔ `ArchitectStandaloneAgent` | role_agent_service.py:29 ↔ architect_agent.py:16 |
| D | chief_engineer | `ChiefEngineerAgent` ↔ `ChiefEngineerStandaloneAgent` | role_agent_service.py:30 ↔ chief_engineer_agent.py:10 |
| E | finops.budget_guard | `CFOAgent` ↔ `StandaloneRoleAgent` | role_agent_service.py:31 ↔ budget_agent.py:22 |
| F | llm.control_plane | `HRAgent` ↔ `HRAgent(StandaloneRoleAgent)` | role_agent_service.py:32 ↔ llm_config_agent.py:11 |
| G | qa.audit_verdict | `QAAgent` ↔ `StandaloneRoleAgent` | role_agent_service.py:33 ↔ qa_agent.py:19 |

---

### Ring 2: roles.adapters ↔ orchestration.workflow_runtime (双向循环)

#### roles.adapters → workflow_runtime

文件: `polaris/cells/roles/adapters/internal/base.py` 行 17:
```python
from polaris.cells.orchestration.workflow_runtime.public.service import RoleOrchestrationAdapter
# BaseRoleAdapter extends RoleOrchestrationAdapter (行 25)
```

文件: `polaris/cells/roles/adapters/internal/__init__.py` 行 10-11:
```python
from polaris.cells.orchestration.workflow_runtime.public.service import RoleOrchestrationAdapter
from polaris.cells.orchestration.workflow_runtime.public.service import (
    configure_orchestration_role_adapter_factory,
)
```

文件: `polaris/cells/roles/adapters/internal/base.py` 行 408 (lazy):
```python
from polaris.cells.orchestration.workflow_runtime.public.service import sanitize_step_detail
```

#### workflow_runtime → roles.adapters

文件: `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/generic_pipeline_workflow.py` 行 292:
```python
from polaris.cells.roles.adapters.public.service import create_role_adapter
```

文件: `polaris/cells/orchestration/pm_dispatch/internal/orchestration_command_service.py` 行 37:
```python
from polaris.cells.roles.adapters.public.service import register_all_adapters
```

**共享类型**: `RoleOrchestrationAdapter` 接口（roles.adapters 实现，workflow_runtime 定义）

---

### Ring 3: roles.adapters 额外跨 Cell 依赖（非循环，但加重耦合）

| 源文件 | 导入 Cell | 导入内容 | 性质 |
|--------|---------|---------|------|
| base.py:18 | runtime.task_runtime | `TaskRuntimeService` | 顶层 |
| base.py:22 | llm.dialogue | `generate_role_response` | 顶层 |
| director_adapter.py:20 | llm.dialogue | `generate_role_response, RoleOutputParser` | 顶层 |
| director_adapter.py:59 | director.execution | `DirectorService` (lazy) | lazy |
| director_adapter.py:340,458 | roles.runtime | `registry` (lazy) | lazy |
| director_execution_backend.py:270,311,345 | factory.pipeline | `FactoryPipelineService` (lazy) | lazy |
| pm_adapter.py:16 | orchestration.pm_planning | `PMPlanningService` | 顶层 |
| workflow_adapter.py:21,234 | roles.kernel | `RoleExecutionKernel, RoleToolGateway` | 顶层 |
| workflow_node.py:26,203 | roles.kernel | `RoleExecutionKernel, RoleToolGateway` | 顶层 |

---

## 2. 拆解方案

### 方案 A：插件式注册（注册中心反转）— 推荐

**思路**: 废除 `roles.runtime` 对业务 Cell 的顶层 import，改为业务 Cell 通过回调/注册函数主动注册到 `roles.runtime` 的注册表。

**实现**:

1. 在 `polaris/domain/agents/` 创建角色代理注册中心接口:
```python
# polaris/domain/agents/role_agent_registry.py
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.agent_runtime_base import RoleAgent

class IRoleAgentRegistry(ABC):
    @abstractmethod
    def register(self, name: str, agent_cls: type[RoleAgent], lifecycle_mode: str) -> None: ...
    @abstractmethod
    def get(self, name: str) -> type[RoleAgent] | None: ...
    @abstractmethod
    def list_all(self) -> dict[str, tuple[type[RoleAgent], str]]: ...
```

2. `roles.runtime` 提供注册入口（**不导入具体 Agent 类**）:
```python
# polaris/cells/roles/runtime/internal/role_agent_service.py
# 删除顶层 import: PMAgent, DirectorAgent, ArchitectAgent, ...
# 改为从 bootstrap 注入的注册中心获取
class AgentService:
    def __init__(self, registry: IRoleAgentRegistry):
        self._registry = registry

    @property
    def AGENT_CLASSES(self) -> dict[str, type[RoleAgent]]:
        # 从注册中心获取，不再硬编码
        return {name: cls for name, (cls, _) in self._registry.list_all().items()}
```

3. 业务 Cell 启动时注册（通过 bootstrap 或 entry point）:
```python
# polaris/cells/orchestration/pm_planning/internal/pm_agent.py
# 或 bootstrap/agent_bootstrap.py
from polaris.domain.agents.role_agent_registry import get_global_registry
from polaris.cells.orchestration.pm_planning.public.service import PMAgent

get_global_registry().register("PM", PMAgent, lifecycle_mode="continuous")
```

4. `standalone_runner.py` 中的 `ArchitectStandaloneAgent`, `ChiefEngineerStandaloneAgent`, `DirectorStandaloneAgent` 保持为 `roles.runtime` 内部类（它们是 RoleAgent 的 standalone 装饰器变体，与具体业务 Cell 解耦）。

**优点**:
- 完全消除循环依赖
- 业务 Cell 可以独立演进，不需修改 `roles.runtime`
- 支持动态加载/卸载角色
- 符合 ACGA 2.0 Cell 公开契约原则

**缺点**:
- 需要 bootstrap 层配合
- 现有 7 个 AGENT_CLASSES 映射需要迁移
- 启动顺序需保证注册先于使用

**适用场景**: 长期架构目标，推荐作为最终态

---

### 方案 B：共享 Domain 实体（接口下沉）

**思路**: 将 `RoleAgent` 基类、`AgentMessage`、`WorkerPool` 等核心类型下沉到 `polaris/domain/` 层，作为跨 Cell 共享的事实来源。

**实现**:

1. 将 `agent_runtime_base.py` 中的核心类型移入 `polaris/domain/agents/`:
```
polaris/domain/agents/
    __init__.py
    role_agent.py       # RoleAgent, AgentMessage, AgentStatus, AgentState, MessageType
    worker_pool.py      # WorkerPool, WorkerConfig, WorkerTask, WorkerResult
    protocol.py         # ProtocolFSM, ProtocolBus, ProtocolType
```

2. `roles.runtime` 只从 `polaris.domain.agents` 导入，不从业务 Cell 导入

3. 业务 Cell 通过 `polaris/domain/agents/role_agent.py` 的 `RoleAgent` 接口实现自己的 Agent，standalone 包装器也在 domain 层

**优点**:
- 共享类型单一真相
- 类型一致性最好
- 符合 ACGA 2.0 domain 层沉淀原则

**缺点**:
- 需要拆分现有 `agent_runtime_base.py`
- `RoleAgent` 基类包含较多业务语义，直接下沉 domain 可能有越界风险
- 7 个业务 Cell 仍各自实现自己的 Agent 子类，registry 问题未解决

**适用场景**: 与方案 A 配合使用，作为类型共享层

---

### 方案 C：Lazy Import 全面覆盖（短期缓解）

**思路**: 在 `roles.runtime` 中将所有业务 Cell 的 import 从顶层改为 lazy import（类似已有的 `get_role_system_prompt` 模式）。

**实现**:

将 `role_agent_service.py` 中的:
```python
# 顶层 import (现状)
from polaris.cells.orchestration.pm_planning.public.service import PMAgent
from polaris.cells.director.execution.public.service import DirectorAgent
# ...
AGENT_CLASSES = {"PM": PMAgent, "Director": DirectorAgent, ...}
```

改为:
```python
# 移除顶层 import，改为 lazy property
@property
def AGENT_CLASSES(self) -> dict[str, type[RoleAgent]]:
    from polaris.cells.orchestration.pm_planning.public.service import PMAgent
    from polaris.cells.director.execution.public.service import DirectorAgent
    # ...
    return {"PM": PMAgent, "Director": DirectorAgent, ...}
```

**优点**:
- 无需重构
- 打破 Python 运行时循环
- 已有成功先例（`get_role_system_prompt` 模式）

**缺点**:
- 仍是单向依赖反转，未解决架构层 God Cell 问题
- 每个业务 Cell 仍需导入 `roles.runtime`（无法独立测试）
- 只是掩盖问题，不解决根本架构缺陷

**适用场景**: 作为迁移过渡期方案，快速止血

---

## 3. 环 2 专项拆解方案

**roles.adapters ↔ orchestration.workflow_runtime 循环**:

**方案**: 将 `RoleOrchestrationAdapter` 接口下沉到 `polaris/domain/contracts/`:

```
polaris/domain/contracts/
    __init__.py
    orchestration.py    # IRoleOrchestrationAdapter, RoleEntrySpec
```

- `orchestration.workflow_runtime` 定义接口在 `polaris/domain/contracts/orchestration.py`
- `roles.adapters` 实现接口，从 `polaris.domain.contracts` 导入，不从 `orchestration.workflow_runtime` 导入
- `orchestration.workflow_runtime` 不再导入 `roles.adapters`（通过 `polaris/domain/contracts` 解耦）

---

## 4. 推荐方案

### 短期（1 周内）: 方案 C（Lazy Import 全面覆盖）

立即在 `role_agent_service.py` 中将 7 个业务 Cell 的顶层 import 改为 lazy property，消除 Python 运行时循环。

**关键文件**: `polaris/cells/roles/runtime/internal/role_agent_service.py`

### 中期（2-3 周）: 方案 A + 方案 B 结合

1. 创建 `polaris/domain/agents/` 层（方案 B）
2. 实现 `IRoleAgentRegistry` 接口和全局注册中心（方案 A）
3. 业务 Cell 通过 bootstrap 回调注册
4. 消除 `roles.adapters ↔ workflow_runtime` 循环（接口下沉到 domain）

### 长期: 评估 roles Cell 拆分可行性

当前 `roles/` 下的 6 个子 Cell（runtime/kernel/adapters/session/engine/profile）实际上是 6 个独立的 Cell，应考虑：
- 拆分为独立 Cell（各自有 `cell.yaml`）
- 或明确 `roles/` 为组合 Cell，内部严格遵守无循环依赖

---

## 5. 风险评估

### 高风险（阻断）
- **拆解过程中**，`AGENT_CLASSES` 映射丢失导致所有角色启动失败
- `standalone_runner.py` 的 `StandaloneRoleAgent` 继承链断裂

### 中风险
- `delivery/http/v2/director.py` 和 `delivery/http/v2/pm.py` 依赖 `roles.kernel.public` 和 `roles.adapters.public`，注册中心变化可能影响 HTTP 路由
- `orchestration.pm_dispatch` 的 `register_all_adapters()` 需要在 workflow_runtime 初始化后调用

### 低风险
- `llm.dialogue/role_dialogue.py` 使用 lazy import，当前已部分缓解

### 影响范围矩阵

| 改动范围 | 涉及 Cell | 回归测试重点 |
|---------|---------|-----------|
| role_agent_service.py lazy 化 | 7 个业务 Cell + roles.runtime | 启动所有角色 Agent |
| Domain 层提取 | roles.* + 所有业务 Cell | 类型一致性检查 |
| workflow_runtime 接口下沉 | roles.adapters + workflow_runtime | Pipeline workflow 执行 |
| Bootstrap 注册注入 | bootstrap + 所有业务 Cell | 启动顺序验证 |

---

## 6. 工时估算

| 阶段 | 方案 | 工时 |
|------|------|------|
| 分析 | 本报告 | 1h |
| 短期 | 方案 C: Lazy Import | 2-3h |
| 中期 | 方案 A + B | 3-5 人天 |
| 测试 | 全回归验证 | 1-2 人天 |
| **总计** | | **4-6 人天** |

---

## 7. 附录：roles 子 Cell 内聚性评估

`roles/` 当前包含 6 个子模块，其中 **runtime** 是 God Cell 根，**adapters** 是跨 Cell 耦合集中点：

| 子模块 | 行数（估算） | 主要职责 | 内聚性 |
|-------|------------|---------|--------|
| runtime | ~2000 | Agent 生命周期、WorkerPool、Protocol FSM | 低（导入 7 个业务 Cell） |
| kernel | ~1500 | LLM 调用、工具网关、上下文、提示构建 | 高 |
| adapters | ~1500 | 角色编排适配器 | 中（跨 Cell 依赖多） |
| session | ~800 | 会话管理、数据存储 | 高 |
| engine | ~600 | ReAct/PlanSolve/ToT/Hybrid 引擎 | 高 |
| profile | ~400 | 角色配置、工具策略 | 高 |

**结论**: `roles.runtime` 和 `roles.adapters` 是架构问题集中点，应优先处理。

---

## 8. 下一步行动

- [ ] **P0**: 在 `role_agent_service.py` 中实施 Lazy Import（方案 C），消除运行时循环
- [ ] **P1**: 创建 `polaris/domain/agents/role_agent_registry.py` 接口
- [ ] **P1**: 评估 `RoleOrchestrationAdapter` 下沉到 `polaris/domain/contracts/` 的可行性
- [ ] **P2**: 业务 Cell bootstrap 注册实现
- [ ] **P2**: 更新 `cells.yaml` 中的 `roles.runtime` 的 `depends_on` 声明
