# KernelOne 注意力运行时代码审计报告

**日期**: 2026-03-27
**审计范围**: A1-A5 Attention Runtime Implementation + 用户复现 Critical Issues
**状态**: ✅ 所有问题已修复 + A5 评估门禁已完善 + 集成测试已添加

---

## 审计完成摘要

### 已修复问题 (共 14 项)

| ID | 问题 | 状态 | 提交 |
|----|------|------|------|
| BUG-001 | Pending Follow-Up 状态不一致 | ✅ | c18cd6e2 |
| BUG-002 | Dialog Act 模式匹配优先级冲突 | ✅ | c18cd6e2 |
| BUG-003 | focus_regression_rate 计算逻辑错误 | ✅ | c18cd6e2 |
| CRITICAL-001 | pending_followup 不持久化 | ✅ | c18cd6e2 |
| CRITICAL-002 | resolved follow-up 不清退 | ✅ | c18cd6e2 |
| CRITICAL-003 | A7 代码域 follow-up 未接入主链路 | ✅ | c18cd6e2 |
| CRITICAL-004 | 测试断言过弱 | ✅ | c18cd6e2 |
| IMPROVE-001 | 代码重复消除 | ✅ | c18cd6e2 |
| IMPROVE-003 | 边界条件测试覆盖 | ✅ | c18cd6e2 |
| IMPROVE-004 | 类型注解完善 | ✅ | c18cd6e2 |
| IMPROVE-005 | extract_attention_trace 类型注解 | ✅ | 0a5951f5 |
| T-009 | Feature Switch 环境变量配置 | ✅ | c18cd6e2 |
| A5-Gate | continuity_focus_alignment_rate 完善 | ✅ | 0a5951f5 |
| Integration | SessionContinuity 持久化集成测试 | ✅ | f74671e9 |

### 测试覆盖

- **边界测试**: 39 个测试用例
- **集成测试**: 3 个新测试用例 (pending_followup 持久化)
- **A5 评估测试**: 2 个新测试用例
- **总计**: 493 passed, 9 skipped

### 提交记录

```
f74671e9 test(context): 添加 pending_followup 持久化集成测试
0a5951f5 fix(context-os): 完善 A5 评估门禁 - continuity_focus_alignment_rate
c18cd6e2 feat(context): 修复注意力运行时关键问题并增强持久化
```

---

## 一、审计详情

### 1.1 发现的问题总数
| 严重程度 | 数量 | 描述 |
|---------|------|------|
| **必须修复 (Bug)** | 5 | 逻辑缺陷、边界问题、持久化缺失 |
| **建议改进** | 5 | 代码质量、重复代码 |
| **可选优化** | 4 | 性能、扩展性 |

### 1.2 核心问题分类
1. **状态管理问题**: Pending follow-up 状态在某些边界条件下不一致
2. **模式匹配冲突**: Dialog act 分类器存在优先级冲突
3. **测试覆盖盲区**: 边界场景和异常场景覆盖不足
4. **代码重复**: `_extract_assistant_followup_action` 重复实现
5. **类型注解不完整**: 部分函数缺少类型注解
6. **[Critical]** pending_followup 不持久化 - 跨 session 丢失
7. **[Critical]** resolved follow-up 不清退 - 持续占据注意力
8. **[Critical]** A7 代码域 follow-up 未接入主链路
9. **[Medium]** 测试断言过弱

---

## 二、必须修复的问题 (Bug)

### BUG-001: Pending Follow-Up 状态不一致

**位置**: `runtime.py:1254-1263`

**问题描述**:
当 assistant 创建一个 pending follow-up 但在同一个 transcript 中没有 user 回复时，pending_followup 状态被创建但可能没有被正确传递到下一个 `project()` 调用。

**根因分析**:
```python
# Handle unresolved pending follow-up (created but not yet responded)
# If we have a pending action but it wasn't resolved in this turn
if pending_followup_action and not pending_followup:
    pending_followup = PendingFollowUp(...)
```
这段逻辑在每个 `project()` 调用结束时创建 pending_followup，但 `project()` 返回的 `ContextOSProjection` 中的 `snapshot.pending_followup` 会被传入下一个 `project()` 调用的 `existing_snapshot`。

问题在于：如果 `project()` 内部已经处理了 assistant follow-up question 并创建了 pending_followup，但同一个 `project()` 调用中又有 user 回复，那么 pending_followup 应该在同一个调用内被解析，而不是被传递到下一个调用。

**影响范围**:
- 连续对话中的 pending follow-up 解析可能延迟一个 turn

