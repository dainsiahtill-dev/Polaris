# Headroom → Polaris：Token 估算器收敛（T2-C，单一校准 TokenEstimator）

> 范围：跨 `polaris/kernelone/`、`polaris/cells/roles/kernel/`、`polaris/domain/`、`polaris/infrastructure/`。
> 来源：`docs/research/HEADROOM_CROSS_POLLINATION_20260616.md` §T2-C；遵 `src/backend/CLAUDE.md §4.1`（两阶段执行：先 Blueprint 再实现）。
> 本蓝图为 **Expert-B** 落地依据。所有「现状」均经 codegraph 实读锚定（file:line 为本仓当前盘上事实）。
> 约束：§8 禁业务代码、显式 UTF-8、strict + 完整类型注解 + mypy clean、§6.6 不改写 raw 工具名（本任务不触工具名，记录于 §6）、fail-closed、复用优先不造轮子。
> **本蓝图仅为计划，不含任何代码改动。**

---

## 0. 问题陈述

re-audit 命名了 5 处 fork，但 codegraph + grep 实读揭示 fork 集**远大于 5**：盘上至少 **20+ 处独立 token 估算实现**，使用 **5 种互不一致的公式**。这些公式喂入预算门（budget gate）、压缩触发（compaction trigger）、淘汰决策（eviction）与用量遥测（usage telemetry）。估算分叉 = 预算判定分叉 = 同一段文本在不同代码路径被判「装得下 / 装不下」，是 weak-Director write-convergence 与 vLLM `prompt+max_tokens > max_model_len` 400 的上游噪声源之一。

T2-C 目标：**收敛到单一、可校准的 canonical TokenEstimator + 薄适配器**，行为保持 + 地板安全（floor-safe），并把 HF-tokenizer 采用**推迟到收敛之后**（理由见 §7）。

---

## 1. 现状：fork 全清单（codegraph 锚定，file:line + blast radius）

### 1.1 两个互相竞争的「canonical」（**核心矛盾**）

| # | 符号 | 文件:行 | 公式 | tiktoken | content-type 感知 | live importer / caller |
|---|------|---------|------|----------|------------------|------------------------|
| C1 | `TokenEstimator.estimate` / `_heuristic_estimate` | `kernelone/llm/engine/token_estimator.py:40` / `:75` | ASCII `len/4`；CJK 比例>0.3 用 `CJK_CHARS_PER_TOKEN=1`；code 用 `=3`；混合用加权平均 | 是（cl100k/o200k） | 是 | 经 `TokenEstimatorAdapter` + `ServiceLocator` 全局注入；`token_service.estimate_tokens:163`、`budget_gate.estimate_tokens_for_text:252`、`akashic/working_memory:402`、`accel/token_estimator:45`（shim）、`prompt_budget.py:48/234/293` |
| C2 | `estimate_tokens`（codex Wave-1） | `kernelone/context/_token_estimator.py:6` | `int(ascii/4) + int(cjk*1.5)`（**CJK=1.5，与 C1 的 CJK=1 不一致**）；无 content-type；无 tiktoken | 否 | 否 | 9 处 ContextOS live import：`intelligent_compressor:27`、`crushers/base:24`、`context_os/helpers:12`、`context_os/models_v2:1021`、`context_os/models:1230`、`context_os/domain_adapters/generic:8`、`chunks/assembler:111/622` |

> **关键事实 1（决定整个方案）**：C1 与 C2 对同一段 CJK 文本给出**不同**结果（CJK 系数 1 vs 1.5）。C1 自带 2026-06-12 factory-bench 校准注释，明确「现代 BPE 一个 CJK≈1 token，旧的 *0.5 低估 2x 导致 vLLM 400」。C2 的 `*1.5` 反而**高估**（errs-safe 方向正确，但与 C1 不一致 → 预算门两套真相）。收敛必须裁定其一为 canonical 公式；C1 是唯一带 live 校准证据 + tiktoken 能力 + content-type 感知者 → **canonical = C1**（`TokenEstimator`）。

