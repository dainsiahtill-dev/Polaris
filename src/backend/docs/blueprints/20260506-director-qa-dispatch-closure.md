# 2026-05-06 Director / QA Dispatch Closure Blueprint

## 1. 当前理解

Polaris Electron E2E 的目标链路是：

1. Court 生成产品文档和 PM 任务合同。
2. PM 将任务持久化到 `runtime/contracts/pm_tasks.contract.json`。
3. Director 基于 PM 合同执行任务，并产出可审计的任务状态。
4. QA 基于 Director 结果产出 `runtime/results/integration_qa.result.json`。
5. 前端和 Playwright 只读取 runtime 投影，不依赖单个进程的内存状态。

本轮失败不是单点异常，而是多个入口对“PM 合同是否已经进入执行闭环”的假设不一致：

- PM fallback 任务能落盘，但任务 scope 可退化成与真实工作区不匹配的合成 Python 路径。
- 前端 Director 工作区使用旧 snapshot seed 队列，并在启动 Director 后才同步任务。
- Director 允许无 command 的任务以成功结果结束，掩盖 PM 合同缺少可执行契约的问题。
- Workflow / post-dispatch QA 写入路径不统一，Playwright 等待的 canonical runtime 产物可能永远缺失。
- PM orchestration 在 dispatch 阶段失败时缺少足够稳定的失败闭环，容易留下 stale engine 状态。
- PMService 启动 PM CLI 时固定传入 `--timeout 0`，真实 LLM 卡住时 planning fallback 不会触发，`pm_tasks.contract.json` 无法落盘。
- execution_broker 日志 drain 有 30 秒硬截断，长时间 PM/Director 进程后续输出不会进入 `runtime/logs/*.process.log`，排障证据缺失。
- PM planning 的 explicit Ollama backend 边界返回 `OllamaResponse` 对象，但上层 pipeline 按 `str` 处理，错误响应也不会转成异常进入 fallback。
- PM fallback 虽然写入了手工 `quality_gate`，但 Workflow runtime 会重新运行 `evaluate_pm_task_quality()`；fallback 任务缺少依赖链时仍会被 runtime 判定为 `not execution-ready`。
- Workflow 父执行已经 `failed` 时，dispatch summary 仍可能只看子任务投影，把 Director 留在 `queued/deferred`，导致前端和 QA 等待错误状态。
- WorkflowEngine 对未预期异常缺少统一持久化证据，部分失败只留下 `{"status":"failed"}`，没有 error payload，排查成本过高。
- Embedded child workflow 输入和 KernelOne DAG 合同共用顶层 `tasks` 字段；Director child payload 会被误判为 DAG，并且 nested `TaskContract` 会被 `asdict()` 序列化成内部字段 `task_id`，导致 handler 读不到业务 `id`。

## 2. 证据清单

已阅读文件：

