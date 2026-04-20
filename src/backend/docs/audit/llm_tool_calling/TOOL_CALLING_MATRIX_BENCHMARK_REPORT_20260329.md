# Tool Calling Matrix Benchmark 审计报告

**日期**: 2026-03-29
**状态**: 已识别问题，待修复
**测试套件**: `tool_calling_matrix`
**角色**: `director`
**运行ID**: `de44da70`

---

## 执行摘要

| 指标 | 值 |
|------|-----|
| 总测试用例 | 20 |
| 完成进度 | 7/20 (35%) |
| 通过 | 5 |
| 失败 | 1 |
| 卡住 | 1 |
| 运行时长 | >15 分钟 |

---

## 问题清单

### P0 - 阻塞性问题

#### 1. `repo_apply_diff` 参数验证循环

**严重程度**: 🔴 阻塞

**现象**:
```
[director] 工具执行返回失败结果: repo_apply_diff - Parameter validation failed: Missing required parameter: diff
```
持续出现，导致 Case 7 (`l3_file_edit_sequence`) 陷入无限重试循环。

**根因分析**:
- 工具规格定义 `diff` 为必需参数
- LLM 调用时使用 `patch` 别名或未提供正确参数名
- 工具处理逻辑未处理别名映射

**影响范围**:
- `Case 7`: `l3_file_edit_sequence` (L3)
- 所有使用 `repo_apply_diff` 的测试用例

---

### P1 - 严重问题

#### 2. `repo_rg` Cooldown 过于严格

**严重程度**: 🟠 严重

**现象**:
```
[TurnEngine] PolicyLayer 拦截工具: tool=repo_rg
reason=tool 'repo_rg' is in cooldown (called 8 times, threshold=8)
```

**根因分析**:
- TurnEngine 的 PolicyLayer 对 `repo_rg` 设置了 8 次调用上限
- L1/L2 测试用例需要多次调用搜索工具验证结果
- Budget 配置与 benchmark 测试场景不兼容

**影响范围**:
- `Case 2`: `l1_grep_search` (FAIL, score=55.0)
- 其他需要多次搜索的测试用例

---

### P2 - 一般问题

#### 3. LLM Lifecycle 资源泄漏

**严重程度**: 🟡 警告

**现象**:
```
LLM lifecycle appears unclosed (run_id=llm_director_xxx, age=821.53s, role=director, model=)
```

**根因分析**:
- 多个 LLM 调用未正确关闭
- Executor 或 Provider 资源未释放

**影响范围**:
- 资源泄漏
- 长时间运行时可能导致 OOM

---

## 测试结果详情

| # | Case ID | Level | 状态 | 分数 | 耗时(ms) | 备注 |
|---|---------|-------|------|------|----------|------|
| 1 | `l1_directory_listing` | L1 | ✅ PASS | 100.0 | 6,328 | |
| 2 | `l1_grep_search` | L1 | ❌ FAIL | 55.0 | 14,149 | cooldown拦截 |
| 3 | `l1_read_tail` | L1 | ✅ PASS | 90.0 | 6,359 | |
| 4 | `l1_single_tool_accuracy` | L1 | ✅ PASS | 100.0 | 9,081 | |
| 5 | `l2_complex_types_enum` | L2 | ✅ PASS | 94.17 | 8,413 | |
| 6 | `l2_multi_file_read` | L2 | ✅ PASS | 85.0 | 4,471 | |
| 7 | `l3_file_edit_sequence` | L3 | 🔄 卡住 | - | >300,000 | 无限循环 |

---

## 修复计划

详见: `BLUEPRINT_FIX_TOOL_CALLING_MATRIX_ISSUES_20260329.md`

---

## 附录

### 相关文件

- 工具规格: `polaris/kernelone/tools/contracts.py`
- PolicyLayer: `polaris/cells/roles/kernel/internal/policy_layer.py`
- TurnEngine: `polaris/cells/roles/kernel/internal/turn_engine.py`
- Benchmark: `polaris/cells/llm/evaluation/internal/tool_calling_matrix.py`

### 审计时间

- 完成时间: 2026-03-29
- 审计人: Python 架构与代码治理实验室