### 1.2 完全独立的 CJK-aware fork（重复 C2 公式或近似）

| # | 符号 | 文件:行 | 公式 | 角色 / blast radius |
|---|------|---------|------|---------------------|
| F1 | `_estimate_history_tokens`（func） | `kernelone/context/compaction_strategy.py:292` | `ascii/4 + cjk*1.5 + 4/item` | **热路径**：`compact():158` 压缩前后量算 `tokens_recovered`（T2-A no-op 门依赖它） |
| F2 | `_estimate_tokens_for_messages`（func） | `kernelone/context/history_materialization.py:~605` | 同 F1 | history/receipt 物化量算 |
| F3 | `ContextAssembler._estimate_text_tokens` | `cells/roles/kernel/internal/services/context_assembler.py:933` | `ascii/4 + cjk*1.5 + other/2` （**含 other/2，第 3 种**） | 装配回退估算 |
| F4 | `context_gateway TokenEstimator.estimate` | `cells/roles/kernel/internal/context_gateway/token_estimator.py:17` | `ascii/4 + cjk*1.5 + other/2 + overhead 4` | **最热路径**：`gateway.py` 在 `:475/:575/:576/:594/:646/:1337/:1684` 共 7+ 处调用，驱动压缩阈值/系统提示预留/投影预算 |
| F5 | `AttentionRanker._estimate_tokens`（staticmethod） | `kernelone/context/context_os/attention/ranker.py:~215` | `ascii/4 + cjk*1.5` | 注意力排序（淘汰相关） |
| F6 | `MultiResolutionStore._estimate_tokens`（staticmethod） | `kernelone/context/context_os/multi_resolution_store.py:~342` | `ascii/4 + cjk*1.5` | 多分辨率存储分级 |
| F7 | `working_memory._estimate_tokens` | `kernelone/akashic/working_memory.py:402` | 注入器→`estimator_cls.estimate`→回退 `len//4` | 已部分走 C1，但有自带 `len//4` 兜底 |

### 1.3 纯 `chars/4`（ASCII-only，CJK 盲）fork

| # | 符号 | 文件:行 | 公式 | 角色 / blast radius |
|---|------|---------|------|---------------------|
| F8 | `Usage.estimate`（classmethod） | `kernelone/llm/shared_contracts.py:274` | `chars//4`（prompt + completion） | **热路径**：executor/stream 8 处 live 调用（`executor.py:477`、`stream/executor.py:866/1172`、`resilience.py:927`、`normalizer.py:186`、`shared_contracts.py:392`），写入 `Usage` → 遥测/finops/预算回执 |
| F9 | `_estimate_text_tokens`（func） | `kernelone/tool_state/compaction.py:230` | `max(1,len/4)` | tool-state 预算门 |
| F10 | `ToolLoopController._estimate_text_tokens`（staticmethod） | `cells/roles/kernel/internal/tool_loop_controller.py:1062` | `max(1,len/4)`（与 F9 逐字复制） | tool-loop 预算门；其 `_estimate_history_tokens:1051` 聚合之 |
| F11 | `PromptChunk.__post_init__` 兜底 | `kernelone/context/chunks/taxonomy.py:181` | `max(1,len//4)` | chunk `estimated_tokens` 兜底（当未显式传入）；喂 `ChunkBudgetTracker` |
| F12 | `LLMCompactService.compact` 内联 | `domain/services/llm_compact_service.py:96` / `:118` | `len//4` | 压缩前后 `original/compressed_token_estimate` |
| F13 | `IntelligentCompressor` 常量 `_CHARS_PER_TOKEN=4.0` | `kernelone/context/intelligent_compressor.py:45` | 注：实际 `_estimate_tokens:466` 已委托 C2；此常量为孤儿/部分路径 | 压缩量算 |
| F14 | `compressors/registry.py:73` | `len(content)//4` | `CompressionCost` |
| F15 | `RoleContextCompressor.MAX_CHARS_PER_TOKEN=4` | `kernelone/context/compaction.py:328/402` | `len(str(messages))//max_chars_per_token` | 压缩量算 |
| F16 | `llm_caller/caller.py:451`、`invoker.py:284/803` | `len//4` | LLM 调用前 prompt 估算 |
| F17 | `token_tracking_wrapper.py:84`、`codex_adapter.py:461/462/476/478` | `chars//4` | 用量遥测 |
| F18 | `memory/integration.py:184/239` | `len/4` | persona/memory 估算 |
| F19 | `scout/internal/distiller.py:15` `_CHARS_PER_TOKEN=4` | `token_budget*4` 反向（token→chars） | scout 蒸馏预算 |

