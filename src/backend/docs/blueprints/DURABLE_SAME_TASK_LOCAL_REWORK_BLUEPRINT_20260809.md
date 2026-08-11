# Durable Same-Task Local Rework Blueprint

- 状态: Restart integration hardening active; fresh Bench verification pending
- 日期: 2026-08-09
- 关联: ADR-0100, ADR-0101

## 问题

Factory QA 在 Director execution attempt 之外运行 verifier。QA 发现普通代码错误时，
`execution_attempt=None`，因此 deterministic repair 和 Director local rework 都无法
绑定原任务。Factory 过去只能结束整条链，下一次运行又从 PM/Chief Engineer 开始。

第一版候选桥把 QA failure 先写入 `runtime.task_market`，再尝试重开 TaskRuntime。
该方案不成立：Factory Director 的生产 claim 读取 `runtime.task_runtime`；失败样本也
没有 TaskMarket work item。TaskMarket requeue 不会改变 TaskRuntime 行，因此会形成
不可达的第二事实源。

## 目标架构

```text
canonical QA artifact
        |
        v
VerificationGuard emits one owner-sealed residual
        |
        v
workflow_orchestration persists action + dispatch claim in completion cursor
        |
        v
bootstrap action owner validates action and calls runtime.task_runtime public command
        |
        v
reopen exact numeric TaskRuntime row, preserve PM/CE contract and failure context
        |
        v
Director claims only that row, edits code, reruns affected verifier
```

当前实现已完成 sealed diagnostic、durable cursor、TaskRuntime exact-row reopen，以及
backend lifespan-owned `FactoryRunDriverRuntimeV1`。HTTP Router 只向该 runtime 提交；
backend 启动会恢复 `RUNNING/RECOVERING` runs，并合并 pending exact-row local-rework
actions，因此 action commit 后重启不再依赖新 HTTP 请求。执行算法 callback 仍位于
delivery module，后续可做代码归属迁移，但任务生命周期已不再由 Router-owned task 掌控。

进程重启恢复采用两阶段物理尝试代际切换：先严格 replay 并永久关闭旧 coordinator，
确认所有旧 start receipt 已有 terminal settlement；再通过显式
`resume_recovered_run` lifecycle claim 获取更高 workspace fencing token，创建空的新
execution epoch。禁止复活旧 attempt authority，也禁止把“旧 epoch 永久关闭”扩大为
“整个 Factory run 永久关闭”。真实后端子进程测试已证明无需新 HTTP start 请求即可
从 `RUNNING → RECOVERING → terminal`。

`FAILED` run 的显式同阶段 retry 也必须执行相同代际切换。若 backend 已重启、
process-local coordinator 缺失，`retry_run_from_stage` 必须先从 durable role-evidence / provider-lifecycle
facts 重放并结清旧 epoch；若仍在同一进程但 terminal drain 已关闭 coordinator，则必须验证
持久化 release evidence 与 physical-attempt drain 均已 settled。只有这些前置证据成立，才能开
新 epoch 并继续原 Director 阶段。普通第二服务不得借 retry 之外的 mutation 绕过 replay fence。

## 模块职责

- `factory.pipeline`: 只在 CE contract、Director/QA/Run Ledger owner facts 变化后发送
  workspace-scoped wake；不拥有 retry cursor，不签发执行 authority。
- `factory.verification_guard`: 从 CE contract 和 owner evidence 产出 sealed residual，
  包含 exact owner task、affected target、evidence refs、repair/verifier intent。
- `orchestration.workflow_orchestration` + `workflow_runtime`: 持久化 action、claim、
  receipt 和 bounded convergence cursor；每次只调度一个 dependency-ready residual。
- `runtime.task_runtime`: 唯一 task lifecycle owner；验证 receipt hash/identity、
  active lease、attempt budget 和 exact row binding；只重开原 numeric row，保存
  failure context，不修改其他任务。
- `director`: 继续消费 TaskRuntime claim；不接收 QA 旁路，不回退 PM/CE。
- lifecycle-owned Factory stage driver: 必须在 backend lifespan 内恢复所有 durable
  runnable Factory run，消费已提交 action 对应的 exact TaskRuntime row，并复用原
  PM/CE authority、failure diagnostic 和 verifier scope。HTTP Router 只能提交/查询，
  不再拥有执行循环生命周期。
- bootstrap action owner: 当前 mainline 的同任务 action 直接落 TaskRuntime；不得假定
  staged TaskMarket work item 存在。TaskMarket 只保留已真实发布 item 的跨角色 broker 用途。

## 不变量

1. action 必须来自 owner-sealed VerificationGuard diagnostic 和 durable cursor claim。
2. action 必须绑定 workspace、Factory run、completion contract、owner task、diagnostic、
   owner snapshot、owner bundle、attempt ordinal 和 lease。
3. affected target、evidence refs、repair source/verifier ids 必须完整进入 action；不得只传
   一个无法执行的 diagnostic id。
4. TaskRuntime receipt 必须绑定 action id、claim id、当前 owner execution anchor。
5. TaskRuntime 只允许重开该 Factory run 内唯一匹配的 numeric row。
6. active lease、冲突 alias、过期/伪造 receipt、attempt budget exhausted 全部 fail-closed。
7. 相同 receipt replay 幂等；真实新 execution 后允许新 receipt，最多三次。
8. 不重建 PM contract、不重跑 CE、不 reset unrelated tasks、不修改目标项目。
9. action committed 后必须存在可查询的 stage-driver claim；仅 TaskRuntime pending row、
   Router 内存 task 或 process-local queue 均不得计作 durable progress。
10. backend 在 action commit、Director edit、verifier rerun 任一切点重启后，必须从
    durable claim 恢复且最多产生一次物理 effect。
11. restart replay 只永久关闭旧 physical-attempt epoch；新 epoch 必须使用更高
   workspace fencing token，旧进程/旧 grant/旧 provider request 永远不可复活。
12. `FAILED` retry 必须以 terminal release + settled physical-attempt evidence 为前置；
    coordinator 缺失时必须先 durable replay，禁止直接 new coordinator 或复活旧 grant。

## 失败防御

- TaskMarket item 缺失不能阻断当前 TaskRuntime mainline。
- VerificationGuard diagnostic seal、owner bundle 或 cursor claim 不合法时拒绝。
- owner task 为空、冲突 alias、run/contract mismatch 时拒绝。
- receipt payload 任一字段被改写时 hash 校验失败。
- 已有 active execution lease 时不得抢占。

## 验证

1. RED: durable convergence action 当前只 requeue TaskMarket，TaskRuntime owner row 不变。
2. GREEN: owner action receipt 来自 TaskRuntime，exact row 被重开。
3. 负例: forged action/claim、cross-run、conflicting alias、active lease、stale replay 均拒绝。
4. 回归: Factory admission/router、TaskRuntime full focused gates、Ruff、Mypy。
5. Factory 在 CE、Director、QA owner facts commit 后均发送显式 wake；无 timer/polling。
6. 独立反证审计通过后，fresh isolated L1-01 必须证明失败从 Director 原任务继续；
   只有 `COMPLETED_VERIFIED` 才能声明项目跑通。
7. crash-recovery: action commit 后终止 backend，重启后无需 HTTP 客户端重提请求，
   lifespan-owned stage driver 必须自动消费 exact row 并继续 affected verifier。
8. failed-retry recovery: 真实 terminal close 后，同进程与重启后两种 `retry_run_from_stage`
   都只开启新 epoch；重启路径必须先 replay，普通第二服务直接执行仍返回
   `factory_physical_attempt_replay_required`。
