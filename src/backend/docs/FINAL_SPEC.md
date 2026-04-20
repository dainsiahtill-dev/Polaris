# Polaris 最终架构规范 v1.2

- 状态: Final
- 生效日期: 2026-03-20
- 适用范围: `` 下的 Polaris 后端代码与后续 Agent-Native 子系统
- 强制级别: MUST
- 文档目标: 给出 Polaris 后端的目标架构、迁移边界、治理门禁与首批落地样板，使系统在复杂协作场景下保持可执行、可审计、可迁移、可回滚，并能被 AI/Agent 正确理解

## 0. 文档定位

本文档是 Polaris 后端的目标架构规范。它不是纯概念文档，也不是一次性重构提案，而是一份同时约束以下内容的执行性规范：

- 系统的能力边界与组织真相
- 规范根目录的职责与依赖方向
- `KernelOne`、`Cell`、`Infrastructure` 三者之间的关系
- 状态所有权与副作用边界
- 存储、归档、历史审计等高副作用子域的标准落地方式
- AI/Agent 的默认阅读、修改与验证入口
- 迁移顺序、旧目录退役策略与 CI 门禁

### 0.1 与其他资产的关系

Polaris 当前处于迁移期，因此必须同时区分“当前事实”和“目标架构”：

1. `AGENTS.md` 是执行规则的最高优先级约束。
2. [`docs/AGENT_ARCHITECTURE_STANDARD.md`](./AGENT_ARCHITECTURE_STANDARD.md) 是 Agent 执行入口标准，约束默认阅读顺序、复用策略与门禁执行路径。
3. [`docs/graph/catalog/cells.yaml`](./graph/catalog/cells.yaml) 与 [`docs/graph/subgraphs/*.yaml`](./graph/subgraphs/) 描述当前仓库的图谱事实，是“现在系统长什么样”的主要依据。
4. 本文档定义“系统必须收敛到什么样子”，并约束迁移行为、边界判定与治理门禁。
5. [`docs/ARCHITECTURE_SPEC.md`](./ARCHITECTURE_SPEC.md) 与 [`docs/KERNELONE_ARCHITECTURE_SPEC.md`](./KERNELONE_ARCHITECTURE_SPEC.md) 作为支撑性规范存在；若与本文档的最终裁决冲突，以本文档为准，但不得违反 `AGENTS.md`。

### 0.2 术语约定

为避免把未来状态写成既成事实，本文统一使用以下术语：

- `当前`：已在仓库中存在，并可在图谱资产或代码中定位到的事实。
- `目标`：本文要求系统最终收敛到的状态。
- `迁移中`：当前已有等价或近似实现，但尚未按目标边界定型。
- `完成`：同时满足代码实现、图谱资产、契约、测试与治理门禁。

## 1. 设计目标

本规范的首要目标不是“目录更整齐”，而是同时解决以下问题：

1. 降低 AI/Agent 对局部能力的发现成本。
2. 降低人类开发者对模块边界的理解成本。
3. 降低跨模块修改时的影响分析与回归成本。
4. 提高长期演进中的边界稳定性与架构抗腐化能力。
5. 让状态、副作用、迁移路径与运行时证据具备可观测性与可审计性。
6. 在复杂协作场景下，统一 `fs/db/ws/stream/runtime` 等技术能力的抽象方式。
7. 让 `runtime/history/archive/storage-layout` 等高风险子域可以被稳定建模、验证和回滚。

## 2. 非目标

本规范不追求以下事情：

- 不追求把每个能力都拆成独立进程或微服务。
- 不追求所有模块都使用统一重型样板。
- 不追求把所有同步调用都改成事件总线。
- 不追求一次性全仓推倒重构。
- 不追求把所有代码都塞进 `KernelOne`。
- 不追求让 `KernelOne` 替代 `application`、`domain` 或 `Cell` 的职责边界。

本规范明确允许：

- 逻辑上按节点解耦，物理上保持同进程部署。
- 局部重复优先于错误抽象。
- 已经到了迁移完成阶段，不再需要兼容垫片。
- 对热路径保持直连和同进程优先。
- 将可被 Agent/AI 复用的运行时与基础设施能力尽量下沉到 `KernelOne`，前提是不携带 Polaris 业务语义。

## 3. 当前仓库事实与迁移基线

截至 2026-03-20，后端已经完成从 reset seed 到业务能力收口阶段的图谱扩展，但距离目标架构仍有明显差距。

### 3.1 已存在的图谱事实

当前仓库状态，`docs/graph/catalog/cells.yaml` 处于 `phase1_public_phase2_composite_phase3_business_cells_declared` 阶段。

当前 graph catalog 已声明的第一批公共 Cell 包括：

- `context.catalog`
- `delivery.api_gateway`
- `policy.workspace_guard`
- `runtime.state_owner`
- `runtime.projection`
- `audit.evidence`
- `archive.run_archive`
- `archive.task_snapshot_archive`
- `archive.factory_archive`
- `context.engine`

当前 `docs/graph/subgraphs/` 中已恢复并纳入当前事实的子图资产包括：

- `storage_archive_pipeline`

这意味着 Polaris 已从 `reset_seed` 阶段进入“第一批 public Cell + phase-2 组合能力 + phase-3 业务能力簇并存”的治理基线，但仍处于“图谱先行、实现持续收口”的迁移中阶段。仓库中可能存在更多候选 Cell 或 subgraph 草稿资产；除非它们已经进入 catalog、通过最小治理校验并在本文同步为当前事实，否则仍应视为目标架构而非当前现状。

### 3.2 尚未收口的热点区域

大量目标 Cell 仍未完成“声明即实现”的一致性收敛，旧根目录中的实现也尚未被稳定能力边界完全接管。

这意味着本文档中提出的能力边界虽已进入 graph 声明层，但其中不少仍处于“已声明、未完全收口”的迁移中状态，不应被误写为“已全部完成”。

### 3.3 当前状态与目标状态的裁决

因此，本文档做出如下强制裁决：

1. 当前代码修改必须尊重现有 `cells.yaml` 的 owned paths 与现状边界。
2. 目标架构的新增 Cell、子图、目录骨架与状态矩阵，必须被视为迁移目标，而不是已完成事实。
3. 任何文档、代码或测试都不得把未落地的目标状态伪装成当前仓库事实。
4. 宣称“迁移完成”必须同时满足：代码归位、图谱更新、契约稳定、门禁通过、兼容层可删除。