### 1.4 其它分叉公式（第 4、5 种）

| # | 符号 | 文件:行 | 公式 |
|---|------|---------|------|
| F20 | `TieredSummarizer._estimate_tokens` | `context_os/summarizers/tiered.py:~388` | `cjk//2 + non_cjk//4`（**CJK//2 = 第 4 种**） |
| F21 | `SemanticSummarizer` 估算 | `context_os/summarizers/semantic.py:~256` | `english/4 + chinese/2`（**chinese/2 = 第 5 种**） |
| F22 | `compression_engine.py:183/425` `chars_per_token=2` | 压缩裁剪用 `chars_per_token=2`（裁剪方向，非估算，但常量分叉） |

### 1.5 「合法」适配/契约层（**不是 fork，是收敛承载点**）

| 符号 | 文件:行 | 角色 |
|------|---------|------|
| `TokenEstimatorPort`（ABC） | `kernelone/llm/toolkit/contracts.py:92` | core 层抽象契约（`estimate_tokens` / `estimate_messages_tokens`） |
| `TokenEstimatorAdapter` | `kernelone/llm/engine/token_estimator.py:203` | C1 → Port 适配器（model→tokenizer_hint 映射） |
| `_ServiceLocator` | `kernelone/llm/toolkit/contracts.py:186` | DI 容器：`register_token_estimator` / `get_token_estimator`（lazy 默认 = `TokenEstimatorAdapter`） |
| `ensure_token_estimator_registered` | `kernelone/llm/engine/token_estimator.py:236` | 显式注册入口（注：grep 显示 bootstrap/delivery **未** 调用 → 全靠 `get_token_estimator` 的 lazy `_register_default_token_estimator`） |
| `infrastructure/accel/token_estimator.py` | `:1` | **已声明 deprecated shim**，re-export C1；提供 dict-metadata + calibration（`estimate_tokens_for_text` 带 `calibration`/`fallback_chars_per_token`）。**无 live importer**（grep 证实） |
| `TokenService` | `domain/services/token_service.py:163` | 已委托 C1（`from ...token_estimator import TokenEstimator`） |

---

## 2. 目标态文本架构（单 canonical + 薄适配器）

```
                       ┌─────────────────────────────────────────────┐
                       │  CANONICAL: TokenEstimator (C1)              │
                       │  kernelone/llm/engine/token_estimator.py     │
                       │  - _heuristic_estimate(content_type)         │
                       │    ASCII/4 · CJK(=1) · CODE(=3) · 混合加权    │
                       │  - tiktoken (cl100k/o200k) when hint given   │
                       │  - estimate() / estimate_messages()          │
                       │  - 校准常量集中：CHARS/CJK/CODE_PER_TOKEN    │
                       └───────────────┬─────────────────────────────┘
                                       │ (唯一公式实现)
                  ┌────────────────────┼─────────────────────────────┐
                  │                    │                             │
        TokenEstimatorPort      context/_token_estimator       (薄便捷函数)
        (ABC, contracts.py)     .estimate_tokens (C2 → 改为委托)  estimate_tokens()
                  │                    │                             │
        TokenEstimatorAdapter   9 ContextOS importer 不变           各 fork 改为
        (model→hint 映射)        (导入名不变，公式收敛到 C1)        委托 canonical
                  │
        ServiceLocator (DI) ──→ get_token_estimator() lazy 默认
                  │
   ┌──────────────┼───────────────┬──────────────┬─────────────────┐
budget_gate   working_memory   context_assembler  gateway      Usage.estimate
(已走C1)       (已走C1+兜底)     (F3 收敛)         (F4 收敛)    (F8 收敛, 见§3 hot)
```

