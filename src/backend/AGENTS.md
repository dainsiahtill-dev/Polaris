# Polaris Backend Agent Rules

状态: Active  
适用范围: `src/backend`  
规范基线: `docs/AGENT_ARCHITECTURE_STANDARD.md` + `docs/FINAL_SPEC.md` + `docs/真正可执行的 ACGA 2.0 落地版.md`

本文件是后端目录下 Agent 的最高优先级执行规则。  
`CLAUDE.md` 与 `GEMINI.md` 只是镜像摘要，不得引入额外或冲突规则；若冲突，以本文件为准。

---

## 1. 权威关系与裁决顺序

迁移期固定按以下顺序裁决：

1. `AGENTS.md`
2. `docs/AGENT_ARCHITECTURE_STANDARD.md`
3. `docs/graph/catalog/cells.yaml` 与 `docs/graph/subgraphs/*.yaml`
4. `docs/FINAL_SPEC.md`
5. `docs/真正可执行的 ACGA 2.0 落地版.md` 与 `docs/ACGA_2.0_PRINCIPLES.md`
6. 历史 2026-04-16 治理资产（仅解释旧引擎，不覆盖 2026-10-20 批准重构）：
   - `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
   - `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
   - `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`

规则：

1. 先按当前 graph 事实处理边界。
2. 再按 `FINAL_SPEC.md` 判断迁移方向。
3. 最后在前两者约束内应用 ACGA 2.0 的 Descriptor / Semantic / Governance 规则。
4. 禁止把目标态或规划态写成当前事实。当前工程行为以 `../../docs/blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md` 批准方案及本文件工程主线为准，旧目标态不能恢复旧准入。

## 2. 核心目标

本仓是迁移中的 ACGA 2.0 图谱系统。默认目标是：

1. 边界可解释
2. 状态可追责
3. 副作用可审计
4. 迁移可回滚
5. Agent 以最小上下文拿到正确真相
6. 语义检索不越过 graph 声明的合法边界

## 3. 默认阅读顺序

处理中等及以上任务时，按以下顺序读取：

1. `docs/AGENT_ARCHITECTURE_STANDARD.md`
2. `docs/graph/catalog/cells.yaml`
3. 相关 `docs/graph/subgraphs/*.yaml`
4. `docs/FINAL_SPEC.md`
5. 若任务涉及 Context Plane / Descriptor / Semantic Search，再读 ACGA 2.0 文档
6. 目标 Cell 的 `cell.yaml`、`README.agent.md`；`generated/context.pack.json` 仅在实际存在时读取，不虚构缺失 pack
7. 若存在，再读 `generated/descriptor.pack.json`、`generated/impact.pack.json`、`generated/verify.pack.json`
8. 目标 Cell 的公开契约
9. 必要时才进入 `owned_paths`

不要先全仓扫描源码再猜边界。

## 4. 强制原则

### 4.1 Graph First

Graph 是唯一架构真相，优先于目录树。

### 4.2 Cell First

Cell 是最小自治边界。

### 4.2.1 Reuse First + KernelOne Foundation

1. 先复用已有 Cell 的公开能力，禁止重复造轮子。
2. 缺口优先补齐既有 Cell，再评估新增 Cell。
3. 所有新开发必须基于 `KernelOne` 契约与运行时能力，不允许绕过 `KernelOne` 直连底层实现。
4. 复用优先级固定为：`existing cell public contract` > `kernelone contract` > `new implementation`。

### 4.3 Public/Internal Fence

跨 Cell 依赖只能走公开边界，禁止直接依赖其他 Cell 的 `internal/`。

### 4.4 Contract First

跨 Cell 协作必须通过契约表达：`command/query/event/result/error/stream/effect`。

### 4.5 Graph-Constrained Semantic

先 Graph 约束，再 Descriptor 排序；向量检索不得创建边界、扩大授权或绕开 graph。

### 4.6 Descriptor / Context / Verify 分工

