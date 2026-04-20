# Team Gamma: llm_caller.py 重构蓝图

## 目标文件
`polaris/cells/roles/kernel/internal/llm_caller.py` (2932行)

## 架构分析

### 当前问题
1. **响应类型混杂**: LLMResponse, StructuredLLMResponse, NormalizedStreamEvent 在同一文件
2. **Provider格式化耦合**: NativeProviderFormatter, AnnotatedProviderFormatter 内嵌
3. **流式处理复杂**: 大量流式处理逻辑散落在主类中

### 职责拆分矩阵

| 职责 | 行数 | 目标模块 |
|------|------|---------|
| 响应类型 | ~400 | `response_types.py` |
| Provider格式化 | ~400 | `provider_formatter.py` |
| 流式处理 | ~600 | `stream_handler.py` |
| 错误处理 | ~300 | `error_handling.py` |
| 辅助函数 | ~300 | `helpers.py` |
| 核心Caller | ~500 | `llm_caller.py` (保留) |

## 拆分方案

### 目标结构
```
polaris/cells/roles/kernel/internal/
├── llm_caller.py                # Facade (50行)
├── llm_caller/
│   ├── __init__.py
│   ├── caller.py                # LLMCaller核心 (500行)
│   ├── response_types.py        # 响应类型 (350行)
│   ├── provider_formatter.py    # Provider格式化 (350行)
│   ├── stream_handler.py        # 流式处理 (500行)
│   ├── error_handling.py        # 错误处理 (250行)
│   └── helpers.py               # 辅助函数 (250行)
```

### 模块契约

#### `response_types.py`
```python
"""LLM 响应类型定义。"""

from dataclasses import dataclass, field
from typing import Any
from enum import Enum

class ResponseStatus(str, Enum):
    """响应状态。"""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"

@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token使用量。"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

@dataclass(slots=True)
class LLMResponse:
    """LLM 响应基类。"""
    content: str
    status: ResponseStatus
    usage: TokenUsage | None = None
    latency_ms: int = 0
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == ResponseStatus.SUCCESS

@dataclass(slots=True)
class StructuredLLMResponse(LLMResponse):
    """结构化LLM响应（含工具调用）。"""
    tool_calls: tuple[dict[str, Any], ...] = ()
    thinking: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "StructuredLLMResponse":
        """从原始响应构建。"""
        ...

@dataclass(frozen=True, slots=True)
class NormalizedStreamEvent:
    """规范化流式事件。"""
    event_type: str  # "chunk" | "tool_call" | "thinking" | "complete"
    content: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
```

#### `provider_formatter.py`
```python
"""Provider 格式化器模块。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

class ProviderFormatter(Protocol):
    """Provider 格式化器协议。"""

    def format_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """格式化请求。"""
        ...

    def parse_response(
        self,
        raw: dict[str, Any]
    ) -> StructuredLLMResponse:
        """解析响应。"""
        ...

class NativeProviderFormatter:
    """原生Provider格式化器。"""

    __slots__ = ('_provider_type',)

    def __init__(self, provider_type: str) -> None:
        self._provider_type = provider_type

    def format_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """使用Provider原生格式。"""
        ...

    def parse_response(
        self,
        raw: dict[str, Any]
    ) -> StructuredLLMResponse:
        """解析原生响应格式。"""
        ...

class AnnotatedProviderFormatter:
    """注解Provider格式化器。"""

    __slots__ = ('_inner', '_annotations')

    def __init__(
        self,
        inner: ProviderFormatter,
        annotations: dict[str, Any],
    ) -> None:
        self._inner = inner
        self._annotations = annotations

    def format_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """添加注解后格式化。"""
        request = self._inner.format_request(messages, tools, **kwargs)
        request["annotations"] = self._annotations
        return request
```

