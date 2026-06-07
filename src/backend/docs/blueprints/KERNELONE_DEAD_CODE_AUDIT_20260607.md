# KernelOne 死代码审计与分阶段收敛蓝图 (2026-06-07)

## 1. 背景与目标

承接角色债收敛之后，对 `src/backend/polaris/kernelone/`（底层运行时基座，1152 个 `.py`
文件，798 个非测试模块）做一次全量可达性审计，量化"建好但从未接线"的 aspirational /
leftover 代码，并给出**分阶段、可验证**的收敛计划。

## 2. 方法论（静态可达性，AST 解析）

1. 用 `ast` 解析全仓 `polaris/**` 的每个模块，构建 import 图：
   - `import a.b` → `a.b`
   - `from a.b import c` → 同时记 `a.b` 与 `a.b.c`（关键：捕获属性式子模块导入）
   - 相对导入 `from ..x import y` → 解析为绝对 `pkg.x` + `pkg.x.y`
2. **live roots** = 所有非 `kernelone`、非测试模块（delivery / cells / application /
   domain / bootstrap / infrastructure）。
3. 从 live roots 做 BFS 传递闭包；`kernelone` 中**不在闭包内**的模块即"生产不可达"。

## 3. 核心发现

- `kernelone` 非测试模块 **798** 个；live 可达 **401**；**生产不可达 397（~107k 行，约占该层一半）**。
- 死代码高度集中在 aspirational 子系统（按行数）：
  | 子系统 | 死模块 | ~行数 | 性质 |
  |---|---|---|---|
  | context（死子集） | 71 | 24763 | 多套并行 context 实现 |
  | benchmark / holographic | 59 | 18558 | 基准/"全息"回放，仅自引用 |
  | audit（死子集） | 30 | 8639 | omniscient 审计管线 |
  | llm（死子集） | 43 | 7914 | ⚠️ 含动态发现 handler，**误报风险高** |
  | multi_agent / neural_syndicate | 11 | 5842 | 多智能体编排 |
  | cognitive | 29 | 5796 | perception/reasoning/personality/evolution |
  | akashic | 9 | 5013 | 知识管线 |
  | role | 23 | 3042 | 与 cells/roles + recipe composer 并行 |
  | single_agent（死子集） | 13 | 2681 | 已部分清理 |
  | …（其余 ~30 子系统） | | | |

## 4. 风险分级与误报源（务必先验证再删）

- **动态发现假阳性**：`ToolHandlerRegistry.load_all()` 按目录遍历加载
  `llm/toolkit/executor/handlers/**`，这些 handler 经 `register_handlers()` 注册、
  无静态 import，会被本方法**误判为死**。`llm` 的 43 个"死模块"必须逐一排除此情况。
- **Port-adapter 基座**：`effect` / `locks` / `scheduler` / `ws` / `messages` 实现了
  `contracts.technical.master_types` 声明的 Port（"replace with Redis/NATS"），属"先建基座、
  尚未接线"。删除会改变架构意图（声明的 Port 失去唯一实现）→ **保留并标记，删除前需确认**。
- **测试覆盖**：部分死子系统自带 passing 测试（如 observability 3、performance 2）。
  删除即连带删除其测试——对生产死代码是正确的，但需在 commit 说明。

## 5. 分阶段收敛计划

### Wave 1（本蓝图执行）：纯 aspirational、零导入者孤岛
零生产 + 零 kernelone 导入者、非 Port-adapter、不受动态发现影响，且与已被批准删除的
`dynamic_role`/`subagent_runtime` 同构。7 个子系统 / 33 文件 / 5654 行：
`agent`、`dialogue`、`distributed`、`learning`、`reasoning`、`performance`、`observability`。
（三处对 `agent` 的引用经核实均为**指向已不存在路径的过时注释/日志串**，非真实 import。
`learning`/`reasoning` 的唯一外部测试是 `agent/tests/test_phase3_integration.py`，随 `agent` 一并删除。）

### Wave 2（后续，需逐子树验证）：动态发现排除后的 llm 死子集
对 `llm` 43 个候选逐一确认是否为 `register_handlers()` 动态加载；排除后删除真死部分。

### Wave 3（后续，需 ADR）：大型 aspirational 子系统
`cognitive` / `akashic` / `multi_agent` / `benchmark`(holographic) / `context` 死子集 /
`audit` omniscient 死子集。体量大、含测试、可能为路线图脚手架 → 每子树独立
Verification Card + ADR，确认非路线图后再删。

### Wave 4（后续，需确认）：Port-adapter 基座
`effect` / `locks` / `scheduler` / `ws` / `messages` + `kernelone/__init__.py` 中对应的
死 `__all__`/`_LAZY_MODULES` 条目。要么接线，要么连同声明的 Port 一并下线。

## 6. 验证门禁（每个 Wave）

`ruff check --fix` + `ruff format` + `mypy`（受影响面）+ `pytest`（相关 + import-fence
`test_kernelone_release_gates` + `pytest --collect-only` 无新增 error）。fail-closed。

## 7. 多专家裁决结果（2026-06-07，已执行）

Workflow `kernelone-deadcode-adjudication`（64 agent：每子系统 1 审计员 + 每 DELETE 1 对抗反驳）。
**42 个静态死子系统中 31 个被推翻保留**——反驳专家找到了静态图漏掉的真实消费者
（`register_handlers()` 动态加载的工具 handler、包 `__init__` 的 eager/lazy re-export、
活 cell 的导入）。教训：子系统粒度的静态"死"假阳性率 ~74%，对抗式复核不可省。

