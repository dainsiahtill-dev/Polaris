# Cognitive Runtime Architecture

- 状态: Draft / Phase-1+2 Landed / Shadow Wired (Default On) / Not Mainline Gating
- 适用范围: `src/backend/polaris/`
- 角色: 说明 `Cognitive Runtime` 的定位、分层归属、与 `Resident/AGI` 及既有能力的复用关系
- 历史草案别名: `factory.governor`

> 本文不是 graph 真相，不替代 `src/backend/docs/graph/**`、`cell.yaml`、`src/backend/docs/FINAL_SPEC.md`。
> 本文的目的，是在 Polaris 内部先把 `Cognitive Runtime` 作为目标能力域的职责、边界和复用策略写清，避免重复实现。

> 当前新增约束：
> `Cognitive Runtime` 现阶段只能先落骨架，不能接入真正的 Polaris 业务主链。
> 原因不是方向改变，而是当前优先级必须先把历史代码跑通、把已有链路收敛稳定，再逐步接入认知补偿与工厂治理能力。

> 当前代码事实（2026-03-26）：
> `Cognitive Runtime` 的 Phase-1 skeleton 已落在：
> `polaris/domain/cognitive_runtime/`、
> `polaris/application/cognitive_runtime/`、
> `polaris/infrastructure/cognitive_runtime/`、
> `polaris/cells/factory/cognitive_runtime/`、
> `polaris/delivery/http/routers/cognitive_runtime.py`、
> `polaris/bootstrap/cognitive_runtime/`。
> 当前已实现 `resolve_context`、`lease_edit_scope`、`validate_change_set`、
> `record_runtime_receipt`、`export_handoff_pack`、
> `map_diff_to_cells`、`request_projection_compile`、
> `promote_or_reject`、`record_rollback_ledger` 及只读查询；
> runtime receipt / handoff 持久化明确使用 SQLite：
> `runtime/cognitive_runtime/cognitive_runtime.sqlite`。
> 这些实现是 authority facade，不是第二套 Context OS。
> 当前已对 `roles.runtime` 与 `director.execution` 接入 shadow-sidecar：
> 执行链会旁路写入 receipt / handoff，但不会用 `Cognitive Runtime`
> 去阻断、裁决或替代生产主链。
> 运行时模式可开关（默认开启 shadow）：`KERNELONE_COGNITIVE_RUNTIME_MODE=off|shadow|mainline`。

---

## 1. 一句话定义

`Cognitive Runtime` 是 Polaris 中面向 AI/Agent/LLM 的外置认知支撑层。

它的职责不是替代 `Director`、`factory.pipeline`、`llm.control_plane` 或 `Resident/AGI`，而是：

- 替 AI/Agent 背负长期上下文
- 稳住跨 session、跨 context window 的连续性
- 把 `proposal -> reconcile -> verify -> promote / reject` 放进运行时裁决链
- 为无人值守编码、重构、恢复和演化提供 scope、receipt、handoff 与 evidence

一句话说：

**`Cognitive Runtime` = 建立在 KernelOne 之上的认知补偿运行时，而不是新的模型层、元数据平台或现实工种。**

它本质上不是现实世界里的岗位映射物，更像：

- AI 的外置工作记忆
- AI 的上下文稳定器
- AI 的执行边界控制层
- AI 的脑外器官

### 1.1 在 `PM -> ChiefEngineer -> Director -> QA` 流程中的角色

`Cognitive Runtime` 不是这条链上的又一个新角色。

更准确地说：

- `PM -> ChiefEngineer -> Director -> QA` 是纵向业务执行链
- `Cognitive Runtime` 是覆盖整条链的横向认知与治理控制层
- `kernelone` 是更底下的技术底座

因此，`Cognitive Runtime` 在这条链中的最佳定义是：

**横向的上下文卸载、运行时裁决、证据归因与链路交接层。**

它不是“纵向旁观者”，因为它未来会拥有真实权力：

- 裁剪上下文
- 发放 scope lease
- 阻止越界写入
- 验证 change set
- 记录 runtime receipts
- 决定 handoff 资产
- 为 promote / reject 提供依据

但它也不是新的 PM / ChiefEngineer / Director / QA。

它不直接承担这些角色的业务职责：

