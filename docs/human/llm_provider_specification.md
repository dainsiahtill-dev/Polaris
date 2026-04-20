# LLM 接入规范

> **版本**: v1.0.0 | **状态**: 活跃 | **最后更新**: 2026-02-07

本文档定义了 Polaris 系统中所有大语言模型（LLM）提供商必须遵循的接入规范，确保新模型接入时能够保持架构一致性，特别是思考过程解析、明文输入处理、实时流式对话等核心特性的统一支持。

---

## 1. 核心架构要求

### 1.1 BaseProvider 继承规范

所有 LLM 提供商实现必须继承 `BaseProvider` 基类，并实现所有抽象方法。继承结构确保了 API 调用模式的一致性，使得 Director 和 QA 模块能够以统一的方式与不同提供商交互。

```python
from .base_provider import BaseProvider, ProviderInfo, ValidationResult
from ..types import HealthResult, InvokeResult, ModelInfo, ModelListResult

class CustomProvider(BaseProvider):
    """自定义 LLM 提供商实现"""
    
    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Custom Provider",
            type="custom",
            description="自定义模型提供商",
            version="1.0.0",
            author="Polaris Team",
            documentation_url="https://docs.example.com/api",
            supported_features=[
                "thinking_extraction",
                "model_listing",
                "health_check",
                "streaming",
                "chinese_support",
            ],
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="API"
        )
```

必须实现的核心方法包括：`get_provider_info()` 返回提供商元数据、`get_default_config()` 提供默认配置、`validate_config()` 验证用户配置、`health()` 检查服务连通性、`list_models()` 获取可用模型列表、`invoke()` 执行推理请求。这些方法构成了 LLM 提供商的完整生命周期管理，任何未实现的方法都会导致接入失败。

### 1.2 ProviderInfo 规范

`ProviderInfo` 是提供商的身份凭证，定义了提供商的能力边界和特性支持声明。正确填写 `supported_features` 列表至关重要，因为这决定了系统对该提供商的功能预期和能力调用。

| 特性标识 | 说明 | 必需程度 |
|---------|------|---------|
| `thinking_extraction` | 支持从响应中提取思考过程 | **必需** |
| `model_listing` | 支持通过 API 获取模型列表 | **必需** |
| `health_check` | 支持健康检查端点 | **必需** |
| `streaming` | 支持 Server-Sent Events 流式响应 | 推荐 |
| `chinese_support` | 原生支持中文输入输出 | 推荐 |
| `multimodal` | 支持图像、音频等多模态输入 | 可选 |
| `file_operations` | 支持文件读取和处理 | 可选 |
| `autonomous_access` | 支持自主文件访问 | 可选 |

### 1.3 配置验证机制

所有提供商必须实现 `validate_config()` 方法，该方法负责检查用户提供配置的完整性和合法性。验证逻辑应该返回标准化的 `ValidationResult`，包含规范化后的配置、验证错误列表和警告信息。

```python
@classmethod
def validate_config(cls, config: Dict[str, Any]) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    normalized = dict(config)
    
    # 必需字段验证
    base_url = str(config.get("base_url") or "").strip()
    if not base_url:
        errors.append("Base URL is required")
    else:
        normalized["base_url"] = base_url.rstrip("/")
    
    api_key = config.get("api_key", "")
    if not api_key:
        errors.append("API key is required")
    
    # 可选字段警告
    max_tokens = int(config.get("max_tokens") or 0)
    if max_tokens > 196608:
        warnings.append(f"max_tokens {max_tokens} exceeds recommended limit 196608")
        normalized["max_tokens"] = 196608
    
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        normalized=normalized
    )
```

配置验证遵循"早失败"原则，任何必需字段缺失都应立即报错，而不是等到实际调用时才暴露问题。同时，警告信息用于提示用户潜在的配置风险，如参数超出模型支持范围等情况。

---

## 2. 思考过程解析规范

### 2.1 思考内容的重要性

思考过程（Thinking Process）是 LLM 拟人化能力的核心组成部分。在 Polaris 系统中，思考内容被用于"自言自语"（Inner Voice）功能，让用户能够观察 AI 的推理过程，增强交互的真实感和透明度。因此，**所有提供商都必须实现思考内容提取能力**，无论原 API 是否原生支持思考标记。

不同提供商的思考内容呈现方式各异：OpenAI 使用 `</thinking>` XML 标签、MiniMax 使用 `reasoning_content` JSON 字段、Claude 可能使用其他格式。统一适配这些格式是接入规范的核心要求之一。

### 2.2 InvokeResult 思考字段定义

