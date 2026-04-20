# Polaris CLI 可视化增强 - 团队执行计划 v1.1
**文档版本**: 1.1.0
**创建日期**: 2026-03-27
**更新日期**: 2026-03-27
**蓝图**: `POLARIS_CLI_VISUAL_ENHANCEMENT_BLUEPRINT_2026-03-27.md`
**团队规模**: 10 人高级 Python 工程师
**关键需求**: 每条信息（消息级别）可折叠，包括 DEBUG 信息

---

## 1. 团队架构

```
┌─────────────────────────────────────────────────────────┐
│                    Technical Lead                        │
│                   [Dev-Lead: 李工]                       │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐
   │Dev-1    │    │Dev-2    │    │Dev-3    │
   │渲染基础 │    │折叠组件 │    │终端集成 │
   │ 2天     │    │ 3天     │    │ 2天     │
   └────┬────┘    └────┬────┘    └────┬────┘
        │               │               │
        │               │               │
        ▼               ▼               ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐
   │Dev-4    │    │Dev-5    │    │Dev-6    │
   │Diff解析 │    │Diff渲染 │    │集成测试 │
   │ 2天     │    │ 2天     │    │ 2天     │
   └────┬────┘    └────┬────┘    └────┬────┘
        │               │               │
        │               │               │
        ▼               ▼               ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐
   │Dev-7    │    │Dev-8    │    │Dev-9    │
   │E2E测试  │    │性能优化 │    │文档审查 │
   │ 2天     │    │ 2天     │    │ 贯穿    │
   └─────────┘    └─────────┘    └─────────┘
                    │
                    ▼
               ┌─────────┐
               │Dev-10   │
               │CI/CD    │
               │ 1天     │
               └─────────┘
```

---

## 2. 角色与职责

### 2.1 Technical Lead - 李工 (Dev-Lead)

**职责**:
- 整体架构决策
- 代码审查（所有 PR）
- 技术风险管控
- 与项目方沟通

**交付物**:
- 架构决策记录 (ADR)
- 代码审查报告
- 阶段验收报告

---

### 2.2 Dev-1: 渲染基础层 - 张工

**任务**: Phase 1 - 基础渲染层

**具体工作**:
1. **RichConsole 封装** (`visualization/rich_console.py`)
   - 封装 `rich.console.Console`
   - 支持 UTF-8 强制
   - 终端宽度自适应检测
   - 主题切换机制
   - 回退到纯文本模式

2. **ConsoleTheme 配置** (`visualization/theme.py`)
   - 定义折叠颜色主题
   - 定义 Diff 颜色主题
   - 明/暗主题支持
   - ANSI 颜色兼容

3. **Renderable 契约** (`visualization/contracts.py`)
   - `VisualizationContract` 接口
   - `RenderMode` 枚举
   - 类型注解完整

**验收标准**:
```python
# 必须通过
def test_console_utf8():
    console = RichConsole()
    console.print(Text("中文测试 ✓"))
    assert console.width > 0

def test_theme_switch():
    theme = ConsoleTheme.load("dark")
    assert theme.fg_add == "green"
```

**工程规范**:
- PEP 8 严格合规
- 100% 类型注解
- 必要 docstring (Google 风格)
- 单项测试覆盖

---

### 2.3 Dev-2: 可折叠组件（核心） - 王工

**任务**: Phase 2 - 消息级折叠实现

**具体工作**:

1. **MessageItem** (`visualization/message_item.py`) ← 核心新文件
   - `id`, `type: MessageType`, `title`, `content`, `is_collapsed` 属性
   - `MessageType` 枚举：USER/ASSISTANT/THINKING/TOOL_CALL/TOOL_RESULT/**DEBUG**/SYSTEM/ERROR/METADATA
   - **DEBUG 信息默认折叠** ← 关键需求
   - `expand()`, `collapse()`, `toggle()` 方法
   - `get_default_collapse()` 根据类型返回默认折叠状态
   - Rich `Tree` 渲染

2. **CollapsibleMessageGroup** (`visualization/message_item.py`)
   - 批量管理多个 MessageItem
   - `expand_all()`, `collapse_all()`
   - **`expand_by_type(MessageType.DEBUG)`** ← 关键方法
   - **`collapse_by_type(MessageType.DEBUG)`** ← 关键方法
   - 类型默认折叠映射

