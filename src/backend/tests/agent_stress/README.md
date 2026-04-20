# Polaris AI Agent 专项压测框架

针对 Polaris Factory 主链的自动化多项目压测系统。

运行方式：

```bash
python -m tests.agent_stress.runner
```

核心约束：

- **纯黑盒**：压测框架只通过 Polaris HTTP API 与 backend 交互，不调用内部 CLI，不直接操作文件
- **自动自举**：若本地 backend 未运行，框架会自动启动一个临时 backend 进程，压测结束后关闭
- **路径策略由 `core.stress_path_policy` 统一管理**，不得绕过

---

## 快速开始

### 1. 角色可用性探针（压测前必做）

```bash
# 仅运行探针
python -m tests.agent_stress.probe

# 输出 JSON 报告
python -m tests.agent_stress.probe --json -o probe_report.json
```

### 2. 完整压测

```bash
# 运行 3 轮（推荐每批 3 轮，批后审计）
python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --rounds 3

# 指定类别
python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --category crud,security

# 从指定轮次恢复
python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --resume-from 5

# 切换选择策略
python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --strategy complexity_asc
```

### 2.5 单角色工具习惯压测

用于只跑一个角色的工具调用习惯，不必每次都重跑整条 Factory 主链：

```bash
# 默认会创建 fresh workspace，自动自举 backend，并输出 JSON/Markdown 报告
python -m tests.agent_stress.role_tool_habits --role qa

# 自定义 prompt 语料
python -m tests.agent_stress.role_tool_habits \
  --role director \
  --prompt-file C:/Temp/role_tool_prompts.json
```

输出会包含：

- 流式 `tool_call` / `tool_result` 事件
- 常见 LLM 习惯分类（如 `key -> query`、`ls/tree/find`、markdown 噪声、shell operator）
- 失败类别汇总，便于继续补归一化

### 3. 人类观测终端（默认随 runner 启动）

```bash
# runner 会自动拉起观测窗口并执行压测
python -m tests.agent_stress.runner --workspace C:/Temp/agent-stress-workspace --rounds 3

# 独立启动观测器
python -m tests.agent_stress.observer --workspace C:/Temp/agent-stress-workspace --rounds 3
```

---

## Backend 上下文解析

框架按以下优先级解析 backend URL 和 token，无需手动配置端口：

1. **CLI 参数**：`--backend-url` / `--token`（最高优先级）
2. **环境变量**：`POLARIS_BASE_URL` / `POLARIS_TOKEN`
3. **Desktop backend info 文件**：`~/.polaris/runtime/desktop-backend.json`（Electron 桌面版写入）
4. **自动自举**：若以上均无效，`backend_bootstrap.py` 会自动启动临时 backend 进程

自动自举使用随机端口（`--port 0`）和临时 token，确保每次压测隔离。
自举成功后会将 URL/token 写回 `desktop-backend.json` 供其他工具感知。

---

## 路径约束

路径策略由 `core.stress_path_policy` 集中管理：

- **workspace**：默认由 `default_stress_workspace_base()` 生成，可通过 `--workspace` 覆盖
- **runtime root（ramdisk）**：默认由 `default_stress_runtime_root()` 生成，可通过 `--ramdisk-root` 覆盖
- **策略违规检查**：`runtime_layout_policy_violations()` 在运行前校验路径合法性

不要在代码中硬编码路径（如 `C:/Temp/` 或 `X:/`）；所有路径都应通过策略函数获取。

---

## 项目池

覆盖 6 个类别、20 个日常练手型项目：

| 类别 | 项目 |
|------|------|
| CRUD / 表单 | 个人记账簿、待办事项清单、博客系统、个人简历生成器 |
| 实时通信 | 实时聊天室 (WebSocket)、在线剪贴板 |
| 编辑器 / 内容 | Markdown 编辑器、静态网站生成器、RSS 阅读器 |
| 工具 / 自动化 | 天气预报、抽奖工具、图片占位符生成器、聚合搜索、单位转换器、自动签到、截图工具 |
| 安全 / 文件 | 密码管理器、文件断点续传器 |
| 互动 / 游戏 | 番茄钟、贪吃蛇/俄罗斯方块 |

