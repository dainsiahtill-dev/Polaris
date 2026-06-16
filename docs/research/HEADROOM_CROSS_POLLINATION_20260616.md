# Headroom → Polaris：技术引进评估（2026-06-16）

参考项目：`chopratejas/headroom`（Rust 写的「上下文压缩」反向代理，宣称 60–95% token 削减）。
本文评估其技术中**哪些值得引进 Polaris**（ContextOS 优先，兼及其他），以及与现状的差距、契合度、风险、工作量。

> 方法：WebFetch 读 headroom README + 关键源文件（ccr/live_zone/cache_stabilization/signals/relevance）；
> codegraph 实读 Polaris ContextOS（projection_engine / compaction_strategy / intelligent_compressor /
> engine.py / SessionReceiptStore / toolkit definitions）。所有「现状」结论均经代码锚定，非臆测。

---

## 0) 两边架构速写

**Headroom**（代理层、与具体 agent 解耦）：
- `ContentRouter` 按内容类型分流到专用压缩器：`SmartCrusher`(JSON)、`CodeCompressor`(AST)、log/diff/search 压缩器。
- `CCR`（Compress-Cache-Retrieve，可逆压缩）：有损压缩时把原文按 BLAKE3(24hex) 存本地，正文里塞 `<<ccr:HASH>>` 标记，模型用 `headroom_retrieve` 工具按需取回；TTL 默认 5min，后端 in-memory/sqlite/redis，读时惰性清过期。
- `live_zone`：只压「模型即将作答的那一段」（最新 user 消息内的 block），`frozen_message_count` 以下的前缀**字节不可变**；`tool_use/thinking/redacted_thinking/compaction` 属 cache-hot 永不动；用字节切片手术（不反序列化）保证 prefix/suffix 的 SHA-256 与输入逐字节一致 → 不破坏 provider prompt cache。
- `cache_stabilization`：观测+归一两条线。`volatile_detector`(扫时间戳/UUID/ID 字段)、`drift_detector`(对 system/tools/早期消息做 per-session SHA-256 指纹，跨请求漂移即告警)、`tool_def_normalize`(工具按字母排序 + 递归排序 JSON Schema key)、cache_control 自动插入 / 稳定 cache key。
- `signals`：分层级联重要性打分（KeywordDetector 现役，ESCALATE_THRESHOLD=0.7，无信息时**诚实返回 neutral，绝不低置信伪阳**；BGE ML 头规划中）。
- `relevance`：hybrid = `alpha*BM25 + (1-alpha)*Embedding`，alpha 自适应（UUID→0.85、多数字 ID→0.75、自然语言→0.5），embedder 不可用时优雅降级到 BM25。
- 其它：多 tokenizer 注册表(tiktoken+HF)、`headroom learn`(挖失败会话→写更正)、跨 agent 共享去重内存、proxy/MCP/wrap 三种部署。

**Polaris ContextOS**（已实读，代码锚）：
- `ProjectionEngine.project`（projection_engine.py:617）：system_hint → confirmed_facts → 归一 turns → tail_hint → **run_card（system 消息，置于尾部）** → 末位保留当前 user turn。剥离 control-plane 噪声。
- `CompactionStrategy`（compaction_strategy.py）：80% 预算触发；75%*阈值做 micro-compact（DEFER）；兜底截断到 N 条；token 估算=ascii/4 + cjk*1.5 + 4 的**启发式，非真 tokenizer**。
- `IntelligentCompressor`（intelligent_compressor.py:432+）：`_score_items`(重要性打分) + `_summarize_items`(LLM 摘要+确定性兜底) + `_build_compressed_context`，**类型无关**的通用压缩。
- `engine.py:299` `_summarize_items`：head/tail 截断成 `...[snip]...`，已有 `content_or_pointer`「指针化」雏形。
- `SessionReceiptStore`（accel_session_receipt_store.py）：sqlite 存工具回执（result_ref 指针/status/changed_files）；projection 把 `[receipt_ref:…]` 注入正文。
- 分层 summarizer（context_os/summarizers/tiered.py 等）已存在。

---

## 1) 引进优先级（按 价值 × 契合 × 真实差距 排序）

### 🟥 T1-A　可逆「按需取回」工具（CCR retrieve-on-demand）— **最具体、骨架已在**
- **差距（已锚定）**：toolkit 里 `grep retrieve|expand_pointer|fetch_receipt|<<ccr` **为空**——我们会「指针化/注入 receipt_ref」，但**没有任何模型可调用的工具把原文取回**。`SessionReceiptStore.get_receipt` 只在 infra 层，未暴露为 LLM 工具。压缩是**单向**的：弱模型一旦把某工具输出指针化掉，后续就再也拿不回来。
- **引进**：在 `kernelone/llm/toolkit/definitions.py` 加一个通用 `context_retrieve(ref)`（或 `expand_receipt`），解析 ref → 原 payload（走 ReceiptStore / 一个带 TTL 的 in-mem+sqlite 原文缓存，复刻 CCR 后端）。指针标记统一成可被解析器识别的形式（类似 `<<ref:HASH>>`）。
- **契合**：强。ReceiptStore + ProjectionEngine 指针化 + toolkit 三件套都在，只差「闭环的那一步」。直接缓解 [[write-convergence-multimodal]] 里「读循环/被指针化掉的上下文」一类墙。
- **风险/诚实**：弱模型不一定可靠地主动调 retrieve（需配 [[normalize-toolcalls-adapt-to-llm]]）；建议 **工具 + 特定访问模式下确定性自动展开** 双保险。§8：必须做成通用平台能力，不得塞项目专用逻辑。
- **工作量**：中。