`InvokeResult` 数据类包含 `thinking` 字段，用于承载从 LLM 响应中提取的思考内容。该字段为可选值，当提供商不支持思考内容时返回 `None`。

```python
@dataclass
class InvokeResult:
    ok: bool
    output: str
    latency_ms: int
    usage: Usage
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
    streaming: bool = False
    thinking: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "ok": self.ok,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "usage": self.usage.to_dict(),
            "streaming": self.streaming,
        }
        if self.error:
            payload["error"] = self.error
        if self.raw is not None:
            payload["raw"] = self.raw
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        return payload
```

`thinking` 字段的内容应该是**纯净的思考文本**，不包含任何 XML 标签、格式标记或元数据。如果原始响应包含结构化的思考信息（如 MiniMax 的 `reasoning_details`），应该在 provider 层进行预处理和清洗。

### 2.3 多格式思考内容提取

根据提供商 API 的不同特性，思考内容可能以多种形式出现。Provider 实现需要识别并统一处理这些格式，确保输出的一致性。

**格式一：XML 标签格式**（如 OpenAI 兼容 API）

```
<think>
用户要求我回复"OK"，这是一个简单的指令。
</think>
OK
```

处理方式：使用正则表达式 `re.sub(r'<think[^>]*>.*?<\/think>', '', content, flags=re.DOTALL | re.IGNORECASE)` 提取并移除标签内的内容。

**格式二：JSON 字段格式**（如 MiniMax）

```json
{
  "message": {
    "content": "OK",
    "reasoning_content": "用户要求我回复OK，这是一个简单的指令。"
  }
}
```

处理方式：从 `message.reasoning_content` 字段提取内容，如果为空则检查 `message.reasoning_details` 中的结构化信息。

**格式三：流式响应中的 delta**（如 SSE 格式）

```json
data: {"choices":[{"delta":{"content":"O","reasoning_content":"用户"}}]}
data: {"choices":[{"delta":{"content":"K","reasoning_content":"要求我回复"}}]}
data: {"choices":[{"delta":{"content":".","reasoning_content":"OK。"}}]}
```

处理方式：在流式解析循环中，累积所有 chunk 的 `delta.reasoning_content`，最后合并为一个完整的思考字符串。

### 2.4 思考内容清洗规则

提取后的思考内容需要经过标准化处理，确保格式一致性和可用性。清洗规则包括：移除首尾空白字符、替换多余空白为单个空格、移除潜在的 Markdown/HTML 标记、保持中文标点符号的正常使用。

```python
def _clean_content(self, content: str) -> str:
    if not content:
        return ""
    # 移除 XML 思考标签
    cleaned = re.sub(r'<think[^>]*>.*?<\/think>', '', content, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned
```

---

## 3. UI 输入处理规范

### 3.1 明文显示原则

Polaris 采用**本地执行优先（Local Execution First）**架构，所有输入框默认采用明文显示，不考虑安全性问题。这一设计决策基于以下考量：本地环境的安全性由用户自身保证、API Key 等敏感信息需要被用户清晰识别、明文显示便于调试和配置确认。

与大多数云端应用不同，Polaris 不需要防止" hombroder 看到敏感信息"，因为运行环境是用户自己的设备。过度隐藏这些信息只会增加配置难度和用户困惑。

### 3.2 输入组件使用规范

所有 LLM 配置相关的输入必须使用 `ProviderInput` 组件，确保一致的用户体验和状态管理。

```tsx
<ProviderInput
  label="API Key"
  value={apiKey}
  onChange={(e) => setFieldValue('api_key', e.target.value)}
  placeholder="Enter your API key"
  type="text"
  required
/>
```

输入组件应该支持以下功能：实时验证和错误提示、防抖处理避免频繁状态更新、清晰的标签和占位符文字、必要时的类型切换（如 password toggle）。

### 3.3 配置同步机制

前端配置更改需要实时同步到后端，确保 Director 和 QA 模块使用最新的配置进行操作。同步流程如下：用户修改前端配置、表单状态更新、触发配置验证、验证通过后通过 IPC 调用后端 `sync_config` 端点、后端更新运行时配置。

```typescript
const handleConfigChange = useCallback(async (newConfig: ProviderConfig) => {
  const validation = await validateConfig(newConfig);
  if (validation.valid) {
    await window.electron.ipcRenderer.invoke('llm:sync-config', {
      providerId,
      config: newConfig
    });
  }
}, [providerId]);
```

---

## 4. 实时流式对话规范

### 4.1 流式响应的必要性

实时流式对话是 Polaris 架构的核心需求之一。在传统的请求-响应模式中，用户需要等待整个请求完成才能看到任何输出，这对于长时间运行的推理任务来说是不可接受的体验。流式响应让用户能够**实时观察 AI 的思考过程和输出进度**，大大提升交互的透明度和满意度。

