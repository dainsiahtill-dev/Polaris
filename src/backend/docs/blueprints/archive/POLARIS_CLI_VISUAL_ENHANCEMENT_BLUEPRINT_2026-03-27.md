# Polaris CLI 可视化增强蓝图 v1.1
**文档版本**: 1.1.0
**创建日期**: 2026-03-27
**更新日期**: 2026-03-27
**目标**: `polaris/delivery/cli` - CLI 会话可视化与 Diff View 增强
**状态**: 待实施

---

## 1. 审计摘要

### 1.1 需求来源
用户在 `polaris/delivery/cli` 中需要以下能力：
1. **可折叠/可展开的会话或信息展示**（每条信息，不是每行）
2. **Diff View 用于展示代码变更（新增/修改/删除）**
3. **DEBUG 信息可折叠**

### 1.2 当前状态

| 项目 | 当前状态 | 备注 |
|------|----------|------|
| CLI 框架 | `argparse` (stdlib) | 主框架 |
| 富文本库 | `rich>=0.50.0` | 已存在，未充分利用 |
| TUI 框架 | `textual>=0.50.0` | 已存在，toad/ 使用 |
| Diff 能力 | 无 | 需新增 |
| 折叠能力 | 无 | 需新增 |

### 1.3 技术选型

| 方案 | 库依赖 | 复杂度 | 推荐度 |
|------|--------|--------|--------|
| Rich (推荐) | `rich` | 低 | ⭐⭐⭐⭐⭐ |
| Textual TUI | `textual` | 高 | ⭐⭐⭐ |
| 纯 ASCII | 无 | 最低 | ⭐⭐⭐ |

**决策**: 采用 Rich 作为主要渲染引擎，支持交互式折叠与专业 Diff View。

---

## 2. 功能需求

### 2.1 可折叠/可展开信息展示

#### 2.1.0 核心原则：每条信息都可折叠

> **重要**: 折叠粒度为**信息/消息级别**，不是行级别。任何独立的信息单元都应支持折叠。

**信息类型全覆盖**:
| 类型 | 描述 | 默认状态 | 可配置 |
|------|------|----------|--------|
| 用户消息 | User 输入 | 展开 | ✅ |
| Assistant 响应 | AI 回复 | 展开 | ✅ |
| Thinking | AI 思考过程 | 折叠 | ✅ |
| Tool Call | 工具调用 | 折叠 | ✅ |
| Tool Result | 工具结果 | 折叠 | ✅ |
| **DEBUG** | **调试信息** | **折叠** | **✅** |
| System | 系统消息 | 折叠 | ✅ |
| Error | 错误信息 | 展开 | ✅ |
| Metadata | 元信息 (token等) | 折叠 | ✅ |

#### 2.1.1 核心场景

| 场景 | 描述 | 优先级 |
|------|------|--------|
| 会话折叠 | 折叠历史会话，仅显示摘要 | P0 |
| 消息折叠 | 折叠长消息内容，hover/click 展开 | P0 |
| 工具调用折叠 | 折叠工具调用详情，默认显示名称 | P0 |
| 元数据折叠 | 折叠 Token 统计、时间戳等元信息 | P1 |
| Thinking 折叠 | 折叠 AI 思考过程 | P1 |

#### 2.1.2 交互模式

```
# 折叠状态
[▶] 会话 #123: "实现用户登录功能"
    用户: /login --username admin
    🤖: [Thinking... 点击展开] 

# 展开状态
[▼] 会话 #123: "实现用户登录功能"
    用户: /login --username admin
    🤖: 分析需求中...
        - 检查用户表结构
        - 验证密码加密方式
        - 生成登录接口
```

#### 2.1.3 DEBUG 信息折叠

> **关键需求**: DEBUG 级别信息默认折叠，需要时展开查看。

```python
# DEBUG 信息渲染示例
[▶] [DEBUG] kernelone/fs: 操作耗时 12ms
    # 展开后显示完整调用栈
    File "polaris/kernelone/fs/storage.py", line 45
    └─ read_file(path="/tmp/cache.json")
       └─ _fetch_from_cache() -> hit
```

