# ACGA 3 Autonomous Factory Spec

- 状态: Draft / Supplement
- 适用范围: Polaris 仓库级无人值守软件工厂方向补充
- 角色: 说明 Polaris 如何沿用 ACGA 3 理念增强 AI/Agent/LLM 的编码、重构与受控自演化能力

> 本文不是当前后端正式真相，不替代 `src/backend/docs/graph/**`、`src/backend/docs/FINAL_SPEC.md` 与 `cell.yaml`。本文描述的是仓库级目标方案与演化方向。

---

## 1. 目标

Polaris 采用 ACGA 3 理念推进无人值守自动化软件开发工厂时，目标不是“让 AI 更会聊天”，而是让系统更可靠地：

1. 写代码
2. 改代码
3. 重构代码
4. 自动提出更优实现
5. 自动验证、比较、晋升或拒绝候选版本

一句话：

**ACGA 3 的工厂目标，是让 Polaris 成为受控自演化的软件工厂控制面。**

### 1.1 上下文负担卸载是核心目标之一

Polaris 采用 ACGA 3 理念建设无人值守软件工厂时，必须明确一个核心目标：

**不是让模型记住更多，而是让系统替模型记住正确的东西。**

这意味着：

- 长期上下文归运行时，不归模型
- 真相资产归治理内核，不归会话记忆
- LLM 负责理解、提案和执行局部任务
- 系统负责切片、压缩、交接、验证和回放

为了实现这件事，ACGA 3 的工厂运行时必须至少具备 4 类上下文卸载能力：

1. `Context Capture`
   - 将 graph、cell、projection、receipt、verify 等上下文沉淀为运行时资产
2. `Context Slicing`
   - 在每次任务执行前生成最小必要且正确的上下文切片
3. `Context Handoff`
   - 支撑跨 session、跨 agent、跨 context window 的稳定交接
4. `Context Guarding`
   - 通过 scope、policy、verification 与 receipts 防止上下文漂移导致越界与误改

---

## 2. 核心裁决

### 2.1 不是靠 AI 遵守文档，而是靠运行时裁决

Polaris 的 ACGA 3 工厂模式必须采用零信任假设：

- LLM 不可信
- Agent 不可信
- LLM 写出的元数据不可信
- 候选 proposal 不可信

只有运行时基于真实代码、真实符号、真实文件写入、真实工具调用、真实测试结果重建并验证后的资产，才可以成为受控派生资产。

### 2.2 LLM 管 proposal，不管 truth

在 ACGA 3 工厂模式里：

- LLM / Agent 负责读取裁剪后的上下文
- LLM / Agent 负责提交 proposal
- LLM / Agent 负责申请受控动作

但以下内容不得由 LLM 直接维护为正式真相：

- graph truth
- projection map 的正式写入
- back-mapping 的正式写入
- state owner / effect owner 的生效裁决
- promotion / rollback 的最终裁决记录

### 2.3 Context is runtime-owned, not model-owned

ACGA 3 必须显式拒绝“由模型长期背着项目上下文前进”的做法。

系统应将以下内容从会话记忆中外移到运行时：

- task context
- graph / cell 邻接关系
- projection / back-mapping 资产
- runtime receipts
- verify / comparison 证据

LLM 看到的应该始终是：

- 当前步骤所需的最小上下文
- 被治理内核裁剪后的合法候选空间
- 可申请的受控动作

### 2.4 ACGA 3 的重点不是“更重治理”，而是“更强工厂能力”

ACGA 3 在 Polaris 中的价值，不是额外增加元数据负担，而是增强以下工厂能力：

- 更准确地找到应改范围
- 更安全地执行跨文件修改
- 更稳定地做符号级重构
- 更自动地做验证、恢复与再尝试
- 更可靠地进行候选版本比较与晋升

### 2.5 演化必须经过 promotion pipeline

ACGA 3 不允许“AI 觉得更好就直接生效”。

