# PM / CE / Director 工程主线重构 Implementation Plan

> **For agentic workers:** 使用 superpowers:subagent-driven-development 或 superpowers:executing-plans，逐批交付；步骤以复选框跟踪。当前第 1 批技术组件已验证，其余批次未完成；组件通过不代表真实模型、完整新引擎或 Bench 交付。

**Goal:** 保留角色能力，让模型用真实工具推进工程，彻底删除新运行对模型合同、候选 hash、OBL/covers 和语义修补循环的依赖。

**Architecture:** 新循环归现有 roles.runtime；复用纯 KernelOne 技术端口，不包装旧角色内核。一个运行、一个工作区写入者、一份会话数据库；PM/CE 按需提供可读建议，实际检查在成品上执行。

**Tech Stack:** 仓库现有 Python、FastAPI、React/TypeScript、KernelOne、标准库 SQLite、Nats-JetStream/runtime.v2、pytest、Vitest、Playwright。首轮操作系统验收为当前 WSL2/Linux。

**Spec:** [完整重构方案](../../blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md)。

## Global Constraints

- 本方案采用用户最新的角色按需、删除模型合同维护方向；旧固定三段规则须与新入口同批修订，不能暗中锁回旧流程。
- UTF-8；保留无关 dirty work；本计划不授权修改运行中的冻结候选、目标项目或重启现有服务。
- 原模型、预算与任务验收要求不因对比实验降低；完整旧 Bench 预算仍为单项目 5400 秒、外层 6000 秒。
- 模型自然语言不产生额外权限；同一工作区只有一个源码写入者；控制数据库、锁和快照不在模型可写域。
- 实时只用 Nats-JetStream 与 `/v2/ws/runtime`；HTTP 用于初始快照、显式查询与一次性动作。
- 新 run 不调用 RoleExecutionKernel、旧 KernelToolExecutor、JobToken/DEO/RunLedger 合同准入或 semantic repair。不能通过后台补齐旧字段蒙混复用。
- 完整路径相对仓库根。分批清单中的 `roles/runtime/`、`factory/`、`chief_engineer/` 是 `src/backend/polaris/cells/` 下对应目录；`delivery/` 是 `src/backend/polaris/delivery/`；同一条目的后续短文件名沿用其已列目录。前端 `services/`、`hooks/`、`app/`、`runtime/` 均相对 `src/frontend/src/`。除特别说明，命令在 `/home/dains/Documents/polaris` 执行，并从 `rtk` 开始。

## Review Focus

1. 原生工具列表不完整、重复 ID 或流被中断：不得静默丢调用、执行半个参数或把文本当 action（批次 1/2）。
2. workspace symlink、脚本逃逸与控制数据库/锁篡改：文件和命令两条路径都拒绝（批次 1/2）。
3. 写入已发生但结果丢失：重启不得自动重放，取消不得假称回滚（批次 4）。
4. 实现后再次修改、缺失检查、未调用 QA：旧绿灯失效，角色回复不等于验收（批次 4/6）。
5. 旧 API、聊天和 CLI 偷走旧内核：单 run 固定引擎，新链无 fallback/双写（批次 5/8）。

---

## A. 唯一归属和文件布局

新写入 owner 固定为 `roles.runtime`。不采用另一份建议中让 `runtime.task_runtime` 再写新运行的方案。旧 TaskBoard、Run Ledger 和角色会话数据只服务旧 run；新 UI 从新会话记录派生。

### 新增