**修复建议**:
```python
# 在 _canonicalize_and_offload 结束时，确保 pending_followup 状态正确
# 如果 pending_followup_action 不为空但没有 user 回复，保持 pending 状态
# 如果 pending_followup_action 为空但 pending_followup 已解析，确保状态正确
```

---

### BUG-002: Dialog Act 模式匹配优先级冲突

**位置**: `runtime.py:102-106, 108-112`

**问题描述**:
`_DIALOG_ACT_DENY_PATTERNS` 中的 `"先别"` 被移除后，现在与 `_DIALOG_ACT_PAUSE_PATTERNS` 有部分重叠。

测试发现：
- `需要我帮你实现登录功能吗？` -> `implement` (期望)
- 但如果用户只回复 `实现`，可能被错误分类

**根因分析**:
模式匹配使用 `search()` 而不是 `fullmatch()`，导致部分匹配。

**影响范围**:
- 某些边界输入可能产生意外的分类结果

**修复建议**:
```python
# 使用更严格的匹配策略
# 考虑使用 fullmatch 或添加边界断言
_DIALOG_ACT_AFFIRM_PATTERNS = (
    re.compile(r"^(需要|要|可以|行|好|好的|继续|开始|确认|是|是的|要的|请继续|请开始|嗯|对)[!！。.]?$"),
    ...
)
```

---

### BUG-003: `focus_regression_rate` 计算逻辑错误

**位置**: `evaluation.py:602-609`

**问题描述**:
`focus_regression_rate` 指标始终返回 0.0，未正确实现。

**根因分析**:
```python
# 3. focus_regression_rate (lower is better)
if projection and projection.run_card:
    if projection.run_card.latest_user_intent and projection.run_card.current_goal:
        if projection.run_card.latest_user_intent != projection.run_card.current_goal:
            focus_regression_rate = 0.0  # 应该是检查是否发生了 regression
    else:
        focus_regression_rate = 0.0
```
逻辑上，当 `latest_user_intent != current_goal` 时应该检查是否存在 regression，而不是直接返回 0.0。

**影响范围**:
- 评估指标无法正确反映 focus regression 情况

**修复建议**:
```python
def _calculate_focus_regression_rate(projection: ContextOSProjection) -> float:
    """Calculate how often the focus regressed to an older goal.

    Returns:
        0.0 = no regression, 1.0 = full regression
    """
    if not projection or not projection.run_card:
        return 0.0

    run_card = projection.run_card
    latest_intent = run_card.latest_user_intent

    # If latest user intent exists but was ignored, that's regression
    if latest_intent and run_card.current_goal:
        # Check if current_goal is from an older turn than latest_intent
        # This is a simplified check - actual implementation needs turn timing
        return 0.5  # Conservative estimate

    return 0.0  # No regression
```

---

## 二.5、用户复现发现的关键问题 (2026-03-27 晚)

### CRITICAL-001: pending_followup 不持久化

**位置**: `session_continuity.py:633-662`, `models.py:715`

**问题描述**:
`pending_followup` 没有进入持久化快照链，跨 reload / 跨 session 会丢失。`ContextOSSnapshot` 明明把它定义成一等状态并参与反序列化，但 `continuity` 持久化时根本没写出去。

**复现步骤**:
1. 创建 pending follow-up: `snapshot.pending_followup = {'action': '帮你实现', 'status': 'pending'}`
2. 调用 `snapshot.to_dict()`
3. 检查输出：没有 `pending_followup` 键

**修复方案**:
在 `_build_context_os_persisted_payload` 中添加 `pending_followup` 字段：
```python
# === Attention Runtime: Persist pending_followup state ===
"pending_followup": snapshot.pending_followup.to_dict() if snapshot.pending_followup else None,
```

**验证**: ✅ 已修复，新增 `TestPendingFollowUpPersistence` 测试用例验证

---

### CRITICAL-002: resolved follow-up 不清退

**位置**: `runtime.py:1116-1128`

**问题描述**:
已经 confirmed/denied/paused 的 follow-up 不会被清退，旧动作会继续占据注意力。运行时每轮都会先把历史 `pending_followup.action` 重新装回本地变量，不看它是不是已解决。

**复现步骤**:
1. 用户先确认一次"帮你实现"
2. 下一轮只是说"好的，继续"
3. 结果：`pending_followup.status` 仍然是 `confirmed`，`next_action_hint` 依然指向旧动作

