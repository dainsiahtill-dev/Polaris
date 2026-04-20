# ContextOS Async 一致性修复 — 3人专家团队执行计划

**蓝图**: `CONTEXTOS_ASYNC_CONSISTENCY_REPAIR_20260414.md`
**总工期**: 2 天
**目标**: 62 failed → 0 failed，生产代码 0 async bug

---

## 团队分工

```
┌──────────────────────────────────────────────────────────────────┐
│                    工作包依赖关系                                   │
│                                                                    │
│  包A: 测试修复 (大文件)     包B: 测试修复 (小文件)               │
│  test_attention_runtime     test_continuity                      │
│  test_attention_runtime_*   test_context_os_observer             │
│  test_context_os_pipeline   test_context_os_evaluation           │
│         ↓                         ↓                               │
│         └──────────┬───────────────┘                              │
│                    ↓                                               │
│             包C: 生产代码修复                                      │
│             evaluation.py async                                    │
│             _build_context_os 单例化                               │
│             deprecated API 清理                                    │
│                    ↓                                               │
│             最终门禁验证                                           │
└──────────────────────────────────────────────────────────────────┘
```

---

## 包 A — 测试修复 (大文件集群)

**负责人**: 专家 A
**预估**: 1 天
**优先级**: P0

### A.1 修复 test_attention_runtime.py (24 失败)

**修复模式**: 每个失败方法加 `@pytest.mark.asyncio` + `async def` + `await`

```python
# 示例修复 (模式适用于全部 24 个方法):
# 修复前:
def test_pending_followup_created_on_assistant_question(self):
    engine = StateFirstContextOS(...)
    projection = engine.project(messages=[...])  # ← coroutine
    assert projection.snapshot is not None       # ← AttributeError

# 修复后:
@pytest.mark.asyncio
async def test_pending_followup_created_on_assistant_question(self):
    engine = StateFirstContextOS(...)
    projection = await engine.project(messages=[...])  # ← await
    assert projection.snapshot is not None
```

**逐方法修复清单** (24 个):

1. `TestPendingFollowUpState.test_pending_followup_created_on_assistant_question` (~L127)
2. `TestPendingFollowUpState.test_pending_followup_confirmed_on_affirm` (~L140)
3. `TestPendingFollowUpState.test_pending_followup_denied_on_deny` (~L158)
4. `TestPendingFollowUpState.test_pending_followup_paused_on_pause` (~L176)
5. `TestRunCardV2.test_run_card_v2_fields_populated` (~L221)
6. `TestRunCardV2.test_run_card_v2_last_turn_outcome` (~L240)
7. `TestActiveWindowRootHardening.test_pending_followup_source_in_active_window` (~L258)
8. `TestActiveWindowRootHardening.test_latest_message_in_active_window` (~L276)
9. `TestActiveWindowRootHardening.test_min_recent_floor_respected` (~L287)
10. `TestSealGuard.test_seal_blocked_when_pending_followup` (~L303)
11. `TestSealGuard.test_seal_allowed_when_no_pending` (~L312)
12. `TestAttentionObservability.test_extract_attention_trace` (~L326)
13. `TestAttentionObservability.test_attention_runtime_case_evaluation` (~L349)
14. `TestAttentionObservability.test_evaluation_gate_continuity_alignment` (~L454)
15. `TestAttentionObservability.test_evaluation_gate_focus_regression_failure` (~L468)
16. `TestAttentionObservability.test_seal_blocked_in_attention_metrics` (~L480)
17. `TestAttentionObservability.test_context_redundancy_rate_detects_repeated_context` (~L495)
18. `TestResolvedFollowUpCleanup.test_resolved_followup_not_carried_forward` (~L509)
19. `TestResolvedFollowUpCleanup.test_run_card_clears_resolved_followup` (~L532)
20. `TestResolvedFollowUpCleanup.test_continuation_turn_keeps_latest_intent_and_hides_resolved_followup` (~L543)
21. `TestPendingFollowUpPersistence.test_pending_followup_serialization_roundtrip` (~L573)
22. `TestPendingFollowUpPersistence.test_pending_followup_in_snapshot_dict` (~L604)
23. `TestCodeDomainEnhancement.test_code_followup_intent_recognized` (~L633)
24. `TestCodeDomainEnhancement.test_code_workflow_hints_in_artifact_metadata` (~L651)

**额外**: `TestContextWindowResolution` 3 个测试因缺少 `llm_config.json` 配置失败，需添加 fixture mock。

### A.2 修复 test_attention_runtime_boundaries.py (7 失败)

同模式，7 个方法全部加 `@pytest.mark.asyncio` + `async def` + `await`：

1. `test_empty_messages` (~L285)
2. `test_single_message` (~L291)
3. `test_none_role_message` (~L300)
4. `test_empty_content_message` (~L308)
5. `test_zero_recent_window` (~L316)
6. `test_negative_recent_window` (~L326)
7. `test_very_large_recent_window` (~L334)

