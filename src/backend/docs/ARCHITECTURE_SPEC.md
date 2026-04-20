# ACGA v1.0

Agent-Centric Graph Architecture

状态: Draft v1.0  
适用范围: Polaris 后端及后续 Agent-Native 子系统  
目标: 用一套可执行、可审计、可迁移的正式规范，支撑“系统图谱化、节点能力化、边界契约化、上下文化、治理自动化”。

> 注：v1.0 作为基础规范继续有效；语义检索与描述卡增强原则见 `docs/ACGA_2.0_PRINCIPLES.md`。

---

## 1. 设计目标

ACGA 的首要目标不是“目录更整齐”，而是同时优化以下五件事：

1. AI/Agent 对局部能力的发现成本
2. 人类开发者对模块边界的理解成本
3. 跨模块修改时的影响分析与回归成本
4. 长期演进中的边界稳定性与架构抗腐化能力
5. 运行时在复杂协作场景下的可观测性、可替换性与可控副作用

ACGA 明确假设：

- 系统将持续由 AI/Agent 参与修改
- 代码库规模会增长，目录扫描会成为主要成本
- 需要控制 AI 修改半径，而不是只优化运行时结构
- 需要把“架构边界”从口头约定升级为机器可验证资产

---

## 2. 非目标

ACGA 不追求以下事情：

1. 不追求把每个能力都拆成独立进程或微服务
2. 不追求所有节点使用统一重型样板代码
3. 不追求把所有同步调用都改成事件总线
4. 不追求一次性全仓重构
5. 不追求消灭所有重复代码

ACGA 允许：

- 逻辑上节点化、物理上同进程部署
- 局部重复优先于错误抽象
- 在迁移期保留兼容节点包裹遗留实现

---

## 3. 总体模型

ACGA 将系统拆成六个平面：

### 3.1 Graph Plane

系统结构真相层。描述所有能力单元、依赖关系、状态归属、子图、入口、出口。

### 3.2 Capability Plane

系统真正的能力载体层。每个能力单元称为一个 `Cell`。

### 3.3 Flow Plane

负责跨 Cell 的流程编排。它描述“谁在什么时候与谁协作”，但不持有核心业务不变量。

### 3.4 Context Plane

专门为 AI/Agent 服务。负责节点检索、上下文包生成、影响分析、验证规划。

### 3.5 Effect Plane

所有外部副作用的唯一出入口。包括文件、数据库、HTTP、LLM、进程、消息队列。

### 3.6 Governance Plane

自动化架构治理层。负责元数据校验、依赖对账、契约兼容、上下文刷新、目标化测试选择。

一句话总结：

`Graph Plane` 决定系统怎么被理解，`Capability Plane` 决定能力怎么被实现，`Context Plane` 决定 AI 怎么高效工作，`Governance Plane` 决定这套边界能否长期成立。

---

## 4. 一等公民

ACGA 只有七个正式一等公民：

### 4.1 Cell

一个可独立理解、独立测试、独立替换、独立授权修改的稳定能力边界。

### 4.2 Port

Cell 的公开交互边界。所有跨 Cell 协作都必须经由 Port。

### 4.3 Contract

Port 交换的数据结构与行为语义。包括命令、查询、事件、结果、错误、流片段。

### 4.4 Relation

Graph 中的边。描述依赖、发布、订阅、状态拥有、投影、治理等关系。

### 4.5 Flow

一个跨 Cell 的业务或平台协作路径。

### 4.6 Context Pack

面向 AI 的最小上下文包，替代“先扫仓库再理解”的工作方式。

### 4.7 Fitness Rule

自动化架构门禁规则，用于验证边界、契约、上下文、性能约束是否仍然成立。

---

## 5. 核心原则

### 5.1 Graph First

系统的第一组织真相是能力图谱，不是目录树。

### 5.2 Cell First

最小自治单位是 Cell，不是 controller、service、utils。

### 5.3 Public/Internal Fence

每个 Cell 必须严格区分公开面与内部实现。跨 Cell 只能依赖公开面。

### 5.4 Contract First

跨 Cell 协作必须通过契约完成，不允许直接耦合对方内部类型。

### 5.5 Context First for AI

AI 的默认入口必须是 `cell.yaml`、`README.agent.md`、`context.pack.json`，不是全仓扫描。

### 5.6 Single State Owner

每个状态只能有一个 Cell 拥有写权限。

### 5.7 Explicit Effects

所有副作用必须显式声明，禁止未声明的文件写入、数据库写入、网络调用、进程拉起。

### 5.8 Governance by Automation

边界不能靠约定，必须靠机器校验。

---

## 6. Cell 规范

### 6.1 Cell 粒度

一个 Cell 应当承载一个稳定能力或一个稳定职责边界，例如：

- `director.execution`
- `pm.task_contract`
- `qa.integration_gate`
- `context.catalog`
- `tooling.executor`

以下对象不得直接成为 Cell：

- `common`
- `helpers`
- `misc`
- `utils`
- 无明确输入输出的工具拼盘

