# ContextOS 工程门禁规范 v1.0

> **文档编号**: GOVERNANCE-CONTEXTOS-GATES-20260423
> **适用范围**: polaris/kernelone/context/ 全目录
> **生效日期**: 2026-04-23
> **维护人**: Principal Architect

---

## 门禁 1: 关键模型禁止直接 model_copy(update=...)

**规则**: 对 snapshot、transcript、budget plan 等关键模型，禁止直接使用 `model_copy(update=...)`。

**理由**: `model_copy(update=...)` 绕过 Pydantic 字段校验，字段名拼写错误会导致静默失败（如 `_metadata` vs `metadata`）。

**正确做法**:
```python
# ✅ 使用 validated_replace() helper
from polaris.kernelone.context.context_os.model_utils import validated_replace

updated = validated_replace(item, metadata=(...))
```

**错误做法**:
```python
# ❌ 禁止：绕过校验，字段名错误静默失败
item.model_copy(update={"_metadata": {...}}, deep=True)
```

**检查方式**:
- Code Review 时必须检查所有 `model_copy.*update=` 用法
- 静态扫描规则（待接入 CI）

**违规后果**: 🔴 红灯，必须改为 `validated_replace()` 或显式构造新实例

---

## 门禁 2: 禁止双轨锁（threading + asyncio 同时保护同一状态）

**规则**: 同一对象不得同时被 `threading.Lock`/`RLock` 和 `asyncio.Lock` 保护。

**理由**: 两套锁之间无互斥关系，sync/async 调用并发修改同一状态时导致数据竞态。

**正确做法**:
```python
# ✅ 统一使用 asyncio.Lock，sync facade 委托到 async core
class ContentStore:
    def __init__(self):
        self._async_lock = asyncio.Lock()
    
    async def write(self, ...): ...
    
    def intern(self, ...):
        # sync facade 委托到 async core
        return asyncio.run(self._intern_async(...))
```

**错误做法**:
```python
# ❌ 禁止：双轨锁保护同一 self._store
self._lock = threading.Lock()
self._async_lock = asyncio.Lock()
self._store = {}
```

**检查方式**:
- 静态扫描：同一类中同时出现 `threading.Lock` 和 `asyncio.Lock` 且都访问同一字段
- Code Review checklist

**违规后果**: 🔴 红灯，必须二选一（推荐 async-only + sync delegation）

---

## 门禁 3: async critical section 内禁止重 CPU / 重 I/O

**规则**: `async with lock:` 临界区内只应执行：
- 状态读取
- 版本检查（CAS）
- 最小化状态修改

**禁止在锁内执行**:
- 大文件读写
- 复杂正则匹配/数据处理
- JSON 序列化/反序列化（大数据量）
- 网络请求
- 数据库查询

**正确做法**:
```python
# ✅ Snapshot -> Compute -> Commit
async def project(self):
    async with self._lock:
        snapshot = self._take_snapshot()
    
    # 锁外计算（CPU 密集型）
    projection = await loop.run_in_executor(None, self._compute, snapshot)
    
    async with self._lock:
        self._commit(projection)
```

**检查方式**:
- Code Review：检查 `async with` 块内的代码复杂度
- 性能测试：高并发下监控事件循环延迟

**违规后果**: 🟡 黄灯，必须有注释说明原因，或重构为锁外计算

---

## 门禁 4: 反序列化路径禁止 except Exception 静默降级

**规则**: 反序列化/数据转换路径中，禁止使用裸 `except Exception:` 静默吞掉异常并进入 fallback。

**正确做法**:
```python
# ✅ 明确捕获已知异常，记录日志，系统异常继续传播
try:
    event = TranscriptEventV2.model_validate(data)
except ValidationError as e:
    logger.warning("Validation failed for event: %s", e)
    event = TranscriptEventV2(...)  # fallback
except (KeyboardInterrupt, SystemExit):
    raise  # 不捕获系统信号
except Exception as e:
    logger.exception("Unexpected error during validation: %s", e)
    raise  # 未知异常继续传播
```

**错误做法**:
```python
# ❌ 禁止：吞掉所有异常，包括系统信号和内存错误
try:
    event = TranscriptEventV2.model_validate(data)
except Exception:
    event = TranscriptEventV2(...)  # 静默降级，无日志
```

**检查方式**:
- 静态扫描：`except Exception:` 后跟非 raise/fallback 代码
- Code Review

**违规后果**: 🔴 红灯，必须收窄异常类型并添加结构化日志

---

## 门禁 5: 所有 Cache 必须有边界

**规则**: 所有内存缓存必须至少设置以下之一：
- `max_entries`（最大条目数）
- `max_bytes`（最大字节数）
- TTL（生存时间，必须配合定期清理）

**正确做法**:
```python
# ✅ 有界缓存
from polaris.kernelone.context.context_os.bounded_cache import LRUBoundedCache

self._cache = LRUBoundedCache(max_entries=128, max_bytes=500_000_000)
```

**错误做法**:
```python
# ❌ 禁止：无界缓存
self._cache: dict[str, Any] = {}
```

**检查方式**:
- 静态扫描：`dict()` 或 `OrderedDict()` 作为缓存使用且无上限
- Code Review
- 单元测试：验证缓存满载后是否触发驱逐

**违规后果**: 🟡 黄灯，无上限必须有技术理由（如：已知数据集大小且受控）

---

## 门禁 6: 新旧策略切换必须有 Contract Test

**规则**: 引入新的策略/接口（如 `SelectorPolicy`、`CompressionRegistry`）时，必须确保：
1. 默认行为不变（与旧实现输出一致）
2. 新策略显式启用才改变输出
3. 无隐式漂移（token budget、selection 顺序等）

**正确做法**:
```python
# ✅ Contract test: 验证默认策略与旧行为一致
def test_default_selector_policy_matches_legacy():
    legacy = ExplorationPolicy()
    new = ExplorationPolicy(selector_policy=DefaultSelectorPolicy())
    
    assert legacy.select(...) == new.select(...)

def test_new_policy_explicitly_enabled():
    policy = ExplorationPolicy(selector_policy=SemanticRankSelectorPolicy())
    # 显式启用时输出可能不同，但必须有预期
```

**检查方式**:
- CI 测试门禁：contract test 失败禁止合入
- Code Review：检查是否有对应的 contract test

**违规后果**: 🔴 红灯，无 contract test 禁止合入主干

---

## 落地计划

| 门禁 | 接入方式 | 优先级 | 负责人 | 截止日期 |
|------|---------|--------|--------|----------|
| 1 | Code Review + 静态扫描 | P0 | F1 负责人 | 2026-04-24 |
| 2 | Code Review + 自定义 lint | P0 | F2 负责人 | 2026-04-24 |
| 3 | Code Review + 性能测试 | P1 | F3 负责人 | 2026-04-25 |
| 4 | Code Review + 静态扫描 | P0 | 全团队 | 2026-04-24 |
| 5 | Code Review + 单元测试 | P1 | F4 负责人 | 2026-04-25 |
| 6 | CI 测试门禁 | P1 | QA 团队 | 2026-04-26 |

---

**批准状态**: ✅ 待团队确认后生效
