# ContextOS Async 一致性修复蓝图 2026-04-14

## 背景与目标

2026-04-14 三人专家团队审计发现 ContextOS 存在系统性 async/sync 不一致问题。
根因：commit 1524d192 将 `SessionContinuityEngine.project()` 改为 async，但调用链和测试未全部同步更新。

### 当前状态快照

| 指标 | 数值 |
|------|------|
| 核心源码文件 | 5 个，共 6,903 行 |
| 测试文件 | 37 个，共 11,926 行 |
| 测试结果 | **62 failed** / 750 passed / 4 skipped |
| async 方法（源码） | 19 个 |
| 缺少 await 的调用点（测试） | **44+ 个 `project()` + 11+ 个 `build_pack()`** |
| 缺少 await 的调用点（源码） | **1 个**（evaluation.py:676） |
| CRITICAL bug（已修复） | 1 个（`SessionContinuityStrategy.project()`） |

### 根因链路

```
SessionContinuityEngine.project() → async (commit 1524d192)
         ↓ 未 await
SessionContinuityStrategy.project() → 返回 coroutine 对象
         ↓ 未 await
HistoryMaterializationStrategy.get_continuity_pack() → 返回 coroutine 对象
         ↓
所有同步调用者获取 coroutine 而非实际结果
         ↓
AttributeError: 'coroutine' object has no attribute 'xxx'
```

### 本次治理目标

| 维度 | 当前 | 目标 |
|------|------|------|
| async 一致性 | 62 个测试失败 | 0 个失败 |
| 测试通过率 | 750/812 (92.4%) | 812/812 (100%) |
| 生产代码 bug | 1 个 evaluation.py | 0 个 |
| deprecated API | 3 处 `utcnow()` | 0 处 |
| ContextOS 实例管理 | 每次 project() 新建 | 单例复用 |

---

## 架构图：Async 调用链修复

```
                    ┌───────────────────────────────────┐
                    │         Console Host               │
                    │  _project_session_continuity()     │
                    │  ✅ await project_to_projection()  │
                    └──────────────┬────────────────────┘
                                   │
                    ┌──────────────▼────────────────────┐
                    │   SessionContinuityStrategy        │
                    │  ✅ async project()                │
                    │  ✅ async project_to_projection()  │
                    │  ✅ async build_pack()             │
                    │  ✅ async build_continuity_...()   │
                    └──────────────┬────────────────────┘
                                   │
                    ┌──────────────▼────────────────────┐
                    │  SessionContinuityEngine           │
                    │  async project()                   │
                    │  async build_pack()                │
                    │  ⚠️ _build_context_os() 每次新建  │
                    └──────────────┬────────────────────┘
                                   │
                    ┌──────────────▼────────────────────┐
                    │  StateFirstContextOS               │
                    │  async project()                   │
                    │  🔒 asyncio.Lock (每次新建=无效)   │
                    └───────────────────────────────────┘

修复范围:
  🔴 Phase 1: 62 个测试 async 修复
  🟠 Phase 2: evaluation.py 生产代码修复
  🟡 Phase 3: _build_context_os() 单例化
  🟢 Phase 4: deprecated API + 清理
```

---

## Phase 1: 测试 Async 修复 (P0)

### 1.1 影响范围

62 个失败测试分布在 7 个测试文件中：

| 测试文件 | 失败数 | 根因 | 修复模式 |
|---------|--------|------|---------|
| `test_attention_runtime.py` | 24 | `engine.project()` 无 await | async + await |
| `test_attention_runtime_boundaries.py` | 7 | `engine.project()` 无 await | async + await |
| `test_continuity.py` | 16 | `build_pack()` + `project()` 无 await | async + await |
| `test_context_os_observer.py` | 4 | `engine.project()` 无 await | async + await |
| `test_context_os_evaluation.py` | 3 | `engine.project()` 无 await | async + await |
| `test_safety_hardening.py` | 1 | `build_pack()` 无 await | async + await |
| `test_intelligent_compressor.py` | 2 | 浮点精度断言 | 调整 tolerance |
| `test_context_os_pipeline.py` | 5 | `runner.project()` 无 await | async + await |

### 1.2 修复模式

**模式 A: 同步测试 → 异步测试**

```python
# 修复前:
def test_something(self):
    projection = engine.project(messages=[...])
    assert projection.snapshot is not None  # ❌ projection 是 coroutine

# 修复后:
@pytest.mark.asyncio
async def test_something(self):
    projection = await engine.project(messages=[...])
    assert projection.snapshot is not None  # ✅
```