3. **键盘交互处理**
   - Space: 切换当前项
   - 方向键: 导航
   - Ctrl+[/]: 全部折叠/展开
   - 数字键 1-9: 折叠到指定层级
   - **Ctrl+D: 展开所有 DEBUG** ← 关键快捷键
   - **Ctrl+Shift+D: 折叠所有 DEBUG** ← 关键快捷键
   - Ctrl+T: 展开/折叠所有 THINKING

**验收标准**:
```python
def test_debug_default_collapsed():
    """DEBUG 信息默认折叠"""
    msg = MessageItem(
        id="debug-1",
        type=MessageType.DEBUG,
        title="Kernel 操作",
        content="完整调用栈...",
    )
    assert msg.is_collapsed  # DEBUG 默认折叠
    assert msg.get_default_collapse() == True

def test_fold_by_type():
    """按类型批量折叠 DEBUG"""
    group = CollapsibleMessageGroup(...)
    group.collapse_by_type(MessageType.DEBUG)
    # 验证所有 DEBUG 被折叠
    for item in group.items:
        if item.type == MessageType.DEBUG:
            assert item.is_collapsed

def test_nested_collapse():
    parent = CollapsibleItem(id="p", title="Parent", content=Text("..."))
    child = CollapsibleItem(id="c", title="Child", content=Text("..."))
    parent.add_child(child)

    parent.collapse()
    assert parent.is_collapsed
    assert child.is_collapsed  # 递归折叠

def test_max_level():
    # 超过 10 层应抛异常
    with pytest.raises(MaxLevelExceeded):
        create_nested(11)
```

**工程规范**:
- 单一职责：CollapsibleItem 只管单项折叠
- 异常明确：自定义 `FoldStateError`
- 边界处理：空内容、空标题、循环引用

---

### 2.4 Dev-3: 终端集成 - 赵工

**任务**: Phase 2 - terminal_console 集成

**具体工作**:
1. **terminal_console.py 改造**
   - 集成 VisualizationContext
   - 会话折叠渲染
   - 消息内容折叠
   - 工具调用折叠

2. **渲染策略**
   - 默认折叠级别配置
   - 用户偏好持久化
   - 回退到纯文本（无 Rich）

3. **交互适配**
   - 检测终端能力
   - 智能回退策略
   - 日志记录

**验收标准**:
```python
def test_integration_session_fold():
    # 模拟终端会话
    console = MockConsole()
    host = DirectorConsoleHost(console=console)
    
    # 发送长消息
    host.render_turn(long_message)
    
    # 验证折叠标记
    assert "[▶]" in output
```

**工程规范**:
- 不破坏现有 API 契约
- 向后兼容
- 增量改写，不重写

---

### 2.5 Dev-4: Diff 解析器 - 陈工

**任务**: Phase 3 - Diff 解析

**具体工作**:
1. **Diff 解析器** (`visualization/diff_parser.py`)
   - unified diff 格式解析
   - `DiffFile`, `DiffHunk`, `DiffLine` 数据类
   - 行号映射
   - 变更类型识别 (add/delete/modify)

2. **Unified 渲染**
   - 单列 Diff 显示
   - +/- 标记着色
   - 行号对齐
   - 上下文行控制

3. **语法高亮集成**
   - 复用已有 syntax 模块
   - 多语言支持
   - 失败回退到纯文本

**验收标准**:
```python
def test_diff_parse_unified():
    diff_text = """
    --- a/f.txt
    +++ b/f.txt
    @@ -1,3 +1,4 @@
     context
    -deleted
    +added
    +added2
    """
    result = parse_unified_diff(diff_text)
    assert len(result.files) == 1
    assert result.files[0].hunks[0].lines[1].type == "delete"

def test_line_mapping():
    # 验证新旧行号映射正确
    ...
```

---

### 2.6 Dev-5: Diff 渲染器 - 刘工

**任务**: Phase 3 - Diff 渲染

**具体工作**:
1. **Side-by-Side 渲染** (`visualization/diff_view.py`)
   - 双列 Diff 显示
   - 同步滚动（视觉模拟）
   - 宽度自适应
   - 对齐算法

2. **Diff Stats**
   - 文件统计：+10 -5 =3
   - 汇总视图
   - 颜色编码

