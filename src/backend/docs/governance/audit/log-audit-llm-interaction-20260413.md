# LLM 交互链路日志审计报告

**审计时间**: 2026-04-13
**审计任务**: #86 - LLM交互分析
**审计范围**: `polaris/kernelone/llm/` + `polaris/cells/llm/`
**审计性质**: 研究与文档（不修改代码）

---

## 目录

1. [审计执行摘要](#1-审计执行摘要)
2. [异常处理分析](#2-异常处理分析)
3. [Token计算与预算控制](#3-token计算与预算控制)
4. [Provider失败Fallback逻辑](#4-provider失败fallback逻辑)
5. [流式与非流式一致性](#5-流式与非流式一致性)
6. [Prompt注入风险](#6-prompt注入风险)
7. [问题汇总与建议](#7-问题汇总与建议)

---

## 1. 审计执行摘要

### 1.1 审计范围

| 目录 | 文件数 | 主要功能 |
|------|--------|----------|
| `polaris/kernelone/llm/engine/` | 20+ | 执行器、预算、弹性策略 |
| `polaris/kernelone/llm/providers/` | 10+ | Provider注册与抽象 |
| `polaris/kernelone/llm/provider_adapters/` | 5+ | 协议适配器 |
| `polaris/kernelone/llm/toolkit/` | 15+ | 工具定义与解析 |
| `polaris/kernelone/llm/robust_parser/` | 6 | 响应解析与修复 |
| `polaris/kernelone/llm/reasoning/` | 4 | Thinking标签处理 |
| `polaris/cells/llm/tool_runtime/` | 5+ | 角色工具编排 |
| `polaris/cells/llm/dialogue/` | 6 | 对话服务 |

### 1.2 架构评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 异常处理 | ★★★☆☆ | 统一异常体系，嵌套try-catch需优化 |
| Token预算 | ★★★★☆ | 多层预算控制，压缩策略完善 |
| Provider Fallback | ★★★☆☆ | 多Provider支持，fallback机制存在 |
| 流式一致性 | ★★★☆☆ | 路径分离但有桥接 |
| Prompt安全 | ★★★☆☆ | 有sanitizer，标签重写有隔离 |

---

## 2. 异常处理分析

### 2.1 统一异常体系

**发现**: LLM模块使用统一的异常继承体系 (`polaris/kernelone/llm/exceptions.py`)

```
LLMError (from kernelone.errors)
├── ToolParseError (非重试)
├── ResponseParseError (非重试)
├── JSONParseError (非重试)
├── RateLimitError (可重试) → from kernelone.errors
├── AuthenticationError (非重试) → from kernelone.errors
├── LLMTimeoutError (可重试) → from kernelone.errors
├── ToolExecutionError (可重试)
├── BudgetExceededError (可重试)
├── ConfigurationError (非重试)
├── ProviderError
└── NetworkError (可重试)
```

**优点**:
- 统一的 `retryable` 属性判断
- 与 `kernelone.errors` 基础类集成
- 上下文管理器支持 (`tool_execution_context`, `json_parsing_context`)

**问题**:
1. **嵌套异常吞噬风险** (`executor.py` L167-181):
```python
except asyncio.CancelledError:
    raise  # 正确传播
except (RuntimeError, ConnectionError, TimeoutError) as exc:
    logger.exception("[executor] invoke failed")  # 仅记录日志
    response = AIResponse.failure(...)
    return response  # 吞掉了原始异常
```

2. **弹性策略中的异常处理** (`resilience.py` L790-808):
```python
except (RuntimeError, ConnectionError) as exc:
    # 分类错误并决定是否重试
    if not self._is_retryable(last_category):
        return AIResponse.failure(...)  # 非重试错误快速返回
    # 可重试错误继续循环
```

### 2.2 错误分类机制

**位置**: `polaris/kernelone/llm/error_categories.py`

错误分类函数 `classify_error()` 实现了两层分类：
1. **优先**: 从 `LLMError` 子类提取类别
2. **兜底**: 基于关键词的启发式分类

```python
def classify_error(error: Exception) -> ErrorCategory:
    # 第一层: LLMError子类
    category = _category_from_exception(error)
    if category is not None:
        return category
    # 第二层: 关键词匹配
    error_str = str(error).lower()
    if "timeout" in error_str: return TIMEOUT
    if "rate limit" in error_str: return RATE_LIMIT
    # ...
```

**观察**:
- 语义层错误 (`INVALID_RESPONSE`, `JSON_PARSE`, `CONFIG_ERROR`) 快速失败
- 传输层错误 (`TIMEOUT`, `RATE_LIMIT`, `NETWORK_ERROR`) 进入重试循环

### 2.3 异常处理问题汇总

| 问题 | 位置 | 严重程度 | 说明 |
|------|------|----------|------|
| 异常信息丢失 | `executor.py` L172 | 中 | 捕获后仅记录日志，原始异常丢失 |
| 双重异常处理 | `resilience.py` L790 | 低 | RuntimeError/ConnectionError在两处处理 |
| 错误消息冗余 | `executor.py` L427-432 | 低 | 同一错误字符串重复构建 |

---

## 3. Token计算与预算控制

### 3.1 Token估算器架构

**位置**: `polaris/kernelone/llm/engine/token_estimator.py`

```python
class TokenEstimator:
    CHARS_PER_TOKEN = 4
    CJK_CHARS_PER_TOKEN = 2
    CODE_CHARS_PER_TOKEN = 3
```

**估算策略**:
1. **真实Tokenizer**: 优先使用 `tiktoken` (cl100k_base, o200k_base)
2. **启发式估算**: 回退策略，根据内容类型调整

```python
@classmethod
def estimate(cls, text: str, content_type: str = "general", tokenizer_hint: str | None = None) -> int:
    if tokenizer_hint:
        real_count = cls._estimate_with_real_tokenizer(text, tokenizer_hint)
        if real_count is not None:
            return real_count
    return cls._heuristic_estimate(text, content_type)  # 兜底
```

**问题发现**:
- `tokenizer_hint` 为 `None` 时直接使用启发式
- 某些Provider未正确传递 `tokenizer_hint`

### 3.2 预算管理器

**位置**: `polaris/kernelone/llm/engine/prompt_budget.py`

`TokenBudgetManager` 实现了多层次预算控制：

```python
class TokenBudgetManager:
    def enforce(self, input_text: str, model_spec: ModelSpec, ...) -> TokenBudgetDecision:
        # 1. 计算安全边距 (5% 或 2048 tokens)
        safety_margin_tokens = max(2048, int(max_context_tokens * 0.05))
        
        # 2. 计算可用prompt tokens
        allowed_prompt_tokens = max_context_tokens - reserve_output_tokens - safety_margin_tokens
        
        # 3. 检查是否超限
        if requested_prompt_tokens <= allowed_prompt_tokens:
            return allowed=True
        
        # 4. 路由到压缩器
        router = CompressionRouter(...)
        compressed_text, result = router.route_and_compress(...)
```

**压缩策略** (按优先级):
1. `role_context_compressor`: 对话内容使用角色上下文压缩
2. `code_rules`: 代码内容删除import和注释
3. `line_compaction`: 通用行压缩，保留头尾
4. `hard_trim`: 最终兜底硬截断

### 3.3 Token计算问题汇总

| 问题 | 位置 | 严重程度 | 说明 |
|------|------|----------|------|
| 估算精度 | `token_estimator.py` L68-100 | 中 | 启发式对混合内容精度有限 |
| 预算传递 | `executor.py` L388-404 | 低 | 压缩结果需额外传递到context |
| 安全边距 | `prompt_budget.py` L433 | 低 | 固定5%/2048可能不适用于所有模型 |

---

## 4. Provider失败Fallback逻辑

### 4.1 Provider注册与发现

**位置**: `polaris/kernelone/llm/providers/registry.py`

```python
class ProviderManager:
    def get_provider_instance(self, provider_type: str) -> BaseProvider | None:
        normalized = self._normalize_provider_type(provider_type)
        # 懒加载单例模式
```

**已知Provider类型**:
- `openai`, `openai_compat`
- `anthropic`, `claude`
- `ollama`
- `minimax`
- `kimi`
- `gemini_api`, `gemini_cli`

### 4.2 弹性策略

**位置**: `polaris/kernelone/llm/engine/resilience.py`

**RetryConfig**:
```python
@dataclass
class RetryConfig:
    max_attempts: int = 2  # 默认 2 次尝试
    base_delay: float = 1.0
    max_delay: float = 30.0
    retryable_errors: list[ErrorCategory] = [
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.NETWORK_ERROR,
    ]
    transport_only: bool = True  # 仅重试传输层错误
```

**重试决策**:
```python
def is_retryable_by_category(self, category: ErrorCategory) -> bool:
    # 语义层错误: fast-fail
    if category in (INVALID_RESPONSE, JSON_PARSE, CONFIG_ERROR):
        return False
    # 传输层错误: 通过配置判断
    return category in self.retry_config.retryable_errors
```

### 4.3 熔断器

**位置**: `polaris/kernelone/llm/engine/resilience.py`

```python
class CircuitBreaker:
    # 状态转换
    CLOSED → OPEN: 失败次数超过阈值
    OPEN → HALF_OPEN: 恢复超时后
    HALF_OPEN → CLOSED: 成功次数超过阈值
```

**配置**:
```python
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 3
    success_threshold: int = 2
```

### 4.4 多Provider Fallback

**位置**: `polaris/kernelone/llm/engine/resilience.py`

```python
class MultiProviderFallbackManager:
    async def invoke(self, *args, **kwargs) -> FallbackExecutionResult:
        for attempts, endpoint in enumerate(self._providers, start=1):
            try:
                value = await endpoint.invoke(*args, **kwargs)
                return result
            except self._fallback_on_exception_types as exc:
                if is_last or not self._should_fallback(exc):
                    raise
                fallback_used = True  # 切换到下一个Provider
```

**Fallback触发条件**:
- HTTP 429, 500, 502, 503, 504
- 网络异常

### 4.5 Provider失败处理问题汇总

| 问题 | 位置 | 严重程度 | 说明 |
|------|------|----------|------|
| Provider实例缓存 | `registry.py` L94-116 | 低 | 单例模式，失败后仍使用缓存 |
| Fallback链单一 | `resilience.py` L544-577 | 中 | 仅支持顺序fallback，无智能路由 |
| 健康检查 | `registry.py` L252-277 | 低 | `health_check_all` 异常被吞噬 |

---

## 5. 流式与非流式一致性

### 5.1 执行器架构

**非流式**: `polaris/kernelone/llm/engine/executor.py` → `AIExecutor.invoke()`
**流式**: `polaris/kernelone/llm/engine/stream/executor.py` → `StreamExecutor.invoke_stream()`

### 5.2 非流式执行路径

```python
async def invoke(self, request: AIRequest) -> AIResponse:
    # 1. 解析provider和model
    provider_id, model = self._resolve_provider_model(request)
    
    # 2. 获取provider实例
    provider_instance = get_provider_manager().get_provider_instance(provider_type)
    
    # 3. 构建调用配置
    invoke_cfg = self._build_invoke_config(provider_cfg, request.options)
    
    # 4. 预算检查
    budget_decision = self.token_budget.enforce(...)
    
    # 5. 执行调用
    result = await _invoke_with_timeout(
        asyncio.to_thread(provider_instance.invoke, prompt_input, model, invoke_cfg)
    )
    
    # 6. 标准化响应
    return AIResponse.success(output=output, usage=result.usage, ...)
```

### 5.3 流式执行路径

```python
async def invoke_stream(self, request: AIRequest) -> AIStreamGenerator:
    # 1. 解析和预算检查 (同非流式)
    ...
    
    # 2. 检测provider是否支持结构化流
    if _provider_supports_structured_stream(provider_instance):
        # 结构化流路径 (首选)
        async for event in self._invoke_structured_stream(...):
            yield event
    else:
        # 文本流路径 (兼容)
        async for event in self._invoke_text_stream(...):
            yield event
```

### 5.4 关键一致性检查点

| 检查项 | 非流式 | 流式 | 一致性 |
|--------|--------|------|--------|
| Provider解析 | `_resolve_provider_model` | `_resolve_provider_model` | ✓ 一致 |
| 预算检查 | `token_budget.enforce` | `token_budget.enforce` | ✓ 一致 |
| 错误分类 | `classify_error` | `classify_error` | ✓ 一致 |
| 响应标准化 | `ResponseNormalizer.extract_json_object` | `ResponseNormalizer.extract_json_object` | ✓ 一致 |
| Usage估算 | `Usage.estimate` | `Usage.estimate` | ✓ 一致 |
| 超时控制 | `_INVOKE_TIMEOUT_SEC` | `stream_timeout` | ⚠️ 分离 |

### 5.5 流式特有逻辑

**Tool Call累积** (`stream/executor.py` L617-725):
```python
async def _invoke_structured_stream(self, ...):
    pending_tool_calls: dict[str, _ToolCallAccumulator] = {}
    
    async for raw_event in provider_instance.invoke_stream_events(...):
        decoded = adapter.decode_stream_event(raw_event)
        
        for tool_call in decoded.tool_calls:
            # 累积参数直到完整
            emitted = self._accumulate_stream_tool_call(pending_tool_calls, tool_call, ...)
            if emitted:
                yield AIStreamEvent.tool_call_event(emitted)
```

### 5.6 流式与非流式一致性问题汇总

| 问题 | 位置 | 严重程度 | 说明 |
|------|------|----------|------|
| 超时分离 | `executor.py` L86-93 vs `stream/executor.py` L518 | 低 | 超时配置分离 |
| 错误处理路径 | `executor.py` L167-181 vs `stream/executor.py` L438-462 | 中 | 流式有专门异常处理 |
| Provider检测 | `stream/executor.py` L362 | 中 | 结构化流检测依赖 `invoke_stream_events` 方法存在 |

---

## 6. Prompt注入风险

### 6.1 Reasoning标签重写

**位置**: `polaris/kernelone/llm/reasoning/sanitizer.py`

**防护机制**: 会话级别的标签混淆

```python
class ReasoningSanitizer:
    def rewrite(self, text: str) -> SanitizationResult:
        # 将标准标签替换为会话唯一标签
        # <think> → <think:abc123>
        # </think> → </think:abc123>
```

**风险评估**:
- **输入重写**: 将用户输入中的标准 `<think>` 标签重写为会话特定标签
- **输出恢复**: 展示时可恢复为标准标签
- **流式支持**: `rewrite_chunk()` 处理分块到达的标签

### 6.2 工具参数验证

**位置**: `polaris/kernelone/llm/toolkit/definitions.py`

```python
def validate_arguments(self, arguments: dict[str, Any]) -> tuple[bool, list[str]]:
    # 1. 检查必填参数
    required_names = {p.name for p in self.parameters if p.required}
    missing = required_names - provided_names
    
    # 2. 类型检查
    for name, value in arguments.items():
        expected_type = param_map[name].type
        # 基础类型验证
```

**观察**: 仅做基础类型验证，无深度注入防护

### 6.3 Prompt构建中的潜在风险

**位置**: `polaris/kernelone/llm/provider_adapters/base.py`

```python
def serialize_transcript_for_prompt(state: ConversationStateLike) -> str:
    lines: list[str] = []
    for item in state.transcript:
        if item_type == "UserMessage":
            lines.append(f"User: {item.content}")  # 直接拼接
        elif item_type == "AssistantMessage":
            lines.append(f"Assistant: {item.content}")
        # ...
    return "\n".join(lines)
```

**潜在风险**: 对话内容直接拼接进prompt，无转义处理

### 6.4 已知风险点

| 风险点 | 位置 | 严重程度 | 说明 |
|--------|------|----------|------|
| 标签注入 | `sanitizer.py` | 低 | 有会话隔离，但标准标签仍可重写 |
| 内容拼接 | `provider_adapters/base.py` L42-66 | 中 | 无特殊字符转义 |
| 工具参数 | `definitions.py` L155-192 | 低 | 基础类型检查，无深度验证 |

### 6.5 Prompt注入缓解措施

1. **Reasoning标签隔离**: 会话级别标签混淆
2. **错误消息清理**: `ResponseNormalizer.extract_json_object` 提取结构化数据
3. **工具白名单**: `RoleToolGateway` 基于角色配置过滤工具

---

## 7. 问题汇总与建议

### 7.1 优先级矩阵

```
                    高影响
                        │
         ┌──────────────┼──────────────┐
         │              │              │
    ┌────┴────┐   ┌─────┴─────┐  ┌────┴────┐
    │ 异常    │   │ Fallback  │  │ Prompt  │
    │ 信息丢失 │   │ 路由单一  │  │ 注入    │
    └─────────┘   └───────────┘  └─────────┘
         │              │              │
         ▼              ▼              ▼
    ┌─────────────────────────────────────────┐
    │  中优先级：建议改进，但不阻塞             │
    └─────────────────────────────────────────┘
```

### 7.2 关键发现

| 类别 | 数量 | 严重程度分布 |
|------|------|--------------|
| 异常处理 | 3 | 1中、2低 |
| Token预算 | 3 | 2中、1低 |
| Provider Fallback | 3 | 1中、2低 |
| 流式一致性 | 3 | 1中、2低 |
| Prompt安全 | 4 | 1中、3低 |

### 7.3 改进建议

#### 高优先级
1. **异常信息保留**: 在 `executor.py` L172考虑保留原始异常链
2. **Fallback路由优化**: 支持更灵活的Provider fallback策略

#### 中优先级
3. **超时配置统一**: 将非流式和流式超时配置统一管理
4. **Provider健康状态**: 添加失败后的实例刷新机制
5. **内容拼接转义**: 在 `serialize_transcript_for_prompt` 中添加特殊字符处理

#### 低优先级
6. **Tokenizer Hint传递**: 确保所有Provider正确传递tokenizer类型
7. **预算日志增强**: 在预算超限时记录更详细的压缩统计

### 7.4 与上次审计对比

参考: `docs/audit/llm_tool_calling/MASTER_AUDIT_REPORT_20260328.md`

**延续的问题** (24个问题中):
- A-1: 三处Tool定义同步 (部分改善 → definitions.py标记废弃)
- B-1: Parser架构 (结构化解析器已添加)
- C-1: 双ProviderManager (已统一到 infrastructure)
- D-1: 角色策略双路径 (仍存在)

**本次新增关注点**:
- 流式/非流式一致性细节
- Prompt注入的深度分析
- Token预算压缩策略

---

## 附录：审计文件索引

| 文件 | 审计重点 |
|------|----------|
| `polaris/kernelone/llm/engine/executor.py` | 非流式执行、异常处理 |
| `polaris/kernelone/llm/engine/stream/executor.py` | 流式执行、一致性 |
| `polaris/kernelone/llm/engine/resilience.py` | 重试、熔断、Fallback |
| `polaris/kernelone/llm/engine/prompt_budget.py` | Token预算、压缩 |
| `polaris/kernelone/llm/engine/token_estimator.py` | Token估算 |
| `polaris/kernelone/llm/error_categories.py` | 错误分类 |
| `polaris/kernelone/llm/exceptions.py` | 异常体系 |
| `polaris/kernelone/llm/providers/registry.py` | Provider注册 |
| `polaris/kernelone/llm/providers/base_provider.py` | Provider抽象 |
| `polaris/kernelone/llm/provider_adapters/base.py` | 协议适配器 |
| `polaris/kernelone/llm/reasoning/sanitizer.py` | 标签重写 |
| `polaris/kernelone/llm/toolkit/definitions.py` | 工具定义 |
| `polaris/kernelone/llm/robust_parser/core.py` | 响应解析 |

---

*审计完成时间*: 2026-04-13
*审计方法*: 代码审查 + 模式分析
*审计性质*: 研究与文档（不修改代码）