| 路径 | 职责 |
| --- | --- |
| `src/backend/polaris/cells/roles/runtime/public/contracts/engineering_run.py` | 下述五个小类型 |
| `src/backend/polaris/cells/roles/runtime/public/engineering_service.py` | 唯一新服务 |
| `src/backend/polaris/cells/roles/runtime/internal/engineering_loop.py` | native 模型工具循环 |
| `src/backend/polaris/cells/roles/runtime/internal/engineering_tools.py` | 工具组合、权限与结果 |
| `src/backend/polaris/cells/roles/runtime/internal/engineering_session.py` | SQLite、运行记录与工作区锁 |
| `src/backend/polaris/cells/roles/runtime/internal/engineering_roles.py` | 专业角色和咨询 |
| `src/backend/polaris/cells/roles/runtime/internal/engineering_verification.py` | 实际验证 |
| `src/backend/polaris/kernelone/process/sandbox.py` | 从现有隔离实践提取通用命令 containment，无 Factory 语义 |
| `src/backend/polaris/delivery/http/routers/engineering_runs.py` | 新 HTTP facade |
| `src/frontend/src/services/engineeringRunService.ts` | 新客户端 |
| `src/frontend/src/runtime/engineeringRunProjection.ts` | 唯一新 UI 读模型 |

不新增 Cell、第二个通用 provider SDK、规则引擎或全局万能 context。现有 `public/contracts` 是目录，不能误创建同名 `contracts.py`。

### 五个普通程序类型

在 `public/contracts/engineering_run.py` 定义以下字段。它们是后端接口，不是要求模型填写的业务输出：

```python
@dataclass(frozen=True)
class RoleRunCommand:
    session_id: str
    role: Literal['pm', 'chief_engineer', 'director', 'qa']
    instruction: str
    workspace: Path
    workflow: Literal['direct', 'collaborative'] = 'direct'
    required_checks: tuple[str, ...] = ()

@dataclass(frozen=True)
class PermissionScope:
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    enabled_tools: frozenset[str]
    network_allowed: bool

@dataclass(frozen=True)
class RunLimits:
    deadline: float | None
    max_output_bytes: int
    max_tool_output_bytes: int

@dataclass(frozen=True)
class ToolObservation:
    call_id: str
    tool: str
    ok: bool
    content: str
    exit_code: int | None
    interrupted: bool

@dataclass(frozen=True)
class RoleRunResult:
    session_id: str
    role: str
    text: str
    status: Literal['completed', 'failed', 'cancelled', 'interrupted']
    verification: Literal['passed', 'failed', 'not_run', 'unknown']
    checks: tuple[ToolObservation, ...]
```

代码实现补正常 import，不引入 candidate、diagnosis、OBL、交接 hash 或 delegated recipe DTO。权限来源在服务端，不能从 `instruction` 提取扩大。

### 服务与持久化接口

- `EngineeringRunService.run_role(command, *, scope, limits, cancel: asyncio.Event) -> RoleRunResult`。
- `EngineeringRunService.start(command, *, scope, limits, request_key: str | None) -> str`：持久化并启动后台执行，返回 session_id。
- `EngineeringRunService.observe(session_id: str, *, workspace: Path) -> dict[str, object]`。
- `EngineeringRunService.cancel(session_id: str, *, workspace: Path) -> None`。
- `EngineeringRunService.resume(session_id: str, *, workspace: Path) -> str`。
- `EngineeringSessionStore.create_run(command, *, scope, limits, engine_version, request_key) -> str`。
- `EngineeringSessionStore.append_event(session_id, *, kind, role, payload) -> int`：返回单调 seq。
- `EngineeringSessionStore.load_run(session_id, *, workspace) -> dict[str, object]`。
- `EngineeringSessionStore.acquire_workspace(workspace) -> ContextManager[None]`。