1. Descriptor 用于检索
2. Context Pack 用于工作
3. Verify Pack 用于验证

禁止把三者混成单一万能资产。

### 4.7 Single State Owner

一个 source-of-truth 状态只能有一个 Cell 拥有写权限。

### 4.8 Explicit Effects

文件、数据库、网络、WebSocket、子进程、外部工具、LLM、Descriptor、Embedding、Semantic Index 都是 effect，必须显式声明并可审计。

### 4.9 UTF-8 Mandatory

所有文本文件读写必须显式 UTF-8。

### 4.10 No Dual Graph Truth

禁止引入 `.acga/graph` 或任何第二套 graph 真相目录。

### 4.11 Truthful Migration

未落地的目录、Cell、契约或流程，不得写成“当前已完成事实”。

### 4.12 Realtime Single-Rail（Nats-JetStream Only）

1. Polaris 产品实时链路只能使用统一 Nats-JetStream + `/v2/ws/runtime` WebSocket runtime.v2 协议。
2. 禁止新增或恢复 SSE、`StreamingResponse` 事件流、HTTP 长轮询、定时 HTTP 轮询、文件轮询伪实时、轮询兜底。
   - Searchable forbidden terms: 禁止轮询, HTTP long polling, timer HTTP polling, file polling, polling fallback, SSE.
3. 后端实时事件必须发布到 JetStream subject，并通过 `delivery/ws` runtime.v2 subject builder 暴露为 channel；前端必须通过 `RuntimeTransportProvider`/`runtimeSocketManager` 订阅。
4. HTTP endpoint 只能承担初始快照、显式用户刷新、一次性 command/query；不得作为产品实时刷新循环。
5. 测试 harness 可以轮询状态端点等待异步流程完成，但测试轮询不得被产品代码复用或包装成运行时机制。
6. 涉及首页、Factory、PM、ChiefEngineer、Director、ContextOS 的实时显示变更，必须附带 Playwright 证据证明页面从 WebSocket 推送更新，无刷新、无轮询。

### 4.13 Single Engineering Owner（默认 Director，按需协作）

