# AGENTS.md Increment: Electron Panel Auto-Triage (Playwright)

把下面片段合并到 `~/.codex/AGENTS.md`：

```md
## Electron Panel Auto-Triage (Playwright)

### Contract
Goal: 自动复现并修复 Electron 桌面端回归问题。
Acceptance Criteria:
- `npm run test:e2e` 通过（允许有显式 `skip`）。
- Electron 主进程终端输出（actionable）无报错。
- Renderer Console（actionable）无报错。

### Execution Order (Fixed)
1. 执行 `npm run test:e2e -- --list`，确认当前测试清单。
2. 执行 `npm run test:e2e`（Red）。
3. 读取 `test-results/electron/**` 与 `playwright-report/**` 的 trace/screenshot。
4. 仅修改最小必要文件修复问题。
5. 重跑 `npm run test:e2e` 至 Green。
6. 若项目提供脚本，再执行：`npm run test --silent` 与 `npm run build`。

### Guardrails
- 禁止跳过失败测试直接声称修复完成。
- 禁止在同一轮引入无关重构。
- 每次失败必须保留证据（trace/screenshot/log）。
- 禁止使用任何已下线的旧 E2E 命令与旧 runner 脚本。
```
