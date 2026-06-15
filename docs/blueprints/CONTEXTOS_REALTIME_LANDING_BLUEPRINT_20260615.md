# ContextOS 实时看板 — 全量落地蓝图（2026-06-15）

## 背景与根因

ContextOS 实时看板"在运行却不更新"的根因已定位并修复（见
`useRuntime` WS 实时框架接线 + `channel_utils.resolve_channel_path` 的 `runtime_events`
按 run 隔离解析 + `parseLlmStreamLine` 识别 journal `llm` 通道真实词汇）。剩余两处缺口：

1. **`items_count` / 投影在非 PM 角色轮次不出现**：真实角色轮次的上下文装配走
   `RoleContextGateway.build_context`，它用自己的 `ProjectionEngine`，**不发**
   `context.build` 观测事件（只有 PM **规划**管线发 `prompt_context`）。故 Director/CE/QA
   轮次没有投影/在窗项数信号。
2. **全局 token HUD 恒空**：`useUsageStats` 轮询幽灵文件
   `runtime/events/llm.observations.jsonl`（后端零写入），故 HUD token 恒为空。

## 方案

### (a) RoleContextGateway 发 `context.build`（镜像 ContextEngine）

- **文本架构图**
  ```
  orchestration(run_events) ──> 角色轮次(RoleTurnRequest.run_id)
        └─ ToolLoopController.build_context_request() ──(run_id/mode 注入)──> ContextRequest
              └─ RoleContextGateway._build_context_impl()
                    ├─ 既有: ProjectionEngine.project → active_window_size / token_estimate
                    └─ 新增: emit_event(context.build, output={items_count, total_tokens,...})
                             → runs/<run_id>/events/runtime.events.jsonl
                             → WS runtime_events 通道 → 看板「投影 / 在窗项数」
  ```
- **职责**：网关是角色上下文装配组件，发自身装配观测与 `ContextEngine._emit_context_events`
  对齐（同 `name="context.build"`、同 `output` 结构）。
- **events_path 解析（fail-safe）**：网关用 `request.events_path`（若已注入）；否则用
  `resolve_run_dir(workspace, "", run_id)`（kernelone 单一规范解析器，与 orchestration 同源）
  推导 `runs/<run_id>/events/runtime.events.jsonl`。**仅当 run 目录已存在**（orchestration 已创建）
  才发——避免重蹈"写到错误/幽灵文件"覆辙（fail-closed，解析偏差则跳过不写）。
- **数据**：`items_count = active_window_size`（投影活动窗口真实项数）、
  `total_tokens = token_estimate`（装配后真实 token）。角色轮次不落盘快照文件，故**不发**
  `context.snapshot`（诚实：无回执就不伪造）。
- **技术理由**：复用既有 `emit_event` 效果通道（可审计）；不引入新 schema；不做深层 events_path
  穿线（用 workspace+run_id 自解析 + 存在性守卫，最小改面）。

### (b) useUsageStats 改由 WS 实时流派生

- 删除幽灵文件轮询；改为对 `llmStreamEvents`（journal `llm` 通道，含真实 raw.data usage）
  做纯聚合：`totals`/`calls`/`by_mode`（按 role）。push 驱动，无轮询、无文件。
- `refresh` 退化为 no-op，`loading=false`/`error=null` 保持返回形状兼容；App.tsx 传
  `llmStreamEvents` 入参。

## 影响面与边界

- 跨 Cell：网关（roles.kernel）→ kernelone（emit_event / resolve_run_dir），方向合规。
- Effect：新增一次 `emit_event` 文件追加（context.build），已 fail-safe 守卫。
- 不改契约 schema；不改 state ownership；不触 Descriptor / Semantic Index。

## 验证

- 后端：`ruff` / `mypy` / `pytest`（网关 + 控制器单测：events_path 解析、存在性守卫、emit 内容）。
- 前端：`typecheck` / `lint` / `test`（useUsageStats 聚合单测替换 path 测）。
- E2E：真实 per-run 形态播种 + 看板呈现（已有 contextos-realtime-audit spec 扩展）。
