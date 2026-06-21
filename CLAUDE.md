# CLAUDE.md

本文件用于指导在本仓库工作的 AI 编码代理。仅保留可执行、可验证的技术约束。

**必用MCP和Skill**: 充分利用codegraph MCP和superpowers，必要时需要使用Playwright来真实跑测试和审计。

## 0) 后端权威入口（2026-03-22）
- 对于任何 `src/backend` 任务，必须先读 `src/backend/AGENTS.md`。
- 统一架构执行标准入口：`src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`。
- 后端强制规则：`Cell` 开发先复用已有 Cell 公开能力；所有新开发必须基于 `KernelOne` 底座能力与契约链路。
- 若本文件与 `src/backend/AGENTS.md` 或 `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` 存在冲突，以后两者为准。

## 1) 真实入口路径
- 桌面入口: `src/electron/main.cjs`
- 后端入口: `src/backend/server.py` -> `src/backend/polaris/delivery/http/app_factory.py` (FastAPI)
- 前端入口: `src/frontend/src/main.tsx`（Vite 配置: `src/frontend/vite.config.ts`）
- PM CLI: `src/backend/polaris/delivery/cli/pm/cli.py`（控制台脚本 `pm`）
- Director CLI (推荐): `src/backend/polaris/delivery/cli/director/cli_thin.py`（控制台脚本 `director`）
- Architect CLI: `src/backend/polaris/cells/architect/design/internal/architect_cli.py`
- Chief Engineer CLI: `src/backend/polaris/cells/chief_engineer/blueprint/internal/chief_engineer_cli.py`

## 2) 维护优先级路径
- 后端新架构目标根: `src/backend/polaris`
- 后端新功能目标分层: `src/backend/polaris/bootstrap`, `src/backend/polaris/delivery`, `src/backend/polaris/application`, `src/backend/polaris/domain`, `src/backend/polaris/kernelone`, `src/backend/polaris/infrastructure`, `src/backend/polaris/cells`
- 后端图谱与治理真相: `src/backend/docs/graph`, `src/backend/docs/governance`, `src/backend/docs/templates`
- 后端 API 与服务: `src/backend/polaris/delivery`
- Loop / 角色内核（优先修改）: `src/backend/polaris/cells/roles`, `src/backend/polaris/kernelone`
- Director Runtime/Accel: `src/backend/polaris/cells/director`
- PM/Director 编排层: `src/backend/polaris/delivery/cli/pm`, `src/backend/polaris/delivery/cli/director`
- 前端主 UI: `src/frontend/src/app`
- 测试: `tests/electron`, `src/backend/polaris/tests`

说明:
- `src/backend/polaris` 是后端 ACGA 2.0 迁移承载根；新的主实现优先进入这里
- 旧根 `src/backend/{app,core,api,scripts}` 已在 ACGA 2.0 迁移中删除并迁入 `src/backend/polaris/{bootstrap,delivery,application,domain,kernelone,infrastructure,cells}`

## 3) 常用命令
```bash
# 全栈开发（Electron + Backend + Frontend）
npm run dev

# 前端 / Electron 单独运行
npm run dev:renderer
npm run dev:electron

# 后端单独运行
python src/backend/server.py --host 127.0.0.1 --port 49977

# PM CLI (项目管理) - 控制台脚本 pm = polaris.delivery.cli.pm.cli:main
pm --workspace <repo> --run-director --director-iterations 1

# Director CLI (推荐) - 控制台脚本 director = polaris.delivery.cli.director.cli_thin:main
director --workspace <repo> --iterations 1

# Architect CLI (架构设计 - 交互式)
python -m polaris.cells.architect.design.internal.architect_cli --mode interactive --workspace <repo>

# Chief Engineer CLI (技术分析 - 交互式)
python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli --mode interactive --workspace <repo>

# 统一角色对话 API (所有 5 个角色)
# POST /v2/role/{pm|architect|chief_engineer|director|qa}/chat

# V2 API 端点
# PM: /v2/pm/*
# Director: /v2/director/*
# Role Chat: /v2/role/{role}/chat
```

## 4) 验证命令（按改动面最小执行）
```bash
# 前端改动
npm run typecheck
npm run lint
npm run test

# Electron E2E (唯一 E2E 测试)
npm run test:e2e

# Python/后端改动
pytest
pytest src/backend/tests

# 工厂冒烟（可选）
python scripts/run_factory_e2e_smoke.py --workspace .
```

