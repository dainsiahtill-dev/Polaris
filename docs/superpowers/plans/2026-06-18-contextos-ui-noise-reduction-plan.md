# ContextOS 实时视图 UI 降噪重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 `src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx` 及其相关文件，减少视觉噪音并保留高价值可观测信息。

**Architecture:** 在现有 ContextOS 数据模型 (`contextOSData.ts`) 和遥测层 (`contextOSTelemetry.ts`) 之上，仅修改前端呈现层。通过合并 Header 状态元素、删除冗余面板（组件健康 / footer / 按模式分布）、简化 SectionCard 和角色内部面板，构建更清晰的三层信息架构：Header → Pipeline → 两栏主体。

**Tech Stack:** React + TypeScript (strict), Tailwind CSS, Vitest + React Testing Library, Playwright.

## Global Constraints

- TypeScript 必须保持 `strict`；公共接口禁止 `any`。
- 不修改后端接口、事件类型或 WebSocket 数据流。
- `contextOSData.ts` 中的接口和 `ContextOSModel` 字段保留，不得删除（可新增但不可逆删）。
- 所有高价值信号（运行状态、token 总量、调用次数/时延、错误、角色状态、决策流、窗口占用）必须继续可观测。
- 现有测试 `ContextOSWorkspace.test.tsx`、`contextOSData.test.ts`、`contextOSTelemetry.test.ts` 需要更新断言以适应新 UI，但不得删除测试覆盖。
- `BenchStatusStrip` 组件本身保留，仅禁止在 `ContextOSWorkspace` 的 `SectionCard` 中使用。
- 提交前必须运行并通过：`npm run typecheck`、`npm run lint`、`npm run test -- src/frontend/src/app/components/contextos`。
- 所有文本文件必须使用 UTF-8。

---

## File Structure

| 文件 | 职责 | 变更类型 |
|---|---|---|
| `src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx` | 主视图组件：重构 Header、管线、两栏布局、删除冗余区块 | 修改 |
| `src/frontend/src/app/components/contextos/contextOSData.ts` | 数据派生层：保留所有字段，可能新增辅助派生 | 可能修改 |
| `src/frontend/src/app/components/contextos/ContextOSWorkspace.test.tsx` | 单元测试：更新断言以匹配新 UI | 修改 |
| `docs/superpowers/specs/2026-06-18-contextos-ui-noise-reduction-design.md` | 设计文档（已存在） | 只读参考 |
| `src/frontend/src/app/components/factory/BenchStatusStrip.tsx` | 工厂 bench 状态条组件 | 不修改，仅停止在 ContextOS 中引用 |

---

### Task 1: 设计审查与最终确认

**Files:**
- Read: `docs/superpowers/specs/2026-06-18-contextos-ui-noise-reduction-design.md`
- Read: `src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx`
- Output: `docs/superpowers/reviews/2026-06-18-contextos-design-review.md`

**Interfaces:**
- Consumes: 设计文档与当前实现源码。
- Produces: 一份设计审查报告，列出对设计的同意、疑虑和修正建议。

- [ ] **Step 1: 阅读设计文档与当前实现**

阅读上述两个文件，重点核对：
- 高价值信号是否全部覆盖。
- 删除元素（组件健康面板、footer、按模式分布）是否确实与保留元素重复。
- Header 合并方案是否在视觉上仍清晰可读。

- [ ] **Step 2: 输出审查报告**

在 `docs/superpowers/reviews/2026-06-18-contextos-design-review.md` 中记录：
- 同意的决策。
- 任何疑虑（例如某信号是否会被误删）。
- 具体的修正建议（如果有）。

- [ ] **Step 3: 必要时修订设计文档**

如果审查发现必须修改的点，编辑 `docs/superpowers/specs/2026-06-18-contextos-ui-noise-reduction-design.md` 并说明修订原因。

---

### Task 2: 前端实现

**Files:**
- Modify: `src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx`
- Modify: `src/frontend/src/app/components/contextos/contextOSData.ts`（如需要新增辅助派生）
- Modify: `src/frontend/src/app/components/contextos/ContextOSWorkspace.test.tsx`

