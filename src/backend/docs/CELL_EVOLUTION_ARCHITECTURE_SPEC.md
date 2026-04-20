# Cell Evolution Architecture Spec v2.0

- 状态: Proposed（实验版本）
- 生效日期: 2026-03-21
- 适用范围: Polaris 后端 `src/backend`
- 角色: `FINAL_SPEC.md`、`ACGA_2.0_PRINCIPLES.md` 与现有 Cell 化治理资产的补充性战略规范
- 强制级别: MUST NOT weaken `AGENTS.md` / `FINAL_SPEC.md` / `docs/graph/**`
- 核心创新点: `Cell Wave-Particle Duality Model`（Cell 波粒二象性模型）

> 本文定义 Polaris 内部提出的一套核心建模方法：Cell 既不是目录，也不是单一 embedding 向量，而是一种同时具有“语义波态”“契约粒态”“物理投影”的内部架构 IR（Intermediate Representation）。用户最终看到的仍然是传统工程代码；Cell 主要作为 Polaris 内部的高维能力模型、生成模型、治理模型与运行时追踪模型存在。

---

## 0. 文档定位

### 0.1 这份文档解决什么问题

Polaris 已经明确采用：

- `Graph First`
- `Cell First`
- `Graph-Constrained Semantic`
- `Descriptor over Raw Source`
- `KernelOne as Agent/AI Operating Substrate`

但现有规范主要回答的是：

1. 当前图谱真相是什么
2. 目标物理分层应该是什么
3. Cell 的治理边界如何约束
4. Descriptor、Context Pack、Verify Pack 分别负责什么

仍然缺少一套对下面问题的系统回答：

1. 如果 Cell 不是目录，而是一个高维能力对象，它的正式定义是什么
2. 语义聚类、embedding、运行时证据、契约边界和传统文件结构之间如何协同
3. 如果 Polaris 未来要替用户生成代码，是否也应该使用同一套 Cell 思维模型
4. 用户最终仍然希望看到传统代码结构，那么 Cell 应该如何被“隐藏”在系统内部
5. 如何避免让“高维自由度”破坏可审计性、可回滚性、可测试性和可维护性

本文专门回答这些问题。

### 0.2 与其他规范的优先级关系

若发生冲突，裁决顺序如下：

1. `AGENTS.md`
2. `docs/graph/catalog/cells.yaml` 与 `docs/graph/subgraphs/*.yaml`
3. `docs/FINAL_SPEC.md`
4. `docs/ACGA_2.0_PRINCIPLES.md`
5. 本文

结论：

- 本文不能创建第二套 Graph 真相
- 本文不能放松 `Graph First`
- 本文不能让向量空间替代架构裁决
- 本文不能把目标实现写成当前事实

### 0.3 当前事实与目标态的诚实边界

本文是目标架构与执行模型说明，不是当前实现完成证明。

当前已经存在的事实包括：

- Polaris 已采用 Cell 化图谱治理框架
- `docs/graph/catalog/cells.yaml` 是当前 Graph 真相目录
- Descriptor Pack 已进入 ACGA 2.0 设计与部分落地
- `polaris/cells/` 已开始承载正式 Cell 资产
- 运行时、审计、投影、任务看板、Task Runtime 等正在逐步收口到 Cell 模式

但以下内容在当前仓库中仍属于目标态或部分落地态：

- 完整的 Cell IR 编译链
- 面向目标项目代码生成的 Cell 内部模型
- 从用户需求到 Cell 图再到传统代码的完整投影编译器
- 文件改动与运行时事件到 Cell 的全面反向映射系统
- 基于波粒二象性术语的统一治理与观测面板

因此，本文中的“应当”“目标”“建议实现”都应被视为正式设计要求，而非对当前仓库完成度的声称。

### 0.4 一句话总述

Polaris 采用：

`Wave for Discovery, Particle for Truth, Projection for Delivery`

含义是：

- Cell 的波态负责语义发现、邻接、聚类、演化候选和上下文压缩
- Cell 的粒态负责契约、生效、边界、状态拥有权和副作用裁决
- 文件系统与传统代码只是 Cell 的物理投影，不是 Cell 的全部真相
- 用户最终消费的是传统工程；Polaris 内部操作的是 Cell 这一层更高维的架构 IR

---

## 1. 核心裁决

### 1.1 Cell 不是文件夹，也不是单一向量点

Cell 的正式定义不是目录，不是 router，不是 service，不是 utils，也不是单一 embedding 向量。

Cell 是一个至少同时具有以下三重本质的内部对象：

1. `Semantic Wave`：语义波态，表达能力趋同、语义邻接、运行上下文相似性和演化倾向
2. `Contract Particle`：契约粒态，表达正式边界、公开契约、状态拥有权、effects 与验证门禁
3. `Projection Artifact`：投影结果，表达在传统代码、运行时事件和测试资产中的具体落点

