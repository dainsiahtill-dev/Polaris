# Cell Evolution Rollout Checklist

本清单用于把 `docs/CELL_EVOLUTION_ARCHITECTURE_SPEC.md` 从设计说明推进到可执行治理资产。

原则只有一句：

`液态发现，固态生效。`

含义是：

- 语义空间负责发现、聚类、排序、提出候选
- Graph、Manifest、Contract、Harness 和 CI 负责批准、拒绝或回滚

---

## 1. 前置门禁

在启动任何 Cell Evolution 实现前，必须先确认：

1. `AGENTS.md`、`docs/FINAL_SPEC.md`、`docs/ACGA_2.0_PRINCIPLES.md` 已被视为上位裁决。
2. 团队明确接受 `Graph` 是唯一架构真相，向量索引只能做发现层。
3. `docs/graph/**` 与 `workspace/meta/context_catalog/**` 已被明确区分为真相资产与派生资产。
4. 允许落地 Proposal 和 Decision Log，但不允许自动改写主线 Graph 真相。
5. 目标仓库的 UTF-8、effect、state owner、import fence 基线已存在最低可执行检查。

如果以上任一项不满足，不应进入实现阶段。

---

## 2. Phase 0: 治理资产入库

目标：

- 先把 Proposal、Decision Log、Rollout Checklist 作为治理资产落地

必备产物：

- `docs/CELL_EVOLUTION_ARCHITECTURE_SPEC.md`
- `docs/governance/schemas/cell-evolution-proposal.schema.yaml`
- `docs/governance/schemas/cell-evolution-decision-log.schema.yaml`
- 本清单

退出条件：

1. 架构说明书与现有 `Graph First` 裁决不冲突。
2. Proposal schema 能表达 merge/split/boundary tightening 三类提案。
3. Decision Log schema 能表达批准、拒绝、替代、回滚四类决策。

硬失败条件：

- 任何文档把向量索引写成架构真相
- 任何 schema 允许 Proposal 直接生效而不经过治理裁决

---

## 3. Phase 1: 信号采集

目标：

- 把“感觉上的耦合”变成结构化输入信号

必须采集的信号：

1. AST digest
2. import graph
3. call graph
4. state read/write paths
5. effect receipts
6. runtime failure clusters
7. descriptor drift
8. targeted test failures

退出条件：

1. 每个信号都能生成稳定、可重放的 digest。
2. 采集过程不改写业务代码。
3. 所有文本 I/O 显式 UTF-8。

硬失败条件：

- 采集器通过隐式运行目标代码来“猜测”副作用
- 采集结果无法关联到具体文件、契约或 receipt

---

## 4. Phase 2: Descriptor 与多通道向量

目标：

- 为每个候选 Cell 构建高维语义表示

最低要求：

1. `purpose_vector`
2. `contract_vector`
3. `state_vector`
4. `effect_vector`
5. `failure_vector`
6. `evolution_vector`

退出条件：

1. Embedding 输入基于 descriptor，而不是源码原文。
2. 每次索引刷新都有 `embedding_runtime_fingerprint`。
3. 每次刷新都有 receipt / trace。

硬失败条件：

- 直接对源码全文做 embedding 并拿来裁决边界
- semantic index 覆盖 `docs/graph/**`

---

## 5. Phase 3: Proposal Engine

目标：

- 让系统输出“候选边界建议”，而不是输出未经审计的重组结果

最低支持的 Proposal 类型：

1. `merge_candidate`
2. `split_candidate`
3. `boundary_tightening_candidate`

每个 Proposal 必须包含：

1. 证据
2. 风险
3. 候选 `owned_paths`
4. 候选 `depends_on`
5. 候选 `state_owners`
6. 候选 `effects_allowed`
7. 候选 graph 更新点
8. 验证计划

退出条件：

1. 所有 Proposal 满足 schema。
2. Proposal 可回溯到输入 fingerprint。
3. Proposal 只写入派生目录，不写 Graph 真相目录。

硬失败条件：

- Proposal 直接改写 `docs/graph/catalog/cells.yaml`
- Proposal 直接执行主线 import rewrite

---

## 6. Phase 4: Universal Harness

目标：

- 证明“候选 Cell”在隔离上下文中能独立生存

最低能力：

