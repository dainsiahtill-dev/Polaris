# ContextOS 实时视图 UI 降噪重构设计

**Date:** 2026-06-18  
**Scope:** Frontend-only refactor of `src/frontend/src/app/components/contextos/`.  
**Goal:** 减少视觉噪音，保留并强化高价值可观测信息，提升实时视图的可用性。

## Background

`ContextOSWorkspace` 已在 `2026-06-18-contextos-realtime-roles-design.md` 中完成了“为 5 个主角色各提供内部 ContextOS 状态面板”的能力落地。当前视图功能完整，但信息密度过高、重复元素多、视觉层级混乱，用户在实时监控时难以快速抓住关键信号。

本设计在保留全部高价值实时数据的前提下，对信息架构和视觉呈现进行重构。

## Current Noise Audit

基于对 `ContextOSWorkspace.tsx`、`contextOSData.ts`、`contextOSTelemetry.ts` 及现有测试的代码审计，识别出以下主要噪音源：

1. **BenchStatusStrip 污染每个 SectionCard**  
   `SectionCard` 内注入 `<BenchStatusStrip />`，使工厂 bench 进度条出现在 ContextOS 的每一张卡片顶部。Bench 状态与上下文操作系统无关，且 bench 未运行时也会渲染一条空/空闲条。

2. **Header 徽章过载**  
   顶部同时存在：阶段、PM 迭代轮次、质量门、实时活动芯片、token 芯片、遥测新鲜度徽章、WS 状态徽章。信息互相重叠，同一指标在 10cm 内出现多次。

3. **指标重复**  
   - token 总量：header 芯片、右侧“上下文预算”大标题、footer 三处。
   - 调用次数：header 活动芯片、footer、决策流徽章三处。
   - 时延：header 活动芯片、footer 两处。

4. **左侧“组件健康”面板与中央管线高度重叠**  
   7 张组件健康卡（TruthLog / WorkingMem / ProjectionEngine / LLM 角色门 / 平均消耗 / Projection.project / Receipt · Telemetry）的指标大多可直接从中央 8 段管线读出。两者并存造成“同一系统两张地图”。

5. **角色信号面占用过大**  
   5 个 hex 卡片 + 展开后的 `RoleInternalPanel`（4 段 mini-pipeline、6 个统计卡、事件列表、解释性段落）占据大量垂直空间，且部分统计（如按角色 calls / prompt / completion）与顶部汇总重复。

6. **Footer Outcome Feedback Loop**  
   底部又增加一条政策标签 + 重复指标（提示 / 输出 / 时延 / LIVE 徽章）的横条，与 header 完全冗余。

7. **中英混杂标签**  
   “Context Budget”、“RoleSignalPlane”、“Outcome Feedback Loop”、“Decision Log / Receipts” 等标签中英混杂，增加认知负荷。

8. **过度装饰性样式**  
   大量 `bg-black/30`、ring、shadow、多段渐变边框使卡片看起来都很“重”，没有清晰的主次关系。

## High-Value Signals to Preserve

以下信号是用户真正需要实时监控的，必须保留并使其更容易被扫描：

- **运行状态**：是否有角色在运行 / 系统是否 LIVE / 遥测是否新鲜。
- **资源消耗**：token 总量、提示/输出拆分、单次平均、上下文窗口占用。
- **调用健康**：调用次数、最近时延、错误数。
- **系统管线**：8 段 ContextOS 装配流程的当前活跃段。
- **角色状态**：5 主角色的活动/阻塞/空闲状态，以及选中后的内部事件流。
- **决策/事件流**：最近发生的真实观测事件或回执。
- **异常信号**：LLM 受阻角色、错误事件、窗口占用过高。

## Recommended Approach

**方案 B：信息架构重构。**

不删除功能，而是重新划分信息层级：
- 第 1 层：单一、克制的顶部状态栏（运行状态 + 资源 + 连接健康）。
- 第 2 层：简化的中央管线图（保留作为系统地图）。
- 第 3 层：两栏主体——左侧角色与上下文详情，右侧预算、事件分布与决策流。

该方案去掉重复面板和低信号装饰，同时保留所有高价值数据。

## Design Detail

### 1. Header 状态栏（单行合并）