**裁决**：
1. **canonical 公式 = C1**（`TokenEstimator`，CJK=1，content-type 感知，tiktoken-capable，唯一带 live 校准证据）。
2. **C2（`context/_token_estimator.estimate_tokens`）保留为 ContextOS 的导入入口**（9 个 live importer + crushers 兄弟模块依赖其名），但**内部改为薄委托 C1**（`return TokenEstimator.estimate(text)`），消除 CJK 1.5 vs 1 分叉。导入路径/签名不变 → 对 9 个 importer 零破坏。
3. **所有 §1.2–§1.4 的 fork** 收敛为「委托 canonical」：保留各自的**方法名与签名**（callers 不动），方法体改为调用 canonical（直接 `TokenEstimator.estimate` 或经 `ServiceLocator.get_token_estimator()`）。
4. **`infrastructure/accel/token_estimator.py`** 已是 deprecated shim 且无 live importer → 不动（已 re-export C1）；其 `calibration` 能力是未来 HF 采用的承载点（§7）。
5. **校准是单点**：所有 chars-per-token 常量集中到 C1 的类常量（`CHARS_PER_TOKEN` / `CJK_CHARS_PER_TOKEN` / `CODE_CHARS_PER_TOKEN`）。删除散落的 `4` / `1.5` / `2` 字面量（除裁剪方向的 F22，属压缩裁剪非估算，单独标注保留）。

---

## 3. 迁移计划（behavior-preserving + floor-safe）

> 原则：**先消形不同值的分叉，再统一名**。每一步「保签名、改内部、加测试钉住数值」。任何改变预算/压缩判定的调用点 = hot-path，需 L2-floor bench 验证不回归。

### 阶段 A — 形等价收敛（零数值漂移，最安全，优先）
对**当前已等价或方向无害**的 fork，改为委托 canonical，并用单测钉住「新旧返回值在代表性语料上一致」：
- **A1（C2 收敛）**：`context/_token_estimator.estimate_tokens` 内部改委托 `TokenEstimator.estimate`。**⚠ 数值会变**（CJK 1.5→1）→ 见阶段 B，不在 A。
- **A2**：F9/F10/F11/F12/F14/F16/F17/F18 等纯 `len//4` 且**仅喂遥测/非 budget-gate 临界**的点 → 委托 canonical 的 ASCII 路径（`content_type="general"`）。纯 ASCII 文本下 `len//4` ≡ canonical → 数值不变；含 CJK 文本下 canonical 给更高值（errs-safe）。**逐点判定是否 budget-临界**（见 §3 hot-path 清单）。

### 阶段 B — 有数值漂移的收敛（**需 L2-floor bench**）
凡收敛后会改变 budget/compaction 判定数值的点，**必须**先跑 L2-floor bench（标准绑定 int4，留出 L2-07..12，审计 0 dead-letter/budget/HTTP/symbol-drift），证明 6/6 RUNNABLE 不回归后才落：
- **B1（C2 → C1，CJK 1.5→1）**：影响全部 9 个 ContextOS importer 的压缩/淘汰量算。CJK 文本估值下降 ~33% → 压缩**更晚**触发 → 风险方向是「prompt 更大」→ 必须 bench。
- **B2（F4 context_gateway，最热）**：`gateway.py` 7+ 调用驱动压缩阈值/系统预留/投影预算。F4 公式 `ascii/4+cjk*1.5+other/2` → C1 `cjk=1` 后 CJK 段下降、other 段（C1 归入 general/4）变化。**最高风险**，单独 bench。
- **B3（F1 compaction_strategy / F2 history_materialization）**：F1 喂 T2-A 的 `tokens_recovered` no-op 门（兄弟蓝图 `HEADROOM_COMPACTION_CRUSH`）。收敛改变 recovered 值 → 可能翻转 no-op 判定。与 Expert-C 的 T2-A 协同验证。
- **B4（F8 `Usage.estimate`，热但仅遥测/回执）**：8 处 executor/stream live 调用。改为 canonical 后 CJK 用量估值上升。需确认 finops/budget-from-usage 路径不因此误判超预算（fail-closed 方向：高估 → 早压，安全；但要确认不触发**错误的** circuit_breaker）。bench 观测 budget 审计行。

