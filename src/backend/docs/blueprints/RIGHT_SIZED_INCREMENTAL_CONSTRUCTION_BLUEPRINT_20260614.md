# Right-Sized Incremental Construction Blueprint (大文件增量构建)

- 日期: 2026-06-14
- 范围: `src/backend/polaris` — chief_engineer.blueprint (CE fission) + kernelone.llm.engine (budget) + roles.kernel (gateway/budget) + runtime.task_market (intra-file chaining)
- 关联: R7（[[repair-preserving-edit-r7]]，edit-mode + 反缩水）、F8（file-ownership ledger）、ADR-0090（弱模型 harness）、CapabilityProfile(MAPE-K) 早前专家设计
- 前置阅读: `AGENTS.md`, `docs/AGENT_ARCHITECTURE_STANDARD.md`

## 1. 问题与不可回避的物理事实

实测 r29：CE 把整个 12 函数砖块游戏的 main.js 裂成**一个步骤** s4_js；弱 Director（qwen3.6-27b@16k，单轮可用输出 ~8-10k tokens ≈ ~250 行）把 ~256 行的写**截断**（finish_reason=length）→ 括号不闭合 → PreWriteGuard 拦下 → **零落盘** → 3 试死信 `director_no_materialized_changes`。

**不可回避的物理事实**：一个**窗口有界的弱模型，永远无法可靠地一次写出任意大的文件**。放大输出预算（把 qwen 8192→16384）只是创可贴：①模型特定、②只多买 ~1k token、③下一个 300+ 行文件照样死。

**长远可靠的唯一正解**：**绝不把超过"一轮容量"的工作单元交给码农；当文件大于一轮，就跨轮增量构建**。这正是"好组织让弱模型胜任"命题的硬核——组织（CE/market）负责把活切到码农一口能吃下的大小。

## 2. 人类已有理论锚点
| 理论 | 映射 |
|---|---|
| 分而治之 / 工作分解结构(WBS) | CE 把大文件切成骨架+增量填充步 |
| Scaffolding（先骨架后填充，编译器/代码生成常用） | 骨架步先写全部函数签名桩（可解析），填充步逐组实现 |
| 增量式开发 / 持续集成（小步可验证提交） | 每个填充步是有界、可 verify 的 delta，文件逐步长成 |
| 自适应控制 / 能力感知（MAPE-K） | 单轮预算从模型窗口推导（A），决定切多细 |
| 回归守卫（R7-C 反缩水） | 填充步不得抹掉已累积内容 |

## 3. 三件套（durable，可用于任意大小文件 / 任意弱模型）

### A. 能力自适应预算（generic，非 qwen 魔数）
- 从模型窗口推导**单轮可用输出预算**：`usable_output ≈ resolved_window × FRACTION − prompt − overhead`，而非硬编码 `max_output_tokens=8192`。
- 落点（待 scout 确认）：`_executor_base.resolve_requested_output_tokens` / `executor.py` 输出预算计算 / `prompt_budget.clamp_output_tokens_to_window`；仅对 materialization/write 轮放大，read/decision 轮不变。
- 推理模型注意：reasoning_content 也吃预算，FRACTION 要给推理留头。
- 折进早前 CapabilityProfile(MAPE-K) 设计：一处从 ModelSpec 解析，env 硬覆盖，constrained tier == 今日默认（零行为变化迁移）。
- 产物：**单轮行预算** `one_turn_line_budget`（供 B 用），例如 qwen@16k ≈ ~180-220 行（留语法/重试余量，取保守）。

### B. CE 右-size 分解门（确定性，不信任 CE 自觉）
- 在 CE 提步之后、发布之前，对**单文件代码步**做确定性变换：当 `est_lines`（或签名数 × 经验行/函数）> `one_turn_line_budget` → 拆为：
  - **骨架步** `<id>_skeleton`：`write_file` 写出该文件**全部函数签名 + 顶层结构的桩**（空体 `{}` / `pass` / `return`），小、可解析、一轮能写完。verify = 文件存在 + 每个签名 grep 命中。
  - **K 个填充步** `<id>_fillN`：每步实现**一组函数体**（按 one_turn_line_budget 分组），`edit_on_prior=true`、`target_file` 同文件、`depends_on=[骨架步, fill(N-1)]` 串行。verify = 该组函数体的结构判据（非空体 / 关键调用）。
- 拆分是 deterministic（基于签名清单 + 行预算），不靠 LLM。**fail-open**：无法可靠拆分（签名缺失/异形）→ 不拆，保持原步（退化为今日行为）。
- 复用既有机制：market `_exec_claim_ready` + depends_on 串行；file-ownership ledger 属主链；R7-A 让填充步走 edit_blocks；R7-C 防填充抹掉骨架/前组。

