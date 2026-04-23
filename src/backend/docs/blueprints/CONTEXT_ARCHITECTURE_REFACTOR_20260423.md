# ContextOS 架构重构蓝图 v1.0

> **文档编号**: BLUEPRINT-CONTEXT-ARCH-20260423  
> **编制日期**: 2026-04-23  
> **架构负责人**: Principal Architect  
> **执行团队**: 4人精英小组  
> **预计工期**: 12.5 人时

---

## 1. 业务背景与痛点

### 1.1 当前现状

ContextOS 是 Polaris 核心认知运行时（Cognitive Runtime）的基础设施层，负责：
- 会话状态的持久化与投影（Session State Projection）
- Token 预算的分配与强制执行（Token Budget Enforcement）
- 内容压缩与摘要（Content Compression & Summarization）
- 探索策略的决策（Exploration Policy Decision）

### 1.2 核心痛点

| 痛点 | 影响 | 优先级 |
|------|------|--------|
| 缺乏统一会话协议 | L4（应用层）与 L5（内核层）耦合，难以测试和 Mock | P0 |
| 压缩策略分散 | `intelligent_compressor.py`、`compaction.py`、`summarizers/` 各自为政，难以扩展 | P0 |
| ExplorationPolicy 不可插拔 | 启发式规则硬编码，无法动态切换策略 | P1 |
| 异步一致性隐患 | 部分模块仍使用 threading.RLock，与 asyncio 生态冲突 | P1 |

---

## 2. 系统架构图

### 2.1 目标态架构（重构后）

```
┌─────────────────────────────────────────────────────────────────────┐
│                           L4: Application Layer                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │  Role Session   │  │   Turn Engine   │  │  Exploration Loop   │  │
│  │   Orchestrator  │  │   Controller    │  │   (WorkingSetAsm)   │  │
│  └────────┬────────┘  └────────┬────────┘  └──────────┬──────────┘  │
└───────────┼────────────────────┼──────────────────────┼─────────────┘
            │                    │                      │
            ▼                    ▼                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      L5: KernelOne - ContextOS                       │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    ContextSessionProtocol                    │    │
│  │  (add/remove/query/archive - 标准CRUD接口)                   │    │
│  └───────────────────────────┬─────────────────────────────────┘    │
│                              │                                       │
│  ┌───────────────────────────┴─────────────────────────────────┐    │
│  │                  StateFirstContextOS                         │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │    │
│  │  │ ContentStore│  │ ReceiptStore│  │  WorkingStateMgr    │  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘  │    │
│  └───────────────────────────┬─────────────────────────────────┘    │
│                              │                                       │
│  ┌───────────────────────────┴─────────────────────────────────┐    │
│  │                 CompressionRegistry                          │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐  │    │
│  │  │  CodeStruct  │ │   Semantic   │ │   TieredSummarizer  │  │    │
│  │  │ Compressor   │ │ Compressor   │ │   (via Adapter)     │  │    │
│  │  └──────────────┘ └──────────────┘ └─────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              SelectorPolicy (Protocol)                       │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐  │    │
│  │  │ GreedySelect │ │ BudgetAware  │ │   SemanticRank      │  │    │
│  │  │   Strategy   │ │   Strategy   │ │     Strategy        │  │    │
│  │  └──────────────┘ └──────────────┘ └─────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责边界

| 模块 | 职责 | 禁止事项 |
|------|------|----------|
| **contracts.py** | 定义 L4/L5 边界协议 | 不得包含实现逻辑 |
| **context_os/** | 状态管理与投影引擎 | 不得直接访问 LLM Provider |
| **compressors/** | 压缩策略注册与执行 | 不得持有业务状态 |
| **exploration_policy.py** | 探索决策策略 | 不得执行 I/O 操作 |

---

## 3. 核心数据流

### 3.1 会话生命周期（ContextSessionProtocol）

```
┌─────────────┐     add()      ┌──────────────┐     query()     ┌─────────────┐
│   Session   │ ─────────────→ │  ContentStore│ ──────────────→ │  Projection │
│   Request   │                │  (Append-only│                 │   Engine    │
└─────────────┘                │   TruthLog)  │                 └─────────────┘
       │                       └──────────────┘                        │
       │                              │                               │
       ▼                              ▼                               ▼
┌─────────────┐                ┌──────────────┐                 ┌─────────────┐
│   remove()  │ ─────────────→ │ VersionCtrl  │ ──────────────→ │  Snapshot   │
│  (Soft Del) │                │ (乐观锁控制)  │                 │   Store     │
└─────────────┘                └──────────────┘                 └─────────────┘
       │                              │                               │
       ▼                              ▼                               ▼
┌─────────────┐                ┌──────────────┐                 ┌─────────────┐
│  archive()  │ ─────────────→ │ ReceiptStore │ ──────────────→ │  ColdStore  │
│ (Hard Del)  │                │ (Compaction) │                 │ (Archive)   │
└─────────────┘                └──────────────┘                 └─────────────┘
```

### 3.2 压缩策略流水线

```
Input Content
      │
      ▼
