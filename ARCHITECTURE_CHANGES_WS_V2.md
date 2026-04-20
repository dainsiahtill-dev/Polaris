# WebSocket 实时推送架构级修复 - 变更说明

## 1. 深度思考与根因诊断

### 原始问题
原有的 WebSocket 实时推送系统存在多个问题：
1. **watcher 生命周期混乱**: 没有引用计数，导致重复创建或提前释放
2. **MessageBus 订阅泄漏**: 每个 WebSocket 连接独立订阅，连接关闭时取消订阅不可靠
3. **背压处理缺失**: 队列满时直接丢弃事件，没有 resync 机制
4. **错误可观测性差**: send 错误被静默吞掉，无法诊断
5. **跨 workspace 信号干扰**: wait_for_update 没有 workspace 过滤

### 根因定位
- **生命周期所有权不清晰**: watcher、MessageBus 订阅、ws 连接缓冲分散在多个模块
- **TOCTOU 竞态**: ensure_watch 检查-创建非原子
- **缺乏全局事件分发层**: 每个连接独立订阅导致 N 倍订阅数

## 2. 改动摘要 (Modification Summary)

### 2.1 阶段 A: 重构 watcher 生命周期 (realtime_hub.py)
- **新增 `WatchEntry` dataclass**: 封装 observer、handler、ref_count、state、creation_lock
- **新增 `WatchState` 枚举**: STARTING、RUNNING、FAILED、STOPPING、STOPPED
- **修改 `ensure_watch()`**: 使用"获取或创建"原子语义，同一路径并发创建仅允许一个 creator
- **新增 `release_watch()`**: 减少引用计数，为 0 时停止 observer
- **新增 `get_watch_info()` / `list_watches()`**: 用于调试和可观测性

### 2.2 阶段 B: 引入扇出服务 (runtime_event_fanout.py)
- **新增 `ConnectionSink`**: 每个连接独立的有界缓冲 (deque(maxlen)) + 丢弃计数
- **新增 `RuntimeEventFanout`**: 全局单例服务
  - `ensure_subscribed()`: 确保全局只订阅一次 FILE_WRITTEN/TASK_TRACE
  - `register_connection()`: 注册连接，返回 ConnectionSink
  - `unregister_connection()`: 注销连接，清理 sink
  - `drain_events()`: 拉取事件并返回丢弃计数

### 2.3 阶段 C: 替换 runtime_ws 连接模型 (runtime_ws.py)
- **删除**: 连接内 `file_events`/`task_trace_events` 队列和 `_enqueue_*` 系列函数
- **删除**: MessageBus 直接订阅/取消订阅逻辑
- **新增**: 使用 `RUNTIME_EVENT_FANOUT.register_connection()` 注册连接
- **新增**: 使用 `REALTIME_SIGNAL_HUB.release_watch()` 释放 watcher
- **修改**: `wait_for_update()` 调用传 `workspace=cache_root` 避免跨 workspace 唤醒

### 2.4 阶段 D: 发送链路可观测性重建 (runtime_ws.py)
- **新增 `WebSocketSendError` 异常类**: 包含 error_type、message、original_error
- **重写 `_send_json_locked()`**: 分类处理异常
  - `serialization_error`: JSON 序列化失败
  - `connection_reset`: 连接被对端重置
  - `broken_pipe`: 管道断裂
  - `websocket_disconnect`: 客户端断开
  - `connection_closed`: 连接已关闭
  - `runtime_error`: 其他运行时错误
  - `unknown_error`: 未知错误
- **新增 `_send_json_safe()`**: 包装层，错误时记录审计日志并返回 False
- **修改 `drain_fanout_events()`**: 检查 dropped_count，触发强制 resync

### 2.5 阶段 E: 清理遗留模块与前端代码
- **修改 `app/routers/__init__.py`**: 移除 `websocket` 导入，添加注释说明已弃用
- **修改 `src/frontend/src/runtime/index.ts`**: 注释掉 `useRuntimeSocket` 导出，添加弃用说明
- **保留 `app/routers/websocket.py`**: 文件保留但不再被主应用导入（供旧测试临时使用）

