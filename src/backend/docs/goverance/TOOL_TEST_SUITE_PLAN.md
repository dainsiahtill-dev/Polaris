# 工具测试套件计划 (Tool Test Suite Plan)

## 目标
在 benchmark 框架下，建立一个独立于 LLM 的工具测试套件，验证所有 `repo_*` / `search_code` 等工具的标准化、参数处理、返回值正确性。

## 现状问题
1. **Pattern 尾随空格丢失**: `"^def "` 被 strip 成 `"^def"`
2. **repo_symbols_index 实现不完整**: 传递 `file="."` 给期望单个文件的函数
3. **search_code 同样有 strip 问题**
4. **缺少工具层面的回归测试**

## 测试套件设计

### 1. 测试文件位置
```
polaris/kernelone/llm/toolkit/tests/test_tools_normalization.py
polaris/kernelone/llm/toolkit/tests/test_tools_execution.py
```

### 2. 测试覆盖的工具 (按优先级)

**P0 - 核心搜索工具 (Critical)**
- `repo_rg` - pattern 带空格、正则元字符、别名映射
- `search_code` / `ripgrep` / `grep` - 同上
- `repo_tree` / `list_directory` - 路径处理、递归
- `glob` - 文件匹配模式

**P1 - 文件读取工具**
- `repo_read_head` / `repo_read_tail` / `repo_read_slice`
- `repo_read_around`

**P2 - 文件写入工具**
- `write_file` / `edit_file` / `search_replace`

**P3 - 其他工具**
- `repo_symbols_index` - 需要完整实现
- `repo_map` - 代码结构映射
- `execute_command` - 命令执行

### 3. 测试用例设计原则

#### 3.1 参数标准化测试 (normalize_tool_arguments)
```python
# Pattern 保留尾随空格
def test_repo_rg_pattern_preserves_trailing_space():
    normalized = normalize_tool_arguments("repo_rg", {"pattern": "^def "})
    assert normalized["pattern"] == "^def "  # 不应丢失空格

# 正则元字符不被错误处理
def test_repo_rg_regex_metacharacters_preserved():
    patterns = ["^def ", "$end", "class|def", "[a-z]+", "foo.*bar"]
    for p in patterns:
        normalized = normalize_tool_arguments("repo_rg", {"pattern": p})
        assert normalized["pattern"] == p

# 别名映射正确
def test_repo_rg_query_alias_maps_to_pattern():
    normalized = normalize_tool_arguments("repo_rg", {"query": "foo bar"})
    assert "pattern" in normalized
```

#### 3.2 工具执行测试 (直接调用 executor)
```python
# 使用真实工作区
@pytest.fixture
def temp_workspace(tmp_path):
    # 创建测试文件结构
    (tmp_path / "src" / "main.py").write_text("def foo(): pass\\nclass Bar: pass\\n")
    return str(tmp_path)

def test_repo_rg_finds_def_in_file(temp_workspace):
    executor = AgentAccelToolExecutor(workspace=temp_workspace)
    result = executor.execute("repo_rg", {"pattern": "^def ", "path": "src"})
    assert result["success"] == True
    assert len(result["result"]["matches"]) == 1

def test_repo_symbols_index_returns_all_symbols(temp_workspace):
    # 当前会报错 "Missing symbol name"
    executor = AgentAccelToolExecutor(workspace=temp_workspace)
    result = executor.execute("repo_symbols_index", {"paths": ["."]})
    assert result["success"] == True
```

#### 3.3 边界条件测试
```python
# 空 pattern
def test_repo_rg_empty_pattern_returns_error():
    result = executor.execute("repo_rg", {"pattern": ""})
    assert result["success"] == False

# 不存在的路径
def test_repo_rg_nonexistent_path():
    result = executor.execute("repo_rg", {"pattern": "test", "path": "/nonexistent"})
    assert result["success"] == False
```

### 4. 测试执行方式
```bash
# 运行所有工具测试
pytest polaris/kernelone/llm/toolkit/tests/test_tools_execution.py -v

# 只运行标准化测试
pytest polaris/kernelone/llm/toolkit/tests/test_tools_normalization.py -v

# 使用标记过滤
pytest -m "tool:repo_rg" -v
```

### 5. 实现步骤

**Phase 1: 建立测试框架和 P0 工具测试 (Day 1)**
- [ ] 创建 `polaris/kernelone/llm/toolkit/tests/` 目录
- [ ] 实现 `normalize_tool_arguments` 的参数标准化测试
- [ ] 修复 `repo_rg` pattern 尾随空格问题
- [ ] 测试 `repo_tree`, `list_directory`, `glob`

**Phase 2: P1 文件读取工具 (Day 2)**
- [ ] 实现 `repo_read_head/tail/slice/around` 测试
- [ ] 验证边界条件处理

**Phase 3: P2 文件写入工具 (Day 3)**
- [ ] 实现写入工具的测试
- [ ] 验证 `search_replace` 逻辑

**Phase 4: P3 其他工具和修复 (Day 4)**
- [ ] 修复 `repo_symbols_index` 实现
- [ ] 测试 `execute_command` (安全边界)
- [ ] 完整回归测试

### 6. 验收标准
- 所有 P0 工具的标准化测试 100% 通过
- 所有 P0 工具的执行测试 100% 通过
- 无 regression: 已有功能不被破坏

### 7. 关键测试用例列表

| 工具 | 测试场景 | 预期结果 |
|------|---------|---------|
| repo_rg | `pattern="^def "` | 保留尾随空格 |
| repo_rg | `query="foo bar"` | 转为 OR 模式 `foo\|bar` |
| repo_rg | `path="src/"` 带尾斜杠 | 正确解析为目录 |
| repo_rg | 正则 `.*`, `+`, `?` | 原样保留 |
| search_code | `query="^def "` | 保留尾随空格 |
| repo_symbols_index | `paths=["."]` | 返回所有符号，不报错 |
| repo_tree | `path="src"`, `recursive=true` | 返回完整树结构 |
| glob | `pattern="**/*.py"` | 返回所有 Python 文件 |
| repo_read_head | `file="foo.py"`, `n=10` | 返回前 10 行 |
| execute_command | `command="ls -la"` | 执行并返回结果 |
