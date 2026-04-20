# P0-3 修复蓝图：AkashicSemanticMemory delete() 幽灵数据 Bug

## 1. 问题描述

### 1.1 幽灵数据复现

**问题位置**: `polaris/kernelone/akashic/semantic_memory.py:322-334`

**现象**:
1. 调用 `delete(memory_id)` 删除一个 memory item
2. Item 从内存 `_items` 中移除，加入 `_deleted_ids`
3. JSONL compaction 通过 `asyncio.create_task()` **异步**执行（fire-and-forget）
4. **如果进程在 compaction 完成前崩溃**，JSONL 文件保留已删除 item
5. 重启后 `_load()` 重新加载 JSONL，由于 `_deleted_ids` 是内存态已丢失，幽灵数据"复活"

### 1.2 根因分析

```python
# 当前实现 (line 322-334)
async def delete(self, memory_id: str) -> bool:
    async with self._lock:
        if memory_id not in self._items:
            return False
        del self._items[memory_id]
        self._deleted_ids.add(memory_id)
    # 问题：fire-and-forget，进程崩溃时不保证完成
    asyncio.create_task(self._compact_jsonl())
    return True
```

**问题链**:
1. `_deleted_ids` 是内存态 `set`，进程崩溃后丢失
2. `asyncio.create_task()` 不保证在进程崩溃前执行
3. JSONL 是 append-only，没有事务保护
4. `_load()` 没有持久化的删除记录可参考

### 1.3 影响评估

| 影响维度 | 严重程度 | 说明 |
|---------|---------|------|
| 数据一致性 | **HIGH** | 删除后重启，数据"复活" |
| 磁盘空间 | **MEDIUM** | 幽灵数据持续占用空间 |
| 搜索准确性 | **MEDIUM** | 已删除数据仍出现在搜索结果中 |

---

## 2. 修复方案

### 2.1 方案 A：同步等待 Compaction（推荐）

**核心思路**: 将 `asyncio.create_task()` 改为 `await`，确保 compaction 完成后再返回。

**优点**:
- 实现简单，改动最小
- 保证删除操作的持久化完成
- 无需引入新的持久化机制

**缺点**:
- delete 操作 latency 增加（需要等待文件 I/O）
- 在极高并发场景下可能影响吞吐量

```python
async def delete(self, memory_id: str) -> bool:
    async with self._lock:
        if memory_id not in self._items:
            return False
        del self._items[memory_id]
        self._deleted_ids.add(memory_id)
    # 同步等待 compaction 完成
    await self._compact_jsonl()
    return True
```

### 2.2 方案 B：软删除标记 + 惰性清理

**核心思路**: 在 JSONL 中保留删除标记，重启时惰性清理。

**实现**:
```python
# _load() 时检查删除标记
if item.memory_id in self._deleted_ids:
    continue  # 跳过已删除项
```

**缺点**:
- `_deleted_ids` 仍是内存态，进程崩溃后仍会丢失
- 复杂且收益不明显

### 2.3 方案 C：Append-Only 删除日志

**核心思路**: 创建 `.del` 日志文件记录删除操作。

**缺点**:
- 需要额外文件管理
- `_deleted_ids` 问题仍未解决
- 过度设计

---

## 3. 推荐方案：A（同步等待）

**理由**:
1. **简单直接**：最小改动，最大收益
2. **符合预期**：`delete()` 返回时应保证数据已真正删除
3. **无状态泄漏**：不需要新的持久化机制
4. **可测试性强**：易于验证删除的持久化保证

---

## 4. 数据流对比

### 修复前
```
delete() → del _items[memory_id] → add _deleted_ids → asyncio.create_task() → 返回
                                                      ↓
                                              进程崩溃（compaction 未执行）
                                                      ↓
                                              JSONL 仍含幽灵数据
```

### 修复后
```
delete() → del _items[memory_id] → add _deleted_ids → await _compact_jsonl() → 返回
                                                               ↓
                                                       JSONL 已清理
```

---

## 5. 副作用分析

### 5.1 性能影响

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 单次删除 | ~1ms | ~10-50ms (取决于 JSONL 大小) |
| 并发删除 100 次 | ~100ms (全部 fire-and-forget) | ~1000-5000ms (串行等待) |

**缓解措施**:
- JSONL compaction 在 `len(_deleted_ids)` > 阈值时才执行完整清理
- 小量删除仍快速返回

### 5.2 向后兼容性

**无影响**：
- 方法签名不变
- 返回值语义不变
- 公共 API 接口不变

---

## 6. 实施步骤

1. 修改 `delete()` 方法，将 `asyncio.create_task()` 改为 `await`
2. 验证 `delete()` 方法的调用方无死锁风险
3. 添加单元测试覆盖进程崩溃场景
4. 运行 `ruff check --fix` 和 `mypy`
5. 验证所有现有测试通过

---

## 7. 验证计划

```bash
# 代码规范检查
ruff check polaris/kernelone/akashic/semantic_memory.py --fix
ruff format polaris/kernelone/akashic/semantic_memory.py
mypy polaris/kernelone/akashic/semantic_memory.py --strict

# 单元测试
pytest polaris/kernelone/akashic/tests/ -v -k "delete"
```

---

## 8. 风险评估

| 风险 | 等级 | 缓解 |
|-----|------|------|
| 删除操作 latency 增加 | LOW | 实际影响可忽略（文件 I/O 本就应该等待） |
| compaction 失败导致 delete 失败 | LOW | compaction 有 try/except 保护 |

---

**文档状态**: 待评审
**预计工时**: 1h
