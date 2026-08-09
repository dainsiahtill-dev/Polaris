# CLAUDE.md

本文件用于指导在本仓库工作的 AI 编码代理。仅保留可执行、可验证的技术约束。

**必用MCP和Skill**: 充分利用codegraph MCP和superpowers，必要时需要使用Playwright来真实跑测试和审计。

## -1) 强制工具链：RTK + CodeGraph + Caveman

- **RTK**：每条 shell 命令及每个 chained segment 都必须以 `rtk` 开头。优先使用 RTK 原生命令；无对应命令时使用 `rtk proxy <command>`。裸 shell 命令只允许用于明确的 RTK 故障诊断。
- **CodeGraph**：源码发现、架构/调用链/影响分析、代码审查及修改前上下文必须先调用 `mcp__codegraph__codegraph_explore`。返回源码视为已读，禁止再用 `rg`/reader 重复读取；只有 CodeGraph 未覆盖、未索引或明确不可用时，才允许使用 RTK fallback，并记录原因。
- **Caveman**：默认启用 `caveman` 的 `full` 模式，压缩过程更新与结果，删除重复叙述和无关日志，但必须保留精确技术名、错误文本、命令、验证数字、风险与未完成门禁。安全警告、不可逆操作和可能产生歧义的多步流程恢复完整表达。
- 三者必须共同使用，不能用其中一个替代另两个。标准顺序：加载适用 Skill -> CodeGraph 定位/审计 -> RTK 执行与验证 -> Caveman 压缩汇报。

## 0) 后端权威入口（2026-03-22）
- 对于任何 `src/backend` 任务，必须先读 `src/backend/AGENTS.md`。
- 统一架构执行标准入口：`src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`。
- 后端强制规则：`Cell` 开发先复用已有 Cell 公开能力；所有新开发必须基于 `KernelOne` 底座能力与契约链路。
- 若本文件与 `src/backend/AGENTS.md` 或 `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` 存在冲突，以后两者为准。

### 0.0) 无人值守项目完成权威（强制）

- 项目完成身份必须精确绑定 `(workspace, project_id, run_id, completion_contract_hash)`；禁止按“最新 run”、路径猜测、跨 workspace 搜索或 caller 自报字段拼接完成事实。
- Chief Engineer 只拥有整个 PM task-set 的 typed completion contract；合同必须绑定 owner task、required artifact、verifier modality、canonical argv/cwd、entrypoint（或显式 N/A）和合同 hash。Director 只能无损传递该合同及 hash，不能缩减、改写或自行声明项目完成。
- `factory.verification_guard` 只消费 owner contract 并执行物理验证；artifact hash、依赖准备、build/test/lint、entrypoint 都必须形成按 obligation/owner/hash 绑定的 typed receipt。禁止信任 caller-supplied evidence、generic mapping receipt、TaskBoundary、stage gate、日志摘要或“磁盘看起来存在”；`audit.evidence` 只能保存审计副本，不能制造执行权威。
- `orchestration.workflow_runtime` 只拥有 durable convergence cursor：残差、依赖、attempt budget、action reservation、settlement 和终态；跨进程更新必须 CAS，effect 前必须先 reserve，崩溃重放必须复用 deterministic action id。禁止在 Factory、adapter、bench 或 Agent 中另藏重试循环/第二事实源。
- `runtime.projection` 是唯一可签发 `completed_verified` 的 owner。只有 exact completion contract 与全部 owner-sealed physical receipts 一致、无 missing/failed obligation 时才能完成；VerificationGuard、Director、QA、workflow runtime 和外部 Supervisor 都不得签发最终成功。
- 角色失败必须阶段局部恢复，禁止默认重跑整条 `PM -> Chief Engineer -> Director`：CE 输出/schema 失败只重试 `chief_engineer_review`，复用已提交 PM contract 并把失败证据注入下一次最终 provider request；Director 失败只保留已完成 task、重开未完成 task，执行 bounded `edit/repair -> affected verifier`；QA/Verifier 失败只回到 exact owner Director task。PM 仅在 PM contract 本身无效或被显式 supersede 时重跑。重复唤醒必须用 TaskMarket durable idempotency receipt 去重；局部预算耗尽输出 `model_ceiling`/结构化 blocker，不自动升级上游。只有不可变合同矛盾、权限或架构 authority 冲突可 fail-closed，且仍禁止自动改写合同。
- Director 局部 repair 的“进展”必须同时满足：authoritative write receipt 对应负责路径的真实 fingerprint 变化、required verifier 复跑、诊断或 missing target 净减少且不引入新诊断。纯读取、同内容写入、只改变诊断签名、等量换错或错误增多一律记为 stagnation；连续 2 次 stagnation 必须停止 Provider 调用，输出 `director_quality_repair_stalled` + `model_ceiling`，并保持 `retry_scope=same_director_task_only`、禁止回退 PM/CE。
- TaskRuntime 执行历史与 TaskBoundary 交付权威是独立轴。Factory 权威投影只能从已提交 PM stage event 的 immutable artifact binding 重验并取得 PM 合同任务，禁止从可变 `tasks/plan.json`/mirror 重建完成义务；PM contract IDs、TaskRuntime IDs、TaskBoundary IDs 归一化后集合必须完全一致。缺失合同任务、`N`/`TASK-N` 合同 alias collision、重复 runtime identity 必须 fail-closed，settlement/verifier 辅助行与 boundary 不得新增或污染交付义务。TaskRuntime 已终态 `failed/cancelled` 时，只有 canonical `completed_verified` TaskBoundary 同时具备 ledger append/content 坐标、evidence refs、零 missing/failed obligation，才可保留失败历史并授权进入 QA；`pending/in_progress/blocked` 或仅磁盘扫描永远不得放行。
- Factory stage 失败不得仅以“可能本地修复”为由占住 workspace lease。local rework 必须查询 TaskMarket canonical `TaskRequeueReceiptV1`，校验 workspace/task/idempotency/receipt hash，并证明 owner 属于 immutable PM contract、TaskRuntime 绑定当前 factory run、目标阶段符合局部恢复策略（CE=`pending_design`；Director/QA=`pending_exec`）；纯 metadata、伪 receipt ref、辅助 task、错 factory/task/stage receipt 一律不得 defer drain。否则必须写 `completed_at`、终结并释放 lease。Director/QA 普通修复优先在当前 stage 或 owner task 内完成，不能靠无 receipt 的 `decision_pending` 延迟终态。
- `model_ceiling` 只能由 owner query 的最终请求快照、physical-attempt、Run Ledger settlement、provider health、repair coverage 和 residual 证据共同封存；禁止 caller 自报 attempt 数、预算或通用 JSON receipt 触发 terminal。未满足证据时必须 fail-closed 为非终态。
- 外层 Supervisor 只做项目级调度（启动、读取归因、推进/暂停/告警），不得进入 Run Ledger 成功条件。Bench 仅在 unit/type/architecture gates 全绿后作为稀缺验证；失败先产出唯一 residual/module attribution，再修 owner Cell，禁止无归因反复长跑。

### 0.1) Director deterministic repairs 收敛边界（强制）

确定性修复内核唯一归属 `director.runtime`：

