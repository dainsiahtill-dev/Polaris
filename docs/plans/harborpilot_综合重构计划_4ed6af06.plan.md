---
name: Polaris 综合重构计划 - 落盘体系 + NATS JetStream 实时推送（执行版 v4）
overview: 在“文件为审计真源 + NATS JetStream 负责实时分发”约束下，完成 Polaris 存储分层治理、实时链路全量替换、MessageBus 全异步化、WS/SSE 协议升级与一次硬切迁移。
todos:
  - id: phase0-baseline-freeze
    content: "Phase 0: 基线冻结与契约固化"
    status: pending
  - id: phase1-storage-policy
    content: "Phase 1: 存储分类策略与路径治理"
    status: pending
  - id: phase2-history-archive
    content: "Phase 2: 历史归档服务与索引"
    status: pending
  - id: phase3-nats-infra
    content: "Phase 3: NATS JetStream 基础设施层"
    status: pending
  - id: phase4-bus-async
    content: "Phase 4: MessageBus 全异步重构与 NATS 适配"
    status: pending
  - id: phase5-runtime-publishers
    content: "Phase 5: Runtime 发布链路改造（writer/trace/status）"
    status: pending
  - id: phase6-ws-sse-gateway
    content: "Phase 6: WebSocket/SSE 网关协议 v2 升级"
    status: pending
  - id: phase7-frontend-adapter
    content: "Phase 7: 前端传输层协议适配"
    status: pending
  - id: phase8-hard-cut-migration
    content: "Phase 8: 一次硬切迁移与上线切换"
    status: pending
  - id: phase9-cleanup-and-acceptance
    content: "Phase 9: 旧模块下线、测试验收与审计收口"
    status: pending
isProject: false
---

# Polaris 综合重构计划（Claude 执行落地版 v4）

## 0. 执行边界与铁律

1. 所有文本文件读写必须显式 UTF-8。
2. 本次为一次硬切，不保留旧实时链路 fallback（仅保留紧急代码回滚手段）。
3. 文件日志是审计真源，JetStream 只承担实时分发和回放能力。
4. 目标是根因级重构，不做局部补丁式并存方案。
5. 前端不直连 NATS，统一由后端 WS/SSE 网关桥接。

---

## 1. 已锁定决策（不再变更）

### 1.1 落盘体系

1. 迁移策略：一次切换。
2. 历史策略：永久保留 + 压缩（`.zst`）。
3. TaskBoard 当前态模型保持不变：`runtime/tasks/plan.json` 与 `runtime/tasks/task_*.json` 继续作为当前态真相源。
4. 切换后禁止 legacy 写入。

### 1.2 NATS 实时推送

1. 迁移策略：硬切，一次完成。
2. 部署模式：单节点 JetStream（J1）。
3. 投递语义：至少一次（at-least-once）。
4. NATS 不可用时：`/ready` 返回不就绪（503）。
5. 前端连接：后端 WS/SSE 网关，不直连 NATS。
6. 协议：升级到 runtime v2（cursor + ack + resume）。

---

## 2. 成功标准（DoD）

### 2.1 功能正确性

1. runtime 相关实时事件全部经 NATS JetStream 分发。
2. WS/SSE 客户端断线重连后可从 cursor 恢复，不丢不乱（允许 at-least-once 重复）。
3. 旧模块 `runtime_event_fanout/realtime_hub/log_pipeline/realtime` 不再参与主链路。

### 2.2 可靠性

1. NATS 临时抖动时，发布链路可重试并在恢复后继续消费。
2. 消费者 slow-consumer 不拖垮生产者，具备背压策略。
3. 所有关键发布/消费异常有结构化日志与指标。

### 2.3 性能目标（单机 J1）

1. 端到端推送延迟：p50 < 120ms，p95 < 300ms。
2. 稳态吞吐：>= 2,000 events/s（本地压测基线）。
3. 内存稳定，无无界队列增长。

### 2.4 审计与可追溯

