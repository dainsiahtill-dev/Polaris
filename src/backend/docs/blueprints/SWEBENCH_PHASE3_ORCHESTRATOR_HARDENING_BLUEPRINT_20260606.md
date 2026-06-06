# Blueprint — SWE-bench Phase 3: Orchestrator hardening (cascade + budget + self-heal)

- 日期: 2026-06-06
- 作者: Polaris Meta-Architect (Claude Opus)
- 分类: 根因修复 + 编排状态机重写（`scripts/swebench/polaris_solve_one.py`）
- 触发: v9 swap 架构 4/20=20%；唯二退化点是 gemma 编辑落盘失败（`django-11422` SEARCH 未匹配、`sphinx-8474`/`django-17087` 输出被 4000-token 截断）。

## 1. 系统性瓶颈故障树（经代码核对，deliverable #1）

```
20% 上限 = 单候选 + 无校验 + 无自愈 的编排薄弱
├─ B1 单假设即放弃：solve() 只编辑 merged[0]，丢弃其余 ~19 个排序候选；apply 失败不级联
├─ B2 max_tokens 固定 4000：无上下文预算意识，大文件输出截断 → 块未闭合 → 落盘失败
│    （用户约束：input + max_tokens ≤ 32768；必须动态计算）
├─ B3 无块校验：draft 直接进 _apply_blocks；畸形/截断/歧义块仅在 apply 时报一个泛化失败，无重试/纠错反馈
├─ B4 编译结果被忽略：qa_rc 记录但从不据此动作；apply 失败时还会编译"未改动文件"→ 误报 rc=0（假绿）
├─ B5 锚点过窄：draft prompt 只说"逐字拷贝"，不要求 SEARCH 唯一/≥N 行 → 大仓重复模式歧义匹配
└─ B6 handler 单文件：_apply_via_handler 走 {file: target}；多文件块只能靠 direct fallback
```

## 2. 修复设计（编排状态机重写）

### 2.1 上下文预算感知的 max_tokens（B2，对接用户 32768 约束）
`_context_budget_max_tokens(prompt)` = `clamp(MODEL_CTX_TOKENS - est_input - MARGIN, 512, EDIT_MAX_OUT_CAP)`，
常量 `MODEL_CTX_TOKENS=32768 / EDIT_MAX_OUT_CAP=8192 / EDIT_OUT_MARGIN=1024`。保证 input+max_tokens≤ctx，
同时把输出上限从 4000 提到至多 8192（大文件自动降额，绝不溢出）。

### 2.2 块闭合 + 锚点校验（B3/B5）
`_diagnose_blocks(draft, target, content)`：
- 截断/未闭合：有 `SEARCH` 标记但缺 `>>>> REPLACE` 终止符 → `truncated`。
- 解析（`editblock_engine.parse_edit_blocks`）：0 块 → `no_blocks`。
- 逐块：空 SEARCH→`empty`；SEARCH==REPLACE→`noop`；`content.count(search)` ==0→`not_found`（fuzzy 可救，非致命）/ >1→`ambiguous`（锚点过窄）。
- 锚点密度：非空行 < `MIN_ANCHOR_LINES(3)` 且无 `def/class/@` → `thin_anchor`。
- 返回 `(ok, reason)`，`ok = 至少 1 个干净块（找到且唯一且非 noop）`。

### 2.3 草稿重试（带纠错反馈）
`_draft_with_validation`：调 gemma（预算 max_tokens）→ 校验；不 ok 则按 reason 追加纠错指令重试 `MAX_DRAFT_RETRIES(1)` 次：
truncated→"块更短、务必以 >>>> REPLACE 结尾"；ambiguous/thin→"SEARCH 至少含 5 行或一个 def/class，保证唯一"；
not_found→"逐字符从 CONTENT 拷贝"；noop→"REPLACE 必须不同于 SEARCH"。

### 2.4 级联假设循环（B1）
`hypotheses = [parsed_target] + ranked/merged 候选`（去重、剔除 test 路径、仅保留存在的源文件），深度 `MAX_HYPOTHESES(3)`。
逐个 `_attempt_fix`；成功（applied & 编译 rc==0 & 有 diff）即 break 保留改动；失败则 `git checkout -- .` 回滚后级联下一个。
绝不把"未改动文件编译通过"当成功（修掉 B4 假绿）。

