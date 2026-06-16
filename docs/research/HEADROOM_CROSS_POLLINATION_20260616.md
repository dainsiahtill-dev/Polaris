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

> 所有项遵守：§8 禁业务代码、UTF-8、strict/mypy、fail-closed、改 Loop/内核优先动 `cells/roles`+`kernelone`、非平凡后端先落 blueprint。本文为评估，不含代码改动。
