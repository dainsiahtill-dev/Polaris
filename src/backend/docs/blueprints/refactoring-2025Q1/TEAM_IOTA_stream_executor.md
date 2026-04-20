# Team Iota: stream_executor.py 重构蓝图

## 目标文件
`polaris/kernelone/llm/engine/stream_executor.py` (1724行)

## 架构分析

### 当前问题
1. **流式组件混杂**: Config/State/Buffer/Accumulator 在同一文件
2. **工具调用累积逻辑复杂**: `_ToolCallAccumulator` 内嵌
3. **结果追踪器内嵌**: `_StreamResultTracker` 占用空间

### 拆分方案

```
polaris/kernelone/llm/engine/
├── stream_executor.py           # Facade (50行)
├── stream/
│   ├── __init__.py
│   ├── executor.py              # StreamExecutor核心 (400行)
│   ├── config.py                # StreamConfig, StreamState (200行)
│   ├── backpressure.py          # BackpressureBuffer (350行)
│   ├── tool_accumulator.py      # _ToolCallAccumulator (250行)
│   └── result_tracker.py        # _StreamResultTracker (200行)
```

### 核心契约

```python
# config.py
@dataclass(frozen=True, slots=True)
class StreamConfig:
    """流式配置 - 不可变。"""
    timeout_seconds: float = 300.0
    chunk_timeout_seconds: float = 30.0
    max_buffer_size: int = 10000

class StreamState(Enum):
    """流式状态。"""
    IDLE = "idle"
    STREAMING = "streaming"
    COMPLETED = "completed"
    ERROR = "error"

# backpressure.py
class BackpressureBuffer:
    """背压缓冲区 - 防止内存溢出。"""

    __slots__ = ('_buffer', '_max_size', '_dropped')

    def push(self, chunk: str) -> bool:
        """推入块，返回是否成功。"""
        if len(self._buffer) >= self._max_size:
            self._dropped += 1
            return False
        self._buffer.append(chunk)
        return True

    def flush(self) -> str:
        """刷新并返回累积内容。"""
        ...
```

---

**Team Lead**: _________________
**Date**: 2025-03-31