# ContextOS 上下文追踪失效深度审计报告

**日期**: 2026-03-31
**审计人**: Claude Code
**问题等级**: P0 - 核心功能失效

---

## 执行摘要

ContextOS 上下文追踪功能在所有 7 个 runtime 项目中**完全失效**：
- `context_tokens_before`: **null** (所有项目)
- `context_tokens_after`: **null** (所有项目)
- `compression_strategy`: **null** (所有项目)

**根本原因**: LLM Caller 未将 `ContextResult.token_estimate` 和压缩策略传递到事件发射器。

---

## 问题清单

### 问题 1: context_tokens_before/after 未传递 [P0]

**严重性**: 阻塞
**影响**: 所有 ContextOS 上下文统计失效

| 位置 | 问题 |
|------|------|
| `llm_caller.py:814` | `context_result.token_estimate` 未被捕获 |
| `llm_caller.py:1339` | `_emit_call_start_event()` 缺少 `context_tokens_before` 参数 |
| `llm_caller.py:1009` | `_emit_call_end_event()` 缺少 `context_tokens_after` 参数 |
| `events.py:71-72` | 字段已定义但调用处未传递 |

**修复方案**:
1. 扩展 `_emit_call_start_event()` 方法签名，添加 `context_tokens_before: int | None = None`
2. 扩展 `_emit_call_end_event()` 方法签名，添加 `context_tokens_after: int | None = None`
3. 在调用处传递 `context_result.token_estimate`

---

### 问题 2: compression_strategy 未传递 [P0]

**严重性**: 阻塞
**影响**: 压缩策略无法追踪

| 位置 | 问题 |
|------|------|
| `llm_caller.py:1354` | metadata 中未包含 `compression_strategy` |
| `context_gateway.py:338` | 已有 `compression_strategy` 信息但未传递 |
| `llm_caller.py:322` | 压缩发生在 context_gateway，未传递到 caller |

**修复方案**:
1. `build_context()` 返回值需要包含 `compression_applied` 和 `compression_strategy`
2. 在 `_prepare_llm_request()` 后获取压缩信息
3. 通过 metadata 传递到事件发射器

---

### 问题 3: compress() 只压缩视图层 [P1]

**严重性**: 高
**影响**: transcript_log 无限增长

| 位置 | 问题 |
|------|------|
| `models.py:842-847` | `compress()` 文档明确说明"不修改底层 transcript_log" |
| `models.py:862-879` | 只压缩 `active_window`，不压缩 `transcript_log` |

**修复方案**:
1. 添加 `ContextOSProjection.trim_transcript_log()` 方法
2. 当 `active_window` 被截断时，同步清理 `transcript_log` 头部
3. 保留 `is_root=True` 的事件不被清理

---

### 问题 4: micro_compact() 格式不匹配 [P1]

**严重性**: 高
**影响**: 工具结果无法被压缩

| 位置 | 问题 |
|------|------|
| `compaction.py:400-403` | `micro_compact()` 期望 `role="user"` + `content=[{type:"tool_result"}]` |
| ContextOS 实际 | `role="tool"` + `content="..."` 格式 |

**修复方案**:
1. 在 `RoleContextCompressor` 添加 ContextOS 专用的 `_compact_tool_results()` 方法
2. 识别 `role="tool"` 事件格式
3. 将旧工具结果替换为 `[Previous tool result: {tool_name}]` 占位符

---

### 问题 5: 工具调用循环 [P2]

**严重性**: 中
**影响**: 模型陷入编辑循环

| 位置 | 问题 |
|------|------|
| 日志分析 | 39 次 LLM 调用，大量 `edit_file` 失败 |
| 编码问题 | 响应内容出现乱码 |

**修复方案**:
1. 添加工具调用频率监控
2. 当同一工具调用超过阈值时触发警告
3. 检查 `precision_edit` 和 `edit_file` 参数别名冲突

---

## 修复清单

| # | 问题 | 文件 | 优先级 | 状态 |
|---|------|------|--------|------|
| 1 | context_tokens_before/after 传递 | `llm_caller.py` | P0 | 待修复 |
| 2 | compression_strategy 传递 | `llm_caller.py` | P0 | 待修复 |
| 3 | compress() 截断 transcript_log | `models.py` | P1 | 待修复 |
| 4 | micro_compact() 格式适配 | `compaction.py` | P1 | 待修复 |
| 5 | 工具调用循环检测 | `tool_loop_controller.py` | P2 | 待修复 |

---

## 验证标准

修复后，日志应显示：
```json
{
  "context_tokens_before": 8500,
  "context_tokens_after": 7200,
  "compression_strategy": "L1_semantic_L2_truncate"
}
```

---

## 影响项目

| 项目 | context_tokens_before | 问题状态 |
|------|---------------------|----------|
| director-test-driven-fix | null | 需修复 |
| director-security-patch | null | 需修复 |
| director-safe-scope-plan | null | 需修复 |
| director-root-cause-locator | null | 需修复 |
| director-code-refactor | null | 需修复 |
| director-feature-branch | null | 需修复 |
| director-root-cause-locator-3c06a87 | null | 需修复 |

---

**报告生成时间**: 2026-03-31T
