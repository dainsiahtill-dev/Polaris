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

### 1.1 当前工程主线镜像

1. 新工程运行的唯一 owner 是现有 `roles.runtime.public.engineering_service.EngineeringRunService`。HTTP、CLI、UI、取消、恢复与错误路径共用该服务，不新增 Cell、任务表、ledger 或第二 controller。
2. 默认 `workflow=direct`，Director 读取目标、调用工具、根据真实结果继续工作；PM/Chief Engineer 按需只读咨询。显式 `workflow=collaborative` 才执行 PM、Chief Engineer、Director 协作；未调用角色显示 `not_called`。咨询一层返回，不递归派生总控；讨论不创建工程 run。
3. PM/CE 使用可读文本提供规划、设计或审阅。新运行不要求 PM 合同、CE portfolio/blueprint、handoff/hash、OBL/covers 图、semantic patch、Task Market、JobToken/DEO/Run Ledger 旧链准入，也不受旧一 turn 一 batch 规则约束。普通编译/测试失败回到同一 Director，不自动 deterministic repair 或重启规划。
4. 工具授权来自 host 持久化且不可由模型扩大的 `PermissionScope`、当前 profile、预算与 deadline；显式空权限保持为空。归一化后仍须检查真实路径/symlink/允许根，经 KFS 读写和 OS/process sandbox 执行命令；不支持 containment 时明确拒绝，禁止裸跑。PM/CE/QA 默认只读，同 workspace 仅一个源码写入者。
5. 唯一工程事实记录是 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`，保存运行、事件、真实工具观察和验证；新运行不写旧 TaskBoard/TaskMarket/Run Ledger/role-session 镜像。保留 runtime.v2/Nats-JetStream、ContextOS 最终 provider request、UTF-8、实例 workspace 绑定与 storage 单根。
6. 角色回复结束与 `completed` 不等于 `accepted`。实际检查必须绑定当前源码；用户已声明检查必须保留，未声明时由 Director 结合实际工程选择并实际执行验证。空检查、模型自报、快照条数或 QA 文本不得冒充通过；任务要求的独立 QA 必须审查同一当前候选，拒绝后继续同一任务。
7. 恢复使用原身份、workspace、原始权限与 deadline，并重新校验当前 host policy；保留失败、interrupted/UNKNOWN 和未知效果，不自动重放可能已执行的命令或写入。旧 ID 不升级为新 run；历史按 `engine_version` 只读，旧在途运行留在原冻结 owner，不在新进程恢复旧写入口。
8. 旧 Factory/RoleRuntimeService/execute_role_session/RoleExecutionKernel 写准入与自动 fallback 退役；历史解码器、只读查询与显式可选技术工具可保留，禁止据此声称历史源码已全部删除。旧规范仅解释旧记录，不能重新约束新运行。底层 hash 可用于证据/快照完整性，不能要求模型填写业务 hash。真实 HTTP 次数与费用仅用物理证据，未知保持 UNKNOWN。

运行时推送保留有界摘要和可读证据引用，不能复制全量 provider/tool 历史拖慢执行；最终请求与完整工具观察保存在其真实 owner。审计/最终快照持久化到 `~/.polaris/` 或目标 canonical runtime。

## 2. 多实例与 Bench 边界速记

- Polaris 后端实例仍然是单 workspace 绑定；多项目观测通过平台级 Instance Registry + `/launcher` 管理多个独立实例。
- Launcher 打开的实例页面必须携带 `instance` / `backend` / `token` / `workspace` 绑定；后端与前端都必须把该 workspace 绑定传入 API/WebSocket 观测链路，禁止回退到默认主仓 workspace。
- 需要被总控观测的 CLI/Agent/内部测试启动项必须通过 `python -m polaris.delivery.cli.backend serve --register-instance ...` 或 `/v2/instances` 注册。
- Instance Registry 只是发现/运维视图，不是 PM、Chief Engineer、Director、QA、ContextOS、ReceiptStore 或 Run Ledger 的事实源。
- `factory_bench` / L1-L12 只属于内部测试态；可以注册 `kind=bench_project` 供总控观测。共享后端注册只能视为可观测测试实例，不能冒充独立生产实例，也不得把 Bench 语义写成生产项目模型。
- `metadata.backend_binding=shared_backend_workspace_switch` 的 `bench_project` 执行 restart/独立启动时必须分配新 backend/frontend 端口，禁止复用共享 backend 端口。
- 仅在显式冻结旧引擎内部测试中，多 Agent 并行跑 `factory_bench` 时 runner 必须显式使用 `--launcher-instance-mode isolated --bench-session-reporting off`，确保每个项目的 Factory run 打到独立 backend；Launcher 可见性来自 Instance Registry 和项目实例自己的 runtime.v2。共享主后端 `/v2/factory/bench/sessions` 只是内部兼容观测桥，只有串行调试时才允许 `--launcher-instance-mode observed --bench-session-reporting shared`。
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
4. 任一 Bench/角色/工具/QA/runtime/控制面失败必须先动态调试 exact run；静态分析只解释动态证据。目标项目只读且禁止修改，修复只能落 Polaris 元平台，并补回归测试、同运行验证、机器可读缺陷记录。
5. 新工程 exact-run 归因先读同 workspace 的 engineering snapshot/SQLite、最终 provider request、tool observation、当前 verifier 和 QA；不转查 latest Factory run 或写旧 ledger。历史 Factory causal API 只解释旧记录。保留当前失败/UNKNOWN，定位 owner 与最窄修复边界。

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

本节约束主 Agent 修改 Polaris 平台时的工程设计流程；不是产品 engineering run 的 PM/CE 蓝图准入，不要求普通用户任务先生成文档或合同。

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

## 8. 历史治理镜像

`AGENTS.md §17` 中的旧 TransactionKernel/ContextHandoffPack 设计仅用于历史读取与冻结旧 owner；新工程不采用一 turn 一 ToolBatch、旧 commit/handoff 或业务合同准入。当前工程主线、技术权限与实际验证规则见 §1.1；本文件不得复活旧引擎写入口。

See docs/blueprints/COGNITIVE_LIFEFORM_ARCHITECTURE_ALIGNMENT_MEMO_20260417.md