1. isolated DI container
2. contract-based mock neighbors
3. effect sandbox
4. deterministic fixtures
5. contract verdict
6. effect declared-only verdict
7. state owner uniqueness verdict

退出条件：

1. Harness 能运行单 Cell contract test。
2. Harness 能在不触达真实外部依赖的前提下跑邻居协作。
3. Harness 输出结构化 verdict。

硬失败条件：

- Harness 依赖真实生产路径或真实外部服务
- Harness 无法捕获未声明 effect

---

## 7. Phase 5: Governance Integration

目标：

- 把 Proposal 从“好主意”升级为“可审计候选”

必须接入的门禁：

1. `owned_paths` 冲突检查
2. `state_owners` 唯一性检查
3. public/internal import fence
4. effect declared-only 检查
5. graph consistency 检查
6. descriptor freshness 检查

退出条件：

1. Proposal 在 governance gate 里有明确通过或拒绝结果。
2. 所有拒绝都有结构化原因。
3. Decision Log 以 append-only 方式写入。

硬失败条件：

- 用自然语言评论替代结构化裁决
- 拒绝或批准没有证据引用

---

## 8. Phase 6: 受控落地

目标：

- 只把通过 Harness 和 Governance 的 Proposal 变成正式变更

必须遵守：

1. 先更新 `cell.yaml` / `cells.yaml` / subgraph 草案
2. 再执行受控 codemod 或手工重构
3. 再跑 targeted tests 与整链回归
4. 最后写入 Decision Log

退出条件：

1. Graph 真相、物理代码、验证证据三者一致。
2. 变更可回滚。
3. Compat/shim 删除条件被明确记录。

硬失败条件：

- 代码先改了，但 Graph 没更新
- Graph 先宣称完成，但代码和测试未跟上

---

## 9. 首批试点范围

推荐先做以下目标簇，不要一开始全仓演化：

1. `runtime.state_owner`
2. `runtime.projection`
3. `audit.evidence`
4. `archive.run_archive`
5. `archive.task_snapshot_archive`
6. `archive.factory_archive`
7. `policy.workspace_guard`

原因：

这些能力都同时具备：

1. 明确状态边界
2. 明确副作用边界
3. 明确历史 evidence 价值
4. 高治理收益

说明：

- `compatibility.legacy_bridge` 曾用于迁移过渡，但已从当前活动 graph 退场，不再作为试点目标簇。

不建议首批试点：

1. 大而模糊的 `roles.runtime`
2. 大而活跃的 `llm.control_plane`
3. 任意需要大规模 import rewrite 的热点簇

---

## 10. 运行模式建议

### 10.1 初期模式

- 审计模式
- 只生成 Proposal
- 不自动批准
- 不自动执行 codemod

### 10.2 中期模式

- 审计模式 + Harness
- 允许自动拒绝明显违规 Proposal
- 仍不允许自动生效

### 10.3 后期模式

- 审计模式 + Harness + 受控 CI Gate
- 允许自动生成 patch preview
- 仍要求人工或显式策略批准后才更新 Graph 真相

---

## 11. 成功指标

以下指标可作为 rollout 成败判断：

1. Proposal 的命中率高于人工拍脑袋拆分。
2. `owned_paths` 冲突数量下降。
3. `state_owners` 冲突数量下降。
4. 未声明副作用数量下降。
5. God File 的拆分回归率下降。
6. Graph 补全速度明显提高。
7. AI/Agent 的上下文装配命中率提高。

---

## 12. 一票否决项

出现以下任一情况，必须暂停 rollout：

1. 语义索引被当成架构真相使用。
2. 动态渲染源码目录进入主执行路径。
3. 自动 import 重写直接写入主线而无 preview。
4. Decision Log 缺失或不可追溯。
5. Harness 无法证明 Cell 独立运行能力。
6. Graph 更新与代码事实持续脱节。

---

## 13. 交付定义

Cell Evolution 只有同时满足以下条件，才可宣称“进入可执行阶段”：

1. Proposal schema 已稳定。
2. Decision Log schema 已稳定。
3. 派生资产目录与 Graph 真相目录已严格分离。
4. 至少一个试点簇完成从 Proposal 到 Graph 更新的闭环。
5. 试点过程具备完整 receipt、decision log 和回归证据。