1. 文件日志与 JetStream 事件可通过 `event_id`/`run_id`/`seq` 关联。
2. 支持按 `workspace + run_id + cursor` 重放核对。

---

## 3. 目标架构（最终态）

### 3.1 总体数据流

1. 业务组件先写 canonical 日志（UTF-8 JSONL）。
2. 写入成功后立即构造 `RuntimeEventEnvelope` 并发布到 JetStream。
3. WS/SSE 网关从 JetStream 按 workspace/channel 订阅，向客户端推送。
4. 客户端以 `cursor` ACK；重连时带上 `cursor` 进行恢复。

### 3.2 统一事件信封（RuntimeEventEnvelope）

```json
{
  "schema_version": "runtime.v2",
  "event_id": "uuid",
  "workspace_key": "ws_3f9ab1c2",
  "run_id": "run_xxx",
  "channel": "log.llm",
  "kind": "chunk",
  "ts": "2026-03-12T08:00:00.000Z",
  "cursor": 12345,
  "trace_id": "trace_xxx",
  "payload": {},
  "meta": {
    "producer": "director",
    "source": "log_pipeline.writer"
  }
}
```

约束：

1. `cursor` 使用 JetStream stream sequence（消费侧可直接读取元数据）。
2. `event_id` 全局唯一，重复投递由消费侧按 `event_id` 去重。
3. 单条 `payload` 上限 256KB；超限内容写文件并只传引用。

### 3.3 Subject 命名规范

```
hp.runtime.<workspace_key>.<channel>
```

channel 固定集合：

1. `log.system`
2. `log.process`
3. `log.llm`
4. `event.file_edit`
5. `event.task_trace`
6. `status.snapshot`
7. `sse.factory`
8. `sse.docs`
9. `sse.interview`

### 3.4 JetStream 资源规范

Stream：`HP_RUNTIME`

1. subjects: `hp.runtime.>`
2. retention: `limits`
3. storage: `file`
4. replicas: `1`
5. max_age: `168h`
6. max_bytes: `20GB`
7. max_msg_size: `262144`
8. duplicate_window: `2m`

Consumer 策略：

1. WS 网关：`pull durable`，durable 命名 `ws_<workspace_key>_<client_id_hash>`。
2. SSE 网关：`pull durable`，durable 命名 `sse_<workspace_key>_<stream_key_hash>`。
3. ack policy：explicit。
4. ack_wait：`30s`。
5. max_deliver：`10`。
6. deliver_policy：默认 `all`；重连恢复时使用 `by_start_sequence`。

---

## 4. 代码级实施方案（分阶段）

## Phase 0：基线冻结与契约固化

目标：先把“现状协议、事件类型、测试基线”冻结，减少并行改动风险。

实施：

1. 在 `docs/plans/` 新增 `runtime_protocol_v1_snapshot.md`，记录当前 WS/SSE 消息类型。
2. 导出现有关键测试清单和通过基线（pytest + frontend test）。
3. 对下述接口做契约快照：
   1. `/v2/ws/runtime`
   2. `/ready`
   3. 现有 SSE 入口（factory/docs/interview/role_session）

完成条件：

1. 基线测试可稳定复现。
2. v1 契约文档可作为回归比对输入。

---

## Phase 1：存储分类策略与路径治理

目标：落盘分层统一，后续归档与实时解耦。

新增：

1. `src/backend/core/polaris_loop/storage_policy.py`
2. `src/backend/app/services/storage_policy_service.py`

修改：

1. `src/backend/infrastructure/storage/layout.py`（若当前 layout 在其他路径，以现实现路径为准）
2. `src/backend/config.py`（新增 history 配置项）

统一分类：

1. `GLOBAL_CONFIG`
2. `WORKSPACE_PERSISTENT`
3. `RUNTIME_CURRENT`
4. `RUNTIME_RUN`
5. `WORKSPACE_HISTORY`

核心接口：

```python
class StoragePolicyService:
    def get_policy(self, logical_path: str) -> StoragePolicy: ...
    def resolve_target_path(self, logical_path: str, workspace: str, runtime_root: str) -> str: ...
    def should_archive(self, logical_path: str, terminal_status: str) -> bool: ...
```