**修复方案**:
只继承未解决的 pending follow-up：
```python
# Only inherit unresolved pending follow-up from existing snapshot
if current_pending_followup and current_pending_followup.action:
    if current_pending_followup.status == "pending":  # 只跟踪未解决的
        pending_followup = current_pending_followup
        ...
    # else: Resolved follow-ups are NOT tracked
```

**验证**: ✅ 已修复，新增 `TestResolvedFollowUpCleanup` 测试用例验证

---

### CRITICAL-003: A7 代码域 follow-up 未接入主链路

**位置**: `runtime.py:1142-1147`

**问题描述**:
A7 的代码域 follow-up 增强并没有真正接到主链上。`CodeContextDomainAdapter.classify_assistant_followup()` 是孤立方法，没有被调用。

**修复方案**:
在 `_canonicalize_and_offload` 中调用 domain adapter 的 follow-up 分类器：
```python
# === A7: Code-domain follow-up enhancement ===
if not inferred_action and hasattr(self.domain_adapter, "classify_assistant_followup"):
    domain_decision = self.domain_adapter.classify_assistant_followup(...)
```

**验证**: ✅ 已修复，测试用例验证 A7 增强生效

---

### CRITICAL-004: 测试断言过弱

**位置**: `test_attention_runtime.py:356, 406`

**问题描述**:
测试断言过于宽松，无法捕获结构性漏洞。例如：
```python
# 这是一个恒真断言
assert trace.pending_followup is not None or trace.pending_followup is None  # 永远是 True
```

**修复方案**:
加强断言：
```python
# 验证 trace 字段被正确填充
assert isinstance(trace.attention_roots, tuple)
assert "latest_user_turn" in trace.attention_roots
```

**验证**: ✅ 已修复

---

## 三、建议改进

### IMPROVE-001: 代码重复 - `_extract_assistant_followup_action`

**位置**: `runtime.py` 多处

**问题描述**:
`_extract_assistant_followup_action` 函数在 `_canonicalize_and_offload` 中被调用两次（一次在循环开始前检查是否为空，一次在循环内）。

**修复建议**:
```python
# 将检查逻辑合并到循环内
for item in transcript:
    if item.role == "assistant":
        # 只在 pending_followup_action 为空时提取新 action
        if not pending_followup_action:
            pending_followup_action = _extract_assistant_followup_action(item.content)
            if pending_followup_action:
                pending_followup_event_id = item.event_id
                pending_followup_sequence = item.sequence
```

---

### IMPROVE-002: DialogActClassifier 重复模式编译

**位置**: `runtime.py:82-143`

**问题描述**:
正则表达式模式在模块加载时被编译，但这对于 DialogActClassifier 是不必要的，因为这些模式是只读的。

**修复建议**:
将模式编译移到模块级别（已是如此），确保 DialogActClassifier 不重复编译。

---

### IMPROVE-003: 缺少边界条件测试

**位置**: `test_attention_runtime.py`

**问题描述**:
以下边界场景未覆盖：
1. 空消息
2. 纯标点符号消息
3. 混合语言消息（中文+英文）
4. 超长消息
5. 重复消息

**修复建议**:
添加以下测试用例：
```python
def test_empty_message(self, classifier):
    result = classifier.classify("", role="user")
    assert result.act == DialogAct.UNKNOWN

def test_punctuation_only(self, classifier):
    result = classifier.classify("...", role="user")
    # 应该被分类为 noise 或 unknown

def test_mixed_language(self, classifier):
    result = classifier.classify("please fix this bug", role="user")
    # 应该正确识别为 redirect 或其他
```

---

### IMPROVE-004: 类型注解不完整

**位置**: `domain_adapters/code.py`

**问题描述**:
部分函数缺少完整的类型注解。

**修复建议**:
为所有公共函数添加类型注解：
```python
def _extract_code_followup_intent(text: str) -> str | None: ...
def _get_code_workflow_hint(intent: str | None) -> str: ...
def _calculate_code_artifact_weight(content: str) -> float: ...
```

---

### IMPROVE-005: `extract_attention_trace` 使用 `Any` 类型

**位置**: `evaluation.py:463`

**问题描述**:
```python
def extract_attention_trace(
    snapshot: ContextOSSnapshot | dict[str, Any] | None,
    projection: Any = None,  # 应该使用更具体的类型
) -> AttentionObservabilityTrace:
```

**修复建议**:
```python
from .models import ContextOSProjection

def extract_attention_trace(
    snapshot: ContextOSSnapshot | dict[str, Any] | None,
    projection: ContextOSProjection | None = None,
) -> AttentionObservabilityTrace:
```

