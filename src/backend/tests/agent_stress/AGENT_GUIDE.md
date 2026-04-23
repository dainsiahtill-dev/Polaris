# AI Agent 使用指南 - Polaris 压测诊断

本文档帮助 Claude/Gemini 等 AI Agent 使用压测框架生成的诊断数据来定位和修复 Polaris 问题。

**重要提示**：
- 所有调试动作必须基于当前有效的 backend context；不要假设固定端口可用
- backend 已停止时，接口超时不是压测框架 bug
- backend URL 和 token 从 `backend_context.py` 解析，或由 `backend_bootstrap.py` 自动自举

---

## 1. 获取 Backend 上下文

在执行任何调试命令之前，先确认 backend URL 和 token：

```bash
# 方式一：查看 desktop-backend.json（Electron 桌面版或自举时写入）
cat %APPDATA%/.polaris/runtime/desktop-backend.json

# 方式二：通过环境变量
echo $KERNELONE_BASE_URL
echo $KERNELONE_TOKEN

# 方式三：运行预检，确认 backend 可达
python -c "
import asyncio
from tests.agent_stress.preflight import BackendPreflightProbe
from tests.agent_stress.backend_context import resolve_backend_context

ctx = resolve_backend_context()
print('URL:', ctx.backend_url, '| Source:', ctx.source)
"
```

---

## 2. 快速定位问题

### 2.1 查看执行摘要

```
总轮次: 20
通过: 15 (75.0%)
失败: 5 (25.0%)

主要失败点:
  - director: 3 次
  - pm: 2 次
```

### 2.2 查看失败轮次的诊断报告

`stress_reports/diagnostics/round_{N}_diagnostic.json`：

```json
{
  "round_number": 3,
  "project_name": "密码管理器",
  "failure_category": "llm_timeout",
  "failure_point": "director",
  "root_cause_analysis": "Phase 'director' timed out waiting for LLM response...",
  "suggested_fixes": [
    "Increase timeout settings",
    "Reduce prompt complexity",
    "Switch to faster model"
  ],
  "evidence": [...],
  "related_logs": [
    "Polaris backend logs",
    "Factory run details: GET /v2/factory/runs/{id}"
  ]
}
```

### 2.3 查看可观测性数据

`stress_reports/diagnostics/round_{N}_observability.json`：

- `llm_calls`：LLM 调用历史（请求/响应/token 使用/延迟）
- `stage_transitions`：阶段转换记录
- `tool_executions`：工具执行记录
- `error_events`：错误事件列表

---

## 3. 失败分类与修复策略

### LLM_UNAVAILABLE

**特征**：LLM 服务返回 503/502 或连接错误

**修复步骤**：
1. 用探针确认角色 LLM 可用：`python -m tests.agent_stress.probe`
2. 检查 Polaris 的 Provider 配置：`GET /llm/status`
3. 验证 API Key 是否过期

### LLM_TIMEOUT

**特征**：LLM 调用超时（默认 60s+）

**修复步骤**：
1. 检查超时配置：`polaris/application/roles/` 中对应角色的配置
2. 若 prompt 过长，优化角色 prompt 模板
3. 考虑切换更快的模型

### LLM_FORMAT_ERROR

**特征**：LLM 输出无法解析为 JSON 或不符合预期格式

**修复步骤**：
1. 查看 `evidence.failed_llm_calls[0].response_preview` 中的原始输出
2. 增强 prompt 中的格式约束
3. 在输出解析处添加更健壮的错误处理

位置：`polaris/application/roles/` 对应角色 → `app/llm/usecases/role_dialogue.py`（当前运行主实现）

### TOOL_EXECUTION_FAILED

**特征**：工具（git/python/bash）执行失败

**修复步骤**：
1. 查看 `evidence.failed_tool_executions` 中的错误信息
2. 检查工具依赖是否安装
3. 验证工具权限配置

### TASK_DESERIALIZATION_FAILED