### 2.6 阶段 F: 补充测试矩阵
- **新增 `test_realtime_hub_v2.py`**: 13 个测试
  - 并发 ensure_watch 单 observer 验证
  - ref_count 准确性
  - TOCTOU 保护
  - 工作区隔离
- **新增 `test_runtime_event_fanout.py`**: 19 个测试
  - ConnectionSink 有界缓冲
  - 全局单次订阅不变式
  - 事件分发
  - 并发注册/注销
- **修改 `test_websocket_signal_hub.py`**: 更新为使用新架构

## 3. 实施代码 (Implementation)

### 关键文件变更列表

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/backend/app/services/realtime_hub.py` | 重写 | 新增引用计数和生命周期管理 |
| `src/backend/app/services/runtime_event_fanout.py` | 新增 | 全局事件扇出服务 |
| `src/backend/api/v2/runtime_ws.py` | 重写 | 使用新架构，改进错误处理 |
| `src/backend/app/routers/__init__.py` | 修改 | 移除 websocket 导入 |
| `src/frontend/src/runtime/index.ts` | 修改 | 注释弃用 hook 导出 |
| `src/backend/tests/test_realtime_hub_v2.py` | 新增 | RealtimeSignalHub v2 测试 |
| `src/backend/tests/test_runtime_event_fanout.py` | 新增 | RuntimeEventFanout 测试 |
| `src/backend/tests/test_websocket_signal_hub.py` | 修改 | 更新为使用新架构 |

## 4. 风险说明 (Risk Assessment)

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| watcher 引用计数泄漏 | 中 | 新增 `list_watches()` 可观测性，支持手动检查 |
| fanout 单点故障 | 低 | 单进程设计，失败会随进程重启恢复 |
| 消息丢失（高负载） | 中 | 有界缓冲 + 丢弃计数 + 强制 resync |
| 向后兼容性 | 低 | 保留 websocket.py 文件，仅移除导入 |
| 跨 workspace 信号 | 低 | 已添加 workspace 参数过滤 |

## 5. 影响范围 (Impact Scope)

### 受影响的模块
1. **RealtimeSignalHub**: 完全重写，新增引用计数 API
2. **runtime_ws.py**: 连接模型替换为 fanout
3. **前端 runtime/index.ts**: 弃用 hook 不再导出

### 向后兼容性
- WebSocket 外部协议: 无变化
- 内部 API (`ensure_watch`): 语义变化（现在是 ref_count++）
- 旧 websocket.py: 文件保留但不再被主应用使用

## 6. 验证与测试命令 (Verification & Testing)

### 新增测试
```bash
# RealtimeSignalHub v2 测试
python -m pytest src/backend/tests/test_realtime_hub_v2.py -v

# RuntimeEventFanout 测试
python -m pytest src/backend/tests/test_runtime_event_fanout.py -v

# WebSocket 架构集成测试
python -m pytest src/backend/tests/test_websocket_signal_hub.py -v
```

### 回归测试
```bash
# 现有 WebSocket 测试
python -m pytest src/backend/tests/test_runtime_ws_migration.py -v
python -m pytest src/backend/tests/test_runtime_ws_snapshot_workspace_context.py -v
python -m pytest src/backend/tests/test_ws_connection_audit.py -v
```

### 全部通过结果
```
42 passed in 14.46s
```

## 7. 架构不变式

修复后必须满足以下不变式：

1. **RealtimeSignalHub.ensure_watch(root)**: 同一路径只启动 1 个 observer，ref_count 精确
2. **RealtimeSignalHub.release_watch(root)**: ref_count 为 0 时 observer 必被 stop/join
3. **RuntimeEventFanout**: 同一进程只订阅一次 MessageBus
4. **ConnectionSink**: 连接断开后 sink 必被清理
5. **Backpressure**: dropped_count > 0 时 runtime_ws 必触发 resync
6. **Error Observability**: send 错误必记录 audit，禁止静默吞异常

## 8. 回滚策略

若生产环境出现问题：
1. 临时回退: 切换回上一 tag
2. 诊断: 使用 `REALTIME_SIGNAL_HUB.list_watches()` 和 `RUNTIME_EVENT_FANOUT.get_global_stats()`
3. 热修复: 如为局部 bug，可针对性修复而不回滚

本轮不保留双实现长期共存，避免架构再次分叉。