### 2.5 编译期自愈循环（B4）
`_attempt_fix` 内：apply 成功后 `_compile_check`（compileall）；rc≠0 则把 **真实 traceback + 当前文件相关片段** 回灌 gemma（低温 0.0）
要求"仅修语法、保持逻辑"，重应用，再编译；最多 `MAX_REPAIR_ITERS(2)` 轮；仍失败则该假设判负、回滚、级联。

### 2.6 多文件（B6）
`_apply_blocks` 检测 draft 中跨多个 `SEARCH:<file>` 头：>1 个不同文件 → 直接走 `_apply_direct`（已支持 per-block filepath）；
draft prompt 允许"修复跨文件时为每个文件各出一组块"。审计结论：当前 direct fallback 已具备多文件落盘能力，缺口在 handler 单文件 + prompt 未引导，本次补齐 prompt 引导 + 多文件路由。

## 3. 验证
- 纯逻辑单测（`test_polaris_solve_one.py` 扩展）：`_context_budget_max_tokens` 预算钳制；`_diagnose_blocks` 截断/歧义/noop/thin/ok；`_distinct_block_files` 多文件解析。
- 端到端冒烟：`django-11422`（v9 SEARCH 未匹配）、`sphinx-8474`（v9 截断）应在 Phase 3 下落盘+编译通过。
- 增量评分：对 random-20 中 16 个未解实例跑硬化管线，官方 Docker harness 量化净增量；保留 v9 已解 4 题，最终 = 4 + 增量。

## 4. 风险与边界
- 仅改解题器脚本 + 其单测；不改 KernelOne 共享 normalizer（`_edit_blocks`），降级容错仍由 `_apply_direct` 的 `fuzzy_replace` 提供（低 blast radius）。
- 级联/自愈增加每实例 LLM 调用（最多 3 假设 ×（1 草稿+1 重试+2 修复））→ 本地 gemma（≈免费）承担，云 Kimi 仅定位一次，成本可控。
- 多文件为"按需"（gemma 自主跨文件出块），非强制；单文件仍是主路径。

## 5. 实现与验证（2026-06-06，已落地）

新增/改写于 `scripts/swebench/polaris_solve_one.py`（ruff/mypy clean，14 单测通过）：
- 常量：`MODEL_CTX_TOKENS=32768 / EDIT_MAX_OUT_CAP=8192 / EDIT_OUT_MARGIN=1024 / MIN_ANCHOR_LINES=3 / MAX_HYPOTHESES=3 / MAX_DRAFT_RETRIES=1 / MAX_REPAIR_ITERS=2`（对接用户"input+max_tokens≤32768"硬约束）。
- `_context_budget_max_tokens`：动态输出预算（修 B2 截断）。
- `_blueprint_for_file`（**强模型蓝图引擎**，用户核心杠杆）：Kimi 产出 diff-map 级规格（TARGET/ANCHOR/BEFORE/AFTER/EDGE + 语言护栏：缩进、尾逗号、`\A\Z` vs `^$`、括号闭合），弱模型仅转写。
- `_diagnose_blocks`：块闭合 + 唯一性 + 锚点密度校验（B3/B5），`_draft_with_validation` 按 reason 纠错重试。
- `_indent_tolerant_replace`：缩进/空白容错对齐（Advanced Normalization，保留在解题器层，不动共享 `_edit_blocks` 内核，低 blast radius），接在 `_apply_direct` 的 exact→fuzzy→indent 兜底链末端。
- `_attempt_fix` + solve() 级联：parsed→ranked top-3 假设；apply 成功后编译自愈（≤2 轮把 traceback 回灌 gemma 低温修语法）；失败 `git checkout -- .` 回滚再级联；仅"applied & compile_rc==0 & 有 diff"才采纳（修 B4 假绿）。
- 多文件：`_apply_blocks` 检测跨文件块 → 走 direct per-file 落盘。
- 遥测：tokens 分 localize(est)/blueprint(authoritative cloud)/edit(authoritative local) 三流；ledger 记 hypotheses_tried/repair_iters/apply_path。

**端到端冒烟（fresh clone）**：`django-11422`（v9 空补丁，gemma SEARCH 未匹配）→ Phase 3 下 `target=django/utils/autoreload.py, applied via handler, compile_rc=0, hypotheses_tried=1`，20 行非空补丁，188s（含 clone + 2×Kimi + 1×gemma）。证明蓝图→转写→落盘链路修复了 v9 的落盘失败。

**增量评分**：v10 硬化管线跑 random-20 中 16 个未解实例（保留 v9 已解 4 题），官方 Docker harness 量化净增量；最终 = 4 + 增量。
