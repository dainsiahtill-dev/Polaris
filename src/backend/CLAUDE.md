# Claude Backend Playbook

适用范围: `src/backend`  
权威规则: `AGENTS.md`

本文件是 `AGENTS.md` 的镜像摘要，不是独立权威。若冲突，以 `AGENTS.md` 为准。

---

## 0. 强制工具链镜像

- 所有 shell 命令和 chained segment 必须以 `rtk` 开头；优先 RTK 原生命令，否则使用 `rtk proxy`。
- 源码发现、架构/调用链/影响分析、代码审查和修改前上下文必须先使用 CodeGraph MCP；其返回源码视为已读，不重复读取。CodeGraph 未覆盖或不可用时才使用 RTK fallback，并记录原因。
- 默认启用 `caveman(full)` 压缩更新与报告；精确错误、命令、验证数字、风险和未完成门禁不得省略。安全警告、不可逆操作和歧义流程使用完整表达。
- 固定顺序：适用 Skill -> CodeGraph -> RTK 执行/验证 -> Caveman 汇报。三者必须共同使用，不得互相替代。

---

## 1. 裁决顺序

固定按以下顺序裁决：

1. `AGENTS.md`
2. `docs/AGENT_ARCHITECTURE_STANDARD.md`
3. `docs/graph/catalog/cells.yaml` 与 `docs/graph/subgraphs/*.yaml`
4. `docs/FINAL_SPEC.md`
5. ACGA 2.0 文档
6. 2026-04-16 的 Blueprint / VC / ADR

规则：先服从当前 graph，再按 `FINAL_SPEC.md` 判断迁移方向，最后应用 ACGA 2.0 增强规则。

### 1.1 无人值守完成闭环镜像

