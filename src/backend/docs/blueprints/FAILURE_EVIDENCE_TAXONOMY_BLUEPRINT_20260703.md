# FailureEvidenceV1 Taxonomy Blueprint (2026-07-03)

## 1. 问题陈述（量化证据）

2026-07-03 对抗审计确认：失败分类在每层由字符串重推导——保住单个 `tool_dispatch_dropped` 类穿越全链动了 **49 处代码 / 16 文件 / 8 层**；**18 个 UPPERCASE failure_class 字面量**在 10 个文件手工赋值、零共享 enum；全仓存在 **3 个同名 `FailureClass` enum**（`kernelone/audit/failure_envelope.py` 死代码零生产引用、`roles/kernel/public/turn_contracts.py:450` 公共契约在用、error-correlation 侧一份）；~20 个平行 ad-hoc 分类字段；`roles/runtime/public/result_mapping.py` 把整个失败空间坍缩成 2 个 error_code；bench_gates 用 12 处 json.dumps + ~33 个子串判断反推失败类。思想实验实证：今天新增失败类（`llm_timeout` / `session_not_active` / `coverage_gap`）**到不了 Run Ledger**——每类都需重打 8 层补丁。

## 2. 目标态架构

```
检测点(kernel completion / batch executor / adapter scope guard / llm timeout / session guard)
   │  在检测那一刻构造 FailureEvidenceV1 并 append_run_ledger_event(单一摄入点)
   ▼
control_plane.run_ledger.public.failure_evidence
   FailureEvidenceV1{failure_class: FailureClassV1(enum), responsible_layer,
                     failure_stage, retryable, reason, evidence_refs, metadata}
   │  下游全部只读投影
   ▼
result_mapping: error_code = failure_class 原样透传(未知才回落 role_runtime_error)
adapter: 上游已有 typed class 恒优先于 workspace-diff 推断(不再默认改写为 INCOMPLETE_MATERIALIZATION)
factory / QA / bench: 从 Run Ledger 投影读取, 删除子串嗅探
```

裁决要点：

1. **`FailureClassV1` 单一 enum**落位 `control_plane.run_ledger.public`：收编现存 18 个 UPPERCASE 字面量为成员；`kernelone/audit/failure_envelope.FailureClass`（死代码）删除或别名到新 enum；`roles/kernel/public/turn_contracts.FailureClass` 是**公共契约不动其形**——提供双向映射函数并测试钉住两 enum 的语义对应，后续大版本再合并。
2. 其余 ~20 个 ad-hoc 字段（`materialization_mode`、`root_cause_hint`……）降级为 `metadata`，不得作为分类判定键。
3. 检测点写入是 fail-closed 义务：现有 debug-log-and-swallow 的 except 块改为至少 append 一条 evidence。
4. 泛化性验收（本蓝图的硬验收）：把 `llm_timeout` 作为第二个真实类接线——adapter 超时路径（现为散文 `director_{stage}_llm_timeout`）构造 typed evidence 后，**不加任何逐层补丁**即可在 Run Ledger 投影与 bench failure_category 中看到 `LLM_TIMEOUT`。

## 3. Phase 2a 落地范围（本蓝图）

1. `failure_evidence.py`：`FailureClassV1`（含现存 18 类 + `LLM_TIMEOUT` + `SESSION_NOT_ACTIVE` + `COVERAGE_GAP` 预留）+ `FailureEvidenceV1` + `append_failure_evidence()`（走既有 `append_run_ledger_event` 单入口）+ run ledger projection 读取/汇总（沿 `tool_lifecycle` 事件先例）。
2. `result_mapping._to_contract_result`：删除 2 值坍缩——上游 metadata/turn 结果携带 typed failure_class 时原样透传为 `error_code`，`role_runtime_error` 仅作未知回落；既有 `tool_dispatch_dropped` 行为不变（它成为 enum 成员）。
3. `execute_method` 失败投影：读取 typed class（metadata 优先），子串嗅探降级为兼容回落并记 `failure_class_source=substring_fallback`；upstream-class-wins 优先序钉测试。
4. `llm_timeout` 接线为泛化性证明（adapter 超时路径 + Run Ledger 投影断言 + bench runner 消费断言）。
5. 死 enum 处置：`failure_envelope.FailureClass` 零生产引用坐实后删除（保留 shim 别名一版）。

Phase 2b（后续）：factory/bench 子串嗅探全量替换为 ledger 投影读取；QA verdict 引擎接入；`turn_contracts.FailureClass` 合并评估（需跨 cell 契约变更流程）。

## 4. 验证

- 全链回归：completion/result_mapping/adapter/factory char/bench_gates/run_ledger 套件全绿。
- 新增：enum 完备性（18 字面量全部有成员且值相等——用 grep 清单钉）、透传测试（每类 error_code 端到端）、llm_timeout 零补丁直达测试、substring_fallback 兼容测试。

## 4.1 2026-07-04 增量落地记录

- `control_plane.run_ledger.public.task_boundary` 已新增
  `TaskBoundaryFailureClassV1`，将 TaskBoundary 专属 failure class 从本地裸字符串
  表升级为 public typed enum；外部 `TaskBoundaryVerdictV1.failure_class` 输出值保持
  兼容。
- `evaluate_task_boundary_verdict()` 与 task-boundary normalizer 已改为消费
  `TaskBoundaryFailureClassV1`，`FailureClassV1.TOOL_DISPATCH_DROPPED` 继续由
  tool lifecycle canonical enum 负责，避免重复定义同一工具生命周期失败类。
- `test_failure_taxonomy_boundary_fence.py` 已扩展到扫描 `*FailureClassV1`，并登记
  `TaskBoundaryFailureClassV1` 的 owner，防止新增 V1 后缀枚举绕过 taxonomy fence。
- 验证：
  `rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_task_boundary.py src/backend/polaris/tests/architecture/test_failure_taxonomy_boundary_fence.py -q`；
  `rtk ruff check src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py src/backend/polaris/cells/control_plane/run_ledger/public/__init__.py src/backend/polaris/cells/control_plane/run_ledger/tests/test_task_boundary.py src/backend/polaris/tests/architecture/test_failure_taxonomy_boundary_fence.py`；
  `rtk mypy src/backend/polaris/cells/control_plane/run_ledger/public/task_boundary.py src/backend/polaris/cells/control_plane/run_ledger/public/__init__.py`。

## 5. 风险与边界

- `turn_contracts.FailureClass` 是 roles.kernel 公共契约，本阶段仅映射不合并，避免跨 cell 破坏性变更。
- bench_gates 的子串检查本阶段保留（Phase 2b 替换），typed 路径与其并行工作——不允许出现"typed 有、子串无"导致 gate 判定变化的中间态：新 evidence 事件对既有 gate 是加性。
