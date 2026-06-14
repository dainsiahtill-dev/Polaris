# Repair-Preserving Edit Blueprint (R7 修复保留式编辑)

- 日期: 2026-06-14
- 范围: `src/backend/polaris` — roles.kernel + director.task_consumer + kernelone.quality
- 关联: ADR-0090 (weak-model harness hardening), F8 (file-ownership ledger), F11/F12 (bootstrap clobber / Prong A from-scratch write)
- 前置阅读: `AGENTS.md`, `docs/AGENT_ARCHITECTURE_STANDARD.md`

## 0. 实施进度 (2026-06-14)
- ✅ **R7-A 已落地+实测**：`resolve_repair_edit_target` + `restrict_tool_definitions_to_edit`（`_FULL_REWRITE_TOOLS`/`_ANCHORED_EDIT_TOOLS`，subtractive=只删 `write_file`/`append_to_file`、保留读+编辑工具、无锚定编辑工具时 fail-open）于 `tool_helpers.py`；对称分支 wired 进 6 个 kernel pin 点（engine.py×2 / kernel/turn_engine.py×2 / kernel/core.py×2，`else`-分支与 Prong A 互斥）。12 个新单测。
- ✅ **R7-C 已落地+实测**：`_repair_prior_target_size` / `_repair_shrink_error` / `_repair_shrink_guard_ratio`（env `KERNELONE_REPAIR_SHRINK_GUARD_RATIO`，默认 0.6，<400B 不守卫，fail-open）于 `director_consumer.py`；前置体量快照 + 后置反缩水门 wired 进 `_process_claim`。9 个新单测（含 poll_once 集成：缩水→requeue REPAIR_SHRANK_FILE / 保留式→ack pending_qa）。
- ✅ **活路径已核实**：市场 mainline 走 `dispatch_pipeline.py` → `DirectorExecutionConsumer.poll_once()` → `_process_claim`（R7-C 在此）；R7-A 在 RoleExecutionKernel turn 层，所有执行路径必经。非 DirectorPool 死路径。
- 验收：456 passed（roles.kernel + director.task_consumer 全套），ruff/mypy clean。
- ⏳ **R7-B 待落地**：gateway 修复轮指令（把 prose「不要原样重写」换成 `[REPAIR — LOCALIZED EDIT REQUIRED]` 强指令 + 失败签名 + 注入带行号现状切片）。
- ⏳ **R7 升级阶梯待落地**：连续 K 轮发不出有效编辑 → 放开 write_file 但仍挂 R7-C。

## 1. 问题陈述 (Result-oriented)

绑定弱模型 (Director=qwen3.6-27b-int4@16k) 跑实战项目时，**生成**能力在 r27/r28 已被解封：
一旦拆掉人为输出天花板，弱 qwen 能一次写出 5762 字节、22 个构件的真实砖块游戏。
**剩余的最后通用根因 (R7)** 出在**修复轮**，不在首写轮：

> 当 QA 或活体语法门 (`node --check`) 因**一个**错误（如对象字面量里的 `dx: 0;` 分号）
> 把一个**已存在且实质完整**的文件弹回 `pending_exec`，弱模型被再问时**不做定点修复**，
> 而是**从零重写成一个更小、更简化的版本**，丢掉 Ball/Brick/计分/生命。
> 结果：一个本来只差一个分号就能跑的产物，被"修"成了不可玩的残体。

这是弱模型的真实能力短板（外科式编辑 / 不破坏既有内容的能力弱），
**架构必须替它兜住**，而不是寄望它自己学会。这与跨父 file-ownership ledger 同源：
同一个"edit-not-rewrite（改而非重写）"原则，从"跨父文件冲突"推广到"同步骤修复"。

## 2. 人类已有理论锚点 (Theory grounding)

每条机制都显式对应一个成熟理论，避免拍脑袋：

