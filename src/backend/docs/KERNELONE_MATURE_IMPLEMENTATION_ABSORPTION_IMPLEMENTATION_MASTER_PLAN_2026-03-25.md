# KernelOne 成熟实现吸收实施主计划

状态: Draft  
日期: 2026-03-25  
范围: `polaris/kernelone/context/`、`polaris/kernelone/llm/`、`polaris/kernelone/telemetry/`、`polaris/kernelone/process/`、`polaris/cells/roles/runtime/`、`polaris/cells/roles/kernel/`、`polaris/cells/context/catalog/`、`polaris/cells/workspace/integrity/`、`polaris/delivery/cli/`

> 本文是实施主计划，不是 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md`、各 Cell manifest 为准。  
> 本文从 `docs/KERNELONE_MATURE_IMPLEMENTATION_ABSORPTION_BLUEPRINT_2026-03-25.md` 派生，负责把“吸收成熟实现的正确部分”拆成可执行路线、可量化里程碑和验收门禁。  
> 当前第一份外部成熟样本为 `aider`。

---

## 1. 最终执行裁决

这条线的执行策略不是“搬一个外部项目”，而是：

`先清基座与边界 -> 再吸收高价值机制 -> 再做统一观测与量化 -> 最后再接入更高阶策略`

强制执行顺序如下：

1. 先修基础边界和缓存污染问题
2. 先落 Repo Intelligence / Model Catalog / Prompt Chunk 三个高 ROI 基座
3. 同步落最终请求级 debug / receipt
4. 再接 Gradient Router 和 near-limit LLM summary
5. 最后处理 shell / rendering / cost / cache warming 等增强项

---

## 2. 总体工作流

本计划分成六个工作流，但执行上是强依赖链，而不是平均并行推进。

### WS0. 基座清障与边界收口

目标：

1. 先把明显的基座污染点清掉
2. 为后续成熟机制接入预留正确扩展点
3. 确保新能力不会继续以 stopgap 形态落地

### WS1. Repo Intelligence Engine

目标：

1. 把 `aider` 的 repomap 机制升级为 Polaris 的代码智能/工作集底座
2. 替换当前“轻量 skeleton 但无排序/无图/无持久 cache”的弱实现

### WS2. Model Capability Catalog

目标：

1. 把 provider/model 差异从硬编码分支收成数据驱动画像
2. 为后续 weak model、reasoning、cache-control、cost hint 提供统一真相

### WS3. Prompt Chunk Assembler + Final Request Debug

目标：

1. 把 prompt 组装从字符串拼接升级为 chunk-aware 契约
2. 把“最终发给 LLM 的完整请求”稳定输出为 debug receipt

### WS4. Gradient Router + Near-limit Summary

目标：

1. 引入任务分层模型路由
2. 把 LLM summary 作为 near-limit 策略插件接入 continuity / compaction

### WS5. 增强项与长期硬化

目标：

1. 补 reasoning 反注入
2. 补 tool schema 静态校验
3. 评估 shell runtime、markdown stream、token 计费、cache warming

---

## 3. WS0 基座清障与边界收口

### 3.1 交付物

1. 修复 `polaris/kernelone/context/cache_manager.py` 中 `CacheTier.REPO_MAP` 的持久缓存目录映射错误
2. 明确 `repo intelligence` 的正式落点，不采用 `semantic/graph.py`
3. 明确 `prompt chunk` 与 `session continuity` 的职责边界
4. 补一份最小的结构验证测试，避免后续继续污染缓存/目录语义

### 3.2 核心动作

1. 把 `repo_map` 的缓存与 `session_continuity` 缓存完全隔离
2. 建立新的 `repo_intelligence` 包或明确的 façade + internal 模块结构
3. 在 `RoleRuntimeService` 和 `prompt builder` 之间预留 chunk-aware 接口
4. 审计当前 stopgap 工具回执和 read receipt 注入点

### 3.3 验收标准

1. `repo_map` 缓存不会再落到 `session_continuity` 子目录
2. 新能力的命名不会与正式 graph truth 混淆
3. runtime 已有明确接入 seam，后续功能可增量接入

---

## 4. WS1 Repo Intelligence Engine

### 4.1 目标

把 `aider/repomap.py` 里的高价值机制吸收到 Polaris，形成 `KernelOne Repo Intelligence Engine`。

### 4.2 推荐模块结构

1. `polaris/kernelone/context/repo_intelligence/facade.py`
2. `polaris/kernelone/context/repo_intelligence/tags.py`
3. `polaris/kernelone/context/repo_intelligence/ranker.py`
4. `polaris/kernelone/context/repo_intelligence/renderer.py`
5. `polaris/kernelone/context/repo_intelligence/cache.py`

### 4.3 功能切片

#### Phase 1A

1. 统一 tree-sitter `def/ref` 提取
2. 记录文件级 mtime / hash
3. 提供 tags 的持久缓存

#### Phase 1B

1. 建立 file-symbol 引用图
2. 支持 mention / symbol / active file personalization
3. 输出 ranked candidate list

#### Phase 1C

1. 提供 token budget 下的 repo map fitting
2. 提供 lines-of-interest 邻域渲染
3. 与 `working_set.py` 对接

### 4.4 不做的事

1. 不替代 `docs/graph/**`
2. 不直接改写 `context.catalog` 真相资产
3. 不把 repo intelligence 当作新的架构真相层

### 4.5 验收标准

1. 在相同任务下，重复 full-file read 次数下降
2. 候选文件排序明显优于当前启发式
3. repo map 构建具备持久缓存和增量刷新
4. LoI 渲染可被 prompt chunk assembler 消费

### 4.6 建议测试

1. unit: tags 提取
2. unit: rank personalization
3. unit: token budget fitting
4. unit: mtime cache 命中/失效
5. integration: “总结项目代码”场景下不再反复读同一文件

---

## 5. WS2 Model Capability Catalog

### 5.1 目标

把 provider/model 能力画像从“分散硬编码 + 局部 fallback”收成 KernelOne 的统一数据驱动目录。

### 5.2 推荐落点

1. `polaris/kernelone/llm/model_profiles/`
2. `polaris/kernelone/llm/engine/model_catalog.py`
3. `polaris/kernelone/llm/provider_adapters/`

### 5.3 功能切片

#### Phase 2A

1. 定义 Polaris 自己的 model profile schema
2. 支持 alias -> canonical model 映射
3. 支持 context window / output token / tokenizer / cost hint

#### Phase 2B

1. 支持 `supports_tools`
2. 支持 `supports_reasoning`
3. 支持 `supports_cache_control`
4. 支持 `streaming`
5. 支持 provider-specific accepts settings

#### Phase 2C

1. 支持 weak model / strong model / editor model 等 tier 描述
2. 与 `GradientRouter` 对接

### 5.4 处理原则

1. 不直接把 `aider` YAML 变成 Polaris 内部长期真相
2. 必须定义 Polaris 自己的 schema 与校验
3. 旧的 `model_resolver.py` 应逐步降级为兼容 facade，避免长期双真相

### 5.5 验收标准

1. provider/model 能力查询不再依赖大量硬编码 fallback
2. 新模型接入主要靠 profile 数据而不是改核心逻辑
3. 后续 weak model / summary / reasoning 行为可由 catalog 提供元数据支撑

---

## 6. WS3 Prompt Chunk Assembler + Final Request Debug

### 6.1 目标

建立 Polaris 的 chunk-aware prompt assembly，并把最终实际发给 LLM 的请求内容完整可观测化。

### 6.2 推荐落点

1. `polaris/kernelone/context/prompt_chunks.py`
2. `polaris/kernelone/context/prompt_chunk_assembler.py`
3. `polaris/cells/roles/kernel/internal/prompt_builder.py`
4. `polaris/kernelone/telemetry/debug_stream.py`
5. `polaris/kernelone/llm/engine/stream_executor.py`

### 6.3 功能切片

#### Phase 3A

定义 chunk taxonomy：

1. `system`
2. `examples`
3. `continuity`
4. `history_done`
5. `repo_intelligence`
6. `readonly_assets`
7. `working_set`
8. `current_turn`
9. `reminder`

#### Phase 3B

1. 支持 chunk 级 cache-control
2. 支持 chunk 级 token 统计
3. 支持 chunk 级裁剪/淘汰策略

#### Phase 3C

1. 输出最终实际请求的 debug receipt
2. 只输出最终送给 LLM 的完整内容，不重复打印中间态
3. 标记本轮使用的策略、是否压缩、是否启用 session continuity、是否启用 weak model

### 6.4 特别要求

必须满足用户此前对 debug/观测链路的要求：

1. 只关心最终给到 LLM 的完整内容
2. 避免重复观测数据
3. 请求内容、策略、压缩决策、continuity 决策都要能结构化显示

### 6.5 验收标准

1. 角色层不再拼接大字符串 prompt
2. debug 能稳定展示最终实际请求
3. chunk 级 token 分布可用于后续优化
4. 工具结果和 repo intelligence 可以按 chunk 单独保留/淘汰

---

## 7. WS4 Gradient Router + Near-limit Summary

### 7.1 目标

在不推翻当前 `roles.session + SessionContinuityEngine` 主线的前提下，引入更聪明的模型分层与 near-limit 压缩策略。

### 7.2 推荐落点

1. `polaris/kernelone/llm/engine/gradient_router.py`
2. `polaris/kernelone/context/session_continuity.py`
3. `polaris/kernelone/context/history_materialization.py`

### 7.3 功能切片

#### Phase 4A: Gradient Router

建议的任务类型：

1. `summarize`
2. `compact`
3. `plan`
4. `reflect`
5. `execute`
6. `finalize`

路由原则：

1. `summarize/compact/plan/reflect` 优先尝试 weak model
2. `execute/finalize` 默认 strong model
3. 缺少 weak model 时，自动回退主模型

#### Phase 4B: Near-limit Summary Plugin

1. 引入 head/tail 保留策略
2. 只有接近模型真实 context 上限时才触发
3. 摘要失败必须 deterministic 降级
4. 结果必须继续回到结构化 continuity pack 或 materialization 语义，而不是生成黑盒长字符串

### 7.4 不允许

1. 不允许“每轮都摘要”
2. 不允许让 LLM summary 取代原始 session source-of-truth
3. 不允许把连续压缩做成不可解释黑盒

### 7.5 验收标准

1. 压缩只在 near-limit 触发
2. LLM summary 使用率受控且可量化
3. 规划/摘要类任务的 token 成本明显下降

---

## 8. WS5 增强项与长期硬化

### 8.1 Reasoning 反注入

交付物：

1. 随机 reasoning tag
2. 输出前重写
3. 入 history 前剥离

### 8.2 Tool Schema 静态校验

交付物：

1. JSON Schema 导出
2. 启动期校验
3. CI 校验

### 8.3 Shell Runtime 强化

交付物：

1. 评估交互式 shell adapter
2. Windows / Unix 分平台行为清晰

### 8.4 Markdown Stream / Delivery 渲染

交付物：

1. CLI 流式显示增强
2. 与真实流式输出链路一致，不做伪流式限速

### 8.5 Token Cost / Cache Warming

交付物：

1. provider-specific cost accounting
2. 可选 prompt cache warming

这些增强项必须排在 WS1-WS4 之后。

---

## 9. 执行顺序

推荐严格按以下顺序推进：

1. WS0 基座清障
2. WS1 Repo Intelligence
3. WS2 Model Capability Catalog
4. WS3 Prompt Chunk Assembler + Final Request Debug
5. WS4 Gradient Router + Near-limit Summary
6. WS5 增强项与长期硬化

理由：

1. 没有干净缓存与模块边界，后续能力会落成新 stopgap。
2. 没有 repo intelligence，代码域的“更聪明”只会继续依赖 prompt 硬扛。
3. 没有 model catalog，weak model / reasoning / cache-control 无法稳定落地。
4. 没有 chunk assembler 和 final request debug，就无法精确量化任何优化。
5. 没有前四步，LLM summary 和 gradient router 很容易再次变成不可观测黑盒。

---

## 10. 量化基线与评测

### 10.1 每阶段必测指标

1. 每轮最终 prompt token
2. full-file read 次数
3. 重复读取同一文件次数
4. repo intelligence cache hit ratio
5. prompt cache hit ratio
6. near-limit compaction 触发率
7. LLM summary 触发率
8. 首字延迟
9. 流式总时延
10. tool loop stalled 次数

### 10.2 最小对照场景

1. “总结这个项目代码”
2. “找出为什么循环读取同一文件”
3. “完善某个复杂模块并给出计划”
4. “长会话后继续执行未完成任务”

### 10.3 成功标准

1. 在相同任务下，重复读文件问题被系统性压制
2. 工具回执和上下文组装可被清晰解释
3. 最终给到 LLM 的请求内容稳定可观测
4. 近上限压缩不再提前、频繁、黑盒触发

---

## 11. 主要风险与防御

### 风险 1

把成熟实现当作整块产品搬运，导致 Polaris 再长出第二内核。

防御：

1. 只吸收机制，不吸收产品控制流
2. 所有能力都必须回到 KernelOne / role runtime canonical 接入面

### 风险 2

把 repo intelligence 误用为 graph truth 替代。

防御：

1. 明确命名不使用 `semantic/graph.py`
2. graph 真相继续只在 `docs/graph/**`

### 风险 3

LLM summary 重新演变成默认黑盒压缩。

防御：

1. 只允许 near-limit 触发
2. 必须可量化、可回退、可观测

### 风险 4

新能力落在 Director 私有逻辑，未来其他角色无法复用。

防御：

1. 强制先落 KernelOne
2. role 只做 overlay，不拥有底座机制

---

## 12. 计划完成态

该计划完成后，Polaris 应形成如下稳定结构：

`roles.session truth -> RoleRuntimeService -> KernelOne repo intelligence / model catalog / prompt chunks / continuity / gradient router -> final request debug receipt -> provider execution`

届时：

1. Polaris 不再依赖零散 stopgap 去“临时变聪明”
2. Director/Coder/Writer 等未来角色都能共享同一基础能力
3. 外部成熟实现的吸收会成为一套可持续、可审计、可量化的演进机制

---

## 13. 一句话执行口径

先把基座建对，再把成熟机制吸收进去；先让它可观测、可比较、可复用，再追求“更聪明”。
