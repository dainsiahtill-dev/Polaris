1. 最终裁决
1.1 必须坚持的硬规则

Agent 执行入口统一到 `docs/AGENT_ARCHITECTURE_STANDARD.md`。  
该入口用于约束默认阅读顺序、门禁执行和文档同步；若冲突，以 `AGENTS.md` 为准。

Graph 是唯一架构真相，Vector 只是发现层。
架构边界不能由向量检索推断出来，只能由 docs/graph/catalog/cells.yaml、docs/graph/subgraphs/*.yaml、以及各 Cell 的 cell.yaml 共同声明；向量检索只允许在 Graph 先约束出的候选集合内排序。

六个规范根目录继续有效。
物理实现必须收敛到 bootstrap / delivery / application / domain / kernelone / infrastructure，Cell 不是替代这些目录，而是叠加在这些目录之上的能力边界。

KernelOne 不是普通技术工具箱，而是面向 AI/Agent 的类 Linux 系统与基础设施层；但它依然不能吸收 Polaris 业务语义。
archive.*、runtime.state_owner、业务 query/result/event、workspace guard、migration status 这类东西都不能塞进 KernelOne；KernelOne 应优先承载 fs/storage/db/ws/stream/events/message_bus/trace/effect/llm/process/tool_runtime/agent_runtime/context_runtime/context_compaction/task_graph 这类 Agent-OS 技术子系统。

Embedding 禁止直接吃源码。
必须先生成结构化 Descriptor，再对 Descriptor 的“可检索语义文本”做 embedding。源码只能作为生成 Descriptor 的输入，不是向量模型的直接语料。

跨 Cell 依赖只允许 public Contract + DI。
不能直接跨 Cell 依赖对方 internal/，测试也必须通过 fake/mock contract 保证独立运行。

Cell 开发必须先复用既有能力。
所有 Cell 新开发先复用其他 Cell 的公开能力；存在缺口时先补齐既有能力边界，再评估新增 Cell。

所有开发必须以 KernelOne 为技术底座。
新开发的技术能力、I/O 和副作用链路必须通过 KernelOne 契约接入，禁止绕过 KernelOne 直接耦合底层实现。

语义索引写入本身是 effect，必须审计。
Descriptor 生成、Embedding、Index upsert/delete 都要留下 receipt / trace。

1.2 两个必须纠偏的点

前面讨论里有两个思路现在要明确否掉：

不要再引入 .acga/graph 作为第二套图谱真相目录。 当前规范已经把 docs/graph/** 定成正式图谱资产，再来一套 .acga/graph 只会制造双真相。

不要把新自动化继续放进 scripts/。 scripts/ 在当前规范里属于旧根目录，不能再承载新功能主实现。新 CLI 入口应放在 `polaris/delivery/cli/`，核心逻辑落在 `polaris/application/`、`polaris/cells/` 或 `polaris/kernelone/`。

1.3 当前仓库的物理承载方式

对于这个仓库，新的目标架构不直接落在 backend 顶层，而是统一落在 `polaris/` 下，避免与当前仍在服役的 `app/`、`core/`、`api/`、`scripts/` 等旧根目录直接冲突。

因此，本文后续凡是提到：

- `bootstrap/`，都应读作 `polaris/bootstrap/`
- `delivery/`，都应读作 `polaris/delivery/`
- `application/`，都应读作 `polaris/application/`
- `domain/`，都应读作 `polaris/domain/`
- `kernelone/`，都应读作 `polaris/kernelone/`
- `infrastructure/`，都应读作 `polaris/infrastructure/`
- `cells/`，都应读作 `polaris/cells/`
- `tests/`，都应读作 `polaris/tests/`

但 `docs/graph/**`、`docs/governance/**`、`docs/templates/**` 仍然保留在仓库顶层 `docs/` 下，继续作为跨迁移阶段共享的架构真相资产。

2. 最终形态：一套“分层物理结构 + 能力图谱 + 图约束语义检索”的统一架构

这套方案的核心不是“只保留目录”或者“只保留 Cell”，而是三层同时成立：

2.1 物理分层
bootstrap/        组装根、配置、生命周期、DI
delivery/         HTTP / WS / CLI 入口
application/      用例、事务边界、工作流编排
domain/           业务规则、实体、值对象、领域策略
kernelone/        Agent/AI 类 Linux 运行时底座
infrastructure/   具体 adapter、持久化、消息、遥测、LLM/FS/DB 后端
cells/            Cell 的 manifest / contracts / tests / generated packs
docs/graph/       Graph 真相资产
tests/            架构 / 集成 / 子图 / 契约测试
workspace/meta/context_catalog/
                  Descriptor 汇总、Index 状态、LanceDB 本地索引
2.2 逻辑能力边界

Cell 是最小自治能力边界。

一个 Cell 可以拥有多个规范根目录中的实现文件，通过 owned_paths 声明。

cells/<domain>/<name>/ 负责承载该 Cell 的 manifest、public contracts、README、generated packs、tests。

application/、domain/、delivery/、infrastructure/ 中的实际代码仍按分层放置，但归属由 Cell 通过 owned_paths 接管。

这正好把“物理目录为编译和分层服务”与“逻辑 Cell 为能力和授权服务”分开了。

2.3 AI/Agent 发现与执行

Graph 决定 AI 能看哪些 Cell。

Descriptor 决定 AI 如何按语义发现候选 Cell。

Context Pack 决定 AI 拿到候选后应该读什么。

Verify Pack 决定改完代码后该验证什么。

这和 ACGA v1.0 的 Context Plane 一脉相承，但加入了 ACGA 2.0 的 Graph-Constrained Semantic 检索。

3. 资产布局：哪些是手工真相，哪些是机器生成

这里必须分清楚，否则后面一定漂。

3.1 手工维护、纳入评审的真相资产
docs/graph/catalog/cells.yaml
docs/graph/subgraphs/*.yaml
docs/governance/schemas/*.yaml
docs/governance/ci/fitness-rules.yaml
cells/*/*/cell.yaml
cells/*/*/README.agent.md
cells/*/*/public/contracts/**

这些是人写、机器校验的。

3.2 机器生成、可重建的派生资产
cells/*/*/generated/context.pack.json
cells/*/*/generated/impact.pack.json
cells/*/*/generated/verify.pack.json
cells/*/*/generated/descriptor.pack.json
workspace/meta/context_catalog/descriptors.json
workspace/meta/context_catalog/index_state.json
workspace/meta/context_catalog/lancedb/**

这些是机器生成、CI 校验新鲜度的。
我的建议是：每个 Cell 的 generated/*.pack.json 和 descriptor.pack.json 进仓库、可审查；聚合 catalog 和 LanceDB 索引不进仓库，可重建。

4. Graph Plane：怎么把“图是真相”真正落地
4.1 真相分工

最稳妥的做法是：

cells/*/*/cell.yaml：Cell 局部真相
负责 owned_paths / ports / depends_on / state_owners / effects_allowed / verify_targets

