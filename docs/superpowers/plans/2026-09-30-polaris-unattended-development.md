# Polaris 无人值守自动化开发 Implementation Plan

> **For agentic workers:** 使用 `superpowers:executing-plans` 逐包执行；只有共享接口已确定、文件范围互斥时才用 `superpowers:subagent-driven-development`。主 Agent 独立验收。用户要求不新建分支，默认当前主仓排他修改；不得手工改生成项目。本文交付计划，不表示任务已实施。

**Goal:** 在已有 Polaris 基础上建立可持久恢复、动态可诊断、物理证据闭合的无人值守开发流程，并以固定版本的单项目和 L1-L12 矩阵证明。

**Architecture:** 保留 PM → Chief Engineer → Director；QA/Verifier 把局部失败交回原 owner。TaskRuntime 唯一拥有 DEO claim/receipt/recovery/parent-close；adapter mutation port 消费权限执行；execution_broker 拥有项目 artifact/verifier/process 证据。二者按 identity/hash 关联。Run Ledger 管有效证据，runtime.projection 管唯一 ProjectOutcome。workflow 持有恢复 cursor；审计、外部 Agent 和 Bench 均不成为新的完成权威。

**Tech Stack:** 现有 Python/Cell/KernelOne/KFS、NATS JetStream、runtime.v2 WebSocket、TypeScript 前端、pytest/Ruff/Mypy/Playwright，以及已有语言工具链。

**Spec:** [历史复盘与总方案](../../../src/backend/docs/blueprints/POLARIS_UNATTENDED_DEVELOPMENT_MASTER_PLAN_20260930.md)。

**重构优先级说明：** [执行与恢复闭环的第一刀](../../../src/backend/docs/blueprints/POLARIS_FOUNDATION_REFACTOR_DECISION_20260930.md)。

**Baseline:** 2026-09-30，HEAD `0d475c60474f2b1ea4a06ea136c0f817d2c49bfa`；测试状态本轮未刷新，原始证据和当前源码已做只读核对。所有任务默认 `planned`。

**Execution record:** 用户已批准实施。上行是计划冻结时的历史基线；当前候选代码、实际门禁、开放证明义务见 [实施进展](../../../src/backend/docs/governance/POLARIS_UNATTENDED_IMPLEMENTATION_PROGRESS_20260930.md) 与对应 `polaris-unattended-execution-status-20260930.json`，不得把 planned 或 local_verified 误称 fresh_verified。

## Global Constraints

- 先读当前根目录/最近 `AGENTS.md`、后端架构标准、graph、目标 Cell manifest；不沿用旧行号猜写入位置。
- 每条 shell 命令及链式子命令以 RTK 开头；CodeGraph 先用于代码发现。缺工具或索引不可用时记录真实降级。
- 失败必须有 exact-run 动态调试或真实 handler 重放；静态线索标为 hypothesis。
- 工程 Agent 不直接编辑生成目标项目；真实产品写入仅由 Polaris 已授权 Director/工具路径完成。
- 复用现有 Cell public contract 与 KernelOne；不得跨 Cell import internal；查询不偷写。
- 工具归一化先于权限检查；保留 hash-bound registry view；不可推断则带证据 fail-closed。
- runtime 默认唯一根 `<workspace>/.polaris/runtime`；main 端口 `49977/5173` 不分配给 Bench。
- 项目观测仅 NATS/WS；HTTP 初始快照/显式查询/命令合法，产品定时轮询不合法。
- 普通 Director/QA 失败不重跑 PM/CE；候选局部 CAS 回滚合法，重置整个项目不合法。
- 不降低当前 required 验收，不伪造 receipts，不用外部 Agent 报告代替平台原生证据。
- 不自动提交他人修改；每桶完成检查差异与门禁后，按仓库既定提交权限处理。本文不要求创建新分支或发布。
- 结构性工作先更新 Verification Card/ADR；本文是总计划，不代替每包的假设与反证记录。
- 显式 UTF-8；完整证据先归档再清 `/tmp`。新运行目录只创建唯一新目录，禁止删除仍被证据引用的旧目录。

## Review Focus

1. 丢 ACK/跨进程 replay 后是否重复执行 effect：由 P04 的并发/崩溃矩阵验收。
2. 六轴读取期间发生 rework 是否产生混合 epoch 假绿：由 P02 的 owner-head 变更矩阵验收。
3. 最后一轮仍有进展却预算退出，cursor/stop_reason 是否丢失：由 P01/P03 重放验收。
4. QA skip、历史 PASS、环境 hash 变化是否错误提升为当前成功：由 P08 验收。
5. 归一化是否扩大权限或提前消费其他语义修复并丢审计：由 P05/P06 的合法不变、幂等、歧义拒绝测试验收。

## 1. 执行顺序与任务边界

```text
P00 基线
 ├─ P01 停止证据
 ├─ P02 结果权威
 ├─ P04 effect 安全
 ├─ P05 请求与工具
 └─ P14 文档/记忆（贯穿）

P01 + P02 + P04 → P03 持久化阶段恢复
P05 → P06 CE 可行性
P02 → P08a verifier 身份/诊断/skip
P03 + P05 + P06 + P08a → P07 Director 收敛
P08a + P07 → P08b QA / 复测失效图（P08 关闭）
P04 → P09 存储/实例/观测
P06 + P08 → P10 语言能力
P03 + P08 + P09 → P11 调度
相关包 + P10 + P11 → P12 固化/准入 → P13 fresh 与 N-batch
```

