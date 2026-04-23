# CLI 适配层 v2 迁移指南

## 概述

本文档描述了 Polaris 后端从「厚 CLI + 薄服务」到「薄 CLI + 核心 OO 化」的架构迁移。

## 变更摘要

### 架构变化

| 层级 | 旧架构 | 新架构 |
|------|--------|--------|
| CLI 入口 | 包含业务逻辑 | 仅参数解析 + 调用适配 |
| 启动核心 | 分散在 server.py | 统一到 `BackendBootstrapper` |
| 进程编排 | PM/Director 各一套 | 统一到 `RuntimeOrchestrator` |
| 配置管理 | 多处加载点 | 统一到 `ConfigSnapshot` |

### 新增模块

| 模块 | 路径 | 职责 |
|------|------|------|
| RuntimeOrchestrator | `core/runtime_orchestrator.py` | 统一进程编排 |
| ProcessAuditEvent | 同上 | 结构化审计事件 |
| ConfigSnapshot | `domain/models/config_snapshot.py` | 不可变配置快照 |
| BackendLaunchRequest | `application/dto/backend_launch.py` | 启动请求 DTO |
| ProcessLaunchRequest | `application/dto/process_launch.py` | 进程启动 DTO |

### 修改的模块

| 模块 | 变更类型 | 说明 |
|------|----------|------|
| `server.py` | 重构 | 精简为薄 CLI 适配层 |
| `app/routers/system.py` | 增强 | `/health` 端点增加 PM/Director 状态 |

## API 兼容性

### 保持不变的接口

- **CLI 参数**: 所有现有参数名和默认值保持不变
- **API 端点**: `/v2/pm/*`, `/v2/director/*` 响应结构不变
- **backend_started 事件**: 格式保持不变
- **环境变量**: 所有 `KERNELONE_*` 环境变量保持不变

### 新增的 API

- **`GET /health` 增强**: 返回 `pm` 和 `director` 子状态

## 使用示例

### 启动后端

```bash
# 启动方式不变
python src/backend/server.py --workspace /path/to/workspace --port 49977
```

### 启动 PM

```bash
# 启动方式不变
python src/backend/scripts/pm/cli.py --workspace /path/to/workspace --run-once
```

### 启动 Director

```bash
# 启动方式不变
python src/backend/scripts/loop-director.py --workspace /path/to/workspace
```

### 使用新的 RuntimeOrchestrator

```python
from core.runtime_orchestrator import RuntimeOrchestrator, get_default_orchestrator

# 获取默认编排器
orchestrator = get_default_orchestrator()

# 启动 PM
result = await orchestrator.spawn_pm(
    workspace=Path("/path/to/workspace"),
    mode=RunMode.SINGLE,
)

# 监听审计事件
def on_event(event):
    print(event.to_json())
    
orchestrator.add_event_listener(on_event)
```

### 使用增强的 /health 端点

```bash
# 获取完整健康状态
curl -H "Authorization: Bearer <token>" http://localhost:49977/health

# 响应示例
{
  "ok": true,
  "version": "0.1",
  "timestamp": "2026-03-06T...",
  "pm": {"running": false, "status": "idle"},
  "director": {"state": "idle", "status": "idle"}
}
```

## 配置优先级

配置合并遵循以下优先级（从低到高）:

1. **DEFAULT**: 代码中的硬编码默认值
2. **PERSISTED**: `.polaris/config.json` 文件
3. **ENV**: 环境变量 (`KERNELONE_*`)
4. **CLI**: 命令行参数

### 示例

```python
from domain.models.config_snapshot import ConfigSnapshot, SourceType

# 创建配置快照
snapshot = ConfigSnapshot.merge_sources(
    default={"server.port": 49977, "logging.level": "INFO"},
    persisted={"server.port": 49988},
    env={"logging.level": "DEBUG"},
    cli={"server.port": 49999},
)

# 最终值
assert snapshot.get("server.port") == 49999  # CLI 最高优先级
assert snapshot.get("logging.level") == "DEBUG"  # ENV 最高优先级

# 查询来源
assert snapshot.get_source("server.port") == SourceType.CLI
```

## 审计事件

RuntimeOrchestrator 会发出以下结构化事件:

```json
{"event": "process_starting", "type": "pm", "workspace": "...", "pid": 1234}
{"event": "process_started", "type": "pm", "workspace": "...", "pid": 1234, "duration_ms": 150}
{"event": "process_retrying", "type": "director", "attempt": 2, "max_attempts": 3}
{"event": "process_failed", "type": "pm", "exit_code": 1, "error": "..."}
{"event": "process_completed", "type": "director", "duration_ms": 5000}
```

## 迁移检查清单

- [x] CLI 参数名保持不变
- [x] API 响应结构无破坏变更
- [x] backend_started 事件格式兼容
- [x] 环境变量兼容性
- [x] 新增 /health 端点增强

## 相关文档

- [ADR-001: Thin CLI Adapter Policy](../architecture/adr-001-thin-cli-policy.md)
- [ConfigSnapshot 设计](../core/config-snapshot.md)
- [端口接口定义](../ports/README.md)

## 常见问题

### Q: 现有脚本还能正常工作吗?

A: 是的。所有现有的 CLI 脚本 (`pm`, `director`, `server.py`) 保持完全兼容。

### Q: 如何使用新的 RuntimeOrchestrator?

A: 请参考上面的「使用新的 RuntimeOrchestrator」示例。

### Q: 旧代码是否需要修改?

A: 不需要。现有代码可以继续工作，新架构向后兼容。

---

*本文档最后更新于 2026-03-06*