- Exact identity 固定为 `(workspace, project_id, run_id, completion_contract_hash)`；禁止 latest-run、跨 workspace fallback 或 caller 自报身份。
- Chief Engineer 拥有 whole-project typed completion contract；Director 只做 hash-bound 无损传递。合同必须覆盖 owner task、artifact、canonical verifier argv/cwd/modality、entrypoint或显式 N/A。
- VerificationGuard 负责物理验证和 obligation-bound typed receipts，但不拥有最终成功；caller evidence、TaskBoundary、stage gate、generic audit receipt、日志摘要与磁盘猜测都不是权威。
- workflow runtime 只拥有 durable convergence cursor，并使用跨进程 CAS、reserve-before-effect、deterministic action id 与 crash replay；不得在 Factory/adapter/bench 中复制重试状态机。
- 角色失败必须阶段局部恢复，禁止默认重跑整条 `PM -> Chief Engineer -> Director`：CE 输出/schema 失败只重试 `chief_engineer_review`，复用已提交 PM contract 并把失败证据注入下一次最终 provider request；Director 失败只保留已完成 task、重开未完成 task，执行 bounded `edit/repair -> rerun affected verifier`；QA/Verifier 失败只回到 exact owner Director task。PM 仅在 PM contract 本身无效或被显式 supersede 时重跑。TaskMarket requeue 必须以 action id 原子幂等；达到局部预算后输出 model ceiling/blocker，不得把失败改写成 PASS 或自动升级上游。
- `mutation_bypass_blocked` 是 owner Director 的普通局部恢复态：只有 `Director + materialize_changes` 可保留同一 session、已提交合同、最终请求上下文、读取结果和失败 receipt，下一 turn 直接 edit/write 后只重跑受影响 verifier；禁止创建新 Director 规划会话或重启 PM/CE。该恢复必须有独立于自适应 extra-turn 的硬预算（当前最多 2 次 bypass）；耗尽后输出 `director_quality_repair_stalled`、`model_ceiling`、`retry_scope=same_director_task_only` 并停止 Provider 调用。`inline_patch_escape_blocked` 等权限/策略边界仍 fail-closed。
- Provider structured-output 若把 schema 声明的 object/array 额外 JSON 字符串化，或把 root siblings 串进首个 object field，必须先在 `roles.kernel` 传输层做 caller-schema-proven 有界归一化，禁止直接重跑整角色。只允许 JSON container 解码与单层已知无效 escape 修正；结果必须仅含声明字段、保持现有 sibling 不变并通过完整 JSON Schema，否则 fail-closed。归一化必须投影 `schema_normalization_applied` / `schema_normalization_policy`，不得承担语义修复。
- Director 局部 repair 的“进展”必须同时满足：authoritative write receipt 对应负责路径的真实 fingerprint 变化、required verifier 复跑、诊断或 missing target 净减少且不引入新诊断。纯读取、同内容写入、只改变诊断签名、等量换错或错误增多一律记为 stagnation；连续 2 次 stagnation 必须停止 Provider 调用，输出 `director_quality_repair_stalled` + `model_ceiling`，并保持 `retry_scope=same_director_task_only`、禁止回退 PM/CE。
- TaskRuntime 执行历史与 TaskBoundary 交付权威是独立轴。Factory 权威投影只能从已提交 PM stage event 的 immutable artifact binding 重验并取得 PM 合同任务，禁止从可变 `tasks/plan.json`/mirror 重建完成义务；PM contract IDs、TaskRuntime IDs、TaskBoundary IDs 归一化后集合必须完全一致。缺失合同任务、`N`/`TASK-N` 合同 alias collision、重复 runtime identity 必须 fail-closed，settlement/verifier 辅助行与 boundary 不得新增或污染交付义务。TaskRuntime 已终态 `failed/cancelled` 时，只有 canonical `completed_verified` TaskBoundary 同时具备 ledger append/content 坐标、evidence refs、零 missing/failed obligation，才可保留失败历史并授权进入 QA；`pending/in_progress/blocked` 或仅磁盘扫描永远不得放行。
- Factory stage 失败不得仅以“可能本地修复”为由占住 workspace lease。local rework 必须查询 TaskMarket canonical `TaskRequeueReceiptV1`，校验 workspace/task/idempotency/receipt hash，并证明 owner 属于 immutable PM contract、TaskRuntime 绑定当前 factory run、目标阶段符合局部恢复策略（CE=`pending_design`；Director/QA=`pending_exec`）；纯 metadata、伪 receipt ref、辅助 task、错 factory/task/stage receipt 一律不得 defer drain。否则必须写 `completed_at`、终结并释放 lease。Director/QA 普通修复优先在当前 stage 或 owner task 内完成，不能靠无 receipt 的 `decision_pending` 延迟终态。
- DEO member 已 claim 为 `EFFECT_STARTED` 后，任何物理执行前的 policy/target drift 拒绝必须先消费 one-use fence，再以 recovery + `DEAD_LETTER` 收敛；不得把 claimed denial 留成永久非终态。fence 或终态写入有歧义时保留 reconciliation blocker，禁止 abort 相关 contingency 或伪造成功。
- 运行时观察链不得复制 Durable TaskRuntime/Provider/Tool 全量证据。事实流与持久化快照保留完整 payload；runtime.v2/NATS/状态投影只能发送有界语义摘要、不可变 fact 坐标与可解析 evidence ref，默认单事件预算 64 KiB。高频事件只能按明确 canonical kind 触发 status refresh，并采用单任务 latest-wins 合并；禁止用模糊字符串匹配、每事件/每连接同步全量重建或对子树反复 JSON 序列化，避免 observer 反向拖死执行面。
- runtime.projection 独占 `completed_verified` 签发。`model_ceiling` 必须由 owner-query facts 封存，禁止由自报 attempt/budget/JSON 触发。
- Bench 仅用于门禁全绿后的稀缺验证；失败必须先落唯一 residual/module attribution。外部 Supervisor 只做项目调度，不进入平台事实源或成功条件。
- `/tmp` 只用于可丢弃 scratch/worktree；bench 审计、最终请求快照、缺陷 manifest 与 handoff 必须持久化到 `~/.polaris/bench_runs`、`~/.polaris/audit_archives` 或仓内治理资产。

## 2. 多实例与 Bench 边界速记

- Polaris 后端实例仍然是单 workspace 绑定；多项目观测通过平台级 Instance Registry + `/launcher` 管理多个独立实例。
- Launcher 打开的实例页面必须携带 `instance` / `backend` / `token` / `workspace` 绑定；后端与前端都必须把该 workspace 绑定传入 API/WebSocket 观测链路，禁止回退到默认主仓 workspace。
- 需要被总控观测的 CLI/Agent/内部测试启动项必须通过 `python -m polaris.delivery.cli.backend serve --register-instance ...` 或 `/v2/instances` 注册。
- Instance Registry 只是发现/运维视图，不是 PM、Chief Engineer、Director、QA、ContextOS、ReceiptStore 或 Run Ledger 的事实源。
- `factory_bench` / L1-L12 只属于内部测试态；可以注册 `kind=bench_project` 供总控观测。共享后端注册只能视为可观测测试实例，不能冒充独立生产实例，也不得把 Bench 语义写成生产项目模型。
- `metadata.backend_binding=shared_backend_workspace_switch` 的 `bench_project` 执行 restart/独立启动时必须分配新 backend/frontend 端口，禁止复用共享 backend 端口。
- 多 Agent 并行跑 `factory_bench` 时 runner 必须显式使用 `--launcher-instance-mode isolated --bench-session-reporting off`，确保每个项目的 Factory run 打到独立 backend；Launcher 可见性来自 Instance Registry 和项目实例自己的 runtime.v2。共享主后端 `/v2/factory/bench/sessions` 只是内部兼容观测桥，只有串行调试时才允许 `--launcher-instance-mode observed --bench-session-reporting shared`。
- Launcher 实时状态只走 runtime.v2 WebSocket `status.instances`，禁止新增 HTTP polling 或文件轮询。