并行只读可立即进行；并行写入前由主 Agent生成实际文件排他集合。P01/P03/P07 可能触碰相同 Factory/workflow 文件，必须串行。P02/P04 跨公共契约时先固定接口。前一桶已知存在 blocker 时，不在相同边界叠加新重构。

### 1.1 每个包统一交付

每包应交付：问题证据、适用版本、owner、动态复现/反证、最小代码改动、RED/GREEN、模块及受影响 cascade、独立复核、剩余风险、归档 refs、下一动作。单测绿只能标 `local_verified`；同 run 恢复成功标 `recovery_verified`；fresh 无干预才进入 `fresh_verified`。

任务里的新增测试名称是**拟新增验收场景**；既有路径已按当前源码/专家审查定位。执行前先确认文件/符号仍存在，重构后优先追 owner，不恢复已删除的 shim。

## 2. P00：当前事实、历史证据和准入基线

**目标：** 所有人使用同一份可验证的当前状态，解决“旧文档说不存在，新代码早已有”的治理漂移。

**读取/拟修改范围：**

- `src/backend/docs/graph/catalog/cells.yaml`，相关 subgraphs。
- `src/backend/polaris/cells/runtime/projection/cell.yaml`、TaskRuntime 的 `cell.yaml`/`README.agent.md`。
- `src/backend/docs/governance/PLATFORM_MODULE_SOLIDIFICATION.md`、`PLATFORM_UNATTENDED_AUTOMATION.md`。
- 既有源码/归档只读；根据证据同步治理资产，不改执行语义。

**接口：** 消费当前 Git/source fingerprint、module registry、Cell public exports、运行档案。产出带时间和证据等级的 baseline manifest；此 manifest 为工程记录，不是运行 authority。

- [ ] 读取当前 HEAD、diff、活跃实例身份和待办 frontier；分开历史成功、当前候选资格、未关闭项目、foreign-owned 项目。
- [ ] 核对 ProjectOutcome owner-bound 实现与 catalog 的 pure-reducer 描述，明确当前和历史 scope。
- [ ] 核对 DEO 阶段描述，逐条验证 manifest 的 closed 与实际门禁/证据，不根据 class 存在宣布闭桶。
- [ ] 核对既有 A009B3 physical-attempt qualification：从 `docs/superpowers/plans/2026-07-18-role-final-provider-request-audit-gate.md` 追当前实现、关闭证据和有效禁令。旧 pending 不自动变永久禁止，新计划也不自动解除既有门禁。
- [ ] 以 registry 为准列出 10 个模块与状态；修正旧 M01 sealed/M10 缺失文档。
- [ ] 解析 120 项 catalog/canonical aliases；形成 first-open 项目记录，不能默认回到 L1-01 或从记忆直接跳 L3-24。
- [ ] 冻结当前 N-batch 政策。若采纳本文“3 canary＋每层 3×10”建议，必须记录 policy version 与生效边界，不追溯重写旧成绩。
- [ ] 运行影响面基线门禁，保存完整日志；collection error、环境错误、断言红、未运行分别记账。
- [ ] 只在对应准入满足时标 `BENCH_SCHEDULABLE`；没有新代码也可能基线红。

**验收：** 每个 current claim 有源码/执行证据；历史声明有日期；不存在“clean tree 所以测试通过”的跳步；P01/P02/P04 的写入范围明确。

## 3. P01：r92 动态重放与完整停止决定

**目标：** 所有修复循环出口都可解释，并保存正确的续跑位置。

**现有落点：**

- `src/backend/polaris/cells/factory/pipeline/internal/factory_workspace_quality_impl.py`
- `src/backend/polaris/cells/factory/pipeline/internal/factory_workspace_quality_evidence.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_characterization_workspace_quality_checks.py`
- 类型/纯策略如需抽离，归 `director.runtime` 既有公开契约及 internal scheduler；Factory 保持编排职责。

**接口：** 输入实际 effective policy、完整 residual frontier、accepted candidate receipt、轮数/时间/预算和已消费扩展。输出类型化 continue/stop/revalidate/park 决定、reason、owner、resume 条件及证据引用。扩展现有结果 DTO；不要凭本文创建同名平行状态机。

- [ ] 从 r92 原文件保全 `max_rounds=8`、10 轮、末轮 progress、空 stop_reason；固定 hash。
- [ ] 对循环自然耗尽、extension helper 拒绝、deadline、cancel、空诊断但命令失败等出口设动态观察点。
- [ ] 在测试 scratch 中调用真实决策 handler 重放输入；不执行 provider，不写原目标目录。
- [ ] 新增 `test_repair_round_exhaustion_emits_explicit_stop_reason`：预算退出必须非空 reason，success=false，残差完整保留。
- [ ] 新增 `test_progress_at_final_round_preserves_continuation_cursor`：末轮已接受进展被保留，下一动作可解释，不直接宣称 pass。
- [ ] 新增 `test_extra_rounds_record_authorized_reason_and_consumption`：8 基础＋最多2扩展条件明确，计数和 reason 相符。
- [ ] RED 后最小实现；保持原额度，先修记账和决定完整性。
- [ ] 回归 r90 distinct frontier handoff：仍只一次，global fuse 不重置。
- [ ] 若 r92 实际分支无法从归档确定，记录 `historical_branch_unproven`，用受控同路径 replay 证明新不变量。

**通过标准：** 每条出口都有确定原因；相同输入得到相同策略决定；已接受产物没有因循环结束被丢弃；不增加无证据重试。

## 4. P02：ProjectOutcome 权威与当前事实切面

