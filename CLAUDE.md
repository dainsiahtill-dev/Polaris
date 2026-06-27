# CLAUDE.md

本文件用于指导在本仓库工作的 AI 编码代理。仅保留可执行、可验证的技术约束。

**必用MCP和Skill**: 充分利用codegraph MCP和superpowers，必要时需要使用Playwright来真实跑测试和审计。

## 0) 后端权威入口（2026-03-22）
- 对于任何 `src/backend` 任务，必须先读 `src/backend/AGENTS.md`。
- 统一架构执行标准入口：`src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`。
- 后端强制规则：`Cell` 开发先复用已有 Cell 公开能力；所有新开发必须基于 `KernelOne` 底座能力与契约链路。
- 若本文件与 `src/backend/AGENTS.md` 或 `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` 存在冲突，以后两者为准。

### 0.1) Director deterministic repairs 收敛边界（强制）

确定性修复内核唯一归属 `director.runtime`：

- Canonical implementation: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/`
- Cross-cell public surface: `polaris.cells.director.runtime.public` / `polaris.cells.director.runtime.public.service`
- Legacy strategy host only: `src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/`

RepairEngine canonical final pipeline 固定为：`Typed Diagnostics -> Coverage Report -> RepairPlan -> PatchComposer -> PolicyGate -> Transactional Executor -> Revalidation Evidence -> Authoritative Receipt -> Ledger/LLM Context`。任何新规则、bench 修复或 adapter bridge 都必须沿这条链路闭环，禁止把 diagnostic regex、patch 生成、policy gate、executor、revalidation、receipt 或 ledger/context 投影拆回 `roles.adapters`、Factory、QA、bench harness 或 public wrapper 的私有分支。

`director.runtime/internal/repair_kernel` 是 Cell 私有实现。其他 Cell，尤其是 `roles.adapters`，不得直接 import `polaris.cells.director.runtime.internal.repair_kernel`。`execute_method.py` 若需要 repair catalog、summary 或 planning，只能使用 `director.runtime.public.service`。legacy `tool_results` 投影为 repair_kernel summary 必须使用 `ProjectDirectorRepairKernelSummaryV1` + `project_director_repair_kernel_summary`；`build_director_repair_kernel_summary` 只保留在 runtime public 兼容层和测试中，`roles.adapters` 不得调用。post-execution 语言修复必须通过 `roles.adapters/internal/director/post_execution_repair_bridge.py` 统一入口；step 调度事实源必须来自 `query_director_repair_post_execution_schedule`，bridge 只允许保存 `step_id -> runner` 绑定，且 runner key 集合必须与 runtime schedule 完全一致，禁止在 adapter 里重新定义 phase/priority/depends_on 目录。materialization-quality 修复必须通过 `roles.adapters/internal/director/materialization_quality_repair_bridge.py` 统一入口，并消费 `run_director_materialization_quality_repair_schedule`；bridge 只允许绑定 runtime 声明的 `step_id`，且 runner key 集合必须与 runtime-owned schedule 完全一致，禁止在 adapter 里新增、删除或重排 schedule step。当前 materialization schedule 是八个 runtime-owned step：`materialization.hygiene_scaffold`、`materialization.typescript_scaffold`、`materialization.typescript_compiler`、`materialization.node_manifest`、`materialization.rust_compiler`、`materialization.target_runtime`、`materialization.python_import`、`materialization.go_import`。禁止恢复单个 `materialization.quality_repair_host` 大步骤；旧 `_apply_deterministic_materialization_quality_repairs` facade 已硬切删除，禁止恢复、转发或作为测试/bench/Agent 入口。禁止在 `execute_method.py`、Factory、QA 或 bench harness 里直接 import 具体语言 repair 函数。

public repair planning/execution 只能通过通用 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` 和 `plan_director_repair` / `run_director_repair`。禁止新增 `plan_director_<language>_*`、`run_director_<language>_*` 或按规则命名的 public facade；语言/规则分派必须留在 `director.runtime.internal.repair_kernel` 的 dispatcher/registry 后面。

public convergence API 是 `run_director_repair_convergence` + `RunDirectorRepairConvergenceCommandV1`，并由 adapter 注入 verifier callback，callback 必须返回 `DirectorRepairVerifierSnapshotInputV1`。`director.runtime.public` 只负责把 adapter-supplied verifier DTO/callback 投影为 runtime verifier snapshot，不直接执行 verifier command；`roles.adapters`、Factory、QA、bench、public wrappers 均不得 import `director.runtime.internal` 或绕过 public convergence API。`_runtime_bridge.run_runtime_repair_with_director_tools` 只有在调用方提供真实 `convergence_verifier` 且 verifier 产出 command、exit code、residual diagnostics、raw output ref 等 evidence 时，才允许走 convergence path 并投影 authoritative receipt。没有 verifier evidence 时不得伪造 success；receipt 必须保持 non-authoritative，并显式保留 `metadata.requires_revalidation=true` / `authoritative=false` 或等价 public 投影。