## 4. 总体模型

Polaris 的目标后端架构统一采用：

`Graph + Cells + KernelOne + Infrastructure + Context Packs + Governance`

### 4.1 六大平面

| 平面 | 职责 | 关键产物 |
| --- | --- | --- |
| Graph Plane | 系统结构真相层，描述能力单元、依赖关系、状态归属、子图与入口出口 | `cells.yaml`、`subgraphs/*.yaml` |
| Capability Plane | 真正承载能力的实现层；最小稳定边界是 `Cell` | `cell.yaml`、`public/`、`internal/` |
| Flow Plane | 描述跨 Cell 的协作路径，但不拥有核心业务不变量 | workflow、subgraph、应用编排 |
| Context Plane | 为 AI/Agent 提供最小上下文包、影响分析与验证路径 | `context.pack.json`、`impact.pack.json`、`verify.pack.json` |
| Effect Plane | 收口所有外部副作用，并提供审计、超时、权限与追踪约束 | effect port、KernelOne contract、adapter |
| Governance Plane | 让边界、契约、上下文和副作用可以被机器自动校验 | schema、fitness rules、CI gates |

### 4.2 一句话总述

- Graph Plane 决定系统如何被理解。
- Cells 决定能力如何被切分和授权修改。
- KernelOne 决定技术运行时与 AI/Agent 基础设施如何被统一提供。
- Infrastructure 决定具体后端如何接入。
- Context Plane 决定 AI/Agent 如何低成本获得正确信息。
- Governance Plane 决定这套边界能否长期成立。

## 5. 一等公民

本文档中的一等公民只有以下对象：

- `Cell`：一个可独立理解、独立测试、独立替换、独立授权修改的稳定能力边界。
- `Port`：Cell 的公开交互边界，所有跨 Cell 协作都必须经过 Port。
- `Contract`：Port 交换的数据结构与语义，包括命令、查询、事件、结果、错误与流片段。
- `Relation`：Graph 中的边，用于描述依赖、调用、发布、订阅、状态拥有与治理关系。
- `Flow`：一个跨 Cell 的业务或平台协作路径。
- `Context Pack`：面向 AI/Agent 的最小上下文包。
- `Fitness Rule`：可自动执行的架构门禁规则。
- `KernelOne Technical Subsystem`：纯技术、可独立测试、不带 Polaris 业务语义的运行时子系统。

## 6. 核心原则

### 6.1 Graph First

系统的第一组织真相是能力图谱，不是目录树。

### 6.2 Cell First

最小自治单位是 Cell，而不是 controller、service、helpers 或 utils。

### 6.3 Public/Internal Fence

每个 Cell 必须严格区分公开面与内部实现。跨 Cell 依赖只能落在公开边界上。

### 6.4 Contract First

跨 Cell 协作必须通过契约完成，不允许直接耦合对方内部类型。

### 6.5 Context First for AI

AI/Agent 的默认入口必须是 `cells.yaml`、`subgraph.yaml`、`cell.yaml`、`README.agent.md` 和 `context.pack.json`，而不是先全仓扫描。

### 6.6 Single State Owner

每个 source-of-truth 状态只能有一个 Cell 拥有写权限。

### 6.7 Explicit Effects

所有副作用必须显式声明，禁止未声明的文件写入、数据库写入、网络调用、子进程拉起或外部工具调用。

### 6.8 Governance by Automation

边界不能靠口头约定，必须通过机器可执行的门禁持续验证。

### 6.9 KernelOne as Agent/AI Operating Substrate

`KernelOne` 不是普通工具层，而是 Polaris 面向 AI/Agent 的类 Linux 操作系统底座。它承载统一技术运行时能力、平台无关契约与 Agent/AI 基础设施，但绝不承载 Polaris 业务策略。

### 6.10 Cells on KernelOne

`Cell` 决定能力与状态边界；`KernelOne` 提供统一技术底座；`Infrastructure` 提供具体实现。这三者是正交关系，不是替代关系。

### 6.11 ACGA 2.0（Graph-Constrained Semantic）增强原则

在不放松 v1.0 边界约束的前提下，Polaris 采用 ACGA 2.0 增强原则（见 [`docs/ACGA_2.0_PRINCIPLES.md`](./ACGA_2.0_PRINCIPLES.md)）：

1. Graph 仍是唯一架构真相，向量索引仅是发现层。
2. Embedding 必须基于结构化 Descriptor，而非源码原文。
3. 检索路径必须遵循 `Intent -> Graph Filter -> Semantic Rank -> Neighbor Expansion`。
4. 语义索引构建与刷新属于 Effect，必须被审计与门禁约束。

## 7. 规范根目录

Polaris 后端的目标结构收敛到以下六个规范根目录：

```text
bootstrap/        组装根、启动、环境绑定
delivery/         HTTP / WebSocket / CLI 传输适配层
application/      用例编排、事务边界、应用流程
domain/           业务规则、实体、值对象、领域端口
kernelone/        Agent/AI 类 Linux 运行时底座、AI/Agent 基础设施、六边形技术子系统
infrastructure/   存储、消息、遥测、插件等具体适配器
```

其他后端顶层目录一律视为旧根目录。迁移完成前可以存在兼容垫片，但新功能不得继续进入旧根目录。

### 7.1 根目录职责矩阵

| 根目录 | 角色 | 允许依赖 | 禁止依赖 |
| --- | --- | --- | --- |
| `bootstrap/` | 组装根与进程启动 | 所有规范根目录 | 旧根目录 |
| `delivery/` | HTTP、WebSocket、CLI 传输层 | `application/`；极窄的 `domain/` 或 `kernelone/` 公共 API | `infrastructure/` 具体适配器、旧根目录 |
| `application/` | 用例与编排层 | `domain/`、`kernelone/` 公共 API、`application/` 内部包 | `delivery/`、具体基础设施细节 |
| `domain/` | 业务规则与领域模型 | `domain/` 内部包、`domain/ports/`、极少数获准技术契约 | `delivery/`、`application/`、具体基础设施细节 |
| `kernelone/` | Agent/AI 运行时底座与纯技术能力 | `kernelone/` 内部 | `delivery/`、`application/`、`domain/`、旧根目录 |
| `infrastructure/` | 出站适配器实现 | `kernelone/` 端口/契约、显式声明的应用或领域端口、稳定领域模型/值对象 | `delivery/`、`application/` 用例/工作流、`domain/` 策略/服务 |