**特征**：PM/Director 的输出无法解析为任务

**修复步骤**：
1. 查看 `evidence.failed_llm_calls` 中的 LLM 输出
2. 检查任务 schema 是否变化
3. 增强反序列化逻辑的错误处理

### WORKFLOW_EXECUTION_ERROR

**特征**：Factory/工作流状态机错误

**修复步骤**：
1. 查看 `raw_api_responses.factory_status` 中的详细状态
2. 用事件流追踪阶段转换：`GET /v2/factory/runs/{id}/events`
3. 检查 `polaris/application/services/factory_run_service.py`

---

## 4. 常用调试命令

```bash
# 预检 backend（区分不可达/鉴权失败/settings不可用）
python -c "
import asyncio
from tests.agent_stress.preflight import BackendPreflightProbe
from tests.agent_stress.backend_context import resolve_backend_context

async def main():
    ctx = resolve_backend_context()
    async with BackendPreflightProbe(ctx.backend_url, ctx.token) as probe:
        r = await probe.run()
    print(r.status, r.latency_ms, 'ms')

asyncio.run(main())
"

# 角色探针（检查所有角色 LLM 是否可用）
python -m tests.agent_stress.probe

# 查询特定 Factory run 状态（将 <BASE> 和 <TOKEN> 替换为实际值）
curl -H "Authorization: Bearer <TOKEN>" <BASE>/v2/factory/runs/<run_id>

# 查询运行时事件流
curl -H "Authorization: Bearer <TOKEN>" <BASE>/v2/factory/runs/<run_id>/events

# 查询 Director 任务列表
curl -H "Authorization: Bearer <TOKEN>" <BASE>/v2/director/tasks

# 查询 LLM/Provider 状态
curl -H "Authorization: Bearer <TOKEN>" <BASE>/llm/status

# 查询角色 LLM 可用性
curl -H "Authorization: Bearer <TOKEN>" <BASE>/v2/role/director/chat/status
```

---

## 5. 诊断示例

### 示例 1：Director 阶段 LLM 格式错误

**问题**：Round #5 在 director 阶段失败

1. 打开 `diagnostics/round_5_diagnostic.json`
2. `failure_category` 是 `llm_format_error`
3. 查看 `evidence.failed_llm_calls[0].response_preview`：
   ```
   I'll help you implement the login feature. First, let me check...
   ```
   LLM 输出了自然语言而非 JSON。

**修复**：在 `app/llm/usecases/role_dialogue.py` 的 director 角色 prompt 中增强格式约束。

### 示例 2：PM 阶段任务为空

**问题**：Round #3 pm 阶段成功但没有生成任务

1. 查看 `round_3_observability.json` 中的 `llm_calls`
2. 最后一次 LLM 调用成功但返回了空任务列表
3. 查看 `raw_snapshots[0].director_tasks` 为空

**修复**：
- 检查 PM prompt 是否正确传递了需求
- 增强 PM 的约束，确保至少生成一个任务

---

## 6. 验证修复

修复问题后，运行单轮压测验证：

```bash
python -m tests.agent_stress.runner \
  --workspace C:/Temp/stress-verify \
  --rounds 1 \
  --category crud
```

验证通过后，更新 `stress_report.md` 中的回归结果。

---

## 7. 框架模块速查

| 模块 | 职责 |
|------|------|
| `backend_context.py` | 解析 backend URL/token（env / desktop-backend.json） |
| `backend_bootstrap.py` | 自动启动临时 backend 进程（自举） |
| `preflight.py` | backend 预检（可达性 / 鉴权 / settings） |
| `contracts.py` | HTTP API 字段归一化、阶段推断、失败证据提取 |
| `probe.py` | 角色 LLM 可用性探针 |
| `tracer.py` | 任务血缘与运行时事件追踪 |
| `observability.py` | 诊断数据收集与报告生成 |
| `engine.py` | Factory 端到端运行驱动 |
