# Polaris/KernelOne 架构收敛计划

**版本**: v1.0
**日期**: 2026-04-04
**状态**: 待执行
**优先级**: P0-P2

---

## 1. 执行摘要

通过 AST 静态分析、模式匹配和架构审计，在 `polaris/kernelone` (703 items, ~600+ Python 文件) 中发现 **7 大类结构性重复**。然而，经过深度验证后：

| 类别 | 实际情况 | 处理策略 |
|------|---------|---------|
| **ProviderManager 权威分散** | ✅ **已修复** | 无需操作 |
| **双轨缓存系统** | ⚠️ 需决策 | 取决于业务优先级 |
| **ToolRegistry vs ToolSpecRegistry** | ⚠️ 架构迁移中 | Phase 5 待完成 |
| **LLM 工具链重复** | ❌ 误报 | 仅为 facade 模式 |
| **_normalize_json_value 重复** | ✅ 10行小问题 | 建议修复 |
| **策略注册表重复** | ⚠️ 需评估 | 低优先级 |
| **Akashic 记忆系统** | ⚠️ 需评估 | 低优先级 |

---

## 2. 已确认修复的问题 (无需操作)

### 2.1 ProviderManager 权威分散 ✅ 已解决

**状态**: `polaris/cells/llm/provider_runtime/internal/providers.py` 已修正为直接返回 infrastructure 单例。

**当前架构**:
```
cells/provider_runtime/internal/providers.py
    └── get_provider_manager() → infrastructure 单例 (正确)

kernelone/llm/providers/registry.py
    └── get_provider_manager() → 委托 infrastructure (已废弃，仅类型兼容)
```

**无需操作**: Authority dispersion 问题已在之前的审计中修复。

---

## 3. P0 - 双轨缓存系统 (需要决策)

### 3.1 现状分析

| 属性 | `KernelOneCacheManager` (cache.py) | `TieredAssetCacheManager` (cache_manager.py) |
|------|----------------------------------|---------------------------------------------|
| **缓存层级** | 3层: SESSION/WORKSPACE/PERSISTENT | 5层: SESSION_CONTINUITY/REPO_MAP/SYMBOL_INDEX/HOT_SLICE/PROJECTION |
| **缓存路径** | `.polaris/cache/` | `.polaris/kernelone_cache/` |
| **Hot Slice TTL** | 5分钟, 最多20条 | 5分钟, 最多50条 |
| **Continuity/Projection TTL** | **24小时** | **2分钟** |
| **Repo Map TTL** | 无TTL (仅mtime失效) | 10分钟 |
| **Hot Slice Key格式** | `hot_slice:<file_path>` | `slice\|{path}\|{start}\|{end}` |
| **实现完整性** | 早期实现 | 更完整, 实现 IAssetCache 协议 |

### 3.2 关键问题

1. **路径不兼容**: 两者使用不同缓存路径，不能共享数据
2. **TTL 语义冲突**: "continuity" 概念在两个实现中完全不同 (24h vs 2min)
3. **Key格式不兼容**: 无法互通
4. **working_set.py 使用 duck typing**: 接受任一实现，但无法共享缓存

### 3.3 收敛方案 A: 保留 TieredAssetCacheManager (推荐)

**策略**: 以 `cache_manager.py` 为 canonical 实现，废弃 `cache.py`

```python
# cache.py 改造为 facade
from .cache_manager import TieredAssetCacheManager

# 保持向后兼容导出
class KernelOneCacheManager(TieredAssetCacheManager):
    """已废弃，请使用 TieredAssetCacheManager"""
    pass

__all__ = ['KernelOneCacheManager', 'TieredAssetCacheManager', ...]
```

**优点**:
- 统一的 5 层架构
- 更完整的 IAssetCache 协议实现
- 更好的可测试性

**缺点**:
- 缓存路径变更，可能丢失既有缓存数据
- 需要回归测试

### 3.4 收敛方案 B: 保留双轨但明确边界

**策略**: 文档化两者的使用场景，禁止混用

| 场景 | 实现 |
|------|------|
| Symbol indexing / Hot slice | `TieredAssetCacheManager` |
| Legacy compatibility | `KernelOneCacheManager` |

**缺点**:
- 维护两套缓存逻辑
- 可能导致内存浪费

### 3.5 收敛方案 C: 不收敛

**理由**:
- 两者 TTL 语义完全不同，强行合并会破坏业务逻辑
- `TieredAssetCacheManager` 是更新的架构，有完整的协议定义
- `KernelOneCacheManager` 可能被外部依赖使用

**推荐**: 方案 A - 以 `TieredAssetCacheManager` 为 canonical

---

## 4. P1 - ToolRegistry vs ToolSpecRegistry (架构迁移中)

### 4.1 现状