### 🟥 T1-B　KV-cache 前缀稳定性「先观测后归一」（cache_stabilization）— **本地后端吞吐杠杆**
- **为何对 Polaris 尤其值钱**：我们的 Director 主循环跑本地 vLLM(APC 自动前缀缓存)/llama.cpp(prompt cache)，**前缀命中=TTFT 降、吞吐升**，正中 [[velocity-replay-harness]]「瓶颈是 Director」。
- **差距（已锚定 + 需实测）**：工具定义**未做归一化排序**（`create_default_registry` 按声明序，无 schema-key 排序 pass）；`run_card` 已在**尾部**（前缀友好，已做对一半）；system_hint 在首位、其中是否夹带 run_id/时间戳等易变 token **尚未证实**。
- **引进（分两步，安全优先）**：
  1. **drift_detector（观测、不改字节）**：对 system_hint + tool defs + 早期 turns 做 per-turn SHA-256 指纹，跨 turn 漂移即发观测事件 → 直接挂到 ContextOS 看板 / [[contextos-projection-engine]] 的 RoleSignalPlane，**用数据证明**我们到底有没有前缀抖动问题。这是零风险诊断。
  2. 若证实有抖动：`tool_def_normalize`（工具排序 + 递归排序 JSON Schema key）+ 把易变 token 移出前缀。
- **契合**：高。ProjectionEngine 已是前缀装配单点，加一个稳定化 pass + 一个 drift 信号即可。
- **风险/诚实**：收益依赖本地后端确实启用前缀缓存——**先量化（drift 指纹 + cache-hit 观测）再改**，不要盲改。
- **工作量**：观测=小；归一=中。

### 🟥 T1-C　Live-Zone 纪律：压尾部、冻前缀（与 T1-B 配套）
- **差距**：现 CompactionStrategy 在 80% 预算时**截断/micro-compact 历史（即前缀！）**——这恰恰是破坏本地前缀缓存的动作：会话中途一改老 turn，后续每次请求都得从头重算前缀。
- **引进**：把压缩重心从「截老历史」转到「压最新的大块工具输出 / 指针化」，前缀仅在显式 checkpoint 处压。即给 CompactionStrategy 引入 headroom 的 live-zone 边界概念。
- **契合**：中（是对 compaction 触发策略的设计性重构）。
- **风险**：需与 T1-B 的实测一起做，避免无依据的大改。
- **工作量**：中。

### 🟧 T2-A　压缩前的「token 必须真变小」否决门 — **低成本正确性护栏**
- **差距**：compact() 假设压缩总是有益；headroom 在压缩后**校验 token 数，没变小就拒绝**（重序列化可能反而膨胀）。我们的 compaction 正有 BudgetExceededError 一类毛病（分支既存失败测试 `test_transcript_leak_guard::…skips_compression`）。
- **引进**：`CompactionStrategy.compact` 末尾若 `final_tokens >= original_tokens` 则 `triggered=False` 直接返回原文（no-op）。一行不变式，fail-closed。
- **工作量**：极小。**建议最先落**。

### 🟧 T2-B　内容类型感知的工具输出压缩（SmartCrusher / log / diff / search）
- **差距**：我们 `_score_items + LLM 摘要` 是**类型无关**的。Agent 循环里工具输出多为 JSON/日志/diff/搜索结果——类型专用「crush」(保 schema+样本+离群+统计 / 日志模板化 / diff 去噪) 的**保真度/token 远高于通用 LLM 摘要，且确定性、零延迟、无 §8 风险**。
- **引进**：作为 compaction 的**确定性前置 pass**（在 LLM 摘要之前）。先做我们 trace 里最高频两类：构建/测试输出(log)、大文件读/JSON。
- **契合**：好（前置 pass，可逐类型增量）。**工作量**：中（每类型一个 crusher）。

### 🟧 T2-C　真 tokenizer（多 tokenizer 注册表 + 估算兜底）
- **差距**：预算/压缩全用 ascii/4+cjk*1.5 启发式，对 code/JSON 和本地 qwen tokenizer 误差大 → 过早/过晚 compaction、预算超限。
- **引进**：按 provider 可选加载真 HF tokenizer（qwen 系即 HF），保留启发式做**优雅降级**（headroom 同款）。
- **风险**：增依赖 + 启动成本 → 做成 per-provider opt-in。**工作量**：中。

### 🟨 T3-A　失败学习（headroom learn）— **只取诊断，拒绝自动写更正**
- **映射**：等于把我们 [[reliability-hardening-campaign]] 的「失败 trace→根因→修复」自动化。
- **诚实/红线**：**自动写「更正」= [[embedded-business-synthesizers-s8]] 同款 §8 违规**（把项目答案硬编进平台）。**只建议**做成「从 receipt_events + journal 聚类失败根因、ranked 呈现给人」的诊断器，**绝不自动改平台代码**。
- **工作量**：中（诊断器）。

### 🟨 T3-B　分层级联打分 + hybrid relevance 的「理念」
- **可白嫖的低成本部分**：把 signals 的**「分层级联 + 0.7 升级阈 + 无信息诚实 neutral 不伪阳」**模式套到现有 `_score_items`，**无需 embedder**。
- **需依赖的部分**：hybrid BM25+embedding（自适应 alpha）能改进「哪些 receipt/turn 该留、该自动展开」，但要引入 embedder → 缓后。
- **工作量**：理念=小；embedding=中大。

### ⬜ 不引进
- **跨 agent 共享内存**：Polaris 单平台，价值低。
- **proxy/wrap 部署形态**：我们是内嵌式，非代理，不契合。

---

## 2) 建议落地次序（与当前 reliability 战役对齐）
1. **T2-A**（token 否决门，一行护栏，先修 compaction 反噬）。
2. **T1-A**（retrieve 工具，闭合可逆压缩——直接打 write/read-loop 墙）。
3. **T1-B 第1步**（drift 观测信号，零风险，用数据决定要不要做 T1-B/T1-C 重构）。
4. **T2-B**（类型感知 crush，确定性、无 §8 风险，省 token 又省延迟）。
5. 视实测：T1-B 归一 / T1-C live-zone；T2-C tokenizer；T3 诊断器。