当前 header 的 7 个独立元素合并为 4 个高信息密度组：

```
[返回] [ContextOS 实时视图 · workspace]   [阶段 badge]   [调用 · token · 时延 chip]   [遥测新鲜度 + WS chip]   [上下文结构 toggle] [刷新]
```

- **阶段 badge**：保留，合并运行/阻塞/空闲三色点 + 阶段名。
- **调用 · token · 时延 chip**：把当前分散的“实时活动 chip”和“token chip”合并为一个资源芯片。例如 `1,240 调用 · 45k tok · 320ms`。无 token 时显示 `等待首次 LLM 调用`。
- **遥测新鲜度 + WS chip**：合并 WS LIVE/RECONNECT/OFFLINE 与“实时遥测 · 刚刚”。用一个 badge 表达连接与数据新鲜度。
- **删除**：独立的“迭代”徽章（信息仍在角色详情/上下文结构中可查看）、独立的质量门 badge（qualityGate 状态保留为更小的图标或在工具提示中显示）、重复的 token chip。

### 2. 中央管线图（保留并简化）

保留 8 段水平管线作为系统地图，但降低视觉重量：

- 每个 `PipelineNode` 只显示两行：标签 + metric，去掉 `hint` 子标题（改为 title/tooltip）。
- 去掉管线卡片底部的 explanatory footer（“投影排序(含预算规划) → 角色信号 → …”），这些文字是静态说明，不属于实时数据。
- 空闲时保留半透明水印，但减少装饰性 ring/shadow。
- Receipt 反馈闭环节点与管线保持同一视觉语言。

### 3. 主体两栏布局

#### 左栏：角色与上下文（占主要宽度）

**角色卡片区**
- 5 角色卡从 hex 网格改为紧凑的水平行或 5 列小网格。
- 每张卡显示：状态点、官职首字、角色名、token/事件数。
- 选中状态用 subtle ring 而非高对比色块表示。

**角色内部面板（RoleInternalPanel）**
- 4 段 mini-pipeline 改为横向紧凑条，去掉箭头之间的重度边框。
- 6 个统计卡合并为 3 个高信号组：
  - **活动**：事件数 / 投影数 / 回执数
  - **调用**：调用数 / 最近时延
  - **Token**：提示 / 输出
- 事件列表保留，但：
  - 去掉重复的时间戳列（用相对时间或 kind 列即可）。
  - token / 时延 / 快照徽章合并为一行，避免每个事件 3 行元数据。
- 面板顶部不再显示大段解释文字；需要说明时使用 `title` 属性或折叠提示。

**上下文结构（ContextStructurePanel）**
- 默认隐藏，仅通过 header 的“上下文结构”toggle 打开。
- 打开后以紧凑网格展示 TruthLog / WorkingMem / ProjectionEngine / ReceiptStore 4 个核心指标 + 角色上下文窗口 + 最近结构事件。

#### 右栏：资源与事件流（较窄）

**上下文预算卡**
- 保留 token 大标题、提示/输出进度条、上下文窗口占用条。
- 删除“按模式分布”卡（与事件类型分布及 budget 拆分重复）。

**事件类型分布卡**
- 保留，因为它是真实观测数据的分类视图。

**决策/回执流卡**
- 保留，但简化每行：
  - 合并 actor + kind 为单一色调标签。
  - token / 时延 / 快照徽章只保留最重要的 1-2 个（kind + token）。
  - 空状态保持友好提示。

### 4. 删除的元素

- `SectionCard` 中的 `<BenchStatusStrip />`（BenchStatusStrip 组件本身保留，仅不在 ContextOS 使用）。
- 左侧“组件健康”整个面板。
- Footer “Outcome Feedback Loop” 条。
- “按模式分布”卡。
- 管线图底部的静态说明文字。
- 角色面板内的解释性段落。

### 5. 视觉统一

- 标签统一为中文为主，括号英文为辅，例如：
  - “上下文预算 (Context Budget)”
  - “角色信号面 (RoleSignalPlane)”
  - “决策/回执流 (Decision Log)”
- 统一使用更克制的边框和背景：
  - 减少 `bg-black/30` 的使用；卡片用 `bg-bg-panel/40` 或更透明背景。
  - 状态色只用于真正需要吸引注意的元素（错误、活跃脉冲）。
  - 减少 ring/shadow 装饰，依靠间距和字体层级建立层次。
