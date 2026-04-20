# Canonical Code Exploration And Context Assembly Blueprint

状态: Draft  
日期: 2026-03-25  
范围: `polaris/kernelone/context/`、`polaris/kernelone/tools/`、`polaris/cells/context/catalog/`、`polaris/cells/workspace/integrity/`、`polaris/cells/roles/kernel/`、`polaris/cells/roles/runtime/`

> 本文是目标蓝图，不是 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`、各 Cell manifest 为准。  
> 本文的作用是把 Polaris 在“代码阅读 / 上下文组装 / 会话连续性 / 压缩触发”上的 canonical 路线收敛成一套主流、可执行、可审计的方案。
>
> 2026-03-25 更新：
> 本文现在应被理解为“canonical default profile”的蓝图，而不是唯一策略本身。
> 更完整的上位蓝图见 `docs/KERNELONE_CONTEXT_STRATEGY_FRAMEWORK_BLUEPRINT_2026-03-25.md`。
> 后续演进方向是 `KernelOne Context Strategy Framework + canonical default profile`，而不是继续把探索/压缩逻辑硬编码在单一 policy 中。
> 本文进一步降级为 `Phase 1: code domain` 子蓝图。
> 它不是 Polaris 的通用 Agent foundation 本体，只是共享基础能力的第一个高难度验证场景。

---

## 1. 结论

本蓝图定义的是 Polaris 当前建议采用的 `Phase 1: code domain canonical default profile`。
它应运行在更高一层的共享 Agent foundation / strategy framework 之内，而不是作为系统中唯一不可替换的策略。

Polaris 的 canonical 策略不应是：

1. 默认整文件 `read_file`
2. 每轮都做 context compaction
3. 让角色自己决定“先读什么、读多少、何时压缩”
4. 把工具回执和大段源码直接当长期工作内存

Polaris 的 canonical 策略应收敛为：

`Repo Map / Symbol Index -> Precision Search -> Range Read -> Neighbor Expansion -> Context Budget Gate -> Near-Limit Compaction -> Hot Asset Cache`

也就是：

1. 先给模型轻量全局地图
2. 再让模型按符号或语义检索目标
3. 默认只读取局部片段
4. 必要时逐步扩圈
5. 接近模型上下文窗口上限时才压缩
6. 高频资产放缓存，不重复“读整仓”

这就是当前业界最主流的 coding-agent 读码策略，也是 Polaris 在 `Phase 1: code domain` 中应该固化成 runtime contract 的方向。

---

## 2. 为什么必须这样做

即使模型具备 128k、200k、1M 甚至更大的上下文窗口，也不意味着系统应该每轮把完整文件或大量整仓源码塞给模型。

原因不是单一的 token 成本，而是四个问题同时存在：

1. Token 成本会线性放大
2. 首字延迟和整体响应时延会显著恶化
3. 大量无关源码会稀释注意力，触发典型的 `lost in the middle`
4. 当工具结果显得“不完整”或“边界不清”时，模型容易重复调用 `read_file`

因此，长上下文窗口的正确用法不是“吃满”，而是“提供更大的安全工作集上限”。  
真正的默认策略仍应是最小充分上下文。

---

## 3. 当前仓库里已经有的正确资产

Polaris 不是从零开始。当前仓库已经有一部分可以直接复用的基础能力。
但这些基础能力在长期上属于共享 Agent foundation，而不是只属于 code domain：

### 3.1 Repo Map

- `polaris/kernelone/context/repo_map.py`

当前已经具备：

1. 按语言构建 repository skeleton
2. 基于 tree-sitter 或 fallback regex 提取 class / function / method
3. 生成轻量文本地图和统计信息

这意味着“先给全局骨架再读局部”不是新想法，而是已有能力没有被提升为 runtime 默认策略。

### 3.2 KernelOne 工具层已有精细化读码工具

- `polaris/kernelone/tools/contracts.py`
- `polaris/kernelone/tools/runtime_executor.py`

当前已出现或已接入的相关工具面包括：

1. `repo_map`
2. `repo_symbols_index`
3. `repo_read_slice`
4. `repo_read_around`
5. `repo_read_head`
6. `repo_read_tail`
7. `treesitter_find_symbol`

这说明 Polaris 已经具备“按范围读”和“按符号找”的雏形，只是还没有在 roles runtime 上形成强约束默认流。
这也是为什么 code domain 适合作为第一阶段验证场景：约束最强、难度最高、收益最大。

### 3.3 Workspace / Code Intel

- `polaris/cells/workspace/integrity/internal/code_intel.py`

当前已经具备：

1. 相关文件检索
2. snippet 提取
3. 任务相关 symbol 汇总

这部分能力与 repo map / symbol index 可以直接组成“宏观到微观”的探索链路。

### 3.4 Session Continuity / Context Compaction

- `docs/SESSION_CONTINUITY_ENGINE_BLUEPRINT_2026-03-25.md`
- `polaris/kernelone/context/compaction.py`
- `polaris/kernelone/context/session_continuity.py`（已建立方向）

这说明“接近上限才压缩”的后半段基础已经存在，不需要再额外造一套并行 memory 系统。

### 3.5 Context Plane 图谱

- `docs/graph/subgraphs/context_plane.yaml`

当前 graph 已经承认：

1. `context.catalog`
2. `context.engine`
3. `workspace.integrity`
4. `roles.runtime`

共同构成上下文检索与组装链路。

---

## 4. 当前结构上的问题

虽然基础资产存在，但系统行为仍不够 canonical，主要问题有五类。

### 4.1 默认读取策略还不够强约束

`read_file` 仍然过于容易成为第一选择，而不是：

1. repo map
2. symbol index
3. range read
4. expansion

的最后一环之一。

### 4.2 上下文预算决策仍然偏角色局部逻辑

`roles.kernel` 已经开始根据模型窗口动态控制 `read_file` 回执保留，但这仍只是 tool-loop 层止血，尚未升级成统一的 code exploration budget policy。

### 4.3 `roles.kernel` 仍存在直接 history compaction 逻辑

- `polaris/cells/roles/kernel/internal/context_gateway.py`

当前仍直接在 gateway 内按 `max_history_turns` 和 summarize 策略处理 history。  
这在 session continuity 上已经部分收口，但在“代码探索上下文”上仍没有统一 planner。

### 4.4 工具调用还没有显式的探索阶段规划

当前角色更多是“自由调用工具”，而不是经过一个明确的 exploration policy：

1. map
2. search
3. slice
4. expand
5. optional full read

### 4.5 缓存分层还不够清晰

当前已经有 session continuity、descriptor、repo map 雏形，但还没有把以下几类资产清楚分层：

1. session continuity cache
2. repository structural cache
3. symbol index cache
4. hot file cache
5. prompt cache

---

## 5. Canonical 主流策略

### 5.1 阶段 A：Repository Mapping

默认第一步不是 `read_file`，而是获取代码库结构化地图。

最小输出应包括：

1. 目录树摘要
2. 候选文件列表
3. 每个文件的 class / function / method skeleton
4. 语言与文件规模统计

默认工具来源：

1. `repo_map`
2. `repo_symbols_index`
3. `workspace.integrity` 的 symbol/snippet 能力

阶段 A 的目标是回答：

1. 哪个文件可能相关
2. 文件里有哪些公开符号
3. 哪些文件太大，不应该直接整文件读

### 5.2 阶段 B：Precision Search

有了 repo map 之后，第二步应当是“找目标”，而不是“继续盲读”。

默认手段：

1. `ripgrep`
2. symbol index
3. `treesitter_find_symbol`
4. 未来的 `find_references / go_to_definition` 等 symbol navigation

阶段 B 的目标是回答：

1. 用户关心的是哪个 symbol / file / path
2. 定义点在哪里
3. 调用点在哪里
4. 需要读的是实现、调用方还是契约

### 5.3 阶段 C：Range Read

一旦锁定文件，默认行为必须是局部读取，不是整文件读取。

canonical 默认：

1. 先读目标 symbol 所在 span
2. 再按需要读前后 30-150 行
3. 对超大文件禁止直接 full-file read，除非显式升级

工具层 canonical：

1. `repo_read_slice`
2. `repo_read_around`
3. `repo_read_head`
4. `repo_read_tail`

`read_file` 不应被删除，但应该降级为：

1. 小文件读取
2. 明确要求通读时
3. 局部读取工具不可用时的 fallback

### 5.4 阶段 D：Neighbor Expansion

如果当前片段不足以完成判断，系统按邻接关系逐步扩圈，而不是直接整文件/整仓放进 prompt。

扩圈优先级：

1. 同文件相邻片段
2. 目标 symbol 的定义/调用邻接
3. 相关契约和公开接口
4. 测试文件
5. 配置和 schema

这里的核心是“incremental working set”，不是“无限读”。

### 5.5 阶段 E：Budget Gate

所有读取的代码片段、repo map、descriptor、session continuity 都应进入同一个 context budget gate。

预算来源优先级应为：

1. 真实模型 context window
2. provider/model resolved spec
3. role context policy fallback
4. default fallback

在预算 gate 里，系统决定：

1. 当前 working set 是否还能继续扩圈
2. 是否需要裁掉旧 exploration evidence
3. 是否把 read receipt 保留为全文、截断版或 summary

### 5.6 阶段 F：Near-Limit Compaction

context compaction 不是默认步骤，而是 near-limit fallback。

触发条件应当是：

1. working set 接近模型窗口阈值
2. 当前 prompt 已明显超过安全 headroom
3. 历史探索证据已经大于当前任务所需

不触发条件：

1. 新 session 刚开始
2. 当前 working set 还很小
3. 只是读了几个 symbol 和少量 range

### 5.7 阶段 G：Hot Asset Cache

高频资产应进入缓存，而不是让模型重复读取。

应缓存的对象：

1. repo map
2. symbol index
3. descriptor pack
4. 最近热文件片段
5. session continuity projection

不应缓存为“真相”的对象：

1. graph truth
2. source-of-truth session rows
3. public contract ownership

---

## 6. Polaris 的 canonical 决策

### 6.1 默认探索顺序

在 `Phase 1: code domain` 中，Polaris 应固定以下默认探索顺序：

1. `repo_map` / `repo_symbols_index`
2. `search_code` / `ripgrep` / symbol lookup
3. `repo_read_slice` / `repo_read_around`
4. neighbor expansion
5. 必要时才允许 `read_file`
6. 接近上限才触发 compaction

### 6.2 `read_file` 的角色重新定义

`read_file` 不是废弃，而是重新降级为高成本工具。

建议固定策略：

1. 小文件可直接读
2. 中大文件默认拒绝整文件读，先提示 range/symbol read
3. 只有在“明确需要通读 + budget 允许”时才允许全量读

### 6.3 `roles.kernel` 不再决定探索策略

`roles.kernel` 负责：

1. transcript
2. tool loop
3. stream contract
4. context budget gate

但不应继续承担“读码策略设计器”的职责。

探索策略应当逐步上移/收敛到：

1. `kernelone.context` 的 exploration policy
2. `context.engine` 的上下文组装流程
3. `tools` 层的 canonical read/search capability

### 6.4 Session Continuity 与 Code Exploration 必须分层

`session continuity` 负责：

1. 过去对话的稳定事实
2. open loops
3. recent window

`code exploration context` 负责：

1. repo map
2. symbol evidence
3. code slices
4. affected tests / configs

两者都进入 prompt，但不是同一类资产，也不应相互替代。

---

## 7. 明确拒绝的做法

以下做法在 Polaris 里应明确视为非 canonical：

1. 每轮默认整文件 `read_file`
2. 每轮一开始先做 context compaction
3. 由角色提示词临时约束“先读文件再说”
4. 把大段工具回执长期塞进 history
5. 让 session continuity 承担代码检索职责
6. 让 repo map / descriptor / cached index 反向覆盖 graph truth

---

## 8. 建议的落地架构

### 8.1 KernelOne

主承载：

1. `polaris/kernelone/context/repo_map.py`
2. `polaris/kernelone/context/` 下新增统一 exploration policy / working-set assembler
3. `polaris/kernelone/tools/` 下统一 read/search/symbol navigation contract

### 8.2 Context Plane

主承载：

1. `context.catalog` 提供 graph-constrained candidate set
2. `context.engine` 负责 orchestration
3. `workspace.integrity` 提供 code intel / symbol / snippet 能力

### 8.3 Roles

`roles.runtime` / `roles.kernel` 只消费 canonical assembled context，不继续各自手写探索规则。
更高一层的共享 Agent foundation 则为未来 document / fiction / research domain 留出 adapter seam。

---

## 9. 分阶段落地

### Phase 1：文档与策略收口

1. 固化本文为 canonical blueprint
2. 把 “默认整文件读取” 明确打为非 canonical
3. 明确 `read_file` 的降级定位

### Phase 2：工具面收口

1. 给 `read_file` 增加明确的 range-first 策略
2. 让 `repo_read_slice / repo_read_around` 成为推荐路径
3. 提升 `repo_map / repo_symbols_index / treesitter_find_symbol` 的 roles 级默认使用优先级

### Phase 3：Context Assembler 收口

1. 引入 exploration working-set assembler
2. 统一 repo map、symbol、slice、neighbor expansion 的预算管理
3. 接近模型窗口上限时才压缩

### Phase 4：缓存层收口

1. repo map cache
2. symbol index cache
3. hot slice cache
4. continuity projection cache
5. prompt cache 对热资产做成本优化

### Phase 5：治理收口

1. 更新相关 ADR / verification card / stopgap audit
2. 将 ad-hoc exploration 路径列入待淘汰清单
3. 将这套策略纳入 runtime 和 E2E 回归基线

---

## 10. 验证标准

要证明这套蓝图真正落地，不是只看“能不能跑”，而是看以下行为是否成立：

1. 角色在读码前先产出 repo map / symbol candidate，而不是直接整文件读
2. 大文件默认走 slice/around，而不是 full-file read
3. history / session continuity / code exploration 三类资产能被区分
4. 只有接近模型窗口时才触发 compaction
5. 热文件重复访问时，系统优先命中缓存而不是重复读取
6. 相同请求下，`read_file` 重复循环显著下降

---

## 11. 最终完成态

完成后，Polaris 在 `Phase 1: code domain` 的 canonical 行为应当是：

1. 从全局骨架开始，而不是从源码洪水开始
2. 按 symbol 和 range 精确读取，而不是默认整文件读取
3. 让 working set 逐步扩圈，而不是一次性塞满上下文
4. 让 compaction 成为 near-limit fallback，而不是默认动作
5. 让 repo map / symbol index / continuity / cache 都成为正式资产，而不是止血逻辑

这才是 Polaris 在 `Phase 1: code domain` 应该采用的主流策略，也是后续把共享 Agent foundation、role overlays、其他 domain adapters 真正收敛成统一系统的前提。
