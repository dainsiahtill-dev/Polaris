# CLI 可视化增强 - 质量门禁与工程规范 v1.2
**文档版本**: 1.2.0
**创建日期**: 2026-03-27
**更新日期**: 2026-03-27
**状态**: 已生效

---

## 0. 功能规格 (Feature Spec)

### 0.1 可折叠 DEBUG 消息

**核心功能**:
- DEBUG 消息默认折叠，使用 `[▶]` 标记
- 鼠标点击 `[▶]` / `[▼]` 标记可展开/折叠单个消息
- Alt+D 快捷键切换所有 DEBUG 可见性
- Alt+Shift+D 折叠所有 DEBUG
- `/debug` 命令行控制

**终端支持**:
- 支持的终端: Windows Terminal, iTerm2, kitty, GNOME Terminal 等现代终端
- 使用 ANSI OSC 8 超链接序列实现可点击标记
- 使用 SGR 扩展鼠标追踪检测点击事件

---

## 1. 绝对禁止规则

### 1.1 测试文件禁止规则 ⚠️

> **铁律**: 严禁在 `src/backend/polaris/`、`src/backend/app/`、`src/backend/core/` 等源代码目录下创建任何测试文件。

**禁止模式** (src/backend/ 下的源代码目录):
```
❌ src/backend/polaris/delivery/cli/visualization/test_*.py
❌ src/backend/polaris/delivery/cli/visualization/*_test.py
❌ src/backend/polaris/cells/**/test_*.py
❌ src/backend/app/**/test_*.py
❌ src/backend/core/**/test_*.py
```

**正确模式** (测试必须放在 tests/ 目录):
```
✅ tests/delivery/cli/visualization/test_message_item.py
✅ tests/delivery/cli/visualization/test_diff_parser.py
✅ tests/delivery/cli/visualization/test_collapsible.py
```

### 1.2 目录结构规范

```
polaris/
├── src/backend/                              ← ❌ 源代码目录（禁止放测试）
│   └── polaris/
│       └── delivery/
│           └── cli/
│               └── visualization/             ← 源代码
│                   ├── __init__.py
│                   ├── message_item.py     ← ✅ 代码文件
│                   ├── collapsible.py       ← ✅ 代码文件
│                   └── diff_parser.py      ← ✅ 代码文件
│
└── tests/                                    ← ✅ 测试目录
    └── delivery/
        └── cli/
            └── visualization/
                ├── __init__.py
                ├── test_message_item.py     ← ✅ 测试文件
                ├── test_collapsible.py      ← ✅ 测试文件
                └── test_diff_parser.py      ← ✅ 测试文件
```
                └── test_diff_parser.py