### 7.2 默认执行流向

```text
bootstrap
   -> delivery
      -> application
         -> domain
         -> kernelone
bootstrap
   -> infrastructure
      -> kernelone/application/domain ports
```

关键规则：

1. `bootstrap/` 是唯一允许组装对象图的根目录。
2. 默认请求路径是 `delivery -> application -> domain/kernelone`。
3. `delivery/` 必须保持薄，只关注传输语义。
4. `application/` 拥有编排、事务边界、重试与执行顺序控制。
5. `domain/` 拥有业务规则与领域语义。
6. `kernelone/` 绝不能再次膨胀为新的 `core/` 垃圾场。
7. `infrastructure/` 只负责边界处的出站适配与映射。

### 7.3 归属判定顺序

当某段代码看起来可以放进多个目录时，按以下顺序裁决：

1. 处理 HTTP、WebSocket、CLI 或传输错误语义的，归属 `delivery/`。
2. 编排一个用户动作或系统动作的，归属 `application/`。
3. 表达业务不变量、业务概念或领域规则的，归属 `domain/`。
4. 能作为 Agent/AI 通用运行时 / 基础设施 / OS 能力复用、且不带 Polaris 业务语义的，应优先评估进入 `kernelone/`。
5. 绑定具体后端、SDK、数据库、队列、文件系统、插件宿主或遥测后端的，归属 `infrastructure/`。
6. 负责进程、生命周期和依赖装配的，归属 `bootstrap/`。

## 8. Cell 架构规范

### 8.1 Cell 粒度

一个 Cell 应承载一个稳定能力或一个稳定职责边界，例如：

- `director.execution`
- `policy.workspace_guard`
- `audit.evidence`
- `context.engine`
- `archive.run_archive`

以下对象不得直接成为 Cell：

- `common`
- `helpers`
- `misc`
- `utils`
- 无明确输入输出的“工具拼盘”

### 8.2 Cell 类型

| `kind` | 作用 |
| --- | --- |
| `capability` | 真正能力实现 |
| `workflow` | 流程编排 |
| `policy` | 权限、校验、限流、规则判断 |
| `projection` | 只读聚合与查询视图 |
| `integration` | 外部系统接入 |
| `compatibility` | 遗留封装与反腐层 |

### 8.3 标准目录

```text
cells/
  <domain>/
    <name>/
      cell.yaml
      README.agent.md
      public/
        api.py
        contracts/
          commands.py
          queries.py
          events.py
          results.py
          errors.py
      internal/
        application/
        domain/
        ports/
          out.py
        adapters/
      tests/
        test_contracts.py
        test_behavior.py
        test_invariants.py
      generated/
        context.pack.json
        impact.pack.json
        verify.pack.json
```

简单 Cell 可以收缩，但至少应包含：

- `cell.yaml`
- `README.agent.md`
- `public/`
- `tests/`
- `generated/context.pack.json`

### 8.4 内部结构

Cell 内部默认采用“小六边形”结构：

```text
public input -> internal application -> internal domain/policy -> output ports -> adapters
```

这意味着：

- 对外只能暴露公开契约。
- 对内实现允许自由演化。
- 所有输出副作用必须先经过输出端口。

## 9. Port 与 Contract 规范

### 9.1 Port 类型

| `mode` | 语义 |
| --- | --- |
| `command` | 引发状态变化 |
| `query` | 只读请求 |
| `event` | 发布已发生事实 |
| `stream` | 持续输出 |
| `effect` | 外部副作用调用 |

### 9.2 Port 规则

1. `command` 必须可审计。
2. `query` 不得产生副作用。
3. `event` 只能表达事实，不能伪装命令。
4. `effect` 必须出现在 `effects_allowed` 中。
5. Port 的参数与返回值必须使用公开 Contract。

### 9.3 Contract 分类

公开 Contract 固定分为：

- `Command`
- `Query`
- `Event`
- `Result`
- `Error`
- `StreamChunk`

### 9.4 Envelope

跨 Cell 通信应统一消息信封：

```python
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class Envelope(Generic[T]):
    message_id: str
    trace_id: str
    run_id: str
    source_cell: str
    target_cell: str
    contract_name: str
    schema_version: int
    payload: T
```

### 9.5 版本规则

- 契约必须显式版本化。
- breaking change 必须升级 major version。
- 非兼容变更不得静默覆盖。
- 跨 Cell 不得暴露内部领域对象。

## 10. KernelOne 规范

### 10.1 定义

`KernelOne` 是 Polaris 面向 AI/Agent 的类 Linux 操作系统底座，而不只是狭义“技术内核”。它提供：

- 平台无关技术契约
- 技术运行时能力
- AI/Agent 基础设施
- Agent/AI 可调用的 OS 风格基础设施服务
- 六边形技术子系统
- 与 Polaris 业务语义无关的技术编排

### 10.2 准入规则

只有同时满足以下条件，一个能力才允许进入 `kernelone/`：

1. 不包含 Polaris 业务词汇或具体用例语义。
2. 抽离成独立技术包后仍然有意义。
3. 能被多个上层场景复用，或本身就是基础运行时子系统。
4. 可以在不导入 `delivery/`、`application/`、`domain/` 或旧根目录的情况下独立测试。
5. 暴露的是稳定技术契约，而不是为了让上层直接拿到某个具体后端实现。

只要任一条件不满足，它就不属于 `kernelone/`。

### 10.3 允许拥有的技术子系统

`KernelOne` 可以拥有以下 Agent/AI 通用 OS 子系统：

- `runtime`
- `fs`
- `storage`
- `db`
- `ws`
- `stream`
- `events`
- `eventbus`
- `message_bus`
- `trace`
- `locks`
- `scheduler`
- `auth_context`
- `agent_runtime`
- `tool_runtime`
- `llm`
- `process`
- `telemetry`
- `effect`
- `effect_context`
- `receipt`
- `context_runtime`
- `context_compaction`
- `task_graph`
- `technical_contracts`

`KernelOne` 必须足够强，能够承接横跨多个 Cell 的 Agent/AI 通用技术底座能力，而不是只剩占位目录。