离开其中任何一项，Cell 都是不完整的：

- 只有波态，没有粒态：会变成不受控的语义云
- 只有粒态，没有波态：会退化为僵硬的静态模块声明
- 只有投影，没有前两者：会退化为普通目录结构

### 1.2 Cell 是 Polaris 的内部架构 IR

本文做出正式裁决：

**Cell 在 Polaris 中的最佳定位是内部架构 IR。**

这里的 IR（Intermediate Representation）不是编译器术语的照搬，而是一个工程组织层面的中间表示：

- 它比源码文件更抽象
- 它比自然语言需求更结构化
- 它足以支撑规划、检索、编排、生成、验证和恢复
- 它最终可以投影成传统代码结构
- 它也可以从传统代码与运行时事实中被反向识别和更新

这意味着 Polaris 内部的真实工作单元应当逐步从“文件”升级为“Cell IR 节点”。

### 1.3 用户看到传统代码，系统内部运行 Cell

本文明确区分两个视角：

1. `Internal Architecture View`：Polaris 内部使用 Cell 作为高维能力模型
2. `External Delivery View`：用户最终拿到的是传统 repo、模块、类、接口、测试与配置文件

因此：

- 用户不需要默认理解 Cell
- 生成代码时不应强迫用户接受 Polaris 的内部抽象
- Polaris 必须能够把 Cell IR 投影成符合目标生态习惯的传统工程结构
- Polaris 也必须能把用户后续对传统代码的改动反向归因到 Cell

### 1.4 Graph 决定真相，Vector 决定候选

ACGA 2.0 的核心原则在本文中进一步收敛为：

- `Graph` 决定正式边界、合法依赖、状态拥有权和副作用权限
- `Descriptor / Embedding / Similarity` 决定候选、排序、邻接和演化建议

禁止以下错误做法：

- 用相似度高低决定 state owner
- 用聚类结果直接宣布新 Cell 已正式存在
- 用向量索引绕过 `cells.yaml`
- 用 embedding 相似性自动授权跨 Cell 写入

### 1.5 波态可以流动，粒态必须稳定

本文正式采用以下语言：

- `Wave Form`：Cell 在高维语义空间中的存在形态
- `Particle Form`：Cell 在治理与执行层中的稳定存在形态
- `Collapse`：从波态候选进入粒态生效的过程
- `Projection`：从 Cell IR 输出为传统代码、测试、配置、运行时事件等低维载体的过程
- `Back-Mapping`：从低维载体反向追溯到 Cell 的过程

这意味着 Polaris 采用的是：

`Liquid Discovery, Solid Activation, Stable Projection`

---

## 2. Cell 波粒二象性模型

### 2.1 为什么不是修辞，而是工程模型

“波粒二象性”在本文中不是文学比喻，而是一套工程上可执行的双模建模方式。

它的价值在于同时解决两个长期冲突：

1. `自由发现` 与 `严格治理` 的冲突
2. `高维语义组织` 与 `传统代码交付` 的冲突

如果只有“波”：

- 容易形成漂亮但不可验证的语义叙事
- 代码归属、写权限、测试边界会失控

如果只有“粒”：

- 会让 Cell 退化成另一种硬编码目录规范
- 失去自动发现、上下文压缩、演化建议和能力聚落识别能力

波粒二象性的意义就是：

- 用波态承载自由度
- 用粒态承载确定性
- 用投影承载交付
- 用回映射承载闭环

### 2.2 波态定义：Semantic Halo（语义光晕）

每个 Cell 都应有一个“语义光晕”，它不是一个单点，而是一组高维信号的集合。

语义光晕至少应包含以下通道：

1. `purpose_signal`
   - 它解决什么问题
   - 它服务哪个能力域

2. `contract_signal`
   - 它暴露哪些 command / query / event / result / error
   - 它接收和输出哪些语义结构

3. `state_signal`
   - 它读写哪些 source-of-truth
   - 它与哪些状态快照或 runtime 实体相关

4. `effect_signal`
   - 它涉及哪些副作用类型
   - 文件写、LLM 调用、HTTP、DB、消息、进程、工具调用等

5. `runtime_signal`
   - 实际执行时经常与哪些事件、任务、失败类型共现
   - 哪些角色或工具经常引用它

6. `evolution_signal`
   - 它最近的改动方向
   - 它正在趋近哪些邻近能力
   - 它正在分裂还是收敛

7. `verification_signal`
   - 它通常需要怎样的测试与 smoke 验证
   - 它的典型回归面是什么

这些信号共同构成 Cell 在语义空间中的“波包”，而不是一个简化过度的单向量点。

### 2.3 粒态定义：Contract Nucleus（契约核）

每个正式 Cell 都必须有一个“契约核”，它是这个 Cell 的正式存在依据。

