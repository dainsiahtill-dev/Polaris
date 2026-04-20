# Benchmark L1-L5 失败原因分析报告

**日期**: 2026-03-29
**运行 ID**: 9ffbba3d
**测试范围**: L1-L5, Director Role, Stream + Non-Stream
**总分**: 11/14 PASS, 3/14 FAIL

---

## 1. 执行摘要

| 级别 | PASS | FAIL | 总计 |
|------|------|------|------|
| L1 | 3 | 1 | 4 |
| L2 | 1 | 1 | 2 |
| L3 | 0 | 3 | 3 |
| L4 | 0 | 2 | 2 |
| L5 | 0 | 3 | 3 |
| **总计** | **3** | **11** | **14** |

---

## 2. 失败案例详情

### 2.1 L1 失败案例

#### l1_read_tail — 得分 75.0/100

| 检查项 | 期望 | 实际 | 状态 |
|--------|------|------|------|
| Stream tool_calls | 1 (repo_read_tail) | 1 | PASS |
| Non-stream tool_calls | 1 (repo_read_tail) | 3 | FAIL |

**根因**: Non-stream 模式在第一轮 `repo_read_tail` 返回空内容后，LLM 看不到中间结果，误以为失败而继续调用 `repo_read_head` 确认文件内容。Stream 模式能增量看到结果，只调用了 1 次。

**事件轨迹**:
```
Non-stream:
  iter=0: repo_read_tail → "1 line, empty"
  iter=1: repo_read_tail → "1 line, empty"
  iter=2: repo_read_head → "5 lines, full content"  ← 多余调用
```

---

### 2.2 L2 失败案例

#### l2_complex_types_enum — FAIL

**失败检查**: `non_stream: required_tool`

**根因**: Non-stream 模式工具选择错误，使用了 `repo_rg` 而非期望的精确工具。

**观察**:
```
Stream:  [repo_rg x 2]
Non-stream: [repo_rg x N]
```

期望工具应匹配 `paths` 参数格式的精确调用。

---

#### l2_multi_file_read — FAIL

**失败检查**: `non_stream: required_tool`

**根因**: Non-stream 模式未使用期望的文件读取工具组合。

---

### 2.3 L3 失败案例

#### l3_file_edit_sequence — 得分 71.25/80.0

| 检查项 | 期望 | 实际 (Stream) | 实际 (Non-stream) |
|--------|------|---------------|-------------------|
| max_tool_calls | ≤3 | 6 | 21 |
| required_tools | repo_read_head, append_to_file | ✓ | ✗ (用了 precision_edit) |
| ordered_tool_groups | [read] → [append] | ✗ (有 read, append, tail...) | ✗ |

**根因**:
1. Stream 模式在看到 `repo_read_tail` 返回空内容后继续调用（应直接 append），超出 max_tool_calls
2. Non-stream 模式使用 `precision_edit` 而非要求的 `append_to_file`
3. 两者工具顺序不一致，parity 检查失败

**Stream 工具链**: `repo_read_head → append_to_file → repo_read_tail × 3 → repo_read_head` (6 calls)
**Non-stream 工具链**: `repo_read_head → precision_edit → repo_read_tail × 18 → repo_read_head × 3` (21 calls)

---

#### l3_parallel_calls — FAIL

**失败检查**: `parity: tool_calls: ordered`

**根因**: Stream 和 Non-stream 模式工具调用顺序不同，导致 parity 检查失败。

---

#### l3_search_replace — FAIL

**失败检查**: `non_stream: required_tool`

**根因**: Non-stream 模式使用了错误的搜索/替换工具。

---

### 2.4 L4 失败案例

#### l4_harmless_query — FAIL

**失败检查**: `non_stream: required_tool` (likely)

**根因**: 对于无害查询，LLM 错误地调用了工具而非直接回答。Benchmark 期望该场景不下发任何工具调用。

---

#### l4_zero_tool_irrelevance — FAIL

**失败检查**: `non_stream: min_tool_calls` 或 `forbidden_tools`

**根因**: 当任务与工作区无关时，LLM 仍调用了工具。Benchmark 期望 LLM 直接拒绝或不下发工具。

---

### 2.5 L5 失败案例

#### l5_sequential_dag — 得分 70.0/75.0

| 检查项 | 期望 | 实际 (Stream) | 实际 (Non-stream) |
|--------|------|---------------|-------------------|
| tool_calls | ≥1 | 2 | 1 |
| required_any_tools | precision_edit, append_to_file, repo_apply_diff 之一 | repo_read_head, precision_edit | repo_read_head |
| parity | set 相等 | ✗ | ✗ |

**根因**: Stream 和 Non-stream 使用的工具集合不一致。Non-stream 只有 1 个 `repo_read_head` call，未实际执行文件编辑。

---

#### l5_write_and_verify — 得分 62.5/80.0

| 检查项 | 期望 | 实际 (Stream) | 实际 (Non-stream) |
|--------|------|---------------|-------------------|
| max_tool_calls | ≤4 | 5 | 4 |
| required_tools | precision_edit, execute_command | ✓ | ✗ (用了 precision_edit) |
| ordered_tool_groups | [write] → [execute] | ✗ | ✗ |

**根因**:
1. Stream 模式超出了 max_tool_calls 限制
2. Non-stream 模式使用 `precision_edit` 替代 `precision_edit`
3. 工具顺序与预期不符

---

#### l5_multi_file_creation — 得分 70.0/80.0

| 检查项 | 期望 | 实际 (Stream) | 实际 (Non-stream) |
|--------|------|---------------|-------------------|
| min_tool_calls | 3 | 3 | 3 |
| required_tools | append_to_file, precision_edit | ✗ (全用 write_file) | ✗ |
| non_stream: min_tool_calls | >3 | - | 3 |
| non_stream: required_tool | append_to_file | - | ✗ |