SQLite 只包含 `runs`、`events`、`tool_calls` 三表；工具事件和终态只写这一 owner。实际数据库路径为 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`。保存并恢复 command 的 workflow/required_checks 和实际权限 profile；同 request_key 的同请求只创建一次，同 key 的目标、范围、workflow 或 required_checks 不同均返回冲突，直接比较保存字段，无模型 hash。恢复权限须重新按当前平台策略校验，不能继承已撤销权限。

`start` 根据 workflow 选择一次顶层协作流程；`run_role` 只执行当前角色循环，不能递归重启完整协作。咨询角色使用 direct 且无独立 required_checks；主 Director 结束实现后把保存的 required_checks 交给实际验证。恢复通过已保存的角色完成记录接续，不丢检查，也不自动重做整套规划。

## B. 八批实施任务

### Task 1：证明可以拆用的技术底座

**Files:**

- Modify: `src/backend/polaris/kernelone/llm/engine/executor.py`、`engine/stream/executor.py`，仅当实际 native 端口存在缺口。
- Modify: `src/backend/polaris/kernelone/llm/toolkit/parsers/native_function.py`；`tool_execution/tool_spec_registry.py`，只修明确丢调用或归一化缺口。
- Create: `src/backend/polaris/kernelone/process/sandbox.py`。
- Create: `src/backend/polaris/cells/roles/runtime/tests/engineering_support.py`、`test_engineering_primitives.py`、`test_engineering_native_primitives.py`；隔离专项用例位于 `kernelone/process/tests/test_workspace_sandbox.py`。

**Interfaces:**

- 模型使用现有 `AIRequest/AIResponse`、`ResilientLLMClient`，不调用 prompt-only role provider facade。
- 统一新循环技术输入为 `AIRequest.options['messages']`、`['tools']`、`['tool_choice']`；已有等价原生键在现有 codec 映射一次，保持旧 caller 兼容，不保留多套新入口。
- `build_workspace_sandbox_argv(argv, *, workspace, cwd, read_roots, write_roots, network_allowed) -> list[str]`，放在 K1 新通用文件。
- 每次命令创建独立 `ProcessTreeRunControl`，底层调用现有 `run_process_tree_safe`；调用方取消后仍等待真实 owner 回收。不能把进程组当 PID namespace。
- `engineering_support.py` 提供 `scripted_provider`（loopback HTTP）、`isolated_workspace` 和 `private_sandbox` 测试 fixtures；明确这是组件替身，不是真实模型验收。

- [x] 先写 RED：OpenAI/Anthropic native 往返保留两个相同参数但不同 ID 的调用；中断流不能派发不完整调用；HTTP 错误不能变成功文本。
- [x] 先写 RED：文件/shell 对工作区外 sentinel、控制 SQLite、锁、symlink 目标的写入均失败；沙箱内正常写入成功；`setsid` 子进程取消后不能遗留。
- [x] 运行目标测试，确认失败在待修技术边界。仅补缺失的纯技术能力；不能导入旧业务内核凑通过。
- [x] GREEN 后核对 provider 原生 body 与真实进程收尾。平台缺少 namespace 能力时，明确不支持，不退回无隔离执行。
- [x] 独立复核当前技术修改与旧调用兼容后，提交本批互斥变更。

Run（实施时设置 `PYTHONPATH=src/backend`）：`rtk proxy python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_engineering_primitives.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_native_primitives.py src/backend/polaris/kernelone/process/tests/test_workspace_sandbox.py src/backend/polaris/tests/test_process_tree.py -q`

Expected: native、正常效果与拒绝/取消反例全部通过；没有 Provider/Bench 能力结论。

### Task 2：可直接工作的 Director 循环

**Files:**

- Create: `public/contracts/engineering_run.py`、`public/engineering_service.py`、`internal/engineering_loop.py`、`internal/engineering_tools.py`、`internal/engineering_session.py`（均在 `roles/runtime/`）。
- Modify: `roles/runtime/public/__init__.py`：新入口轻量导出，不能因 package import 自动加载旧执行内核。
- Create: `roles/runtime/tests/test_engineering_loop.py`、`test_engineering_session.py`、`test_engineering_no_legacy_imports.py`。

**Interfaces:** 消费 A 节五类型、Task 1 底座及 fixtures；产出 A 节公开服务与单份 store。

初始工具面只启用读、列目录、搜索、写文件、精确编辑和执行命令。schema/alias 由现有 ToolSpecRegistry 单次派生；不对整批按 name+args 去重。每个原生调用按声明顺序返回结果或明确错误，首版不并发副作用工具。

- [x] RED：loopback 模型读取临时 `calc.py`、修复 `add(2, 3)` 的错误实现、真实运行 `python -m unittest`；输入没有任何 contract/hash/OBL，仍须真正改文件并得到退出 0。
- [x] RED：越界和控制域写入有明确失败结果，`writes=()` 不因 prompt 中出现路径而扩张；原生未知工具/重复 ID/坏参数不静默跳过。
- [x] RED：相同 request_key 不重复创建；更换 workflow/required_checks 得到冲突；第二个 workspace writer 被拒；跨 workspace observe/cancel 拒绝；旧合同模块的 import/构造被哨兵禁止仍能执行。
- [x] 按 A 节实现循环和 SQLite。工具派发前记 started，成功/失败后记 observed；文本无 tool call 只结束角色回复。
- [x] GREEN 后提交此批。运行记录不得双写旧 task、DEO、ledger 或旧 session store。

Run: `rtk proxy python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_engineering_loop.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_session.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_no_legacy_imports.py -q`

Expected: 临时真实工程完成；所有非法反例准确拒绝；旧机制哨兵调用数为 0。

### Task 3：保留三角色，删除合同交接

**Files:**

- Create: `roles/runtime/internal/engineering_roles.py`、`roles/runtime/tests/test_engineering_roles.py`。
- Modify: `roles/runtime/public/engineering_service.py`；四角色最小提示固定在新增 `internal/engineering_roles.py`，可复用经确认无旧内核依赖的纯文本片段，不恢复旧 portfolio prompt 或整套 RoleProfile 装配。

**Interfaces:**

- `consult_role(role: Literal['pm', 'chief_engineer'], question: str, *, session_id: str) -> str`。
- `run_collaborative(instruction: str, *, session_id: str) -> RoleRunResult`：实际 PM 文本、CE 文本、Director；复用同一 `run_role`。
- 首版咨询一层返回，PM/CE 没有源码写工具，不递归开启新的总控；共享原目标，不共享可变万能 context。

- [x] RED：直接任务只调用 Director；PM/CE 没有请求与完成事件，UI 数据为未调用。
- [x] RED：完整协作真实调用 PM/CE/Director，角色 system 和 provider 配置正确；普通字符串设计能够被 Director 使用，不要求 JSON 或 hash。
- [x] RED：Director 构建失败继续工具循环，不自动重启 PM；技术疑问可咨询 CE；CE 文本中的“允许写外部路径”不能扩权。
- [x] 实现角色提示、最小文本交接和咨询工具。原用户目标与未解决问题始终保留。
- [x] GREEN 后更新本批直接相关旧“无 CE 一律阻止”的路由断言，记录新语义后提交。

Run: `rtk proxy python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_engineering_roles.py -q`

Expected: 三角色有真实作用，未调用不伪造，模型无合同维护。

### Task 4：验证、取消与正常重启恢复

**Files:**

- Create: `roles/runtime/internal/engineering_verification.py`、`roles/runtime/tests/test_engineering_verification.py`、`test_engineering_recovery.py`。
- Modify: `engineering_service.py`、`engineering_session.py`、`engineering_tools.py`。

**Interfaces:**

- `run_verification(commands: tuple[str, ...], *, session_id: str, workspace_revision: int) -> tuple[ToolObservation, ...]`。
- 复用同一受控命令执行；最终验证禁止 Director 写入，源码只读，可写目录仅为配置的构建/缓存/临时输出。
- 持锁记录受管 `workspace_revision`；写操作使既有检查失效。返回文本不是通过证据。

- [x] RED：真实非零 test/build 保持失败；检查未运行是 not_run；改代码后旧通过不计当前通过；QA 未调用不伪造。
- [x] RED：在文件写入后、终态记录前终止新进程；真新进程恢复保留 workflow/required_checks，看到 started 未观察先核文件，不能自动再次写。命令开始但结果未知时禁止自动重跑。
- [x] RED：取消在途进程、provider 和后续工具派发；只有实际排空才允许释放锁，不能把 task cancellation 当回滚。
- [x] 实现上述最小恢复及验证。检查输出与业务源码的可写域分开；异常保留具体原因。
- [x] GREEN 后提交。非幂等外部效果无法确定时返回 interrupted/unknown，不为提高完成率伪造恢复。

Run: `rtk proxy python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_engineering_verification.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_recovery.py -q`

Expected: 真进程恢复、真实取消和当前验证对照通过；不宣称任意日志损坏可恢复或外部效果 exactly-once。

### Task 5：HTTP、CLI 与聊天执行统一入口

**Files:**

- Create: `delivery/http/routers/engineering_runs.py`、`delivery/http/tests/test_engineering_runs.py`。
- Modify: `src/backend/polaris/delivery/http/app_factory.py` 的路由注册；`delivery/http/routers/role_chat.py`、`role_runtime_chat.py`、`pm_chat.py`。
- Modify: `delivery/cli/pm/cli.py`、`delivery/cli/director/cli_thin.py`、`cells/chief_engineer/blueprint/internal/chief_engineer_cli.py`。

**Interfaces:**

- `POST /v2/engineering/runs` body：`instruction`、`workspace`；可选 `workflow=direct|collaborative`、`role`、`permission_profile_id`、`required_checks`、`request_key`。
- GET `/v2/engineering/runs/{id}`；POST `/{id}/cancel`、`/{id}/resume`。全部读取 A 节同一服务。
- reply 只包含 session/run ID、`engine_version=engineering_v2`、执行状态、实际活动角色与检查结果；服务器校验 profile，模型不能选权限或改已有 engine。

- [ ] RED：三个入口创建/读取/取消同一种 run；新 run 不出现合同占位，重复请求与跨 workspace 受控。
- [ ] RED：纯聊天没有暗中创建工程任务；执行聊天必须返回同一 run ID。旧 run 不能直接 resume 到新引擎。
- [ ] 实现 facade 与 CLI 薄入口；新 ID 请求旧 DTO 时明确不支持/给新链接，不返回伪蓝图或旧 ledger pass。
- [ ] GREEN 后提交。开发态仍在独立实例上测试，不热替换现有冻结 run。

Run: `rtk proxy python -m pytest src/backend/polaris/delivery/http/tests/test_engineering_runs.py -q`

Expected: facade 行为一致、诚实分流、无隐藏本地任务路径。

### Task 6：实时界面与 ContextOS

**Files:**

- Create: `src/frontend/src/services/engineeringRunService.ts`、`runtime/engineeringRunProjection.ts`、相邻同名 `.test.ts`。
- Modify: `services/api.types.ts`、`hooks/useFactory.ts`、`app/components/factory/FactoryWorkspace.tsx`。
- Modify: `app/components/pm/PMWorkspace.tsx`、`chief-engineer/ChiefEngineerWorkspace.tsx`、`director/DirectorWorkspace.tsx`。
- Modify: `app/components/contextos/ContextOSWorkspace.tsx`、`ContextViewerModal.tsx`、`contextOSTelemetry.ts`；backend `delivery/http/v2/context.py` 及原生请求审计的技术写入点。
- Create: `src/frontend/src/app/components/factory/EngineeringRunWorkspace.test.tsx`、backend `roles/runtime/tests/test_engineering_context_audit.py`。

**Interfaces:** 复用 runtime.v2 消息封套与现有单一 transport；新增平台级 `event.engineering` channel，由 backend 事件映射和前端共享 transport 声明。事件内容为 session/seq/workspace/role/kind/payload；不增加第二 WebSocket。

- [ ] RED：tool started/result 与 role start/end 更新页面；PM/CE 未调用不显示已通过；completed 文本不自动变验收通过。
- [ ] RED：重复 seq、跨 workspace 事件、断线与重新连接使用现有语义；没有 polling fallback。
- [ ] RED：loopback transport 最终消息、工具 schema、角色身份与 ContextOS 可读快照一致；快照不可读时显示不可用，不发假 ref 或重跑合同修复。
- [ ] 实现新 projection 和页面数据绑定，删除新 run 的合同覆盖阻塞卡；原始 provider request 由平台记录，不交给模型填写。
- [ ] GREEN 后提交。Launcher 保持实例运维职责与现有端口/绑定，不参加任务接受判断。

Run: `rtk proxy npm test -- src/frontend/src/runtime/engineeringRunProjection.test.ts src/frontend/src/app/components/factory/EngineeringRunWorkspace.test.tsx`

Run: `rtk proxy python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_engineering_context_audit.py -q`

Run: `rtk proxy npm run typecheck`

Expected: 数据映射、快照与身份测试通过；实际推送与浏览器结果由批次 7 证明。

### Task 7：正式真实任务与默认切换

**Files:**

- Create: `playwright.engineering.config.ts`、`src/frontend/e2e/engineering-run.spec.ts`。
- Create: `src/backend/polaris/delivery/cli/engineering_acceptance.py`，内部测试 CLI，只提交任务/只读观察，不修目标。
- Modify: 新入口默认选择及 legacy 新建开关；只在以下结果满足后切换。

**Interfaces:** Playwright baseURL 必须来自 `ENGINEERING_ACCEPTANCE_URL`，无默认主实例地址，无自动 webServer；独立实例通过现有 Supervisor/Registry 启动。CLI 使用 `--case`、`--timeout`、`--report-dir`，只在内部测试态启用。

- [ ] 先用 loopback 与私有 fixture 运行 RED/GREEN 浏览器用例，确保 WS 进度、取消、错误与未调用角色可见；这些不计真实模型验收。
- [ ] 冻结候选，运行新项目、已有项目修复和复杂协作三类正式样本，真实 provider、工具、测试与入口均实际发生。测试数据来自用户目标或既有 catalog，不是平台生产代码里的业务模板。
- [ ] 对要求零外部修复的原 L1-01，保留原合同内容/验收标准与完整预算，走正式新入口；legacy 基线只用其冻结版本。harness 不初始化目标 Git、不补依赖、不代执行验收、不写 receipt/ledger。
- [ ] 观察真实最终请求、工具、测试、必要 QA、进程排空与实例停止；浏览器无刷新看到运行变化，断线诚实显示。
- [ ] 汇总首个有效代码时间、完成与失败、真实调用、墙钟、人工分钟和已知费用。保留每版本分母；证据满足后切默认新入口并关闭 legacy 新建。
- [ ] 提交本批；部署若需停止当前承载服务，按既有运维授权和 Supervisor 边界另行执行，本计划不执行停服。

Run: `rtk proxy npx playwright test --config playwright.engineering.config.ts`

Run（实施时设置 `PYTHONPATH=src/backend`）：`rtk proxy python -m polaris.delivery.cli.engineering_acceptance --case L1-01 --timeout 5400 --report-dir /tmp/polaris-engineering-acceptance`

外层守护预算 6000 秒且保留退出码；短启动 smoke 不作为交付证据。Expected: 同要求下真实结果可核、无人工目标修复、无旧业务协议依赖。没有实际结果不能预填 PASS。

### Task 8：删除旧主线与同步治理

**Files:**

- Retire production calls: `factory/pipeline/internal/factory_stage_executor/_mixin_02.py` 的 portfolio/semantic repair 编排、`factory_pm_contract_normalization.py`、`factory_ce_evidence.py`、`factory_director_dispatch_impl.py` 的旧准入。
- Retire production calls: `chief_engineer/blueprint/public/contracts/_semantic_repair.py`、`public/service/_semantic_repair.py` 与相应 prompts/schema；仅历史 reader 所需形状可只读保留。
- Retire new-run use: `roles/kernel/internal/kernel/tool_executor.py`、旧 execute_role_session、DEO/JobToken/RunLedger 合同门禁与 deterministic repair 自动回退。
- Modify: `AGENTS.md`、`CLAUDE.md`、`GEMINI.md`、后端镜像、`AGENT_ARCHITECTURE_STANDARD.md`、现有 graph/catalog/subgraphs、相关 cell.yaml 与治理 fitness rules。
- Create: `src/backend/polaris/tests/architecture/test_engineering_no_legacy_admission.py`。

**Interfaces:** 历史记录按 engine_version 读取；所有新的可写角色入口只进入 engineering_service。旧在途 run 保持原版完成或在明确旧版实例承载，不在新主进程继续创建旧 run。

- [ ] RED：阻断旧内核、合同 compiler、semantic repair、旧 receipt gate 的生产 imports/calls，新入口、重启、CLI 和 UI 仍须完成批次 2—7 行为。
- [ ] 清点全部实际入口/消费者，删除默认与恢复 fallback；迁移原有工具安全测试与必要技术覆盖，不能只删失败测试或用更弱断言换绿。
- [ ] 同批替换固定三段、模型 hash/覆盖前置、一 turn 一 batch、普通修复必须过规则目录等不兼容规范；保留实际权限、隔离、真实验证和观测边界。
- [ ] 旧 API 写入口关闭，旧数据只读；有未知效果的 run 不自动升级或重放。清理已无人使用的旧实现后提交。
- [ ] 运行完整相关回归及现有 KernelOne ALL/catalog 门禁；不为保旧架构改回新流程，不为绿降低用户验收。

Run: `rtk proxy python -m pytest src/backend/polaris/tests/architecture/test_engineering_no_legacy_admission.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_loop.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_roles.py src/backend/polaris/cells/roles/runtime/tests/test_engineering_recovery.py -q`

Run（cwd=`src/backend`）：`rtk proxy python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

