# CLI 可视化增强缺陷修复 - 团队执行计划 v1.0
**文档版本**: 1.0.0
**创建日期**: 2026-03-27
**蓝图**: 
- `CLI_VISUAL_AUDIT_REPORT_2026-03-27.md` (审计报告)
- `CLI_VISUAL_FIX_BLUEPRINT_2026-03-27.md` (修复蓝图)
**团队规模**: 10 人高级 Python 工程师
**任务**: 修复 16 个审计发现的问题

---

## 1. 团队架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Technical Lead                           │
│                     [Dev-Lead: 李工]                         │
│                    负责: 蓝图修复 + 架构                       │
└───────────────────────────┬─────────────────────────────────┘
                            │
    ┌───────────────────────┼───────────────────────┐
    │                       │                       │
    ▼                       ▼                       ▼
┌─────────┐           ┌─────────┐           ┌─────────┐
│Dev-1    │           │Dev-2    │           │Dev-3    │
│张工     │           │王工     │           │赵工     │
│基础设施 │           │核心组件  │           │终端集成  │
│修复     │           │修复     │           │修复     │
│1d       │           │2d       │           │1d       │
└────┬────┘           └────┬────┘           └────┬────┘
     │                     │                     │
     │                     │                     │
     ▼                     ▼                     ▼
┌─────────┐           ┌─────────┐           ┌─────────┐
│Dev-4    │           │Dev-5    │           │Dev-6    │
│陈工     │           │刘工     │           │周工     │
│Diff解析 │           │Diff渲染 │           │集成验证  │
│修复     │           │修复     │           │修复     │
│1d       │           │1d       │           │1d       │
└────┬────┘           └────┬────┘           └────┬────┘
     │                     │                     │
     │                     │                     │
     ▼                     ▼                     ▼
┌─────────┐           ┌─────────┐           ┌─────────┐
│Dev-7    │           │Dev-8    │           │Dev-9    │
│吴工     │           │郑工     │           │冯工     │
│测试修复 │           │性能验证 │           │代码审查  │
│1d       │           │0.5d     │           │贯穿     │
└─────────┘           └─────────┘           └─────────┘
                                              │
                                              ▼
                                         ┌─────────┐
                                         │Dev-10   │
                                         │陈工(II) │
                                         │文档修复 │
                                         │0.5d     │
                                         └─────────┘
```

---

## 2. 问题修复分配

### 2.1 Dev-Lead: 李工

**职责**: 蓝图文档修复 + 架构决策

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| C3 | 任务矩阵 ID 重复 | 删除重复行 | 5min |
| C5 | Markdown 标题重复 | 修正标题层级 | 1min |
| - | 蓝图文档整体审查 | 确保修复后文档一致 | 30min |

**具体操作**:
```bash
# C3: 删除 TEAM_PLAN.md 行 565-574
# (重复的任务矩阵)

# C5: BLUEPRINT.md 修正
# 行 274-277 改为:
#### 3.2.2 CollapsibleItem (通用折叠组件)

#### 3.2.3 DiffView
```

---

### 2.2 Dev-1: 张工 (基础设施)

**职责**: 类型注解 + 模块结构 + RichConsole

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| M4 | 模块结构缺少 message_item.py | 添加到模块结构 | 10min |
| M7 | 类型注解兼容性 | 添加 `from __future__ import annotations` | 1h |
| m2 | VisualizationContext 类型注解 | 使用 Union 替代 `\|` | 10min |
| m3 | render_context 未定义 | 补充类设计或删除引用 | 1h |

**输出文件**:
- `visualization/__init__.py` - 修复导出
- `visualization/render_context.py` - 补充定义或标记 TODO

**验收标准**:
```python
# test_import_compatibility.py
def test_all_files_have_future_annotations():
    """验证所有模块都有类型注解兼容性"""
    import os
    import ast
    
    for root, dirs, files in os.walk("polaris/delivery/cli/visualization"):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path) as fp:
                    tree = ast.parse(fp.read())
                
                # 检查有 from __future__ import annotations
                has_future = any(
                    isinstance(node, ast.ImportFrom) 
                    and node.module == "__future__"
                    and any(a.name == "annotations" for a in node.names)
                    for node in ast.walk(tree)
                )
                assert has_future, f"{path} missing future annotations"
