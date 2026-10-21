# CLAUDE.md

## 主线研发（用户决定，2026-10-20）

所有源码修改与提交直接在主仓 `main` 进行，不再创建开发分支或开发 worktree。已存在的冻结副本只读保留证据；测试与审计资料继续放在 `~/.polaris/`。


本文件用于指导在本仓库工作的 AI 编码代理。仅保留可执行、可验证的技术约束。

**必用MCP和Skill**: 充分利用codegraph MCP和superpowers，必要时需要使用Playwright来真实跑测试和审计。

## -1) 强制工具链：RTK + CodeGraph + Caveman

- **RTK**：每条 shell 命令及每个 chained segment 都必须以 `rtk` 开头。优先使用 RTK 原生命令；无对应命令时使用 `rtk proxy <command>`。裸 shell 命令只允许用于明确的 RTK 故障诊断。
- **CodeGraph**：源码发现、架构/调用链/影响分析、代码审查及修改前上下文必须先调用 `mcp__codegraph__codegraph_explore`。返回源码视为已读，禁止再用 `rg`/reader 重复读取；只有 CodeGraph 未覆盖、未索引或明确不可用时，才允许使用 RTK fallback，并记录原因。
- **Caveman**：默认启用 `caveman` 的 `full` 模式，压缩过程更新与结果，删除重复叙述和无关日志，但必须保留精确技术名、错误文本、命令、验证数字、风险与未完成门禁。安全警告、不可逆操作和可能产生歧义的多步流程恢复完整表达。
- 三者必须共同使用，不能用其中一个替代另两个。标准顺序：加载适用 Skill -> CodeGraph 定位/审计 -> RTK 执行与验证 -> Caveman 压缩汇报。

## 0) 后端权威入口（2026-03-22）
- **用户事件降噪**：成功的 stage/task/worker 心跳仅用于保活与审计，禁止刷入普通“最近事件”或重复通知。展示过滤必须在窗口截取之前；不删底层事实，不隐藏心跳失败/告警、阶段切换、工具结果或交付进展。仅有保活或当前窗口缺少调用证据，不能宣称模型从未执行；前端验收需真实 runtime.v2 推送、无 reload/产品轮询。
- 对于任何 `src/backend` 任务，必须先读 `src/backend/AGENTS.md`。
- 统一架构执行标准入口：`src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`。
- 后端强制规则：`Cell` 开发先复用已有 Cell 公开能力；所有新开发必须基于 `KernelOne` 底座能力与契约链路。
- 若本文件与 `src/backend/AGENTS.md` 或 `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` 存在冲突，以后两者为准。

### 当前工程主线

