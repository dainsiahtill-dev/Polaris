# ADR-0101: Durable Project-Completion Actions Use TaskRuntime Authority

- 状态: Implemented; restart integration complete; fresh isolated L1-01 verified
- 日期: 2026-08-09
- 关联: ADR-0097, ADR-0100

## 背景

普通 build/test/lint failure 发生在 Factory QA verifier，但 owning Director execution
attempt 已经 settlement。若 QA failure 没有回到原 TaskRuntime row，Factory 只能终止
链路，外层重跑便重复 PM 和 Chief Engineer，浪费 token 且丢失局部失败上下文。

TaskMarket requeue 曾被考虑为桥接方案。当前生产 Factory Director dispatch 读取
TaskRuntime，TaskMarket mainline migration 仍是 staged；实际失败 run 也没有对应
TaskMarket work item。使用 TaskMarket 会增加不可达的第二生命周期事实源。

## 决策

1. VerificationGuard 从 owner evidence 生成 sealed residual；workflow runtime 的 durable
   cursor 生成 action 和 dispatch claim。
2. bootstrap action owner 对当前 TaskRuntime mainline 通过 `runtime.task_runtime.public`
   发出 same-task local-rework command；不得要求 staged TaskMarket item 先存在。
3. TaskRuntime 验证 action、claim、唯一 task identity、execution anchor、lease 和预算后，
   重开同一 numeric row，并保存 failure diagnostic 供下一次 Director claim 使用。
4. Factory 不拥有 retry loop；它只在 CE/Director/QA owner facts commit 后显式唤醒
   durable completion supervisor。
5. TaskMarket 不参与当前 mainline 的同任务局部修复。
6. 仅普通实现/验证失败可走该路径；PM contract 或 CE architecture authority 变化仍由
   上游显式处理。
7. HTTP Router 不得拥有 durable execution lifecycle。需要一个 backend lifespan-owned
   Factory stage driver 查询 committed action/TaskRuntime exact row，持久化 driver claim，
   执行 Director/affected verifier，并在进程重启后自动恢复。
8. restart recovery 必须先 replay/settle 并永久关闭旧 physical-attempt epoch；随后只允许
   通过显式 lifecycle claim 和更高 workspace fencing token 创建新 epoch。旧 provider
   attempt 不可重放，但同一 Factory run 不得因此永久失去继续执行能力。

## 被拒绝方案

1. **整链重跑 PM/CE**：浪费 token，丢失已验证 contract/blueprint，无法局部收敛。
2. **TaskMarket requeue 后假定 Director 会执行**：当前生产 claim 不读该状态，形成
   第二事实源。
3. **Factory 自建 process-local receipt/retry loop**：与 ADR-0100 durable cursor 冲突。
4. **Factory 直接改 TaskRuntime internal 文件**：跨 Cell 越界，绕过 public contract。
5. **直接修改生成项目**：Bench 量具污染，违反元平台边界。

## 后果

- ordinary residual 可在原 Director task 内 bounded retry。
- PM/CE provider token 不再被普通代码错误重复消耗。
- TaskRuntime 保持唯一 execution lifecycle owner。
- workflow orchestration 与 TaskRuntime 增加 typed public contract，需要同步 graph/catalog 和测试。
- backend lifespan-owned driver 已恢复 live/recovering runs 与 pending local-rework
  actions；真实 backend process restart 已验证旧 epoch 关闭、新 epoch 续跑且无需新
  HTTP 请求。
- 2026-08-12 的 fresh isolated L1-01 r44 已达到 `COMPLETED_VERIFIED`。Task 3
  在 TS6133 后保持 exact Director task ownership，完成局部 repair、affected verifier
  复跑与 settlement，未重启 PM/CE。该证据只关闭“至少一个项目”的验收项；平台级
  就绪仍需顺序推进后续项目与 N-batch 无新通用根因证据。完整记录见
  `../UNATTENDED_COMPLETION_FIRST_PROOF_20260812.md`。

## 验收

- action/claim forge、cross-run、identity conflict、active lease 全部 fail-closed。
- exact row reopen、unrelated rows unchanged、same receipt idempotent。
- Director 下一轮 final request 包含 owner-sealed failure context 和原 PM/CE authority。
- focused Ruff/Mypy/Pytest 通过。
- fresh isolated L1-01 到 `COMPLETED_VERIFIED`；否则只能报告新的精确断点。