## 5) 强约束
- 所有文本文件读写必须显式使用 UTF-8。
- TypeScript 保持 `strict`，公共接口禁止 `any`。
- 变更 Loop / 角色内核时，优先修改 `src/backend/polaris/cells/roles` 与 `src/backend/polaris/kernelone`。
- 不提交运行时产物: `.polaris/runtime/**`, `playwright-report/**`, `test-results/**`。
- 验证失败不得标记任务完成（fail-closed）。

## 6) 常用环境变量
- `KERNELONE_WORKSPACE`
- `KERNELONE_RENDERER_PORT`
- `KERNELONE_BACKEND_PORT`
- `KERNELONE_PM_PROVIDER`, `KERNELONE_PM_MODEL`

## 7) 核心系统地图（防重复造轮子）

以下模块已实现，禁止重复创建：

### 7.1) LLM 工具系统
**唯一实现**: `src/backend/polaris/kernelone/llm/toolkit/`

```python
# ✅ 正确用法
from polaris.kernelone.llm.toolkit import (
    AgentAccelToolExecutor,      # 统一工具执行器
    parse_tool_calls,            # 工具调用解析
)

# 获取角色工具集成（注册表 ROLE_TOOL_INTEGRATIONS 现位于 tool_runtime cell）
from polaris.cells.llm.tool_runtime.internal.role_integrations import ROLE_TOOL_INTEGRATIONS

integration = ROLE_TOOL_INTEGRATIONS["pm"](workspace=".")
prompt = integration.get_system_prompt()
```

**禁止行为**:
- ✗ 在 `polaris/cells/llm/` 下新建 `*ToolIntegration` 类
- ✗ 自定义 `TOOL_CALL:...ARGS:...` 格式
- ✗ 直接调用底层 `tools.py`

**相关文件**:
- `polaris/kernelone/llm/toolkit/definitions.py` - 工具定义（单一事实来源）
- `polaris/kernelone/llm/toolkit/executor/` - 工具执行（目录）
- `polaris/cells/llm/tool_runtime/internal/role_integrations.py` - 5个角色的工具集成
- `polaris/kernelone/llm/toolkit/parsers/` - 工具调用解析（目录）

### 7.2) 角色对话系统
**唯一实现**: `src/backend/polaris/cells/llm/dialogue/internal/role_dialogue.py`

```python
# ✅ 正确用法
from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response

result = await generate_role_response(
    workspace=workspace,
    settings=settings,
    role="pm",  # 或 architect, chief_engineer, director, qa
    message=message,
)
```

**角色提示词注册表**: `ROLE_PROMPT_TEMPLATES`
- `pm` - 尚书令 (项目管理)
- `architect` - 中书令 (架构设计)
- `chief_engineer` - 工部尚书 (技术分析)
- `director` - 工部侍郎 (代码执行)
- `qa` - 门下侍中 (质量审查)
- `scout` - 探子 (只读代码探索，sub-agent，即将由 PM/Director 调用)

**禁止行为**:
- ✗ 在 `polaris/cells/llm/dialogue/` 下新建独立角色对话文件（已统一到 `role_dialogue.py`）
- ✗ 在角色 CLI/internal 模块下内嵌角色提示词
- ✗ 创建新的 `generate_xxx_response()` 函数

### 7.3) Provider 系统
- ✗ 直接操作 `base_provider.provider_registry`
- ✗ 绕过 `ProviderManager` 创建 Provider 实例

### 7.4) 任务管理系统
**唯一实现**: `src/backend/polaris/cells/runtime/task_runtime/internal/task_board.py`

```python
# ✅ 正确用法
from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoard

board = TaskBoard(workspace=".")
board.create(subject="实现登录功能", priority="high")
```

### 7.5) 已删除模块（历史记录）

| 模块 | 替代方案 | 状态 |
|------|----------|------|
| `pm_dialogue.py` | `polaris.cells.llm.dialogue.internal.role_dialogue.generate_role_response(role="pm", ...)` | 已删除 |
| `pm_tools.py` | `polaris.kernelone.llm.toolkit.AgentAccelToolExecutor` | 已删除 |
| `api/routers/pm.py` | `polaris/delivery/http/routers/pm_chat.py` + `pm_management.py`（`/v2/pm`） | 已删除 |
| `workflow_nodes_compat.py` | `polaris/cells/roles/adapters/internal/workflow_adapter.py` | 已删除 |

### 7.6) 新增能力检查清单

在实现新功能前，检查：