**目标：** UI、外部队列和最终验收从同一个现役 owner-bound outcome 得出项目完成/阻塞结论；Factory 的前终态安全判断仍由其自身 owner 事实决定。

**现有落点：**

- `src/backend/polaris/cells/runtime/projection/public/contracts.py`
- `src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py`
- `src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py`
- `src/backend/polaris/bootstrap/project_completion_convergence_runtime.py`
- `src/backend/polaris/cells/runtime/projection/tests/test_project_outcome.py`
- `src/backend/polaris/cells/runtime/projection/tests/test_project_outcome_authority_binding.py`
- `src/backend/polaris/tests/unit/bootstrap/test_runtime_projection_project_outcome_owner.py`

**现有接口：** `ProjectOutcomeQueryV1` 是 unbound 纯投影输入；`ProjectOutcomeAuthorityQueryV1(workspace, project_id, run_id, completion_contract_hash)` 通过 owner 获取事实。只增强后者证据完整性与消费者，不新建 FinalOutcomeArbiter。

- [ ] 列出 Factory、UI、workflow、Bench 当前成功判断入口，区分权威 outcome 与历史 `real_run_green` advisory。
- [ ] 防止终局判断循环：Factory 依据 stage result、barrier、fence 完成 chain，然后由 ProjectOutcome 聚合；Factory 不得反过来等待含 chain.completed 条件的 ProjectOutcome 才完成。
- [ ] 动态 fault injection 在 owner 读取之间触发 reopen/rework/文件 hash 变更。
- [ ] 新增 `test_owner_advance_during_outcome_read_rejects_mixed_epoch`、`test_artifact_change_invalidates_prior_verifier_success`。
- [ ] 逐轴注入 delivery/chain/QA/TaskBoundary/TaskRuntime/RunLedger 的 failed/missing/pending，确保不能合成全绿。
- [ ] 使用 owner head/epoch 与既有 binding 做一致性校验；必要时有界重读，超过次数输出 stale/blocked，查询不得写状态。
- [ ] 新增 `test_caller_supplied_green_projection_cannot_authorize_completion`、`test_historical_qa_fail_is_not_current_epoch_fail`。
- [ ] 收紧消费者：当前 required test/depth fail 时，不展示 `DELIVERY_VERIFIED` 作为完整业务交付结论。
- [ ] 再校验 persisted successful quality_gate 和终态结算，保持 failed 与 missing 分离。

**通过标准：** 跨 run/workspace/contract/epoch 的证据不能拼绿；成功证据可回查唯一 owner；历史错误可见但不污染已依法失效的当前 epoch。

## 5. P04：claim、fence、effect、receipt 与 settlement

先做 P04 再实施依赖它的 P03。这个包拆三个串行子桶，每桶独立 RED/GREEN，不一次重写整个 DEO。

**现有落点：**

- `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation/_repository_class.source`
- `src/backend/polaris/cells/roles/kernel/internal/directed_effect_dispatch.py`
- `src/backend/polaris/cells/roles/adapters/internal/director/directed_effect_mutation_port.py`
- `src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py`
- `src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_consumer.py`
- `factory_settlement_journal.py`、`factory_settlement_runtime.py`（同目录）。

**接口：** 复用现有 claim/receipt commit/recovery/dead-letter/reconcile public 操作。claim 的“取回证据”与“新增可消费执行权限”必须有明确不同语义；ack 丢失不自动授予第二次 effect。

### P04a：exact replay 反证

- [ ] 对照 R139 lifecycle test、旧 concurrency test 和 README 的 replay 语义，记录冲突而非修改断言凑绿。
- [ ] 两进程同时 claim；持久 append 后丢 ACK；新进程重建消费 context；旧 fence 过期后晚到执行。
- [ ] 统计 instrumented真实 effect port 的调用次数，不能只检查两个 Python 对象是否相等。
- [ ] 证明每 operation 一次 physical commit 或明确 ambiguous；不确定命令禁止自动重跑。
- [ ] 若发现缺口，修唯一 owner 的 durable consumption/confirmation 语义；保留合法 ACK recovery。

### P04b：effect 与 receipt 崩溃边界

- [ ] write 前、write 后 receipt 前、receipt 后 response 前、parent close 中途分别 crash。
- [ ] `RECOVERY_PENDING` 扫描保持有界；恢复观察现有 effect，不重新执行结果未知 effect。
- [ ] no-op、policy denial、真实 mutation、partial failure、rollback failure 都有不同回执。
- [ ] 恢复后的 before/after/hash/operation/attempt 身份严格匹配；不能把磁盘存在直接登记为历史成功。
- [ ] 旧 owner 晚到写入和跨 workspace 回执必须拒绝。

### P04c：安全释放与业务完成分离

- [ ] 注入 task terminal 但 child/process 未结束、open effect 未解释的状态。
- [ ] 证明 `release_allowed` 只在所有成员已结算/明确 abort/dead-letter 或有受控隔离时成立。
- [ ] 失败项目可释放资源，但 `passed` 仍 false；禁止永远锁租约或改 failed 为 missing。
- [ ] 两 settlement consumer、重复/乱序 wake、journal claim 后 crash、checkpoint 前跳/回退都验收。

**既有测试入口：**