**rotation 策略**：前 20 轮完整覆盖所有项目各一次，第 21 轮起循环。

---

## 输出报告

```
stress_reports/
├── probe_report.json                    # 角色探针报告
├── stress_results.json                  # 中间结果（支持恢复）
├── stress_audit_package.json            # 完整审计包
├── stress_report.md                     # Markdown 报告
├── summary.txt                          # 执行摘要
├── human_observer.log                   # 观测器控制台原始日志
└── diagnostics/
    ├── round_N_observability.json       # 运行时可观测性数据
    └── round_N_diagnostic.json          # 失败诊断报告（仅失败轮次）
```

### 诊断报告结构（`round_N_diagnostic.json`）

```json
{
  "round_number": 3,
  "project_name": "密码管理器",
  "failure_category": "llm_timeout",
  "failure_point": "director",
  "root_cause_analysis": "...",
  "suggested_fixes": ["..."],
  "evidence": [...],
  "related_logs": ["..."],
  "raw_api_responses": {}
}
```

### 审计包结构（`stress_audit_package.json`）

```json
{
  "schema_version": "1.0.0",
  "stress_test_id": "stress_20250308_120000",
  "run_state": "initialized|running|completed|aborted",
  "audit_package_health": {
    "score": 0,
    "issues": []
  },
  "probe_report": {},
  "project_results": [
    {
      "round": 1,
      "project_id": "expense-tracker",
      "overall_result": "PASS",
      "workspace": "C:/Temp/agent-stress-workspace/projects/expense-tracker"
    }
  ],
  "runtime_forensics": {
    "summary": {
      "total_factory_runs": 1,
      "in_progress_runs": 0
    }
  },
  "artifact_integrity": {
    "missing_required_core_artifacts": [],
    "stage_artifacts": {
      "checked": 4,
      "missing": 0
    }
  },
  "stress_rounds": [
    {
      "round": 1,
      "project_name": "个人记账簿",
      "category": "crud",
      "complexity": 2,
      "result": "PASS|FAIL|PARTIAL",
      "stages": {
        "architect": "success",
        "pm": "success",
        "chief_engineer": "skipped",
        "director": "success",
        "qa": "partial"
      },
      "failure_point": "director",
      "root_cause": "...",
      "evidence": "..."
    }
  ],
  "coverage_summary": {
    "categories_covered": ["crud", "realtime"],
    "projects_completed": 15,
    "projects_failed": 5
  },
  "failure_analysis": {
    "director": 3,
    "pm": 1,
    "qa": 1
  }
}
```

---

## 架构

**核心原则：只通过 Polaris HTTP API 交互，不直接操作文件，不调用内部 CLI。**

### 模块列表

```
tests/agent_stress/
├── __init__.py              # 包初始化
├── backend_context.py       # backend URL/token 解析（env → desktop-backend.json → unresolved）
├── backend_bootstrap.py     # backend 自举器（自动启动临时 backend 进程）
├── preflight.py             # backend 预检（区分：不可达 / 鉴权失败 / settings 不可用）
├── contracts.py             # HTTP API 合同辅助函数（字段归一化、阶段推断）
├── project_pool.py          # 项目池定义（6 类 20 项目）
├── probe.py                 # 角色 LLM 可用性探针
├── tracer.py                # 运行时追踪器（任务血缘、事件流）
├── observability.py         # 可观测性收集器（AI Agent 诊断数据）
├── engine.py                # 压测执行引擎
├── runner.py                # 主运行器（CLI 入口）
├── observer.py              # 观测器包装层（拉起独立控制台）
├── observer/                # 人类观测终端（Rich TUI）
│   ├── main.py              # 观测器主逻辑
│   ├── cli.py               # 观测器 CLI 参数
│   ├── state.py             # 观测器状态模型
│   ├── projection.py        # 状态投影（从 API 数据更新观测状态）
│   ├── renderers.py         # Rich 渲染器
│   └── constants.py         # 常量
└── test_*.py                # 框架自测（backend bootstrap、observer projection）
```

