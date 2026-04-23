# CLI 可视化增强 - 缺陷修复蓝图 v1.0
**文档版本**: 1.0.0
**创建日期**: 2026-03-27
**蓝图**: `CLI_VISUAL_AUDIT_REPORT_2026-03-27.md`
**状态**: 待实施

---

## 1. 修复摘要

### 1.1 修复范围

| 严重程度 | 数量 | 状态 |
|----------|------|------|
| CRITICAL | 5 | 阻塞问题，必须立即修复 |
| MAJOR | 7 | 功能缺陷，应优先修复 |
| MINOR | 4 | 代码质量，建议修复 |

### 1.2 修复负责人

| 问题类别 | 负责人 | 工时 |
|----------|--------|------|
| 蓝图文档修复 | Dev-Lead (李工) | 1h |
| 核心组件修复 (C1, C2, C4, M2, M3, m1) | Dev-2 (王工) | 6h |
| 基础设施修复 (M4, M7, m2, m3) | Dev-1 (张工) | 3h |
| Diff 组件修复 (M1, M6) | Dev-4 (陈工) | 3h |
| 测试修复 (M5) | Dev-7 (吴工) | 1h |

---

## 2. CRITICAL 修复

### 2.1 C1: MessageItem 默认折叠逻辑重构

**目标**: 修复 `is_collapsed` 默认值逻辑错误

**问题**: `default_factory=lambda: True` 导致所有类型都默认折叠

**修复代码**:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Union, ClassVar

# visualization/message_item.py

class CollapsibleError(Exception):
    """可折叠组件基础异常"""
    pass


class MaxLevelExceeded(CollapsibleError):
    """超过最大嵌套层级"""
    
    def __init__(self, max_level: int, actual_level: int) -> None:
        self.max_level = max_level
        self.actual_level = actual_level
        super().__init__(
            f"Maximum nesting level {max_level} exceeded, got {actual_level}"
        )


class FoldStateError(CollapsibleError):
    """折叠状态操作错误"""
    pass


@dataclass
class MessageItem:
    """可折叠的信息单元（消息级别，非行级别）
    
    每个 MessageItem 代表一条独立的、可折叠的消息。
    根据类型自动决定默认折叠状态。
    
    Attributes:
        id: 消息唯一标识
        type: 消息类型
        title: 简短标题/摘要
        content: 完整内容
        is_collapsed: 折叠状态，None 表示使用类型默认值
        timestamp: 时间戳
        metadata: 元数据
        children: 子消息列表
    """
    
    MAX_NESTING_LEVEL: ClassVar[int] = 10
    
    id: str
    type: MessageType
    title: str
    content: str
    is_collapsed: Union[bool, None] = None  # None = 使用类型默认值
    timestamp: Union[datetime, None] = None
    metadata: Union[dict[str, Any], None] = None
    children: list[MessageItem] = field(default_factory=list)
    
    # 类型特定的默认折叠策略
    DEFAULT_COLLAPSE_MAP: ClassVar[dict[MessageType, bool]] = {
        MessageType.USER: False,          # 用户消息默认展开
        MessageType.ASSISTANT: False,     # 回复默认展开
        MessageType.THINKING: True,       # Thinking 默认折叠
        MessageType.TOOL_CALL: True,      # 工具调用默认折叠
        MessageType.TOOL_RESULT: True,    # 工具结果默认折叠
        MessageType.DEBUG: True,          # DEBUG 默认折叠 ← 关键需求
        MessageType.SYSTEM: True,        # 系统消息默认折叠
        MessageType.ERROR: False,         # 错误默认展开
        MessageType.METADATA: True,      # 元信息默认折叠
    }
    
    def __post_init__(self) -> None:
        """后处理：根据类型设置默认折叠状态"""
        if self.is_collapsed is None:
            self.is_collapsed = self.DEFAULT_COLLAPSE_MAP.get(
                self.type, 
                False
            )
    
    @property
    def effective_collapsed(self) -> bool:
        """获取实际折叠状态（考虑嵌套层级）"""
        return self._calculate_depth() > self.MAX_NESTING_LEVEL or self.is_collapsed
    
    def _calculate_depth(self) -> int:
        """计算嵌套深度"""
        if not self.children:
            return 1
        return 1 + max(
            (child._calculate_depth() for child in self.children),
            default=0
        )
    
    def expand(self) -> None:
        """展开当前项"""
        if self.effective_collapsed:
            self.is_collapsed = False
    
    def collapse(self) -> None:
        """折叠当前项"""
        if not self.effective_collapsed:
            self.is_collapsed = True
    
    def toggle(self) -> None:
        """切换折叠状态"""
        self.is_collapsed = not self.is_collapsed
    
    def is_leaf(self) -> bool:
        """是否为叶子节点"""
        return len(self.children) == 0
    
    def get_default_collapse(self) -> bool:
        """获取类型的默认折叠状态"""
        return self.DEFAULT_COLLAPSE_MAP.get(self.type, False)
    
    def add_child(self, child: MessageItem) -> None:
        """添加子消息
        
        Args:
            child: 子消息
            
        Raises:
            TypeError: child 不是 MessageItem 类型
            MaxLevelExceeded: 超过最大嵌套层级
        """
        if not isinstance(child, MessageItem):
            raise TypeError(
                f"Expected MessageItem, got {type(child).__name__}"
            )
        
        # 检查嵌套深度
        total_depth = self._calculate_depth() + child._calculate_depth()
        if total_depth > self.MAX_NESTING_LEVEL:
            raise MaxLevelExceeded(
                self.MAX_NESTING_LEVEL,
                total_depth
            )
        
        self.children.append(child)
