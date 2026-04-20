# Polaris 统一日志系统重构方案（真相源 + 三通道 + LLM增强 + 高信号展示）

## 摘要
1. 以“每次运行独立 Journal + 全局索引”为唯一真相源，彻底结束“部分日志靠文件尾读、部分日志靠结构化事件、部分日志无来源”的割裂状态。  
2. 将实时日志统一为 `system / process / llm` 三个新频道，同时提供“永久兼容层”保持旧频道长期可用。  
3. 引入异步 LLM 全量增强链路：原始日志先可见，LLM 后台补充“去噪、摘要、结构化标签”，前端默认展示高信号时间线。  
4. 根因修复纳入方案：`runtime_events` 路径语义错位、`emit_llm_event` 混写、`seq=0` 直写事件、`director_llm` 生产缺失。  

## 现状结论（作为设计依据）
1. 当前实时链路是“文件驱动推送”：WebSocket 读取 channel 对应文件并发 `snapshot/line`，`RealtimeSignalHub` 只负责唤醒，不携带正文。  
2. 真实数据源是双轨并存：`subprocess stdout/stderr` 文本日志 + `emit_event/emit_llm_event/emit_dialogue` JSONL 结构化事件。  
3. 关键缺陷包括：channel 语义重叠、噪音事件重复、LLM 事件写入目标不一致、`director_llm` 通道几乎无生产、`runtime_events` 对“当前 run”感知不足。  

## 目标架构（决策完备）
1. 定义统一日志事件模型 `CanonicalLogEventV2`，所有来源先归一化再分发。  
2. 所有写入统一走 `LogEventWriter`，禁止业务代码直接 `open(..., "a")` 手写日志事件。  
3. 存储采用三层文件：  
   - `journal.raw.jsonl`：原始事实，不可变（审计源）。  
   - `journal.norm.jsonl`：归一化事实（统一 schema）。  
   - `journal.enriched.jsonl`：LLM 增强结果（可重建）。  
4. 运行级目录：`runtime/runs/<run_id>/logs/`。  
5. 全局索引：`runtime/events/log.index.jsonl`，仅保存检索元数据与定位信息，不重复存整段正文。  
6. 新频道固定为：`system`、`process`、`llm`。  
7. 旧频道长期支持，通过“查询适配”映射到新模型，不再依赖各自散落文件作为独立真相源。  

## 统一事件 Schema（核心接口）
1. 新增 `CanonicalLogEventV2` 字段集合：  
   - `schema_version`、`event_id`、`ts`、`ts_epoch`、`seq`、`run_id`。  
   - `domain`（`system|process|llm|user`）。  
   - `channel`（`system|process|llm`）。  
   - `severity`（`debug|info|warn|error|critical`）。  
   - `kind`（`state|action|observation|output|error`）。  
   - `actor`、`source`、`message`、`refs`、`tags`。  
   - `raw`（原始片段/原始 payload）。  
   - `fingerprint`、`dedupe_count`。  
   - `enrichment`（LLM增强字段：`signal_score`、`summary`、`normalized_fields`、`noise`）。  
2. 序号策略：每个 `run_id` 独立单调递增；全局索引维护 `(run_id, seq)` 到物理偏移。  
3. 兼容字段保留：旧前端依赖的 `event/name/output/data` 在适配层可投影生成。  

## 频道合并与永久兼容映射
| 旧频道 | 新频道 | 永久适配策略 |
|---|---|---|
| `pm_subprocess`, `director_console`, `runlog` | `process` | 按 `source/actor` 过滤投影，保持原文本行可取 |
| `pm_llm`, `director_llm`, `ollama` | `llm` | 从归一化+增强事件投影；无结构化时回退 raw |
| `runtime_events`, `engine_status` | `system` | 按状态事件投影，保留旧消息名兼容 |
| `pm_log`, `pm_report`, `planner`, `qa`, `dialogue` | `system`（实时）+ 文件读取（历史） | WS 走统一投影，文件 API 继续可读原工件 |

## 后端实施步骤
1. Phase A（日志内核）  
   - 新建 `log_pipeline` 模块：`ingest -> normalize -> dedupe -> persist -> publish`。  
   - 新建 `LogEventWriter` 与 `LogQueryService`。  
   - 增加 `run_id` 解析器与 `active_run` 解析逻辑。  
2. Phase B（来源接入）  
   - 子进程 stdout/stderr 改为进入 ingest 适配器。  
   - `emit_event/emit_llm_event/emit_dialogue` 统一走 `LogEventWriter`。  
   - Director v2 message bus 事件桥接入 `system`。  
   - 前端用户操作新增轻量上报端点接入 `user -> system`。  
