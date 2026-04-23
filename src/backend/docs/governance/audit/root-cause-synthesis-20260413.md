# 根因综合与修复建议

**日期**: 2026-04-13
**任务**: #92
**状态**: ✅ 完成

---

## 1. 执行摘要

本次综合审计覆盖 9 个维度，发现 **3 个 BLOCKER 级问题**、**14 个 HIGH 级问题**、**25 个 MEDIUM/LOW 级问题**。

| 维度 | BLOCKER | HIGH | MEDIUM | LOW | 状态报告 |
|------|---------|------|--------|-----|---------|
| 架构契约 | 2 | 2 | 1 | 0 | ✅ |
| 系统事件流 | 0 | 2 | 3 | 2 | ✅ |
| LLM 交互 | 0 | 1 | 5 | 11 | ✅ |
| 资源配额 | 0 | 0 | 3 | 4 | ✅ |
| 安全权限 | 0 | 1 | 3 | 2 | ✅ |
| 性能延迟 | 0 | 1 | 4 | 2 | ✅ |
| 数据一致性 | 0 | 0 | 3 | 0 | ✅ |
| 可观测性 | 0 | 2 | 4 | 3 | ✅ |
| Cell 合并 | 2 | 4 | 2 | 1 | ✅ |
| **合计** | **4** | **14** | **28** | **25** | **9/9 ✅** |

---

## 2. BLOCKER 级问题（必须立即修复）

### 2.1 director.tasking → director.execution 内部导入
**严重程度**: BLOCKER
**发现来源**: #84 架构契约分析
**位置**: `polaris/cells/director/tasking/internal/worker_executor.py:157`
**问题**: `tasking.internal.file_apply_service` 直接导入 `execution.internal` 模块，违反 Cell 边界

**根因**: director 子 Cell 在 Phase 1-3 迁移时保留了旧有的跨 Cell internal 调用习惯

**修复方案**:
1. 将 `director.execution.internal.file_apply_service` 的能力提升到 `director.execution.public` 契约
2. `director.tasking.internal.worker_executor` 改为导入 `director.execution.public`
3. 更新 `cells.yaml` 中 `director.execution` 的 `public_contracts.modules`

**预计工时**: 1-2 人天

### 2.2 events.fact_stream 单写者违规
**严重程度**: BLOCKER
**发现来源**: #84 架构契约分析
**位置**: 多个 cells 声称直接写入 `runtime/events/*`
**问题**: `events.fact_stream` 是唯一合法 writer，但 22 个 Cell 声称 effects_allowed 包含 `fs.write:runtime/events/*`

**根因**: cells.yaml 中 `effects_allowed` 声明与 `events.fact_stream` 单写者 reality 不一致

**修复方案**:
1. 从所有 Cell 的 `effects_allowed` 中移除 `fs.write:runtime/events/*`
2. 统一通过 `events.fact_stream` 的 public contract 写入
3. 新增 `fitness-rules.yaml` 规则 `events_fact_stream_singleton_writer` (blocker 级别)

**预计工时**: 0.5 人天（CI 脚本自动化）

### 2.3 roles.kernel → roles.session 内部导入
**严重程度**: BLOCKER（ACGA 2.0 约束）
**发现来源**: #84 架构契约分析
**位置**: `polaris/cells/roles/kernel/internal/kernel.py:21`, `kernel/core.py:38`
**问题**: `roles.kernel` 直接导入 `roles.session.internal` 模块

**根因**: kernel 和 session 在设计时未完全解耦，kernel 需要 session 的内部路径存储机制

**修复方案**:
1. 在 `roles.session.public.contracts` 中添加路径解析 public contract
2. `roles.kernel.internal.kernel` 改为通过 public contract 访问 session 能力
3. 迁移后移除 `kernel/internal/kernel.py` 的 direct import

**预计工时**: 2-3 人天

---

## 3. HIGH 级问题（短期内修复）

### 3.1 重复 dangerous_patterns 定义（5 处）
**来源**: #84 架构契约分析
**位置**: `roles/adapters`, `roles/kernel` 等
**问题**: 同一危险模式检测逻辑在多处独立实现，维护成本高

**修复**: 统一使用 `kernelone.security.dangerous_patterns` canonical 源头

### 3.2 重复 _resolve_artifact_path 定义（1 处）
**来源**: #84 架构契约分析
**问题**: `docs/court_workflow` 中有独立实现

**修复**: 统一使用 `kernelone.storage.io_paths`

### 3.3 流式/非流式超时配置分离
**来源**: #86 LLM 交互分析
**位置**: `polaris/kernelone/llm/`
**问题**: 流式和非流式超时配置分开管理，容易不一致

**修复**: 统一超时配置管理