**DEBUG 信息特征**:
- 带有 `[DEBUG]` 标签
- 包含时间戳、模块路径、操作耗时
- 可能包含长调用栈或数据 dump

**交互行为**:
| 操作 | 效果 |
|------|------|
| 单击/回车 | 展开当前 DEBUG |
| `Ctrl+D` | 展开所有 DEBUG |
| `Ctrl+Shift+D` | 折叠所有 DEBUG |
| 右键菜单 | 复制 / 永久展开 / 加入白名单 |

#### 2.1.4 信息级别折叠树

```python
# 完整的折叠层级（消息级折叠，非内容内嵌）
[▼] 会话 #123
    ├── [▼] [USER] /login --username admin
    │       # 默认展开，内容较短
    │
    ├── [▶] [THINKING] 分析中...
    │       # 默认折叠
    │
    ├── [▶] [TOOL] git_diff
    │       # 默认折叠
    │
    ├── [▶] [DEBUG] HTTP 请求完成
    │       # 默认折叠，内容不直接显示
    │       # 展开后显示:
    │       #   status: 200
    │       #   latency: 45ms
    │       #   headers: {...}
    │
    └── [▶] [ASSISTANT] 登录功能已实现
            # 默认展开
```

**注意**: DEBUG 内容是消息内容的一部分，不是子消息节点。
```

#### 2.1.5 键盘/鼠标交互

| 操作 | 键盘 | 鼠标 |
|------|------|------|
| 展开/折叠单个 | `→` / `←` 或 `Space` | Click |
| 展开/折叠全部 | `Ctrl+[` / `Ctrl+]` | - |
| 折叠层级 | `1-9` 数字键 | - |
| **展开所有 DEBUG** | **`Ctrl+D`** | - |
| **折叠所有 DEBUG** | **`Ctrl+Shift+D`** | - |
| **展开/折叠当前类型** | **`Ctrl+T`** (Thinking) | - |
| 永久展开当前 | `Ctrl+Enter` | - |

### 2.2 Diff View 展示

#### 2.2.1 变更类型

| 类型 | 标记 | 颜色 |
|------|------|------|
| 新增 | `+` | Green |
| 删除 | `-` | Red |
| 修改 | `~` | Yellow |
| 重命名 | `@` | Blue |

#### 2.2.2 Diff 格式

```
diff --git a/src/auth/login.py b/src/auth/login.py
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -10,7 +10,9 @@ def authenticate(username: str, password: str) -> bool:
-    hashed = hashlib.md5(password.encode()).hexdigest()
+    hashed = hashlib.sha256(password.encode()).hexdigest()
+    # TODO: 改用 bcrypt
  return check_password(hashed, stored_hash)
```

#### 2.2.3 Side-by-Side 模式

```
┌─ src/auth/login.py ──────────────────────────────────┐
│ FILE 1                    │ FILE 2                     │
├───────────────────────────┼───────────────────────────┤
│ def authenticate(...):    │ def authenticate(...):    │
│     ...                   │     ...                    │
│ -    hashed = md5(...)    │ +    hashed = sha256(...)  │
│ +    # TODO: bcrypt       │ +    # TODO: bcrypt        │
└───────────────────────────┴───────────────────────────┘
```

---

## 3. 架构设计

### 3.1 模块结构

```
polaris/delivery/cli/
├── visualization/                    # 新增可视化模块
│   ├── __init__.py
│   ├── collapsible.py              # 可折叠组件
│   ├── diff_view.py                 # Diff View 渲染器
│   ├── rich_console.py              # Rich Console 封装
│   ├── render_context.py            # 渲染上下文
│   ├── theme.py                     # 主题配置
│   └── contracts.py                 # 可视化契约接口
├── terminal_console.py              # 改造：集成可视化
├── director/
│   └── console_host.py              # 改造：集成 Diff
└── ...
```

### 3.2 核心类设计

#### 3.2.1 MessageItem (核心：每条信息可折叠)

```python
from enum import Enum, auto
from dataclasses import dataclass, field