3. Phase C（根因修复）  
   - 修正 `runtime_events` 对当前 run 的解析与读取。  
   - 修复 `emit_llm_event` 混写目标，LLM 生命周期统一进 `llm`。  
   - `docs/init/apply` 改用统一写入器，移除 `seq=0` 直写。  
   - 为 `director_llm` 补全生产链（至少由 v2 LLM 调用/结果事件驱动）。  
4. Phase D（WebSocket 协议）  
   - `/ws` 保留，新增对 `system/process/llm` 的原生支持。  
   - 新增结构化消息 `type=event`；同时继续支持旧 `snapshot/line`。  
   - 旧频道由适配层动态投影，不再硬编码文件路径作为唯一来源。  
5. Phase E（LLM 异步全量增强）  
   - 后台 worker 消费 `journal.norm.jsonl` 全量事件。  
   - 结果写 `journal.enriched.jsonl`，失败不阻塞实时链路。  
   - 增强失败或超时时自动标记 `enrichment.status=failed`，前端退回规则化展示。  
6. Phase F（运维与保留）  
   - 原始日志长期保留。  
   - 增强日志滚动清理（默认 30 天，可配置）。  
   - 全局索引按 run 分片，防止单文件无限膨胀。  

## 前端展示方案（高信号时间线默认）
1. 新增 `UnifiedLogsView`，默认卡片时间线按 `system/process/llm` 可切。  
2. 默认开启“高信号模式”：隐藏噪音项，显示“折叠计数 + 可展开原文”。  
3. 提供四类筛选：`channel`、`severity`、`actor/source`、`run_id/task_id`。  
4. 卡片标准结构：标题、摘要、关键字段 chips、原文折叠、关联跳转（任务/文件/运行）。  
5. 保留“原始流”视图，一键切回 tail 模式，确保排障时可见全部事实。  
6. 兼容旧组件：`LogsModal`/`LogViewer` 先接适配层，再逐步切到统一数据模型。  

## 公开 API / 接口 / 类型变更
1. WebSocket 新订阅频道：`system|process|llm`。  
2. WebSocket 新消息类型：`event`（结构化），并继续支持旧 `snapshot/line`。  
3. 新增查询接口：`GET /logs/query`（按 `run_id/channel/severity/cursor/limit` 拉取）。  
4. 新增操作上报接口：`POST /logs/user-action`（前端关键操作入库）。  
5. 统一类型：前后端共享 `CanonicalLogEventV2` 与 `LogEnrichmentV1`。  
6. 保持旧接口长期可用：旧频道、旧字段、旧日志面板均不移除，仅标记 legacy。  

## 测试与验收场景
1. 单元测试  
   - 事件归一化映射正确。  
   - 去重指纹与折叠计数正确。  
   - 旧频道投影结果与历史行为等价。  
   - LLM 增强失败时降级路径正确。  
2. 集成测试  
   - 单 run 与并发 run 下 `seq` 单调且无串写。  
   - `system/process/llm` 频道 snapshot + 增量推送一致。  
   - 旧频道订阅与新频道并行订阅无冲突。  
   - `runtime_events` 能正确跟随当前 run。  
3. 端到端测试  
   - 前端默认高信号时间线展示正确。  
   - 噪音折叠与“查看原文”交互正确。  
   - 切换 run/channel/filter 后数据一致。  
4. 混沌与故障注入  
   - 文件截断、日志轮转、watchdog 抖动下不丢事件。  
   - LLM 服务超时/不可用时实时展示不阻塞。  
   - 大量日志写入下 WebSocket 不雪崩。  

## 监控、审计与回滚
1. 监控指标：事件吞吐、端到端延迟、增强成功率、去重率、WS掉线率。  
2. 审计保证：原始 journal 不可变；增强层可重建；所有投影可追溯到 `(run_id, seq, event_id)`。  
3. 回滚策略：  
   - 一键切回 legacy 文件驱动推送。  
   - 保留旧前端读取路径。  
   - 新增功能以 feature flag 控制分批放量。  

## 假设与默认（已锁定）
1. LLM 处理模式：异步增强，且全量事件参与增强。  
2. 频道迁移策略：永久兼容层，不进行一次性硬切。  
3. 展示默认：高信号时间线，原始流作为随时可切换备选。  
4. 真相源模型：`Run Journal + 全局索引`。  
5. 保留策略：原始长期保留，增强日志滚动保留（默认 30 天）。  
6. 所有文本读写保持显式 UTF-8。  
