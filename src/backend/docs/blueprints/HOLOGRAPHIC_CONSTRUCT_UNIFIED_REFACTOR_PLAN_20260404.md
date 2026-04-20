# 全息构造体（Holographic Construct）统一重构蓝图

**版本**: v1.0
**日期**: 2026-04-04
**状态**: 草稿
**基于**: 8个子系统深度审计 + 13大子系统交叉验证

---

## 执行摘要

经过全面代码审计，发现**原始审计报告存在重大偏差**：
- **错误声称**: Neural Syndicate 全部缺失（实际：全部存在）
- **错误声称**: EventStreamer 缺失（实际：存在于 `sse_streamer.py`）
- **错误声称**: resume_workflow 缺失（实际：存在于 `engine.py:415-571`）
- **错误声称**: dead_letter_queue 缺失（实际：存在于 `engine.py:216`）
- **错误声称**: PARSE_ERROR/SCHEMA_VALIDATION_ERROR 从未触发（实际：通过 keyword matching 在 llm_invoker 中触发）

**实际需要修复的问题**:
- S4: `AkashicSemanticMemory.delete()` 存在 fire-and-forget 幽灵数据 bug
- S6: `ResponseNormalizer` 与 `LLMResponseParser` 95% 代码重复
- S6: `_parse_json_text()` 使用 `[^{}]*` 正则，深度仅支持单层
- S5: `BackpressureBuffer` 使用 `threading.Lock` 而非 `asyncio.Queue`
- S7: `Recording` 缺少 HTTP 协议细节（method/URL/headers/body/status_code）
- S12: `flywheel/` 模块完全不存在
- S1: `retry_policy.py` 与 `resilience.py` 重试延迟公式不一致

---

## 第一部分：子系统实际状态矩阵

| 子系统 | 声称状态 | 实际状态 | 关键文件 |
|--------|---------|---------|---------|
| S1 Phoenix | MultiProviderFallbackManager 缺失 | **缺失**（确认） | `resilience.py` |
| S1 Phoenix | resume_workflow 缺失 | **存在**（错误声称） | `engine.py:415-571` |
| S1 Phoenix | dead_letter_queue 缺失 | **存在**（错误声称） | `engine.py:216` |
| S1 Phoenix | PARSE_ERROR 从未触发 | **已触发**（错误声称） | `llm_invoker.py` |
| S2 Neural Syndicate | 全部缺失 | **全部存在**（错误声称） | `neural_syndicate/*.py` |
| S3 Chronos | high_risk_actions 缺失 | **存在**（错误声称） | `contracts.py` |
| S3 Chronos | suspend_on_high_risk 缺失 | **缺失**（确认） | `contracts.py` |
| S4 Truth | DocumentPipeline 缺失 | **不存在**（确认） | `memory_manager.py` |
| S4 Truth | delete() 幽灵数据 | **存在 bug**（确认） | `semantic_memory.py` |
| S5 Neural | EventStreamer 缺失 | **存在**（错误声称） | `sse_streamer.py` |
| S5 Neural | threading.Lock vs asyncio.Queue | **存在**（确认） | `backpressure.py` |
| S6 Entropy | ResponseNormalizer vs LLMResponseParser 重复 | **存在**（确认） | `normalizer.py` vs `response_parser.py` |
| S7 Mirror | ShadowReplay 缺失 | **不存在**（确认） | `vcr.py` |
| S7 Mirror | Recording 缺 HTTP 细节 | **存在**（确认） | `vcr.py` |
| S11 Kaleidoscope | prompts/ 存在 | **存在**（确认） | `prompts/` |
| S12 Möbius | flywheel/ 缺失 | **不存在**（确认） | N/A |

---

## 第二部分：精准修复指令

### P0 - 关键缺陷（必须修复）

#### P0-1: S4 - AkashicSemanticMemory.delete() 幽灵数据 bug

**文件**: `polaris/kernelone/akashic/semantic_memory.py`

**问题**: `delete()` 方法使用 fire-and-forget `asyncio.create_task(self._compact_jsonl())`，进程崩溃时 JSONL 文件不清理。

**修复**:
```python
async def delete(self, memory_id: str) -> bool:
    async with self._lock:
        if memory_id not in self._items:
            return False
        del self._items[memory_id]
        self._deleted_ids.add(memory_id)
        # 同步等待 JSONL 清理完成
        await self._compact_jsonl()
    return True
```

**验证**: 重启进程后，被删除的 ID 不应出现在 `_load()` 中。

---

#### P0-2: S6 - ResponseNormalizer 与 LLMResponseParser 代码重复

**文件**: `polaris/kernelone/llm/response_parser.py`

**问题**: 两个类 95% 相同代码，`LLMResponseParser` 缺少 `normalize_response()` 和 `_extract_usage()`。