- 不代替 PM 做任务拆解
- 不代替 ChiefEngineer 产施工图
- 不代替 Director 写代码
- 不代替 QA 做验收裁决

一句话说：

- 纵向角色负责干活
- `Cognitive Runtime` 负责让这些角色以可治理、可交接、可追溯的方式干活

在不同阶段，它与这条链的关系可收敛为：

1. `PM` 阶段
   - 整理和沉淀任务上下文、handoff 资产、约束边界
2. `ChiefEngineer` 阶段
   - 接收 blueprint proposal，并把 blueprint 升级为可治理输入资产
3. `Director` 阶段
   - 发放 edit scope lease、裁剪执行上下文、校验 change set、收口 receipts
4. `QA` 阶段
   - 组装 verify/evidence 资产，并把验收结果串回 blueprint、task、change set 和 receipts

当前阶段必须继续保持诚实边界：

- 现在它还只是旁路骨架和未来控制面的定义
- 未来它才应升级为整条链的横向 authority layer

---

## 2. 为什么概念名改为 `Cognitive Runtime`

推荐概念名调整为：`Cognitive Runtime`

推荐模块族名调整为：`cognitive_runtime`

历史草案中的 `factory.governor` 仍然保留为别名，用于解释先前讨论稿，但后续新文档应优先使用 `Cognitive Runtime`。

这样调整的原因有四个：

1. 它不是现实组织里的工种，而是专门为补偿 LLM 缺陷而存在的运行时层。
2. 它真正解决的是工作记忆不足、上下文漂移、跨会话连续性差，而不只是“治理”。
3. 它和项目里的 `Resident/AGI` 更容易形成上下分层，而不是语义冲突。
4. 它更准确表达了“context is runtime-owned, not model-owned” 这条硬原则。

因此，本文件后文统一采用：

- 概念名: `Cognitive Runtime`
- 推荐代码族名: `cognitive_runtime`
- 历史草案别名: `factory.governor`

不再优先使用以下命名：

- `acga3`
- `evolution_engine`
- `context_center`
- `meta_manager`
- `factory.control_plane`

原因：

1. `acga3` 是版本名，不适合作为长期能力命名
2. `evolution_engine` 太窄，装不下上下文卸载、scope gate、handoff 与 receipts
3. `context_center` 太偏 context，不足以表达执行边界与运行时裁决
4. `meta_manager` 会把系统误导成“元数据维护器”
5. `factory.control_plane` 与现有 `llm.control_plane` 过于接近，容易混淆

---

## 3. 与 Polaris 分层的关系

`Cognitive Runtime` 不是新的顶层根目录，而是一个跨 Polaris 现有分层协作的新能力域。

### 3.1 与 `kernelone/` 的关系

`kernelone/` 是底座。

`Cognitive Runtime` 必须复用 `kernelone/` 提供的通用技术能力，例如：

- `fs / storage`
- `events / message_bus`
- `trace / receipt`
- `process`
- `context / context_compaction`
- `locks / scheduler`

但 `kernelone/` 不拥有以下内容：

- 工厂真相
- Cell 级裁决
- proposal / promotion policy
- Polaris 业务状态拥有权

结论：

- `Cognitive Runtime` 建立在 `kernelone/` 之上
- `Cognitive Runtime` 不进入 `kernelone/`
- `Cognitive Runtime` 必须消费 `KernelOne State-First Context OS`，而不是自己重造第二套 working-memory runtime

### 3.1.1 与 `State-First Context OS` 的关系

`Cognitive Runtime` 与 `State-First Context OS` 不是替代关系，而是上下层关系。

推荐裁决：

1. `Cell IR / Graph truth`
   - 拥有长期边界真相、契约真相、Projection/Back-Mapping 真相
2. `KernelOne State-First Context OS`
   - 拥有 working set、state patch、artifact offload、episode sealing、retrieval、budget 控制
3. `Cognitive Runtime`
   - 拥有横向 authority：`scope lease`、`validate_change_set`、`handoff`、`runtime receipt`、`promote/reject`

必须明确一条铁律：

**`Cognitive Runtime` 拥有的是 working-set authority，不是 truth-set authority。**

这意味着：

1. 它不能替代 `context.engine`
2. 它不能替代 `roles.session`
3. 它不能替代 `resident.autonomy`
4. 它不能自己再发明一套新的 transcript / memory / continuity canonical truth