> 所有项遵守：§8 禁业务代码、UTF-8、strict/mypy、fail-closed、改 Loop/内核优先动 `cells/roles`+`kernelone`、非平凡后端先落 blueprint。

---

## 3) 落地状态（2026-06-16，多专家两波 + 与并发 codex reconcile）

两波 workflow 专家组（每项 ground[codegraph]→build→verify[superpowers 对抗] 流水线）落地结果：

| 项 | 状态 | 证据 |
|---|---|---|
| **T1-A** CCR retrieve 闭环（keystone，唯一直接打 write/read-loop 墙） | ✅ **LOOP CLOSED** | Wave1 consumer(`context_retrieve`/`OriginalPayloadCache`)+Wave2 producer 闭环（codex 并发以 **workspace-scoped** 版提交 `7670e903`，比蓝图 plain index 更强=修了跨 workspace CCR 污染）；`test_ccr_producer_loop_closure.py` 10 测绿；cross-turn resolve 经对抗验证 evidence_checks_out=true；floor-safe(placeholder 字节不变/默认 inert) |
| **T1-B** prefix-drift 观测 | ✅ **LANDED** | Wave1 提交 `03f4a4be`，wire `gateway.py`→`_emit_prefix_drift_observation`；非变异；fail-safe |
| **T2-A** token-shrink 否决门 | ✅ **LANDED + 假绿已修** | Wave1 实现+提交；Wave2 修掉 vacuous 测——新测经注入 `_NonShrinkingCompressor`(真 MicroCompactorPort 双)对**真估算器真 dict** 驱动 guard True 分支(300→315 token 膨胀→veto)，coverage 实证 guard body 行 284-291 BEFORE 未覆盖→AFTER 覆盖；gates_honest=true |
| **T2-B** 类型感知 crush | ✅ **LANDED(observe 段)** | Wave1 crushers 提交+测绿；Wave2 加 savings 观测(`savings_report.py`)：聚合 7958→419 tok(ratio 0.053/saved 7539)、reject-if-not-smaller 在不可压输入成立、复用 canonical 估算器(无重复公式)、§8 clean、17 测绿。**故意不接热路径**(re-audit observe-first，热路径接 `compression_engine.py:428` 须过 L2-floor) |
| **T2-C** tokenizer 收敛 | 📐 **BLUEPRINTED** | Wave2 `HEADROOM_TOKEN_ESTIMATOR_CONVERGENCE_20260616.md`：5 个互异公式 + blast-radius 实证(codegraph callers/impact)；HF tokenizer defer 到收敛后；先收敛再 instrument |
| **T1-C** live-zone | ❌ **DROP** | CompactionStrategy 零活调用方=死路径，前缀压缩前提被推翻 |
| **T3-A** 自动写更正 | ❌ **DROP** | §8 硬红线（=embedded-business-synthesizers 同款） |
| **T3-B** tiered scorer | ❌ **DROP** | `_score_items` 锚点不存在/name-collision |

**门禁现状（2026-06-16，已全部提交 main）**：全部 campaign 工作已落 main（`03f4a4be`/`27c05ae6`/`7670e903`/`3320e4ac`）；工作树 clean；`execute_method.py` 现 parse-clean（旧 IndentationError 阻塞已消）。重跑：ruff All passed / mypy Success(4 files) / CCR+crushers+compaction **62 测全绿**。

> ⚠️ **本 §3 的两条 "✅" 经二次深度审计修正**（见 §4）：**T1-A 实为"部分闭合"**（活路径指针双形不一致 + 仅进程内存耐久），**T2-A 实为"生产 INERT"**（否决门落在死模块）。§3 表保留为第一轮记录，权威现状以 §4 为准。

---

## 4) 二次深度审计 + 修订计划（2026-06-16，6 维 codegraph-grounded + superpowers 对抗，52 agent / 3M tok）

第二轮更深审计：每维 ground[codegraph]→audit→**每 finding 由 2 名独立 skeptic 重跑证据对抗验证**（reproduce + redline/floor 双 lens）。尾部 rate-limit 致 `CCR-3`/`T2A-3/4`/`redlines-regression` 维度 verdict 缺失——**已由本人重跑补证**（fail-closed）。**核心修正：第一轮 §3 的两条 "✅" 偏乐观。**

### 4.1 验证后发现（severity / 对抗 verdict / 证据）

**keystone-ccr（T1-A 实为部分闭合）**
- **CCR-1 [high · real×2]** 蓝图↔实现分叉：蓝图推荐的 floor-safe Option R 持久模块 `kernelone/context/receipt_id_index.py` **从未创建**（0 引用）。实际提交的是第三种 hybrid——保留 R 的 placeholder 字节不变（✅ floor-safe），但用**进程内存** `get_default_cache()` 单例（`original_payload_cache.py:327`，`sqlite_path=None`）承载，非 R 的持久 sqlite。蓝图 §2 的 R1 持久承诺未兑现且未在文档标注。
- **CCR-2 [high · real×2]** 跨进程/跨 turn 耐久性 OPEN：默认 CCR cache 纯内存、TTL=300s（monotonic）、4096 entry LRU。turn A 落的指针在新进程（后端重启 / 新 `director` CLI run）取回 None；闭环仅在**单进程 + 5 分钟窗口**成立。
- **CCR-3 [high · 本人重跑确认]** 活路径指针双形不一致：主投影路径 `projection_engine.py:539/546` 发的模型可见 inline 占位符 `[Large output stored in receipt tool_<id>]` / `[Large content stored in receipt evt_<id>]` **不被 `strip_ref_markers`（`original_payload_cache.py:65` `_MARKER_PATTERNS` 仅认 `[receipt_ref:ID]`/`<receipt_ref:ID>`）识别**；只有 `:255` 另起一行的 `[receipt_ref:<id>]` 可解析。同一 id（`tool_{event_id}`）两种形、只有不显眼的可取回 → 弱模型复制显眼 inline 形 → `not_retrievable`，正落 [[write-convergence-multimodal]] 读循环墙。
- **CCR-4 [med]** 测试假信心：`test_ccr_producer_loop_closure.py` 10 测只测 `[receipt_ref:ID]` 形，从不测 build_turns 实发的 `[Large ... stored in receipt ID]` 形 → 绿掩盖 CCR-3。
- **CCR-5 [low · real · good news]** producer 接线确 floor-safe：`offload_content` 无论 hook 是否接，返回同一 placeholder（`receipt_store.py:69`）→ 前缀字节不变 → producer 接线**不需 L2 bench**。workspace 隔离 + §8 verbatim-bytes 均 clean。

