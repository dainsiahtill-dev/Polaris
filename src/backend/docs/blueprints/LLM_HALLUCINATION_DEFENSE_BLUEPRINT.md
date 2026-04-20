# LLM 幻觉防御体系蓝图

**状态**: 已实现
**创建日期**: 2026-04-13
**相关文件**: `polaris/kernelone/tool_execution/code_validator.py`

---

## 1. 设计哲学

### 核心理念转变

| 旧思维 (Strict Validation) | 新思维 (Smart Fallback) |
|---------------------------|-------------------------|
| LLM 必须生成正确代码 | LLM 生成的代码我们会兜底修复 |
| 验证失败 → 拒绝 → LLM 重新生成 | 验证失败 → 自动修复 → 继续执行 |
| 严格要求 LLM perfectionist | 宽松接收，智能修复 |

### 为什么选择 Smart Fallback？

1. **用户体验更流畅**: 任务直接完成，不用等待 LLM 重试
2. **减少 LLM 负担**: LLM 不用 perfectionist，减少 token 消耗
3. **系统更 robust**: 不被 LLM 的小错误卡住
4. **实际可行**: LLM 生成代码时的小错误（return0, if(）是可预测的

---

## 2. 架构设计

### 多层智能修复体系

```
LLM Output
    ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: 第三方工具格式化                                     │
│   - ruff format (Python)                                    │
│   - prettier (JS/TS/JSX/TSX)                               │
│   - gofmt (Go)                                             │
│   - rustfmt (Rust)                                         │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Regex 修复幻觉模式                                  │
│   - return0 → return 0                                     │
│   - if( → if (                                            │
│   - print x → print(x)                                     │
│   - Tab → 4空格                                            │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: AST 语法验证                                        │
│   - Python AST 解析                                         │
│   - 括号匹配检查 (JS/TS/Go/Rust)                           │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: 模糊匹配安全化                                      │
│   - 精确匹配优先                                            │
│   - 字符级幻觉修复                                           │
│   - 位置验证                                                │
└─────────────────────────────────────────────────────────────┘
    ↓
通过 → 写入文件
```

### 降级策略

当第三方工具不可用时，系统逐层降级：

```
第三方工具 (ruff/prettier/gofmt/rustfmt)
    ↓ (不可用)
Regex 修复模式 (return0 → return 0)
    ↓
AST/括号验证
    ↓
写入文件 (保守策略)
```

---

## 3. 已实现的修复模式

### Python 幻觉修复

| 错误模式 | 修复后 | 说明 |
|---------|--------|------|
| `return0` | `return 0` | 关键字和值之间缺少空格 |
| `returnNone` | `return None` | 关键字和值之间缺少空格 |
| `if(` | `if (` | 关键字后缺少空格 |
| `for(` | `for (` | 关键字后缺少空格 |
| `while(` | `while (` | 关键字后缺少空格 |
| `def(` | `def (` | 关键字后缺少空格 |
| `print "hello"` | `print("hello")` | print 缺少括号 |

### 多语言支持

| 语言 | 格式化工具 | 验证方式 |
|------|------------|---------|
| Python | ruff format | AST + Regex |
| JavaScript/TypeScript | prettier | 括号匹配 |
| Go | gofmt | 括号匹配 |
| Rust | rustfmt | 括号匹配 |

---

## 4. 集成点

### write_file 工具

```python
# filesystem.py 中的 _handle_write_file
validation_result = validate_code_syntax(text, rel)
if not validation_result.is_valid:
    return {"ok": False, "error": ...}
if validation_result.fixed_code is not None:
    text = validation_result.fixed_code  # 使用修复后的代码
```

### edit_blocks 工具

```python
# filesystem.py 中的 _handle_edit_blocks
replace_validation = validate_code_syntax(block.replace_text, block_rel)
if not replace_validation.is_valid:
    return error
if replace_validation.fixed_code is not None:
    block.replace_text = replace_validation.fixed_code
```

---

## 5. 关键数据结构

### SyntaxValidationResult

```python
@dataclass
class SyntaxValidationResult:
    is_valid: bool
    errors: list[CodeSyntaxError] | None = None
    suggestions: list[str] | None = None
    fixed_code: str | None = None  # 自动修复后的代码
```

### HallucinationFix

```python
@dataclass
class HallucinationFix:
    original: str
    fixed: str
    explanation: str
    line: int
    confidence: float = 0.95
```

---

## 6. 未来改进

### Phase 6: 增强 JS/TS 幻觉检测 ✅

```python
# 已实现：JS/TS 特有的幻觉模式检测
JS_FIX_PATTERNS = [
    (r'(\w+)\s*\n\s*}', r'\1;\n}', "Missing semicolon"),
    (r'\bfunction\s*\(', r'function (', "Missing space after 'function'"),
    (r'=>\s*{', r' => {', "Arrow function block syntax"),
]
```

### Phase 7: 智能缩进修复 ✅

```python
# 已实现：上下文感知缩进修复
def _fix_indentation(code: str) -> tuple[str, list[HallucinationFix]]:
    # Tab → 4 空格
    # 2 空格 → 4 空格（向上取整）
    # 混合缩进 → 统一为4空格
```

### Phase 8: 后验检查 ✅

```python
# 已实现：写入后验证
@dataclass
class PostWriteVerification:
    success: bool
    expected: str
    actual: str | None = None
    error: str | None = None

def verify_written_code(filepath, expected_content) -> PostWriteVerification:
    ...
```

---

## 7. 测试验证

```bash
# 运行测试
pytest polaris/kernelone/tool_execution/tests/test_code_validator.py -v

# 手动验证
python -c "
from polaris.kernelone.tool_execution.code_validator import validate_code_syntax

# return0 自动修复
result = validate_code_syntax('def test():\n    return0\n', 'test.py')
print(f'is_valid: {result.is_valid}')
print(f'fixed: {repr(result.fixed_code)}')
"
```

---

## 8. 相关文档

- `polaris/kernelone/tool_execution/code_validator.py` - 核心实现
- `polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py` - 集成点
- `polaris/kernelone/tool_execution/tests/test_code_validator.py` - 测试用例
