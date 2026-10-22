# 当前验证、同任务修复与最终接受

> **2026-10-22 后续候选**：三组交接说明对照未证下一步验证／交回收益，未集成该段提示词。当前Director请求尾部只呈现原host deadline及准备时刻余量，原system保持。r2组件、K1和目录增量完成；完整3.12回归588通过／1测试JSON尾文本假设失败，实际反馈完整，修测试消费后相关9项通过，旧失败保留。尚无该候选原生网页结果，不新增停止门禁或宣称H2收益。详见 docs/blueprints/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md；只更新论文源文，未验证主Launcher已加载。

> **2026-10-22 当前证据边界**：下文 ExecutionBroker 等部分保留旧引擎分析。当前工程同一 owner 保存真实检查、当前候选及独立 QA；QA 只读计算能力已接通，验收收益尚未证明。r05 从未进入正式验证与 QA，不能把该失败归因于 QA 卡住。本次只过滤绑定所需事件，完整当前 manifest、权限与接受条件不变；532 项相关回归与 K1 ALL 不替代真实网页交付。详见仓库 docs/blueprints/POLARIS_ENGINEERING_EVENT_SELECTION_20261022.md。

## 5. 真实验证：许可、执行、证明与当前性分开

Verifier Policy 只评估 proposal，不 mint execution authority。ExecutionBroker 从 bootstrap-bound owner port 解析 exact DTO，验证 workspace/project/run/contract/obligation、argv/cwd 与 executable identity；spawn 前重新解析 authority，若已变化则拒绝。随后冻结全部声明输入，绑定 input hash，启动受控进程并产出 owner 收据。

“命令执行成功”仍可能只是 candidate observation。正式验收需要收据 seal、当前输入/authority、指定 proof 与稳定输出。长驻入口还可以按已声明 readiness profile 判断 ready 后受控终止；不能把它等同于任意进程存活，也不能把 CLI 缺失替换成宿主旧 dist 能跑。

VerificationGroup 的用途是让 build→type/test→entry 在同一个干净候选上共享派生产物。开始时只复制批准的 authored inputs 和依赖，避免 stale dist。entry 不通过时前面的 exit 0 是实际进展，但未 seal 的整组并不是完整 authority proof。actual12 正是 ENV/build/type/test 为 0、exact ENTRY 为 1；它比旧 host npm start 更真实，也仍是失败。

源码：policy proposal（src/backend/polaris/cells/control_plane/verifier_policy/public/service.py:856）、spawn 前再授权（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1367）、receipt proof（src/backend/polaris/cells/runtime/execution_broker/internal/project_verification_authority.py:1045）。

## 6. 至少六种“完成”，不能压成一个绿色状态

| 判断 | 它实际回答的问题 | 与全项目交付的关系 |
| --- | --- | --- |
| provider terminal | transport/worker 是否结束，terminal 是否持久化 | 无正确实现含义 |
| effect receipt committed | 受权副作用是否真实、可追责地发生 | 有效果仍可编译/测试失败 |
| TaskBoundary completed_verified | 当前任务声明的产物、依赖、证据、verifier 条件是否满足 | 范围来自当前任务边界；不能替全项目 QA |
| TaskRuntime completed/settled | owner attempt 是否终态、还能否写；任务投影是否 converged | completed task 可以伴随项目 QA 未运行或失败 |
| Factory completed | orchestration session/阶段链是否结束 | `complete_run` 明确写 verified=False |
| ProjectOutcome completed_verified | 六轴当前 owner 证明是否全部满足且 authority_bound | 这是验收汇总结果；仍需将资源释放单独报告 |

TaskBoundary 保留 missing/failed evidence 与 missing/failed verifier 两组字段。单个 `status` 按优先级取一个主要原因，因此 UI/诊断不能只读 status 丢掉其他并存失败。它还会用当前磁盘存在性修正 completed/pending artifact 列表；这属于读模型对齐，不是自动生成 authoritative effect receipt。强验收仍应消费 Broker/DEO 与当前合同证明。

`complete_run` 的 success 参数只闭合 orchestration lifecycle，不升级已经 FAILED 的 run，不授权 verified。成功 QA 后通知项目 completion 要等 Factory completed 事件先 durable，避免在 Factory chain 尚未闭合时查询最终汇总器。

源码：TaskBoundary（src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py:815）、Factory 完成定义（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:1212）、post-terminal notify（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_physical.py:382）。

## 7. operational closed 与 passed 的真正差异

当前 Factory settlement barrier 的公式可直接从源码得到：

```text
closed = 找到该 Factory 的事实
         && open_lifecycle_count == 0
         && (没有 missing required evidence || 失败情况下所有 dispatch 有严格闭合证明)
release_allowed = closed
passed = closed && run_projection.ok && failed_lifecycle_count == 0
         && missing_required_modalities 为空 && failed_required_modalities 为空
```

所以失败 run 完全可以 `closed=true/release_allowed=true/passed=false`。这是安全清理的正确结果：失败的工具/命令确实终态，证明仍保留失败，不需要补成功收据才能释放工作区。反过来，active_task_count=0 也不充分：曾经 dispatch 的 effect 或 provider attempt 可能仍未闭合。

当前 `_lifecycle_counts` 用最新 task facts 求 task 状态，却给 failed_count 加上历史 tool lifecycle failed_count。这个混合使 barrier.passed 可能长期保持 false，值得审计其名称、consumer 和 acceptance revision。**不能由此直接推出最终交付必然被历史失败毒化**：当前 ProjectOutcome adapter 并不读取这个 passed，而是读另一组 exact-scope ledger/current physical owner 结果。需要分别证明最终 consumer 是否使用历史失败计数，不能删旧失败、只扫描新 attempt、或伪造 pass。