契约核至少包含：

- `cell_id`
- purpose
- visibility
- owned_paths
- public_contracts
- depends_on
- state_owners
- effects_allowed
- verification targets
- lifecycle status
- source hash / graph fingerprint

只有契约核是正式真相。语义光晕不是正式真相。

### 2.4 投影定义：Physical / Runtime / Verification Projection

Cell 不能停留在抽象层。它必须能够被投影到低维执行世界中。

典型投影包括：

1. `Physical Projection`
   - Python 文件
   - package 目录
   - tests
   - config
   - templates

2. `Runtime Projection`
   - taskboard rows
   - sessions
   - snapshots
   - audit evidence
   - runtime.v2 events
   - LLM lifecycle hints

3. `Verification Projection`
   - unit tests
   - integration tests
   - smoke commands
   - fitness rules

### 2.5 反向映射定义：Back-Mapping

投影不是单向的。

Polaris 必须具备从以下低维事实反向映射 Cell 的能力：

- 文件改动
- 导入关系
- LLM 调用链
- 工具调用记录
- 运行时任务状态
- 审计事件
- 测试失败

如果做不到回映射，Cell 只能是一次性的规划对象，而不能成为长期架构基元。

### 2.6 波粒术语表

为后续实现统一词汇，本文定义以下术语：

| 术语 | 工程含义 |
| --- | --- |
| Wave Form | Cell 在语义空间中的高维存在形态 |
| Particle Form | Cell 在治理与执行层的正式存在形态 |
| Semantic Halo | Cell 的多通道语义信号集合 |
| Contract Nucleus | Cell 的正式契约内核 |
| Collapse | 候选 Cell 通过治理后转为正式生效 |
| Resonance | 两个或多个能力长期高频共现并表现出收敛趋势 |
| Repulsion | 经验证不应合并的能力之间被施加治理隔离 |
| Drift | 物理代码与 Cell 契约/语义逐渐偏离 |
| Projection | 从 Cell IR 编译/渲染到传统代码与运行时载体 |
| Back-Mapping | 从物理或运行时事实反推 Cell 归属 |

### 2.7 Resonance 与 Repulsion 的治理意义

`Resonance` 不是“应该自动合并”，而是“值得进入候选评估”。

常见 Resonance 信号包括：

- 长期高频共同变更
- 高频共同失败
- 一致的状态读写模式
- 相近的 contracts
- 相同的人类/Agent 工作上下文

`Repulsion` 表示即便两个能力语义相似，也不应被并入同一 Cell。典型原因包括：

- state owner 必须隔离
- side effect 等级不同
- 生命周期不同
- 安全等级不同
- 人工审批责任不同

### 2.8 Collapse 的正式条件

Cell 从波态候选进入粒态生效，必须通过以下步骤：

1. 生成 proposal
2. 通过 Graph 约束过滤
3. 通过契约一致性校验
4. 通过 state owner / effect owner 裁决
5. 通过验证基座（Harness）
6. 通过人工或 CI 审批
7. 更新 `docs/graph/**` 与相关 Cell 资产

没有完成以上步骤，不得声称一个候选能力已经成为正式 Cell。

---

## 3. Cell 作为内部架构 IR

### 3.1 为什么必须引入 IR 层

如果 Polaris 未来要稳定地：

- 接收自然语言需求
- 自动拆解能力边界
- 分配多 Agent 上下文
- 生成传统代码
- 执行验证
- 做恢复与继续执行
- 维护长期架构演进

就不能直接在“需求”和“文件”之间硬连。

需求太松散，文件太低维。

中间必须有一层更稳定的结构化抽象，这一层就是 `Cell IR`。

### 3.2 Cell IR 的核心职责

Cell IR 必须同时承担以下职责：

1. **能力建模**
   - 描述某个能力边界是什么

2. **上下文裁剪**
   - 为 PM / Architect / Director / QA 提供最小必要上下文

3. **生成中间层**
   - 让生成器先输出 Cell 图，再输出传统代码

4. **治理中间层**
   - 让 CI、schema、fitness rules 能对“能力边界”而不是散落文件做约束

5. **运行时归因**
   - 让任务、事件、审计证据可以追溯到稳定能力单元

6. **演化中间层**
   - 让系统能知道“哪些东西在往一起收敛，哪些应该分开”

### 3.3 Cell IR 不是源码替代品

必须明确：

- Cell IR 不是源码真相
- Cell IR 不是文件系统替代品
- Cell IR 不是一套新的运行时解释语言

它的职责是：

- 在高维语义与低维代码之间充当正式桥梁
- 让 Polaris 的规划、生成、治理、验证和恢复使用同一套内部对象模型

### 3.4 建议的 Cell IR 结构

