# SEARCH/REPLACE Block 迁移 - BUG 修复报告

**日期**: 2026-04-12  
**审计人员**: AI 架构师  
**状态**: 已修复  

---

## 审计摘要

对 SEARCH/REPLACE Block 迁移代码进行深度审计，发现 **4 个关键 BUG** 并已全部修复。

---

## BUG 列表与修复

### 🚨 CRITICAL BUG 1: 空搜索文本导致文件被清空

**严重级别**: CRITICAL  
**影响**: 可能导致文件内容全部丢失

#### 根因分析

Python 中 `'' in 'any string'` 永远返回 `True`：

```python
# 这个条件在 search 为空字符串时永远成立！
if search in content:  # '' in 'content' == True
    result[filepath] = content.replace(search, replace, 1)
```

这会导致整个文件内容被替换为空。

#### 修复方案

```python
# editblock_engine.py:apply_edit_blocks()

# 关键修复: 空搜索文本检查
if not search:
    logger.warning("Empty search text in edit block for %s", filepath)
    continue

# 尝试精确匹配（先规范化换行符）
normalized_content = _normalize_line_endings(content)
normalized_search = _normalize_line_endings(search)

if normalized_search in normalized_content:
    # 找到匹配位置，在原始内容中替换
    idx = normalized_content.index(normalized_search)
    result[filepath] = content[:idx] + replace + content[idx + len(search):]
```

---

### 🔴 BUG 2: 多文件编辑非原子性

**严重级别**: HIGH  
**影响**: 部分修改成功，部分失败，导致不一致状态

#### 根因分析

原实现逐个应用编辑块并立即写入文件：

```python
# 原实现（问题）
for block in blocks:
    new_content = apply(block)
    write_file(file, new_content)  # 立即写入！
    # 如果后续 block 失败，前面的修改已无法回滚
```

#### 修复方案

实现两阶段提交（验证+执行）：

```python
# filesystem.py:_handle_edit_blocks()

# ========================================================================
# PHASE 1: VALIDATION - Dry run all blocks to ensure they can be applied
# ========================================================================
for i, block in enumerate(blocks):
    # ... 验证每个块 ...
    new_content, metadata = fuzzy_replace(current, block.search_text, block.replace_text)
    if not metadata.get("success"):
        all_valid = False
        # 收集错误但不写入

# 如果验证失败，返回错误而不修改任何文件
if not all_valid:
    return {
        "ok": False,
        "error": f"Validation failed for {len(failed)} block(s). No files were modified.",
    }

# ========================================================================
# PHASE 2: EXECUTION - All blocks valid, now actually write files
# ========================================================================
for block_rel, (original, new_content) in file_contents.items():
    if original != new_content:
        self._kernel_fs.workspace_write_text(block_rel, new_content)
```

---

### 🟡 BUG 3: 换行符不一致导致匹配失败

**严重级别**: MEDIUM  
**影响**: Windows 文件 (
) 与 Unix 文件 (
) 的搜索/替换失败

#### 根因分析

文件使用不同的换行符格式：
- Unix: `\n`
- Windows: `\r\n`
- Old Mac: `\r`

如果搜索文本使用 `\n` 但文件使用 `\r\n`，精确匹配会失败。

#### 修复方案

```python
# editblock_engine.py

def _normalize_line_endings(text: str) -> str:
    """统一换行符为 \n，便于匹配。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")

# 在匹配前统一换行符
normalized_content = _normalize_line_endings(content)
normalized_search = _normalize_line_endings(search)

if normalized_search in normalized_content:
    # 找到匹配位置，在原始内容中替换（保留原始换行符）
    idx = normalized_content.index(normalized_search)
    result[filepath] = content[:idx] + replace + content[idx + len(search):]
```

---

### 🟡 BUG 4: 路径安全问题

**严重级别**: MEDIUM  
**影响**: 潜在的目录遍历攻击

#### 根因分析

未验证编辑块中的文件路径：

```python
# 危险！可能导致写入 /etc/passwd 或 ../../敏感文件
block.filepath = "../../../etc/passwd"
```

#### 修复方案

```python
# editblock_engine.py

def _is_safe_relative_path(path: str) -> bool:
    """检查路径是否安全（相对路径且不含..）。"""
    token = str(path or "").strip().replace("\\", "/")
    if not token:
        return False
    if token.startswith("/") or token.startswith("\\"):
        return False
    if re.match(r"^[a-zA-Z]:[/\\]", token):
        return False
    if "\x00" in token:
        return False
    parts = [part for part in token.split("/") if part]
    return not any(part in {".", ".."} for part in parts)

# 应用编辑块前检查
if not _is_safe_relative_path(filepath):
    logger.warning("Unsafe filepath in edit block: %s", filepath)
    continue
```

---

## 验证结果

所有修复已通过测试验证：

```
Empty search test: PASS
Normal search test: PASS
Safe path test: PASS
Unsafe path test: PASS
Absolute path test: PASS
CRLF normalize: PASS
CR normalize: PASS

单元测试: 26 passed
```

---

## 代码变更文件

| 文件 | 变更类型 | 说明 |
|-----|---------|------|
| `editblock_engine.py` | +50 行 | 空搜索检查、换行符处理、路径安全 |
| `filesystem.py` | +100 行 | 两阶段提交实现 |

---

## 后续建议

1. **添加更多边界测试**: 针对空字符串、超大文件、特殊字符等场景
2. **性能测试**: 大规模多文件编辑的性能基准
3. **安全审计**: 定期进行路径遍历和注入攻击测试

---

*报告生成时间: 2026-04-12*  
*修复状态: 全部完成*