完成条件：

1. 所有 runtime/history 路径解析可由策略服务统一输出。
2. 无硬编码路径分支遗留。

---

## Phase 2：历史归档服务与索引

目标：将 run/factory/task 历史稳定归档到 workspace history。

新增：

1. `src/backend/app/services/history_archive_service.py`
2. `src/backend/app/services/history_manifest_repository.py`

索引文件：

1. `workspace/.polaris/history/index/runs.index.jsonl`
2. `workspace/.polaris/history/index/tasks.index.jsonl`
3. `workspace/.polaris/history/index/factory.index.jsonl`

Manifest 结构：

```python
@dataclass
class ArchiveManifest:
    scope: Literal["run", "task", "factory"]
    id: str
    workspace: str
    archived_at: str
    reason: str
    source_paths: list[str]
    archived_paths: list[str]
    file_count: int
    total_size_bytes: int
    sha256: str
```

行为约束：

1. 归档异步执行，不阻塞主执行链路。
2. 写 manifest/index 前后都做原子写（临时文件 + rename）。
3. 压缩统一 zstd，文本读写显式 UTF-8。

完成条件：

1. run/factory/task 三类归档成功并可检索。
2. 归档失败可重试且不影响主流程完成态。

---

## Phase 3：NATS JetStream 基础设施层

目标：提供可复用的 NATS 连接、JetStream stream/consumer 管理能力。

新增：

1. `src/backend/infrastructure/messaging/nats_client.py`
2. `src/backend/infrastructure/messaging/jetstream_admin.py`
3. `src/backend/infrastructure/messaging/nats_types.py`

修改：

1. `src/backend/config.py`：新增 `NATSConfig`
2. `src/backend/api/main.py`：lifespan 启停 NATS
3. `pyproject.toml`：新增 `nats-py[nkeys,jetstream]>=2.13.1`

配置模型建议：

```python
class NATSConfig(BaseModel):
    enabled: bool = True
    required: bool = True
    url: str = "nats://127.0.0.1:4222"
    user: str = ""
    password: str = ""
    connect_timeout_sec: float = 3.0
    reconnect_wait_sec: float = 1.0
    max_reconnect_attempts: int = -1
    stream_name: str = "HP_RUNTIME"
```

健康策略：

1. `required=true` 且连接失败时，应用不进入 ready。
2. `/ready` 返回 503，并带 `nats_connected=false`。

完成条件：

1. 服务启动自动建 stream，重复启动幂等。
2. NATS 断连和重连状态可观测。

---

## Phase 4：MessageBus 全异步重构与 NATS 适配

目标：统一 bus 语义，避免 sync handler 与 async pipeline 混用。

修改：

1. `src/backend/application/message_bus.py`
2. `src/backend/infrastructure/di/container.py`（注入新 bus）

接口改造：

```python
class MessageBus:
    async def subscribe(self, message_type: MessageType, handler: AsyncMessageHandler) -> bool: ...
    async def unsubscribe(self, message_type: MessageType, handler: AsyncMessageHandler) -> bool: ...
    async def publish(self, message: Message) -> None: ...
```

适配策略：

1. 引入 `LegacySyncHandlerAdapter`，临时托管旧同步 handler。
2. 主线全部迁到 async handler 后移除 adapter。
3. 保留 actor direct queue 语义，但底层统一 async。

完成条件：

1. 无 `bus.subscribe(...)` 同步调用残留。
2. 所有 message handler 错误被捕获并记录。

---

## Phase 5：Runtime 发布链路改造（writer/trace/status）

目标：所有 runtime 推送事件都走 JetStream。

修改：

1. `src/backend/core/polaris_loop/log_pipeline/writer.py`
2. `src/backend/core/llm_toolkit/executor.py`
3. `src/backend/app/services/pm_realtime.py`
4. `src/backend/app/services/qa_realtime.py`

关键改造：

