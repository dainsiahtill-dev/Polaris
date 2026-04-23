# CLAUDE.md

本文件用于指导在本仓库工作的 AI 编码代理。仅保留可执行、可验证的技术约束。

## 0) 后端权威入口（2026-03-22）
- 对于任何 `src/backend` 任务，必须先读 `src/backend/AGENTS.md`。
- 统一架构执行标准入口：`src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`。
- 后端强制规则：`Cell` 开发先复用已有 Cell 公开能力；所有新开发必须基于 `KernelOne` 底座能力与契约链路。
- 若本文件与 `src/backend/AGENTS.md` 或 `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` 存在冲突，以后两者为准。

## 1) 真实入口路径
- 桌面入口: `src/electron/main.cjs`
- 后端入口: `src/backend/server.py` -> `src/backend/app/main.py` (FastAPI)
- 前端入口: `src/frontend/src/main.tsx`（Vite 配置: `src/frontend/vite.config.ts`）
- PM CLI: `src/backend/scripts/pm/cli.py`
- Director CLI (推荐): `src/backend/scripts/director/cli_thin.py`
- Architect CLI: `src/backend/core/polaris_loop/role_agent/architect_cli.py`
- Chief Engineer CLI: `src/backend/core/polaris_loop/role_agent/chief_engineer_cli.py`

## 2) 维护优先级路径
- 后端新架构目标根: `src/backend/polaris`
- 后端新功能目标分层: `src/backend/polaris/bootstrap`, `src/backend/polaris/delivery`, `src/backend/polaris/application`, `src/backend/polaris/domain`, `src/backend/polaris/kernelone`, `src/backend/polaris/infrastructure`, `src/backend/polaris/cells`
- 后端图谱与治理真相: `src/backend/docs/graph`, `src/backend/docs/governance`, `src/backend/docs/templates`
- 后端 API 与服务: `src/backend/app`
- Loop 核心（优先修改）: `src/backend/core/polaris_loop`
- Director Runtime/Accel: `src/backend/core/director_runtime`
- PM/Director 编排层: `src/backend/scripts/pm`, `src/backend/scripts/director`
- 前端主 UI: `src/frontend/src/app`
- 测试: `tests/electron`, `src/backend/tests`

说明:
- `src/backend/polaris` 是后端 ACGA 2.0 迁移承载根；新的主实现优先进入这里
- `src/backend/app`、`src/backend/core`、`src/backend/api`、`src/backend/scripts` 仍是现有运行事实，但在后端迁移任务里应默认视为旧根目录

## 3) 常用命令
```bash
# 全栈开发（Electron + Backend + Frontend）
npm run dev

# 前端 / Electron 单独运行
npm run dev:renderer
npm run dev:electron

# 后端单独运行
python src/backend/server.py --host 127.0.0.1 --port 49977

# PM CLI (项目管理)
python src/backend/scripts/pm/cli.py --workspace <repo> --run-director --director-iterations 1

# Director CLI (推荐)
python -m scripts.director.cli_thin --workspace <repo> --iterations 1

# Architect CLI (架构设计 - 交互式)
python -m core.polaris_loop.role_agent.architect_cli --mode interactive --workspace <repo>

# Chief Engineer CLI (技术分析 - 交互式)
python -m core.polaris_loop.role_agent.chief_engineer_cli --mode interactive --workspace <repo>

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
- 变更 Loop 核心时，优先修改 `src/backend/core/polaris_loop`。
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
**唯一实现**: `src/backend/core/llm_toolkit/`

```python
# ✅ 正确用法
from core.llm_toolkit import (
    AgentAccelToolExecutor,      # 统一工具执行器
    ROLE_TOOL_INTEGRATIONS,      # 角色工具注册表
    parse_tool_calls,            # 工具调用解析
)

# 获取角色工具集成
integration = ROLE_TOOL_INTEGRATIONS["pm"](workspace=".")
prompt = integration.get_system_prompt()
```

**禁止行为**:
- ✗ 在 `app/llm/usecases/` 下新建 `*ToolIntegration` 类
- ✗ 自定义 `TOOL_CALL:...ARGS:...` 格式
- ✗ 直接调用底层 `tools.py`

**相关文件**:
- `llm_toolkit/definitions.py` - 工具定义（单一事实来源）
- `llm_toolkit/executor.py` - 工具执行
- `llm_toolkit/integrations.py` - 5个角色的工具集成
- `llm_toolkit/parsers.py` - 工具调用解析

### 7.2) 角色对话系统
**唯一实现**: `src/backend/app/llm/usecases/role_dialogue.py`

```python
# ✅ 正确用法
from app.llm.usecases.role_dialogue import generate_role_response

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
- ✗ 在 `app/llm/usecases/` 下新建独立角色对话文件（已统一到 `role_dialogue.py`）
- ✗ 在 `role_agent/` 下内嵌角色提示词
- ✗ 创建新的 `generate_xxx_response()` 函数

### 7.3) Provider 系统
- ✗ 直接操作 `base_provider.provider_registry`
- ✗ 绕过 `ProviderManager` 创建 Provider 实例

### 7.4) 任务管理系统
**唯一实现**: `src/backend/app/services/task_board.py`

```python
# ✅ 正确用法
from app.services.task_board import TaskBoard

board = TaskBoard(workspace=".")
board.create(subject="实现登录功能", priority="high")
```

### 7.5) 已删除模块（历史记录）

| 模块 | 替代方案 | 状态 |
|------|----------|------|
| `pm_dialogue.py` | `role_dialogue.generate_role_response(role="pm", ...)` | 已删除 |
| `pm_tools.py` | `llm_toolkit.executor.AgentAccelToolExecutor` | 已删除 |
| `api/routers/pm.py` | `api/v2/pm.py` | 已删除 |
| `workflow_nodes_compat.py` | `app/roles/workflow_adapter.py` | 已删除 |

### 7.6) 新增能力检查清单

在实现新功能前，检查：

1. **工具能力?** → 先看 `llm_toolkit/` 是否已存在
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