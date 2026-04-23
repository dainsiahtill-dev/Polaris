# Vitest 测试依赖安装说明

## 需要安装的依赖

运行以下命令安装测试相关依赖：

```bash
npm install -D vitest @vitest/ui @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @vitest/coverage-v8
```

## 依赖说明

- `vitest` - 测试框架（类似 Jest，但与 Vite 集成更好）
- `@vitest/ui` - 提供可视化测试界面
- `@testing-library/react` - React 组件测试工具
- `@testing-library/jest-dom` - 提供额外的 DOM 断言
- `@testing-library/user-event` - 模拟用户交互
- `jsdom` - 提供浏览器环境模拟
- `@vitest/coverage-v8` - 代码覆盖率工具

## 运行测试

安装依赖后，可以使用以下命令：

```bash
# 运行测试
npm test

# 运行测试并查看 UI
npm run test:ui

# 运行测试并生成覆盖率报告
npm run test:coverage
```

---

# Playwright Electron 测试

## 安装 Playwright 浏览器

```bash
npx playwright install
```

## 运行 Electron E2E 测试

```bash
npm run test:electron
```

> 默认优先使用 `.venv` 的 Python 启动后端。如果需要自定义解释器，请设置 `KERNELONE_PYTHON`。

## 一句话任务速查

```bash
# 非 LLM 主流程回归（推荐）
npm run test:e2e -- tests/electron/realtime-visibility.spec.ts tests/electron/panel-error.spec.ts

# PM -> Director 全链路（需要真实设置）
set KERNELONE_E2E_USE_REAL_SETTINGS=1
npm run test:e2e -- tests/electron/pm-director-real-flow.spec.ts

# 任务合同仅做结构检查（不执行）
npm run test:e2e:task -- --dry-run --task-file infrastructure/e2e/tasks/complex-project-fullstack.task.json
```

说明：
- 默认严格解析，不匹配会直接报错。
- 词典文件为 `infrastructure/e2e/panel-locators.json`，优先补别名和 `data-testid`。

## Electron 自动化完整手册

详细的一句话任务流程、自动修复循环、词典扩展和证据排查请看：

- `docs/testing/PLAYWRIGHT_ELECTRON_AUTOMATION.md`
