Polaris 无人值守自动化开发工厂执行计划 v1.0
1. 摘要
目标：在不改变“Fix-Forward + 证据优先 + UTF-8 强约束”前提下，把当前系统从“可运行”提升为“可长期无人值守长跑”。
工期：4 个迭代周（每周一个里程碑门禁）。
完成判定：run_factory_e2e_smoke.py --workspace . --full 全绿；system/process/llm 成为主日志通道；CI 覆盖后端+前端+Electron+工厂烟测；失败可在 3 hops 内定位并触发外部告警。
2. 范围与非目标
范围：修复主流程断点（路径契约、Auditor 语义、日志写入语义）；完成统一日志链路（写入/查询/WS/兼容层）；建立无人值守运行门禁（CI、告警、runbook）。
非目标：不做前端视觉重构；不启用默认自动回滚；不做历史日志强制全量回灌（仅提供可选迁移工具）。
3. 关键默认决策（已锁定）
Auditor 语义采用 fail-closed：audit_result=FAIL 且缺少 defect ticket 时强阻断。
路径契约采用“双兼容一规范”：运行时代码统一使用 runtime/...；输入层兼容 .polaris/runtime/... 一个发布周期。
日志采用“单写入口”：业务代码不得直写 JSONL，必须经 LogEventWriter 或 Adapter。
新日志能力采用 feature flag：KERNELONE_LOG_PIPELINE_ENABLED（开发默认开，生产灰度后全开）。
所有文本读写显式 UTF-8，并增加 CI 编码守卫阻断非 UTF-8 文件。
4. 公开接口与类型变更
WebSocket 协议：新增 system|process|llm 订阅；新增 {"type":"event","action":"query"} 查询请求与 query_result 响应；保留 legacy snapshot/line。
HTTP API：新增 GET /logs/query（run_id/channel/severity/actor/task_id/cursor/limit/high_signal_only）；新增 POST /logs/user-action（关键用户操作入库）。
类型契约：冻结 CanonicalLogEventV2、LogQuery、LogQueryResult，并作为前后端共享契约。
兼容策略：旧频道长期可读，通过投影层映射到新模型，不再作为真相源。
5. 分阶段执行（决策完整）
Phase A（第 1 周）：基线修复与契约统一
修复路径契约：在路径归一层接收 .polaris/runtime/... 并转换为 runtime/...；运行时代码统一写法为 runtime/...。
修复 Auditor 阻断语义：恢复缺陷票据缺失时 hard_block=true，错误码统一为 DEFECT_TICKET_MISSING。
通过当前失败用例并恢复冒烟全绿。
主要改动文件：execution.py、storage_layout.py、test_pm_loop.py、test_loop_pm_utils.py。
Phase B（第 2 周）：统一日志写入链路闭环
修复写入语义：重写 LogEventWriter 为真实 append（lock + append + fsync），禁止覆盖式写入行为。
单写入口落地：emit_event/emit_llm_event/emit_dialogue 改为统一调用 log_pipeline.adapters，保留过渡期双写。
统一 run_id 上下文：latest_run.json、RunContextManager、WebSocket 读取路径统一。
主要改动文件：writer.py、adapters.py、io_utils.py、io_events.py、run_context.py。
Phase C（第 3 周）：查询与实时通道产品化
完成 LogQueryService 的频道过滤、分页、cursor、高信号过滤，并落地 /logs/query。
WebSocket 新旧通道并行：system/process/llm 走结构化事件；legacy 频道走投影兼容。
落地 /logs/user-action，将关键 UI 操作写入 domain=user/channel=system。
主要改动文件：websocket.py、query.py、constants.py、realtime_hub.py。
Phase D（第 4 周）：无人值守运行保障与发布门禁
CI 升级为多作业门禁：backend tests、frontend type/lint/test、electron panel e2e、factory smoke full；PR 全绿才允许合并。
编码治理：新增 UTF-8 扫描并修复损坏文档编码（含设计系统文档）。
监控告警：增加吞吐、延迟、增强成功率、失败码分布、重试次数指标；接入 Telegram 告警（默认）。
输出运维 runbook：故障分级、人工接管条件、恢复步骤、审计路径。
6. 测试与验收场景
单元测试：写入器 append-only 与序号单调；路径兼容与越权拒绝；Auditor fail-closed 判定。
集成测试：PM -> ChiefEngineer -> Director -> QA 在新路径与新日志下通过；WebSocket 新旧通道一致；/logs/query 分页和 cursor 正确。
端到端测试：Electron 面板任务回归通过；factory smoke 全绿。
混沌测试：日志轮转/并发写入不丢事件；LLM 增强失败不阻塞实时链路；断线重连 cursor 续读正确。
7. 每阶段固定门禁命令
Phase A Gate：python scripts/run_factory_e2e_smoke.py --workspace .
Phase B Gate：test_v2_websocket_snapshot.py 与 test_pm_loop.py
Phase C Gate：pytest -q src/backend/tests 与 npm run test:e2e
Phase D Gate：run_factory_e2e_smoke.py --workspace . --full、npm run test:e2e、CI 全绿
8. 风险与回退策略
风险：双写期可能重复事件；投影层可能导致新旧显示差异；CI 扩容初期耗时上升。
回退：KERNELONE_LOG_PIPELINE_ENABLED=0 一键回旧链路；保留 legacy 订阅与读取路径；每阶段合并前打可回退 tag 并保留验证日志。
9. 交付物清单
代码：路径契约修复、Auditor 语义修复、统一日志链路、查询与 WS 接口、CI 门禁。
测试：单元/集成/E2E 用例更新，full smoke 通过。
文档：日志系统规范、运维 runbook、发布回退说明、接口契约说明。
审计证据：关键命令日志、失败复盘记录、每阶段 Gate 通过记录。
10. 假设与默认
本次优先“稳定性与可运维性”，不并行推进大规模功能扩张。
允许一个发布周期兼容层，之后执行去旧路径清理。
外部通知默认 Telegram，企业通道后续替换。
保持 Fix-Forward 根原则，自动回滚默认关闭不变。

11. ACGA 3 补充方向（仓库级补充，不替代本计划）
本计划 v1.0 的重点仍然是“把当前系统从可运行提升为可长期无人值守长跑”，因此其交付核心仍是稳定性、可运维性、统一日志链路与门禁。

在此基础上，仓库新增 ACGA 3 方向的补充文档，用于定义下一阶段目标：

- 从“自动化开发指挥台”推进为“无人值守软件工厂控制面”
- 让 AI/Agent/LLM 持续写代码、改代码、重构代码
- 允许系统在受控边界内自动演化更好的算法、实现与局部架构
- 强调 `proposal -> projection -> verification -> comparison -> promotion / rollback` 的演化闭环
- 明确 `LLM 管 proposal，不管 truth`，以及“同一个治理内核，两种运行模式”

补充文档入口：

- `ACGA3_FACTORY_POSITIONING.md`
- `docs/architecture/ACGA_3_AUTONOMOUS_FACTORY_SPEC.md`
- `src/backend/docs/ACGA_3.0_RFC.md`

注意：

- 本节是产品与架构方向补充，不改写当前 v1.0 周计划的既有门禁与完成判定
- 当前真实后端架构真相仍以 `src/backend/docs/graph/**`、`src/backend/docs/FINAL_SPEC.md` 与相关 `cell.yaml` 为准