3. **渲染优化**
   - 大文件分页
   - 虚拟化渲染
   - 增量更新

**验收标准**:
```python
def test_side_by_side_alignment():
    diff = DiffView.from_unified(unified_text)
    panel = diff.render_side_by_side()
    
    # 验证对齐
    lines = str(panel).split("\n")
    for line in lines:
        if line.startswith("-"):
            # 绿色对应行应该在右侧
            ...

def test_large_file_paging():
    # 10000 行 Diff 渲染时间 < 1s
    ...
```

---

### 2.7 Dev-6: Director 集成 - 周工

**任务**: Phase 4 - director 集成

**具体工作**:
1. **director/console_host.py 改造**
   - Diff 结果展示
   - 工具调用折叠
   - 会话历史折叠

2. **工具调用折叠**
   - 工具名 + 状态
   - 参数摘要
   - 结果预览

3. **Rollback 机制**
   - 集成失败回退
   - 优雅降级

**验收标准**:
```python
def test_tool_call_fold():
    # 模拟工具调用
    result = execute_tool("git_diff", {...})
    
    # 验证折叠输出
    output = render_tool_result(result)
    assert "[▶] git_diff" in output
    assert "+1 -2" in output  # 折叠后仍显示摘要
```

---

### 2.8 Dev-7: E2E 测试 - 吴工

**任务**: Phase 4 - 完整测试

**具体工作**:
1. **E2E 测试套件**
   - CLI 交互测试
   - 折叠/展开流程
   - Diff 展示流程
   - **DEBUG 折叠/展开流程** ← 关键测试
   - 按类型折叠测试
   - 边界条件测试

2. **回归测试**
   - 现有功能无破坏
   - 性能无退化
   - 兼容性保持

3. **测试报告**
   - 覆盖率报告
   - 缺陷报告
   - 性能报告

**验收标准**:
```python
def test_e2e_fold_interaction():
    # 启动 CLI
    process = subprocess.Popen(["python", "-m", "polaris.delivery.cli"])

    # 发送折叠命令
    process.stdin.write(b"[Space]\n")

    # 验证输出
    output = process.stdout.read()
    assert "[▼]" in output

    process.terminate()

def test_e2e_debug_fold():
    """测试 DEBUG 信息折叠流程"""
    process = subprocess.Popen(["python", "-m", "polaris.delivery.cli"])

    # 发送 Ctrl+D 展开所有 DEBUG
    process.stdin.write(b"\x04")  # Ctrl+D

    # 验证 DEBUG 展开
    output = process.stdout.read()
    assert "[DEBUG]" in output
    # DEBUG 内容可见

    # 发送 Ctrl+Shift+D 折叠所有 DEBUG
    process.stdin.write(b"\x04\x04")  # Ctrl+Shift+D

    process.terminate()

def test_e2e_debug_default_collapsed():
    """测试 DEBUG 信息默认折叠"""
    process = subprocess.Popen(["python", "-m", "polaris.delivery.cli"])

    # 发送消息，触发 DEBUG 输出
    process.stdin.write(b"test command\n")

    output = process.stdout.read()
    # DEBUG 应该折叠显示
    assert "[▶] [DEBUG]" in output  # 折叠标记
    # 详细 DEBUG 内容不应该直接可见

    process.terminate()

def test_regression_console():
    # 确保原有功能正常
    ...
```

---

### 2.9 Dev-8: 性能优化 - 郑工

**任务**: 贯穿全周期 - 性能优化

**具体工作**:
1. **渲染性能**
   - 大内容虚拟化
   - 懒加载策略
   - 增量渲染

2. **Diff 性能**
   - 并行语法高亮
   - Diff 计算缓存
   - 流式渲染

3. **内存优化**
   - 大文件处理
   - 内存泄漏检测
   - 资源释放

**验收标准**:
```python
def test_perf_large_content():
    # 10000 行内容折叠/展开 < 100ms
    start = time.time()
    render_large_content(10000)
    assert time.time() - start < 0.1

def test_memory_no_leak():
    # 1000 次折叠/展开后内存增长 < 10MB
    ...
```

---

### 2.10 Dev-9: 代码审查与文档 - 冯工

**任务**: 贯穿全周期 - 质量把控