1. **工具能力?** → 先看 `polaris/kernelone/llm/toolkit/` 是否已存在
2. **角色对话?** → 先看 `role_dialogue.ROLE_PROMPT_TEMPLATES` 是否已有
3. **Provider?** → 先看 `providers/provider_registry.py` 是否已支持
4. **任务管理?** → 先看 `task_board.py` 是否满足需求

如果不确定，查看对应模块的 `__init__.py` 中的 **"防重复造轮子提示"** 区域。

## 8) 绝对禁止：在 Polaris 项目中添加业务代码

**铁律**：Polaris 是元工具平台，禁止在主仓代码中添加任何目标项目/业务相关代码。

### 8.1) 禁止行为
- ❌ 在 `worker_executor.py` 或任何 Polaris 源码中为特定项目添加代码模板（如 Express、Django、React 等）
- ❌ 在 Polaris 代码库中硬编码目标项目的配置、路径、或文件名
- ❌ 为解决特定项目问题而修改 Polaris 核心逻辑（应修复通用逻辑）

## 🛠️ 核心开发规范与质量验收标准 (Core Quality Gates)

作为资深 Python 研发专家，你产出的任何代码**必须（MUST）**在提交或宣告任务完成前，通过以下三道质量网关。绝对不允许提交未经这三个工具实际运行并验证通过的代码。

### 1. 代码规范与格式化 (Ruff)
* **要求**：所有 Python 代码必须严格符合 PEP 8 规范，保持高度整洁和一致性。
* **强制动作**：在编写或修改代码后，必须立即运行 `ruff check . --fix` 和 `ruff format .`。
* **验收标准**：Ruff 检查过程必须静默，不能有任何残留的 Error、Warning 甚至未使用的 Import。

### 2. 静态类型安全 (Mypy)
* **要求**：所有函数签名、类的方法和关键变量**必须**包含完整的 Python 类型提示（Type Hints）。
* **强制动作**：执行 `mypy <你的代码文件>.py` 进行静态类型推导分析。
* **验收标准**：Mypy 必须输出 "Success: no issues found"。严禁使用 `# type: ignore` 来掩盖真实的类型冲突（除非在与无类型提示的老旧第三方库交互且极其必要的情况下）。

### 3. 自动化测试与逻辑验证 (Pytest)
* **要求**：任何业务逻辑代码都必须配有对应的单元测试用例（文件需以 `test_` 开头）。
* **强制动作**：执行 `pytest <你的测试文件>.py -v`。
* **验收标准**：所有测试用例必须 100% 绿色通过（PASS）。

### 🔄 强制自我修正协议 (Self-Correction Protocol)
如果在上述任何一个步骤中，工具抛出异常或返回非 0 状态码，你必须进入自修复循环：
1. **禁止逃逸**：严禁直接输出带有 Bug 的最终代码，或对人类说“请你这样修改...”。你必须亲自解决。
2. **分析报错**：仔细阅读并提取终端输出的 Traceback 或具体的 Error Message。
3. **闭环修复**：根据报错信息反思根本原因，修改你的代码，并**重新运行**对应的检查工具。
4. **循环熔断**：重复此过程，直到三个工具全部验收通过。如果在同一个问题上连续失败 5 次，请停止重试，向人类求助，并提供精炼后的报错上下文和你之前的尝试思路。


## 外部并行工程 Agent 调用规范

Codex、Claude Code 等主 Agent 可以通过 OpenCode CLI 将独立工程任务派发给额外 Agent。

### 调用方式

单个任务：

```bash
opencode run "<完整任务提示词>"
```

多个互不重叠的任务可以并行执行：

```bash
opencode run "<Agent 01 完整提示词>" &
opencode run "<Agent 02 完整提示词>" &
opencode run "<Agent 03 完整提示词>" &
wait
```

并行 Agent 可以在同一仓库中工作，但必须负责互不重叠的代码范围。不得让多个 Agent 同时修改同一文件或同一功能链路。

### 主 Agent 职责

调用 OpenCode 前，主 Agent必须先：

1. 阅读仓库中的 `AGENTS.md` 及相关架构文档。
2. 检查 `git status`、`git diff`、失败测试和用户反馈。
3. 使用仓库提供的代码图谱、符号索引或 MCP 工具审计相关代码。
4. 将问题拆分成多个互不重叠、可独立完成的任务包。
5. 为每个任务包明确目标、代码范围、禁止事项和验收命令。

适合并行的任务示例：

- 修复一个独立的配置传递问题。
- 修复一个独立的超时判定问题。
- 审计一个多实例调度链路。
- 修复一个提供商集成的静默降级问题。
- 为一个已经确认的缺陷补充生产修复和回归测试。