```

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
    # is_collapsed=None 时，应自动设置为类型默认值
    assert msg.is_collapsed is True, "DEBUG should be collapsed by default"
    assert msg.get_default_collapse() is True

def test_user_default_expanded():
    """用户消息默认展开"""
    msg = MessageItem(
        id="user-1",
        type=MessageType.USER,
        title="/login",
        content="用户输入内容",
    )
    assert msg.is_collapsed is False, "USER should be expanded by default"

def test_explicit_override():
    """显式设置覆盖默认值"""
    msg = MessageItem(
        id="debug-1",
        type=MessageType.DEBUG,
        title="永久展开的 DEBUG",
        content="...",
        is_collapsed=False,  # 显式覆盖
    )
    assert msg.is_collapsed is False

def test_nested_max_level():
    """嵌套层级超限"""
    parent = MessageItem(id="p", type=MessageType.USER, title="P", content="")
    
    # 创建深度为 10 的嵌套
    current = parent
    for i in range(9):
        child = MessageItem(
            id=f"c{i}", 
            type=MessageType.DEBUG, 
            title=f"C{i}", 
            content=""
        )
        current.add_child(child)
        current = child
    
    # 再添加一个会超过限制
    over_child = MessageItem(
        id="over", 
        type=MessageType.DEBUG, 
        title="Over", 
        content=""
    )
    
    with pytest.raises(MaxLevelExceeded) as exc_info:
        current.add_child(over_child)
    
    assert exc_info.value.max_level == 10
    assert exc_info.value.actual_level == 11
```

---

### 2.2 C2: 快捷键冲突修复

**目标**: 替换与终端控制字符冲突的快捷键

**问题**: `Ctrl+D` 在 Unix 终端中是 EOF 信号

**修复方案**:

```python
# visualization/keyboard.py

from enum import Enum, auto

class FoldShortcut(Enum):
    """折叠相关快捷键
    
    避免使用终端控制字符：
    - Ctrl+C: SIGINT (中断)
    - Ctrl+D: EOF
    - Ctrl+Z: SIGTSTP (挂起)
    - Ctrl+\\: SIGQUIT
    """
    
    # 按类型折叠/展开 (使用 Alt 键，不常用)
    EXPAND_ALL_DEBUG = "alt+d"        # Alt+D: 展开所有 DEBUG
    COLLAPSE_ALL_DEBUG = "alt+shift+d"  # Alt+Shift+D: 折叠所有 DEBUG
    EXPAND_ALL_THINKING = "alt+t"      # Alt+T: 展开所有 THINKING
    COLLAPSE_ALL_THINKING = "alt+shift+t"
    
    # 通用操作
    TOGGLE_CURRENT = "space"           # Space: 切换当前项
    EXPAND_ALL = "ctrl+alt+["          # Ctrl+Alt+[: 展开全部
    COLLAPSE_ALL = "ctrl+alt+]"        # Ctrl+Alt+]: 折叠全部
    PERMANENT_EXPAND = "ctrl+enter"    # Ctrl+Enter: 永久展开
    
    # 层级导航
    FOLD_TO_LEVEL = "digit"            # 1-9: 折叠到指定层级

# E2E 测试修复
def test_e2e_debug_fold():
    """测试 DEBUG 信息折叠流程"""
    process = subprocess.Popen(
        ["python", "-m", "polaris.delivery.cli"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    # 使用 Alt+D 展开所有 DEBUG (不是 Ctrl+D)
    # 在 curses/textual 中需要发送 ANSI Escape 序列
    ALT_D = b"\x1b[d"  # Alt+D 的 ANSI 序列
    
    process.stdin.write(b"test command\n")
    process.stdin.write(ALT_D)
    process.stdin.flush()
    
    # 验证 DEBUG 展开
    output = process.stdout.read()
    assert "[DEBUG]" in output
    
    process.terminate()
```