换句话说：

- `Context OS` 负责“这次 prompt 怎么组”
- `Cognitive Runtime` 负责“谁有权决定这么组、如何交接、如何裁决”

### 3.2 与 `cells/` 的关系

`cells/` 是 `Cognitive Runtime` 的公开能力边界。

建议目标落位：

- `polaris/cells/factory/cognitive_runtime/`

这一层应承载：

- `cell.yaml`
- `README.agent.md`
- `public/contracts.py`
- `public/service.py`
- `tests/`

结论：

- `cells/factory/cognitive_runtime/` 是对外契约与治理边界
- 它不是全部主实现所在

### 3.3 与 `application/` 的关系

`application/` 是 `Cognitive Runtime` 的主编排层。

建议目标落位：

- `polaris/application/cognitive_runtime/`

这里承载的应是：

- `resolve_context`
- `lease_edit_scope`
- `validate_change_set`
- `map_diff_to_cells`
- `record_runtime_event`
- `request_projection_compile`
- `promote_or_reject`

结论：

- `application/cognitive_runtime/` 负责编排
- 不是最终真相层

### 3.4 与 `domain/` 的关系

`domain/` 是 `Cognitive Runtime` 的模型与规则层。

建议目标落位：

- `polaris/domain/cognitive_runtime/`

这里应定义：

- `ContextSnapshot`
- `ContextHandoffPack`
- `EditScopeLease`
- `ChangeSetValidationResult`
- `Proposal`
- `ReconcileResult`
- `FitnessSpec`
- `PromotionDecision`

### 3.5 与 `delivery/` 的关系

`delivery/` 负责对外入口。

建议目标落位：

- `polaris/delivery/http/routers/cognitive_runtime.py`

后续可扩展：

- `polaris/delivery/mcp/cognitive_runtime_server.py`

### 3.6 与 `infrastructure/` 的关系

`infrastructure/` 负责具体适配器。

建议目标落位：

- `polaris/infrastructure/cognitive_runtime/`

这里可以承载：

- receipt store adapter
- symbol index adapter
- local daemon transport
- benchmark runner
- local persistence

### 3.7 与 `bootstrap/` 的关系

`bootstrap/` 负责把 `Cognitive Runtime` 组装成常驻运行时。

建议目标落位：

- `polaris/bootstrap/cognitive_runtime/`

---

## 4. 与现有关键能力的关系

### 4.1 与 `factory.pipeline`

`factory.pipeline` 负责：

- projection
- reprojection
- back-mapping
- projection experiment

`Cognitive Runtime` 负责：

- proposal 收口
- context handoff
- scope lease
- policy gate
- runtime refs
- promotion / rollback 决策

关系裁决：

- `Cognitive Runtime` 调用 `factory.pipeline`
- `Cognitive Runtime` 不替代 `factory.pipeline`

### 4.2 与 `llm.control_plane`

`llm.control_plane` 负责：

- 多模型接入
- provider 路由
- token / cost / streaming 管理

`Cognitive Runtime` 负责：

- 给 LLM 什么上下文
- 允许申请什么动作
- proposal 如何被裁决

关系裁决：

- `Cognitive Runtime` 消费 `llm.control_plane`
- `llm.control_plane` 不是认知补偿运行时

### 4.3 与 `context.engine`

`context.engine` 已经是 Context Plane 的公开门面。

关系裁决：

- `Cognitive Runtime` 不应重复发明第二套 context engine
- `Cognitive Runtime` 应站在 `context.engine` 与 `kernelone.context` 之上，补足：
  - handoff
  - scope-aware slicing
  - proposal/truth 分层
  - change-set 验证
  - authority / lease / receipt / promote-reject

### 4.3.1 与 `CELL_EVOLUTION_ARCHITECTURE_SPEC` 的兼容关系

`docs/CELL_EVOLUTION_ARCHITECTURE_SPEC.md` 解决的是：

1. Cell 作为长期内部架构 IR 如何组织
2. `Wave for Discovery, Particle for Truth, Projection for Delivery` 如何成立
3. Graph truth、语义候选、Projection 与 Back-Mapping 如何协同