但 `KernelOne` 的“强”只能体现在技术子系统，不体现在 Polaris 业务门面。凡是跨多个能力域重复出现、且脱离 Polaris 业务语义后仍成立的 Agent/AI OS 能力，应优先收敛到：

- `runtime`
- `effect`
- `trace`
- `stream`
- `events`
- `message_bus`
- `ws`
- `locks`
- `scheduler`
- `agent_runtime`
- `context_runtime`
- `context_compaction`
- `task_graph`
- `tool_runtime`
- `llm`
- `process`
- `technical_contracts`

### 10.4 禁止承载的内容

`KernelOne` 严禁承载：

- Polaris 业务策略
- 归档 run、终态任务快照、factory 终态等业务能力
- workspace 路径授权规则
- 业务状态拥有权
- 用例编排
- 业务分支判断
- 对 `delivery/`、`application/`、`domain/` 或旧根目录的反向导入

以下对象即使看起来“技术上可复用”，也不得进入 `kernelone/`：

- archive / finalize / migration status 等 Polaris 业务 command、query、result、event
- Polaris 业务专属的 runtime/archive/factory DTO
- workspace 授权规则与业务 permission 判定
- 仍依赖 Polaris 逻辑子树命名的业务 layout 定义

这类对象必须进入：

- `application/contracts/`
- `application/queries/`
- `cells/*/public/contracts/`
- 或稳定的 `domain/` 业务对象

### 10.5 推荐结构

```text
kernelone/
  runtime/
    contracts.py
    service.py
    errors.py
  fs/
    contracts.py
    service.py
    errors.py
  db/
    contracts.py
    service.py
    errors.py
  ws/
    contracts.py
    service.py
  stream/
    contracts.py
    service.py
  events/
    contracts.py
    service.py
  trace/
    contracts.py
    service.py
  locks/
    contracts.py
    service.py
  scheduler/
    contracts.py
    service.py
  auth_context/
    contracts.py
    service.py
  agent_runtime/
    contracts.py
    service.py
  tool_runtime/
    contracts.py
    service.py
  llm/
    contracts.py
    service.py
  process/
    contracts.py
    service.py
  telemetry/
    contracts.py
    service.py
  effect/
    context.py
    trace.py
    policy.py
    receipt.py
  contracts/
    technical/
```

业务 contract 的目标落位如下：

- `application/contracts/`
- `cells/*/public/contracts/`

不得把 Polaris 业务 command / result / event 继续塞进 `kernelone/contracts/`。

### 10.6 Cells on KernelOne

强制调用链如下：

```text
delivery
   -> application workflow
      -> capability cell
         -> effect port
            -> kernelone runtime contract
               -> infrastructure adapter
                  -> fs/db/ws/queue/stream backend
```

其含义是：

- Cell 拥有能力边界。
- KernelOne 提供技术执行能力。
- Infrastructure 绑定具体后端。
- Delivery 不得直接触达具体 adapter。
- KernelOne 不拥有业务状态。

## 11. Infrastructure 规范

### 11.1 定义

`infrastructure/` 负责：

- 出站端口的具体实现
- 持久化、消息、遥测、插件、存储集成
- 外部记录与内部模型之间的映射
- 适配器生命周期辅助逻辑

### 11.2 Port 实现规则

`infrastructure/` 可以实现定义在以下位置的端口：

- `kernelone/`
- `application/ports/`
- `domain/ports/`

### 11.3 严禁事项

`infrastructure/` 严禁：

- 承担 HTTP、CLI、WebSocket 传输行为
- 承担用例编排
- 承担领域决策逻辑
- 演化为未分类代码堆放层
- 导入应用用例或领域策略来完成业务判断

## 12. 边界对象与映射

### 12.1 对象归属矩阵

| 对象类型 | 应归属 | 禁止演化成 |
| --- | --- | --- |
| HTTP / WebSocket / CLI 请求响应结构 | `delivery/` | 领域模型或持久化模型 |
| 应用层命令 / 查询 / 结果 / 只读模型 | `application/` | 传输契约或 ORM 记录 |
| Domain entity / value object / policy / domain event | `domain/` | 原始传输 payload |
| 技术运行时契约 / 技术模型 | `kernelone/` | Polaris 业务 DTO |
| ORM 记录 / SDK DTO / 消息封装对象 | `infrastructure/` | 应用结果对象或领域实体 |

### 12.2 映射规则

- `delivery/` 负责把传输载荷映射为应用输入。
- `application/` 负责在用例输入/输出与领域概念、技术契约之间做映射。
- `domain/` 不负责 HTTP 或持久化序列化。
- `infrastructure/` 负责在适配器边界把外部格式映射为内部模型。
- 当多个 delivery 机制需要同一只读数据形状时，应先定义应用层只读模型或 projection，再映射为各自传输结构。

## 13. Graph Plane 与 Context Plane 规范

### 13.1 Graph 资产

图谱必须至少包括：

- `docs/graph/catalog/cells.yaml`
- `docs/graph/subgraphs/*.yaml`

可选扩展：

- `docs/graph/relations.yaml`
- `docs/graph/contracts.yaml`

### 13.2 Relation 类型

建议固定以下边类型：

- `depends_on`
- `calls`
- `queries`
- `emits`
- `subscribes`
- `owns_state`
- `projects_to`
- `governs`
- `wraps_legacy`

### 13.3 子图规则

每个子图必须声明：

- `entry_cells`
- `cells`
- `exit_cells`
- `critical_contracts`
- `state_boundaries`
- `verify_targets`

### 13.4 当前已存在的子图

当前仓库中已存在并可作为治理基线的子图包括：

- `storage_archive_pipeline`

`storage_archive_pipeline` 已恢复为当前 graph 资产，用于承载运行时状态、历史归档与审计证据之间的第一条正式协作路径。本文后续定义以 `docs/graph/subgraphs/storage_archive_pipeline.yaml` 为准；若仓库中存在其他子图草稿，在未同步进入 catalog 与测试前，不得视为“当前事实”。

### 13.5 AI/Agent 标准读取顺序

AI/Agent 必须遵循以下读取顺序：