| 理论 | 出处 | 映射到本蓝图的机制 |
|---|---|---|
| SEARCH/REPLACE 编辑块 + "lazy coding/elision" 缓解 | Aider edit-format | `edit_blocks` 工具即此格式；修复轮强制走它而非 `write_file` |
| 最小编辑 / 默认保留 (preserve-by-default) | str_replace_editor (OpenHands/Claude) | 锚定式替换，未触及区域逐字保留 |
| Delta Debugging（最小失败区域定位） | A. Zeller, *Why Programs Fail* | 把编辑范围**界定到失败处**，而非整文件 |
| Self-Refine（具体反馈驱动自我改进） | Madaan et al. 2023 | 把**精确**失败信号（语法错误行 / 未过 verify 子句）回灌进修复提示 |
| 回归守卫 / fail-closed 门 | 软件工程回归测试原则 | 确定性"反缩水门"，**不信任**模型自觉，体量骤缩即拒收 |

核心立场：**前两条"松绑+引导"靠改变模型输入；最后一条"兜底"靠确定性门**。
弱模型合规性不可靠，所以**最强保证来自不依赖模型的确定性门 (R7-C)**。

## 3. 现状数据流（已确认的真实路径）

1. 弱 Director 首写 `main.js`（真实游戏，已存在、实质完整）。
2. 活体语法门 / QA 发现一个错误 → `director_consumer.fail_task_stage(requeue_stage="pending_exec")`，
   携带 `last_failure`（QA verify 输出 / 语法错误）。
   （`director_consumer.py:572-587` EXEC_TARGET_MISSING、`:808-814` bounce teaching 已是此机制。）
3. 重排后 Director 再执行：`_execute_task` 把 `construction_step` + `pre_state_verify`(punch_list) +
   `last_failure` 注入 adapter context（`director_consumer.py:800-814`）。
4. **缺陷点**：此修复轮里，工具集**仍含 `write_file`**，提示**未强制保留**，
   **无反缩水门** → 弱模型重写成更小版本，QA/语法可能这次过了，但产物退化。

对照：首写轮 Prong A（`tool_helpers.resolve_from_scratch_write_target` +
`restrict_tool_definitions_to_write`，键控**文件不存在**）已落地。R7 是其**对称反演**（键控**文件存在 + 有 last_failure**）。

## 4. 设计：三道防线（unblock → steer → fail-closed gate）

### Prong R7-A — 工具限定：强制外科式编辑，禁用整文件重写
- 新增纯函数 `resolve_repair_edit_target(context_override, workspace)`（`llm_caller/tool_helpers.py`）：
  当且仅当 `construction_step` 存在 **且** 单一 `target_file` **已存在** **且** context 携带非空 `last_failure`
  （即这是一次修复/弹回轮）→ 返回该 target，否则 `None`。
  与 `resolve_from_scratch_write_target`（键控文件**不存在**）互斥对称。
  env `KERNELONE_REPAIR_PRESERVE_EDIT ∈ {off,none,disabled,false,0}` 关闭。
- 新增 `restrict_tool_definitions_to_edit(tool_definitions)`：
  保留 `_EDIT_KEEP_TOOLS = {edit_blocks, edit_file, repo_apply_diff, treesitter_replace_node,
  treesitter_insert_method, treesitter_rename_symbol, execute_command}`，
  **丢弃 `write_file` / `append_to_file` / `precision_edit`(已弃用)**。
  若过滤后无任何编辑工具存活 → 返回原列表（永不把一轮工具清空）。definitions 绝不原地修改。
- 与 Prong A **不同的关键点**：修复轮**保留读工具**（`repo_read_slice` 等），
  因为 `edit_blocks` 的 SEARCH/REPLACE 需要锚定既有内容。或由 R7-B 直接把带行号的当前文件切片注入提示，
  让模型直接发**行区间** `edit_blocks`（`file`+`start`+`end`+`replace`），免一次读。

### Prong R7-B — 保留式指令：Self-Refine 精确反馈引导
- 在修复轮注入一条系统指令，**点名**：文件已存在、其行数/字节数、**精确失败**
  （`last_failure` 里的语法错误行 / 未过 verify 子句逐字引用），并要求：
  "只修该错误；保留所有既有函数/类/逻辑；**不要**从零重写或简化；用 SEARCH/REPLACE（edit_blocks）发局部编辑。"
- 附带**当前文件带行号切片**（或全文，受 16k 预算约束），让弱模型能直接发行区间编辑。
- 落点：`last_failure` 已被线程进 adapter context 处（`director_consumer.py:808-814`）+ 修复提示构造层。