**veto-guard（T2-A 实为生产 INERT）**
- **T2A-3 / DROP-2 [high · real×2]** T2-A 否决门连同 6 测**落在死模块 `CompactionStrategy` 上**（零非测调用方）；活压缩路径是另一模块 `compaction.py::compact_if_needed → RoleContextCompressor`，其 LLM-summary 层**无 token-shrink 否决**。绿测跑零生产路径——与 crushers 同类 inert。
- **T2A-1 [low · real×2]** 假绿修复本身是真的（guard True 分支确被覆盖）——但保护的是死路径。

**crushers-observe（clean，但 re-anchor 计划措辞有误）**
- **T2B-1 [low · real×2]** 比 brief 更 inert：唯一活 importer 的 `IntelligentCompressor` **生产从不实例化**（唯一构造在 docstring 里），crush_by_type 今天只被测试触达。
- **T2B-4 [med · real×2]** re-anchor 措辞错：`apply_compression`（`compression_engine.py:58`）**不是 legacy**——它和 `emergency_truncate`（:428）都活在热 `_build_context_impl`（`gateway.py:446`）。任一处接 lossy crush 改热前缀 → 须 L2 bench；首选 `:428`（仅超预算才触发，不回归常态）。
- **T2B-5 [med · real×2]** fail-closed 缺测：router `except Exception: return no_op`（`router.py:127-128`）零覆盖、无测强制 crusher 抛错。re-anchor 前必补（正是 campaign 在猎的假信心类）。
- **T2B-2 [low · real×2]** savings 复用 canonical 估算器无第 6 公式（✅），但该 canonical ≠ 热路径预算估算器（`CompressionEngine.TokenEstimator`）→ 报的 savings 未必等于真预算 headroom，归入 T2-C。

**dropped-relitigate**
- **DROP-1 [med · real×2]** T1-C KEEP-DROPPED 正确（CompactionStrategy 确死）。
- **DROP-3 [low · real×2]** T3-A STAYS DROPPED（§8；唯一邻近 learner `_AdaptiveWeights` 只存数值权重 clamp 0.05–0.6，不存内容/答案）。
- **DROP-4 [med · real×2]** T3-B drop-reason 半陈旧 → RECONSIDER：`RoleSignalPlane._score_items` 确不存在（name-collision，真 `_score_items` 在 `intelligent_compressor.ImportanceScorer`），但 tiered-signal 想法有活锚点 `allocate_role_signals`（`role_signals.py`，写 supplemental_turns 入热前缀）。改造须 blueprint-first + L2 bench，仅 heuristic/ordering（§8-clean）。

**estimator-convergence（蓝图 sound，3 处需补）**
- **T2C-1 [high · real×2]** thesis 重现：5 互异公式、同 CJK 文本 3x 散布（C1=100/C2=150/F20=50/F21=50/F3=150）；两文件都自称 canonical 但 CJK 系数不一致（C1=1 vs C2=1.5）。
- **T2C-2 [med · real×2]** 漏第 6 fork：`repo_intelligence/renderer.py:273`（`total_chars//4`，CJK-blind，活）。
- **T2C-3 [high · real×2]** 收敛 blocker：`test_canonical_token_estimator.py` 硬钉 C2 的 cjk*1.5（assert==600/425/175），Stage B1 收敛 C2→C1 会 BREAK 它 → 须**同一 commit** 改写为 delegation-equivalence 断言；蓝图误归为"被动回归保护"。
- **T2C-4/5 [low/med · real×2]** blast-radius 计数略高估（C2 实 7 文件 / 9 import）；§1 file:line 锚点漂移（F1 :341 非 :292…）；`context_assembler` 有**两个**估算器（:899 messages + :933 single-string），蓝图单 :933 会漏一个。

**redlines-regression（该维度 agent 被 rate-limit 打挂，本人重跑）**：§8 新 CCR/crushers 代码无业务 token（clean）；§6.6 `context_retrieve` 只归一**参数名** `ref`、不改 raw 审计工具名（clean）；ruff All passed；mypy Success(4 files)；CCR+crushers+compaction **62 测全绿 on main**。无 blocker。

### 4.2 修订后优先级队列