**Interfaces:**
- Consumes: `ContextOSModel`, `ContextOSEvent`, `buildContextOSModel`, `buildTelemetryFromStream`。
- Produces: 重构后的 `ContextOSWorkspace` 组件，保持 `ContextOSWorkspaceProps` 不变，所有现有数据字段继续可用。

#### Step 1: 准备——在本地分支工作并运行基线测试

- [ ] 确认当前在 feature 分支（不要直接在 `main` 上修改）。
- [ ] 运行基线测试：

```bash
npm run test -- src/frontend/src/app/components/contextos
```

Expected: 当前测试通过（47/47）。

#### Step 2: 重构 Header

- [ ] 打开 `src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx`。
- [ ] 将 Header 右侧的多个 badge/chip 合并为以下 4 个元素：
  1. 阶段 badge（保留 `StatusBadge`，显示阶段名 + 运行/空闲点）。
  2. 资源 chip：合并调用数、token 总量、最近时延。
  3. 遥测/WS 状态 badge：合并 WS 状态和遥测新鲜度。
  4. “上下文结构” toggle 按钮 + 刷新按钮。

实现后的 Header JSX 结构示例（保持现有 className 风格，只调整内容）：

```tsx
<div className="flex items-center gap-2">
  <StatusBadge color={model.running ? 'success' : 'default'} variant="dot" pulse={model.running}>
    <span className="font-mono text-[10px]">阶段 {phaseLabel}</span>
  </StatusBadge>

  <div
    className="flex items-center gap-1.5 rounded-lg border border-accent-secondary/20 bg-black/30 px-2.5 py-1"
    data-testid="contextos-resource-chip"
    title="调用次数 · token 总量 · 最近时延"
  >
    <Activity className="h-3.5 w-3.5 text-accent-secondary" />
    <span className="font-mono text-[11px] font-bold text-text-main">{model.calls.toLocaleString()}</span>
    <span className="text-[9px] font-bold uppercase tracking-wider text-accent-secondary/70">调用</span>
    {model.totalTokens > 0 && (
      <>
        <span className="text-text-dim/60">·</span>
        <Coins className="h-3 w-3 text-gold" />
        <span className="font-mono text-[11px] font-bold text-text-main">{model.totalTokens.toLocaleString()}</span>
        <span className="text-[9px] font-bold uppercase tracking-wider text-gold/70">tok</span>
      </>
    )}
    {model.realLatencyMs !== null && (
      <span className="font-mono text-[10px] text-text-muted">· {model.realLatencyMs}ms</span>
    )}
  </div>

  <StatusBadge
    color={model.telemetryActive ? (telemetryFresh ? 'success' : 'warning') : 'default'}
    variant="dot"
    pulse={model.telemetryActive && telemetryFresh}
  >
    <span className="font-mono text-[10px]" data-testid="contextos-telemetry-freshness">
      {model.telemetryActive
        ? `${telemetryFresh ? '实时遥测' : '遥测'}${freshnessLabel ? ` · ${freshnessLabel}` : ''}`
        : '遥测待命'}
      <span className="ml-1 text-text-dim/70">· {wsLabel}</span>
    </span>
  </StatusBadge>

  <Button
    variant={showStructure ? 'default' : 'outline'}
    size="sm"
    onClick={() => setShowStructure((value) => !value)}
    data-testid="contextos-structure-toggle"
  >
    <Database className="mr-1.5 h-3.5 w-3.5" />
    上下文结构
  </Button>

  <Button variant="outline" size="sm" onClick={handleRefresh} data-testid="contextos-refresh">
    <RefreshCw className="h-3.5 w-3.5" />
  </Button>
</div>
```

- [ ] 删除独立的 `model.iteration` badge、独立的质量门 badge、独立的 WS 状态 badge、独立的 token chip。
- [ ] 质量门状态保留为更 subtle 的形式：若 `qualityGate` 存在，在阶段 badge 后加一个微型 dot 或用 tooltip 显示。

#### Step 3: 简化 SectionCard

- [ ] 修改 `SectionCard` 组件，移除 `<BenchStatusStrip />` 注入：