```text
polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py
polaris/cells/runtime/task_runtime/tests/test_execution_attempt_settlement.py
polaris/cells/roles/kernel/tests/test_directed_effect_lifecycle.py
polaris/cells/roles/adapters/tests/test_director_directed_effect_mutation_port.py
polaris/tests/integration/test_deo_2c_production_receipt_wiring.py
polaris/cells/control_plane/run_ledger/tests/test_factory_settlement_barrier.py
polaris/cells/factory/pipeline/tests/test_factory_settlement_consumer.py
polaris/cells/factory/pipeline/tests/test_factory_settlement_runtime.py
polaris/tests/architecture/test_deo_2d_zero_unbound_mutation_surfaces.py
```

**通过标准：** 对被测文件操作无重复 commit、无未解释副作用；unknown 不变 success；失败能安全终结；释放后旧 owner 零新写入。外部网络命令只承诺可分类/可恢复的已知语义，不宣称无条件 exactly-once。

## 6. P03：持久化阶段恢复与预算守恒

**依赖：** P01、P02、P04。

**现有落点：**

- `src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py`
- `src/backend/polaris/cells/orchestration/workflow_runtime/public/` 与该 Cell 的 cursor/recovery owner。
- `src/backend/polaris/cells/orchestration/workflow_orchestration/tests/test_project_completion_convergence.py`
- `src/backend/polaris/cells/orchestration/workflow_orchestration/tests/test_project_completion_supervisor.py`
- Factory 既有 stage persistence、workspace admission、run-service 测试。

**现有接口：** `retry_run_from_stage` 支持阶段恢复；`quality_gate` retry 保留上游。新实现不能把 runner `director_resume` 当成相同 API：后者可能创建不同 run，且不能算 fresh 全链证明。

- [ ] 定义一项目一 active cursor/claim，不复制 receipt/outcome 权威。
- [ ] cursor 保存 exact epoch、合同/蓝图 hashes、accepted checkpoint、residual signature、所有预算消费和下一合法动作。
- [ ] `test_qa_retry_does_not_invoke_pm_or_ce`：用调用计数及保存文件 hash 证明，不只检查字符串。
- [ ] `test_restart_resumes_failed_owner_and_preserves_completed_siblings`：真实持久化后重建 service/process。
- [ ] `test_reopen_does_not_reset_nonprogress_or_extra_round_budget`：新 epoch 不能洗掉累计限制。
- [ ] 上游合同改变时只失效依赖闭包；未变合同不重新生成。
- [ ] 分离 provider wait timeout、tool execution lease、settlement deadline、Factory 总 deadline；child repair 继承有效预算。
- [ ] 取消/超时先 drain 和归档已知 effects；resume 需新 claim，禁止复活失效 token。
- [ ] 有进展但运行预算耗尽保存 checkpoint；无可执行下一步给出 typed blocker，而不是反复创建同一失败任务。

**通过标准：** worker 重启后原阶段安全恢复，PM/CE 不重复调用，历史记录不删除，已通过证据仅按依赖/epoch 失效。

## 7. P05：最终请求和工具的真实执行一致性

**现有落点：**

- `src/backend/polaris/kernelone/llm/toolkit/tool_normalization/__init__.py`
- `src/backend/polaris/cells/roles/kernel/internal/tool_gateway.py`
- `src/backend/polaris/cells/roles/kernel/public/final_request_evidence_cutoff.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_final_request_evidence_cutoff.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_llm_invoker_final_request_receipt.py`
- `src/backend/polaris/kernelone/llm/toolkit/tests/test_tools_normalization.py`

**接口：** 复用 `normalize_tool_arguments_from_snapshot` 和现役 ToolSpec hash view；最终请求快照为“实际发送了什么”的证据，不能代替 effect/verifier 对“执行结果”的证明。

- [ ] 建请求传输矩阵：stream/non-stream、native/text、主调用/repair、不同 provider adapter。
- [ ] 复用既有 A009B3 cutoff/physical-attempt qualification，覆盖每个 retry/fallback 的实际物理请求、parity/预算及 snapshot qualification；不能以新审计摘要替代现行执行准入。
- [ ] 用捕获真实 adapter 请求的 fixture 证明 role、tools、tool_choice、response_format、timeout 和 coverage 一致。
- [ ] 注入 prompt 要求工具但 schema 缺失、alias 参数不一致、空 search、重复 key、非法路径；要求发送前或执行前明确拒绝。
- [ ] `test_same_registry_snapshot_used_for_normalization_and_authorization`：禁止规范化一套、权限另一套。
- [ ] `test_repair_request_contains_current_diagnosis_and_source_hash`：失败反馈不能取旧候选，sibling API 不得被 slim 静默裁掉。
- [ ] 必需证据是否 included 由引用/hash/call identity证明；token 占用率单列，不设填满窗口 KPI。
- [ ] 原始请求受本地权限保护；UI 输出摘要/refs，查询最终快照需同 workspace。

**通过标准：** 每次物理调用可还原最终请求；必需工具真实可调用；参数归一化保持 scope；不靠“模型答应会修”确认 effect。

## 8. P06：CE 可执行合同与局部候选修复

**现有落点：**

- `src/backend/polaris/cells/chief_engineer/blueprint/public/service/_portfolio.py`
- `src/backend/polaris/cells/chief_engineer/blueprint/public/service/_semantic_repair.py`
- `src/backend/polaris/cells/chief_engineer/blueprint/public/contracts/_semantic_repair.py`
- `src/backend/polaris/cells/factory/pipeline/internal/factory_stage_executor/_mixin_02.py`
- `src/backend/polaris/cells/chief_engineer/blueprint/public/tests/test_semantic_repair.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_characterization_ce_handoff.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_characterization_ce_handoff_lease.py`