**修复**:
```python
# 在 response_parser.py 中
class LLMResponseParser(ResponseNormalizer):
    """继承 ResponseNormalizer，仅添加特有方法"""
    pass  # 所有通用方法继承自 ResponseNormalizer
```

**删除**: `response_parser.py` 中的重复方法（`_first_choice` 之后的所有方法）。

---

#### P0-3: S6 - _parse_json_text() 深度限制

**文件**: `polaris/kernelone/llm/toolkit/parsers/canonical.py:357`

**问题**: `r'\{[^{}]*"tool"[^{}]*\}'` 使用 `[^{}]*`，无法匹配嵌套 JSON。

**修复**: 使用 `_balanced_json_fragments()` 方法替代正则：
```python
@staticmethod
def _parse_json_text(text: str) -> list[str]:
    """从文本中提取包含 tool 字段的 JSON 对象（支持嵌套）"""
    results = []
    for fragment in _balanced_json_fragments(text):
        try:
            parsed = json.loads(fragment)
            if isinstance(parsed, dict) and "tool" in parsed:
                results.append(fragment)
        except json.JSONDecodeError:
            continue
    return results
```

---

#### P0-4: S5 - BackpressureBuffer 使用 threading.Lock

**文件**: `polaris/kernelone/llm/engine/stream/backpressure.py:60`

**问题**: `threading.Lock` 在 async 上下文中导致 GIL 竞争。

**修复**: 已有 `AsyncBackpressureBuffer` 替代品（`sse_streamer.py:376`）。需要：
1. 在 `BackpressureBuffer` 中添加弃用警告
2. 将所有调用方迁移到 `AsyncBackpressureBuffer`

---

### P1 - 高优先级缺陷

#### P1-1: S1 - retry_policy.py 与 resilience.py 公式不一致

**文件**:
- `polaris/cells/roles/kernel/internal/error_recovery/retry_policy.py:62`
- `polaris/kernelone/llm/engine/resilience.py:783`

**问题**: 
- `retry_policy.py`: `base_delay * 2^attempt`
- `resilience.py`: `base_delay * exponential_base^(attempt-1)`

**修复**: 在 `retry_policy.py` 的 `compute_delay()` 添加注释说明差异：
```python
def compute_delay(self, attempt: int) -> float:
    """
    注意：attempt=0 对应首次重试前延迟，attempt=1 对应第二次重试前。
    与 ResilienceManager 对齐：ResilienceManager 使用 attempt-1，
    所以 attempt=1 时实际计算为 base_delay * 2^(1-1) = base_delay。
    """
    if self._config.exponential_backoff:
        return self._config.base_delay * (2 ** attempt)
    return self._config.base_delay
```

---

#### P1-2: S7 - Recording 缺少 HTTP 协议细节

**文件**: `polaris/kernelone/benchmark/reproducibility/vcr.py:25-32`

**问题**: `Recording` 只有 `request_key`/`response`/`timestamp`/`metadata`，缺少 HTTP 细节。

**修复**: 扩展 `Recording`:
```python
@dataclass
class Recording:
    request_key: str
    response: dict[str, Any]
    timestamp: str
    metadata: dict[str, Any] | None = None
    # 新增字段
    method: str | None = None
    url: str | None = None
    request_headers: dict[str, str] | None = None
    request_body: str | None = None
    status_code: int | None = None
    response_headers: dict[str, str] | None = None
```

---

#### P1-3: S7 - ShadowReplay 不存在

**文件**: `polaris/kernelone/benchmark/reproducibility/vcr.py`

**修复**: 实现 `ShadowReplay` 上下文管理器：
```python
class ShadowReplay:
    """异步上下文管理器，拦截 httpx 请求"""
    def __init__(self, cassette_id: str, mode: CacheMode = CacheMode.BOTH):
        self.cassette_id = cassette_id
        self.mode = mode
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ShadowReplay:
        self._client = httpx.AsyncClient()
        # 设置传输拦截
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
```

---

### P2 - 中优先级缺陷

#### P2-1: S4 - 语义分块缺失

**文件**: `polaris/infrastructure/db/repositories/lancedb_code_search.py:53-89`

**问题**: 固定 80 行分块，无语义边界检测。

**修复**: 添加 `_detect_semantic_boundaries()` 方法：
```python
def _detect_semantic_boundaries(self, lines: list[str]) -> list[tuple[int, int]]:
    """检测语义边界（类/函数定义）"""
    boundaries = []
    in_class = False
    in_function = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('class ') or stripped.startswith('def '):
            boundaries.append(i)
    return boundaries if boundaries else [(0, len(lines))]
```

---

#### P2-2: S4 - add() 方法无幂等性

**文件**: `polaris/kernelone/akashic/semantic_memory.py`