class MessageType(Enum):
    """信息类型枚举"""
    USER = auto()
    ASSISTANT = auto()
    THINKING = auto()
    TOOL_CALL = auto()
    TOOL_RESULT = auto()
    DEBUG = auto()       # DEBUG 信息
    SYSTEM = auto()
    ERROR = auto()
    METADATA = auto()

@dataclass
class MessageItem:
    """可折叠的信息单元（消息级别，非行级别）"""
    id: str
    type: MessageType
    title: str                    # 简短标题/摘要
    content: str | Renderable     # 完整内容
    is_collapsed: bool = field(
        default_factory=lambda: True  # DEBUG/THINKING 默认折叠
    )
    timestamp: datetime | None = None
    metadata: dict[str, Any] | None = None
    children: list[MessageItem] = field(default_factory=list)  # 嵌套信息

    # 类型特定的默认折叠策略
    DEFAULT_COLLAPSE_MAP: ClassVar[dict[MessageType, bool]] = {
        MessageType.USER: False,          # 用户消息默认展开
        MessageType.ASSISTANT: False,      # 回复默认展开
        MessageType.THINKING: True,        # Thinking 默认折叠
        MessageType.TOOL_CALL: True,       # 工具调用默认折叠
        MessageType.TOOL_RESULT: True,     # 工具结果默认折叠
        MessageType.DEBUG: True,           # DEBUG 默认折叠 ← 关键需求
        MessageType.SYSTEM: True,          # 系统消息默认折叠
        MessageType.ERROR: False,          # 错误默认展开
        MessageType.METADATA: True,       # 元信息默认折叠
    }

    def expand(self) -> None: ...
    def collapse(self) -> None: ...
    def toggle(self) -> None: ...
    def is_leaf(self) -> bool: ...
    def get_default_collapse(self) -> bool: ...

@dataclass
class CollapsibleMessageGroup:
    """消息组容器"""
    id: str
    items: list[MessageItem]
    collapsed_by_type: dict[MessageType, bool] = field(default_factory=dict)

    def expand_all(self) -> None: ...
    def collapse_all(self) -> None: ...
    def expand_by_type(self, msg_type: MessageType) -> None: ...
    def collapse_by_type(self, msg_type: MessageType) -> None: ...
```

#### 3.2.2 CollapsibleItem (通用折叠组件)

#### 3.2.3 DiffView

```python
@dataclass
class DiffHunk:
    """Diff 块"""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine]

@dataclass
class DiffLine:
    """Diff 行"""
    line_type: Literal["add", "delete", "context", "header"]
    content: str
    old_line_no: int | None
    new_line_no: int | None

class DiffView:
    """Diff View 渲染器"""
    
    def render_unified(self, hunks: list[DiffHunk]) -> Panel: ...
    def render_side_by_side(self, hunks: list[DiffHunk]) -> Table: ...
    def render_stat(self, stats: DiffStats) -> Text: ...
```

#### 3.2.3 VisualizationContext

```python
@dataclass
class VisualizationContext:
    """渲染上下文"""
    console: Console
    theme: ConsoleTheme
    collapse_state: dict[str, bool]
    diff_mode: Literal["unified", "side-by-side"]
    
    def render(self, item: CollapsibleItem | DiffView) -> Renderable: ...
