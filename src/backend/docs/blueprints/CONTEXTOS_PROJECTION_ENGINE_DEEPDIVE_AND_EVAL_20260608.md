# Blueprint: ContextOS / ProjectionEngine 深挖 + 评测矩阵 + 深层缺陷修复 (2026-06-08)

> 目标（`/goal`）：深挖 ContextOS 和 ProjectionEngine，写对应 agentic-eval 矩阵评估真实效果，
> 顺便修复深层 bug/逻辑缺陷。

## 1. 架构真相（深挖结论）

ContextOS 是 ~46k 行子系统。真实 turn 的上下文组装是**两阶段**（已核对调用链）：

```
RoleExecutionKernel._execute_transaction_kernel_turn  (kernel/core.py:1120)
  └─ RoleContextGateway.build_context()                (context_gateway/gateway.py)
       ├─ StateFirstContextOS.project()   [阶段1] 读 TruthLog/WorkingState，跑 pipeline，
       │                                          产出 ContextOSProjection.active_window
       └─ ProjectionEngine.project()      [阶段2] gateway.py:313：把投影**组装成 LLM messages**
```

**四件套职责**（`kernelone/context/`）：
- **TruthLog**（`truth_log_service.py`）：不可变真相日志。
- **WorkingState**（`working_state_manager.py`）：可变工作态。
- **ReceiptStore**（`receipt_store.py`）：大工具输出内容寻址存储（`ContentStore` 去重），prompt 里只放引用。
- **ProjectionEngine**（`projection_engine.py`，**LIVE 热路径**）：最终 prompt 组装器，负责
  control-plane 隔离、按 sequence 时序排列、receipt 卸载、user 指令置末、structured_findings 注入。

## 2. 发现的深层缺陷（3 个）

### F1 — 内容级 control-plane 泄漏（已修）
ProjectionEngine docstring 自称 "Control-plane noise is excluded at **both payload and turn level**"，
但实现只剥离**顶层键**与**每条 turn 的 metadata 键**——turn **content** 里嵌入的
`<tool_result>…</tool_result>` 包裹标签、`[system warning]/[circuit breaker]` 整行**原样透传给模型**。
专用检测器 `control_plane_noise.is_control_plane_noise()` 存在却**从未被调用**。实测探针证实泄漏。

### F2 — 自适应学习恒为空转（已修内部正确性）
`record_outcome()` 把 route/confidence/recency 分**硬编码为 0.5**，而真正计算质量的
`_compute_projection_quality()` **从不被调用** → 权重学习永远朝 0.5 分布收敛，等于无操作。
更深的架构性阻断：`ProjectionEngine` 在 gateway.py:208 / core.py:1145 **每 turn 新建**，
其滚动窗口学习状态跨 turn 不累积；且 `sort_events` 以唯一 `sequence` 为主键，权重沦为永不触发的
tiebreaker。

### F3 — 冗余双投影（记录，未改）
`kernel/core.py:1145` 对 gateway 已投影过的 `context_result.messages` **再投影一次**，并用一个
**全新空 ReceiptStore()**。实测会多出一条重复 system 消息（3→4）。属热路径、改动风险高，本次仅记录，
建议后续以"直接前置 system_prompt"替代再投影。

## 3. 评测矩阵（真实效果评估）

新增**确定性** agentic-eval 套件（无需 LLM——ProjectionEngine 的效果是 prompt 卫生/正确性属性，
确定性可判定，符合 deterministic-judge 哲学）：

- 模块：`cells/llm/evaluation/internal/context_projection_matrix.py`
- 入口：`run_context_projection_matrix_suite`，经 `evaluation/public/service.py` 导出，接入
  `agentic_eval` CLI：`--suite context_projection_matrix`（含独立报告 `_report_context_projection_matrix`、
  `__main__` choices）。
- 8 个用例覆盖真实效果：control-plane 隔离（顶层+metadata）、**内容级 control-plane 剥离**、
  **信号角色内容不被改动**、user 指令置末、receipt 卸载（省 token + 原文可取回）、structured_findings
  注入、时序+system_hint 置首、空窗口健壮性。
- 硬门禁：control-plane 泄漏总数必须为 0 且全部 PASS。

**运行结果**（`python -m polaris.delivery.cli agentic-eval --suite context_projection_matrix`）：
**8/8 PASS，control_plane_leaks=0，receipt_chars_saved=7157**。其中 `content_level_control_plane_stripped`
正是 F1 的回归守门（修复前必 FAIL）。

## 4. 修复

### F1 修复（安全、保守）
- `control_plane_noise.py` 新增 `strip_control_plane_markers()`：去 `<tool_result>` 包裹标签（保留中间内容）、
  删 `[system warning]/[system reminder]/[circuit breaker]/tool result:` 整行。