1. `writer.py` 保持“先落盘后发布”。
2. 发布失败进入 `publish_retry_queue`（内存 + 指标），后台重试。
3. `file_edit/task_trace/status` 统一转换为 `RuntimeEventEnvelope`。

可靠性策略：

1. 发布重试：指数退避（100ms 起，最多 10 次）。
2. 达到重试上限后打 `P0` 级日志并上报指标。
3. 不破坏落盘成功语义（审计真源优先）。

完成条件：

1. runtime 事件发布覆盖率 100%。
2. 压测下无无界重试堆积。

---

## Phase 6：WebSocket/SSE 网关协议 v2 升级

目标：去除文件轮询 fanout，改为 JetStream 消费桥接。

修改：

1. `src/backend/api/v2/runtime_ws.py`
2. `src/backend/app/routers/sse_utils.py`
3. `src/backend/app/routers/factory.py`

### 6.1 WS v2 协议

客户端订阅：

```json
{
  "type": "SUBSCRIBE",
  "protocol": "runtime.v2",
  "workspace": "<abs path or workspace key>",
  "client_id": "ui-main",
  "channels": ["log.llm", "event.file_edit", "status.snapshot"],
  "cursor": 0,
  "tail": 200
}
```

服务端推送：

```json
{
  "type": "EVENT",
  "protocol": "runtime.v2",
  "cursor": 12345,
  "event": {"...": "RuntimeEventEnvelope"}
}
```

客户端 ACK：

```json
{
  "type": "ACK",
  "protocol": "runtime.v2",
  "cursor": 12345
}
```

错误与控制：

1. `ERROR`：协议错误、无权限、cursor 非法。
2. `PING/PONG`：连接保活。
3. `RESYNC_REQUIRED`：服务端提示客户端发起 `SUBSCRIBE` 重新建立游标。

### 6.2 SSE v2 协议

1. 使用 `Last-Event-ID` 作为 cursor 恢复点。
2. SSE data 包含统一 `event/cursor/ts`。
3. 工厂/文档/访谈流统一通过 NATS subject 分发。

完成条件：

1. `runtime_ws` 不再依赖 `runtime_event_fanout/realtime_hub/log_pipeline/realtime`。
2. WS/SSE 都支持断线恢复。

---

## Phase 7：前端传输层协议适配

目标：最小侵入兼容 runtime.v2。

修改：

1. `src/frontend/src/api.ts`
2. `src/frontend/src/runtime/transport/runtimeSocketManager.ts`
3. `src/frontend/src/app/hooks/useRuntime.ts`

改造要点：

1. 首次连接发送 `SUBSCRIBE(protocol=runtime.v2)`。
2. 收到 `EVENT` 后维护本地 `lastCursor`。
3. 每 N 条或每 500ms 批量 ACK。
4. 重连时携带 `cursor=lastCursor`。
5. 去重键：`event_id`。

完成条件：

1. 页面刷新/网络闪断后流恢复正常。
2. 不出现历史重复堆叠（允许一次性重复但会被去重层消化）。

---

## Phase 8：一次硬切迁移与上线切换

目标：单次窗口完成切换与验证。

新增：

1. `src/backend/scripts/migrate_storage_layout_v2.py`
2. `src/backend/scripts/migrate_runtime_protocol_v2.py`

执行顺序：

1. 冻结写入窗口（停止 PM/Director 新任务进入）。
2. 执行 storage/history 迁移（支持 `--dry-run`、`--backup-dir`）。
3. 执行协议升级标记写入：`runtime/protocol.version = runtime.v2`。
4. 发布新版本，启动后做 ready/health 验证。
5. 跑冒烟回归（WS + SSE + 压测 observer）。

完成条件：

1. 切换窗口内完成并通过冒烟。
2. 旧链路代码未被调用。

---

## Phase 9：旧模块下线、测试验收与审计收口

待删除：

1. `src/backend/app/services/runtime_event_fanout.py`
2. `src/backend/app/services/realtime_hub.py`
3. `src/backend/core/polaris_loop/log_pipeline/realtime.py`

测试新增/改造：

