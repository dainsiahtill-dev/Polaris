# ADR-026: Sequential-Thinking 内核化与状态边界

## 1. 状态（已采纳/日期/作者）

- 日期：2026-03-11
- 状态：已采纳
- 作者：Codex（Polaris vNext 升级）

## 2. 背景（为何不能继续分散循环）

Polaris 在角色执行链存在多处回合推进逻辑（内核与外围适配层并存），造成以下问题：

1. 重复循环导致预算不可控，出现重复工具调用与无效重试。
2. sequential 行为缺少统一契约，无法稳定解释终止原因与收敛路径。
3. 状态写入边界不清晰，存在污染 workflow/taskboard 主状态字段风险。
4. 观测链路缺少 sequential 专项投影，导致压测失败归因成本高。

继续维持分散循环会加剧 workflow 与 taskboard 的耦合冲突，阻碍稳定迭代。

## 3. 决策（内核唯一回合驱动、子状态边界、保留字约束、投影策略）

本 ADR 采纳以下不可逆架构决策：

1. 内核唯一回合驱动：
   - `RoleExecutionKernel` 接入统一 `SequentialEngine`，成为唯一回合推进器。
   - `role_dialogue` 与 `workflow_adapter.execute_role_with_tools` 不再外层循环，只透传内核回合结果。

2. 子状态边界：
   - sequential 仅允许写 `metadata.seq.*`。
   - 不得改写 workflow/taskboard 主状态字段。

3. 保留字约束：
   - 禁写字段：`phase/status/retry_count/max_retries/completed_phases/workflow_state`。
   - 违规写入必须触发 `seq.reserved_key_violation`，并拒绝写入。

4. 统一预算与终止规则：
   - 预算参数固定为 `max_steps/max_tool_calls_total/max_no_progress_steps/max_wall_time_seconds`。
   - 终止原因必须写入 `sequential_stats.termination_reason`，预算耗尽需明确 `budget_exhausted=true`。

5. 投影策略：
   - 新增 `seq.*` 事件接入既有 WS/SSE runtime 通道。
   - observer 增加 `sequential_trace` 面板。
   - `projection-focus` 扩展为 `llm|seq|all`，默认 `all`。

6. 角色启用策略：
   - vNext 默认仅 `director + adaptive` 启用 sequential。
   - 其他角色默认 disabled，通过配置显式打开。

## 4. 后果（正面/负面）

正面后果：

1. 回合控制收敛，消除双重循环引起的行为漂移。
2. 主状态机边界清晰，workflow/taskboard 稳定性提升。
3. sequential 运行轨迹可观测、可解释、可审计。
4. 压测失败定位速度提升，支持基于证据的闭环修复。

负面后果：

1. 初期开发成本上升，需要补齐契约、事件、测试与迁移。
2. 事件量增大，投影层可能出现噪声与渲染压力。
3. 如配置不当，可能导致预算过紧影响收敛率。

## 5. 不可退让约束清单（不允许改主状态字段、不允许内容代写兜底）

1. 不允许 sequential 改写任何 workflow/taskboard 主状态字段。
2. 不允许通过自动内容代写、模板填充、隐性兜底来伪造通过。
3. 不允许隐藏失败；失败必须携带可审计终止原因与证据。
4. 不允许多处回合推进并存；内核之外不得新增 while-loop 推进器。
5. 不允许记录原始 CoT 文本；仅允许结构化摘要与必要审计字段。
6. 不允许删除或跳过 `seq.reserved_key_violation` 违规证据。

## 6. 迁移与回滚策略（配置回退但代码路径统一）

迁移策略：

1. 先统一代码路径（内核单驱动），再按角色逐步开启 sequential。
2. 保持主状态机不变，仅附加 `metadata.seq.*` 与 `sequential_stats`。
3. 先发布 `projection-focus=all` 默认，再开放 `seq` 精细过滤。

回滚策略：

1. 使用配置将 `sequential_mode` 回退为 `disabled`。
2. 保留统一内核路径，不回退到多循环旧架构。
3. 投影层可降级隐藏 `sequential_trace`，但不移除后端事件兼容层。

## 7. 验证证据要求（测试与压测证据路径）

必须具备以下证据后方可宣告完成：

1. 单元测试证据：
   - sequential 状态机流转
   - 预算耗尽终止
   - 无进展终止
   - 保留字写入拦截

2. 集成测试证据：
   - workflow/taskboard 主状态迁移未变化
   - 不存在双重循环推进
   - `seq.*` 事件可被 runtime_ws 与 observer 消费

3. 压测闭环证据：
   - `probe-only -> 1轮 smoke -> 标准轮次`
   - 主链到 QA 可达
   - `phase` 无污染
   - `seq_budget_exhausted` 可解释

4. 证据文件路径建议：
   - `stress_results.json`
   - `stress_audit_package.json`
   - `stress_report.md`
   - `summary.txt`
   - `.polaris/factory/*`

5. 所有文本证据必须采用 UTF-8 编码。