┌─────────────────┐
│ CompressionReg  │ ──→ 策略选择（基于 Content-Type + Budget）
│   .select()     │
└────────┬────────┘
         │
    ┌────┴────┬────────────┬──────────────┐
    ▼         ▼            ▼              ▼
┌───────┐ ┌────────┐ ┌──────────┐ ┌──────────────┐
│  Code │ │Semantic│ │ Extract  │ │   Tiered     │
│Struct │ │   LLM  │ │   SLM    │ │ Summarizer   │
└───┬───┘ └───┬────┘ └────┬─────┘ └──────┬───────┘
    │         │           │              │
    └─────────┴───────────┴──────────────┘
                    │
                    ▼
            ┌──────────────┐
            │ Compression  │ ──→ 质量评估 + 元数据追踪
            │    Result    │
            └──────────────┘
```

---

## 4. 关键技术选型

### 4.1 Protocol vs ABC

**选择 Protocol（运行时结构子类型检查）**

理由：
- L4/L5 解耦需要鸭子类型，无需强制继承
- 支持现有类的渐进式适配（无需修改继承链）
- 与 `mypy --strict` 完美兼容

```python
# 正确做法
class ContextSessionProtocol(Protocol):
    async def add(self, artifact: Artifact) -> None: ...

# 错误做法（过度约束）
class ContextSessionABC(ABC):
    @abstractmethod
    async def add(self, artifact: Artifact) -> None: ...
```

### 4.2 注册表模式 vs 依赖注入

**选择注册表模式（Registry Pattern）**

理由：
- 压缩策略是算法族，生命周期短（per-request）
- 避免 DI 容器的复杂性和隐藏依赖
- 策略发现机制支持动态扩展

### 4.3 asyncio.Semaphore vs asyncio.Lock

**保留 asyncio.Lock（Phase 1 已完成）**

现状：
- `runtime.py` 已使用 `asyncio.Lock`（line 182）
- 需要排查 `content_store.py`、`history_materialization.py` 的残留 RLock

升级策略：
- 读多写少场景：保留 `asyncio.Lock`（已经满足）
- 高并发读场景：未来可引入 `aiocache` 或 `read-write lock`

---

## 5. 实施计划

### 5.1 任务分解与委派

| 任务ID | 任务描述 | 委派专家 | 工时 | 依赖 |
|--------|----------|----------|------|------|
| T1 | 设计 ContextSessionProtocol | 契约架构师 | 2h | 无 |
| T2 | 实现 CompressionRegistry | 工厂模式专家 | 3h | T1 |
| T3 | 重构 ExplorationPolicy（SelectorPolicy Protocol） | 策略模式专家 | 2h | T1 |
| T4 | 修复异步一致性（RLock → Lock） | 并发专家 | 4h | T2 |
| T5 | 编写 ADR-007 & 测试覆盖 | 技术文档专家 | 1.5h | T4 |

### 5.2 文件变更清单

**新建文件**:
- `polaris/kernelone/context/compressors/registry.py`
- `docs/adr/ADR-007_async_migration.md`

**修改文件**:
- `polaris/kernelone/context/contracts.py` (+60 lines)
- `polaris/kernelone/context/exploration_policy.py` (+80 lines, -30 lines)
- `polaris/kernelone/context/context_os/content_store.py` (RLock audit)
- `polaris/kernelone/context/history_materialization.py` (RLock audit)

### 5.3 风险与回滚策略

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Protocol 定义不完善 | 中 | 高 | T1 完成后进行接口评审 |
| 压缩注册表性能瓶颈 | 低 | 中 | 添加 LRU 缓存，可回退到直接导入 |
| RLock 替换引入竞态 | 中 | 高 | T4 增加 stress test，分阶段灰度 |
| 策略模式破坏现有决策 | 低 | 高 | T3 保留默认策略，新策略需显式启用 |

---

## 6. 验收标准

### 6.1 门禁检查清单

- [ ] `ruff check . --fix` 无警告
- [ ] `mypy --strict polaris/kernelone/context/` 零错误
- [ ] `pytest polaris/kernelone/context/ -v` 100% 通过
- [ ] `python -c "from polaris.kernelone.context.contracts import ContextSessionProtocol; print('OK')"` 成功

### 6.2 功能验证

- [ ] `CompressionRegistry.register()` 支持 Decorator 语法
- [ ] `SelectorPolicy` 可在 `ExplorationContext` 中动态切换
- [ ] `content_store.py` 中无 `threading.RLock` 残留
- [ ] ADR-007 文档完整记录决策过程

---

## 7. 团队分工

```
Principal Architect (你)
        │
        ├──→ 契约架构师 (Contract Architect)
        │      └─ T1: ContextSessionProtocol 设计
        │
        ├──→ 工厂模式专家 (Factory Specialist)
        │      └─ T2: CompressionRegistry 实现
        │
        ├──→ 策略模式专家 (Strategy Specialist)
        │      └─ T3: ExplorationPolicy 重构
        │
        └──→ 并发与文档专家 (Concurrency & Docs)
               ├─ T4: 异步一致性修复
               └─ T5: ADR-007 + 测试
```

---

**批准状态**: ✅ 待执行  
**最后更新**: 2026-04-23  
**下一里程碑**: T1-T3 并行开发完成（Day 1）