**接口：** 既有 `ChiefEngineerSemanticRepairCandidateV1`、Diagnosis/Patch/Receipt、completion contract；规范化后的 current candidate 与 patch 保持精确 hash 绑定。

- [ ] 冻结 PM goal/scope/验收/命令 authority；CE artifact、entrypoint、verifier 与 task dependency 对齐。
- [ ] `test_required_artifacts_are_feasible_in_authorized_scope`：文件数量/模态可行，缺 create 工具提前暴露。
- [ ] `test_verifier_owner_has_dependency_complete_coverage`：并行无共同依赖 owner 时禁止任意重绑。
- [ ] r91 原始 `[[[risk]]]`、missing root、多项错误同时出现的候选做离线 golden replay。
- [ ] 合法输入不变、normalize 幂等、源对象未修改、语义冲突拒绝、权限不扩张、hash 在 normalization 后计算。
- [ ] `test_baseline_normalization_does_not_consume_unrelated_recovery_signal`：回归本次历史审计信号丢失。
- [ ] typed patch 原子变更节点和关联边，删除 entrypoint 不留 dangling verifier。
- [ ] 将所有可兼容的诊断一次交给有限 repair；无需全重建原 portfolio；无法唯一归一化则 fail-closed并提供准确路径。

**通过标准：** schema、DTO、业务可行性三层一致；CE repair 留在 CE；真实 PM 矛盾才上升，不能强迫 Director 越权补洞。

## 9. P07：Director 事务式修复和因果进度

**现有落点：**

- `src/backend/polaris/cells/director/runtime/internal/repair_kernel/scheduler.py`
- 同 Cell 的 diagnostics、registry、composer、transactional executor。
- `src/backend/polaris/cells/factory/pipeline/internal/factory_workspace_quality_impl.py`（迁移 consumer）
- `src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_convergence_scheduler.py`
- `src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_public_convergence.py`
- `src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_precise_edit_and_coverage.py`

**接口：** 优先 `query_director_repair_coverage`、plan probe、`PlanDirectorRepairCommandV1`、`RunDirectorRepairCommandV1`、`run_director_repair_convergence`。Factory 执行编排，不重新拥有 rule catalog 或语言 patch 算法。

- [ ] 先 coverage，再 plan probe；metadata-only/reserved/unplannable 只记录状态，不作为 effect 成功。
- [ ] 确定性可证明修复优先；无法证明语义的新业务逻辑交给原 Director 编辑，不生成 stub。
- [ ] 从 current verifier 因果位置选 owner：compiler TU/精确 header 优先于 observer traceback，scope 必须与不可变 authority 相交。
- [ ] 输入 repair context 包含失败测试 setup/call/expected/actual、相关定义、accepted hashes、允许写入集合和负约束。
- [ ] 多文件接口变更形成最小授权 bundle；每个 candidate hash/actual affected set 与 guard 一致。
- [ ] `test_equal_count_diagnostic_swap_is_not_progress`、`test_occurrence_multiplicity_prevents_false_reduction`。
- [ ] `test_build_barrier_forward_unmask_requires_same_verifier_evidence`：新错误只有已证明屏障解除才允许有界继续。
- [ ] `test_rejected_candidate_cas_rollback_preserves_unrelated_new_write`：回滚冲突隔离，不覆盖外部更新。
- [ ] 分离“任务所需 edit”与合法 no-op；如果无需改代码且当前 verifier 已通过，走证据重验而非强制造假 edit。
- [ ] 逐步把 Factory 重复的纯进度/轮预算/owner rotation 决策迁到 runtime owner；先独立 shadow 对账再 cutover，不一次大重写。

**通过标准：** 每次接受候选有物理 effect＋因果 verifier 证明；停滞/回归可解释；单次失败不抹掉已接受结果、不错误释放下游依赖。

## 10. P08：QA 分级、命令证据和复测失效图

分两阶段：**P08a** 在 P02 后、P07 前完成 typed verifier 身份、完整诊断、failed/missing/skip 语义；**P08b** 在 P07 后完成依赖失效复测与最终 QA 集成。P07 不能建立在未经统一的 raw error/skip 语义上。下面前五项属于 P08a，后三项属于 P08b。

**现有落点：**

- `src/backend/polaris/cells/factory/pipeline/internal/factory_workspace_quality.py`
- 同目录 `factory_workspace_quality_evidence.py`
- `src/backend/polaris/cells/control_plane/verifier_policy/public/service.py`
- `src/backend/polaris/cells/control_plane/run_ledger/public/projection.py`
- `src/backend/polaris/cells/control_plane/verifier_policy/tests/test_public_service.py`
- `src/backend/polaris/cells/control_plane/run_ledger/tests/test_failure_evidence.py`
- `src/backend/polaris/cells/runtime/execution_broker/tests/test_project_verification_receipts.py`

**接口：** 复用 verifier snapshot / receipt / effective gates；扩充 typed disposition 必须版本化并同步消费者。

- [ ] 使用 r92 三个独立残差，证明 g++/CMake PASS 不覆盖 unittest/depth FAIL。
- [ ] 动态重放 domain shim skip 分类，区分 executable 缺失、编译失败、测试声明 skip；不能只解析“not available”文字。
- [ ] `test_required_skip_is_not_success`、`test_exit_zero_with_failed_test_inventory_is_not_success`、`test_no_execution_is_missing_not_failed`。
- [ ] 使用 command/test inventory/environment/input hashes 绑定 verifier；测试删除/skip 数增加必须可见。
- [ ] 风格和纯建议默认 advisory；合同要求继续 required；政策改变单独版本化，不追溯修改失败 run。
- [ ] 局部修复只重跑受影响 verifier 与保护集合；最终完成前完整必需验收再执行。
- [ ] 同 epoch 旧 PASS 被新 artifact invalidation；历史 FAIL 经合法复测 supersede，而非删除。
- [ ] verifier stdout/stderr 保留 failure island＋完整日志引用，避免 tail-only 丢根因和重复诊断。

