# Context OS + Cognitive Runtime 原理与运行流程说明

- Status: Active (实现与蓝图对齐文档)
- Date: 2026-03-26
- Scope: `src/backend/polaris/**`
- Audience: 平台架构、角色运行时、工具链与治理链路维护者

---

## 1. 一句话结论

`Context OS` 负责“把什么上下文送进模型”（working memory runtime），`Cognitive Runtime` 负责“谁有权这么送、如何校验、如何交接、如何留证”（authority/runtime governance）。

两者是上下层关系，不是替代关系。

---

## 2. 为什么要拆成两层

传统 Agent 常见问题：

1. 把 transcript 历史和 prompt 工作集混在一起，越聊越重，越重越乱。
2. 一到 token 压力就整段摘要，导致事实丢失与回放不可逆。
3. 工具结果、文件大块内容直接塞历史，造成“噪声主导上下文”。
4. 执行链有动作没证据，后续无法回答“谁批准了什么、为什么过了写门禁”。

分层后的职责：

1. `Context OS` 解决“可持续上下文装配”。
2. `Cognitive Runtime` 解决“跨角色治理、scope/校验/receipt/handoff”。

---

## 3. 体系位置（含 TurnEngine）

```text
User / Workflow / CLI / API
  -> TurnEngine (统一循环与工具执行)
    -> context.gateway + context.engine
      -> SessionContinuityEngine (兼容门面)
        -> KernelOne State-First Context OS (上下文操作系统)
          -> 产出 run_card / context_slice_plan / prompt-facing continuity block
    -> LLM + Tool Loop
    -> roles.session (会话真相)

Cross-cutting:
Cognitive Runtime (resolve_context / lease_edit_scope / validate_change_set /
                   record_runtime_receipt / export_handoff_pack)
  -> 复用 roles.session + context.memory + write_gate + impact_analyzer
  -> SQLite receipt/handoff 持久化
```

---

## 4. Context OS 的作用原理

实现主入口：

- `polaris/kernelone/context/context_os/runtime.py`
- `StateFirstContextOS.project(...)`

### 4.1 State-First 核心思想

1. 保留不可变事实（transcript log），不直接覆盖真相。
2. 对 prompt 只构建“工作视图”（head/tail anchor、active window、artifact stubs、episode cards）。
3. 压缩只作用于“闭环历史的派生层”，不覆盖原始事件语义。

### 4.2 事件四路分流（RoutingClass）

模型在摄取前先做路由：

1. `clear`：低信号噪声，清理或降权。
2. `patch`：状态补丁，进入 working state。
3. `archive`：大 payload 归档为 artifact。
4. `summarize`：可封装为 episode 的叙事片段。

### 4.3 一等对象（First-Class Objects）

定义位于 `polaris/kernelone/context/context_os/models.py`：

1. `TranscriptEvent`：不可变事件
2. `ArtifactRecord`：外置大块内容（带 `restore_tool`）
3. `StateEntry`：带 `supersedes` 的版本化状态
4. `DecisionEntry`：决策账本
5. `EpisodeCard`：闭环记忆单元（64/256/1k 视图）
6. `RunCard`：当前工作记忆最小卡片
7. `ContextSlicePlan`：一次 prompt 组装“纳入/排除”计划
8. `BudgetPlan`：预算与压力控制

### 4.4 预算控制与活跃窗口

`StateFirstContextOS` 内部关键步骤：

1. `_plan_budget(...)` 计算 `soft/hard/emergency` 压力区间
2. `_collect_active_window(...)` 以活跃性而非“最近N轮”选择窗口
3. `_seal_closed_episodes(...)` 在闭环时封存 episode
4. `_build_run_card(...)` 与 `_build_context_slice_plan(...)` 生成 prompt-facing 控制面

### 4.5 记忆读取接口

Context OS 原生提供：

1. `search_memory(...)`
2. `read_artifact(...)`
3. `read_episode(...)`
4. `get_state(...)`

`roles.session` 通过 `RoleSessionContextMemoryService` 将这些能力暴露为 session 级查询。

---

## 5. Cognitive Runtime 的作用原理

实现主入口：

- `polaris/application/cognitive_runtime/service.py`
- `polaris/cells/factory/cognitive_runtime/public/service.py`

### 5.1 定位

`Cognitive Runtime` 是横向 authority facade，不是第二套 Context OS，也不是第二套 session truth。

它负责：

1. `resolve_context`
2. `lease_edit_scope`
3. `validate_change_set`
4. `record_runtime_receipt`
5. `export_handoff_pack`

### 5.2 复用链路（不重复造轮子）

1. 会话真相读取：`RoleSessionService`
2. 上下文记忆读取：`RoleSessionContextMemoryService`
3. 上下文装配：`context.engine`（通过 `get_anthropomorphic_context_v2`）
4. 写门禁：`WriteGate`
5. 影响分析：`ImpactAnalyzer`

### 5.3 持久化（SQLite）

实现：`polaris/infrastructure/cognitive_runtime/sqlite_store.py`

默认数据库：

- `runtime/cognitive_runtime/cognitive_runtime.sqlite`

表：

1. `cognitive_runtime_receipts`
2. `cognitive_runtime_handoffs`

存储的是 runtime 证据与交接包，不是业务主真相。

---

## 6. 端到端运行流程（一次完整 turn）

### Phase A：输入与上下文准备

1. TurnEngine 收到用户输入（`run` / `run_stream`）。
2. `context.gateway` 组装上下文请求。
3. `SessionContinuityEngine` 调用 Context OS 投影，生成：
   - continuity prompt block
   - run_card
   - context_slice_plan
4. 上述信息写回 `roles.session.context_config["state_first_context_os"]`（派生快照，用于 continuity/debug，不是会话 source-of-truth）。