### A.3 修复 test_context_os_pipeline.py (5 失败)

同模式，修复所有 `runner.project()` 调用为 `await runner.project()`。

### 验证命令

```bash
python -m pytest polaris/kernelone/context/tests/test_attention_runtime.py \
    polaris/kernelone/context/tests/test_attention_runtime_boundaries.py \
    polaris/kernelone/context/tests/test_context_os_pipeline.py -v
```

预期: 全部 passed

---

## 包 B — 测试修复 (小文件集群 + 断言修复)

**负责人**: 专家 B
**预估**: 1 天
**优先级**: P0

### B.1 修复 test_continuity.py (16 失败)

**两类修复**:

**类型 1: `build_pack()` 无 await** (6 个方法)
```python
# 修复前:
def test_build_pack_from_messages(self):
    pack = engine.build_pack(messages=[...])  # ← coroutine
    assert pack.summary  # ← AttributeError

# 修复后:
@pytest.mark.asyncio
async def test_build_pack_from_messages(self):
    pack = await engine.build_pack(messages=[...])
    assert pack.summary
```

方法清单:
1. `TestContinuityPackGeneration.test_build_pack_from_messages` (~L137)
2. `TestContinuityPackGeneration.test_build_pack_requires_messages` (~L144)
3. `TestContinuityPackGeneration.test_build_pack_tracks_source_count` (~L149)
4. `TestContinuityPackGeneration.test_build_pack_sets_generated_at` (~L154)
5. `TestContinuityPackGeneration.test_build_pack_with_existing_pack` (~L160)
6. `TestLowSignalFiltering.test_omitted_count_tracked` (~L190)
7. `TestOpenLoopExtraction.test_respects_max_open_loops` (~L236)
8. `TestStableFactsExtraction.test_build_pack_includes_stable_facts` (~L243)
9. `TestStableFactsExtraction.test_respects_max_stable_facts` (~L260)

**类型 2: `project()` 无 await** (7 个方法)
10. `TestSessionContinuityProjection.test_projection_returns_recent_messages` (~L286)
11. `TestSessionContinuityProjection.test_projection_updates_prompt_context` (~L305)
12. `TestSessionContinuityProjection.test_projection_detects_changes` (~L324)
13. `TestSessionContinuityProjection.test_projection_persisted_context_os_excludes_raw_truth_keys` (~L350)
14. `TestPendingFollowUpPersistenceIntegration.test_persisted_payload_includes_pending_followup` (~L506)
15. `TestPendingFollowUpPersistenceIntegration.test_context_os_snapshot_roundtrip_includes_pending_followup` (~L551)
16. `TestPendingFollowUpPersistenceIntegration.test_resolved_followup_not_carried_in_session` (~L582)

### B.2 修复 test_context_os_observer.py (4 失败)

同模式，4 个方法。注意：observer 通知在 async project() 中触发，修复后应正确接收事件。

1. `test_on_event_created_notification` (~L140)
2. `test_failing_observer_does_not_break_notification` (~L168)
3. `test_observer_with_partial_implementation` (~L189)
4. `test_multiple_observers_all_notified` (~L219)

### B.3 修复 test_context_os_evaluation.py (3 失败)

同模式。额外需检查 `evaluate_attention_runtime_case()` 如果改为 async，这里的调用也需 await。

1. `test_context_os_quality_suite_reports_recovery_metrics` (~L15)
2. `test_context_os_rollout_gate_reports_threshold_failures` (~L97)
3. `TestAttentionRuntimeReportGenerator.test_generate_attention_runtime_report`

### B.4 修复 test_safety_hardening.py (1 失败)

1. `TestInjectionPrevention.test_code_injection_in_messages` (~L311) — `build_pack()` 无 await

### B.5 修复 test_intelligent_compressor.py (2 失败)

**非 async 问题**，是浮点精度断言：

```python
# 修复前:
assert score_pinned > score_ref
# 实际值: 0.999999999985101 vs 0.9999999999862471 → score_ref 更大

# 修复后:
assert score_pinned >= score_ref - 1e-12  # 允许浮点误差
# 或者：
assert abs(score_pinned - score_ref) < 1e-10  # 近似相等
```

方法:
1. `TestImportanceScorer.test_score_pinned_item_boost`
2. `TestImportanceScorer.test_score_reference_count_boost`

### 验证命令

```bash
python -m pytest polaris/kernelone/context/tests/test_continuity.py \
    polaris/kernelone/context/tests/test_context_os_observer.py \
    polaris/kernelone/context/tests/test_context_os_evaluation.py \
    polaris/kernelone/context/tests/test_safety_hardening.py \
    polaris/kernelone/context/tests/test_intelligent_compressor.py -v
```

