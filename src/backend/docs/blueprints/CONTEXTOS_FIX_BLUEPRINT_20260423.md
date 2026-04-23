# ContextOS 缺陷修复蓝图 v2.0

> **文档编号**: BLUEPRINT-CONTEXT-FIX-20260423  
> **编制日期**: 2026-04-23  
> **前置文档**: `docs/blueprints/CONTEXT_ARCHITECTURE_REFACTOR_20260423.md`  
> **审计基准**: ContextOS 缺陷审计报告 — 最终版（v1.0）  
> **架构负责人**: Principal Architect  
> **执行团队**: 4人精英修复小组  
> **工期**: 7天（Day 1-2 止血，Day 3-5 根治，Day 6-7 验证）

---

## 1. 修复目标

基于审计报告识别的 **4项P0** 缺陷，本蓝图制定可执行的修复路线，确保：
1. **无损修复** — 外部接口和行为绝对一致
2. **防御性编程** — 修复根因而非症状
3. **可验证** — 每项修复配套单元测试
4. **可回滚** — 变更原子化，单 PR 单缺陷

---

## 2. 修复任务分解

### 2.1 任务矩阵

| 任务ID | P0缺陷 | 委派专家 | 工时 | 依赖 | PR范围 |
|--------|--------|----------|------|------|--------|
| **F1** | P0-2: metadata 字段名错误 | 模型一致性专家 | 4h | 无 | `pipeline/stages.py` + 测试 |
| **F2** | P0-1: ContentStore 状态隔离 | 并发架构专家 | 12h | F1 | `content_store.py` + `snapshot.py` |
| **F3** | P0-3: async 热路径阻塞 | 性能工程专家 | 8h | F2 | `runtime.py` + `snapshot.py` |
| **F4** | P0-4: 缓存边界与 cleanup | 资源管理专家 | 6h | F3 | `runtime.py` + `cache_manager.py` |

### 2.2 修复原则

```
┌─────────────────────────────────────────────────────────────┐
│                    修复执行原则                              │
├─────────────────────────────────────────────────────────────┤
│ 1. 单 PR 单缺陷 — 禁止批量混改                               │
│ 2. 先写测试再修复 — TDD 强制                                  │
│ 3. 保留旧接口 — 向后兼容垫片（deprecation warning）           │
│ 4. 防御性边界 — 输入校验、异常处理、日志记录                   │
│ 5. 类型安全 — mypy --strict 零错误                           │
│ 6. 代码规范 — ruff check --fix 零警告                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 架构决策记录

### ADR-F1: `model_copy(update=...)` 字段名修复

**决策**: `pipeline/stages.py:1092` 处 `"_metadata"` 修正为 `"metadata"`，并在关键模型层引入 `validated_replace()` helper。

**理由**: 
- Pydantic V2 `model_copy(update=...)` 不验证 update 字典的键名
- 字段名拼写错误导致 metadata 更新静默失败
- 需要统一封装防止未来同类错误

### ADR-F2: ContentStore 状态隔离

**决策**: ContentStore 采用 **Async-Only Core + Sync Facade Delegation** 模式。

**理由**:
- ContextOS 主运行时已经是 async-first
- sync API 调用方（如 pipeline stages）可以委托到事件循环
- 避免双轨锁的互斥域分裂

**实现**:
```python
class ContentStore:
    def __init__(self, ...):
        self._async_lock = asyncio.Lock()
        self._store: dict[str, str] = {}      # hash -> content
        self._key_index: dict[str, str] = {}  # key -> hash
    
    async def write(self, key: str, content: str) -> ContentRef:
        async with self._async_lock:
            ref = await self._intern_async(content)
            self._key_index[key] = ref.hash
            return ref
    
    # sync facade — 仅用于兼容，内部委托到 async
    def intern(self, content: str) -> ContentRef:
        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(
                self._intern_async(content), loop
            ).result(timeout=5.0)
        except RuntimeError:
            # 无运行中的事件循环，直接运行
            return asyncio.run(self._intern_async(content))