```

---

### 2.3 Dev-2: 王工 (核心组件)

**职责**: MessageItem + CollapsibleItem 核心逻辑修复

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| C1 | MessageItem 默认折叠逻辑 | 重构 `is_collapsed` 默认逻辑 | 2h |
| C4 | 折叠树结构错误 | 修正文档示例 | 1h |
| M2 | add_child 方法未定义 | 实现方法 | 1h |
| M3 | MaxLevelExceeded 未定义 | 定义异常类 | 30min |
| m1 | collapsed_by_type 初始化 | 添加 __post_init__ | 30min |

**输出文件**:
- `visualization/message_item.py` - 核心重构
- `visualization/collapsible.py` - 添加 add_child

**验收标准**:
```python
# test_message_item.py
class TestMessageItemDefaultCollapse:
    @pytest.mark.parametrize("msg_type,expected", [
        (MessageType.USER, False),
        (MessageType.ASSISTANT, False),
        (MessageType.ERROR, False),
        (MessageType.THINKING, True),
        (MessageType.TOOL_CALL, True),
        (MessageType.TOOL_RESULT, True),
        (MessageType.DEBUG, True),  # ← 关键测试
        (MessageType.SYSTEM, True),
        (MessageType.METADATA, True),
    ])
    def test_default_collapse_by_type(self, msg_type, expected):
        msg = MessageItem(id="test", type=msg_type, title="T", content="C")
        assert msg.is_collapsed == expected

class TestMaxLevelExceeded:
    def test_exception_defined(self):
        exc = MaxLevelExceeded(10, 11)
        assert exc.max_level == 10
        assert exc.actual_level == 11
        assert "10" in str(exc)
        assert "11" in str(exc)
    
    def test_nesting_limit(self):
        parent = MessageItem(id="p", type=MessageType.USER, title="P", content="")
        current = parent
        for i in range(9):  # 9层
            child = MessageItem(id=f"c{i}", type=MessageType.DEBUG, title=f"C{i}", content="")
            current.add_child(child)
            current = child
        
        # 第10层 OK
        child10 = MessageItem(id="c10", type=MessageType.DEBUG, title="C10", content="")
        current.add_child(child10)  # 不抛异常
        
        # 第11层失败
        child11 = MessageItem(id="c11", type=MessageType.DEBUG, title="C11", content="")
        with pytest.raises(MaxLevelExceeded):
            current.add_child(child11)
```

---

### 2.4 Dev-3: 赵工 (终端集成)

**职责**: terminal_console 集成修复

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| - | terminal_console 集成 | 集成新的 MessageItem | 1d |
| m4 | 流式输出处理 | 设计流式折叠方案 | 4h |

**输出文件**:
- 改造 `terminal_console.py`
- 流式输出处理设计文档

---

### 2.5 Dev-4: 陈工 (Diff 解析)

**职责**: Diff 解析器修复

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| M1 | diff_view 缺少修改类型 | 添加 modify 类型 | 2h |
| M6 | parse_diff_lines 未定义 | 实现函数 | 1h |

**输出文件**:
- `visualization/diff_parser.py`

**验收标准**:
```python
# test_diff_parser.py
class TestParseDiffLines:
    def test_add_line(self):
        lines = parse_diff_lines("+ new content")
        assert len(lines) == 1
        assert lines[0].line_type == "add"
        assert lines[0].content == "new content"
    
    def test_delete_line(self):
        lines = parse_diff_lines("- old content")
        assert len(lines) == 1
        assert lines[0].line_type == "delete"
    
    def test_context_line(self):
        lines = parse_diff_lines(" unchanged")
        assert len(lines) == 1
        assert lines[0].line_type == "context"
    
    def test_modify_pair(self):
        """验证修改对识别"""
        diff = "- old line\n+ new line\n  context"
        lines = parse_diff_lines(diff)
        # 删除行应标记为 modify_pair
        assert lines[0].is_modify_pair is True
```

---

### 2.6 Dev-5: 刘工 (Diff 渲染)

**职责**: Diff 渲染修复 + Stats

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| - | Side-by-Side 渲染 | 修复对齐逻辑 | 1d |

---

### 2.7 Dev-6: 周工 (集成验证)

**职责**: director 集成 + 验证

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| - | director 集成 | 集成 Diff 展示 | 1d |

---

### 2.8 Dev-7: 吴工 (测试)

**职责**: 测试修复 + 补充

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| C2 | Ctrl+D 冲突 | 修复 E2E 测试快捷键 | 2h |
| M5 | test_fold_by_type 不完整 | 补充断言 | 30min |
| - | 完整测试覆盖 | 补充边界测试 | 1d |

**验收标准**:
```python
# test_e2e_keyboard.py
class TestKeyboardShortcuts:
    def test_alt_d_expands_debug(self):
        """Alt+D 展开所有 DEBUG"""
        process = subprocess.Popen(...)
        
        # 发送 Alt+D
        process.stdin.write(b"\x1b[d")  # Alt+D ANSI sequence
        process.stdin.flush()
        
        output = process.stdout.read()
        # 验证 DEBUG 内容可见
        assert "[DEBUG]" in output
        
        process.terminate()
    
    def test_no_ctrl_d_in_tests(self):
        """确保测试不直接使用 Ctrl+D"""
        import ast
        # 扫描所有测试文件
        for root, dirs, files in os.walk("tests/"):
            for f in files:
                if f.startswith("test_") and f.endswith(".py"):
                    path = os.path.join(root, f)
                    with open(path) as fp:
                        content = fp.read()
                    
                    # 检查不包含 \x04 (Ctrl+D)
                    assert b"\\x04" not in content.encode()
                    assert r"\x04" not in content