1. 新工程运行的唯一 owner 是现有 `roles.runtime.public.engineering_service.EngineeringRunService`。HTTP、CLI、UI、取消、恢复与错误路径共用该服务，不新增 Cell、任务表、ledger 或第二 controller。
2. 默认 `workflow=direct`，Director 读取目标、调用工具、根据真实结果继续工作；PM/Chief Engineer 按需只读咨询。显式 `workflow=collaborative` 才执行 PM、Chief Engineer、Director 协作；未调用角色显示 `not_called`。咨询一层返回，不递归派生总控；讨论不创建工程 run。
3. PM/CE 使用可读文本提供规划、设计或审阅。新运行不要求 PM 合同、CE portfolio/blueprint、handoff/hash、OBL/covers 图、semantic patch、Task Market、JobToken/DEO/Run Ledger 旧链准入，也不受旧一 turn 一 batch 规则约束。普通编译/测试失败回到同一 Director，不自动 deterministic repair 或重启规划。
4. 工具授权来自 host 持久化且不可由模型扩大的 `PermissionScope`、当前 profile、预算与 deadline；显式空权限保持为空。归一化后仍须检查真实路径/symlink/允许根，经 KFS 读写和 OS/process sandbox 执行命令；不支持 containment 时明确拒绝，禁止裸跑。PM/CE/QA 默认只读，同 workspace 仅一个源码写入者。
5. 唯一工程事实记录是 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`，保存运行、事件、真实工具观察和验证；新运行不写旧 TaskBoard/TaskMarket/Run Ledger/role-session 镜像。保留 runtime.v2/Nats-JetStream、ContextOS 最终 provider request、UTF-8、实例 workspace 绑定与 storage 单根。
6. 角色回复结束与 `completed` 不等于 `accepted`。实际检查必须绑定当前源码；用户已声明检查必须保留，未声明时由 Director 结合实际工程选择并实际执行验证。空检查、模型自报、快照条数或 QA 文本不得冒充通过；任务要求的独立 QA 必须审查同一当前候选，拒绝后继续同一任务。
7. 恢复使用原身份、workspace、原始权限与 deadline，并重新校验当前 host policy；保留失败、interrupted/UNKNOWN 和未知效果，不自动重放可能已执行的命令或写入。旧 ID 不升级为新 run；历史按 `engine_version` 只读，旧在途运行留在原冻结 owner，不在新进程恢复旧写入口。
8. 旧 Factory/RoleRuntimeService/execute_role_session/RoleExecutionKernel 写准入与自动 fallback 退役；历史解码器、只读查询与显式可选技术工具可保留，禁止据此声称历史源码已全部删除。旧规范仅解释旧记录，不能重新约束新运行。底层 hash 可用于证据/快照完整性，不能要求模型填写业务 hash。真实 HTTP 次数与费用仅用物理证据，未知保持 UNKNOWN。

### 4.14 Role Tool Failure External Audit（主 Agent 专用，禁止产品化）

1. PM、Chief Engineer、Director、QA 任一角色出现工具调用失败、工具调用缺失、工具参数无法归一化、工具结果被误判成功、或 LLM 输出被错误送入 action/parser 时，主 Agent 可以安排 OpenCode 外部 Agent 独立审计，不能只凭主 Agent 口头推断结案。
2. OpenCode 审计只属于 Codex/Claude 等主 Agent 的外部工程协作手段，绝对不是 Polaris 后端、Factory、Run Ledger、ContextOS、ReceiptStore、runtime event、UI 或质量门禁的一部分。后端产品代码不得调度 OpenCode、等待 OpenCode、生成 `opencode_audit` 平台字段，或把 OpenCode 状态作为成功/失败依据。
3. 审计范围必须覆盖最终送入 provider 的 LLM request（messages、tool schema、response format、token 估算、覆盖度 flags）、工具调用解析与归一化链路、`ToolSpecRegistry` aliases/arg_aliases、runtime event、LLM 调用日志、ContextOS 证据、bench session 和角色日志；若事件中 `messages`/`content` 被 redacted，主 Agent 必须把 `context_snapshot_ref` 对应的 `runtime/contexts/<shard>/<hash>` 快照文件纳入自己的外部审计证据包，禁止只看 redacted event。
4. 外部 Agent 不应长期只做审计。对于已经有明确根因、授权文件范围、验收命令和无交叉依赖的账本项，主 Agent 应优先使用 Claude CLI JSON Sub-Agent 的 `mode=implementation` 让子 Agent 直接修改代码/测试/文档；OpenCode 审计默认只读，只在 Claude CLI 不可用或用户显式要求时作为兼容审计路径。任何可写子任务都必须互斥范围明确、不得跨桶写入，且完成后由主 Agent 复核 diff、运行门禁并提交。
5. 平台自身失败分类由真实工程原生证据完成：原目标/设计、Director Execution、LLM Output、Tool/Permission、Verification/QA、Context Budget、Baseline Issue、Runtime Environment；旧 PM Contract/Blueprint 只用于历史归因。无法归类时视为 Polaris 证据链缺口，先补当前工程 owner 的 runtime/tool observation/command 证据，不得用 OpenCode 审计状态补位。

### 4.15 Director Multi-Binding Degraded Execution

1. Director 多绑定/多路 fanout 中，单个 provider 多次连接失败或 readiness/connectivity 失败时，Factory 启动与 Director 调度必须显式跳过该 binding，只运行仍可用的 Director binding。
2. 该降级只适用于 Director 多绑定；PM、Chief Engineer、QA 以及单绑定 Director 仍必须 fail-closed。
3. 跳过 binding 不得静默 fallback：`/v2/llm/status` 必须展示 `DEGRADED`、`skipped_bindings`、`skip_reason`；bench/runtime 证据必须保留 provider_id、model、binding_id 与失败原因。
4. 如果 Director 所有 bindings 均不可用，Factory 必须 BLOCKED，不得继续运行。
5. LLM route audit 必须以实际可达/可用 binding 为准，不得要求已跳过的坏 binding 产生 LLM 调用；也不得把跳过伪装为该 binding 成功运行。

### 4.16 Bench Is Internal Test/Dev/Audit Harness Only

1. `Bench`、`Factory Bench`、`factory_bench`、`L1-L12 bench`、benchmark harness、压力测试脚本/API/UI 均只允许存在于内部测试/开发/审计模式，用来压测 Polaris、发现平台通用根因、生成审计样本。
2. 当前 L1-L12 压测、factory bench session、bench panel、bench API 可以在内部测试模式运行；它们不得进入正式项目功能、生产工作台、用户交付体验或控制面事实源。
3. 正式环境/生产环境不得出现 Bench 入口、Bench 菜单、Bench 面板、Bench 命名业务 API、Bench 专属状态模型或 Bench 文案。
4. Run Ledger、Job Token、ContextOS、ReceiptStore、Verifier/Gate Policy 等是平台内置基础设施，必须以平台级契约、平台级 projection、平台级 API 暴露；禁止把这些能力归属到 Bench 或依赖 `benchService` 承载正式语义。
5. Bench 可以作为平台基础设施的内部 producer/consumer：写入测试样本、读取 projection、聚合压力测试结果。但它只能验证平台能力，不得反向决定平台架构边界。
6. 如果某项能力未来要进入正式项目工作台，必须先从 Bench 命名空间抽离为平台级 Cell/contract/API/UI 类型，再由正式视图消费；禁止把 `factory_audits.json`、bench session、bench route、bench-only metadata 直接接入生产 UI。
7. 审计时发现生产路径、正式 UI、设置页、ContextOS、TaskBoard、QA 工作台或 public API 依赖 Bench 命名空间时，按 P0 边界污染处理，必须迁移到真实平台 owner 的 projection/API；工程 run 使用单份 engineering SQLite 投影，不能新增旧 Run Ledger 镜像。
8. Browser、视觉、多模态 QA、用户脚本、领域脚本等 verifier modality 是平台 Control Plane 的可选能力，默认关闭；设置页可以保存启用意图，但只有当前环境显式声明可用时才允许设为 hard-required evidence，禁止由 Bench 或内部压力测试状态决定正式项目必须启用这些能力。
9. 新工程写入与命令权限由 host `PermissionScope` 与当前 profile 控制；实际工具身份、效果、退出码进入单份工程记录。Job Token/旧 receipt chain 仅历史读取，不是新运行授权前置。

## 5. 根目录与归属裁决

规范根目录继续解释为：

- `bootstrap/` -> `polaris/bootstrap/`
- `delivery/` -> `polaris/delivery/`
- `application/` -> `polaris/application/`
- `domain/` -> `polaris/domain/`
- `kernelone/` -> `polaris/kernelone/`
- `infrastructure/` -> `polaris/infrastructure/`
- `cells/` -> `polaris/cells/`
- `tests/` -> `polaris/tests/`

共享真相资产继续保留在仓库顶层：

- `docs/graph/`
- `docs/governance/`
- `docs/templates/`

归属裁决顺序：

1. HTTP / WebSocket / CLI / transport -> `delivery/`
2. 用例编排 / workflow / 事务边界 -> `application/`
3. 业务规则 / 实体 / 策略 -> `domain/`
4. Agent/AI 通用 OS 能力 -> `kernelone/`
5. SDK / 存储 / 消息 / 插件 / 遥测适配 -> `infrastructure/`
6. 启动与装配 -> `bootstrap/`

旧根迁移状态（2026-04-24，Squad V 完成）：

- `app/`、`core/`、`api/`：已不存在于本仓库。
- `director_interface.py`：旧根 shim 已退役；唯一实现入口为 `polaris/delivery/cli/pm/director_interface_core.py`。
- `server.py`：旧根 shim 已退役；唯一后端服务入口为 `python -m polaris.delivery.server` / `polaris/delivery/server.py`。
- `scripts/`：仍保留（86 个文件），仅作为历史工具/诊断脚本；新功能必须写入 `polaris/delivery/cli/` 或对应 Cell 目录。

## 6. 开工前必做

中等及以上任务开工前必须确认：

1. 目标 Cell 或治理资产
2. 相关 subgraph
3. `owned_paths`
4. `depends_on`
5. `state_owners`
6. `effects_allowed`
7. `verification.gaps`
8. 若涉及 Context Plane / Descriptor / Semantic Index，确认 pack 与 `workspace/meta/context_catalog/*` 边界

## 7. 修改规则

1. 默认只修改目标 Cell 的 `owned_paths`
2. 修改公共边界、Descriptor、Semantic Search 或治理门禁时，至少同步评估：
   - `docs/graph/catalog/cells.yaml`
   - `docs/graph/subgraphs/*.yaml`
   - `docs/governance/schemas/*.yaml`
   - `docs/governance/ci/fitness-rules.yaml`
   - `docs/governance/ci/pipeline.template.yaml`
3. 禁止新增或扩大 `common/ helpers/ misc/ 无边界 utils/ base_utils.py`
4. 兼容层只能做薄垫片，禁止双边长期打补丁

## 8. 验证与结构性修复协议

### 8.1 基本规则

修改后必须明确说明：

1. 改了哪个 Cell 或治理资产
2. 是否跨 Cell
3. 是否触及公开契约、状态拥有、副作用或 Descriptor / Index 规则
4. 跑了什么验证
5. 哪些风险还没验证

### 8.2 验证门禁

对代码改动，必须实际运行并通过：

1. `ruff check <paths> --fix`
2. `ruff format <paths>`
3. `mypy <paths>`
4. `pytest <tests> -q`

### 8.3 自修复循环

若任一门禁失败：

1. 分析错误
2. 本地修复
3. 重新运行对应门禁
4. 连续同类失败 5 次再向人类求助

### 8.4 Verification Card / ADR 适用范围

对 `pattern` 或 `structural` 问题，必须执行 `§8.6`。

### 8.5 输出要求

禁止只说“应该可以”“大概没问题”。结论必须对应证据和验证。

### 8.6 Pre-Fix Thinking Protocol（修前思考协议）

#### 8.6.1 适用范围

所有 `pattern` 和 `structural` 级问题，修复前必须填写 Verification Card。

#### 8.6.2 分类

1. `one_off`: 局部错误，可直接修 + 测试
2. `pattern`: 同类错误重复出现，必须出 ADR 或设计文档
3. `structural`: 多模块共享同一错误假设，必须出 ADR

#### 8.6.3 强制步骤

1. 写出 Assumption Register
2. 逐条找代码证据验证假设
3. 做 pre-mortem，写明修错的最可能位置
4. 写 Verification Plan，具体到测试文件 / 命令 / 预期
5. 填写 Verification Card：
   - `docs/governance/templates/verification-cards/vc-<yyyymmdd>-<slug>.yaml`
6. 若分类为 `structural`，补 ADR：
   - `docs/governance/decisions/adr-<number>-<slug>.md`

## 9. 状态与副作用

1. 查询路径禁止偷写
2. `workspace/history/*` 不是运行时 source-of-truth
3. Descriptor / Embedding / Index 写入本身是 effect
4. 归档、压缩、解压与文本落盘都必须显式 UTF-8

## 10. 测试与质量门禁

优先跑与改动最相关的最小门禁集合；完成修复后再补回归。  
若改动涉及 runtime / contracts / governance，高风险门禁优先于大而全测试。

### 10.1 两阶段执行模型（Blueprint First）

本节约束主 Agent 修改 Polaris 平台时的工程设计流程；不是产品 engineering run 的 PM/CE 蓝图准入，不要求普通用户任务先生成文档或合同。

接到具体任务后，默认按两个阶段执行：

1. **阶段一：Blueprint & Architecture**
   - 先输出架构/重构方案
   - 方案必须落到 `docs/blueprints/*.md`
   - 至少包含：文本架构图、模块职责、核心数据流、技术理由
2. **阶段二：Execution & Implementation**
   - 再按任务类型落地实现、重构、修复、测试或文档更新
   - 实施前后都要受本文件门禁约束

除极小型纯文字修正外，禁止跳过 blueprint 直接进入实现。

### 10.2 工程标准（Engineering Standards）

所有实现与重构默认遵守：

1. 严格基于 Ruff/Black 约束的现代 PEP 8
2. 清晰命名、单一职责、低耦合、高内聚、隐藏内部状态
3. 防御性编程：类型注解、边界处理、合理异常处理；禁止裸 `except:`
4. 关键类和复杂函数应有清晰 docstring
5. 严禁过度设计、炫技、隐藏副作用和重复代码
6. 类型安全优先：以 `mypy --strict` / 等价严格类型门禁为目标
7. 默认按工程化模块组织，而不是临时脚本堆砌

### 10.3 任务协议（Task Protocols）

1. **新需求/写代码**：交付可生产使用的完整实现，不交付伪代码
2. **重构**：默认无损重构，保持外部接口和行为一致，并说明改进维度
3. **代码审查**：按 `Blocker / Suggestion / Nitpick` 输出，包含定位、根因、建议和严重度
4. **Bug 修复**：必须写清现象、根因和防御性修复方案，禁止头痛医头
5. **测试编写**：默认使用 `pytest`，覆盖 Happy Path、Edge Cases、Exceptions、Regression

### 10.4 输出结构（Output Format）

交付说明默认按以下顺序组织：

1. `结果 (Result)`
2. `分析 (Analysis)`
3. `风险与边界 (Risks & Boundaries)`
4. `测试 (Testing)`
5. `自检 (Self-Check)`
6. `后续优化 (Future Optimization)`

### 10.5 Benchmark / 矩阵测试只走 agentic-eval CLI（强制）

> 边界声明：本节所有 benchmark/bench/matrix 语义均指内部测试/开发/审计 harness。它们可以在当前测试模式运行，但不得作为生产功能、正式项目工作台、用户可见产品入口或控制面事实源暴露。

1. 任何**矩阵测试 / benchmark / 评分跑分**必须表达为 agentic-eval **CASE JSON**
   （`polaris/cells/llm/evaluation/fixtures/agentic_benchmark/cases/*.json`），
   并通过 agentic-eval CLI 运行：`python -m polaris.delivery.cli.agentic_eval --suite <suite> [--level l1-l6]`。
2. **禁止**把 benchmark / 矩阵的"评分跑分"写成 pytest（即 `test_*.py` 中调用
   `run_*_suite(...)` 或 `UnifiedBenchmarkRunner().run_suite(...)`）。pytest 只允许承载
   框架**组件**单测（models / validators / helpers），不得承载 suite/matrix 执行。
3. 新增校验器属于 agentic-eval 框架的 judge 组件（`unified_judge.py`），通过 CASE 的
   `judge.validators` 引用并经 CLI 评分，不是 pytest benchmark。
4. 门禁：`docs/governance/ci/scripts/check_no_pytest_benchmark.py`
   （fitness-rule `benchmark_cli_only`，enforced 于
   `polaris/tests/architecture/test_no_pytest_benchmark_gate.py`）。

## 11. 交付要求

交付时至少说明：

1. 改动范围
2. 根因或设计理由
3. 已完成验证
4. 剩余风险

## 12. 禁止事项

1. 禁止绕过 graph 和 Cell 边界
2. 禁止把规划态写成现状
3. 禁止引入第二套 graph truth 或 handoff truth
4. 禁止未经声明的副作用
5. 禁止为了过测试回退历史旧实现“续命”

## 13. 镜像同步规则

1. `CLAUDE.md` 与 `GEMINI.md` 只是镜像摘要
2. 修改 `§15 / §16 / §17` 时，必须同步三个指令文件
3. 若存在冲突，以本文件为准并立即修复镜像漂移

## 14. 执行自检

动手前自问：

1. 我修改的是哪个 Cell 或治理资产？
2. 我是否先看了 graph、`FINAL_SPEC.md` 和所需 ACGA 2.0 文档？
3. 我是否只改了受控边界？
4. 我是否引入了未声明 effect？
5. 我是否给出了真实验证结论？

若任何一项回答不清楚，先不要写代码。

---

## 15. 当前架构现实快照（2026-05-07）

本节记录当前事实，不得与目标态混写。修改须同步 `CLAUDE.md` 与 `GEMINI.md`。

### 15.1 Graph 图谱现状

- `docs/graph/catalog/cells.yaml` — `migration_status: phase1_public_phase2_composite_phase3_business_cells_declared`
- cells.yaml 声明的 Cell：**62 个**（统计命令：`grep "^  - id:" docs/graph/catalog/cells.yaml | wc -l`，2026-05-07）
- `polaris/cells/*/generated/descriptor.pack.json` 当前覆盖：**63 / 62**
- `docs/graph/subgraphs/` 当前有 **15** 个 subgraph yaml（统计命令：`ls docs/graph/subgraphs/*.yaml | wc -l`）：
  - `archive_pipeline.yaml`
  - `audit_pipeline.yaml`
  - `code_intelligence_pipeline.yaml`
  - `context_assembly_pipeline.yaml`
  - `context_plane.yaml`
  - `director_pipeline.yaml`
  - `director_workflow_pipeline.yaml`
  - `event_pipeline.yaml`
  - `execution_governance_pipeline.yaml`
  - `finops_pipeline.yaml`
  - `knowledge_pipeline.yaml`
  - `llm_pipeline.yaml`
  - `pm_pipeline.yaml`
  - `roles_execution_pipeline.yaml`
  - `storage_archive_pipeline.yaml`

### 15.2 polaris/ 结构现状（`*.py` 快照，2026-05-07）

统计命令：`find polaris -name "*.py" | awk -F/ '{print $2}' | sort | uniq -c`

- `polaris/bootstrap/`: 16
- `polaris/delivery/`: 279
- `polaris/application/`: 16
- `polaris/domain/`: 44
- `polaris/kernelone/`: 1143
- `polaris/infrastructure/`: 155
- `polaris/cells/`: 1238
- `polaris/tests/`: 897
- `polaris/config/`: 5
- **总计**：**3796** 个 Python 文件

### 15.3 测试与收集现状

- `pytest --collect-only -q`（2026-05-07）结果：**28677 collected / 0 errors**
- 真实覆盖率（2026-04-24）：**23.3%**（69360/297487 lines，`pytest --cov=polaris`）
- 0% 覆盖率模块：390 个（delivery: 155, cells: 103, kernelone: 103, infrastructure: 20, bootstrap: 7, application: 1, domain: 1）

### 15.4 当前主要 gap

1. Descriptor 覆盖已提升至 **63 / 62**
2. ~~部分历史 Cell 仍未完成 `depends_on` 对齐（catalog gate 中 26 个 high 级别遗留、9 个 blocker）~~ 已于 2026-05-07 清零（catalog governance gate 现 issue_count=0 / blocker_count=0 / high_count=0）
3. ~~`fitness-rules.yaml` blocker 尚未全量自动化执行~~ 已于 2026-05-07 启动 Stage 1 接入（`.github/workflows/governance-gates.yml` audit-only），Stage 2-5 待后续 wave 启用（详见 `docs/governance/ci/STAGED_ROLLOUT_PLAN.md`）
4. `KERNELONE_` 与 `KERNELONE_` 仍混用
5. ~~`.gitignore` 字面 `.github` 规则导致所有 GHA workflow 文件未被 git 跟踪（GHA 实际从未运行过任何 workflow）~~ 已于 2026-05-07 修复（删除 `.gitignore` 第 97 行 `.github` 字面规则；stage 仓库根 4 个 workflow；删除错位的 `src/backend/.github/workflows/performance.yml`）

### 15.5 未登记 Cell（已清零）

以下 Cell 已于 2026-04-25 全部补登至 `cells.yaml`，无剩余未登记 Cell：

- ~~`roles.host`~~
- ~~`director.delivery`~~
- ~~`director.runtime`~~
- ~~`director.planning`~~
- ~~`director.tasking`~~

### 15.6 环境变量前缀现状（2026-05-07）

- `KERNELONE_`: **1825 处 / 375 文件**
- `KERNELONE_`: **225 处 / 43 文件**

### 15.7 CLI 入口点（已更新）

- 后端服务：`python -m polaris.delivery.server --host 127.0.0.1 --port 49977`
- PM CLI：`python -m polaris.delivery.cli.pm.cli`
- Director CLI：`python -m polaris.delivery.cli.director.cli_thin`
- Architect CLI：`python -m polaris.cells.architect.design.internal.architect_cli`
- Chief Engineer CLI：`python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli`
- Console：`python -m polaris.delivery.cli console --backend plain`

---

## 16. 自动化治理工具

### 16.1 Descriptor Pack 批量生成器

**命令**: `python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`

用途：批量生成 `polaris/cells/*/generated/descriptor.pack.json`。  
任何涉及 `owned_paths` 内 Python 源码或公共 docstring 的改动，提交前应评估是否需要执行。

### 16.2 KernelOne 发布门禁执行器

**命令**: `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