### 3.4 Provider Fallback 路由单一
**来源**: #86 LLM 交互分析
**问题**: 当前仅支持顺序 fallback，无智能路由

**修复**: 实现基于模型评级的智能 fallback 路由

### 3.5 Prompt 模板注入风险
**来源**: #89 安全与权限分析
**问题**: `_build_reinjection_prompt()` 缺少输入验证，无明确转义机制

**修复**: 添加输入验证和转义

### 3.6 PATH 白名单未实现
**来源**: #89 安全与权限分析
**问题**: `_filter_safe_path_entries()` 标记为 TODO

**修复**: 实现 PATH 白名单过滤

### 3.7 ContextOS Transcript 多次遍历
**来源**: #90 性能与延迟分析
**位置**: `context/context_os/runtime.py`
**问题**: 同一 transcript 被遍历 3 次（merge, canonicalize, patch）

**修复**: 实现单次遍历 + 结果复用

### 3.8 缓存命中时同步刷盘（fsync）
**来源**: #90 性能与延迟分析
**位置**: `context/cache_manager.py:536`
**问题**: 每次缓存命中都写回磁盘并 fsync，高并发下性能差

**修复**: 实现异步写回 + 批量 fsync

### 3.9 RLock 阻塞事件循环
**来源**: #90 性能与延迟分析
**位置**: `context/context_os/runtime.py:169`
**问题**: `threading.RLock` 阻塞 asyncio 事件循环

**修复**: 替换为 `asyncio.Lock`

### 3.10 Metrics 体系碎片化（4 套并行）
**来源**: #32 可观测性体系
**问题**: `MetricsCollector`, `MetricsRecorder`, `AuditMetricsCollector`, Cell 级 MetricsCollector 并存

**修复**: 收敛为 1 套 Metrics 标准

### 3.11 Tracing 体系碎片化（4 套并行）
**来源**: #32 可观测性体系
**问题**: `DistributedTracer`, `UnifiedTracer`, `TraceCarrier`, `CognitiveTelemetry` 并存

**修复**: 收敛为 1 套 Tracing 标准

### 3.12 LLM Executor / Tool Execution 埋点缺失
**来源**: #32 可观测性体系
**问题**: 核心路径无 spans，高优先级补全

**修复**: 补充埋点

---

## 4. MEDIUM/LOW 级问题汇总

### 4.1 静默失败点（系统事件流）
- `llm_caller/invoker.py:1525-1528`: `asyncio.create_task` 后 `except RuntimeError: pass`
- `io_events.py:404`: 无追踪的 fire-and-forget
- `uep_publisher.py:351`: bus 为 None 时静默返回

### 4.2 异常处理
- `executor.py` L172: 捕获后丢失原始异常链
- 206 处 `except Exception` / `except:` 异常吞噬

### 4.3 Schema 演化
- `EventBase.event_version` 已定义但无迁移逻辑
- `UEPStreamEventPayload` / `UEPLifecycleEventPayload` 无版本字段

### 4.4 环境变量混用
- `KERNELONE_` (769 处) 和 `KERNELONE_` (225 处) 同一代码层混用

### 4.5 冷启动问题
- `ModelCatalog` 每次 `resolved_context_window` 调用都重新实例化
- 模块级 `_load_llm_config()` 在导入时执行

### 4.6 资源配额问题
- `close_sync()` 失败仅 warn，无重试（中）
- 文件锁异常时无清理（中）
- Multi-agent 用 `asyncio.Lock` 无法在 sync 上下文使用（中）
- 语义缓存无大小限制（低）
- Provider 失败计数在实例化前就增加（低）
- 重复 agent 分配静默成功（低）
- `_loop_break_tools` 无超时机制（低）

---

## 5. 修复 Roadmap

| 阶段 | 时间 | 修复项 |
|------|------|--------|
| **Sprint 1** | 1-2 周 | BLOCKER 级全部修复 |
| **Sprint 2** | 3-4 周 | HIGH 级 P0 项 |
| **Sprint 3** | 5-8 周 | HIGH 级 P1 项 + MEDIUM 级 |
| **Sprint 4** | 9-12 周 | Metrics/Tracing 收敛 + 低优先级 |

---

## 6. 关键参考

- `docs/governance/audit/log-audit-architecture-contracts-20260413.md`
- `docs/governance/audit/log-audit-event-stream-20260413.md`
- `docs/governance/audit/log-audit-llm-interaction-20260413.md`
- `docs/governance/audit/log-audit-security-permission-20260413.md`
- `docs/governance/audit/log-audit-performance-latency-20260413.md`
- `docs/governance/audit/log-audit-data-consistency-20260413.md`
- `docs/governance/audit/observability-gap-analysis-20260413.md`
- `docs/governance/audit/cell-dependency-analysis-and-merge-plan-20260413.md`