1. 新工程运行的唯一 owner 是现有 `roles.runtime.public.engineering_service.EngineeringRunService`。HTTP、CLI、UI、取消、恢复与错误路径共用该服务，不新增 Cell、任务表、ledger 或第二 controller。
2. 默认 `workflow=direct`，Director 读取目标、调用工具、根据真实结果继续工作；PM/Chief Engineer 按需只读咨询。显式 `workflow=collaborative` 才执行 PM、Chief Engineer、Director 协作；未调用角色显示 `not_called`。咨询一层返回，不递归派生总控；讨论不创建工程 run。
3. PM/CE 使用可读文本提供规划、设计或审阅。新运行不要求 PM 合同、CE portfolio/blueprint、handoff/hash、OBL/covers 图、semantic patch、Task Market、JobToken/DEO/Run Ledger 旧链准入，也不受旧一 turn 一 batch 规则约束。普通编译/测试失败回到同一 Director，不自动 deterministic repair 或重启规划。
4. 工具授权来自 host 持久化且不可由模型扩大的 `PermissionScope`、当前 profile、预算与 deadline；显式空权限保持为空。归一化后仍须检查真实路径/symlink/允许根，经 KFS 读写和 OS/process sandbox 执行命令；不支持 containment 时明确拒绝，禁止裸跑。PM/CE/QA 默认只读，同 workspace 仅一个源码写入者。
5. 唯一工程事实记录是 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`，保存运行、事件、真实工具观察和验证；新运行不写旧 TaskBoard/TaskMarket/Run Ledger/role-session 镜像。保留 runtime.v2/Nats-JetStream、ContextOS 最终 provider request、UTF-8、实例 workspace 绑定与 storage 单根。
6. 角色回复结束与 `completed` 不等于 `accepted`。实际检查必须绑定当前源码；用户已声明检查必须保留，未声明时由 Director 结合实际工程选择并实际执行验证。空检查、模型自报、快照条数或 QA 文本不得冒充通过；任务要求的独立 QA 必须审查同一当前候选，拒绝后继续同一任务。
7. 恢复使用原身份、workspace、原始权限与 deadline，并重新校验当前 host policy；保留失败、interrupted/UNKNOWN 和未知效果，不自动重放可能已执行的命令或写入。旧 ID 不升级为新 run；历史按 `engine_version` 只读，旧在途运行留在原冻结 owner，不在新进程恢复旧写入口。
8. 旧 Factory/RoleRuntimeService/execute_role_session/RoleExecutionKernel 写准入与自动 fallback 退役；历史解码器、只读查询与显式可选技术工具可保留，禁止据此声称历史源码已全部删除。旧规范仅解释旧记录，不能重新约束新运行。底层 hash 可用于证据/快照完整性，不能要求模型填写业务 hash。真实 HTTP 次数与费用仅用物理证据，未知保持 UNKNOWN。

旧 [后端完成权威与修复细则](docs/agent-guides/backend-authority-and-repair.md) 仅解释历史 Factory/合同记录和显式可选 repair 工具，不能成为新工程准入。新主线依据 [批准方案](docs/blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md) 与后端架构标准。

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
- 工程循环 / 角色运行（优先修改）: `src/backend/polaris/cells/roles`, `src/backend/polaris/kernelone`
- Director Runtime/Accel: `src/backend/polaris/cells/director`
- Director deterministic repair kernel: `src/backend/polaris/cells/director/runtime/internal/repair_kernel`（只允许 cell 内实现使用；跨 Cell 走 public service）
- PM/Director 编排层: `src/backend/polaris/delivery/cli/pm`, `src/backend/polaris/delivery/cli/director`
- 前端主 UI: `src/frontend/src/app`
- 测试: `tests/electron`, `src/backend/polaris/tests`

说明:
- `src/backend/polaris` 是后端 ACGA 2.0 迁移承载根；新的主实现优先进入这里
- 旧根 `src/backend/{app,core,api,scripts}` 已在 ACGA 2.0 迁移中删除并迁入 `src/backend/polaris/{bootstrap,delivery,application,domain,kernelone,infrastructure,cells}`

## 3) 常用命令

启动、调试或验证前，读取 [开发与验证命令](docs/agent-guides/development-reference.md)。原始启动参数、环境变量和命令示例完整保留；继续遵守本入口的 RTK 规则。

## 4) 验证命令（按改动面最小执行）

使用上述参考中的对应门禁；只改文档时检查内容完整性、引用和实际加载。未运行的门禁不得报告通过；改代码仍须遵守下方质量验收标准。

## 5) 强约束
- 所有文本文件读写必须显式使用 UTF-8。
- TypeScript 保持 `strict`，公共接口禁止 `any`。
- 变更 Loop / 角色内核时，优先修改 `src/backend/polaris/cells/roles` 与 `src/backend/polaris/kernelone`。
- 不提交运行时产物: `.polaris/runtime/**`, `playwright-report/**`, `test-results/**`。
- 默认 runtime 唯一物理根是目标项目 `<workspace>/.polaris/runtime`；Instance Registry、backend CLI、ContextOS、ReceiptStore、Run Ledger 与内部 Bench launch receipt 必须消费同一根。外部 cache/RAM-disk 仅允许显式 opt-in；禁止新建 `<workspace>/runtime` 或默认外置到 system cache，旧外部 namespace 只读兼容、禁止双写/自动搬迁。
- 验证失败不得标记任务完成（fail-closed）。
- 任何 Bench、角色、工具、QA、runtime 或控制面失败都必须先对 exact run 做动态 DEBUG：最终 provider request/response、tool lifecycle、真实 effect、当前 verifier/QA 与 engineering SQLite；旧 TaskRuntime/Run Ledger/settlement 仅解释历史 run；静态分析只能解释动态证据，不能替代动态复现。生成的目标项目只允许只读取证与执行 verifier，绝对禁止直接修改；修复必须落在 Polaris 元平台、具备通用性，并附回归测试、同运行验证与机器可读缺陷记录。
- 多项目并行观测必须用 Instance Registry + `/launcher` 启动或发现多个单-workspace 实例；不要把单个 backend/UI 临时改造成多 workspace 状态拼接层。
- 从 Launcher 打开的实例工作台必须通过 URL query 或 `VITE_POLARIS_*` 显式绑定 `instance`、`backend`、`token`、`workspace`；前端 API 与 `/v2/ws/runtime` 必须使用该 workspace 绑定，禁止静默回退到默认 backend、默认 workspace 或主仓 runtime。
- 需要被总控观测的 Agent/CLI/内部压力测试启动项必须注册实例；Launcher 只读实例发现状态，不能成为 PM、Chief Engineer、Director、QA、ContextOS、ReceiptStore 或 Run Ledger 的事实源。
- `factory_bench`、L1-L12 和 benchmark harness 只属于内部测试/开发/审计模式；共享后端 bench 注册只能作为“可观测的测试实例”，不得冒充独立生产实例，正式产品/生产环境不得出现 Bench 入口、Bench 文案、Bench 专属 UI/API 或 Bench 事实模型。
- `metadata.backend_binding=shared_backend_workspace_switch` 的 `bench_project` 执行 restart/独立启动时，Supervisor 必须分配新的 backend/frontend 端口并启动独立实例，禁止复用共享 backend 端口。
- 仅在显式冻结旧引擎内部测试中，多 Agent 并行跑 `factory_bench` 时 runner 必须显式使用 `--launcher-instance-mode isolated --bench-session-reporting off`，让每个项目的 Factory run 指向自己的 backend；Launcher 可见性来自 Instance Registry 和项目实例自己的 runtime.v2。共享主后端 `/v2/factory/bench/sessions` 只是内部兼容观测桥，只有串行调试时才允许 `--launcher-instance-mode observed --bench-session-reporting shared`，不得用于共享 49977 的并发压测。
- 显式冻结旧引擎的完整 L1-L12 可运行性验收必须使用 `run_factory_bench.py` 的 `--timeout 5400` 和外层 `timeout --kill-after=30s 6000s`；`540s/600s` 只允许启动/失败路径 smoke，不得作为 runnable 或 `COMPLETED_VERIFIED` 证据。R38 已证明 540s 总预算在三波 Director + QA/safety 保留 310s 后只给 CE 188s，导致确定性的 `provider_stream_timeout:188s`。
- `49977/5173` 只属于 `main` 开发实例。bench、Factory Bench、临时项目或 Agent 私有实例不得手工指定这些端口，不得向主后端 `POST /settings` 切换到 bench workspace；必须通过 Instance Supervisor/Launcher 自动分配非主端口，并打开对应实例 URL。
- Launcher 实时状态只走 runtime.v2 WebSocket `status.instances`；禁止用 HTTP polling、文件轮询或 Bench session 替代正式实时链路。
- 当前承载 Launcher API 的实例不能通过自己的 `/v2/instances/{id}/stop|restart|delete` 自我停止、自我重启或删除自身记录；这类操作应返回 fail-closed，前端也必须禁用当前控制实例的危险操作。清理 stale bench 只能作用于 stopped、backend dead、`metadata.internal_test_only=true` 的内部测试实例。
- Run Ledger 投影必须区分 `missing_required_modalities` 与 `failed_required_modalities`：前者是控制面/工具链没有记录证据，后者是证据存在但命令、browser smoke、用户脚本或其它 verifier 失败。不要把 failed evidence 写成 missing evidence；内部 bench 只能消费这个平台级语义，不能定义自己的成功/失败事实源。
- LLM 事件里的 `context_snapshot_ref` 必须是同 workspace 下 `/v2/context/{hash}` 和 `/v2/context/{hash}/final-request` 都可读取的 24 位 hex key。ContextOS 读取候选链必须优先包含 `<workspace>/.polaris/runtime` 与 Instance Registry 同 workspace 的 `runtime_root`，并只把旧 KernelOne system cache 作为只读兼容候选；404 要返回 `context_hash`、`workspace`、`searched_paths`，前端不能把跨 workspace hash 送进完整上下文 modal。
- `event.bench` 是内部测试态全局事件流；只有总控/主开发页在显式 `globalObserver` 模式下可以订阅。实例工作台、PM/CE/Director/QA/ContextOS 项目页默认只能消费调用方传入的 scoped bench 数据，`enabled` 本身不得触发 `useFactoryBench({autoSelect:"newest"})`。

## 6) 常用环境变量
- `KERNELONE_WORKSPACE`
- `KERNELONE_RENDERER_PORT`
- `KERNELONE_BACKEND_PORT`
- `KERNELONE_PM_PROVIDER`, `KERNELONE_PM_MODEL`

## 7) 核心系统地图（防重复造轮子）

新增或修改工具、角色对话、Provider、任务管理能力前，必须读取 [核心系统地图](docs/agent-guides/system-map.md)，先复用现有实现。原唯一实现、禁止行为、导入示例、历史替代路径与检查清单均保留在该文件。

## 8) 绝对禁止：在 Polaris 项目中添加业务代码

**铁律**：Polaris 是元工具平台，禁止在主仓代码中添加任何目标项目/业务相关代码。

### 8.1) 禁止行为
- ❌ 在 `worker_executor.py` 或任何 Polaris 源码中为特定项目添加代码模板（如 Express、Django、React 等）
- ❌ 在 Polaris 代码库中硬编码目标项目的配置、路径、或文件名
- ❌ 为解决特定项目问题而修改 Polaris 核心逻辑（应修复通用逻辑）

## 9) 内部量具、最终请求与可选 repair 工具

### 9.1) 修复层级铁律：修系统，不修量具

**禁止在 bench 测量层做 repair**。`bench_gates.py` 是审计/量具，只负责检测和归因。
普通代码修复由同一 Director 工具循环完成。只有显式调用独立 deterministic repair 工具时，才适用 `director.runtime.public.service` 的 coverage、plan/compose、policy、receipt/revalidation 规则；禁止从新运行自动回退旧 schedule 或 legacy strategy host。

### 9.2) Director 上下文强制审计清单

每次 bench 失败或代码质量问题，**必须先审计 Director 最终 LLM 请求**再做下游修复：

1. **context_snapshot_ref** → 读取完整 provider_request
2. **context_window_utilization** → < 10% 是红旗（说明关键信息未注入）
3. **目标与设计** → 原目标、相关代码和实际使用的设计完整；direct 任务不要求 CE Blueprint
4. **Task 描述完整性** → 不得截断
5. **role identity** → system prompt 中 Director 身份是否正确
6. **tools** → 是否包含 write_file, read_file, execute_command 等必要工具
7. **tool_choice** → 是否正确（auto vs forced）

审计位置：通过 `resolve_storage_roots(workspace).runtime_root / "contexts" / <shard> / <hash>` 读取当前 canonical ContextOS 快照；默认位于 `<workspace>/.polaris/runtime/contexts/<shard>/<hash>`。旧 `~/.cache/kernelone/.polaris/projects/<workspace-key>/runtime` 与 `~/.cache/polaris/...` 只能作为只读兼容候选，不得作为新写链路依据。`context_snapshot_ref` 必须是 `/v2/context/{hash}` 可读取的 24 位 hex 快照 key；不得把 `request_hash`、`prompt_hash`、`call_id`、`turn_id`、文件路径或旧事件字符串当成完整上下文快照引用。

### 9.3) 当前工程上下文与验证

当前 Director 接收原目标、已调用角色的普通文本建议、相关代码与近期真实工具结果。PM/CE 未调用时不补造合同/蓝图或 task ID 映射；历史 BlueprintPersistence 路径只用于原记录读取。

质量检查先绑定当前源码，再实际执行。失败返回同一任务；可选独立 repair 工具必须有其自身 policy、效果和复测证据，不能替代真实检查或独立 QA。

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

调用任何外部工程 Agent 前，必须完整读取 [外部 Agent 契约与模板](docs/agent-guides/external-agent-contract.md)，遵守原调用协议、并发隔离、JSON schema、报告解析、验收和独立复核要求。

外部 Agent 仅作主 Agent 的工程协作工具；禁止成为 Polaris 产品、Factory Bench、Run Ledger、ContextOS、ReceiptStore、UI 或运行时的依赖、状态事实源或成功条件。