### C. 增量构建（多数已落地，确认即可）
- 骨架步：from-scratch 单文件 → Prong A 强制 write_file 一轮写桩。
- 填充步：target 已存在 + edit_on_prior → R7-A `restrict_tool_definitions_to_edit` 强制 edit_blocks（改桩为实现），R7-C 反缩水守护累积内容，consumed_interfaces 注入跨文件名。
- QA 每步 verify；文件逐步长成可运行整体。

## 3.5 实施进度（2026-06-14）
- ✅ **B 已落地+实测（主路，durable correctness）**：新 `step_splitter.py`（纯函数 `split_oversize_steps`：触发=代码靶+签名≥4+(est_lines≥100 或 签名≥5)；骨架步全签名桩 from-scratch + K 填充步每组~3 函数 edit_blocks、线性 depends_on 链；verify=`node --check`/`py_compile`+符号 grep；fail-open=env关/已拆/超24步/未知后缀/跨父属主；**跨父属主 owned_elsewhere 守护**防 clobber；**被拆步的依赖者重指向终态 fill**防悬挂依赖）。hook 进 `ce_consumer._claim_and_process_one`（读属主后、record_owners 前，re-gate 后采纳）。
- ✅ **R7-A 广义化**：`resolve_repair_edit_target` 现在 last_failure **或** `edit_on_prior` 命中→硬制 edit_blocks，使填充步首试即被强制编辑骨架（非软提示），R7-C 防缩水守累积内容。`_publish_step_tasks` 只对跨父加 edit_on_prior、不会清掉填充步的 edit_on_prior（实证 line 671-675）。
- 验收：26 splitter 单测 + R7-A edit_on_prior 单测 + 225 passed（ce + kernel 回归），ruff/mypy clean。**关键洞见**：B 把每步切到 ~36/~60 行，8192 预算绰绰有余 → r29 收敛**不依赖 A**。
- ⏳ **A 延后（generic window-aware budget）**：设计已 grounding（scout brief 存档：`expand_output_to_window` + 放宽 `resolve_requested_output_tokens` 的 `min(requested,max_output_tokens)` 硬顶 + materialize 轮 gate）。B 之后单独成批验证——A 触执行器热路径（全 28k 测经过），与 B 同跑会混淆归因且增风险；右-size 后预算非瓶颈。**先单独验证 B**。

## 4. 关键可行性问题（scout 已求证）
1. CE 提步→发布之间的确定性 hook 点（ce_consumer `_extract_steps`/`_publish_step_tasks` / step_contract）。
2. market 是否支持**同父、同文件、串行**步（骨架→fill1→fill2，depends_on 链 + ledger 不冲突）——还是只支持跨父 edit_on_prior。
3. 骨架步 vs 填充步的 verify 如何通用生成。
4. 单轮行预算的保守取值（含语法/重试余量）。

## 5. 测试计划
- 预算(A)：window-aware 输出预算单测（小窗口模型放大、大窗口不变、env 覆盖、constrained==今日）。
- 分解(B)：给定 12 签名 + 行预算 → 产出 1 骨架 + K 填充步，depends_on 链正确、edit_on_prior 正确、target_file 一致、step_id 唯一、verify 合理；fail-open（异形签名不拆）。
- 增量(C)：骨架步 from-scratch→write_file；填充步→edit_blocks（复用 R7 测试）。
- 集成：市场实跑 main.js 不再单步截断死信；逐步 resolved；终态文件 node-check 有效、构件齐全。

## 6. 风险与边界
- 拆太细 → 步数爆炸 / QA 往返成本。取 one_turn_line_budget 保守但不过细（每填充步 ~3-4 函数）。
- 骨架桩需可解析（语法门会拦不可解析骨架）——桩用最小合法体。
- depends_on 链任一步死 → 后续连坐（已知级联）；填充步死不应抹已写部分（R7-C 守护）。
- 全程 fail-open：分解不确定就不分解，退化为今日单步行为，绝不把可工作的小文件路径搞坏。
- GENERIC：分解基于签名/行预算，无语言/业务特定假设（CLAUDE.md §8）；桩生成按语言（js/py/ts）用最小合法模板（语言通用，非业务）。

## 7. 验收门
ruff/mypy/触及 cell pytest 全绿 → 市场实跑：超预算 main.js 步被自动拆为骨架+填充、逐步 resolved、终态可运行、product_coherent=True。