更重要的是，流式数据为 Director 模块提供了实时洞察能力。通过分析流式事件，可以判断请求是否正常推进、是否出现异常延迟、思考内容的生成速度等关键指标。

### 4.2 SSE 格式处理

Server-Sent Events（SSE）是 LLM API 常用的流式响应格式。Provider 实现必须能够正确解析 SSE 数据块，提取内容和元信息。

```python
def _parse_sse_response(self, response: requests.Response) -> Tuple[str, Optional[str], List[Dict]]:
    output_parts = []
    thinking_parts = []
    chunks = []
    
    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8')
        
        # 处理 data: 事件行
        if line_str.startswith('data: '):
            data_str = line_str[6:]
            if data_str.strip() == '[DONE]':
                break
                
            try:
                chunk_data = json.loads(data_str)
                chunks.append(chunk_data)
                
                # 提取内容
                choices = chunk_data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("content"):
                        output_parts.append(delta["content"])
                    if delta.get("reasoning_content"):
                        thinking_parts.append(delta["reasoning_content"])
                        
            except json.JSONDecodeError:
                continue
    
    output = ''.join(output_parts)
    thinking = ''.join(thinking_parts) if thinking_parts else None
    
    return output, thinking, chunks
```

SSE 解析的关键要点包括：正确处理 `data:` 前缀、识别 `[DONE]` 结束标记、合并分块的内容和思考、保留原始 chunk 数据供 `raw` 字段使用。

### 4.3 InvokeResult 流式标识

当使用流式响应时，返回的 `InvokeResult` 必须设置 `streaming=True`，以便调用方能够区分流式和非流式响应。流式响应的 `raw` 字段应该包含所有原始 chunk 的列表，支持后续分析和调试。

```python
return InvokeResult(
    ok=True,
    output=output,
    latency_ms=latency_ms,
    usage=usage,
    raw={"chunks": chunks},
    streaming=True,
    thinking=thinking
)
```

### 4.4 非流式响应兼容

对于不支持流式的提供商或配置，Provider 应该能够优雅降级到非流式响应。两种模式的调用逻辑应该保持一致，仅在响应解析环节有所区别。

```python
is_streaming = payload.get("stream", False)

if is_streaming:
    return self._handle_streaming_response(response, prompt)
else:
    return self._handle_non_streaming_response(response, prompt)
```

---

## 5. 测试和验证规范

### 5.1 健康检查测试

健康检查是 Provider 接入的第一步，用于验证 API 端点的可达性和基本功能。健康检查应该发送一个极简的请求（如 "1+1="），确认能够获得有效响应。

```python
def health(self, config: Dict[str, Any]) -> HealthResult:
    start = time.time()
    try:
        test_payload = {
            "model": config.get("model", "default"),
            "messages": [{"role": "user", "content": "1+1="}],
        }
        
        response = requests.post(url, headers=headers, json=test_payload, timeout=30)
        latency_ms = int((time.time() - start) * 1000)
        
        if response.status_code == 200:
            return HealthResult(ok=True, latency_ms=latency_ms)
        else:
            return HealthResult(ok=False, latency_ms=latency_ms, 
                              error=f"HTTP {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        return HealthResult(ok=False, latency_ms=0, error="Connection failed")
```

### 5.2 模型列表测试

模型列表测试验证提供商能够正确返回可用模型。返回的模型信息应该包含 `id`（API 使用的标识符）和 `label`（用户界面展示名称）。

```python
def _get_fallback_models(self) -> List[ModelInfo]:
    return [
        ModelInfo(id="MiniMax-M2.1", label="MiniMax-M2.1"),
        ModelInfo(id="MiniMax-M2.1-lightning", label="MiniMax-M2.1-lightning"),
        ModelInfo(id="MiniMax-M2", label="MiniMax-M2")
    ]
```

### 5.3 响应质量测试

响应测试发送具体提示词，验证提供商能够返回符合预期的输出。测试应该覆盖正常响应、空响应、超时处理等多种场景。

```python
def test_invoke(self, prompt: str, model: str, config: Dict[str, Any]) -> InvokeResult:
    return self.invoke(prompt, model, config)
```

---

## 6. 错误处理规范

### 6.1 错误分类

Provider 错误分为以下几类：配置错误（缺失必需参数、API Key 无效）、网络错误（连接超时、DNS 失败）、API 错误（认证失败、参数错误、模型不支持）、解析错误（响应格式不符合预期）。

