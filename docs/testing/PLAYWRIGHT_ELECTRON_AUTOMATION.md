# Playwright Electron 自动化流程手册

> 默认会阻断 LLM 相关测试任务，避免覆盖本地 LLM 配置与角色连线。  
> 如需显式放开，设置 `KERNELONE_E2E_ALLOW_LLM_TESTS=1`。

## 最短命令速查

```bash
# 安装依赖
npm install
npx playwright install

# 非 LLM 主流程回归（推荐）
npm run test:e2e -- src/backend/polaris/tests/electron/realtime-visibility.spec.ts src/backend/polaris/tests/electron/panel-error.spec.ts

# PM -> Director 全链路（需要真实设置）
set KERNELONE_E2E_USE_REAL_SETTINGS=1
npm run test:e2e -- src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts

# Claude 无人值守修复循环（真实 PM -> Director 全链路）
npm run auto:fix:real-flow

# 任务合同 dry-run（不执行，仅检查任务结构）
npm run test:e2e:task -- --dry-run --task-file infrastructure/e2e/tasks/complex-project-fullstack.task.json

# 混合栈编排预检（不执行真实动作）
npm run test:e2e:hybrid -- --dry-run "打开主界面并进入PM工作区"

# OpenAI Computer Use 适配器自检
npm run computer:openai -- --dry-run --prompt "打开设置面板并验证某字段可输入"

# Stagehand 语义兜底自检
npm run test:e2e:semantic -- --dry-run --prompt "打开主界面并进入PM工作区"

# OmniParser 结构化视觉自检
npm run vision:omniparser -- --dry-run --evidence-json .polaris/logs/demo.evidence.json

# 回归
npm run test:e2e:panel
npm run test:e2e
```

## 1. 目标
这套流程用于让 Codex/开发者用最少描述完成 Electron 面板自动化：

1. 启动应用
2. 按固定门禁顺序检查错误（Terminal -> Console -> Panel）
3. 打开目标页面并执行可见性/状态断言（不触发 LLM 配置修改）
4. 失败时保留证据并支持自动修复循环

核心设计目标：`一句话任务 -> 可复现执行 -> 可回归验证`。

---

## 2. 当前实现总览（代码入口）

1. Playwright 配置：`playwright.electron.config.ts`
2. Electron 测试夹具：`src/backend/polaris/tests/electron/fixtures.ts`
3. 固定门禁模板用例：`src/backend/polaris/tests/electron/panel-error.spec.ts`
4. 一句话任务用例：`src/backend/polaris/tests/electron/panel-task.spec.ts`
5. 任务解析器（自然语言 -> 可执行步骤）：`infrastructure/scripts/resolve-panel-task.mjs`
6. 一句话任务执行入口：`infrastructure/scripts/run-panel-task-e2e.mjs`
7. Stagehand 语义兜底入口：`infrastructure/scripts/run-stagehand-panel-task.mjs`
8. 自动修复循环入口：`infrastructure/scripts/auto-fix-panel-task.mjs`
9. 词典（导航/提供商/字段别名与定位）：`infrastructure/e2e/panel-locators.json`
10. 混合栈编排入口：`infrastructure/scripts/run-hybrid-panel-task.mjs`
11. 混合栈配置：`infrastructure/e2e/hybrid-automation.config.json`
12. OmniParser 适配入口：`infrastructure/scripts/run-omniparser-adapter.mjs`
13. npm 脚本定义：`package.json`
14. Claude 真实全链路自动修复入口：`infrastructure/scripts/auto-fix-real-flow.mjs`

---

## 3. 环境准备

1. 安装依赖：`npm install`
2. 安装 Playwright 浏览器：`npx playwright install`
3. Python 后端建议准备 `.venv`（夹具会优先使用），或设置 `KERNELONE_PYTHON`

---

## 4. 常用命令

1. 全量 Electron E2E：`npm run test:e2e`
2. 面板门禁模板：`npm run test:e2e:panel`
3. 非 LLM 可见性回归：
   `npm run test:e2e -- src/backend/polaris/tests/electron/realtime-visibility.spec.ts src/backend/polaris/tests/electron/panel-error.spec.ts`
4. PM -> Director 全链路：
   `set KERNELONE_E2E_USE_REAL_SETTINGS=1 && npm run test:e2e -- src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts`
5. 任务合同 dry-run（仅结构检查，不执行）：
   `npm run test:e2e:task -- --dry-run --task-file infrastructure/e2e/tasks/complex-project-fullstack.task.json`
6. 混合栈 dry-run：
   `npm run test:e2e:hybrid -- --dry-run "<一句话任务>"`