- Canonical implementation: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/`
- Cross-cell public surface: `polaris.cells.director.runtime.public` / `polaris.cells.director.runtime.public.service`
- Legacy strategy host only: `src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/`

RepairEngine canonical final pipeline 固定为：`Typed Diagnostics -> Coverage Report -> RepairPlan -> PatchComposer -> PolicyGate -> Transactional Executor -> Revalidation Evidence -> Authoritative Receipt -> Ledger/LLM Context`。任何新规则、bench 修复或 adapter bridge 都必须沿这条链路闭环，禁止把 diagnostic regex、patch 生成、policy gate、executor、revalidation、receipt 或 ledger/context 投影拆回 `roles.adapters`、Factory、QA、bench harness 或 public wrapper 的私有分支。

`director.runtime/internal/repair_kernel` 是 Cell 私有实现。其他 Cell，尤其是 `roles.adapters`，不得直接 import `polaris.cells.director.runtime.internal.repair_kernel`。`execute_method.py` 若需要 repair catalog、summary 或 planning，只能使用 `director.runtime.public.service`。legacy `tool_results` 投影为 repair_kernel summary 必须使用 `ProjectDirectorRepairKernelSummaryV1` + `project_director_repair_kernel_summary`；`build_director_repair_kernel_summary` 只保留在 runtime public 兼容层和测试中，`roles.adapters` 不得调用。post-execution 语言修复必须通过 `roles.adapters/internal/director/post_execution_repair_bridge.py` 统一入口；step 调度事实源必须来自 `query_director_repair_post_execution_schedule`，bridge 只允许保存 `step_id -> runner` 绑定，且 runner key 集合必须与 runtime schedule 完全一致，禁止在 adapter 里重新定义 phase/priority/depends_on 目录。materialization-quality 修复必须通过 `roles.adapters/internal/director/materialization_quality_repair_bridge.py` 统一入口，并消费 `run_director_materialization_quality_repair_schedule`；bridge 只允许绑定 runtime 声明的 `step_id`，且 runner key 集合必须与 runtime-owned schedule 完全一致，禁止在 adapter 里新增、删除或重排 schedule step。当前 materialization schedule 是九个 runtime-owned step：`materialization.hygiene_scaffold`、`materialization.typescript_scaffold`、`materialization.typescript_compiler`、`materialization.html_entrypoint`、`materialization.node_manifest`、`materialization.rust_compiler`、`materialization.target_runtime`、`materialization.python_import`、`materialization.go_import`。这些 materialization step 的 `source_tool_kind` 默认为 `callback_schedule_label`，不得把它们当作 `RunDirectorRepairCommandV1` 的 executable source_tool。禁止恢复单个 `materialization.quality_repair_host` 大步骤；旧 `_apply_deterministic_materialization_quality_repairs` facade 已硬切删除，禁止恢复、转发或作为测试/bench/Agent 入口。禁止在 `execute_method.py`、Factory、QA 或 bench harness 里直接 import 具体语言 repair 函数。

public repair planning/execution 只能通过通用 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` 和 `plan_director_repair` / `run_director_repair`。禁止新增 `plan_director_<language>_*`、`run_director_<language>_*` 或按规则命名的 public facade；语言/规则分派必须留在 `director.runtime.internal.repair_kernel` 的 dispatcher/registry 后面。

public convergence API 是 `run_director_repair_convergence` + `RunDirectorRepairConvergenceCommandV1`，并由 adapter 注入 verifier callback，callback 必须返回 `DirectorRepairVerifierSnapshotInputV1`。`director.runtime.public` 只负责把 adapter-supplied verifier DTO/callback 投影为 runtime verifier snapshot，不直接执行 verifier command；`roles.adapters`、Factory、QA、bench、public wrappers 均不得 import `director.runtime.internal` 或绕过 public convergence API。`_runtime_bridge.run_runtime_repair_with_director_tools` 只有在调用方提供真实 `convergence_verifier` 且 verifier 产出 command、exit code、residual diagnostics、raw output ref 等 evidence 时，才允许走 convergence path 并投影 authoritative receipt。没有 verifier evidence 时不得伪造 success；receipt 必须保持 non-authoritative，并显式保留 `metadata.requires_revalidation=true` / `authoritative=false` 或等价 public 投影。

当前 runtime executable binding 口径以 `runtime_repair_bindings()` 和 `query_director_repair_strategy_catalog` 为唯一事实源；禁止在 AGENTS/CLAUDE/README 手写 source_tool 固定总数或长列表作为事实源；需要精确数量或列表时查询 runtime，Rust executable 关键不变量为 20 个。Rust module-file topology 规则 E0583 / E0761 已分别通过 `deterministic_rust_missing_module_file_repair` / `deterministic_rust_duplicate_module_file_repair` 成为 runtime executable；Rust missing lib target、lib root facade、struct literal missing field 也已拆成明确 source_tool；后续 Agent 不得把这些规则重新接回 legacy direct-write helper；所有 runtime binding source_tool 必须通过 `RunDirectorRepairCommandV1(source_tool="<runtime binding source_tool>")` 执行，禁止新增语言/规则专用 public facade。

