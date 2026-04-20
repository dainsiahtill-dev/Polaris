from __future__ import annotations

import pytest
from polaris.kernelone.editing.editblock_engine import (
    EditBlock,
    count_edit_blocks,
    extract_edit_blocks,
    parse_edit_blocks,
    strip_filename,
    validate_edit_blocks,
)


def test_extract_edit_blocks_basic() -> None:
    text = "src/app.py\n<<<<<<< SEARCH\ndef old():\n    return 1\n=======\ndef new():\n    return 2\n>>>>>>> REPLACE\n"
    edits = extract_edit_blocks(text, valid_filenames=["src/app.py"])
    assert len(edits) == 1
    path, before, after = edits[0]
    assert path == "src/app.py"
    assert "def old():" in before
    assert "def new():" in after


def test_extract_edit_blocks_reuse_previous_filename() -> None:
    text = """src/app.py
<<<<<<< SEARCH
a
=======
b
>>>>>>> REPLACE
<<<<<<< SEARCH
c
=======
d
>>>>>>> REPLACE"""
    edits = extract_edit_blocks(text, valid_filenames=["src/app.py"])
    assert len(edits) == 2
    assert edits[0][0] == "src/app.py"
    assert edits[1][0] == "src/app.py"


# =============================================================================
# Enhanced Test Suite for Phase 1 Implementation
# =============================================================================


class TestEditBlockParsing:
    """测试编辑块解析功能"""

    def test_simple_block(self):
        """测试简单编辑块解析"""
        content = """
<<<< SEARCH:src/median.py
    if not values:
        return 0
====
    if not values:
        raise ValueError("Cannot compute median of empty list")
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].filepath == "src/median.py"
        assert "if not values:" in blocks[0].search_text
        assert "raise ValueError" in blocks[0].replace_text

    def test_embedded_filepath(self):
        """测试 SEARCH 行内嵌文件路径"""
        content = """
<<<< SEARCH:src/utils.py
def helper():
    pass
====
def helper():
    return True
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].filepath == "src/utils.py"

    def test_git_style_headers(self):
        """测试 Git 风格的 <<<<<<< SEARCH 格式"""
        content = """test.py
<<<<<<< SEARCH
old code
=======
new code
>>>>>>> REPLACE
"""
        blocks = parse_edit_blocks(content, valid_filenames=["test.py"])
        assert len(blocks) == 1
        assert "old code" in blocks[0].search_text
        assert "new code" in blocks[0].replace_text

    def test_multiple_blocks(self):
        """测试多文件编辑块"""
        content = """
<<<< SEARCH:models.py
class User:
    pass
====
class User:
    def __init__(self, name: str):
        self.name = name
>>>> REPLACE

<<<< SEARCH:schemas.py
class UserSchema:
    pass
====
class UserSchema:
    name: str
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 2
        assert blocks[0].filepath == "models.py"
        assert blocks[1].filepath == "schemas.py"

    def test_no_filepath_in_search(self):
        """测试 SEARCH 行没有文件路径的情况"""
        content = """src/utils.py
<<<< SEARCH
def old_func():
    pass
====
def new_func():
    return 42
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content, valid_filenames=["src/utils.py"])
        assert len(blocks) == 1
        assert blocks[0].filepath == "src/utils.py"

    def test_default_filepath_parameter(self):
        """测试 default_filepath 参数在没有文件路径时使用"""
        content = """
<<<< SEARCH
def old_func():
    pass
====
def new_func():
    return 42
>>>> REPLACE
"""
        # Without default_filepath, blocks should be skipped (no filename found)
        blocks_no_default = parse_edit_blocks(content)
        assert len(blocks_no_default) == 0

        # With default_filepath, blocks should use the provided default
        blocks_with_default = parse_edit_blocks(content, default_filepath="config.py")
        assert len(blocks_with_default) == 1
        assert blocks_with_default[0].filepath == "config.py"
        assert "def old_func():" in blocks_with_default[0].search_text
        assert "def new_func():" in blocks_with_default[0].replace_text

    def test_fence_cleanup(self):
        """测试 Markdown fence 清理"""
        content = """
```python
<<<< SEARCH:src/main.py
print("hello")
====
print("world")
>>>> REPLACE
```
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].filepath == "src/main.py"

    def test_empty_content(self):
        """测试空内容"""
        blocks = parse_edit_blocks("")
        assert len(blocks) == 0

    def test_no_blocks(self):
        """测试没有编辑块的内容"""
        content = "This is just some text without any edit blocks"
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 0

    def test_original_updated_variants(self):
        """测试 ORIGINAL/UPDATED 变体"""
        content = """