```

---

## 4. 实施计划

### 4.1 Phase 1: 基础渲染层 (2天)

**目标**: 构建 Rich 封装和主题系统

| 任务 | Owner | 验收标准 |
|------|-------|----------|
| RichConsole 封装 | Dev-1 | 支持 UTF-8、自适应宽度、主题切换 |
| ConsoleTheme 配置 | Dev-1 | 定义折叠/Diff 颜色主题 |
| Renderable 契约 | Dev-1 | 统一渲染接口 |

### 4.2 Phase 2: 可折叠组件 (3天)

**目标**: 实现可折叠信息展示

| 任务 | Owner | 验收标准 |
|------|-------|----------|
| CollapsibleItem 实现 | Dev-2 | 支持嵌套折叠 |
| CollapsibleGroup 管理 | Dev-2 | 批量展开/折叠 |
| 键盘交互处理 | Dev-2 | 支持 Space/方向键 |
| 与 terminal_console 集成 | Dev-3 | 现有会话流集成 |

### 4.3 Phase 3: Diff View (3天)

**目标**: 实现专业代码 Diff 展示

| 任务 | Owner | 验收标准 |
|------|-------|----------|
| Diff 解析器 | Dev-4 | 支持 unified diff 格式 |
| Unified 渲染 | Dev-4 | 单列 Diff 显示 |
| Side-by-Side 渲染 | Dev-5 | 双列 Diff 显示 |
| 语法高亮集成 | Dev-4 | 使用已有 syntax 模块 |

### 4.4 Phase 4: 集成与测试 (2天)

**目标**: 全流程集成与验收

| 任务 | Owner | 验收标准 |
|------|-------|----------|
| director/console_host 集成 | Dev-6 | Diff 结果展示 |
| 工具调用折叠集成 | Dev-6 | 工具详情折叠 |
| E2E 测试 | Dev-7 | 完整交互流程测试 |
| 回归测试 | Dev-7 | 现有功能无回归 |

---

## 5. API 契约

### 5.1 公共接口

```python
# polaris/delivery/cli/visualization/contracts.py

from typing import Protocol, Renderable
from dataclasses import dataclass
from enum import Enum

class RenderMode(Enum):
    COLLAPSED = "collapsed"
    EXPANDED = "expanded"
    INTERACTIVE = "interactive"

class VisualizationContract(Protocol):
    """可视化契约接口"""
    
    def render(self, mode: RenderMode = RenderMode.INTERACTIVE) -> Renderable:
        """渲染可视化内容"""
        ...
    
    def get_fold_state(self, item_id: str) -> bool:
        """获取折叠状态"""
        ...
    
    def set_fold_state(self, item_id: str, collapsed: bool) -> None:
        """设置折叠状态"""
        ...
```

### 5.2 使用示例

```python
from polaris.delivery.cli.visualization import (
    CollapsibleItem,
    DiffView,
    VisualizationContext,
)

# 可折叠会话
session = CollapsibleItem(
    id="session-123",
    title="会话 #123: 实现登录",
    content=Text("完整会话内容..."),
    is_collapsed=True,
)

# Diff View
diff = DiffView.from_unified(diff_text)
console.print(diff.render_unified())

# 组合使用
ctx = VisualizationContext(console=console)
ctx.render(session)
```

---

## 6. 验收标准

### 6.1 可折叠功能

- [ ] **每条信息可折叠**（消息级别，非行级别）
- [ ] 单项折叠/展开正常
- [ ] 嵌套折叠正确处理
- [ ] 键盘交互响应
- [ ] 鼠标点击交互响应
- [ ] 状态持久化（会话恢复）
- [ ] 无内容时不显示折叠标记
- [ ] **DEBUG 信息默认折叠** ← 关键需求
- [ ] **Ctrl+D 展开所有 DEBUG**
- [ ] **Ctrl+Shift+D 折叠所有 DEBUG**
- [ ] 按类型折叠/展开支持
- [ ] DEBUG 信息包含完整调用栈展开

### 6.2 Diff View 功能

- [ ] 新增行绿色标记
- [ ] 删除行红色标记
- [ ] 修改行黄色标记
- [ ] 行号正确显示
- [ ] 语法高亮正确
- [ ] Unified 模式正确
- [ ] Side-by-Side 模式正确
- [ ] 大文件性能可接受（<10000行）

### 6.3 集成要求

- [ ] 与现有 terminal_console 兼容
- [ ] 主题切换正常
- [ ] UTF-8 内容正确渲染
- [ ] ANSI 颜色正确解析
- [ ] 回退到纯文本（无 Rich 环境）

---

## 7. 风险与边界

### 7.1 风险识别

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Rich 版本兼容性 | 中 | 固定版本 >=0.50.0 |
| 超大内容渲染性能 | 高 | 虚拟化 + 懒加载 |
| 终端宽度自适应 | 中 | 检测终端宽度，回退策略 |
| 嵌套层级过深 | 低 | 限制最大层级为 10 |

### 7.2 边界条件

| 场景 | 处理方式 |
|------|----------|
| 空内容 | 隐藏折叠标记 |
| 超长内容 | 截断 + "..." + 展开提示 |
| 二进制内容 | 显示占位符 "[Binary]" |
| 非 UTF-8 内容 | 尝试解码，失败显示 hex |
| 终端宽度 < 80 | 切换到紧凑模式 |

---

## 8. 测试策略

### 8.1 单元测试

```python
# tests/delivery/cli/visualization/
def test_message_item_fold():
    """测试每条信息（消息级别）可折叠"""
    msg = MessageItem(
        id="msg-1",
        type=MessageType.DEBUG,
        title="HTTP 请求完成",
        content="status: 200, latency: 45ms...",
    )
    # DEBUG 默认折叠
    assert msg.is_collapsed
    msg.expand()
    assert not msg.is_collapsed

