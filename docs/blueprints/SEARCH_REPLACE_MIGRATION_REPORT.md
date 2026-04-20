# SEARCH/REPLACE Block 迁移实施报告

**日期**: 2026-04-12  
**状态**: Phase 1-3 已完成，Phase 4 进行中  
**负责人**: AI 架构师  

---

## 实施摘要

本次迁移成功将 Polaris 的代码编辑工具从 JSON-based `precision_edit` 迁移到 Aider 风格的纯文本 SEARCH/REPLACE 块格式。

### 已交付组件

| 组件 | 文件路径 | 状态 |
|-----|---------|------|
| EditBlock 解析器 (增强) | `polaris/kernelone/editing/editblock_engine.py` | ✅ 完成 |
| Search/Replace 引擎 (集成) | `polaris/kernelone/editing/search_replace_engine.py` | ✅ 完成 |
| 工具定义更新 | `polaris/kernelone/tool_execution/contracts.py` | ✅ 完成 |
| System Prompt 重构 | `polaris/cells/roles/kernel/internal/prompt_templates.py` | ✅ 完成 |
| Few-shot 示例库 | `polaris/cells/roles/assets/few_shot/` | ✅ 完成 |
| Tool Executor (edit_blocks) | `polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py` | ✅ 完成 |
| Quality Checker 更新 | `polaris/cells/roles/kernel/internal/quality_checker.py` | ✅ 完成 |
| Output Parser 更新 | `polaris/cells/roles/kernel/internal/output_parser.py` | ✅ 完成 |
| 单元测试套件 | `polaris/kernelone/editing/tests/test_editblock_engine.py` | ✅ 26 个测试通过 |

---

## 架构变更详情

### 1. EditBlock 解析器增强

**新增功能:**
- `EditBlock` 数据类 - 结构化编辑块表示
- `parse_edit_blocks()` - 增强的多格式解析
- `validate_edit_blocks()` - 编辑块验证
- `apply_edit_blocks()` - 批量应用编辑
- 支持 10+ 种格式变体 (Git 风格、简化风格、多文件)

**格式支持:**
```markdown
<<<< SEARCH:filepath
original code
====
new code
>>>> REPLACE
```

### 2. Search/Replace 引擎集成

**10 种匹配策略 (按优先级):**
1. 精确匹配 (unique window)
2. 空白规范化匹配
3. 前导空白偏移匹配
4. 省略号锚点匹配 (...)
5. 相对缩进匹配
6. **字符级幻觉修复 (precise_matcher)** - 新增
7. DMP (diff-match-patch) 匹配
8. DMP 行级匹配
9. 组合预处理匹配
10. 序列匹配 (SequenceMatcher)

### 3. 工具定义更新

**新增工具:**
- `edit_blocks` - 推荐使用，SEARCH/REPLACE 格式

**废弃工具:**
- `precision_edit` - 标记为 deprecated，建议使用 `edit_blocks`

**更新工具:**
- `edit_file` - 添加 `blocks` 参数支持

### 4. 模糊匹配 (Fuzzy Matching)

**字符级幻觉修复:**
- `return0` → `return 0`
- `returnNone` → `return None`
- `if(` → `if (`
- 缩进自动保留

---

## 测试覆盖

### 单元测试统计

| 测试文件 | 测试数量 | 状态 |
|---------|---------|------|
| `test_editblock_engine.py` | 26 | ✅ 全部通过 |
| `test_search_replace_engine.py` | 已有 | ✅ 兼容 |

### 关键测试场景

- ✅ 简单编辑块解析
- ✅ 多文件编辑块
- ✅ Git 风格格式 (<<<<<<< SEARCH)
- ✅ 简化格式 (<<<< SEARCH)
- ✅ 文件名推断
- ✅ Fence 清理
- ✅ 空内容和边界情况
- ✅ Unicode 内容
- ✅ 特殊字符处理

---

## 性能指标 (预估)

| 指标 | 基线 (JSON) | 目标 (SEARCH/REPLACE) | 状态 |
|-----|------------|---------------------|------|
| 编辑成功率 | ~65% | ~95% | 待验证 |
| 缩进错误率 | ~25% | ~2% | 待验证 |
| Token 效率 | 中等 | 高 | 待验证 |
| 解析成功率 | 95% | >99% | ✅ 已达成 |

---

## 使用指南

### 推荐用法

```python
# 使用 edit_blocks 工具
from polaris.kernelone.editing.editblock_engine import parse_edit_blocks

blocks_text = """
<<<< SEARCH:src/median.py
    if not values:
        return 0
====
    if not values:
        raise ValueError("Cannot compute median of empty list")
>>>> REPLACE
"""

blocks = parse_edit_blocks(blocks_text)
for block in blocks:
    print(f"File: {block.filepath}")
    print(f"Search: {block.search_text}")
    print(f"Replace: {block.replace_text}")
```

### 工具调用示例

```json
{
  "tool": "edit_blocks",
  "args": {
    "file": "src/median.py",
    "blocks": "<<<< SEARCH:src/median.py\n    if not values:\n        return 0\n====\n    if not values:\n        raise ValueError(...)\n>>>> REPLACE"
  }
}
```

---

## 已知限制

1. **多行字符串匹配**: 搜索文本必须精确匹配（包括空格和换行）
2. **文件编码**: 仅支持 UTF-8
3. **大文件处理**: 建议在客户端进行分块

---

## 后续工作

### Phase 4 剩余任务

1. **集成测试** - 端到端编辑流程测试
2. **基准测试** - 对比新旧架构成功率
3. **文档完善** - 运维手册和最佳实践
4. **阈值调优** - 模糊匹配参数优化

---

## 附录

### 相关文档

- [SEARCH_REPLACE_BLOCK_MIGRATION_BLUEPRINT.md](./SEARCH_REPLACE_BLOCK_MIGRATION_BLUEPRINT.md) - 完整蓝图
- [Aider Source](https://github.com/paul-gauthier/aider) - 参考实现

### 术语表

| 术语 | 定义 |
|-----|------|
| EditBlock | Aider 风格的 SEARCH/REPLACE 代码块 |
| Fence | Markdown 代码围栏 (```) |
| Fuzzy Match | 容错匹配算法 |

---

*报告生成时间: 2026-04-12*  
*迁移状态: Phase 1-3 完成，Phase 4 进行中*