- `projection_engine.py` 在 `build_turns` 与 `_normalize_turn` 对**非信号角色**（tool/system）内容调用之；
  **信号角色（user/assistant）内容一字不动**（模型与用户的真实话语）。
- 测试：`test_context_control_plane_noise.py`（新增 5 例）+ 矩阵 2 例。

### F2 修复（内部正确性 + 文档化架构阻断）
- `sort_events` 记住 `_last_projected_events`；`record_outcome` 改用 `_compute_projection_quality`
  从真实事件算质量分（替换硬编码 0.5）。权重现在**确实随结果变化**，并能区分校准好/坏的结果。
- 测试：`test_projection_adaptive_learning.py`（3 例，证明权重不再冻结、能区分校准）。
- **诚实边界**：本修复让机制**内部正确**；要在生产真正生效，还需 (a) ProjectionEngine 跨 turn
  会话级持久化（当前每 turn 新建），(b) `sort_events` 让权重实际参与排序。二者属热路径行为变更，
  风险较高，留作后续 ADR 评估。

## 5. 验证

```bash
ruff check <paths> --fix && ruff format <paths> && mypy <paths>
python -m polaris.delivery.cli agentic-eval --workspace . --suite context_projection_matrix   # 8/8 PASS
pytest polaris/tests/unit/kernelone/test_context_control_plane_noise.py \
       polaris/tests/unit/kernelone/test_projection_adaptive_learning.py \
       polaris/cells/llm/evaluation/tests/test_context_projection_matrix.py -q
```

ProjectionEngine + control_plane 既有定向测试 19 passed；新增测试全绿。F1 是热路径内容变更——
已用 19 项既有测试 + 8 项矩阵 + 5 项单测确认安全（信号角色内容零改动）。

## 6. RoleSignalPlane 原型（角色资产信号注入）

**尽调逆转**:深入 StateFirstContextOS + gateway 后发现"信号面"的轮子**已隐式存在**——
`_build_projection_dict` 早已把 `project_structure`【项目结构】、`task_history`【任务历史】
作为 `supplemental_turns` 注入,且 (a) 角色绑定 = `RoleProfile.context_policy.include_*`(每角色独立),
(b) 预算 = `CompressionEngine`/`max_context_tokens`。故**不新建平面,只泛化这处硬编码循环**。
（同样地,`strategy_overlay` 经核查是"行为调参",与内容注入正交,**不可挪用**。）

**落地**(`cells/roles/kernel/internal/context_gateway/role_signals.py`):
- `RoleContextSignal` 端口 + `RoleSignalRegistry`(镜像 overlay 的 per-role 解析,但只管内容)。
- 2 个 seed provider 由原硬编码重构:`ProjectStructureSignal`(=code_repo 类,全角色 fallback,
  priority 0)、`TaskHistorySignal`(=plan 雏形,priority 1)——**保留原 id/内容/顺序 → 逐字节一致**。
- `allocate_role_signals()` 实现三道防御:
  1. **逐字节 + 绝对索引一致**:seed 以 priority 0/1 最前;`max_chars=None` 时永不卸载;gateway 以
     `per_signal_char_cap=None, total_char_budget=None` 调用 → 与旧实现等价。
  2. **sources 溯源泛化**:每个注入信号把 `id` 压入 sources(trace 谁塞了过期资产)。
  3. **budget_pressure 熔断**:压力下 freshness 未变的 nice-to-have 断流;must-have 经 ReceiptStore
     摘要+引用极限挤入;超 `per_signal_char_cap` 的信号截断为 `receipt://` 占位符(原文可取回)。
- gateway `_build_projection_dict` 的硬编码两段 → registry 驱动循环(pass-through 调用,逐字节一致)。

**验证**:
- 单测 `context_gateway/tests/test_role_signals.py`(7):逐字节/角色隔离/熔断卸载/freshness 断流/
  must-have 存活/溯源。
- 评测矩阵新增 3 例并经 CLI 跑通:`--suite context_projection_matrix` → **11/11 PASS,
  control_plane_leaks=0,receipt_chars_saved=107109**(含 role_signal_byte_identical /
  role_signal_cross_role_isolation / role_signal_circuit_breaker)。
- 热路径安全:gateway 改动用 git-stash 对照确认 `test_llm_caller` 15 红与本改动无关(预存,MagicMock JSON 序列化)。