### 阶段 C — 形不同公式的吸收（F20/F21/F3/F5/F6）
F20（`cjk//2`）、F21（`chinese/2`）、F3/F5/F6（`+other/2`）是独立第 3/4/5 公式。统一委托 canonical → 数值变动较大但都在 summarizer/ranker/assembler 回退路径。逐个加「新值 ≥ 旧值或差异 < X%」回归测试 + 纳入 B 的 bench 批。

### 阶段 D — 清理
删除散落字面量常量（F13 孤儿 `_CHARS_PER_TOKEN`、F15 `MAX_CHARS_PER_TOKEN`、F19 scout 常量按需保留 token→char 反向）；F22 压缩裁剪 `chars_per_token=2` 标注「裁剪方向常量，非估算」保留。更新 `ensure_token_estimator_registered` 在 bootstrap 显式调用（当前仅 lazy）以使注册路径可审计（可选，低优先）。

### Hot-path 清单（需 L2-floor bench 的调用点，按风险降序）
1. **F4 `context_gateway/token_estimator.py:17`** — gateway 压缩决策核心（B2）。
2. **C2/B1 9 个 ContextOS importer** — 压缩/淘汰量算（B1）。
3. **F1 `compaction_strategy.py:292`** — `tokens_recovered` / no-op 门（B3，与 T2-A 耦合）。
4. **F8 `Usage.estimate`** — 用量回执/budget-from-usage（B4）。
5. **`budget_gate.estimate_tokens_for_text:252`** — 已走 C1，但若 C1 公式因校准再调，7 个 working_set caller 同步受影响（回归即可，无新收敛）。

非 hot-path（纯遥测/日志，回归测试即可，无需 bench）：F17、F18、F19、token_tracking_wrapper、codex_adapter。

---

## 4. 测试策略（钉住行为，fail-closed）

- **数值钉子**：对 canonical C1 建「黄金语料表」单测（纯 ASCII / 纯 CJK / code / 混合 / 空 / 仅 emoji-other），固定期望值；任何后续校准改 canonical 即触发该测。
- **收敛等价测**：每个被收敛的 fork，新增「委托后 == `TokenEstimator.estimate(...)`」断言（替代旧的隐式重复公式）。
- **回归保护**：复用既有 `tests/test_token_estimator.py`、`tests/unit/kernelone/test_llm_engine_token_estimator.py`、`context/tests/test_canonical_token_estimator.py`、`context/tests/test_budget_gate.py`、`tests/unit/kernelone/test_chat_messages_budget_compression.py`（已覆盖 C1 与 budget_gate）。
- **L2-floor bench**（阶段 B/C 强制）：标准绑定 int4（local+lan@32K），留出 L2-07..12，要求 6/6 RUNNABLE + 审计干净（参 memory「L2-int4-floor 6/6」）。任一收敛批次跑 bench 见绿才落，**fail-closed：bench 回归即回退该批收敛**。
- 每个改动文件过门：`ruff check <files> --fix && ruff format <files>`、`mypy <files>`（Success: no issues found）、`pytest <your test files> -q`（owned 切片 100%）。

---

## 5. 风险与边界