```

---

## 2. CI/CD 门禁脚本

### 2.1 测试文件位置检查

创建 `docs/governance/ci/check_no_test_in_src.py`:

```python
#!/usr/bin/env python3
"""测试文件位置检查门禁

确保测试文件不在 src/backend/ 目录下。

Usage:
    python check_no_test_in_src.py [--fix]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


# 禁止模式：测试文件正则
TEST_FILE_PATTERNS = [
    re.compile(r"^test_.+\.py$"),           # test_*.py
    re.compile(r"^.+_test\.py$"),            # *_test.py
    re.compile(r"^tests\.py$"),              # tests.py
    re.compile(r"^conftest\.py$"),          # conftest.py
]

# 禁止目录
FORBIDDEN_DIRS = [
    "src/backend",
    "polaris/delivery/cli/visualization",
]

# 允许的测试目录
ALLOWED_TEST_ROOT = "tests"


def find_test_files_in_forbidden_dirs() -> list[tuple[str, str]]:
    """查找禁止目录中的测试文件"""
    violations = []

    for root, dirs, files in os.walk("src/backend"):
        # 跳过已允许的目录
        if ALLOWED_TEST_ROOT in root.split(os.sep):
            continue

        for file in files:
            for pattern in TEST_FILE_PATTERNS:
                if pattern.match(file):
                    rel_path = os.path.relpath(os.path.join(root, file))
                    violations.append((rel_path, pattern.pattern))
                    break

    return violations


def check_test_imports_in_source() -> list[tuple[str, str]]:
    """检查源代码中是否有测试相关的导入"""
    violations = []

    source_files = []
    for root, dirs, files in os.walk("src/backend/polaris/delivery/cli/visualization"):
        for file in files:
            if file.endswith(".py") and not file.startswith("test_"):
                source_files.append(os.path.join(root, file))

    test_import_pattern = re.compile(r"from\s+.*test|import\s+.*test", re.IGNORECASE)

    for filepath in source_files:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        for match in test_import_pattern.finditer(content):
            violations.append((filepath, match.group()))

    return violations


def main() -> int:
    """主函数"""
    parser = argparse.ArgumentParser(description="检查测试文件位置")
    parser.add_argument("--fix", action="store_true", help="自动修复（移动文件）")
    parser.add_argument("--strict", action="store_true", help="严格模式（也检查导入）")
    args = parser.parse_args()

    all_violations = []

    # 检查文件位置
    file_violations = find_test_files_in_forbidden_dirs()
    if file_violations:
        all_violations.append(("FILE_LOCATION", file_violations))

    # 检查导入（可选）
    if args.strict:
        import_violations = check_test_imports_in_source()
        if import_violations:
            all_violations.append(("TEST_IMPORTS", import_violations))

    # 报告结果
    if not all_violations:
        print("✅ 门禁通过: 无测试文件违规")
        return 0

    print("❌ 门禁失败: 发现以下违规")
    print()

    for category, violations in all_violations:
        print(f"【{category}】")
        for path, detail in violations:
            print(f"  - {path}")
            if detail:
                print(f"    匹配: {detail}")
        print()

    if args.fix:
        print("⚠️  自动修复未启用，请手动移动测试文件到 tests/ 目录")

    return 1


if __name__ == "__main__":
    sys.exit(main())
```

### 2.2 GitHub Actions 集成

创建 `.github/workflows/cli-visual-quality.yml`:

```yaml
name: CLI Visualization Quality Gate

on:
  pull_request:
    paths:
      - 'polaris/delivery/cli/visualization/**'
      - 'tests/delivery/cli/visualization/**'

jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Check no test files in src/backend
        run: |
          python docs/governance/ci/check_no_test_in_src.py --strict

      - name: Run visualization tests
        run: |
          pytest tests/delivery/cli/visualization/ -v --cov

      - name: Type check
        run: |
          mypy polaris/delivery/cli/visualization/

      - name: Lint
        run: |
          ruff check polaris/delivery/cli/visualization/
```

---

## 3. 测试文件模板

### 3.1 测试文件位置

所有测试必须放在 `tests/delivery/cli/visualization/` 目录下：

```
tests/delivery/cli/visualization/
├── __init__.py
├── test_message_item.py      # 测试 MessageItem
├── test_collapsible.py       # 测试 CollapsibleItem
└── test_diff_parser.py       # 测试 DiffView
```

### 3.2 测试模板

创建 `tests/delivery/cli/visualization/test_message_item.py`:

```python
"""MessageItem 测试套件

测试消息级折叠核心功能。
测试文件位置: tests/delivery/cli/visualization/
源代码位置: src/backend/polaris/delivery/cli/visualization/
"""

from __future__ import annotations

import pytest

from polaris.delivery.cli.visualization import (
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
        assert msg.is_collapsed == expected, (
            f"{msg_type.name} should default to collapsed={expected}"
        )

    def test_explicit_override(self):
        """显式设置覆盖默认值"""
        msg = MessageItem(
            id="debug-1",
            type=MessageType.DEBUG,
            title="永久展开的 DEBUG",
            content="...",
            is_collapsed=False,  # 显式覆盖
        )
        assert msg.is_collapsed is False

    def test_debug_default_collapsed(self):
        """DEBUG 信息默认折叠"""
        msg = MessageItem(
            id="debug-1",
            type=MessageType.DEBUG,
            title="Kernel 操作",
            content="完整调用栈...",
        )
        assert msg.is_collapsed is True
        assert msg.get_default_collapse() is True


class TestMessageItemNesting:
    """测试嵌套功能"""

    def test_nested_max_level(self):
        """嵌套层级超限"""
        parent = MessageItem(
            id="p",
            type=MessageType.USER,
            title="Parent",
            content="",
        )

        # 创建深度为 10 的嵌套
        current = parent
        for i in range(9):
            child = MessageItem(
                id=f"c{i}",
                type=MessageType.DEBUG,
                title=f"Child {i}",
                content="",
            )
            current.add_child(child)
            current = child

        # 再添加一个会超过限制
        over_child = MessageItem(
            id="over",
            type=MessageType.DEBUG,
            title="Over",
            content="",
        )

        with pytest.raises(MaxLevelExceeded) as exc_info:
            current.add_child(over_child)

        assert exc_info.value.max_level == 10
        assert exc_info.value.actual_level == 11

    def test_add_child_type_check(self):
        """add_child 类型检查"""
        parent = MessageItem(
            id="p",
            type=MessageType.USER,
            title="Parent",
            content="",
        )

        with pytest.raises(TypeError):
            parent.add_child("not a message item")  # type: ignore
```

创建 `tests/delivery/cli/visualization/test_diff_parser.py`:

```python
"""DiffView 测试套件