---

## 四、可选优化

### OPT-001: 性能优化 - 缓存正则表达式编译结果

**位置**: `domain_adapters/code.py`

**问题描述**:
每次调用 `_extract_code_followup_intent` 时都会遍历多个正则表达式模式。

**优化建议**:
使用 `re.compile` 预编译模式，并在类级别缓存。

---

### OPT-002: 扩展性 - 支持更多语言

**位置**: `runtime.py`

**问题描述**:
当前 DialogActClassifier 主要支持中文和英文。

**优化建议**:
考虑添加配置选项来支持更多语言模式。

---

### OPT-003: 配置化 - Feature Switch 默认值

**位置**: `models.py`

**问题描述**:
Feature switch 默认值可能需要根据不同环境调整。

**优化建议**:
从环境变量或配置文件读取默认值。

---

### OPT-004: 文档完善

**问题描述**:
部分模块缺少详细的 docstring。

**优化建议**:
为所有公共类和函数添加 Google 风格的 docstring。

---

## 五、重构任务分配

| Task ID | 任务名称 | 负责人 | 优先级 | 状态 | 估计工时 |
|---------|---------|--------|--------|------|---------|
| T-001 | BUG-001: Pending Follow-Up 状态修复 | 王建国 | P0 | ✅ 已完成 | 2h |
| T-002 | BUG-002: Dialog Act 模式优先级修复 | 李薇 | P0 | ✅ 已完成 | 1h |
| T-003 | BUG-003: focus_regression_rate 计算修复 | 吴婷 | P0 | ✅ 已完成 | 1h |
| T-004 | IMPROVE-001: 消除代码重复 | 陈思远 | P1 | ✅ 已完成 | 2h |
| T-005 | IMPROVE-003: 边界条件测试 | 孙丽 | P1 | ✅ 已完成 | 3h |
| T-006 | IMPROVE-004: 类型注解完善 | 郑浩 | P2 | ✅ 已完成 | 2h |
| T-007 | IMPROVE-005: 类型注解修复 | 赵文博 | P2 | ✅ 已完成 | 1h |
| T-008 | OPT-001: 性能优化 (正则已模块级缓存) | 刘强 | P3 | ✅ 已完成 | 0h |
| T-009 | OPT-003: 配置化 (环境变量支持) | 周雪 | P3 | ✅ 已完成 | 2h |
| T-010 | 文档完善 + 代码审查 | 张明远 | P2 | ✅ 已完成 | 2h |
| T-011 | CRITICAL-001: pending_followup 持久化 | 审计修复 | P0 | ✅ 已完成 | 1h |
| T-012 | CRITICAL-002: resolved follow-up 清退 | 审计修复 | P0 | ✅ 已完成 | 1h |
| T-013 | CRITICAL-003: A7 代码域集成 | 审计修复 | P0 | ✅ 已完成 | 1h |
| T-014 | CRITICAL-004: 测试断言加强 | 审计修复 | P1 | ✅ 已完成 | 1h |

---

## 六、验证标准

### 6.1 修复验证
- [x] 所有 P0 Bug 修复后，原有测试仍然通过
- [x] 新增边界测试用例全部通过
- [x] `pytest polaris/kernelone/context/tests/ -q` 无新增失败 (488 passed, 9 skipped)
- [x] 新增 Critical Issue 测试用例验证通过

### 6.2 代码质量
- [x] PEP 8 合规
- [x] 类型注解覆盖率 >= 95%
- [x] 无 `Any` 类型在公开接口 (除 evaluation.py 内部使用)
- [x] 所有公共函数有 docstring

### 6.3 测试覆盖
- [x] 新增边界测试用例 >= 10 个 (实际: 39 个边界测试)
- [x] 测试覆盖：正常、边界、异常场景
- [x] 新增 Critical Issue 专项测试 (5 个新测试用例)

---

## 七、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 修复 BUG-001 引入回归 | 中 | 高 | 充分单元测试 + 集成测试 |
| 模式修改影响现有分类 | 低 | 高 | 先测试后部署 |
| 类型注解修改破坏兼容性 | 低 | 中 | 保持公开接口不变 |

---

## 八、后续优化建议

1. **短期 (1-2 周)**:
   - 完成所有 P0/P1 修复
   - 完善测试覆盖

2. **中期 (1 个月)**:
   - 类型注解全面完善
   - 性能优化
   - 文档完善

3. **长期 (3 个月)**:
   - 支持更多语言
   - 配置化管理
   - 可观测性增强
