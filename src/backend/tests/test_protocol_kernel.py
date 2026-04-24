"""Protocol Kernel v2.0 - 严格模式测试

测试覆盖：
1. 协议解析：所有方言输入正确归一化
2. 严格语义：SEARCH未命中/多命中失败
3. 安全边界：路径穿越检测
4. 错误契约：结构化错误码
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# 测试目标
from polaris.kernelone.llm.toolkit.protocol_kernel import (
    EditType,
    ErrorCode,
    FileOperation,
    OperationValidator,
    ProtocolParser,
    StrictOperationApplier,
    _detect_path_traversal,
    _is_path_safe,
    _normalize_path,
    apply_protocol_output,
)


class TestPathNormalization:
    """路径归一化测试."""

    def test_normalize_basic(self):
        assert _normalize_path("./src/fastapi_entrypoint.py") == "src/fastapi_entrypoint.py"
        assert _normalize_path("src//fastapi_entrypoint.py") == "src/fastapi_entrypoint.py"
        assert _normalize_path("src\\fastapi_entrypoint.py") == "src/fastapi_entrypoint.py"

    def test_normalize_strip_quotes(self):
        assert _normalize_path("`src/fastapi_entrypoint.py`") == "src/fastapi_entrypoint.py"
        assert _normalize_path("'src/fastapi_entrypoint.py'") == "src/fastapi_entrypoint.py"
        assert _normalize_path('"src/fastapi_entrypoint.py"') == "src/fastapi_entrypoint.py"

    def test_normalize_remove_comments(self):
        assert _normalize_path("src/fastapi_entrypoint.py # comment") == "src/fastapi_entrypoint.py"
        assert _normalize_path("src/fastapi_entrypoint.py // comment") == "src/fastapi_entrypoint.py"


class TestPathSecurity:
    """路径安全测试."""

    def test_detect_traversal_basic(self):
        assert _detect_path_traversal("../etc/passwd") is True
        assert _detect_path_traversal("..\\windows\\system32") is True

    def test_detect_traversal_url_encoded(self):
        assert _detect_path_traversal("%2e%2e%2fetc/passwd") is True
        assert _detect_path_traversal("%252e%252e%252fetc") is True

    def test_safe_path(self):
        assert _detect_path_traversal("src/fastapi_entrypoint.py") is False
        assert _detect_path_traversal("deep/nested/file.py") is False

    def test_is_path_safe_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 安全路径
            safe, full = _is_path_safe(tmpdir, "src/fastapi_entrypoint.py")
            assert safe is True
            assert Path(full).resolve() == Path(os.path.join(tmpdir, "src/fastapi_entrypoint.py")).resolve()

            # 不安全路径 - 穿越
            safe, _ = _is_path_safe(tmpdir, "../outside.py")
            assert safe is False


class TestProtocolParsing:
    """协议解析测试."""

    def test_parse_delete_operation(self):
        text = "DELETE_FILE: src/old.py"
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.DELETE
        assert ops[0].path == "src/old.py"

    def test_parse_file_block(self):
        text = """FILE: src/fastapi_entrypoint.py
def hello():
    pass
END FILE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.FULL_FILE
        assert ops[0].path == "src/fastapi_entrypoint.py"
        assert "def hello():" in ops[0].replace

    def test_parse_create_block(self):
        text = """CREATE: src/new.py
class NewClass:
    pass
END FILE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.CREATE
        assert ops[0].path == "src/new.py"

    def test_parse_search_replace_git_format(self):
        text = """PATCH_FILE: src/fastapi_entrypoint.py
<<<<<<< SEARCH
def old():
    pass
=======
def new():
    pass
>>>>>>> REPLACE
END PATCH_FILE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.SEARCH_REPLACE
        assert ops[0].path == "src/fastapi_entrypoint.py"
        assert "def old():" in ops[0].search
        assert "def new():" in ops[0].replace

    def test_parse_search_replace_simple_format(self):
        text = """PATCH_FILE: src/fastapi_entrypoint.py
SEARCH:
def old():
    pass
REPLACE:
def new():
    pass
END PATCH_FILE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.SEARCH_REPLACE

    def test_parse_standalone_search_replace(self):
        text = """src/fastapi_entrypoint.py
<<<<<<< SEARCH
def old():
    pass
=======
def new():
    pass
>>>>>>> REPLACE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].edit_type == EditType.SEARCH_REPLACE
        assert ops[0].path == "src/fastapi_entrypoint.py"

    def test_parse_empty_search(self):
        text = """PATCH_FILE: src/fastapi_entrypoint.py
<<<<<<< SEARCH
<empty>
=======
def new():
    pass
>>>>>>> REPLACE
END PATCH_FILE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 1
        assert ops[0].search == ""
        assert ops[0].replace == "def new():\n    pass"

    def test_parse_mixed_operations(self):
        text = """DELETE_FILE: src/old.py