### Phase B：模型执行与工具循环

1. TurnEngine 调用 LLM。
2. 解析 assistant 文本、thinking、tool_calls。
3. 工具执行进入 Tool Loop（含预算与策略层）。
4. 工具结果再入 transcript，并触发下一轮上下文投影。

### Phase C：治理与证据

1. 如进入治理流程，由 Cognitive Runtime 发放 scope lease。
2. 变更后执行 `validate_change_set`。
3. 关键动作写入 runtime receipt（SQLite）。
4. 需要跨 session/角色交接时生成 handoff pack。

---

## 7. 持久化产物与边界

### 7.1 roles.session（会话真相）

1. Conversation / Message（DB）
2. `context_config.state_first_context_os`（Context OS 派生快照，非原始会话真相）

### 7.2 Context OS（working memory 产物）

1. transcript_log（投影内可回放）
2. working_state / decision_log / run_card
3. artifact_store / episode_store / context_slice_plan

### 7.3 Cognitive Runtime（治理证据）

1. runtime receipts（操作证据）
2. handoff packs（交接资产）

### 7.4 真相归属不变量（必须满足）

1. `roles.session` 的 Conversation / Message 是原始会话真相。
2. `state_first_context_os` 仅是可重建的 working-memory 投影快照，不能被当成原始真相回写入口。
3. `Context OS` 产物属于 prompt/runtime projection，不拥有业务状态写权限。
4. `Cognitive Runtime` receipt/handoff 属于治理证据，不参与会话真相裁决。

---

## 8. “绝对不能直连”执行规则

适用于 Context OS / Cognitive Runtime / roles.session 持久化链路：

1. 不允许直接拼接 `.polaris/...` 或 `Path.home()` 进行存储写入。
2. 必须走 KernelOne storage/db/fs 边界解析（logical path + policy）。
3. 运行时优先 `runtime/*`，必要时可退到 `workspace/runtime/*`，但仍必须经 KernelOne 解析。
4. 任何绕过 KernelOne 的直连路径都视为架构违规。

---

## 9. 与 TurnEngine 的关系

TurnEngine 是执行循环核心；Context OS 与 Cognitive Runtime是其“上下文与治理外挂层”：

1. TurnEngine 负责模型调用、工具循环、停止条件。
2. Context OS 负责 prompt 侧工作记忆投影与预算装配。
3. Cognitive Runtime 负责 lease/validate/receipt/handoff 的治理闭环。

结论：TurnEngine 不应内嵌第二套上下文系统或第二套治理系统。

---

## 10. 与传统 Agent 的差异（核心优势）

1. 不再把长对话当“线性文本”，而是对象化状态系统（State/Artifact/Episode/RunCard）。
2. 压缩是可逆的（artifact/episode 可读回），不是“一次性摘要替换”。
3. 运行时可审计（receipt/handoff 可追溯）。
4. 分层职责清晰，便于多角色协作扩展（Coder/Writer/Director 等）。

---

## 11. 当前已落地与仍在推进

已落地：

1. Context OS 核心 runtime 与对象模型
2. SessionContinuity 对 Context OS 的兼容门面
3. Cognitive Runtime Phase-1 + Phase-2（含 map_diff/projection/promote_or_reject/rollback）+ SQLite 持久化
4. TurnEngine 与上下文链路的主干接入
5. 可开关运行（默认开启）：
   - `POLARIS_CONTEXT_OS_ENABLED=true|false`
   - `POLARIS_COGNITIVE_RUNTIME_MODE=off|shadow|mainline`
6. `context.engine` 在 `session_id` 存在时自动补全 session continuity/context_os 覆盖（无显式 override 也可接入）

仍在推进：

1. 更强的跨域 adapter 体系（非 code domain）
2. `mainline` 阻断式治理策略的分批灰度
3. 全链路评测基线（context quality + continuity + governance metrics）

---

## 12. 快速排障建议

1. 先看 `roles.session` 中 `state_first_context_os` 是否更新。
2. 再看 `run_card` / `context_slice_plan` 是否反映当前目标与 open loops。
3. 若治理链路异常，检查 Cognitive Runtime receipt 是否落库。
4. 若出现“上下文漂移”，优先检查 active window 与 budget pressure，而不是先调 prompt 文案。

---

## 14. 外部审计建议收口（2026-03-27）

下面是已采纳为后续硬化任务的高优先级建议：

1. 防止多账本真相分裂：把 transcript truth / projection snapshot / governance evidence 的职责边界做成代码级 invariant。
2. 强化四路分流可靠性：采用“确定性规则优先 + 低置信度升级分类”策略，避免每轮都走 LLM 路由。
3. 提供可逆运行保证：Artifact/Episode 必须带 provenance 和 restore path，支持 reclassify/reopen。
4. 引入 turn 事务包络：统一串联 routing -> projection -> lease -> validate -> receipt，避免半提交状态。
5. 防止 working_state 退化：固定 schema + `supersedes` 版本规则 + 冲突合并策略。
6. SQLite 定位为当前实现：短期通过单写队列/WAL 稳定并发，中长期保持可迁移性，不写死为唯一后端。

---

## 13. 读图顺序（建议）

1. `docs/KERNELONE_STATE_FIRST_CONTEXT_OS_BLUEPRINT_2026-03-26.md`
2. `docs/KERNELONE_STATE_FIRST_CONTEXT_OS_IMPLEMENTATION_MASTER_PLAN_2026-03-26.md`
3. `docs/KERNELONE_STATE_FIRST_CONTEXT_OS_PHASE1_EXECUTION_BLUEPRINT_2026-03-26.md`
4. `docs/cognitive_runtime_architecture.md`
5. 本文（运行流程与作用原理总览）
