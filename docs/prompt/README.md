# docs/prompt 文档说明

本目录保存 Polaris 当前版本的“元设计师 / 无人值守审计修复代理”提示词合同。

## 文件定位

- `元设计师-自动化测试v5.1.md`
  - 当前稳定执行合同
  - 自动修复脚本当前读取此文件
- `元设计师-自动化测试v5.2.md`
  - 当前注释增强版，事实基线与 `v5.1` 相同
- `元设计师-自动化测试v5.md`
  - 历史别名入口，内容已按当前项目对齐
  - 不代表继续兼容旧接口或旧字段
- `元设计师-重复压测项目池.md`
  - Polaris 重复压测专用项目池
  - 与 `v5.1` / `v5.2` 组合使用
- `元设计师-AI代理无头压测.md`
  - Claude / Codex / 通用 AI Agent 无头压测专项合同
  - 与 `v5.1` / `v5.2` + `元设计师-重复压测项目池.md` 组合使用
- `运行run_agent_headless_stress-通用AI代理-独立提示词.md`
  - 完全独立、不依赖其他提示词文档的脚本专用提示词
  - 只用于运行 `scripts/run_agent_headless_stress.py`
  - 失败后要求根因修复 Polaris 并继续重跑
- `运行run_agent_headless_stress-Claude-Gemini直贴版.md`
  - 超短直贴版
  - 适合直接粘贴到 Claude / Gemini 对话框
  - 失败后要求根因修复 Polaris 并继续重跑
- `运行tests.agent_stress.runner-多项目生成压测-通用AI代理-独立提示词.md`
  - `tests.agent_stress.runner` 最新主链收敛版独立提示词
  - 默认执行模式为 `project_serial`（同项目收敛后再切下一个）
  - 支持主链策略开关：`--skip-architect-stage` / `--run-chief-engineer-stage` / `--require-*`
  - 成功标准包含链路证据与真实代码产物，失败后要求修复 Polaris 并重跑
- `运行tests.agent_stress.runner-Claude-Gemini直贴版.md`
  - 可直接粘贴到 Claude / Gemini / Codex 的精简执行版
  - 与主链收敛版保持同一事实基线与命令参数
- `元设计师.md`
  - 通用简版
- `元设计师2.md`
  - 审计优先简版
- `元设计师3.md`
  - 清单版

## 当前统一事实基线

1. Polaris 主执行链仍是 `Architect/Court -> PM -> Director -> QA`，`Chief Engineer` 按需插入
2. `Software Engineering AGI` 已存在，但当前实现层是 `resident`，执行仍桥接 `PM/Director`
3. AGI 当前没有独立 LLM role
4. 主执行栈按当前治理为 `Playwright -> Computer Use`
5. `workspace` 持久化与重启保持已成为一级门禁
6. Core 层角色 LLM 访问只走 `core.llm_toolkit.contracts.ServiceLocator`，不应重新引入 `app.llm.*` 直接依赖
7. 编排共享合同与文件追踪正式位于 `src/backend/core/orchestration/`；app 层只负责注册 role adapter factory
8. 对外 JSON / runtime workflow 合同统一使用任务键 `id`
9. 对外超时与重试字段统一使用 `timeout_seconds`、`global_timeout_seconds`、`initial_interval_seconds`、`max_interval_seconds`
10. Director 任务血缘当前正式证据字段仍是 `metadata.pm_task_id`
11. 只能修改 Polaris，不能手工修改目标项目代码
12. `tests.agent_stress.runner` 当前默认采用 `project_serial` 收敛执行，主链策略为 `(可选 architect) -> PM -> (可选 chief_engineer) -> Director -> QA`
13. AI Agent 高轮次压测中，workspace 自我升级门禁与 backend context 自动发现已由代码 / 脚本层强制执行，提示词不再重复展开低层参数细节
14. 独立审计 LLM 已通过 `src/backend/application/audit_llm_runtime.py` 注入到 TaskService；audit technical role fixed as `qa`, court mapping as `QA / Quality Assurance`
15. 审计策略字段统一为：`audit_llm_enabled`、`audit_llm_role`、`audit_llm_timeout`、`audit_llm_prefer_local_ollama`、`audit_llm_allow_remote_fallback`
16. 审计诊断新增命令：`python src/backend/scripts/audit_cli.py role-info --format json --workspace .`；本地审计一键烟测脚本：`src/backend/scripts/audit_local_only_smoke.ps1`

## 审计 LLM 快速检查（建议每轮开始执行）

```bash
python src/backend/scripts/audit_cli.py role-info --format human --workspace .
```

检查要点：
1. `Tech role` 必须是 `qa`
2. `Court role` must be `QA`
3. 若要求本地模型，`Provider type` 必须是 `ollama`

如需一键配置并验活本地审计链路：

```powershell
powershell -ExecutionPolicy Bypass -NoProfile -File src/backend/scripts/audit_local_only_smoke.ps1 -Workspace . -Model ministral-3:14b
```

如有冲突，以仓库根 `AGENTS.md` 为最高优先级。