```

---

### 2.9 Dev-8: 郑工 (性能验证)

**职责**: 性能基准测试

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| - | 性能测试 | 验证修复后性能不降 | 30min |

---

### 2.10 Dev-9: 冯工 (代码审查)

**职责**: 全程质量把控

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| - | 代码审查 | 按必须改/建议改/可选优化分级 | 贯穿 |

**审查重点**:
1. C1 修复是否保持向后兼容
2. C2 修复是否真正解决冲突
3. M1-M7 修复是否完整
4. 测试覆盖率是否达标

---

### 2.11 Dev-10: 陈工 (II) (文档)

**职责**: 文档同步修复

| 问题 ID | 问题描述 | 操作 | 工时 |
|---------|----------|------|------|
| C3 | TEAM_PLAN 任务重复 | 删除重复任务 | 5min |
| C4 | BLUEPRINT 折叠树 | 修正示例 | 5min |
| C5 | BLUEPRINT 标题重复 | 修正标题 | 5min |
| - | 文档一致性检查 | 确保修复后文档同步 | 30min |

---

## 3. 任务分配矩阵 (修正版)

| 任务 | 负责人 | 依赖 | 工时 | 优先级 |
|------|--------|------|------|--------|
| T-D1: 删除任务重复 (C3) | Dev-10 | - | 5min | P0 |
| T-D2: 修正标题 (C5) | Dev-10 | - | 5min | P0 |
| T-D3: 修正折叠树示例 (C4) | Dev-2 | - | 1h | P0 |
| T-D4: MessageItem 重构 (C1) | Dev-2 | - | 2h | P0 |
| T-D5: MaxLevelExceeded (M3) | Dev-2 | - | 30min | P0 |
| T-D6: add_child 方法 (M2) | Dev-2 | T-D4 | 1h | P0 |
| T-D7: collapsed_by_type (m1) | Dev-2 | T-D4 | 30min | P1 |
| T-D8: Diff 解析器 (M1, M6) | Dev-4 | - | 3h | P0 |
| T-D9: 类型注解兼容 (M7) | Dev-1 | - | 1h | P0 |
| T-D10: 模块结构 (M4) | Dev-1 | T-D4 | 10min | P0 |
| T-D11: VisualizationContext (m2) | Dev-1 | - | 10min | P2 |
| T-D12: render_context (m3) | Dev-1 | - | 1h | P2 |
| T-D13: E2E 测试修复 (C2) | Dev-7 | - | 2h | P0 |
| T-D14: test_fold_by_type (M5) | Dev-7 | T-D4 | 30min | P1 |
| T-D15: 完整测试覆盖 | Dev-7 | T-D8, T-D13 | 1d | P1 |
| T-D16: terminal_console 集成 | Dev-3 | T-D4, T-D9 | 1d | P0 |
| T-D17: Diff 渲染修复 | Dev-5 | T-D8 | 1d | P1 |
| T-D18: director 集成 | Dev-6 | T-D16 | 1d | P1 |
| T-D19: 流式输出设计 (m4) | Dev-3 | T-D16 | 4h | P2 |
| T-D20: 性能验证 | Dev-8 | T-D15 | 30min | P2 |
| T-D21: 代码审查 | Dev-9 | 全程 | 贯穿 | P0 |
| T-D22: 文档一致性检查 | Dev-10 | T-D1-T-D21 | 30min | P1 |

---

## 4. 执行时间线

```
Day 1
├── 09:00 - 09:30  全体: 审计报告解读 + 分工确认
├── 09:30 - 12:00 并行:
│   ├── Dev-10: T-D1, T-D2 (文档修复)
│   ├── Dev-2: T-D4, T-D5 (MessageItem 重构)
│   └── Dev-1: T-D9 (类型注解)
├── 12:00 - 13:00 午休
├── 13:00 - 17:30 并行:
│   ├── Dev-2: T-D3, T-D6, T-D7
│   ├── Dev-4: T-D8 (Diff 解析)
│   ├── Dev-7: T-D13 (E2E 测试)
│   └── Dev-Lead: T-C3, T-C5 (蓝图修复)
└── 17:30         每日站会