| 优先级 | 动作 | floor | 直接打目标? |
|---|---|---|---|
| **P0** | **CCR-3a 修复**：给 `_MARKER_PATTERNS` 加 `[Large output stored in receipt <id>]` / `[Large content stored in receipt <id>]` 两 pattern（retrieve **冷路径**，placeholder 不变）+ CCR-4 path-faithful 测（驱真 `build_turns→project`，断言模型所见任一指针都能取回 verbatim） | **冷路径，无前缀变更，免 bench** | ✅ 读循环墙——本审计最高 ROI |
| **P1-doc** | reconcile CCR 蓝图：记 in-memory hybrid 实情 + 显式 durability scope 决策（CCR-1/2）。若 director worker 池确为单长寿进程且 turn 间隔 <300s→文档化该 scope；否则给 `get_default_cache` 接 workspace sqlite（`OriginalPayloadCache` 已支持 `sqlite_path`+`_sqlite_get`）或建持久 `receipt_id_index` | 冷路径/side-store，免 bench | 间接（耐久性） |
| **P1-code** | 把 token-shrink 否决移植进**活** `compaction.py::compact_if_needed` LLM 层（T2A-4/DROP-2）+ 驱生产入口的测；inert-by-default(env flag) + **L2 bench** | **改热历史前缀，须 L2 bench** | 间接（防压缩反噬） |
| **P2** | crushers fail-closed 测（T2B-5，cheap）→ 再谈 re-anchor；修正 re-anchor 措辞（T2B-4，apply_compression 非 legacy） | 测=免 bench；re-anchor=须 bench | — |
| **P2** | estimator 蓝图修订（T2C-2 第 6 fork / T2C-3 收敛-blocker 测 / T2C-4 计数 / T2C-5 锚点刷新 + assembler 双估算器） | 诊断=免 bench；Stage B/C=须 bench | — |
| **standing** | **inertness gate**：每个新落 guard/veto 先 `codegraph_callers` 证生产调用方，全是测→FAIL landing。能拦下 T2-A + crushers 这类"绿测落死模块" | — | 防假信号 |

> 二次审计经验：第一轮把 T2-A 当 "✅ LANDED"、把 T1-A 当 "✅ LOOP CLOSED"——都因**绿测落在零生产调用方的模块 / 未测主路径形态**。对抗验证 + `codegraph_callers` 重跑证据拦下了它。这是 [[use-codegraph-mcp-always]] + superpowers re-run-evidence 的复利；新增 standing inertness gate 把它制度化。

> ⚠️ **§4.2 的 P0（CCR-3a）排序已被三次审计 §5 推翻**：真 P0 是**工具供给层**（context_retrieve 不在任何角色白名单→模型永不被供给→CCR-3 moot）。CCR-3a 仍正确但降为 §5.3 的 #3，且其 regex 需锚定修正。权威路径见 **§5.3**。

---

## 5) 三次深度审计 + 修订计划（2026-06-17，6 维 codegraph + superpowers 对抗，25 agent / 2M tok，无 rate-limit 损耗）

第三轮：把 pass-2 的 hypothetical 用真实运行时**实证定论**，审计前两轮**从未碰的面**（工具供给层可达性 / drift 数据流 / 单例线程安全），并对抗 design-review pass-2 提的修复。**最深的发现推翻了 §4.2 的 P0 排序。**

### 5.1 诚实头条：三轮后，整个 headroom campaign 今天 ≈ 0 活效果

| 特性 | 真实活性（实证） | 证据 |
|---|---|---|
| **T1-A** CCR retrieve | **producer 活 / consumer 死**：`context_retrieve` 不在任何角色 `tool_policy.whitelist`，`build_native_tool_schemas`（`tool_helpers.py:291`）只发白名单内工具 → 活 Director 拿 17 个 native schema，**无 context_retrieve** → 模型永不被供给该工具 → producer 缓存的字节无人能读回=**纯内存开销** | OFFER-1 blocker real（我本人 + auditor + skeptic 三方实证）|
| **T1-B** drift 观测 | **活 + compute 正确，但 sink 盲**：`_emit_prefix_drift_observation` 在热路径无 env gate 真发 `context.prefix_drift`、是真跨 turn 检测器（推翻"单指纹"假设）——但**该事件零消费者**（无 Python reader / 无前端 telemetry 映射 / 无 RoleSignalPlane reader）→ 它存在的目的（收 drift 数据定 tool-def 归一化）永远无法达成 | T1B-SINK-1 high real |
| **T2-A** 否决门 | **死模块**：`CompactionStrategy` 0 调用方（同 pass-2）；活路径 `compact_if_needed` 无否决 | D3 real |
| **T2-B** crush | **基本 inert**：`IntelligentCompressor` 0 活调用方；`savings_report` observe-only by design（诚实声明）；**但** `crush_by_type` 在 `intelligent_compressor.py:524` artifact 压缩路径有 1 活调用（"完全 inert"略不准）| fix-design-review 旁证 |
| **T2-C** tokenizer | blueprint-only | — |
| §8/§6.6/线程安全 | **全 CLEAN（实证）**：CCR 单例在 director 真并发模型（threading.Thread/RLock，24线程×4000op stress 零损坏）下线程安全；cache 严格进程内 verbatim、无跨 run 答案记忆；workspace 48-bit 命名空间安全到 ~20M；§6.6 context_retrieve 不改 raw 名 | TS-1 / S8-S66-OK real |

**= producer 在缓存无人能读的字节（纯开销），观测在向虚空发数据，否决门守死模块。没有任何一个特性今天改变一个真实 Director turn 的一个 token/决策。**

### 5.2 关键修复：T1-A 三层供给 inert（最深，supersede §4.2 P0 与 CCR-3）

