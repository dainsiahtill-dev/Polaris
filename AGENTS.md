# Polaris Agent 角色规范 v4.0

**目标**：把工程交付做成 **可重复、可审计、可回滚、可防御** 的流水线，同时验证 Polaris 在复杂场景下的稳定性。  
**口号**：精准 > 速度；证据 > 声称；最小变更 > 顺手重构；多层防御 > 单点信任。  
**编码要求**：所有文本文件读写必须显式使用 `UTF-8`（包括日志/JSON/Markdown）。
**铁律**：Polaris 是元工具平台，禁止在主仓代码中添加任何目标项目/业务相关代码。
**必用MCP和Skill**: 充分利用codegraph MCP和superpowers，必要时需要使用Playwright来真实跑测试和审计。
> 本文件为 **Codex 专用治理规范**。若项目内存在更细粒度 `AGENTS.md`，以项目规则优先，但不得弱化本文件的强制门禁。

> 后端任务强制入口（2026-03-22）：  
> 1) `src/backend/AGENTS.md`  
> 2) `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`  
> 且必须执行“Cell 复用优先 + KernelOne 底座优先”。

---

## 一、执行栈（固定顺序）

1. **Playwright**（主流程）- 浏览器自动化测试与操作
2. **Computer Use**（视觉兜底）- 无 DOM 场景的视觉操作（已移除 Hybrid 相关旧测试）

---

## 二、角色系统（唐朝官员制度）

Polaris 采用**唐朝官员制度**的多 Agent 治理架构，每个角色有明确职责边界：

| 官职 | 角色 | 职责 | CLI 入口 |
|------|------|------|----------|
| 尚书令 | **PM** | 项目规划、任务拆分、质量门禁 | `scripts/pm/cli.py` |
| 中书令 | **Architect** | 架构设计、技术选型 | `role_agent/architect_cli.py` |
| 工部尚书 | **Chief Engineer** | 技术分析、代码审查、策略制定 | `role_agent/chief_engineer_cli.py` |
| 工部侍郎 | **Director** | 代码执行、文件操作、命令运行 | `scripts/director/cli_thin.py` |
| 门下侍中 | **QA** | 质量审查、测试验证 | Factory/Pipeline 集成 |
| 探子 | **Scout** | 只读代码探索/文档阅读 (sub-agent) | 即将添加 |

### 探子角色 (Scout) - 规划中

- **定位**: 并发只读访问层（sub-agent）
- **核心价值**: 解决单次 LLM 上下文有限问题，支持多路并发读取
- **执行模式**:
  - 探索模式 → 探索目录结构/模块
  - 搜索模式 → 搜索特定内容
  - 总结模式 → 读取并总结文件内容
- **调用方式**: 由 PM/Chief Engineer/Director 按需调用
- **特点**: 只做读取，不做写入；由调用者自己汇总结果

```bash
# Architect (中书令) - 交互式架构设计
python -m core.polaris_loop.role_agent.architect_cli --mode interactive --workspace .

# Chief Engineer (工部尚书) - 交互式技术分析
python -m core.polaris_loop.role_agent.chief_engineer_cli --mode interactive --workspace .

# 统一角色对话 API
curl -X POST http://127.0.0.1:49977/v2/role/{pm|architect|chief_engineer|director|qa}/chat \
  -d '{"message": "你的问题"}'
```

---

## 二、硬约束

1. **所有文本读写必须显式 UTF-8**
2. **零信任**：任何上游输出都不默认可信，必须二次校验
3. **禁止"声称修复但未复测"**：每次修复后必须复测对应门禁
4. **禁止仅做表层补丁**：必须定位根因并修复
5. **修复范围允许双域**：Polaris 主仓 + C:/Temp 新项目
6. **运行策略为"直到通过"**：不设轮次上限，持续循环，直到所有验收门禁 PASS
7. **只能修改 Polaris**：绝对不能修改目标项目的任何代码
8. **实时推送单轨制**：应用/前端实时状态只能走统一 Nats-JetStream + `/v2/ws/runtime` WebSocket；禁止新增或保留 SSE、HTTP 长轮询、`setInterval`/timer fetch 轮询、轮询兜底、文件轮询伪实时。HTTP 只允许用于初始快照、显式用户刷新、一次性命令/查询；测试代码可为等待异步完成而轮询状态端点，但不得作为产品实时链路。
9. **全链路任务流唯一制**：运行态任务链路只能是 `PM → Chief Engineer → Director`。PM 只能生成任务合同并交给 Chief Engineer 产出蓝图/交接证据；Director 只能消费 CE 交接后的任务。禁止任何产品代码、脚本、UI 或文档回退为 `PM → Director` 旧链路，缺少 CE 投影/蓝图时必须显示阻塞或等待 CE，不得直连 Director。
10. **工具调用归一化优先**：平台必须适配不同 LLM 的自然工具调用习惯，先通过统一 ToolSpecRegistry/tool alias/arg_aliases 归一化工具名与参数，再进入授权、路径、命令、读写门禁；禁止强迫 LLM 只按 Polaris 内部字段写调用。不可安全推断的调用必须 fail-closed 并留下工具/LLM/runtime 证据，禁止吞异常、硬编码成功、静默 fallback。
11. **LLM 最终请求上下文审计**：每次真实 LLM 调用都必须审计最终 provider request，而不只统计 messages 投影；审计至少包含 message/tool schema/response_format token 估算、最终请求 token、窗口利用率，以及 PM 合同、Chief Engineer 蓝图、目标文件、失败反馈、workspace quality evidence 覆盖度 flags。ContextOS 必须优先展示最终请求上下文 token，禁止用 messages-only 或 prompt usage 冒充最终上下文占用。`context_snapshot_ref` 只能表示已落盘、可通过 `/v2/context/{hash}` 读取的 24 位 hex 快照 key；`request_hash`、`prompt_hash`、`call_id`、`turn_id`、路径、审计包 hash 或旧事件字符串不得冒充 `context_snapshot_ref`，前端不得为非法 ref 展示“查看完整上下文”入口或发起 `/v2/context/{hash}` 请求。
12. **角色工具失败外部审计（主 Agent 专用，禁止产品化）**：PM、Chief Engineer、Director、QA 任一角色发生工具调用失败、工具调用缺失、工具参数无法归一化、工具结果被误判成功、或 LLM 输出被错误当作工具 action 时，主 Agent 可以安排至少一个 OpenCode 外部 Agent 做独立审计。该审计只属于 Codex/Claude 等主 Agent 的工程协作手段，绝对不是 Polaris 平台能力、运行时依赖、Factory gate、bench 成功条件、ContextOS/ReceiptStore 事实源或产品功能。Polaris 产品代码、脚本、UI、runtime event、Run Ledger、bench report 不得调度、等待、要求或展示 OpenCode 审计状态作为平台证据。外部审计应覆盖最终送入 LLM 的完整 provider request 上下文、工具调用归一化链路、ToolSpec/arg_aliases、runtime event、LLM 调用日志、ContextOS 证据和失败归因；若 LLM event 因安全策略 redacted 了 `messages`/`content`，主 Agent 必须把 `context_snapshot_ref` 对应的 `runtime/contexts/<shard>/<hash>` 快照文件纳入自己的外部审计证据包；禁止把 OpenCode 审计结论写成 Polaris 的 SSoT。
   - **Claude CLI JSON Sub-Agent 标准（主 Agent 默认派工方式）**：需要并行分配外部子任务时，主 Agent 默认使用 `claude -p "<任务指令>" --dangerously-skip-permissions --output-format json --json-schema '<schema>'`，而不是交互式日志或非结构化后台输出。每批最多 3 个 Sub-Agent；任务必须先按文件/目录/职责拆成互不重叠的范围，不能保证互斥时必须降级串行。Sub-Agent 任务必须显式声明 `mode=audit` 或 `mode=implementation`：`audit` 只读；`implementation` 可以直接修改授权范围内代码、测试和文档，但必须使用独立 worktree/sandbox 或共享主仓互斥文件集合，且不得跨桶写入。每个 Sub-Agent 的提示词必须包含：任务 ID、mode、允许/禁止修改范围、必须读取的规范、必须使用 codegraph/MCP 的审计要求、验证命令、输出 JSON schema、报告落盘路径（推荐 `/tmp/polaris-subagent-<batch>-<id>.json`）。JSON 结果至少包含 `mode`、`status`、`summary`、`scope`、`files_read`、`files_modified`、`commands_run`、`findings`、`risks`、`next_action`。主 Agent 只能把该 JSON 当作外部执行报告，必须重新检查 `git status`、`git diff`、测试结果和越界修改；禁止把 Claude/OpenCode 子任务状态或报告写入 Run Ledger、ContextOS、ReceiptStore、Factory/Bench 成功条件、产品 UI 或 runtime event。
