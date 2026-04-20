# ACGA 3.0 草案（RFC）

- 状态: Draft / Proposed
- 生效日期: 未生效
- 适用范围: Polaris 后端 `src/backend`
- 角色: 对 `ACGA 2.0` 与 `CELL_EVOLUTION_ARCHITECTURE_SPEC.md` 的正式化收敛提案
- 强制级别: 在正式批准前，不得替代 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`

> 本文目标不是把新概念“升级命名”后直接宣布完成，而是把 `Cell Evolution` 中真正有价值的部分收敛为可执行、可审计、可验证的 `ACGA 3.0` 候选标准。

---

## 0. 文档定位

### 0.1 本草案试图升级什么

`ACGA 2.0` 已经明确：

- Graph First
- Cell First
- Graph-Constrained Semantic
- Descriptor over Raw Source
- KernelOne as Agent/AI Operating Substrate

但 `ACGA 2.0` 仍主要聚焦于：

1. Graph 真相
2. Cell 边界与治理
3. Descriptor / Context Pack / Verify Pack
4. KernelOne 与 Effect Plane

`ACGA 3.0` 候选提案要补齐的，是 2.0 尚未正式闭环的三段：

1. **Cell IR**：让 Cell 成为 Polaris 内部的正式工作对象，而不只是治理清单
2. **Projection Map**：让“Cell -> 传统代码/测试/配置/运行时载体”的映射成为一等资产
3. **Runtime Back-Mapping**：让“文件修改 / 工具调用 / 运行时事件 / 测试结果 -> Cell”成为正式能力

### 0.2 本草案不升级什么

`ACGA 3.0` **不**意味着：

- 放弃 `Graph` 作为唯一架构真相
- 允许向量空间替代边界裁决
- 把所有能力塞进 `KernelOne`
- 把 Cell 强行暴露给最终用户作为新的目录规范
- 把目标态写成当前已完成事实

### 0.3 与现有规范的裁决顺序

若发生冲突，优先级保持为：

1. `AGENTS.md`
2. `docs/graph/catalog/cells.yaml` 与 `docs/graph/subgraphs/*.yaml`
3. `docs/FINAL_SPEC.md`
4. `docs/ACGA_2.0_PRINCIPLES.md`
5. 本文

结论：

- 本文只能提出 `ACGA 3.0` 候选标准
- 在正式批准前，本文不能创建第二套真相
- 本文不得弱化现有边界、门禁与零信任规则

---

## 1. 核心裁决

### 1.1 ACGA 3.0 的一句话定义

`ACGA 3.0 = ACGA 2.0 + Cell IR + Projection Map + Runtime Back-Mapping`

它的目标不是替换 2.0，而是把 2.0 从“图约束的语义检索与治理体系”推进到“可长期维护的内部架构操作系统”。

### 1.2 Graph 仍然是唯一架构真相

`ACGA 3.0` 维持 2.0 的核心裁决不变：

- `docs/graph/**` 是唯一架构真相
- `cell.yaml` / `cells.yaml` / `subgraph.yaml` 决定正式边界
- Descriptor、Cell IR、Projection Map、Back-Mapping Index 都是派生资产

它们可以：

- 帮助检索
- 帮助规划
- 帮助执行
- 帮助恢复
- 帮助审计

但它们不能：

- 反向覆盖 Graph
- 自动宣布新 Cell 正式存在
- 自动扩大状态写权限或 effect 半径

### 1.3 Cell 在 ACGA 3.0 中的正式定位

在 `ACGA 3.0` 中：

- `Cell` 仍是最小自治能力边界
- `Cell IR` 是 Cell 的内部工作表示
- `Contract Nucleus` 是正式生效边界
- `Physical / Runtime / Verification Projection` 是投影结果

更严格地说：

- **Graph 真相** 决定 Cell 的合法存在
- **Cell IR** 决定 Polaris 内部如何理解、生成、修改和恢复 Cell
- **Projection** 决定用户最终看见的传统工程

### 1.4 Projection Map 与 Back-Mapping 是一等资产

`ACGA 3.0` 的关键升级是承认：

- 没有 `Projection Map`，投影只是一次性生成
- 没有 `Back-Mapping`，运行时与人工修改无法回到 Cell 视角

因此，`Projection Map` 与 `Back-Mapping Index` 必须成为治理资产，而不是调试附产物。

### 1.5 平台 Cell 与目标项目 Cell 必须分命名空间

若 Polaris 自身与目标项目同时采用 Cell 模型，则必须区分：

- `platform.*`：Polaris 自身 Cell
- `target.*`：目标项目 Cell

该隔离必须从“文档建议”升级为“正式门禁”，以避免：

- 检索污染
- 审计归因污染
- 运行时事件归因混乱
- target 投影产物与 platform 真相资产互相污染

### 1.6 Runtime 证据必须能追溯到 Cell

`ACGA 3.0` 不再接受“运行时只知道 task/file，不知道 cell”的状态长期存在。

关键事件、receipt 与审计证据应尽量具备：

- `cell_id` 或 `cell_ids`
- `projection_run_id`
- `projection_ref`
- `back_mapping_ref`
- `task_id / run_id / trace_id`

允许过渡期通过 `refs` 间接关联，但不允许长期缺失 Cell 归因能力。

### 1.7 KernelOne 的角色不升级为“架构真相层”

`KernelOne` 仍然是 Agent/AI OS 底座，不升级为：

- Graph 真相层
- Cell 粒态真相层
- Projection 策略拥有者
- Polaris 业务状态拥有者

它在 `ACGA 3.0` 中增强的，应只是技术底座职责，而不是业务裁决权。

---

## 2. 术语裁决

### 2.1 规范性术语

`ACGA 3.0` 的规范性术语固定为：

- `Cell IR`
- `Semantic Candidate`
- `Contract Nucleus`
- `Projection Map`
- `Back-Mapping Index`
- `Runtime Cell Refs`

### 2.2 非规范性比喻术语

以下术语允许作为解释性术语使用，但不作为治理 schema 的正式字段名：

- `Wave`
- `Particle`
- `Semantic Halo`
- `Collapse`
- `Resonance`
- `Repulsion`

原因：

- 这些术语在概念解释上很强
- 但作为正式门禁与 schema 词汇时，歧义偏高
- `ACGA 3.0` 应优先使用可执行、可校验、机器友好的命名

---

## 3. 四层执行模型

### 3.1 Semantic Discovery Plane

职责：

- 从 graph、contract、descriptor、代码摘要、事件与日志中提取候选语义信号
- 形成 `Semantic Candidate`
- 进行 Graph 先约束、语义后排序的检索

主要资产：

- `descriptor.pack.json`
- `workspace/meta/context_catalog/descriptors.json`
- 语义索引状态

限制：

- 不得宣布正式边界
- 不得直接写业务状态
- 不得绕过 Graph 做越权扩展

### 3.2 Truth and Governance Plane

职责：

- 维护 Graph 真相
- 维护正式 `cell.yaml`
- 裁决 `depends_on`、`state_owners`、`effects_allowed`
- 执行 schema / compatibility / import fence / ownership 门禁

主要资产：

- `docs/graph/**`
- `polaris/cells/**/cell.yaml`
- `docs/governance/**`

### 3.3 Projection Plane

职责：

- 将 `Cell IR` 输出为传统代码结构
- 产出 `Projection Map`
- 记录 `cell -> files`、`file -> cells`、`test -> cells`

主要资产：

- `cell_ir.json` 或等价 schema 资产
- `projection_map.json`
- 生成出的传统工程文件

限制：

- 不得让用户默认承担 Polaris 内部抽象负担
- 不得让 Projection 成为不可逆的一次性过程

### 3.4 Runtime and Audit Plane

职责：

- 把任务、工具调用、文件修改、测试结果、审计证据映射回 Cell
- 产出 `Back-Mapping Index`
- 为恢复、继续执行、事故归因提供稳定支撑

主要资产：

- `back_mapping_index.json`
- runtime event refs
- audit receipt refs

---

## 4. ACGA 3.0 新增资产要求

### 4.1 必须存在的真相资产

以下资产仍然是正式真相或正式治理资产：

- `docs/graph/catalog/cells.yaml`
- `docs/graph/subgraphs/*.yaml`
- `polaris/cells/**/cell.yaml`
- `docs/governance/**`

### 4.2 必须存在的派生资产

若宣称进入 `ACGA 3.0`，至少应能在目标链路上稳定产出：

1. `Descriptor`
2. `Cell IR Snapshot`
3. `Projection Map`
4. `Back-Mapping Index`
5. `Runtime Cell Refs`

### 4.3 Projection Map 最小字段

`Projection Map` 至少应能回答：

- `file -> cell_ids`
- `cell_id -> files`
- `file -> contracts`
- `test_case -> cell_ids`
- `runtime ref -> cell_ids` 或可追溯锚点

### 4.4 Back-Mapping Index 最小字段

`Back-Mapping Index` 至少应能回答：

- `file_path`
- `qualified_symbol`
- `line_start / line_end`
- `cell_ids`
- `syntax_source`
- `mapping_strategy`

### 4.5 Runtime Cell Refs 最小字段

关键 runtime / audit 事件应尽量具备：

- `task_id`
- `run_id`
- `trace_id`
- `cell_id` 或 `cell_ids`
- `projection_ref`
- `artifact_ref`

---

## 5. KernelOne 在 ACGA 3.0 中的职责

### 5.1 允许增强的方向

`KernelOne` 可以承接以下 `ACGA 3.0` 技术底座能力：

- embedding port
- syntax parsing / AST / Tree-sitter contract
- effect receipt / trace / timeout
- safe process execution
- artifact-safe filesystem primitives
- runtime refs / event envelope primitives
- projection / back-mapping 所需的中立技术 helper

### 5.2 明确禁止承接的方向

以下内容仍不得进入 `KernelOne`：

- `cell.yaml` 真相裁决
- Graph 真相
- Polaris 业务 `state_owners`
- Projection 策略与目标业务模板裁决
- target 项目业务 Cell 生命周期
- Polaris 业务 query / result / event

### 5.3 正确关系

正确关系仍为：

`Cell / workflow -> effect port -> KernelOne technical contract -> infrastructure adapter`

`ACGA 3.0` 只增强这条链路的观测性与可回映射性，不改变其权力分配。

---

## 6. 治理门禁升级

若宣称进入 `ACGA 3.0`，除 `ACGA 2.0` 门禁外，还必须补齐以下门禁：

1. `Cell IR` schema 校验
2. `Projection Map` schema 校验
3. `Back-Mapping Index` schema 校验
4. `Projection Map` freshness 校验
5. `Back-Mapping Index` freshness 校验
6. runtime / audit `cell refs` 覆盖率校验
7. `platform.*` / `target.*` 命名空间隔离校验
8. target runtime execution safety gate
9. projection -> verification -> refresh back-mapping 的整链回归测试

### 6.1 Target Runtime Execution Safety Gate

`ACGA 3.0` 必须承认一个现实问题：

- target 项目需要被运行与验证
- 但运行目标项目不能破坏 Polaris 自身安全边界

因此必须引入正式门禁，定义：

- 哪些 target module 可以执行
- 哪些执行形态允许进入验证链
- 如何以白名单或签名方式隔离 `platform.*` 与 `target.*`

### 6.2 Runtime Cell Refs Coverage Gate

关键链路事件至少应实现可量化覆盖，例如：

- task lifecycle
- tool call / tool result
- file write receipt
- verification passed / failed
- archive receipt

若这些事件长期不能回到 Cell，`ACGA 3.0` 只能算半落地。

---

## 7. 正式发布条件

本文明确区分：

- `ACGA 3.0 Draft`
- `ACGA 3.0 Beta`
- `ACGA 3.0 Final`

### 7.1 Draft 条件

满足以下条件即可称为 `Draft`：

- 术语统一
- 文档定稿为 RFC
- 至少存在一条实验链路产出 `Cell IR + Projection Map + Back-Mapping Index`

### 7.2 Beta 条件

满足以下条件方可称为 `Beta`：

- `Projection Map` 与 `Back-Mapping Index` 有正式 schema
- runtime / audit 已开始携带稳定 `cell refs`
- 至少一条样板链路可持续重放
- governance 已接入对应校验门禁

### 7.3 Final 条件

满足以下条件方可称为 `Final`：

1. Graph 真相、Descriptor、Projection、Back-Mapping、Runtime 审计形成闭环
2. `platform.*` / `target.*` 命名空间隔离已成为硬门禁
3. target runtime execution safety gate 已落地并通过回归测试
4. 至少一条端到端链路稳定支持：
   - 需求分解
   - Cell IR 生成
   - 传统代码投影
   - 测试验证
   - runtime back-mapping
   - selective reprojection
   - 审计恢复
5. 关键事件的 `cell refs` 覆盖达到可接受阈值

---

## 8. 当前诚实边界

截至本文起草时，仓库中已经出现 `ACGA 3.0` 候选要素的局部实现，例如：

- `context.catalog` 对 descriptor / context catalog 的收口
- `factory.pipeline` 对 projection / reprojection / back-mapping 的实验实现
- `KernelOne` 对 effect / trace / process / fs / embedding port 的底座支撑

但这些事实**不等于**：

- `ACGA 3.0` 已正式落地
- 全仓已经具备统一 `Cell IR`
- runtime/audit 已全面具备 `cell refs`
- projection / back-mapping 已完成平台级收口

因此，任何对外表述都必须使用：

- `Draft`
- `RFC`
- `实验链路`
- `局部落地`

而不能直接写成：

- `ACGA 3.0 已完成`
- `ACGA 3.0 已全仓启用`

---

## 9. 推荐迁移顺序

### Phase 0: 术语与 schema 草案

- 固化 `Cell IR / Projection Map / Back-Mapping Index / Runtime Cell Refs` 术语
- 定义最小 schema

### Phase 1: Projection Map 资产化

- 把 `projection_map.json` 升级为正式治理资产
- 引入 schema / freshness 校验

### Phase 2: Back-Mapping 平台化

- 把符号级映射从实验实现收口到正式能力
- 接入 AST / Tree-sitter 契约

### Phase 3: Runtime Cell Refs

- 为关键 runtime / audit 事件补齐 `cell refs`
- 建立 coverage gate

### Phase 4: Target Runtime Safety

- 建立 target module 执行白名单与命名空间隔离门禁
- 让投影项目验证成为正式受控链路

### Phase 5: 端到端闭环验收

- 完成至少一条从需求到恢复的完整样板链路
- 达到 `Beta` / `Final` 发布条件

---

## 10. 一句话总结

`ACGA 3.0` 的价值，不在于引入更华丽的新词，而在于把 Polaris 从“图约束的 Cell 治理系统”推进为“以 Cell IR 为内部对象、以 Projection Map 为交付桥梁、以 Runtime Back-Mapping 为长期维护支点”的完整工程闭环。

在正式批准前，最诚实的表述应是：

> `ACGA 3.0` 是 Polaris 的下一代候选架构标准；它已经具备明确方向与局部原型，但仍需通过 schema、门禁、runtime refs 与端到端回归验证，才能成为正式版本。