| 注册表 | 文件 | 状态 | 用途 |
|--------|------|------|------|
| `ToolSpecRegistry` | `tools/tool_spec_registry.py` | **Canonical** | 单一权威工具定义源 |
| `ToolRegistry` (agent) | `agent/tools/registry.py` | Phase 3 未完成 | Agent 内置工具管理 |
| `ToolRegistry` (llm) | `llm/toolkit/definitions.py` | **已废弃** | 旧版工具注册 |
| `EventRegistry` | `events/typed/registry.py` | 独立保留 | 事件订阅发布 |

### 4.2 关键发现

1. **ToolSpecRegistry 是 canonical**: 有完整的别名、handler 路由、LLM schema 生成功能
2. **agent/tools/registry.py 未集成**: `direct_executor.py` 中有 `NotImplementedError`，等待 Phase 5
3. **llm/toolkit/definitions.py 已废弃**: 注释明确说明使用 `_TOOL_SPECS` 替代

### 4.3 收敛方案

**当前不应强制收敛**，原因：

1. `agent/tools/registry.py` 的 `ToolRegistry` 尚未完成集成，有 NotImplementedError
2. 强行收敛会导致运行时错误
3. Phase 5 规划中有明确的迁移路径

**建议**: 等待 Phase 5 完成后，再评估收敛。

---

## 5. P1 - LLM 工具链重复 (误报)

### 5.1 验证结果

| 声称重复 | 实际情况 |
|---------|---------|
| `parsers.py` vs `parsers/*.py` | **无重复** - parsers.py 是 facade 再导出 |
| `message_normalizer.py` vs `parsers/utils.py` | **无重复** - 不同用途 (消息规范化 vs 工具调用解析) |
| `runtime_executor.py` vs `executor/*.py` | **无重复** - wrapper 模式，runtime_executor 委托 AgentAccelToolExecutor |

### 5.2 发现的实际重复

**`_normalize_json_value` 函数**: 10 行代码，在两处完全相同：

| 文件 | 行号 |
|------|------|
| `llm/tools/normalizer.py` | 86-95 |
| `llm/toolkit/executor/runtime.py` | 31-41 |

**推荐处理**: 保留 `llm/toolkit/executor/runtime.py` 版本，删除 `llm/tools/normalizer.py` 中的副本。

---

## 6. P2 - 策略/代码域注册表

### 6.1 现状

| 文件 | 用途 |
|------|------|
| `context/strategy_registry.py` | 策略注册 |
| `context/strategy_overlay_registry.py` | 策略覆盖注册 |
| `context/strategy_code_domain.py` | 代码分类 |
| `context/chunks/taxonomy.py` | 分块分类 |

### 6.2 评估

需要进一步分析 `strategy_overlay_registry.py` 是否可被主注册表吸收。

---

## 7. P2 - Akashic 记忆系统

### 7.1 现状

| 文件 | 用途 |
|------|------|
| `akashic/memory_manager.py` | 记忆管理 |
| `akashic/integration.py` | 集成 |
| `akashic/knowledge_pipeline/` | 知识管道 |

### 7.2 评估

需要进一步分析记忆检索逻辑是否真的重复，以及 SemanticCache 与主缓存系统的关系。

---

## 8. 收敛实施计划

### Phase 1: 修复确认的重复 (Week 1)

| 任务 | 操作 | 影响 |
|------|------|------|
| `_normalize_json_value` 去重 | 删除 `llm/tools/normalizer.py` 中的副本 | 低 |

### Phase 2: 决策双轨缓存 (Week 2-3)

| 任务 | 操作 | 影响 |
|------|------|------|
| 双轨缓存收敛决策 | 选定方案 A/B/C | 中 |

### Phase 3: 等待 ToolRegistry 集成 (Phase 5)

当前不建议操作，等待 Phase 5 完成。

### Phase 4: 策略注册表评估 (Week 4)

| 任务 | 操作 | 影响 |
|------|------|------|
| 分析 strategy_overlay_registry | 评估合并可行性 | 低 |

---

## 9. 验收检查清单

```bash
# 1. _normalize_json_value 去重后测试
python -m pytest polaris/kernelone/llm/toolkit/tests/ -v

# 2. 缓存系统测试
python -m pytest polaris/kernelone/context/tests/ -v

# 3. 架构门禁
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```

---

## 10. 风险评估

| 风险 | 等级 | 缓解 |
|-----|------|------|
| 缓存路径变更 | MEDIUM | 迁移期保留旧路径读取 |
| ToolRegistry 强制收敛 | HIGH | 等待 Phase 5 完成 |
| 策略注册表误合并 | LOW | 先评估再操作 |

---

**文档状态**: 待评审
**预计工时**: Phase 1 (1h), Phase 2 (1d), Phase 3 (等待), Phase 4 (2h)