**模式 B: 浮点精度**

```python
# 修复前:
assert score_pinned > score_ref  # 两个 0.9999... 值比较

# 修复后:
assert score_pinned >= score_ref - 1e-12  # 允许浮点误差
```

### 1.3 逐文件修复清单

#### test_attention_runtime.py (734行, 24 失败)

需修改方法（全部加 `@pytest.mark.asyncio` + `async def` + `await`）：

| 类 | 方法 | 行号 |
|----|------|------|
| TestPendingFollowUpState | test_pending_followup_created_on_assistant_question | ~127 |
| TestPendingFollowUpState | test_pending_followup_confirmed_on_affirm | ~140 |
| TestPendingFollowUpState | test_pending_followup_denied_on_deny | ~158 |
| TestPendingFollowUpState | test_pending_followup_paused_on_pause | ~176 |
| TestRunCardV2 | test_run_card_v2_fields_populated | ~221 |
| TestRunCardV2 | test_run_card_v2_last_turn_outcome | ~240 |
| TestActiveWindowRootHardening | test_pending_followup_source_in_active_window | ~258 |
| TestActiveWindowRootHardening | test_latest_message_in_active_window | ~276 |
| TestActiveWindowRootHardening | test_min_recent_floor_respected | ~287 |
| TestSealGuard | test_seal_blocked_when_pending_followup | ~303 |
| TestSealGuard | test_seal_allowed_when_no_pending | ~312 |
| TestAttentionObservability | test_extract_attention_trace | ~326 |
| TestAttentionObservability | test_attention_runtime_case_evaluation | ~349 |
| TestAttentionObservability | test_evaluation_gate_continuity_alignment | ~454 |
| TestAttentionObservability | test_evaluation_gate_focus_regression_failure | ~468 |
| TestAttentionObservability | test_seal_blocked_in_attention_metrics | ~480 |
| TestAttentionObservability | test_context_redundancy_rate_detects_repeated_context | ~495 |
| TestResolvedFollowUpCleanup | test_resolved_followup_not_carried_forward | ~509 |
| TestResolvedFollowUpCleanup | test_run_card_clears_resolved_followup | ~532 |
| TestResolvedFollowUpCleanup | test_continuation_turn_keeps_latest_intent_and_hides_resolved_followup | ~543 |
| TestPendingFollowUpPersistence | test_pending_followup_serialization_roundtrip | ~573 |
| TestPendingFollowUpPersistence | test_pending_followup_in_snapshot_dict | ~604 |
| TestCodeDomainEnhancement | test_code_followup_intent_recognized | ~633 |
| TestCodeDomainEnhancement | test_code_workflow_hints_in_artifact_metadata | ~651 |

另外 3 个 TestContextWindowResolution 失败（ValueError: Context window not configured）需添加 llm_config mock。

#### test_attention_runtime_boundaries.py (480行, 7 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| TestStateFirstContextOSBoundaryCases | test_empty_messages | ~285 |
| TestStateFirstContextOSBoundaryCases | test_single_message | ~291 |
| TestStateFirstContextOSBoundaryCases | test_none_role_message | ~300 |
| TestStateFirstContextOSBoundaryCases | test_empty_content_message | ~308 |
| TestStateFirstContextOSBoundaryCases | test_zero_recent_window | ~316 |
| TestStateFirstContextOSBoundaryCases | test_negative_recent_window | ~326 |
| TestStateFirstContextOSBoundaryCases | test_very_large_recent_window | ~334 |
| TestAttentionRuntimeEvaluationBoundaryCases | test_conversation_with_empty_messages | ~341 |