当前 runtime executable binding 口径为 31 个 `source_tool`，其中 Rust 18 个；当前 executable 列表为 `deterministic_cpp_include_path_repair`、`deterministic_cpp_missing_private_members_repair`、`deterministic_cpp_placeholder_declaration_repair`、`deterministic_cpp_standard_include_repair`、`deterministic_cpp_struct_getter_field_access_repair`、`deterministic_go_bare_import_string_repair`、`deterministic_java_accessor_alias_repair`、`deterministic_java_test_dependency_repair`、`deterministic_patch_residue_cleanup`、`deterministic_rust_crate_import_rewrite_repair`、`deterministic_rust_dependency_repair`、`deterministic_rust_derive_repair`、`deterministic_rust_duplicate_module_file_repair`、`deterministic_rust_field_rename_suggestion_repair`、`deterministic_rust_incompatible_copy_derive_repair`、`deterministic_rust_lib_root_facade_repair`、`deterministic_rust_line_suggestion_repair`、`deterministic_rust_method_self_signature_repair`、`deterministic_rust_missing_binary_entrypoint_repair`、`deterministic_rust_missing_lib_target_repair`、`deterministic_rust_missing_module_file_repair`、`deterministic_rust_serde_derive_repair`、`deterministic_rust_struct_literal_missing_field_repair`、`deterministic_rust_trait_import_repair`、`deterministic_rust_unresolved_pub_use_repair`、`deterministic_rust_unused_import_repair`、`deterministic_rust_wrong_crate_path_repair`、`deterministic_typescript_duplicate_object_property_repair`、`deterministic_typescript_enum_member_separator_repair`、`deterministic_typescript_nullable_canvas_context_repair`、`deterministic_typescript_return_object_semicolon_repair`。Rust module-file topology 规则 E0583 / E0761 已分别通过 `deterministic_rust_missing_module_file_repair` / `deterministic_rust_duplicate_module_file_repair` 成为 runtime executable；Rust missing lib target、lib root facade、struct literal missing field 也已拆成明确 source_tool；后续 Agent 不得把这些规则重新接回 legacy direct-write helper；所有 listed source_tool 必须通过 `RunDirectorRepairCommandV1(source_tool="<listed source_tool>")` 执行，禁止新增语言/规则专用 public facade。

`deterministic_rust_post_repair` 只是 aggregate post-execution callback / legacy schedule label，不是 `runtime_repair_bindings()` 暴露的 `executable_runtime` source_tool，不得传给 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1`。`delete_file` 已作为 repair kernel operation/tool 能力存在并有 receipt/policy 语义；但不能因 `delete_file` operation/tool 存在就恢复 aggregate Rust topology repair。Rust post aggregate 不得作为可执行绑定恢复；新增或迁移余量仍需 coverage、policy 和 revalidation evidence 后才能拆成明确 source_tool。

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
当前 runtime executable binding 口径同步为 31 个 source_tool，其中 Rust 18 个；Rust module-file topology E0583/E0761 已通过 `deterministic_rust_missing_module_file_repair` / `deterministic_rust_duplicate_module_file_repair` 成为 runtime executable，Rust missing lib target、lib root facade、struct literal missing field 也已拆成明确 source_tool。`deterministic_rust_post_repair` 不是 executable binding，只能作为 aggregate post-execution callback / legacy schedule label 观察；不得恢复 aggregate Rust topology repair。`delete_file` 已是 repair kernel operation/tool 能力，但不能因此恢复 aggregate Rust topology repair；新增/迁移仍必须先走 coverage、policy 和 revalidation evidence。最新 targeted gate 摘要：`842 passed`。

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

Codex、Claude Code 等主 Agent 可以通过 OpenCode CLI 将独立工程任务派发给额外 Agent。

### 调用方式

单个任务：

```bash
opencode run "<完整任务提示词>"
```

多个互不重叠的任务可以并行执行：

```bash
opencode run "<Agent 01 完整提示词>" &
opencode run "<Agent 02 完整提示词>" &
opencode run "<Agent 03 完整提示词>" &
wait
```

并行 Agent 可以在同一仓库中工作，但必须负责互不重叠的代码范围。不得让多个 Agent 同时修改同一文件或同一功能链路。

### 主 Agent 职责

调用 OpenCode 前，主 Agent必须先：

1. 阅读仓库中的 `AGENTS.md` 及相关架构文档。
2. 检查 `git status`、`git diff`、失败测试和用户反馈。
3. 使用仓库提供的代码图谱、符号索引或 MCP 工具审计相关代码。
4. 将问题拆分成多个互不重叠、可独立完成的任务包。
5. 为每个任务包明确目标、代码范围、禁止事项和验收命令。

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

每个 `opencode run` 必须获得完整、自包含的提示词，不能依赖当前对话中的隐含上下文。

提示词至少应包含：

- Agent 编号和名称。
- 独立任务目标。
- 必须阅读的规范文件。
- 必须使用的代码审计工具。
- 允许修改的代码范围。
- 禁止修改的范围。
- 强调充分利用codegraph MCP
- 不可违反的架构约束。
- 必须运行的验证命令。
- 最终 JSON 报告格式。

### OpenCode Agent 通用规则

每个 OpenCode Agent 必须遵守：

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
8. 最终必须输出 JSON 审计报告。
9. 充分使用codegraph。

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

最终输出 JSON：
{
  "agent_id": "<编号>/<名称>",
  "status": "completed | blocked | failed",
  "issue": "...",
  "root_cause": "...",
  "files_changed": [],
  "tests_changed": [],
  "commands_run": [
    {
      "command": "...",
      "exit_code": 0,
      "result": "..."
    }
  ],
  "remaining_risks": [],
  "blocked_reason": null
}
```

### 结果回收

所有 OpenCode Agent 完成后，主 Agent 不得直接相信其报告，必须重新审计：

```bash
git status
git diff
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

OpenCode 并行派工必须满足：

- 任务必须窄。
- 修改范围必须互不重叠。
- 证据必须明确。
- 验收命令必须可执行。
- 最终报告必须机器可读。
- 所有结果必须由主 Agent 独立复核。