**具体工作**:
1. **代码审查**
   - 按 `必须改/建议改/可选优化` 分级
   - 审查报告输出
   - 问题跟踪闭环

2. **文档维护**
   - API 文档
   - 使用指南
   - 变更日志

3. **规范培训**
   - PEP 8 合规检查
   - 类型注解检查
   - docstring 规范

**审查报告模板**:
```markdown
## 代码审查报告 - [PR Title]

### 必须改 (Blocker)
| 文件 | 问题 | 原因 | 建议 | 严重程度 |
|------|------|------|------|----------|
| xxx.py:123 | 变量命名不清晰 | 可读性差 | 改为 `user_session_id` | HIGH |

### 建议改 (Warning)
...

### 可选优化 (Suggestion)
...
```

---

### 2.11 Dev-10: CI/CD 集成 - 陈工 (II)

**任务**: 收尾 - CI/CD

**具体工作**:
1. **GitHub Actions**
   - 测试自动化
   - 代码质量检查
   - 发布流程

2. **测试集成**
   - pytest 配置
   - 覆盖率阈值
   - 性能基准

3. **文档发布**
   - API 文档生成
   - Changelog 更新
   - Release Notes

**验收标准**:
```yaml
# .github/workflows/cli-visual.yml
- name: Test Visualization
  run: pytest tests/delivery/cli/visualization/ -v --cov

- name: Type Check
  run: mypy polaris/delivery/cli/visualization/

- name: Lint
  run: ruff check polaris/delivery/cli/visualization/
```

---

## 3. 任务分配矩阵

| 任务 | 负责人 | 依赖 | 预计工时 | 优先级 |
|------|--------|------|----------|--------|
| T1: RichConsole 封装 | Dev-1 | - | 1d | P0 |
| T2: ConsoleTheme 配置 | Dev-1 | T1 | 0.5d | P0 |
| T3: Renderable 契约 | Dev-1 | T1 | 0.5d | P0 |
| **T4: MessageItem (消息级折叠)** | Dev-2 | T1 | 1d | **P0** |
| T5: MessageType 枚举 + DEBUG 支持 | Dev-2 | T4 | 0.5d | **P0** |
| **T6: CollapsibleMessageGroup (按类型折叠)** | Dev-2 | T4 | 1d | **P0** |
| T7: DEBUG 默认折叠策略 | Dev-2 | T5 | 0.5d | **P0** |
| **T8: Ctrl+D / Ctrl+Shift+D 快捷键** | Dev-2 | T6 | 0.5d | **P0** |
| T9: 键盘交互 (Space/方向键等) | Dev-2 | T8 | 0.5d | P1 |
| T10: terminal_console 集成 | Dev-3 | T1,T3 | 1d | P0 |
| T11: 会话折叠渲染 | Dev-3 | T10 | 1d | P0 |
| T12: Diff 解析器 | Dev-4 | T1 | 1.5d | P0 |
| T13: Unified 渲染 | Dev-4 | T12 | 0.5d | P0 |
| T14: 语法高亮 | Dev-4 | T13 | 0.5d | P1 |
| T15: Side-by-Side 渲染 | Dev-5 | T12 | 1d | P0 |
| T16: Diff Stats | Dev-5 | T15 | 0.5d | P1 |
| T17: 渲染优化 | Dev-5 | T15 | 0.5d | P2 |
| T18: director 集成 | Dev-6 | T10,T13 | 1d | P0 |
| T19: 工具调用折叠 | Dev-6 | T18 | 0.5d | P1 |
| **T20: DEBUG 信息流集成** | Dev-6 | T7 | 0.5d | **P0** |
| T21: E2E 测试 (消息折叠) | Dev-7 | T11,T19 | 1d | P0 |
| **T22: E2E 测试 (DEBUG 折叠)** | Dev-7 | T20 | 1d | **P0** |
| T23: 回归测试 | Dev-7 | T21 | 0.5d | P0 |
| T24: 性能测试 | Dev-8 | T17 | 1d | P1 |
| T25: 内存优化 | Dev-8 | T24 | 1d | P1 |
| T26: 代码审查 | Dev-9 | 全程 | 贯穿 | P0 |
| T27: CI/CD | Dev-10 | T23 | 1d | P0 |

---

## 4. 沟通机制

### 4.1 日常同步

