# ACGA 2.0 架构原则

> Agent 执行入口标准请先读：`docs/AGENT_ARCHITECTURE_STANDARD.md`。  
> 本文给出架构原则，不替代 `AGENTS.md` 的强制执行规则。

## 1. 核心裁决

### 1.1 Graph 是唯一架构真相

- **原则**：架构边界不由向量检索推断，只能由 `docs/graph/catalog/cells.yaml`、`docs/graph/subgraphs/*.yaml` 以及各 Cell 的 `cell.yaml` 共同声明
- **推论**：向量检索只允许在 Graph 先约束出的候选集合内排序

### 1.2 六个规范根目录

物理实现必须收敛到：

```
bootstrap/        # 组装根、配置、生命周期、DI
delivery/         # HTTP / WS / CLI 入口
application/      # 用例、事务边界、工作流编排
domain/           # 业务规则、实体、值对象、领域策略
kernelone/        # Agent/AI 类 Linux 运行时底座
infrastructure/   # 具体 adapter、持久化、消息、遥测、LLM/FS/DB 后端
cells/            # Cell manifest / contracts / tests / generated packs
docs/graph/       # Graph 真相资产
```

**重要**：Cell 不是替代这些目录，而是叠加在目录之上的能力边界。

**仓库落地说明**：在当前仓库里，这些目标根目录统一承载在 `polaris/` 下，例如 `polaris/delivery/`、`polaris/application/`、`polaris/cells/`。`docs/graph/` 与 `docs/governance/` 继续保留在仓库顶层作为共享真相资产。

### 1.3 KernelOne 准入规则

KernelOne 不是普通工具层，而是面向 AI/Agent 的类 Linux 运行时底座；但它仍然只承载无 Polaris 业务语义的技术能力，**禁止**吸收：
- archive.* 业务语义
- runtime.state_owner 业务状态
- 业务 query/result/event
- workspace guard 策略
- migration status

**允许**承载：
- fs / storage / db / ws / stream / events / message_bus / trace / effect / llm / process / tool_runtime / agent_runtime / context_runtime / context_compaction / task_graph

### 1.4 Embedding 禁止直接吃源码

必须先生成结构化 Descriptor，再对 Descriptor 的"可检索语义文本"做 embedding。
源码只能作为生成 Descriptor 的输入，不是向量模型的直接语料。

### 1.5 跨 Cell 依赖规则

跨 Cell 依赖只允许：
- public Contract
- 依赖注入（DI）

**禁止**直接跨 Cell 依赖对方 `internal/`，测试也必须通过 fake/mock contract 保证独立运行。

### 1.6 语义索引写入是 Effect

Descriptor 生成、Embedding、Index upsert/delete 都要留下 receipt / trace。

### 1.7 Cell 复用优先 + KernelOne 底座优先（MUST）

- 所有 Cell 开发先复用其他 Cell 的公开能力，再考虑新增实现。
- 所有新开发必须通过 `KernelOne` 契约与运行时底座接入副作用链路。
- 禁止绕过 `KernelOne` 直接构建底层技术耦合。

---

## 2. 架构分层

### 2.1 三层模型

