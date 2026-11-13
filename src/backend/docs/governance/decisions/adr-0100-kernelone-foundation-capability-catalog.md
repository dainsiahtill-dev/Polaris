# ADR-0100: KernelOne 底座能力目录与债务登记

- 状态: accepted
- 日期: 2026-08-10
- 决策范围: `polaris/kernelone`、`polaris/cells/kernelone`、底座准入与债务登记链
- 关联计划: `docs/blueprints/KERNELONE_FOUNDATION_HARDENING_PLAN_20260810.md`

## 背景

`polaris/kernelone` 已成为多层技术底座（LLM / Context / Effect / FS / Events / Quality），
顶层子树数量持续增长。缺少"能力目录 + 准入清单"时：

1. 新 Agent 无法判断某个能力应复用哪个公共面，容易在 Cells 内重复实现。
2. 底座继续堆目录，向第二个 `core/` 垃圾场滑落。
3. 已知结构性债务没有统一登记，残余风险无法被门禁观测。

## 决策

1. **能力目录是底座入���的唯一事实源。**
   `docs/graph/catalog/kernelone_capabilities.yaml` 列出底座顶层子树及其准入策略；
   `polaris/tests/architecture/test_kernelone_toplevel_admission.py` 以该目录为准冻结顶层子树集合。
   新增顶层子树必须同时提供 ADR 与目录条目，否则准入测试失败。

2. **底座不得再膨胀为新的 `core/`。**
   底座只提供技术实现，Cell 的 `public/` 契约是对外唯一消费面；
   `polaris/cells/kernelone/*` 与 `polaris/kernelone/*` 的双入口由债务登记显式追踪。

3. **结构性债务必须登记完整证据链。**
   `docs/governance/debt.register.yaml` 中每条未退役债务必须链接：至少一条 ADR、
   一张验证卡、一个 verify pack 与一组测试。缺任一组即视为未登记，
   `polaris/tests/architecture/test_structural_bug_governance_assets.py` 负责门禁。

4. **状态词汇收敛。**
   债务状态只有 `active`（未完成收敛工作）、`monitoring`（已冻结为长期守卫的不变量）
   与 `retired`（已关闭）三种；`active` 条目必须进入门禁的显式白名单，
   `monitoring` 条目必须进入预期 ID 集合。

## 后果

- 底座顶层子树集合被冻结，新增子树需要 ADR + 目录条目。
- 15 条 KernelOne 底座结构性债务以 `monitoring` 状态登记，并各自链接证据链；
  它们是守卫型不变量，不计入"未完成收敛工作"。
- 债务登记不再接受缺少证据链的条目：登记即必须可验证。

## 验证

- `polaris/tests/architecture/test_kernelone_toplevel_admission.py`
- `polaris/tests/architecture/test_repair_kernel_path_ssot_fence.py`
- `polaris/tests/architecture/test_structural_bug_governance_assets.py`
- `polaris/cells/kernelone/generated/verify.pack.json`