`Cognitive Runtime` 不应推翻这层定义，而应补上其上的运行时层：

1. `Cell IR` 负责长期真相寻址
2. `State-First Context OS` 负责短期 working memory 装配
3. `Cognitive Runtime` 负责横向 authority 与治理裁决

因此，正确关系不是“Cell Evolution vs Cognitive Runtime”二选一，而是：

`Cell Evolution` 提供长期真相模型，`Context OS` 提供工作记忆运行时，`Cognitive Runtime` 提供 authority layer。

### 4.4 与 `director.execution`

`director.execution` 仍是工部执行工作流。

关系裁决：

- `Director` 继续负责执行任务
- `Cognitive Runtime` 负责治理、交接、裁决与长期上下文卸载
- `Director` 可以作为 `Cognitive Runtime` 的执行者之一
- `Director` 不应继续独自扩张成新的全局治理核心

### 4.5 与 `chief_engineer.blueprint`

`chief_engineer.blueprint` 是当前最值得被 `Cognitive Runtime` 吸收和上提的上游能力之一。

它当前已经具备的定位非常明确：

- 为 Director 生成 task-level implementation blueprint
- 进行 dependency analysis
- 维护 `target_files / scope_paths / unresolved_imports / scope_for_apply`
- 不直接执行代码写入

关系裁决：

- `chief_engineer.blueprint` 不是 `Cognitive Runtime`
- 但它非常适合作为 `Cognitive Runtime` 的 blueprint source

在长期目标里，这条线应从“任务级施工图”逐步增强为“两层蓝图”：

1. `Task Blueprint`
   - 面向当前任务的施工图
   - 关注 `target_files / scope_paths / unresolved_imports / construction_plan`
2. `Project Architecture Blueprint`
   - 面向整个项目的架构蓝图
   - 关注模块边界、依赖拓扑、API contracts、data flows、architecture decisions

当前 `chief_engineer.py` 中已经出现了这类更高层的结构，例如：

- `ProjectBlueprint`
- `ModuleArchitecture`
- `ApiContract`
- `DataFlowEdge`
- `ArchitectureDecision`
- `ModuleRestructuring`

这说明 `ChiefEngineer Blueprint` 不只是一个临时施工图生成器，它已经具备成为“项目代码架构蓝图来源”的雏形。

因此，`Cognitive Runtime` 对它的正确吸收方式是：

- 不推翻现有 `chief_engineer.blueprint` 角色
- 不把 `ChiefEngineer` 直接改名成 `Cognitive Runtime`
- 而是把它产出的 blueprint 体系上提为 `Cognitive Runtime` 的关键输入资产之一

更具体地说：

- `ChiefEngineer` 负责生成和维护 blueprint proposal
- `Cognitive Runtime` 负责对 blueprint 做：
  - reconcile
  - scope lease
  - runtime handoff
  - receipt 归因
  - promotion / reject

结论：

- `ChiefEngineer` 是 blueprint producer
- `Cognitive Runtime` 是 blueprint governor

这两者不是替代关系，而是上游提案与下游治理的关系。

### 4.6 与 `Resident/AGI`

项目中的 `Resident/AGI` 已经有相近但更高层的目标和实现。

当前事实包括：

- `docs/resident/resident-engineering-rfc.md`
- `docs/resident/agi-value-proposition.md`
- `polaris/domain/models/resident.py`
- `polaris/cells/resident/autonomy/internal/resident_runtime_service.py`

这条线当前明确在做：

- 持续身份
- 持续议程
- 目标治理
- 决策轨迹
- 技能沉淀
- 反事实实验
- 受控自我改进

关系裁决：

- `Resident/AGI` 不是 `Cognitive Runtime`
- `Cognitive Runtime` 也不是第二套 `Resident/AGI`

更准确的分层应是：

- `Resident/AGI` = 长期身份、议程、目标治理、学习进化层
- `Cognitive Runtime` = 外置工作记忆、上下文稳定、执行边界、handoff 与 receipt 层

因此，长期正确关系不是二选一，而是：

- `Resident/AGI` 站在 `Cognitive Runtime` 之上，或消费其能力
- `PM / ChiefEngineer / Director / QA` 同样可消费 `Cognitive Runtime`
- `kernelone` 继续作为两者共同底座

一句话说：