以下情况不得并行：

- 多个 Agent 需要修改同一个文件。
- 多个任务依赖同一个公共接口变更。
- 一个任务的实现依赖另一个任务的结论。
- 任务边界尚未明确。

此时应改为串行执行。

### Agent 提示词要求

每个 `opencode run` 必须获得完整、自包含的提示词，不能依赖当前对话中的隐含上下文。

提示词至少应包含：

- Agent 编号和名称。
- 独立任务目标。
- 必须阅读的规范文件。
- 必须使用的代码审计工具。
- 允许修改的代码范围。
- 禁止修改的范围。
- 强调充分利用codegraph MCP
- 不可违反的架构约束。
- 必须运行的验证命令。
- 最终 JSON 报告格式。

### OpenCode Agent 通用规则

每个 OpenCode Agent 必须遵守：

1. 修改前阅读根目录及相关子目录中的 `AGENTS.md`。
2. 修改前检查 `git status` 和现有 `git diff`。
3. 保留用户已有修改，不得覆盖、回退或清理无关改动。
4. 先使用指定的代码图谱、符号索引或 MCP 工具审计代码路径，禁止先盲目搜索和修改。
5. 只能修改任务明确授权的范围。
6. 禁止顺手重构、全仓格式化或修改无关代码。
7. 必须修复根因，禁止表层绕过。
8. 禁止硬编码成功、吞掉异常、静默 fallback 或禁用检查。
9. 禁止仅修改测试来制造通过结果。
10. 禁止用 mock 或 fake 替代任务要求验证的真实执行路径。
11. 禁止修改生成物或下游项目来掩盖源代码缺陷。
12. 修改后必须运行任务中指定的质量门禁。
13. 未实际执行的命令不得报告为通过。
14. 最终必须输出机器可读的 JSON 报告。
15. 所有文本文件读写必须显式使用 `UTF-8`（包括日志/JSON/Markdown/代码文件）。

### 标准提示词模板

```text
你是 <项目名称> 工程修复 Agent <编号>/<名称>。

硬性要求：
1. 必须先阅读 AGENTS.md 以及以下相关规范：
   - <相关 AGENTS.md>
   - <架构文档>
2. 必须先使用 <代码图谱或 MCP 工具> 审计相关代码路径，禁止先盲改。
3. 只能修改本任务明确授权的代码范围。
4. 必须保留现有无关修改，禁止覆盖或回退用户工作。
5. 修复必须针对根因，禁止表层绕过、硬编码成功、静默 fallback 或只改测试。
6. 不得违反任务列出的架构约束。
7. 修改后必须运行全部验收命令。
8. 最终必须输出 JSON 审计报告。
9. 充分使用codegraph。

任务目标：
<这个 Agent 独立负责的缺口>

预期结果：
<修复后应当观察到的行为>

允许修改范围：
- <文件、目录或符号>

禁止修改范围：
- <文件、目录、生成物或下游项目>

架构约束：
- <必须保留的架构约束>
- <禁止新增的实现方式>

必须完成：
1. <审计或实现要求>
2. <测试要求>
3. <兼容性要求>

必须验证：
- <lint 命令>
- <format check 命令>
- <type check 或 build 命令>
- <相关测试命令>

最终输出 JSON：
{
  "agent_id": "<编号>/<名称>",
  "status": "completed | blocked | failed",
  "issue": "...",
  "root_cause": "...",
  "files_changed": [],
  "tests_changed": [],
  "commands_run": [
    {
      "command": "...",
      "exit_code": 0,
      "result": "..."
    }
  ],
  "remaining_risks": [],
  "blocked_reason": null
}
```

### 结果回收

所有 OpenCode Agent 完成后，主 Agent 不得直接相信其报告，必须重新审计：

```bash
git status
git diff
```

并检查：

- 是否越过任务范围。
- 是否修改了无关文件。
- 是否覆盖了已有修改。
- 是否只改测试而未修复生产代码。
- 是否加入硬编码成功、异常吞噬或静默降级。
- 是否用 mock 替代真实执行路径。
- 是否违反架构约束。
- 报告中的测试和质量门禁是否真的执行。
- 多个 Agent 的修改合并后是否仍然通过验证。

主 Agent 对最终代码、测试结果和最终回复负责。

### 核心原则

OpenCode 并行派工必须满足：

- 任务必须窄。
- 修改范围必须互不重叠。
- 证据必须明确。
- 验收命令必须可执行。
- 最终报告必须机器可读。
- 所有结果必须由主 Agent 独立复核。