```yaml
cell_id: target.accounting.ledger
namespace: target
kind: capability
status: candidate

semantic_halo:
  purpose:
    summary: Maintain ledger transactions and account balances.
    embedding_ref: vec://purpose/target.accounting.ledger
  contracts:
    terms:
      - ledger entry
      - transaction
      - balance
      - account
    embedding_ref: vec://contract/target.accounting.ledger
  state:
    resources:
      - app/state/ledger.db
      - runtime/tasks/*
    embedding_ref: vec://state/target.accounting.ledger
  effects:
    allowed:
      - fs.write
      - db.write
      - http.inbound
    embedding_ref: vec://effect/target.accounting.ledger
  runtime:
    common_roles:
      - pm
      - director
      - qa
  evolution:
    neighbors:
      - target.accounting.category
      - target.accounting.monthly_summary

contract_nucleus:
  purpose: Own ledger write path and ledger query contracts.
  public_contracts:
    commands:
      - CreateLedgerEntry
      - UpdateLedgerEntry
    queries:
      - GetLedgerEntry
      - ListLedgerEntries
    results:
      - LedgerEntryResult
      - LedgerListResult
  state_owners:
    - target_runtime/ledger/*
  effects_allowed:
    - fs.write:target_runtime/ledger/*
    - db.write:ledger_entries
  depends_on:
    - target.shared.time
    - target.shared.money

projection:
  templates:
    - fastapi/service
    - sqlalchemy/model
    - pytest/unit
  output_paths:
    - app/models/ledger.py
    - app/services/ledger_service.py
    - app/api/ledger_router.py
    - tests/test_ledger_service.py

verification:
  required:
    - unit
    - integration
    - smoke

provenance:
  source_request_id: req_123
  graph_fingerprint: sha256:...
  source_hash: sha256:...
  confidence: 0.82
```

### 3.5 平台内部 Cell 与目标项目 Cell 必须分命名空间

如果 Polaris 自身和用户生成项目都采用 Cell 模型，必须强制区分命名空间：

1. `platform.*`
   - Polaris 自身的 Cell
   - 例如 `platform.runtime.task_runtime`
   - 例如 `platform.audit.evidence`

2. `target.*`
   - 用户目标项目的 Cell
   - 例如 `target.accounting.ledger`
   - 例如 `target.delivery.http_api`

禁止把两类 Cell 混在一个统一 ID 空间中，否则会造成：

- 审计污染
- 检索污染
- 上下文错误扩散
- 运行时事件归因混乱

### 3.6 Cell IR 与当前 graph 的关系

对于 Polaris 自身：

- 正式生效边界仍由 `docs/graph/catalog/cells.yaml` 管理
- Cell IR 可以看成 graph 的更丰富工作对象模型
- graph 是最终真相；Cell IR 是 richer working model

对于目标项目：

- Cell IR 可以先存在于工作区生成资产中
- 一旦用户选择保留治理能力，也可以为目标项目生成对应的 Graph/Manifest 资产
- 如果用户只想拿传统代码，不强制暴露 Cell manifest

---

## 4. 四层执行模型

### 4.1 Semantic Plane

职责：

- 从需求、代码、契约、日志、事件中提取语义信号
- 构建 descriptor
- 生成多通道 embedding
- 形成候选能力聚落、邻接图与演化提案

注意：

- 该层负责发现，不负责最终裁决
- 该层可以是高维、概率性、近似性的

### 4.2 Truth and Governance Plane

职责：

- 维护正式 Cell manifest 和 graph
- 裁决 depends_on、state_owners、effects_allowed
- 执行 schema、fitness rules、compatibility checks
- 决定谁能正式生效

注意：

- 这是“粒态层”
- 这里必须确定、稳定、可回滚

### 4.3 Projection Plane

职责：

- 将 Cell IR 渲染为传统项目结构
- 选择模板、文件命名、模块布局、测试骨架、配置布局
- 重写 import、注入接口、输出 README、脚本和配置

注意：

- 这里输出的必须是“用户熟悉的传统代码”
- Projection Plane 不能把 Cell 强行暴露给最终用户

### 4.4 Runtime and Audit Plane

职责：

- 把任务、执行会话、LLM 调用、工具调用、文件变更和测试结果映射到 Cell
- 为恢复、继续执行、回溯和审计提供稳定归因
- 为 Observer / Taskboard / Projection 提供结构化实时事件

注意：

- 没有 Runtime Plane，Cell 只是生成期抽象
- 有了 Runtime Plane，Cell 才能成为长期运行系统的一等对象

---

## 5. 从用户需求到传统代码的完整链路

### 5.1 总体链路

建议的总流程如下：

```text
User Intent
  -> Requirement Normalization
  -> Capability Decomposition
  -> Graph-Constrained Semantic Retrieval
  -> Cell IR Graph Synthesis
  -> Governance Validation
  -> Projection Compilation
  -> Traditional Repository Output
  -> Verification
  -> Runtime Back-Mapping
  -> Evolution Feedback
```