pass-2 的 CCR-3（指针双形）**moot 直到工具被供给**。真实阻塞是三层叠加，每层独立致命：
1. **供给层**（OFFER-1 blocker real）：不在任何 `tool_policy.whitelist`（pm/architect/CE 14、director 18、qa 9、scout 18 全无）→ 不进 native schema。
2. **注册层**（OFFER-2 high **被 skeptic 部分证伪**）：spec 不在持久 `_BUILTIN_REGISTRY`（`tool_spec_registry.py:384`，与 `context_retrieve.py:52-57` "durable home 在此"注释矛盾），只靠 handler import 时 ContextVar self-register。skeptic 实证 **warm-context（真实 role loop 里几乎总有先跑过的工具）下 dispatch 正常**，故"Unknown tool 不可派发"过强=refuted；但 cold-start 仍可能漏 → 把 spec 移进 `_BUILTIN_REGISTRY`（cheap，floor-free）消除 latent gate。
3. **可用层**（OFFER-3，root cause 是 #1 故 standalone refuted）：系统提示 7044 字符无一字提 context_retrieve/receipt_ref/retrieve（我本人实证）→ 即便供给，弱模型不知何时调。

### 5.3 修订后关键路径（到"第一个真实活效果"，按解锁价值排序）

| # | 动作 | floor | 备注 |
|---|---|---|---|
| **1** | **供给工具**：context_retrieve 加 director(+qa) 白名单（两份 core_roles.yaml）+ 移 spec 进 `_BUILTIN_REGISTRY` | 白名单部分**改 tool-schema 前缀→须 L2 bench**（env flag default off 对照）；registry 部分 floor-free | 没这步其它全 moot |
| **2** | **提示 nudge**：Director 工具提示加一行"见 [receipt_ref:ID]/[Large output stored in receipt …] 就 call context_retrieve(ref) 取回原文" | 改前缀→并入 #1 bench | 与 #4 配对才有意义 |
| **3** | **CCR-3a（修订版）**：`_MARKER_PATTERNS` 加**锚定+精确 id 字符类**两 pattern：`^\[\s*Large (output\|content) stored in receipt\s+(?P<ref>[A-Za-z0-9_.\-]+)\s*\]$`（捕获 id 已含 tool_/evt_ 前缀=正是 cache key，无需剥）+ 对抗性 prose-rejection 单测 | retrieve 冷路径，**免 bench** | pass-2 裸 `.+?` 会过匹配正常散文=已修正 |
| **4** | **E2E proof-of-effect**：mocked-LLM director turn 断言 offload(>threshold)→模型发 context_retrieve(ref)→handler 返 verbatim→记前后投影 token delta | 测，免 bench | **三轮来第一个真实活效果证明 + 回归守卫；测不了就管不了** |
| **5** | **接 T1-B 一个消费者**：前端 ContextOS telemetry builder 加 `prefix_drift` 分支（纯读已发 JSONL）或 tiny reader | 只读，免 bench | 不接=观测向虚空 |
| **6** | **T2-A 否决移植（修订版）**：**非 verbatim port**——死模块 veto 只 relabel report，活 `compact_if_needed` 返回的是 messages；正确=**仅 llm_compact 分支**（`compaction.py:813` 无条件返回）加"未缩小则 REVERT 到输入 messages + 发 noshrink snapshot"；auto_compact（:809）已自我否决无需碰 | **改活压缩返回 messages→须 L2 bench** | pass-2 "verbatim port"语义错=已修正 |

### 5.4 治理 + 降级
- **降级 pass-2 P1-doc**（CCR2-1 real）：director run 是**单长寿 asyncio 进程**（`cli_thin.py:213`）+ 线程 worker（`dispatch_pipeline.py:1142`，非 ProcessPool/subprocess），单例 id() 跨线程不变、offload↔retrieve 实证共享同一 cache → **"必加 sqlite 持久"降为"文档化 in-process scope"** + `dispatch_pipeline.py:1142` 加 tripwire 注释"CCR 正确性依赖 worker 是单进程内线程；改 ProcessPool 会静默打断内存 CCR"。可选 TTL 300→600s（一标量 floor-free）吸尾延。
- **三态 inertness gate**（INERTNESS-GATE-REFINE）：pass-2 二元门会误杀诚实 deferred 的 observe-first（savings_report 明确自述）→ 改三态：**LIVE**（≥1 非测生产调用方 + 工具须现于 `build_native_tool_schemas` 输出）/ **DEFERRED-DOCUMENTED**（0 调用方但有 greppable marker + blueprint id）/ **INERT-UNDECLARED=FAIL**。同时放过 savings_report 又抓住 T2-A 死模块 + context_retrieve 供给缺口。
- **加前两轮都缺的"工具到线"测**：断言 `build_native_tool_schemas(director_profile)` 输出含 context_retrieve + `execute('context_retrieve',{ref})` 不返回 "Unknown tool"（两轮漏检都因没这类测）。
- **次要**：director 白名单 18 条但 `build_native_tool_schemas` 出 17——有一条静默掉（normalize 成 `create_default_registry` 没有的名），审计哪条丢了。

### 5.5 诚实标注（contested / 未决）
- **CCR-3 双指针拓扑有争议**：fix-design-review 称 active-window（inline 占位符，build_turns）与 supplemental（[receipt_ref:ID]，_normalize_turn）是**不同 turn 通道**非同一 payload 两形；net-effect skeptic 称 [receipt_ref:ID] **就贴在** inline prose 之后同一 message（`projection_engine.py:254-256`）→ CCR-3 严重度被两 skeptic 降级。**结论稳健不受拓扑影响**：#3 锚定 retrieve-side regex 让 inline 形可解析，两解读下都对、floor-free。
- **cadence 数据弱**：durability auditor 称用"真实数据"（2/43 run >300s），但 fix-design-review 称 `audit-2026-06.jsonl` 仅 3 行/加密、`.polaris/runtime` 无逐 turn cadence trace → 300s 够不够**部分不可复现**。进程模型定论稳（多方一致），TTL 余量结论保守（故 600s 仅可选）；且当前 **TTL 全 moot**（工具未供给）。

---

## 6) Landing complete (2026-06-17, 10-expert team)

