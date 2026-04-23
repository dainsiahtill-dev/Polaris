# C桶架构整治蓝图

> **文档编号**: BLUEPRINT-CONTEXTOS-ARCH-CLEANUP-20260423
> **类型**: 架构级技术债整治
> **工期**: 4周（可并行）
> **优先级**: P1（次于P0修复）

---

## C1: sync/async 边界收敛

**现状**: ContentStore 已采用 async-only + sync facade 模式，但全目录仍有零散 sync 调用。

**目标**: 明确 ContextOS 内核为 async-first，所有 sync API 仅为兼容 facade。

**实施路线**:
1. 梳理全目录 sync 方法清单（grep `def .*\(` 不含 async）
2. 分类：
   - **Facade 类**: 保留，标记 `@sync_facade`，内部必须委托到 async core
   - **计算密集型**: 移到锁外，使用 `run_in_executor()`
   - **遗留代码**: 评估是否可移除
3. 制定 `SYNC_API_DEPRECATION` 时间表：
   - v2.1: 标记所有 sync facade 为 deprecated
   - v2.2: 移除非必要 sync facade
   - v2.3: 内核完全 async-only

---

## C2: immutable model 统一突变模式

**现状**: 混合使用 `model_copy(update=...)`、`replace()`、`frozen dataclass`。

**目标**: 所有不可变模型统一使用 `validated_replace()` 或显式重建。

**实施路线**:
1. 创建 `ModelMutationPolicy` 枚举：
   ```python
   class ModelMutationPolicy(Enum):
       VALIDATED_REPLACE = "validated_replace"  # 推荐
       EXPLICIT_REBUILD = "explicit_rebuild"    # 复杂变更
       READONLY = "readonly"                    # 禁止任何突变
   ```
2. 为关键模型标注策略：
   - `TranscriptEventV2` → `VALIDATED_REPLACE`
   - `ContextOSSnapshotV2` → `READONLY`
   - `BudgetPlanV2` → `EXPLICIT_REBUILD`
3. 静态扫描：禁止非策略允许的突变方式

---

## C3: 锁范式统一

**目标**: 所有并发代码统一使用 "Snapshot → Compute → Validate/Commit" 范式。

**规范**:
```python
# 标准模板
async def critical_operation(self, input_data):
    # 1. 拍快照（锁内，最小化）
    async with self._lock:
        snapshot = self._take_snapshot()
    
    # 2. 计算（锁外，允许 run_in_executor）
    result = await self._compute(snapshot, input_data)
    
    # 3. 验证 + 提交（锁内，原子化）
    async with self._lock:
        if self._validate(result):
            self._commit(result)
    
    return result
```

**检查点**:
- `async with` 块内代码行数 ≤ 10 行
- 禁止在锁内调用外部服务
- 禁止在锁内做 JSON dump/load（大对象）

---

## C4: Contract Boundary 测试

**现状**: 契约边界审计未完成，新 Protocol（`SelectorPolicy`、`ContextSessionProtocol`）缺乏向后兼容测试。

**目标**: 为所有跨模块边界建立 contract test。

**实施路线**:
1. 创建 `polaris/kernelone/context/tests/contracts/` 目录
2. 为每个 Protocol 编写 contract test：
   - `test_selector_policy_contract.py`
   - `test_context_session_protocol_contract.py`
   - `test_compression_registry_contract.py`
3. 每个 contract test 验证：
   - 默认实现满足 Protocol
   - 新方法有默认行为（不破坏旧调用方）
   - 类型签名一致性（通过 mypy）

---

## 优先级与排期

| 整治项 | 工期 | 依赖 | 建议启动时间 |
|--------|------|------|-------------|
| C1 sync/async 收敛 | 2周 | F2 完成 | 2026-04-28 |
| C2 immutable model | 1周 | F1 完成 | 2026-04-24 |
| C3 锁范式统一 | 1周 | F3 完成 | 2026-04-28 |
| C4 contract test | 1周 | C1-C3 | 2026-05-05 |

**关键里程碑**: 2026-05-12 前完成全部架构整治，准备 v2.1 发布。