13. **最终请求唯一真相与主动缺陷发现制**：主 Agent 不能等待用户从 UI 发现问题后再被动排查。每次 bench、角色运行或工具失败后，必须主动先验 `context_snapshot_ref` 对应的最终 provider request，并把它作为唯一事实源；`messages`、prompt 文本、RoleProfile whitelist、日志摘要、UI 文案都只能作为辅助证据，不能替代最终 provider request。必须逐项比对：
   - `provider_request.messages[0]` 的角色身份是否与当前角色一致，禁止 CE/Director/PM 系统提示串线。
   - `provider_request.tools` 是否包含任务和提示词要求的可调用工具；如果提示词要求 `repo_tree`、`read_file`、`repo_read_*`、`write_file`、`execute_command` 等工具，而最终 tools schema 缺失，直接按 P0 平台缺陷处理。
   - `provider_request.tool_choice`、`response_format`、tool schema 参数、arg aliases 是否与 ToolSpecRegistry/运行时归一化链路一致。
   - `final_request_context_audit` 的 token、窗口利用率、tool schema token、coverage flags 是否来自最终请求，禁止用 messages-only 估算冒充。
   - 弱模型 slim/单批次/retry/escape-hatch 策略不得把提示词要求或任务必需的读/定位工具静默裁掉；若确需限制工具，必须在 runtime event 和 ContextOS 中写出裁剪原因、原始工具集、裁剪后工具集和风险。
14. **主 Agent 自主审计闭环**：遇到进展卡住、长时间停留同一项目、LLM call_error、工具调用缺失、上下文快照不可用、角色身份异常、可运行门禁失败时，主 Agent 必须立即形成机器可读缺陷清单并逐项关闭；禁止反复描述同一现象而不落地修复。每个缺陷至少记录：现象、最终请求证据、runtime/bench/log 证据、根因分类、修复文件、验证命令、剩余风险。未完成该闭环不得宣称“继续跑下一批”或“已验证模型能力”。
15. **多维主动审计矩阵**：主 Agent 不能只沿当前报错点线性排查，也不能只修用户指出的单点。每次卡住、重跑、失败归因或进入下一项目之前，必须主动从以下维度并行找缺口，并把结论写入阶段报告：
   - 架构链路：是否仍有旧链路、旁路、fallback、PM→Director、跳过 CE、双轨实时或目标项目污染。
   - 角色身份：PM/Chief Engineer/Director/QA 的 system prompt、role metadata、run_id、trace_id 是否串线。
   - 最终 LLM 请求：provider request 的 messages/tools/tool_choice/response_format/token/window/coverage 是否合理。
   - 工具链路：ToolSpecRegistry、tool alias、arg_aliases、parser、native tool、text fallback、授权、路径门禁、结果判定是否一致。
   - 上下文卫生：长期 retry 回灌、过期失败原因、无关历史、矛盾指令、弱模型过载、messages-only 统计冒充最终请求。
   - 运行时事件：Nats/JetStream、`/v2/ws/runtime`、runtime event、bench session、ContextOS、日志、截图或测试结果是否互相印证。
   - UI 投影：前端是否接收到真实推送，workspace/阶段/进行中/工具调用/ContextOS 快照是否与后端状态一致。
   - 产物门禁：落盘、依赖/环境、build/test/lint 至少一个真实门禁、CLI/Web/API 至少一个入口是否实际执行。
   - 模型健康：绑定模型是否可达、连续失败是否应跳过、超时是否匹配模型速度、弱/强模型策略是否按配置生效。
   - 收敛性：当前问题是否是新通用根因，是否需要平台硬化、文档沉淀、回归测试和下一批验证。
