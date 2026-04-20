# Verification Card: LLM工具调用收敛 Phase 1 - ToolSpecRegistry

**验证卡片**: VC-20260328-001
**Phase**: Phase 1
**负责人**: 工程师甲 (Platform-Infra)
**技术总监**: Dains
**创建时间**: 2026-03-28
**目标完成日期**: 2026-04-14

---

## 验证目标

建立ToolSpecRegistry作为LLM工具调用的单一权威源头

---

## 验证条件

### 条件1: ToolSpecRegistry正确实现

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| ToolSpec dataclass | 代码审查 | frozen dataclass包含所有字段 | ✅ |
| ToolSpecRegistry singleton | 单元测试 | 单例模式正确实现 | ✅ |
| register()方法 | 单元测试 | 正确处理canonical_name和aliases | ✅ |
| get()方法 | 单元测试 | 别名查找正确 | ✅ |
| generate_llm_schemas() | 单元测试 | 输出正确的OpenAI格式 | ✅ |
| generate_handler_registry() | 单元测试 | 输出正确的handler映射 | ✅ |

### 条件2: contracts.py迁移完成

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| contracts.py委托Registry | 代码审查 + 测试 | 归一化使用Registry | ✅ |
| _TOOL_SPECS迁移完成 | 单元测试 | 29个工具正确迁移 | ✅ |
| 别名解析向后兼容 | 单元测试 | rg->ripgrep, grep->search_code | ✅ |
| validate_tool_step()正常 | 单元测试 | 验证逻辑正确 | ✅ |

### 条件3: 遗留项 (Phase 2+)

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| definitions.py使用Registry | 代码审查 + 测试 | STANDARD_TOOLS从Registry生成 | ⏳ |
| registry.py使用Registry | 代码审查 + 测试 | handler映射从Registry生成 | ⏳ |

### 条件4: CI门禁

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| 单元测试存在 | 文件检查 | test_tool_spec_registry.py存在 | ✅ |
| 单元测试通过 | pytest | 25/25通过 | ✅ |
| 迁移测试通过 | pytest | 6/6通过 | ✅ |
| contracts测试通过 | pytest | 21/21通过 | ✅ |

### 条件5: 回归测试

| 检查项 | 验证方法 | 预期结果 | 状态 |
|--------|---------|---------|------|
| tools测试套件 | pytest | 140/140通过 | ✅ |
| 定义一致性问题 | 需修复 | 13个STANDARD_TOOLS未在Registry | ⚠️ |

---

## 验证执行记录

### 2026-03-28 验证

```
执行者: Dains (技术总监)
验证结果: □ 通过  ■ 有条件通过  □ 未通过
```

| 条件 | 结果 | 备注 |
|------|------|------|
| ToolSpecRegistry正确实现 | ✅ | 全部通过 |
| contracts.py迁移完成 | ✅ | 29个工具迁移成功 |
| definitions.py使用Registry | ⚠️ | 13个工具未迁移 |
| CI门禁 | ⚠️ | 单元测试通过，一致性检查待修复 |
| 回归测试 | ✅ | tools测试140/140通过 |

**验证签字**: Dains

---

## 偏差记录

| 日期 | 偏差描述 | 影响评估 | 处理方式 |
|------|---------|---------|---------|
| 2026-03-28 | definitions.py中13个工具(STANDARD_TOOLS)不在Registry中 | 中 | Phase 2修复：让definitions.py使用Registry生成 |
| 2026-03-28 | ripgrep和search_code作为canonical被别名占用 | 低 | 保持向后兼容，LLM仍可通过别名调用 |

---

## Phase 1 总结

### 已完成
1. `ToolSpecRegistry` 实现完成 (frozen dataclass + singleton registry)
2. `contracts.py` 的 29 个工具成功迁移到 Registry
3. 别名解析遵循旧的 two-pass 逻辑，保持向后兼容
4. 25 个单元测试 + 21 个 contracts 测试全部通过
5. LLM schema 生成正常工作 (OpenAI + Anthropic)

### 待完成 (Phase 2)
1. `definitions.py` 的 `STANDARD_TOOLS` 需要迁移到 Registry
2. 需要统一 `definitions.py` 和 `contracts.py` 的工具定义
3. 需要更新 `executor/core.py` 和 `llm_caller.py` 使用 Registry

---

*卡片状态*: 有条件通过 - Phase 2 修复后可最终验收
*最后更新*: 2026-03-28