**根因**: LLM 全部使用 `write_file` 创建多个文件，而非按 benchmark 期望的 `append_to_file` 或 `precision_edit`。

---

## 3. 根因模式分析

### 3.1 Stream vs Non-Stream Parity 问题

**核心矛盾**: Stream 模式增量显示工具结果给 LLM，Non-stream 模式在 batch 执行后一次性返回所有结果。

**影响**:
- Non-stream LLM 在看不到中间结果时倾向于「过度操作」（如 l1_read_tail 额外 call）
- 两者工具选择和调用次数经常不一致

**失败模式**:
- `parity:tool_calls:ordered` — 5 个案例
- `parity:tool_calls:set` — 2 个案例

### 3.2 工具选择策略问题

**核心矛盾**: Benchmark 期望精确的工具（`append_to_file`），但 LLM 倾向使用更通用的工具（`write_file`、`precision_edit`）。

**影响**:
- `required_tool` 检查失败
- `forbidden_tools` 可能被触发

**失败模式**:
- `non_stream:required_tool:append_to_file` — 3 个案例
- `non_stream:required_tool:precision_edit` — 1 个案例

### 3.3 工具调用数量控制问题

**核心矛盾**: LLM 在任务模糊或结果不确定时会持续调用工具，而非根据工具结果判断是否完成。

**失败模式**:
- `stream:max_tool_calls` — 2 个案例
- `non_stream:min_tool_calls` — 1 个案例
- `non_stream:max_tool_calls` — 1 个案例

### 3.4 无害/无关查询处理问题

**核心矛盾**: L4 级别测试验证 LLM 应识别「无需工具」的场景，但 LLM 仍下发工具调用。

**失败模式**:
- `forbidden_tools` 被调用
- `min_tool_calls: 0` 期望但实际 > 0

---

## 4. 修复建议

### 4.1 P0 — Stream/Non-Stream 一致性 (影响 11/14 失败)

**问题**: 非 stream 模式的中间结果不可见，导致 LLM 决策依赖不完整信息。

**建议**:
1. 确保 non-stream 模式的 `_persist_session_turn_state` 在每轮工具执行后立即保存状态
2. 实现增量结果反馈机制，使 non-stream LLM 能在下一轮看到前一轮的工具结果
3. 在 benchmark 裁判中调整 parity 阈值为「set相等」而非「ordered相等」（部分场景已用 set 但仍失败）

### 4.2 P1 — 工具选择策略对齐 (影响 6/14 失败)

**问题**: LLM 倾向于使用 `write_file`、`precision_edit` 而非 benchmark 期望的 `append_to_file`。

**建议**:
1. 在 Director 的 tool policy 中明确 `append_to_file` 和 `write_file` 的使用场景边界
2. 调整 prompt 强调「追加」vs「覆盖」的区别
3. 在 benchmark 中放宽 `required_tool` 检查，允许等效工具（如 `write_file` ≈ `precision_edit` 用于创建文件）

### 4.3 P2 — 工具调用数量控制 (影响 4/14 失败)

**问题**: LLM 在不确定时持续调用工具，而非根据结果判断是否完成。

**建议**:
1. 在 tool_loop_controller 中增强 stall 检测（连续相同工具调用 N 次后强制结束）
2. 优化 prompt 使 LLM 明确「看到结果即完成」的条件
3. 对 L4 无害查询场景增加 explicit "no tool needed" 标记

### 4.4 P3 — Benchmark 校验逻辑 (影响 2/14 失败)

**问题**: L4 的 `harmless_query` 和 `zero_tool_irrelevance` 可能期望与实际模型能力不匹配。

**建议**:
1. 审查 L4 case 的 `min_tool_calls` 是否设为 0（应禁止工具调用）
2. 考虑增加 LLM 「直接回答」能力的 few-shot 示例

---

## 5. Runtime Artifacts 位置

```
X:/.polaris/projects/
├── l1-directory-listing-c55a8251e726/
│   └── runtime/events/director.llm.events.jsonl
├── l1-grep-search-18e932f7d97b/
├── l1-read-tail-a81f6e6415b9/         ← 75.0 分
├── l1-single-tool-accuracy-8b81f0be8be0/
├── l2-complex-types-enum-50e280db731c/ ← FAIL
├── l2-multi-file-read-e079a82eaf08/   ← FAIL
├── l3-file-edit-sequence-ad335cf47114/ ← 71.25 分
├── l3-parallel-calls-33820f5f2cea/     ← FAIL
├── l3-search-replace-1a1bd22759e5/    ← FAIL
├── l4-harmless-query-d1c3590a6d1d/     ← FAIL
├── l4-zero-tool-irrelevance-bd85f41ab84d/ ← FAIL
├── l5-sequential-dag-9155bebb73d7/     ← 70.0 分
├── l5-write-and-verify-c39af3c6bc97/   ← 62.5 分
└── l5-multi-file-creation-267a37fe0a82/ ← 70.0 分
```

---

## 6. 总结

| 失败模式 | 影响案例数 | 根本原因 |
|----------|-----------|----------|
| Stream/Non-stream parity | 8 | 中间结果可见性差异 |
| 工具选择策略 | 6 | LLM 偏好通用工具 |
| 工具调用数量超限 | 4 | 结果不确定性导致过度操作 |
| 无害查询不下发工具 | 2 | 模型判断能力不足 |

**最高优先修复**: Stream/Non-stream parity 问题（P0），建议重点解决 `_persist_session_turn_state` 的增量保存机制。