1. **数值漂移翻转 budget 判定**：收敛后 CJK 估值统一为 CJK=1（比 C2 的 1.5 低）→ 压缩更晚 → prompt 可能更大 → vLLM 400 风险。**缓解**：阶段 B 全程 L2-floor bench；C1 注释已证 CJK=1 是 live 校准值，但 ContextOS（C2 路径）此前用 1.5 → 收敛是「降估」方向，必须实测而非推断。
2. **F8 `Usage.estimate` 改动波及 finops/遥测**：用量回执上升可能触发 budget-from-usage 的误超预算。**缓解**：B4 单独 bench + 审计 budget 行；保留 fail-closed（高估早压安全）。
3. **跨 Cell 文件归属**：fork 散落在 `roles/kernel`（F3/F4/F10）、`kernelone/context`（多数）、`domain`（F12）、`infrastructure`（shim）。落地时**逐文件确认 owner**，跨 expert 文件（如 codex 持有的 `transaction/**`、Expert-C 持有的 `intelligent_compressor.py`/`compaction_strategy.py` T2-A 段）**STOP 并上报，不直接编辑**。本蓝图不规定谁改哪文件，仅给收敛图；执行阶段按 file-ownership 分派。
4. **F1 与 T2-A 耦合**：`compaction_strategy._estimate_history_tokens` 同时被 T2-A no-op 门用。收敛必须与 Expert-C 协同，避免双方同时改同段产生冲突。
5. **bootstrap 注册路径**：`ensure_token_estimator_registered` 当前无人调用，全靠 lazy `_register_default_token_estimator`。收敛不依赖显式注册即可工作（lazy 默认 = C1），但若未来注入自定义 estimator，需确保收敛点都走 `ServiceLocator` 而非硬编 `TokenEstimator`（当前多数 fork 硬编 → 收敛后部分仍硬编 C1，可接受，单点真相已达成）。

---

## 6. S8 / S6.6 合规

- **S8（禁业务代码）**：本收敛纯属平台通用能力（token 估算），无任何目标项目名/模板/域模型。canonical C1 的常量是语言通用启发式（ASCII/CJK/code），非业务硬编。✅
- **S6.6（不改写 raw 工具名）**：本任务不触及工具调用解码/审计层，不改写任何 raw 工具名。token 估算在 budget/compaction 层，与工具名 canonical-gate 无交集。✅
- **fail-closed**：估算失败/tiktoken 不可用 → C1 已回退启发式（errs-safe 高估方向），不静默放行超预算 prompt。收敛保持该语义。✅

---

## 7. 为何 HF-tokenizer 采用「推迟到收敛之后」（DEFERRED）

1. **先有单点，才谈换芯**：当前 20+ fork、5 公式。在分叉态下接 HF tokenizer，只能接进其中一两个调用点，其余仍跑启发式 → 预算真相更碎。**必须先收敛到 C1 单点**，HF 才有唯一注入位（`_estimate_with_real_tokenizer` 或新增 `hf` 分支 + `ServiceLocator` 注入）。
2. **承载点已就位**：C1 已有 `tokenizer_hint` 分派（cl100k/o200k via tiktoken）+ `infrastructure/accel` shim 的 `calibration`/`fallback_chars_per_token`。HF 采用 = 在 canonical 内加一个 `hf:<model>` 分支，复用既有回退骨架，**不需新架构**——前提是 fork 已收敛。
3. **依赖与冷启动成本**：HF `transformers`/`tokenizers` 是重依赖（首次加载 tokenizer 有 IO/内存成本），不能散落进 20 个热路径。单点 + lazy 缓存（一次加载）才可控。
4. **校准基线需先固定**：要量化 HF 相对启发式的增益，需先有「单一启发式基线」（C1）作对照。分叉态下无法做 A/B。**收敛 = HF 采用的前置条件**，故 DEFERRED 是顺序约束而非否决。
5. **floor-safe 顺序**：HF 给更准（通常更低）的 token 数 → 压缩更晚 → 与 §5.1 同向风险。先用收敛 + L2-floor bench 锁住启发式地板，再在同一 bench 框架下评估 HF，避免两个变量同时动。