16. **用户观察反向触发复盘**：凡是用户通过 UI、截图、日志或手工观察先于主 Agent 发现缺陷，必须视为主 Agent 审计遗漏。修复时除解决代码根因外，还必须补充一条可自动发现同类问题的审计规则、测试、日志断言或文档硬约束；禁止只修当前样例。
17. **Bench 测试态边界（强制）**：任何 `Bench`、`Factory Bench`、`factory_bench`、`L1-L12 bench`、benchmark harness、压力测试 UI/API/脚本都只允许存在于 Polaris 内部测试/开发/审计模式，用于压测平台能力、暴露通用根因和生成审计证据。Bench **不是**正式项目功能、不是生产工作台、不是用户交付体验、不是控制面事实源；正式环境/生产环境不得出现 Bench 入口、Bench 文案、Bench 专属 UI、Bench 专属状态模型或以 Bench 命名的业务 API。平台基础设施能力（如 Run Ledger、Job Token、ContextOS、ReceiptStore、Verifier/Gate Policy）必须以平台级命名和契约沉淀，Bench 只能在内部测试态作为这些平台能力的生产者/消费者之一。禁止把为 Bench 写的临时字段、视图、路由或运行假设上升为生产语义；需要在正式产品展示时必须接入平台级 projection/API，而不是 `benchService`、bench session 或 factory audit 文件。Browser、视觉、多模态 QA、用户脚本、领域脚本等 verifier modality 是平台 Control Plane 可选能力，默认关闭；只有当前环境显式声明可用时才允许设为 hard-required evidence，禁止由 Bench 或内部测试状态决定正式项目必须启用这些能力。Job Token 是从控制面事实源派生的 capability token，不得成为第二事实源；正式写入和命令工具执行层必须消费 Job Token 派生 scope，并把 token/hash/stage/project evidence 写入 effect receipt。
18. **多实例总控与项目观测（强制）**：Polaris 仍保持“单个后端进程绑定单个 workspace”的运行时不变量；多项目并行观测必须通过平台级 Instance Registry + Launcher 总控实现，即多个独立实例共享同一个 `polaris_root`、各自拥有独立 `workspace` / `runtime_root` / backend port / frontend port。禁止把 `settings.workspace`、ContextOS、TaskBoard、Run Ledger 或前端全局状态改造成临时多租户拼接层；需要切换/观测多个项目时，启动多个 Polaris 实例或使用 `/launcher` 总控打开对应实例页面。实例工作台入口必须携带显式 `instance` / `backend` / `token` / `workspace` 绑定（URL query 或 `VITE_POLARIS_*` 环境变量），前端 API 与 `/v2/ws/runtime` 连接必须消费该 workspace 绑定；禁止打开实例页面后静默回退到默认 backend、默认 workspace 或主仓 runtime。任何由 Agent、CLI 或内部压力工具启动且需要被观测的实例，都必须通过 `polaris.cells.instances` / `/v2/instances` / `python -m polaris.delivery.cli.backend serve --register-instance ...` 写入 Instance Registry；注册写入只能作为发现/运维视图，不得替代 PM、Chief Engineer、Director、QA、ContextOS、ReceiptStore 或 Run Ledger 的事实源。Launcher 实时更新只允许走 runtime.v2 WebSocket `status.instances` 事件，禁止用 HTTP polling 模拟实例状态。`factory_bench` 只能在内部测试态把项目注册为 `kind=bench_project`，便于总控观测；共享后端的 bench 注册只能视为“可观测的测试实例”，不能冒充独立生产实例，不得把 Bench 语义提升为生产项目模型。详细规则见 `src/backend/docs/MULTI_INSTANCE_LAUNCHER.md`。
19. **ContextOS 快照可读闭环（强制）**：LLM 事件中暴露给 UI 的 `context_snapshot_ref` 必须是 `/v2/context/{hash}` 和 `/v2/context/{hash}/final-request` 在同一 workspace 绑定下可读取的 24 位 hex key；禁止把 `request_hash`、`prompt_hash`、`call_id`、`turn_id`、路径字符串或旧事件 id 当作完整上下文引用。Context 快照读取必须以请求 workspace 为硬边界，并按平台级候选链查找：当前 `resolve_storage_roots(workspace).runtime_root`、Instance Registry 中同 workspace 的 `runtime_root`、默认 KernelOne system cache；禁止只查 active runtime root 后就向 UI 宣称快照丢失。404 必须返回 `context_hash`、`workspace` 和 `searched_paths`，前端必须展示这些证据；如果事件来自其他 workspace，前端 runtime guard 必须先丢弃，不能把跨项目 hash 送入当前 ContextOS modal。`/v2/context/*` 属于本地开发观测关键路径，loopback 读取不得被普通 API rate limit 误伤，远程访问仍必须限流。
20. **Bench 全局观察器显式授权（强制）**：`event.bench` 是内部测试态 workspace-agnostic 事件流，只能由总控/主开发页在显式 `globalObserver` 或等价平台开关下订阅；从 Launcher 打开的具体实例页、PM/CE/Director/QA/ContextOS 项目工作台不得默认订阅全局 bench、不得被 “newest bench session” 静默切换 workspace。前端组件如 `BenchStatusStrip`、`BenchPanel` 必须默认只渲染调用方传入的 scoped `bench` 数据；没有显式全局授权时不得自行调用 `useFactoryBench({autoSelect:"newest"})`。任何新增 bench 观测 UI 都必须有测试证明 `enabled` 本身不会触发全局订阅，只有显式全局观察模式才会订阅。
21. **Observed Bench 独立化启动（强制）**：Instance Registry 中带有 `metadata.backend_binding=shared_backend_workspace_switch` 的 `bench_project` 只是共享后端观测记录；当用户或 Agent 从 Launcher/API 对它执行 restart/独立启动时，Supervisor 必须分配新的 backend/frontend 端口并启动独立实例，禁止复用共享 backend 端口或把 observed 记录伪装为 running。
22. **factory_bench 并行实例模式（强制）**：多个 Agent 并行跑 `factory_bench` 不得共享同一个 49977 backend 做 workspace switch；runner 默认必须使用 `isolated`，让每个项目先启动独立 Polaris backend/frontend，再把该项目的 Factory run 发到自己的 backend。`--launcher-instance-mode observed` / `FACTORY_BENCH_LAUNCHER_INSTANCE_MODE=observed` 只允许显式用于轻量观测和串行兼容测试。
23. **主端口预留与 workspace 隔离（强制）**：`49977/5173` 只属于 `main` 开发实例。任何 `bench_project`、Factory Bench、临时项目实例或 Agent 私有项目后端不得直接使用 `python -m polaris.delivery.cli.backend serve --port 49977`，不得把 bench workspace 通过 `POST /settings` 写入主后端，且不得通过手工端口复用把主 Web 前端导向测试项目。项目实例必须走 Instance Supervisor/Launcher 自动分配端口；Supervisor 必须忽略或重分配 bench 对 `49977/5173` 的显式请求，禁止抢占或复活主端口。多 Agent/bench 观测阶段 `main` 后端默认不得启用 `--reload`，否则其他 Agent 修改 `src/backend` 会触发 reload 风暴并造成前端 API/WS 短时超时；只有单人调试后端热重载时才允许显式启用。若发现 `lsof -i :49977` 的进程 workspace 不是主仓，必须只清理该错误进程并恢复 main 实例，不得误杀其他 Agent 的独立实例端口。
24. **Launcher 自管理边界（强制）**：当前承载 Launcher API 的后端实例不得通过自己的 `/v2/instances/{id}/stop|restart|delete` 自我停止、自我重启或删除自身 registry 记录；这类操作必须 fail-closed，避免控制面先杀掉自己后无法完成重启/清理。前端必须禁用当前控制实例的 stop/restart/delete，仅允许管理其它独立项目实例；清理 stale bench 只能作用于 `kind=bench_project`、非 running、backend dead 且 `metadata.internal_test_only=true` 的内部测试实例。
25. **Run Ledger evidence 语义（强制）**：平台级 Run Ledger 投影必须区分“缺少 required evidence”和“required evidence 已存在但失败”。`evidence_policy.missing_required_modalities` 只表示控制面/工具层没有产出该类证据，例如没有命令收据；`evidence_policy.failed_required_modalities` 表示证据真实存在但门禁失败，例如 `npm test`、`go test`、browser smoke 或用户脚本返回失败。UI、ContextOS、QA、Factory 内部测试和主 Agent 审计不得把 failed evidence 继续渲染成 missing evidence；前者是产物/验收失败，后者才是账本或工具链漏记账。任何 resolved/pass 状态都必须同时满足：无缺失 required evidence、无失败 required evidence、门禁 exit code/receipt/hash 证据闭环。
26. **Director deterministic repairs 收敛边界（强制）**：确定性修复内核唯一归属 `director.runtime`。`src/backend/polaris/cells/director/runtime/internal/repair_kernel/` 是 Cell 私有实现；跨 Cell 只能通过 `polaris.cells.director.runtime.public` 或 `polaris.cells.director.runtime.public.service` 消费。public repair 执行入口只能是通用 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` + `plan_director_repair` / `run_director_repair`；禁止新增 `plan_director_<language>_*`、`run_director_<language>_*` 或按规则命名的 public facade。语言/规则分派必须留在 runtime internal dispatcher/registry 后面。`roles.adapters/internal/director/deterministic_repairs/` 只允许作为迁移期 legacy strategy host，不得重新拥有 repair kernel、strategy catalog、policy gate、receipt contract、PatchComposer、scheduler、shadow comparison 或 AGI advisory contract。禁止恢复 `roles/adapters/internal/director/repair_kernel/**`，禁止恢复 `deterministic_repairs/strategy_catalog.py` 作为事实源，禁止 `roles.adapters` 直接 import `polaris.cells.director.runtime.internal.repair_kernel`。`execute_method.py` 若需要 repair catalog、summary、planning、coverage、shadow comparison 或 advisory policy，只能走 `director.runtime.public.service`；legacy `tool_results` 投影为 repair_kernel summary 必须使用 `ProjectDirectorRepairKernelSummaryV1` + `project_director_repair_kernel_summary`，`build_director_repair_kernel_summary` 只保留在 runtime public 兼容层和测试中，`roles.adapters` 不得调用；post-execution 语言修复只能通过 `roles.adapters/internal/director/post_execution_repair_bridge.py` 的统一入口，step 调度事实源必须来自 `query_director_repair_post_execution_schedule`，bridge 只允许保存 `step_id -> runner` 绑定，且 runner key 集合必须与 runtime schedule 完全一致，禁止在 adapter 里重新定义 phase/priority/depends_on 目录；禁止在 `execute_method.py`、Factory、QA 或 bench harness 里直接 import 具体语言 repair 函数。新增 deterministic repair 必须遵循 `Diagnostic -> Plan -> Compose -> Policy/Execute -> Receipt -> Revalidate`，planner/composer 不得直接写文件；commit 副作用必须经由 Director policy-gated 工具适配器执行，精确 `text_replace` 优先走 `edit_file`，整文件生成、结构化序列化、fallback 或 rollback 才允许走 `write_file`，并产出 before/after hash receipt；多轮执行必须通过 repair kernel scheduler 建模 priority、depends_on、round_number、max_rounds 和 cycle breaker。Receipt 必须能绑定 post-check evidence，至少包含 verifier command、exit code、before/after diagnostics、resolved/residual diagnostic ids、errors_before/errors_after/net_error_reduction。未来 AGI/Resident 只能作为 non-authoritative advisory：不得写文件、生成 authoritative plan、覆盖 policy、给 success verdict、注册规则、成为 Run Ledger/ReceiptStore/ContextOS 事实源；任何 suggested-rule payload 必须先通过 `validate_director_repair_advisory`，该入口只读、只标准化或拒绝建议，不产出 repair plan 或注册规则；validation summary 也必须显式投影 `agi_execution_authority=false`、`writes_allowed=false`、`registration_allowed=false`、`authoritative_receipts_allowed=false`、`suggested_rules_are_advisory_only=true`。
   - 当前 runtime executable binding 口径以 `runtime_repair_bindings()` 为唯一事实源；禁止在 AGENTS/CLAUDE/README 手写 source_tool 固定总数或长列表作为事实源；需要精确数量或列表时运行 `runtime_repair_bindings()` 或查询 `query_director_repair_strategy_catalog`，Rust executable 关键不变量为 20 个。Rust module-file topology 规则 E0583 / E0761 已分别通过 `deterministic_rust_missing_module_file_repair` / `deterministic_rust_duplicate_module_file_repair` 成为 runtime executable；Rust missing lib target、lib root facade、struct literal missing field 也已拆成明确 source_tool；后续 Agent 不得把这些规则重新接回 legacy direct-write helper；所有 runtime binding source_tool 必须通过 `RunDirectorRepairCommandV1(source_tool="<runtime binding source_tool>")` 执行，禁止新增语言/规则专用 public facade。
   - `deterministic_rust_post_repair` 只是 aggregate post-execution callback / legacy schedule label，不是 `runtime_repair_bindings()` 暴露的 `executable_runtime` source_tool，不得传给 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1`。Runtime schedule 的每个 step 都会投影 `source_tool_kind` 与 `executable_runtime_source_tool`；任何 schedule consumer 必须以这两个字段为准，只有 `source_tool_kind="executable_runtime"` 且 `executable_runtime_source_tool=true` 的 source_tool 才能作为 public Plan/Run source_tool。`delete_file` 已作为 repair kernel operation/tool 能力存在并有 receipt/policy 语义；但不能因 `delete_file` operation/tool 存在就恢复 aggregate Rust topology repair。Rust post aggregate 不得作为可执行绑定恢复；新增或迁移余量仍需 coverage、policy 和 revalidation evidence 后才能拆成明确 source_tool。
   - Targeted gate 同步：最新 targeted gate 摘要为 `842 passed`（2026-06-26 docs/metrics sync 口径引用）；后续调整 binding/gate 文档时必须同步更新该摘要或命令证据。
   - `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` 只能接受 `runtime_repair_bindings()` 暴露的 `executable_runtime` source_tool。未知、未注册、`reserved_only` 或仅 `metadata_rule_registered` 的 source_tool 必须 fail-closed，并在 public planning/run result 的一等 `error_code` 返回 `unsupported_repair_source_tool`；不得写 workspace，不得静默 fallback 到 legacy regex/direct-write helper，也不得由 adapter/bench/QA 自行补救执行。
   - materialization-quality 修复也必须通过 runtime schedule：`roles.adapters/internal/director/materialization_quality_repair_bridge.py` 只能消费 `run_director_materialization_quality_repair_schedule`，并只绑定 runtime 声明的 `step_id`；runner key 集合必须与 runtime-owned schedule 完全一致，禁止在 adapter 里新增、删除或重排 schedule step，禁止在 adapter 里重新定义该阶段 phase/priority/depends_on。当前 materialization schedule 已拆成 `materialization.hygiene_scaffold`、`materialization.typescript_scaffold`、`materialization.typescript_compiler`、`materialization.html_entrypoint`、`materialization.node_manifest`、`materialization.rust_compiler`、`materialization.target_runtime`、`materialization.python_import`、`materialization.go_import` 九个 runtime-owned step；这些 materialization step 的 `source_tool_kind` 默认为 `callback_schedule_label`，不得把它们当作 `RunDirectorRepairCommandV1` 的 executable source_tool。禁止恢复单个 `materialization.quality_repair_host` 大步骤。旧 `_apply_deterministic_materialization_quality_repairs` facade 已硬切删除，禁止恢复、转发或作为测试/bench/Agent 入口。
27. **Repair coverage 先于补规则（强制）**：遇到新的 compiler/verifier diagnostic，不得先在 legacy deterministic function 里临时补 regex。必须先通过 `director.runtime.public.service.query_director_repair_coverage` 或 internal registry 形成 coverage report；`known_rule_matched=false` 是可审计平台缺口，必须记录 diagnostic code/path/message/archetype/phase 建议后再决定是否新增规则。Coverage report 是只读发现层，禁止写文件、禁止隐式自动注册新 source_tool、禁止让 AGI suggested rule 直接成为 authoritative rule。未来更多编程/脚本语言的专项修复由后续 Agent 通过 L1-L12/九十多个项目 bench 证据逐步补齐；开工前必须先查 `query_director_repair_language_slots`，优先复用已有 reserved slot（例如 Vue/Svelte、Scala/Groovy、Elixir/Erlang、Haskell/OCaml/F#、Zig/Nim/Crystal、Perl/PowerShell/Julia、Objective-C/MATLAB/Fortran/Terraform、Dockerfile/Make/Bazel/Starlark、YAML/JSON/TOML/Nix、GraphQL/Proto/Solidity/Vyper 等），没有槽位才在 `director.runtime` registry 中补 reserved slot。slot 的 `implementation_status` 必须按三态理解：`reserved_only` 只表示预留扩展落点，`metadata_rule_registered` 只表示 catalog/coverage 已有规则元数据，只有 `executable_runtime` 才允许通过 `RunDirectorRepairCommandV1` 执行。新增语言规则必须先落 catalog/archetype/coverage/receipt/verifier evidence，再接入 legacy bridge 或 runtime scheduler，禁止为单个样例直接扩写 `execute_method.py` 分支。迁移旧策略时必须先暗跑：通过 `compare_director_repair_shadow_run` 对账 legacy tool_results 与新 kernel receipt 的 files/source_tools，matched 后才能切断旧路径；shadow comparison 只读、不得写 workspace。`CompareDirectorRepairShadowRunV1.comparison_mode` 必须显式区分 `independent_shadow_run` 与 `legacy_projection_self_check`；只有 `independent_shadow_run` 且 scope/hash/revalidation/authoritative receipts 全部满足时才允许 `cutover_ready=true`。legacy summary projection 内嵌的 `dark_launch_comparison` 只是 `legacy_projection_self_check`，必须保持 `cutover_ready=false` 和 `independent_shadow_required` blocker，不能作为切断旧路径的证据。

#### factory_bench 标准启动方式（内部测试态）

其他 Agent/Claude/Codex 执行 L1-L12 压力测试时必须使用以下模式，禁止共享主后端 `49977` 做并发 workspace switch：

```bash
rtk proxy bash -lc '
set -euo pipefail

PROJECT_ID=L1-04
WORK_DIR=/tmp/factory-bench-l1-04-r23

case "$WORK_DIR" in
  /tmp/factory-bench-*) rm -rf -- "$WORK_DIR" ;;
  *) echo "unsafe WORK_DIR: $WORK_DIR" >&2; exit 2 ;;
esac

NO_PROXY="*" no_proxy="*" timeout --kill-after=30s 600s \
  python src/backend/scripts/factory_bench/run_factory_bench.py \
  --project-ids "$PROJECT_ID" \
  --work-dir "$WORK_DIR" \
  --timeout 540 \
  --max-failed 0 \
  --real-run-timeout 120 \
  --launcher-instance-mode isolated \
  --bench-session-reporting off \
  2>&1 | tee "$WORK_DIR.runner.log" | tail -25
'
```

硬性要求：
- 必须保留 `set -euo pipefail`，禁止用 `2>&1 | tail` 隐藏 runner 失败退出码。
- 必须显式传 `--launcher-instance-mode isolated`，即使当前默认也是 isolated。
- 必须显式传 `--bench-session-reporting off`。isolated 项目的可见性来自 Instance Registry/Launcher 和该项目自己的 backend/runtime.v2；共享主后端 `/v2/factory/bench/sessions` 只是内部兼容观测桥，不是运行依赖，也不得成为并发压测路径。
- `WORK_DIR` 只能是 `/tmp/factory-bench-*` 或用户明确授权的临时目录；删除前必须做路径 guard。
- Runner 启动后会在 Launcher 注册 `kind=bench_project` 的内部测试实例，每个项目应有独立 backend/frontend 端口、独立 workspace、独立 `/v2/ws/runtime` 和独立 ContextOS。
- `timeout` 只会杀 runner；已启动的 isolated backend/frontend 可能继续留在 Launcher 中供观察。异常中断后必须从 Launcher 停止或删除对应测试实例。
- 禁止手工 `kill`、`pkill`、`lsof -ti :PORT | xargs kill` 或清理不属于自己本次 bench 的端口。停止/删除实例必须优先走 Launcher 或 `/v2/instances/{instance_id}`，并且只操作自己启动的 `instance_id` / `WORK_DIR`。若确需清理孤儿进程，必须先比对 `~/.polaris/instances/registry.json`、进程命令行中的 workspace、backend/frontend port，确认不是其他 Agent 的实例。
- 禁止手动指定已被占用的 backend/frontend 端口；默认让 `--launcher-instance-mode isolated` 自动分配端口。平台 Supervisor 对显式端口冲突必须 fail-closed，不能抢占或误关已有进程。
- 禁止手动指定 `49977` / `5173` 给 bench 或临时项目实例；这两个端口保留给 `main`。即使旧 registry 记录里保存了这些端口，restart/独立启动也必须重新分配到非主端口。
- 禁止为了“切换观察项目”而向主后端 `POST /settings {"workspace": "<bench workspace>"}`。进入项目必须打开该项目自己的 `frontend_url` 或带 `backend` / `workspace` / `token` 的实例 URL。
- 如果只是串行调试且明确需要共享 49977，才允许显式 `--launcher-instance-mode observed --bench-session-reporting shared`；observed/session 记录只能作为轻量观测，不得用于并发压测或当作独立项目实例。shared session POST 必须短超时、best-effort、失败熔断，不能拖慢或改变 bench 结论。

### 实时推送硬门禁

- 首页主工作区、Factory 工作区、PM/ChiefEngineer/Director 工作区、ContextOS 实时视图必须通过同一套 Nats-JetStream runtime.v2 WebSocket 接收推送。内部 Bench harness 可以消费同一事实流做压力测试和审计，但不得成为正式产品实时链路或生产 UI 的依赖。
- 禁止为了"兜底"并行保留第二套实时机制；发现双轨（SSE + WS、WS + HTTP polling、WS + 文件轮询）视为 P0。
- WebSocket 订阅/连接失败必须 fail-closed：UI 应显示断线/订阅失败并等待用户操作或连接恢复；禁止自动调用 HTTP status/get/list 接口作为实时兜底，禁止用“最近一次快照”冒充正在实时更新。
- 新增实时事件必须先定义 JetStream subject/channel 映射，再由 `RuntimeTransportProvider`/`runtimeSocketManager` 订阅；不得在组件内用 `setInterval` 调接口模拟实时。
- 允许的非推送请求只有：页面加载初始 snapshot、用户点击刷新、命令提交后的单次确认、Playwright/pytest 等测试等待循环。
- 审计时必须 grep `EventSource`、`text/event-stream`、`StreamingResponse`、`setInterval`、`pollInterval`、`polling`、`轮询`、`fallback`、`fetchRunStatus`；命中产品实时路径即失败，除非有明确注释证明是 UI 动画/时钟/重连/测试等待而非数据刷新。
- 审计时必须 grep `PM → Director`、`PM->Director`、`PM -> Director`、`PM 规划 → Director`；命中产品链路、UI 文案或运行路径即失败，除非明确标记为历史档案。当前唯一允许的主链路文本是 `PM → Chief Engineer → Director`。

### 后端迁移承载规则

对于 `src/backend` 下的 ACGA 2.0 / Cell 化 / Context Plane / Governance 演进：

- 任何 Agent 开工前必须先读：`src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
- 所有 Cell 开发必须先复用已有 Cell 公开能力，所有新开发必须基于 `src/backend/polaris/kernelone/` 能力与契约链路
- 新架构目标实现统一落在 `src/backend/polaris/`
- 其中规范根目录解释为：
  - `bootstrap/` -> `src/backend/polaris/bootstrap/`
  - `delivery/` -> `src/backend/polaris/delivery/`
  - `application/` -> `src/backend/polaris/application/`
  - `domain/` -> `src/backend/polaris/domain/`
  - `kernelone/` -> `src/backend/polaris/kernelone/`
  - `infrastructure/` -> `src/backend/polaris/infrastructure/`
  - `cells/` -> `src/backend/polaris/cells/`
  - `tests/` -> `src/backend/polaris/tests/`
- `src/backend/docs/graph/`、`src/backend/docs/governance/`、`src/backend/docs/templates/` 继续保留在仓库现有位置，作为共享真相与治理资产
- `src/backend/app/`、`src/backend/core/`、`src/backend/api/` 已移除；`src/backend/scripts/` 仍保留（历史脚本），新功能应写入 `polaris/delivery/cli/`

---

## 三、测试目标：复杂项目验证

### 3.1 复杂项目要求

为验证 Polaris 稳定性，每次测试需生成**足够复杂**的项目：

- **功能复杂度**：至少 3 个以上模块/服务
- **代码规模**：至少 500+ 行代码（前端+后端）
- **依赖复杂度**：至少 3 层依赖关系
- **测试覆盖**：需要单元测试 + 集成测试
- **配置复杂度**：至少包含配置文件、环境变量、构建脚本

### 3.2 推荐测试项目类型

```
1. RESTful API 服务（Express/Koa + 数据库）
2. Web 应用（React + 后端 API）
3. CLI 工具（Node.js/Python）
4. 微服务架构（多个服务通信）
5. 带数据库的完整应用
```

### 3.3 项目复杂度指标

| 指标 | 最低要求 | 验证方式 |
|------|---------|---------|
| 文件数量 | ≥10 个 | `find . -type f | wc -l` |
| 代码行数 | ≥500 行 | `wc -l **/*.{ts,js,py}` |
| 模块数量 | ≥3 个 | 目录结构检查 |
| 配置文件 | ≥3 个 | package.json, tsconfig.json 等 |
| 测试文件 | ≥2 个 | `**/*.test.ts` |

---

## 四、执行顺序（固定）

### A. 环境预检

1. 启动 Polaris（Electron + backend 可达）
2. 通过 `window.polaris.getBackendInfo` 获取 baseUrl 和 token
3. 验证 `/settings`、`/runtime/storage-layout` 可访问
4. 若 workspace 不是目标目录，优先走 UI 切换；若 OS 文件选择器不可自动化，则使用 `/settings` 更新 workspace 并记录"UI例外降级"

### B. 政事堂立项（中书令）

1. 在主界面点击"生成计划"打开"政事堂"
2. 在"圣意目标"填入项目需求（**必须足够复杂**）
3. 点击"发起奏对"，至少 1 轮，直到"廷议状态"显示可拟定条陈或"已齐备"
4. 点击"拟定条陈"，等待预览生成完成
5. 点击"批红 / 用印"，确认 docs 成功写入
6. 校验 docs 已生成且 plan 已同步到 runtime contracts

### C. PM 运行与质量门禁

1. 进入 PM 工作区（enter-pm-workspace），点击单次督办（pm-workspace-run-once）
2. 测试等待 `/v2/pm/status`：必须观察到 `running=true` 后再回到 `false`（仅限 Playwright/pytest 等测试 harness 等待异步完成，禁止复用为产品实时方案）
3. 校验 `/state/snapshot`：
   - `tasks` 数量 > 0
   - `completed_task_count` > 0
   - `last_director_status` 非空

4. **PM 质量硬门禁**：
   - 分数 >= 80
   - critical issues = 0
   - 任务必须具备：目标、作用域、可执行步骤、可测验收

5. **提示词穿透检测**（PM 输出与任务合同）：
   - 检测关键词：`you are`, `role`, `system prompt`, `no yapping`, `提示词`, `角色设定`, `<thinking>`, `<tool_call>`
   - 一旦命中视为 P0 失败，立即修复并重跑 PM

### D. Director 执行与工具审计

1. 进入 Director 工作区（enter-director-workspace），点击执行（director-workspace-execute）
2. 测试等待 `/v2/director/status`：必须进入 RUNNING 再退出 RUNNING（仅限 Playwright/pytest 等测试 harness 等待异步完成，禁止复用为产品实时方案）
3. 测试读取 `/v2/director/tasks`：必须存在 `metadata.pm_task_id` 关联任务，不得把该读取包装成前端实时刷新循环

4. **工具调用审计**：
   - 检查是否存在工具调用证据
   - 检查是否出现 `unauthorized=false`/越权阻断事件
   - 检查是否有危险命令/路径穿越被触发
   - 必须打开最新 `context_snapshot_ref`，核对 `provider_request.tools` 与 Director 当前任务一致；不能只看 prompt、RoleProfile 或 UI 摘要。
   - 如果 Director prompt 中要求 `repo_tree`/`read_file`/`repo_read_*`，但最终 provider request 未提供这些工具 schema，视为 P0 上下文/工具装配缺陷，先修平台再继续 bench。
   - 如果模型输出 `<function=...>`、`[TOOL_CALL]`、JSON tool call、自然语言工具意图等任一形态却没有进入真实 tool execution，必须审计 parser、ToolSpecRegistry、arg_aliases、provider tool_choice、native/text fallback 全链路。

5. 若工具策略异常、越权或无效调用导致失败，修复根因后重跑 Director

### E. 验收与闭环

1. 检查 `integration_qa` 结果为通过态（目标 `reason=integration_qa_passed`）
2. 若失败，定位失败源头（PM 合同、Director 执行、工具策略、代码实现、测试基线）并修复
3. 修复后先回归失败门禁，再做整链回归（政事堂→PM→Chief Engineer→Director→QA）

---

## 五、修复循环（直到通过）

1. **收集证据**：UI 截图、trace、renderer/terminal 错误、runtime 事件、状态与结果文件
2. **根因分类**：
   - 配置类
   - 提示词类
   - 任务质量类
   - 工具授权类
   - 代码实现类
   - 测试基线类
3. **生成最小充分修复方案并实施**（不得跳过验证）
4. **重跑失败环节 → 重跑全链路**
5. **仅当所有门禁 PASS 才结束**
6. **主动发现优先**：每轮重跑后必须先审计最终 provider request、ContextOS 快照、runtime events、bench session、目标产物和门禁结果；不得只看终端是否还在运行，也不得等用户截图指出异常后再补查。
7. **经验沉淀**：任何由用户观察才暴露的问题，都必须在根因修复同时沉淀到本文件或相关子目录 `AGENTS.md`/架构文档的强制约束中，避免同类问题再次靠人工观察发现。

---

## 六、最终输出（强审计包，JSON）

返回一个 JSON 对象，字段至少包括：

```json
{
  "status": "PASS|FAIL",
  "workspace": "string",
  "rounds": "number",
  "pm_quality_history": [
    { "round": 1, "score": 85, "issues": [] }
  ],
  "leakage_findings": [
    { "type": "prompt_leakage", "evidence": "path/to/file", "fixed": true }
  ],
  "director_tool_audit": {
    "total_calls": 10,
    "unauthorized_blocked": 0,
    "dangerous_commands": 0,
    "findings": []
  },
  "issues_fixed": [
    { "issue": "description", "root_cause": "category", "fix": "path/to/fix", "verified": true }
  ],
  "acceptance_results": {
    "court_phase": "PASS",
    "pm_phase": "PASS",
    "director_phase": "PASS",
    "qa_phase": "PASS"
  },
  "evidence_paths": {
    "screenshots": [],
    "logs": [],
    "snapshots": []
  },
  "next_risks": []
}
```

**要求**：每个失败与修复都有证据路径和前后状态对比

---

## 七、推荐执行入口

```bash
# 列出可用 Electron E2E 测试
npm run test:e2e -- --list

# 运行 Electron E2E 测试（唯一 E2E 测试）
npm run test:e2e

# 工厂冒烟测试
python scripts/run_factory_e2e_smoke.py --workspace .

# 独立运行各角色（推荐）
# Architect - 架构设计
python -m core.polaris_loop.role_agent.architect_cli --mode interactive --workspace .

# Chief Engineer - 技术分析
python -m core.polaris_loop.role_agent.chief_engineer_cli --mode interactive --workspace .

# PM - 项目管理
python -m polaris.delivery.cli.pm.cli --workspace . --start-from pm

# Director - 代码执行
python -m scripts.director.cli_thin --workspace . --iterations 1
```

---

## 八、核心已实现功能（避免重复造轮子）

以下功能已在代码库中实现，更新指南请勿重复创建：

### 8.1) 智能错误恢复
- **实现**: `src/backend/polaris/kernelone/tool_execution/error_classifier.py`
- **功能**: 错误分类（可重试 vs 不可重试）、指数退避、熔断器

### 8.2) MCP 协议支持
- **实现**: `src/backend/infrastructure/tools/mcp_client.py`
- **功能**: MCP 客户端，支持 stdio 和 HTTP MCP 服务器

### 8.3) Tree-sitter 代码解析
- **实现**: `src/backend/infrastructure/tools/treesitter.py`
- **功能**: AST 解析、符号定位、重命名、节点替换

### 8.4) 代码依赖分析
- **实现**: `src/backend/infrastructure/tools/code_analysis.py`
- **功能**: 依赖图生成、复杂度分析

### 8.5) 实时 WebSocket
- **实现**: `src/backend/polaris/delivery/ws/runtime_endpoint.py`
- **功能**: 实时状态推送、事件流、心跳保活

### 8.6) 多编辑模式
- **实现**: `prompts/generic.json`（`precision_edit` 为工具操作名，非独立 `precision_editor.py` 文件）
- **功能**: tool_first、precision_edit、repo_apply_diff、treesitter_* 等

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

## 外部并行工程 Agent 调用规范（主 Agent 专用）

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

## Factory Bench 架构约束（2026-06-25 沉淀）

### 修复层级铁律

**bench_gates.py 是量具，不做修复。** Factory/Bench gate 只能测量和记录 evidence，不得改写 workspace、自动初始化 manifest、删除/重排源码或“顺手修复”目标项目。

确定性修复必须收敛在 Director 执行链路，事实源和公共契约归 `director.runtime`：
- Canonical kernel: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/`
- Cross-cell public surface: `polaris.cells.director.runtime.public` / `polaris.cells.director.runtime.public.service`
- Legacy strategy host only: `src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/`

RepairEngine canonical final pipeline 固定为：`Typed Diagnostics -> Coverage Report -> RepairPlan -> PatchComposer -> PolicyGate -> Transactional Executor -> Revalidation Evidence -> Authoritative Receipt -> Ledger/LLM Context`。任何 Agent 不得把 diagnostic regex、patch 生成、policy gate、executor、revalidation、receipt 或 ledger/context 投影拆回 `roles.adapters`、Factory、QA、bench harness 或 public wrapper 的私有分支。

禁止其他 Agent 新增或恢复：
- `src/backend/polaris/cells/roles/adapters/internal/director/repair_kernel/**`
- `src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/strategy_catalog.py`
- 从 `roles.adapters` 直接 import `polaris.cells.director.runtime.internal.repair_kernel`

如需新增规则，先在 `director.runtime` 建 typed diagnostic/plan/composition/receipt 能力，再通过 public service 暴露给 legacy caller；不得把新事实源放回 `roles.adapters`。
public 执行/规划只能通过 `PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` 和 `plan_director_repair` / `run_director_repair`；不得再新增语言或规则专用 public 函数名。
public 收敛执行只能通过 `run_director_repair_convergence` + `RunDirectorRepairConvergenceCommandV1`，并由 adapter 注入 verifier callback，callback 必须返回 `DirectorRepairVerifierSnapshotInputV1`。`director.runtime.public` 只负责把 adapter-supplied verifier DTO/callback 投影为 runtime verifier snapshot，不直接执行 verifier command；`roles.adapters`、Factory、QA、bench、public wrapper 均不得 import `director.runtime.internal` 或绕过 public convergence API。
`_runtime_bridge.run_runtime_repair_with_director_tools` 只有在调用方提供真实 `convergence_verifier` 且该 verifier 产出命令、exit code、residual diagnostics、raw output ref 等 evidence 时，才允许走 convergence path 并投影 authoritative receipt。没有 verifier evidence 时不得伪造 success；receipt 必须保持 non-authoritative，并显式保留 `metadata.requires_revalidation=true` / `authoritative=false` 或等价 public 投影。
新增规则前必须先补或更新 repair coverage 报告，让 uncovered diagnostic 从 `known_rule_matched=false` 变成明确匹配的 `rule_id/source_tool`。
`query_director_repair_strategy_catalog` 是 deterministic repair 迁移状态读模型；每个 item 必须暴露 `implementation_status`，summary 必须区分 `executable_runtime` 与 `legacy_strategy_host` 的 source_tool 数量和列表。后续 Agent 迁规则前必须先看该 catalog，禁止凭 grep 认定“还剩多少”。
当前 runtime executable binding 口径必须由 `runtime_repair_bindings()` / `query_director_repair_strategy_catalog` 动态派生，禁止手写固定 source_tool 总数；Rust executable 关键不变量为 20 个。Rust module-file topology E0583/E0761 已通过 `deterministic_rust_missing_module_file_repair` / `deterministic_rust_duplicate_module_file_repair` 成为 runtime executable，Rust missing lib target、lib root facade、struct literal missing field 也已拆成明确 source_tool。`deterministic_rust_post_repair` 不是 executable binding，只能作为 aggregate post-execution callback / legacy schedule label 观察；不得恢复 aggregate Rust topology repair。`delete_file` 已是 repair kernel operation/tool 能力，但不能因此恢复 aggregate Rust topology repair；新增/迁移仍必须先走 coverage、policy 和 revalidation evidence。最新 targeted gate 摘要：`842 passed`。
新增语言/DSL 前必须先查 `query_director_repair_language_slots`；`reserved_only` 不能执行，`metadata_rule_registered` 只能做 coverage/catalog，只有 `executable_runtime` 才能通过 public repair run 写入。
Coverage gate 是新增/迁移规则的入口：`known_rule_matched=false`、metadata-only（`metadata_rule_registered`）、reserved-only（`reserved_only`）都是可审计缺口，不是可执行修复。新语言、新 bench 样例或新 verifier diagnostic 必须先进入 language slot / strategy catalog / coverage report，补齐 rule_id、source_tool、archetype、phase、receipt/verifier evidence 后，才允许新增 runtime executable binding；禁止为了单个样例往 `execute_method.py` 加语言分支或从 bench/QA 直接调用 legacy helper。
后续 RepairEngine/bench Agent 的安全扩展顺序固定为：先查 `query_director_repair_language_slots` 选择或新增 reserved slot，再用 `query_director_repair_coverage` 记录 uncovered diagnostic，随后补 catalog/archetype/phase/source_tool 元数据，最后才接入 runtime executable binding、受控 bridge/scheduler、policy receipt 与 revalidation evidence。未到 `executable_runtime` 前只能记录 gap 或输出 advisory；Factory、QA、bench harness 不得代为执行、注册或回退 legacy helper。
未知、未注册、`reserved_only` 或仅 `metadata_rule_registered` 的 `source_tool` 必须在 `PlanDirectorRepairCommandV1`/`RunDirectorRepairCommandV1` 的 public result 一等返回 `unsupported_repair_source_tool`，不得写 workspace，不得静默 fallback 到 legacy regex/direct-write helper。
多轮确定性修复必须使用 repair kernel scheduler 建模 `priority`、`depends_on`、`round_number`、`max_rounds` 与 cycle breaker；`run_director_post_execution_repair_schedule(..., max_rounds=3)` 是 post-execution 收敛入口，adapter 只能绑定 step runner，禁止在 `execute_method.py` 或新语言 helper 里新写独立循环。receipt 必须携带 post-check evidence，证明对应 diagnostic 是否消失，并通过 `revalidation_coverage` 汇总 evidence missing/failed 状态；直接 `run_director_repair`/executor 写入成功只能表示 patch 已应用，缺少 revalidation evidence 时必须保持 `authoritative=false` 且 `metadata.requires_revalidation=true`，不得冒充闭环权威 receipt。public `RepairReceiptV1` 必须投影 `authority_hash` / `projection_hash`，且 revalidation evidence 是 authority hash 材料。复测 evidence 存在但 exit code 非 0 时必须标记 failed post-check，不能设为 authoritative，也不能渲染成 missing evidence，且必须在 `failed_revalidation_receipt_ids` / `failed_revalidation_source_tools` 中定位失败对象，禁止只给失败计数。大文件编辑必须优先产出 span/context unique text patch 并通过 `edit_file` 精确提交；JSON 必须走 structured merge / canonical serialization；TOML/YAML 规则未具备结构化 merge 能力时必须 reserved fail-closed。`write_file` 只允许用于新文件、结构化整文件序列化、fallback 或 rollback，并必须在 receipt/metadata 中记录 reason、fallback source、before/after hash 与 policy decision。迁移旧策略必须先用 `compare_director_repair_shadow_run(comparison_mode="independent_shadow_run")` 暗跑对账 legacy tool_results 与 kernel receipts 的文件/source_tool 范围；`comparison_mode="legacy_projection_self_check"` 永远不能作为 cutover 证据。legacy `tool_results` 转 repair_kernel summary 必须走 `ProjectDirectorRepairKernelSummaryV1` + `project_director_repair_kernel_summary`；`build_director_repair_kernel_summary` 是 runtime public 兼容 helper，不得从 `roles.adapters` 调用。Legacy `deterministic_repairs` 目录只是 migration strategy host；`execute_method.py`、Factory、QA、bench、public wrappers 禁止直接调用具体 `_apply_deterministic_*` / `repair_*` 函数。
AGI Advisory Overlay 只能输出 suggested_rules、coverage gap、archetype 或 evidence 建议；不得写文件、注册规则、绕过 policy、生成 authoritative plan/receipt、覆盖 verifier 结论或成为 Run Ledger、ReceiptStore、ContextOS、Factory/Bench 成功条件的事实源。任何 AGI 建议必须先通过 `validate_director_repair_advisory` 只读校验，且校验结果必须显式保留 `agi_execution_authority=false`、`writes_allowed=false`、`registration_allowed=false`、`authoritative_receipts_allowed=false`、`suggested_rules_are_advisory_only=true`。

### Director 上下文强制审计

bench 失败后，**先审计 Director 最终 LLM 请求**（context_snapshot_ref），再做修复。必查：
1. CE Blueprint 是否注入（`BlueprintOverviewSignal` 对 director 角色必须 applies_to=True）
2. context_window_utilization < 10% 是红旗
3. task_id 映射是否断裂（PM 数字 ID ↔ CE TASK-N 前缀）
4. Task 描述是否被截断

### 跨文件一致性防御

优先级：**预防**（CE 蓝图注入）> **检测**（质量门）> **修复**（deterministic repairs）。
禁止只做修复层（打地鼠反模式）。