PATCH_FILE: src/fastapi_entrypoint.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
END PATCH_FILE

FILE: src/new.py
content
END FILE"""
        ops = ProtocolParser.parse(text)
        assert len(ops) == 3
        assert ops[0].edit_type == EditType.DELETE
        assert ops[1].edit_type == EditType.SEARCH_REPLACE
        assert ops[2].edit_type == EditType.FULL_FILE

    def test_parse_no_changes(self):
        ops = ProtocolParser.parse("")
        assert ops == []

        ops = ProtocolParser.parse("NO_CHANGES")
        assert ops == []

    def test_deduplication(self):
        text = """DELETE_FILE: src/old.py
DELETE_FILE: src/old.py
DELETE_FILE: src/old.py"""
        ops = ProtocolParser.parse(text)
        # 应该去重
        assert len(ops) == 1


class TestStrictApply:
    """严格模式执行测试."""

    def test_apply_full_file_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            op = FileOperation(
                path="new.py",
                edit_type=EditType.FULL_FILE,
                replace="def hello(): pass",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is True
            assert result.changed is True
            assert result.error_code == ErrorCode.OK

            # 验证文件内容
            with open(os.path.join(tmpdir, "new.py"), encoding="utf-8") as f:
                assert f.read() == "def hello(): pass"

    def test_apply_full_file_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 先创建文件
            with open(os.path.join(tmpdir, "same.py"), "w", encoding="utf-8") as f:
                f.write("same content")

            op = FileOperation(
                path="same.py",
                edit_type=EditType.FULL_FILE,
                replace="same content",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is True
            assert result.changed is False
            assert result.error_code == ErrorCode.NOOP

    def test_apply_search_replace_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 先创建文件
            with open(os.path.join(tmpdir, "fastapi_entrypoint.py"), "w", encoding="utf-8") as f:
                f.write("def old(): pass\n")

            op = FileOperation(
                path="fastapi_entrypoint.py",
                edit_type=EditType.SEARCH_REPLACE,
                search="def old(): pass",
                replace="def new(): pass",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is True
            assert result.changed is True

            with open(os.path.join(tmpdir, "fastapi_entrypoint.py"), encoding="utf-8") as f:
                assert "def new(): pass" in f.read()

    def test_apply_search_not_found_strict(self):
        """严格模式：SEARCH未命中应失败."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "fastapi_entrypoint.py"), "w", encoding="utf-8") as f:
                f.write("def existing(): pass\n")

            op = FileOperation(
                path="fastapi_entrypoint.py",
                edit_type=EditType.SEARCH_REPLACE,
                search="def nonexistent(): pass",
                replace="def new(): pass",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is False
            assert result.error_code == ErrorCode.SEARCH_NOT_FOUND
            assert "not found" in result.error_message.lower()

    def test_apply_search_ambiguous(self):
        """严格模式：SEARCH多命中应失败."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "fastapi_entrypoint.py"), "w", encoding="utf-8") as f:
                f.write("def duplicate(): pass\ndef duplicate(): pass\n")

            op = FileOperation(
                path="fastapi_entrypoint.py",
                edit_type=EditType.SEARCH_REPLACE,
                search="def duplicate(): pass",
                replace="def new(): pass",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is False
            assert result.error_code == ErrorCode.SEARCH_AMBIGUOUS
            assert "2 matches" in result.error_message

    def test_apply_search_not_found_uses_native_fuzzy_engine(self, monkeypatch):
        """allow_fuzzy_match=True 时应优先尝试 KernelOne 原生 fuzzy 引擎。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "fastapi_entrypoint.py")
            with open(target, "w", encoding="utf-8") as f:
                f.write("def existing():\n    return 1\n")

            op = FileOperation(
                path="fastapi_entrypoint.py",
                edit_type=EditType.SEARCH_REPLACE,
                search="def missing():\n    return 1\n",
                replace="def rewritten():\n    return 2\n",
            )

            from polaris.kernelone.llm.toolkit.protocol_kernel import StrictOperationApplier as _Applier

            monkeypatch.setattr(
                _Applier,
                "_apply_native_fuzzy_replace",
                staticmethod(lambda **_: "def rewritten():\n    return 2\n"),
            )

            result = StrictOperationApplier.apply(op, tmpdir, allow_fuzzy_match=True)
            assert result.success is True
            assert result.changed is True
            with open(target, encoding="utf-8") as f:
                text = f.read()
            assert "def rewritten():" in text

    def test_apply_empty_search_as_full_file(self):
        """空SEARCH视为全文件替换."""
        with tempfile.TemporaryDirectory() as tmpdir:
            op = FileOperation(
                path="new.py",
                edit_type=EditType.SEARCH_REPLACE,
                search="",
                replace="new content",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is True
            assert result.changed is True

    def test_apply_delete_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建文件
            with open(os.path.join(tmpdir, "old.py"), "w", encoding="utf-8") as f:
                f.write("content")

            op = FileOperation(
                path="old.py",
                edit_type=EditType.DELETE,
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is True
            assert result.changed is True
            assert not os.path.exists(os.path.join(tmpdir, "old.py"))

    def test_apply_delete_noop(self):
        """删除不存在的文件视为NOOP."""
        with tempfile.TemporaryDirectory() as tmpdir:
            op = FileOperation(
                path="nonexistent.py",
                edit_type=EditType.DELETE,
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is True
            assert result.changed is False
            assert result.error_code == ErrorCode.NOOP

    def test_apply_path_traversal_blocked(self):
        """路径穿越应被阻止."""
        with tempfile.TemporaryDirectory() as tmpdir:
            op = FileOperation(
                path="../etc/passwd",
                edit_type=EditType.FULL_FILE,
                replace="evil",
            )
            result = StrictOperationApplier.apply(op, tmpdir)

            assert result.success is False
            assert result.error_code == ErrorCode.PATH_TRAVERSAL


class TestApplyReport:
    """执行报告测试."""

    def test_report_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建初始文件
            with open(os.path.join(tmpdir, "a.py"), "w", encoding="utf-8") as f:
                f.write("a")

            report = apply_protocol_output(
                """DELETE_FILE: a.py
FILE: b.py
content
END FILE
FILE: c.py
content
END FILE""",
                tmpdir,
                strict=False,  # 允许部分成功
            )

            assert report.ops_total == 3
            assert report.ops_applied == 3
            assert len(report.changed_files) == 3
            assert report.success is True

    def test_report_with_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建文件但不包含SEARCH内容
            with open(os.path.join(tmpdir, "fastapi_entrypoint.py"), "w", encoding="utf-8") as f:
                f.write("existing")

            report = apply_protocol_output(
                """PATCH_FILE: fastapi_entrypoint.py
<<<<<<< SEARCH
nonexistent
=======
new
>>>>>>> REPLACE
END PATCH_FILE""",
                tmpdir,
                strict=False,  # 非严格模式，继续处理
            )

            assert report.ops_failed == 1
            assert report.success is False
            assert ErrorCode.SEARCH_NOT_FOUND in report.error_codes

    def test_report_to_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report = apply_protocol_output(
                """FILE: test.py
content
END FILE""",
                tmpdir,
            )

            d = report.to_dict()
            assert d["protocol_version"] == "2.0-strict"
            assert d["stats"]["total"] == 1
            assert d["stats"]["applied"] == 1


class TestValidator:
    """验证器测试."""

    def test_validate_empty_path(self):
        op = FileOperation(path="", edit_type=EditType.FULL_FILE, replace="content")
        result = OperationValidator.validate(op, "/tmp")
        assert result.valid is False
        assert result.error_code == ErrorCode.INVALID_PATH

    def test_validate_traversal(self):
        op = FileOperation(path="../etc/passwd", edit_type=EditType.FULL_FILE, replace="evil")
        result = OperationValidator.validate(op, "/tmp/workspace")
        assert result.valid is False
        assert result.error_code == ErrorCode.PATH_TRAVERSAL

    def test_validate_outside_workspace(self):
        op = FileOperation(path="/absolute/path.py", edit_type=EditType.FULL_FILE, replace="content")
        result = OperationValidator.validate(op, "/tmp/workspace")
        assert result.valid is False
        assert result.error_code == ErrorCode.PATH_OUTSIDE_WORKSPACE

    def test_validate_missing_replace(self):
        op = FileOperation(path="test.py", edit_type=EditType.SEARCH_REPLACE, search="old")
        result = OperationValidator.validate(op, "/tmp")
        assert result.valid is False
        assert result.error_code == ErrorCode.EMPTY_OPERATION


class TestProtocolCompatibility:
    """协议兼容性测试 - 历史方言."""

    def test_patch_file_variations(self):
        """测试PATCH_FILE的各种变体."""
        variations = [
            # 带冒号
            "PATCH_FILE: src/fastapi_entrypoint.py\ncontent\nEND PATCH_FILE",
            # 带空格
            "PATCH_FILE src/fastapi_entrypoint.py\ncontent\nEND PATCH_FILE",
            # 大写
            "patch_file: src/fastapi_entrypoint.py\ncontent\nEND PATCH_FILE",
        ]
        for v in variations:
            ops = ProtocolParser.parse(v)
            assert len(ops) >= 1, f"Failed: {v[:30]}..."

    def test_file_variations(self):
        """测试FILE的各种变体."""
        variations = [
            "FILE: src/fastapi_entrypoint.py\ncontent\nEND FILE",
            "FILE src/fastapi_entrypoint.py\ncontent\nEND FILE",
            "file: src/fastapi_entrypoint.py\ncontent\nend file",
        ]
        for v in variations:
            ops = ProtocolParser.parse(v)
            assert len(ops) >= 1, f"Failed: {v[:30]}..."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