#### test_continuity.py (629行, 16 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| TestContinuityPackGeneration | test_build_pack_from_messages | ~137 |
| TestContinuityPackGeneration | test_build_pack_requires_messages | ~144 |
| TestContinuityPackGeneration | test_build_pack_tracks_source_count | ~149 |
| TestContinuityPackGeneration | test_build_pack_sets_generated_at | ~154 |
| TestContinuityPackGeneration | test_build_pack_with_existing_pack | ~160 |
| TestLowSignalFiltering | test_omitted_count_tracked | ~190 |
| TestOpenLoopExtraction | test_respects_max_open_loops | ~236 |
| TestStableFactsExtraction | test_build_pack_includes_stable_facts | ~243 |
| TestStableFactsExtraction | test_respects_max_stable_facts | ~260 |
| TestSessionContinuityProjection | test_projection_returns_recent_messages | ~286 |
| TestSessionContinuityProjection | test_projection_updates_prompt_context | ~305 |
| TestSessionContinuityProjection | test_projection_detects_changes | ~324 |
| TestSessionContinuityProjection | test_projection_persisted_context_os_excludes_raw_truth_keys | ~350 |
| TestPendingFollowUpPersistenceIntegration | test_persisted_payload_includes_pending_followup | ~506 |
| TestPendingFollowUpPersistenceIntegration | test_context_os_snapshot_roundtrip_includes_pending_followup | ~551 |
| TestPendingFollowUpPersistenceIntegration | test_resolved_followup_not_carried_in_session | ~582 |

#### test_context_os_observer.py (223行, 4 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| TestObserverNotification | test_on_event_created_notification | ~140 |
| TestObserverErrorHandling | test_failing_observer_does_not_break_notification | ~168 |
| TestObserverErrorHandling | test_observer_with_partial_implementation | ~189 |
| TestObserverErrorHandling | test_multiple_observers_all_notified | ~219 |

#### test_context_os_evaluation.py (297行, 3 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| - | test_context_os_quality_suite_reports_recovery_metrics | ~15 |
| - | test_context_os_rollout_gate_reports_threshold_failures | ~97 |
| TestAttentionRuntimeReportGenerator | test_generate_attention_runtime_report | ~xxx |

#### test_safety_hardening.py (344行, 1 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| TestInjectionPrevention | test_code_injection_in_messages | ~311 |

#### test_intelligent_compressor.py (225行, 2 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| TestImportanceScorer | test_score_pinned_item_boost | ~xxx |
| TestImportanceScorer | test_score_reference_count_boost | ~xxx |

修复方式：调整浮点断言 tolerance，非 async 问题。

#### test_context_os_pipeline.py (400行, 5 失败)

| 类 | 方法 | 行号 |
|----|------|------|
| - | test_* (使用 `runner.project()` 无 await) | ~364, 376 |

---

## Phase 2: 生产代码修复 (P0)

### 2.1 evaluation.py:676 — 同步调用 async project()

```python
# 修复前 (evaluation.py:676):
def evaluate_attention_runtime_case(...):  # sync
    ...
    projection = runtime.project(...)  # ← coroutine 对象！

# 修复后:
async def evaluate_attention_runtime_case(...):
    ...
    projection = await runtime.project(...)
```

**级联修复**: 检查所有调用 `evaluate_attention_runtime_case()` 的地方，确保 await。

### 2.2 _build_context_os() 实例复用

```python
# 修复前 (session_continuity.py:633):
def _build_context_os(self, *, domain=None):
    return StateFirstContextOS(...)  # 每次新建 → Lock 无效

# 修复后:
class SessionContinuityEngine:
    def __init__(self, ...):
        self._context_os_cache: dict[str, StateFirstContextOS] = {}

    def _build_context_os(self, *, domain=None):
        key = domain or "generic"
        if key not in self._context_os_cache:
            self._context_os_cache[key] = StateFirstContextOS(...)
        return self._context_os_cache[key]
```

### 2.3 project() 传递 context_os_domain

```python
# 修复前 (session_continuity.py:484):
context_os_domain = self._resolve_context_os_domain(request, existing_snapshot)
context_os_projection = await self._build_context_os(domain=context_os_domain).project(...)

# project() 已通过 _resolve_context_os_domain 处理，但缺少显式参数
# 确保 _resolve_context_os_domain 覆盖所有 domain 来源
```

---

## Phase 3: Deprecated API 与代码清理 (P1)

### 3.1 datetime.utcnow() 替换 (3 处)

| 文件 | 行号 | 替换为 |
|------|------|--------|
| `context_os/runtime.py` | 253 | `datetime.now(timezone.utc)` |
| `context_os/snapshot.py` | 37 | `datetime.now(timezone.utc)` |
| `context_os/snapshot.py` | 203 | `datetime.now(timezone.utc)` |

### 3.2 except Exception 审计加固

context 子系统中 `except Exception` 为 0，但 `contextlib.suppress(Exception)` 有 5 处：