**`Resident/AGI` 更像长期驻留的软件工程心智内核，`Cognitive Runtime` 更像给这颗心智和各执行角色共用的脑外工作记忆与执行控制层。**

这也意味着两个明确边界：

1. 不应在 `Cognitive Runtime` 里再造一套新的 identity / agenda / goal governance / skill graph
2. 不应把 context slicing、scope lease、change-set validation、runtime receipt authority 反向塞给 `Resident/AGI`

---

## 5. 已有的候选能力与可吸收资产

`Cognitive Runtime` 不应从零重写。以下能力在 Director、Role Runtime、Context Plane 和 Resident/AGI 中已经存在，应优先复用或上提。

### 5.1 候选一：确定性上下文采集

来源文件：

- `polaris/cells/director/execution/internal/context_gatherer.py`

当前能力：

- 规则式采集上下文
- 无 LLM 调用
- 只读
- 组装目标文件、参考文件、repo tree、package meta

适合作为：

- `Context Capture`
- `Context Slicing`

当前不足：

- 仍偏文件级
- 未接入 graph / cell / projection / back-mapping
- 仍局限于 Director 本地执行前准备

### 5.2 候选二：任务生命周期与治理门禁

来源文件：

- `polaris/cells/director/execution/internal/task_lifecycle_service.py`

当前能力：

- 写范围校验 `validate_write_scope`
- 进度与 stalled 检测 `check_progress`
- 状态持久化
- 生命周期与 trajectory 查询
- evidence 保存与导出
- verification 期独立 audit 接入

适合作为：

- `Edit Scope Lease`
- `validate_change_set`
- `Runtime Receipts / Handoff`
- `stuck detection / recovery`

当前不足：

- 仍然是 Director 任务中心视角
- 尚未上升为 Cell / Projection / Promotion 视角

### 5.3 候选三：执行历史与快照

来源文件：

- `polaris/cells/director/execution/internal/director_agent.py`
- `polaris/cells/roles/runtime/internal/agent_runtime_base.py`

当前能力：

- execution history
- history lookup
- snapshot save/load
- agent state load/save
- persistent task history

适合作为：

- `Context Handoff`
- `Execution Receipt Trail`
- `resume / replay`

当前不足：

- 仍然是 role/agent 级持久化
- 尚未成为工厂级 canonical handoff contract

### 5.4 候选四：ChiefEngineer Blueprint 资产

来源文件：

- `polaris/cells/chief_engineer/blueprint/cell.yaml`
- `polaris/cells/chief_engineer/blueprint/public/contracts.py`
- `polaris/delivery/cli/pm/chief_engineer.py`

当前能力：

- 任务级 blueprint contract
- 施工范围和依赖分析
- `scope_for_apply`
- `unresolved_imports`
- `verify_ready`
- 更高层的项目架构数据结构雏形

适合作为：

- `Project Architecture Blueprint`
- `Task Blueprint`
- `Scope Hints`
- `Dependency Topology Input`
- `Architecture Decision Input`

当前不足：

- 仍然主要服务于 `PM -> ChiefEngineer -> Director`
- 尚未成为工厂级长期架构蓝图真相
- 尚未与 runtime receipts / projection / promotion 闭环打通

### 5.5 候选五：Resident/AGI 的长期认知资产

来源文件：

- `polaris/domain/models/resident.py`
- `polaris/cells/resident/autonomy/internal/resident_runtime_service.py`
- `docs/resident/resident-engineering-rfc.md`

当前能力：

- `ResidentIdentity`
- `ResidentAgenda`
- `DecisionRecord`
- `GoalProposal`
- `SkillArtifact`
- `ExperimentRecord`
- `ImprovementProposal`

适合作为：

- 长期认知资产的上层宿主
- 决策轨迹和演化治理的既有事实来源
- `Cognitive Runtime` 未来 northbound consumer 的核心候选

当前不足：

- 更偏长期身份、议程、目标和学习
- 尚未成为统一的 execution-context authority
- 尚未替整个角色链提供通用 `scope lease / handoff / validate_change_set` 运行时

---

## 6. 必须复用、禁止重写的现有能力

### 6.1 必须复用：角色记忆与快照骨架

来源：

- `polaris/cells/roles/runtime/internal/agent_runtime_base.py`