- `src/backend/AGENTS.md`
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
- `src/backend/polaris/delivery/cli/pm/tasks_utils.py`
- `src/backend/polaris/delivery/cli/pm/orchestration_engine.py`
- `src/backend/polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
- `src/backend/polaris/cells/director/execution/service.py`
- `src/backend/polaris/delivery/http/v2/director.py`
- `src/frontend/src/hooks/useProcessOperations.ts`
- `src/frontend/src/app/App.tsx`
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- `src/backend/polaris/cells/orchestration/pm_planning/service.py`
- `src/backend/polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py`
- `src/backend/polaris/cells/runtime/execution_broker/internal/service.py`
- `src/backend/polaris/kernelone/workflow/engine.py`
- `src/backend/polaris/kernelone/workflow/contracts.py`
- `src/backend/polaris/cells/orchestration/workflow_runtime/internal/embedded_api.py`
- `src/backend/polaris/cells/orchestration/workflow_runtime/internal/models.py`
- `src/backend/polaris/kernelone/workflow/tests/test_kernelone_c_fixes.py`
- `src/backend/polaris/tests/unit/cells/orchestration/workflow_runtime/internal/test_embedded_api.py`
- `src/backend/polaris/cells/orchestration/workflow_runtime/tests/test_workflow_runtime_models.py`
- `src/backend/polaris/tests/test_pm_zero_tasks_fallback.py`
- `src/backend/polaris/tests/test_orchestration_engine_integration_qa.py`

运行证据：

- Electron acceptance 已能进入 Court/PM/Director，但在等待 `runtime/results/integration_qa.result.json` 超时。
- 最新 runtime 下存在 `runtime/contracts/pm_tasks.contract.json`。
- 最新 runtime 下不存在 `runtime/results/integration_qa.result.json`。
- `runtime/state/dispatch/shangshuling.registry.json` 已存在，证明 dispatch 已开始但未形成 QA 闭环。
- `runtime/state/engine.status.json` 出现 `dispatching` / stale orphan 状态。
- PM fallback 任务在 TypeScript/Jest 工作区中生成了 `pyproject.toml`、`src/fastapi_entrypoint.py` 等不匹配路径。
- Electron acceptance 复测后，最新 runtime 下只有 `runtime/contracts/plan.md`，没有 `runtime/contracts/pm_tasks.contract.json`。
- 目标工作区 `.polaris/pm_data/state.json` 已初始化但 `total_tasks_created=0`，证明 PM 进程启动后没有完成规划落盘。
- `runtime/logs/pm.process.log` 长度为 0，和 execution_broker 日志 drain 固定 30 秒截止共同导致 PM 子进程无可审计输出。
- 最新 Electron runtime DB 中 `workflow_execution.status=failed`，但 Director 投影仍表现为 queued/deferred。
- 同一 workflow 的 `workflow_event` 只包含 `workflow_execution_finished`，`result_json={"status":"failed"}` 缺少具体异常。
- 一次复现显示 fallback 任务被 runtime PM gate 拒绝：`all tasks are low-action/generic and not execution-ready`。
- 本地复现显示 `WorkflowContract.from_payload(asdict(DirectorWorkflowInput(...tasks...)))` 抛出 `task_id_missing`。
- 本地复现显示 `_payload_from_value(DirectorWorkflowInput(...))` 现在必须输出 `tasks[0].id` 而不能泄漏 `tasks[0].task_id`。

尚未确认：

- Workflow runtime 在 Electron 子进程中是否存在额外事件循环生命周期问题。
- Electron 全链在本轮最小修复后是否还有新的 UI 层等待或速率限制问题。

## 3. 缺陷分析

### 缺陷 A：PM fallback 忽略实际 workspace 文件

触发条件：LLM provider 失败或 PM 返回空任务，requirements 中没有显式文件路径，但工作区已有真实文件。

根因：`build_requirements_fallback_payload()` 只从 requirements 提取路径，提取不到时直接按 tech stack 合成路径；`workspace_files` 只参与 tech stack 检测，没有参与任务文件候选选择。

影响：Director/QA 会审计错误路径，实际项目文件不进入任务合同，Electron E2E 的复杂项目验收不可信。

### 缺陷 B：QA 产物路径漂移

触发条件：Workflow 进入 post-dispatch QA 或 deferred QA 分支。

根因：post-dispatch helper 只写 `runs/<run_id>/qa/integration_qa.result.json`；acceptance 和 runtime 投影等待的是 `runtime/results/integration_qa.result.json`。

影响：真实 QA 可能已运行，但 Electron/Playwright 等待 canonical 产物时超时。

### 缺陷 C：Director 无 command 任务可假成功

触发条件：前端或 API 创建 Director 任务但未携带可执行 command。

根因：`DirectorService._run_command(None)` 返回 `success=True` 和 `output="No command"`。

影响：任务没有真实工具调用或执行证据，仍可能被统计为完成。

### 缺陷 D：前端 Director seed 存在竞态和旧 snapshot 依赖

触发条件：用户进入 Director 工作区后立即点击执行，或 PM snapshot 尚未同步到顶层 App state。

根因：`startDirectorCallback()` 先启动 Director 再 seed PM 任务；`App.tsx` 传入的是旧 `pmTasks`，不是进入工作区后刷新的 `progressPmTasks`；`DirectorWorkspace` fallback polling 把 `Response` 当数组使用。

影响：Director 可在空队列下启动并快速回到 idle，`/v2/director/tasks` 为空。

### 缺陷 E：PM planning timeout 被禁用

触发条件：Electron 使用真实 LLM 设置，模型不可达、响应慢或长时间生成。

根因：`PMService._build_command()` 固定传 `--timeout 0`；`run_pm_planning_iteration()` 会将该值规范化为 `state.timeout=0`，而 KernelOne timeout 语义中 `<=0` 表示禁用。PM LLM 调用因此可能等待到外部测试超时，无法走 invoke error fallback。

影响：PM 子进程启动但不产出 `pm_tasks.contract.json`，Court -> PM -> Director 全链断在规划阶段。

### 缺陷 F：执行代理日志 drain 30 秒硬截断

触发条件：PM/Director 子进程运行超过 30 秒，尤其是 LLM 等待或长任务执行。

根因：`ExecutionBrokerService._drain_stream_to_log()` 设置固定 `deadline = loop.time() + 30.0`。进程仍在运行时 drain 任务会退出并关闭日志文件。

影响：后续 stdout/stderr 丢失，`pm.process.log` 不能作为事故排查证据。

### 缺陷 G：PM Ollama backend 返回值契约不一致

触发条件：PM CLI 显式使用 `--pm-backend ollama`，或者未来 backend 解析落到 explicit Ollama。

根因：`CellPmInvokePort.invoke()` 声明返回 `str`，但 Ollama 分支直接返回 `invoke_ollama()` 的 `OllamaResponse`。`OllamaResponse` 的错误通过 metadata 表达，不抛异常；上层 `run_pm_planning_iteration()` 只有异常路径会立刻触发 planning fallback。

影响：PM 规划可能在 JSON 解析层收到非字符串对象，或对空响应进行多轮质量重试，延迟合同落盘。

### 缺陷 H：PM fallback 合同的手工质量声明和 runtime gate 不一致

触发条件：PM fallback 生成 2 个以上任务，但任务之间没有 `depends_on` / `dependencies`，然后进入 Workflow runtime 的 `validate_task_contract`。

根因：`build_requirements_fallback_payload()` 写入了 `quality_gate.critical_issues=0` 和 `score=85`，但真正执行链路会重新调用 `evaluate_pm_task_quality()`。该 gate 对多任务合同要求至少存在依赖链，否则认为任务不可执行。

影响：PM fallback 合同看起来“通过”，但 Director workflow 在 runtime 中失败，Electron 前端无法进入可靠的 Director/QA 闭环。

### 缺陷 I：Workflow 父失败被投影成 Director queued/deferred

触发条件：Workflow 父执行 `status=failed`，但子任务投影中仍有 pending/未决任务，或任务摘要来自旧 dispatch payload。

根因：`_summarize_workflow_execution()` 只聚合子任务状态，没有把父 workflow terminal failure 作为强事实传播到未决任务和 Director 状态决策。

影响：真实执行已失败，但 UI/API 仍显示 queued/deferred；QA 产物会停在 `workflow_execution_incomplete`，排障方向被误导。

### 缺陷 J：WorkflowEngine 未预期异常缺少可审计 error payload

触发条件：workflow handler 抛出 `RuntimeError` / `ValueError` 之外的异常，例如 TypeError、第三方库异常或取消路径。

根因：`WorkflowEngine._run_workflow()` 的异常处理范围过窄，最终事件和 `result_json` 没有保证包含 `state.last_error`。

影响：runtime DB 只能看到 workflow failed，缺少失败原因；同类生产事故只能靠外围日志猜测，不能从状态事实还原。

### 缺陷 K：Child workflow 输入合同和 DAG 合同字段冲突

触发条件：PM workflow 通过 `execute_child_workflow(DirectorWorkflow.run, DirectorWorkflowInput(...tasks...))` 提交 Director child workflow。

根因：`execute_child_workflow()` 把 dataclass input 直接转成顶层 dict；`WorkflowContract.from_payload()` 看到顶层 `tasks` 就按 DAG contract 解析。但 `DirectorWorkflowInput.tasks` 是业务任务合同，不是 KernelOne `TaskSpec`。同时 `asdict()` 会把 nested `TaskContract` 序列化成内部字段 `task_id`，而 `TaskContract.from_mapping()` 读取的是外部合同键 `id`。

影响：Director child workflow 可能在提交阶段被判定为 invalid DAG contract，或进入 handler 后拿到空任务，导致 Director/QA 无法闭环。

## 4. 修复方案

最小修改点：

1. PM fallback 使用真实 `workspace_files` 生成任务 `scope_paths` / `target_files`，只有真实文件不可用时才使用合成路径。
2. post-dispatch QA 和 deferred QA 同时写入 canonical runtime artifact。
3. Director 无 command 执行改为失败，并返回明确错误。
4. 前端启动 Director 前先从 PM 任务合同 seed 队列；必要时读取 `/state/snapshot` 作为 fresh fallback。
5. `DirectorWorkspace` fallback polling 正确解析 JSON。
6. PM workflow dispatch 的 post-dispatch QA 优先使用 workflow summary tasks，而不是旧的 todo dispatch payload。
7. PMService 使用独立 PM planning SLA：优先 `KERNELONE_PM_PLANNING_TIMEOUT_SECONDS`，其次正数 `settings.timeout`，否则使用 `min(settings.llm.timeout, 60)`；不再传 `--timeout 0`。
8. execution_broker 日志 drain 默认跟随子进程生命周期，仅保留显式 env 上限用于诊断测试。
9. `CellPmInvokePort` 将 Ollama response 规范化成字符串，并把 metadata error 提升为 `RuntimeError`，复用已有 PM invoke fallback。
10. PM fallback 合同为多任务自动补齐最小顺序依赖链，保证手工 quality gate 与 runtime quality gate 一致。
11. Workflow 父执行进入 terminal failure 时，将未决子任务投影为 failed，并让 Director 状态直接暴露 failed。
12. WorkflowEngine 对取消和未预期异常统一写入 `last_error`、`result_json.error` 和 `workflow_execution_finished.payload.error`。
13. Embedded child workflow payload 增加内部 `_workflow_contract_mode=legacy` marker，避免业务 `tasks` 被 DAG parser 抢占。
14. Workflow runtime input dataclass 增加显式 `to_dict()`，`embedded_api` 序列化时优先使用 `to_dict()`，保证 nested `TaskContract.id` 合同不退化成内部 `task_id`。

不修改内容：

- 不新增 Cell。
- 不引入新框架。
- 不改变 PM/Director/QA HTTP 公共接口形状。
- 不把目标项目业务代码写入 Polaris 主仓。
- 不改变全局 `normalize_timeout_seconds()` 的 `<=0` 禁用语义。
- 不把 PM 进程总生命周期 timeout 和 PM 单次 LLM planning timeout 混为同一个设置。
- 不放宽 `evaluate_pm_task_quality()` 的质量门禁，只让 fallback 合同满足现有门禁。
- 不改变 Workflow runtime 的公开表结构，只增强失败状态投影和事件 payload。
- 不移除 KernelOne 顶层 `tasks` DAG 兼容路径；只有 embedded child workflow 内部 marker 明确要求 legacy 时才跳过 DAG parser。

兼容性说明：

- PM 合同 schema 不变，只提升 fallback 字段质量。
- QA canonical artifact 是新增同步写入，不移除 per-run artifact。
- Director 无 command 从假成功变为失败，这是 bug fix；对真实带 command 的任务无影响。
- PM CLI 仍保留 `--timeout` 参数形状；只改变 Electron/服务启动时传入的默认值。
- execution_broker 日志 drain 的 env 上限是新增可选行为，默认行为只增加证据保留能力。
- PM explicit Ollama 成功输出仍返回同一文本；只改变错误响应和对象泄漏行为。
- Fallback 新增依赖链只影响 fallback 合同，用户或 LLM 明确提供的依赖不被覆盖。
- Director 父 workflow 失败投影为 failed 是状态修正，不改变成功路径。
- Workflow final event 仅新增 error 字段，不改变已有 status 字段。
- Child workflow marker 是内部字段，handler input parser 会忽略它；成功路径输入语义不变。
- `to_dict()` 只把已有 dataclass 字段按现有外部合同键稳定化，不新增业务字段。

## 5. 测试计划

Happy Path：

- TypeScript workspace fallback 任务应引用真实 `package.json`、`src/**`、`tests/**` 文件。
- post-dispatch QA 应同时写 per-run 和 runtime canonical artifact。

Edge Cases：

- requirements 没有文件路径但 workspace_files 可用。
- deferred QA 分支无即时 Director 完成结果时仍写 canonical pending artifact。

Exception Cases：

- Director command 缺失时返回失败而非成功。
- frontend fresh snapshot 请求失败时保持原任务列表，不阻塞已有任务 seed。
- PM LLM 超时后应进入规划失败 fallback，而不是让 PM 子进程无界等待。
- execution_broker 默认日志 drain 不应有 30 秒 wall-clock cutoff。
- explicit Ollama backend 返回 `OllamaResponse` 时应交付纯文本；metadata error 应进入异常路径。
- fallback 多任务合同应通过 `evaluate_pm_task_quality()` 的 runtime gate。
- workflow 父执行失败时，Director 结果应落为 failed 而不是 queued/deferred。
- workflow handler 抛出未预期异常时，runtime DB 必须持久化 error。
- child workflow payload 顶层有业务 `tasks` 时必须走 legacy handler，不得被 DAG parser 拒绝。
- nested `TaskContract` 在 Director handoff 中必须保留外部 `id` 键。

Regression Cases：

- Electron acceptance 不再因缺少 `runtime/results/integration_qa.result.json` 永久等待。
- `/v2/director/tasks` 在 PM 合同存在时有可审计 task rows。
- Electron acceptance 不再因真实 LLM 长时间无响应而缺失 `runtime/contracts/pm_tasks.contract.json`。
- PM/Director 长运行子进程日志不再在 30 秒后静默截断。
- PM fallback 不再在 Workflow `validate_task_contract` 阶段失败。
- workflow failed 不再被 Electron/QA 误判为仍在等待。
- WorkflowEngine failed 事件可直接给出异常原因。
- PM -> Director -> DirectorTask child handoff 不再因字段冲突丢任务。

## 6. 回滚方案

若本次修复引入异常，可通过 Git 回滚以下文件：

- `src/backend/polaris/delivery/cli/pm/tasks_utils.py`
- `src/backend/polaris/delivery/cli/pm/orchestration_engine.py`
- `src/backend/polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
- `src/backend/polaris/cells/director/execution/service.py`
- `src/frontend/src/hooks/useProcessOperations.ts`
- `src/frontend/src/app/App.tsx`
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- `src/backend/polaris/cells/orchestration/pm_planning/service.py`
- `src/backend/polaris/cells/runtime/execution_broker/internal/service.py`
- `src/backend/polaris/kernelone/workflow/engine.py`
- `src/backend/polaris/kernelone/workflow/contracts.py`
- `src/backend/polaris/cells/orchestration/workflow_runtime/internal/embedded_api.py`
- `src/backend/polaris/cells/orchestration/workflow_runtime/internal/models.py`
- 对应新增/修改测试文件

重点复查：

- PM fallback task quality score 和 critical issue 计数。
- Director task completion semantics。
- runtime canonical QA artifact freshness。
- Electron acceptance full-chain audit JSON。
- `runtime/logs/pm.process.log` 是否持续记录 PM 子进程后续输出。
- Workflow runtime DB 的 failed row 是否包含可审计 error。
- Director/QA 是否把父 workflow failed 作为终态处理。
- Child workflow payload 是否始终保留外部任务合同键。
