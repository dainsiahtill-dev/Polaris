# 双轨缓存系统收敛架构方案

**版本**: v1.0
**日期**: 2026-04-04
**状态**: 待执行
**优先级**: P0

---

## 1. 执行摘要

`KernelOneCacheManager` (cache.py) 和 `TieredAssetCacheManager` (cache_manager.py) 是两套独立的缓存实现。收敛方案为 **Option A - Facade 模式**：以 `TieredAssetCacheManager` 为 canonical 实现，将 `KernelOneCacheManager` 改造为其子类/ facade，保留旧 TTL 语义以确保向后兼容。

**关键冲突**: continuity (24h) vs projection (2min) — 这是真实业务语义差异，不能简单合并。

---

## 2. 系统架构分析

### 2.1 双轨现状对比

| 属性 | `KernelOneCacheManager` (cache.py) | `TieredAssetCacheManager` (cache_manager.py) |
|------|----------------------------------|---------------------------------------------|
| **缓存根路径** | `.polaris/cache/` | `.polaris/kernelone_cache/` |
| **热切片TTL** | 5分钟, 最多20条 | 5分钟, 最多50条 |
| **Continuity/Projection TTL** | **24小时** | **2分钟** |
| **Repo Map TTL** | 无TTL (仅mtime失效) | 10分钟 |
| **热切片Key格式** | `hot_slice:<file_path>` | `slice\|{path}\|{start}\|{end}` |
| **协议** | 无 | `IAssetCache` Protocol |
| **get_or_compute** | 无 | 有 |
| **Eviction统计** | 无 | 有 |
| **CacheTier枚举** | `SESSION/WORKSPACE/PERSISTENT` | 5层细分 |
| **代码行数** | ~250行 | ~430行 |

### 2.2 关键冲突点

1. **TTL语义完全不同**: continuity_pack=24h vs projection=2min (业务语义冲突)
2. **路径不兼容**: 不能共享缓存数据
3. **Key格式不兼容**: 无法互通

### 2.3 现有 duck typing 防护

`working_set.py:225` 已接受两种实现：
```python
cache_manager: KernelOneCacheManager | TieredAssetCacheManager | None = None
```

---

## 3. 模块职责划分 (收敛后)

```
polaris/kernelone/context/
├── cache_manager.py          # [Canonical] 5层缓存实现 + IAssetCache协议
│   └── TieredAssetCacheManager (Canonical)
├── cache.py                  # [Facade] 旧接口兼容层
│   └── KernelOneCacheManager (Facade → TieredAssetCacheManager)
└── __init__.py              # 统一导出
```

---

## 4. 收敛方案: Facade 模式

### 4.1 TTL 映射策略

| 功能 | KernelOneCacheManager (旧) | TieredAssetCacheManager (新) | 收敛后 |
|------|---------------------------|-----------------------------|-------|
| Hot Slice TTL | 5min, max 20 | 5min, max 50 | **max 50** (更宽松) |
| Continuity Pack | 24h TTL | N/A | facade 覆盖为 **24h TTL** |
| Projection Pack | N/A | 2min TTL | 独立使用 2min |
| Repo Map | 无TTL (mtime) | 10min TTL | **10min TTL** |

### 4.2 实现: KernelOneCacheManager as Facade

```python
class KernelOneCacheManager(TieredAssetCacheManager):
    """[已废弃] 向后兼容 facade。请直接使用 TieredAssetCacheManager。

    本类继承 TieredAssetCacheManager，但覆盖以下参数以保留旧语义：
    - projection_ttl=86400.0 (24h) vs 新系统默认 120s
    - hot_slice_max_entries=20 vs 新系统默认 50

    缓存路径仍为 .polaris/cache/ (通过覆盖 _resolve_cache_root)
    """

    # 保持旧缓存路径
    def _resolve_cache_root(self) -> Path:
        root = Path(self._workspace) / ".polaris" / "cache"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def __init__(
        self,
        workspace: str | Path,
        *,
        hot_slice_max_entries: int = 20,       # 旧限制
        continuity_ttl: float = 86400.0,       # 24h，旧语义
        hot_slice_ttl: float = 300.0,
        **kwargs,
    ) -> None:
        # projection_ttl 对应 continuity pack 语义
        kwargs.setdefault('projection_ttl', continuity_ttl)
        kwargs.setdefault('hot_slice_max_entries', hot_slice_max_entries)
        kwargs.setdefault('hot_slice_ttl', hot_slice_ttl)
        super().__init__(workspace, **kwargs)
```

---

## 5. 实施步骤

### Phase 1: Facade 改造 (低风险)
- [ ] 修改 `KernelOneCacheManager` 继承 `TieredAssetCacheManager`
- [ ] 删除已不需要的重复实现代码 (~150行)
- [ ] 覆盖 `_resolve_cache_root` 保持旧路径
- [ ] 覆盖 `__init__` 参数保持旧 TTL 语义
- [ ] 保留 `get_cache_manager()` 返回 `KernelOneCacheManager` (向后兼容)

### Phase 2: 统一导出
- [ ] `__init__.py` 标记 `KernelOneCacheManager` 为 deprecated
- [ ] 文档化推荐使用 `TieredAssetCacheManager`

### Phase 3: 清理 (后续)
- [ ] 检查所有直接实例化 `KernelOneCacheManager` 的位置
- [ ] 评估是否可以迁移到 `TieredAssetCacheManager`

---

## 6. 风险评估

| 风险 | 等级 | 缓解 |
|-----|------|------|
| Continuity TTL 差异 (24h vs 2min) | **HIGH** | facade 层覆盖 projection_ttl=86400s |
| 缓存路径不同导致旧缓存孤立 | MEDIUM | 可接受，旧缓存自然过期 |
| `get_session()` 方法丢失 | LOW | 旧系统 session_cache 移至 facade |

---

## 7. 验收标准

```bash
# 缓存相关测试
pytest polaris/kernelone/context/tests/test_cache_manager.py -v
pytest polaris/kernelone/context/tests/test_tiered_cache.py -v

# working_set 集成测试
pytest polaris/kernelone/context/tests/ -k working_set -v

# 导入验证
python -c "from polaris.kernelone.context import KernelOneCacheManager, TieredAssetCacheManager"
```