## 3. 最小必要规则

### 3.1 Graph / Cell / KernelOne

1. Graph 是唯一架构真相
2. Cell 是最小自治边界
3. 先复用公开 Cell 能力，再复用 `KernelOne`，最后才新增实现

### 3.2 Public Contract / Effects / UTF-8

1. 跨 Cell 只能走公开契约，禁止直连 `internal/`
2. 文件、数据库、网络、子进程、LLM、Descriptor、Embedding、Index 都是 effect，必须可审计
3. 所有文本读写必须显式 UTF-8

### 3.3 归属与旧根冻结

规范根目录统一落在 `polaris/` 下。

旧根迁移状态（2026-04-24，Squad V 完成）：
- `app/`、`core/`、`api/`：已不存在于本仓库。
- `director_interface.py`：旧根 shim 已退役；唯一实现入口为 `polaris/delivery/cli/pm/director_interface_core.py`。
- `server.py`：旧根 shim 已退役；唯一后端服务入口为 `python -m polaris.delivery.server` / `polaris/delivery/server.py`。
- `scripts/`：仍保留（86 个文件），仅作为历史工具/诊断脚本；新功能必须写入 `polaris/delivery/cli/` 或对应 Cell 目录。

## 3. 默认工作入口

开始中大型修改前，按顺序读取：

1. `docs/AGENT_ARCHITECTURE_STANDARD.md`
2. `docs/graph/catalog/cells.yaml`
3. 相关 subgraph
4. `docs/FINAL_SPEC.md`
5. 任务需要时再读 ACGA 2.0 文档
6. 目标 Cell 的 `cell.yaml`、`README.agent.md`、packs 与公开契约

不要先全仓扫描源码。

## 4. 动手前与验证

开始修改前至少确认：

1. 目标 Cell 或治理资产
2. `owned_paths / depends_on / state_owners / effects_allowed / verification.gaps`
3. 是否触及 Descriptor / Context Plane / Semantic Index

代码改动必须实际运行并通过最小门禁：

1. `ruff check <paths> --fix`
2. `ruff format <paths>`
3. `mypy <paths>`
4. `pytest <tests> -q`

结构性问题遵循 `AGENTS.md §8.6`：Verification Card + 必要 ADR。

### 4.1 两阶段执行模型

1. 先做 `Blueprint & Architecture`
   - 方案先落到 `docs/blueprints/*.md`
   - 至少包含：文本架构图、模块职责、核心数据流、技术理由
2. 再做 `Execution & Implementation`

除极小型纯文字修正外，默认不能跳过 blueprint 直接实现。

### 4.2 工程标准

1. 遵循 Ruff/Black 约束下的现代 PEP 8
2. 清晰命名、单一职责、低耦合、高内聚
3. 类型注解、防御性边界处理、合理异常处理；禁止裸 `except:`
4. 关键类和复杂函数需要清晰 docstring
5. 严禁过度设计、炫技、隐藏副作用、重复代码

### 4.3 任务协议与输出

1. 新需求：交付可生产使用的完整实现
2. 重构：默认无损重构，保持外部接口和行为一致
3. Bug 修复：写清现象、根因、防御性修复
4. 测试：默认 `pytest`，覆盖正常/边界/异常/回归
5. 输出结构默认按：
   - `Result`
   - `Analysis`
   - `Risks & Boundaries`
   - `Testing`
   - `Self-Check`
   - `Future Optimization`

## 5. 交付要求

交付时至少说明：

1. 改了哪个 Cell 或治理资产
2. 是否跨 Cell
3. 是否触及契约 / 状态拥有 / effect / Descriptor / Index
4. 跑了什么验证
5. 还剩哪些风险

---

## 6. 当前架构现实快照（2026-05-07）

> 本节是 `AGENTS.md §15` 的镜像摘要。如有冲突，以 `AGENTS.md` 为准。

### 6.1 Graph 图谱现状

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

### 6.2 polaris/ 结构现状（`*.py` 快照，2026-05-07）

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

### 6.3 测试与收集现状

- `pytest --collect-only -q`（2026-05-07）结果：**28677 collected / 0 errors**
- 真实覆盖率（2026-04-24）：**23.3%**（69360/297487 lines，`pytest --cov=polaris`）
- 0% 覆盖率模块：390 个（delivery: 155, cells: 103, kernelone: 103, infrastructure: 20, bootstrap: 7, application: 1, domain: 1）