Day 2
├── 09:00 - 12:00 并行:
│   ├── Dev-3: T-D16 (terminal_console 集成)
│   ├── Dev-4: 继续 Diff 解析
│   ├── Dev-1: T-D10, T-D11, T-D12
│   └── Dev-7: T-D14, T-D15
├── 12:00 - 13:00 午休
├── 13:00 - 17:30 并行:
│   ├── Dev-5: T-D17 (Diff 渲染)
│   ├── Dev-6: T-D18 (director 集成)
│   ├── Dev-9: T-D21 (代码审查)
│   └── Dev-3: T-D19 (流式输出)
└── 17:30         每日站会 + 阶段报告

Day 3
├── 09:00 - 12:00 继续开发 + 修复审查问题
├── 12:00 - 13:00 午休
├── 13:00 - 15:00 并行:
│   ├── Dev-8: T-D20 (性能验证)
│   ├── Dev-10: T-D22 (文档一致性)
│   └── Dev-9: 最终审查
├── 15:00 - 17:00 修复 + 测试
└── 17:00         验收 + 交付报告
```

---

## 5. 质量门禁

### 5.1 必须通过的检查

| 检查项 | 工具 | 阈值 |
|--------|------|------|
| 单元测试 | pytest | 90% 覆盖率 (+10%) |
| 类型检查 | mypy | 100% 注解 |
| 代码规范 | ruff | 0 errors |
| 安全扫描 | bandit | 0 high |
| **无终端冲突快捷键** | 自定义脚本 | 100% |

### 5.2 关键验证

```python
# test_no_terminal_conflicts.py
def test_no_conflicting_shortcuts():
    """确保不使用终端控制字符"""
    import os
    import ast
    
    CONFLICT_KEYS = {
        b"\\x03",  # Ctrl+C
        b"\\x04",  # Ctrl+D
        b"\\x1a",  # Ctrl+Z
        b"\\x1c",  # Ctrl+\
    }
    
    for root, dirs, files in os.walk("polaris/delivery/cli/visualization"):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path) as fp:
                    content = fp.read()
                
                for key in CONFLICT_KEYS:
                    assert key not in content.encode(), \
                        f"{path} contains conflicting key {key}"
```

---

## 6. 风险缓解

### 6.1 主要风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| C1 重构破坏现有调用 | 中 | 高 | 保持 is_collapsed=None 时用新逻辑，显式值优先 |
| C2 快捷键用户习惯 | 高 | 中 | 提供配置 + 迁移脚本 |
| M1 Diff 解析逻辑变化 | 低 | 高 | 完整回归测试 |

### 6.2 回滚计划

| 问题 | 回滚策略 |
|------|----------|
| C1 | 恢复 default_factory=True |
| C2 | 恢复 Ctrl+D，添加警告日志 |
| M1 | 恢复 Literal["add", "delete", "context", "header"] |

---

## 7. 验收标准

### 7.1 功能验收

- [ ] MessageItem 按类型正确折叠
- [ ] DEBUG 默认折叠
- [ ] USER/ASSISTANT/ERROR 默认展开
- [ ] add_child 方法正常工作
- [ ] MaxLevelExceeded 正确抛出
- [ ] parse_diff_lines 正确解析
- [ ] E2E 测试不使用冲突快捷键

### 7.2 质量验收

- [ ] 0 CRITICAL 问题残留
- [ ] 0 MAJOR 问题残留
- [ ] 测试覆盖率 ≥ 90%
- [ ] 0 type errors
- [ ] 0 lint errors

---

## 8. 输出物清单

| 类型 | 内容 | 负责人 |
|------|------|--------|
| 修复代码 | `visualization/message_item.py` | Dev-2 |
| 修复代码 | `visualization/diff_parser.py` | Dev-4 |
| 修复代码 | `visualization/__init__.py` | Dev-1 |
| 修复代码 | 测试文件 | Dev-7 |
| 修复文档 | 蓝图文档修正 | Dev-10, Dev-Lead |
| 验证报告 | 审计修复验证 | Dev-9 |
| 交付报告 | 最终交付清单 | Dev-Lead |

---

**团队执行计划结束**