- 字体层级：标题 `text-xs font-semibold`，指标 `font-mono text-sm`，辅助说明 `text-[10px] text-text-dim`。

## Data Model Changes

`contextOSData.ts` 需要少量调整以支持更紧凑的视图：

1. 在 `ContextOSModel` 中保留所有现有字段（避免破坏测试和父组件）。
2. 新增或调整派生：
   - `budget` 保持 `[prompt, completion]` 两张切片。
   - `byModeSlices` 保留计算，但 UI 默认不再渲染；需要时可加回调试开关。
   - `components` 保留计算，但 UI 默认不再渲染；作为潜在的隐藏诊断数据。
   - `policies` 保留，但移到 header tooltip 或上下文结构中，不再作为 footer 标签行。
3. 不删除任何接口字段，保证向后兼容和测试稳定性。

## Testing Strategy

1. **单元测试**：更新 `ContextOSWorkspace.test.tsx` 中断言：
   - 移除“7 张组件健康卡”断言（删除该面板）。
   - 调整 header 断言：合并后的资源 chip 应同时包含调用数、token、时延。
   - 保留 8 段管线、5 角色卡、角色内部面板、上下文结构面板的测试。
   - 新增断言：BenchStatusStrip 不应在 ContextOS 卡片内渲染。

2. **类型检查**：`npm run typecheck` 必须全绿。

3. **Lint**：`npm run lint` 无新增 warning。

4. **Playwright 视觉审计**：
   - 访问 `http://127.0.0.1:5173/`。
   - 导航到 ContextOS 实时视图。
   - 捕获空状态、运行中状态、选中角色状态三张截图。
   - 人工/启发式检查：无重复指标条、无无关 bench strip、视觉焦点清晰。

## 新增需求： per-LLM / per-worker 真实上下文查看器

> **状态**：6 专家并行研讨后已定案。详细实施计划见 `docs/superpowers/plans/2026-06-18-per-llm-context-viewer-plan.md`。

### 定案方案：C —— 摘要 + hash 按需拉取

**核心原则**：
- 事件流只增体积小、无敏感的摘要/引用字段（`call_id`、`turn_id`、`prompt_hash`、`context_snapshot_ref`、消息数、token 数）。
- 完整上下文（压缩后的 `effective_chat_messages`）按 hash 写入磁盘（`runtime/contexts/<shard>/<hash>`）。
- 前端点击“查看完整上下文”时，通过 `GET /v2/context/{hash}` 按需拉取并结构化展示。

**为何不选 A/B**：
- **A（事件内联完整上下文）**：大 prompt 会撑爆 WebSocket/JSONL，且把可能含敏感代码/密钥的内容广播给所有连接客户端，违反 F14 教训。
- **B（ContentStore + hash）**：现有 `ContentStore` 是内存-only、50MB 上限，不可直接复用；需要新建磁盘存储子系统。
- **C（摘要 + hash）**：在现有 `emit_llm_event` 自由 `data` 字典上纯增量添加，无需改 bridge/WS 协议；事件体积小；安全性好。

### 关键后端发现

- `LLMInvoker.call()` 已能拿到 `messages` 列表和 `input_text`。
- `AIExecutor.invoke()` 在调用 provider 前会执行 `compress_chat_messages_to_budget()`，生成**最终真正发送**的 `effective_chat_messages`，但当前只把 SHA256 写入 `final_request_receipt_sink`，未进入事件流。
- `emit_llm_event` 的 `data` 字典是自由格式，bridge 会原样透传到前端 `parseLlmStreamLine`。
- 现有 `ContentStore` 不能持久化；需新建基于 `StorageLayout` 的磁盘 hash 存储。

### 后端改动

1. **`src/backend/polaris/kernelone/llm/engine/executor.py`**
   - 在 `_record_final_request_receipt()` 后，把 post-compression `effective_chat_messages` 序列化并按 SHA-256 hash 写入 `runtime/contexts/<hash[:2]>/<hash>`。
   - 将 `context_snapshot_ref` 注入 `request.context`，供上游事件发射使用。
   - 把 `context_snapshot_ref` 同时加入 receipt payload，保证 receipt sink 与事件流一致。