7. OpenAI Computer Use 适配器 dry-run：
   `npm run computer:openai -- --dry-run --prompt "打开设置面板并验证某字段可输入"`
8. Stagehand 语义兜底 dry-run：
   `npm run test:e2e:semantic -- --dry-run --prompt "打开主界面并进入PM工作区"`
9. OmniParser 适配器 dry-run：
   `npm run vision:omniparser -- --dry-run --evidence-json .polaris/logs/demo.evidence.json`
10. Claude 无人值守修复循环（真实全链路）：
   `npm run auto:fix:real-flow`

---

## 5. 一句话任务是怎么执行的

### 5.1 数据流
1. 输入一句话 prompt
2. `resolve-panel-task.mjs` 读取 `panel-locators.json`，生成任务 JSON
3. `run-panel-task-e2e.mjs` 将任务 JSON 写入 `E2E_PANEL_TASK_JSON_BASE64`
4. `panel-task.spec.ts` 读取任务并按步骤执行

也可通过 `--task-file <task.json>` 直接跳过自然语言解析。

### 5.2 任务 JSON 关键结构
1. `navigationSteps`: 页面导航点击路径
2. `fieldAction`: 字段定位与输入断言（如 `inputValue`、`expectContains`）
3. `gateConfig`: 严格模式和等待时间

### 5.3 解析严格模式（默认开启）
1. 默认严格校验词典命中；未命中会直接报错并停止执行。
2. 如需调试未收录词条，可显式加：
   - `--allow-provider-fallback`
   - `--allow-field-fallback`
3. 不建议在稳定回归中长期使用 fallback，建议补齐 `panel-locators.json`。

---

## 6. 三段门禁（必须顺序）

顺序固定：`Terminal Gate -> Console Gate -> Panel Gate`

1. Terminal Gate  
检查 Electron 主进程 stdout/stderr 是否有 actionable error

2. Console Gate  
检查 renderer `pageerror` + `console.error`（支持忽略规则）

3. Panel Gate  
执行面板导航与字段操作，断言目标行为成功

对应实现见：`src/backend/polaris/tests/electron/panel-error.spec.ts` 与 `src/backend/polaris/tests/electron/panel-task.spec.ts`。

---

## 7. 关键环境变量

### 7.1 面板模板/任务通用
1. `E2E_PANEL_STRICT_ERRORS=1`
2. `E2E_PANEL_STRICT_TERMINAL_ERRORS=1`
3. `E2E_PANEL_STARTUP_SETTLE_MS`
4. `E2E_PANEL_POST_ACTION_SETTLE_MS`
5. `E2E_PANEL_IGNORE_CONSOLE_REGEX`（`;` 分隔正则）
6. `E2E_PANEL_TERMINAL_ERROR_REGEX`（`;` 分隔正则）
7. `E2E_PANEL_IGNORE_TERMINAL_REGEX`（`;` 分隔正则）
8. `E2E_PANEL_TASK_FILE`（可替代命令行 `--task-file`）
9. `E2E_PANEL_TASK_DICTIONARY`（可替代命令行 `--dictionary`）
10. `E2E_PANEL_REQUIRE_ARIA_SNAPSHOT=1`（默认开启）
11. `E2E_PANEL_SEMANTIC_CLICK=1`（默认开启）
12. `KERNELONE_PANEL_SEMANTIC_FALLBACK=1`（`run-panel-task-e2e` 失败后尝试语义兜底）
13. `KERNELONE_PANEL_SEMANTIC_CMD`（语义兜底命令模板，支持 `{prompt}` `{task_file}` `{workspace}`）

### 7.2 panel-error.spec.ts 专用
1. `E2E_PANEL_TRIGGER_SELECTOR` / `E2E_PANEL_TRIGGER_TEXT`
2. `E2E_PANEL_TARGET_SELECTOR` / `E2E_PANEL_TARGET_TEXT`

### 7.3 自动修复循环专用
1. `E2E_PANEL_AUTOFIX_MAX_ATTEMPTS`（默认 2）
2. `E2E_PANEL_AUTOFIX_SKIP_BUILD=1`
3. `E2E_PANEL_CODEX_MODEL`
4. `E2E_PANEL_CODEX_DANGEROUS=1`

### 7.4 Claude 真实全链路修复循环
1. `KERNELONE_REAL_FLOW_AUTOFIX_MAX_ATTEMPTS`（默认 2）
2. `KERNELONE_REAL_FLOW_AUTOFIX_SKIP_BUILD=1`
3. `KERNELONE_CLAUDE_MODEL`
4. `KERNELONE_CLAUDE_PERMISSION_MODE`（默认 `bypassPermissions`）
5. `KERNELONE_CLAUDE_AGENT`
6. `KERNELONE_CLAUDE_ALLOWED_TOOLS`
7. `KERNELONE_CLAUDE_NO_SESSION_PERSISTENCE=0`（默认关闭会话持久化）
8. 基础提示词合同：`docs/prompt/元设计师-自动化测试v5.1.md`