1. 查询 `docs/graph/catalog/cells.yaml`。
2. 定位目标 Cell。
3. 读取相关 `docs/graph/subgraphs/*.yaml`。
4. 读取目标 `cell.yaml`。
5. 读取 `README.agent.md`。
6. 读取 `generated/context.pack.json`。
7. 读取 `public/contracts`。
8. 仅在必要时读取 `owned_paths`。
9. 若仍不足，再扩张到邻接 Cell。

默认禁止先全仓扫描。

### 13.6 Context Pack 生成原则

每个稳定 public Cell 至少应生成：

- `context.pack.json`
- `impact.pack.json`
- `verify.pack.json`

这些资产必须：

- 可自动重建
- 可缓存
- 支持增量更新
- 只包含对 AI/Agent 真正必要的信息

### 13.7 ACGA 2.0 Descriptor 资产（新增）

在 Context Pack 之外，Context Plane 应生成并维护结构化语义描述卡：

- `workspace/meta/context_catalog/descriptors.json`
- `workspace/meta/context_catalog/index_state.json`

Descriptor 至少应包含：

- `cell_id`
- `classification`
- `capability_summary`
- `public_contracts`
- `dependencies`
- `state_owners`
- `effects_allowed`
- `source_hash`
- `embedding_provider`
- `embedding_model_name`
- `embedding_device`

这些资产必须满足 `docs/governance/schemas/semantic-descriptor.schema.yaml`，并附带 `embedding_runtime_fingerprint` 以支持索引新鲜度校验。

### 13.8 Graph 约束检索规则（新增）

AI/Agent 语义检索必须先使用 Graph 过滤，再进行语义排序；禁止绕过 Graph 直接全库检索后反向解释边界。  
标准检索链路：

`Intent -> Graph Filter -> Semantic Rank(stage-1) -> Rerank(stage-2, optional) -> Neighbor Expansion -> Context Pack -> Verify Pack`

## 14. Effect Plane 规范

### 14.1 总规则

所有外部副作用必须经由：

- `Effect Cell`，或
- 显式 `effect` Port

执行。典型副作用对象包括：

- 文件系统
- 数据库
- HTTP 客户端
- LLM 网关
- 进程执行器
- 消息总线

### 14.2 Effect 规则

1. capability Cell 不得直接持有未声明的 I/O 副作用。
2. effect 路径必须支持 trace、timeout、权限、审计。
3. 所有文本读写必须显式 UTF-8。
4. effect 行为必须可验证、可观测、可重放定位。

### 14.3 KernelOne 与 Effect Plane 的关系

`KernelOne` 是 effect 的技术执行内核，不是 effect 的业务拥有者。正确关系如下：

- Cell 声明为什么要产生该 effect。
- KernelOne 提供执行 effect 所需的技术 contract、trace、context、timeout、receipt 能力。
- Infrastructure 负责把该技术 contract 绑定到具体后端。

### 14.4 Effect 声明格式

建议统一采用如下声明格式：

```text
<effect-type>:<scope>
```

示例：

- `fs.read:runtime/runs/*`
- `fs.write:workspace/history/runs/*`
- `network.http_outbound:llm/*`
- `process.spawn:sandboxed/*`

## 15. 状态与投影规范

### 15.1 单写原则

每个状态只能被一个 Cell 声明为 `state_owners`。

### 15.2 Projection 规则

其他 Cell 若需要读取该状态，应通过以下方式之一：

- Query Port
- Projection Cell
- 订阅事件后构建只读视图

### 15.3 禁止事项

以下行为一律禁止：

- 多个 Cell 并发写同一 source-of-truth 状态
- 在查询路径中偷偷写状态
- 在兼容垫片中引入新的状态写入点

### 15.4 KernelOne 的状态边界

`KernelOne` 可以拥有技术层缓存、trace buffer、连接状态、调度中间态、agent mailbox、中立 task graph、context compaction snapshot 等技术状态；但它不得拥有 Polaris 业务状态，也不得声明业务 `state_owners`。

## 16. 存储与归档子系统规范

存储与归档是 Polaris 首个必须标准化落地的高副作用参考子域。该子域的规范既服务运行时恢复，也服务历史审计与迁移治理。

### 16.1 逻辑路径分类

| 逻辑域 | 推荐路径 | 性质 | 保留策略 |
| --- | --- | --- | --- |
| 运行时状态 | `runtime/*` | 热态、可变、短周期 | 可覆盖、按运行时生命周期清理 |
| 合同与任务快照 | `runtime/contracts/*`、`runtime/tasks/*` | 运行时核心事实 | 受状态拥有规则约束 |
| 运行时事件 | `runtime/events/*` | 审计与增量事实 | 可压缩归档 |
| 运行历史 | `workspace/history/*` | 冷态、长期保留 | 默认永久保留 |
| Factory 历史 | `workspace/history/factory/*` | 冷态、可审计 | 默认永久保留 |

### 16.2 生命周期模型

- `runtime/*` 面向当前运行。
- `workspace/history/*` 面向历史审计、回放与恢复。
- 终态归档必须从 runtime 事实导出到 history 事实，不能反过来把 history 当作运行时 source-of-truth。

### 16.3 统一路径解析入口

所有路径解析都必须统一收口到 `resolve_storage_roots(workspace)` 或其等价的稳定契约实现。它至少应解析：

- `runtime_root`
- `workspace_persistent_root`
- `history_root`
- `config_root`

### 16.4 稳定查询接口

存储与归档子域必须暴露或兼容以下稳定查询能力：

- `GET /runtime/storage-layout`
- `GET /runtime/migration-status`
- `GET /history/runs`
- `GET /history/runs/{run_id}/manifest`
- `GET /history/tasks/{snapshot_id}/manifest`
- `GET /history/factory/{run_id}/manifest`

如果当前实现尚未完全采用该命名，至少应保证存在语义等价的稳定 Contract，并在迁移中收敛到上述接口语义。

### 16.5 压缩规则

- 历史大文件、事件流与 JSONL 归档允许压缩。
- 压缩与解压都必须保持显式 UTF-8 文本语义。
- 压缩不能改变 manifest、index 与查询 Contract 的语义稳定性。

### 16.6 归档失败策略

- 归档失败不得静默吞掉。
- 归档失败必须留下结构化审计证据。
- 归档失败可以重试，但不能破坏源运行时事实。
- manifest/index 更新必须具备幂等策略，避免重复写入造成历史污染。

## 17. 首批正式 Cell