### 5.2 Requirement Normalization

目标：

- 把用户自然语言需求清洗为较稳定的能力说明
- 提取约束：技术栈、复杂度、部署方式、测试要求、质量门禁、输入输出形式

输出应至少包括：

- functional goals
- non-functional constraints
- stack preferences
- quality gates
- prohibited patterns
- expected deliverables

### 5.3 Capability Decomposition

目标：

- 不直接生成文件，而是先识别候选能力单元
- 初步形成候选 Cell 列表

例如，用户要“个人记账簿”时，系统内部更合理的第一步不是直接建目录，而是先拆出：

- `target.accounting.ledger`
- `target.accounting.category`
- `target.accounting.budget_alert`
- `target.reporting.monthly_summary`
- `target.delivery.http_api`
- `target.delivery.web_ui`
- `target.tests.integration_suite`

### 5.4 Graph-Constrained Semantic Retrieval

目标：

- 在已有模板库、经验库、内部 starter cells 中找到最相近的能力片段
- 但检索必须受 graph 约束，不能任意扩散

检索顺序建议为：

1. intent filter
2. stack filter
3. capability-type filter
4. graph-constrained candidate set
5. descriptor ranking
6. neighbor expansion
7. human/agent validation

### 5.5 Cell IR Graph Synthesis

目标：

- 把候选能力单元装配成一个内部 Cell 图
- 明确每个 Cell 的 contracts、state、effects、depends_on、verification

这里的结果应当是：

- 可规划
- 可验证
- 可投影
- 可回映射

而不是“先生成 20 个文件再看像不像样”。

### 5.6 Governance Validation

目标：

- 在写代码前，先验证内部 Cell 图是否自洽

至少要检查：

- 是否存在重复 state owner
- 是否存在未声明 effects
- 是否存在循环依赖
- 是否存在 contract 不闭合
- 是否存在 testing gap
- 是否存在安全级别冲突

### 5.7 Projection Compilation

目标：

- 把 Cell IR 输出为目标生态的传统工程结构

例如对 Python / FastAPI 项目，Projection Compiler 可能输出：

- `app/api/*.py`
- `app/services/*.py`
- `app/models/*.py`
- `app/repositories/*.py`
- `tests/*.py`
- `pyproject.toml`
- `.env.example`
- `README.md`

这里的关键原则是：

- 输出必须符合目标生态习惯
- 不应让用户看到 Polaris 内部术语
- 代码应是传统、可维护、可移植的工程形态

### 5.8 Verification

生成完传统代码后，必须立刻进入验证：

- unit tests
- integration tests
- smoke tests
- lint / format / import checks
- contract checks
- runtime task simulation

### 5.9 Runtime Back-Mapping

在系统执行、修复、恢复或压测期间，Polaris 必须能够回答：

- 当前任务命中的是哪个 Cell
- 当前失败属于哪个能力边界
- 哪些文件改动对应哪个 Cell
- 哪个 LLM 请求在为哪个 Cell 服务
- 这次工具调用是在执行哪个 Cell 的哪条 effect path

如果回答不了这些问题，Cell 就还没有真正成为工作单元。

### 5.10 Evolution Feedback

运行后的真实证据必须反哺波态：

- 哪些能力长期耦合
- 哪些能力长期冲突
- 哪些测试经常一起失败
- 哪些任务总在跨同一组模块改动
- 哪些 contracts 总是被一起理解和实现

这些信号更新 Semantic Halo，但不能直接改写粒态真相。

---

## 6. 投影规则：为什么传统文件系统仍然重要

### 6.1 文件系统是投影，不是幻觉

文件系统不是架构真相，但它仍然是：

- 编译器/解释器的执行载体
- Git diff 和 blame 的责任载体
- IDE、调试器、静态分析器的工作载体
- 回滚和事故排查的固定参照系
- 人类开发者的主要阅读界面

因此：

- 传统代码不是“低级残留物”
- 它是必须稳定存在的投影平面
- Polaris 不应走“每次启动动态生成整个代码目录”的路线

### 6.2 Projection Compiler 的正式职责

Projection Compiler 应至少负责：

1. 选择目标架构模板
2. 把 Cell IR 映射为模块布局
3. 生成 contracts 与实现骨架
4. 生成测试骨架与配置骨架
5. 重写 imports
6. 输出 projection map
7. 记录每个输出文件由哪些 Cell 投影而来

### 6.3 一对多 / 多对一映射都允许

不应把 Cell 与目录结构硬绑定成一一对应关系。

真实情况应该允许：

1. 一个 Cell 投影为多个文件
2. 多个 Cell 共同投影到一个模块目录
3. 一个物理文件承载多个 Cell 的接口胶合层

因此，真正的绑定器不是目录层级，而是：

- projection map
- owned_paths
- back-mapping metadata