必须复用的原因：

- 已经具备 `AgentMemory`
- 已经具备 `save_state / load_state`
- 已经具备 `save_snapshot / load_snapshot`
- 已经具备 role 级 message bus 代理

裁决：

- `Cognitive Runtime` 不应再次发明一套新的 agent memory 框架

### 6.2 必须复用：通用 context compaction 能力

来源：

- `polaris/kernelone/context/compaction.py`

必须复用的原因：

- 已有 `RoleContextCompressor`
- 已有 `RoleContextIdentity`
- 已有 `CompactSnapshot`

裁决：

- `Cognitive Runtime` 可扩展更高层 handoff contract
- 但不能重复造一套新的 compaction 引擎

### 6.3 必须复用：KernelOne Context Engine

来源：

- `polaris/kernelone/context/engine/engine.py`
- `polaris/kernelone/context/manager.py`

必须复用的原因：

- 已有 provider 模型
- 已有 budget ladder
- 已有 snapshot / context event 发射逻辑

裁决：

- `Cognitive Runtime` 不应重新实现第二个基础 ContextEngine
- 应在其上补治理、handoff、scope lease 和 proposal/truth 分层

### 6.4 必须复用：Director 的治理门禁与证据链

来源：

- `polaris/cells/director/execution/internal/task_lifecycle_service.py`

必须复用的原因：

- 已经形成 write gate、progress tracker、evidence store、lifecycle、trajectory

裁决：

- `Cognitive Runtime` 应上提这些能力
- 不应再发明第二套平行的 state/evidence/lifecycle 体系

### 6.5 必须复用：ChiefEngineer 的 blueprint 数据模型

来源：

- `polaris/cells/chief_engineer/blueprint/public/contracts.py`
- `polaris/delivery/cli/pm/chief_engineer.py`

必须复用的原因：

- 已经形成了 task blueprint 的 contract 语义
- 已经积累了项目架构蓝图的雏形结构
- 与 Director 的施工图协作链已经存在

裁决：

- `Cognitive Runtime` 不应再发明第二套平行 blueprint 语义
- 应尽量吸收并规范化现有 `ChiefEngineer` blueprint 资产
- 后续若需要 project-level architecture blueprint，也应优先从现有 `ProjectBlueprint` 体系演进，而不是另造一套完全独立模型

### 6.6 必须复用：Resident/AGI 的长期认知边界

来源：

- `docs/resident/resident-engineering-rfc.md`
- `polaris/domain/models/resident.py`
- `polaris/cells/resident/autonomy/internal/resident_runtime_service.py`

必须复用的原因：

- 已经形成长期身份、议程、目标、技能、实验、自改提案的语义边界
- 已经有 resident state、API、runtime projection 与测试覆盖
- 继续平行造第二套长期认知内核会制造双重真相

裁决：

- `Cognitive Runtime` 不应再发明第二套 resident-style identity / agenda / goal ledger
- 后续若接入长期认知与自主演化，优先与 `Resident/AGI` 对齐或让其消费 `Cognitive Runtime`
- `Cognitive Runtime` 只应拥有 execution-context authority，不应吞掉 Resident 的长期心智语义

---

## 7. 历史“脑区”实现的处理原则

仓库中已存在更早的 `workspace/brain/*` 概念与实现，例如：

- `polaris/kernelone/memory/integration.py`
- `polaris/domain/services/todo_service.py`
- `polaris/kernelone/storage/policy.py`

这些实现说明：

- Polaris 过去已经把长期记忆、todo、reflection 视为一类共享能力

但在 `Cognitive Runtime` 设计中，裁决如下：

1. 可以吸收其概念价值
2. 可以复用其中的底层存储与路径策略
3. 不能把 `workspace/brain/*` 原样复制成新的工厂真相层
4. 不能让旧脑区继续成为 proposal/truth 的权威边界

结论：

- 旧 brain 是历史能力来源
- 不是 `Cognitive Runtime` 的最终 canonical model

---

## 8. `Cognitive Runtime` 的最小目标能力

第一阶段已落地以下能力（保持主链兼容）：

1. `resolve_context`
   - 基于 `context.engine`、context catalog、projection 信息生成最小上下文
2. `lease_edit_scope`
   - 基于现有 write gate 生成更正式的 edit scope lease
