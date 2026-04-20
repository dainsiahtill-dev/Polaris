# Playwright Electron E2E Tests

**这是 Polaris 唯一的 E2E 测试套件。**

## Prereqs
- `npm install`
- `npx playwright install`

## Commands
- Run all Electron E2E tests:
  - `npm run test:e2e`
- Run panel regression template only:
  - `npm run test:e2e:panel`
- Run one-line natural language panel task:
  - `npm run test:e2e:task -- "打开设置面板并验证主题切换按钮可见"`
- Run one-line task with auto Codex repair loop:
  - `npm run auto:fix:panel -- "打开设置面板并验证主题切换按钮可见"`
- Run the real PM/Director flow with unattended Claude repair rounds:
  - `npm run auto:fix:real-flow`
- Run hybrid stack (Playwright main + Computer Use + Airtest/SikuliX fallback):
  - `npm run test:e2e:hybrid -- "打开设置面板并验证主题切换按钮可见"`
- Run hybrid stack in continuous retry mode:
  - `npm run auto:fix:hybrid -- "打开设置面板并验证主题切换按钮可见"`
- Dry-run OpenAI Computer Use fallback adapter:
  - `npm run computer:openai -- --dry-run --prompt "打开设置面板并验证某字段可输入"`

## Panel Template Environment Variables
- `E2E_PANEL_TRIGGER_SELECTOR`: selector for the button/entry that opens target panel
- `E2E_PANEL_TRIGGER_TEXT`: fallback button text (regex, case-insensitive)
- `E2E_PANEL_TARGET_SELECTOR`: selector that must be visible after panel opens
- `E2E_PANEL_TARGET_TEXT`: fallback visible text assertion
- `E2E_PANEL_STRICT_ERRORS=1`: fail on `pageerror` and actionable `console.error`
- `E2E_PANEL_STRICT_TERMINAL_ERRORS=1`: fail on actionable Electron terminal errors before panel action (defaults to enabled when `E2E_PANEL_STRICT_ERRORS=1`)
- `E2E_PANEL_IGNORE_CONSOLE_REGEX`: optional extra ignore regex, separated by `;`
- `E2E_PANEL_TERMINAL_ERROR_REGEX`: optional override regex for terminal error detection, separated by `;`
- `E2E_PANEL_IGNORE_TERMINAL_REGEX`: optional ignore regex for terminal lines, separated by `;`
- `E2E_PANEL_STARTUP_SETTLE_MS`: wait time before terminal/console baseline gate (default `1200`)
- `E2E_PANEL_POST_ACTION_SETTLE_MS`: wait time after panel action before final checks (default `800`)
- `E2E_PANEL_AUTOFIX_MAX_ATTEMPTS`: max Codex repair rounds for `auto:fix:panel` (default `2`)
- `E2E_PANEL_AUTOFIX_SKIP_BUILD=1`: skip `npm run build` in auto-fix loop
- `E2E_PANEL_CODEX_MODEL`: optional model override passed to `codex exec --model`
- `E2E_PANEL_CODEX_DANGEROUS=1`: optionally enable `--dangerously-bypass-approvals-and-sandbox` for non-interactive Codex runs
- `POLARIS_REAL_FLOW_AUTOFIX_MAX_ATTEMPTS`: max Claude repair rounds for `auto:fix:real-flow` (default `2`)
- `POLARIS_REAL_FLOW_AUTOFIX_SKIP_BUILD=1`: skip `npm run build` in the real-flow repair loop
- `POLARIS_CLAUDE_MODEL`: optional model override passed to `claude --model`
- `POLARIS_CLAUDE_PERMISSION_MODE`: optional permission mode override (default `bypassPermissions`)
- `POLARIS_CLAUDE_AGENT`: optional Claude custom agent name
- `POLARIS_CLAUDE_ALLOWED_TOOLS`: optional `claude --allowedTools` override
- `POLARIS_CLAUDE_NO_SESSION_PERSISTENCE=0`: keep Claude session history instead of the default stateless round execution

Built-in ignored terminal noise:
- Chromium cache permission/cache creation lines
- DevTools `Autofill.enable` protocol warning

Example:

```bash
E2E_PANEL_TRIGGER_SELECTOR='[data-testid="open-settings"]' \
E2E_PANEL_TARGET_SELECTOR='[data-testid="settings-modal"]' \
E2E_PANEL_STRICT_ERRORS=1 \
npm run test:e2e:panel
```

## Notes
- If `src/frontend/dist/index.html` exists, tests load dist directly. For absolute `/assets/*` in `index.html`, fixture auto-generates `index.e2e.html` with relative paths.
- Otherwise set `POLARIS_DEV_SERVER_URL` to a running dev server URL.
- `.venv` is preferred for backend start. Override with `POLARIS_PYTHON`.
- Gate order in `panel-error.spec.ts`: `terminal -> console -> panel`.
- Gate order in `panel-task.spec.ts`: `terminal -> console -> panel`.
- One-line task resolution dictionary: `infrastructure/e2e/panel-locators.json`.
- Suggested AGENTS snippet path: `docs/templates/AGENTS.electron-playwright.md`.
- Auto-fix runner writes a UTF-8 log to `.polaris/logs/autofix_panel_*.log`.
- Real-flow auto-fix writes UTF-8 prompt/output/audit files to `.polaris/logs/autofix_real_flow_*`.
- Auto-fix requires local Codex CLI auth (`codex login`) before execution.
- Real-flow auto-fix requires local Claude CLI auth (`claude`) and real Polaris settings because it forces `POLARIS_E2E_USE_REAL_SETTINGS=1`.
- Hybrid runner writes a UTF-8 audit JSON to `.polaris/logs/hybrid_*.audit.json`.
- Hybrid fallback command wiring guide: `docs/testing/HYBRID_UI_AUTOMATION.md`.
- 默认会阻断 LLM 相关测试任务；仅当显式设置 `POLARIS_E2E_ALLOW_LLM_TESTS=1` 才允许执行。
- OpenAI Base URL 使用环境变量 `OPENAI_BASE_URL`（默认 `https://api.openai.com/v1`）。