### 16.3 Catalog 治理门禁

**命令**: `python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

迁移期默认阻断模式是 `fail-on-new`；`hard-fail` 只适用于已清债域。

### 16.4 当前 CI/CA 门禁矩阵（2026-04-16）

当前后端治理以 `docs/governance/ci/pipeline.template.yaml` 为准。关键 gate：

1. `catalog_governance_audit`
2. `catalog_governance_fail_on_new`
3. `catalog_governance_hard_fail`
4. `kernelone_release_gate`
5. `delivery_cli_hygiene_gate`
6. `opencode_convergence_gate`
7. `manifest_catalog_reconciliation_gate`
8. `structural_bug_governance_gate`
9. `tool_calling_canonical_gate`

补充规则：

- `opencode_convergence_gate` 若存在，只能用于审计本仓已引入的 OpenCode 机制兼容代码，禁止触发外部 OpenCode CLI、禁止调度外部 Agent、禁止作为 Factory/角色运行成功条件。

1. `docs/governance/ci/fitness-rules.yaml` 中的 `agent_instruction_snapshot_consistent` 要求 `AGENTS.md / CLAUDE.md / GEMINI.md` 的快照事实保持一致。
2. 修改 `§15 / §16 / §17` 时必须同步三个指令文件。

---

## 17. 历史目标态边界（2026-04-16，已由工程主线替代）

旧 TransactionKernel 单一 turn commit、一 turn 一 ToolBatch、panic/handoff_workflow、ContextHandoffPack 与合同控制面属于历史引擎契约，仅用于解码、只读审计或原冻结实例。新运行不得以这些规则限制 native 工具循环，不得从错误/恢复路径回流旧内核。

继续保留技术原则：权限由 host 决定；模型与工具内容不成为控制面权威；最终 provider request 原样审计；prompt 上下文只选择相关证据；失败与 UNKNOWN 不能改成成功。当前实现与历史设计的差距必须明确记录。

See docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md
