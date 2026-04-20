# Chronos Mirror: 确定性回放调试层 (2026-04-04)

## 1. 问题诊断 (Schrödinger's Bug Analysis)

### 1.1 现有 `CacheReplay` 的架构缺陷

| 缺陷 | 描述 | 影响 |
|------|------|------|
| **函数级录制** | `@cache.replay` 装饰器需要调用方显式包装 | 业务代码必须修改，无法拦截 session 内所有 HTTP 调用 |
| **HTTP 细节缺失** | `Recording` 仅含 `request_key` (SHA256) + `response` | 无法验证请求是否与历史完全一致（URL/headers/status 均未知） |
| **无网络层拦截** | `CacheReplay` 是函数装饰器，非 aioresponses 式网络拦截 | 第三方库的 HTTP 调用无法被录制 |
| **多 Provider 路由缺失** | LLM 调用涉及多 Provider (OpenAI/Anthropic/Gemini) | 无 provider 级别路由和拦截 |
| **无时序保证** | `DeterministicMockProvider` 按索引访问 | cassette 内多个请求的时间顺序不保证 |

### 1.2 `aioresponses` vs `CacheReplay` 架构对比

```
aioresponses (网络层拦截):
┌─────────────────────────────────────────────────────────────┐
│  Business Code (无需修改)                                    │
│      │                                                       │
│      ▼                                                       │
│  httpx.AsyncClient.send() ◄──── Patch (unittest.mock)       │
│      │                                                       │
│      ▼                                                       │
│  Cassette Lookup: (method + url + body_hash)                │
│      │                                                       │
│      ├── Match Found ──► Return Recorded Response            │
│      └── No Match ──► Raise UnrecordedRequestError           │
└─────────────────────────────────────────────────────────────┘

CacheReplay (函数级装饰器):
┌─────────────────────────────────────────────────────────────┐
│  @cache.replay  ◄── 必须显式包装                             │
│  async def my_llm_call(messages):                           │
│      return await actual_llm_call(messages)                  │
└─────────────────────────────────────────────────────────────┘
```

## 2. 架构蓝图 (Time-Travel Architecture)

### 2.1 核心组件拓扑

```
┌──────────────────────────────────────────────────────────────────────┐
│  ShadowReplay(cassette_id="task-123", mode="both")                  │
│      │                                                               │
│      ├── __aenter__:                                                 │
│      │     ├── Load/Create Cassette                                  │
│      │     ├── Patch httpx.AsyncClient.send()                        │
│      │     └── Register sanitization hook                            │
│      │                                                               │
│      ├── __aexit__:                                                  │
│      │     ├── Save Cassette (if record mode)                        │
│      │     └── Restore original send()                               │
│      │                                                               │
│      └── Context: session-level, non-invasive                        │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ShadowRecorder (录制层)                                              │
│      │                                                               │
│      ├── _patched_send() ──► Intercept HTTP                          │
│      │                                                               │
│      ├── SanitizationHook.sanitize() ──► 脱敏落盘                    │
│      │                                                               │
│      └── CassetteWriter ──► {cassette_id}.jsonl                       │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ShadowPlayer (回放层)                                                │
│      │                                                               │
│      ├── _find_recording(method, url, body_hash) ──► 查找匹配项      │
│      │                                                               │
│      ├── _verify_request() ──► 验证请求完整性                         │
│      │                                                               │
│      └── UnrecordedRequestError ──► 阻断未录制请求                   │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Cassette 格式 (扩展自 Recording)

```json
{
  "cassette_id": "task-123",
  "created_at": "2026-04-04T12:00:00Z",
  "mode": "record",
  "version": "1.0",
  "entries": [
    {
      "sequence": 0,
      "timestamp": "2026-04-04T12:00:00.123Z",
      "request": {
        "method": "POST",
        "url": "https://api.openai.com/v1/chat/completions",
        "headers": {
          "Authorization": "Bearer [REDACTED]",
          "Content-Type": "application/json"
        },
        "body_hash": "a1b2c3d4...",
        "body_preview": "{\"model\": \"gpt-4\", \"messages\": [...]}"
      },
      "response": {
        "status_code": 200,
        "headers": {"Content-Type": "application/json"},
        "body_hash": "e5f6g7h8...",
        "body_preview": "{\"choices\": [{\"message\": {...}}]}",
        "tokens_used": 150
      },
      "latency_ms": 250.5
    }
  ],
  "sanitized": true,
  "sanitizer_version": "1.0"
}
```

### 2.3 复用现有基础设施

| 现有组件 | 复用方式 |
|----------|----------|
| `SanitizationHook` | 对 cassette 落盘前脱敏 (API Key, Token, Bearer) |
| `GlobalStateIsolationManager` | patch/restore 模式保存 `httpx.AsyncClient.send` 原始引用 |
| `CacheReplay.mode` 设计 | 复用 `record`/`replay`/`both` 三模式 |
| `conftest.py` markers | 添加 `@pytest.mark.shadow_replay` |

## 3. 核心 API 设计

### 3.1 ShadowReplay 异步上下文管理器

```python
class ShadowReplay:
    """
    Non-invasive HTTP recording and replay via context manager.

    Usage:
        async with ShadowReplay(cassette_id="task-123", mode="both") as replay:
            # All httpx.AsyncClient calls are intercepted
            result = await call_llm_api(prompt)  # Recorded
            result = await call_llm_api(prompt)  # Replayed
    """

    async def __aenter__(self) -> ShadowReplay: ...
    async def __aexit__(self, *args) -> None: ...

    # Mode: "record" | "replay" | "both"
    # Cassette storage: {cache_dir}/{cassette_id}.jsonl