测试基于 difflib 的 Diff 解析功能。
"""

from __future__ import annotations

import pytest

from polaris.delivery.cli.visualization import DiffView, compute_diff


class TestDiffViewCompute:
    """测试 Diff 计算"""

    def test_add_line(self):
        """新增行"""
        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"
        diff = compute_diff(old, new, "test.txt")

        assert len(diff.files) == 1
        assert diff.files[0].insertions == 1
        assert diff.files[0].deletions == 0

    def test_delete_line(self):
        """删除行"""
        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"
        diff = compute_diff(old, new, "test.txt")

        assert len(diff.files) == 1
        assert diff.files[0].deletions == 1

    def test_modify_line(self):
        """修改行"""
        old = "line1\nline2\nline3\n"
        new = "line1\nline2_modified\nline3\n"
        diff = compute_diff(old, new, "test.txt")

        assert len(diff.files) == 1
        assert diff.files[0].insertions == 1
        assert diff.files[0].deletions == 1

    def test_no_changes(self):
        """无变化"""
        text = "line1\nline2\n"
        diff = compute_diff(text, text, "test.txt")

        assert len(diff.files) == 1
        # 无 hunks 表示无变化


class TestDiffViewRender:
    """测试 Diff 渲染"""

    def test_render_unified(self):
        """unified 格式渲染"""
        old = "line1\nline2\n"
        new = "line1\nline2_modified\n"
        diff = compute_diff(old, new, "test.txt")
        output = diff.render_unified()

        assert "--- a/test.txt" in output
        assert "+++ b/test.txt" in output
        assert "-line2" in output
        assert "+line2_modified" in output

    def test_render_stat(self):
        """统计信息渲染"""
        old = "line1\nline2\nline3\n"
        new = "line1\nline2_modified\nline3\nline4\n"
        diff = compute_diff(old, new, "test.txt")
        stat = diff.render_stat()

        assert "test.txt" in stat
        assert "+1" in stat  # 1 insertion
        assert "-1" in stat  # 1 deletion
```

---

## 4. 执行检查命令

### 4.1 本地检查

```bash
# 检查测试文件位置
python docs/governance/ci/check_no_test_in_src.py

# 严格模式（也检查导入）
python docs/governance/ci/check_no_test_in_src.py --strict

# 运行测试
pytest tests/delivery/cli/visualization/ -v

# 类型检查
mypy polaris/delivery/cli/visualization/

# Lint 检查
ruff check polaris/delivery/cli/visualization/
```

### 4.2 CI 门禁清单

- [ ] `check_no_test_in_src.py` 返回 0
- [ ] `pytest tests/delivery/cli/visualization/` 通过
- [ ] `mypy polaris/delivery/cli/visualization/` 无错误
- [ ] `ruff check polaris/delivery/cli/visualization/` 无错误

---

## 5. 违规处理流程

```
发现违规测试文件
       │
       ▼
┌──────────────────┐
│ 1. 识别违规      │
│ - 文件在 src/backend/
│ - 或测试在源码目录
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 2. 立即转移      │
│ - 移动到 tests/
│ - 更新导入路径
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 3. 提交修复      │
│ - git add + commit
│ - 确保 CI 通过
└──────────────────┘
```

---

## 6. 常见错误与修复

### 错误 1: 在源码目录创建测试文件

```bash
# 错误
touch src/backend/polaris/delivery/cli/visualization/test_message_item.py

# 修复
mv src/backend/polaris/delivery/cli/visualization/test_message_item.py \
   tests/delivery/cli/visualization/test_message_item.py
```

### 错误 2: pytest 配置路径错误

```ini
# pytest.ini - 错误配置
python_paths = src/backend

# pytest.ini - 正确配置
python_paths = src
testpaths = tests
```

---

**门禁规则结束**