源码：counts（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:217）、barrier 公式（src/backend/polaris/cells/control_plane/run_ledger/public/settlement_barrier.py:726）、最终 owner adapter 判据（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:782）。

## 8. current acceptance 与 all-history resource closure

完整历史有两个用途，必须分别投影。

资源安全需要检查每个 stable execution identity。新 session 覆盖任务当前 row，不会抹掉旧 session 的 effects；全部旧 attempts 都需要 strict closure。RunLedger public query 明确构造每个 task/external/session/attempt/role/worker/run tuple 的 observer query。仅检查最新成功 attempt 会遗漏旧未完成副作用。

验收当前版本则需要 CE 合同和当前 input/authority revision 的证明。旧失败必须留在历史中，不能继续代表当前候选结果。ProjectOutcome adapter 会将 Factory frozen terminal rows 与新 live repair rows 按 owner/seq 合并，剔除 removed live rows，再按 CE covered task ids 限定；它也可以用当前、全义务闭合的 physical diagnostics 证明任务已完成。这里必须保持 exact current owner bundle，不可凭“最后一条时间更晚”覆盖另一合同或项目。

原终态清理的顺序为：physical attempts closure → child settlement → stage claim reconciliation → ledger release barrier → 保存 frozen TaskRuntime projection → 正式 reset(keep_plan=True) → lease CAS release。清理删除 live row/session 后还要从 immutable facts/frozen projection 找到原任务、CE 和 session；这就是 completed/failed/sparse-heartbeat/reset 历史组合为何在恢复中暴露过不同错误。

源码：全部 attempts（src/backend/polaris/cells/control_plane/run_ledger/public/service.py:1497）、frozen/live merge（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:146）、freeze-before-reset-before-release（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_lifecycle.py:427）。

## 10. Repair 与 recovery 的区别及收敛边界

Repair 改善目标实现，recovery 恢复原控制权限与继续执行资格。ordinary compiler/test/entry failure 应产生 typed diagnostic，送回同一个 Director owner，在 CE 允许范围修复；hash/auth/symlink/contract drift 则应拒绝候选或阻塞，不能当 ordinary feedback 自动吞掉。

repair kernel 的规则、coverage、计划、composer、调度顺序和 cycle breaker 归 director.runtime。roles.adapters 的 bridge 只绑定 runtime 宣布的 step；Factory 不应私自加语言 regex 或另一份 scheduler。coverage_gap 只说明没有 deterministic rule，不代表普通 LLM repair 不允许，也不意味着 advisory 可以注册规则或写文件。

保存候选与完成 owner 是两种 verdict。某轮修复让 TEST 通过但 ENTRY 暴露新问题，可以在其它已通过 gates 保持、scope/authority/cmd 不漂移的条件下保存增量，并继续同任务；不能把 accepted candidate 当 Task completed，也不能在全部 verifier 尚未完成时一律 rollback 有价值进度。现有 materialization/quality 共享 convergence 已处理这一语义，但 ordinary no-progress、平台存储失败和 provider failure 仍要分开。

router 自动 rework 不能只把 FAILED 改成 RUNNING。`_stage_local_rework_is_authorized` 要求 Service 投影的 durable workflow owner action，包含 v2 deferred-drain、action id、diagnostic id、正确 decision owner；Service 的 strict stage transaction guard 与 physical replay fence 再防止 stale callback 覆盖当前 run。QA rework、Director local rework、官方用户 retry 是不同入口，应复用 owner authority 并保留 PMCE。

源码：repair ownership（src/backend/polaris/cells/director/runtime/public/service/_execution.py:825）、router rework authority（src/backend/polaris/delivery/http/routers/_factory_handlers/mapping.py:129）、guarded mutation（src/backend/polaris/cells/factory/pipeline/internal/factory_run_service/_service_core.py:971）。

## 11. 最终 ProjectOutcome 是多 owner 绑定，纯 reducer 不是 authority

纯 reducer 只计算 completion_candidate，始终 authority_bound=false/completed_verified=false。bootstrap-bound owner adapter 查询当前 CE completion contract、TaskRuntime、RunLedger、physical diagnostics；校验身份、projection hash 与 evidence refs。另一 public owner port 查询 Factory chain 和 durable completed 事件。authority binder 验证两组 exact DTO 与所有 axis hash/ref，才把候选升级为 completed_verified。

六轴是 delivery、chain、QA、TaskBoundary、TaskRuntime、RunLedger。当前 adapter 还要求账本 available、ready、integrity_ok/outcome_ok/capability.ok、无有效 missing/failed、QA 与 TaskBoundary 通过。physical diagnostics 必须 authority_bound、覆盖全适用 obligation、无 missing/failed；它不能从文件存在或四个 exit 0 推出全绿。

这个设计避免“caller success flag 就是交付”，也避免简单重新造一个万能 completion service。需要继续核对每个 producer 怎样给这些 owner 产生当前、可重读的事实。现有控制面最复杂的点是 fact gathering、恢复后的当前性与异步顺序，而不是纯 reducer。

源码：纯 reducer（src/backend/polaris/cells/runtime/projection/internal/project_outcome.py:15）、owner authority binding（src/backend/polaris/cells/runtime/projection/internal/project_outcome_authority.py:69）、当前 owner bundle（src/backend/polaris/bootstrap/runtime_projection_project_outcome_owner.py:431）。