净结果 7 DELETE / 1 WIRE / 2 ESCALATE，已落地：
- **Wave 2**（commit `11dbb8f9`）：删除 `testing`、`tool_creation`、`ws`（被 `delivery/ws` 取代）、
  `planning.self_reflective_engine`，共 2510 行。
- **Wave 2b**（commit `4f8c0951`）：删除 `prompts.catalog`/`prompts.utils`，并把 workspace-escape
  安全测试**迁移**到活的后继 `StreamingPatchBuffer`/`StrictOperationApplier`（已验证通过，未丢覆盖）。
- **HELD**（与"保留的死簇"交叉纠缠，须整簇处理）：`prompt_registry`（被保留的
  `prompt_registry_hot_reload` + `benchmark.holographic.runner` 导入）、`runtime.run_id`
  （被死的 `audit.evidence_paths` 导入）→ 并入 benchmark/holographic 死簇专项。
- **WIRE**（待专项实现）：`editing` 按 ADR-0062 把 OpenCode replacer 链接入
  `apply_fuzzy_search_replace`；ADR 要求做成 pre-processor（改编辑热路径行为）→ 需回归语料后再落。
- **ESCALATE**（产品决策，勿自动删）：`multi_agent`（neural_syndicate 愿景，今日审计已为其留 ADR 口）、
  `messages`（OpenCode Part 类型，是某 ENFORCED CI 门禁声明的收敛锚点，删除会触发门禁）。

## 8. 续批裁决（2026-06-07，已执行）

承接 `全量推进`，对剩余三桶逐一落地。关键修正：**之前的子系统级"死"判定漏掉了"父包
`__init__.py` 的 eager re-export"这一类边**——导入任一子模块会执行父包 `__init__`，
其 eager import 的全部模块因此在导入期即为活。补上该语义后，多处"死"被推翻为活。

### 8.1 benchmark 死簇（精确划定后删除，commit `bb738a08` + `8482c115`）
live edge 确认：`cells/llm/evaluation/public/service.py` 真实导入
`benchmark.unified_judge/unified_models/unified_runner`，`infrastructure/accel/eval`
导入 `benchmark.adapters.context_adapter`——**benchmark 整体非死**。补上父包 `__init__`
eager re-export 语义后，死子集从朴素 59 → 19，再排除被活测试 `test_unified_judge` 行使
的 `validators/` → 安全删除 17 个模块：`_archived`、`chaos/`（自带测试）、`llm/`（自带测试）、
`contextos_cases.py`、`holographic_runner.py`（自述 back-compat shim，仅其测试引用）、
`reporting/formatters.py`（未被 `reporting/__init__` 导入），计 7573 行。codegraph 对抗复核
零外部 caller。治理同步：从 reverse-dep-fence baseline 移除 `holographic_runner.py`，
从 `kfs_direct_write_baseline.txt` 移除 2 条，修正 2 处指向已删 `benchmark.llm.tool_accuracy`
的 Intent-Separation docstring。门禁绿（release-gate 5 passed、reverse-dep+kfs 7/1skip、
collect-only 0 error、安全回归 10/10）。
> 注：本批因仓库存在并发提交者（git user `openhands`），改动被其 `bb738a08`(删除)+
> `8482c115`(我的 4 处 docstring/治理编辑) 两次提交合并落地，树态一致、门禁在提交前已绿。

### 8.2 editing → 接入（ADR-0062，commit `72acb405`）
把此前仅被测试/门禁声明、生产不可达的 `editing.replacers.get_replacer_chain` 接入活路径
`apply_fuzzy_search_replace`，置于最宽松的 `_sequence_match_apply` 之前作为**精确层**。
新增覆盖：首尾行锚定 + 中段差异很大的 block-anchor 编辑（10 种既有策略与 SequenceMatcher
均返回 None）现可命中，且带**唯一性护栏**（候选在内容中出现>1 次则跳过，绝不静默改歧义位）。
TDD 红→绿；editing 全套 80 passed、applier 路径（protocol kernel + 安全回归）50 passed、
ruff+mypy 干净。replacer 链由此从死代码转为生产可达，满足收敛门禁的 canonical-import 意图。

### 8.3 multi_agent → 已是活（仅删死叶 knowledge_share，commit `47c3bfda`）
修正后 multi_agent **11 活 / 1 死**：`bus_port` 被活的 `cells/roles/runtime/internal/
{kernel_one_bus_port,bus_port}.py` 导入。原 ESCALATE 判定过保守。仅 `knowledge_share`
（KnowledgeSharingBus）为死叶——multi_agent 是无 `__init__` 的命名空间包，唯一引用是其自带测试。
删除模块 + 测试（355+289 行）。multi_agent 套件 26 passed。

### 8.4 messages → 仍待产品定夺（未动）
`messages` + `messages.part_types`（460 行 OpenCode Part 类型）**零生产导入者**（grep 命中皆为
`kernelone_messages_dropped_total` 指标串，非 import），是真正"建好未接线"。但它是
`opencode_convergence_gate._RULES` 中显式声明的收敛锚点（`deep_prefix=messages.part_types`，
canonical=`from polaris.kernelone.messages import Part, ...`），且属 staged-rollout 治理范围。
门禁规则当前**空转**（无人深导入即不触发），删除规则后亦不报错。两条"彻底"路径都重：
(A) 大型 WIRE——把 handoff/消息体系迁到 Part 类型（ADR 级，数周）；(B) 删除 + 退役门禁规则 +
更新 staged-rollout（治理反转）。鉴于并发提交者正活跃于 OpenCode/role-runtime 区域，
不宜单方面删除已声明的收敛目标 → 留待人类定夺（见对话）。其余三桶已全部彻底接入或彻底删除。