### 7.5 Computer Use（OpenAI 适配器）
1. `OPENAI_API_KEY`（必需）
2. `OPENAI_BASE_URL`（可选）
3. `KERNELONE_COMPUTER_USE_MODEL`
4. `KERNELONE_COMPUTER_USE_START_URL`
5. `KERNELONE_COMPUTER_USE_MAX_STEPS`
6. `KERNELONE_COMPUTER_USE_HEADLESS`

### 7.6 Semantic / Stagehand
1. `KERNELONE_STAGEHAND_MODEL`（默认 `gpt-4.1-mini`）
2. `KERNELONE_STAGEHAND_START_URL`
3. `KERNELONE_STAGEHAND_TIMEOUT_MS`
4. `KERNELONE_STAGEHAND_HEADLESS`
5. `KERNELONE_STAGEHAND_VERIFY_CMD`

### 7.7 OmniParser
1. `KERNELONE_HYBRID_OMNIPARSER_CMD`（hybrid 阶段命令模板）
2. `KERNELONE_OMNIPARSER_ENGINE_CMD`（外部 OmniParser 引擎命令，可选）
3. `KERNELONE_OMNIPARSER_TIMEOUT_MS`

---

## 8. 证据输出与排查路径

1. Playwright 报告：`playwright-report/`
2. 测试产物：`test-results/electron/`
3. 关键附件：`renderer-errors`、`trace.zip`、失败截图
4. 自动修复日志：`.polaris/logs/autofix_panel_*.log`
5. Claude 真实全链路修复日志：`.polaris/logs/autofix_real_flow_*.log`
6. Claude 真实全链路审计：`.polaris/logs/autofix_real_flow_*.audit.json`

建议排查顺序：
1. 看 `renderer-errors`（先判定卡在哪个 gate）
2. 看 `aria-snapshot-baseline` / `aria-snapshot-postpanel`（语义状态是否异常）
3. 看 `trace.zip` 回放动作路径
4. 看失败截图定位 UI 选择器偏差

---

## 9. 如何扩展自动化能力（后续新增功能）

### 9.1 新增“可一句话识别”的面板/字段（首选）
编辑 `infrastructure/e2e/panel-locators.json`：

1. `navigation` 中补充页面导航别名和 selectorCandidates
2. `providers` 中补充目标对象别名和打开步骤
3. `fields` 中补充字段别名、selectorCandidates、labelCandidates、输入断言
4. `intents` 中补充动作意图词（如输入/点击/切换）

### 9.2 新增复杂交互动作（代码扩展）
当词典驱动不够时，在 `src/backend/polaris/tests/electron/panel-task.spec.ts` 增加动作处理逻辑：

1. 在 `executeNavigationStep` 增加特殊导航策略
2. 在 `resolveFieldLocator` 增加字段定位策略
3. 新增动作执行与断言函数（保持最小改动）

---

## 10. 推荐给 Codex 的描述模板

日常只需要一句话，建议包含 4 个要素：

1. 入口页面（例如“打开设置面板”）
2. 子页面（例如“PM 工作区”）
3. 目标对象（例如“任务进度卡片”）
4. 断言动作（例如“当前阶段和进度百分比可见”）

示例：

`打开主界面并进入PM工作区，确保任务进度面板显示当前阶段与百分比`

如果要自动修复一起执行：

`用 auto:fix:panel 跑这句话任务，失败就读取 trace 和 renderer-errors 做最小修复直到通过`

---

## 11. 常见问题

1. 为什么“我一句话描述了但没点到目标”？  
通常是 `panel-locators.json` 别名或 selectorCandidates 不够稳定。现在默认严格匹配会直接报错，优先补词典和 `data-testid`，不要长期依赖 fallback。

2. 为什么本机运行和 CI 表现不同？  
检查 `KERNELONE_DEV_SERVER_URL`、本地 `.venv`、以及是否有额外弹窗/权限提示。

3. 自动修复为什么不生效？  
`auto:fix:panel` 依赖本地 Codex CLI 能执行（含登录态），并且需要可写工作区。

---

## 12. 变更后最小回归

每次修改自动化流程，建议至少执行：

1. `npm run test:e2e:task -- --dry-run "<一句话任务>"`
2. `npm run test:e2e:task -- "<一句话任务>"`
3. `npm run test:e2e:panel`

如果流程改动较大，再跑：

4. `npm run test:e2e`