所有演化必须进入：

`Observe -> Diagnose -> Propose -> Project -> Verify -> Compare -> Promote / Reject -> Learn`

---

## 3. 运行时分层

### 3.1 Truth Store

职责：

- 维护 graph truth
- 维护 contract nucleus
- 维护 state owner / effects allowed
- 维护 projection map、fingerprint 与版本信息

特点：

- 权威、可审计、可回滚
- 不由 LLM 直接写入

### 3.2 Derived Indexes

职责：

- descriptor
- embedding / semantic halo
- symbol index
- co-change 图
- failure 共现图

特点：

- 可重建
- 即便错误，也不应直接污染真相

### 3.3 Runtime Receipts

职责：

- 记录 task/session/llm_request/tool_call/file_write/test_result 到 cell 的归因

特点：

- 是恢复、回放、跨 session 继续工作的底座

### 3.4 Policy / Validation Engine

职责：

- 越界写入检查
- effect 权限检查
- 重复 state owner 检查
- contract 闭合检查
- verification gap 检查

特点：

- 所有正式生效动作都必须走这里

---

## 4. 同一个治理内核，两种运行模式

### 4.1 内嵌模式

适用于：

- 开发
- 单机
- 低延迟场景

特征：

- 作为 Polaris 进程内模块运行
- 代码改动与元数据更新更容易做成单机原子流程

### 4.2 守护进程模式

适用于：

- 多 Agent
- 跨 session 持续运行
- 外部 IDE / CLI / agent 接入
- 统一审计与统一状态管理

特征：

- 起本地守护进程
- Polaris 和各 Agent 通过本地 API 或 MCP 访问

### 4.3 推荐实施顺序

Phase A

- 先做 Polaris 内部模块
- 但严格按服务接口设计

Phase B

- 挂到本地 Socket / HTTP
- 形成单机守护进程

Phase C

- 再加 MCP northbound 接口
- 供 LLM / Agent / IDE 使用

---

## 5. 必要运行时组件

### 5.0 这些组件共同承担上下文卸载

本节所有运行时组件，并不只是为了“多一层治理”，而是共同承担 ACGA 3 的上下文负担卸载目标：

- `Context Capture`
- `Context Slicing`
- `Context Handoff`
- `Context Guarding`

换句话说，它们的共同职责是替 AI/Agent 背项目，而不是让模型持续背着整个项目世界工作。

### 5.1 Context Resolver

作用：

- 基于 graph、cell、descriptor、AST、搜索结果，为 LLM 提供最小但正确的编码上下文

### 5.2 Change Scope Resolver

作用：

- 把“我想修什么/改什么”映射为：
  - 目标文件
  - 目标符号
  - 目标 Cell
  - 允许 effect 范围

### 5.3 Safe Edit Orchestrator

作用：

- 接收 proposal
- 执行 edit
- 收集 diff / write receipt / tool receipt
- 拒绝越权写入与非法 effect

### 5.4 Refactor Mapper

作用：

- 维护符号级 `Projection Map` 与 `Back-Mapping Index`
- 支撑 rename / extract / move / split / merge 等重构

### 5.5 Verification Loop

作用：

- 选择性测试
- smoke / lint / type / benchmark
- failure attribution
- stuck detection
- interruption recovery

### 5.6 Comparison & Promotion Controller

作用：

- 比较 champion 与 challenger
- 根据 Fitness Spec 决定是否 promotion
- 写入 promotion ledger / rollback ledger

### 5.7 Receipt Store

作用：

- 统一存储：
  - tool call receipt
  - file write receipt
  - test receipt
  - runtime refs
  - comparison receipt

---

## 6. 三层信任模型

### 6.1 Tier 0: 观察事实

包括：

- 真实文件内容
- AST / Tree-sitter 符号
- 实际工具调用
- 实际文件写入
- 实际 runtime / audit 事件
- 实际测试与 benchmark 结果