**通过标准：** failed/missing/skipped/not_applicable 可区分；质量政策既不过度将 advisory 硬卡死，也绝不把 required 红色软化为绿色。

## 11. P09：运行根、实例、性能与观测闭环

**现有落点：**

- `src/backend/polaris/cells/instances/internal/service.py` 与 public service。
- `src/backend/polaris/delivery/http/v2/instances.py`
- `src/backend/polaris/kernelone/events/sourcing/file_store.py`
- storage.layout owner、KFS/locked_regular_file 相关公开能力。
- `src/frontend/src/app/launcher/LauncherWorkspace.tsx`
- 前端 runtime transport 与 ContextOS view model。

**接口：** 复用 Instance Registry/Supervisor；HTTP identity、runtime.v2、`resolve_storage_roots`、context endpoints；不新增多租户共享 backend。

- [ ] workspace/runtime/instance/backend PID/fingerprint 一致；身份检查显式绕过环境 proxy。
- [ ] fresh workspace incarnation 与 lock authority 生命周期动态反证，禁止清锁掩盖 drift。
- [ ] terminal 后 cleanup 不误杀其他实例；main 禁止自身 stop/restart/delete。
- [ ] event stream 扩大、多个慢 observer、WS 断连时，真实 execution 不被误取消。
- [ ] 全扫描和巨型 frame 场景建立性能基线；按 durable head 增量投影，UI 用有界摘要＋refs。
- [ ] `error_code=none` 成功事件、历史 failed/recovered、当前 blocked 分类各有测试。
- [ ] 正确角色 task 身份投影：PM 合同与 CE 内部 review task 分开统计。
- [ ] 显式 globalObserver 隔离：普通实例 `enabled=true` 不订阅全局 Bench，newest session 不切换其 workspace。
- [ ] Context 同 workspace 候选链覆盖 active runtime、Registry 相同 workspace runtime 和旧 system cache 只读发现；两个 context/final-request endpoint 均可读取。
- [ ] 404 返回 context_hash/workspace/searched_paths 并在 UI 展示；loopback Context 读取不被普通 API 限流误伤，远程仍限流。
- [ ] observed restart 使用新的独立端口；多 Agent 场景 main/项目实例不启自动 reload 风暴。
- [ ] Playwright 验 HTTP/前端资源/API/WS/workspace binding；主 Agent实际查看 Launcher 和 ContextOS，不止 `/health`。

**既有测试入口：**

```text
src/backend/polaris/cells/instances/tests/test_instance_service.py
src/backend/polaris/tests/unit/delivery/http/v2/test_context_router_hardening.py
src/backend/polaris/tests/architecture/test_contextos_snapshot_summary_fence.py
src/frontend/src/app/launcher/LauncherWorkspace.test.ts
src/frontend/src/app/components/contextos/contextosViewModel.test.ts
src/frontend/src/hooks/useFactoryBench.test.tsx
```

**通过标准：** 实例身份不漂移、runtime 无裸根、观测不拥有取消权、合法快照同 workspace 可读、历史错误不伪装当前故障。

## 12. P10：语言能力矩阵与软件/游戏验证

**现有落点：** director.runtime language slots/catalog/public repair API、verifier policy、toolchain/environment owner；内部目录 `src/backend/scripts/factory_bench/projects_v2.json` 只作为测试输入。

- [ ] 盘点七语言的 toolchain 版本、依赖准备、build/test/lint/entrypoint 支持；不能把 catalog 出现当作支持已验证。
- [ ] 定义平台级 capability manifest，遵从现有 owner；字段包括 language/version、project shape、toolchain probe、verification modalities、environment constraints、证据版本。
- [ ] CLI、Web、API、library、游戏/模拟分别有入口义务；library 的不适用入口须显式，不随便伪造 CLI。
- [ ] 测试跨文件/多模块/跨语言 harness 的 owner 解析，特别是 Python 测 C++/Rust 时不能默认改 Python observer。
- [ ] reserved slot 和 metadata rule 仍不可执行；新增 binding 先 coverage/plan/receipt/revalidation。
- [ ] 对可选 browser/visual/headless 引擎进行 availability 与合同双准入。
- [ ] 游戏验收包含输入事件、状态变化、关键规则/确定性 seed、资源和存档需求；空白与首帧只是基础 smoke。

**通过标准：** 对每种支持能力能回答“在哪个版本、环境、模型和测试矩阵证明”；未覆盖语言显式未验证，不用平台 regex 承诺无限语义修复。

## 13. P11：持续执行、等待与成本预算

**现有落点：** `orchestration.workflow_runtime`、`workflow_orchestration`、`resident.autonomy` 已有 goal/cursor/convergence 接口；工程 Bench 队列是测试驱动，不写产品成功事实。