```

### 3.2 关键设计决策

1. **JSONL 格式**: 每行一个 entry，便于流式追加和 diff
2. **body_hash 查找**: SHA256(request_body) 用于精确匹配
3. **UnrecordedRequestError**: replay 模式下未找到请求时抛出，避免静默失败
4. **时序序列号**: `sequence` 字段保证回放顺序

## 4. 实施计划

### Phase 1: 核心引擎 (第一周) ✅
- [x] `shadow_replay/core.py` - `ShadowReplay` 上下文管理器
- [x] `shadow_replay/cassette.py` - Cassette 数据结构
- [x] `shadow_replay/recorder.py` - ShadowRecorder 录制逻辑
- [x] `shadow_replay/player.py` - ShadowPlayer 回放逻辑
- [x] `shadow_replay/http_intercept.py` - httpx 拦截层

### Phase 2: 脱敏集成 (第一周) ✅
- [x] `shadow_replay/sanitization.py` - 复用 SanitizationHook
- [x] 脱敏落盘流程

### Phase 3: pytest 集成 (第二周) ✅
- [x] `shadow_replay/conftest.py` - fixtures 和 markers
- [x] `@pytest.mark.shadow_replay` marker

### Phase 4: 验证与文档 (第二周) ✅
- [x] 使用文档 `shadow_replay/README.md`
- [x] 蓝图文档 `docs/blueprints/CHRONOS_MIRROR_DETERMINISTIC_REPLAY_20260404.md`
- [x] 单元测试 (52 tests passing)
- [x] 集成测试

## 5. 文件结构

```
polaris/kernelone/benchmark/reproducibility/
├── vcr.py                    # 现有 CacheReplay (保留)
├── mocks.py                  # 现有 DeterministicMockProvider (保留)
├── fixtures.py               # 现有 fixtures (保留)
├── conftest.py               # 现有 conftest (保留)
├── shadow_replay/            # 新增: 时空倒影
│   ├── __init__.py
│   ├── core.py               # ShadowReplay 上下文管理器
│   ├── cassette.py           # Cassette 数据结构
│   ├── recorder.py           # 录制逻辑
│   ├── player.py             # 回放逻辑
│   ├── http_intercept.py     # httpx 拦截
│   ├── sanitization.py       # 脱敏集成
│   ├── exceptions.py         # 自定义异常
│   ├── conftest.py           # pytest 集成
│   └── README.md             # 使用文档
```

## 6. 验收标准

1. **零侵入**: 业务代码无需修改，`with ShadowReplay(cassette_id):` 即可拦截
2. **完整录制**: cassette 包含 HTTP 请求/响应完整细节
3. **精确回放**: 基于 `(method + url + body_hash)` 查找匹配项
4. **时序保证**: `sequence` 字段保证回放顺序
5. **敏感信息脱敏**: API Key/Token 落盘前被 `[REDACTED]` 替换
6. **未录制请求阻断**: replay 模式下对未找到的请求抛出 `UnrecordedRequestError`
7. **现有系统兼容**: 复用 `CacheReplay.mode` 设计，与现有 fixtures 共存