首批必须优先定型的目标 Cell 包括：

1. `delivery.api_gateway`
2. `policy.workspace_guard`
3. `audit.evidence`
4. `context.engine`
5. `runtime.projection`
6. `runtime.state_owner`
7. `archive.run_archive`
8. `archive.task_snapshot_archive`
9. `archive.factory_archive`

### 17.1 当前状态说明

上述目标中，已有一部分在当前图谱中存在明确 Cell 资产；其余仍处于“已声明、待进一步实现收口”的迁移状态。  
`compatibility.legacy_bridge` 已在本分支从活动 graph 退场，保留为历史迁移阶段概念，不再作为首批正式 Cell。

### 17.2 迁移要求

对于尚未显式落地的目标 Cell，必须至少完成以下动作后，才可宣称其存在：

- 拥有独立 `cell.yaml`
- 拥有明确 `owned_paths`
- 拥有公开 Contract
- 拥有 `state_owners` 与 `effects_allowed` 声明
- 拥有最小验证目标
- 已从兼容分片中收口为稳定能力单元

对应的首批目标模板资产已放在：

- `docs/templates/targets/storage_archive/runtime/state_owner/`
- `docs/templates/targets/storage_archive/archive/run_archive/`
- `docs/templates/targets/storage_archive/archive/task_snapshot_archive/`
- `docs/templates/targets/storage_archive/archive/factory_archive/`

## 18. 参考子图：Storage Archive Pipeline

### 18.1 定位

`storage_archive_pipeline` 是当前已恢复的正式子图资产，用于规范运行时状态、历史归档与审计证据之间的协作路径。它已经在 `docs/graph/subgraphs/storage_archive_pipeline.yaml` 中落地，但相关实现仍有“已声明、未完全收口”的迁移 gap。

### 18.2 子图定义

```yaml
version: 1
id: storage_archive_pipeline
entry_cells:
  - delivery.api_gateway

cells:
  - delivery.api_gateway
  - policy.workspace_guard
  - runtime.state_owner
  - runtime.projection
  - audit.evidence
  - archive.run_archive
  - archive.task_snapshot_archive
  - archive.factory_archive

exit_cells:
  - runtime.projection
  - audit.evidence
  - archive.run_archive
  - archive.task_snapshot_archive
  - archive.factory_archive

critical_contracts:
  - RuntimeProjectionQueryV1
  - WorkspaceWriteGuardQueryV1
  - WorkspaceArchiveWriteGuardQueryV1
  - PersistRuntimeTaskStateCommandV1
  - PersistRuntimeRunCommandV1
  - AppendEvidenceEventCommandV1
  - ArchiveRunCommandV1
  - ArchiveTaskSnapshotCommandV1
  - ArchiveFactoryRunCommandV1

state_boundaries:
  - owner: runtime.state_owner
    state: runtime/tasks/*
  - owner: runtime.state_owner
    state: runtime/contracts/*
  - owner: runtime.state_owner
    state: runtime/state/*
  - owner: runtime.state_owner
    state: runtime/runs/*
  - owner: audit.evidence
    state: runtime/events/*
  - owner: archive.run_archive
    state: workspace/history/runs/*
  - owner: archive.task_snapshot_archive
    state: workspace/history/tasks/*
  - owner: archive.factory_archive
    state: workspace/history/factory/*

verify_targets:
  tests:
    - tests/architecture/test_graph_reality.py
    - tests/architecture/test_polaris_layout.py
    - tests/architecture/test_polaris_kernel_fs_guard.py
    - tests/architecture/test_architecture_invariants.py
    - tests/test_log_pipeline_storage_layout.py
    - tests/test_websocket_signal_hub.py
  contracts:
    - RuntimeProjectionQueryV1
    - WorkspaceWriteGuardQueryV1
    - WorkspaceArchiveWriteGuardQueryV1
    - PersistRuntimeTaskStateCommandV1
    - PersistRuntimeRunCommandV1
    - AppendEvidenceEventCommandV1
    - ArchiveRunCommandV1
    - ArchiveTaskSnapshotCommandV1
    - ArchiveFactoryRunCommandV1
```

对应的当前子图资产位于：

- `docs/graph/subgraphs/storage_archive_pipeline.yaml`

## 19. 状态所有权矩阵

Polaris 必须维护明确的状态所有权矩阵：

| 状态路径 | 唯一写拥有者 Cell | 其他读取方式 |
| --- | --- | --- |
| `runtime/tasks/*` | `runtime.state_owner` | query / projection |
| `runtime/contracts/*` | `runtime.state_owner` | query |
| `runtime/state/*` | `runtime.state_owner` | projection |
| `runtime/runs/*` | `runtime.state_owner` | archive / query |
| `runtime/events/*` | `events.fact_stream`（唯一写拥有者） | query / archive |
| `workspace/history/runs/*` | `archive.run_archive` | history query |
| `workspace/history/tasks/*` | `archive.task_snapshot_archive` | history query |
| `workspace/history/factory/*` | `archive.factory_archive` | history query |
| `*.index.jsonl` | `audit.evidence` 或明确声明的 archive index owner | list query |

## 20. Effect 声明矩阵

### 20.1 `runtime.state_owner`

允许：

- `fs.read:runtime/*`
- `fs.write:runtime/tasks/*`
- `fs.write:runtime/contracts/*`
- `fs.write:runtime/state/*`

禁止：

- `fs.write:workspace/history/*`

### 20.2 `archive.run_archive`

允许：

- `fs.read:runtime/runs/*`
- `fs.write:workspace/history/runs/*`
- `fs.write:workspace/history/runs.index.jsonl`

### 20.3 `archive.task_snapshot_archive`

允许：

- `fs.read:runtime/tasks/*`
- `fs.write:workspace/history/tasks/*`

### 20.4 `archive.factory_archive`

允许：

- `fs.read:workspace/factory/*`
- `fs.write:workspace/history/factory/*`

### 20.5 `audit.evidence`

允许：

- `fs.write:runtime/events/*`
- `fs.write:workspace/history/**/*.json`
- `fs.write:workspace/history/**/*.jsonl`
- `fs.write:workspace/history/**/*.jsonl.zst`

### 20.6 `delivery.api_gateway`

禁止任何直接：

- `fs.write`
- `db.write`
- 通过具体 adapter 进行 `db.read`
- 通过具体 backend 执行 `message.publish`

