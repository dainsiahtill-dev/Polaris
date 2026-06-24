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
11. **LLM 最终请求上下文审计**：每次真实 LLM 调用都必须审计最终 provider request，而不只统计 messages 投影；审计至少包含 message/tool schema/response_format token 估算、最终请求 token、窗口利用率，以及 PM 合同、Chief Engineer 蓝图、目标文件、失败反馈、workspace quality evidence 覆盖度 flags。ContextOS 必须优先展示最终请求上下文 token，禁止用 messages-only 或 prompt usage 冒充最终上下文占用。
12. **角色工具失败外部审计（主 Agent 专用，禁止产品化）**：PM、Chief Engineer、Director、QA 任一角色发生工具调用失败、工具调用缺失、工具参数无法归一化、工具结果被误判成功、或 LLM 输出被错误当作工具 action 时，主 Agent 可以安排至少一个 OpenCode 外部 Agent 做独立审计。该审计只属于 Codex/Claude 等主 Agent 的工程协作手段，绝对不是 Polaris 平台能力、运行时依赖、Factory gate、bench 成功条件、ContextOS/ReceiptStore 事实源或产品功能。Polaris 产品代码、脚本、UI、runtime event、Run Ledger、bench report 不得调度、等待、要求或展示 OpenCode 审计状态作为平台证据。外部审计应覆盖最终送入 LLM 的完整 provider request 上下文、工具调用归一化链路、ToolSpec/arg_aliases、runtime event、LLM 调用日志、ContextOS 证据和失败归因；若 LLM event 因安全策略 redacted 了 `messages`/`content`，主 Agent 必须把 `context_snapshot_ref` 对应的 `runtime/contexts/<shard>/<hash>` 快照文件纳入自己的外部审计证据包；禁止把 OpenCode 审计结论写成 Polaris 的 SSoT。
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
19. **Observed Bench 独立化启动（强制）**：Instance Registry 中带有 `metadata.backend_binding=shared_backend_workspace_switch` 的 `bench_project` 只是共享后端观测记录；当用户或 Agent 从 Launcher/API 对它执行 restart/独立启动时，Supervisor 必须分配新的 backend/frontend 端口并启动独立实例，禁止复用共享 backend 端口或把 observed 记录伪装为 running。
20. **factory_bench 并行实例模式（强制）**：多个 Agent 并行跑 `factory_bench` 不得共享同一个 49977 backend 做 workspace switch；runner 默认必须使用 `isolated`，让每个项目先启动独立 Polaris backend/frontend，再把该项目的 Factory run 发到自己的 backend。`--launcher-instance-mode observed` / `FACTORY_BENCH_LAUNCHER_INSTANCE_MODE=observed` 只允许显式用于轻量观测和串行兼容测试。

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

Codex、Claude Code 等主 Agent 可以通过 OpenCode CLI 将独立工程任务派发给额外 Agent。OpenCode 只能作为主 Agent 的外部工程协作/审计工具使用，不属于 Polaris 平台自身。禁止在 Polaris 产品代码、Factory Bench、Run Ledger、ContextOS、ReceiptStore、UI、runtime event 或质量门禁中引入对 OpenCode 的运行时依赖、调度逻辑、状态投影或成功条件。

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
6. 若任务源自角色工具调用失败，主 Agent 可在自身工作流中派发 OpenCode 审计最终 LLM 上下文、工具调用归一化路径、ToolSpec/arg_aliases、runtime event、LLM 调用日志与 ContextOS 证据；若事件中 `messages`/`content` 被 redacted，必须同时提供 `context_snapshot_ref` 对应的完整上下文快照文件；审计任务默认只读，除非已经拆出互不重叠的明确修复范围。
7. Factory Bench 与 Polaris 运行时不得要求、生成或消费 `opencode_audit` 作为机器可读平台字段；角色工具失败归因必须依赖 Polaris 自身的 provider request、runtime event、ContextOS、ReceiptStore、Run Ledger、命令门禁和日志证据。

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

## Factory Bench 架构约束（2026-06-25 沉淀）

### 修复层级铁律

**bench_gates.py 是量具，不做修复。** 所有确定性修复必须放在 Director 执行链路：
`src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/`

### Director 上下文强制审计

bench 失败后，**先审计 Director 最终 LLM 请求**（context_snapshot_ref），再做修复。必查：
1. CE Blueprint 是否注入（`BlueprintOverviewSignal` 对 director 角色必须 applies_to=True）
2. context_window_utilization < 10% 是红旗
3. task_id 映射是否断裂（PM 数字 ID ↔ CE TASK-N 前缀）
4. Task 描述是否被截断

### 跨文件一致性防御

优先级：**预防**（CE 蓝图注入）> **检测**（质量门）> **修复**（deterministic repairs）。
禁止只做修复层（打地鼠反模式）。