docs/graph/subgraphs/*.yaml：协作面真相
负责 entry_cells / cells / exit_cells / critical_contracts / state_boundaries / verify_targets

docs/graph/catalog/cells.yaml：全局归一化目录
建议由工具根据 Cell manifest 和 subgraph 资产编译出来，并提交到仓库作为全局可审查快照

这能避免两类错误：
一类是只靠局部 cell.yaml，AI 读全局图太慢；另一类是只靠全局 cells.yaml，局部字段和代码现实脱节。

4.2 cell.yaml 最小必备字段

最小模板建议固定为：

cell_id: context.engine
kind: capability
owner: architecture-team
public: true
domain: context

owned_paths:
  - cells/context/engine/**
  - application/services/context/**
  - tests/context/**

state_owners:
  - workspace/meta/context_catalog/index_state.json

inbound_ports:
  - name: resolve_context
    mode: query
    contract: ResolveContextQueryV1

outbound_ports:
  - name: read_graph
    mode: effect
    contract: GraphReadEffectV1
  - name: search_descriptor_index
    mode: effect
    contract: SemanticIndexSearchEffectV1
  - name: write_context_catalog
    mode: effect
    contract: ContextCatalogWriteEffectV1

depends_on:
  - audit.evidence
  - policy.workspace_guard

effects_allowed:
  - fs.read:docs/graph/**
  - fs.read:cells/**/generated/*.json
  - fs.read:workspace/meta/context_catalog/**
  - fs.write:workspace/meta/context_catalog/**

verify_targets:
  - tests/context/test_context_engine_contracts.py
  - tests/context/test_graph_constrained_search.py
4.3 subgraph.yaml 的作用

子图不是画图文件，而是工作流和验证单元。
例如 storage_archive_pipeline 一旦真的落地，就要声明入口、出口、关键 Contract、状态边界和验证目标，CI 和 AI 都读它。Polaris 最终规范已经把 storage/archive 子域明确为第一批必须标准化的高副作用参考子域。

5. Capability Plane：Cell 如何和六个根目录共存

这是很多架构容易说不清的地方。我的最终建议是：

5.1 新 Cell 的标准骨架仍然放在 polaris/cells/
polaris/cells/
  <domain>/
    <name>/
      cell.yaml
      README.agent.md
      public/
        api.py
        contracts/
      internal/
      tests/
      generated/
5.2 但实现不要求全塞进 cells/

Cell 可以通过 owned_paths 合法拥有：

polaris/application/usecases/**

polaris/application/workflows/**

polaris/domain/services/**

polaris/delivery/http/routers/**

polaris/infrastructure/archive/**

也就是说：

目录解决分层

Cell 解决能力归属

Graph 解决系统理解

Vector 解决发现效率

5.3 跨 Cell 协作链路固定为

delivery -> application workflow/usecase -> cell public port -> internal logic -> outbound effect port -> kernelone technical contract -> infrastructure adapter

这个链路必须作为硬规则，不允许跳过 application 直接做业务编排，也不允许 delivery 直连具体 infrastructure，更不允许 KernelOne 反向吸收业务语义。

6. Context Plane：Descriptor、Packs、Semantic Search 怎么闭环

这里就是 ACGA 2.0 的核心。

6.1 不要一上来拆太多新 Cell

当前图谱里已经有 context.engine，所以第一阶段不要先新建一堆 context.catalog / context.pack_builder 公共 Cell。
最务实的做法是：

先把 context.engine 扩成 Context Plane 门面 Cell

在 context.engine/internal/ 里实现四个内部模块：

graph_resolver

descriptor_pipeline

semantic_search

pack_builder

等这块真正热起来，再考虑拆成：

context.catalog

context.pack_builder

context.engine（编排门面）

这符合“先能跑，再精拆”的迁移原则，也不会和当前 graph 事实冲突。

6.2 Descriptor 不是 Context Pack

必须把这两个东西分开：

Descriptor：给检索用，短、小、可嵌入、强分类

Context Pack：给工作用，完整但仍是最小必需上下文

6.3 Descriptor 生成要走两阶段

不要直接把整个 Cell 源码喂给 LLM，然后赌输出稳定。
最稳的工程化做法是：

Stage A：静态提取器

读取 cell.yaml

读取 README.agent.md

解析 public/contracts

提取 owned_paths 内公开函数/类签名、docstring、import 依赖、effect 调用线索

形成一个 machine digest

Stage B：LLM 规范化器

输入 machine digest，而不是全仓源码

输出严格符合 schema 的 descriptor.pack.json

temperature=0

JSON only

不允许输出代码片段

失败就重试/修正，不允许脏写

这样做的好处是：
Descriptor 既利用了 LLM 的抽象能力，又不会被源码噪音和 prompt 漂移拖垮。

6.4 descriptor.pack.json 最小字段

建议至少包含：

descriptor_version

cell_id

classification.kind

classification.domain

classification.subgraphs

capability_summary

when_to_use

when_not_to_use

public_contracts

dependencies

state_owners

effects_allowed

key_invariants

source_hash

graph_fingerprint

embedding_provider

embedding_model_name

embedding_device

embedding_runtime_fingerprint

generated_at

其中真正参与 embedding 的文本建议由固定模板拼出来，而不是直接把整个 JSON 原样向量化。模板可由以下字段组成：

cell_id + kind/domain + capability_summary + when_to_use + when_not_to_use + public_contracts semantic summary + dependencies semantic summary + key_invariants + effects summary

6.5 Context / Impact / Verify Pack 的职责边界

context.pack.json

这个 Cell 是干什么的

对外 Port 和 Contract

依赖哪些 Cell

拥有哪些状态 / effect

关键 owned_paths

常见调用方式

邻接 Cell

impact.pack.json

反向依赖的 Cell

受影响的 subgraph

受影响的 contract

受影响的状态路径 / effect 路径

风险等级

推荐最小测试集

verify.pack.json

必跑 unit / contract / integration / architecture tests

需要注入的 fake ports

必须验证的行为不变量

必须观察的副作用与 audit receipt

7. Semantic Index：首版用 LanceDB，但它只做“图约束后的排序器”
7.1 存储位置

首版直接用嵌入式 LanceDB，路径放在：

workspace/meta/context_catalog/lancedb/

不要新建 .acga/vector_index 这一类新体系路径。

7.2 Index 元数据必须带 Graph 信息

LanceDB 每条记录至少要带：

cell_id

domain

kind

subgraph_ids

public_contract_names

dependencies

state_owners

effects_allowed

source_hash

graph_fingerprint

descriptor_hash

updated_at

vector

因为 Graph Filter 必须发生在搜索前，索引表本身必须能 prefilter。

7.3 标准检索链路固定为
Intent
 -> Graph Filter
 -> Semantic Rank (stage-1)
 -> Rerank (optional)
 -> Neighbor Expansion
 -> Context Pack
 -> Verify Pack

细化一下就是：

Intent Parsing
把用户/Agent 意图解析成：

target_domain

target_subgraphs

expected_kind

contract_keywords

effect_scope

state_scope

Graph Filter
从 graph/catalog + subgraph 资产中过滤掉不可能的 Cell。
这一步必须先做，不能跳。

Semantic Rank
只在过滤后的候选集内做向量检索。

Optional Rerank
用 contract overlap、graph proximity、freshness 做二次排序。

Neighbor Expansion
只按 depends_on / calls / queries / owns_state 做 1-hop 扩张；workflow 最多 2-hop。

Context Pack + Verify Pack 装配

Fallback
如果语义层空集，只允许在 graph 资产、descriptor、cell_id、contract 名字里做 lexical fallback。
禁止 repo-wide blind scan，更禁止 grep 全仓后扔给 LLM。

7.4 一条硬规则

向量检索永远不能新建边界。
它只能在已有图谱的合法边界内“发现”，不能推断“这个 Cell 可能也应该依赖那个 Cell”。

8. Runtime Evidence：把“索引刷新”和“Agent 选型”都变成可追责事件

语义层一旦上线，没有证据就很难治理，所以必须把 Runtime Evidence 做起来。

8.1 谁拥有这块状态

第一阶段建议由现有 context.engine 暂时拥有：

workspace/meta/context_catalog/descriptors.json

workspace/meta/context_catalog/index_state.json

workspace/meta/context_catalog/lancedb/**

等 Context Plane 后续拆分时，再把这些 state owner 转给 context.catalog。

8.2 需要审计的事件

至少记录四类 receipt：

descriptor_generated

semantic_index_upserted

semantic_index_deleted

context_query_executed

每条 receipt 至少要带：

trace_id

cell_id

source_hash

descriptor_hash

graph_fingerprint

embedding_model

action

status

timestamp

这部分通过 audit.evidence 收口最合理，因为它本来就是当前 graph 里的公共 Cell，而且最终规范已经给了它高副作用写入权限。

9. Governance Plane：CI 要怎么卡，才不会腐化

你前面说得对：没有自动化，这套架构一定会烂掉。

9.1 最终推荐流水线
validate-manifests
build-system-graph
check-boundaries
check-contract-compat
generate-cell-packs
generate-descriptors
validate-descriptors
sync-context-catalog
sync-semantic-index
select-tests
run-targeted-tests
run-subgraph-integration-tests
publish-architecture-report

这和 ACGA v1.0 / Polaris v1.1 里的治理方向是一致的，只是把 Descriptor 和 Semantic Index 正式纳入了门禁。

9.2 必须新增的架构门禁

cell.yaml / subgraph.yaml schema 校验

import fence：跨 Cell 只能导 public/

owned_paths 冲突校验

state_owners 唯一性校验

contract compatibility 校验

declared graph vs actual imports 对账

generated/*.pack.json freshness 校验

descriptor.pack.json schema 校验

descriptor freshness 校验

semantic index freshness 校验

未声明 effect 校验

UTF-8 文本读写校验

旧根目录冻结检查

KernelOne 准入检查

9.3 关键扫描器怎么做

依赖扫描器

先用 owned_paths 建立 file -> cell 映射

再 AST 扫 import

如果 A 直接导入 B 的 internal，失败

如果 A 实际依赖 B，但 depends_on 未声明，失败

effect 扫描器

扫 open/write_text/requests/httpx/subprocess/db client

映射回所属 Cell

若未通过 effect port / effects_allowed 声明，失败

KernelOne 准入检查

扫 kernelone/ 是否导入业务词汇、application、domain、delivery、旧根目录

发现业务 contract 或业务 DTO 混入，失败

10. 当前项目的实际起点：从“当前事实”出发，不假装目标已经完成

这点必须特别强调。Polaris 最终规范已经明确区分了“当前事实”和“目标状态”，不能把目标 Cell 当成已经存在。

10.1 第一批先接入 Descriptor 的，应当是当前已存在的公共 Cell

也就是先覆盖这些：

delivery.api_gateway

storage.layout

policy.workspace_guard

policy.permission

orchestration.pm_planning

orchestration.pm_dispatch

orchestration.workflow_runtime

director.execution

context.engine

audit.evidence

runtime.projection

先把这些真实存在的 Cell 做全：

cell.yaml

README.agent.md

context.pack.json

impact.pack.json

verify.pack.json

descriptor.pack.json

10.2 第二批再去做首个完整业务纵切：storage_archive_pipeline

也就是把目标 Cell 真正落下来：

runtime.state_owner

archive.run_archive

archive.task_snapshot_archive

archive.factory_archive

audit.evidence

policy.workspace_guard

这条子图最适合作为 ACGA 2.0 的第一条完整验证链，因为它同时覆盖：

单写状态拥有权

高副作用 file system 写入

归档 / 历史 / manifest / index

audit 证据

query contract 稳定性

integration 验证

而这些正是最终规范里最重要也最容易腐化的部分。

补充：

`compatibility.legacy_bridge` 属于迁移过渡边界，已在当前活动 graph 退场，不再作为该子图的正式节点。

11. 分阶段落地顺序
Phase 0：裁决入库

交付物：

docs/ACGA_2.0_PRINCIPLES.md

docs/governance/schemas/semantic-descriptor.schema.yaml

docs/templates/descriptor.pack.json

docs/governance/ci/fitness-rules.yaml 扩展 descriptor / semantic index 规则

FINAL_SPEC.md 增补 ACGA 2.0 章节

退出标准：

文档与 schema 合并

.acga/graph / scripts/ 方案被明确否决

Phase 1：Graph 对账基础设施

交付物：

polaris/delivery/cli/ 下的 graph build / validate 入口

polaris/application/ 或 polaris/cells/context/engine/internal/ 中的 graph compiler

docs/graph/catalog/cells.yaml 可由 manifest 编译并校验

退出标准：

所有当前 public Cell 通过 schema

graph catalog 与 cell manifests 一致

Phase 2：Descriptor 闭环

交付物：

descriptor_pipeline

每 Cell generated/descriptor.pack.json

workspace/meta/context_catalog/descriptors.json

index_state.json

LanceDB index

audit receipts

退出标准：

当前已存在 public Cell 全部有新鲜 descriptor

任何源码/contract 变化都能触发增量刷新

Phase 3：Graph-Constrained Search 上线

交付物：

ResolveContextQueryV1

SearchCellsQueryV1

GetContextPackQueryV1

GetVerifyPackQueryV1

Graph filter + semantic rank + neighbor expansion

lexical fallback + hard stop

退出标准：

Agent 默认检索链路切到新 Context Plane

禁止 blind repo scan 成为默认路径

Phase 4：Governance 全门禁

交付物：

import fence

state owner uniqueness

effect declared only

descriptor freshness

semantic index freshness

KernelOne admission

targeted tests selection

退出标准：

CI 能阻止边界腐化

架构报告可追溯失败证据

Phase 5：Storage/Archive 子图成型

交付物：

runtime.state_owner

archive.run_archive

archive.task_snapshot_archive

archive.factory_archive

storage_archive_pipeline 子图资产

对应 contract / tests / verify packs

退出标准：

runtime/history/archive 单写规则成立

子图集成测试全绿

compatibility 层变薄

Phase 6：扩展到更多热点子图

优先扩展到：

director.execution

pm.task_contract

tooling.executor

qa.integration_gate

12. 第一批要直接开工的文件和模块

这是最关键的“从哪写第一笔代码”。

12.1 文档与 schema

docs/ACGA_2.0_PRINCIPLES.md

docs/governance/schemas/semantic-descriptor.schema.yaml

docs/templates/descriptor.pack.json

docs/graph/subgraphs/storage_archive_pipeline.template.yaml

12.2 Context Plane 实现

polaris/cells/context/engine/internal/graph_resolver.py

polaris/cells/context/engine/internal/descriptor_pipeline.py

polaris/cells/context/engine/internal/semantic_search.py

polaris/cells/context/engine/internal/pack_builder.py

12.3 技术内核与适配器

polaris/kernelone/llm/：Descriptor 生成调用契约

polaris/kernelone/fs/：文件读取/写入契约

polaris/kernelone/trace/：trace / receipt

polaris/kernelone/effect/：effect policy

polaris/infrastructure/llm/：本地/远程 LLM adapter

polaris/infrastructure/storage/：catalog / index persistence adapter

12.4 CLI 入口

放在 `polaris/delivery/cli/`，而不是 `scripts/`：

polaris/delivery/cli/build_system_graph.py

polaris/delivery/cli/generate_descriptors.py

polaris/delivery/cli/sync_context_catalog.py

polaris/delivery/cli/select_tests.py

CLI 只做参数解析，核心逻辑不放这里。

12.5 架构测试

tests/architecture/test_graph_manifest_reconciliation.py

tests/architecture/test_cross_cell_public_import_only.py

tests/architecture/test_descriptor_freshness.py

tests/architecture/test_semantic_index_freshness.py

tests/architecture/test_kernelone_admission.py

13. 最后给你的“最终版本一句话”

ACGA 2.0 在这个项目里的最终落地，不是“再建一套新目录”，而是：

用 六个规范根目录 约束物理分层；

用 Cell + Graph 约束能力边界与授权修改半径；

用 Descriptor + LanceDB 把 AI 检索升级成“先图约束、后语义排序”；

用 KernelOne 承接 Agent/AI 运行时底座与通用 OS 能力，而不吞业务；

用 Audit + CI 把描述卡、索引、状态拥有权、effect 和 import fence 全部变成可验证资产。

最合理的下一步，不是继续讨论概念，而是直接把这 5 个东西落成文件：
ACGA_2.0_PRINCIPLES.md、semantic-descriptor.schema.yaml、descriptor.pack.json 模板、context.engine 的四个内部模块骨架、以及 CI 的 descriptor/index 门禁。

AI 自演进闭环（受控版）

本架构允许 AI 在 Graph、Contract、Effect 和 Governance 约束下自动提出并实现低风险升级，但不允许无审计、无验证、无图谱更新的自发改写。

Cell 的生产代码依赖 Contract，而不依赖其他 Cell 的物理实现；但 Graph 必须显式声明允许的 provider 关系与依赖边。

运行时可使用 Contract Router 做 provider 解析，但解析范围必须先经过 Graph 约束，再做版本兼容与策略选择。

独立测试必须支持 Contract Sandbox：依据 public contract 和 verify pack 自动注入 Virtual Provider / Mock Provider。

运行时证据可以生成拓扑演化建议，包括 merge、split、co-locate、replace；但任何拓扑变更都必须经过 impact analysis、verify、subgraph integration、audit、graph update 与 promotion/rollback。

热路径优先同进程直连；禁止把所有依赖抽象都演化成无差别消息总线。

这段和现有规范是相容的，因为它没有推翻 “Graph 是真相” “Contract 是边界” “Context Pack 是 AI 默认入口” “Effect 必须可审计” “application 拥有执行边界” “kernelone 是 Agent/AI 的运行时底座但不承载业务语义” 这些根规则。