### 6.4 目标输出必须“像人写的”，而不是“像 Polaris 写的”

这是非常关键的产品约束。

如果 Polaris 生成的目标项目带有明显的“内部模型投影味道”，用户体验会非常差。

所以 Projection 的原则是：

- 对用户隐藏 Cell
- 对系统保留 Cell
- 对代码输出保持目标生态原生风格

例如：

- Python 项目就输出 Python 社区习惯的模块划分
- React 项目就输出前端生态习惯的组件/feature 结构
- CLI 项目就输出命令、配置、service、tests 这些常规结构

### 6.5 Projection Map 必须是一等资产

Projection Map 建议成为正式资产，至少记录：

- `file -> cells`
- `cell -> files`
- `file -> contracts`
- `runtime event -> cells`
- `test case -> cells`

没有 Projection Map，后续修复和审计会退化为全文搜索。

---

## 7. 回映射与审计：让 Cell 成为长期工作对象

### 7.1 为什么回映射比生成更重要

很多生成系统只能“生成一次”，但不能“长期维护”。

Polaris 如果要真正成为工程交付系统，必须做到：

- 初次生成代码
- 增量修改代码
- 恢复中断任务
- 重构与迁移
- 压测和观测
- 失败回溯

这要求系统能从低维世界反向识别 Cell。

### 7.1.1 Projection 与 Back-Mapping 是工程实现的核心瓶颈

本文明确承认：`Cell IR -> 传统代码` 的正向投影只是难题的一半；真正的深水区在于 `代码修改 / 运行时事件 -> Cell` 的逆向回映射。

原因有三：

1. 文件级 Diff 不能可靠表达 Cell 归属
   - 一个文件可能同时承载多个 Cell 的投影片段
   - 同一 Cell 也可能跨多个文件存在

2. IDE 人工修改会快速破坏“文件路径即边界”的幻觉
   - 开发者通常直接在物理代码上工作
   - 如果没有稳定的符号级锚点，系统很难低延迟判断某次修改到底影响哪个 Cell

3. 运行时事件天然只看得到低维载体
   - 任务、LLM、工具、日志、测试都首先指向文件、命令、事件
   - 如果没有回映射层，这些证据无法上升回 Cell 视角

因此，Polaris 不应把回映射实现为“路径表 + 文本搜索”的轻量功能，而应把它作为正式中间层能力建设：

- 维护 `Projection Map`
- 维护符号级 `Back-Mapping Index`
- 优先使用 `Tree-sitter` / AST 切片锚定类、函数、方法、变量等稳定结构
- 在运行时事件与审计 receipt 中持续写入 `cell_id` 或可追溯 `refs`

如果做不到这一点，Cell 最终仍会退化成一次性规划模型，而不是可持续维护的工程基元。

### 7.2 应当建立的回映射矩阵

建议至少维护以下映射：

1. `task_id -> cell_id[]`
2. `session_id -> cell_id[]`
3. `llm_request_id -> cell_id`
4. `tool_call_id -> cell_id`
5. `file_path -> cell_id[]`
6. `test_case -> cell_id[]`
7. `runtime event -> cell_id[]`
8. `audit receipt -> cell_id[]`

### 7.3 运行时事件必须能指向 Cell

对于 Polaris 自身运行时，应尽量让以下结构化事件带上 Cell 归因：

- task claimed
- task running
- task suspended
- task resumed
- llm waiting
- llm completed
- tool call
- tool result
- file write receipt
- qa passed / failed

如果暂时做不到直接携带，也至少要通过 `refs` 或 projection 快照间接关联。

### 7.4 审计证据应服务于架构理解，而不仅是合规

审计系统不仅是“记录发生了什么”，它还应帮助系统理解：

- 哪些 Cell 在实际运行中共现
- 哪些 Cell 的边界经常被越界访问
- 哪些 Cell 的验证总是不够
- 哪些能力经常需要一起恢复或重跑

这让审计从被动日志变成主动演化信号源。

---

## 8. 为什么要对用户隐藏 Cell

### 8.1 用户关心的是交付，不是内部中间表示

对绝大多数用户而言，他们关心的是：

- 最终项目目录
- API 是否可用
- 页面是否可用
- 测试是否通过
- 部署是否稳定

他们并不需要学习 Polaris 内部的 Cell、Descriptor、Projection Map、Semantic Halo 这些概念。

因此，产品层面应遵循：

- 默认隐藏 Cell
- 默认展示传统工程抽象
- 仅在高级调试、解释模式、架构视图中暴露 Cell

### 8.2 不隐藏会导致什么问题

如果把 Cell 直接暴露给普通用户，会带来：

- 概念负担过高
- 学习曲线陡峭
- 用户把内部实现误认为交付结果
- 用户试图直接手工操作高维模型，导致治理失效

