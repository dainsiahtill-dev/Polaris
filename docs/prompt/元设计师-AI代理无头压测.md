# Polaris 元设计师 AI 代理无头压测提示词

本文件是 `docs/prompt/元设计师-自动化测试v5.1.md` 的专项补充，适用于 `Claude`、`Codex`、通用 AI Agent 这类代理执行链的无头压测。

## 使用方式

1. 基础治理、门禁、修复循环仍以 `docs/prompt/元设计师-自动化测试v5.1.md` 为总合同。
2. 项目题目池使用 `docs/prompt/元设计师-重复压测项目池.md`。
3. 这份提示词只负责把压测主链从“浏览器 UI 驱动”收敛到“后端正式接口驱动”。
4. Playwright 在这个场景里只保留少量 UI 冒烟，不再作为高频压测主入口。

## 目标定位

你不是在做普通性能压测，也不是在压 REST QPS。

你要验证的是：

- Polaris 的 `Architect/Court -> PM -> Director -> QA` AI 代理链是否稳定
- Claude / Codex 这类代理在 Polaris 里是否持续输出有效任务合同与执行结果
- runtime WebSocket、Factory SSE、PM 合同、Director 血缘、QA 结果是否一致
- 当链路失败时，Polaris 能否自行定位根因、修复并回归

## 当前正式无头执行面

控制面只使用当前正式接口：

- `POST /settings`
- `GET /runtime/storage-layout`
- `POST /v2/factory/runs`
- `GET /v2/factory/runs/{run_id}`
- `GET /v2/factory/runs/{run_id}/events`
- `GET /v2/factory/runs/{run_id}/artifacts`
- `GET /state/snapshot`
- `GET /v2/director/tasks`
- `GET /v2/role/{role}/chat/status`

实时观测面只使用：

- `GET /v2/factory/runs/{run_id}/stream` 作为 Factory SSE
- `WS /v2/ws/runtime` 作为统一 runtime 实时流

说明：

- 不要再为本场景新增旧兼容接口或旁路接口
- 压测脚本只能通过 Polaris 现有正式接口驱动；不得替 Polaris 预写目标项目代码、文档、配置、AGENTS、runtime 工件
- 允许的外部准备动作仅限：创建一个空目标目录作为 workspace 容器，以及把审计报告写到独立 report 目录
- backend context 自动发现与 workspace/self-upgrade 门禁由脚本实现负责，不需要在提示词里重复展开低层参数细节

## 当前推荐执行入口

```bash
python scripts/run_agent_headless_stress.py --agent-label codex
```

如果你在压 Claude，则把 `--agent-label` 换成 `claude`。

## 必过门禁

1. 角色就绪门禁
   - `architect`、`pm`、`director`、`qa` 必须在 `/v2/role/{role}/chat/status` 返回 `ready=true`
2. Factory 主链门禁
   - 必须通过 `/v2/factory/runs` 创建 run
   - Factory SSE 必须看到 `done` 事件
3. Runtime 可观测性门禁
   - `/v2/ws/runtime` 必须收到有效消息
   - 不允许整轮无 status / process / runtime 观测
4. PM 质量门禁
   - `runtime/contracts/pm_tasks.contract.json` 中 `quality_gate.score >= 80`
   - `critical_issue_count = 0`
   - 每个任务必须具备目标、作用域、执行清单、可测验收
   - 对外任务键只认 `id`
5. Director 血缘门禁
   - `/v2/director/tasks[*].metadata.pm_task_id` 必须存在有效关联
6. QA 门禁
   - `runtime/results/integration_qa.result.json.reason == integration_qa_passed`
7. 泄漏门禁
   - PM 合同、计划文档、摘要中不得出现：
     - `you are`
     - `role`
     - `system prompt`
     - `no yapping`
     - `提示词`
     - `角色设定`
     - `<thinking>`
     - `<tool_call>`

## 证据要求

每轮都必须落 UTF-8 审计包，至少包含：

- 本轮 directive
- Factory events
- Factory artifacts
- snapshot
- director tasks
- pm contract
- qa result
- runtime 事件文件路径
- runtime WebSocket 统计
- Factory SSE 统计

## 失败后的动作

如果某轮失败，必须明确说明：

1. 是角色未就绪、PM 合同失真、Director 断链、runtime 观测缺失，还是 QA 不通过
2. 证据文件在哪
3. 根因是什么
4. 修复后是否重跑成功

禁止用“重跑一次试试”代替根因分析。

## 输出补充

最终 JSON 审计包除了 `v5.1` 的字段外，至少再补这些字段：

```json
{
  "mode": "ai_agent_headless_stress",
  "agent_label": "codex",
  "required_roles": ["architect", "pm", "director", "qa"],
  "stress_rounds": [],
  "coverage_summary": {},
  "next_risks": []
}
```

如果是 Claude / Codex 对比压测，必须分别输出独立报告，不能把不同代理的结果混在一个 run 里。 