**验收标准**:
```python
def test_shortcut_no_conflict():
    """快捷键不与终端控制字符冲突"""
    for shortcut in FoldShortcut:
        key_combo = shortcut.value.lower()
        
        # 检查不包含冲突键
        assert "ctrl+c" not in key_combo
        assert "ctrl+d" not in key_combo
        assert "ctrl+z" not in key_combo
        assert "ctrl+\\" not in key_combo
        
        # 允许的组合
        assert "alt+" in key_combo or "space" in key_combo or "digit" in key_combo
```

---

### 2.3 C3: 任务矩阵 ID 去重

**修复**: 删除 `TEAM_PLAN.md` 行 565-574 的重复任务

---

### 2.4 C4: 折叠树示例修复

**修复**: 修正文档中的折叠树示例

```python
# 修复前（错误）
[▼] [DEBUG] HTTP 请求完成
    ├── status: 200
    ├── latency: 45ms
    └── headers: {...}

# 修复后（正确）
[▼] [DEBUG] HTTP 请求完成
    status: 200
    latency: 45ms
    headers: {...}
```

---

### 2.5 C5: Markdown 标题去重

**修复**: 
```markdown
#### 3.2.2 CollapsibleItem (通用折叠组件)

#### 3.2.3 DiffView
```

---

## 3. MAJOR 修复

### 3.1 M1: Diff 组件修复

```python
# visualization/diff_parser.py

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

@dataclass
class DiffLine:
    """Diff 行
    
    Attributes:
        line_type: 行类型 (add/delete/modify/context/header)
        content: 原始内容
        old_line_no: 原始文件行号（删除/上下文行）
        new_line_no: 新文件行号（新增/上下文行）
        is_modify_pair: 是否与相邻行组成修改对
    """
    
    line_type: Literal["add", "delete", "modify", "context", "header"]
    content: str
    old_line_no: Union[int, None] = None
    new_line_no: Union[int, None] = None
    is_modify_pair: bool = False


def parse_diff_lines(diff_text: str) -> list[DiffLine]:
    """解析 unified diff 文本为行列表
    
    Args:
        diff_text: unified diff 格式文本
        
    Returns:
        DiffLine 对象列表
        
    Raises:
        ValueError: diff 格式无效
        
    Example:
        >>> lines = parse_diff_lines("+ new line\\n- old line\\n  context")
        >>> lines[0].line_type
        'add'
    """
    lines: list[DiffLine] = []
    pending_delete: Union[DiffLine, None] = None
    
    for line in diff_text.splitlines():
        if not line:
            continue
            
        prefix = line[0]
        content = line[1:] if len(line) > 1 else ""
        
        if prefix == "+":
            diff_line = DiffLine(
                line_type="add",
                content=content,
                new_line_no=_extract_line_no(content),
            )
            lines.append(diff_line)
            
        elif prefix == "-":
            diff_line = DiffLine(
                line_type="delete",
                content=content,
                old_line_no=_extract_line_no(content),
            )
            pending_delete = diff_line
            lines.append(diff_line)
            
        elif prefix == " ":
            # 上下文行，检查是否与待处理的删除行组成修改对
            if pending_delete is not None:
                pending_delete.is_modify_pair = True
            pending_delete = None
            
            diff_line = DiffLine(
                line_type="context",
                content=content,
                old_line_no=_extract_line_no(content),
                new_line_no=_extract_line_no(content),
            )
            lines.append(diff_line)
            
        elif line.startswith("@@"):
            # hunk 头
            lines.append(DiffLine(
                line_type="header",
                content=line,
            ))
            pending_delete = None
            
        elif line.startswith("diff ") or line.startswith("---") or line.startswith("+++"):
            # 文件头
            lines.append(DiffLine(
                line_type="header",
                content=line,
            ))
    
    return lines


def _extract_line_no(content: str) -> Union[int, None]:
    """从内容中提取行号（如果存在）"""
    import re
    match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", content)
    if match:
        return int(match.group(1))
    return None
```

---

### 3.2 M4: 模块结构修复