### 6.4 当前主要 gap

1. Descriptor 覆盖已提升至 **63 / 62**
2. ~~部分历史 Cell 仍未完成 `depends_on` 对齐（catalog gate 中 26 个 high 级别遗留、9 个 blocker）~~ 已于 2026-05-07 清零（catalog governance gate 现 issue_count=0 / blocker_count=0 / high_count=0）
3. ~~`fitness-rules.yaml` blocker 尚未全量自动化执行~~ 已于 2026-05-07 启动 Stage 1 接入（`.github/workflows/governance-gates.yml` audit-only），Stage 2-5 待后续 wave 启用（详见 `docs/governance/ci/STAGED_ROLLOUT_PLAN.md`）
4. `KERNELONE_` 与 `KERNELONE_` 仍混用
5. ~~`.gitignore` 字面 `.github` 规则导致所有 GHA workflow 文件未被 git 跟踪（GHA 实际从未运行过任何 workflow）~~ 已于 2026-05-07 修复（删除 `.gitignore` 第 97 行 `.github` 字面规则；stage 仓库根 4 个 workflow；删除错位的 `src/backend/.github/workflows/performance.yml`）

### 6.5 未登记 Cell（已清零）

以下 Cell 已于 2026-04-25 全部补登至 `cells.yaml`，无剩余未登记 Cell：

- ~~`roles.host`~~
- ~~`director.delivery`~~
- ~~`director.runtime`~~
- ~~`director.planning`~~
- ~~`director.tasking`~~

### 6.6 环境变量前缀现状（2026-05-07）

- `KERNELONE_`: **1825 处 / 375 文件**
- `KERNELONE_`: **225 处 / 43 文件**

### 6.7 CLI 入口点（已更新）

- 后端服务：`python -m polaris.delivery.server --host 127.0.0.1 --port 49977`
- PM CLI：`python -m polaris.delivery.cli.pm.cli`
- Director CLI：`python -m polaris.delivery.cli.director.cli_thin`
- Architect CLI：`python -m polaris.cells.architect.design.internal.architect_cli`
- Chief Engineer CLI：`python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli`
- Console：`python -m polaris.delivery.cli console --backend plain`

---

## 7. 自动化治理工具

### 7.1 Descriptor Pack 批量生成器

`python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`

### 7.2 KernelOne 发布门禁执行器

`python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

### 7.3 Catalog 治理门禁

`python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

### 7.4 当前 CI/CA 门禁矩阵（2026-04-16）

关键 gate：

- `catalog_governance_audit`
- `catalog_governance_fail_on_new`
- `catalog_governance_hard_fail`
- `kernelone_release_gate`
- `delivery_cli_hygiene_gate`
- `opencode_convergence_gate`
- `manifest_catalog_reconciliation_gate`
- `structural_bug_governance_gate`
- `tool_calling_canonical_gate`

补充规则：

1. `agent_instruction_snapshot_consistent` 要求三份指令文件的快照事实一致
2. 修改 `AGENTS.md §15 / §16 / §17` 时必须同步镜像文件

---

## 8. 最新目标态治理裁决（2026-04-16，非当前事实）

> 本节是 `AGENTS.md §17` 的镜像摘要，不是当前现实快照。

### 8.1 权威来源

- `../../docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md`
- `docs/governance/templates/verification-cards/vc-20260416-transaction-kernel-contextos-tool-refactor.yaml`
- `docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md`
- `docs/blueprints/AGENT_INSTRUCTION_ALIGNMENT_BLUEPRINT_20260416.md`
- `docs/blueprints/AGENT_INSTRUCTION_COMPACTION_BLUEPRINT_20260416.md`
- `docs/blueprints/AGENT_ENGINEERING_DISCIPLINE_ALIGNMENT_BLUEPRINT_20260416.md`

### 8.2 核心裁决

1. `TransactionKernel` 是唯一 turn 事务执行内核与唯一 commit point
2. 一个 turn 内必须满足：`len(TurnDecisions) == 1`、`len(ToolBatches) <= 1`、`hidden_continuation == 0`
3. ContextOS 目标态固定为 `TruthLog / WorkingState / ReceiptStore / ProjectionEngine`
4. control-plane 字段不得进入 data plane
5. `ContextHandoffPack` 是 canonical handoff contract，`roles.kernel` 禁止再造第二套 handoff schema

### 8.3 镜像规则

1. 本文件不是独立权威
2. 若 `AGENTS.md §15 / §16 / §17` 更新，必须同步更新本文件

---

See docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md