---

## 8. Self-check 门禁（执行阶段，非本蓝图）

`ruff check <files> --fix && ruff format <files>`、`mypy <files>`（Success: no issues found）、`pytest <your test files> -q`（owned 切片 100% 绿）、阶段 B/C 额外 L2-floor bench 6/6 RUNNABLE + 审计干净。**本蓝图为 §4.1 第一阶段产物（计划），不含实现。**

---

## 9. 二次深度审计修订（2026-06-16，codegraph + superpowers 对抗 real×2）

> 核心 thesis 经独立重现**成立**：5 互异公式、同 100 CJK 字符 3x 散布（C1=100 / C2=150 / F20=50 / F21=50 / F3=150），两文件都自称 canonical 但 CJK 系数不一致（C1=1 vs C2=1.5）。verdict=canonical→C1 维持。以下 4 处补正：

1. **漏掉第 6 个 fork（T2C-2）**：补 `repo_intelligence/renderer.py:273` `_estimate_tokens`（`total_chars//4`，**CJK-blind**，活、经 :190 可达）入 §1.3。执行前确认 `LoIRenderResult.total_tokens` 是否流入任何预算/驱逐 gate：若仅 metadata → Stage-A 委托、免 bench；若入 gate → Stage-B + bench。`engine/utils.py:26` 是既有「委托」先例（非 fork）。
2. **收敛 blocker 误归类（T2C-3，high）**：`test_canonical_token_estimator.py` **硬钉 C2 的 cjk*1.5**（`assert ==600 / ==425 / ==175`）。Stage B1 一旦把 C2→C1 收敛即 BREAK 它。本蓝图把它列为「被动回归保护」是错的——它是 **convergence-blocker，Stage B1 必须在同一 commit 改写**：把 exact-value 断言换成 delegation-equivalence（`canonical_estimate(text)==TokenEstimator.estimate(text)`）+ CJK=1 下的新 golden 值。新增 §4 子规则：**任何钉某 fork 字面公式的测，收敛时必须迁成 delegation-equivalence 断言，不得 break-and-ignore**。
3. **blast-radius 计数高估（T2C-4）**：实测 C2 = **7 importer 文件 / 9 import 语句**（含 assembler 两处 + crushers re-export），非 9 文件；`Usage.estimate` ≈ 6 活 caller；gateway = 6 活 `.estimate()` + 1 delegate。**热路径/floor 门控判断不变**（gateway :594 → budget_pressure → 投影变异，B1/B2 仍须 L2 bench）；仅修计数避免执行者追幽灵调用点。
4. **§1 锚点已漂移（T2C-5）**：执行前对每个符号跑一遍 `grep -n` 刷新（F1 在 `:341` 非 `:292`；F20 `:376`；F21 `:241`；F2 `:594`）。**`context_assembler` 有两个估算器**：`_estimate_tokens_fallback`（messages 路径，:899）+ `_estimate_text_tokens`（single-string，:933）——本蓝图单 `:933` 锚点会漏掉 :899，收敛集必须含两者。

**额外门**（与 T2-B 对齐）：T2-B 的 savings 用 `_token_estimator`（canonical CJK）量，但热路径预算执行用 `CompressionEngine.TokenEstimator`。re-anchor 证明须在**预算执行所用的同一估算器**下复测 savings，否则报的 savings 未必等于真预算 headroom。

**Stage A 预检**：对每个 `len//4` fork 先证它是否真流入预算/驱逐决策（vs 纯 metadata/日志）——只有「入 gate」的 fork 才需进 bench-gated 批次；`infrastructure/accel` 的 `token_estimator.py` shim **0 活 importer**，是未来 HF 注入的免费承载点、非现役风险。

---

## 10) Landing (2026-06-17) — T2-C scope: blueprint-only, NOT in this landing batch