这是最底层可信输入。

### 6.2 Tier 1: 受控派生资产

包括：

- descriptor
- projection map
- back-mapping index
- runtime cell refs
- comparison receipt

这些资产必须由 runtime 根据 Tier 0 生成、刷新、校验或签发。

### 6.3 Tier 2: LLM 候选输出

包括：

- candidate Cell IR
- candidate refactor plan
- candidate architecture evolution
- candidate descriptor text
- confidence / rationale

这些只允许作为 proposal，不能直接成为真相或正式派生资产。

---

## 7. LLM/Agent 接口原则

### 7.1 只读资源

建议把对 LLM 的只读资源形态收敛为：

- `cell://platform.runtime.task_runtime`
- `projection://file/...`
- `task://.../context-pack`
- `verify://change-set/...`

### 7.2 受控工具

建议把对 LLM 的受控动作收敛为：

- `resolve_context`
- `lease_edit_scope`
- `validate_change_set`
- `map_diff_to_cells`
- `record_runtime_event`
- `request_projection_compile`

### 7.3 设计原则

- LLM 不直接读写真相层底层文件
- LLM 只消费裁剪后的上下文
- LLM 只申请受控动作
- LLM 不负责维护长期元数据世界

---

## 8. 自治等级

### L0

- 只读分析
- 不改代码

### L1

- 同 Cell 内局部修复与优化
- 不改 public contract
- 不改 state owner / effect owner

### L2

- 同 Cell 内多文件重构
- 允许跨符号 rename / extract / move
- contract 保持兼容

### L3

- 跨 Cell 重构
- 允许 projection 变化
- 允许 contract 演进，但必须走更严格 gate

### L4

- 架构演化
- 允许 Cell split / merge / capability 收敛
- 必须走最强 promotion / rollback / replay 验证

说明：

- “无人值守”不等于“没有门禁”
- 自治等级越高，越需要更强的 runtime proof，而不是更强的 prompt

---

## 9. 演化闭环

### 9.1 Observe

输入：

- 失败样本
- 回归热点
- 重复修改区域
- 性能瓶颈
- 高成本路径

### 9.2 Diagnose

判断问题属于：

- 算法问题
- 实现问题
- 重构机会
- 架构边界问题

### 9.3 Propose

由 LLM / Agent 生成候选：

- implementation proposal
- refactor proposal
- architecture evolution proposal

### 9.4 Project

将候选投影为：

- 真实代码变更
- projection map
- back-mapping index

### 9.5 Verify

验证：

- correctness
- regression
- benchmark
- state/effect 合规
- import fence
- replay / recovery

### 9.6 Compare

比较：

- champion vs challenger
- correctness
- performance
- cost
- stability
- maintainability

### 9.7 Promote / Reject

输出：

- promotion ledger
- rollback ledger
- evidence bundle

### 9.8 Learn

沉淀到：

- descriptor / runtime refs / evidence / heuristics

注意：

- 学到的是受控派生资产
- 不是把 LLM 幻想结果直接写回真相

---

## 10. 当前诚实边界

当前 Polaris 已具备部分相关基础：

- PM / Director / QA 工程闭环
- workflow runtime / audit / events / background execution
- context catalog
- factory pipeline 中的 projection / reprojection / back-mapping 原型

但以下内容仍属于目标态：

- 统一的 Autonomous Coding Control Runtime
- 统一的 promotion / rollback ledger
- 全链路 runtime cell refs
- 面向无人值守软件工厂的 ACGA 3 完整控制面

因此，当前更准确的表述应是：

> Polaris 正在从“可治理的自动化开发指挥台”向“受控自演化的软件工厂控制面”演进。

---

## 11. 配套文档

- [ACGA 3 Factory Positioning](../../ACGA3_FACTORY_POSITIONING.md)
- [Backend ACGA 3 RFC](../../src/backend/docs/ACGA_3.0_RFC.md)