1. `src/backend/tests/test_nats_client.py`
2. `src/backend/tests/test_jetstream_bridge_ws.py`
3. `src/backend/tests/test_runtime_ws_protocol_v2.py`
4. `src/backend/tests/test_history_archive_service.py`
5. `src/backend/tests/test_storage_migration_v2.py`
6. `src/frontend/src/runtime/transport/runtimeSocketManager.protocol.test.ts`

必须保留并更新：

1. `src/backend/tests/test_runtime_ws_migration.py`
2. `src/backend/tests/test_websocket_architecture_integration.py`
3. `src/backend/tests/test_log_pipeline_storage_layout.py`

验收门禁：

1. 单元测试通过率 100%。
2. 集成测试通过率 100%。
3. E2E（runtime 面板 + LogViewer + observer）全部通过。
4. 压测 30 分钟无消费者堆积告警。

---

## 5. 对外接口与契约变更清单

### 5.1 后端配置

新增环境变量：

1. `POLARIS_NATS_ENABLED`
2. `POLARIS_NATS_REQUIRED`
3. `POLARIS_NATS_URL`
4. `POLARIS_NATS_USER`
5. `POLARIS_NATS_PASSWORD`
6. `POLARIS_NATS_STREAM`

### 5.2 WebSocket

1. 默认协议从 v1 升到 `runtime.v2`。
2. 新增 `ACK` 命令。
3. 旧 `line/snapshot` 混合推送改为统一 `EVENT`。

### 5.3 SSE

1. 所有 runtime 相关 SSE 都带 cursor。
2. 支持 `Last-Event-ID` 续传。

### 5.4 健康检查

1. `/ready` 增加 NATS 连接状态判定。
2. NATS required 且断连时返回 503。

---

## 6. Claude 执行分包（可直接照做）

### 包 A：基础设施包（先做）

1. 增加配置模型与依赖。
2. 增加 NATS 客户端与 JetStream 管理器。
3. 接入 `api/main.py` 生命周期。
4. 补齐 `test_nats_client.py`。

交付判定：

1. 本地启动可连 NATS。
2. stream 幂等创建通过。

### 包 B：存储与归档包

1. 引入 StoragePolicyService。
2. 实现 HistoryArchiveService + manifest/index。
3. 补齐归档单测。

交付判定：

1. 归档可查可验 hash。

### 包 C：消息总线包

1. MessageBus 改全 async。
2. 旧调用点批量改造。
3. 保留临时 sync adapter（仅过渡）。

交付判定：

1. 无同步 subscribe 残留。

### 包 D：实时发布链路包

1. writer/executor/status 事件统一发布 NATS。
2. 引入重试和指标。
3. 关联 event_id 与 run_id。

交付判定：

1. 关键事件全部可在 JetStream 看到。

### 包 E：WS/SSE 网关包

1. runtime_ws 升级到协议 v2。
2. sse_utils + factory/docs/interview 流改 NATS 消费。
3. 协议兼容处理与错误码落地。

交付判定：

1. 断线恢复 + ACK 正常。

### 包 F：前端协议包

1. runtimeSocketManager 发送 v2 SUBSCRIBE。
2. 维护 cursor/ack/reconnect。
3. useRuntime 去重逻辑改以 event_id 为准。

交付判定：

1. UI 无明显重复/丢失。

### 包 G：迁移与切换包（最后）

1. 执行迁移脚本 dry-run + real run。
2. 一次窗口硬切。
3. 完成全链路验收与审计记录。

交付判定：

1. 旧模块可安全删除。

---

## 7. 测试计划（详细）

### 7.1 单元测试

1. NATS 连接重试、断线重连。
2. EventEnvelope 编解码、字段校验、大小限制。
3. StoragePolicy 分类与路径解析。
4. Archive manifest/index 原子写。
5. MessageBus async subscribe/publish/unsubscribe。

### 7.2 集成测试

1. writer 落盘后发布成功。
2. WS v2 订阅 + ACK + 断线恢复。
3. SSE Last-Event-ID 恢复。
4. NATS 抖动下消息重投递。