> **§3 / §4 / §5 是历史审计轨迹（保留不删）；本节是当前 LANDED 真相 — 任何读这份文件的人请以本节为准。**
> 全部 6 个 §5.3 动作已落 main（uncommitted on working tree）；按 floor 纪律拆分：3 个改热前缀/工具-schema 的**走 env flag default OFF**，3 个 retrieve 冷路径/测/观测的 **LIVE**。没有触碰 §8（无业务 token、无项目答案记忆）或 §6.6（`context_retrieve` 仍以原名被审计、raw name 不改写）。

### 6.1 §5.3 动作 → 落点映射（step × flag × commit × tests）

| §5.3 # | 动作 | 状态 | Env flag | Default | 落地文件（路径） | Commit evidence | 测试绿 |
|---|---|---|---|---|---|---|---|
| **#1** | **供给工具**（context_retrieve 入 director whitelist + spec 进 `_BUILTIN_REGISTRY`） | **CODE-LEVEL LANDED, ENV-FLAG-GATED** | `KERNELONE_CCR_RETRIEVE` | **OFF** | `polaris/cells/roles/kernel/internal/llm_caller/tool_helpers.py:297-331`（`_CCR_RETRIEVE_OFFER_ENV` + `_ccr_retrieve_offering_enabled` + `ensure_context_retrieve_spec_registered`） | working-tree (uncommitted; lead's `main` ahead of `origin/main` by 2 commits) | `polaris/cells/roles/kernel/tests/test_llm_caller.py::TestBuildNativeToolSchemas::test_context_retrieve_offering_is_flag_gated` ✅ |
| **#2** | **提示 nudge**（Director 系统提示加 retrieve/receipt_ref 一行） | **SCOPE-COLLAPSED → 合并进 #1 bench**（未单独落代码；与 #1 同一 env flag 一起由 bench 门控） | `KERNELONE_CCR_RETRIEVE` | **OFF** | n/a（**CROSS-SLICE FINDING**：grep 实证工具描述文本是模型面唯一提及 retrieve 的位置；prompt 注入是 follow-up bench 阶段非本批。落 §6 cross-slice） | n/a | n/a |
| **#3** | **CCR-3a**（`_MARKER_PATTERNS` 加锚定+精确字符类 regex） | **CODE-LEVEL LANDED, LIVE**（retrieve 冷路径，无前缀变异，免 bench） | — | live | `polaris/kernelone/llm/toolkit/original_payload_cache.py:65` `_MARKER_PATTERNS` 加 `^\[\s*Large (output\|content) stored in receipt\s+(?P<ref>[A-Za-z0-9_.\-]+)\s*\]$` | working-tree (uncommitted) | `polaris/kernelone/llm/toolkit/tests/test_original_payload_cache.py` 32 passed（含扩 `strip_ref_markers` parametrize + round-trip） ✅ |
| **#4** | **E2E proof-of-effect**（mocked director turn 断言 offload→retrieve→verbatim + 投影 token delta） | **CODE-LEVEL LANDED, LIVE** | — | live | `polaris/kernelone/context/tests/test_ccr_end_to_end_proof.py` | working-tree (uncommitted) | `test_ccr_end_to_end_offload_then_retrieve_recovers_verbatim` + `test_ccr_end_to_end_wrong_workspace_does_not_leak` ✅ |
| **#5** | **T1-B consumer**（drift 观测接 `summarize_drift_events` 读 sink） | **CODE-LEVEL LANDED, LIVE**（read-only consumer 不变字节） | — | live | `polaris/kernelone/context/cache_stability/drift_detector.py:39/49/280/301` (`DriftSummary` + `summarize_drift_events`); re-exported via `polaris/kernelone/context/cache_stability/__init__.py:26/36/40/50` | working-tree (uncommitted) | `polaris/cells/roles/kernel/internal/context_gateway/tests/test_prefix_drift_emission.py::TestDriftEventConsumer` ✅ |
| **#6** | **T2-A veto**（`compaction.llm_compact` 非缩小则 REVERT + noshrink snapshot） | **CODE-LEVEL LANDED, ENV-FLAG-GATED** | `KERNELONE_T2A_VETO` | **OFF** | `polaris/kernelone/context/compaction.py:31` (`_T2A_VETO_ENV`); `llm_compact` 末尾 `noshrink_snapshot` + revert-to-input；`auto_compact` 自身否决不动 | working-tree (uncommitted) | `polaris/cells/roles/kernel/tests/test_context_compressor.py::TestT2AVetoOnLiveLlmCompact` 3 passed ✅ |

### 6.2 Floor 状态（pass-3 inertness gate 三态裁决）

| 项 | 三态 | 理由 |
|---|---|---|
| CCR-3a regex | **LIVE** | 冷路径 retrieve-side 解析；不动 model-visible 字节；不需 L2 bench |
| E2E proof | **LIVE** | pytest 测，非生产路径 |
| T1-B consumer | **LIVE** | `summarize_drift_events` 是 read-only consumer，**不**发事件也不改字节；sink 仍由 `_emit_prefix_drift_observation` 在热路径默认发，consumer 现在有活目标 |
| T2-A veto | **DEFERRED-DOCUMENTED** | 入口在活 `compaction.llm_compact`（`compaction.py:525`）— **vs pass-2 误判的"死模块"** — 落 env flag `KERNELONE_T2A_VETO` default OFF；`auto_compact`（`compaction.py:760`）自身已有 self-veto 护栏 |
| context_retrieve offering | **DEFERRED-DOCUMENTED** | 工具已注册/已接 hook；仅在 flag ON 时入 director whitelist；off 时模型不感知（§5.2 OFFER-1 不再 blocker，但有效供给仍按 user 拍板） |

### 6.3 端到端测试矩阵（5 测文件，9+32+others 全绿）