| 文件 | 行号 | 当前 | 建议 |
|------|------|------|------|
| `cache_manager.py` | 530 | `suppress(Exception)` | 缩窄为 `suppress(OSError, ValueError)` |
| `cache_manager.py` | 591 | `suppress(Exception)` | 缩窄为 `suppress(OSError, ValueError)` |
| `cache_manager.py` | 676 | `suppress(Exception)` | 缩窄为 `suppress(OSError, ValueError)` |
| `repo_intelligence/cache.py` | 295 | `suppress(Exception)` | 缩窄为 `suppress(OSError, ValueError)` |
| `repo_intelligence/cache.py` | 311 | `suppress(Exception)` | 缩窄为 `suppress(OSError, ValueError)` |

### 3.3 Docstring 更新

更新 `history_materialization.py` 中所有 docstring 使用示例为 async 形式。

---

## Phase 4: 测试加固 (P2)

### 4.1 新增测试

| 测试 | 目的 | 优先级 |
|------|------|--------|
| `test_async_chain_project()` | 验证 project() 全链路 await 正确 | P0 |
| `test_async_chain_build_pack()` | 验证 build_pack() 全链路 await 正确 | P0 |
| `test_context_os_instance_reuse()` | 验证 _build_context_os() 返回同一实例 | P1 |
| `test_concurrent_project()` | 验证并发 project() 不破坏状态 | P1 |
| `test_evaluation_async()` | 验证 evaluate_attention_runtime_case() async 修复 | P1 |

### 4.2 测试质量提升

- 所有新增 async 测试必须用 `@pytest.mark.asyncio`
- 添加 `conftest.py` 共享 fixtures：`engine_with_code_domain`, `engine_with_generic_domain`
- 添加 pytest.ini 配置确认 `asyncio_mode = auto`

---

## 验证计划

### Phase 1 验证（每批完成后执行）

```bash
# 验证单个文件修复
python -m pytest polaris/kernelone/context/tests/test_attention_runtime.py -v
python -m pytest polaris/kernelone/context/tests/test_continuity.py -v
python -m pytest polaris/kernelone/context/tests/test_context_os_observer.py -v
python -m pytest polaris/kernelone/context/tests/test_context_os_evaluation.py -v
python -m pytest polaris/kernelone/context/tests/test_safety_hardening.py -v

# 全量验证
python -m pytest polaris/kernelone/context/tests/ -v --tb=short
```

### Phase 2 验证

```bash
# 验证生产代码修复
python -m pytest polaris/kernelone/context/ -v --tb=short
python -m pytest polaris/cells/roles/kernel/tests/ -v --tb=short
```

### 最终门禁

```bash
# 全量回归
python -m pytest polaris/kernelone/context/tests/ -q
# 预期: 0 failed, 812+ passed
```

---

## 关键文件清单

| 文件 | 行数 | 修改类型 |
|------|------|---------|
| `polaris/kernelone/context/tests/test_attention_runtime.py` | 734 | Phase 1: 24 个方法 async 修复 |
| `polaris/kernelone/context/tests/test_continuity.py` | 629 | Phase 1: 16 个方法 async 修复 |
| `polaris/kernelone/context/tests/test_attention_runtime_boundaries.py` | 480 | Phase 1: 7 个方法 async 修复 |
| `polaris/kernelone/context/tests/test_context_os_observer.py` | 223 | Phase 1: 4 个方法 async 修复 |
| `polaris/kernelone/context/tests/test_context_os_evaluation.py` | 297 | Phase 1: 3 个方法 async 修复 |
| `polaris/kernelone/context/tests/test_safety_hardening.py` | 344 | Phase 1: 1 个方法 async 修复 |
| `polaris/kernelone/context/tests/test_intelligent_compressor.py` | 225 | Phase 1: 2 个断言修复 |
| `polaris/kernelone/context/tests/test_context_os_pipeline.py` | 400 | Phase 1: 5 个方法 async 修复 |
| `polaris/kernelone/context/context_os/evaluation.py` | 1773 | Phase 2: async 修复 |
| `polaris/kernelone/context/session_continuity.py` | 857 | Phase 2: _build_context_os 单例化 |
| `polaris/kernelone/context/context_os/runtime.py` | 2070 | Phase 3: utcnow 替换 |
| `polaris/kernelone/context/context_os/snapshot.py` | ~250 | Phase 3: utcnow 替换 |
| `polaris/kernelone/context/cache_manager.py` | ~700 | Phase 3: suppress 缩窄 |
| `polaris/kernelone/context/repo_intelligence/cache.py` | ~400 | Phase 3: suppress 缩窄 |