### 7.3 E2E

1. `npm run test:e2e`
2. `python scripts/run_factory_e2e_smoke.py --workspace .`
3. observer 压测场景（LLM 连续流式 + 文件编辑 + task trace 并发）。

### 7.4 压测与混沌

1. 网络抖动（断连 5s/30s）恢复测试。
2. slow-consumer 测试。
3. 大消息与高频消息混合测试。

---

## 8. 风险与防御

| 风险 | 防御措施 |
| --- | --- |
| NATS 短时不可用 | 发布重试 + ready fail-fast + 告警 |
| at-least-once 重复消费 | 客户端按 `event_id` 去重 |
| 消费者积压 | pull consumer + 批量 ACK + 背压限流 |
| 一次切换失败 | 切换前快照备份 + 可执行回滚步骤 |
| 历史体积持续增长 | zstd 压缩 + index 分片 + 定期巡检 |

---

## 9. 回滚计划（硬切后的紧急回退）

### 9.1 代码回滚

1. 回退到切换前 tag：`pre_nats_cutover`。
2. 重启服务，恢复旧版本二进制。

### 9.2 数据回滚

1. 使用迁移前 `--backup-dir` 快照恢复 runtime/history。
2. 校验 manifest/hash 一致性。

### 9.3 运行回滚验证

1. `/ready`、`/health` 通过。
2. runtime 页面、factory 流、observer 冒烟通过。

---

## 10. 本计划默认假设

1. 当前环境允许引入并运行 NATS 单节点服务。
2. 可在部署窗口内暂停新任务进入。
3. 本次重构优先 backend 主链路，frontend 做必要协议适配，不做额外 UI 重构。
4. 未列出的业务领域事件不纳入本次 runtime 实时总线改造。

---

## 11. Claude 执行 Runbook（逐步骤、逐命令、逐验收）

> 说明：本章节是给 Claude 的直接执行手册。按步骤顺序执行，不跨步并行；每步结束必须跑对应自检命令并记录结果。

### Step 0：创建执行分支与基线快照

目标：冻结当前基线，确保后续每一步可回溯。

操作：

1. 新建分支：`feat/runtime-nats-jetstream-cutover`
2. 保存基线报告：
   1. `docs/plans/runtime_protocol_v1_snapshot.md`
   2. `docs/plans/runtime_test_baseline.md`
3. 记录当前关键接口样例：
   1. `/v2/ws/runtime`
   2. `/ready`
   3. factory/docs/interview 的 SSE 路由

自检命令：

```bash
git rev-parse --abbrev-ref HEAD
python -m pytest src/backend/tests/test_runtime_ws_migration.py -q
```

通过标准：

1. 分支正确。
2. 基线文件已创建。
3. runtime_ws 基线测试可执行。

---

### Step 1：接入 NATS 配置与生命周期

目标：后端启动即初始化 NATS，失败时 `/ready` 不就绪。

改动文件：

1. `src/backend/config.py`
2. `src/backend/api/main.py`
3. `src/backend/api/routers/primary.py`
4. `pyproject.toml`

实施要点：

1. 在 `Settings` 中新增 `nats` 配置子模型（enabled/required/url/user/password/stream）。
2. 在 `api/main.py` lifespan 启动阶段调用 `init_nats()`，关闭阶段 `close_nats()`。
3. 在 `primary.py` 的 `/ready` 响应中增加 NATS 检查项。
4. `required=true` 且未连接时 `/ready` 返回 503。

自检命令：

```bash
python -m pytest src/backend/tests -k "primary or ready" -q
```

通过标准：

1. NATS 连接状态能出现在 `/ready`。
2. required 场景返回正确状态码。

---

### Step 2：新增 NATS 客户端与 JetStream 管理层

目标：提供可复用连接、流创建、消费者创建、发布 API。

新增文件：

1. `src/backend/infrastructure/messaging/nats_client.py`
2. `src/backend/infrastructure/messaging/jetstream_admin.py`
3. `src/backend/infrastructure/messaging/nats_types.py`