预期: 全部 passed

---

## 包 C — 生产代码修复 + 最终门禁

**负责人**: 专家 C
**预估**: 1 天
**优先级**: P0 → P1 → P2（按顺序）

### C.1 修复 evaluation.py:676 (P0)

**文件**: `polaris/kernelone/context/context_os/evaluation.py`

```python
# 修复前 (line 676):
def evaluate_attention_runtime_case(
    self,
    ...,
) -> AttentionRuntimeCaseResult:
    ...
    runtime = StateFirstContextOS(...)
    projection = runtime.project(  # ← sync call to async method
        messages=[msg],
        existing_snapshot=snapshot,
        recent_window_messages=8,
    )

# 修复后:
async def evaluate_attention_runtime_case(
    self,
    ...,
) -> AttentionRuntimeCaseResult:
    ...
    runtime = StateFirstContextOS(...)
    projection = await runtime.project(
        messages=[msg],
        existing_snapshot=snapshot,
        recent_window_messages=8,
    )
```

**级联修复**: 搜索所有调用 `evaluate_attention_runtime_case` 的地方，加 await。

### C.2 _build_context_os() 单例化 (P1)

**文件**: `polaris/kernelone/context/session_continuity.py`

```python
# 修复前:
class SessionContinuityEngine:
    def _build_context_os(self, *, domain=None):
        return StateFirstContextOS(...)  # 每次新建

# 修复后:
class SessionContinuityEngine:
    def __init__(self, ...):
        ...
        self._context_os_cache: dict[str, StateFirstContextOS] = {}

    def _build_context_os(self, *, domain: str | None = None) -> StateFirstContextOS:
        key = domain or "generic"
        if key not in self._context_os_cache:
            self._context_os_cache[key] = StateFirstContextOS(
                policy=...,
                domain=key,
            )
        return self._context_os_cache[key]
```

### C.3 datetime.utcnow() 替换 (P1)

**文件与行号**:
- `context_os/runtime.py:253` → `datetime.now(timezone.utc).isoformat()`
- `context_os/snapshot.py:37` → `datetime.now(timezone.utc).isoformat()`
- `context_os/snapshot.py:203` → `datetime.now(timezone.utc).isoformat()`

确保 `from datetime import datetime, timezone` 已导入。

### C.4 contextlib.suppress 缩窄 (P2)

**文件**:
- `cache_manager.py:530,591,676` → `suppress(OSError, ValueError, RuntimeError)`
- `repo_intelligence/cache.py:295,311` → `suppress(OSError, ValueError, RuntimeError)`

### C.5 最终门禁

**在包 A 和包 B 完成后执行**:

```bash
# 全量回归测试
python -m pytest polaris/kernelone/context/tests/ -q --tb=line

# 集成测试
python -m pytest polaris/cells/roles/kernel/tests/ -q --tb=line

# 预期结果:
# 0 failed, 812+ passed
```

**回归验证清单**:
- [ ] 全量 context 测试 0 失败
- [ ] context_gateway 集成测试通过
- [ ] console_host 集成测试通过
- [ ] 无新增 deprecation warning
- [ ] ruff check 通过
- [ ] mypy 通过（如有类型变更）

---

## 执行时间线

```
Day 1 上午:
  包A 开始: test_attention_runtime.py (24个方法)
  包B 开始: test_continuity.py (16个方法)
  包C 开始: evaluation.py async 修复

Day 1 下午:
  包A 继续: test_attention_runtime_boundaries.py + test_context_os_pipeline.py
  包B 继续: test_context_os_observer + test_context_os_evaluation + test_safety_hardening
  包C 继续: _build_context_os 单例化 + datetime 替换

Day 2 上午:
  包A/B: 修复 test_intelligent_compressor.py 断言
  包C: suppress 缩窄 + docstring 更新
  全量回归测试

Day 2 下午:
  最终门禁
  蓝图状态更新
  提交代码
```

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| async 修复引入新 bug | 低 | 高 | 每批修复后立即运行验证 |
| observer 通知在 async 修复后仍不触发 | 中 | 中 | 检查 observer 注册时序 |
| _build_context_os 单例化导致状态泄漏 | 中 | 高 | 单元测试验证实例隔离 |
| evaluate_attention_runtime_case 调用者遗漏 | 低 | 高 | grep 全量搜索调用者 |
| TestContextWindowResolution 需 mock | 低 | 低 | fixture 配置 llm_config |

---

## 质量门禁

1. **pytest**: `polaris/kernelone/context/tests/` 全量 0 失败
2. **ruff**: `ruff check` 无 error/warning
3. **类型安全**: `mypy` 关键文件无 error（如有类型变更）
4. **集成**: `context_gateway` + `console_host` 测试通过
5. **文档**: 蓝图状态标记为 completed