`deterministic_rust_post_repair` 只是 aggregate post-execution callback / legacy schedule label，不是 `runtime_repair_bindings()` 暴露的 `executable_runtime` source_tool，不得传给 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1`。Runtime schedule 的每个 step 都会投影 `source_tool_kind` 与 `executable_runtime_source_tool`；任何 schedule consumer 必须以这两个字段为准，只有 `source_tool_kind="executable_runtime"` 且 `executable_runtime_source_tool=true` 的 source_tool 才能作为 public Plan/Run source_tool。`delete_file` 已作为 repair kernel operation/tool 能力存在并有 receipt/policy 语义；但不能因 `delete_file` operation/tool 存在就恢复 aggregate Rust topology repair。Rust post aggregate 不得作为可执行绑定恢复；新增或迁移余量仍需 coverage、policy 和 revalidation evidence 后才能拆成明确 source_tool。

Targeted gate 同步：最新 targeted gate 摘要为 `842 passed`（2026-06-26 docs/metrics sync 口径引用）；后续调整 binding/gate 文档时必须同步更新该摘要或命令证据。

`PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` 只能接受 `runtime_repair_bindings()` 暴露的 `executable_runtime` source_tool。未知、未注册、`reserved_only` 或仅 `metadata_rule_registered` 的 source_tool 必须 fail-closed，并在 public planning/run result 的一等 `error_code` 返回 `unsupported_repair_source_tool`；不得写 workspace，不得静默 fallback 到 legacy regex/direct-write helper，也不得由 adapter/bench/QA 自行补救执行。

禁止恢复或新增旧架构入口：

- `src/backend/polaris/cells/roles/adapters/internal/director/repair_kernel/**`
- `src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/strategy_catalog.py`
- `roles.adapters` 下自有的 repair policy gate、PatchComposer、receipt contract 或 AGI advisory contract

新增 deterministic repair 必须走 `Diagnostic -> Plan -> Compose -> Policy/Execute -> Receipt -> Revalidate`。Planner/Composer 不得直接写文件；commit 副作用必须通过 Director policy-gated 工具适配器执行，并在 receipt 中记录 before/after hash、operation ids、rule/source_tool。大文件和精确编辑场景必须优先产出 span/context unique text patch，并通过 `edit_file` 精确提交；JSON 必须走 structured merge / canonical serialization；TOML/YAML 规则未具备结构化 merge 能力时必须 reserved fail-closed。`write_file` 只允许用于新文件、结构化整文件序列化、fallback 或 rollback，并必须在 receipt/metadata 中记录 reason、fallback source、before/after hash 与 policy decision。`director.runtime` 只接收 adapter 注入的 writer/editor callable，不得 import 或直接调用 `DirectorToolExecutor`。多轮执行必须通过 repair kernel scheduler 建模 `priority`、`depends_on`、`round_number`、`max_rounds` 与 cycle breaker；`run_director_post_execution_repair_schedule(..., max_rounds=3)` 是 post-execution 收敛入口，adapter 只能绑定 step runner，禁止把收敛循环重新藏进 `execute_method.py` 或新的语言 post-repair 函数。Receipt 必须携带 post-check evidence，至少包含 verifier command、exit code、before/after diagnostics、resolved/residual diagnostic ids、errors_before/errors_after/net_error_reduction，并由 `revalidation_coverage` 汇总 missing/failed evidence。直接 `run_director_repair`/executor 写入成功只能表示 patch 已应用；缺少 revalidation evidence 时 receipt 必须保持 `authoritative=false` 且 `metadata.requires_revalidation=true`，不得冒充闭环权威。public `RepairReceiptV1` 必须投影 `authority_hash` / `projection_hash`，且 revalidation evidence 是 authority hash 材料。复测 evidence 存在但 exit code 非 0 时必须标记为 failed post-check，不能设为 authoritative，也不能继续渲染成 missing evidence，且必须通过 `failed_revalidation_receipt_ids` / `failed_revalidation_source_tools` 定位失败对象，禁止只给失败计数。未来 AGI/Resident 只能作为 non-authoritative advisory：只能输出 suggested_rules、coverage gap、archetype 或 evidence 建议；不得写文件、生成 authoritative plan/receipt、覆盖 policy、给 success verdict、注册规则，且不得成为 Run Ledger、ReceiptStore、ContextOS、Factory/Bench 成功条件的事实源。任何 AGI suggested-rule payload 必须先通过 `validate_director_repair_advisory`；该入口只读、只标准化或拒绝建议，不产出 repair plan 或注册规则；validation summary 也必须显式投影 `agi_execution_authority=false`、`writes_allowed=false`、`registration_allowed=false`、`authoritative_receipts_allowed=false`、`suggested_rules_are_advisory_only=true`。

### 0.2) Repair coverage 先于补规则（强制）

遇到新的 compiler/verifier diagnostic 时，先走 repair coverage，而不是先补 legacy regex。通过 `director.runtime.public.service.query_director_repair_coverage` 或 internal registry 产出 coverage report；`known_rule_matched=false`、metadata-only（`metadata_rule_registered`）、reserved-only（`reserved_only`）都是可审计平台缺口，不是可执行修复。新增语言、新 bench 样例或新 verifier diagnostic 必须先进入 language slot / strategy catalog / coverage report，补齐 rule_id、source_tool、archetype、phase、receipt/verifier evidence 后，才允许新增 runtime executable binding。Coverage report 只读：不得写文件、不得隐式自动注册新 `source_tool`、不得让 AGI suggested rule 直接变成 authoritative rule；禁止为了单个 bench 样例直接扩写 `execute_method.py` 分支或从 bench/QA 直接调用 legacy helper。迁移旧策略时必须先暗跑：通过 `compare_director_repair_shadow_run(comparison_mode="independent_shadow_run")` 对账 legacy tool_results 与新 kernel receipt 的 files/source_tools，matched 后才能切断旧路径；shadow comparison 只读、不得写 workspace。`CompareDirectorRepairShadowRunV1.comparison_mode` 必须显式区分 `independent_shadow_run` 与 `legacy_projection_self_check`；只有 `independent_shadow_run` 且 scope/hash/revalidation/authoritative receipts 全部满足时才允许 `cutover_ready=true`。legacy summary projection 内嵌的 `dark_launch_comparison` 只是 `legacy_projection_self_check`，必须保持 `cutover_ready=false` 和 `independent_shadow_required` blocker，不能作为切断旧路径的证据。Legacy `deterministic_repairs` 目录只是 migration strategy host；`execute_method.py`、Factory、QA、bench、public wrappers 禁止直接调用具体 `_apply_deterministic_*` / `repair_*` 函数。

`query_director_repair_strategy_catalog` 是 deterministic repair 迁移状态的只读模型。每个 item 必须暴露 `implementation_status`；summary 必须区分 `executable_runtime` 与 `legacy_strategy_host` 的 source_tool 数量和列表。后续 Agent 迁规则前必须先看该 catalog，禁止只靠 grep 判断“还剩多少”。
当前 runtime executable binding 口径必须由 `runtime_repair_bindings()` / `query_director_repair_strategy_catalog` 动态派生，禁止手写固定 source_tool 总数；Rust executable 关键不变量为 20 个。Rust module-file topology E0583/E0761 已通过 `deterministic_rust_missing_module_file_repair` / `deterministic_rust_duplicate_module_file_repair` 成为 runtime executable，Rust missing lib target、lib root facade、struct literal missing field 也已拆成明确 source_tool。`deterministic_rust_post_repair` 不是 executable binding，只能作为 aggregate post-execution callback / legacy schedule label 观察；不得恢复 aggregate Rust topology repair。`delete_file` 已是 repair kernel operation/tool 能力，但不能因此恢复 aggregate Rust topology repair；新增/迁移仍必须先走 coverage、policy 和 revalidation evidence。最新 targeted gate 摘要：`842 passed`。

未来更多编程/脚本语言的专项 deterministic repair 由后续 Agent 通过 L1-L12/九十多个项目 bench 证据逐步补齐。开工前必须先查 `query_director_repair_language_slots`，优先复用已有 reserved slot（例如 Vue/Svelte、Scala/Groovy、Elixir/Erlang、Haskell/OCaml/F#、Zig/Nim/Crystal、Perl/PowerShell/Julia、Objective-C/MATLAB/Fortran/Terraform、Dockerfile/Make/Bazel/Starlark、YAML/JSON/TOML/Nix、GraphQL/Proto/Solidity/Vyper 等）；没有槽位时只能在 `director.runtime` registry 中补只读 reserved slot，不能在执行链路里加空分支。slot 的 `implementation_status` 必须按三态理解：`reserved_only` 只表示预留扩展落点，`metadata_rule_registered` 只表示 catalog/coverage 已有规则元数据，只有 `executable_runtime` 才允许通过 `RunDirectorRepairCommandV1` 执行。新增语言规则必须先落 catalog/archetype/coverage/receipt/verifier evidence，再接入 legacy bridge 或 runtime scheduler；禁止为了单个 bench 样例直接扩写 `execute_method.py` 分支。
后续 RepairEngine/bench Agent 的安全扩展顺序固定为：先查 `query_director_repair_language_slots` 选择或新增 reserved slot，再用 `query_director_repair_coverage` 记录 uncovered diagnostic，随后补 catalog/archetype/phase/source_tool 元数据，最后才接入 runtime executable binding、受控 bridge/scheduler、policy receipt 与 revalidation evidence。未到 `executable_runtime` 前只能记录 gap 或输出 advisory；Factory、QA、bench harness 不得代为执行、注册或回退 legacy helper。

Factory/Bench gate 是量具，不做修复。`bench_gates.py` 不得改写 workspace、自动初始化 manifest、删除/重排源码或把测量逻辑伪装成 deterministic repair。

## 1) 真实入口路径
- 桌面入口: `src/electron/main.cjs`
- 后端入口: `src/backend/server.py` -> `src/backend/polaris/delivery/http/app_factory.py` (FastAPI)
- 后端实例入口（推荐）: `python -m polaris.delivery.cli.backend serve ...`
- 前端入口: `src/frontend/src/main.tsx`（Vite 配置: `src/frontend/vite.config.ts`）
- 多实例总控 UI: `/launcher`（例如 `http://127.0.0.1:5173/launcher`）
- 实例管理 API: `/v2/instances`（平台发现/运维视图，不是 PM/CE/Director/QA 事实源）
- PM CLI: `src/backend/polaris/delivery/cli/pm/cli.py`（控制台脚本 `pm`）
- Director CLI (推荐): `src/backend/polaris/delivery/cli/director/cli_thin.py`（控制台脚本 `director`）
- Architect CLI: `src/backend/polaris/cells/architect/design/internal/architect_cli.py`
- Chief Engineer CLI: `src/backend/polaris/cells/chief_engineer/blueprint/internal/chief_engineer_cli.py`

## 2) 维护优先级路径
- 后端新架构目标根: `src/backend/polaris`
- 后端新功能目标分层: `src/backend/polaris/bootstrap`, `src/backend/polaris/delivery`, `src/backend/polaris/application`, `src/backend/polaris/domain`, `src/backend/polaris/kernelone`, `src/backend/polaris/infrastructure`, `src/backend/polaris/cells`
- 后端图谱与治理真相: `src/backend/docs/graph`, `src/backend/docs/governance`, `src/backend/docs/templates`
- 后端 API 与服务: `src/backend/polaris/delivery`
- Loop / 角色内核（优先修改）: `src/backend/polaris/cells/roles`, `src/backend/polaris/kernelone`
- Director Runtime/Accel: `src/backend/polaris/cells/director`
- Director deterministic repair kernel: `src/backend/polaris/cells/director/runtime/internal/repair_kernel`（只允许 cell 内实现使用；跨 Cell 走 public service）
- PM/Director 编排层: `src/backend/polaris/delivery/cli/pm`, `src/backend/polaris/delivery/cli/director`
- 前端主 UI: `src/frontend/src/app`
- 测试: `tests/electron`, `src/backend/polaris/tests`

说明:
- `src/backend/polaris` 是后端 ACGA 2.0 迁移承载根；新的主实现优先进入这里
- 旧根 `src/backend/{app,core,api,scripts}` 已在 ACGA 2.0 迁移中删除并迁入 `src/backend/polaris/{bootstrap,delivery,application,domain,kernelone,infrastructure,cells}`

## 3) 常用命令
```bash
# 全栈开发（Electron + Backend + Frontend）
npm run dev

# 前端 / Electron 单独运行
npm run dev:renderer
npm run dev:electron

# 后端单独运行
# 仅用于 main 开发实例；bench/临时项目实例不得占用 49977。
python src/backend/server.py --host 127.0.0.1 --port 49977

# 后端实例运行（main 开发实例；会注册到 Launcher）
cd src/backend
KERNELONE_CONTEXT_ADMIN_ENABLED=1 python -m polaris.delivery.cli.backend serve \
  --workspace /path/to/workspace \
  --runtime-root /path/to/workspace/runtime \
  --port 49977 \
  --token polaris-local-dev \
  --frontend-port 5173 \
  --register-instance \
  --instance-id main \
  --instance-name "Main Polaris Dev" \
  --kind development
# 单人调试后端热重载时才追加 --reload；多 Agent/bench 观测阶段不要默认开启。

# Web 前端单独运行（绑定当前后端实例）
VITE_POLARIS_BACKEND_URL=http://127.0.0.1:49977 \
VITE_POLARIS_BACKEND_TOKEN=polaris-local-dev \
VITE_POLARIS_INSTANCE_ID=main \
VITE_POLARIS_WORKSPACE=/path/to/workspace \
npm run dev:renderer -- --host 127.0.0.1 --port 5173

# PM CLI (项目管理) - 控制台脚本 pm = polaris.delivery.cli.pm.cli:main
pm --workspace <repo> --run-director --director-iterations 1

# Director CLI (推荐) - 控制台脚本 director = polaris.delivery.cli.director.cli_thin:main
director --workspace <repo> --iterations 1

# Architect CLI (架构设计 - 交互式)
python -m polaris.cells.architect.design.internal.architect_cli --mode interactive --workspace <repo>

# Chief Engineer CLI (技术分析 - 交互式)
python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli --mode interactive --workspace <repo>

# 统一角色对话 API (所有 5 个角色)
# POST /v2/role/{pm|architect|chief_engineer|director|qa}/chat

# V2 API 端点
# PM: /v2/pm/*
# Director: /v2/director/*
# Role Chat: /v2/role/{role}/chat
```

## 4) 验证命令（按改动面最小执行）
```bash
# 前端改动
npm run typecheck
npm run lint
npm run test

# Electron E2E (唯一 E2E 测试)
npm run test:e2e

# Python/后端改动
pytest
pytest src/backend/tests

# 工厂冒烟（可选）
python scripts/run_factory_e2e_smoke.py --workspace .
```

## 5) 强约束
- 所有文本文件读写必须显式使用 UTF-8。
- TypeScript 保持 `strict`，公共接口禁止 `any`。
- 变更 Loop / 角色内核时，优先修改 `src/backend/polaris/cells/roles` 与 `src/backend/polaris/kernelone`。
- 不提交运行时产物: `.polaris/runtime/**`, `playwright-report/**`, `test-results/**`。
- 验证失败不得标记任务完成（fail-closed）。
- 多项目并行观测必须用 Instance Registry + `/launcher` 启动或发现多个单-workspace 实例；不要把单个 backend/UI 临时改造成多 workspace 状态拼接层。
- 从 Launcher 打开的实例工作台必须通过 URL query 或 `VITE_POLARIS_*` 显式绑定 `instance`、`backend`、`token`、`workspace`；前端 API 与 `/v2/ws/runtime` 必须使用该 workspace 绑定，禁止静默回退到默认 backend、默认 workspace 或主仓 runtime。
- 需要被总控观测的 Agent/CLI/内部压力测试启动项必须注册实例；Launcher 只读实例发现状态，不能成为 PM、Chief Engineer、Director、QA、ContextOS、ReceiptStore 或 Run Ledger 的事实源。
- `factory_bench`、L1-L12 和 benchmark harness 只属于内部测试/开发/审计模式；共享后端 bench 注册只能作为“可观测的测试实例”，不得冒充独立生产实例，正式产品/生产环境不得出现 Bench 入口、Bench 文案、Bench 专属 UI/API 或 Bench 事实模型。
- `metadata.backend_binding=shared_backend_workspace_switch` 的 `bench_project` 执行 restart/独立启动时，Supervisor 必须分配新的 backend/frontend 端口并启动独立实例，禁止复用共享 backend 端口。
- 多 Agent 并行跑 `factory_bench` 时 runner 必须显式使用 `--launcher-instance-mode isolated --bench-session-reporting off`，让每个项目的 Factory run 指向自己的 backend；Launcher 可见性来自 Instance Registry 和项目实例自己的 runtime.v2。共享主后端 `/v2/factory/bench/sessions` 只是内部兼容观测桥，只有串行调试时才允许 `--launcher-instance-mode observed --bench-session-reporting shared`，不得用于共享 49977 的并发压测。
- 完整 L1-L12 可运行性验收必须使用 `run_factory_bench.py` 的 `--timeout 5400` 和外层 `timeout --kill-after=30s 6000s`；`540s/600s` 只允许启动/失败路径 smoke，不得作为 runnable 或 `COMPLETED_VERIFIED` 证据。R38 已证明 540s 总预算在三波 Director + QA/safety 保留 310s 后只给 CE 188s，导致确定性的 `provider_stream_timeout:188s`。
- `49977/5173` 只属于 `main` 开发实例。bench、Factory Bench、临时项目或 Agent 私有实例不得手工指定这些端口，不得向主后端 `POST /settings` 切换到 bench workspace；必须通过 Instance Supervisor/Launcher 自动分配非主端口，并打开对应实例 URL。
- Launcher 实时状态只走 runtime.v2 WebSocket `status.instances`；禁止用 HTTP polling、文件轮询或 Bench session 替代正式实时链路。
- 当前承载 Launcher API 的实例不能通过自己的 `/v2/instances/{id}/stop|restart|delete` 自我停止、自我重启或删除自身记录；这类操作应返回 fail-closed，前端也必须禁用当前控制实例的危险操作。清理 stale bench 只能作用于 stopped、backend dead、`metadata.internal_test_only=true` 的内部测试实例。
- Run Ledger 投影必须区分 `missing_required_modalities` 与 `failed_required_modalities`：前者是控制面/工具链没有记录证据，后者是证据存在但命令、browser smoke、用户脚本或其它 verifier 失败。不要把 failed evidence 写成 missing evidence；内部 bench 只能消费这个平台级语义，不能定义自己的成功/失败事实源。
- LLM 事件里的 `context_snapshot_ref` 必须是同 workspace 下 `/v2/context/{hash}` 和 `/v2/context/{hash}/final-request` 都可读取的 24 位 hex key。ContextOS 读取候选链必须包含 active runtime root、Instance Registry 同 workspace 的 `runtime_root`、默认 KernelOne system cache；404 要返回 `context_hash`、`workspace`、`searched_paths`，前端不能把跨 workspace hash 送进完整上下文 modal。
- `event.bench` 是内部测试态全局事件流；只有总控/主开发页在显式 `globalObserver` 模式下可以订阅。实例工作台、PM/CE/Director/QA/ContextOS 项目页默认只能消费调用方传入的 scoped bench 数据，`enabled` 本身不得触发 `useFactoryBench({autoSelect:"newest"})`。

## 6) 常用环境变量
- `KERNELONE_WORKSPACE`
- `KERNELONE_RENDERER_PORT`
- `KERNELONE_BACKEND_PORT`
- `KERNELONE_PM_PROVIDER`, `KERNELONE_PM_MODEL`

## 7) 核心系统地图（防重复造轮子）

以下模块已实现，禁止重复创建：

### 7.1) LLM 工具系统
**唯一实现**: `src/backend/polaris/kernelone/llm/toolkit/`

```python
# ✅ 正确用法
from polaris.kernelone.llm.toolkit import (
    AgentAccelToolExecutor,      # 统一工具执行器
    parse_tool_calls,            # 工具调用解析
)

# 获取角色工具集成（注册表 ROLE_TOOL_INTEGRATIONS 现位于 tool_runtime cell）
from polaris.cells.llm.tool_runtime.internal.role_integrations import ROLE_TOOL_INTEGRATIONS

integration = ROLE_TOOL_INTEGRATIONS["pm"](workspace=".")
prompt = integration.get_system_prompt()
```

**禁止行为**:
- ✗ 在 `polaris/cells/llm/` 下新建 `*ToolIntegration` 类
- ✗ 自定义 `TOOL_CALL:...ARGS:...` 格式
- ✗ 直接调用底层 `tools.py`

**相关文件**:
- `polaris/kernelone/llm/toolkit/definitions.py` - 工具定义（单一事实来源）
- `polaris/kernelone/llm/toolkit/executor/` - 工具执行（目录）
- `polaris/cells/llm/tool_runtime/internal/role_integrations.py` - 5个角色的工具集成
- `polaris/kernelone/llm/toolkit/parsers/` - 工具调用解析（目录）

### 7.2) 角色对话系统
**唯一实现**: `src/backend/polaris/cells/llm/dialogue/internal/role_dialogue.py`

```python
# ✅ 正确用法
from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response

result = await generate_role_response(
    workspace=workspace,
    settings=settings,
    role="pm",  # 或 architect, chief_engineer, director, qa
    message=message,
)
```

**角色提示词注册表**: `ROLE_PROMPT_TEMPLATES`
- `pm` - 尚书令 (项目管理)
- `architect` - 中书令 (架构设计)
- `chief_engineer` - 工部尚书 (技术分析)
- `director` - 工部侍郎 (代码执行)
- `qa` - 门下侍中 (质量审查)
- `scout` - 探子 (只读代码探索，sub-agent，即将由 PM/Director 调用)

**禁止行为**:
- ✗ 在 `polaris/cells/llm/dialogue/` 下新建独立角色对话文件（已统一到 `role_dialogue.py`）
- ✗ 在角色 CLI/internal 模块下内嵌角色提示词
- ✗ 创建新的 `generate_xxx_response()` 函数

### 7.3) Provider 系统
- ✗ 直接操作 `base_provider.provider_registry`
- ✗ 绕过 `ProviderManager` 创建 Provider 实例

### 7.4) 任务管理系统
**唯一实现**: `src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py`

```python
# ✅ 正确用法
from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoard

board = TaskBoard(workspace=".")
board.create(subject="实现登录功能", priority="high")
```

### 7.5) 已删除模块（历史记录）

| 模块 | 替代方案 | 状态 |
|------|----------|------|
| `pm_dialogue.py` | `polaris.cells.llm.dialogue.internal.role_dialogue.generate_role_response(role="pm", ...)` | 已删除 |
| `pm_tools.py` | `polaris.kernelone.llm.toolkit.AgentAccelToolExecutor` | 已删除 |
| `api/routers/pm.py` | `polaris/delivery/http/routers/pm_chat.py` + `pm_management.py`（`/v2/pm`） | 已删除 |
| `workflow_nodes_compat.py` | `polaris/cells/roles/adapters/internal/workflow_adapter.py` | 已删除 |

### 7.6) 新增能力检查清单

在实现新功能前，检查：

1. **工具能力?** → 先看 `polaris/kernelone/llm/toolkit/` 是否已存在
2. **角色对话?** → 先看 `role_dialogue.ROLE_PROMPT_TEMPLATES` 是否已有
3. **Provider?** → 先看 `providers/provider_registry.py` 是否已支持
4. **任务管理?** → 先看 `task_board.py` 是否满足需求

如果不确定，查看对应模块的 `__init__.py` 中的 **"防重复造轮子提示"** 区域。

## 8) 绝对禁止：在 Polaris 项目中添加业务代码

**铁律**：Polaris 是元工具平台，禁止在主仓代码中添加任何目标项目/业务相关代码。

### 8.1) 禁止行为
- ❌ 在 `worker_executor.py` 或任何 Polaris 源码中为特定项目添加代码模板（如 Express、Django、React 等）
- ❌ 在 Polaris 代码库中硬编码目标项目的配置、路径、或文件名
- ❌ 为解决特定项目问题而修改 Polaris 核心逻辑（应修复通用逻辑）

## 9) Factory Bench 与 Director 上下文架构约束（2026-06-25 沉淀）

### 9.1) 修复层级铁律：修系统，不修量具

**禁止在 bench 测量层做 repair**。`bench_gates.py` 是审计/量具，只负责检测和归因。
所有代码修复必须放在 Director 执行链路中，并通过 `director.runtime.public.service`
与 `roles.adapters` 的工具适配器落到 Director policy-gated 工具；确保真实项目（非 bench）
也能受益。

```
✅ 正确入口: director.runtime public schedule/RunDirectorRepairCommandV1
✅ 迁移期实现: deterministic_repairs/go_repairs.py 只能作为 legacy strategy host，被 bridge 调用
❌ 错误位置: bench_gates.py                       → 仅 bench 测量时调用
```

### 9.2) Director 上下文强制审计清单

每次 bench 失败或代码质量问题，**必须先审计 Director 最终 LLM 请求**再做下游修复：

1. **context_snapshot_ref** → 读取完整 provider_request
2. **context_window_utilization** → < 10% 是红旗（说明关键信息未注入）
3. **CE Blueprint 注入** → Director 必须收到 CE 技术蓝图（target_files, acceptance_criteria, execution_checklist）
4. **Task 描述完整性** → 不得截断
5. **role identity** → system prompt 中 Director 身份是否正确
6. **tools** → 是否包含 write_file, read_file, execute_command 等必要工具
7. **tool_choice** → 是否正确（auto vs forced）

审计位置：通过 `resolve_storage_roots(workspace).runtime_root / "contexts" / <shard> / <hash>` 读取当前 canonical ContextOS 快照；开发环境通常位于 `~/.cache/kernelone/.polaris/projects/<workspace-key>/runtime/contexts/<shard>/<hash>`。旧 `~/.cache/polaris/...` 路径不得作为新链路依据。`context_snapshot_ref` 必须是 `/v2/context/{hash}` 可读取的 24 位 hex 快照 key；不得把 `request_hash`、`prompt_hash`、`call_id`、`turn_id`、文件路径或旧事件字符串当成完整上下文快照引用。

### 9.3) CE Blueprint → Director 注入链路

```
CE 生成蓝图 → BlueprintPersistence 存储 → get_blueprint_status() 查询
→ ContextGateway._get_blueprint_overview() → role_signals.BlueprintOverviewSignal
→ Director system message
```

关键约束：
- `BlueprintOverviewSignal.applies_to()` 必须包含 `director` 角色（不仅 chief_engineer）
- `_latest_blueprint_for_task()` 必须支持 task_id 标准化匹配（`TASK-1` ↔ `1`）
- `BlueprintPersistence` 查找路径必须与 CE 写入路径一致

### 9.4) 跨文件一致性三层防御

| 层级 | 职责 | 位置 | 杠杆 |
|------|------|------|------|
| **预防** | CE 蓝图注入 Director 上下文 | `role_signals.py` | 最高 |
| **检测** | 质量门发现 coherence 错误 | `quality_gate.py` | 中等 |
| **修复** | 确定性 repair 自动修正 | `director.runtime` repair kernel + bridge-bound legacy strategy host | 最低 |

**禁止只做修复层** — 那是打地鼠。必须先确认预防层是否工作。

### 9.5) Task ID 映射规范

PM TaskBoard 和 CE Blueprint 使用不同 task_id 格式时，所有查询层必须做标准化：
- PM 用数字 ID：`1, 2, 3, 4`
- CE 用前缀 ID：`TASK-1, TASK-2`
- Director 用 orchestration ID：`task-0-director, task-1-director`

`_normalize_task_token()` 函数统一去前缀比较。所有跨角色的 task_id 查找必须使用此函数。

---

## 🛠️ 核心开发规范与质量验收标准 (Core Quality Gates)

作为资深 Python 研发专家，你产出的任何代码**必须（MUST）**在提交或宣告任务完成前，通过以下三道质量网关。绝对不允许提交未经这三个工具实际运行并验证通过的代码。

### 1. 代码规范与格式化 (Ruff)
* **要求**：所有 Python 代码必须严格符合 PEP 8 规范，保持高度整洁和一致性。
* **强制动作**：在编写或修改代码后，必须立即运行 `ruff check . --fix` 和 `ruff format .`。
* **验收标准**：Ruff 检查过程必须静默，不能有任何残留的 Error、Warning 甚至未使用的 Import。

### 2. 静态类型安全 (Mypy)
* **要求**：所有函数签名、类的方法和关键变量**必须**包含完整的 Python 类型提示（Type Hints）。
* **强制动作**：执行 `mypy <你的代码文件>.py` 进行静态类型推导分析。
* **验收标准**：Mypy 必须输出 "Success: no issues found"。严禁使用 `# type: ignore` 来掩盖真实的类型冲突（除非在与无类型提示的老旧第三方库交互且极其必要的情况下）。

### 3. 自动化测试与逻辑验证 (Pytest)
* **要求**：任何业务逻辑代码都必须配有对应的单元测试用例（文件需以 `test_` 开头）。
* **强制动作**：执行 `pytest <你的测试文件>.py -v`。
* **验收标准**：所有测试用例必须 100% 绿色通过（PASS）。

### 🔄 强制自我修正协议 (Self-Correction Protocol)
如果在上述任何一个步骤中，工具抛出异常或返回非 0 状态码，你必须进入自修复循环：
1. **禁止逃逸**：严禁直接输出带有 Bug 的最终代码，或对人类说“请你这样修改...”。你必须亲自解决。
2. **分析报错**：仔细阅读并提取终端输出的 Traceback 或具体的 Error Message。
3. **闭环修复**：根据报错信息反思根本原因，修改你的代码，并**重新运行**对应的检查工具。
4. **循环熔断**：重复此过程，直到三个工具全部验收通过。如果在同一个问题上连续失败 5 次，请停止重试，向人类求助，并提供精炼后的报错上下文和你之前的尝试思路。


## 外部并行工程 Agent 调用规范

Codex、Claude Code 等主 Agent 可以把独立工程任务派发给外部 Sub-Agent。默认协议是 **Claude CLI JSON Sub-Agent**；OpenCode 只保留为兼容审计路径。所有外部 Agent 只能作为主 Agent 的工程实施/审计工具使用，不属于 Polaris 平台自身。禁止在 Polaris 产品代码、Factory Bench、Run Ledger、ContextOS、ReceiptStore、UI、runtime event 或质量门禁中引入对 Claude/OpenCode 外部 Agent 的运行时依赖、调度逻辑、状态投影或成功条件。

### 调用方式

单个任务默认使用 Claude CLI JSON 模式：

```bash
claude -p "<完整任务提示词>" \
  --dangerously-skip-permissions \
  --output-format json \
  --json-schema '<JSON_SCHEMA>'
```

多个互不重叠的任务可以并行执行，最多 3 个 Sub-Agent。Sub-Agent 必须显式声明 `mode=audit` 或 `mode=implementation`：审计任务只读；实施任务可以直接写代码，但共享主仓并发写入只允许在文件/目录/职责集合完全互斥时使用，否则必须使用独立 worktree/sandbox，或降级为串行。

默认派工策略是 **implementation-first for bounded work**：当账本项已经有明确根因、授权文件范围、验收命令和无交叉依赖时，主 Agent 必须优先派 `mode=implementation` Sub-Agent 直接修改代码/测试/文档，而不是只让外部 Agent 审计。`mode=audit` 只适用于边界尚未确定、需要先确认事实源、跨 Cell 共享接口仍未定、或可能与其他正在运行的 Agent 冲突的高风险阶段。审计完成后如果形成了互斥范围，下一轮必须转为 `mode=implementation` 或由主 Agent 亲自收敛，禁止长期停留在“只审计不落地”。

### Principal Architect 生产级执行标准

所有 `mode=implementation` Sub-Agent 都必须按 Principal Architect 标准执行，而不是按临时补丁工模式执行：

强制提示词前缀必须包含：请以首席 Python 架构师标准完成任务：先做职责划分与架构设计，再给出生产级实现。要求高内聚、低耦合、类型清晰、边界明确、异常可追踪、结构可扩展、代码可测试、日志与配置工程化，并解释设计取舍、风险点和后续演进方向。

每个 `mode=implementation` Sub-Agent 还必须保持工程素质底线：先定义问题边界、职责分层和模块关系，再落地代码；保持高内聚、低耦合、单一职责和接口清晰；核心逻辑必须与 I/O、存储、第三方依赖和框架细节隔离；优先设计可扩展、可替换、可测试的结构；避免过度设计但保留合理扩展点。Python 代码必须严格遵循 PEP 8、语义命名、完整公共类型注解、必要 docstring、低嵌套和单一职责；禁止重复代码、魔法数字、全局可变状态和炫技式写法。工程实现必须使用 `logging` 而非 `print` 作为正式输出手段，配置与代码分离，资源生命周期明确，错误处理具体、可观测、可排查，并考虑幂等性、并发安全、输入校验、异常输入、空值处理和失败恢复。

Sub-Agent 报告的 `execution_summary` 或 `architecture_blueprint` 必须按以下顺序覆盖：需求理解与设计假设；架构设计与模块划分；核心接口说明；完整代码实现摘要；关键设计决策与取舍；风险、边界条件与性能分析；测试策略与后续演进建议。

1. 修改前必须先形成架构蓝图，并在 JSON 报告的 `architecture_blueprint` 中说明系统拓扑、模块职责、核心数据流、状态转移和关键技术取舍。
2. 实现必须是完整生产级交付，禁止占位、伪代码、演示分支、空壳符号、`TODO`、`NotImplemented`、无意义 `pass` 或“后续补齐”。
3. 公共接口必须具备清晰类型注解、契约边界和错误语义；Python 代码必须遵循现代 PEP 8、Ruff/Black 约束，优先通过 `mypy --strict` 或项目等价严格门禁。
4. 核心逻辑必须与 I/O、配置、存储、网络、框架和 CLI/HTTP 适配层解耦，保持高内聚、低耦合、单一职责、可替换和可测试。
5. 错误处理必须捕获具体异常并保留可定位上下文；禁止裸 `except`、吞异常、静默 fallback、硬编码成功或把失败改写为通过。
6. 必须显式考虑空值、非法输入、重复调用、幂等性、并发安全、资源释放、跨平台路径、权限边界和回滚/恢复路径。
7. 报告必须包含 `complexity_analysis`，说明关键逻辑时间复杂度、空间复杂度、潜在性能瓶颈和未来优化方向。
8. 测试必须覆盖 Happy Path、Edge Cases、Exceptions 和 Regression；不得用 mock/fake 替代任务要求验证的真实平台路径。
9. 报告必须包含 `self_check`，至少声明：无占位实现、无越界修改、类型/格式/测试门禁结果、剩余风险和是否需要主 Agent 合并复核。
10. 若任务边界不足以安全写代码，Sub-Agent 必须返回 `status=blocked` 并说明缺口；不得猜测授权范围或扩大 scope。

```bash
claude -p "<Agent 01 完整提示词>" --dangerously-skip-permissions --output-format json --json-schema '<JSON_SCHEMA>' > /tmp/polaris-subagent-<batch>-01.json &
claude -p "<Agent 02 完整提示词>" --dangerously-skip-permissions --output-format json --json-schema '<JSON_SCHEMA>' > /tmp/polaris-subagent-<batch>-02.json &
claude -p "<Agent 03 完整提示词>" --dangerously-skip-permissions --output-format json --json-schema '<JSON_SCHEMA>' > /tmp/polaris-subagent-<batch>-03.json &
wait
```

禁止把 `--bg` 作为默认派工协议：它与 `-p` 的 JSON 闭环语义不同，且容易留下非结构化日志。`claude agents --json` 只用于查看 Claude 会话状态，不是 Polaris 外部子任务报告。

OpenCode 兼容路径只允许在 Claude CLI 不可用或用户显式要求时使用，并且必须落盘同等 JSON 报告。OpenCode 结论不得成为 Polaris 事实源。

### 输出 JSON Schema 基线

每个 Sub-Agent 必须按 schema 输出机器可读 JSON，并同时由 shell 重定向落盘到 `/tmp/polaris-subagent-<batch>-<id>.json`。推荐最小 schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "task_id",
    "mode",
    "status",
    "summary",
    "scope",
    "files_read",
    "files_modified",
    "commands_run",
    "findings",
    "risks",
    "architecture_blueprint",
    "complexity_analysis",
    "testing_evidence",
    "self_check",
    "next_action"
  ],
  "properties": {
    "task_id": {"type": "string"},
    "mode": {"type": "string", "enum": ["audit", "implementation"]},
    "status": {"type": "string", "enum": ["success", "blocked", "failed"]},
    "summary": {"type": "string"},
    "scope": {"type": "array", "items": {"type": "string"}},
    "files_read": {"type": "array", "items": {"type": "string"}},
    "files_modified": {"type": "array", "items": {"type": "string"}},
    "commands_run": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["command", "exit_code", "purpose"],
        "properties": {
          "command": {"type": "string"},
          "exit_code": {"type": "integer"},
          "purpose": {"type": "string"}
        }
      }
    },
    "findings": {"type": "array", "items": {"type": "string"}},
    "risks": {"type": "array", "items": {"type": "string"}},
    "architecture_blueprint": {"type": "string"},
    "complexity_analysis": {"type": "string"},
    "testing_evidence": {"type": "array", "items": {"type": "string"}},
    "self_check": {"type": "array", "items": {"type": "string"}},
    "next_action": {"type": "string"}
  }
}
```

Claude CLI 的 `--output-format json` stdout 是 Claude 执行 envelope，不一定直接等于上面的 schema 根对象。主 Agent 回收时必须按顺序解析：

1. 先读取顶层 JSON。
2. 若顶层存在 `structured_output` 且非空，优先把它作为 Sub-Agent 报告。
3. 否则若顶层存在字符串字段 `result`，必须再次 `json.loads(result)` 得到 Sub-Agent 报告。
4. 只有解析出的内层报告匹配 schema，才算该 Sub-Agent 有效完成。
5. 若进程超时、exit code 非 0、顶层 `is_error=true`、`result` 非 JSON 或 schema 校验失败，必须把该 Sub-Agent 标记为 `blocked | failed`，禁止把外层 envelope 当成成功报告。

### 主 Agent 职责

调用外部 Sub-Agent 前，主 Agent 必须先：

1. 阅读仓库中的 `AGENTS.md` 及相关架构文档。
2. 检查 `git status`、`git diff`、失败测试和用户反馈。
3. 使用仓库提供的代码图谱、符号索引或 MCP 工具审计相关代码。
4. 将问题拆分成多个互不重叠、可独立完成的任务包。
5. 为每个任务包明确目标、代码范围、禁止事项和验收命令。
6. 为每个任务包写明 JSON schema、报告落盘路径和允许修改边界。
7. 若任务源自角色工具调用失败，主 Agent 可派发只读审计最终 LLM 上下文、工具调用归一化路径、ToolSpec/arg_aliases、runtime event、LLM 调用日志与 ContextOS 证据；若事件中 `messages`/`content` 被 redacted，必须同时提供 `context_snapshot_ref` 对应的完整上下文快照文件；审计任务默认只读，除非已经拆出互不重叠的明确修复范围。
8. Factory Bench 与 Polaris 运行时不得要求、生成或消费 `claude_subagent_audit`、`opencode_audit` 或类似外部审计字段作为机器可读平台字段；角色工具失败归因必须依赖 Polaris 自身的 provider request、runtime event、ContextOS、ReceiptStore、Run Ledger、命令门禁和日志证据。

适合并行的任务示例：

- 修复一个独立的配置传递问题。
- 修复一个独立的超时判定问题。
- 审计一个多实例调度链路。
- 修复一个提供商集成的静默降级问题。
- 为一个已经确认的缺陷补充生产修复和回归测试。

以下情况不得并行：

- 多个 Agent 需要修改同一个文件。
- 多个任务依赖同一个公共接口变更。
- 一个任务的实现依赖另一个任务的结论。
- 任务边界尚未明确。

此时应改为串行执行。

### Agent 提示词要求

每个 `claude -p` 必须获得完整、自包含的提示词，不能依赖当前对话中的隐含上下文。

提示词至少应包含：

- Agent 编号和名称。
- 独立任务目标。
- 必须阅读的规范文件。
- 必须使用的代码审计工具。
- 允许修改的代码范围。
- 禁止修改的范围。
- 强调充分利用codegraph MCP
- 不可违反的架构约束。
- 工业级工程标准：UTF-8、完整实现、类型注解、异常边界、测试矩阵、复杂度说明和自检项。
- 必须运行的验证命令。
- JSON schema 和报告落盘路径。

### Sub-Agent 通用规则

每个外部 Sub-Agent 必须遵守：

1. 修改前阅读根目录及相关子目录中的 `AGENTS.md`。
2. 修改前检查 `git status` 和现有 `git diff`。
3. 保留用户已有修改，不得覆盖、回退或清理无关改动。
4. 先使用指定的代码图谱、符号索引或 MCP 工具审计代码路径，禁止先盲目搜索和修改。
5. 只能修改任务明确授权的范围。
6. 禁止顺手重构、全仓格式化或修改无关代码。
7. 必须修复根因，禁止表层绕过。
8. 禁止硬编码成功、吞掉异常、静默 fallback 或禁用检查。
9. 禁止仅修改测试来制造通过结果。
10. 禁止用 mock 或 fake 替代任务要求验证的真实执行路径。
11. 禁止修改生成物或下游项目来掩盖源代码缺陷。
12. 修改后必须运行任务中指定的质量门禁。
13. 未实际执行的命令不得报告为通过。
14. 最终必须输出机器可读的 JSON 报告。
15. 所有文本文件读写必须显式使用 `UTF-8`（包括日志/JSON/Markdown/代码文件）。
16. `mode=implementation` 的 Sub-Agent 必须交付生产级完整实现，不得提交占位、伪代码、演示代码、未实现分支、空壳类/函数或“后续补齐”类文本。
17. Python 代码必须遵循现代 PEP 8、Ruff/Black 约束和清晰命名；公共函数、类、dataclass、TypedDict、协议和返回对象必须有明确类型注解与边界说明。
18. 复杂核心逻辑必须与 I/O、配置、存储、网络、框架细节解耦；不得把一次性胶水逻辑塞进跨 Cell 公共路径。
19. 异常处理必须捕获具体异常并保留可定位错误信息；禁止裸异常捕获、吞异常、用 `pass` 掩盖失败、或把失败改写成成功。
20. 修改必须考虑空值、非法输入、重复调用、幂等性、并发安全、资源释放和跨平台路径边界；发现无法覆盖的边界必须在 JSON `risks` 中明确说明。
21. 核心算法或扫描逻辑必须在报告中说明时间复杂度、空间复杂度、潜在性能瓶颈和后续优化方向。
22. 测试必须覆盖 Happy Path、Edge Cases、Exceptions 和 Regression；可以用 mock 隔离外部依赖，但不得 mock 掉任务要求验证的真实平台路径。
23. `mypy --strict` 或项目等价严格类型门禁能跑时必须运行；若仓库当前 strict 不可用，至少运行任务指定 mypy/pyright 门禁，并在报告中说明 strict 阻塞原因。
24. 输出报告必须包含自检结论：无占位实现、无越界文件、门禁命令和退出码、剩余风险、以及是否需要主 Agent 复核合并。

### 标准提示词模板

```text
你是 <项目名称> 工程修复 Agent <编号>/<名称>。

硬性要求：
1. 必须先阅读 AGENTS.md 以及以下相关规范：
   - <相关 AGENTS.md>
   - <架构文档>
2. 必须先使用 <代码图谱或 MCP 工具> 审计相关代码路径，禁止先盲改。
3. 只能修改本任务明确授权的代码范围。
4. 必须保留现有无关修改，禁止覆盖或回退用户工作。
5. 修复必须针对根因，禁止表层绕过、硬编码成功、静默 fallback 或只改测试。
6. 不得违反任务列出的架构约束。
7. 修改后必须运行全部验收命令。
8. 最终必须按调用方提供的 JSON schema 输出执行报告，并由调用方落盘到 /tmp/polaris-subagent-<batch>-<id>.json。
9. 充分使用codegraph。
10. 所有文本读写必须显式使用 UTF-8。
11. 动代码前必须先形成架构蓝图，并在 JSON `architecture_blueprint` 中记录系统拓扑、模块职责、数据流、状态转移和技术取舍。
12. 必须交付生产级完整实现：禁止占位、伪代码、演示代码、空壳符号、未实现分支或“后续补齐”。
13. Python 代码必须具备清晰类型注解、具体异常处理、可定位错误信息和必要的 Google Style docstring；禁止裸异常捕获、吞异常、静默 fallback。
14. 核心逻辑必须与 I/O、配置、存储、网络、框架细节解耦；优先高内聚、低耦合、单一职责。
15. 必须覆盖 Happy Path、Edge Cases、Exceptions、Regression；不得用 mock/fake 替代任务要求验证的真实平台路径。
16. 报告中必须说明关键逻辑的时间复杂度、空间复杂度、性能瓶颈、剩余风险和自检结果。

任务目标：
<这个 Agent 独立负责的缺口>

预期结果：
<修复后应当观察到的行为>

允许修改范围：
- <文件、目录或符号>

禁止修改范围：
- <文件、目录、生成物或下游项目>

架构约束：
- <必须保留的架构约束>
- <禁止新增的实现方式>

必须完成：
1. <审计或实现要求>
2. <测试要求>
3. <兼容性要求>

必须验证：
- <lint 命令>
- <format check 命令>
- <type check 或 build 命令>
- <相关测试命令>

最终输出 JSON（必须匹配调用方 --json-schema）：
{
  "task_id": "<batch>/<编号>",
  "mode": "audit | implementation",
  "status": "success | blocked | failed",
  "summary": "...",
  "scope": [],
  "files_read": [],
  "files_modified": [],
  "commands_run": [
    {
      "command": "...",
      "exit_code": 0,
      "purpose": "..."
    }
  ],
  "findings": [],
  "risks": [],
  "architecture_blueprint": "...",
  "complexity_analysis": "...",
  "testing_evidence": [],
  "self_check": [],
  "next_action": "..."
}
```

### 结果回收

所有 Sub-Agent 完成后，主 Agent 不得直接相信其报告，必须读取 `/tmp/polaris-subagent-*.json` 并重新审计：

```bash
rtk git status
rtk git diff
```

并检查：

- 是否越过任务范围。
- 是否修改了无关文件。
- 是否覆盖了已有修改。
- 是否只改测试而未修复生产代码。
- 是否加入硬编码成功、异常吞噬或静默降级。
- 是否用 mock 替代真实执行路径。
- 是否违反架构约束。
- 报告中的测试和质量门禁是否真的执行。
- 多个 Agent 的修改合并后是否仍然通过验证。

主 Agent 对最终代码、测试结果和最终回复负责。

### 核心原则

外部 Sub-Agent 并行派工必须满足：

- 任务必须窄。
- 修改范围必须互不重叠。
- 证据必须明确。
- 验收命令必须可执行。
- 最终报告必须机器可读。
- 所有结果必须由主 Agent 独立复核。