> 本节是当前 LANDED 真相。**本切片（10-expert headroom team 2026-06-17）专注于 T1-A / T1-B / T2-A；T2-C 不在本批落地点。** 任何读这份文件的人请知：§1-§9 的所有 fork 仍盘上存在（grep 实证：20+ 独立实现、5 互异公式未变），本蓝图仍为**计划阶段产物**，没有任何估算器收敛代码在 working tree。

### 10.1 本批 landing 范围 vs T2-C

| 项 | 本批 landing | T2-C 关系 |
|---|---|---|
| `KERNELONE_CCR_RETRIEVE`（offering flag, default OFF） | ✅ landed | 无关（CCR 工具层，非估算器） |
| `KERNELONE_T2A_VETO`（llm_compact 收缩否决, default OFF） | ✅ landed | **直接相关** — T2-A veto 读 `llm_compact` 返回 tokens（用 F1 `compaction_strategy._estimate_history_tokens` or 类似启发式）；若 F1 在 Stage B1 收敛到 C1（cjk=1.5→1），**veto 判定数值会变** → 本批 veto 的活判定逻辑仍用盘上原公式，未触 T2-C |
| CCR-3a anchored regex | ✅ landed | 无关（retrieve 解析层） |
| E2E proof + drift consumer | ✅ landed | 无关（CCR 工具/观测层） |

### 10.2 T2-C 阶段 B1 实施前必做的预检（保持本蓝图 §9 警告）

§9 的 4 处修订在本批**未触**，landing 时仍是审计警告：

1. **T2C-2 第 6 fork**：`repo_intelligence/renderer.py:273`（`total_chars//4`，CJK-blind，活）未确认是否入 gate；执行前必查 `LoIRenderResult.total_tokens` 路径
2. **T2C-3 收敛-blocker 测**：`test_canonical_token_estimator.py` 硬钉 C2 `cjk*1.5`（assert==600/425/175） — **本批未改**；Stage B1 实施必须在同一 commit 改写为 delegation-equivalence 断言 + CJK=1 新 golden 值（否则 break）
3. **T2C-4 blast-radius 计数**：本批未复核（C2 实 7 文件/9 import 仍待 Stage A 预检逐点确认）
4. **T2C-5 锚点漂移**：`compaction_strategy._estimate_history_tokens` 在 `:341` 非 `:292`（F1 漂移），`context_assembler` 有**两个**估算器（`:899` messages 路径 + `:933` single-string 路径） — 本批未触；执行前对每个符号 `grep -n` 刷新

### 10.3 T2-C 与本批 landing 的耦合点（须 lead reconcile）

- **T2-A veto (`KERNELONE_T2A_VETO`) × F1 公式**：当前 veto 用盘上启发式判定"未缩小"；若 F1 在 Stage B1 收敛为 C1，**veto 触发频率会变**（CJK 段估值降 ~33% → auto_compact 后 `tokens_recovered` 变 → llm_compact 入口的 recovered 假设变 → veto 判定翻不翻转需重测）。本批 veto 测 3 测（`TestT2AVetoOnLiveLlmCompact`）是当前盘上公式下绿的；F1 收敛后须**同一 commit 重测**。
- **CCR-3a × T2-C**：无关（CCR 字节层不读 token 估算）。
- **T1-B drift consumer × T2-C**：无关（read-only consumer）。

### 10.4 Gate 状态（docs-only）

- ruff/mypy 不适用（无 python 改动）
- pytest：本切片未触 T2-C 实现 → 既有 `tests/test_token_estimator.py` + `tests/unit/kernelone/test_llm_engine_token_estimator.py` + `context/tests/test_canonical_token_estimator.py` 全部保持原状，**未跑**（不属于本切片 owned tests）
- §8/§6.6：本切片未触代码 → 不变；T2-C 阶段 B1 实施时仍须重审 §8（无业务 token，是语言通用启发式 → clean）与 §6.6（不触工具名 → clean）