```

### ADR-F3: async 热路径优化

**决策**: `project()` 采用 **Snapshot → Compute → Validate/Commit** 范式。

**理由**:
- 锁内只保留状态读取和最小化提交
- CPU 密集型计算移出锁外
- 文件 I/O 通过 `run_in_executor()` 执行

### ADR-F4: 缓存边界策略

**决策**: 所有 cache 必须实现 `BoundedCache` Protocol。

**理由**:
- 无界缓存是长跑 OOM 的根因
- 统一接口便于监控和治理

```python
class BoundedCache(Protocol):
    max_entries: int
    max_bytes: int
    
    def put(self, key: str, value: Any) -> None: ...
    def get(self, key: str) -> Any | None: ...
    def evict_if_needed(self) -> None: ...
```

---

## 4. 数据流与模块交互

### F1 修复数据流

```
WindowCollector.process()
    │
    ├──→ 旧代码: model_copy(update={"_metadata": dict})  [错误字段名]
    │
    └──→ 新代码: validated_replace(item, metadata=tuple(...))  [正确字段名 + 验证]
                │
                └──→ helper: 字段名白名单检查 + 类型转换 + model_validate()
```

### F2 修复数据流

```
ContentStore
    ├──→ Sync Facade (intern/get/release)
    │       └──→ 委托到 Async Core
    │
    └──→ Async Core (write/read/delete/update)
            └──→ asyncio.Lock 统一保护
                    ├──→ _store: hash -> content
                    └──→ _key_index: key -> hash
```

### F3 修复数据流

```
StateFirstContextOS.project()
    │
    ├──→ Lock: 拍快照 (copy minimal state)
    │
    ├──→ Unlock: CPU 计算 (run_in_executor)
    │       └──→ _project_via_pipeline_sync()
    │
    └──→ Lock: CAS 提交 (validate + commit)
```

---

## 5. 验收标准

### 5.1 每任务门禁

| 任务 | ruff | mypy | pytest | 压力测试 |
|------|------|------|--------|----------|
| F1 | ✅ | ✅ | ✅ | N/A |
| F2 | ✅ | ✅ | ✅ | 并发读写 1000 次 |
| F3 | ✅ | ✅ | ✅ | 100并发 project() 调用 |
| F4 | ✅ | ✅ | ✅ | 缓存满载后 OOM 检查 |

### 5.2 全局回归

- [ ] `pytest polaris/kernelone/context/ -x` 100% 通过
- [ ] `pytest polaris/kernelone/context/tests/ -x` 100% 通过
- [ ] `ruff check polaris/kernelone/context/ --fix` 零警告
- [ ] `mypy --strict polaris/kernelone/context/` 零错误

---

## 6. 风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ContentStore 状态隔离引入死锁 | 中 | 高 | 单线程 actor 模式，锁顺序文档化 |
| async 路径变更破坏 sync 调用方 | 中 | 高 | 保留 sync facade，添加 deprecation warning |
| validated_replace() 性能回归 | 低 | 中 | benchmark 对比，允许 opt-out |
| 缓存边界导致缓存命中率骤降 | 低 | 中 | 渐进式收紧边界，监控 metrics |

**回滚策略**: 每任务独立分支，主干通过 CI 后才合入。

---

## 7. 团队分工

```
Principal Architect (你)
        │
        ├──→ F1: 模型一致性专家 (Model Consistency Specialist)
        │      └─ 修复 metadata 字段名 + validated_replace() helper
        │
        ├──→ F2: 并发架构专家 (Concurrency Architect)
        │      └─ ContentStore 状态隔离 + snapshot 锁统一
        │
        ├──→ F3: 性能工程专家 (Performance Engineer)
        │      └─ async 热路径优化 + run_in_executor 改造
        │
        └──→ F4: 资源管理专家 (Resource Management Specialist)
               └─ 缓存边界 + cleanup 完善 + BoundedCache Protocol
```

---

**批准状态**: ✅ 待执行  
**下一里程碑**: F1 完成并通过 Code Review（Day 1）
