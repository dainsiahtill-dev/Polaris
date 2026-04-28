# Polaris Phase 0: 指标字典与埋点方案

本文档定义 Polaris 各子系统的核心指标、埋点数据 schema 及告警阈值。

## 目录

- [1. PM Metrics](#1-pm-metrics)
- [2. Director Metrics](#2-director-metrics)
- [3. QA Metrics](#3-qa-metrics)
- [4. 记忆检索指标](#4-记忆检索指标)
- [5. 系统指标](#5-系统指标)

---

## 1. PM Metrics

### 1.1 交付成功率

| 属性 | 值 |
|------|-----|
| **指标名称** | `pm.delivery.success_rate` |
| **定义** | integration_qa 一次通过率，即 PM 执行后 QA 首次审核即 PASS 的比例 |
| **计算公式** | `count(where integration_qa.first_attempt == PASS) / count(all integration_qa)` |
| **采样频率** | 每次 PM 执行完成后 |
| **告警阈值** | < 80% 黄色预警，< 60% 红色告警 |
| **数据来源** | `audit_service.AuditVerdict.accepted`, `task_board` 执行记录 |
| **暴露方式** | HTTP API `GET /pm/metrics/delivery-rate` |

### 1.2 评分（Score）

| 属性 | 值 |
|------|-----|
| **指标名称** | `pm.quality.score` |
| **定义** | PM 生成任务的质量评分（0-100） |
| **计算公式** | 基于任务合同四要素（目标、作用域、步骤、验收）完整性加权得分 |
| **采样频率** | 每次任务创建时 |
| **告警阈值** | < 85 分黄色预警，< 70 分红色告警 |
| **数据来源** | `TaskBoard.create_task()` 时生成的质量评估 |
| **暴露方式** | HTTP API `GET /pm/metrics/quality-score` |

### 1.3 Critical Issues 数量

| 属性 | 值 |
|------|-----|
| **指标名称** | `pm.quality.critical_issues` |
| **定义** | PM 任务中的关键缺陷数量 |
| **计算公式** | `sum(evidence_collector.has_critical_issues() for each task)` |
| **采样频率** | 每次 QA 审核完成后 |
| **告警阈值** | > 0 黄色预警，> 2 红色告警 |
| **数据来源** | `EvidencePackage.has_critical_issues()` |
| **暴露方式** | HTTP API `GET /pm/metrics/critical-issues` |

### 1.4 任务完成率

| 属性 | 值 |
|------|-----|
| **指标名称** | `pm.task.completion_rate` |
| **定义** | PM 分配任务的完成比例 |
| **计算公式** | `completed_task_count / total_task_count` |
| **采样频率** | 每次 PM 执行轮次结束后 |
| **告警阈值** | < 90% 黄色预警，< 75% 红色告警 |
| **数据来源** | `TaskBoard` 状态快照 |
| **暴露方式** | HTTP API `GET /pm/metrics/completion-rate` |

### 1.5 任务合同质量

| 属性 | 值 |
|------|-----|
| **指标名称** | `pm.task.contract_quality` |
| **定义** | 任务合同四要素完整性：目标(scope)、作用域(deliverable)、步骤(steps)、验收(acceptance) |
| **计算公式** | 每要素 25 分，满分 100 分 |
| **采样频率** | 每次任务创建时 |
| **告警阈值** | < 80 分黄色预警，< 60 分红色告警 |
| **数据来源** | 任务创建时的结构化解析 |
| **暴露方式** | HTTP API `GET /pm/metrics/contract-quality` |

### 1.6 提示词穿透检测

| 属性 | 值 |
|------|-----|
| **指标名称** | `pm.security.prompt_leakage` |
| **定义** | PM 输出中是否包含提示词泄露关键词 |
| **计算公式** | `any(keyword in output for keyword in ["you are", "role", "system prompt", "no yapping", "提示词", "角色设定", "<thinking>", "<tool_call>"])` |
| **采样频率** | 每次 PM 输出时 |
| **告警阈值** | 任何命中即红色告警 |
| **数据来源** | PM 输出文本分析 |
| **暴露方式** | HTTP API `GET /pm/metrics/security` |

---

## 2. Director Metrics

### 2.1 状态转换闭环

| 属性 | 值 |
|------|-----|
| **指标名称** | `director.status.closed_loop` |
| **定义** | Director 执行状态是否正常从 running=true 转换到 running=false |
| **计算公式** | 轮询 `/v2/director/status`，验证状态机转换完整性 |
| **采样频率** | 每次 Director 执行时（每 2 秒轮询） |
| **告警阈值** | 状态卡在 running > 5 分钟视为异常 |
| **数据来源** | `/v2/director/status` API 响应 |
| **暴露方式** | HTTP API `GET /director/metrics/status` |

### 2.2 PM 任务关联完整性

| 属性 | 值 |
|------|-----|
| **指标名称** | `director.task.pm_association` |
| **定义** | Director 任务与 PM 任务的关联完整性 |
| **计算公式** | `count(tasks where metadata.pm_task_id is not null) / count(all tasks)` |
| **采样频率** | 每次 Director 执行完成后 |
| **告警阈值** | < 100% 黄色预警 |
| **数据来源** | `/v2/director/tasks` API |
| **暴露方式** | HTTP API `GET /director/metrics/association` |

### 2.3 工具调用审计 - Unauthorized 阻断数

| 属性 | 值 |
|------|-----|
| **指标名称** | `director.audit.unauthorized_blocked` |
| **定义** | 未经授权的工具调用被阻断的次数 |
| **计算公式** | `sum(1 for call in tool_calls where call.unauthorized == true)` |
| **采样频率** | 每次工具调用时 |
| **告警阈值** | > 0 黄色预警，> 5 红色告警 |
| **数据来源** | `RoleToolGateway.execute()` 返回的 `unauthorized` 字段 |
| **暴露方式** | HTTP API `GET /director/metrics/unauthorized` |

### 2.4 工具调用审计 - Dangerous Command 拦截数

| 属性 | 值 |
|------|-----|
| **指标名称** | `director.audit.dangerous_command_blocked` |
| **定义** | 危险命令（路径穿越、恶意操作）被拦截的次数 |
| **计算公式** | `sum(1 for call in tool_calls where call.dangerous == true)` |
| **采样频率** | 每次工具调用时 |
| **告警阈值** | > 0 黄色预警 |
| **数据来源** | `RoleToolGateway._is_dangerous_command()`, `RoleToolGateway._is_path_traversal()` |
| **暴露方式** | HTTP API `GET /director/metrics/dangerous-commands` |

### 2.5 工具调用总数

| 属性 | 值 |
|------|-----|
| **指标名称** | `director.tool.total_calls` |
| **定义** | Director 执行期间的工具调用总次数 |
| **计算公式** | `count(all tool_calls)` |
| **采样频率** | 每次 Director 执行完成后 |
| **告警阈值** | 无（仅监控） |
| **数据来源** | `AgentAccelToolExecutor` 执行日志 |
| **暴露方式** | HTTP API `GET /director/metrics/tool-calls` |

### 2.6 工具调用成功率

| 属性 | 值 |
|------|-----|
| **指标名称** | `director.tool.success_rate` |
| **定义** | 工具调用成功率 |
| **计算公式** | `count(where tool_result.status == success) / count(all tool_calls)` |
| **采样频率** | 每次工具调用完成后 |
| **告警阈值** | < 90% 黄色预警，< 75% 红色告警 |
| **数据来源** | `AgentAccelToolExecutor` 执行结果 |
| **暴露方式** | HTTP API `GET /director/metrics/tool-success-rate` |

---

## 3. QA Metrics

### 3.1 Integration QA 通过率

| 属性 | 值 |
|------|-----|
| **指标名称** | `qa.integration.pass_rate` |
| **定义** | Integration QA 整体通过率 |
| **计算公式** | `count(where verdict.accepted == true) / count(all verdicts)` |
| **采样频率** | 每次 QA 审核完成后 |
| **告警阈值** | < 95% 黄色预警，< 85% 红色告警 |
| **数据来源** | `IndependentAuditService.run_audit()` 返回的 `AuditVerdict` |
| **暴露方式** | HTTP API `GET /qa/metrics/pass-rate` |

### 3.2 审计结果分布

| 属性 | 值 |
|------|-----|
| **指标名称** | `qa.audit.result_distribution` |
| **定义** | QA 审核结果的三态分布（PASS/FAIL/INCONCLUSIVE） |
| **计算公式** | 统计 `AuditVerdict.accepted` 的三种状态数量及比例 |
| **采样频率** | 每次 QA 审核完成后 |
| **告警阈值** | inconclusive > 20% 黄色预警 |
| **数据来源** | `IndependentAuditService.get_stats()` |
| **暴露方式** | HTTP API `GET /qa/metrics/distribution` |

### 3.3 QA 审核延迟

| 属性 | 值 |
|------|-----|
| **指标名称** | `qa.performance.latency_ms` |
| **定义** | QA 审核从发起到完成的总耗时 |
| **计算公式** | `audit_end_timestamp - audit_start_timestamp` |
| **采样频率** | 每次 QA 审核完成后 |
| **告警阈值** | > 60000ms（1分钟）黄色预警，> 120000ms 红色告警 |
| **数据来源** | `IndependentAuditService.run_audit()` 内部计时 |
| **暴露方式** | HTTP API `GET /qa/metrics/latency` |

### 3.4 缺陷票据生成率

| 属性 | 值 |
|------|-----|
| **指标名称** | `qa.defect.ticket_generation_rate` |
| **定义** | QA 审核失败时生成缺陷票据的比例 |
| **计算公式** | `count(where verdict.defect_ticket is not empty) / count(where verdict.accepted == false)` |
| **采样频率** | 每次 QA 审核完成后 |
| **告警阈值** | < 90% 黄色预警 |
| **数据来源** | `AuditVerdict.defect_ticket` |
| **暴露方式** | HTTP API `GET /qa/metrics/ticket-rate` |

---

## 4. 记忆检索指标

### 4.1 检索延迟

| 属性 | 值 |
|------|-----|
| **指标名称** | `memory.search.latency_ms` |
| **定义** | 记忆检索从请求到返回的耗时 |
| **计算公式** | `response_timestamp - request_timestamp` |
| **采样频率** | 每次检索请求时 |
| **告警阈值** | > 500ms 黄色预警，> 2000ms 红色告警 |
| **数据来源** | `ContextEngine.search()` 内部计时 |
| **暴露方式** | HTTP API `GET /memory/metrics/latency` |

### 4.2 NDCG@10

| 属性 | 值 |
|------|-----|
| **指标名称** | `memory.search.ndcg_at_10` |
| **定义** | 检索结果的前 10 项归一化折损累积增益 |
| **计算公式** | `DCG@10 / IDCG@10`，其中 DCG = sum(relevance_i / log2(i+2)) |
| **采样频率** | 每日（基于评测集） |
| **告警阈值** | < 0.7 黄色预警，< 0.5 红色告警 |
| **数据来源** | `ContextEngine` 评测集（需预先构建） |
| **暴露方式** | HTTP API `GET /memory/metrics/ndcg` |

### 4.3 召回率

| 属性 | 值 |
|------|-----|
| **指标名称** | `memory.search.recall` |
| **定义** | 相关文档被检索到的比例 |
| **计算公式** | `retrieved_relevant / total_relevant` |
| **采样频率** | 每日（基于评测集） |
| **告警阈值** | < 0.8 黄色预警，< 0.6 红色告警 |
| **数据来源** | `ContextEngine` 评测集 |
| **暴露方式** | HTTP API `GET /memory/metrics/recall` |

### 4.4 记忆存储增长率

| 属性 | 值 |
|------|-----|
| **指标名称** | `memory.storage.growth_rate` |
| **定义** | 记忆存储的日增长率 |
| **计算公式** | `(current_size - previous_size) / previous_size` |
| **采样频率** | 每日 |
| **告警阈值** | > 50% 黄色预警（异常增长检测） |
| **数据来源** | `MemoryStore.size()`, `ReflectionStore.size()` |
| **暴露方式** | HTTP API `GET /memory/metrics/growth` |

---

## 5. 系统指标

### 5.1 API 响应延迟

| 属性 | 值 |
|------|-----|
| **指标名称** | `system.api.latency_ms` |
| **定义** | HTTP API 端到端响应延迟 |
| **计算公式** | `response_timestamp - request_timestamp` |
| **采样频率** | 每个 API 请求 |
| **告警阈值** | P99 > 2000ms 黄色预警，P99 > 5000ms 红色告警 |
| **数据来源** | FastAPI 中间件/日志 |
| **暴露方式** | HTTP API `GET /system/metrics/api-latency` |

### 5.2 并发任务数

| 属性 | 值 |
|------|-----|
| **指标名称** | `system.concurrency.active_tasks` |
| **定义** | 当前活跃的 PM/Director 任务数 |
| **计算公式** | `count(tasks where status == RUNNING)` |
| **采样频率** | 每 10 秒 |
| **告警阈值** | > 10 黄色预警，> 20 红色告警 |
| **数据来源** | `TaskBoard` + `Director` 状态 |
| **暴露方式** | HTTP API `GET /system/metrics/concurrency` |

### 5.3 错误率

| 属性 | 值 |
|------|-----|
| **指标名称** | `system.error.rate` |
| **定义** | API 请求错误率（5xx + 4xx） |
| **计算公式** | `error_requests / total_requests` |
| **采样频率** | 每分钟 |
| **告警阈值** | > 5% 黄色预警，> 10% 红色告警 |
| **数据来源** | FastAPI 日志 |
| **暴露方式** | HTTP API `GET /system/metrics/error-rate` |

### 5.4 Worker 队列积压

| 属性 | 值 |
|------|-----|
| **指标名称** | `system.worker.backlog` |
| **定义** | Worker 任务队列积压数量 |
| **计算公式** | `queue.size()` |
| **采样频率** | 每 30 秒 |
| **告警阈值** | > 100 黄色预警，> 500 红色告警 |
| **数据来源** | `WorkerExecutor` 队列状态 |
| **暴露方式** | HTTP API `GET /system/metrics/worker-backlog` |

### 5.5 LLM 调用延迟

| 属性 | 值 |
|------|-----|
| **指标名称** | `system.llm.latency_ms` |
| **定义** | LLM 提供商调用延迟 |
| **计算公式** | `llm_response_timestamp - llm_request_timestamp` |
| **采样频率** | 每次 LLM 调用 |
| **告警阈值** | > 30000ms 黄色预警，> 60000ms 红色告警 |
| **数据来源** | `ProviderManager` 调用日志 |
| **暴露方式** | HTTP API `GET /system/metrics/llm-latency` |

### 5.6 LLM 调用成本

| 属性 | 值 |
|------|-----|
| **指标名称** | `system.llm.cost_usd` |
| **定义** | LLM 调用的累计成本（美元） |
| **计算公式** | `sum(token_count * token_price for each call)` |
| **采样频率** | 每小时 |
| **告警阈值** | > 10 美元/小时黄色预警，> 50 美元/小时红色告警 |
| **数据来源** | `ProviderManager` 计费日志 |
| **暴露方式** | HTTP API `GET /system/metrics/llm-cost` |

---

## 附录 A：指标暴露 API 汇总

| 端点 | 返回指标 |
|------|----------|
| `GET /pm/metrics/delivery-rate` | 交付成功率 |
| `GET /pm/metrics/quality-score` | 评分 |
| `GET /pm/metrics/critical-issues` | Critical Issues |
| `GET /pm/metrics/completion-rate` | 任务完成率 |
| `GET /pm/metrics/contract-quality` | 任务合同质量 |
| `GET /pm/metrics/security` | 提示词穿透 |
| `GET /director/metrics/status` | 状态闭环 |
| `GET /director/metrics/association` | PM 关联 |
| `GET /director/metrics/unauthorized` | Unauthorized 阻断 |
| `GET /director/metrics/dangerous-commands` | Dangerous Command 拦截 |
| `GET /director/metrics/tool-calls` | 工具调用总数 |
| `GET /director/metrics/tool-success-rate` | 工具成功率 |
| `GET /qa/metrics/pass-rate` | QA 通过率 |
| `GET /qa/metrics/distribution` | 审计分布 |
| `GET /qa/metrics/latency` | QA 延迟 |
| `GET /qa/metrics/ticket-rate` | 缺陷票据率 |
| `GET /memory/metrics/latency` | 检索延迟 |
| `GET /memory/metrics/ndcg` | NDCG@10 |
| `GET /memory/metrics/recall` | 召回率 |
| `GET /memory/metrics/growth` | 存储增长 |
| `GET /system/metrics/api-latency` | API 延迟 |
| `GET /system/metrics/concurrency` | 并发数 |
| `GET /system/metrics/error-rate` | 错误率 |
| `GET /system/metrics/worker-backlog` | 队列积压 |
| `GET /system/metrics/llm-latency` | LLM 延迟 |
| `GET /system/metrics/llm-cost` | LLM 成本 |

---

## 附录 B：告警级别定义

| 级别 | 触发条件 | 通知方式 | 示例 |
|------|----------|----------|------|
| **INFO** | 正常业务数据 | 不通知 | 通过率 > 95% |
| **WARNING (黄色)** | 轻微异常 | 记录日志 | 通过率 80-95% |
| **ERROR (红色)** | 严重异常 | 告警通知 | 通过率 < 60% |

---

## 附录 C：版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-03-04 | 初始版本 |