实施要点：

1. 使用 `nats.py`（`nats-py`）实现异步连接。
2. Stream `HP_RUNTIME` 幂等创建。
3. 提供 `publish_envelope()`、`create_or_bind_consumer()`、`fetch_batch()`。
4. 所有 publish API 明确超时与异常类型。

自检命令：

```bash
python -m pytest src/backend/tests -k "nats_client or jetstream" -q
```

通过标准：

1. 重复启动不会重复创建 stream。
2. 连接断开后可自动重连。

---

### Step 3：落盘策略与历史归档服务

目标：统一路径分类、归档索引、压缩策略。

新增文件：

1. `src/backend/core/polaris_loop/storage_policy.py`
2. `src/backend/app/services/storage_policy_service.py`
3. `src/backend/app/services/history_archive_service.py`
4. `src/backend/app/services/history_manifest_repository.py`

修改文件：

1. `src/backend/infrastructure/storage/layout.py`

实施要点：

1. 统一 `runtime/history` 解析入口。
2. run/task/factory 三类归档统一落 manifest。
3. 索引 JSONL 采用原子写。
4. 压缩使用 zstd。

自检命令：

```bash
python -m pytest src/backend/tests -k "storage_policy or history_archive" -q
```

通过标准：

1. 归档文件、manifest、index 三者一致。
2. 归档失败可重试。

---

### Step 4：MessageBus 全异步重构

目标：清除同步订阅模型，统一 async handler。

改动文件：

1. `src/backend/application/message_bus.py`
2. `src/backend/infrastructure/di/container.py`
3. 所有直接调用 `bus.subscribe(...)` 的调用点

实施要点：

1. `subscribe/unsubscribe/publish` 全异步。
2. 为旧同步 handler 提供临时 adapter（过渡期）。
3. 最终在本阶段结束时，清理全部同步调用残留。

自检命令：

```bash
rg -n "\.subscribe\(" src/backend | rg -v "await"
python -m pytest src/backend/tests -k "message_bus" -q
```

通过标准：

1. 无同步 subscribe 残留。
2. 现有 actor/queue 语义不回归。

---

### Step 5：Runtime 发布链路全部接 JetStream

目标：writer、trace、status 都发布标准 Envelope。

改动文件：

1. `src/backend/core/polaris_loop/log_pipeline/writer.py`
2. `src/backend/core/llm_toolkit/executor.py`
3. `src/backend/app/services/pm_realtime.py`
4. `src/backend/app/services/qa_realtime.py`

实施要点：

1. 严格执行“先落盘后发布”。
2. 每类事件映射到固定 channel。
3. 发布失败进入重试队列（指数退避 + 上限）。

自检命令：

```bash
python -m pytest src/backend/tests -k "log_pipeline or llm_toolkit_executor or realtime" -q
```

通过标准：

1. JetStream 可看到与落盘对应的 event。
2. 重试机制在失败注入下生效。

---

### Step 6：重写 runtime_ws 为 JetStream 网关

目标：移除 in-process fanout 依赖，改为 JetStream 消费。

改动文件：

1. `src/backend/api/v2/runtime_ws.py`

实施要点：

1. 连接建立后按 workspace/channels 创建或绑定 durable consumer。
2. 支持 `SUBSCRIBE(protocol=runtime.v2)`、`ACK`、`PING/PONG`。
3. 客户端重连带 cursor 时用 `by_start_sequence` 恢复。
4. 服务端统一发送 `EVENT` 包装。
5. 删除旧的 `RUNTIME_EVENT_FANOUT` 与 `LOG_REALTIME_FANOUT` 读取路径。

自检命令：

```bash
python -m pytest src/backend/tests -k "runtime_ws or websocket" -q
```

通过标准：

1. 断线恢复成功。
2. ACK 后不重复推送已确认区间（允许重连窗口内 at-least-once 重复）。

---

### Step 7：SSE 路由接入 JetStream

目标：factory/docs/interview/role_session 流统一 NATS 驱动。

改动文件：

