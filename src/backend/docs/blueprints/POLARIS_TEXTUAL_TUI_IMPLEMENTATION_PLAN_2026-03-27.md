# Polaris CLI Textual TUI 实施方案 v1.0

**文档版本**: 1.0.0
**创建日期**: 2026-03-27
**状态**: 待实施
**团队规模**: 6 人

---

## 1. 背景与目标

### 1.1 问题陈述

现有 `terminal_console.py` 使用 ANSI 转义序列实现鼠标支持，在跨平台场景下存在以下问题：
- Windows PowerShell 不支持 `select.select()` 监听 stdin
- 鼠标事件解析依赖终端模拟器实现（兼容性差）
- 无法精确追踪屏幕行号，点击定位不准确

### 1.2 解决方案

使用 **Textual** 框架重写 CLI 界面：
- 原生跨平台鼠标/键盘事件处理
- 内置 `@on_click` 装饰器实现可点击部件
- 精确的屏幕位置管理和布局系统
- 成熟的 TUI 组件库

### 1.3 目标

| 目标 | 指标 |
|------|------|
| 鼠标点击折叠 | 点击 `[▶]` 展开，单击切换状态 |
| 键盘快捷键 | Alt+D 切换所有，方向键导航 |
| 跨平台 | Windows/macOS/Linux 原生支持 |
| 性能 | 1000+ DEBUG 消息无卡顿 |
| 兼容性 | 与现有 `--backend plain` 并存 |

---

## 2. 架构设计

### 2.1 模块结构

```
polaris/delivery/cli/
├── terminal_console.py      # 现有 ANSI 版（保留）
├── textual_console.py       # 新 Textual 版
└── cli_integration.py       # 统一入口
```

### 2.2 核心组件

```
┌─────────────────────────────────────────────────────────┐
│                    PolarisTextualApp                      │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ HeaderBar   │  │ StatusBar   │  │ InputField      │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                 MessageContainer                     │  │
│  │  ┌───────────────────────────────────────────────┐  │  │
│  │  │ CollapsibleDebugItem (可点击)                 │  │  │
│  │  │   ├── Header: [▶] [fs][read] (+5 lines)     │  │  │
│  │  │   └── Content: (展开时显示 JSON)              │  │  │
│  │  └───────────────────────────────────────────────┘  │  │
│  │  ┌───────────────────────────────────────────────┐  │  │
│  │  │ UserMessage / AssistantMessage / ToolResult   │  │  │
│  │  └───────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.3 数据流

```
User Input → InputField → LLM Provider → Stream Events
                                            ↓
                              ┌─────────────────────────┐
                              │   Event Router        │
                              └─────────────────────────┘
                                            ↓
              ┌─────────────────────────────┼─────────────────────────────┐
              ↓                             ↓                             ↓
    ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
    │ ContentChunk    │      │ DebugEvent      │      │ ToolResult     │
    │ (直接显示)      │      │ (折叠/展开)     │      │ (JSON 显示)    │
    └─────────────────┘      └─────────────────┘      └─────────────────┘
```

---

## 3. 功能规格

### 3.1 消息类型

| 消息类型 | 显示样式 | 折叠支持 |
|----------|----------|----------|
| USER | 青色 + 粗体 | ❌ |
| ASSISTANT | 绿色 | ❌ |
| THINKING | 黄色 | ✅ |
| TOOL_CALL | 蓝色 | ✅ |
| TOOL_RESULT | 蓝色 | ✅ |
| DEBUG | 灰色 | ✅ (默认折叠) |
| SYSTEM | 洋红 | ✅ |
| ERROR | 红色 + 粗体 | ❌ |
| METADATA | 灰色 | ✅ |

### 3.2 折叠行为

| 操作 | 触发 | 效果 |
|------|------|------|
| 单击标题 | 鼠标点击 | 切换折叠状态 |
| 双击内容 | 鼠标双击 | 无效果 |
| Alt+D | 键盘 | 切换所有 DEBUG |
| Alt+Shift+D | 键盘 | 折叠所有 DEBUG |
| Ctrl+D | 键盘 | 展开所有 DEBUG |
| ↑/↓ | 方向键 | 导航消息 |
| Enter | 键盘 | 展开当前项 |
| -/= | 键盘 | 折叠当前项 |

### 3.3 快捷键绑定

| 快捷键 | 动作 | 作用域 |
|--------|------|--------|
| Alt+D | toggle_all_debug | 全局 |
| Alt+Shift+D | collapse_all_debug | 全局 |
| Ctrl+D | expand_all_debug | 全局 |
| Ctrl+C | quit | 全局 |
| ↑/↓ | scroll_messages | 消息区 |
| Tab | focus_input | 全局 |
| /help | show_help | 输入区 |

### 3.4 事件流集成

Textual 控制台需要与现有 `RoleConsoleHost` 集成：

```python
# 伪代码
async def stream_events():
    async for event in host.stream_turn(...):
        if event.type == "debug":
            app.add_debug(
                category=event.category,
                label=event.label,
                payload=event.payload,
            )
        elif event.type == "content_chunk":
            app.add_content(event.content)
        # ...