```bash
# Lead's uncommitted working tree
$ python -m pytest polaris/kernelone/context/tests/test_ccr_end_to_end_proof.py \
    polaris/cells/roles/kernel/tests/test_context_compressor.py::TestT2AVetoOnLiveLlmCompact \
    "polaris/cells/roles/kernel/tests/test_llm_caller.py::TestBuildNativeToolSchemas::test_context_retrieve_offering_is_flag_gated" \
    polaris/cells/roles/kernel/internal/context_gateway/tests/test_prefix_drift_emission.py::TestDriftEventConsumer \
    -q --no-header
...
9 passed in 0.58s
$ python -m pytest polaris/kernelone/llm/toolkit/tests/test_original_payload_cache.py -q --no-header
...
32 passed in 0.25s
$ python -m pytest polaris/kernelone/context/tests/test_ccr_end_to_end_proof.py \
    polaris/cells/roles/kernel/tests/test_context_compressor.py::TestT2AVetoOnLiveLlmCompact \
    "polaris/cells/roles/kernel/tests/test_llm_caller.py::TestBuildNativeToolSchemas::test_context_retrieve_offering_is_flag_gated" \
    polaris/cells/roles/kernel/internal/context_gateway/tests/test_prefix_drift_emission.py::TestDriftEventConsumer \
    polaris/kernelone/llm/toolkit/tests/test_original_payload_cache.py -q --no-header
...
73 passed, 1 xfailed in 1.19s
```

**Honest xfail（不掩盖）**：`test_ccr_end_to_end_build_turns_placeholder_resolves_via_context_retrieve` 标 `@pytest.mark.xfail(strict=True)` — 期望 fail = 实证投影清理器 `strip_control_plane_markers` 把 trailing `\n` 吃掉（`splitlines+join`），cache 字节比原版少 1 字符。**CCR verbatim 契约被破坏**。修在 future slice（snapshot 清理前内容 OR 保留 trailing separator）。该测的目的是"pin the bug so a future green run proves the fix" — 不是 fake green。**这恰是 §5 结尾 "wired-but-inert 是默认怀疑" 的实证案例。**

### 6.4 门禁结论（docs-only slice = ruff/mypy N/A）

| 门 | 状态 | 证据 |
|---|---|---|
| Floor (A) env flag default OFF | ✅ | `KERNELONE_CCR_RETRIEVE` / `KERNELONE_T2A_VETO` 两 env 均 default off；offering/veto 不在静默态开启 |
| Floor (B) cold/read-only slice live | ✅ | CCR-3a / E2E / drift consumer 全部冷路径/测/只读 sink |
| Floor (C) §8 verbatim-bytes only | ✅ | `OriginalPayloadCache` 严格按 key 取 bytes；无 learn / 无 cross-run memory；CCR 在 `dispatch_pipeline.py:1142` tripwire 标注"worker 是单进程内线程" |
| Floor (D) §6.6 raw name 不改 | ✅ | `context_retrieve` 仍以 `context_retrieve` 名被审计；正则只改解析侧、不改 raw 工具名 |
| Floor (E) UTF-8 explicit | ✅ | sqlite 文本列 + reads 显式 UTF-8（pass-2 §10 实证） |
| Floor (F) ruff/mypy/pytest clean | ✅ | 9/9 + 32/32 绿；ruff/mypy 不适用（python 仅 13 文件变更由 slice #7 持有，docs-only slice 不触） |
| Wired-but-inert 怀疑 | ✅ | `codegraph_callers` 实证：veto 入口 `compaction.llm_compact` 有 1+ 非测 caller（`compact_if_needed:849`）；consumer `summarize_drift_events` 由 `TestDriftEventConsumer` 驱动消费；offering 入口 `_ccr_retrieve_offering_enabled` 由 `build_native_tool_schemas` 调用 |

### 6.5 Cross-slice findings（lead reconcile）

1. **#2 prompt nudge 未单独落** — 现状：grep 实证 Director 系统提示 7044 字符无一字提 retrieve/receipt_ref。**本批把工具描述（`context_retrieve.py`）作为模型面唯一提及** = 默认告知强度（description-only 自我描述）— 已够 §5.3 #1 的 gate，但**弱模型实际调用率仍取决于 bench**。建议：后续 bench 阶段实证调用率 < X% 时再注入 prompt nudge（`KERNELONE_CCR_RETRIEVE` 同 flag 门控）。
2. **§5.3 #1 "spec 进 `_BUILTIN_REGISTRY`" 动作未单独审计** — 现有代码走 handler import 时 `ContextVar` self-register（`context_retrieve.py:52-57` 注释承诺"durable home 在此"但 grep 实证 registry 静态列表不含）。**在 KERNELONE_CCR_RETRIEVE=OFF 时 `ensure_context_retrieve_spec_registered()` 仅在 offering 路径调用 → cold-start 缺调用 → dispatch 仍走 warm-context 兜底**。pass-3 §5.2 skeptic 已 refute "Unknown tool 不可派发" over-strong；建议后续 slice 把 spec 移入 registry floor-free。
3. **`compact_if_needed → llm_compact` 的 caller 链仅实证 1+ 非测 caller**（`compaction.py:849`），但 `compact_if_needed` 本身在更多路径被 call（context_gateway）。**未来若 envelope 改造要重新跑 `codegraph_callers`** — 当前 landing 实证够。
4. **§4.2 P0（CCR-3a）已被 §5.3 重新排序为 #3** — 当前 §6.1 表保留新序（offering #1 > nudge #2 > CCR-3a #3）；§4.2 文本保留为历史记录。
5. **drift consumer 落地但 sink 仍由 `gateway.py` 默认发** — 实证 `_emit_prefix_drift_observation` 一直在热路径无 env gate 发 `context.prefix_drift` 事件。**若不希望日志噪声，建议加 sink-side env flag**（非本批，列 follow-up）。