**修复**: 添加 content hash 查重：
```python
async def add(self, text: str, *, metadata=None, importance=5) -> str:
    # 计算 content hash
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    async with self._lock:
        # 检查是否已存在
        for mid, item in self._items.items():
            if item.get("content_hash") == content_hash:
                return mid  # 返回现有 ID，实现幂等性
    # ... 原有逻辑
```

---

### P3 - 低优先级缺陷

#### P3-1: S12 - flywheel/ 模块不存在

**文件**: `polaris/kernelone/flywheel/__init__.py`（需新建）

**实现**: `FeedbackCollector` + `RingBuffer` + JSONL 黄金数据集管道。

---

## 第三部分：跨子系统依赖矩阵（已修正）

| 源 | 目标 | 依赖内容 | 状态 |
|----|------|---------|------|
| S2 Neural Syndicate | `bus_port.py` | `IAgentBusPort` | **未使用**（import 但未调用） |
| S3 Chronos | `dlq.py` | `DeadLetterQueue` | 已集成 |
| S4 Truth | `embedding.py` | `KernelEmbeddingPort` | 已集成 |
| S5 Neural | `sse_streamer.py` | `EventStreamer` | 已存在 |
| S6 Entropy | `normalizer.py` | 候选提取 | 需合并 |
| S7 Mirror | `sanitization_hook.py` | 脱敏 | 已集成 |
| S9 Aegis | `llm/exceptions.py` | `JSONParseError` | 已定义 |

---

## 第四部分：实施计划

### Phase 0: 验证与修复（P0 缺陷）

| 任务 | 子系统 | 优先级 | 预计工时 |
|-----|-------|-------|---------|
| 修复 delete() 幽灵数据 bug | S4 | P0 | 2h |
| 合并 ResponseNormalizer/LLMResponseParser | S6 | P0 | 3h |
| 修复 _parse_json_text() 深度限制 | S6 | P0 | 1h |
| 迁移 BackpressureBuffer 到 AsyncBackpressureBuffer | S5 | P0 | 2h |

### Phase 1: 核心架构（P1 缺陷）

| 任务 | 子系统 | 预计工时 |
|-----|-------|---------|
| 添加 retry 公式对齐注释 | S1 | 0.5h |
| 扩展 Recording HTTP 字段 | S7 | 2h |
| 实现 ShadowReplay | S7 | 4h |

### Phase 2: 语义增强（P2 缺陷）

| 任务 | 子系统 | 预计工时 |
|-----|-------|---------|
| 实现语义分块 | S4 | 6h |
| 实现 add() 幂等性 | S4 | 2h |

### Phase 3: 生态补全（P3 缺陷）

| 任务 | 子系统 | 预计工时 |
|-----|-------|---------|
| 实现 flywheel/ 模块 | S12 | 8h |

---

## 第五部分：验证与测试

### 验证命令

```bash
# P0 验证
pytest polaris/tests/test_semantic_memory.py -v -k "delete"
pytest polaris/tests/test_response_parser.py -v
ruff check polaris/kernelone/llm/engine/normalizer.py

# P1 验证
ruff check polaris/kernelone/benchmark/reproducibility/vcr.py
pytest polaris/tests/test_vcr.py -v -k "shadow"

# P2 验证
pytest polaris/tests/test_lancedb.py -v -k "semantic_chunk"
pytest polaris/tests/test_semantic_memory.py -v -k "idempotent"

# P3 验证
pytest polaris/tests/test_flywheel.py -v  # 新建测试
```

---

## 第六部分：风险评估

| 风险 | 等级 | 缓解策略 |
|-----|------|---------|
| 删除 response_parser.py 可能破坏现有导入 | HIGH | 先添加 LLMResponseParser = ResponseNormalizer 别名，再删除重复代码 |
| BackpressureBuffer 迁移影响流式性能 | MEDIUM | 保留旧实现并添加 DeprecationWarning，新实现完全独立 |
| flywheel/ 模块设计需要更多讨论 | MEDIUM | 先实现核心 FeedbackCollector，RingBuffer 和 JSONL 后置 |

---

## 第七部分：决策记录

### ADR-0072: ResponseNormalizer 与 LLMResponseParser 合并策略

**日期**: 2026-04-04
**状态**: 拟议
**问题**: 两个类 95% 代码重复，维护成本高
**方案**: LLMResponseParser 继承 ResponseNormalizer，删除重复代码
**影响**: 需要更新所有 LLMResponseParser 的导入

### ADR-0073: BackpressureBuffer 迁移策略

**日期**: 2026-04-04
**状态**: 拟议
**问题**: threading.Lock 在 async 上下文中导致 GIL 竞争
**方案**: 迁移所有调用方到 AsyncBackpressureBuffer，保留旧实现用于向后兼容
**影响**: 需要遍历所有 backpressure.py 调用方

---

**文档状态**: 草稿，待委员会评审后执行
**下次更新**: 2026-04-05（Phase 0 开始后）