### 6.2 Cell 类型

允许的 Cell 类型：

| kind | 作用 |
| --- | --- |
| `capability` | 真正能力实现 |
| `workflow` | 流程编排 |
| `policy` | 权限、校验、限流、规则判断 |
| `projection` | 只读聚合与查询视图 |
| `integration` | 外部系统接入 |
| `compatibility` | 遗留封装与反腐层 |

### 6.3 标准目录

复杂 Cell 的标准目录如下：

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

简单 Cell 可以收缩，但必须至少包含：

- `cell.yaml`
- `README.agent.md`
- `public/`
- `tests/`
- `generated/context.pack.json`

### 6.4 Cell 内部结构

Cell 内部默认采用“小六边形”：

`public input -> internal application -> internal domain/policy -> output ports -> adapters`

这意味着：

- 对外是公开契约
- 对内允许实现自由演化
- 输出副作用必须先经过输出端口

---

## 7. Port 与 Contract 规范

### 7.1 Port 类型

固定使用五类 Port：

| mode | 语义 |
| --- | --- |
| `command` | 引发状态变化 |
| `query` | 只读请求 |
| `event` | 发布已发生事实 |
| `stream` | 持续输出 |
| `effect` | 外部副作用调用 |

### 7.2 Port 规则

1. `command` 必须可审计
2. `query` 不得产生副作用
3. `event` 只能表达事实，不能伪装命令
4. `effect` 必须在 `effects_allowed` 中声明
5. Port 的参数和返回值必须使用公开 Contract

### 7.3 Contract 分类

Contract 固定分为：

- `Command`
- `Query`
- `Event`
- `Result`
- `Error`
- `StreamChunk`

### 7.4 Envelope

跨 Cell 通信应统一包裹消息信封：

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

### 7.5 Contract 版本规则

1. 契约必须显式版本化
2. breaking change 必须升级 major version
3. 非兼容变更不得静默覆盖
4. 跨 Cell 不得暴露内部领域对象

---

## 8. Graph Plane 规范

### 8.1 Graph 资产

图谱必须至少包括：

- `graph/catalog/cells.yaml`
- `graph/subgraphs/*.yaml`
- 未来可扩展的 `relations.yaml`、`contracts.yaml`

### 8.2 Relation 类型

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

### 8.3 子图规则

一个子图表示一个稳定的业务或平台协作面，例如：

- `director_pipeline`
- `pm_pipeline`
- `qa_pipeline`
- `context_plane`

子图必须声明：

- `entry_cells`
- `cells`
- `exit_cells`
- `critical_contracts`
- `state_boundaries`
- `verify_targets`

### 8.4 Graph 是真相，不是纯文档

Graph 必须被以下环节消费：

1. AI 上下文选择
2. 影响分析
3. 回归测试选择
4. 架构一致性校验

---

## 9. Context Plane 规范

### 9.1 Context Plane 的职责

Context Plane 负责四件事：

1. 维护可查询的 Cell 目录
2. 为任务构建最小上下文包
3. 计算改单元影响面
4. 生成验证路径

### 9.2 Context Pack 类型

每个 Cell 至少应生成：

- `context.pack.json`
- `impact.pack.json`
- `verify.pack.json`

### 9.3 AI 标准读取顺序

AI 必须遵循以下读取顺序：

1. 查询 `graph/catalog/cells.yaml`
2. 定位目标 Cell
3. 读取 `cell.yaml`
4. 读取 `README.agent.md`
5. 读取 `generated/context.pack.json`
6. 读取 `public/contracts`
7. 仅在需要时读取 `owned_paths`
8. 若仍不足，再扩张到邻接 Cell

默认禁止先全仓扫描。

### 9.4 Context Pack 生成原则

Context Pack 必须：

- 可自动重建
- 可缓存
- 支持增量更新
- 只包含对 AI 真的必要的信息

---

## 10. Effect Plane 规范

### 10.1 Effect Cell

所有外部副作用必须经由 Effect Cell 或显式 `effect` Port 执行。典型对象包括：

- 文件系统
- 数据库
- HTTP 客户端
- LLM 网关
- 进程执行器
- 消息总线

### 10.2 Effect 规则

1. capability Cell 不得直接持有未声明的 I/O 副作用
2. effect 路径必须支持 trace、timeout、权限、审计
3. 文件写入必须显式 UTF-8
4. effect 行为必须可被验证和观测

---

## 11. 状态与投影

### 11.1 单写原则

每个状态只能被一个 Cell 声明为 `state_owners`。

### 11.2 Projection 规则

其他 Cell 若需要读取该状态，应通过：

- Query Port
- Projection Cell
- 订阅事件后构建只读视图

不允许多个 Cell 直接并发写同一状态源。

---

## 12. Governance Plane 规范

### 12.1 强制门禁

CI 至少必须执行以下校验：