```tsx
function SectionCard({ title, subtitle, icon: Icon, children, className, action }) {
  return (
    <section className={cn('flex flex-col rounded-xl border border-white/[0.07] bg-bg-panel/40 backdrop-blur-sm', className)}>
      <header className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2.5">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="h-3.5 w-3.5 shrink-0 text-accent-secondary" />
          <span className="truncate text-xs font-semibold text-text-main">{title}</span>
          {subtitle && <span className="truncate text-[10px] text-text-dim">{subtitle}</span>}
        </div>
        {action}
      </header>
      {/* 删除 <BenchStatusStrip /> */}
      <div className="min-h-0 flex-1 p-3">{children}</div>
    </section>
  );
}
```

- [ ] 确认 `BenchStatusStrip` 的 import 在 `ContextOSWorkspace.tsx` 中可删除（若 Header 不再使用）。

#### Step 4: 删除左侧组件健康面板

- [ ] 在主 grid 中删除左侧“组件健康”列，将布局从 `xl:grid-cols-[260px_minmax(0,1fr)_300px]` 改为 `xl:grid-cols-[minmax(0,1fr)_300px]`。
- [ ] 删除 `ComponentRow` 子组件（若不再使用）。
- [ ] 删除 `COMPONENT_ICONS` 常量（若不再使用）。
- [ ] 保留 `contextOSData.ts` 中的 `ComponentHealth` 接口和 `components` 字段，但 UI 不再渲染。

#### Step 5: 简化中央管线图

- [ ] 修改 `PipelineNode`，只保留 label 和 metric，去掉 `hint` 子标题：

```tsx
function PipelineNode({ stage }: { stage: PipelineStage }) {
  const Icon = STAGE_ICONS[stage.id] ?? Activity;
  const style = STATE_STYLES[stage.state];
  return (
    <div
      data-testid={`contextos-stage-${stage.id}`}
      data-state={stage.state}
      className={cn(
        'relative flex w-[112px] shrink-0 flex-col items-center gap-1 rounded-xl border px-2 py-3 text-center transition-all duration-500',
        style.ring,
      )}
      title={`${stage.component} — ${stage.hint}`}
    >
      <div className={cn('flex h-9 w-9 items-center justify-center rounded-lg bg-black/30', style.text)}>
        <Icon className="h-4 w-4" />
        {stage.state === 'active' && (
          <span className="absolute right-1.5 top-1.5 flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-secondary opacity-75 motion-reduce:animate-none" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent-secondary" />
          </span>
        )}
      </div>
      <div className="text-[11px] font-semibold leading-tight text-text-main">{stage.label}</div>
      <div className={cn('mt-0.5 rounded-full bg-black/30 px-1.5 py-0.5 font-mono text-[9px]', style.text)}>
        {stage.metric}
      </div>
    </div>
  );
}
```

- [ ] 删除管线卡片底部的静态说明文字（“投影排序(含预算规划) → 角色信号 → …”）。

#### Step 6: 简化角色信号面

- [ ] 将角色卡片区从 `grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5` 改为更紧凑的 5 列小网格，减少 padding。
- [ ] 简化 `RoleHex`：保留官职首字、名称、状态、token/事件数，去掉 `courtTitle` 子标题（改为 tooltip）。
- [ ] 删除角色面板下方的解释性段落。

#### Step 7: 压缩 RoleInternalPanel

- [ ] 将 4 段 mini-pipeline 的节点宽度从 `w-[92px]` 减小到 `w-[80px]`，并减少内边距。
- [ ] 将 6 个统计卡合并为 3 个：

```tsx
<div className="mb-3 grid grid-cols-3 gap-2">
  <RoleInternalStat label="活动" value={`${ctx.eventCount} · ${ctx.projectionCount} · ${ctx.receiptCount}`} sub="事件 · 投影 · 回执" />
  <RoleInternalStat label="调用" value={ctx.calls} sub={ctx.lastEventAt ? formatFreshness(ctx.lastEventAt) : '无活动'} />
  <RoleInternalStat label="Token" value={`${contextOSFormat.tokens(ctx.promptTokens)} / ${contextOSFormat.tokens(ctx.completionTokens)}`} sub="提示 / 输出" highlight={ctx.totalTokens > 0} />
</div>
```