```

---

## 4. 团队分工（6 人）

### 4.1 角色分配

| # | 角色 | 负责模块 | 技能要求 |
|----|------|----------|----------|
| 1 | Tech Lead | 架构设计 + 集成 | Textual 专家，5+ 年 |
| 2 | Senior Dev | 核心应用 + 事件流 | Python 异步，TUI |
| 3 | Senior Dev | 可折叠组件 + 动画 | CSS for TUI，UX |
| 4 | Mid Dev | 消息渲染器 | 数据可视化 |
| 5 | Mid Dev | 键盘/鼠标处理 | 事件系统 |
| 6 | Junior Dev | 测试 + 文档 | pytest，TDD |

### 4.2 任务分配

#### Tech Lead (角色 1) - 架构设计

| 任务 | 预估 | 依赖 |
|------|------|------|
| 设计整体架构 | 1d | - |
| 制定组件契约 | 0.5d | 设计 |
| Code Review | 2d | 2,3,4,5 |
| 集成测试 | 1d | 所有 |
| 性能优化 | 1d | 集成测试 |

#### Senior Dev (角色 2) - 核心应用

| 任务 | 预估 | 依赖 |
|------|------|------|
| PolarisTextualApp 骨架 | 1d | - |
| 布局系统 (Header/Body/Footer) | 1d | 骨架 |
| InputField 组件 | 1d | 布局 |
| 消息容器 + 滚动 | 1d | InputField |
| 事件流集成 | 2d | 消息容器 |
| 状态管理 | 1d | 事件流 |

#### Senior Dev (角色 3) - 可折叠组件

| 任务 | 预估 | 依赖 |
|------|------|------|
| CollapsibleWidget 基础 | 1d | - |
| 点击动画 | 0.5d | 基础 |
| 内容显示动画 | 1d | 基础 |
| CSS 样式系统 | 1d | 动画 |
| 嵌套折叠 | 1d | CSS |
| 折叠动画优化 | 0.5d | 嵌套 |

#### Mid Dev (角色 4) - 消息渲染器

| 任务 | 预估 | 依赖 |
|------|------|------|
| MessageRenderer 基类 | 0.5d | - |
| UserMessageWidget | 0.5d | 基类 |
| AssistantMessageWidget | 0.5d | 基类 |
| ToolResultWidget | 1d | 基类 |
| SyntaxHighlight (JSON) | 1d | ToolResult |
| Markdown 渲染 | 1d | Assistant |

#### Mid Dev (角色 5) - 键盘/鼠标处理

| 任务 | 预估 | 依赖 |
|------|------|------|
| 全局快捷键绑定 | 0.5d | - |
| 焦点管理 | 0.5d | 快捷键 |
| 鼠标点击检测 | 1d | 焦点 |
| 方向键导航 | 1d | 焦点 |
| 快捷键帮助面板 | 0.5d | 快捷键 |
| 命令历史 (↑/↓) | 1d | InputField |

#### Junior Dev (角色 6) - 测试 + 文档

| 任务 | 预估 | 依赖 |
|------|------|------|
| 单元测试框架 | 0.5d | - |
| 组件测试 | 2d | 1-5 |
| 集成测试 | 1d | 事件流 |
| E2E 测试 | 1d | 集成 |
| 使用文档 | 0.5d | E2E |
| API 文档 | 0.5d | 代码 |

---

## 5. 实施计划

### 5.1 Sprint 1: 基础框架 (5 天)

**目标**: 可运行的 Textual 应用骨架

| Day | 角色 1 | 角色 2 | 角色 3 | 角色 4 | 角色 5 | 角色 6 |
|-----|---------|---------|---------|---------|---------|---------|
| 1 | 架构设计 | 骨架 | - | - | - | - |
| 2 | 组件契约 | 布局 | - | - | - | - |
| 3 | Review | InputField | CollapsibleWidget | - | - | - |
| 4 | Review | 消息容器 | 基础动画 | Message基类 | 快捷键 | - |
| 5 | Review | 滚动 | CSS | UserWidget | 焦点 | 单元测试 |

**验收**: 运行 `python -m polaris.delivery.cli chat --backend textual` 显示空界面

### 5.2 Sprint 2: 核心功能 (5 天)

**目标**: DEBUG 折叠 + 事件流

| Day | 角色 1 | 角色 2 | 角色 3 | 角色 4 | 角色 5 | 角色 6 |
|-----|---------|---------|---------|---------|---------|---------|
| 6 | 事件契约 | - | 嵌套折叠 | - | - | - |
| 7 | - | 事件流集成 | 动画优化 | ToolWidget | 鼠标点击 | 组件测试 |
| 8 | Review | - | - | JSON Highlight | - | - |
| 9 | Review | 状态管理 | - | Markdown | 命令历史 | 集成测试 |
| 10 | 优化 | - | - | - | - | E2E |

**验收**: `--debug` 时 DEBUG 消息默认折叠，点击可展开

### 5.3 Sprint 3: 完善与优化 (5 天)

**目标**: 生产就绪

| Day | 角色 1 | 角色 2 | 角色 3 | 角色 4 | 角色 5 | 角色 6 |
|-----|---------|---------|---------|---------|---------|---------|
| 11 | 性能分析 | 优化 | - | - | - | - |
| 12 | - | - | 视觉优化 | 样式调整 | 快捷键帮助 | 测试 |
| 13 | Code Review | Bug Fix | Bug Fix | Bug Fix | Bug Fix | - |
| 14 | - | - | - | - | - | 文档 |
| 15 | 最终 Review | 集成测试 | - | - | - | 发布 |

**验收**: 所有测试通过，性能达标，文档完整

---

## 6. 技术规格

### 6.1 依赖

```txt
# requirements-cli.txt
textual>=0.8.0
textual-dev>=1.0.0  # 开发依赖
```

### 6.2 Python 版本

- Python 3.11+
- 类型提示 (strict mode)

### 6.3 测试覆盖

| 模块 | 覆盖率目标 |
|------|-------------|
| textual_console.py | 90%+ |
| 组件测试 | 85%+ |
| 集成测试 | 80%+ |

### 6.4 性能要求

| 指标 | 目标 |
|------|------|
| 冷启动 | < 2s |
| 1000 条 DEBUG | < 100ms 渲染 |
| 内存占用 | < 200MB |

---

## 7. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| Textual 版本兼容性 | 高 | 低 | 固定版本，CI 测试 |
| 事件流集成复杂 | 中 | 中 | Tech Lead 介入设计 |
| 性能问题 | 中 | 低 | Sprint 3 专门优化 |
| 跨平台差异 | 中 | 中 | CI 多平台测试 |

---

## 8. 成功标准

### 8.1 功能验收

- [ ] `--backend textual --debug` 启动正常
- [ ] DEBUG 消息默认折叠
- [ ] 鼠标点击 `[▶]` 展开
- [ ] 鼠标点击 `[▼]` 折叠
- [ ] Alt+D 切换所有 DEBUG
- [ ] 键盘方向键导航正常
- [ ] 与 LLM 事件流正常集成

### 8.2 质量验收

- [ ] 所有单元测试通过
- [ ] 集成测试通过
- [ ] 性能达标
- [ ] 文档完整

### 8.3 用户验收

- [ ] 新用户 < 5 分钟上手
- [ ] 鼠标交互直观
- [ ] 与 `--backend plain` 体验一致

---

## 9. 文件清单

### 9.1 新增文件

```
polaris/delivery/cli/
├── textual_console.py           # 主应用 (角色 2)
├── textual/
│   ├── __init__.py
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── collapsible.py      # 可折叠组件 (角色 3)
│   │   ├── message.py          # 消息组件 (角色 4)
│   │   └── input.py            # 输入组件 (角色 2)
│   ├── bindings.py             # 快捷键绑定 (角色 5)
│   └── styles.py                # 样式定义 (角色 3)
└── test_textual_console.py      # 测试 (角色 6)
```

### 9.2 修改文件

```
polaris/delivery/cli/
├── polaris_cli.py              # 添加 --backend textual
├── terminal_console.py         # 可选：添加集成点
└── docs/
    └── textual_console.md      # 用户文档
```

---

**文档结束**
