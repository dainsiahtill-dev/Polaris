# Polaris API 架构审计报告

## 1. API 端点清单

| 命名空间 | 方法 | 路径 | 状态 | 备注 |
|---------|------|------|------|------|
| v2 | POST | /v2/pm/chat | 可用 | 仅 PM 角色，非统一接口 |
| v2 | POST | /v2/pm/chat/stream | 可用 | SSE 流式 |
| v2 | GET | /v2/role/{role}/chat/status | 可用 | 5 角色状态查询 |
| v2 | GET | /v2/role/chat/roles | 可用 | 列出支持角色 |
| v2 | CRUD | /v2/roles/sessions/* | 可用 | 会话完整生命周期 |
| v2 | POST | /v2/roles/sessions/{id}/messages/stream | 可用 | 角色对话 SSE |
| v2 | POST | /v2/stream/chat | 可用 | Neural Weave SSE |
| v2 | POST | /v2/stream/chat/backpressure | 可用 | 显式背压 |
| v2 | CRUD | /v2/factory/runs/* | 可用 | 无人值守流水线 |
| v2 | GET | /v2/factory/runs/{id}/stream | 可用 | JetStream SSE 降级轮询 |
| v2 | CRUD | /v2/conversations/* | 可用 | 对话管理 |
| v2 | GET | /v2/roles/capabilities/{role} | 可用 | 角色能力 |
| - | POST | /cognitive-runtime/* | 可用 | 无 v2 前缀，直接暴露 Cell 服务 |
| - | CRUD | /agent/* | 可用 | 无 v2 前缀，V1 兼容层 |
| - | CRUD | /pm/* | 可用 | 无 v2 前缀，文档/任务/需求管理 |
| - | POST | /docs/init/* | 可用 | 无 v2 前缀 |
| - | POST | /llm/interview/* | 可用 | 无 v2 前缀 |
| - | GET/POST | /llm/* | 可用 | 配置/状态/Provider |
| - | GET/POST | /runtime/* | 可用 | 存储布局/清理 |
| - | GET/POST | /settings, /health, /state/snapshot | 可用 | 系统级 |
| - | POST | /agents/apply, /agents/feedback | 可用 | AGENTS.md 管理 |

## 2. 缺失的关键 API 端点

1. **统一角色对话入口** — CLAUDE.md 声明 `POST /v2/role/{role}/chat` 为 5 角色统一入口，实际仅实现了 `/v2/pm/chat`，architect/chief_engineer/director/qa 缺失独立对话端点。
2. **Director v2 API** — CLAUDE.md 提及 `/v2/director/*`，当前无对应实现。
3. **PM v2 API** — `/pm/*` 未纳入 `/v2/pm/*` 命名空间。
4. **Chief Engineer / QA 独立流式端点** — 角色会话路由依赖 `/v2/roles/sessions/{id}/messages/stream`，无按角色的快捷入口。
5. **OpenAPI Schema 端点** — 未显式配置 `response_model`，Swagger 自动生成质量差。

## 3. API 契约不一致点

1. **返回类型不统一**：大量端点使用 `dict[str, Any]` 而非 Pydantic `response_model`（如 `role_chat.py`、`pm_chat.py`），导致 OpenAPI 无法生成准确 Schema。
2. **错误格式不一致**：`_shared.py` 定义了 `StructuredHTTPException`（ADR-003 格式），但许多路由仍直接返回 `{"ok": False, "error": str}` 或抛出裸 `HTTPException`，客户端需兼容多种错误形状。
3. **命名空间混乱**：`cognitive_runtime.py` 使用 `/cognitive-runtime`（无 v2），`agent.py` 使用 `/agent`（无 v2），`pm_management.py` 使用 `/pm`（无 v2），与 v2 目标态混杂。
4. **SSE 事件类型不统一**：`sse_utils.py` 使用 `event: complete/error/ping`，`stream_router.py` 使用 `event: done/error`，`agent.py` 使用 `event: content_chunk/thinking_chunk/tool_call/complete/done`，前端需处理多套 SSE 方言。
5. **认证覆盖缺口**：`/health`、`/ready`、`/live`、`/v2/stream/health` 无 `require_auth`，在暴露环境中存在信息泄露风险。

## 4. SSE 端点健壮性评估

| 维度 | 状态 | 说明 |
|------|------|------|
| 重连 | 部分 | `SSEJetStreamConsumer` 支持 `last_event_id` 游标恢复；但无显式客户端重连策略或 `retry` 字段 |
| 错误处理 | 良好 | `sse_jetstream_generator` 修复了 B4（异常遮蔽）和 B5（UnicodeDecodeError）；`finally` 块确保 `disconnect()` 总是被调用 |
| 背压 | 良好 | `_SSE_QUEUE_MAX_SIZE = 50` 限制队列增长；`stream_router.py` 提供显式 `AsyncBackpressureBuffer` 端点 |
| 安全 | 良好 | S1-S6 加固：payload 大小限制、subject 模式校验、workspace_key 校验、HMAC 签名、replay 窗口、随机 consumer 名 |
| 测试覆盖 | 良好 | `test_sse_utils.py` + `test_sse_regression.py` 覆盖异常保留、断连清理、负游标拒绝、非法 subject 拒绝 |

## 5. 认证/授权中间件覆盖

- **认证**：`require_auth` 仅校验 Bearer Token（Authorization Header），无二进制令牌泄漏防护（query param 已禁止）。
- **限流**：`RateLimitMiddleware` 存在但默认依赖环境变量启用，未在全局强制挂载；token-bucket 算法正确，支持渐进式退避（30s -> 240s）。
- **审计**：`AuditContextMiddleware` 自动注入 X-Trace-ID / X-Run-ID / X-Task-ID，但仅透传未与请求授权上下文绑定。
- **RBAC**：完全缺失，所有通过认证的请求拥有同等权限。
- **Metrics/Logging**：`MetricsMiddleware` 和 `RequestLoggingMiddleware` 覆盖完整，但同样依赖环境变量启用。

## 6. 公开 API 与内部 API 边界

| 问题 | 严重程度 | 说明 |
|------|---------|------|
| 内部服务直接暴露 | 中 | `cognitive_runtime.py` 直接将 `CognitiveRuntimePublicService` 的 Command/Query 对象映射为 HTTP 端点，缺少 API 层契约转换 |
| Cell internal 穿透 | 中 | `RoleSessionContextMemoryService`（Context OS 内存）通过 `/v2/roles/sessions/{id}/memory/*` 直接暴露，未经过聚合层 |
| 无版本控制的遗留路由 | 低 | `/llm/*`、`/docs/*`、`/agents/*` 等大量路由无版本前缀，未来变更将破坏兼容性 |

## 7. 推荐的 API 优先级开发列表

1. **P0 — 统一角色对话端点**：实现 `POST /v2/role/{role}/chat` 和 `/v2/role/{role}/chat/stream`，复用 `generate_role_response` / `generate_role_response_streaming`，替换现有的 `/v2/pm/chat` 专用实现。
2. **P0 — 错误响应标准化**：为所有 v2 路由强制使用 `StructuredHTTPException` 或统一 `{"ok": false, "code": "...", "message": "...", "details": {}}` 格式，清理裸 `HTTPException` 和直接返回 `dict` 的端点。
3. **P1 — v2 命名空间归一化**：将 `/pm/*` 迁移至 `/v2/pm/*`，`/cognitive-runtime/*` 迁移至 `/v2/cognitive-runtime/*`，`/agent/*` 迁移至 `/v2/agent/*`；旧路由保留 shim 兼容层。
4. **P1 — OpenAPI 契约补全**：为所有 v2 端点补充 Pydantic `response_model`，使 Swagger UI 和前端类型生成可用。
5. **P2 — SSE 事件类型统一**：制定 canonical SSE event schema（`content_chunk/thinking_chunk/tool_call/tool_result/complete/error/ping`），统一 `sse_utils.py`、`stream_router.py`、`agent.py` 的输出。
6. **P2 — 认证覆盖补全**：为 `/v2/stream/health` 等生产环境端点添加 `require_auth`，或至少添加 IP 白名单/内部网络校验。
7. **P3 — RBAC 基础框架**：在 `require_auth` 中引入角色声明解析（JWT claims 或 token metadata），为后续细粒度授权预留扩展点。