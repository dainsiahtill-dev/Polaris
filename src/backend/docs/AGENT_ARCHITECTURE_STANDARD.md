# Polaris Agent 最新架构执行标准（ACGA 2.0）

状态: Active  
生效日期: 2026-10-21（工程主线同步）
适用范围: `src/backend`（所有人类/AI/Agent 协作开发）  

> 本文件是给 Agent 的统一执行入口，目标是减少多文档漂移。  
> 若与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准；本文件不得弱化 `AGENTS.md` 的任何强制规则。

---

## 1. 设计目标

1. 提供单一、稳定、可执行的架构标准入口，避免 Agent 任务偏移。
2. 明确“当前事实 vs 目标架构 vs 治理增强”三层语义，禁止混写。
3. 把高风险动作（状态写入、副作用、跨 Cell 调用）纳入可审计链路。
4. 保证迁移期可回滚，避免“新旧双轨长期并存”失控。

---

## 2. 权威关系（裁决顺序）

1. `AGENTS.md`（最高优先级执行规则）
2. `docs/graph/catalog/cells.yaml` + `docs/graph/subgraphs/*.yaml`（当前图谱事实）
3. `docs/FINAL_SPEC.md`（目标架构与迁移裁决）
4. `docs/真正可执行的 ACGA 2.0 落地版.md` + `docs/ACGA_2.0_PRINCIPLES.md`（ACGA 2.0 增强）
5. `docs/ARCHITECTURE_SPEC.md` + `docs/KERNELONE_ARCHITECTURE_SPEC.md`（支撑规范）

时序冲突处理规则：

1. 先服从 graph 当前事实做边界判断。
2. 再按 `FINAL_SPEC.md` 决定迁移方向。
3. 最后在上述边界内应用 ACGA 2.0 检索与治理增强。
4. 必须显式记录 gap，禁止把目标态写成现状。工程主线以 `../../../docs/blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md` 批准方案为准；旧 FINAL_SPEC/TransactionKernel/任务审计合同不得恢复已退役的新运行准入。

---

## 3. 默认工作流（中大型任务）

1. 读取 `docs/graph/catalog/cells.yaml`。
2. 读取目标相关 `docs/graph/subgraphs/*.yaml`。
3. 读取 `docs/FINAL_SPEC.md` 对应章节。
4. 涉及 Context Plane / Descriptor / Semantic Index 时，再读 ACGA 2.0 文档。
5. 读取目标 Cell 的 `cell.yaml`、`README.agent.md`、`generated/context.pack.json`。
6. 若存在，再读 `generated/descriptor.pack.json`、`generated/impact.pack.json`、`generated/verify.pack.json`。
7. 仅在必要时进入 `owned_paths` 源码。

禁止默认全仓扫描后再猜边界。

---

## 4. 架构硬标准

### 4.1 Graph First + Cell First

1. 目录不是边界，Cell 才是最小自治边界。
2. 跨 Cell 访问只能通过 `public/contracts.py` 等公开契约。
3. 禁止直接依赖其他 Cell 的 `internal/` 实现。

### 4.2 Contract First

跨 Cell 协作必须落在结构化契约上：

- command / query / event
- result / error / stream
- effect

### 4.3 Single State Owner

1. 每个 source-of-truth 状态只能有一个 Cell 写权限。
2. 其他 Cell 只能通过 query/projection/订阅事件读取。

### 4.4 Explicit Effects

文件写、数据库写、网络、子进程、工具调用、LLM 调用、Descriptor/Index 更新都必须声明且可审计。

### 4.5 No Dual Truth

1. `docs/graph/**` 是唯一图谱真相。
2. 检索缓存、向量库、context catalog 只能是派生资产，不能反向覆盖 graph。

### 4.6 Cell Reuse First + KernelOne Foundation（MUST）

1. 所有 Cell 开发必须先复用其他 Cell 的现有公开能力（contract/query/event/service）。
2. 禁止在未评估可复用能力前直接新建并行实现。
3. 缺口优先补齐既有 Cell 或其可治理子能力，再评估新增 Cell。
4. 所有新开发必须基于 `KernelOne` 作为底座（contract/runtime/effect chain），不允许绕过 `KernelOne` 直接耦合底层实现。
5. 默认决策顺序固定为：`reuse existing cell` -> `reuse kernelone capability` -> `new implementation`。

---

## 5. 目录归属标准（当前仓）

规范根目录在 `polaris/` 下落地：