3. `validate_change_set`
   - 基于 changed files、scope、impact、evidence 做受控校验
4. `record_runtime_receipt`
   - 收口 execution / file write / verification 的归因信息
5. `export_handoff_pack`
   - 替下一轮 agent/session 准备稳定交接包

这五项也正好对应它最核心的四类认知补偿能力：

- `Context Capture`
- `Context Slicing`
- `Context Handoff`
- `Context Guarding`

第二阶段已补齐（仍以 shadow-sidecar 方式运行）：

1. `map_diff_to_cells`
2. `request_projection_compile`
3. `promote_or_reject`
4. `rollback_ledger`

### 8.1 当前阶段裁决：Shadow 接入，可开关，不接业务阻断主链

在历史代码尚未完全跑通之前，`Cognitive Runtime` 当前阶段维持以下约束：

1. 允许 `Phase-1 + Phase-2` 能力完整落地并持久化（SQLite）
2. 允许 `roles.runtime` / `director.execution` 旁路写 receipt/handoff/decision/rollback 资产
3. 允许通过开关启停：`KERNELONE_COGNITIVE_RUNTIME_MODE=off|shadow|mainline`
4. 保持对现有 Director / roles.runtime / kernelone.context / resident.autonomy 的复用边界

当前阶段明确不做以下内容：

1. 不接入 Polaris 真实业务主流程
2. 不替换当前 Director 主执行链
3. 不新增正式业务状态 source-of-truth
4. 不把 `Cognitive Runtime` 接到生产性自动演化闭环里
5. 不要求现阶段 graph 立即声明一个已经完全落地的新 public Cell
6. 若 graph/catalog 为了治理收口登记 `factory.cognitive_runtime`，只能以 skeleton + gap 状态诚实登记，不能伪装成主链 owner

换句话说，当前阶段的目标是：

- 先把骨架搭对
- 先把职责边界写清
- 先把复用清单钉死
- 先避免重复实现

而不是提前把一个尚未成熟的认知补偿内核塞进 Polaris 主业务。

---

## 9. 初始目录建议

建议的目标目录如下：

```text
polaris/
  application/
    cognitive_runtime/
  bootstrap/
    cognitive_runtime/
  delivery/
    http/
      routers/
        cognitive_runtime.py
  domain/
    cognitive_runtime/
  infrastructure/
    cognitive_runtime/
  cells/
    factory/
      cognitive_runtime/
```

说明：

- `cells/factory/cognitive_runtime/` 是公开边界
- `application/domain/infrastructure/bootstrap` 是主实现分层
- 不新增新的 Polaris 顶层根目录
- 若未来它从“工厂第一消费者”演进成更通用的认知层，优先调整 cell 归属，不要推翻 `cognitive_runtime` 这条模块族命名

---

## 10. 明确禁止的重复实现

在 `Cognitive Runtime` 推进过程中，禁止以下重复建设：

1. 再造一套新的基础 context engine
2. 再造一套新的 agent memory / snapshot 框架
3. 再造一套与 Director 平行的 evidence / lifecycle / trajectory 系统
4. 再造一套新的 resident-style identity / agenda / goal ledger
5. 再造一套新的 “brain” 根目录作为正式真相
6. 把 `Cognitive Runtime` 塞进 `kernelone/`
7. 让 `Director` 自己无限长大成全局治理核心

---

## 11. 当前结论

当前最准确的判断是：

- Director 里已经有 `Cognitive Runtime` 的几个关键胚胎
- `Resident/AGI` 里已经有长期认知与学习进化层的既有事实
- 但这些能力仍分散在执行工作流、角色运行时、KernelOne context 与 Resident autonomy 子系统里
- `Cognitive Runtime` 的正确做法，不是从零发明，而是：
  - 复用
  - 上提
  - 重组
  - 明确权威边界

因此下一步的正确方向不是“再写一个更大的 Director”，也不是“再造一个第二 Resident”，而是：

**把 Director、roles.runtime、kernelone.context 中已有的上下文、记忆、scope gate、evidence、recovery 能力，重组为新的 `Cognitive Runtime`；再让 `Resident/AGI` 和 `PM / ChiefEngineer / Director / QA` 站在这个认知补偿层之上工作。**
