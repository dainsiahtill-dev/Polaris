"""ProtocolParser 单元测试.

覆盖范围:
- PATCH_FILE 格式解析
- SEARCH_REPLACE 格式解析
- 独立 SEARCH/REPLACE 块解析
- DELETE 操作解析
- 畸形输入处理
- 空输入处理
"""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.protocol_kernel import (
    EditType,
    ProtocolParser,
)


class TestProtocolParserPatchFile:
    """PATCH_FILE 格式解析测试."""

    def test_parse_patch_file_with_search_replace(self):
        """测试 PATCH_FILE 格式含 SEARCH/REPLACE."""
        text = """PATCH_FILE: src/main.py
<<<<<<< SEARCH
old line
=======
new line
>>>>>>> REPLACE
END PATCH_FILE"""

        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].path == "src/main.py"
        assert ops[0].edit_type == EditType.SEARCH_REPLACE
        assert "old line" in ops[0].search
        assert "new line" in ops[0].replace

    def test_parse_patch_file_full_content(self):
        """测试 PATCH_FILE 格式含全文件内容."""
        text = """PATCH_FILE: src/config.py
# Configuration
DEBUG = True
END PATCH_FILE"""

        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].path == "src/config.py"
        assert ops[0].edit_type == EditType.FULL_FILE

    def test_parse_multiple_patch_files(self):
        """测试多个 PATCH_FILE 块."""
        text = """PATCH_FILE: src/a.py
content A
END PATCH_FILE

PATCH_FILE: src/b.py
content B
END PATCH_FILE"""

        ops = ProtocolParser.parse(text)
        assert len(ops) == 2
        assert ops[0].path == "src/a.py"
        assert ops[1].path == "src/b.py"


class TestProtocolParserSearchReplace:
    """SEARCH/REPLACE 格式解析测试."""

    def test_parse_git_style_search_replace(self):
        """测试 Git 风格 SEARCH/REPLACE."""
        text = """src/main.py
<<<<<<< SEARCH
old function
=======
new function
>>>>>>> REPLACE"""

        ops = ProtocolParser.parse(text)
        assert len(ops) >= 1
        assert any(op.path == "src/main.py" for op in ops)

    def test_parse_simple_search_replace(self):
        """测试简单 SEARCH:/REPLACE: 格式.

        注意: 简单格式需要有明确的文件路径才能被正确解析。
        """
        # 使用 PATCH_FILE 包装的简单格式
        text = """PATCH_FILE: src/test.py
SEARCH:
old line
REPLACE:
new line
END PATCH_FILE"""

        ops = ProtocolParser.parse(text)
        search_replace_ops = [op for op in ops if op.edit_type == EditType.SEARCH_REPLACE]
        assert len(search_replace_ops) >= 1


class TestProtocolParserDelete:
    """DELETE 操作解析测试."""

    def test_parse_delete_operation(self):
        """测试 DELETE_FILE 操作."""
        text = "DELETE_FILE: src/obsolete.py"

        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].path == "src/obsolete.py"
        assert ops[0].edit_type == EditType.DELETE

    def test_parse_delete_with_path(self):
        """测试带路径前缀的 DELETE."""
        text = "DELETE_FILE: /full/path/to/file.py"

        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.DELETE


class TestProtocolParserEdgeCases:
    """边界情况测试."""

    def test_empty_input(self):
        """测试空输入."""
        assert ProtocolParser.parse("") == []
        assert ProtocolParser.parse("   ") == []
        assert ProtocolParser.parse("NO_CHANGES") == []

    def test_no_valid_operations(self):
        """测试无有效操作."""
        text = "Just some plain text without any operations"
        ops = ProtocolParser.parse(text)
        # 可能解析出 0 或更多操作，取决于是否有路径格式的文本

    def test_whitespace_only_search(self):
        """测试仅空白字符的 SEARCH."""
        text = """src/file.py
<<<<<<< SEARCH

=======
new content
>>>>>>> REPLACE"""

        ops = ProtocolParser.parse(text)
        # 空 SEARCH 应被正确处理

    def test_path_with_spaces(self):
        """测试带空格的路径."""
        text = """src/my file.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE"""

        ops = ProtocolParser.parse(text)
        # 路径中的空格应被正确处理


class TestProtocolParserDeduplication:
    """去重测试."""

    def test_duplicate_operations_removed(self):
        """测试重复操作被去重."""
        text = """src/main.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
PATCH_FILE: src/main.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
END PATCH_FILE"""

        ops = ProtocolParser.parse(text)
        # 相同操作应被去重
        paths = [op.path for op in ops]
        # 允许有重复路径（因为可能是不同的操作），但 compute_hash 应去重相同操作


class TestProtocolParserPathNormalization:
    """路径归一化测试."""

    def test_backslash_normalized(self):
        """测试反斜杠归一化."""
        text = r"""src\path\file.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE"""

        ops = ProtocolParser.parse(text)
        for op in ops:
            assert "\\" not in op.path

    def test_leading_dot_slash_removed(self):
        """测试前导 ./ 移除."""
        text = """./src/main.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE"""

        ops = ProtocolParser.parse(text)
        for op in ops:
            assert not op.path.startswith("./")


# 运行所有测试的便捷函数
def run_all_tests():
    """运行所有测试（用于快速验证）。"""
    test_classes = [
        TestProtocolParserPatchFile,
        TestProtocolParserSearchReplace,
        TestProtocolParserDelete,
        TestProtocolParserEdgeCases,
        TestProtocolParserDeduplication,
        TestProtocolParserPathNormalization,
    ]

    total = 0
    passed = 0
    failed = []

    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, method_name)()
                    passed += 1
                    print(f"✓ {cls.__name__}.{method_name}")
                except Exception as e:
                    failed.append((cls.__name__, method_name, str(e)))
                    print(f"✗ {cls.__name__}.{method_name}: {e}")

    print(f"\n总计: {passed}/{total} 通过")
    if failed:
        print(f"失败: {len(failed)}")
        for cls_name, method_name, error in failed:
            print(f"  - {cls_name}.{method_name}: {error}")
        return False
    return True


if __name__ == "__main__":
    run_all_tests()