- `polaris/bootstrap/`：启动装配与生命周期
- `polaris/delivery/`：HTTP/WS/CLI 传输入口
- `polaris/application/`：用例/编排（迁移中可收缩）
- `polaris/domain/`：领域模型与规则
- `polaris/kernelone/`：Agent/AI OS 运行时底座（技术能力）
- `polaris/infrastructure/`：外部系统适配器
- `polaris/cells/`：业务能力主承载层
- `polaris/tests/`：规范层测试

旧根目录（`app/`、`core/`、`scripts/`、`api/`）一律冻结为历史语义，禁止新增主实现、兼容入口、测试专用转发或任何旧版本回流路径。
发现调用方仍依赖旧路径时，必须迁移调用方到 canonical 路径；禁止通过 shim、别名模块或从 `git` 历史恢复旧实现来“糊过”测试或门禁。

---

## 6. KernelOne 专项标准

1. `kernelone/` 只承载平台无关技术能力，不承载 Polaris 业务语义。
2. 高风险副作用链路遵循：
   `Cell -> effect port -> kernelone contract -> infrastructure adapter`
3. 禁止 `kernelone -> application/domain/delivery/infrastructure/cells` 反向依赖。
4. 发布前必须通过 KernelOne release gate：
   `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

---

## 7. 运行时 I/O 与 KFS 标准

必须走 KFS（强制）：

1. LLM/Agent 工具读写。
2. 业务/运行时落盘：`runtime/*`、`workspace/history/*`、`workspace/meta/*`、证据/审计/任务状态等。
3. 用户工作区读写与归档路径读写。

可不走 KFS（仅少数例外）：

1. 进程启动期读取包内静态资源（模板/schema）。
2. 无法改造的第三方库内部 I/O。
3. 纯内存临时处理（不落盘）。

---

## 8. 实时观测标准

1. 实时推送必须使用统一 Nats-JetStream + `/v2/ws/runtime` WebSocket runtime.v2；禁止产品代码新增或保留 SSE、HTTP 长轮询、定时 HTTP 轮询、文件轮询伪实时、轮询兜底。
   - Searchable forbidden terms: 禁止轮询, HTTP long polling, timer HTTP polling, file polling, polling fallback, SSE.
2. HTTP 只允许用于初始快照、显式用户刷新、一次性 command/query；不得通过 `setInterval`、timer、后台 loop 调 HTTP 端点模拟实时。
3. 新增实时事件必须先定义 JetStream subject/channel 映射，并通过 `delivery/ws` runtime.v2 subject builder 进入统一通道；前端统一通过 `RuntimeTransportProvider`/`runtimeSocketManager` 订阅。
4. 观测事件优先结构化 payload（禁止只靠 message 文本猜测状态）。
5. LLM 生命周期（waiting/done/failed）和 Taskboard 状态必须由统一事件事实驱动。
6. 测试 harness 可以轮询状态端点等待异步完成，但不得把测试轮询包装为产品实时机制。
7. 首页、Factory、PM、ChiefEngineer、Director、ContextOS 的实时显示变更必须以 Playwright 或等价浏览器审计证明：WebSocket 推送到达后页面无刷新更新。

## 8.1 单一工程 owner 与可选协作

1. 新工程运行的唯一 owner 是现有 `roles.runtime.public.engineering_service.EngineeringRunService`。HTTP、CLI、UI、取消、恢复与错误路径共用该服务，不新增 Cell、任务表、ledger 或第二 controller。
2. 默认 `workflow=direct`，Director 读取目标、调用工具、根据真实结果继续工作；PM/Chief Engineer 按需只读咨询。显式 `workflow=collaborative` 才执行 PM、Chief Engineer、Director 协作；未调用角色显示 `not_called`。咨询一层返回，不递归派生总控；讨论不创建工程 run。
3. PM/CE 使用可读文本提供规划、设计或审阅。新运行不要求 PM 合同、CE portfolio/blueprint、handoff/hash、OBL/covers 图、semantic patch、Task Market、JobToken/DEO/Run Ledger 旧链准入，也不受旧一 turn 一 batch 规则约束。普通编译/测试失败回到同一 Director，不自动 deterministic repair 或重启规划。
4. 工具授权来自 host 持久化且不可由模型扩大的 `PermissionScope`、当前 profile、预算与 deadline；显式空权限保持为空。归一化后仍须检查真实路径/symlink/允许根，经 KFS 读写和 OS/process sandbox 执行命令；不支持 containment 时明确拒绝，禁止裸跑。PM/CE/QA 默认只读，同 workspace 仅一个源码写入者。
5. 唯一工程事实记录是 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`，保存运行、事件、真实工具观察和验证；新运行不写旧 TaskBoard/TaskMarket/Run Ledger/role-session 镜像。保留 runtime.v2/Nats-JetStream、ContextOS 最终 provider request、UTF-8、实例 workspace 绑定与 storage 单根。
6. 角色回复结束与 `completed` 不等于 `accepted`。实际检查必须绑定当前源码；用户已声明检查必须保留，未声明时由 Director 结合实际工程选择并实际执行验证。空检查、模型自报、快照条数或 QA 文本不得冒充通过；任务要求的独立 QA 必须审查同一当前候选，拒绝后继续同一任务。
7. 恢复使用原身份、workspace、原始权限与 deadline，并重新校验当前 host policy；保留失败、interrupted/UNKNOWN 和未知效果，不自动重放可能已执行的命令或写入。旧 ID 不升级为新 run；历史按 `engine_version` 只读，旧在途运行留在原冻结 owner，不在新进程恢复旧写入口。
8. 旧 Factory/RoleRuntimeService/execute_role_session/RoleExecutionKernel 写准入与自动 fallback 退役；历史解码器、只读查询与显式可选技术工具可保留，禁止据此声称历史源码已全部删除。旧规范仅解释旧记录，不能重新约束新运行。底层 hash 可用于证据/快照完整性，不能要求模型填写业务 hash。真实 HTTP 次数与费用仅用物理证据，未知保持 UNKNOWN。

## 8.2 工具调用归一化标准

1. 平台必须适配不同 LLM 的自然工具调用习惯，不得要求模型死记 Polaris 内部工具名或单一参数字段。
2. 工具入口必须先按 `ToolSpecRegistry` 的 tool aliases 与 `arg_aliases` 归一到 canonical 工具/参数，再进入授权、路径、命令、读写门禁。
3. 归一化只能处理同义、同意图、可安全推断的字段，例如 `create_file -> write_file`、`run_command -> execute_command`、`filename/path -> file`、`text/body/source -> content`、`query/search/q -> pattern`。
4. 不可安全推断的调用必须 fail-closed，并把原始工具名、参数、归一化结果与拒绝原因写入工具/LLM/runtime 证据；禁止吞异常、硬编码成功或把危险命令改写成看似成功的安全命令。
5. 弱模型 Director 产生的格式变体应优先通过统一归一化层吸收；不得在 Factory、Director prompt 或业务流程里堆叠特殊分支。

## 8.3 LLM 最终请求上下文审计标准

1. 每次真实 LLM 调用都必须审计最终 provider request，而不只统计 ContextOS messages 投影。
2. 审计字段至少包含：message 数量/字符/token 估算、native tool schema 数量/字符/token 估算、response_format token 估算、最终请求 token 估算、上下文窗口大小与利用率。
3. 工程角色调用须记录实际所需上下文的覆盖：原目标、实际使用的规划/设计、目标文件/目标项目上下文、失败反馈、workspace quality evidence；PM/CE 未调用的材料记不适用，不能变成强制合同准入。
4. ContextOS 实时视图必须优先展示最终请求上下文 token（含工具 schema 和 response format），不得继续用 messages-only 或 prompt usage 冒充最终上下文占用。
5. 最终请求上下文明显低于预期时，先归因为上下文装配/覆盖度/预算策略问题并留下 runtime event 证据；禁止通过扩大 max_tokens 或伪造 token 数掩盖缺失上下文。
6. Token/window utilization 只是容量指标，不是质量指标。最终请求审计须记录实际所需/已包含/缺失的证据与工具、role identity、tool schema registry coverage、工程 run/tool observation 绑定和 freshness；不能以旧 Run Ledger 绑定或业务覆盖图作为新运行前置。
7. `docs/governance/schemas/final-request-evidence-coverage.schema.yaml` 仅保留为旧合同链的历史审计契约。字符串命中只能作为低置信度线索，不能证明上下文正确注入；旧 contract/blueprint/handoff schema 只审计历史引擎，新运行不要求模型生成这些字段。

## 8.4 角色工具失败外部审计标准（主 Agent 专用，禁止产品化）

1. PM、Chief Engineer、Director、QA 任一角色发生工具调用失败、工具调用缺失、工具参数无法归一化、工具结果被误判成功、或 LLM 输出被错误当作 action 执行时，主 Agent 可以派发 OpenCode 外部 Agent 做独立审计。
2. OpenCode 外部审计只属于 Codex/Claude 等主 Agent 的协作工具，绝对不是 Polaris 平台架构元素。任何后端 Cell、Factory Pipeline、Run Ledger、ContextOS、ReceiptStore、runtime event、UI、bench gate 或 QA gate 都不得调度、等待、记录或依赖 OpenCode 审计状态。
3. 审计必须检查最终送入 provider 的完整 LLM request、ContextOS 组装结果、tool schema/response format、token 估算与覆盖度 flags，并确认是否缺原目标、实际使用的规划/设计、目标文件、失败反馈或 workspace quality evidence；未调用 PM/CE 不视为缺失。若 LLM event 中 `messages`/`content` 被 redacted，主 Agent 必须读取 `context_snapshot_ref` 对应的 `runtime/contexts/<shard>/<hash>` 快照文件，不得用 redacted event 代替完整上下文审计。
4. 审计必须沿工具解析与归一化路径确认 `ToolSpecRegistry` aliases/arg_aliases、自然语言/JSON/XML/native tool call 解析、授权门禁、路径门禁和命令门禁是否按顺序执行。
5. 审计必须收集 runtime event、LLM 调用日志、ContextOS 证据、bench session、角色日志和失败命令输出；不得用“模型没按格式输出”作为未审计的终结解释。
6. OpenCode 审计默认只读；若需要修复，主 Agent 必须先拆成互不重叠的授权范围，并在回收后零信任复核 diff、测试与证据链。
7. Factory Bench 的机器可读审计包只能记录 Polaris 原生证据：provider request、runtime event、ContextOS snapshot、ReceiptStore、Run Ledger、命令门禁、日志和截图/测试结果。禁止新增或要求 `opencode_audit` 作为平台字段；已存在的历史 `opencode_audit` 字段只可视为过时兼容数据，不得参与成功判定或缺陷归因。

## 8.5 Director 多绑定降级执行标准

1. Director 多绑定 fanout 允许对已证实不可连通或 readiness/connectivity 失败的单个 binding 做显式跳过，只调度仍可用的 Director binding。
2. 降级执行只适用于 Director 多绑定；PM、Chief Engineer、QA、单绑定 Director 不允许借此绕过 readiness。
3. 降级状态必须可观测：`/v2/llm/status` 输出 `DEGRADED`、`degraded_roles`、`factory_degraded_roles`、`skipped_bindings`、`skip_reason`；Factory/bench/runtime 证据必须记录被跳过的 provider/model/binding_id 和原因。
4. 所有 Director bindings 都不可用时必须 BLOCKED，不得继续运行。
5. 审计口径必须以实际可用 binding 为准；已跳过的 binding 不要求 LLM 调用证据，但必须有跳过证据，禁止把 degraded run 报告成全路成功。

## 8.6 Bench / Benchmark 测试态与生产边界标准

1. `Bench`、`Factory Bench`、`factory_bench`、`L1-L12 bench`、benchmark harness、压力测试脚本/API/UI 只允许存在于内部测试/开发/审计模式，服务于压力测试、根因发现、回归验证和审计样本生成。
2. 当前 L1-L12 压测、factory bench session、bench panel、bench API 可以在内部测试模式运行；这些设施不得被解释为正式项目功能、生产工作台、用户交付体验或控制面事实源。
3. 正式环境/生产环境不得出现 Bench 入口、Bench 菜单、Bench 面板、Bench 专属 API、Bench 文案或 Bench 专属状态模型。
4. Run Ledger、Job Token、ContextOS、ReceiptStore、Verifier/Gate Policy 是平台基础设施，必须以平台级 Cell/contract/projection/API/type 命名和暴露。Bench 只能在内部测试态作为这些平台能力的 producer/consumer，不得承载正式语义。
5. 禁止让正式 UI、ContextOS、TaskBoard、QA 工作台、Settings、public API 或生产运行时依赖 `benchService`、bench session、`factory_audits.json`、bench-only metadata 或任何 Bench 命名空间。
6. 如果某项能力先在 Bench 中验证成功，进入正式产品前必须完成命名空间提升：从 Bench 抽离到其真实平台 owner 的 projection/API；工程 run 从单份 engineering SQLite 投影，再由正式视图消费，不恢复旧 Run Ledger 写入。
7. 审计发现生产路径出现 Bench 依赖时按 P0 边界污染处理；修复优先级高于新增功能。
8. 正式工程循环、tool gating、prompt layer 和工具执行器不得解析 `[Benchmark Tool Contract]`、`benchmark_require_*` 或类似 Bench 文本/字段；内部 evaluator 的测量要求不能在生产循环恢复单批次限制、旧 tool_contract 或权限 barrier 绕过；正式行为仍由 host 权限和真实目标决定。
9. Browser、视觉、多模态 QA、用户脚本、领域脚本等 verifier modality 是平台级 Control Plane 可选能力，默认关闭；设置页可以保存启用意图，但只有当前环境显式声明可用时才允许把它们设为 hard-required evidence，禁止由 Bench harness 或内部压力测试状态决定正式项目是否必须启用这些能力。
10. 新工程工具消费 host `PermissionScope` 与当前 profile，并记录真实调用、效果与结果；Job Token/DEO/旧 Run Ledger 合同链仅历史读取，不是新运行前置，模型不能扩大授权。

## 8.7 工程运行审计与历史契约

1. 新工程以 `EngineeringRunService` 与 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3` 保存同一 run 的目标、原始权限/deadline、角色实际调用、tool started/observation、验证与 QA。不得复制到旧 task/ledger/role-session 事实表。
2. 最终 provider request、native tool 身份、实际工具结果、KFS 效果与进程退出码可互相追溯；不可用证据明确 unknown/unavailable，不能用模型回复、messages-only 或快照条数补造。
3. 验证必须实际发生且绑定当前源码；已声明检查保留，未声明时由 Director 结合真实工程选择检查。必要独立 QA 检查同一候选；工程完成、验证与验收各自如实展示。
4. `POLARIS_TASK_EXECUTION_AUDIT_SPEC.md`、`ce-handoff-decision`、`execution-envelope`、`run-provenance-bundle` 与旧 final-request coverage schema 仅解释历史合同链。新运行不要求 PM/CE/hash/handoff/OBL、旧 dispatch envelope、JobToken 或 semantic repair 准入。
5. 独立 deterministic repair 工具可显式保留在 `director.runtime`，跨 Cell 只走 public；coverage/plan/compose/policy/receipt/revalidation 约束只适用于这些工具，普通编译错误直接反馈 Director，禁止默认自动 schedule/fallback。

## 8.8 反证式架构验收标准

1. `docs/governance/POLARIS_SKEPTICAL_ARCHITECTURE_REVIEW.md` 是 Polaris 执行架构可靠性审查的固定入口。
2. 涉及 Factory、Director、ToolCallLifecycle、TaskRuntime、Run Ledger、TaskBoundary、QA verdict、cancel/deadline barrier、repair convergence 或 bench taxonomy 的底座改动，必须按该清单做反证式验收。
3. 单测、summary gate、coverage pass 或局部 bench 改善不能单独证明架构收敛；至少需要 fresh isolated 项目的完整事实链证据。
4. fresh isolated 工程未达到 accepted 时，定位 `final provider request -> provider response -> native tool dispatch -> durable tool observation/effect -> current-source verifier -> required QA -> engineering snapshot`；历史 Factory 的 TaskBoundary/TaskRuntime/Run Ledger 链只用于原记录。
5. 若任一状态仍依赖 raw TaskBoard/session JSON、prompt 文本、messages-only 统计、legacy deterministic repair、bench direct-write workaround 或第二套投影，必须按架构未证明处理，禁止声称“底座已收敛”。

---

## 9. 文档与治理同步标准

架构或边界变更时，至少同步评估：

1. `docs/graph/catalog/cells.yaml`
2. 相关 `docs/graph/subgraphs/*.yaml`
3. `docs/governance/ci/fitness-rules.yaml`
4. `docs/governance/ci/pipeline.template.yaml`
5. Cell 下 `context/impact/verify/descriptor` 资产新鲜度
6. `AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 是否需要镜像更新

---

## 10. 执行门禁（最小集）

1. `python -m pytest -q polaris/tests/architecture/test_kernelone_release_gates.py`
2. `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
3. descriptor 语义变化时评估 `polaris.cells.context.catalog.internal.descriptor_pack_generator`；其 CLI 默认遍历全部 Cell 并写 pack/receipt，必须限定授权范围或隔离输出，禁止为单 Cell 文档同步广泛覆盖源码/资产。实际不存在的 pack 不得虚构已更新

若无法执行，必须明确：

1. 哪些门禁未跑
2. 未跑原因
3. 残余风险

---

## 11. 完成定义（Definition of Done）

1. 边界变化有图谱与契约证据。
2. 副作用链路可审计、可追踪。
3. 测试与门禁通过，或失败有明确归因与下一步。
4. 文档真相与代码事实一致，不掩盖 gap。