| 时间 | 形式 | 参与者 | 内容 |
|------|------|--------|------|
| 09:00 | Standup | 全员 | 昨日进度 / 今日计划 / 阻塞 |
| 15:00 | Sync | 相关模块 | 跨模块对接问题 |
| 17:30 | 报告 | Dev-Lead | 风险/进展汇总 |

### 4.2 PR 审查流程

```
┌─────────────┐
│ 开发者提交  │
└──────┬──────┘
       ▼
┌─────────────┐
│ CI 检查     │ ──▶ 失败 ──▶ 修复
│ - lint      │
│ - type check│
│ - unit test │
└──────┬──────┘
       ▼
┌─────────────┐
│ Dev-9 审查  │ ──▶ 必须改 ──▶ 修复
│ (质量关)    │ ──▶ 建议改 ──▶ 可选
└──────┬──────┘
       ▼
┌─────────────┐
│ Dev-Lead    │ ──▶ 架构问题 ──▶ 讨论
│ (技术关)    │
└──────┬──────┘
       ▼
┌─────────────┐
│   Merge     │
└─────────────┘
```

### 4.3 问题升级

```
Level 1: 阻塞当前任务
  └─ ▶ 立即在团队群 @ 相关人

Level 2: 影响其他模块
  └─ ▶ 发起紧急 sync 会议

Level 3: 架构决策
  └─ ▶ 提 ADR，TL 裁决
```

---

## 5. 质量门禁

### 5.1 必须通过的检查

| 检查项 | 工具 | 阈值 |
|--------|------|------|
| 单元测试 | pytest | 80% 覆盖率 |
| 类型检查 | mypy | 100% 注解 |
| 代码规范 | ruff | 0 errors |
| 安全扫描 | bandit | 0 high |
| 性能基准 | pytest-benchmark | 无退化 |

### 5.2 禁止的行为

- ❌ `except Exception:` 无日志
- ❌ `any` 类型（必须具体类型）
- ❌ 魔法数字（必须命名常量）
- ❌ 重复代码（必须抽象）
- ❌ 无 docstring 的公共 API

---

## 6. 输出物清单

### 6.1 代码交付

| 模块 | 文件 | 状态 |
|------|------|------|
| 渲染基础 | `visualization/rich_console.py` | 待开发 |
| 主题配置 | `visualization/theme.py` | 待开发 |
| 契约接口 | `visualization/contracts.py` | 待开发 |
| **消息级折叠** | `visualization/message_item.py` | **待开发** |
| 可折叠 | `visualization/collapsible.py` | 待开发 |
| Diff 解析 | `visualization/diff_parser.py` | 待开发 |
| Diff 渲染 | `visualization/diff_view.py` | 待开发 |
| 集成改造 | `terminal_console.py` | 待改造 |
| 集成改造 | `director/console_host.py` | 待改造 |
| 测试套件 | `tests/delivery/cli/visualization/` | 待开发 |

### 6.2 文档交付

- [ ] API 参考文档
- [ ] 使用指南
- [ ] 变更日志
- [ ] 团队复盘报告

---

## 7. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Rich API 变更 | 低 | 中 | 固定版本，兼容性测试 |
| 终端兼容性问题 | 中 | 高 | 多终端测试，回退策略 |
| 性能不达标 | 中 | 中 | 提前性能测试，优化迭代 |
| 需求变更 | 中 | 中 | 保持灵活性，模块化设计 |

---

## 8. 成功标准

### 8.1 功能标准

- [ ] **每条信息（消息级）可折叠**
- [ ] 可折叠展示正常
- [ ] Diff View 正常
- [ ] 键盘/鼠标交互正常
- [ ] 状态持久化正常
- [ ] **DEBUG 信息默认折叠** ← 关键需求
- [ ] **Ctrl+D / Ctrl+Shift+D 快捷键正常**
- [ ] **按类型折叠/展开正常**

### 8.2 质量标准

- [ ] 80% 测试覆盖率
- [ ] 0 lint errors
- [ ] 0 type errors
- [ ] 0 security issues

### 8.3 性能标准

- [ ] 10000 行内容渲染 < 1s
- [ ] 1000 次交互后内存增长 < 10MB
- [ ] 启动时间无明显增加
- [ ] 100 个 DEBUG 信息折叠/展开 < 100ms

---

**文档结束**