### 8.3 高级模式可以暴露“解释视图”，但不暴露内部复杂度

建议未来提供：

- `Normal Mode`
  - 用户只看到传统 repo 与功能模块

- `Explain Mode`
  - 用户可查看某个模块背后对应哪些 Cell
  - 可查看为何某次修复命中这组模块
  - 可查看任务与能力边界的映射

- `Architecture Mode`
  - 给高级用户和平台维护者查看完整 Cell 图、projection map、runtime back-mapping 和演化 proposal

---

## 9. 与 Polaris 角色系统的关系

### 9.1 PM / Architect / Director / QA 不应直接以文件为基本认知单位

未来最理想的状态是：

- `Architect` 主要在 Cell 图层工作
- `PM` 主要在 capability / task / contract 层工作
- `Director` 在 Cell 归因约束下操作传统代码投影
- `QA` 在 Cell 验证面和传统测试面之间做闭环

也就是说，角色之间看到的最低共同抽象应逐步从“文件”提升为“Cell + Projection”。

### 9.2 Director 的执行目标应当是“命中 Cell 并修改投影”

Director 不是直接对散落文件乱写。

更理想的语义是：

1. 领取某个任务
2. 该任务映射到一个或多个 Cell
3. Director 在这些 Cell 的允许边界内工作
4. 最终修改的是传统代码投影文件
5. 审计系统保留 `task -> cell -> file` 的完整链路

### 9.3 QA 应当既验证文件，也验证 Cell 边界

QA 的最终闭环不应只有“测试过了没”。

还应包括：

- 是否越界写入其他 Cell 状态
- 是否新增未声明副作用
- 是否破坏 projection map
- 是否造成 semantic halo 与 contract nucleus 明显背离

---

## 10. 与 ACGA 2.0 的关系

### 10.1 本文是 ACGA 2.0 的进一步工程化表达

ACGA 2.0 已经明确：

- Graph 是真相
- Descriptor 优于 Raw Source
- 检索必须 Graph-Constrained
- 语义索引是派生资产，不是真相

本文进一步补上的，是一个更完整的中间层模型：

- Cell 不只是 graph 节点
- Cell 也是 Polaris 的内部架构 IR
- Cell 同时面向检索、生成、治理、运行与恢复

### 10.2 Descriptor 在波态中的定位

Descriptor 不是 Cell 本身，而是 Semantic Halo 的结构化入口。

Descriptor 的作用是：

- 提供可嵌入的短文本
- 提供统一分类字段
- 提供跨模型稳定的检索入口

Descriptor 不应该承担：

- 完整契约真相
- 完整运行时真相
- 完整治理真相

### 10.3 Graph-Constrained Semantic 的升级版解释

在本文模型下，Graph-Constrained Semantic 的正确理解是：

1. 先用 Graph 裁剪合法候选空间
2. 再用 Descriptor / Embedding 做波态排序
3. 再回到粒态层做生效裁决
4. 最终再投影为传统代码与运行时事实

这形成一个闭环，而不是一次性检索动作。

---

## 11. 目标项目生成场景示例

### 11.1 示例需求

用户输入：

“帮我生成一个个人记账簿项目，包含账单录入、分类管理、月度统计、预算提醒、导入导出、完整测试与本地持久化。”

### 11.2 内部 Cell 视角

Polaris 内部不应立即生成文件，而应先形成类似这样的 target cells：

- `target.accounting.ledger`
- `target.accounting.category`
- `target.accounting.budget_alert`
- `target.reporting.monthly_summary`
- `target.import_export.csv_io`
- `target.persistence.local_store`
- `target.delivery.http_api`
- `target.tests.contract_suite`

### 11.3 投影视角

最终交付给用户的传统结构可能是：

```text
app/
  api/
    ledger_router.py
    category_router.py
    summary_router.py
  services/
    ledger_service.py
    category_service.py
    budget_service.py
  models/
    ledger.py
    category.py
  repositories/
    ledger_repository.py
    category_repository.py
  storage/
    sqlite_store.py
tests/
  test_ledger_service.py
  test_budget_service.py
  test_api_integration.py
pyproject.toml
README.md
.env.example
```

用户只看到这个投影结果。

### 11.4 回映射视角

而 Polaris 内部仍知道：

- `app/services/ledger_service.py` 主要由 `target.accounting.ledger` 投影而来
- `test_api_integration.py` 同时覆盖 `target.delivery.http_api` 与 `target.accounting.*`
- 某次 Director 修复命中了 `target.persistence.local_store`
- 某次 QA 失败与 `target.reporting.monthly_summary` 强相关

这就是“对用户隐藏 Cell，对系统保留 Cell”的实际意义。

---

## 12. 不可接受的错误实现

### 12.1 把向量空间当成架构真相

错误。

后果：

- 边界漂移不可控
- 权限与写入归属不可靠
- 审计无法成立