**已追加(角色专属 provider)**:`BlueprintOverviewSignal`(role==chief_engineer)与
`VerdictHistorySignal`(role==qa, level=must-have)已实现并注册。双门控:role-bound +
新 `context_policy.include_blueprint_overview / include_verdict_history`(**默认 False**)+
访问器优雅降级(无数据→不注入)→ **baseline 逐字节不变**。`RoleProfile.context_policy`
schema 扩两 flag(默认 False)。gateway 传入 flag,访问器暂为 None-hook(注释标明接
`chief_engineer.blueprint.public.get_blueprint_status` / `qa.audit_verdict.public` verdict 查询;
task→资产 id 映射确定后一行接上,因 flag 默认关,热路径零风险)。
测试:`test_role_signals.py` 增 4 例(CE 仅蓝图 / QA 仅判定且 must-have 抗压 / flag 关时 baseline /
蓝图↔判定隔离);eval 增 `role_specific_assets_and_isolation`,CLI **12/12 PASS**。

**已接真实数据(blueprint)**:`blueprint_overview` 访问器已接 `chief_engineer.blueprint.public.
get_blueprint_status`(按 task_id+workspace 只读寻址),经纯函数 `render_blueprint_overview` 渲染
(summary/推荐/风险),lazy-import + try/except → 失败/缺失返回 None(不注入)。实测:missing task →
None(优雅降级);present blueprint → 真实概览。仅 role==chief_engineer 且 flag 开时调用。
测试:`test_blueprint_overview_render.py`(4)。

**verdict_history → 已接真实数据(本轮在 qa cell 建了持久化+只读契约)**:原先 qa.audit_verdict
没有"按 task 读已持久化判定"的只读查询。本轮新建:
- `qa/audit_verdict/internal/verdict_persistence.py`(`VerdictPersistence`,原子写,
  `runtime/qa_verdicts/{id}.json`,镜像 BlueprintPersistence)。
- `run_qa_audit` 完成后**旁路持久化**最新判定(`_persist_qa_verdict`,try/except 兜底,绝不影响审计)。
- 公开只读查询 `get_qa_verdict(GetQaVerdictQueryV1) -> QaAuditResultV1`(`_latest_verdict_for_task`
  取最新),导出至 qa public `__init__`。
- gateway `_get_verdict_history` 调之 + 纯函数 `render_verdict_history`(判定/score/问题/建议)。
实测:persist→read→render 出真实判定概览;missing→None。qa cell 测试 + gateway 测试全绿(persist 旁路未破坏 qa 集成测试)。

**已落地(会话级 freshness 持久化 → 解架构阻断)**:gateway/ProjectionEngine 每 turn 新建会丢失
跨 turn 状态。本轮建 `role_signal_freshness.py`(`RoleSignalFreshnessCache`,**模块级单例 + 有界 LRU**,
按 task_id 记住"模型上次实际看到的每个信号 freshness")。`allocate_role_signals` 现输出
`injected_freshness`;gateway 按 task_id `get_previous_freshness` → 传入 allocator → 用真实注入的
freshness `record_injected_freshness` 回写。预算压力用便宜的早期预估(history token vs
`max_context_tokens * trigger_pct`,失败→False 安全降级)。**无压力 → budget_pressure=False → 不断流
→ 逐字节一致**;**压力 + 自上次注入未变 → 断流 nice-to-have**,把窗口让给即时工具结果。
测试:`test_role_signal_freshness.py`(缓存单元 4 + 跨 turn 熔断 3);context_gateway 25 全绿。

**已落地(ProjectionEngine 自适应学习跨 turn 持久 → 解第二处同源阻断)**:用同款模块级缓存模板,
`projection_engine.py` 新增 `_AdaptiveState`(weights + outcomes 窗口)+ `_ADAPTIVE_STATE_STORE`
(按 `learning_key` 分桶)。`ProjectionEngine(learning_key=...)` 引用共享状态(权重 adjust 原地变更、
outcomes 原地增删 → 跨 turn 存活);gateway 以 `learning_key=role_id` 构造,各角色独立累积。
默认 key 保持现有构造点行为。这让我此前修过的"内部正确但因每 turn 清零而空转"的自适应权重
**真正跨 turn 累积**(滚动窗口 ≥5 才触发 adjust,现在能跨 turn 攒够)。测试:
`test_projection_adaptive_learning.py` 增跨实例持久 + 角色隔离用例(4 全绿)。

**仍留 ADR**:① 在 turn 完成处调用 `record_outcome(success)` 把真实结果喂给学习(当前无 live 调用方),
以及评估"权重是否应影响 sort_events 排序"(目前 sequence 为主键、权重为 tiebreaker,需产品决策);
② 角色信号 default flag 仍为 False —— 待评估后逐角色 opt-in(CE→blueprint, QA→verdict);
③(可选)freshness / adaptive 缓存升级为磁盘持久化以跨进程存活。

## 7. 已知非本任务缺陷（未改）
- `cells/runtime/projection/internal/test_constants_io_helpers.py` 7 例红：与 ContextOS ProjectionEngine
  无关（是 runtime 读文件 IO helper 的 mock proxy 测试），仅因 `-k projection` 名字匹配被带出，预存。