- [ ] 事件列表去掉独立的时间戳列，用相对时间和 kind 合并：

```tsx
<div className="grid grid-cols-[80px_1fr] items-start gap-2 ...">
  <span className="font-mono text-[10px] text-text-dim">{contextOSFormat.clock(event.ts)}</span>
  <div>...</div>
</div>
```

#### Step 8: 删除 Footer 和按模式分布

- [ ] 删除 `Outcome Feedback Loop` footer 整个区块。
- [ ] 删除右侧“按模式分布”`SectionCard`。
- [ ] 保留“上下文预算”和“事件类型分布”。

#### Step 9: 统一标签语言

- [ ] 将所有卡片 `subtitle` 改为中文为主、英文为辅：
  - “上下文预算 (Context Budget)”
  - “事件类型分布 (Event Types)”
  - “角色信号面 (RoleSignalPlane)”
  - “决策/回执流 (Decision Log)”

#### Step 10: 更新测试

- [ ] 打开 `ContextOSWorkspace.test.tsx`。
- [ ] 删除断言“7 张组件健康卡”存在：

```tsx
// 删除以下循环
for (const id of ['truthlog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'telemetry']) {
  expect(screen.getByTestId(`contextos-component-${id}`)).toBeTruthy();
}
```

- [ ] 更新 Header 断言：合并后的资源 chip 应同时包含调用数和 token：

```tsx
const resourceChip = screen.getByTestId('contextos-resource-chip');
expect(resourceChip.textContent).toContain('1'); // calls
expect(resourceChip.textContent).toContain('3,386'); // tokens
```

- [ ] 新增断言：BenchStatusStrip 不在 ContextOS 中渲染：

```tsx
expect(screen.queryByTestId('bench-status-strip')).toBeNull();
```

- [ ] 保留并更新管线、角色卡、角色内部面板、上下文结构面板的测试。

#### Step 11: 运行验证

- [ ] 运行类型检查：

```bash
npm run typecheck
```

Expected: 无错误。

- [ ] 运行 lint：

```bash
npm run lint
```

Expected: 无新增 warning。

- [ ] 运行 ContextOS 测试：

```bash
npm run test -- src/frontend/src/app/components/contextos
```

Expected: 全部通过。

#### Step 12: 提交

```bash
git add src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx
# 若修改了 contextOSData.ts 也加入
git add src/frontend/src/app/components/contextos/contextOSData.ts
git add src/frontend/src/app/components/contextos/ContextOSWorkspace.test.tsx
git commit -m "refactor(contextos): reduce UI noise and consolidate high-signal observability

- Merge header badges into phase + resource + telemetry chips
- Remove component-health panel, footer, and mode-distribution card
- Remove BenchStatusStrip from ContextOS SectionCards
- Simplify pipeline nodes and RoleInternalPanel
- Unify labels to Chinese-primary with English subtitle
- Update tests to match new layout

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Playwright 视觉审计

**Files:**
- Create: `src/frontend/e2e/contextos-visual-audit.spec.ts`（或项目现有 e2e 目录）

**Interfaces:**
- Consumes: 重构后的 `ContextOSWorkspace` 渲染输出。
- Produces: Playwright 截图和审计报告，确认视觉噪音已减少。

#### Step 1: 定位 e2e 目录

- [ ] 确认项目 Playwright 配置。运行：

```bash
ls src/frontend/e2e/ 2>/dev/null || ls tests/e2e/ 2>/dev/null || ls e2e/ 2>/dev/null
```

- [ ] 根据现有目录创建测试文件。若 `src/frontend/e2e/` 存在，则文件路径为 `src/frontend/e2e/contextos-visual-audit.spec.ts`。

#### Step 2: 编写视觉审计测试

```tsx
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5173';