#### `stream_handler.py`
```python
"""流式处理模块。"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable

@dataclass(slots=True)
class StreamConfig:
    """流式配置。"""
    timeout_seconds: float = 300.0
    chunk_timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base: float = 1.0

@dataclass(slots=True)
class StreamState:
    """流式状态（可变）。"""
    chunks_received: int = 0
    total_tokens: int = 0
    last_chunk_time: float = 0.0
    error_count: int = 0

class StreamHandler:
    """流式处理器。"""

    __slots__ = ('_config', '_state', '_accumulator')

    def __init__(self, config: StreamConfig) -> None:
        self._config = config
        self._state = StreamState()
        self._accumulator: list[str] = []

    async def process_stream(
        self,
        stream: AsyncIterator[dict[str, Any]],
        on_chunk: Callable[[str], None] | None = None,
    ) -> StructuredLLMResponse:
        """处理流式响应。"""
        ...

    def _handle_timeout(self) -> None:
        """处理超时。"""
        ...

    def _accumulate_chunk(self, chunk: str) -> None:
        """累积块。"""
        ...

    def _build_response(self) -> StructuredLLMResponse:
        """构建最终响应。"""
        ...
```

## 实现步骤

### Step 1: 创建目录结构
```bash
mkdir -p polaris/cells/roles/kernel/internal/llm_caller
touch polaris/cells/roles/kernel/internal/llm_caller/__init__.py
```

### Step 2: 提取 Response Types
```python
# 1. 迁移 LLMResponse, StructuredLLMResponse, NormalizedStreamEvent
# 2. 添加 TokenUsage, ResponseStatus
# 3. 添加 from_raw 工厂方法
```

### Step 3: 提取 Provider Formatter
```python
# 1. 迁移 ProviderFormatter 协议
# 2. 迁移 NativeProviderFormatter, AnnotatedProviderFormatter
# 3. 添加 format_request/parse_response 抽象
```

### Step 4: 提取 Stream Handler
```python
# 1. 迁移流式处理逻辑
# 2. 创建 StreamConfig, StreamState
# 3. 实现 StreamHandler 类
```

### Step 5: 创建 Facade
```python
# polaris/cells/roles/kernel/internal/llm_caller.py

"""LLM Caller (Facade)。

此文件保留向后兼容性，实际实现已迁移到 llm_caller/ 子模块。
"""

from .llm_caller.caller import LLMCaller
from .llm_caller.response_types import (
    LLMResponse,
    StructuredLLMResponse,
    NormalizedStreamEvent,
    TokenUsage,
    ResponseStatus,
)
from .llm_caller.provider_formatter import (
    ProviderFormatter,
    NativeProviderFormatter,
    AnnotatedProviderFormatter,
)
from .llm_caller.stream_handler import (
    StreamHandler,
    StreamConfig,
)

__all__ = [
    "LLMCaller",
    "LLMResponse",
    "StructuredLLMResponse",
    "NormalizedStreamEvent",
    "TokenUsage",
    "ResponseStatus",
    "ProviderFormatter",
    "NativeProviderFormatter",
    "AnnotatedProviderFormatter",
    "StreamHandler",
    "StreamConfig",
]
```

## 测试策略

### 单元测试结构
```
polaris/cells/roles/kernel/internal/llm_caller/tests/
├── test_response_types.py      # 响应类型测试
├── test_provider_formatter.py  # 格式化器测试
├── test_stream_handler.py      # 流式处理测试
├── test_error_handling.py      # 错误处理测试
└── test_caller.py              # 集成测试
```

### 关键测试用例
```python
# test_response_types.py
class TestStructuredLLMResponse:
    def test_from_raw_anthropic(self) -> None:
        """测试 Anthropic 原始响应解析。"""
        raw = {
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        response = StructuredLLMResponse.from_raw(raw)
        assert response.content == "Hello"
        assert response.usage.prompt_tokens == 10

# test_stream_handler.py
class TestStreamHandler:
    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """测试超时处理。"""
        ...

    @pytest.mark.asyncio
    async def test_chunk_accumulation(self) -> None:
        """测试块累积。"""
        ...
```

## 验收标准

- [ ] 所有模块 < 500行
- [ ] mypy --strict 通过
- [ ] pytest覆盖率 > 80%
- [ ] ruff check/format 通过
- [ ] 原LLM调用功能正常
- [ ] Facade导入向后兼容

## 时间表

| 阶段 | 时间 | 交付物 |
|------|------|--------|
| 设计 | Day 1-2 | 详细设计文档 |
| 实现 | Day 3-7 | 拆分后模块代码 |
| 测试 | Day 8-10 | 单元测试 + 集成测试 |
| 验收 | Day 11-12 | Code Review + 合并 |

---

**Team Lead**: _________________
**Reviewer**: _________________
**Date**: 2025-03-31