- [ ] 产品只定义通用 project active cursor、恢复/等待和预算；Bench 的 catalog 排序、单项目调用和批次晋级全部由 P13 内部 harness 承担，不进入产品 Cell 契约。
- [ ] 只有 owner-bound completed_verified 才推进；blocked/failed/cancelled/foreign-owned 单独记录，不伪装通过。
- [ ] `not_schedulable` 带缺哪项 gate、owner、变化触发条件和仍可做的离线任务。
- [ ] provider 准入共享并发/配额视图；429 背压与宕机分开；有效 Retry-After/backoff 纳入 deadline。
- [ ] 预算从 logical call 到 physical attempts、tool、repair、settle分层累计；重启/换 owner 不归零。
- [ ] 工程策略可以继续修已授权平台桶，但无新证据时不自动 full rerun。
- [ ] provider/model 替换需配置授权和记录新 candidate；不能静默换强模型后宣称原模型通过。
- [ ] model_ceiling 只消费 workflow-runtime 已 seal 且复验的权威结果；报错次数不是模型上限证明。
- [ ] queued/waiting/recovering/blocked/terminal 报告可被人读懂；active heartbeat 仅代表活着，不等于进度。

**通过标准：** 主 Agent关闭交互界面后授权任务仍可恢复/等待；达到停止条件及时停，醒来条件明确；无重复请求洪泛、无工程报告写入 Run Ledger。

## 14. P12：模块封板、跨模块契约和准入

**现有落点：**

- `src/backend/polaris/kernelone/platform_modules/registry.py`
- `src/backend/scripts/platform_modules/run_module_gates.py`
- `src/backend/polaris/kernelone/platform_modules/tests/test_registry.py`
- 现有 graph/architecture release gates 和 Cell verify packs。

- [ ] 单模块 gate 对应 owner invariant，sealed 状态绑定版本与证据，不只写字符串。
- [ ] 变更影响集来自 graph/public contract/实际调用，保持 M03/M04 已有防线。
- [ ] cascade 不含 open M09；显式验证 M09 或 `--mode all`，防止漏验量具。
- [ ] 新增跨模块故障矩阵：claim→effect→receipt→verifier→epoch→outcome→terminal，全链少量真实 handler 集成。
- [ ] 测试大文件拆分保持 fixtures/import/monkeypatch owner；结构重构不同时改行为语义。
- [ ] pytest/格式/类型/graph gate 保存当前源码 fingerprint、退出码和完整日志；不以历史数量作验收。
- [ ] independent shadow 必须独立执行两路径，比较 scope、hash、verifier、receipt；自投影不能通过 cutover。
- [ ] 封板后变更需 defect/unseal/重验影响面；不能因为模块曾 sealed 而跳过当前失败。
- [ ] 明确现行 A009B3 和其他既有执行准入已满足；如已有关闭证据则复核引用，如仍有效阻塞则不能靠新 P12 的绿色绕过。

**通过标准：** 新 Bench 之前所有关键依赖可调度；缺证据/红基线阻止付费 probe，并留下明确下一项修复，不永久空等。

## 15. P13：当前 frontier 与 fresh/N-batch 资格

- [ ] 从 P00 的已验证队列选择当前 first-open；r92 作为具体回归案例，不臆断它是整个目录唯一未完成项目。
- [ ] 在门禁允许后只恢复原失败阶段；记录工程协助次数、PM/CE 调用次数、保留 hash 和最终 ProjectOutcome。
- [ ] 形成固定 candidate：代码 SHA＋影响 diff hash＋config/model/tool schema/policy/catalog/environment hash。
- [ ] fresh 三 canary：选择前冻结项目，包含受影响 archetype和至少三语言组；完整预算，每个单独 isolated。
- [ ] 任一通用平台根因出现，停止发下一个项目，回 owner 桶；保留失败分母。
- [ ] 通过 canary 后选择一个 level 的固定 10 项；N=3 且政策批准后，连续三批各 10/10 完整通过才满足资格。计划覆盖是30 slots/10 unique projects；实际 fresh 重跑增加 started_runs，失败不替换、不从分母删除。
- [ ] 全目录逐级推进到120项；全目录资格最低360 runs，各证据对当前兼容 candidate 有效。历史证明保留但不拼接不兼容版本；分层安排预算，不一次全发。
- [ ] 每项目归档真实入口、required verifier、final request、receipt、QA、current epoch、ProjectOutcome 和无干预记录。
- [ ] 汇总首次成功、平台自动恢复成功、工程协助成功、failed/blocked/cancelled/not_started；各分母公开。

**通过标准：** 只有 fixed candidate 的 fresh 无干预结果计入自主资格；平台自动局部修复可以计入，但工程 Agent现场修改平台/状态的恢复单独记账。

## 16. P14：持续记忆、缺陷闭环与交接

从 P00 起贯穿，不等最后才做。

- [ ] 一次新根因一个机器缺陷记录，字段含 exact identity、evidence、owner、假设置信度、修复/验证、下一步。
- [ ] 每次进展/失败更新 short progress；旧结论修正有 `supersedes` 或明确引用，不删除反证历史。
- [ ] 主 Agent用户报告与机器结果一致；不能反复“全部平台已修、只是模型/环境”而无排除证据。
- [ ] /tmp 证据在 cleanup 前复制到持久工程 archive，hash和可读取性复核；archive 不成为新的 runtime 根。
- [ ] 权威/graph/状态说明改变时同步相关文档，按仓库规则评估 AGENTS/CLAUDE/GEMINI 镜像。
- [ ] 子 Agent 报告保留外部工程属性；主 Agent独立验收，不能出现在产品 gate字段。
- [ ] 交接只需索引即可定位：current candidate、active task、最近完整证据、未解残差、已否定假设、下一条命令及禁行动作。

**通过标准：** 新 Agent读取索引和少量证据即可继续原边界；不会因上下文压缩重新跑历史或重复已否定修法。