<<<< ORIGINAL:src/test.py
old
====
new
>>>> UPDATED
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1


class TestEditBlockValidation:
    """测试编辑块验证功能"""

    def test_valid_block(self):
        """测试有效编辑块"""
        block = EditBlock(
            filepath="test.py",
            search_text="old",
            replace_text="new",
        )
        errors = validate_edit_blocks([block])
        assert len(errors) == 0

    def test_empty_search(self):
        """测试空搜索文本"""
        block = EditBlock(
            filepath="test.py",
            search_text="",
            replace_text="new",
        )
        errors = validate_edit_blocks([block])
        assert len(errors) == 1
        assert "Empty search" in errors[0]

    def test_missing_filepath(self):
        """测试缺少文件路径"""
        block = EditBlock(
            filepath="",
            search_text="old",
            replace_text="new",
        )
        errors = validate_edit_blocks([block])
        assert len(errors) == 1
        assert "Missing filepath" in errors[0]

    def test_identical_search_replace_is_noop(self):
        """search == replace is a no-op block, not a validation error.

        The LLM hallucination pattern (copying search into replace without
        changes) is handled by apply_edit_blocks which skips no-op blocks,
        and by the filesystem handler which provides a helpful error message
        when ALL blocks are no-op.
        """
        block = EditBlock(
            filepath="test.py",
            search_text="same",
            replace_text="same",
        )
        errors = validate_edit_blocks([block])
        assert len(errors) == 0  # no-op is not a validation error


class TestStripFilename:
    """测试文件名清理功能"""

    def test_simple_filename(self):
        """测试简单文件名"""
        assert strip_filename("src/main.py") == "src/main.py"

    def test_with_fence(self):
        """测试带 fence 的文件名"""
        assert strip_filename("```python") is None
        assert strip_filename("```src/main.py") == "src/main.py"

    def test_with_markdown(self):
        """测试带 Markdown 格式的文件名"""
        assert strip_filename("# src/main.py") == "src/main.py"
        assert strip_filename("`src/main.py`") == "src/main.py"

    def test_marker_prefixes(self):
        """测试标记前缀"""
        assert strip_filename("SEARCH") is None
        assert strip_filename("<<<< SEARCH") is None
        assert strip_filename("FILE: src/main.py") is None

    def test_empty_and_whitespace(self):
        """测试空和空白"""
        assert strip_filename("") is None
        assert strip_filename("   ") is None
        assert strip_filename("...") is None


class TestCountEditBlocks:
    """测试计数功能"""

    def test_count(self):
        """测试编辑块计数"""
        content = """
<<<< SEARCH:a.py
a
====
b
>>>> REPLACE

<<<< SEARCH:b.py
c
====
d
>>>> REPLACE
"""
        assert count_edit_blocks(content) == 2

    def test_count_empty(self):
        """测试空内容计数"""
        assert count_edit_blocks("") == 0


class TestExtractEditBlocks:
    """测试向后兼容的提取函数"""

    def test_extract_returns_tuples(self):
        """测试提取返回元组"""
        content = """
<<<< SEARCH:test.py
old
====
new
>>>> REPLACE
"""
        blocks = extract_edit_blocks(content)
        assert len(blocks) == 1
        assert isinstance(blocks[0], tuple)
        assert len(blocks[0]) == 3
        filepath, search, replace = blocks[0]
        assert filepath == "test.py"
        assert "old" in search
        assert "new" in replace


class TestEdgeCases:
    """测试边界情况"""

    def test_unicode_content(self):
        """测试 Unicode 内容"""
        content = """
<<<< SEARCH:test.py
# 中文注释
print("hello 世界")
====
# 新的中文注释
print("你好 world")
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1
        assert "中文" in blocks[0].search_text
        assert "你好" in blocks[0].replace_text

    def test_special_characters(self):
        """测试特殊字符"""
        content = r"""
<<<< SEARCH:test.py
regex = r"[a-z]+"
====
regex = r"\w+"
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1
        assert r"[a-z]+" in blocks[0].search_text

    def test_multiline_with_blank_lines(self):
        """测试带空行的多行内容"""
        content = """
<<<< SEARCH:test.py
def func():
    line1

    line2
====
def func():
    new_line1

    new_line2
>>>> REPLACE
"""
        blocks = parse_edit_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].search_text.count("\n") > 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