## 21. 归档触发规则与关键 Contract

### 21.1 归档触发规则

归档触发必须保持以下行为语义：

- Workflow 进入 `COMPLETED`、`FAILED`、`CANCELLED`、`BLOCKED`、`TIMEOUT` 等终态时触发终态归档。
- PM 迭代终态快照归档源自 `runtime/tasks/plan.json` 与 `runtime/tasks/task_*.json`。
- Factory run 进入终态时触发 Factory 归档。
- TaskBoard 终态事件写入 `runtime/events/taskboard.terminal.events.jsonl` 时，必须显式 UTF-8、flush、fsync。

### 21.2 第一阶段必须稳定化的 Contract

Command：

- `ArchiveRunCommandV1`
- `ArchiveTaskSnapshotCommandV1`
- `ArchiveFactoryRunCommandV1`
- `FinalizeIterationCommandV1`

Query：

- `GetStorageLayoutQueryV1`
- `GetMigrationStatusQueryV1`
- `ListHistoryRunsQueryV1`
- `GetArchiveManifestQueryV1`

Result：

- `ArchiveManifestV1`
- `HistoryRunIndexEntryV1`
- `StorageLayoutResultV1`
- `MigrationStatusResultV1`

Event：

- `RunArchivedEventV1`
- `TaskSnapshotArchivedEventV1`
- `FactoryArchivedEventV1`
- `TaskTerminalEventV1`

## 22. 目录与代码归位规范

### 22.1 目标骨架

```text

  bootstrap/
    config/
    runtime/
    wiring/
    server.py

  delivery/
    http/
      routers/
      schemas/
      middleware/
    ws/
    cli/

  application/
    contracts/
    usecases/
    workflows/
    services/
    ports/
    queries/

  domain/
    entities/
    value_objects/
    services/
    policies/
    ports/

  kernelone/
    runtime/
    fs/
    db/
    effect/
    trace/
    locks/
    scheduler/
    auth_context/
    ws/
    stream/
    events/
    tool_runtime/
    llm/
    process/
    contracts/
    telemetry/
    agent_runtime/

  infrastructure/
    storage/
    archive/
    messaging/
    persistence/
    llm/
    process/
    telemetry/

  cells/
    runtime/
      state_owner/
      state_projection/
    archive/
      run_archive/
      task_snapshot_archive/
      factory_archive/
    context/
      engine/
    audit/
      evidence/
    policy/
      workspace_guard/
    compatibility/
      legacy_bridge/

  docs/
    graph/
      catalog/
      subgraphs/
      templates/

  tests/
    architecture/
    integration/
```

### 22.2 已有实现的归位建议

以下归位建议描述的是目标迁移方向，而不是“已完成事实”：

- `storage_policy.py` -> `domain/policies/storage_policy.py`
- `history_archive_service.py` -> `application/services/history_archive_service.py`
- `archive_hook.py` -> `application/services/archive_hook.py`
- `runtime.py` / `history.py` 路由 -> `delivery/http/routers/runtime.py` / `delivery/http/routers/history.py`
- 压缩、文件复制、索引写入等后端实现 -> `infrastructure/storage/**` 与 `infrastructure/archive/**`
- 纯技术的 path/layout runtime contract -> 若完全无 Polaris 业务语义，可进入 `kernelone/fs/` 或 `kernelone/runtime/`；否则不得进入 `KernelOne`

## 23. 组装根与依赖注入

### 23.1 唯一组装根

`bootstrap/` 是唯一允许的组装根，负责：

- 进程启动与关闭
- 配置加载与校验
- 依赖图装配
- 生命周期管理
- 选择具体适配器与运行时策略

### 23.2 依赖注入规则

必须采用构造注入或等价的显式注入：

```python
class ArchiveService:
    def __init__(self, fs: KernelFS, trace: KernelTrace, archive_store: ArchiveStorePort) -> None:
        self._fs = fs
        self._trace = trace
        self._archive_store = archive_store
```

禁止在 service 内直接实例化具体 adapter。

### 23.3 状态与配置规则

默认禁止：

- 可变模块级单例
- `sys.path` patching
- 在 `bootstrap/` 之外修改 `os.environ`
- 在应用代码中使用 `atexit` 清理关键状态

## 24. 旧根目录退役策略

以下目录一律视为旧根目录：

- `api/`
- `app/`
- `core/`
- `framework/`
- `polaris_app/`
- `scripts/`

### 24.1 旧根目录规则

旧根目录下不得新增功能代码。允许存在的仅限：

- 迁移适配器
- 兼容垫片
- 弃用标记
- 临时转发入口

强制要求：

1. 旧文件不得继续承担新行为的主实现。
2. 每个兼容垫片都必须有删除责任人与退役计划。
3. `kernelone/` 绝不允许导入旧根目录。
4. 在调用方未迁完前，被触达旧模块必须保持足够薄且可直接导入。

### 24.2 迁移策略

迁移必须采用收敛式迁移：

1. 在规范根目录建立主实现。
2. 将调用方改指向规范实现。
3. 需要兼容时，把旧实现降级为薄垫片。（现在不允许使用任何兼容fallback行为）
4. 调用方全部迁完后删除旧实现。

严禁：

- 复制一份逻辑到新根目录后长期双修。
- 旧实现与新实现长期并存。
- 两边都持续打补丁。

## 25. Governance Plane 与 CI 门禁

### 25.1 强制门禁

CI 至少必须执行以下校验：

1. `cell.yaml` 与 `subgraph.yaml` schema 校验。
2. 跨 Cell import fence 校验。
3. `owned_paths` 冲突校验。
4. `state_owners` 冲突校验。
5. 契约兼容性校验。
6. 声明图与代码依赖图对账。
7. `generated/*.pack.json` 新鲜度校验。
8. 影响分析驱动的最小测试集校验。
9. UTF-8 文本读写校验。
10. 未声明副作用校验。
11. `context.catalog` 语义描述卡 schema 校验。
12. `context.catalog` 描述卡 freshness 校验（hash 漂移检测）。

### 25.2 迁移完成前必须额外补齐的门禁

在宣布迁移完成前，还必须具备：