## 17. 执行命令与预期

以下命令供实施阶段使用。本轮只核对可用入口和文件，不把这些命令写成已经运行通过。所有命令的工具 `workdir` 必须为 `/home/dains/Documents/polaris`；不得从 `src/backend` 执行这些仓库根相对路径。文本输出使用 UTF-8。

### 17.1 只读基线

```bash
rtk proxy git status --short
rtk proxy git rev-parse HEAD
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend /home/dains/.pyenv/versions/3.12.3/bin/python src/backend/scripts/platform_modules/run_module_gates.py --list
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend /home/dains/.pyenv/versions/3.12.3/bin/python src/backend/scripts/factory_bench/run_factory_bench.py --help
```

预期：获得现状、模块 ID 和真实参数，不将 `--dry-run` 当只读查询。

### 17.2 P01 和 P02 聚焦回归示例

```bash
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend /home/dains/.pyenv/versions/3.12.3/bin/python -m pytest -q src/backend/polaris/cells/factory/pipeline/tests/test_characterization_workspace_quality_checks.py
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend /home/dains/.pyenv/versions/3.12.3/bin/python -m pytest -q src/backend/polaris/cells/runtime/projection/tests/test_project_outcome.py src/backend/polaris/cells/runtime/projection/tests/test_project_outcome_authority_binding.py
```

预期：新回归修改前以目标不变量失败，修改后全通过；零测试收集不是通过。

### 17.3 模块与 cascade

```bash
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend /home/dains/.pyenv/versions/3.12.3/bin/python src/backend/scripts/platform_modules/run_module_gates.py --module M07_factory_stage_chain
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend /home/dains/.pyenv/versions/3.12.3/bin/python src/backend/scripts/platform_modules/run_module_gates.py --mode cascade --json-out /tmp/polaris-cascade-20260930.json
```

上面 M07 只是 P01/阶段链的示例，实际按缺陷 owner/module 选择。sealed受影响时增加 `--mode sealed`；M09 改动显式 gate；JSON tail 不是完整日志，工程 runner需另外保存全文和退出码。

Python 改动执行 scoped `ruff check`、`ruff format --check`、`mypy` 和 pytest。需要格式修正时只格式化该桶，不全仓改写；涉及 graph 再运行仓库治理 gate。前端命令以当前 `package.json` 为准，不能把已删除测试路径复制到执行脚本。

### 17.4 单项目 fresh 模板

仅 P12 及当前仍有效的 A009/执行准入全部通过后运行。每次使用唯一目录；不在命令中删除旧 run。项目 ID 是示例，必须按当前进度清单选择。

```bash
rtk proxy bash -lc '
set -euo pipefail
PROJECT_ID=L3-24
RUN_STAMP=$(rtk proxy date -u +%Y%m%dT%H%M%SZ)
WORK_DIR=/tmp/factory-bench-${PROJECT_ID,,}-${RUN_STAMP}
rtk proxy test ! -e "$WORK_DIR"
rtk proxy test ! -e "$WORK_DIR.runner.log"
rtk proxy env PYTHONUTF8=1 PYTHONPATH=src/backend NO_PROXY="*" no_proxy="*" \
  timeout --kill-after=30s 6000s \
  /home/dains/.pyenv/versions/3.12.3/bin/python \
  src/backend/scripts/factory_bench/run_factory_bench.py \
  --project-ids "$PROJECT_ID" --work-dir "$WORK_DIR" \
  --timeout 5400 --max-failed 0 --real-run-timeout 120 \
  --launcher-instance-mode isolated --bench-session-reporting off \
  2>&1 | rtk proxy tee "$WORK_DIR.runner.log"
'
```

预期：单项目真实执行；stdout 中 PASS 不替代 owner-bound 最终验收。任何非零退出先归因；不能直接将 PROJECT_ID 换到下一个继续。

## 18. 每桶主 Agent 验收清单

- [ ] 输入是假设还是已证明根因，是否有精确动态 trace？
- [ ] 是否复用了当前 owner/contract，是否出现第二事实源或跨 Cell internal import？
- [ ] 是否保留了所有无关 dirty 工作？目标项目是否只有平台授权路径产生写入？
- [ ] 是否有真实 before/after/receipt/verifier 因果链，非 mock 掉被验的核心路径？
- [ ] current failure、historical error、failed evidence、missing evidence 区分了吗？
- [ ] 当前源码上的门禁命令/退出码/完整日志是否可查？
- [ ] 新版本是否使既有证据失效，需要哪些影响面重验？
- [ ] 失败/预算停止是否有 resume 条件，是否重置了累计耗费？
- [ ] 结论属于 local、recovery、fresh 还是 matrix qualification，是否标对？
- [ ] 文档和记忆是否更新了纠正关系，下一 Agent可否直接接手？

## 19. 如何判断真的比过去更有效率

首批成功标准是：明确停止原因、局部恢复不重启上游、真实编辑与回执一致、失败证据立即可读、当前 outcome不再互相矛盾。随后比较单位成功项目的总 token/墙钟/人工干预、平均残差关闭时间、相同根因复发率，而不是比较 commit或修复规则数量。

如果一轮工作只能增加文档而不能产出更强的可执行证明，下一轮必须收窄到一个 owner/invariant。若 fresh 项目仍失败，依据新证据重新排优先级，不能不经验证就坚持本计划最初的假设。计划的稳定部分是权威/证据/恢复原则，可调整部分是具体根因、文件位置和工作顺序。