### Prong R7-C — 确定性反缩水回归门（fail-closed 兜底，最强保证）
- 在 `director_consumer._execute_task` 执行**前**，对**已存在**的 step target 记录
  `prior_size_bytes` / `prior_line_count` / 结构指纹（函数/类计数，复用 step_verify 已有的结构识别）。
- 执行**后**，若本轮是修复轮（有 `last_failure`）**且** 目标先前实质完整 **且** 结果**骤缩**
  （`new_size < SHRINK_RATIO × prior_size`，默认 `0.6`）**或**结构构件计数下降 →
  `fail_task_stage(requeue_stage="pending_exec")`，teaching error：
  "你的修复删除了既有内容（原 N 行 → 现 M 行）。该文件除 `<error>` 外本可运行。
  只用 edit_blocks 修该错误，不要重写。"
- env `KERNELONE_REPAIR_SHRINK_GUARD_RATIO`（默认 0.6）。**Fail-OPEN**：先前体量未知 / 非修复轮 → 不拦。
- 这是唯一**不信任模型**的防线，是 R7 的硬保证。

### 升级阶梯（防止修复轮无限弹回死信）
- 弱模型若连续 K 轮发不出有效 `edit_blocks` → 第 K+1 轮**放开 `write_file`**，
  但**仍挂 R7-C 反缩水门**并把**先前完整内容**注入提示，使"重写"至少不得小于先前体量。
- 复用现有重试上限/死信机制，不新造。

## 5. 能力自适应耦合 (MAPE-K)
三道防线默认对**窗口 ≤24k 的弱模型**开启。强模型（>64k、能干净外科编辑）应自动关闭 R7-A/R7-B 的强约束，
仅保留 R7-C 作为廉价回归守卫。具体阈值由 `CapabilityProfile`（独立蓝图）统一解析；
本蓝图所有 env 均为该 profile 的硬覆盖（override），保证既有 env 驱动测试不破。

## 6. 落点清单 (file:function)
- `polaris/cells/roles/kernel/internal/llm_caller/tool_helpers.py`：
  `resolve_repair_edit_target`、`restrict_tool_definitions_to_edit`、`_EDIT_KEEP_TOOLS`、`_REPAIR_PRESERVE_EDIT_ENV`。
- Prong A 同款 kernel pin 点（对称分支）：`turn_engine/engine.py`、`kernel/turn_engine.py`、`kernel/core.py`。
- `polaris/cells/director/task_consumer/internal/director_consumer.py`：R7-C 前置体量快照 + 后置反缩水门；R7-B 指令注入。
- `polaris/kernelone/quality/step_verify.py`：复用结构构件识别供 R7-C 结构指纹。

## 7. 测试计划
- `test_tool_helpers.py`：`TestResolveRepairEditTarget`（文件存在+last_failure→target；不存在→None；无 last_failure→None；edit_on_prior 交互；env off）、`TestRestrictToolDefinitionsToEdit`（保留 edit_blocks、丢 write_file、无编辑工具→原样返回）。
- 新建 `test_repair_shrink_guard.py`：骤缩→requeue；体量持平→放行；先前体量未知→fail-open；非修复轮→不拦。
- 反缩水 teaching error 文案断言；env ratio 可调。

## 8. 风险与边界
- 弱模型仍可能发非法 `edit_blocks` → 编辑失败 → 弹回。由升级阶梯 + 死信上限兜底，不无限循环。
- 行区间 `edit_blocks` 需当前行号 → R7-B 注入带行号切片避免额外读轮。
- R7-C 结构指纹用语言无关启发式（行数/字节 + 粗构件计数），避免引入特定语言/业务假设（CLAUDE.md §8）。
- 全部 fail-open / 返回原列表的安全降级：任何一道防线异常都不得把一轮工具清空或误杀正常修复。

## 9. 验收门
`ruff check --fix` + `ruff format` + `mypy`(Success) + 触及 cell 套件 pytest 全绿；
随后市场实跑验证：修复轮后 `main.js` 行数/构件数**不降**，可运行率不因修复而回退。
