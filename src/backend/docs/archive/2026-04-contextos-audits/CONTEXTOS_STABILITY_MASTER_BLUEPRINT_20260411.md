# ContextOS 稳定性治理蓝图 2026-04-11

## 背景与目标

根据"上帝视角"10维审计，ContextOS 当前评分为 **5.5/10**，存在以下核心问题：

1. **内存无限增长** - artifact_store、transcript_log 永不清理
2. **并发安全缺失** - 无锁设计但存在竞态条件
3. **错误处理不足** - 静默吞噬异常掩盖错误
4. **架构 God Object** - StateFirstContextOS 承担7+职责
5. **安全风险** - 无敏感信息过滤、无输入限制

**本次治理目标**: 将 ContextOS 提升至 **8/10**，实现生产级稳定性。

---

## 系统架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         ContextOS Layer                               │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              StateFirstContextOS (God Object)               │    │
│  │   ⚠️ 待拆分: project() 390行 → Pipeline Processors       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                       │
│         ┌──────────────────┼──────────────────┐                   │
│         ▼                  ▼                  ▼                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │Transcript   │  │ Canonical  │  │ Budget      │             │
│  │Merger      │  │ & Offload   │  │ Planner     │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Port/Contract Layer                       │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │    │
│  │  │ContextDomain │ │Artifact     │ │Lifecycle         │  │    │
│  │  │Adapter       │ │StoragePort  │ │Observer          │  │    │
│  │  │(Core)       │ │(NEW)        │ │(NEW)            │  │    │
│  │  └──────────────┘ └──────────────┘ └──────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Data Model Layer                           │    │
│  │  ContextOSSnapshot │ ContextOSProjection │ TranscriptEvent  │    │
│  │  ArtifactRecord    │ EpisodeCard        │ BudgetPlan      │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 模块职责划分

### 新增模块

| 模块 | 职责 | 状态 |
|------|------|------|
| `ports.py` (扩展) | 新增 `ArtifactStoragePort`, `ContextOSObserverPort` | 待创建 |
| `pipeline/` | 拆分后的处理器管道 | 待创建 |
| `policies.py` | Policy 配置分组 | 待创建 |

### 重构模块

| 模块 | 重构内容 | 优先级 |
|------|----------|--------|
| `runtime.py` | 拆分 project() 为管道步骤 | P0 |
| `domain_adapters/contracts.py` | 分离 Observer 接口 | P0 |
| `models.py` | Policy 分组 + 内存限制字段 | P1 |
| `memory_search.py` | 错误处理改进 | P1 |

---

## 核心数据流

```
输入: messages[] + existing_snapshot
         │
         ▼
┌─────────────────┐
│ TranscriptMerger │ ← 合并消息，检测 tool_call
└────────┬────────┘
         │ transcript
         ▼
┌─────────────────┐
│ Canonicalizer    │ ← 分类路由，处理 pending_followup
└────────┬────────┘
         │ transcript + artifacts
         ▼
┌─────────────────┐
│ StatePatcher     │ ← 派生 working_state
└────────┬────────┘
         │ + budget_plan
         ▼
┌─────────────────┐
│ BudgetPlanner    │ ← 规划 token 预算
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ WindowCollector  │ ← 收集 active_window
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ EpisodeSealer    │ ← 密封闭环 episode
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ ArtifactSelector │ ← 选择 artifact_stubs
└────────┬────────┘
         │
         ▼
    ContextOSProjection
         │
         ├── head_anchor (goal + open_loops)
         ├── tail_anchor (next focus)
         ├── active_window (事件窗口)
         ├── artifact_stubs (artifact 引用)
         ├── episode_cards (episode 摘要)
         └── run_card (运行状态)
```

---

## 技术选型理由

### 1. 管道模式 (Pipeline) 替代 God Object

**现状**: `project()` 方法 390 行，违反 SRP
**方案**: 拆分为 7 个独立处理器

**理由**:
- 每个处理器单一职责，易于测试
- 便于后续扩展新的处理步骤
- 符合 Unix 管道哲学

### 2. Observer 模式分离 Lifecycle 事件

**现状**: `ContextDomainAdapter` 包含 8 个方法，违反 ISP
**方案**: 分离为 `ContextDomainAdapter` (核心) + `ContextOSObserver` (通知)

**理由**:
- 核心接口保持稳定
- 通知机制可按需实现
- 减少适配器实现负担

### 3. Artifact Storage Port 抽象

**现状**: 硬编码 500/200 字符阈值
**方案**: 引入 `ArtifactStoragePort`，支持内存/文件/S3 多级存储

**理由**:
- 支持真正的 offload 而非伪压缩
- 便于未来扩展分布式存储
- 解耦存储策略和使用场景

### 4. 内存预算保护

**现状**: 无限内存增长风险
**方案**: 添加 `max_transcript_events`, `max_artifact_store_mb` 限制

**理由**:
- 防止 OOM
- 提供明确的资源边界
- 支持资源受限环境部署

---

## 实施计划

### Phase 1: 核心稳定性 (1-2周)

| 任务 | 负责人 | 目标 |
|------|--------|------|
| P1-1: 修复竞态条件 | Agent-1 | dialog_act_classifier 属性线程安全 |
| P1-2: 错误处理改进 | Agent-2 | memory_search 正确异常传播 |
| P1-3: 输入验证 | Agent-3 | 添加大小限制 |
| P1-4: 压缩 Bug 修复 | Agent-4 | compress 不超出目标 |
| P1-5: 文档完善 | Agent-5 | 线程模型/设计决策文档 |

### Phase 2: 架构重构 (2-4周)

| 任务 | 负责人 | 目标 |
|------|--------|------|
| P2-1: 管道拆分 | Agent-6 | StateFirstContextOS.project() → Pipeline |
| P2-2: Observer 接口 | Agent-7 | 分离 LifecycleObserver |
| P2-3: Storage Port | Agent-8 | ArtifactStoragePort 抽象 |
| P2-4: Policy 重构 | Agent-9 | Policy 分组 |
| P2-5: Token 统一 | Agent-10 | 估算器收敛 |

### Phase 3: 长期演进 (4-8周)

| 任务 | 目标 |
|------|------|
| P3-1: 持久化层 | Snapshot 序列化和恢复 |
| P3-2: 多租户隔离 | tenant_id 字段 |
| P3-3: 性能测试 | 压力测试套件 |

---

## 验收标准

### 代码质量
- [ ] ruff check: 0 errors
- [ ] mypy --strict: 0 warnings
- [ ] pytest: 100% pass
- [ ] 无 DRY 违规
- [ ] 无裸 except:

### 功能正确性
- [ ] compress() 不超出目标 token
- [ ] memory_search 正确传播异常
- [ ] 输入验证拒绝超大 payload
- [ ] 并发调用不崩溃

### 架构健康
- [ ] project() 拆分完成
- [ ] Observer 接口独立
- [ ] ArtifactStoragePort 抽象完成
- [ ] 线程模型文档完整

---

## 风险与缓解

| 风险 | 影响 | 缓解策略 |
|------|------|----------|
| 重构破坏向后兼容 | 高 | 保持外部接口不变，内部实现重构 |
| 性能回归 | 中 | 添加 benchmark 测试 |
| 工期超期 | 中 | 按优先级分阶段交付 |

---

## 版本历史

| 版本 | 日期 | 修改内容 |
|------|------|----------|
| 1.0 | 2026-04-11 | 初始版本 |