1. `src/backend/app/routers/sse_utils.py`
2. `src/backend/app/routers/factory.py`
3. `src/backend/app/routers/docs.py`
4. `src/backend/app/routers/interview.py`
5. `src/backend/app/routers/role_session.py`

实施要点：

1. 建立 SSE consumer 适配器，支持 `Last-Event-ID`。
2. 统一 SSE data 格式：`event_id/cursor/ts/payload`。
3. 连接关闭时及时释放 consumer。

自检命令：

```bash
python -m pytest src/backend/tests -k "factory_router or sse" -q
```

通过标准：

1. SSE 重连可续传。
2. 长连接无资源泄漏。

---

### Step 8：前端 runtime.v2 协议适配

目标：前端可消费 `EVENT`，维护 cursor/ack。

改动文件：

1. `src/frontend/src/api.ts`
2. `src/frontend/src/runtime/transport/runtimeSocketManager.ts`
3. `src/frontend/src/app/hooks/useRuntime.ts`

实施要点：

1. 建连后发送 `SUBSCRIBE`（protocol=runtime.v2）。
2. 本地维护 `lastCursor` 与 `event_id` 去重集合。
3. 批量 ACK（每 500ms 或每 20 条）。
4. 重连携带 `cursor` 恢复。

自检命令：

```bash
npm --prefix src/frontend run test -- --runInBand
```

通过标准：

1. UI 流式面板无明显抖动/重复堆积。
2. 断网恢复后数据连续。

---

### Step 9：一次硬切迁移脚本

目标：切换窗口可重复执行、可审计、可回滚。

新增文件：

1. `src/backend/scripts/migrate_storage_layout_v2.py`
2. `src/backend/scripts/migrate_runtime_protocol_v2.py`

实施要点：

1. 支持 `--dry-run`、`--workspace`、`--backup-dir`。
2. 迁移前后生成校验报告（hash + 文件计数）。
3. 写入协议版本标记，阻断 legacy writer。

自检命令：

```bash
python src/backend/scripts/migrate_storage_layout_v2.py --workspace . --dry-run
python -m pytest src/backend/tests -k "migration" -q
```

通过标准：

1. dry-run 输出可读且不改数据。
2. 实迁后索引和 manifest 一致。

---

### Step 10：删除旧模块并收口

目标：彻底下线旧实时架构。

删除文件：

1. `src/backend/app/services/runtime_event_fanout.py`
2. `src/backend/app/services/realtime_hub.py`
3. `src/backend/core/polaris_loop/log_pipeline/realtime.py`

实施要点：

1. 删除前先移除引用。
2. 更新测试和导入。
3. 保证无死引用。

自检命令：

```bash
rg -n "runtime_event_fanout|realtime_hub|LOG_REALTIME_FANOUT|RUNTIME_EVENT_FANOUT" src/backend src/frontend
python -m pytest src/backend/tests -q
```

通过标准：

1. 搜索无引用。
2. 后端全量测试通过。

---

### Step 11：全链路验收与压测

目标：确认功能、性能、稳定性达标。

执行命令：

```bash
python -m pytest src/backend/tests -q
npm --prefix src/frontend run test -- --runInBand
npm run test:e2e
python scripts/run_factory_e2e_smoke.py --workspace .
```

压测建议：

1. 30 分钟持续推流。
2. 注入 5 次网络断连。
3. 检查重复率、丢失率、延迟 p95。

通过标准：

1. 全部门禁 PASS。
2. 关键指标满足第 2 章 DoD。

---

## 12. 提交与变更管理要求（给 Claude）

1. 每个 Step 至少一个独立提交，提交信息格式：`feat(runtime-nats): <step-x summary>`。
2. Step 内禁止混入无关重构。
3. 每个提交都附“变更文件列表 + 自检命令输出摘要”。
4. 遇到设计冲突时，优先保持本计划中“文件真源、硬切、网关转发、at-least-once”四项原则。
5. 如果发现仓库真实路径与计划不一致，以仓库真实路径为准，并在提交说明中记录映射关系。