- 旧根目录不得新增文件或新增功能逻辑
- `bootstrap-only composition` 检查
- `bootstrap/` 与 `infrastructure/` 之外禁止导入具体 adapter
- 审计所有 `delivery -> domain` 直连，确保仅落在窄边界
- 每个新 KernelOne 子系统都经过准入审查
- 架构测试从旧 phase 命名收敛到规范根目录语义

### 25.3 推荐流水线

```text
validate-manifests
build-system-graph
check-boundaries
check-contract-compat
generate-context-packs
sync-context-catalog
select-tests
run-targeted-tests
run-subgraph-integration-tests
publish-architecture-report
```

## 26. 测试与验证规范

### 26.1 测试放置规则

- 单元测试应与生产代码归属对齐。
- 适配器契约测试应放在适配器附近或 `tests/contracts/`。
- 架构测试统一放在 `tests/architecture/`。
- 集成测试应尽量通过真实边界进入系统，而不是深度导入内部模块。

### 26.2 子图验证要求

每个关键子图必须可验证。

### 26.3 存储归档子图最小验证集

至少包括：

- 归档 Contract 测试
- manifest 兼容性测试
- history index 查询测试
- effect 仅声明不越界测试
- state owner 唯一性测试
- `storage-layout` / `migration-status` 接口兼容测试

## 27. 性能与热路径规范

### 27.1 热路径规则

对热路径必须遵守：

- 同进程优先
- 直连调用优先
- 禁止不必要的 JSON 序列化
- 禁止为了“架构优雅”制造事件风暴

### 27.2 冷路径规则

以下内容默认放在冷路径或后台：

- graph 生成
- context pack 生成
- impact 计算
- verify 规划
- 大部分治理校验

### 27.3 AI 场景下的性能收益判断

ACGA 的收益通常首先体现在：

- 更少的仓库扫描
- 更少的无关上下文装配
- 更准确的测试选择
- 更少的跨模块误改

## 28. 规范级别

### 28.1 MUST

以下内容为 MUST：

- 每个 public Cell 必须有 `cell.yaml`
- 每个 public Cell 必须有 `README.agent.md`
- 每个 public Cell 必须定义公开 Contract
- 跨 Cell 只能依赖 `public/`
- 所有文本读写必须显式 UTF-8
- 所有副作用必须显式声明
- 每个状态必须只有一个写入拥有者
- 每个关键子图必须可验证
- `kernelone/` 不得承载 Polaris 业务语义
- `kernelone/` 不得声明业务 `state_owners`
- 所有 `fs/db/ws/stream/agent runtime` 副作用都必须遵循 `Cell -> effect port -> kernelone contract -> infrastructure adapter`
- 旧根目录不得新增功能代码
- 未落地的目标 Cell 不得被描述为当前事实

### 28.2 SHOULD

以下内容为 SHOULD：

- 每个 Cell 生成 `context.pack.json`
- 使用子图组织复杂流程
- 通过 impact analysis 做目标化测试
- 对热路径保持同进程直连
- 为复杂 Cell 生成 `impact.pack.json`
- 为复杂 Cell 生成 `verify.pack.json`
- 为每个 KernelOne 子系统编写准入评审记录

### 28.3 MAY

以下内容为 MAY：

- 引入运行时图与静态图对账
- 引入更细粒度的 `relations/contracts` graph 资产
- 为复杂 effect 增加 receipt、trace replay、impact diff 等高级治理能力

## 29. 迁移执行顺序

### Phase 0: 冻结与盘点

- 冻结旧根目录新增功能
- 建立迁移台账：旧文件 -> 新归属 -> 删除条件
- 识别高频热点文件与跨层导入热点
- 建立 `docs/graph/catalog/cells.yaml` 初版并持续维护

### Phase 1: 图谱优先

- 为第一批 Cell 建立 `cell.yaml`
- 建立 `README.agent.md` 与第一版公开 Contract
- 对齐 `pm_pipeline`、`director_pipeline`、`context_plane` 的现有事实
- 把 `storage_archive_pipeline` 先作为目标子图模板立起来

### Phase 2: 存储归档子图收敛

- 将 archive/runtime/history 主实现归位到规范根目录
- 让 `runtime.state_owner` 与 `archive.*` 边界成型
- 将旧路径降级为薄垫片

### Phase 3: 门禁自动化

- import fence
- state owner 唯一性
- effect 声明检查
- contract compatibility
- pack 新鲜度
- graph 对账

### Phase 4: Context Plane 接入

- `context.catalog`
- `context.pack_builder`
- `impact.pack.json`
- `verify.pack.json`

### Phase 5: 扩展到更多热点子图

优先扩展到：

- `director.execution`
- `pm.task_contract`
- `tooling.executor`
- `qa.integration_gate`

## 30. 样板 `cell.yaml`

```yaml
cell_id: archive.run_archive
kind: capability
owner: architecture-team
public: true

owned_paths:
  - application/services/history_archive_service.py
  - infrastructure/archive/**
  - cells/archive/run_archive/**

state_owners: []

inbound_ports:
  - name: archive_run
    mode: command
    contract: ArchiveRunCommandV1

outbound_ports:
  - name: read_runtime_run
    mode: effect
    contract: RuntimeRunReadEffectV1
  - name: write_history_archive
    mode: effect
    contract: HistoryArchiveWriteEffectV1
  - name: emit_run_archived
    mode: event
    contract: RunArchivedEventV1

depends_on:
  - runtime.state_owner
  - audit.evidence
  - policy.workspace_guard

effects_allowed:
  - fs.read:runtime/runs/*
  - fs.write:workspace/history/runs/*
  - fs.write:workspace/history/runs.index.jsonl

verify_targets:
  - tests/archive/test_run_archive_contracts.py
  - tests/archive/test_run_archive_behavior.py
  - tests/integration/test_storage_archive_pipeline.py
```

## 31. 结语

本规范的本质不是换一个更潮的架构名词，而是把 Polaris 从：

- 目录驱动
- 文件聚合
- 全仓扫描式 AI 工作流
- 分散的运行时副作用实现

升级为：

- 图谱驱动
- 能力单元驱动
- Context Pack 驱动
- 由 `Cell + KernelOne + Infrastructure + Governance` 协同约束的可审计后端体系

若以下三点能长期成立，这套架构就会稳定生效：

1. Graph 是否是真相。
2. Contract 是否是边界。
3. Context Pack 是否成为 AI/Agent 的默认入口。
