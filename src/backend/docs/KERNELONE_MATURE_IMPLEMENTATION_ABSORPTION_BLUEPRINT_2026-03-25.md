# KernelOne 成熟实现吸收蓝图

状态: Draft  
日期: 2026-03-25  
范围: `polaris/kernelone/context/`、`polaris/kernelone/llm/`、`polaris/kernelone/telemetry/`、`polaris/cells/roles/runtime/`、`polaris/cells/roles/kernel/`、`polaris/cells/context/catalog/`、`polaris/cells/workspace/integrity/`、`polaris/delivery/cli/`

> 本文是目标蓝图，不是 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`、各 Cell manifest 为准。  
> 本文用于把“吸收市面上相对成熟稳定的实现”这件事，收敛成 Polaris 的正式裁决。  
> 当前第一份外部成熟样本是 `aider`，但本文本身不把 Polaris 绑定到单一外部项目；它定义的是一套可重复的吸收标准与第一阶段落地路径。
>
> 上位蓝图：
> 1. `docs/KERNELONE_CONTEXT_STRATEGY_FRAMEWORK_BLUEPRINT_2026-03-25.md`
> 2. `docs/CANONICAL_CODE_EXPLORATION_CONTEXT_ASSEMBLY_BLUEPRINT_2026-03-25.md`
> 3. `docs/SESSION_CONTINUITY_ENGINE_BLUEPRINT_2026-03-25.md`

---

## 1. 最终决策

Polaris 不应直接移植 `aider` 的产品架构，也不应继续把成熟能力硬编码在单一角色里。

最终决策是：

`吸收成熟实现中的底层算法与运行时机制 -> 下沉到 KernelOne -> 形成共享 Agent Foundation -> 再由 domain adapter 和 role overlay 复用`

换句话说：

1. 不把 `aider` 变成 Polaris 的新内核。
2. 只吸收那些已经被外部项目验证过、且符合 Polaris 长期边界的成熟机制。
3. Phase 1 不先继续打磨 Director 私有能力，而是优先强化所有角色共享的 Agent 基础能力。
4. 第一批吸收源以 `aider` 为主，但最终产物必须是 Polaris 自己的 canonical KernelOne 能力。

---

## 2. 为什么不能直接“把 Aider 搬进来”

`aider` 很成熟，但它成熟的是一套面向单会话 CLI coding agent 的产品实现，不是 Polaris 这种多角色、多域、多运行时的治理平台。

直接移植会带来四个问题：

1. `aider` 的 `Coder` 体系是单体运行时，产品逻辑、编辑逻辑、消息装配、模型路由强耦合。
2. Polaris 的 source-of-truth 在 graph / cell / contract，不在单个 coder runtime。
3. Polaris 未来不只做代码，还会覆盖写作、小说、研究等非 code domain。
4. Polaris 已经拥有 `roles.session`、`RoleRuntimeService`、`KernelOne` 这些 canonical 承载面，不应再并行引入一套新的主内核。

因此，正确策略不是“移植产品”，而是“吸收机制”。

---

## 3. 吸收标准

只有同时满足以下条件的外部实现，才允许被吸收到 Polaris：

1. **通用性成立**：剥离外部产品语义后，仍能作为 Agent/AI 通用运行时能力成立。
2. **边界兼容**：不会破坏 Polaris 的 graph truth、Cell 边界、state owner、effect 约束。
3. **可量化**：能输出 metrics、receipts、benchmarks，方便后续对比演化。
4. **可替换**：落地后应是 KernelOne 内的模块/策略，而不是难以拆除的外部结构嫁接。
5. **许可证兼容**：必须满足许可证要求；当前 `aider` 为 Apache 2.0，可受控复用，但不能省略归属与合规要求。

不满足以上条件的成熟实现，只能作为参考，不得直接进入主实现。

---

## 4. 吸收对象分类

### 4.1 直接吸收类

这类能力已被证明具有高价值、强通用性、与 Polaris 边界兼容，应优先吸收。

#### A. Repo Intelligence

来源：

1. `aider/repomap.py`

应吸收的机制：

1. Tree-sitter `def/ref` 标签提取
2. mtime / 持久缓存驱动的增量刷新
3. 基于 mention / symbol / chat file 的个性化排序
4. token budget 下的 repo map fitting
5. lines-of-interest 邻域渲染

Polaris 裁决：

1. 这是 **KernelOne 的代码智能/工作集能力**，不是角色层技巧。
2. 它应增强 `working set assembly`，减少盲目 `read_file`，并降低对 LLM 生成式探索的依赖。
3. 它不能替代 `docs/graph/**` 或 Cell/Descriptor 真相，只能做运行时候选生成与排序。

#### B. Model Capability Catalog

来源：

1. `aider/models.py`
2. `aider/resources/model-settings.yml`

应吸收的机制：

1. alias -> canonical model 映射
2. 数据驱动的模型画像
3. weak model / editor model / reasoning / cache_control / streaming 能力声明
4. provider-specific settings 接受矩阵
5. context window / output tokens / cost hint 元数据

Polaris 裁决：

1. 这是 **KernelOne LLM 基础设施**，应统一落在 `kernelone.llm`。
2. 应逐步替换当前分散的硬编码 fallback 与 provider 特例分支。
3. 最终形态应是 Polaris 自己的 model profile schema，而不是直接依赖 `aider` 原始 YAML。

#### C. Prompt Chunk Assembler

来源：

1. `aider/coders/chat_chunks.py`

应吸收的机制：

1. 按语义分段装配 prompt
2. chunk 级 cache-control 标记
3. 已完成历史 / repo intelligence / 当前任务 / reminder 分层
4. chunk 粒度的度量与调试

Polaris 裁决：

1. 这是 **KernelOne + roles.kernel 之间的共享装配契约**，不是 CLI 技巧。
2. 当前 `prompt_builder` 的“字符串拼接”应升级为 chunk-aware 结构。
3. 最终实际发给 LLM 的请求应按 chunk 发出结构化 debug receipt。

#### D. Message Normalization

来源：

1. `aider/sendchat.py`

应吸收的机制：

1. alternating role 校验
2. 自动补齐空消息以满足 provider 协议
3. 最后一个非 system message 的合法性约束

Polaris 裁决：

1. 这是 `KernelOne LLM request normalization` 的通用能力。
2. 应放在 provider adapter 前，而不是散落在角色层。

### 4.2 条件吸收类

这类能力有价值，但必须以 Polaris 的 canonical 边界改写后才能接入。

#### A. Near-limit LLM Summary

来源：

1. `aider/history.py`

应吸收的机制：

1. head/tail 保留
2. 最近消息优先
3. 超预算时递归压缩
4. 摘要失败降级链

Polaris 裁决：

1. 不能替代 `SessionContinuityEngine` 主线。
2. 只能作为 `near-limit compaction` 的可选策略插件。
3. 默认 continuity 仍应先走 deterministic 结构化 pack。
4. 只有在接近模型真实上下文上限时，才允许切到 LLM summary 策略。

#### B. Gradient Router

来源：

1. `aider` 的 weak model / editor model / architect-style 双模型梯度设计

应吸收的机制：

1. 规划/摘要/反思走弱模型
2. 执行/最终生成走强模型
3. 任务类型驱动模型分层

Polaris 裁决：

1. 不移植 `ArchitectCoder` 类体系。
2. 改造成 `KernelOne task-class -> model-tier` 路由器。
3. 要兼容未来 code / writing / research 多 domain。

#### C. Reasoning 反注入标签

来源：

1. `aider/reasoning_tags.py`

应吸收的机制：

1. 每次运行生成随机 reasoning tag
2. 输出阶段重写 tag
3. 在上下文回灌前剥离 reasoning 内容

Polaris 裁决：

1. 应增强 thinking / reasoning 流式链路安全性。
2. 不能只在 UI 层做替换，必须进入流式协议和 request assembly 规则。

#### D. Tool Schema 静态校验

来源：

1. `aider` 的 Draft7 JSON Schema 启动时校验

Polaris 裁决：

1. 当前 `Pydantic` 契约不应被替换。
2. 可以补一层 JSON Schema 导出 + 静态校验，用于启动期/CI 发现 schema 漏洞。

### 4.3 参考但暂不落地类

1. Markdown 富文本流式渲染
2. cache warming
3. 分平台 token 计费
4. 更强的跨平台交互式 shell 运行时

这些方向有价值，但当前不是最优先的底座收口项。

### 4.4 明确拒绝类

以下内容不得直接吸收：

1. `aider` 的 monolithic `Coder` 架构
2. `SwitchCoder` 异常式主控制流
3. “单个 function call 强制”的工具策略
4. 把 repo map / semantic ranking 写成新的 graph truth
5. 把工具原始回执直接堆进长期 history
6. 把 LLM summary 当默认 continuity 主线

---

## 5. 架构定位

### 5.1 四层模型

Polaris 对外部成熟实现的吸收，必须落在以下四层中的正确位置：

1. **Truth Plane**
   - `docs/graph/**`
   - cell manifests
   - contracts
   - source-of-truth state owner

2. **Foundation Plane**
   - `KernelOne`
   - session continuity
   - prompt chunking
   - model capability
   - repo intelligence
   - debug / receipts / metrics

3. **Domain Adapter Plane**
   - code
   - document
   - fiction
   - research

4. **Role Overlay Plane**
   - Director
   - future Coder / Writer / Scout-like 子能力
   - governance roles 的轻量覆盖

任何外部成熟机制都必须先判断自己属于哪一层，禁止跳层落地。

### 5.2 Director 的定位

Director 不是“写代码角色”的别名。

Director 的定位是：

1. 执行母角色
2. 交付落地层
3. 可以管理 specialized execution overlays / subagents

因此：

1. `Coder`
2. `Writer`
3. 未来其他执行专精角色

更适合作为 `Director` 系列 overlay / subagent，而不是新的顶层治理角色。

这也是为什么第一阶段必须先把共享 Agent Foundation 建好，而不是继续把能力硬编码在 Director 私有实现里。

---

## 6. 命名与落点裁决

### 6.1 Repo Intelligence 命名

用户提供的建议里出现了 `kernelone/semantic/graph.py`。

该命名 **不采用**。

原因：

1. Polaris 已经有正式的 graph truth 体系。
2. `semantic graph` 很容易被误读为新的架构真相层。
3. 这类能力的本质是运行时代码智能与 working-set candidate generation，不是 graph ownership。

推荐落点：

1. `polaris/kernelone/context/repo_intelligence/`
2. 或 `polaris/kernelone/codeintel/`

推荐子模块：

1. `tags.py`
2. `ranker.py`
3. `renderer.py`
4. `cache.py`
5. `facade.py`

当前 `repo_map.py` 可以保留为 facade / compatibility entry，但主实现应逐步收敛到更清晰的包结构。

### 6.2 Model Capability Catalog 落点

推荐落点：

1. `polaris/kernelone/llm/model_profiles/`
2. `polaris/kernelone/llm/engine/model_catalog.py`
3. `polaris/kernelone/llm/provider_adapters/`

### 6.3 Prompt Chunk Assembler 落点

推荐落点：

1. `polaris/kernelone/context/prompt_chunks.py`
2. `polaris/kernelone/context/prompt_chunk_assembler.py`
3. `polaris/cells/roles/kernel/internal/prompt_builder.py` 作为 role-facing facade

### 6.4 Gradient Router 落点

推荐落点：

1. `polaris/kernelone/llm/engine/gradient_router.py`

### 6.5 Request Debug / Receipt 落点

推荐落点：

1. `polaris/kernelone/telemetry/debug_stream.py`
2. `polaris/kernelone/llm/engine/stream_executor.py`
3. `polaris/cells/roles/runtime/public/service.py`

---

## 7. Polaris 对 `aider` 的正式吸收映射

### 7.1 `aider/repomap.py`

吸收到：

1. repo intelligence
2. working set assembly
3. code slice expansion

不吸收：

1. 其产品级 CLI 展示逻辑

### 7.2 `aider/models.py` + `model-settings.yml`

吸收到：

1. model profile schema
2. alias resolution
3. weak/editor model tier
4. provider capability matrix

不吸收：

1. 直接依赖其原始数据格式作为 Polaris 长期内部真相

### 7.3 `aider/coders/chat_chunks.py`

吸收到：

1. prompt chunk taxonomy
2. cache-control strategy
3. chunk-level measurement

### 7.4 `aider/sendchat.py`

吸收到：

1. message normalization
2. provider-safe role alternation repair

### 7.5 `aider/history.py`

吸收到：

1. near-limit summary strategy

不吸收：

1. 取代 `roles.session + SessionContinuityEngine` 的主线

### 7.6 `aider` 的 Coder / Commands / SwitchCoder

不吸收：

1. 单体控制流
2. exception-based runtime switching
3. coder-type first 架构

---

## 8. 度量标准

外部成熟实现进入 Polaris 后，必须接受统一量化。

至少测以下指标：

1. 每轮最终发给 LLM 的 prompt token
2. repo intelligence 召回命中率
3. 重复读取同一文件次数
4. full-file read 占比
5. near-limit compaction 触发率
6. LLM summary 触发率
7. prompt cache hit ratio
8. 首字延迟与真实流式延迟
9. tool loop stalled 次数
10. debug receipt 完整度

没有这些指标，任何“更聪明了”的结论都不成立。

---

## 9. Phase 1 优先级

### P0

1. Repo Intelligence Engine
2. Model Capability Catalog
3. Prompt Chunk Assembler
4. Final LLM Request Debug / Receipt

### P1

1. Gradient Router
2. Near-limit LLM Summary plugin
3. Reasoning anti-injection
4. Tool Schema static validation

### P2

1. Markdown stream rendering
2. Interactive shell runtime hardening
3. token cost accounting
4. cache warming

---

## 10. 许可证与合规

当前第一份外部成熟样本 `aider` 为 Apache 2.0。

因此：

1. 可以受控复用实现或改写实现。
2. 直接复制或改编代码时，必须满足 Apache 2.0 的归属与许可证要求。
3. 更推荐“吸收机制 + Polaris 自主实现”，而不是大段原样移植。

---

## 11. 非目标

本蓝图当前不做以下事情：

1. 不把 Polaris 改造成 `aider` 风格单体 coder 产品
2. 不废弃当前 `roles.session` / `RoleRuntimeService` / `KernelOne` canonical 结构
3. 不把 repo intelligence 写成新的 graph truth
4. 不把所有 context/continuity 默认切到 LLM summary
5. 不因为 `aider` 有某个能力，就顺手把全部 feature surface 搬进来

---

## 12. 一句话裁决

Polaris 的正确方向不是“变成 Aider”，而是：

**把 Aider 中已经被验证过的 Repo Intelligence、Model Capability、Prompt Chunking、Message Normalization 等成熟机制，收编进 KernelOne，形成面向多角色、多域任务的共享 Agent Foundation。**