### 12.2 让 Cell 成为一套用户必须理解的新目录规范

错误。

后果：

- 用户学习成本高
- 与生态脱节
- 交付结果不自然

### 12.3 让 Projection 成为一次性不可逆过程

错误。

后果：

- 后续无法增量维护
- 无法恢复任务上下文
- 无法做稳定审计

### 12.4 允许波态直接写代码并生效

错误。

正确过程必须是：

`Wave Candidate -> Governance -> Particle Activation -> Projection`

### 12.5 把 Polaris 平台 Cell 与目标项目 Cell 混在一起

错误。

后果：

- 检索污染
- 审计污染
- 事件污染
- 上下文误召回

### 12.6 把文件系统视为可随时推倒重建的瞬态幻影

错误。

后果：

- Git 历史失去意义
- IDE 体验崩溃
- 调试与 hotfix 失控
- 人类开发者无法稳定协作

---

## 13. Polaris 落地建议

### 13.1 第一原则：先把术语和资产统一

短期内最重要的不是实现全套演化引擎，而是统一以下概念：

- Cell = 内部架构 IR
- Wave = Semantic Halo
- Particle = Contract Nucleus
- Projection = 传统代码 / 运行时 / 测试投影
- Back-Mapping = 从文件与事件反追 Cell

### 13.2 第二原则：先做 Projection Map，再做自由演化

如果没有 Projection Map，过早引入大规模自动聚类和自动重组会让系统不可维护。

所以建议顺序是：

1. 先建立 stable cell id
2. 先建立 projection map
3. 先建立 runtime back-mapping
4. 再逐步增强 semantic halo
5. 最后再做 assisted evolution

### 13.3 第三原则：先内部自用，再对外隐藏输出

这套模型最适合的推广路径不是“先暴露给用户”，而是：

1. Polaris 自身先用这套模型治理自己
2. 在平台内部把规划、执行、观测、恢复对齐到 Cell IR
3. 再把它用于目标项目生成
4. 对用户默认只输出传统工程，不输出 Cell 负担

### 13.4 第四原则：用户编排与 Cell 模型分层

未来即便开放用户自定义编排，用户也应主要配置：

- 流程策略
- 角色策略
- 质量门禁
- 恢复策略
- 并发策略

而不应直接编辑底层 Cell 粒态真相。

---

## 14. 实施路线图

### Phase 0：词汇和边界统一

目标：

- 在文档、观测、治理、实现中统一“波态 / 粒态 / 投影 / 回映射”词汇
- 明确 Cell 是内部架构 IR，而不是目录约定

### Phase 1：Descriptor 与多通道语义信号

目标：

- 给每个正式 Cell 补齐更稳定的 descriptor 结构
- 引入 purpose / contract / state / effect / failure 等多通道信号

### Phase 2：Projection Map 资产化

目标：

- 建立 `cell -> files` 与 `file -> cells` 的正式映射资产
- 把测试和运行时事件也逐步接入映射

### Phase 3：Runtime Back-Mapping

目标：

- 让 taskboard、session、llm lifecycle、tool trace、audit evidence 都能追溯 Cell
- 让“任务恢复”真正建立在能力边界之上

### Phase 4：目标项目生成的 Cell IR 编译链

目标：

- 在 Polaris 内部先生成 Cell 图，再投影出传统 repo
- 把 projection compiler 接入模板库与验证链

### Phase 5：Assisted Evolution

目标：

- 基于真实运行证据给出 Cell 收敛/拆分建议
- 仍然坚持 proposal 和治理审批，不做全自动生效

---

## 15. 成功标准

如果这套模型真正落地，Polaris 至少应达到以下结果：

1. 用户生成的项目仍然是传统、自然、可维护的工程结构
2. Polaris 内部对任务、代码、事件、测试和恢复的认知单位逐步从文件升级为 Cell
3. Graph 真相、Descriptor 检索、Projection 输出、Runtime 审计之间形成闭环
4. 任何一次关键修改都能回答：
   - 改了哪个 Cell
   - 为什么改这个 Cell
   - 改动投影到了哪些文件
   - 运行时有哪些证据支持这次判断
5. Cell 不再是“文件夹思维”的升级版，而成为真正的高维能力中间层

---

## 16. 一句话总结

Polaris 的 Cell 不是给用户看的目录，也不是一团不可控的向量云。

**Cell 是 Polaris 内部的高维架构 IR。**

它在语义空间中呈现为波，用于发现、邻接、聚类、检索和演化；
它在治理空间中呈现为粒，用于契约、生效、授权、验证和审计；
它在交付空间中被投影为用户熟悉的传统代码、测试和配置；
它在运行空间中又通过任务、事件和证据被反向映射回来，形成完整闭环。

这就是 Polaris 的核心技术点：

`Cell Wave-Particle Duality Architecture`
