# Execution Budget Policy Blueprint (2026-07-03)

## 1. 问题陈述（量化证据）

2026-07-03 双 workflow 对抗审计（基于 bench r32-r78 战役证据 + 代码坐实）确认：Director 调用路径上的输出预算/超时由 **~21 个独立机制**分散设置，**7 个层**均可改写同一数值：

- 3 个 key 集不同的 context 扫描 reader：`llm_caller/helpers.py`（43 条路径）、`llm_caller/tool_helpers.py`（51 条，仅 strategy payload）、`kernel/transaction_factory.py`（镜像 helpers）。
- 4 个互不知晓的常量 `7000`：`adapter.py`（cap）、`tool_helpers.py`（cap）、`request_preparer.py`（cap）、`retry_escalation_policy.py`（**floor**，语义相反）。
- `min(value, 128_000)` 硬钳制手抄 ≥4 处；12 种 timeout key 拼写；67 个相关 `KERNELONE_*` env var。
- `KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS` 由 2 份独立实现解析；director dispatch timeout 由 2 份实现解析且默认值不同（600+60 vs 1800）。
- 后果实证：「retry 继承 128k 预算」同一缺陷在 5 条路径上被独立重新发现并修复 5 次（bench r38/r40/r45/r55/r71）；未修 sibling 仍存在（`factory_stage_executor.py` workspace quality repair 的 45.0s 最小启动预算，与 r46 已修的 CE 拷贝不一致）。

根因：**预算不是一等事实**。每条调用路径自行组装，路径 N+1 必然因遗漏而回归。

## 2. 目标态架构

```
director.tasking (strategy origin)          roles.kernel (resolver home)             consumers
┌──────────────────────────┐   intent   ┌────────────────────────────────┐  frozen  ┌──────────────────┐
│ execution_strategy       │ ─────────► │ classify_turn_kind(ctx, opts)  │ ───────► │ request_preparer │
│  (task_type→output意图)  │            │ resolve_execution_budget(...)  │  Resolved│ invoker          │
└──────────────────────────┘            │  = 单一优先级编码点            │  BudgetV1│ transaction_     │
                                        │  (ceiling恒min/floor语义显式/  │  (context│   factory        │
      env overrides (单一解析点)  ────► │   deadline恒最高)              │  单一键) │ tool_helpers     │
                                        └────────────────────────────────┘          └──────────────────┘
```

核心裁决：

1. **`ResolvedBudgetV1`**（frozen dataclass）：`max_output_tokens` / `output_floor_tokens` / `llm_timeout_seconds` / `request_timeout_seconds` / `turn_kind` / `provenance`（每个字段记录来源机制与被覆盖的前值）。随 request context 以单一键 `execution_budget` 流动；下游只读。
2. **`classify_turn_kind`** 是唯一 turn 类别分类器（`first_call` / `ordinary_followup` / `forced_write_retry` / `required_tool_retry` / `repair_subcall` / `finalization`），替换 adapter / request_preparer / tool_helpers 三处各自的字符串检测。
3. **优先级语义只编码一次**：ceiling 参与方向恒 `min`；floor 仅在明示 floor 语义处 `max`；factory deadline 恒支配；`128_000` 硬钳制唯一实现。
4. **常量与 env 解析单点化**：`budget_policy.py` 承载 `FORCED_WRITE_OUTPUT_TOKEN_CEILING = 7000` 等命名常量与 env 覆盖解析，四个既有站点全部改为 import；两份 env parser 合一。

Cell 边界说明：strategy 产生地在 `director.tasking`，解析器落位 `roles.kernel`（其 `llm_caller/helpers.resolve_max_tokens/resolve_timeout_seconds` 已是 cell 内事实 funnel，本蓝图是把 funnel 升格为跨路径唯一实现，不新建跨 cell import；`director.tasking` 继续只发布意图 key，不 import roles.kernel）。

## 3. 分阶段落地（每阶段独立可验证）

- **Phase 1（本蓝图落地范围）**：
  1. 新建 `roles/kernel/internal/llm_caller/budget_policy.py`：命名常量表 + 唯一 env 解析 + `classify_turn_kind` + `ResolvedBudgetV1`。
  2. 四个 `7000` 站点、两份 env parser、≥4 处 `min(*,128_000)` 全部收敛到该模块（行为逐值等价，用既有测试钉住）。
  3. `request_preparer.prepare` 在最终请求组装处调用 `resolve_execution_budget` 一次，产出 `ResolvedBudgetV1` 写入 request context/audit metadata（观测性落地，不改变既有 resolve 结果——resolver 就是重构后的同一 funnel）。
  4. `transaction_factory` 与 `tool_helpers` 的独立扫描改为委托 helpers 同一实现（key 集合并集 + 单一优先序，diverging key 逐一列举并测试钉住）。
  5. 修复 sibling：`factory_stage_executor.py` workspace quality repair 45.0s 阈值与 CE 拷贝共享同一常量。
- **Phase 2（后续）**：(role, turn_kind, model_class) 声明式表；env 覆盖收敛为表覆盖单机制；退役 ~10 个路径 env var。
- **Phase 3（后续）**：删除 12 个 legacy budget/timeout context key 拼写的写入端（读端兼容层最后删）。

## 4. 验证与回归护栏

- 既有钉住套件必须全绿：`test_llm_caller_helpers.py`、`test_llm_caller_components.py`、`test_tool_surface.py`、`test_transaction_kernel_facade.py`、`test_role_kernel_transaction_wiring.py`、adapter 预算相关用例、`test_factory_stage_executor_characterization.py`。
- 新增：constants 单源测试（四站点 import 同一符号）、`classify_turn_kind` 全类别表测试、`ResolvedBudgetV1` provenance 测试、reader 合并的逐 key 等价测试。
- Bench oracle：L2-08 isolated 复跑不得出现新的 `call_cancelled`/timeout 类根因。

## 4.1 2026-07-04 增量落地记录

- 共享预算策略实际落位为 `polaris.kernelone.llm.budget_policy`，不是
  `roles/kernel/internal/llm_caller/budget_policy.py`。该 placement amendment
  避免跨 Cell import role-kernel internal，同时允许 `roles.kernel`、
  `roles.adapters`、`factory.pipeline` 共同消费 KernelOne fact。
- `resolve_execution_budget()` 已接管 request-preparer 本地
  `ResolvedBudgetV1` 构造逻辑。`request_preparer` 现在只传入已经解析完成的
  provider request 数字与检测标记；预算策略模块负责冻结 typed projection、
  provenance 和 `classify_turn_kind()` 结果。
- 验证：
  `rtk pytest src/backend/polaris/kernelone/tests/test_llm_budget_policy.py -q`；
  `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_llm_caller_components.py src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -q -k "budget or execution_budget or request_preparer or call_returns_dict"`；
  `rtk ruff check src/backend/polaris/kernelone/llm/budget_policy.py src/backend/polaris/kernelone/tests/test_llm_budget_policy.py src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py`；
  `rtk mypy src/backend/polaris/kernelone/llm/budget_policy.py src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py`。

## 5. 风险与边界

- reader key 集合并集可能改变「先读哪个 key」的边缘行为——所有 diverging key 必须逐一枚举、在测试中钉死新优先序，并在 `provenance` 中可见。
- 不动 `execution_strategy` 的 12-key 扇出（Phase 3 才删写入端），保证 prompt builder / bench gate 消费不受影响。
- 本蓝图不引入新 `KERNELONE_` flag（与 WS7 flag registry 治理一致）。
