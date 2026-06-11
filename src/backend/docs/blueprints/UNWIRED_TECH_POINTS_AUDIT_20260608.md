# 审计：已定义但未接线 / 需进一步完善的技术点 (2026-06-08)

> 目标（`/goal`）：继续深挖还有哪些技术点没有接线或者需要进一步完善的。
> 方法：3 路并行 Explore（未接线公开契约 / 未接线子系统 / 默认关闭的特性开关）+ 定向核验。

## A. 定义了但无 live 调用方（真缺口，可操作）

| # | 技术点 | 证据 | 缺的那根线 |
|---|---|---|---|
| A1 | **ProjectionEngine.record_outcome** 无生产调用方 | 全仓仅 cognitive `pipeline_coordinator` 调的是另一个 quantifier.record_outcome；ProjectionEngine 的从不被调 | turn 收尾处按真实成败调用（本会话已让权重跨 turn 持久，就差喂数据）。**外加产品决策**：权重是否应影响 `sort_events`（当前 sequence 为主键，权重是永不触发的 tiebreaker）|
| A2 | **CTEngineMiddleware**（认知推理中间件）注册函数从不被调 | `create_and_register_ct_middleware()` 仅出现在 docstring；`_global_ct_middleware` 恒为 None | 在 bootstrap/init 注册（但受 A 类认知开关默认关影响，见 B1）|
| A3 | **公开 Command/Query 契约无公开 service 实现** | 见下表 | 补 public service handler，或明确这些契约非公开入口 |

### A3 明细（公开契约声明了但无 public/service.py 处理）——✅ 已闭环 2026-06-11（G4 同款接线）
- `qa/audit_verdict`：`ClaimQaTaskCommandV1` → `public/service.py::claim_qa_task`（task market 定向认领，`ClaimTaskWorkItemCommandV1` 原生支持 task_id；与轮询 QAConsumer 同一市场服务/同一 stage）。
- `director/planning`：→ `public/service.py`：`plan_director_task`（走 `DirectorAgent.handle_message` 的 PM TASK 同款入口）、`get_director_status`（RiskRegistry+QualityTracker 真实持久数据）。**坑**：roles.runtime.public 的 lazy `__getattr__` 解析 AgentMessage/MessageType 到 internal.agent_runtime_base，而 public/contracts.py 导出的是 kernelone shared_contracts 同名异类——跨类 enum 比较静默 False；必须从 `public.service` 导入。
- `director/tasking`：→ `public/service.py`：`create_task`/`cancel_task`/`query_task_status`/`query_task_result`（per-workspace 单例 TaskService + `reset_task_services()` 测试钩子；运行时自建实例不受影响）。
- 测试：3 个 cell 共 19 个契约 handler 测试全绿。

**重要澄清（避免误判）**：director/tasking、director/planning 的 `internal/` 有**丰富实现**
（`worker_executor`、`task_execution_runner`、`director_logic`、`code_generation_engine`、
`worker_pool_service` 等），功能经 **CLI / director.runtime** 路径在用。**未接线的是它们声明的
跨 cell 公开契约 API**（无法用 Command/Query 驱动，只能走 internal/CLI）——是"公开契约形同
aspirational/未实现"的架构不一致，不是功能死代码。

## B. 建好了但默认关闭（大体量休眠子系统）

| # | 子系统 | 开关（默认） | 规模/说明 |
|---|---|---|---|
| B1 | **认知流水线**（perception/reasoning/execution/evolution/personality/governance/value_alignment）| `KERNELONE_COGNITIVE_ENABLED=0`（仓内从未置 1）| **74 文件 / 15,507 行**，默认整体休眠。注意区分：context 的 `KERNELONE_COGNITIVE_RUNTIME_MODE` 默认 `mainline`（开）是另一回事——认知**流水线**本身是关的，且 CTEngineMiddleware（A2）未注册 |
| B2 | **语义检索重排** | `ACCEL_SEMANTIC_RANKER_ENABLED` / `ACCEL_SEMANTIC_RERANKER_ENABLED=False` | 嵌入式代码片段重排（其余 ACCEL_* 多默认开）|
| B3 | **LLM 工具自动生成** | `KERNELONE_ENABLE_LLM_TOOLS=0` | 自动给 ChiefEngineer / Director 注入 LLM 生成的工具 |
| B4 | **SLM 本地推理** | `slm_enabled=False` | 小模型预热/本地推理 |
| B5 | Tri-Council 决策投票 / Session Orchestrator / QA UI 插件 / 硬回滚 | 各自默认 False | 中等成熟度特性 |

## C. 本会话已建、就差最后一根线（多属产品/设计决策）

- C1 角色信号 `include_blueprint_overview` / `include_verdict_history` 默认 False —— 待逐角色 opt-in。
- C2 角色信号熔断：gateway 的 `budget_pressure_detected`（压缩后实测）未回接 allocator；当前用的是
  分配前的便宜预估。可把实测压力反馈进来。
- C3 ProjectionEngine 自适应权重不影响排序（见 A1 后半）。

## D. 非缺口（勿误判为待修）
- `base_sdk` / `auth_context` / `context_os/runtime/state` / `engine/providers` 的 `NotImplementedError`
  = 抽象接口基类（合法）。
- `infrastructure/di/factories` 抛 `NotImplementedError` = **故意废弃**的工厂（合法）。

## 优先级建议
1. **A1（记录-学习闭环）+ C3**：把 ProjectionEngine 自适应学习真正激活——但需先做"权重是否应影响排序"
   的设计决策（否则学了也不影响 prompt）。
2. **A3 / director 契约**：决定 director tasking/planning 的公开契约是补 service 实现，还是降级为
   internal-only（去掉 aspirational 公开声明），消除架构不一致。
3. **B1 认知流水线**：15.5k 行休眠代码——需产品决策"是否启用/分阶段灰度/还是收编"。这是最大的
   "建了但没用"。
4. **A2 CTEngineMiddleware**：随 B1 决策一并处理（注册 + 灰度）。

## 已在本会话修复/接线的相关项（备查）
- `qa.audit_verdict` 新增 verdict 持久化 + `get_qa_verdict` 只读契约（原 `GetQaVerdictQueryV1` 是无实现的契约）。
- `blueprint_overview` 接 `get_blueprint_status`；`verdict_history` 接 `get_qa_verdict`。
- 角色信号面 freshness 跨 turn 持久化；ProjectionEngine 自适应状态跨 turn 持久化（A1 的"持久"部分已做，缺"喂数据"）。