| 层级 | 职责 | 关键资产 |
|------|------|----------|
| **Graph Plane** | 定义能力边界、依赖关系、状态拥有权 | cells.yaml, subgraphs/*.yaml, cell.yaml |
| **Capability Plane** | 实现业务功能，通过 Cell 组织 | Cell 代码、public contracts、tests |
| **Context Plane** | AI/Agent 发现、检索、上下文装配 | Descriptor、Packs、Semantic Index |

### 2.2 协作链路

```
delivery -> application workflow/usecase -> cell public port ->
internal logic -> outbound effect port -> kernelone technical contract ->
infrastructure adapter
```

**硬性规则**：
- 不允许跳过 application 直接做业务编排
- 不允许 delivery 直连具体 infrastructure
- 不允许 KernelOne 反向吸收业务语义

---

## 3. Context Plane 规范

### 3.1 Descriptor 与 Pack 的职责边界

**Descriptor**：给检索用，短、小、可嵌入、强分类

**Context Pack**：给工作用，完整但仍是最小必需上下文

### 3.2 Descriptor 生成两阶段

**Stage A：静态提取器**
- 读取 cell.yaml, README.agent.md
- 解析 public/contracts
- 提取 owned_paths 内公开函数/类签名、docstring、import 依赖
- 形成 machine digest

**Stage B：LLM 规范化器**
- 输入：machine digest（不是全仓源码）
- 输出：严格符合 schema 的 descriptor.pack.json
- 参数：temperature=0, JSON only, 不允许输出代码片段

### 3.3 标准检索链路

```
Intent Parsing -> Graph Filter -> Semantic Rank ->
Optional Rerank -> Neighbor Expansion -> Context/Verify Pack 装配
```

**关键约束**：向量检索永远不能新建边界，只能在已有图谱的合法边界内"发现"。

---

## 4. Cell 规范

### 4.1 cell.yaml 最小必备字段

```yaml
cell_id: context.engine
kind: capability  # or: policy, projection, integration
owner: architecture-team
public: true
domain: context

owned_paths:
  - cells/context/engine/**
  - application/services/context/**

state_owners: []  # 声明状态拥有权路径

effects_allowed:  # 声明允许的副作用
  - fs.read:docs/graph/**
  - fs.write:workspace/meta/**

inbound_ports:    # 提供的 Capability
  - name: resolve_context
    mode: query
    contract: ResolveContextQueryV1

outbound_ports:   # 依赖的 Capability
  - name: read_graph
    mode: effect
    contract: GraphReadEffectV1

depends_on:       # 依赖的 Cell
  - audit.evidence
  - policy.workspace_guard

verify_targets:
  tests:
    - tests/context/test_context_engine.py
```

### 4.2 目录与 Cell 的关系

- **目录解决分层**：代码按 bootstrap/delivery/application/domain/kernelone/infrastructure 放置
- **Cell 解决能力归属**：通过 `owned_paths` 声明哪些文件属于该 Cell
- **Graph 解决系统理解**：全局视图、依赖关系、验证目标
- **Vector 解决发现效率**：语义检索、相似度排序

---

## 5. Governance 规范

### 5.1 CI 门禁清单

- [ ] cell.yaml / subgraph.yaml schema 校验
- [ ] import fence：跨 Cell 只能导 public/
- [ ] owned_paths 冲突校验
- [ ] state_owners 唯一性校验
- [ ] contract compatibility 校验
- [ ] declared graph vs actual imports 对账
- [ ] generated/*.pack.json freshness 校验
- [ ] descriptor.pack.json schema 校验
- [ ] UTF-8 文本读写校验
- [ ] 旧根目录冻结检查
- [ ] KernelOne 准入检查

### 5.2 关键扫描器

**依赖扫描器**：
1. 用 `owned_paths` 建立 file -> cell 映射
2. AST 扫 import
3. 如果 A 直接导入 B 的 internal，失败
4. 如果 A 实际依赖 B，但 `depends_on` 未声明，失败

**Effect 扫描器**：
1. 扫 open/write_text/requests/httpx/subprocess/db client
2. 映射回所属 Cell
3. 若未通过 effect port / `effects_allowed` 声明，失败

**KernelOne 准入检查**：
1. 扫 kernelone/ 是否导入业务词汇、application、domain、delivery
2. 发现业务 contract 或业务 DTO 混入，失败

---

## 6. 分阶段落地

| Phase | 目标 | 关键交付物 |
|-------|------|-----------|
| 0 | 裁决入库 | ACGA_2.0_PRINCIPLES.md, semantic-descriptor.schema.yaml |
| 1 | Graph 对账 | graph build/validate 入口, cells.yaml 编译 |
| 2 | Descriptor 闭环 | descriptor_pipeline, descriptor.pack.json, LanceDB |
| 3 | Graph-Constrained Search | ResolveContextQueryV1, SearchCellsQueryV1 |
| 4 | Governance 全门禁 | import fence, state owner, effect declared |
| 5 | Storage/Archive 子图 | runtime.state_owner, archive.* cells |
| 6 | 热点子图扩展 | director.execution, pm.task_contract |

---

## 7. 一句话总结

ACGA 2.0 的落地不是"再建一套新目录"，而是：

> 用**六个规范根目录**约束物理分层；用 **Cell + Graph** 约束能力边界与授权修改半径；用 **Descriptor + LanceDB** 把 AI 检索升级成"先图约束、后语义排序"；用 **KernelOne** 承接纯技术运行时而不吞业务；用 **Audit + CI** 把描述卡、索引、状态拥有权、effect 和 import fence 全部变成可验证资产。

---

*文档版本: 1.0*  
*最后更新: 2026-03-18*  
*架构负责人: Polaris Architecture Team*
