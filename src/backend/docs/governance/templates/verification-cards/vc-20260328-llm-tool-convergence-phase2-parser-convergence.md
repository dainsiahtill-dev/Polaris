# Verification Card: LLM工具调用收敛 Phase 2 - Parser收敛

**验证卡片**: VC-20260328-002
**Phase**: Phase 2
**负责人**: 工程师乙 (Parser-Master)
**技术总监**: Dains
**创建时间**: 2026-03-28
**目标完成日期**: 2026-04-28

---

## 验证目标

Parser从5层收敛为2层（CanonicalToolCallParser + FormatAdapters）

---

## 验证条件

### 条件1: CanonicalToolCallParser正确实现

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| CanonicalToolCall dataclass | 代码审查 | 包含tool_name/arguments/raw_format/raw_data | ⏳ |
| parse()入口 | 单元测试 | format_hint优先逻辑正确 | ⏳ |
| _parse_with_hint() | 单元测试 | 支持OpenAI/Anthropic/Gemini/Ollama/JSONText | ⏳ |
| _auto_parse() fallback | 单元测试 | auto模式正确处理 | ⏳ |

### 条件2: Parser删除完成

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| prompt_based.py已删除 | 文件检查 | 文件不存在 | ⏳ |
| tool_chain.py已删除 | 文件检查 | 文件不存在 | ⏳ |
| domain/services/parsing.py已删除 | 文件检查 | 文件不存在 | ⏳ |
| 无残留引用 | grep搜索 | 0个引用 | ⏳ |

### 条件3: Argument Key统一

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| CANONICAL_ARGUMENT_KEYS定义 | 代码审查 | 包含所有标准key | ⏳ |
| 所有Adapter使用统一key | 代码审查 + 测试 | 行为一致 | ⏳ |

### 条件4: 测试覆盖

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| Parser单元测试覆盖率 | pytest --cov | > 80% | ⏳ |
| CanonicalToolCallParser测试 | pytest | 100%通过 | ⏳ |
| 格式Adapter测试 | pytest | 100%通过 | ⏳ |

---

## 验证执行记录

### 2026-04-28 验证

```
执行者: Dains (技术总监)
验证结果: □ 通过  □ 未通过  □ 有条件通过
```

| 条件 | 结果 | 备注 |
|------|------|------|
| CanonicalToolCallParser正确实现 | ☐ | |
| Parser删除完成 | ☐ | |
| Argument Key统一 | ☐ | |
| 测试覆盖 | ☐ | |

**验证签字**: _________________

---

*卡片状态*: 待验证
*最后更新*: 2026-03-28