test.describe('ContextOS realtime view visual audit', () => {
  test('captures empty-state screenshot without bench strip pollution', async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    // 导航到 ContextOS 视图；根据实际 UI 调整选择器
    await page.click('[data-testid="nav-contextos"], [data-testid="open-contextos"]');
    await page.waitForSelector('[data-testid="contextos-workspace"]');

    // 关键断言：ContextOS 内不应渲染 bench strip
    await expect(page.locator('[data-testid="bench-status-strip"]')).toHaveCount(0);

    await page.screenshot({ path: 'playwright-report/contextos-empty.png', fullPage: false });
  });

  test('captures running-state screenshot and verifies consolidated header', async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    await page.click('[data-testid="nav-contextos"], [data-testid="open-contextos"]');
    await page.waitForSelector('[data-testid="contextos-workspace"]');

    const resourceChip = page.locator('[data-testid="contextos-resource-chip"]');
    await expect(resourceChip).toBeVisible();

    // Header 只应包含一个资源 chip 和一个遥测 badge，不应有多个重复 token chip
    await expect(page.locator('[data-testid="contextos-resource-chip"]')).toHaveCount(1);

    await page.screenshot({ path: 'playwright-report/contextos-running.png', fullPage: false });
  });

  test('captures role-detail screenshot', async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    await page.click('[data-testid="nav-contextos"], [data-testid="open-contextos"]');
    await page.waitForSelector('[data-testid="contextos-workspace"]');

    await page.click('[data-testid="contextos-role-pm"]');
    await page.waitForSelector('[data-testid="contextos-role-panel-pm"]');

    await page.screenshot({ path: 'playwright-report/contextos-role-pm.png', fullPage: false });
  });
});
```

注意：若项目没有直接导航到 ContextOS 的按钮，测试需要先用种子数据让应用进入 ContextOS 状态，或调整导航方式。可参考现有 e2e 测试的登录/导航模式。

#### Step 3: 运行截图测试

- [ ] 确保前端 dev server 已运行：

```bash
npm run dev:renderer
# 或用户已提供的 http://127.0.0.1:5173/
```

- [ ] 运行 Playwright 测试：

```bash
npx playwright test src/frontend/e2e/contextos-visual-audit.spec.ts --project=chromium
```

Expected: 测试通过，生成 `playwright-report/contextos-*.png`。

#### Step 4: 人工/启发式检查

- [ ] 打开生成的截图，逐项检查：
  - 无 Factory Bench 进度条出现在 ContextOS 卡片内。
  - Header 内没有多个重复的 token/calls/latency 元素。
  - 左侧没有 7 张组件健康卡。
  - 底部没有 Outcome Feedback Loop 条。
  - 右侧没有“按模式分布”卡。
  - 8 段管线清晰可见。
  - 5 角色卡可见且选中 PM 后内部面板可读。

#### Step 5: 输出审计报告

- [ ] 创建 `docs/superpowers/reviews/2026-06-18-contextos-playwright-audit.md`，包含：
  - 测试环境（URL、浏览器、viewport）。
  - 截图文件路径。
  - 检查结果（通过/不通过项）。
  - 发现的问题（如有）。

#### Step 6: 提交

```bash
git add src/frontend/e2e/contextos-visual-audit.spec.ts
git add docs/superpowers/reviews/2026-06-18-contextos-playwright-audit.md
git commit -m "test(contextos): add Playwright visual audit for noise-reduction refactor

- Capture empty, running, and role-detail states
- Verify no bench strip pollution and consolidated header
- Add audit report

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Header 合并 → Task 2 Step 2。
- SectionCard 去 BenchStatusStrip → Task 2 Step 3。
- 删除组件健康面板 → Task 2 Step 4。
- 简化管线 → Task 2 Step 5。
- 简化角色信号面 / RoleInternalPanel → Task 2 Step 6-7。
- 删除 footer / 按模式分布 → Task 2 Step 8。
- 标签统一 → Task 2 Step 9。
- Playwright 视觉审计 → Task 3。

**2. Placeholder scan:** 无 TBD/TODO；所有步骤包含具体文件路径、代码示例、命令。

**3. Type consistency:** `ContextOSWorkspaceProps` 不变；`ContextOSModel` 字段不删；测试用 `data-testid` 与代码一致。

---

## Execution Choice

Plan complete and saved to `docs/superpowers/plans/2026-06-18-contextos-ui-noise-reduction-plan.md`.

**Recommended:** Subagent-Driven Development — dispatch a fresh subagent per task, with task review between tasks.
