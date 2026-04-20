# Verification Card: Tree-sitter 可用性检测与工具过滤

**日期**: 2026-03-29
**验证目标**: ADR-0067 实施完成度
**状态**: 待验证

---

## 验证清单

### Phase 1: 核心检测机制
- [ ] `is_tree_sitter_available()` 函数存在
- [ ] 带 5 秒超时保护
- [ ] 返回 `TreeSitterAvailability` 数据类
- [ ] 结果被缓存（`@lru_cache`）
- [ ] mypy 类型检查通过

### Phase 2: 工具规格标记
- [ ] `TS_DEPENDENT_TOOLS` 常量定义于 `contracts.py`
- [ ] 包含 `repo_symbols_index`, `treesitter_find_symbol`
- [ ] mypy 类型检查通过

### Phase 3: 工具过滤入口
- [ ] `get_available_tools()` 函数存在
- [ ] 正确过滤 `TS_DEPENDENT_TOOLS`
- [ ] `search_code` / `repo_rg` 不受影响
- [ ] mypy 类型检查通过

### Phase 4: 角色配置更新
- [ ] `tool_gateway.py` 集成可用性过滤
- [ ] PM 角色配置中 tree-sitter 工具正确过滤
- [ ] mypy 类型检查通过

### Phase 5: 验证与测试
- [ ] 新增 `test_ts_availability.py` 存在
- [ ] 检测超时测试通过
- [ ] 工具过滤测试通过
- [ ] ripgrep 工具不受影响测试通过
- [ ] ruff 检查通过

---

## 测试用例要求

### 1. 可用性检测
```python
def test_ts_available_when_library_installed():
    """tree-sitter 可用时返回 available=True"""
    result = is_tree_sitter_available()
    assert result.available is True
    assert result.reason is None

def test_ts_unavailable_on_import_error():
    """导入失败时返回 available=False"""
    with patch("tree_sitter_language_pack", side_effect=ImportError()):
        # Clear cache first
        is_tree_sitter_available.cache_clear()
        result = is_tree_sitter_available()
        assert result.available is False

def test_ts_timeout_protection():
    """检测超时保护生效"""
    with patch("tree_sitter_language_pack.get_parser", side_effect=TimeoutError()):
        is_tree_sitter_available.cache_clear()
        result = is_tree_sitter_available(timeout=1.0)
        assert result.available is False
        assert "timeout" in result.reason.lower()
```

### 2. 工具过滤
```python
def test_filter_removes_ts_dependent_when_unavailable():
    """TS 不可用时过滤掉相关工具"""
    ts_unavailable = TreeSitterAvailability(available=False, reason="timeout")
    tools = ["search_code", "repo_symbols_index", "glob"]
    result = get_available_tools(tools, ts_unavailable)
    assert result == ["search_code", "glob"]

def test_search_code_not_filtered():
    """search_code 不受 TS 可用性影响"""
    ts_unavailable = TreeSitterAvailability(available=False, reason="timeout")
    tools = ["search_code", "repo_rg"]
    result = get_available_tools(tools, ts_unavailable)
    assert "search_code" in result
    assert "repo_rg" in result
```

---

## 验收标准

| 标准 | 状态 |
|------|------|
| 全部 5 个 Phase 完成 | 待验证 |
| 新增测试 100% 通过 | 待验证 |
| ruff 检查 0 errors | 待验证 |
| mypy 检查 0 errors | 待验证 |