```python
# visualization/__init__.py

"""Polaris CLI 可视化增强模块

提供：
- 消息级折叠 (MessageItem)
- 通用折叠组件 (CollapsibleItem)
- Diff View 渲染器 (DiffView)
- Rich Console 封装 (RichConsole)
"""

from visualization.message_item import (
    MessageItem,
    MessageType,
    CollapsibleMessageGroup,
    CollapsibleError,
    FoldStateError,
    MaxLevelExceeded,
)
from visualization.collapsible import CollapsibleItem, CollapsibleGroup
from visualization.diff_view import DiffView, DiffHunk, DiffLine
from visualization.rich_console import RichConsole
from visualization.theme import ConsoleTheme
from visualization.contracts import (
    VisualizationContext,
    RenderMode,
    VisualizationContract,
)

__all__ = [
    # 消息级折叠
    "MessageItem",
    "MessageType", 
    "CollapsibleMessageGroup",
    "CollapsibleError",
    "FoldStateError",
    "MaxLevelExceeded",
    # 通用折叠
    "CollapsibleItem",
    "CollapsibleGroup",
    # Diff
    "DiffView",
    "DiffHunk",
    "DiffLine",
    # 渲染
    "RichConsole",
    "ConsoleTheme",
    "VisualizationContext",
    "RenderMode",
    "VisualizationContract",
]
```

---

### 3.3 M7: 类型注解兼容性

所有文件顶部添加:
```python
from __future__ import annotations
```

或使用 Union 语法:
```python
from typing import Union, Optional, Dict, Any

content: Union[str, Renderable]
timestamp: Optional[datetime]
metadata: Optional[Dict[str, Any]]
```

---

## 4. MINOR 修复

### 4.1 m1: collapsed_by_type 初始化

```python
@dataclass
class CollapsibleMessageGroup:
    id: str
    items: list[MessageItem]
    collapsed_by_type: dict[MessageType, bool] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """初始化类型折叠状态映射"""
        # 填充所有类型的默认状态
        for msg_type in MessageType:
            if msg_type not in self.collapsed_by_type:
                default = MessageItem.DEFAULT_COLLAPSE_MAP.get(msg_type, False)
                self.collapsed_by_type[msg_type] = default
```

---

## 5. 修复验收清单

### 5.1 单元测试覆盖

```python
# tests/delivery/cli/visualization/test_message_item.py

import pytest
from visualization.message_item import (
    MessageItem,
    MessageType,
    CollapsibleMessageGroup,
    MaxLevelExceeded,
)


class TestMessageItemDefaultCollapse:
    """测试 MessageItem 默认折叠逻辑"""
    
    @pytest.mark.parametrize("msg_type,expected", [
        (MessageType.USER, False),
        (MessageType.ASSISTANT, False),
        (MessageType.ERROR, False),
        (MessageType.THINKING, True),
        (MessageType.TOOL_CALL, True),
        (MessageType.TOOL_RESULT, True),
        (MessageType.DEBUG, True),
        (MessageType.SYSTEM, True),
        (MessageType.METADATA, True),
    ])
    def test_default_collapse_by_type(self, msg_type, expected):
        """验证每种类型的默认折叠状态"""
        msg = MessageItem(
            id=f"test-{msg_type.name}",
            type=msg_type,
            title="Test",
            content="...",
        )
        assert msg.is_collapsed == expected, \
            f"{msg_type.name} should default to collapsed={expected}"


class TestCollapsibleMessageGroup:
    """测试 CollapsibleMessageGroup"""
    
    def test_collapse_by_type_selective(self):
        """验证按类型折叠只影响目标类型"""
        group = CollapsibleMessageGroup(
            id="test",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."),
                MessageItem(id="d2", type=MessageType.DEBUG, title="D2", content="..."),
                MessageItem(id="u1", type=MessageType.USER, title="U1", content="..."),
            ]
        )
        
        group.collapse_by_type(MessageType.DEBUG)
        
        assert group.items[0].is_collapsed is True
        assert group.items[1].is_collapsed is True
        assert group.items[2].is_collapsed is False  # USER 不受影响
```

---

## 6. 风险评估

### 6.1 修复风险

| 修复项 | 风险 | 缓解措施 |
|--------|------|----------|
| C1 重构 | 改变默认行为可能影响现有调用 | 保持向后兼容，is_collapsed=None 时用新逻辑 |
| C2 快捷键 | 用户习惯改变 | 提供配置选项，允许自定义快捷键 |
| M1 Diff | 解析逻辑变化 | 完整测试 diff_parser |

### 6.2 边界条件

| 场景 | 预期行为 |
|------|----------|
| MessageItem(is_collapsed=True) 传入 DEBUG | 显式值优先，保持折叠 |
| MessageItem(is_collapsed=False) 传入 DEBUG | 显式值优先，展开 DEBUG |
| 嵌套深度正好 10 | 合法 |
| 嵌套深度 11 | 抛出 MaxLevelExceeded |
| 空 diff 文本 | 返回空列表 |

---

## 7. 后续建议

1. **流式输出支持**: 当前设计假设消息完整，需要补充流式输出的实时折叠处理
2. **配置持久化**: 折叠偏好应持久化到用户配置
3. **快捷键自定义**: 提供用户自定义快捷键的能力

---

**修复蓝图结束**