def test_debug_default_collapsed():
    """测试 DEBUG 信息默认折叠"""
    debug_msg = MessageItem(
        id="debug-1",
        type=MessageType.DEBUG,
        title="Kernel 操作",
        content="完整调用栈...",
    )
    assert debug_msg.is_collapsed
    assert debug_msg.get_default_collapse() == True

def test_user_message_default_expanded():
    """测试用户消息默认展开"""
    user_msg = MessageItem(
        id="user-1",
        type=MessageType.USER,
        title="/login",
        content="用户输入内容",
    )
    assert not user_msg.is_collapsed

def test_fold_by_type():
    """测试按类型批量折叠"""
    group = CollapsibleMessageGroup(
        id="session-1",
        items=[
            MessageItem(id="1", type=MessageType.DEBUG, title="D1", content="..."),
            MessageItem(id="2", type=MessageType.DEBUG, title="D2", content="..."),
            MessageItem(id="3", type=MessageType.USER, title="U1", content="..."),
        ]
    )
    group.collapse_by_type(MessageType.DEBUG)
    assert group.items[0].is_collapsed
    assert group.items[1].is_collapsed
    assert not group.items[2].is_collapsed  # USER 不受影响

def test_collapsible_expand_collapse():
    item = CollapsibleItem(id="1", title="Test", content=Text("content"))
    assert item.is_collapsed
    item.toggle()
    assert not item.is_collapsed

def test_diff_line_classification():
    diff = "+ new line\n- old line\n  context"
    lines = parse_diff_lines(diff)
    assert lines[0].type == "add"
    assert lines[1].type == "delete"
    assert lines[2].type == "context"
```

### 8.2 集成测试

```python
def test_console_integration():
    console = RichConsole()
    item = CollapsibleItem(...)
    console.print(item.render())
    # 验证输出不抛异常
```

### 8.3 E2E 测试

```python
def test_terminal_console_fold():
    # 启动 CLI
    # 发送折叠命令
    # 验证输出格式
```

---

## 9. 后续优化

### 9.1 V2 特性

- [ ] 增量 Diff（对比两个版本）
- [ ] 折叠历史记录
- [ ] 搜索 + 高亮
- [ ] 自定义折叠规则

### 9.2 性能优化

- [ ] 大内容虚拟化渲染
- [ ] Diff 计算缓存
- [ ] 并行语法高亮

---

## 10. 参考资料

- [Rich 文档](https://rich.readthedocs.io/)
- [Rich Tree](https://rich.readthedocs.io/en/stable/tree.html)
- [Rich Panel](https://rich.readthedocs.io/en/stable/panel.html)
- [Textual Collapsible](https://textual.textualize.io/widgets/collapsible/)