### 使用的 Polaris API

| 模块 | 方法 + 路径 | 用途 |
|------|------------|------|
| `preflight.py` | `GET /health` | backend 可达性检查（无鉴权） |
| `preflight.py` | `GET /settings` | settings 可用性 + 鉴权验证 |
| `probe.py` | `GET /v2/role/{role}/chat/status` | 角色 LLM 可用性探针 |
| `probe.py` | `GET /llm/status` | Provider 全局健康检查 |
| `engine.py` | `POST /v2/factory/runs` | 创建端到端 Factory 运行 |
| `engine.py` | `GET /v2/factory/runs/{id}` | 轮询 Factory 运行状态 |
| `engine.py` | `GET /v2/factory/runs/{id}/events` | 获取运行时事件 |
| `tracer.py` | `GET /v2/director/tasks` | 任务血缘追踪 |

### 数据流

```
runner.py
    ├── backend_bootstrap.py   # 解析或自举 backend（自动）
    ├── preflight.py           # 验证 backend 可达且鉴权有效
    ├── probe.py               # 验证所有角色 LLM 可用
    ├── engine.py              # 驱动 Factory 端到端运行（HTTP API）
    │       └── POST /v2/factory/runs
    │           └── 轮询 GET /v2/factory/runs/{id}
    ├── tracer.py              # 追踪任务血缘与运行时事件
    └── observability.py       # 收集诊断数据，生成 AI Agent 友好报告
```

---

## 失败分类

框架自动识别以下失效环节：

| 失效环节 | 可能原因 |
|----------|----------|
| `architect` | 架构设计阶段 LLM 输出格式不符合预期 |
| `pm` | PM 任务分解失败或输出格式错误 |
| `chief_engineer` | 技术分析阶段未能生成有效施工蓝图 |
| `director` | 代码执行阶段失败（补丁应用错误或运行时异常） |
| `qa` | QA 审查发现严重质量问题 |
| `llm_failure` | LLM 调用失败（模型不可用或超时） |
| `runtime_error` | 运行时异常（系统资源不足或配置错误） |

---

## 轮次规则

1. 不得连续两轮都只做同一类型的简单 CRUD 项目
2. 每轮项目必须满足复杂度门槛，并额外具备至少 2 个增强特性
3. 重复压测同一项目主题时，下一轮必须显式升阶
4. 每轮记录：项目名称、类别、增强特性、失败点、修复动作、回归结果

---

## 环境要求

- Python 3.10+
- Polaris Backend 可访问（或让框架自动自举）
- 若未传 `--backend-url`/`--token`，框架优先使用当前执行环境的 backend context
- 足够的磁盘空间（每轮约 50-100MB）
- 稳定的 LLM 服务

---

## 注意事项

1. **压测前必须运行探针**，确保所有角色 LLM 可用
2. **不要在生产环境运行**，压测会创建大量文件
3. **LLM 配置只读**，框架不修改任何 LLM 配置
4. **纯黑盒**，框架只用 Polaris HTTP API，不调用内部模块

---

## 扩展

### 添加新项目

在 `project_pool.py` 中添加新的 `ProjectDefinition`：

```python
ProjectDefinition(
    id="my-project",
    name="我的项目",
    category=ProjectCategory.CRUD,
    description="核心能力描述",
    enhancements=[Enhancement.PERSISTENCE, Enhancement.UNIT_TEST],
    stress_focus=["压测重点1", "压测重点2"],
    complexity_level=3,
)
```

### 自定义探针逻辑

在 `probe.py` 中扩展 `RoleAvailabilityProbe` 类。

### 添加新追踪指标

在 `tracer.py` 中扩展 `RuntimeTracer` 类。
