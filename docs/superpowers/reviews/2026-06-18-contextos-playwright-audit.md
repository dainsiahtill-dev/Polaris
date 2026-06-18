# ContextOS UI 降噪重构 — Playwright 视觉审计报告

**Date:** 2026-06-18  
**Environment:** Chromium 1440×900, renderer dev server at http://127.0.0.1:5173/  
**Test command:** `npx playwright test -c playwright.renderer.config.ts src/frontend/e2e/contextos-visual-audit.spec.ts --project=chromium`

## Screenshots

| 场景 | 文件 |
|---|---|
| 空状态（无运行） | `playwright-report/contextos-audit/contextos-empty-state.png` |
| PM 角色内部面板 | `playwright-report/contextos-audit/contextos-role-pm-detail.png` |

## 检查清单

- [x] Header 从 7 个元素合并为 4 个以内（阶段 badge + 资源 chip + 遥测/WS badge + 结构 toggle/刷新）。
- [x] `bench-status-strip` 不在 ContextOS 视图内渲染。
- [x] 8 段管线保留且可识别。
- [x] 5 角色卡保留，选中 PM 后内部面板可读。
- [x] 组件健康面板不再渲染（页面无“组件健康”文本）。
- [x] Footer Outcome Feedback Loop 不再渲染。
- [x] 按模式分布卡不再渲染。
- [x] 截图中无重复 token/calls 芯片。

## 结果

视觉审计通过。重构后的 ContextOS 实时视图噪音显著降低，高价值信号（运行状态、token、调用、管线、角色状态、决策流）仍然清晰可辨。

## 备注

- 当前审计在空状态（无 PM/Director 运行）下完成。运行中状态的截图需要在实际运行后补拍。
- 新增 per-LLM / per-worker 真实上下文查看器需求已在设计文档中记录，但因需要后端采集完整 prompt 上下文，本期未实现 UI 入口。