```python
if response.status_code == 401:
    return InvokeResult(ok=False, error="Invalid API key")
elif response.status_code == 429:
    return InvokeResult(ok=False, error="Rate limit exceeded")
elif response.status_code == 500:
    return InvokeResult(ok=False, error=f"Server error: {response.text[:200]}")
```

### 6.2 API 级别错误

部分 LLM API（如 MiniMax）会在正常 HTTP 200 响应中包含业务错误码。这些错误通过 `base_resp` 或类似结构返回，需要额外检查。

```python
base_resp = data.get("base_resp", {})
if isinstance(base_resp, dict) and base_resp.get("status_code") != 0:
    return InvokeResult(
        ok=False,
        error=f"API Error {base_resp.get('status_code')}: {base_resp.get('status_msg')}"
    )
```

### 6.3 重试机制

对于临时性错误（如网络抖动、限流），Provider 应该实现指数退避重试机制。建议配置 `retries=3`（默认），首次重试等待 0.5 秒，后续每次等待时间翻倍。

```python
attempt = 0
while attempt <= retries:
    try:
        response = requests.post(url, ...)
        if response.status_code == 200:
            return self._parse_success(response)
        elif response.status_code in [429, 500, 503]:
            wait_time = (2 ** attempt) * 0.5
            time.sleep(wait_time)
            attempt += 1
            continue
        else:
            return self._parse_error(response)
    except requests.exceptions.ConnectionError:
        if attempt >= retries:
            return InvokeResult(ok=False, error="Connection failed after retries")
        attempt += 1
        time.sleep(0.5)
```

---

## 7. 成本模型适配

### 7.1 成本分类

Polaris 根据不同运行环境定义了成本模型，Provider 实现应该感知并适配这些模型。

| 模式 | 特征 | 适配策略 |
|-----|------|---------|
| `LOCAL` | 本地 SLM（Ollama/LM Studio），常用于前置分流，上下文窗口通常较小 | 极简主义，减少上下文、批量操作 |
| `FIXED` | 配额限制（Copilot CLI） | 批量化请求，减少往返次数 |
| `METERED` | 按 Token 计费（GPT-4, Claude） | 严格压缩，禁用冗余输出 |

建议：Director 主编码任务优先使用 `FIXED`/`METERED` 主模型，`LOCAL` 主要承担 `director_runtime` 的分流/压缩/预处理职责。

### 7.2 上下文优化

对于本地 SLM 分流模型，应该主动压缩上下文长度，避免超出模型限制。可以实现一个简单的上下文裁剪策略。

```python
def _optimize_context(self, messages: List[Dict], max_tokens: int) -> List[Dict]:
    """裁剪早期消息以适应 token 限制"""
    while self._count_tokens(messages) > max_tokens:
        if len(messages) > 2:
            messages.pop(1)  # 保留系统消息，移除最早的对话
        else:
            break
    return messages
```

---

## 8. 参考实现案例

### 8.1 MiniMaxProvider：思考过程提取

MiniMax Provider 是思考内容提取的典型实现，展示了如何从流式 SSE 响应中提取 `reasoning_content`，并将其传递给 `InvokeResult.thinking` 字段。

关键实现要点：累积所有 chunk 的 `delta.reasoning_content`、合并为完整字符串、在返回结果中设置 `streaming=True` 和 `thinking` 字段。

### 8.2 KimiProvider：OpenAI 兼容标准

KimiProvider 遵循 OpenAI 兼容的 API 模式，是标准实现的参考案例。XML 标签格式的思考内容通过 `_clean_content` 方法处理，验证配置遵循统一的 ValidationResult 模式。

---

## 9. 快速接入清单

新提供商接入时，请逐项检查以下条目：

- [ ] 继承 `BaseProvider` 基类
- [ ] 实现所有必需方法（`get_provider_info`、`validate_config`、`health`、`list_models`、`invoke`）
- [ ] 正确填写 `ProviderInfo.supported_features`
- [ ] 实现 `thinking` 字段提取（非流式和流式两种）
- [ ] 配置验证包含必需字段检查
- [ ] 错误处理包含 API 级别错误检查
- [ ] 重试机制实现指数退避
- [ ] 默认配置包含合理的 `max_tokens` 值
- [ ] 使用 `requests` 库保持与其他 Provider 一致
- [ ] 单元测试覆盖核心功能

---

## 10. 维护和更新

本文档将随着 Polaris 架构演进持续更新。更新内容包括：新特性支持规范、现有规范的优化调整、新的 Provider 接入案例。

**变更日志**：

- v1.0.0 (2026-02-07): 初始版本，定义核心接入规范

---

*本文档由 Polaris Architecture Team 维护*