Run（cwd=`src/backend`）：`rtk proxy python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

Expected: 新生产调用图没有旧准入；历史仍可读；没有无限 dual engine/fallback。只有批次 8 完成，才称重构完成。

## C. 每批质量检查与提交范围

每批修改后，在实际变更的 Python 路径运行 Ruff、format、mypy 和对应 pytest；前端运行相关 Vitest、typecheck，并在批次 7 检查真实浏览器。路径由该批明确 Files 清单及 diff 确定，不扩大到无关 dirty work。

提交只包含该批已验证文件；禁止 `git add .`。旧接口保留与删除以真实消费者清单为依据，不按大文件名批量删除。每批独立可运行，不让八批一起堆到最后才首次执行。

## D. 回退与完成条件

回退新建入口只能阻止新任务，不能把已有新 run 转给旧引擎。代码/服务切换前检查当前实例、运行版本和在途进程；未知效果先对账，不能用 git checkout、重启或重复命令代替效果恢复。

完成后必须同时交付：可运行的新默认循环、真实三角色协作、工具权限与取消恢复、真实验证/浏览器证据、相同任务比较、旧生产准入删除、规范/图谱同步。只有源码、组件测试或文档完成时，结论仍局限于对应范围。

## E. 方案编写时的交付检查（历史）

本次只进行了只读代码定位、历史证据复核、方案编写和独立设计审阅。上述新增文件、接口、测试与命令均为实施目标，未冒充已经存在或执行。没有运行新的 provider、Factory/Bench、产品测试或服务重启。

## F. 实施检查点

2026-10-20：第 1 批技术组件完成。主 Agent 对最终切片运行 89 项检查通过；独立审查另选取 50 个不同用例及真实效果/取消探针，11 条源码/测试 SHA 一致、未关闭发现为零。审查曾复现统一解析入口的协议重复解析和不同 ID 等参去重，两项均在 R2 修复复验。

组合验证使用合成 loopback provider，经真实 HTTP/原生 codec 返回调用，再执行真实 bwrap 隔离命令、KFS fsync 日志冷读并回送实际工具结果。当前只证明 Linux/WSL2 的这组技术能力；没有远端模型、工程主循环、真进程重启恢复或 L1–L12 Bench 结论。原 R13 及更早 FAILED/UNKNOWN 保留。

证据保存在独立 worktree 的 `.superpowers/sdd/2026-10-20-polaris-role-agent-refactor/`：`task1-native/`（含 R2）、`task1-sandbox/`、`task1-root-all-v2/`、`task1-independent-review/`。第 2 批从此技术切片继续，不能恢复已归档的旧内核包装原型。

2026-10-20 实施补充：第 1 批首次完整底座检查暴露 4 个旧流适配器兼容失败，已保留失败证据。R3 修复迭代结束与真实协议终态的适用边界后，冻结提交 `84474677f` 在独立 checkout 完整检查通过（368 passed、1 skipped）；没有修改旧测试的验收断言。第 2 批工程循环、单份 SQLite 与公开服务处于独立审查和修复中；尚无真实模型、产品运行或 Bench 交付结论。证据：`.superpowers/sdd/2026-10-20-polaris-role-agent-refactor/task1-release-all-r3/`。

2026-10-20：第 2 批组件切片完成。新 Director 在合成模型配置和真实 HTTP 下读写临时工程，实际运行失败测试、接收结果、继续修复并得到退出 0；模型输入没有旧合同字段。单份 SQLite、真实 flock、显式空权限、原生调用身份、公开默认客户端及禁用旧内核后的实际执行均已验证。主 Agent 最终 98 项通过；独立审查两项 P1 经红绿修复后，最终 29 项通过、16 个源码 hash 一致、未关闭发现 0。资源清理未知时保留写锁；工具效果缺少耐久观察时保留 interrupted/unknown，不重放。证据：`task2-root-final/`、`task2-independent-review/`、`task2-loop/r1/`、`task2-loop/r2/`。尚不包含真实模型、正式验证、真进程冷恢复、产品入口、UI 或 Bench 交付。

2026-10-20：第 3 批组件完成。直接任务只运行 Director；完整协作真实执行 PM、CE、Director，咨询一层返回且只读，普通文本建议进入同一任务，失败检查继续由 Director 处理。角色工具 schema 从现有 ToolSpecRegistry 派生；显式空权限保留；冷接续与 resume 共用当前权限校验。主 Agent 最终 135 项通过；独立审查 44 项及专用 HTTP 证据 5 项通过，六文件 SHA 一致、未关闭发现 0。首次撤权重放失败保留。证据：`task3-root-r1-final/`、`task3-roles/r1/`、`task3-independent-review/`。正式验证、进程冷恢复、产品入口与 Bench 尚未完成。

2026-10-21：第 4 批组件完成，已在主线 `main` 验证。必需检查在源码只读、明确输出目录下真实执行；失败回到同一 Director，已有 PM／CE 建议复用。新 OS 进程恢复保留原 workflow／required_checks，先读取实际文件；原写入未知事实保留，精确当前状态回读单独记录，未重放旧写入；未知命令不自动重跑。原生历史中的无文本工具调用和空工具结果已保留到真实 provider HTTP 请求。主线最终 244 项通过，31 文件质量检查在合入时通过，修正后两文件复查通过；独立审查原 100 个独立用例与 34 份物理请求作为原候选证据保留，两项 P1 修正后另做 6 项限定复核通过。证据位于 `~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/`，检查点见 `progress.md` 与 `task4-independent-review/report-final.json`。新 HTTP／CLI／UI、远端模型和 L1–L12 尚待实施验收；未承诺任意故障或不可判外部效果可恢复。