1. `cell.yaml` 与 `subgraph.yaml` schema 校验
2. 跨 Cell import fence 校验
3. `owned_paths` 冲突校验
4. `state_owners` 冲突校验
5. 契约兼容性校验
6. 声明图与代码依赖图对账
7. `generated/*.pack.json` 新鲜度校验
8. 影响分析驱动的最小测试集校验
9. UTF-8 文本读写校验
10. 未声明副作用校验

### 12.2 推荐流水线

```text
validate-manifests
build-system-graph
check-boundaries
check-contract-compat
generate-context-packs
select-tests
run-targeted-tests
run-subgraph-integration-tests
publish-architecture-report
```

### 12.3 Fitness Rule 原则

每条 Fitness Rule 必须：

- 能被自动执行
- 能给出失败证据
- 能明确映射到 Cell 或 Subgraph

---

## 13. 性能规范

ACGA 默认采用“逻辑解耦、物理聚合”。

### 13.1 热路径规则

对热路径，必须遵守：

1. 同进程优先
2. 直连调用优先
3. 禁止不必要的 JSON 序列化
4. 禁止为架构优雅而事件风暴化

### 13.2 冷路径规则

以下内容默认放到冷路径或后台：

- graph 生成
- context pack 生成
- impact 计算
- verify 规划
- 大部分治理校验

### 13.3 AI 场景下的性能判断

ACGA 的主要收益通常体现在：

- 更少仓库扫描
- 更少无关上下文装配
- 更准的测试选择
- 更少跨模块误改

因此，整体工程吞吐通常优于纯目录式架构。

---

## 14. 命名规范

### 14.1 Cell ID

Cell ID 统一格式：

`<domain>.<capability>`

例如：

- `director.execution`
- `context.catalog`
- `audit.evidence`

### 14.2 文件命名

推荐：

- `cell.yaml`
- `README.agent.md`
- `context.pack.json`
- `impact.pack.json`
- `verify.pack.json`

禁止使用模糊命名：

- `common.py`
- `misc.py`
- `helpers.py`
- `base_utils.py`

---

## 15. 迁移策略

ACGA 不应一次性推倒重建。推荐四阶段迁移：

### 阶段 1: 图谱覆盖

为现有关键模块补齐：

- `cell.yaml`
- `README.agent.md`
- `graph/catalog/cells.yaml`
- `graph/subgraphs/*.yaml`

此阶段允许代码暂不移动。

### 阶段 2: 公开边界收口

为关键模块补齐 `public/` 与公开 Contract，切断对内部实现的直接依赖。

### 阶段 3: Context Plane 接入

将代码智能从“按文件检索”升级为“按 Cell 检索 + 邻接扩张”。

### 阶段 4: 热点拆分

优先拆解超大文件、耦合热点和遗留接口层，落成真正的 Cell 结构。

---

## 16. 第一批推荐落地 Cell

结合 Polaris 当前代码形态，建议优先抽象以下 Cell：

1. `delivery.http.api_gateway`
2. `pm.task_contract`
3. `director.execution`
4. `tooling.executor`
5. `audit.evidence`
6. `runtime.state_owner`
7. `runtime.state_projection`
8. `qa.integration_gate`
9. `context.catalog`
10. `context.pack_builder`
11. `policy.workspace_guard`

---

## 17. 落地清单

本规范配套的最小落地物必须包括：

1. `docs/graph/catalog/cells.yaml`
2. `docs/graph/subgraphs/*.yaml`
3. `docs/templates/cell.yaml`
4. `docs/templates/README.agent.md`
5. `docs/templates/context.pack.json`

这些文件不是装饰品，而是 ACGA 的最小执行入口。

---

## 18. 规范级别

以下内容为 `MUST`：

- 每个 public Cell 必须有 `cell.yaml`
- 每个 public Cell 必须有 `README.agent.md`
- 每个 public Cell 必须定义公开 Contract
- 跨 Cell 只能依赖 `public/`
- 所有文本读写必须显式 UTF-8
- 所有副作用必须显式声明
- 每个状态必须只有一个写入拥有者
- 每个关键子图必须可验证

以下内容为 `SHOULD`：

- 每个 Cell 生成 `context.pack.json`
- 使用子图来组织复杂流程
- 通过 impact analysis 做目标化测试
- 对热路径保持同进程直连

以下内容为 `MAY`：

- 为复杂 Cell 生成 `impact.pack.json`
- 为复杂 Cell 生成 `verify.pack.json`
- 在未来引入运行时图与静态图对账

---

## 19. 结语

ACGA 的本质不是“换一个更潮的架构名词”，而是把代码库从“目录驱动”升级为“图谱驱动”，把模块从“文件聚合”升级为“能力单元”，把 AI 从“全仓扫描式工作”升级为“上下文包驱动式工作”。

这套规范的成败，不在于目录是否漂亮，而在于以下三点是否长期成立：

1. Graph 是否是真相
2. Contract 是否是边界
3. Context Pack 是否成为 AI 的默认入口

若三者成立，系统就会越来越适合人类与 Agent 共同演化。
