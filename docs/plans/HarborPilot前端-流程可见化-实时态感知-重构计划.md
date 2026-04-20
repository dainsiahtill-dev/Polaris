Polaris 前端“流程可见化 + 实时态感知”全量重构计划（一次性替换版）
摘要
本计划采用一次性重构策略，前后端联动升级实时协议，彻底替换现有前端状态链路与页面结构，不保留旧结构兼容层。目标是在所有核心页面统一展示 PM/ChiefEngineer/Director/Worker 的实时状态、阶段、任务与证据流，达到“用户随时知道系统在做什么、做到哪一步、卡在哪个节点”的可观测体验。实时目标为准实时 1s 内更新。

已锁定决策
范围：全前端重构（非仅主流程页面）。
交付：一次性替换（非灰度迁移）。
协议：前后端同步标准化。
视觉：完全重设视觉系统。
兼容：不兼容旧结构。
实时 SLA：准实时 1s 内。
允许后端同步改造：是。
现状根因（基于仓库审查）
实时数据“有源无核”：前端多个组件分别解析 phase/status/log，语义不一致。
状态映射分裂：PMWorkspace、DirectorWorkspace、FactoryWorkspace、ControlPanel、LlmRuntimeOverlay 各自维护 phase 映射，导致同一时刻显示冲突。
协议混杂：WebSocket 同时推 status/snapshot/line 与多 channel 文本流，前端二次猜测字段。
组件耦合过高：页面组件承担“协议解析 + 状态归一化 + UI 渲染”，难维护和测试。
缺少统一时序视图：没有一套跨角色/跨层级的标准时间线与任务依赖可视化主视图。
主要改造目标
建立统一实时协议（Backend Runtime Event V2）。
建立统一状态内核（Frontend Runtime Store + Selectors）。
建立统一流程可视 UI（Mission Control + 角色节点面板 + 任务依赖图 + 实时事件流）。
建立统一质量门禁（契约测试、状态机测试、UI 集成测试、E2E 回归）。
保证 1s 内可见更新与异常态可解释性（失败/阻塞/重试/依赖卡死）。
关键接口与类型变更（Public API/Schema）
后端 WebSocket（统一到 /ws，消息类型全面切换）
runtime_snapshot_v2：
{
  "type": "runtime_snapshot_v2",
  "schema_version": 2,
  "run_id": "string",
  "ts": "ISO-8601",
  "phase": "pending|intake|docs_check|architect|planning|implementation|verification|qa_gate|handover|completed|failed|blocked|cancelled",
  "roles": {
    "PM": { "state": "idle|analyzing|planning|completed|failed|blocked", "task_id": "string", "task_title": "string", "detail": "string", "updated_at": "ISO-8601" },
    "ChiefEngineer": { "state": "idle|analyzing|planning|completed|failed|blocked", "task_id": "string", "task_title": "string", "detail": "string", "updated_at": "ISO-8601" },
    "Director": { "state": "idle|executing|verification|completed|failed|blocked", "task_id": "string", "task_title": "string", "detail": "string", "updated_at": "ISO-8601" },
    "QA": { "state": "idle|executing|completed|failed|blocked", "task_id": "string", "task_title": "string", "detail": "string", "updated_at": "ISO-8601" }
  },
  "workers": [
    { "id": "string", "state": "idle|claimed|in_progress|completed|failed", "task_id": "string", "updated_at": "ISO-8601" }
  ],
  "tasks": [
    {
      "id": "string",
      "title": "string",
      "level": 1,
      "parent_id": "string|null",
      "state": "pending|ready|claimed|in_progress|completed|failed|blocked|cancelled",
      "blocked_by": ["string"],
      "progress": 0
    }
  ],
  "summary": { "total": 0, "completed": 0, "failed": 0, "blocked": 0 }
}
runtime_event_v2：
{
  "type": "runtime_event_v2",
  "schema_version": 2,
  "event_id": "string",
  "seq": 0,
  "run_id": "string",
  "ts": "ISO-8601",
  "phase": "same as snapshot",
  "role": "PM|ChiefEngineer|Director|QA|Worker",
  "node_level": 1,
  "state": "string",
  "task_id": "string|null",
  "worker_id": "string|null",
  "severity": "debug|info|warning|error",
  "message": "string",
  "detail": "string",
  "metrics": { "latency_ms": 0, "queue_depth": 0, "tokens_used": 0 }
}
删除旧前端依赖的文本行事件解析分支：snapshot/line + channel text parsing 不再作为 UI 主数据源。
前端类型
新增 RuntimeEventV2、RuntimeSnapshotV2、RuntimeRoleState、RuntimeWorkerState、RuntimeTaskNode。
删除旧的松散字段推断逻辑（task.status || task.state、多处 phase fallback）。
所有 UI 只消费 Runtime Store 的 selector 输出，不直接解析 WS 原始 payload。
代码结构重组（明确到目录）
新建 src/frontend/src/runtime/：
v2.ts：V2 类型与校验器。
runtimeSocket.ts：WebSocket 接入与重连。
runtimeStore.ts：统一状态仓库（event apply/reducer）。
selectors/：按视图输出派生数据。
adapters/：仅保留 V2 协议适配。
新建 src/frontend/src/app/mission-control/：
MissionControlPage.tsx
FlowStageRail.tsx
RoleNodeGrid.tsx
TaskHierarchyPanel.tsx
WorkerMatrix.tsx
LiveEventTimeline.tsx
RunHealthPanel.tsx
替换现有页面装配逻辑：
App.tsx
useWebSocket.ts
PMWorkspace.tsx
DirectorWorkspace.tsx
FactoryWorkspace.tsx
ProjectProgressPanel.tsx
RealtimeActivityPanel.tsx
LlmRuntimeOverlay.tsx
ControlPanel.tsx
后端联动改造点
在 websocket.py 增加 V2 snapshot/event 生产器，并将旧 payload 标记为移除。
在 polaris_engine.py 统一输出 PM/ChiefEngineer/Director/QA/Worker 状态枚举，确保前端可直接消费。
在 artifacts.py 输出 snapshot 补全字段（tasks level/parent/blocked_by/workers）。
统一 phase/state 枚举来源为 factory.py 的 RunPhase，禁止多处自定义字符串。
所有 JSON/日志输出继续显式 UTF-8。
实施阶段（一次性替换的执行顺序）
Phase A（协议冻结）：冻结 Runtime Event V2 字段，输出 JSON Schema，后端和前端签署同一协议。
Phase B（后端产流）：WS 新增 runtime_snapshot_v2/runtime_event_v2，并在事件内补齐 role/task/worker/phase。
Phase C（前端状态内核）：落地 Runtime Store、Reducer、Selector，替换现有 useWebSocket 直连解析。
Phase D（新 UI 主壳）：落地 Mission Control 主页面与 6 大核心面板（阶段/角色/任务/worker/事件/健康）。
Phase E（页面接管）：PM/Director/Factory 页面改为共享同一 Runtime Store 输出，不再各自映射 phase。
Phase F（视觉系统重设）：统一 token、排版层级、状态色语义、动画节奏与响应式断点。
Phase G（清理旧代码）：删除旧协议解析、重复 status 映射、过时组件入口和 dead code。
Phase H（全量验证）：执行单元测试、组件测试、E2E、性能与实时 SLA 验证。
Phase I（一次性切换）：主分支合并后全量启用新协议和新页面，旧逻辑不保留。
视觉与交互规范（重设版）
采用“作战指挥台”信息架构：顶部运行态、左侧阶段轨道、中区角色与任务联动、右侧事件时间线。
状态颜色语义固定：running=cyan、completed=emerald、blocked=amber、failed=red、idle=slate。
所有节点必须展示：当前阶段、当前任务、最近更新时间、最近事件摘要。
动效规则：仅保留“活动脉冲 + 状态过渡 + 时间线插入”，禁用无意义动效。
移动端策略：关键信息折叠为“阶段 + 当前任务 + 最新异常 + 一键展开详情”。
测试计划与验收场景
协议契约测试：后端发出的每条 runtime_event_v2/runtime_snapshot_v2 必须通过 schema 校验。
Reducer 状态机测试：事件乱序、重复、丢包恢复（seq gap）后状态仍一致。
组件渲染测试：各角色节点在 idle/running/blocked/failed/completed 下 UI 正确。
跨页面一致性测试：同一时刻 MissionControl/PM/Director/Factory 展示同一 phase/task/state。
依赖阻塞场景：blocked_by 链路显示正确，阻塞来源可追溯到具体 task。
Worker 生命周期场景：CLAIMED -> IN_PROGRESS -> COMPLETED/FAILED 可视化正确。
故障场景：WS 断连重连、后端重启、事件突发高峰时 UI 不假死。
性能场景：200 events/s 连续 5 分钟，前端无明显卡顿，内存增长可控。
E2E 全链路：PM 规划 -> CE 蓝图 -> Director 执行 -> QA 门禁，全程可视并可追踪。
SLA 验收：事件产生到 UI 呈现中位延迟 < 1000ms，P95 < 1500ms。
发布与回滚
发布前门禁：npm run typecheck、npm run lint、npm run test、npm run test:e2e 全绿。
切换策略：一次性替换 App 主入口到新 Mission Control 路由。
回滚策略：保留切换前 tag（如 ui-realtime-v1-final），出现 P0 问题直接回滚到该 tag。
监控指标：WS 连通率、事件延迟、渲染帧耗时、前端错误率、关键流程完成率。
风险与控制
风险：一次性替换范围大。控制：强契约测试 + E2E 基线 + 明确回滚 tag。
风险：后端字段遗漏导致前端空态。控制：snapshot 启动时强校验，缺字段直接告警并阻断上线。
风险：实时高频导致渲染抖动。控制：store 层节流、选择器 memo、时间线虚拟列表。
风险：视觉重设影响可读性。控制：先定义状态语义与信息层级，再做风格实现。
假设与默认值
默认语言为中文展示。
不保留旧协议兼容层（按你的决策执行）。
允许后端实时接口同步改造。
实时目标按“1s 内准实时”验收。
所有新增文本文件与日志读写显式 UTF-8。