2. **`src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker.py`**
   - 在 `_emit_call_end_event` 的 metadata 中带上 `request.context['context_snapshot_ref']`。

3. **新增 `src/backend/polaris/delivery/http/routers/context.py`**
   - `GET /v2/context/{hash}`：按 hash 读取 `runtime/contexts/` 下的存储文件，校验 workspace 归属，返回 JSON。
   - 复用 `require_auth` 做认证；先做 workspace 级别访问控制。

### 前端改动

1. **`src/frontend/src/app/hooks/useRuntime.ts`**
   - 在 `parseLlmStreamLine` 中从 `eventData` 提取 `context_snapshot_ref`、`prompt_hash`、`turn_id`，注入 `LogEntry.meta`。

2. **`src/frontend/src/app/components/contextos/contextOSTelemetry.ts`**
   - `ContextOSEvent` 增加 `contextSnapshotRef`、`promptHash`、`turnId`。
   - `logEntryToEvent` 从 `meta` 映射这些字段。

3. **`src/frontend/src/app/components/contextos/contextOSData.ts`**
   - `RoleInternalContext` 增加 `latestContextSnapshotRef`、`latestCallId`、`latestTurnId`。
   - 从该角色最近带 hash 的事件中取值。

4. **新增 `src/frontend/src/app/components/contextos/ContextViewerModal.tsx`**
   - 只读弹窗，点击角色面板中的“查看完整上下文”后按需 fetch `/v2/context/{hash}`。
   - 结构化展示：system prompt、messages、tool definitions、projected items、request params。
   - 无 hash 时显示占位提示。

### UI/UX 设计

- **角色内部面板 → 最近调用列表**：在现有“最近事件”上方增加一行“最近 LLM 调用”，每个条目显示 `call_id` 前缀、模型、token 数、时延、`context_snapshot_ref` 是否存在。
- **点击条目 → 打开 ContextViewerModal**：默认展示消息结构；支持折叠/展开各 role 的消息段；大内容懒加载/虚拟滚动（二期）。
- **无上下文采集时**：条目显示“完整上下文未采集（需后端开启）”，不报错。

### 安全与性能

- **F14 教训**：脱敏只在 `emit_llm_event` 发射边界进行，不得污染执行路径的 `response.raw`。
- **事件流只传 hash**：不广播完整 prompt。
- **存储**：`runtime/contexts/` 位于 volatile 层，默认 7 天 TTL；二期加 100MB/workspace 上限和主动清理。
- **Auth**：新端点先做 workspace-scoped；二期可按 role 细粒度过滤。

### 实施阶段

**Phase 1（MVP）**：后端 hash 写入 + 事件透传 + 新 API + 前端字段映射 + 基础 ContextViewerModal。
**Phase 2（增强）**：丰富上下文结构化展示、TTL/容量清理、role 级 ACL、运行中状态截图。

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| 磁盘增长 | runtime 层 volatile + 7 天 TTL + 100MB cap |
| hash 碰撞 | `sha256[:24]` + 写入时 collision check；文件内容保留完整 64 位 hash |
| receipt sink 与事件流不一致 | 同一 `context_snapshot_ref` 同时写入 request.context 和 receipt payload |
| 前端拉取延迟 | 仅按需 fetch，列表页只显示摘要 |

## Out of Scope

- 新增角色。
- 深色/浅色主题切换（沿用当前主题系统）。
- Phase 2 的 retention UI、role 级 ACL、复杂上下文搜索。

## Verification Checklist

- [ ] Header 从 7 个元素减至 4 个以内。
- [ ] 组件健康面板不再渲染。
- [ ] Footer Outcome Feedback Loop 不再渲染。
- [ ] SectionCard 内无 BenchStatusStrip。
- [ ] 8 段管线保留且可识别。
- [ ] 5 角色卡保留，选中可展开内部面板。
- [ ] 上下文预算、事件类型分布、决策流保留。
- [ ] `npm run typecheck` 通过。
- [ ] `npm run lint` 通过。
- [ ] `npm run test -- src/frontend/src/app/components/contextos` 通过。
- [ ] Playwright 截图审计完成。
